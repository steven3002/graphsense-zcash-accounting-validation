"""One validation run, from the manifest to the report. No network: everything comes from the packaged evidence."""

import os
import platform
import sys
import time

from . import compare, envs, evidence, report
from . import manifest as manifest_module

EXIT_PASS = 0  # every required check passed
EXIT_FAIL = 1  # the validation ran and at least one required check failed
EXIT_USAGE = 2  # the command line was wrong
EXIT_SETUP = 3  # the validation could not run: manifest, evidence, environments or revisions are not as required
EXIT_REPORT = 4  # the report could not be written

PARTITION_BATCH_SIZE = 100_000  # what graphsense-lib's ingest uses for zec (ingest/dump.py, PARTITIONSIZES)


class ReportError(Exception):
    """The report directory cannot be written."""


def load_evidence(root, manifest):
    """Verify and parse every evidence file. Returns (blocks, previous, listing, runs).

    blocks:   height -> {capture name: parsed block}
    previous: txid -> parsed previous transaction
    listing:  what the report lists as the evidence used
    runs:     the work list for the workers, one entry per (block, capture)
    """
    blocks, previous, listing, runs = {}, {}, [], []
    files = {}
    for item in manifest["previous_transactions"]:
        entry = item["evidence"]
        previous[item["txid"]] = evidence.parse(evidence.read_verified(root, entry))
        files[item["txid"]] = entry
        listing.append(
            {
                "kind": "previous-transaction",
                "txid": item["txid"],
                "capture": entry["capture"],
                "path": entry["path"],
                "sha256": entry["sha256"],
                "content_sha256": entry["content_sha256"],
            }
        )
    for block in manifest["blocks"]:
        blocks[block["height"]] = {}
        for entry in block["evidence"]:
            blocks[block["height"]][entry["capture"]] = evidence.parse(evidence.read_verified(root, entry))
            listing.append(
                {
                    "kind": "block",
                    "height": block["height"],
                    "capture": entry["capture"],
                    "path": entry["path"],
                    "sha256": entry["sha256"],
                    "content_sha256": entry["content_sha256"],
                }
            )
            runs.append(
                {
                    "key": f"{block['height']}/{entry['capture']}",
                    "height": block["height"],
                    "block": {
                        "path": os.path.join(root, *entry["path"].split("/")),
                        "content_sha256": entry["content_sha256"],
                    },
                    "transactions": [
                        {
                            "txid": txid,
                            "path": os.path.join(root, *files[txid]["path"].split("/")),
                            "content_sha256": files[txid]["content_sha256"],
                        }
                        for txid in block["previous_transactions"]
                    ],
                }
            )
    listing.sort(key=lambda item: item["path"])
    return blocks, previous, listing, runs


def _check_writable(directory):
    try:
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, ".write-test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("")
        os.remove(probe)
    except OSError as error:
        raise ReportError(f"cannot write the report: {directory}: {error.strerror}") from None


def run(root, env_dir, report_dir, overrides, transformer=False, echo=print):
    """Run the validation and return the exit status.

    `overrides` maps "before"/"after" to {"python": path} or {"src": directory} for runs of other revisions.
    Raises ManifestError, IntegrityError, SetupError or ReportError when the validation cannot run at all.
    """
    started = time.time()
    _check_writable(report_dir)

    manifest, manifest_sha256 = manifest_module.load(root)
    echo(
        f"manifest    dataset {manifest['dataset']['version']}, {len(manifest['cases'])} cases in {len(manifest['blocks'])} blocks"
    )
    blocks, previous, listing, runs = load_evidence(root, manifest)
    echo(f"evidence    {len(listing)} files match the manifest's checksums")

    checks = compare.Checks()
    allowed_differences = compare.check_evidence(manifest, blocks, previous, checks)
    accounts, models, block_results = compare.check_expected(manifest, blocks, previous, checks)
    for block in block_results:  # how many allowed differences the two captures of a block show; null with one capture
        block["allowed_capture_differences"] = allowed_differences.get(block["height"])
    echo(
        f"expected    {checks['expected.reference'].checked} values recomputed from the evidence, {len(checks['expected.reference'].failures)} differ from the manifest"
    )

    workers, revisions, run_envs = {}, {}, {}
    for side in envs.SIDES:
        override = overrides.get(side, {})
        python = override.get("python") or envs.env_python(env_dir, side)
        pinned = manifest["revisions"][side]
        job = {
            "source_dir": override.get("src"),
            "transformer": transformer,
            "partition_batch_size": PARTITION_BATCH_SIZE,
            "accounting_files": sorted(pinned["files"]),
            "runs": runs,
        }
        echo(f"{side:<11} running GraphSense on {len(runs)} block captures …")
        result = envs.run_worker(python, job)
        workers[side] = result
        problem = (
            envs.identity_problem(pinned, result)
            if not override.get("src")
            else _source_identity_problem(pinned, result)
        )
        if problem and not override:
            raise envs.SetupError(
                f"{side}: {problem}. To run another revision on purpose, use --{side}-python or --{side}-src."
            )
        if result["transformer_import_error"] and not override:
            error = result["transformer_import_error"]
            raise envs.SetupError(
                f"{side}: this environment cannot run the transformer layer ({error['type']}: {error['message']}). "
                "Run the install step with --with-transformer first."
            )
        identity = result["identity"]
        revisions[side] = {
            "pinned": {
                "version": pinned["version"],
                "git_tag": pinned["git"]["tag"],
                "git_commit": pinned["git"]["commit"],
                "wheel_sha256": pinned["wheel"]["sha256"],
                "package_tree_sha256": pinned["package_tree_sha256"],
            },
            # Only what is the same for identical code however it was installed; the version label is in the run section.
            "found": None
            if identity is None
            else {k: identity[k] for k in ("package_tree_sha256", "package_file_count", "files")},
            "import_error": result["import_error"],
            "is_pinned_code": problem is None,
        }
        run_envs[side] = {
            "python": identity["python"] if identity else None,
            "graphsense_lib_version": identity["distribution_version"] if identity else None,
            "distributions": len(identity["distributions"]) if identity else 0,
            "third_party": identity["distributions"] if identity else {},
            "loopback_connections": result["network"]["loopback_connections"],
            "override": "python" if override.get("python") else ("src" if override.get("src") else None),
            "worker_stderr_lines": result["stderr_lines"],
        }
        if problem:
            echo(f"{side:<11} NOT the pinned code: {problem}")
            checks["revisions.identity"].applicable = False
        else:
            checks["revisions.identity"].ok()
    canonical = all(revisions[side]["is_pinned_code"] for side in envs.SIDES)

    before = (
        compare.collect(manifest, workers["before"], "before", checks) if workers["before"]["identity"] else ({}, {})
    )
    after = compare.collect(manifest, workers["after"], "after", checks) if workers["after"]["identity"] else ({}, {})
    for side in envs.SIDES:
        if workers[side]["import_error"]:
            error = workers[side]["import_error"]
            checks["graphsense.ran"].fail(
                f"{side}: graphsenselib could not be imported: {error['type']}: {error['message']}"
            )
    cases = compare.compare_cases(manifest, accounts, models, before, after, checks)

    check_list = checks.to_list()
    results = {
        "tool": report.tool_identity(),
        "manifest": {
            "path": manifest_module.MANIFEST_PATH,
            "sha256": manifest_sha256,
            "schema_version": manifest["manifest_schema_version"],
            "dataset_version": manifest["dataset"]["version"],
        },
        "canonical": canonical,
        "revisions": revisions,
        "evidence": listing,
        "checks": check_list,
        "blocks": block_results,
        "cases": cases,
        "summary": report.summarise(cases, check_list),
        "verdict": "fail" if checks.failed() else "pass",
    }
    transformer_results = None
    if transformer:
        rows, row_checks = compare.compare_transformer(manifest, workers["before"], workers["after"])
        transformer_results = {
            "tool": report.tool_identity(),
            "manifest_sha256": manifest_sha256,
            "canonical": canonical,
            "checks": row_checks,
            "cases": rows,
            "verdict": "fail" if any(check["status"] == "fail" for check in row_checks) else "pass",
        }
    run_facts = {
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "milliseconds": int((time.time() - started) * 1000),
        "python": platform.python_version(),
        "implementation": sys.implementation.name,
        "platform": f"{platform.system()} {platform.machine()}",
        "environments": run_envs,
    }
    document = report.build(results, transformer_results, run_facts, report.read_published(root), canonical)
    problems = report.validate_report(root, document)
    if problems:
        raise envs.SetupError(f"internal error: the report does not follow its schema: {problems[0]}")
    try:
        json_path, markdown_path = report.write(document, report_dir)
    except OSError as error:
        raise ReportError(f"cannot write the report: {report_dir}: {error.strerror}") from None

    echo("")
    echo(report.terminal(document))
    echo("")
    if canonical and not document["matches_published"]:
        echo(
            f"results checksum {document['results_sha256']} differs from the published one ({document['published_results_sha256'] or 'none found'})"
        )
    echo(f"report      {json_path}")
    echo(f"            {markdown_path}")
    echo("")
    echo(report.confirmation(document))
    return document["exit_status"]


def _source_identity_problem(pinned, result):
    """For a source directory there is no installed distribution; the files alone decide whether it is the pinned code."""
    if result["import_error"]:
        return (
            f"graphsenselib cannot be imported: {result['import_error']['type']}: {result['import_error']['message']}"
        )
    identity = result["identity"]
    if identity["package_tree_sha256"] != pinned["package_tree_sha256"]:
        return (
            f"the graphsenselib files differ from the pinned release {pinned['version']} "
            f"(code checksum {identity['package_tree_sha256'][:16]}…, pinned {pinned['package_tree_sha256'][:16]}…)"
        )
    return None
