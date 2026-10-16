"""compare-range and scan: GraphSense's old and corrected code over a block range of a node, and what changes.

compare-range
    1. fetches the range from the node into a capture (or reads an existing one, offline);
    2. runs each revision's exporter, input resolver and transformer on the capture, one revision at a time, in a
       long-running worker (worker.serve);
    3. compares the two revisions' stored rows transaction by transaction and writes a summary.

scan
    runs both revisions' parsers over a range without resolving inputs, lists the blocks whose transactions carry an
    Orchard or Ironwood value and the transactions whose coinjoin flag could change. Blocks are deleted once
    processed, so any range fits.

The summary has a deterministic part, "results", whose sha256 depends only on the node's data for the range, the
code of the two revisions and the tool's version.
"""

import csv
import hashlib
import json
import os
import platform
import tempfile
import time

from . import PROG, __version__, capture, envs, relations, report, session
from . import manifest as manifest_module
from . import node as node_module

SUMMARY_FORMAT = 1
PARTITION_BATCH_SIZE = 100_000  # graphsense-lib's zec partition size (ingest/dump.py, PARTITIONSIZES)
PUBLISHED_DIR = "results"
COMPARE_SCHEMA_PATH = "manifest/compare-summary.schema.json"
SCAN_SCHEMA_PATH = "manifest/scan-summary.schema.json"
CHANGE_FIELDS = ("total_input", "total_output", "fee", "input_count", "output_count", "coinjoin")
UNCHANGED_FIELDS = ("height", "index", "txid", "coinbase", "regular_inputs", "regular_outputs", "cluster_set")
RECORD_FIELDS = (  # what records_sha256 covers: everything compared, in a fixed order
    "height",
    "index",
    "txid",
    "coinbase",
    "input_count",
    "output_count",
    "total_input",
    "total_output",
    "fee",
    "coinjoin",
    "shielded_inputs",
    "shielded_outputs",
    "pool_balances",
    "regular_inputs",
    "regular_outputs",
    "cluster_set",
    "cluster_set_filtered",
)


class RangeError(envs.SetupError):
    """The comparison could not be made: a revision raised, inputs stayed unresolved, or the records disagree."""


def check_range(start, end):
    if start < 0 or end < start:
        raise RangeError(f"not a block range: {start} to {end}")


def _revision_python(env_dir, side, override):
    if override.get("python"):
        return override["python"]
    return envs.env_python(env_dir, side)


def _revision_info(manifest, side, ready, override):
    """What the summary says about one revision, and whether it is the pinned code."""
    pinned = manifest["revisions"][side]
    identity = ready["identity"]
    if override.get("src"):
        problem = (
            None
            if identity["package_tree_sha256"] == pinned["package_tree_sha256"]
            else "files differ from the pinned release"
        )
    else:
        problem = envs.identity_problem(pinned, {"import_error": None, "identity": identity})
    return {
        "pinned": {
            "version": pinned["version"],
            "git_commit": pinned["git"]["commit"],
            "package_tree_sha256": pinned["package_tree_sha256"],
        },
        "found": {key: identity[key] for key in ("package_tree_sha256", "package_file_count", "files")},
        "is_pinned_code": problem is None,
    }, identity["distribution_version"]


INSTALL_HINT = "This command needs the environments of the transformer layer: run install --with-transformer first."


def _import_problem(label, ready, override):
    error = ready["import_error"]
    text = f"{label}: graphsenselib cannot be imported: {error['type']}: {error['message']}."
    return RangeError(text if override else f"{text} {INSTALL_HINT}")


def _failure(label, low, high, answer):
    """The error for a piece of the range that a revision could not process."""
    if answer.get("damaged"):
        return RangeError(f"the capture changed while it was read: {answer['damaged'][0]} is missing or damaged")
    error = answer["exception"]
    return RangeError(f"{label} raised on blocks {low:,}-{high:,}: {error['type']}: {error['message']}")


def _run_revision(python, job, scratch, pieces, status, label, echo_stderr, override):
    """Process every piece of the range with one revision; returns (ready message, finished message)."""
    with session.Session(python, job, scratch, echo_stderr) as worker:
        ready = worker.ready
        if not ready.get("ready"):
            raise _import_problem(label, ready, override)
        for number, (low, high) in enumerate(pieces, 1):
            if status:
                status(f"{label}: blocks {low:,}-{high:,} ({number}/{len(pieces)})")
            answer = worker.process(low, high)
            if answer.get("exception"):
                version = ready["identity"]["distribution_version"] or "from a source tree"
                raise _failure(f"{label} (graphsense-lib {version})", low, high, answer)
        finished = worker.close()
    if finished["network"]["refused"]:
        raise RangeError(f"{label}: GraphSense tried to reach the network: {finished['network']['refused'][0]}")
    return ready, finished


def _records(path):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def _digest_line(record):
    return json.dumps([record[name] for name in RECORD_FIELDS], separators=(",", ":"), ensure_ascii=True)


def explained(before, after):
    """True if the two records differ by exactly the Orchard and Ironwood legs the corrected parser reads.

    Those legs are added as shielded inputs (positive balance) and outputs (negative balance); the counts, totals
    and fee follow from them, and the coinjoin flag may change only where a leg was added. Everything else, the
    transparent side above all, must be the same.
    """
    balances = after["pool_balances"]
    if balances is None:
        return False
    legs = (balances[1], balances[2])
    added_in = [value for value in legs if value > 0]
    added_out = [-value for value in legs if value < 0]

    def fee_follows(record):
        return record["fee"] == (0 if record["coinbase"] else record["total_input"] - record["total_output"])

    return (
        sorted(before["shielded_inputs"] + added_in) == after["shielded_inputs"]
        and sorted(before["shielded_outputs"] + added_out) == after["shielded_outputs"]
        and before["input_count"] + len(added_in) == after["input_count"]
        and before["output_count"] + len(added_out) == after["output_count"]
        and before["total_input"] + sum(added_in) == after["total_input"]
        and before["total_output"] + sum(added_out) == after["total_output"]
        and fee_follows(before)
        and fee_follows(after)
        and (before["coinjoin"] == after["coinjoin"] or bool(added_in or added_out))
        and all(before[name] == after[name] for name in UNCHANGED_FIELDS)
    )


def compare_records(before_path, after_path, changes_path):
    """Compare the two revisions' records line by line. Returns the comparison part of the summary."""
    total = coinbase = 0
    changed = {
        name: 0 for name in ("transactions", "total_input", "total_output", "fee", "input_count", "output_count")
    }
    coinbase_output_changed = 0
    changed_blocks = set()
    fees = {side: {"negative": 0, "zero": 0} for side in envs.SIDES}
    newly = {pool: {"transactions": 0, "out_of_pool": 0, "into_pool": 0} for pool in ("orchard", "ironwood")}
    coinjoin = {"before": 0, "after": 0, "false_to_true": 0, "true_to_false": 0}
    flagged = {side: [] for side in envs.SIDES}
    settings = ("without_coinjoin_filter", "with_coinjoin_filter")
    sets_changed = {name: dict.fromkeys(settings, 0) for name in ("multi_input_address_set", "spark_regular_inputs")}
    edges = {side: dict.fromkeys(settings, 0) for side in envs.SIDES}
    rel = dict.fromkeys(
        (
            "pairs_before",
            "pairs_after",
            "pairs_changed",
            "pairs_only_before",
            "pairs_only_after",
            "transactions_with_changed_pairs",
            "estimated_value_before_of_changed",
            "estimated_value_after_of_changed",
            "absolute_change",
            "largest_change",
        ),
        0,
    )
    unexplained = []
    unresolved = dict.fromkeys(envs.SIDES, 0)
    digests = {side: hashlib.sha256() for side in envs.SIDES}
    with open(changes_path, "w", encoding="utf-8", newline="") as handle:
        out = csv.writer(handle, lineterminator="\n")
        out.writerow(
            ["height", "txid"]
            + [f"{name}_{side}" for name in CHANGE_FIELDS for side in envs.SIDES]
            + ["sapling_balance", "orchard_balance", "ironwood_balance", "relation_pairs_changed"]
        )
        after_records = _records(after_path)
        for before in _records(before_path):
            after = next(after_records, None)
            if after is None or (after["height"], after["txid"]) != (before["height"], before["txid"]):
                raise RangeError(
                    f"the two revisions returned different transactions at {before['height']} {before['txid']}"
                )
            total += 1
            coinbase += before["coinbase"]
            for side, record in (("before", before), ("after", after)):
                digests[side].update(_digest_line(record).encode() + b"\n")
                unresolved[side] += record["unresolved_inputs"]
                if not record["coinbase"]:
                    fees[side]["negative"] += record["fee"] < 0
                    fees[side]["zero"] += record["fee"] == 0
                if record["coinjoin"] is True:
                    coinjoin[side] += 1
                    flagged[side].append(record["txid"])
                edges[side]["without_coinjoin_filter"] += record["cluster_set"] is not None
                edges[side]["with_coinjoin_filter"] += record["cluster_set_filtered"] is not None
            if before["coinjoin"] != after["coinjoin"]:
                coinjoin["false_to_true" if after["coinjoin"] else "true_to_false"] += 1
            for setting, key in zip(settings, ("cluster_set", "cluster_set_filtered"), strict=True):
                sets_changed["multi_input_address_set"][setting] += before[key] != after[key]
                filtering = setting == "with_coinjoin_filter"
                spark_before = relations.cluster_set(before, filtering)
                sets_changed["spark_regular_inputs"][setting] += spark_before != relations.cluster_set(after, filtering)
            if after["pool_balances"] is not None:
                for index, pool in ((1, "orchard"), (2, "ironwood")):
                    value = after["pool_balances"][index]
                    if value:
                        newly[pool]["transactions"] += 1
                        newly[pool]["out_of_pool" if value > 0 else "into_pool"] += abs(value)
            differing = [name for name in CHANGE_FIELDS if before[name] != after[name]]
            for name in differing:
                if name != "coinjoin":
                    changed[name] += 1
            totals_changed = any(name in differing for name in ("total_input", "total_output", "fee"))
            if totals_changed:
                changed["transactions"] += 1
                changed_blocks.add(before["height"])
                coinbase_output_changed += before["coinbase"] and "total_output" in differing
            if not explained(before, after):
                unexplained.append(f"{before['height']} {before['txid']}")
            old, new = relations.estimates(before), relations.estimates(after)
            rel["pairs_before"] += len(old)
            rel["pairs_after"] += len(new)
            rel["pairs_only_before"] += len(old.keys() - new.keys())
            rel["pairs_only_after"] += len(new.keys() - old.keys())
            pairs_changed = 0
            for pair in old.keys() & new.keys():
                if old[pair] != new[pair]:
                    pairs_changed += 1
                    change = abs(new[pair] - old[pair])
                    rel["estimated_value_before_of_changed"] += old[pair]
                    rel["estimated_value_after_of_changed"] += new[pair]
                    rel["absolute_change"] += change
                    rel["largest_change"] = max(rel["largest_change"], change)
            rel["pairs_changed"] += pairs_changed
            rel["transactions_with_changed_pairs"] += pairs_changed > 0
            if differing or pairs_changed:
                out.writerow(
                    [before["height"], before["txid"]]
                    + [record[name] for name in CHANGE_FIELDS for record in (before, after)]
                    + (after["pool_balances"] or [None, None, None])
                    + [pairs_changed]
                )
        if next(after_records, None) is not None:
            raise RangeError("the after revision returned more transactions than the before revision")
    return {
        "transactions": {"total": total, "coinbase": coinbase, "non_coinbase": total - coinbase},
        "changed": {
            **changed,
            "blocks_with_changed_transactions": len(changed_blocks),
            "coinbase_total_output": coinbase_output_changed,
        },
        "fees_of_non_coinbase_transactions": fees,
        "newly_accounted": newly,
        "coinjoin_flags": {**coinjoin, "flagged_transactions": flagged},
        "clustering_input_sets_changed": sets_changed,
        "clustering_edges": edges,
        "relation_estimates": rel,
        "differences_explained": {"unexplained": len(unexplained), "examples": unexplained[:20]},
        "unresolved_inputs": unresolved,
        "records_sha256": {side: digest.hexdigest() for side, digest in digests.items()},
    }


def published_path(root, start, end):
    return os.path.join(root, PUBLISHED_DIR, f"compare-{start}-{end}.json")


def compare_range(root, arguments, overrides, echo=print, status=None, echo_stderr=None):
    """Run compare-range and return the exit status."""
    started = time.time()
    start, end = arguments.start, arguments.end
    check_range(start, end)
    manifest, _ = manifest_module.load(root)
    report_dir = arguments.report_dir or os.path.join(os.getcwd(), "report", f"compare-{start}-{end}")
    os.makedirs(report_dir, exist_ok=True)
    pieces = capture.chunks(start, end)
    node_version = None
    with tempfile.TemporaryDirectory(prefix="gs-zec-validate-", dir=report_dir) as scratch:
        if arguments.from_capture:
            capture_dir = arguments.from_capture
            metadata = capture.open_capture(capture_dir, start, end)
            node_version = metadata["node"]
            echo(f"capture     {capture_dir}: blocks {metadata['start']:,} to {metadata['end']:,}, {node_version}")
        else:
            client = node_module.connect(arguments)
            info = client.call("getinfo", [])
            node_version = info.get("subversion")
            tip = info.get("blocks")
            echo(f"node        {node_version}, height {tip}")
            if isinstance(tip, int) and end > tip:
                raise RangeError(f"the node is at height {tip}; the range ends at {end}")
            capture_dir = arguments.keep_capture or os.path.join(scratch, "capture")
            writer = capture.Writer(capture_dir, start, end)
            capture.fetch(client, start, end, writer, workers=arguments.rpc_workers, status=status)
            writer.finish(node_version, True)
            echo(
                f"fetched     {writer.blocks:,} blocks and {writer.transactions:,} previous transactions ({client.calls:,} requests)"
            )
        infos, versions, finished = {}, {}, {}
        for side in envs.SIDES:
            override = overrides.get(side, {})
            records = os.path.join(scratch, f"{side}.jsonl")
            job = {
                "mode": "compare",
                "capture_dir": os.path.abspath(capture_dir),
                "records_path": records,
                "source_dir": override.get("src"),
                "accounting_files": sorted(manifest["revisions"][side]["files"]),
                "partition_batch_size": PARTITION_BATCH_SIZE,
            }
            python = _revision_python(arguments.env_dir, side, override)
            ready, finished[side] = _run_revision(python, job, scratch, pieces, status, side, echo_stderr, override)
            infos[side], versions[side] = _revision_info(manifest, side, ready, override)
            echo(
                f"{side:<11} graphsense-lib {versions[side] or '(source tree)'}: {len(pieces)} pieces processed"
                + ("" if infos[side]["is_pinned_code"] else " — NOT the pinned code")
            )
        changes_path = os.path.join(report_dir, "changed-transactions.csv")
        compared = compare_records(
            os.path.join(scratch, "before.jsonl"), os.path.join(scratch, "after.jsonl"), changes_path
        )
    for side in envs.SIDES:
        if compared["unresolved_inputs"][side]:
            raise RangeError(f"{side}: {compared['unresolved_inputs'][side]} inputs stayed unresolved")
    canonical = all(infos[side]["is_pinned_code"] for side in envs.SIDES)
    results = {
        "summary_format": SUMMARY_FORMAT,
        "tool": report.tool_identity(),
        "range": {"start": start, "end": end, "blocks": end - start + 1},
        "canonical": canonical,
        "revisions": infos,
        **compared,
    }
    results_sha256 = report.checksum(results)
    published = _read_published(root, start, end) if canonical else None
    problems = []
    if compared["differences_explained"]["unexplained"]:
        problems.append(
            f"{compared['differences_explained']['unexplained']} transactions differ in a way the Orchard and "
            "Ironwood values do not explain"
        )
    if published is not None and published != results_sha256:
        problems.append("the results differ from the published summary for this range")
    document = {
        "results": results,
        "results_sha256": results_sha256,
        "published_results_sha256": published,
        "matches_published": None if published is None else published == results_sha256,
        "verdict": "fail" if problems else "pass",
        "problems": problems,
        "run": {
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "seconds": round(time.time() - started),
            "node": node_version,
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.machine()}",
            "graphsense_lib_versions": versions,
            "worker_answers": {side: finished[side]["answered"] for side in envs.SIDES},
        },
    }
    _check_schema(root, document, COMPARE_SCHEMA_PATH)
    paths = _write_summary(report_dir, document)
    echo("")
    for line in terminal_compare(document):
        echo(line)
    echo(f"report      {paths[0]}")
    echo(f"            {paths[1]}")
    echo(f"            {changes_path}")
    return 1 if problems else 0


def _check_schema(root, document, schema_path):
    problems = report.validate_report(root, document, schema_path)
    if problems:
        raise envs.SetupError(f"internal error: the summary does not follow {schema_path}: {problems[0]}")


def _read_published(root, start, end):
    try:
        with open(published_path(root, start, end), encoding="utf-8") as handle:
            return json.load(handle)["results_sha256"]
    except (OSError, ValueError, KeyError):
        return None


def _write_summary(directory, document):
    json_path = os.path.join(directory, "summary.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, indent=1, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    md_path = os.path.join(directory, "summary.md")
    with open(md_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown_compare(document))
    return json_path, md_path


def _n(value):
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else str(value)


def terminal_compare(document):
    r = document["results"]
    a = r["changed"]
    fees = r["fees_of_non_coinbase_transactions"]
    rel = r["relation_estimates"]
    cj = r["coinjoin_flags"]
    sets = r["clustering_input_sets_changed"]
    lines = [
        f"----- {PROG} compare-range -----",
        f"tool        {PROG} {__version__}",
        f"range       {r['range']['start']:,} to {r['range']['end']:,} ({r['range']['blocks']:,} blocks)",
        f"before      {revision_text(r['revisions']['before'])}",
        f"after       {revision_text(r['revisions']['after'])}",
        f"txs         {_n(r['transactions']['total'])} ({_n(r['transactions']['coinbase'])} coinbase)",
        (
            f"changed     {_n(a['transactions'])} transactions in {_n(a['blocks_with_changed_transactions'])} blocks "
            f"(fee {_n(a['fee'])}, total input {_n(a['total_input'])}, total output {_n(a['total_output'])})"
        ),
        (
            f"fees        negative {_n(fees['before']['negative'])} -> {_n(fees['after']['negative'])}, "
            f"zero {_n(fees['before']['zero'])} -> {_n(fees['after']['zero'])}"
        ),
    ]
    for pool in ("orchard", "ironwood"):
        p = r["newly_accounted"][pool]
        lines.append(
            f"{pool:<11} {_n(p['transactions'])} transactions; newly accounted {_n(p['out_of_pool'])} zat out of the pool, "
            f"{_n(p['into_pool'])} into it"
        )
    lines += [
        (
            f"coinjoin    {_n(cj['before'])} -> {_n(cj['after'])} flagged "
            f"({_n(cj['false_to_true'])} false->true, {_n(cj['true_to_false'])} true->false)"
        ),
        (
            f"clustering  input sets changed: {_n(sets['multi_input_address_set']['with_coinjoin_filter'])} with "
            f"coinjoin filter, {_n(sets['multi_input_address_set']['without_coinjoin_filter'])} without"
        ),
        (
            f"relations   {_n(rel['pairs_changed'])} of {_n(rel['pairs_after'])} pair estimates changed in "
            f"{_n(rel['transactions_with_changed_pairs'])} transactions (total change {_n(rel['absolute_change'])} zat)"
        ),
        f"explained   {_n(r['differences_explained']['unexplained'])} differences not explained by Orchard and Ironwood values",
        f"results     sha256 {document['results_sha256']}",
    ]
    if document["published_results_sha256"]:
        same = "identical" if document["matches_published"] else "DIFFERENT"
        lines.append(f"published   sha256 {document['published_results_sha256']} — {same}")
    verdict = "PASS" if document["verdict"] == "pass" else "FAIL"
    lines.append(f"verdict     {verdict}" + ("" if r["canonical"] else " for a NON-CANONICAL pair of revisions"))
    lines += [f"  - {problem}" for problem in document["problems"]]
    lines.append("-" * 34)
    return lines


def revision_text(info):
    found = info["found"]["package_tree_sha256"][:16]
    if info["is_pinned_code"]:
        return f"graphsense-lib {info['pinned']['version']} (commit {info['pinned']['git_commit'][:12]}), code checksum {found}… — pinned code"
    return f"code checksum {found}… — not the pinned {info['pinned']['version']}"


def markdown_compare(document):
    r = document["results"]
    lines = [
        f"# compare-range {r['range']['start']} to {r['range']['end']}",
        "",
        f"Produced by {PROG} {__version__}. Results checksum `{document['results_sha256']}`; verdict **{document['verdict']}**.",
        "",
        "```",
        *terminal_compare(document),
        "```",
        "",
        "## Results",
        "",
        "```json",
        json.dumps(r, indent=1, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(lines)


def scan(root, arguments, overrides, echo=print, status=None, echo_stderr=None):
    """List the blocks of a range whose transactions carry an Orchard or Ironwood value; returns the exit status.

    Both revisions parse every block, without resolving inputs. The corrected one gives the per-pool values; both
    give an upper bound of the coinjoin flag (worker.coinjoin_bound). A transaction can change its flag with the
    correction only if it carries an Orchard or Ironwood value and one of the two bounds is true: those are the
    candidates, to be decided exactly with compare-range on their blocks.
    """
    started = time.time()
    start, end = arguments.start, arguments.end
    check_range(start, end)
    manifest, _ = manifest_module.load(root)
    report_dir = arguments.report_dir or os.path.join(os.getcwd(), "report", f"scan-{start}-{end}")
    os.makedirs(report_dir, exist_ok=True)
    client = node_module.connect(arguments)
    info = client.call("getinfo", [])
    echo(f"node        {info.get('subversion')}, height {info.get('blocks')}")
    if isinstance(info.get("blocks"), int) and end > info["blocks"]:
        raise RangeError(f"the node is at height {info['blocks']}; the range ends at {end}")
    totals = {
        pool: {"blocks": 0, "transactions": 0, "out_of_pool": 0, "into_pool": 0} for pool in ("orchard", "ironwood")
    }
    affected = {"blocks": 0, "transactions": 0}
    counted = {"blocks": 0, "transactions": 0}
    possible = dict.fromkeys(envs.SIDES, 0)
    candidates = []
    blocks_path = os.path.join(report_dir, "blocks.csv")
    with tempfile.TemporaryDirectory(prefix="gs-zec-validate-", dir=report_dir) as scratch:
        capture_dir = os.path.join(scratch, "capture")
        writer = capture.Writer(capture_dir, start, end, compresslevel=0)
        sessions, infos, versions = {}, {}, {}
        try:
            for side in envs.SIDES:
                override = overrides.get(side, {})
                job = {
                    "mode": "scan",
                    "capture_dir": capture_dir,
                    "records_path": os.path.join(scratch, f"{side}.jsonl"),
                    "source_dir": override.get("src"),
                    "accounting_files": sorted(manifest["revisions"][side]["files"]),
                    "partition_batch_size": PARTITION_BATCH_SIZE,
                }
                worker = session.Session(_revision_python(arguments.env_dir, side, override), job, scratch, echo_stderr)
                sessions[side] = worker
                if not worker.ready.get("ready"):
                    raise _import_problem(side, worker.ready, override)
                infos[side], versions[side] = _revision_info(manifest, side, worker.ready, override)

            def process(low, high):
                for side, worker in sessions.items():
                    answer = worker.process(low, high)
                    if answer.get("exception"):
                        raise _failure(side, low, high, answer)
                capture.remove_blocks(capture_dir, low, high)

            capture.fetch(
                client,
                start,
                end,
                writer,
                previous_transactions=False,
                workers=arguments.rpc_workers,
                status=status,
                done=process,
            )
            finished = {side: worker.close() for side, worker in sessions.items()}
        finally:
            for worker in sessions.values():
                worker.kill()
        for side in envs.SIDES:
            if finished[side]["network"]["refused"]:
                raise RangeError(
                    f"{side}: GraphSense tried to reach the network: {finished[side]['network']['refused'][0]}"
                )
        names = [
            "height",
            "transactions",
            "affected_transactions",
            "orchard_transactions",
            "orchard_out_of_pool",
            "orchard_into_pool",
            "ironwood_transactions",
            "ironwood_out_of_pool",
            "ironwood_into_pool",
            "coinjoin_candidates",
        ]
        with open(blocks_path, "w", encoding="utf-8", newline="") as handle:
            out = csv.writer(handle, lineterminator="\n")
            out.writerow(names)
            before_records = _records(os.path.join(scratch, "before.jsonl"))
            for record in _records(os.path.join(scratch, "after.jsonl")):
                old = next(before_records, None)
                if old is None or old["height"] != record["height"] or old["transactions"] != record["transactions"]:
                    raise RangeError(f"the two revisions parsed block {record['height']} differently")
                if not record["exposes_pool_balances"]:
                    raise RangeError(
                        "the after revision does not expose per-pool balances (it predates the correction)"
                    )
                counted["blocks"] += 1
                counted["transactions"] += record["transactions"]
                possible["before"] += len(old["coinjoin_possible"])
                possible["after"] += len(record["coinjoin_possible"])
                either = set(old["coinjoin_possible"]) | set(record["coinjoin_possible"])
                found = [txid for txid in record["affected"] if txid in either]
                candidates += [[record["height"], txid] for txid in found]
                record["coinjoin_candidates"] = len(found)
                for pool in ("orchard", "ironwood"):
                    if record[f"{pool}_transactions"]:
                        totals[pool]["blocks"] += 1
                        totals[pool]["transactions"] += record[f"{pool}_transactions"]
                        totals[pool]["out_of_pool"] += record[f"{pool}_out_of_pool"]
                        totals[pool]["into_pool"] += record[f"{pool}_into_pool"]
                if record["affected_transactions"]:
                    affected["blocks"] += 1
                    affected["transactions"] += record["affected_transactions"]
                    out.writerow([record[name] for name in names])
            if next(before_records, None) is not None:
                raise RangeError("the before revision returned more blocks than the after revision")
    if counted["blocks"] != end - start + 1:
        raise RangeError(f"{counted['blocks']} blocks were processed, the range has {end - start + 1}")
    results = {
        "summary_format": SUMMARY_FORMAT,
        "tool": report.tool_identity(),
        "range": {"start": start, "end": end, "blocks": end - start + 1},
        "canonical": all(infos[side]["is_pinned_code"] for side in envs.SIDES),
        "revisions": infos,
        "transactions": counted["transactions"],
        "orchard": totals["orchard"],
        "ironwood": totals["ironwood"],
        "affected": affected,
        "coinjoin_screen": {
            "possible_before": possible["before"],
            "possible_after": possible["after"],
            "candidates": len(candidates),
            "candidate_transactions": candidates,
        },
    }
    document = {
        "results": results,
        "results_sha256": report.checksum(results),
        "run": {
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "seconds": round(time.time() - started),
            "node": info.get("subversion"),
            "graphsense_lib_versions": versions,
            "requests": client.calls,
            "python": platform.python_version(),
        },
    }
    _check_schema(root, document, SCAN_SCHEMA_PATH)
    json_path = os.path.join(report_dir, "summary.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, indent=1, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    echo("")
    echo(f"----- {PROG} scan -----")
    echo(f"tool        {PROG} {__version__}")
    echo(f"range       {start:,} to {end:,} ({end - start + 1:,} blocks, {counted['transactions']:,} transactions)")
    echo(f"before      {revision_text(infos['before'])}")
    echo(f"after       {revision_text(infos['after'])}")
    for pool in ("orchard", "ironwood"):
        t = totals[pool]
        echo(
            f"{pool:<11} {t['transactions']:,} transactions in {t['blocks']:,} blocks; {t['out_of_pool']:,} zat out of "
            f"the pool, {t['into_pool']:,} into it"
        )
    echo(f"affected    {affected['transactions']:,} transactions in {affected['blocks']:,} blocks")
    echo(
        f"coinjoin    {len(candidates):,} affected transactions could change their coinjoin flag "
        f"(flag possible: {possible['before']:,} before, {possible['after']:,} after)"
    )
    echo(f"results     sha256 {document['results_sha256']}")
    echo("-" * 22)
    echo(f"report      {json_path}")
    echo(f"            {blocks_path}")
    return 0
