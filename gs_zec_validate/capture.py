"""Captures of a block range: what a node answered, stored so that GraphSense can be run on it again offline.

A capture is a directory:

    capture.json                      written last: format, range, node version, counts, checksums of the indexes
    blocks.tsv                        one line per block:  height, hash, sha256 and length of the content
    transactions.tsv                  one line per previous transaction:  txid, sha256 and length of the content
    blocks/<height>.json.gz           `getblock <height> 2`, the bytes of the result member
    transactions/<xx>/<txid>.json.gz  `getrawtransaction <txid> 1` for every transaction whose outputs the range
                                      spends and which the range does not itself contain

Files are gzip-compressed without a time or a name in the header, so the same answer always gives the same file
(a scan writes them in gzip's stored format, without compression: it deletes them as soon as they are processed).
The worker reads the two indexes and the files; it has its own copy of the few lines that do that.
"""

import collections
import gzip
import hashlib
import io
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

from . import envs

FORMAT = 1
INDEX_BLOCKS = "blocks.tsv"
INDEX_TRANSACTIONS = "transactions.tsv"
METADATA = "capture.json"
# Blocks per request to the worker and blocks per partition, as graphsense-lib's ingest uses them for zec
# (ingest/dump.py, FILESIZES and PARTITIONSIZES).
FILE_BATCH = 1000
PARTITION = 100_000


class CaptureError(envs.SetupError):
    """A capture is incomplete, does not cover the range, or a file differs from its index."""


def _split(start, end, size):
    """graphsense-lib's split_blockrange (ingest/source.py): pieces that end at multiples of `size`."""
    pieces = []
    low = start
    while low <= end:
        high = min((low // size + 1) * size - 1, end)
        pieces.append((low, high))
        low = high + 1
    return pieces


def chunks(start, end, size=FILE_BATCH, partition=PARTITION):
    """Split [start, end] as graphsense-lib's IngestRunner.run does: into partitions, then into file pieces."""
    return [piece for low, high in _split(start, end, partition) for piece in _split(low, high, size)]


def _gzip(content, level=6):
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, compresslevel=level, mtime=0) as handle:
        handle.write(content)
    return buffer.getvalue()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def block_path(directory, height):
    return os.path.join(directory, "blocks", f"{height}.json.gz")


def transaction_path(directory, txid):
    return os.path.join(directory, "transactions", txid[:2], f"{txid}.json.gz")


class Writer:
    """Appends blocks and previous transactions to a capture directory. `compresslevel` 0 stores the files as they are."""

    def __init__(self, directory, start, end, compresslevel=6):
        self.directory = directory
        self.compresslevel = compresslevel
        self.start, self.end = start, end
        self.blocks = self.transactions = 0
        self.content_bytes = 0
        self._known = set()
        if os.path.exists(os.path.join(directory, METADATA)) or os.path.exists(os.path.join(directory, INDEX_BLOCKS)):
            raise CaptureError(f"refusing to write into {directory}: it already holds a capture")
        os.makedirs(os.path.join(directory, "blocks"), exist_ok=True)
        os.makedirs(os.path.join(directory, "transactions"), exist_ok=True)
        for name in (INDEX_BLOCKS, INDEX_TRANSACTIONS):
            with open(os.path.join(directory, name), "w", encoding="ascii"):
                pass

    def _index(self, name, line):
        with open(os.path.join(self.directory, name), "a", encoding="ascii", newline="\n") as handle:
            handle.write(line)

    def add_block(self, height, block_hash, content):
        with open(block_path(self.directory, height), "wb") as handle:
            handle.write(_gzip(content, self.compresslevel))
        self._index(INDEX_BLOCKS, f"{height}\t{block_hash}\t{_sha256(content)}\t{len(content)}\n")
        self.blocks += 1
        self.content_bytes += len(content)

    def has_transaction(self, txid):
        return txid in self._known

    def add_transaction(self, txid, content):
        path = transaction_path(self.directory, txid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(_gzip(content, self.compresslevel))
        self._index(INDEX_TRANSACTIONS, f"{txid}\t{_sha256(content)}\t{len(content)}\n")
        self._known.add(txid)
        self.transactions += 1
        self.content_bytes += len(content)

    def finish(self, node_version, previous_transactions):
        """Write capture.json, which marks the capture as complete."""
        metadata = {
            "capture_format": FORMAT,
            "network": "zec",
            "start": self.start,
            "end": self.end,
            "blocks": self.blocks,
            "previous_transactions": self.transactions,
            "includes_previous_transactions": previous_transactions,
            "content_bytes": self.content_bytes,
            "node": node_version,
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "index_sha256": {
                name: _file_sha256(os.path.join(self.directory, name)) for name in (INDEX_BLOCKS, INDEX_TRANSACTIONS)
            },
        }
        with open(os.path.join(self.directory, METADATA), "w", encoding="utf-8", newline="\n") as handle:
            json.dump(metadata, handle, indent=1, sort_keys=True)
            handle.write("\n")
        return metadata


def _file_sha256(path):
    with open(path, "rb") as handle:
        return _sha256(handle.read())


def open_capture(directory, start=None, end=None):
    """Read and check a complete capture. Returns its metadata; raises CaptureError when it cannot be used."""
    path = os.path.join(directory, METADATA)
    try:
        with open(path, encoding="utf-8") as handle:
            metadata = json.load(handle)
    except FileNotFoundError:
        raise CaptureError(f"{directory} is not a complete capture: {METADATA} is missing") from None
    except (OSError, ValueError) as error:
        raise CaptureError(f"{path} cannot be read: {error}") from None
    if metadata.get("capture_format") != FORMAT:
        raise CaptureError(f"{path}: capture format {metadata.get('capture_format')!r}, this tool reads {FORMAT}")
    for name, digest in metadata["index_sha256"].items():
        if _file_sha256(os.path.join(directory, name)) != digest:
            raise CaptureError(f"{directory}: {name} differs from the checksum in {METADATA}")
    # A run may stop before the capture's end, but must begin at its start: the previous transactions a range
    # spends from inside itself are not stored separately.
    if start is not None and not (metadata["start"] == start and end <= metadata["end"]):
        raise CaptureError(
            f"{directory} holds blocks {metadata['start']} to {metadata['end']}; it can be used for ranges that start "
            f"at {metadata['start']} and end at {metadata['end']} or earlier, not for {start} to {end}"
        )
    if start is not None and not metadata["includes_previous_transactions"]:
        raise CaptureError(f"{directory} holds no previous transactions; it cannot be used to resolve inputs")
    return metadata


def _bounded(pool, function, items, limit):
    """pool.map that keeps at most `limit` requests in flight and yields the answers in order."""
    pending = collections.deque()
    for item in items:
        pending.append(pool.submit(function, item))
        if len(pending) >= limit:
            yield pending.popleft().result()
    while pending:
        yield pending.popleft().result()


def _block_answer(client, height):
    """(hash, [(txid, [spent txids])], content) of one block; the parsed block itself is not kept."""
    block, content = client.result("getblock", [str(height), 2], float)
    if not isinstance(block, dict) or block.get("height") != height or "tx" not in block:
        raise CaptureError(f"getblock {height}: the node's answer is not the block at that height")
    spends = [(tx["txid"], [vin["txid"] for vin in tx.get("vin") or [] if vin.get("txid")]) for tx in block["tx"]]
    return block["hash"], spends, content


def _transaction_answer(client, txid):
    transaction, content = client.result("getrawtransaction", [txid, 1], float)
    if not isinstance(transaction, dict) or transaction.get("txid") != txid:
        raise CaptureError(f"getrawtransaction {txid}: the node's answer is not that transaction")
    return content


def fetch(client, start, end, writer, previous_transactions=True, workers=2, status=None, done=None):
    """Ask the node for every block of [start, end] and, if wanted, every previous transaction it spends from.

    Blocks are written in height order. `done(low, high)` is called for each complete piece of the range, in a
    thread of its own, while the next piece is fetched; it is never called for two pieces at the same time, and an
    exception it raises stops the fetch. At most `workers` requests run at the same time.
    """
    produced = set()
    pieces = chunks(start, end)
    limit = 2 * workers
    pending = None
    with ThreadPoolExecutor(max_workers=workers) as pool, ThreadPoolExecutor(max_workers=1) as handover:
        for number, (low, high) in enumerate(pieces, 1):
            if status:
                status(f"fetching blocks {low:,}-{high:,} ({number}/{len(pieces)})")
            needed = {}  # insertion-ordered set
            answers = _bounded(pool, lambda height: _block_answer(client, height), range(low, high + 1), limit)
            for height, (block_hash, spends, content) in zip(range(low, high + 1), answers, strict=True):
                writer.add_block(height, block_hash, content)
                if not previous_transactions:
                    continue
                for txid, spent in spends:
                    for item in spent:
                        if item not in produced and not writer.has_transaction(item):
                            needed[item] = None
                    produced.add(txid)
            if needed:
                if status:
                    status(f"fetching {len(needed):,} previous transactions for blocks {low:,}-{high:,}")
                wanted = list(needed)
                fetched = _bounded(pool, lambda txid: _transaction_answer(client, txid), wanted, limit)
                for txid, content in zip(wanted, fetched, strict=True):
                    writer.add_transaction(txid, content)
            if done:
                if pending is not None:
                    pending.result()
                pending = handover.submit(done, low, high)
        if pending is not None:
            pending.result()


def remove_blocks(directory, low, high):
    """Delete the block files of [low, high] (a scan does not keep what it has processed)."""
    for height in range(low, high + 1):
        try:
            os.remove(block_path(directory, height))
        except FileNotFoundError:
            pass


def remove(directory):
    shutil.rmtree(directory, ignore_errors=True)
