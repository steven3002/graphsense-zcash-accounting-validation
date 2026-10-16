"""check: are the stored Zcash rows of a block range consistent with the node, after a re-ingestion?

For every block of the range the node's `getblock <height> 1` gives the transaction count and the change of each
value pool (`valuePools[*].valueDeltaZat`). The check worker (checkworker.py), running in the operator's own
GraphSense environment, sums what the Cassandra raw keyspace and/or the Delta lake store for the same block.
The rules, per block and sink:

  rows          one stored transaction per transaction of the block, none missing, none twice
  shielded      shielded outputs minus shielded inputs = the change of the Sprout, Sapling, Orchard and Ironwood
                pools together (value entering the shielded pools is a shielded output)
  transparent   transparent outputs minus transparent inputs = the change of the transparent pool
  fees          no non-coinbase transaction has a negative fee (total input minus total output)
  totals        each row's total input and total output equal the sum of its inputs and outputs
  stored fee    (Delta lake only) the stored fee equals total input minus total output; 0 for a coinbase
  dataset       for blocks of this repository's dataset: every transaction's total input, total output and fee
                equal the dataset's expected values

Rows ingested by graphsense-lib up to 2.16.3 fail `shielded` in every block whose transactions have an Orchard or
Ironwood value, and usually `fees` too; rows re-ingested by 2.16.4 or later pass.
"""

import csv
import json
import os
import platform
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import PROG, __version__, capture, envs, report
from . import manifest as manifest_module
from . import node as node_module

CHECK_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkworker.py")
SHIELDED_POOLS = ("sprout", "sapling", "orchard", "ironwood")
RULES = {
    "rows": "One stored transaction per transaction of the block",
    "shielded": "Shielded outputs minus shielded inputs equal the node's shielded pool changes",
    "transparent": "Transparent outputs minus transparent inputs equal the node's transparent pool change",
    "fees": "No non-coinbase transaction has a negative fee",
    "totals": "Total input and total output equal the sums of the stored inputs and outputs",
    "stored-fee": "The stored fee equals total input minus total output (Delta lake)",
    "dataset": "Transactions of this repository's dataset have the expected totals and fee",
}
PIECE = 1000
SCHEMA_PATH = "manifest/check-report.schema.json"


class CheckError(envs.SetupError):
    """The check cannot run: the worker cannot read the stored data, or the node cannot be used."""


def worker_environment(config):
    """The environment of the check worker: graphsense-lib finds the configuration it would find here.

    graphsense-lib reads GRAPHSENSE_CONFIG_YAML, else .graphsense.yaml in the current directory, else
    ~/.graphsense.yaml (config/config.py). The worker runs in a scratch directory, so the second is passed on.
    """
    environ = dict(os.environ)
    if config:
        environ["GRAPHSENSE_CONFIG_YAML"] = config
    elif not environ.get("GRAPHSENSE_CONFIG_YAML") and os.path.isfile(".graphsense.yaml"):
        environ["GRAPHSENSE_CONFIG_YAML"] = os.path.abspath(".graphsense.yaml")
    return environ


class _Worker:
    def __init__(self, python, job, scratch, config, echo_stderr):
        if not os.path.isfile(python):
            raise CheckError(f"interpreter not found: {python}")
        job_path = os.path.join(scratch, "check-job.json")
        with open(job_path, "w", encoding="utf-8") as handle:
            json.dump(job, handle)
        environ = worker_environment(config)
        self._tail = []
        try:
            self.process = subprocess.Popen(
                [python, "-I", CHECK_WORKER, job_path],
                cwd=scratch,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environ,
            )
        except OSError as error:
            raise CheckError(f"the check worker could not be started: {python}: {error.strerror}") from None

        def drain():
            for line in self.process.stderr or ():
                self._tail = (self._tail + [line.rstrip()])[-20:]
                if echo_stderr:
                    echo_stderr(line.rstrip())

        self._reader = threading.Thread(target=drain, daemon=True)
        self._reader.start()
        self.ready = self.receive()

    def receive(self):
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            self.process.wait()
            self._reader.join(1.0)
            raise CheckError(
                f"the check worker ended unexpectedly (exit status {self.process.returncode})\n" + "\n".join(self._tail)
            )
        return json.loads(line)

    def ask(self, low, high):
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({"start": low, "end": high}) + "\n")
        self.process.stdin.flush()
        return self.receive()

    def close(self):
        if self.process.stdin:
            self.process.stdin.close()
        self.receive()
        self.process.wait(timeout=60)

    def kill(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()


def node_blocks(client, low, high, workers):
    """{height: (hash, transaction count, {pool: change})} from `getblock <height> 1`."""

    def one(height):
        block = client.call("getblock", [str(height), 1], float)
        if not isinstance(block, dict) or block.get("height") != height or "valuePools" not in block:
            raise CheckError(f"getblock {height} 1: the node's answer has no valuePools")
        pools = {entry["id"]: entry["valueDeltaZat"] for entry in block["valuePools"]}
        if any(type(value) is not int for value in pools.values()):
            raise CheckError(f"getblock {height} 1: valuePools without integer valueDeltaZat")
        return height, (block["hash"], len(block["tx"]), pools)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(one, range(low, high + 1)))


def dataset_expectations(manifest):
    """{height: {txid: (total_input, total_output, fee)}} for the blocks of the dataset."""
    expected = {}
    for case in manifest["cases"]:
        values = case["expected"]
        expected.setdefault(case["block_height"], {})[case["txid"]] = [
            values["total_input"],
            values["total_output"],
            values["fee"],
        ]
    return expected


def judge(height, node, stored, sink, expected):
    """The rules evaluated for one block in one sink, and their failures: ({rule}, {rule: text}).

    `shielded` and `transparent` are not evaluated when rows are missing or a value is missing: their sums would
    be incomplete.
    """
    failures = {}
    _, count, pools = node
    missing = stored.get("missing_transactions", [])
    block_rows = stored.get("block_rows", 1)
    evaluated = {"rows", "totals", "fees"}
    if not stored["present"] or missing or stored["transactions"] != count or block_rows != 1:
        failures["rows"] = (
            f"{stored['transactions']} transaction rows stored, the block has {count}"
            + ("" if stored["present"] else "; the block itself is not stored")
            + (f"; {len(missing)} listed transactions have no row, e.g. tx_id {missing[0]}" if missing else "")
            + (f"; {block_rows} block rows" if block_rows != 1 else "")
        )
    if stored["null_values"]:
        failures["totals"] = f"{len(stored['null_values'])} rows with a missing value, e.g. {stored['null_values'][0]}"
    else:
        if stored["totals_inconsistent"]:
            failures["totals"] = f"{len(stored['totals_inconsistent'])} rows, e.g. {stored['totals_inconsistent'][0]}"
        if "rows" not in failures:
            evaluated |= {"shielded", "transparent"}
            shielded_change = sum(pools.get(pool, 0) for pool in SHIELDED_POOLS)
            stored_shielded = stored["shielded_out"] - stored["shielded_in"]
            if stored_shielded != shielded_change:
                failures["shielded"] = f"stored {stored_shielded:,}, the node's pools changed by {shielded_change:,}"
            stored_transparent = stored["transparent_out"] - stored["transparent_in"]
            if stored_transparent != pools.get("transparent"):
                failures["transparent"] = (
                    f"stored {stored_transparent:,}, the node's transparent pool changed by {pools.get('transparent'):,}"
                )
    if stored["negative_fee"]:
        failures["fees"] = f"{len(stored['negative_fee'])} negative fees, e.g. {stored['negative_fee'][0]}"
    if sink == "delta":
        evaluated.add("stored-fee")
        if stored["fee_inconsistent"]:
            failures["stored-fee"] = f"{len(stored['fee_inconsistent'])} rows, e.g. {stored['fee_inconsistent'][0]}"
    if height in expected:
        evaluated.add("dataset")
        found = {txid: values for txid, *values in stored.get("detail", [])}
        wrong = [txid for txid, values in expected[height].items() if found.get(txid) != values]
        if wrong:
            failures["dataset"] = f"{len(wrong)} of {len(expected[height])} transactions differ, e.g. {wrong[0]}"
    return evaluated, failures


def run(root, arguments, echo=print, status=None, echo_stderr=None):
    """Run the check and return the exit status."""
    started = time.time()
    start, end = arguments.start, arguments.end
    if start < 0 or end < start:
        raise CheckError(f"not a block range: {start} to {end}")
    manifest, _ = manifest_module.load(root)
    expected = {height: values for height, values in dataset_expectations(manifest).items() if start <= height <= end}
    sinks = sorted(set(arguments.sink or ["cassandra"]))
    report_dir = arguments.report_dir or os.path.join(os.getcwd(), "report", f"check-{start}-{end}")
    os.makedirs(report_dir, exist_ok=True)
    client = node_module.connect(arguments)
    info = client.call("getinfo", [])
    echo(f"node        {info.get('subversion')}, height {info.get('blocks')}")
    if isinstance(info.get("blocks"), int) and end > info["blocks"]:
        raise CheckError(f"the node is at height {info['blocks']}; the range ends at {end}")
    job = {
        "environment": arguments.gs_env,
        "currency": "zec",
        "sinks": sinks,
        "delta_directory": arguments.delta_path,
        "detail_heights": sorted(expected),
    }
    tallies = {sink: {rule: {"checked": 0, "failed": 0} for rule in RULES} for sink in sinks}
    totals = {sink: {"blocks": 0, "transactions": 0, "coinjoin_transactions": 0} for sink in sinks}
    failing_path = os.path.join(report_dir, "failing-blocks.csv")
    examples = {sink: {} for sink in sinks}
    with tempfile.TemporaryDirectory(prefix="gs-zec-validate-", dir=report_dir) as scratch:
        worker = _Worker(arguments.gs_python, job, scratch, arguments.gs_config, echo_stderr)
        try:
            if not worker.ready.get("ready"):
                error = worker.ready["error"]
                raise CheckError(f"the stored data cannot be read: {error['type']}: {error['message']}")
            echo(
                f"graphsense  graphsense-lib {worker.ready['graphsense_lib']}, raw keyspace {worker.ready['raw_keyspace']}"
                + (f", Delta lake {worker.ready['delta_directory']}" if "delta_directory" in worker.ready else "")
            )
            with open(failing_path, "w", encoding="utf-8", newline="") as handle:
                out = csv.writer(handle, lineterminator="\n")
                out.writerow(["height", "sink", "rule", "detail"])
                pieces = capture.chunks(start, end, size=PIECE)
                for number, (low, high) in enumerate(pieces, 1):
                    if status:
                        status(f"blocks {low:,}-{high:,} ({number}/{len(pieces)})")
                    node = node_blocks(client, low, high, arguments.rpc_workers)
                    answer = worker.ask(low, high)
                    if answer.get("exception"):
                        error = answer["exception"]
                        raise CheckError(f"reading blocks {low:,}-{high:,} failed: {error['type']}: {error['message']}")
                    for sink in sinks:
                        for key, stored in answer["sinks"][sink].items():
                            height = int(key)
                            totals[sink]["blocks"] += 1
                            totals[sink]["transactions"] += stored["transactions"]
                            totals[sink]["coinjoin_transactions"] += stored["coinjoin_transactions"]
                            evaluated, failures = judge(height, node[height], stored, sink, expected)
                            for rule in RULES:
                                if rule not in evaluated:
                                    continue
                                tallies[sink][rule]["checked"] += 1
                                if rule in failures:
                                    tallies[sink][rule]["failed"] += 1
                                    out.writerow([height, sink, rule, failures[rule]])
                                    examples[sink].setdefault(rule, []).append(f"{height}: {failures[rule]}")
            worker.close()
        finally:
            worker.kill()
    failed = any(t["failed"] for sink in sinks for t in tallies[sink].values())
    results = {
        "check_format": 1,
        "tool": report.tool_identity(),
        "range": {"start": start, "end": end, "blocks": end - start + 1},
        "sinks": sinks,
        "rules": {sink: {rule: t for rule, t in tallies[sink].items() if t["checked"]} for sink in sinks},
        "stored": totals,
        "examples": {sink: {rule: items[:10] for rule, items in examples[sink].items()} for sink in sinks},
        "verdict": "fail" if failed else "pass",
    }
    document = {
        "results": results,
        "results_sha256": report.checksum(results),
        "run": {
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "seconds": round(time.time() - started),
            "node": info.get("subversion"),
            "graphsense_lib": worker.ready.get("graphsense_lib"),
            "raw_keyspace": worker.ready.get("raw_keyspace"),
            "python": platform.python_version(),
        },
    }
    problems = report.validate_report(root, document, SCHEMA_PATH)
    if problems:
        raise CheckError(f"internal error: the report does not follow {SCHEMA_PATH}: {problems[0]}")
    json_path = os.path.join(report_dir, "check.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, indent=1, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    echo("")
    echo(f"----- {PROG} check -----")
    echo(f"tool        {PROG} {__version__}")
    echo(f"range       {start:,} to {end:,} ({end - start + 1:,} blocks)")
    for sink in sinks:
        echo(
            f"{sink:<11} {totals[sink]['transactions']:,} transactions stored, {totals[sink]['coinjoin_transactions']:,} flagged coinjoin"
        )
        for rule, t in tallies[sink].items():
            if t["checked"]:
                mark = "PASS" if not t["failed"] else "FAIL"
                echo(f"  [{mark}] {RULES[rule]} (blocks: {t['checked']:,} checked, {t['failed']:,} failed)")
                for example in examples[sink].get(rule, [])[:3]:
                    echo(f"           - block {example}")
    echo(f"verdict     {'FAIL' if failed else 'PASS'} (exit status {1 if failed else 0})")
    echo("-" * 23)
    echo(f"report      {json_path}")
    echo(f"            {failing_path}")
    return 1 if failed else 0
