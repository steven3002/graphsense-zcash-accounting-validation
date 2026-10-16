"""Captures of a block range: the pieces, the directory format, what is fetched, and the checks on reading."""

import gzip
import json
import os

import pytest

from conftest import FakeNode, chain
from gs_zec_validate import capture


def test_chunks_split_at_partitions_and_pieces():
    assert capture.chunks(1_687_104, 1_687_303) == [(1_687_104, 1_687_303)]
    assert capture.chunks(1_699_500, 1_701_200) == [
        (1_699_500, 1_699_999),
        (1_700_000, 1_700_999),
        (1_701_000, 1_701_200),
    ]
    assert capture.chunks(5, 5) == [(5, 5)]
    assert capture.chunks(1_686_904, 1_687_403) == [(1_686_904, 1_686_999), (1_687_000, 1_687_403)]
    assert capture.chunks(0, 2_500, size=1_000, partition=1_200) == [
        (0, 999),
        (1_000, 1_199),
        (1_200, 1_999),
        (2_000, 2_399),
        (2_400, 2_500),
    ]


def test_fetch_writes_blocks_and_only_the_previous_transactions_from_outside_the_range(tmp_path):
    blocks, transactions = chain()
    node = FakeNode(blocks, transactions)
    writer = capture.Writer(str(tmp_path / "cap"), 10, 12)
    done = []
    capture.fetch(node, 10, 12, writer, workers=2, done=lambda low, high: done.append((low, high)))
    metadata = writer.finish("node 1.0", True)
    assert done == [(10, 12)]
    assert sorted(item for method, item in node.asked if method == "getrawtransaction") == ["old1", "old2", "old3"]
    assert metadata["blocks"] == 3 and metadata["previous_transactions"] == 3
    assert capture.open_capture(str(tmp_path / "cap"), 10, 12)["node"] == "node 1.0"
    with open(capture.block_path(str(tmp_path / "cap"), 11), "rb") as handle:
        stored = handle.read()
    assert stored[4:8] == b"\0\0\0\0"  # no time in the gzip header


def test_fetch_without_previous_transactions(tmp_path):
    blocks, transactions = chain()
    node = FakeNode(blocks, transactions)
    writer = capture.Writer(str(tmp_path / "cap"), 10, 12)
    capture.fetch(node, 10, 12, writer, previous_transactions=False)
    writer.finish("n", False)
    assert all(method == "getblock" for method, _ in node.asked)
    with pytest.raises(capture.CaptureError, match="no previous transactions"):
        capture.open_capture(str(tmp_path / "cap"), 10, 12)


def test_stored_files_read_back_like_compressed_ones(tmp_path):
    content = json.dumps({"height": 7, "hash": "h7", "tx": [{"txid": "a" * 64}] * 50}).encode()
    for level in (6, 0):
        directory = str(tmp_path / f"cap{level}")
        writer = capture.Writer(directory, 7, 7, compresslevel=level)
        writer.add_block(7, "h7", content)
        writer.finish("n", True)
        with open(capture.block_path(directory, 7), "rb") as handle:
            stored = handle.read()
        assert gzip.decompress(stored) == content
        assert (len(stored) > len(content)) is (level == 0)


def test_fetch_refuses_a_block_at_another_height(tmp_path):
    blocks, transactions = chain()
    blocks[11] = dict(blocks[11], height=99)
    writer = capture.Writer(str(tmp_path / "cap"), 10, 12)
    with pytest.raises(capture.CaptureError, match="getblock 11"):
        capture.fetch(FakeNode(blocks, transactions), 10, 12, writer)


def test_fetch_refuses_another_transaction(tmp_path):
    blocks, transactions = chain()
    transactions["old2"] = {"txid": "other", "vout": []}
    writer = capture.Writer(str(tmp_path / "cap"), 10, 12)
    with pytest.raises(capture.CaptureError, match="getrawtransaction old2"):
        capture.fetch(FakeNode(blocks, transactions), 10, 12, writer)


def test_writer_refuses_an_existing_capture(tmp_path):
    blocks, transactions = chain()
    writer = capture.Writer(str(tmp_path / "cap"), 10, 12)
    capture.fetch(FakeNode(blocks, transactions), 10, 12, writer)
    writer.finish("n", True)
    with pytest.raises(capture.CaptureError, match="already holds a capture"):
        capture.Writer(str(tmp_path / "cap"), 10, 12)


def test_open_capture_checks(tmp_path):
    directory = str(tmp_path / "cap")
    with pytest.raises(capture.CaptureError, match="not a complete capture"):
        capture.open_capture(directory)
    blocks, transactions = chain()
    writer = capture.Writer(directory, 10, 12)
    capture.fetch(FakeNode(blocks, transactions), 10, 12, writer)
    writer.finish("n", True)
    with pytest.raises(capture.CaptureError, match="not for 9 to 12"):
        capture.open_capture(directory, 9, 12)
    with pytest.raises(capture.CaptureError, match="not for 11 to 12"):
        capture.open_capture(directory, 11, 12)  # block 11 spends t10, which only block 10 holds
    with pytest.raises(capture.CaptureError, match="not for 10 to 13"):
        capture.open_capture(directory, 10, 13)
    assert capture.open_capture(directory, 10, 11)["start"] == 10
    with open(os.path.join(directory, capture.INDEX_TRANSACTIONS), "a", encoding="ascii") as handle:
        handle.write("x\ty\t1\n")
    with pytest.raises(capture.CaptureError, match="differs from the checksum"):
        capture.open_capture(directory)
    with open(os.path.join(directory, capture.METADATA), "w", encoding="utf-8") as handle:
        json.dump({"capture_format": 99}, handle)
    with pytest.raises(capture.CaptureError, match="capture format 99"):
        capture.open_capture(directory)


def test_remove_blocks(tmp_path):
    blocks, transactions = chain()
    directory = str(tmp_path / "cap")
    writer = capture.Writer(directory, 10, 12)
    capture.fetch(FakeNode(blocks, transactions), 10, 12, writer, previous_transactions=False)
    capture.remove_blocks(directory, 10, 11)
    capture.remove_blocks(directory, 10, 11)
    assert sorted(os.listdir(os.path.join(directory, "blocks"))) == ["12.json.gz"]


def test_fetch_hands_each_piece_over_once_and_in_order(tmp_path):
    blocks = {
        h: {"height": h, "hash": f"h{h}", "tx": [{"txid": f"c{h}", "vin": [{"coinbase": "00"}]}]} for h in range(2500)
    }
    order = []
    writer = capture.Writer(str(tmp_path / "cap"), 0, 2499)
    capture.fetch(
        FakeNode(blocks, {}), 0, 2499, writer, previous_transactions=False, done=lambda a, b: order.append((a, b))
    )
    assert order == [(0, 999), (1000, 1999), (2000, 2499)]


def test_an_exception_in_the_handover_stops_the_fetch(tmp_path):
    blocks = {h: {"height": h, "hash": f"h{h}", "tx": []} for h in range(3000)}

    def fail(low, high):
        raise RuntimeError(f"stop at {low}")

    writer = capture.Writer(str(tmp_path / "cap"), 0, 2999)
    with pytest.raises(RuntimeError, match="stop at 0"):
        capture.fetch(FakeNode(blocks, {}), 0, 2999, writer, previous_transactions=False, done=fail)
