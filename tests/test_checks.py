"""check: the per-block rules, the worker's sums, and a whole run against a node and a stand-in worker."""

import json
import types

import pytest

from conftest import ROOT, FakeNodeServer
from gs_zec_validate import checks, checkworker

POOLS = {"transparent": 0, "sprout": 0, "sapling": 0, "orchard": 0, "lockbox": 0, "ironwood": 0}


def stored(transactions=2, **changes):
    value = checkworker.empty()
    value.update(present=True, transactions=transactions)
    value.update(changes)
    return value


def node(count=2, **pools):
    return ("hash", count, {**POOLS, **pools})


def test_a_consistent_block_passes():
    block = stored(shielded_in=100, shielded_out=40, transparent_in=500, transparent_out=1060)
    evaluated, failures = checks.judge(5, node(orchard=-60, transparent=560), block, "cassandra", {})
    assert evaluated == {"rows", "totals", "fees", "shielded", "transparent"} and failures == {}


def test_missing_orchard_legs_fail_the_shielded_rule():
    old = stored(shielded_in=0, shielded_out=0, transparent_in=500, transparent_out=1060, negative_fee=["t"])
    _, failures = checks.judge(5, node(orchard=-60, transparent=560), old, "cassandra", {})
    assert set(failures) == {"shielded", "fees"} and "the node's pools changed by -60" in failures["shielded"]


def failed_rules(*arguments):
    return set(checks.judge(*arguments)[1])


def test_rows_rule():
    assert "rows" in failed_rules(5, node(count=3), stored(), "cassandra", {})
    assert "rows" in failed_rules(5, node(), stored(present=False), "cassandra", {})
    assert "rows" in failed_rules(5, node(), stored(block_rows=2), "delta", {})
    assert "rows" not in failed_rules(5, node(), stored(block_rows=1), "delta", {})


def test_a_listed_transaction_without_its_row_fails_the_rows_rule():
    evaluated, failures = checks.judge(5, node(), stored(transactions=1, missing_transactions=[7]), "cassandra", {})
    assert set(failures) == {"rows"} and "1 listed transactions have no row, e.g. tx_id 7" in failures["rows"]
    assert "shielded" not in evaluated and "transparent" not in evaluated  # their sums would be incomplete


def test_transparent_totals_and_fee_rules():
    evaluated, failures = checks.judge(
        5,
        node(transparent=10),
        stored(transparent_out=11, totals_inconsistent=["x"], fee_inconsistent=["y"]),
        "delta",
        {},
    )
    assert set(failures) == {"transparent", "totals", "stored-fee"} and "stored-fee" in evaluated
    evaluated, failures = checks.judge(5, node(), stored(null_values=["z"]), "delta", {})
    assert set(failures) == {"totals"} and "shielded" not in evaluated
    assert "stored-fee" not in checks.judge(5, node(), stored(), "cassandra", {})[0]


def test_dataset_rule():
    expected = {5: {"aa": [10, 9, 1], "bb": [3, 3, 0]}}
    good = stored(detail=[["aa", 10, 9, 1], ["bb", 3, 3, 0]])
    evaluated, failures = checks.judge(5, node(), good, "cassandra", expected)
    assert "dataset" in evaluated and failures == {}
    bad = stored(detail=[["aa", 10, 0, 10], ["bb", 3, 3, 0]])
    assert "1 of 2 transactions differ, e.g. aa" in checks.judge(5, node(), bad, "cassandra", expected)[1]["dataset"]
    assert "dataset" not in checks.judge(6, node(), good, "cassandra", expected)[0]


def test_the_worker_finds_the_configuration_graphsense_lib_would_find(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GRAPHSENSE_CONFIG_YAML", raising=False)
    assert "GRAPHSENSE_CONFIG_YAML" not in checks.worker_environment(None)
    (tmp_path / ".graphsense.yaml").write_text("environments: {}\n", encoding="utf-8")
    assert checks.worker_environment(None)["GRAPHSENSE_CONFIG_YAML"] == str(tmp_path / ".graphsense.yaml")
    assert checks.worker_environment("/etc/gs.yaml")["GRAPHSENSE_CONFIG_YAML"] == "/etc/gs.yaml"
    monkeypatch.setenv("GRAPHSENSE_CONFIG_YAML", "/srv/gs.yaml")
    assert checks.worker_environment(None)["GRAPHSENSE_CONFIG_YAML"] == "/srv/gs.yaml"


def test_worker_sums():
    summary = checkworker.empty()
    detail = []
    checkworker.add(summary, "t1", False, True, 900, 850, None, [(600, False), (300, True)], [(850, False)], detail)
    checkworker.add(summary, "t2", True, False, 0, 312, 0, [], [(312, False)], None)
    checkworker.add(summary, "t3", False, False, 100, 200, -100, [(100, False)], [(200, False)], None)
    checkworker.add(summary, "t4", False, False, 100, 50, 0, [(90, False)], [(50, True)], None)
    checkworker.add(summary, "t5", False, False, None, 50, None, [(None, False)], [(50, True)], None)
    assert (
        summary["transactions"] == 5 and summary["coinbase_transactions"] == 1 and summary["coinjoin_transactions"] == 1
    )
    assert summary["transparent_in"] == 600 + 100 + 90 and summary["shielded_in"] == 300
    assert summary["transparent_out"] == 850 + 312 + 200 and summary["shielded_out"] == 50
    assert summary["negative_fee"] == ["t3"]
    assert summary["totals_inconsistent"] == ["t4"] and summary["fee_inconsistent"] == ["t4"]
    assert summary["null_values"] == ["t5"]
    assert detail == [["t1", 900, 850, 50]]


def test_dataset_expectations(manifest):
    expected = checks.dataset_expectations(manifest)
    assert sorted(expected) == [block["height"] for block in manifest["blocks"]]
    assert expected[1687194]["bd84ece1a1f9930085768a880d99445ee1ee13686c2e5979b30d920e90866517"] == [89000, 88000, 1000]


class StandInWorker:
    """Answers like checkworker.py would, from per-block sums the test provides."""

    def __init__(self, blocks):
        self.blocks = blocks
        self.ready = {"ready": True, "graphsense_lib": "test", "raw_keyspace": "zec_raw_test"}

    def ask(self, low, high):
        return {
            "done": [low, high],
            "exception": None,
            "sinks": {"cassandra": {str(h): self.blocks[h] for h in range(low, high + 1)}},
        }

    def close(self):
        pass

    def kill(self):
        pass


def run_check(tmp_path, monkeypatch, blocks, pools):
    def reply(body):
        if body["method"] == "getinfo":
            return 200, json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": {"subversion": "/Test/", "blocks": 100}}
            ).encode()
        height = int(body["params"][0])
        block = {
            "height": height,
            "hash": f"h{height}",
            "tx": ["a", "b"],
            "valuePools": [{"id": pool, "valueDeltaZat": value} for pool, value in {**POOLS, **pools[height]}.items()],
        }
        return 200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": block}).encode()

    server = FakeNodeServer(reply)
    monkeypatch.setattr(checks, "_Worker", lambda *args: StandInWorker(blocks))
    arguments = types.SimpleNamespace(
        start=min(blocks),
        end=max(blocks),
        sink=None,
        report_dir=str(tmp_path / "report"),
        gs_env="test",
        gs_python="x",
        gs_config=None,
        delta_path=None,
        rpc_url=server.url,
        rpc_cookie_file=None,
        rpc_user=None,
        rpc_password_env="X",
        rpc_workers=2,
    )
    lines = []
    try:
        status = checks.run(ROOT, arguments, echo=lines.append)
    finally:
        server.close()
    with open(tmp_path / "report" / "check.json", encoding="utf-8") as handle:
        return status, "\n".join(lines), json.load(handle)


def test_a_whole_check_that_passes(tmp_path, monkeypatch):
    blocks = {10: stored(transparent_out=5), 11: stored(shielded_in=7, transparent_out=7)}
    status, text, document = run_check(
        tmp_path, monkeypatch, blocks, {10: {"transparent": 5}, 11: {"transparent": 7, "orchard": -7}}
    )
    assert status == 0 and "verdict     PASS (exit status 0)" in text
    assert document["results"]["rules"]["cassandra"]["shielded"] == {"checked": 2, "failed": 0}


def test_a_whole_check_that_fails_names_the_block(tmp_path, monkeypatch):
    blocks = {10: stored(transparent_out=5), 11: stored(transparent_out=7)}
    status, text, document = run_check(
        tmp_path, monkeypatch, blocks, {10: {"transparent": 5}, 11: {"transparent": 7, "orchard": 7}}
    )
    assert status == 1 and "[FAIL] Shielded outputs minus shielded inputs" in text and "block 11:" in text
    assert document["results"]["verdict"] == "fail"
    with open(tmp_path / "report" / "failing-blocks.csv", encoding="utf-8") as handle:
        assert handle.read().splitlines()[1].startswith("11,cassandra,shielded,")


def test_rules_that_cannot_be_evaluated_are_not_counted_as_checked(tmp_path, monkeypatch):
    blocks = {10: stored(transparent_out=5), 11: stored(transactions=1, missing_transactions=[4], transparent_out=7)}
    status, text, document = run_check(tmp_path, monkeypatch, blocks, {10: {"transparent": 5}, 11: {"transparent": 7}})
    rules = document["results"]["rules"]["cassandra"]
    assert status == 1 and rules["rows"] == {"checked": 2, "failed": 1}
    assert rules["shielded"] == {"checked": 1, "failed": 0} and rules["transparent"] == {"checked": 1, "failed": 0}
    assert "(blocks: 1 checked, 0 failed)" in text


def test_a_node_without_value_pools(tmp_path):
    def reply(body):
        return 200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"height": 3, "hash": "h", "tx": []}}).encode()

    server = FakeNodeServer(reply)
    try:
        client = checks.node_module.Node(server.url)
        with pytest.raises(checks.CheckError, match="no valuePools"):
            checks.node_blocks(client, 3, 3, 1)
    finally:
        server.close()
