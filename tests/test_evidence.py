"""Evidence files: integrity checks and the comparison of two captures."""

import gzip
import os
from decimal import Decimal

import pytest

from conftest import ROOT
from gs_zec_validate import evidence
from gs_zec_validate.evidence import IntegrityError

RULES = [
    {"key": "confirmations", "parents": None, "rule": "ignored", "reason": "grows"},
    {"key": "anchor", "parents": ["orchard", "ironwood"], "rule": "byte-reversed", "reason": "node version"},
]


def test_every_packaged_file_has_the_manifest_checksums(manifest):
    entries = [entry for block in manifest["blocks"] for entry in block["evidence"]]
    entries += [item["evidence"] for item in manifest["previous_transactions"]]
    assert len(entries) == 60
    for entry in entries:
        content = evidence.read_verified(ROOT, entry)
        assert len(content) == entry["content_bytes"]
    on_disk = sorted(
        os.path.join(folder, name)[len(ROOT) + 1 :].replace(os.sep, "/")
        for folder, _, names in os.walk(os.path.join(ROOT, "evidence"))
        for name in names
    )
    assert on_disk == sorted(entry["path"] for entry in entries), (
        "every evidence file is in the manifest and nothing else is"
    )


def first_entry(manifest):
    return manifest["blocks"][0]["evidence"][0]


def test_one_changed_byte_is_detected(manifest, copy_of_repo):
    entry = first_entry(manifest)
    path = copy_of_repo / entry["path"]
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(IntegrityError, match=f"evidence file changed: {entry['path']}"):
        evidence.read_verified(str(copy_of_repo), entry)


def test_recompressed_file_with_other_content_is_detected(manifest, copy_of_repo):
    entry = dict(first_entry(manifest))
    path = copy_of_repo / entry["path"]
    content = gzip.decompress(path.read_bytes())
    assert content.count(b'"height": 600000') == 12  # the block and its 11 transactions
    path.write_bytes(gzip.compress(content.replace(b'"height": 600000', b'"height": 600001', 1)))
    entry["sha256"] = evidence.sha256(path.read_bytes())  # even if the manifest's file checksum were updated ...
    with pytest.raises(IntegrityError, match="evidence content changed"):  # ... the content checksum still catches it
        evidence.read_verified(str(copy_of_repo), entry)


def test_missing_file(manifest, copy_of_repo):
    entry = first_entry(manifest)
    os.remove(copy_of_repo / entry["path"])
    with pytest.raises(IntegrityError, match=f"evidence file missing: {entry['path']}"):
        evidence.read_verified(str(copy_of_repo), entry)


def test_truncated_gzip(manifest, copy_of_repo):
    entry = dict(first_entry(manifest))
    path = copy_of_repo / entry["path"]
    path.write_bytes(path.read_bytes()[:200])
    entry["sha256"] = evidence.sha256(path.read_bytes())
    with pytest.raises(IntegrityError, match="not valid gzip"):
        evidence.read_verified(str(copy_of_repo), entry)


def test_decimals_stay_exact():
    parsed = evidence.parse(b'{"value": 3.28e-05, "valueZat": 3280}')
    assert (
        parsed["value"] == Decimal("0.0000328") and isinstance(parsed["value"], Decimal) and parsed["valueZat"] == 3280
    )


def test_allowed_differences_between_captures():
    a = {
        "confirmations": 5,
        "tx": [
            {"confirmations": 5, "orchard": {"anchor": "0102ff", "valueBalance": Decimal("1E-5")}, "anchor": "aa01"}
        ],
    }
    b = {
        "confirmations": 9,
        "tx": [
            {"confirmations": 9, "orchard": {"anchor": "ff0201", "valueBalance": Decimal("0.00001")}, "anchor": "aa01"}
        ],
    }
    unexpected, allowed = evidence.differences(a, b, RULES)
    assert unexpected == []
    assert sorted(allowed) == ["$.confirmations", "$.tx[0].confirmations", "$.tx[0].orchard.anchor"]


def test_anything_else_is_a_difference():
    base = {"tx": [{"orchard": {"anchor": "0102", "valueBalanceZat": 5}, "anchor": "0102", "vout": [1, 2]}]}

    def differs(change):
        other = {"tx": [{"orchard": {"anchor": "0102", "valueBalanceZat": 5}, "anchor": "0102", "vout": [1, 2]}]}
        change(other["tx"][0])
        return evidence.differences(base, other, RULES)[0]

    assert differs(lambda tx: tx["orchard"].update(valueBalanceZat=6)) == [
        "$.tx[0].orchard.valueBalanceZat: 5 versus 6"
    ]
    assert differs(lambda tx: tx["orchard"].update(anchor="0103")) != [], "an anchor that is not the byte-reversed one"
    assert differs(lambda tx: tx.update(anchor="0201")) != [], (
        "the Sprout anchor of a transaction is not covered by the rule"
    )
    assert differs(lambda tx: tx.update(vout=[1])) == ["$.tx[0].vout: 2 items versus 1"]
    assert differs(lambda tx: tx.update(extra=1)) == ["$.tx[0].extra: present in only one capture"]
    assert differs(lambda tx: tx["orchard"].update(valueBalanceZat="5")) != [], "a number and a text are different"


def test_the_two_captures_of_every_reused_block_agree(manifest):
    for block in manifest["blocks"]:
        if len(block["evidence"]) == 2:
            first, second = (evidence.parse(evidence.read_verified(ROOT, entry)) for entry in block["evidence"])
            unexpected, allowed = evidence.differences(first, second, manifest["capture_differences_allowed"])
            assert unexpected == [] and allowed
