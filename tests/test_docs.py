"""The documentation quotes numbers and names. These tests keep them equal to the manifest, the code and the published checksum."""

import hashlib
import json
import os
import re

from conftest import ROOT, edit_manifest
from gs_zec_validate import __version__, validate
from gs_zec_validate import report as report_module
from gs_zec_validate.compare import FIELDS, TITLES


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_published_checksums_are_quoted_correctly():
    published = report_module.read_published(ROOT)
    for page in ("README.md", "docs/REVIEWING.md", "docs/REPORT.md", "CHANGELOG.md"):
        quoted = set(re.findall(r"\b[0-9a-f]{64}\b", read(*page.split("/"))))
        assert published["results"] in quoted, page
    assert published["transformer-results"] in read("docs", "REPORT.md") and published["transformer-results"] in read(
        "CHANGELOG.md"
    )


def test_readme_quotes_the_manifest_checksum_and_the_versions(manifest):
    readme = read("README.md")
    with open(os.path.join(ROOT, "manifest", "manifest.json"), "rb") as handle:
        assert hashlib.sha256(handle.read()).hexdigest() in readme
    assert f"gs-zec-validate {__version__}" in readme and f"dataset     {manifest['dataset']['version']}" in readme
    for side in ("before", "after"):
        revision = manifest["revisions"][side]
        assert (
            f"graphsense-lib {revision['version']} ({revision['git']['tag']}, commit {revision['git']['commit'][:12]})"
            in readme
        )
        assert revision["package_tree_sha256"][:16] in readme
        assert revision["package_tree_sha256"] in read("docs", "METHOD.md") and revision["git"]["commit"] in read(
            "docs", "METHOD.md"
        )


def test_counts_in_the_documentation(manifest, fake_run):
    cases, blocks, previous = len(manifest["cases"]), len(manifest["blocks"]), len(manifest["previous_transactions"])
    affected = sum(case["old_code"]["differs"] for case in manifest["cases"])
    assert (cases, blocks, previous, affected) == (47, 10, 43, 13)
    readme = read("README.md")
    assert (
        "47 real mainnet transactions" in readme and "the 43 earlier" in readme and "ten Zcash mainnet blocks" in readme
    )
    assert "before differs on 13, 13 of them explained" in readme
    reviewing = read("docs", "REVIEWING.md")
    assert "exactly 13 cases" in reviewing and "the other 34 cases" in reviewing
    _, report, _ = fake_run()
    units = next(check for check in report["results"]["checks"] if check["id"] == "evidence.units")
    assert f"{units['checked']:,} such pairs" in read("docs", "METHOD.md")
    files = sum(len(block["evidence"]) for block in manifest["blocks"]) + previous
    assert f"all {files} files" in read("docs", "EVIDENCE.md")


def test_versions_agree(manifest):
    assert re.search(rf'^version = "{re.escape(__version__)}"$', read("pyproject.toml"), re.MULTILINE)
    changelog = read("CHANGELOG.md")
    assert (
        f"## [{__version__}]" in changelog
        and f"Dataset {manifest['dataset']['version']} ({manifest['dataset']['date']})" in changelog
    )
    assert changelog.index("## [Unreleased]") < changelog.index(f"## [{__version__}]")


def test_the_two_cut_off_dates_are_the_same():
    """`locks/regenerate.sh` resolves as of CUTOFF; uv applies `exclude-newer` of pyproject.toml to `install` as well."""
    in_pyproject = re.findall(r'^exclude-newer = "(.+)"$', read("pyproject.toml"), re.MULTILINE)
    in_script = re.findall(r"^CUTOFF=(.+)$", read("locks", "regenerate.sh"), re.MULTILINE)
    assert len(in_pyproject) == 1 and in_pyproject == in_script, (in_pyproject, in_script)


def test_later_revisions_in_the_readme_are_the_ones_the_workflow_runs():
    readme = read("README.md")
    section = readme[readme.index("**Other revisions.**") : readme.index("## More documentation")]
    workflow = read(".github", "workflows", "validate.yml")
    listed = set(re.findall(r"\b[0-9a-f]{40}\b", section))
    assert listed and listed == set(re.findall(r"^ +ref: ([0-9a-f]{40})$", workflow, re.MULTILINE))


def test_dataset_page_matches_the_manifest(manifest):
    page = read("docs", "DATASET.md")
    rows = {}
    section = page[page.index("## All cases") : page.index("## Descriptions")]
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 14 and re.fullmatch(r"\d+/\d+", cells[0]):
            rows[cells[0]] = cells
    assert len(rows) == len(manifest["cases"])
    number = lambda value: f"{value:,}"
    for case in manifest["cases"]:
        expected = case["expected"]
        wanted = [case["id"], f"`{case['txid']}`", str(case["version"]), ", ".join(case["shapes"])]
        wanted += [
            number(expected[name])
            for name in (
                "transparent_input",
                "transparent_output",
                "sprout_value_balance",
                "sapling_value_balance",
                "orchard_value_balance",
                "ironwood_value_balance",
                "total_input",
                "total_output",
                "fee",
            )
        ]
        wanted.append("differs" if case["old_code"]["differs"] else "same")
        assert rows[case["id"]] == wanted, case["id"]
        assert f"- `{case['id']}` {case['description']}" in page
    for block in manifest["blocks"]:
        assert (
            f"| {block['height']} | `{block['hash']}` | {block['transaction_count']} | {len(block['previous_transactions'])} | {number(block['expected']['fees'])} |"
            in page
        )
    for shape in manifest["required_shapes"]:
        assert f"| `{shape}` |" in page


def test_every_manifest_member_is_documented():
    with open(os.path.join(ROOT, "manifest", "manifest.schema.json"), encoding="utf-8") as handle:
        schema = json.load(handle)
    names = set()

    def collect(node):
        if isinstance(node, dict):
            names.update(node.get("properties", {}))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(schema)
    page = read("docs", "MANIFEST.md")
    missing = sorted(
        name for name in names if f"`{name}`" not in page and f"`{name}" not in page and f".{name}`" not in page
    )
    assert missing == [], missing


def test_every_check_and_field_is_documented():
    page = read("docs", "REPORT.md")
    for check_id in TITLES:
        assert f"| `{check_id}` |" in page, check_id
    for field in FIELDS:
        assert f"`{field}`" in page, field
    for name in ("transformer.ran", "transformer.totals", "transformer.coinjoin"):
        assert f"`{name}`" in page


def test_exit_status_table():
    assert (validate.EXIT_PASS, validate.EXIT_FAIL, validate.EXIT_USAGE, validate.EXIT_SETUP, validate.EXIT_REPORT) == (
        0,
        1,
        2,
        3,
        4,
    )
    for page in (read("README.md"), read("docs", "REPORT.md")):
        for status in range(5):
            assert re.search(rf"^\| {status} \| ", page, re.MULTILINE)


def test_no_placeholders_left_in_the_documentation():
    for folder, _, names in os.walk(ROOT):
        if any(
            part.startswith(".") and part not in (".", ".github")
            for part in os.path.relpath(folder, ROOT).split(os.sep)
        ):
            continue
        for name in names:
            if name.endswith(".md"):
                text = read(os.path.relpath(os.path.join(folder, name), ROOT))
                assert not re.search(r"\b(TODO|TBD|FIXME|XXX)\b", text), name


def test_the_pass_block_in_the_readme_is_what_a_run_prints(fake_run):
    _, report, _ = fake_run()
    printed = report_module.confirmation(report).splitlines()
    readme = read("README.md")
    block = readme[readme.index("----- gs-zec-validate confirmation -----") :].split("```")[0].strip().splitlines()
    assert len(block) == len(printed)
    for shown, real in zip(block, printed, strict=True):
        if not shown.startswith("python "):  # the only line that differs from machine to machine
            assert shown == real


def test_the_failure_lines_quoted_in_the_documentation_are_real(fake_run, copy_of_repo):
    _, _, text = fake_run(swap={"after": "before"}, overrides={"after": {"python": "/x/after/bin/python"}})
    readme = read("README.md")
    quoted = readme[readme.index("  [FAIL] Totals:") :].split("```")[0].rstrip().splitlines()
    assert len(quoted) == 5 and all(line in text for line in quoted)

    def change(document):
        next(case for case in document["cases"] if case["id"] == "3463373/2")["expected"]["fee"] = 30001

    edit_manifest(copy_of_repo, change)
    _, _, text = fake_run(root=copy_of_repo)
    reviewing = read("docs", "REVIEWING.md")
    quoted = reviewing[reviewing.index("case 3463373/2 (6f37465a") :].split("```")[0].strip().splitlines()
    assert len(quoted) == 3 and all(line in text for line in quoted)


def _schema_members(path):
    with open(os.path.join(ROOT, *path.split("/")), encoding="utf-8") as handle:
        schema = json.load(handle)
    names = set()

    def collect(node):
        if isinstance(node, dict):
            names.update(node.get("properties", {}))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(schema)
    return names


def test_every_member_of_the_operator_outputs_is_documented():
    page = read("docs", "OPERATOR-TOOLS.md")
    for path in (
        "manifest/compare-summary.schema.json",
        "manifest/scan-summary.schema.json",
        "manifest/check-report.schema.json",
    ):
        missing = sorted(name for name in _schema_members(path) if f"`{name}" not in page and f".{name}`" not in page)
        assert missing == [], (path, missing)


def test_every_check_rule_and_exit_status_is_documented():
    from gs_zec_validate import checks, cli

    tools, guide = read("docs", "OPERATOR-TOOLS.md"), read("docs", "OPERATOR-GUIDE.md")
    for rule in checks.RULES:
        assert f"| `{rule}` |" in tools, rule
    sections = {heading: body for heading, body in re.findall(r"^## (.+)\n((?:(?!^## ).*\n)*)", tools, re.MULTILINE)}
    for command, codes in (
        ("compare-range", cli.RANGE_EXIT_CODES),
        ("scan", cli.SCAN_EXIT_CODES),
        ("check", cli.CHECK_EXIT_CODES),
    ):
        documented = re.findall(r"^\| (\d) \| ", sections[command], re.MULTILINE)
        assert documented == re.findall(r"^  (\d)  ", codes, re.MULTILINE), command
    assert "check" in guide and "compare-range" in guide and "scan" in guide


def test_the_readme_points_to_the_operator_pages():
    readme = read("README.md")
    assert "docs/OPERATOR-GUIDE.md" in readme and "docs/OPERATOR-TOOLS.md" in readme


def test_no_unfinished_markers_in_the_documentation():
    for folder in (".", "docs", "results"):
        for name in sorted(os.listdir(os.path.join(ROOT, folder))):
            if name.endswith((".md", ".json")):
                assert "@@" not in read(folder, name), (folder, name)


def _range_label(results):
    return f"{results['range']['start']:,} – {results['range']['end']:,}"


def results_tables(root=ROOT):
    """The two tables of section 7 of the operator guide, built from the published compare-range summaries."""
    documents = []
    folder = os.path.join(root, "results")
    for name in sorted(os.listdir(folder)):
        if name.startswith("compare-") and name.endswith(".json"):
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                documents.append(json.load(handle)["results"])
    documents.sort(key=lambda results: results["range"]["start"])

    def legs(pool):
        return f"{pool['out_of_pool']:,} out, {pool['into_pool']:,} in" if pool["transactions"] else "none"

    first = [
        "| Window | Transactions | Changed | Negative fees | Zero fees | Orchard newly accounted (zat) | Ironwood newly accounted (zat) |",
        "|---|---:|---:|---|---|---|---|",
    ]
    second = [
        "| Window | Coinjoin-flagged, before → after | Flags changed | Clustering input sets changed | Relation estimates changed | Their total change (zat) |",
        "|---|---|---:|---:|---|---:|",
    ]
    for results in documents:
        fees = results["fees_of_non_coinbase_transactions"]
        flags = results["coinjoin_flags"]
        sets = results["clustering_input_sets_changed"]["multi_input_address_set"]
        relations = results["relation_estimates"]
        first.append(
            f"| {_range_label(results)} | {results['transactions']['total']:,} | {results['changed']['transactions']:,} "
            f"| {fees['before']['negative']:,} → {fees['after']['negative']:,} | {fees['before']['zero']:,} → "
            f"{fees['after']['zero']:,} | {legs(results['newly_accounted']['orchard'])} | "
            f"{legs(results['newly_accounted']['ironwood'])} |"
        )
        second.append(
            f"| {_range_label(results)} | {flags['before']:,} → {flags['after']:,} | "
            f"{flags['false_to_true'] + flags['true_to_false']:,} | "
            f"{sets['with_coinjoin_filter']:,} with filter, {sets['without_coinjoin_filter']:,} without | "
            f"{relations['pairs_changed']:,} of {relations['pairs_after']:,} | {relations['absolute_change']:,} |"
        )
    return "\n".join(first) + "\n\n" + "\n".join(second)


def test_the_guide_shows_the_published_results():
    assert results_tables() in read("docs", "OPERATOR-GUIDE.md")


def test_the_published_summaries_are_valid_and_canonical():
    folder = os.path.join(ROOT, "results")
    names = sorted(os.listdir(folder))
    assert any(name.startswith("compare-") for name in names) and any(name.startswith("scan-") for name in names)
    for name in names:
        with open(os.path.join(folder, name), encoding="utf-8") as handle:
            document = json.load(handle)
        kind = "compare-summary" if name.startswith("compare-") else "scan-summary"
        assert report_module.validate_report(ROOT, document, f"manifest/{kind}.schema.json") == [], name
        assert report_module.checksum(document["results"]) == document["results_sha256"], name
        assert document["results"]["canonical"] is True and document["results"]["tool"]["version"] == __version__, name
        if kind == "compare-summary":
            assert document["verdict"] == "pass", name
            start, end = document["results"]["range"]["start"], document["results"]["range"]["end"]
            assert name == f"compare-{start}-{end}.json"
