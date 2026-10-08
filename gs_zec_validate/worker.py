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


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            request = json.loads(body)
            if isinstance(request, list):
                payload = b"[" + b",".join(REPLAY.answer(item) for item in request) + b"]"
            else:
                payload = REPLAY.answer(request)
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
