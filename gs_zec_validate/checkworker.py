"""Check worker: reads what an operator's GraphSense stored for a block range and sums it up per block.

Started by `check` with the operator's own interpreter, where graphsense-lib (with its `ingest` extra) is installed:

    <python> -I checkworker.py <job.json>

It reads graphsense-lib's configuration (GRAPHSENSE_CONFIG_YAML, or graphsense-lib's default location) and uses
graphsense-lib's own database access for the Cassandra raw keyspace and the `deltalake` package for the Delta
lake. It needs no node: the tool sends it nothing but block ranges. Protocol as in worker.serve: one JSON object
per line; {"ready": ...}, then one {"done": ...} per {"start": a, "end": b} request, then {"finished": ...}.

Per block and sink it returns sums only, plus the transactions that break a rule. It contains no accounting: the
tool compares the sums with the node.
"""

import json
import os
import sys

SHIELDED = 12  # graphsense-lib's address type of shielded inputs and outputs (ingest/utxo.py, _address_types)


def describe(error):
    return {"type": type(error).__name__, "message": str(error)}


def empty():
    return {
        "transactions": 0,
        "coinbase_transactions": 0,
        "coinjoin_transactions": 0,
        "shielded_in": 0,
        "shielded_out": 0,
        "transparent_in": 0,
        "transparent_out": 0,
        "negative_fee": [],
        "totals_inconsistent": [],
        "fee_inconsistent": [],
        "null_values": [],
    }


def add(summary, txid, coinbase, coinjoin, total_input, total_output, fee, inputs, outputs, detail):
    """Add one stored transaction. `inputs`/`outputs` are (value, is_shielded) pairs; `fee` is None for Cassandra."""
    summary["transactions"] += 1
    summary["coinbase_transactions"] += bool(coinbase)
    summary["coinjoin_transactions"] += coinjoin is True
    values = [value for value, _ in inputs] + [value for value, _ in outputs]
    if any(value is None for value in values) or total_input is None or total_output is None:
        summary["null_values"].append(txid)
        return
    for value, shielded in inputs:
        summary["shielded_in" if shielded else "transparent_in"] += value
    for value, shielded in outputs:
        summary["shielded_out" if shielded else "transparent_out"] += value
    if total_input != sum(value for value, _ in inputs) or total_output != sum(value for value, _ in outputs):
        summary["totals_inconsistent"].append(txid)
    derived = 0 if coinbase else total_input - total_output
    if not coinbase and derived < 0:
        summary["negative_fee"].append(txid)
    if fee is not None and fee != derived:
        summary["fee_inconsistent"].append(txid)
    if detail is not None:
        detail.append([txid, total_input, total_output, derived if fee is None else fee])


class CassandraReader:
    name = "cassandra"

    def __init__(self, db):
        self.raw = db.raw
        self.block_bucket = self.raw.get_block_bucket_size()
        self.tx_bucket = self.raw.get_tx_bucket_size()
        self.keyspace = self.raw.get_keyspace()

    def read(self, low, high, detail_heights):
        blocks = {}
        for height in range(low, high + 1):
            summary = empty()
            row = self.raw.select_one_safe(
                "block_transactions",
                columns=["txs"],
                where={"block_id_group": self.raw.get_id_group(height, self.block_bucket), "block_id": height},
            )
            summary["present"] = row is not None
            detail = [] if height in detail_heights else None
            ids = [item.tx_id for item in (row.txs or [])] if row is not None else []
            futures = [
                self.raw.select_async_safe(
                    "transaction",
                    where={"tx_id_group": self.raw.get_id_group(tx_id, self.tx_bucket), "tx_id": tx_id},
                )
                for tx_id in ids
            ]
            for tx_id, future in zip(ids, futures, strict=True):
                rows = list(future.result())
                if len(rows) != 1:
                    summary.setdefault("missing_transactions", []).append(tx_id)
                    continue
                tx = rows[0]
                add(
                    summary,
                    tx.tx_hash.hex(),
                    tx.coinbase,
                    tx.coinjoin,
                    tx.total_input,
                    tx.total_output,
                    None,
                    [(item.value, item.address_type == SHIELDED) for item in tx.inputs or []],
                    [(item.value, item.address_type == SHIELDED) for item in tx.outputs or []],
                    detail,
                )
            if detail is not None:
                summary["detail"] = detail
            blocks[height] = summary
        return blocks


class DeltaReader:
    name = "delta"

    def __init__(self, directory, storage_options):
        import deltalake  # ty: ignore[unresolved-import]
        from pyarrow import dataset  # ty: ignore[unresolved-import]

        self.directory = directory
        self.dataset = dataset
        self.transactions = deltalake.DeltaTable(
            f"{directory}/transaction", storage_options=storage_options
        ).to_pyarrow_dataset()
        self.blocks = deltalake.DeltaTable(f"{directory}/block", storage_options=storage_options).to_pyarrow_dataset()

    def _rows(self, table, low, high, columns):
        field = self.dataset.field
        partitions = list(range(low // 100_000, high // 100_000 + 1))
        condition = field("partition").isin(partitions) & (field("block_id") >= low) & (field("block_id") <= high)
        return table.to_table(columns=columns, filter=condition).to_pylist()

    def read(self, low, high, detail_heights):
        blocks = {height: empty() for height in range(low, high + 1)}
        block_rows = {}
        for row in self._rows(self.blocks, low, high, ["block_id"]):
            block_rows[row["block_id"]] = block_rows.get(row["block_id"], 0) + 1
        details = {height: [] for height in detail_heights if low <= height <= high}
        columns = [
            "block_id",
            "tx_hash",
            "coinbase",
            "coinjoin",
            "total_input",
            "total_output",
            "fee",
            "inputs",
            "outputs",
        ]
        for tx in self._rows(self.transactions, low, high, columns):
            height = tx["block_id"]
            add(
                blocks[height],
                tx["tx_hash"].hex(),
                tx["coinbase"],
                tx["coinjoin"],
                tx["total_input"],
                tx["total_output"],
                tx["fee"],
                [(item["value"], item["type"] == "shielded") for item in tx["inputs"] or []],
                [(item["value"], item["type"] == "shielded") for item in tx["outputs"] or []],
                details.get(height),
            )
        for height, summary in blocks.items():
            summary["present"] = height in block_rows
            summary["block_rows"] = block_rows.get(height, 0)
            if height in details:
                summary["detail"] = details[height]
        return blocks


def _delta_options(config, sink):
    if not sink.directory.startswith(("s3://", "s3a://")):
        return {}
    options = {"AWS_ALLOW_HTTP": "true", "AWS_S3_ALLOW_UNSAFE_RENAME": "false", "AWS_CONDITIONAL_PUT": "etag"}
    options.update(config.get_s3_credentials(sink.s3_config) or {})
    return options


def main(job_path):
    with open(job_path, encoding="utf-8") as handle:
        job = json.load(handle)
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)

    def send(message):
        protocol.write(json.dumps(message, default=repr) + "\n")
        protocol.flush()

    readers, closing = [], []
    try:
        import graphsenselib  # ty: ignore[unresolved-import]
        from graphsenselib.config import get_config  # ty: ignore[unresolved-import]

        config = get_config()
        environment = config.get_environment(job["environment"])
        keyspace = config.get_keyspace_config(job["environment"], job["currency"])
        described = {"graphsense_lib": graphsenselib.__version__, "raw_keyspace": keyspace.raw_keyspace_name}
        if "cassandra" in job["sinks"]:
            from graphsenselib.db import DbFactory  # ty: ignore[unresolved-import]

            db = DbFactory().from_config(job["environment"], job["currency"]).__enter__()
            closing.append(db)
            readers.append(CassandraReader(db))
            described["cassandra_nodes"] = len(environment.cassandra_nodes)
        if "delta" in job["sinks"]:
            sinks = keyspace.ingest_config.raw_keyspace_file_sinks if keyspace.ingest_config else {}
            directory = job.get("delta_directory") or (sinks["delta"].directory if "delta" in sinks else None)
            if directory is None:
                raise ValueError(
                    f"no Delta lake configured for {job['currency']} in environment {job['environment']} "
                    "(raw_keyspace_file_sinks.delta.directory); pass --delta-path"
                )
            options = (
                _delta_options(config, sinks["delta"]) if "delta" in sinks and not job.get("delta_directory") else {}
            )
            readers.append(DeltaReader(directory, options))
            described["delta_directory"] = directory
    except Exception as error:  # noqa: BLE001  (a reader that cannot start is the result)
        send({"ready": False, "error": describe(error)})
        return 0
    send({"ready": True, **described})
    detail_heights = set(job.get("detail_heights", []))
    try:
        for line in sys.stdin:
            request = json.loads(line)
            low, high = request["start"], request["end"]
            try:
                result = {reader.name: reader.read(low, high, detail_heights) for reader in readers}
            except Exception as error:  # noqa: BLE001
                send({"done": [low, high], "exception": describe(error)})
                continue
            send({"done": [low, high], "exception": None, "sinks": result})
    finally:
        for db in closing:
            db.__exit__(None, None, None)
    send({"finished": True})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
