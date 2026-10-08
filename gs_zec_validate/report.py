"""The report of a validation run: machine-readable JSON, its checksum, and the human-readable renderings.

`report.json` has two kinds of content, kept apart on purpose:

  "results" (and "transformer_results")   what was checked and what came out. Identical, byte for byte, on every
                                          correct run, on any machine. Its sha256 is the "results checksum".
  "run"                                   facts about this particular run: time, platform, Python, durations.

The results checksum is the sha256 of `results` written as JSON with sorted keys, no spaces and ASCII only.
`RESULTS.sha256` in the repository holds the checksum a correct run of the pinned revisions must produce.
"""

import hashlib
import json
import os

from . import PROG, __version__, schema
from .compare import BALANCE_FIELDS, FIELDS, TOTAL_FIELDS, short

REPORT_SCHEMA_PATH = "manifest/report.schema.json"
PUBLISHED_PATH = "RESULTS.sha256"
REPORT_SCHEMA_VERSION = 1


def canonical(document):
    """The one serialisation the checksum is defined on."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def checksum(document):
    return hashlib.sha256(canonical(document)).hexdigest()


def read_published(root):
    """Read RESULTS.sha256: lines "<sha256>  <name>". Returns {name: sha256}; empty if the file is absent."""
    published = {}
    try:
        with open(os.path.join(root, PUBLISHED_PATH), encoding="ascii") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) == 2 and not line.startswith("#"):
                    published[parts[1]] = parts[0]
    except OSError:
        pass
    return published


def summarise(cases, checks):
    """Counts for the summary block."""
    after_compared = sum(1 for case in cases for row in case["fields"].values() if row["after_matches"] is not None)
    after_matched = sum(1 for case in cases for row in case["fields"].values() if row["after_matches"])
    before_compared = sum(1 for case in cases for row in case["fields"].values() if row["before_matches"] is not None)
    before_matched = sum(1 for case in cases for row in case["fields"].values() if row["before_matches"])
    differing = [case for case in cases if case["before"]["differs_from_expected"]]
    return {
        "cases": len(cases),
        "fields_per_case": len(FIELDS),
        "after": {
            "fields_compared": after_compared,
            "fields_matching": after_matched,
            "fields_mismatching": after_compared - after_matched,
            "cases_matching": sum(1 for case in cases if case["after"]["matches_expected"]),
            "cases_mismatching": sorted(case["id"] for case in cases if not case["after"]["matches_expected"]),
        },
        "before": {
            "fields_compared": before_compared,
            "fields_matching": before_matched,
            "fields_mismatching": before_compared - before_matched,
            "cases_differing_from_expected": sorted(case["id"] for case in differing),
            "cases_differing_explained": sum(1 for case in differing if case["before"]["explained"]),
            "cases_differing_unexplained": sorted(case["id"] for case in differing if not case["before"]["explained"]),
            "cases_expected_to_differ": sorted(case["id"] for case in cases if case["old_code_expected_to_differ"]),
        },
        "checks": {
            "passed": sum(1 for check in checks if check["status"] == "pass"),
            "failed": sorted(check["id"] for check in checks if check["status"] == "fail"),
            "not_applicable": sorted(check["id"] for check in checks if check["status"] == "not-applicable"),
        },
    }


def validate_report(root, report):
    """Validate the report against its published schema. Returns a list of problems."""
    with open(os.path.join(root, *REPORT_SCHEMA_PATH.split("/")), encoding="utf-8") as handle:
        rules = json.load(handle)
    return schema.validate(report, rules)


def write(report, directory):
    """Write report.json and report.md. Raises OSError if the directory cannot be written."""
    os.makedirs(directory, exist_ok=True)
    json_path = os.path.join(directory, "report.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    markdown_path = os.path.join(directory, "report.md")
    with open(markdown_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown(report))
    return json_path, markdown_path


# Renderings


def _n(value):
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, list):
        return "[" + ", ".join(_n(item) for item in value) + "]"
    return str(value)


def case_status(case):
    """(before, after) status words for one case."""
    if case["after"]["exception"]:
        after = "EXCEPTION"
    else:
        after = "ok" if case["after"]["matches_expected"] else "MISMATCH"
    before = case["before"]
    if before["exception"]:
        old = "EXCEPTION"
    elif not before["matches_frozen"]:
        old = "CHANGED"
    elif before["differs_from_expected"]:
        old = "wrong (explained)" if before["explained"] else "wrong (UNEXPLAINED)"
    else:
        old = "ok" if before["explained"] else "UNEXPLAINED"
    return old, after


def revision_line(side, info, version_found=None):
    """One line that says which code ran for a side. `version_found` is the installed version label, if any."""
    pinned = info["pinned"]
    found = info["found"]
    if found is None:
        return f"{side:<11} graphsenselib could not be imported — NOT the pinned code (pinned: {pinned['version']}, {pinned['git_tag']})"
    code = f"code checksum {found['package_tree_sha256'][:16]}…"
    if info["is_pinned_code"]:
        return f"{side:<11} graphsense-lib {pinned['version']} ({pinned['git_tag']}, commit {pinned['git_commit'][:12]}), {code} — pinned code"
    label = f"graphsense-lib {version_found}" if version_found else "graphsenselib from a source directory"
    return f"{side:<11} {label}, {code} — NOT the pinned code (pinned: {pinned['version']}, {pinned['git_tag']})"


def terminal(report):
    """The short summary printed at the end of a run."""
    results = report["results"]
    lines = []
    lines.append(
        f"{'case':<11} {'transaction':<14} {'shape':<34} {'expected fee':>13} {'old fee':>13} {'new fee':>13}  {'old':<18} new"
    )
    for case in results["cases"]:
        fee = case["fields"]["fee"]
        old, new = case_status(case)
        shape = ",".join(case["shapes"])
        if len(shape) > 34:
            shape = shape[:33] + "…"
        lines.append(
            f"{case['id']:<11} {short(case['txid']):<14} {shape:<34} {_n(fee['expected']):>13} {_n(fee['before']):>13} {_n(fee['after']):>13}  {old:<18} {new}"
        )
    lines.append("")
    for check in results["checks"]:
        status = {"pass": "pass", "fail": "FAIL", "not-applicable": "n/a "}[check["status"]]
        lines.append(
            f"  [{status}] {check['title']} ({check['checked']} checked"
            + (f", {check['failed']} failed)" if check["failed"] else ")")
        )
        for failure in check["failures"][:8]:
            lines.append(f"           - {failure}")
        if len(check["failures"]) > 8:
            lines.append(f"           - … and {len(check['failures']) - 8} more (see report.json)")
    transformer = report.get("transformer_results")
    if transformer:
        lines.append("")
        lines.append("  Further layer: GraphSense's transformer (stored rows)")
        for check in transformer["checks"]:
            status = {"pass": "pass", "fail": "FAIL"}[check["status"]]
            lines.append(
                f"  [{status}] {check['title']} ({check['checked']} checked"
                + (f", {check['failed']} failed)" if check["failed"] else ")")
            )
            for failure in check["failures"][:8]:
                lines.append(f"           - {failure}")
    return "\n".join(lines)


def confirmation(report):
    """A block of plain text a reviewer can paste as their confirmation. Nothing in it identifies a machine or a user."""
    results, run = report["results"], report["run"]
    summary = results["summary"]
    lines = [
        f"----- {PROG} confirmation -----",
        f"tool        {results['tool']['name']} {results['tool']['version']}",
        f"dataset     {results['manifest']['dataset_version']} (manifest sha256 {results['manifest']['sha256']})",
        revision_line(
            "before", results["revisions"]["before"], run["environments"]["before"]["graphsense_lib_version"]
        ),
        revision_line("after", results["revisions"]["after"], run["environments"]["after"]["graphsense_lib_version"]),
        (
            f"cases       {summary['cases']}; after matches the expected values on {summary['after']['cases_matching']}; "
            f"before differs on {len(summary['before']['cases_differing_from_expected'])}, {summary['before']['cases_differing_explained']} of them explained"
        ),
        f"results     sha256 {report['results_sha256']}",
    ]
    if results["canonical"]:
        published = report["published_results_sha256"]
        same = "identical" if report["matches_published"] else "DIFFERENT"
        lines.append(f"published   sha256 {published or 'none found'} — {same}")
    else:
        lines.append("published   not compared: NON-CANONICAL RUN (not the pinned pair of revisions)")
    if report.get("transformer_results"):
        same = {True: "identical", False: "DIFFERENT", None: "not compared"}[report["transformer_matches_published"]]
        lines.append(
            f"transformer {report['transformer_results']['verdict'].upper()}, sha256 {report['transformer_results_sha256']} — published: {same}"
        )
    scope = "" if results["canonical"] else " for a NON-CANONICAL pair of revisions"
    lines.append(f"verdict     {report['verdict'].upper()}{scope} (exit status {report['exit_status']})")
    lines.append(
        f"python      {run['python']} ({run['implementation']}) on {run['platform']}; environments: before {run['environments']['before']['python']}, after {run['environments']['after']['python']}"
    )
    lines.append("-" * len(lines[0]))
    return "\n".join(lines)


def markdown(report):
    """The whole report as a Markdown document."""
    results = report["results"]
    summary = results["summary"]
    out = [f"# {PROG} report", ""]
    out.append(f"**Verdict: {report['verdict'].upper()}** (exit status {report['exit_status']})")
    failed_checks = [check["title"] for check in results["checks"] if check["status"] == "fail"]
    if failed_checks:
        out += ["", "Failed checks:", ""] + [f"- {title}" for title in failed_checks]
    if results["canonical"] and not report["matches_published"]:
        out += ["", "The results checksum differs from the published one."]
    if not results["canonical"]:
        out.append("")
        out.append(
            "**NON-CANONICAL RUN.** At least one side did not run the pinned revision. This result says nothing about the pinned pair."
        )
    out += ["", "## What was compared", ""]
    out.append(f"- Tool: {results['tool']['name']} {results['tool']['version']}")
    out.append(
        f"- Dataset: {results['manifest']['dataset_version']}, manifest sha256 `{results['manifest']['sha256']}`"
    )
    for side in ("before", "after"):
        line = revision_line(
            side, results["revisions"][side], report["run"]["environments"][side]["graphsense_lib_version"]
        )
        out.append(f"- {side}: {line[len(side) :].strip()}")
    out.append(f"- Evidence: {len(results['evidence'])} files (listed with checksums in report.json)")
    out.append(f"- Results checksum: `{report['results_sha256']}`")
    if results["canonical"]:
        out.append(
            f"- Published checksum: `{report['published_results_sha256']}` — {'identical' if report['matches_published'] else 'DIFFERENT'}"
        )
    out += ["", "## Checks", "", "| Result | Check | Checked | Failed |", "|---|---|---:|---:|"]
    for check in results["checks"]:
        out.append(f"| {check['status']} | {check['title']} | {check['checked']} | {check['failed']} |")
    failures = [(check["title"], failure) for check in results["checks"] for failure in check["failures"]]
    if failures:
        out += ["", "### Failures", ""]
        out += [f"- {title}: {failure}" for title, failure in failures]
    out += ["", "## Summary", ""]
    out.append(f"- Cases: {summary['cases']}, {summary['fields_per_case']} fields each.")
    out.append(
        f"- After revision: {summary['after']['fields_matching']} of {summary['after']['fields_compared']} compared fields match; "
        f"{summary['after']['cases_matching']} of {summary['cases']} cases match in every field."
    )
    out.append(
        f"- Before revision: {summary['before']['fields_matching']} of {summary['before']['fields_compared']} compared fields match; it differs from the expected values on "
        f"{len(summary['before']['cases_differing_from_expected'])} cases, {summary['before']['cases_differing_explained']} of them explained."
    )
    out += [
        "",
        "## Cases",
        "",
        'Amounts in zatoshi. "old" is the before revision, "new" the after revision. A dash means the revision reports no such value.',
        "",
    ]
    out.append(
        "| Case | Transaction | Shapes | Fee: expected | old | new | Total input: expected | old | new | Total output: expected | old | new | Old | New |"
    )
    out.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for case in results["cases"]:
        f = case["fields"]
        old, new = case_status(case)
        cells = [case["id"], f"`{case['txid']}`", ", ".join(case["shapes"])]
        for name in ("fee", "total_input", "total_output"):
            cells += [_n(f[name]["expected"]), _n(f[name]["before"]), _n(f[name]["after"])]
        out.append("| " + " | ".join(cells + [old, new]) + " |")
    note = (
        "Signed balance per pool: positive means value left the pool. Only transactions with a non-zero balance are listed. "
        'The old GraphSense revision exposes no per-pool values, so there is no "old" column.'
    )
    out += ["", "## Pool balances", "", note, ""]
    out.append(
        "| Case | Sprout: expected | new | Sapling: expected | new | Orchard: expected | new | Ironwood: expected | new |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for case in results["cases"]:
        if any(case["fields"][name]["expected"] for name in BALANCE_FIELDS):
            cells = [case["id"]]
            for name in BALANCE_FIELDS:
                cells += [_n(case["fields"][name]["expected"]), _n(case["fields"][name]["after"])]
            out.append("| " + " | ".join(cells) + " |")
    differing = [case for case in results["cases"] if case["before"]["differs_from_expected"]]
    out += ["", "## Where the before revision differs from the expected values", ""]
    if differing:
        out.append("| Case | Field | Expected | Before revision | Explanation |")
        out.append("|---|---|---:|---:|---|")
        for case in differing:
            for name in TOTAL_FIELDS + ("shielded_inputs", "shielded_outputs"):
                row = case["fields"][name]
                if row["before_matches"] is False:
                    out.append(
                        f"| {case['id']} | {name} | {_n(row['expected'])} | {_n(row['before'])} | {case['before']['explanation'] or 'UNEXPLAINED'} |"
                    )
    else:
        out.append("The before revision reports the expected values on every case.")
    out += [
        "",
        "## Blocks",
        "",
        "| Height | Transactions | Fees | Value created | Subsidy | Rule | Holds |",
        "|---:|---:|---:|---:|---:|---|---|",
    ]
    for block in results["blocks"]:
        out.append(
            f"| {block['height']} | {block['transactions']} | {_n(block['fees'])} | {_n(block['value_created'])} | {_n(block['subsidy'])} | {block['rule']} | {_n(block['rule_holds'])} |"
        )
    transformer = report.get("transformer_results")
    if transformer:
        out += ["", "## Further layer: GraphSense's transformer (stored rows)", ""]
        out.append(
            f"Verdict of this layer: **{transformer['verdict'].upper()}**; checksum `{report['transformer_results_sha256']}`."
        )
        out += ["", "| Result | Check | Checked | Failed |", "|---|---|---:|---:|"]
        for check in transformer["checks"]:
            out.append(f"| {check['status']} | {check['title']} | {check['checked']} | {check['failed']} |")
        for check in transformer["checks"]:
            out += [f"- {failure}" for failure in check["failures"]]
        flagged = [
            case for case in transformer["cases"] if any(row and row["coinjoin"] for row in case["rows"].values())
        ]
        out += ["", f"Rows with the coinjoin flag set by either revision: {len(flagged)}."]
    run = report["run"]
    out += ["", "## This run", ""]
    out.append(f"- Started {run['started_utc']}, took {run['milliseconds']} milliseconds.")
    out.append(f"- Python {run['python']} ({run['implementation']}) on {run['platform']}.")
    for side in ("before", "after"):
        env = run["environments"][side]
        out.append(
            f"- Environment {side}: Python {env['python']}, {env['distributions']} installed distributions; "
            f"{env['loopback_connections']} connections to the replay server on 127.0.0.1."
        )
    out.append("")
    return "\n".join(out)


def build(tool_results, transformer_results, run_facts, published, canonical_run):
    """Assemble the final report document and decide the verdict."""
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "results": tool_results,
        "results_sha256": checksum(tool_results),
        "published_results_sha256": published.get("results") if canonical_run else None,
        "matches_published": None,
        "run": run_facts,
    }
    passed = tool_results["verdict"] == "pass"
    if canonical_run:
        report["matches_published"] = report["results_sha256"] == published.get("results")
        passed = passed and report["matches_published"]
    if transformer_results is not None:
        report["transformer_results"] = transformer_results
        report["transformer_results_sha256"] = checksum(transformer_results)
        report["transformer_matches_published"] = (
            (report["transformer_results_sha256"] == published.get("transformer-results")) if canonical_run else None
        )
        passed = (
            passed and transformer_results["verdict"] == "pass" and report["transformer_matches_published"] is not False
        )
    report["verdict"] = "pass" if passed else "fail"
    report["exit_status"] = 0 if passed else 1
    return report


def tool_identity():
    return {"name": PROG, "version": __version__}
