"""compare-range and scan end to end, with the real GraphSense environments of the transformer layer.

The node is either a capture built from the packaged evidence or a local server that answers from it, so the
expected figures follow from the manifest. Skipped until `install --with-transformer` has been run.
"""

import functools
import json
import os
import subprocess
import sys

import pytest

from conftest import ENV_DIR, ROOT, FakeNodeServer, environments_installed
from gs_zec_validate import capture, envs, evidence
from gs_zec_validate import manifest as manifest_module

ZEBRA_CAPTURE = "zebra-6.4.2"


@functools.cache
def transformer_layer_installed():
    if not environments_installed():
        return False
    for side in envs.SIDES:
        completed = subprocess.run(
            [envs.env_python(ENV_DIR, side), "-I", "-c", "import graphsenselib.ingest.transform"],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            return False
    return True


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not transformer_layer_installed(), reason="the environments of the transformer layer are not installed"
    ),
]


def packaged():
    """The manifest, the evidence block contents by height and the previous transactions' contents by txid."""
    manifest, _ = manifest_module.load(ROOT)
    blocks = {}
    for block in manifest["blocks"]:
        entry = next(item for item in block["evidence"] if item["capture"] == ZEBRA_CAPTURE)
        blocks[block["height"]] = (block, evidence.read_verified(ROOT, entry))
    transactions = {
        item["txid"]: evidence.read_verified(ROOT, item["evidence"]) for item in manifest["previous_transactions"]
    }
    return manifest, blocks, transactions


def build_capture(directory, height, leave_out=None):
    manifest, blocks, transactions = packaged()
    block, content = blocks[height]
    writer = capture.Writer(str(directory), height, height)
    writer.add_block(height, block["hash"], content)
    for txid in block["previous_transactions"]:
        if txid != leave_out:
            writer.add_transaction(txid, transactions[txid])
    writer.finish("evidence", True)
    return manifest


def evidence_node():
    """A JSON-RPC server answering getinfo, getblock <height> 2 and getrawtransaction from the packaged evidence."""
    _, blocks, transactions = packaged()

    def reply(body):
        method, params = body["method"], body["params"]
        if method == "getinfo":
            result = json.dumps({"subversion": "/evidence/", "blocks": 3_600_000}).encode()
        elif method == "getblock" and params[1] == 2 and int(params[0]) in blocks:
            result = blocks[int(params[0])][1]
        elif method == "getrawtransaction" and params[0] in transactions:
            result = transactions[params[0]]
        else:
            return 200, b'{"jsonrpc":"2.0","id":1,"error":{"code":-8,"message":"not here"}}'
        return 200, b'{"jsonrpc":"2.0","id":1,"result":' + result + b"}"

    return FakeNodeServer(reply)


def command(*arguments):
    completed = subprocess.run(
        [sys.executable, os.path.join(ROOT, "gs-zec-validate.py"), *arguments, "--env-dir", ENV_DIR],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return completed.returncode, completed.stdout + completed.stderr


def summary(report_dir):
    with open(os.path.join(str(report_dir), "summary.json"), encoding="utf-8") as handle:
        return json.load(handle)["results"]


def expected_legs(manifest, height):
    """Orchard and Ironwood totals the corrected parser must add, from the manifest's expected values."""
    totals = {pool: {"transactions": 0, "out_of_pool": 0, "into_pool": 0} for pool in ("orchard", "ironwood")}
    changed = 0
    for case in manifest["cases"]:
        if case["block_height"] != height:
            continue
        changed += case["old_code"]["differs"]
        for pool, tally in totals.items():
            value = case["expected"][f"{pool}_value_balance"]
            if value:
                tally["transactions"] += 1
                tally["out_of_pool" if value > 0 else "into_pool"] += abs(value)
    return totals, changed


def test_compare_range_from_a_capture_of_the_worked_example(tmp_path):
    manifest = build_capture(tmp_path / "capture", 1687194)
    status, text = command(
        "compare-range",
        "--start",
        "1687194",
        "--end",
        "1687194",
        "--from-capture",
        str(tmp_path / "capture"),
        "--report-dir",
        str(tmp_path / "report"),
    )
    assert status == 0, text
    results = summary(tmp_path / "report")
    totals, changed = expected_legs(manifest, 1687194)
    assert results["canonical"] is True and results["transactions"]["total"] == 5
    assert results["changed"]["transactions"] == changed == 1 and results["changed"]["fee"] == 1
    assert results["newly_accounted"] == totals
    assert results["differences_explained"]["unexplained"] == 0
    assert results["unresolved_inputs"] == {"before": 0, "after": 0}
    with open(tmp_path / "report" / "changed-transactions.csv", encoding="utf-8") as handle:
        rows = handle.read().splitlines()
    assert len(rows) == 2 and rows[1].startswith(
        "1687194,bd84ece1a1f9930085768a880d99445ee1ee13686c2e5979b30d920e90866517,"
    )


def test_compare_range_against_a_node_on_the_three_pool_block(tmp_path):
    manifest, _, _ = packaged()
    server = evidence_node()
    try:
        status, text = command(
            "compare-range",
            "--start",
            "3463373",
            "--end",
            "3463373",
            "--rpc-url",
            server.url,
            "--keep-capture",
            str(tmp_path / "capture"),
            "--report-dir",
            str(tmp_path / "report"),
        )
    finally:
        server.close()
    assert status == 0, text
    results = summary(tmp_path / "report")
    totals, changed = expected_legs(manifest, 3463373)
    assert results["changed"]["transactions"] == changed and results["newly_accounted"] == totals
    assert results["differences_explained"]["unexplained"] == 0
    assert capture.open_capture(str(tmp_path / "capture"), 3463373, 3463373)["node"] == "/evidence/"


def test_the_old_release_in_the_place_of_the_corrected_one_fails(tmp_path):
    build_capture(tmp_path / "capture", 1687194)
    status, text = command(
        "compare-range",
        "--start",
        "1687194",
        "--end",
        "1687194",
        "--from-capture",
        str(tmp_path / "capture"),
        "--after-python",
        envs.env_python(ENV_DIR, "before"),
        "--report-dir",
        str(tmp_path / "report"),
    )
    assert status == 1, text
    results = summary(tmp_path / "report")
    assert results["canonical"] is False and results["differences_explained"]["unexplained"] == 5
    assert "NON-CANONICAL" in text


def test_a_missing_previous_transaction_stops_compare_range(tmp_path):
    build_capture(
        tmp_path / "capture", 1687194, leave_out="077423f5f3b38d7d22fd20850b7b37f2f0633574e1e6907f8126552df09cb39b"
    )
    status, text = command(
        "compare-range",
        "--start",
        "1687194",
        "--end",
        "1687194",
        "--from-capture",
        str(tmp_path / "capture"),
        "--report-dir",
        str(tmp_path / "report"),
    )
    assert status == 3, text
    assert "raised on blocks 1,687,194-1,687,194" in text and "could not be resolved" in text


def test_scan_against_a_node(tmp_path):
    manifest, _, _ = packaged()
    server = evidence_node()
    try:
        status, text = command(
            "scan",
            "--start",
            "3463373",
            "--end",
            "3463373",
            "--rpc-url",
            server.url,
            "--report-dir",
            str(tmp_path / "report"),
        )
    finally:
        server.close()
    assert status == 0, text
    results = summary(tmp_path / "report")
    totals, _ = expected_legs(manifest, 3463373)
    for pool in ("orchard", "ironwood"):
        assert results[pool]["transactions"] == totals[pool]["transactions"]
        assert results[pool]["out_of_pool"] == totals[pool]["out_of_pool"]
        assert results[pool]["into_pool"] == totals[pool]["into_pool"]
    assert results["coinjoin_screen"]["candidates"] == 0
    assert sorted(os.listdir(tmp_path / "report")) == ["blocks.csv", "summary.json"]  # the scratch capture is gone
