"""The report: its checksum, its schema, and what the human-readable forms may and may not contain."""

import getpass
import hashlib
import json
import os
import re
import socket

from conftest import ROOT
from gs_zec_validate import __version__
from gs_zec_validate import report as report_module


def test_checksum_definition():
    """sha256 over the JSON text with sorted keys, no spaces, ASCII only."""
    document = {"b": [1, 2], "a": {"z": None, "y": "é"}}
    text = '{"a":{"y":"\\u00e9","z":null},"b":[1,2]}'
    assert report_module.canonical(document) == text.encode("ascii")
    assert report_module.checksum(document) == hashlib.sha256(text.encode("ascii")).hexdigest()


def test_results_are_identical_on_every_run_and_the_run_section_is_not_part_of_the_checksum(fake_run):
    _, first, _ = fake_run()
    _, second, _ = fake_run()
    assert first["results"] == second["results"] and first["results_sha256"] == second["results_sha256"]
    assert report_module.checksum(first["results"]) == first["results_sha256"]
    changed = dict(first, run=dict(first["run"], started_utc="1970-01-01T00:00:00Z", platform="Other", python="9.9"))
    assert report_module.checksum(changed["results"]) == first["results_sha256"]


def test_checksum_can_be_recomputed_from_the_written_file(fake_run, tmp_path):
    _, document, _ = fake_run()
    with open(tmp_path / "report" / "report.json", encoding="utf-8") as handle:
        written = json.load(handle)
    recomputed = hashlib.sha256(
        json.dumps(written["results"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    assert recomputed == document["results_sha256"] == written["results_sha256"]


def test_report_follows_its_schema_also_when_the_run_fails(fake_run):
    def tamper(side, output):
        output["runs"][0].update(exception={"type": "ValueError", "message": "boom"}, transactions=None)

    for kwargs in ({}, {"transformer": True}, {"tamper": tamper}):
        _, document, _ = fake_run(**kwargs)
        assert report_module.validate_report(ROOT, document) == []


def test_report_identifies_revisions_and_evidence(fake_run, manifest):
    _, document, _ = fake_run()
    results = document["results"]
    assert results["tool"] == {"name": "gs-zec-validate", "version": __version__}
    assert (
        results["manifest"]["dataset_version"] == manifest["dataset"]["version"]
        and len(results["manifest"]["sha256"]) == 64
    )
    for side in ("before", "after"):
        pinned = manifest["revisions"][side]
        info = results["revisions"][side]
        assert info["pinned"] == {
            "version": pinned["version"],
            "git_tag": pinned["git"]["tag"],
            "git_commit": pinned["git"]["commit"],
            "wheel_sha256": pinned["wheel"]["sha256"],
            "package_tree_sha256": pinned["package_tree_sha256"],
        }
        assert info["found"]["package_tree_sha256"] == pinned["package_tree_sha256"] and info["is_pinned_code"] is True
        assert (
            info["found"]["files"]["graphsenselib/ingest/rpc_utxo.py"]
            == pinned["files"]["graphsenselib/ingest/rpc_utxo.py"]
        )
    assert len(results["evidence"]) == 60 and all(
        len(item["sha256"]) == 64 and len(item["content_sha256"]) == 64 for item in results["evidence"]
    )


def test_every_case_shows_expected_before_and_after_for_every_field(fake_run):
    _, document, _ = fake_run()
    example = next(case for case in document["results"]["cases"] if case["id"] == "1687194/4")
    assert example["fields"]["fee"] == {
        "expected": 1000,
        "before": 89000,
        "after": 1000,
        "before_matches": False,
        "after_matches": True,
    }
    assert example["fields"]["orchard_value_balance"] == {
        "expected": -88000,
        "before": None,
        "after": -88000,
        "before_matches": None,
        "after_matches": True,
    }
    assert example["fields"]["sprout_value_balance"]["after"] == 0
    assert example["before"]["explanation"] == "not read by the old revision: Orchard balance -88000"
    assert len(example["fields"]) == 14 and all(len(case["fields"]) == 14 for case in document["results"]["cases"])
    migration = next(case for case in document["results"]["cases"] if case["id"] == "600000/5")
    assert migration["fields"]["sprout_value_balance"] == {
        "expected": 92000000,
        "before": None,
        "after": 92000000,
        "before_matches": None,
        "after_matches": True,
    }


def no_floats(node):
    if isinstance(node, float):
        return False
    if isinstance(node, dict):
        return all(no_floats(value) for value in node.values())
    if isinstance(node, list):
        return all(no_floats(value) for value in node)
    return True


def test_no_floats_anywhere_in_the_report(fake_run):
    _, document, _ = fake_run(transformer=True)
    assert no_floats(document)


def test_nothing_identifies_the_machine_or_the_user(fake_run, tmp_path):
    _, document, _ = fake_run()
    block = report_module.confirmation(document)
    markdown = (tmp_path / "report" / "report.md").read_text(encoding="utf-8")
    written = (tmp_path / "report" / "report.json").read_text(encoding="utf-8")
    forbidden = [socket.gethostname(), getpass.getuser(), os.path.expanduser("~"), str(tmp_path), ROOT]
    for word in forbidden:
        assert word not in block and word not in markdown and word not in written, word
    assert not re.search(r"[/\\](home|Users|tmp)[/\\]", block + markdown + written)


def test_confirmation_block_content(fake_run, manifest):
    _, document, _ = fake_run()
    block = report_module.confirmation(document)
    for needed in (
        f"gs-zec-validate {__version__}",
        f"dataset     {manifest['dataset']['version']}",
        document["results"]["manifest"]["sha256"],
        "graphsense-lib 2.16.3",
        "graphsense-lib 2.16.4",
        "v2.16.3",
        "v2.16.4",
        document["results_sha256"],
        "identical",
        "PASS (exit status 0)",
        "python      ",
    ):
        assert needed in block, needed


def test_markdown_lists_every_case(fake_run, manifest, tmp_path):
    fake_run()
    markdown = (tmp_path / "report" / "report.md").read_text(encoding="utf-8")
    for case in manifest["cases"]:
        assert f"`{case['txid']}`" in markdown
    assert "| 1687194/4 | fee | 1,000 | 89,000 | not read by the old revision: Orchard balance -88000 |" in markdown


def test_published_checksum_file_format():
    published = report_module.read_published(ROOT)
    assert set(published) == {"results", "transformer-results"} and all(
        re.fullmatch(r"[0-9a-f]{64}", value) for value in published.values()
    )
