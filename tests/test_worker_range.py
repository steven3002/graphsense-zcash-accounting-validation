"""The worker's block-range mode, without GraphSense: the capture replay, and the records it builds from rows."""

import json
import os

import pytest

from conftest import FakeNode, chain
from gs_zec_validate import capture, worker


@pytest.fixture()
def replay(tmp_path):
    blocks, transactions = chain()
    directory = str(tmp_path / "cap")
    writer = capture.Writer(directory, 10, 12)
    capture.fetch(FakeNode(blocks, transactions), 10, 12, writer)
    writer.finish("n", True)
    return worker.CaptureReplay(directory)


def answer(replay, method, params, request_id=7):
    return json.loads(replay.answer({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))


def test_replay_answers_blocks_by_height_and_hash(replay):
    assert answer(replay, "getblockhash", [11]) == {"jsonrpc": "2.0", "id": 7, "result": "h11"}
    block = answer(replay, "getblock", ["h11", 2])["result"]
    assert block["height"] == 11 and block["tx"][1]["txid"] == "t11"
    assert answer(replay, "getrawtransaction", ["old2", 1])["result"]["txid"] == "old2"
    assert replay.answered == {"getblockhash": 1, "getblock": 1, "getrawtransaction": 1}


def test_replay_says_what_it_does_not_have(replay):
    for method, params in (
        ("getblockhash", [13]),
        ("getblock", ["h11", 1]),
        ("getblock", ["nope", 2]),
        ("getrawtransaction", ["t10", 1]),
        ("getbestblockhash", []),
    ):
        assert answer(replay, method, params)["error"]["code"] == -8
    assert len(replay.unanswered) == 5


def test_replay_reads_lines_added_later(tmp_path):
    blocks, transactions = chain()
    directory = str(tmp_path / "cap")
    writer = capture.Writer(directory, 10, 12)
    replay = worker.CaptureReplay(directory)
    assert answer(replay, "getblockhash", [10])["error"]
    capture.fetch(FakeNode(blocks, transactions), 10, 12, writer)
    replay.refresh()
    assert answer(replay, "getblockhash", [10])["result"] == "h10"


def test_replay_refuses_a_changed_or_missing_file(replay):
    path = capture.block_path(replay.directory, 12)
    with open(path, "wb") as handle:
        handle.write(capture._gzip(b'{"height":12,"hash":"h12","tx":[]}'))
    assert answer(replay, "getblock", ["h12", 2])["error"] == {"code": -8, "message": "capture file damaged"}
    os.remove(capture.block_path(replay.directory, 11))
    assert answer(replay, "getblock", ["h11", 2])["error"]["message"] == "capture file damaged"
    assert replay.damaged == [path, capture.block_path(replay.directory, 11)] and len(replay.unanswered) == 2


def _address_as_string(item):
    """graphsense-lib's rule (ingest/utxo.py): no address for these types."""
    if item["type"] in ["null", "nulldata", "nonstandard", "witness_unknown", "shielded"]:
        return None
    return item["addresses"]


def _multi_input_address_set(tx, exclude_coinjoin=True):
    if tx.coinbase or (exclude_coinjoin and tx.coinjoin):
        return None
    found = set()
    for item in tx.inputs or []:
        if item.address:
            found.update(item.address)
    return found if len(found) > 1 else None


def row(**changes):
    value = {
        "block_id": 100,
        "index": 3,
        "tx_hash": bytes.fromhex("ab" * 32),
        "coinbase": False,
        "coinjoin": False,
        "input_count": 4,
        "output_count": 3,
        "total_input": 1500,
        "total_output": 1400,
        "fee": 100,
        "sapling_value_balance": 0,
        "orchard_value_balance": 500,
        "ironwood_value_balance": -200,
        "inputs": [
            {"type": "pubkeyhash", "addresses": ["a"], "value": 600},
            {"type": "pubkeyhash", "addresses": ["b"], "value": 300},
            {"type": "multisig", "addresses": ["m1", "m2"], "value": 100},
            {"type": "shielded", "addresses": [], "value": 500},
        ],
        "outputs": [
            {"type": "pubkeyhash", "addresses": ["c"], "value": 1000},
            {"type": "nulldata", "addresses": [], "value": 200},
            {"type": "shielded", "addresses": [], "value": 200},
        ],
    }
    value.update(changes)
    return value


def test_range_record():
    record = worker.range_record(row(), (_address_as_string, _multi_input_address_set))
    assert record["txid"] == "ab" * 32 and record["height"] == 100 and record["index"] == 3
    assert record["regular_inputs"] == [["a", 600], ["b", 300]]  # the multisig input has two addresses
    assert record["regular_outputs"] == [["c", 1000]]
    assert record["shielded_inputs"] == [500] and record["shielded_outputs"] == [200]
    assert record["pool_balances"] == [0, 500, -200]
    assert record["cluster_set"] == ["a", "b", "m1", "m2"] == record["cluster_set_filtered"]
    assert record["unresolved_inputs"] == 0


def test_range_record_of_the_old_code_and_of_a_coinjoin():
    old = row(coinjoin=True)
    for name in ("sapling_value_balance", "orchard_value_balance", "ironwood_value_balance"):
        del old[name]
    record = worker.range_record(old, (_address_as_string, _multi_input_address_set))
    assert record["pool_balances"] is None
    assert record["cluster_set"] == ["a", "b", "m1", "m2"] and record["cluster_set_filtered"] is None


def test_range_record_marks_amounts_that_are_not_integers():
    record = worker.range_record(row(fee=1.5), (_address_as_string, _multi_input_address_set))
    assert record["fee"].startswith("NOT-AN-INTEGER")


def test_scan_records():
    blocks = [{"number": 5}, {"number": 6}]
    txs = [
        {"block_number": 5, "orchard_value_balance": 0, "ironwood_value_balance": 0},
        {"block_number": 5, "orchard_value_balance": -10, "ironwood_value_balance": 4},
        {"block_number": 5, "orchard_value_balance": 3, "ironwood_value_balance": 0},
    ]
    first, second = worker.scan_records(blocks, txs)
    assert first == {
        "height": 5,
        "transactions": 3,
        "exposes_pool_balances": True,
        "orchard_transactions": 2,
        "orchard_out_of_pool": 3,
        "orchard_into_pool": 10,
        "ironwood_transactions": 1,
        "ironwood_out_of_pool": 4,
        "ironwood_into_pool": 0,
        "affected_transactions": 2,
    }
    assert second["transactions"] == 0 and second["affected_transactions"] == 0
    assert not worker.scan_records([{"number": 5}], [{"block_number": 5}])[0]["exposes_pool_balances"]


def test_coinjoin_bound_gives_every_transparent_input_an_address_of_its_own():
    seen = []

    def is_coinjoin(tx):
        seen.append(tx)
        return len({str(item["addresses"]) for item in tx["inputs"]}) >= 3

    tx = {
        "input_count": 3,
        "inputs": [
            {"type": None, "addresses": [], "value": None},
            {"type": None, "addresses": [], "value": None},
            {"type": "shielded", "addresses": [], "value": 7},
        ],
        "outputs": [],
    }
    assert worker.coinjoin_bound(tx, is_coinjoin) is True
    assert [item["addresses"] for item in seen[0]["inputs"]] == [["input-0"], ["input-1"], []]
    assert tx["inputs"][0]["addresses"] == []  # the exporter's dict itself is not changed
    assert worker.coinjoin_bound(dict(tx, inputs=tx["inputs"][1:]), is_coinjoin) is False


def test_scan_records_with_the_coinjoin_bound():
    blocks = [{"number": 5}]
    txs = [
        {"hash": "aa", "block_number": 5, "orchard_value_balance": -10, "ironwood_value_balance": 0, "inputs": []},
        {"hash": "bb", "block_number": 5, "orchard_value_balance": 0, "ironwood_value_balance": 0, "inputs": []},
    ]
    (record,) = worker.scan_records(blocks, txs, is_coinjoin=lambda tx: tx["hash"] == "bb")
    assert record["affected"] == ["aa"] and record["coinjoin_possible"] == ["bb"]
