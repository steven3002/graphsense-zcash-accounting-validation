"""Loading the manifest: strict JSON, the schema, and the rules a schema cannot express."""

import json
import os

import pytest

from conftest import ROOT, edit_manifest
from gs_zec_validate import manifest as manifest_module
from gs_zec_validate.manifest import ManifestError


def test_the_packaged_manifest_loads(manifest):
    assert manifest["manifest_schema_version"] == 1
    assert len(manifest["cases"]) == sum(block["transaction_count"] for block in manifest["blocks"]) == 47
    assert manifest_module.consistency_problems(manifest) == []


def test_every_number_in_the_manifest_is_an_integer():
    with open(os.path.join(ROOT, "manifest", "manifest.json"), encoding="utf-8") as handle:
        json.load(handle, parse_float=lambda text: pytest.fail(f"float in the manifest: {text}"))


def test_full_length_identifiers(manifest):
    for case in manifest["cases"]:
        assert len(case["txid"]) == 64 and len(case["block_hash"]) == 64


def expect_error(copy_of_repo, change, message):
    edit_manifest(copy_of_repo, change)
    with pytest.raises(ManifestError, match=message):
        manifest_module.load(str(copy_of_repo))


def test_schema_violation(copy_of_repo):
    expect_error(
        copy_of_repo,
        lambda d: d["cases"][5]["expected"].update(fee="10000"),
        r"does not follow its schema: \$\.cases\[5\]\.expected\.fee: expected integer",
    )


def test_duplicated_case(copy_of_repo):
    expect_error(copy_of_repo, lambda d: d["cases"].append(dict(d["cases"][7])), "case 600000/7 is listed twice")


def test_removed_case(copy_of_repo):
    expect_error(
        copy_of_repo,
        lambda d: d["cases"].pop(18),
        r"block 1687194 has 5 transactions but 4 cases \(no case for position 4\)",
    )


def test_case_in_an_unknown_block(copy_of_repo):
    expect_error(
        copy_of_repo, lambda d: d["cases"][0].update(block_height=1, id="1/0"), "block 1 is not an evidence block"
    )


def test_previous_transaction_without_evidence(copy_of_repo):
    expect_error(
        copy_of_repo, lambda d: d["previous_transactions"].pop(0), "is needed by a block but has no evidence entry"
    )


def test_evidence_file_listed_twice(copy_of_repo):
    def change(d):
        d["blocks"][1]["evidence"][0]["path"] = d["blocks"][0]["evidence"][0]["path"]

    expect_error(copy_of_repo, change, "is listed more than once")


def test_contradicting_old_code_statement(copy_of_repo):
    expect_error(copy_of_repo, lambda d: d["cases"][0]["old_code"].update(differs=True), "old_code.differs contradicts")


def write_raw(copy_of_repo, text):
    with open(copy_of_repo / "manifest" / "manifest.json", "w", encoding="utf-8") as handle:
        handle.write(text)


def test_not_json(copy_of_repo):
    write_raw(copy_of_repo, "{ this is not json")
    with pytest.raises(ManifestError, match="not valid JSON"):
        manifest_module.load(str(copy_of_repo))


def test_floats_are_refused(copy_of_repo):
    text = (copy_of_repo / "manifest" / "manifest.json").read_text(encoding="utf-8")
    assert '"fee": 1000\n' in text
    write_raw(copy_of_repo, text.replace('"fee": 1000\n', '"fee": 1000.5\n', 1))
    with pytest.raises(ManifestError, match="the number 1000.5 is not an integer"):
        manifest_module.load(str(copy_of_repo))


def test_duplicate_members_are_refused(copy_of_repo):
    text = (copy_of_repo / "manifest" / "manifest.json").read_text(encoding="utf-8")
    write_raw(
        copy_of_repo,
        text.replace('"manifest_schema_version": 1,', '"manifest_schema_version": 1, "manifest_schema_version": 1,', 1),
    )
    with pytest.raises(ManifestError, match="duplicate member 'manifest_schema_version'"):
        manifest_module.load(str(copy_of_repo))


def test_missing_manifest(copy_of_repo):
    os.remove(copy_of_repo / "manifest" / "manifest.json")
    with pytest.raises(ManifestError, match="manifest cannot be read"):
        manifest_module.load(str(copy_of_repo))


def test_checksums_of_text_files_do_not_depend_on_line_ends(copy_of_repo, manifest):
    """A checkout that writes CR LF must give the same manifest checksum and accept the same lock files."""
    original = manifest_module.load(ROOT)[1]
    for relative in ("manifest/manifest.json", "locks/after.txt", "locks/before.txt"):
        path = copy_of_repo / relative
        data = path.read_bytes()
        assert b"\r\n" not in data
        path.write_bytes(data.replace(b"\n", b"\r\n"))
    assert manifest_module.load(str(copy_of_repo))[1] == original
    from gs_zec_validate import envs

    for side in ("before", "after"):
        lock = manifest["revisions"][side]["locks"]["default"]
        assert envs._text_sha256_file(str(copy_of_repo / lock["path"])) == lock["sha256"]
