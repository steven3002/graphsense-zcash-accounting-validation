"""Worker: runs inside one GraphSense environment and returns what that GraphSense revision computes.

It is started by the validation command as

    <environment's python> -I worker.py <job.json> <result.json>

and uses only the standard library and the graphsenselib installed in that environment. It is one
self-contained file on purpose: this is everything that executes next to GraphSense.

What it does:
  1. refuses any connection or name lookup other than loopback that goes through Python's socket module
     (an audit hook, installed before graphsenselib is imported);
  2. identifies the graphsenselib it imported (version, checksums of its files);
  3. serves the packaged evidence through a JSON-RPC replay server on 127.0.0.1;
  4. calls GraphSense's own BtcBlockExporter.export_blocks_and_transactions for every evidence block, so that
     GraphSense's own HTTP client, parser and input resolver run unmodified;
  5. optionally passes the exporter's output through GraphSense's own TransformerUTXO;
  6. writes the transaction records it got back as JSON.

It contains no accounting logic. It feeds evidence in and reads results out.
"""

import gzip
import hashlib
import http.server
import json
import os
import socketserver
import sys
import threading

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
POOL_FIELDS = ("sapling_value_balance", "orchard_value_balance", "ironwood_value_balance")

loopback_connections = [0]  # a list, so the audit hook can update it
refused_attempts = []


def _is_loopback(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    return isinstance(host, str) and (host in LOOPBACK_HOSTS or host.startswith("127."))


def _audit(event, args):
    """Count loopback connections; refuse every other attempt to reach a network or resolve a name."""
    if event in ("socket.connect", "socket.sendto"):
        address = args[1]
        if isinstance(address, tuple):  # (host, port, ...) for IPv4 and IPv6; anything else is a local socket path
            if _is_loopback(address[0]):
                loopback_connections[0] += event == "socket.connect"
            else:
                refused_attempts.append(f"{event} {address[0]}")
                raise RuntimeError(f"network access refused: {event} to {address[0]}")
    elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"):
        if args[0] is not None and not _is_loopback(args[0]):
            refused_attempts.append(f"{event} {args[0]}")
            raise RuntimeError(f"network access refused: {event} for {args[0]}")


# Replay server: answers the three JSON-RPC methods GraphSense's exporter uses, from the packaged evidence.


class Replay:
    """The evidence of the block currently being exported, and the requests GraphSense made for it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.load(None, None, {})

    def load(self, height, block_bytes, transactions):
        self.height = height
        self.block_bytes = block_bytes
        self.block_hash = json.loads(block_bytes)["hash"] if block_bytes else None
        self.transactions = transactions  # txid -> stored bytes
        self.requests = []

    def answer(self, request):
        """One JSON-RPC request object -> one JSON-RPC response, as bytes.

        The stored evidence bytes are placed into the response unchanged; they are never parsed and written again.
        """
        method, params = request.get("method"), request.get("params") or []
        result = None
        if method == "getblockhash" and params == [self.height]:
            result = json.dumps(self.block_hash).encode()
        elif method == "getblock" and params == [self.block_hash, 2]:
            result = self.block_bytes
        elif method == "getrawtransaction" and len(params) == 2 and params[1] == 1 and params[0] in self.transactions:
            result = self.transactions[params[0]]
        with self.lock:
            self.requests.append({"method": method, "params": params, "answered": result is not None})
        request_id = json.dumps(request.get("id")).encode()
        if result is None:  # what a node says for something it does not have; HTTP status stays 200
            return (
                b'{"jsonrpc":"2.0","id":'
                + request_id
                + b',"error":{"code":-8,"message":"not in the packaged evidence"}}'
            )
        return b'{"jsonrpc":"2.0","id":' + request_id + b',"result":' + result + b"}"


REPLAY = Replay()


def _envelope(request, result, message="not in the capture"):
    """One JSON-RPC response around stored result bytes, or the error a node gives for something it does not have."""
    request_id = json.dumps(request.get("id")).encode()
    if result is None:  # HTTP status stays 200, as with a node
        error = json.dumps({"code": -8, "message": message}, separators=(",", ":")).encode()
        return b'{"jsonrpc":"2.0","id":' + request_id + b',"error":' + error + b"}"
    return b'{"jsonrpc":"2.0","id":' + request_id + b',"result":' + result + b"}"


class CaptureReplay:
    """Answers the exporter from a capture directory: any block of the captured range, any previous transaction in it.

    The capture is written by the tool (gs_zec_validate/capture.py) while this worker runs; `refresh` reads the
    index lines added since the last call. Every file is checked against the checksum in its index line; a file
    that is missing or differs is answered with an error and listed in `damaged`.
    """

    def __init__(self, directory):
        self.directory = directory
        self.lock = threading.Lock()
        self.hash_by_height, self.height_by_hash, self.block_digest = {}, {}, {}
        self.transaction_digest = {}
        self.answered = {"getblockhash": 0, "getblock": 0, "getrawtransaction": 0}
        self.unanswered, self.damaged = [], []
        self._offsets = {"blocks.tsv": 0, "transactions.tsv": 0}
        self.refresh()

    def _new_lines(self, name):
        path = os.path.join(self.directory, name)
        with open(path, "rb") as handle:
            handle.seek(self._offsets[name])
            data = handle.read()
        complete = data[: data.rfind(b"\n") + 1]
        self._offsets[name] += len(complete)
        return complete.decode("ascii").splitlines()

    def refresh(self):
        with self.lock:
            for line in self._new_lines("blocks.tsv"):
                height, block_hash, digest, _ = line.split("\t")
                self.hash_by_height[int(height)] = block_hash
                self.height_by_hash[block_hash] = int(height)
                self.block_digest[int(height)] = digest
            for line in self._new_lines("transactions.tsv"):
                txid, digest, _ = line.split("\t")
                self.transaction_digest[txid] = digest

    @staticmethod
    def _content(path, digest):
        """The file's content, or None when it cannot be read or differs from its index line."""
        try:
            with open(path, "rb") as handle:
                content = gzip.decompress(handle.read())
        except (OSError, EOFError):
            return None
        return content if hashlib.sha256(content).hexdigest() == digest else None

    def answer(self, request):
        method, params = request.get("method"), request.get("params") or []
        result = path = digest = None
        with self.lock:
            if method == "getblockhash" and len(params) == 1 and params[0] in self.hash_by_height:
                result = json.dumps(self.hash_by_height[params[0]]).encode()
            elif method == "getblock" and len(params) == 2 and params[1] == 2 and params[0] in self.height_by_hash:
                height = self.height_by_hash[params[0]]
                path, digest = os.path.join(self.directory, "blocks", f"{height}.json.gz"), self.block_digest[height]
            elif method == "getrawtransaction" and len(params) == 2 and params[1] == 1:
                txid = params[0]
                if txid in self.transaction_digest:
                    path = os.path.join(self.directory, "transactions", txid[:2], f"{txid}.json.gz")
                    digest = self.transaction_digest[txid]
        if path is not None:
            result = self._content(path, digest)
        with self.lock:
            if result is None:
                self.unanswered.append({"method": method, "params": params})
                if path is not None:
                    self.damaged.append(path)
            else:
                self.answered[method] += 1
        return _envelope(request, result, "not in the capture" if path is None else "capture file damaged")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True  # headers and body go out in two writes; Nagle would hold the body ~40 ms

    def do_POST(self):
        replay = getattr(self.server, "replay", REPLAY)
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            request = json.loads(body)
            if isinstance(request, list):
                payload = b"[" + b",".join(replay.answer(item) for item in request) + b"]"
            else:
                payload = replay.answer(request)
        except (ValueError, AttributeError):
            payload = b'{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"parse error"}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


# Identity of the imported graphsenselib


def tree_checksum(package_dir):
    """Checksum over every file of a package directory. Returns (sha256, number of files).

    One line per file, "<sha256 of the file>  <path>", the path relative to the directory that contains the
    package and written with "/"; lines sorted by path; the checksum is the sha256 of those lines.
    `__pycache__` directories and compiled files (*.pyc, *.pyo) are left out, because an installer may create them.
    """
    parent = os.path.dirname(package_dir)
    lines = []
    for folder, subfolders, names in os.walk(package_dir):
        subfolders[:] = sorted(name for name in subfolders if name != "__pycache__")
        for name in sorted(names):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(folder, name)
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            lines.append((os.path.relpath(path, parent).replace(os.sep, "/"), digest))
    lines.sort()
    return hashlib.sha256("".join(f"{digest}  {name}\n" for name, digest in lines).encode()).hexdigest(), len(lines)


def identify(package, accounting_files):
    """Describe the imported graphsenselib by checksums, so the caller can tell exactly which code ran."""
    package_dir = os.path.dirname(os.path.abspath(package.__file__))
    parent = os.path.dirname(package_dir)
    tree, file_count = tree_checksum(package_dir)
    files = {}
    for name in accounting_files:
        try:
            with open(os.path.join(parent, *name.split("/")), "rb") as handle:
                data = handle.read()
        except OSError:
            files[name] = None
            continue
        files[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "git_blob": hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(),
        }
    version = None
    distributions = {}
    try:
        from importlib import metadata

        for dist in metadata.distributions():
            name = dist.metadata["Name"]
            if name:
                distributions[name.lower()] = dist.version
        dist = metadata.distribution("graphsense-lib")
        installed = os.path.abspath(str(dist.locate_file("graphsenselib")))
        # Use the version label only if the metadata describes the code that was imported.
        if os.path.normcase(installed) == os.path.normcase(package_dir):
            version = dist.version
    except Exception:  # noqa: BLE001  (no distribution metadata: the checksums identify the code)
        version = None
    return {
        "distribution_version": version,
        "package_tree_sha256": tree,
        "package_file_count": file_count,
        "files": files,
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "implementation": sys.implementation.name,
        "package_dir": package_dir,
        "distributions": dict(sorted(distributions.items())),
    }


# Reading GraphSense's results


def _amount(value):
    """Amounts must be plain integers (zatoshi). Anything else is reported as text, so it can never look like a match."""
    if value is None or type(value) is int:
        return value
    return f"NOT-AN-INTEGER: {value!r} ({type(value).__name__})"


def _total(values):
    return sum(values) if all(type(value) is int for value in values) else "NOT-AN-INTEGER"


def exporter_record(tx):
    """Copy the accounting fields out of one transaction dict returned by GraphSense's exporter."""
    inputs, outputs = tx["inputs"], tx["outputs"]
    shielded_in = [_amount(item["value"]) for item in inputs if item["type"] == "shielded"]
    shielded_out = [_amount(item["value"]) for item in outputs if item["type"] == "shielded"]
    transparent_in = [
        _amount(item["value"]) for item in inputs if item["type"] != "shielded" and item["value"] is not None
    ]
    transparent_out = [
        _amount(item["value"]) for item in outputs if item["type"] != "shielded" and item["value"] is not None
    ]
    record = {
        "txid": tx["hash"],
        "index": tx["index"],
        "version": tx["version"],
        "is_coinbase": tx["is_coinbase"],
        "input_count": tx["input_count"],
        "output_count": tx["output_count"],
        "total_input": _amount(tx["input_value"]),
        "total_output": _amount(tx["output_value"]),
        "fee": _amount(tx["fee"]),
        "transparent_input": _total(transparent_in),
        "transparent_output": _total(transparent_out),
        "shielded_inputs": sorted(shielded_in, key=str),
        "shielded_outputs": sorted(shielded_out, key=str),
        "unresolved_inputs": sum(1 for item in inputs if item["value"] is None),
        # The corrected parser adds three per-pool fields to the transaction. The old one has none.
        "exposes_pool_balances": all(name in tx for name in POOL_FIELDS),
    }
    for name in POOL_FIELDS:
        record[name] = _amount(tx.get(name))
    return record


def transformer_record(row):
    """Copy the accounting fields out of one stored-row dict returned by GraphSense's TransformerUTXO."""
    tx_hash = row.get("tx_hash")
    return {
        "txid": tx_hash.hex() if isinstance(tx_hash, (bytes, bytearray)) else tx_hash,
        "coinbase": row.get("coinbase"),
        "total_input": _amount(row.get("total_input")),
        "total_output": _amount(row.get("total_output")),
        "fee": _amount(row.get("fee")),
        "coinjoin": row.get("coinjoin"),
    }


def read_evidence(item):
    """Decompress one evidence file and insist on the checksum the caller expects."""
    with open(item["path"], "rb") as handle:
        content = gzip.decompress(handle.read())
    found = hashlib.sha256(content).hexdigest()
    if found != item["content_sha256"]:
        raise SystemExit(
            f"evidence changed while running: {item['path']}: content sha256 {found}, expected {item['content_sha256']}"
        )
    return content


def write_result(result, result_path):
    """Write the result file. Anything GraphSense returned that JSON cannot hold is written as text, never dropped."""
    result["network"] = {"loopback_connections": loopback_connections[0], "refused": refused_attempts}
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, default=repr)
    return 0


def describe(error):
    return {"type": type(error).__name__, "message": str(error)}


def main(job_path, result_path):
    with open(job_path, encoding="utf-8") as handle:
        job = json.load(handle)
    result = {"identity": None, "import_error": None, "transformer_import_error": None, "runs": [], "network": None}

    sys.addaudithook(_audit)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"  # the loopback connection must not go through a proxy
    if job.get("source_dir"):
        sys.path.insert(0, job["source_dir"])
    try:
        import graphsenselib  # ty: ignore[unresolved-import]  (exists only in the two GraphSense environments)
        from graphsenselib.ingest.rpc_utxo import BtcBlockExporter  # ty: ignore[unresolved-import]

        result["identity"] = identify(graphsenselib, job["accounting_files"])
    except Exception as error:  # noqa: BLE001  (a failed import is this revision's result)
        result["import_error"] = describe(error)
        return write_result(result, result_path)
    transformer = job["transformer"]
    if transformer:
        try:
            from graphsenselib.ingest.common import BlockRangeContent  # ty: ignore[unresolved-import]
            from graphsenselib.ingest.transform import TransformerUTXO  # ty: ignore[unresolved-import]
        except Exception as error:  # noqa: BLE001  (the environment lacks what the transformer needs)
            result["transformer_import_error"] = describe(error)
            transformer = False

    server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    uri = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        for run in job["runs"]:
            height = run["height"]
            REPLAY.load(
                height, read_evidence(run["block"]), {item["txid"]: read_evidence(item) for item in run["transactions"]}
            )
            out = {
                "key": run["key"],
                "height": height,
                "exception": None,
                "transactions": None,
                "transformer": None,
                "transformer_exception": None,
            }
            try:
                # A fresh exporter per block: no output cache is carried from one block to the next.
                exporter = BtcBlockExporter(uri, max_workers=2, timeout=60, network="zec")
                blocks, transactions = exporter.export_blocks_and_transactions(height, height)
                out["block_hash"] = blocks[0]["hash"]
                out["transactions"] = [exporter_record(tx) for tx in transactions]
            except Exception as error:  # noqa: BLE001  (any exception is this revision's result for the block)
                out["exception"] = describe(error)
            else:
                if transformer:
                    try:
                        # The same hand-over GraphSense's SourceUTXO.read_blockrange does, then its transformer
                        # on the path used without a database (delta-only).
                        content = BlockRangeContent(
                            table_contents={"blocks": blocks, "txs": transactions}, start_block=height, end_block=height
                        )
                        rows = (
                            TransformerUTXO(job["partition_batch_size"], "zec")
                            .transform(content)
                            .table_contents["transaction"]
                        )
                        out["transformer"] = [transformer_record(row) for row in rows]
                    except Exception as error:  # noqa: BLE001  (likewise for the transformer layer)
                        out["transformer_exception"] = describe(error)
            out["requests"] = sorted(REPLAY.requests, key=lambda item: (item["method"], str(item["params"])))
            result["runs"].append(out)
    finally:
        server.shutdown()
        server.server_close()
    return write_result(result, result_path)


# Block ranges (compare-range and scan): records per transaction, or per block, streamed to a file.


class _StoredItem:
    """An input as the clustering function reads it from Cassandra: only its address list."""

    __slots__ = ("address",)

    def __init__(self, address):
        self.address = address


class _StoredRow:
    __slots__ = ("coinbase", "coinjoin", "inputs")

    def __init__(self, coinbase, coinjoin, inputs):
        self.coinbase, self.coinjoin, self.inputs = coinbase, coinjoin, inputs


def range_record(row, functions):
    """The fields compare-range needs from one stored row of GraphSense's TransformerUTXO.

    Every value is copied from the row or computed by a graphsense-lib function: `address_as_string` gives the
    address list Cassandra stores, `multi_input_address_set` the clustering input set.
    """
    address_as_string, multi_input_address_set = functions
    stored_inputs = [address_as_string(item) for item in row["inputs"]]
    stored_outputs = [address_as_string(item) for item in row["outputs"]]
    stored = _StoredRow(row["coinbase"], row["coinjoin"], [_StoredItem(address) for address in stored_inputs] or None)
    sets = {}
    for name, exclude in (("cluster_set", False), ("cluster_set_filtered", True)):
        found = multi_input_address_set(stored, exclude_coinjoin=exclude)
        sets[name] = sorted(found) if found is not None else None
    record = {
        "height": row["block_id"],
        "index": row["index"],
        "txid": row["tx_hash"].hex() if isinstance(row["tx_hash"], (bytes, bytearray)) else row["tx_hash"],
        "coinbase": row["coinbase"],
        "input_count": row["input_count"],
        "output_count": row["output_count"],
        "total_input": _amount(row["total_input"]),
        "total_output": _amount(row["total_output"]),
        "fee": _amount(row["fee"]),
        "coinjoin": row["coinjoin"],
        "shielded_inputs": sorted(_amount(item["value"]) for item in row["inputs"] if item["type"] == "shielded"),
        "shielded_outputs": sorted(_amount(item["value"]) for item in row["outputs"] if item["type"] == "shielded"),
        "pool_balances": [_amount(row[name]) for name in POOL_FIELDS] if all(n in row for n in POOL_FIELDS) else None,
        "regular_inputs": [
            [address[0], _amount(item["value"])]
            for address, item in zip(stored_inputs, row["inputs"], strict=True)
            if address is not None and len(address) == 1
        ],
        "regular_outputs": [
            [address[0], _amount(item["value"])]
            for address, item in zip(stored_outputs, row["outputs"], strict=True)
            if address is not None and len(address) == 1
        ],
        "unresolved_inputs": sum(1 for item in row["inputs"] if item["value"] is None),
    }
    record.update(sets)
    return record


def coinjoin_bound(tx, is_coinjoin):
    """graphsense-lib's is_coinjoin with every transparent input given an address of its own.

    Without input resolution the transparent inputs have no address yet. is_coinjoin reads addresses only to count
    the distinct ones, and the count can only rise when each input gets an address of its own, so this is an upper
    bound: when it is False, is_coinjoin is False whatever the inputs' real addresses are.
    """
    inputs = [
        item if item["type"] == "shielded" else dict(item, addresses=[f"input-{number}"])
        for number, item in enumerate(tx["inputs"])
    ]
    return bool(is_coinjoin(dict(tx, inputs=inputs)))


def scan_records(blocks, transactions, is_coinjoin=None):
    """Per block: how many transactions carry an Orchard or Ironwood value, and how much, from the parser's fields.

    With `is_coinjoin`, also the transactions whose coinjoin flag could be true (coinjoin_bound).
    """
    by_height = {block["number"]: [] for block in blocks}
    for tx in transactions:
        by_height[tx["block_number"]].append(tx)
    records = []
    for height, txs in by_height.items():
        record = {"height": height, "transactions": len(txs), "exposes_pool_balances": True}
        for pool in ("orchard", "ironwood"):
            values = [tx.get(f"{pool}_value_balance") for tx in txs]
            if any(value is None for value in values):
                record["exposes_pool_balances"] = False
                values = [value or 0 for value in values]
            record[f"{pool}_transactions"] = sum(1 for value in values if value != 0)
            record[f"{pool}_out_of_pool"] = sum(value for value in values if value > 0)
            record[f"{pool}_into_pool"] = -sum(value for value in values if value < 0)
        affected = [
            tx
            for tx in txs
            if (tx.get("orchard_value_balance") or 0) != 0 or (tx.get("ironwood_value_balance") or 0) != 0
        ]
        record["affected_transactions"] = len(affected)
        if is_coinjoin is not None:
            record["affected"] = [tx["hash"] for tx in affected]
            record["coinjoin_possible"] = [tx["hash"] for tx in txs if coinjoin_bound(tx, is_coinjoin)]
        records.append(record)
    return records


def serve(job_path):
    """Process block ranges on request, keeping one exporter (and its output cache) for the whole session.

    Protocol: one JSON object per line. The worker writes {"ready": ...} once graphsenselib is imported, then
    reads {"start": a, "end": b} requests from standard input and answers each with {"done": [a, b], ...} after
    appending its records to the job's records file. At the end of input it writes {"finished": ...} and exits.
    """
    with open(job_path, encoding="utf-8") as handle:
        job = json.load(handle)
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)  # whatever else is printed goes to standard error

    def send(message):
        protocol.write(json.dumps(message, default=repr) + "\n")
        protocol.flush()

    sys.addaudithook(_audit)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
    if job.get("source_dir"):
        sys.path.insert(0, job["source_dir"])
    try:
        import graphsenselib  # ty: ignore[unresolved-import]
        from graphsenselib.ingest.rpc_utxo import BtcBlockExporter  # ty: ignore[unresolved-import]

        identity = identify(graphsenselib, job["accounting_files"])
        functions = None
        if job["mode"] == "compare":
            from graphsenselib.ingest.common import BlockRangeContent  # ty: ignore[unresolved-import]
            from graphsenselib.ingest.transform import TransformerUTXO  # ty: ignore[unresolved-import]
            from graphsenselib.ingest.utxo import address_as_string  # ty: ignore[unresolved-import]
            from graphsenselib.utils.utxo import multi_input_address_set  # ty: ignore[unresolved-import]

            functions = (address_as_string, multi_input_address_set)
        else:
            from graphsenselib.ingest.utxo import is_coinjoin  # ty: ignore[unresolved-import]
    except Exception as error:  # noqa: BLE001  (a failed import is this revision's result)
        send({"ready": False, "import_error": describe(error)})
        return 0

    replay = CaptureReplay(job["capture_dir"])
    server = Server(("127.0.0.1", 0), Handler)
    server.replay = replay  # ty: ignore[unresolved-attribute]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    uri = f"http://127.0.0.1:{server.server_address[1]}"
    exporter = BtcBlockExporter(uri, max_workers=2, timeout=600, network="zec", resolve_inputs=job["mode"] == "compare")
    send({"ready": True, "identity": identity})
    try:
        for line in sys.stdin:
            request = json.loads(line)
            low, high = request["start"], request["end"]
            replay.refresh()
            try:
                blocks, transactions = exporter.export_blocks_and_transactions(low, high)
                if job["mode"] == "compare":
                    content = BlockRangeContent(
                        table_contents={"blocks": blocks, "txs": transactions}, start_block=low, end_block=high
                    )
                    rows = (
                        TransformerUTXO(job["partition_batch_size"], "zec")
                        .transform(content)
                        .table_contents["transaction"]
                    )
                    written = [range_record(row, functions) for row in rows]
                else:
                    written = scan_records(blocks, transactions, is_coinjoin)
            except Exception as error:  # noqa: BLE001  (an exception is this revision's result for the range)
                send(
                    {
                        "done": [low, high],
                        "exception": describe(error),
                        "unanswered": replay.unanswered[:5],
                        "damaged": replay.damaged[:5],
                    }
                )
                continue
            with open(job["records_path"], "a", encoding="utf-8", newline="\n") as records:
                records.writelines(json.dumps(record, separators=(",", ":"), default=repr) + "\n" for record in written)
            send({"done": [low, high], "exception": None, "blocks": len(blocks), "records": len(written)})
    finally:
        server.shutdown()
        server.server_close()
    send(
        {
            "finished": True,
            "network": {"loopback_connections": loopback_connections[0], "refused": refused_attempts},
            "answered": replay.answered,
            "unanswered": len(replay.unanswered),
        }
    )
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "--serve":
        sys.exit(serve(sys.argv[2]))
    sys.exit(main(sys.argv[1], sys.argv[2]))
