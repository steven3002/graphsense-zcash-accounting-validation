"""Shared helpers of the test suite.

Most tests need no GraphSense at all: they build the records a GraphSense revision would return straight from
the manifest (`fake_worker`) and exercise the tool around them. Tests marked `e2e` run the two real
environments and are skipped when those have not been installed.
"""

import copy
import http.server
import json
import os
import shutil
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gs_zec_validate import envs, validate
from gs_zec_validate import manifest as manifest_module

LAUNCHER = os.path.join(ROOT, "gs-zec-validate.py")
ENV_DIR = os.environ.get("GS_ZEC_VALIDATE_ENV_DIR", os.path.join(ROOT, envs.DEFAULT_ENV_DIR))
OBSERVED = (
    "is_coinbase",
    "transparent_input",
    "transparent_output",
    "shielded_inputs",
    "shielded_outputs",
    "input_count",
    "output_count",
    "total_input",
    "total_output",
    "fee",
)


@pytest.fixture(scope="session")
def manifest():
    return manifest_module.load(ROOT)[0]


@pytest.fixture()
def copy_of_repo(tmp_path):
    """A private copy of everything a run reads, so that a test can damage it."""
    target = tmp_path / "repo"
    target.mkdir()
    for name in ("manifest", "evidence", "gs_zec_validate", "locks"):
        shutil.copytree(os.path.join(ROOT, name), target / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("gs-zec-validate.py", "RESULTS.sha256"):
        shutil.copy(os.path.join(ROOT, name), target / name)
    return target


def edit_manifest(root, change):
    """Load the manifest of a copy, let `change` modify it, write it back."""
    path = os.path.join(root, "manifest", "manifest.json")
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    change(document)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, indent=1)
        handle.write("\n")


def fake_worker(manifest, side, transformer=False, as_side=None):
    """What the worker would return for `side` if GraphSense reported exactly the manifest's values.

    `as_side` lets a test put one revision's records where the other is expected ("old code as after").
    """
    source = as_side or side
    pinned = manifest["revisions"][source]
    runs = []
    for block in manifest["blocks"]:
        records = []
        for case in manifest["cases"]:
            if case["block_height"] != block["height"]:
                continue
            values = case["expected"] if source == "after" else case["before_observed"]
            record = {name: copy.deepcopy(values[name]) for name in OBSERVED}
            record.update(
                {
                    "txid": case["txid"],
                    "index": case["index"],
                    "version": case["version"],
                    "unresolved_inputs": 0,
                    "exposes_pool_balances": source == "after",
                }
            )
            for pool in ("sapling", "orchard", "ironwood"):
                record[f"{pool}_value_balance"] = (
                    case["expected"][f"{pool}_value_balance"] if source == "after" else None
                )
            records.append(record)
        records.sort(key=lambda record: record["index"])
        requests = [
            {"method": "getblock", "params": [block["hash"], 2], "answered": True},
            {"method": "getblockhash", "params": [block["height"]], "answered": True},
        ]
        requests += [
            {"method": "getrawtransaction", "params": [txid, 1], "answered": True}
            for txid in block["previous_transactions"]
        ]
        for entry in block["evidence"]:
            run = {
                "key": f"{block['height']}/{entry['capture']}",
                "height": block["height"],
                "exception": None,
                "block_hash": block["hash"],
                "transactions": copy.deepcopy(records),
                "transformer": None,
                "transformer_exception": None,
                "requests": copy.deepcopy(requests),
            }
            if transformer:
                run["transformer"] = [
                    {
                        "txid": r["txid"],
                        "coinbase": r["is_coinbase"],
                        "total_input": r["total_input"],
                        "total_output": r["total_output"],
                        "fee": r["fee"],
                        "coinjoin": next(c for c in manifest["cases"] if c["txid"] == r["txid"])[
                            "transformer_observed"
                        ][f"{source}_coinjoin"],
                    }
                    for r in records
                ]
            runs.append(run)
    return {
        "identity": {
            "distribution_version": pinned["version"],
            "package_tree_sha256": pinned["package_tree_sha256"],
            "package_file_count": pinned["package_file_count"],
            "files": copy.deepcopy(pinned["files"]),
            "python": "3.12.0",
            "implementation": "cpython",
            "package_dir": "(fake)",
            "distributions": {"graphsense-lib": pinned["version"]},
        },
        "import_error": None,
        "transformer_import_error": None,
        "runs": runs,
        "network": {"loopback_connections": 3 * len(runs), "refused": []},
        "stderr_lines": 0,
    }


@pytest.fixture()
def fake_run(monkeypatch, manifest, tmp_path):
    """Run the whole validation with `fake_worker` in place of the two GraphSense environments.

    Returns a function `run(root=ROOT, tamper=None, overrides=None, transformer=False)` giving (exit status, report).
    `tamper(side, worker_output)` may modify what a "revision" returns.
    """

    def run(root=ROOT, tamper=None, overrides=None, transformer=False, swap=None):
        def worker(python, job, timeout=600):
            side = "before" if os.sep + "before" + os.sep in python else "after"
            # The "revisions" always return the true values of the packaged manifest, whatever a test did to its copy.
            output = fake_worker(manifest, side, transformer=job["transformer"], as_side=(swap or {}).get(side))
            if tamper:
                tamper(side, output)
            return output

        monkeypatch.setattr(envs, "run_worker", worker)
        report_dir = tmp_path / "report"
        lines = []
        status = validate.run(
            str(root),
            str(tmp_path / "envs"),
            str(report_dir),
            overrides or {},
            transformer=transformer,
            echo=lines.append,
        )
        with open(report_dir / "report.json", encoding="utf-8") as handle:
            return status, json.load(handle), "\n".join(lines)

    return run


def environments_installed():
    return all(os.path.isfile(envs.env_python(ENV_DIR, side)) for side in envs.SIDES)


# Block ranges: a node answering from dictionaries, and a JSON-RPC server for the node client.


class FakeNode:
    """Answers getblock and getrawtransaction from dictionaries, as a node would (result bytes and parsed value)."""

    def __init__(self, blocks, transactions):
        self.blocks, self.transactions = blocks, transactions
        self.asked = []

    def result(self, method, params, parse_float=float):
        self.asked.append((method, params[0]))
        if method == "getblock":
            value = self.blocks[int(params[0])]
        else:
            value = self.transactions[params[0]]
        content = json.dumps(value, separators=(",", ":")).encode()
        return json.loads(content, parse_float=parse_float), content


def tx(txid, *spent):
    return {"txid": txid, "vin": [{"txid": item, "vout": 0} for item in spent] or [{"coinbase": "00"}]}


def chain():
    """Blocks 10 to 12. Block 11 spends an output of block 10 and two older ones; block 12 one older again."""
    blocks = {
        10: {"height": 10, "hash": "h10", "tx": [tx("c10"), tx("t10", "old1")]},
        11: {"height": 11, "hash": "h11", "tx": [tx("c11"), tx("t11", "t10", "old2", "old1")]},
        12: {"height": 12, "hash": "h12", "tx": [tx("c12"), tx("t12a", "old3"), tx("t12b", "t12a")]},
    }
    transactions = {name: {"txid": name, "vout": [{"n": 0, "valueZat": 1}]} for name in ("old1", "old2", "old3")}
    return blocks, transactions


class FakeNodeServer:
    """A local HTTP server; `reply(body_dict) -> (status, bytes)` decides the answer to each request."""

    def __init__(self, reply):
        received = self.received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((body, self.headers.get("Authorization")))
                status, payload = reply(body)
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
