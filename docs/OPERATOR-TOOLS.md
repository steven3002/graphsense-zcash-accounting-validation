# Tools for operators: compare-range, scan, check

Three commands help with the repair that [OPERATOR-GUIDE.md](OPERATOR-GUIDE.md) describes. They work on your own
node and, for `check`, on your own GraphSense data. Unlike `run`, they use the network: the node's JSON-RPC
endpoint (not `compare-range --from-capture`), and for `check` your Cassandra cluster.

| Command | Question it answers | Needs |
|---|---|---|
| `compare-range` | What does the correction change in this block range? | the node; `install --with-transformer` |
| `scan` | Which blocks of this range hold Orchard or Ironwood value, and which transactions could change their coinjoin flag? | the node; `install --with-transformer` |
| `check` | Do the rows my GraphSense stored for this range agree with the node? | the node; your graphsense-lib installation and configuration |

## Node access and credentials

All three take the options of `verify-node`: `--rpc-url`, and either `--rpc-cookie-file` (a file holding
`user:password`, such as a node's `.cookie`) or `--rpc-user` with the password in an environment variable
(`--rpc-password-env`, default `GS_ZEC_VALIDATE_RPC_PASSWORD`). The credentials stay in the tool's own process:
they are sent as HTTP Basic authentication and never printed, logged or written to a file. GraphSense itself never
talks to the node in `compare-range` and `scan`: it reads a capture through a server on 127.0.0.1, and the worker's
audit hook refuses every other connection.

The node needs a transaction index (Zebra always has one) for `compare-range`. At most `--rpc-workers` requests (1
to 4, default 2) are sent at the same time.

Every command shows one changing line on a terminal while it works and prints the result at the end.
`--verbose` also prints what GraphSense writes to standard error, and no changing line.

## compare-range

```
uv run gs-zec-validate.py install --with-transformer
uv run gs-zec-validate.py compare-range --start <height> --end <height> --rpc-url <url> --rpc-cookie-file <file>
```

1. Asks the node for every block of the range (`getblock <height> 2`) and for every transaction whose outputs the
   range spends and does not itself contain (`getrawtransaction <txid> 1`), and stores the answers as a capture.
2. Runs graphsense-lib 2.16.3 and then 2.16.4, each in its environment, on the capture: GraphSense's own
   `BtcBlockExporter` (parser and input resolution, one exporter for the whole range, in pieces of at most 1,000
   blocks that end at multiples of 1,000, as graphsense-lib's ingest batches Zcash) and GraphSense's own
   `TransformerUTXO`, which makes the row GraphSense stores.
3. Compares the two rows of every transaction and writes the summary.

`--before-python`/`--after-python` (another environment's interpreter) and `--before-src`/`--after-src` (a
directory that contains a `graphsenselib` package) run other revisions, for example the graphsense-lib you
re-ingest with. `--keep-capture DIR` keeps the node's answers; `--from-capture DIR` runs again without a node, for
the same range or a shorter one that starts at the same block.

What runs in the tool itself, not in GraphSense: the comparison, and the two figures of GraphSense's Spark job that
exist only in Scala, reproduced in `gs_zec_validate/relations.py` with the Scala lines cited there:

- **Relation estimates**, `round(inValue / totalInput' * outValue)` per sending and receiving address
  (`Transformator.plainAddressRelations`). Spark rounds half up; graphsense-lib's delta updater computes the same
  formula with Python's `round` (half to even), so on an exact half the two differ by one zatoshi. On five windows
  (979,249 pairs) the tool's figures equal the delta updater's except in 148 such ties. On a 400-block sample
  transformed by GraphSense's own Spark job they equal Spark's on every pair once Spark's join with address ids is
  taken into account: a keyspace that does not start at block 0 has no id for senders funded before it
  ([OPERATOR-GUIDE.md](OPERATOR-GUIDE.md#a2-full-transform-of-the-old-and-the-corrected-rows), A2).
- **Spark's clustering input set**, the distinct single-address inputs of a transaction, without coinjoin
  transactions when coinjoin filtering is on (`Transformator.plainAddressCluster`). graphsense-lib's own
  `multi_input_address_set` runs in the worker and is reported as well.

Files written to `--report-dir` (default `report/compare-<start>-<end>`):

- `summary.json`, validated against `manifest/compare-summary.schema.json` before it is written;
- `summary.md`, the same for reading;
- `changed-transactions.csv`, one line per transaction whose totals, fee, counts, coinjoin flag or relation
  estimates differ: the values of both revisions and the corrected per-pool balances.

| Exit status | Meaning |
|---:|---|
| 0 | The summary was written; every difference is explained by the Orchard and Ironwood values. |
| 1 | A difference is not explained, or the results differ from the summary published in `results/` for the range. |
| 2 | The command line was wrong. |
| 3 | The comparison could not run: node, capture, environments, or a revision raised an exception. |

Memory: each revision runs in a process that holds graphsense-lib's output cache, which keeps every output of every
transaction fetched with `getrawtransaction`. Peaks measured for 10,000 blocks: 0.4 to 2.9 GB, highest for recent
blocks. The two revisions run one after the other.

### The summary

`summary.json` has a part that depends only on the node's data for the range, the code of the two revisions and
the tool's version (`results`), its checksum (`results_sha256`, defined as for the validation report in
[REPORT.md](REPORT.md)), and facts about the run.

| Member | Content |
|---|---|
| `results.summary_format` | 1 |
| `results.tool` | `name` and `version` of this tool. |
| `results.range` | `start`, `end`, `blocks`. |
| `results.canonical` | `true` if both revisions are the pinned 2.16.3 and 2.16.4. |
| `results.revisions` | Per side: `pinned` (`version`, `git_commit`, `package_tree_sha256` of the pinned release), `found` (`package_tree_sha256`, `package_file_count` and per accounting file `files` with `sha256` and `git_blob` of what ran), `is_pinned_code`. |
| `results.transactions` | `total`, `coinbase`, `non_coinbase`. |
| `results.changed` | `transactions` whose `total_input`, `total_output` or `fee` differ; the same per field, and for `input_count` and `output_count`; `blocks_with_changed_transactions`; `coinbase_total_output`, coinbase transactions whose total output differs. |
| `results.fees_of_non_coinbase_transactions` | Per side, `negative` and `zero` fees. |
| `results.newly_accounted` | For `orchard` and `ironwood`: `transactions` with a non-zero balance, `out_of_pool` (sum of positive balances, new shielded inputs), `into_pool` (sum of negated negative balances, new shielded outputs). |
| `results.coinjoin_flags` | Flagged transactions `before` and `after`, `false_to_true`, `true_to_false`, and the `flagged_transactions` of each side. |
| `results.clustering_input_sets_changed` | Transactions whose clustering input set differs, `with_coinjoin_filter` and `without_coinjoin_filter`, for `multi_input_address_set` (graphsense-lib) and `spark_regular_inputs` (the Spark job). |
| `results.clustering_edges` | Per side, transactions that contribute a clustering edge (`multi_input_address_set`), with and without the coinjoin filter. |
| `results.relation_estimates` | `pairs_before`, `pairs_after` (sending-receiving pairs per transaction), `pairs_changed`, `pairs_only_before`, `pairs_only_after`, `transactions_with_changed_pairs`, `estimated_value_before_of_changed` and `estimated_value_after_of_changed` (sums over the changed pairs), `absolute_change` (sum of the absolute differences), `largest_change`. |
| `results.differences_explained` | `unexplained`: transactions whose two rows differ in a way the Orchard and Ironwood legs do not explain; `examples`: up to 20 of them. |
| `results.unresolved_inputs` | Per side, inputs that stayed unresolved (must be 0). |
| `results.records_sha256` | Per side, sha256 over the compared fields of every transaction, in order. Equal values mean equal rows. |
| `results_sha256` | The checksum of `results`. |
| `published_results_sha256`, `matches_published` | The checksum of the summary in `results/` for the same range, if the repository has one and the run is canonical, and whether the two are equal. |
| `verdict`, `problems` | `pass` or `fail`, and why. |
| `run` | `started_utc`, `seconds`, `node` (the node's version), `python`, `platform`, `graphsense_lib_versions`, `worker_answers` (requests the replay answered per method). No host name, user name or path. |

## scan

```
uv run gs-zec-validate.py scan --start <height> --end <height> --rpc-url <url> --rpc-cookie-file <file>
```

Asks the node for every block (`getblock <height> 2`), lets both revisions parse it without resolving inputs, and
deletes each piece once it is processed, so any range fits on disk. Per block it counts the transactions with an
Orchard or Ironwood value (from the corrected parser's per-pool fields) and sums their values.

**Coinjoin screen.** Both revisions also evaluate graphsense-lib's `is_coinjoin` on each transaction with every
transparent input given an address of its own. `is_coinjoin` reads addresses only to count the distinct ones, and
that count can only rise, so the result is an upper bound of the real flag: where it is false, the transaction is not
flagged whatever its inputs' addresses are. A transaction can change its flag with the correction only if it carries
an Orchard or Ironwood value; those among them for which either bound is true are the `candidate_transactions`.
`compare-range` on their blocks decides them.

Files written to `--report-dir` (default `report/scan-<start>-<end>`): `summary.json` (validated against
`manifest/scan-summary.schema.json`) and `blocks.csv`, one line per block with at least one affected transaction
(`height`, `transactions`, `affected_transactions`, per pool `*_transactions`, `*_out_of_pool`, `*_into_pool`,
`coinjoin_candidates`).

| Member | Content |
|---|---|
| `results.summary_format`, `results.tool`, `results.range`, `results.canonical`, `results.revisions` | As in compare-range. |
| `results.transactions` | Transactions in the range. |
| `results.orchard`, `results.ironwood` | `blocks` and `transactions` with a non-zero balance of that pool, `out_of_pool`, `into_pool`. |
| `results.affected` | `blocks` and `transactions` with an Orchard or Ironwood value. |
| `results.coinjoin_screen` | `possible_before`, `possible_after` (transactions whose bound is true), `candidates` and `candidate_transactions` (`[height, txid]`). |
| `run` | `started_utc`, `seconds`, `node`, `python`, `graphsense_lib_versions`, `requests` (sent to the node). |

| Exit status | Meaning |
|---:|---|
| 0 | The summary was written. |
| 2 | The command line was wrong. |
| 3 | The scan could not run: node, environments, or a revision raised an exception. |

## check

```
uv run gs-zec-validate.py check --gs-python <interpreter> [--gs-config <graphsense.yaml>] --gs-env <environment> \
    [--sink cassandra] [--sink delta] [--delta-path <dir>] --start <height> --end <height> --rpc-url <url> --rpc-cookie-file <file>
```

`--gs-python` is the interpreter of the environment in which you run graphsense-lib (with its `ingest` extra).
`check` starts `gs_zec_validate/checkworker.py` with it, so the stored rows are read with graphsense-lib's own
database access (Cassandra) and with `deltalake` (Delta lake), using your configuration: `--gs-config`, or what
graphsense-lib itself would read (`GRAPHSENSE_CONFIG_YAML`, else `.graphsense.yaml` in the current directory, else
`~/.graphsense.yaml`). The Delta directory comes from `raw_keyspace_file_sinks.delta.directory` unless `--delta-path`
names a local one; a lake on S3 is read with graphsense-lib's S3 settings for that sink (only local lakes have been
tested). The tool never sees your database credentials; the check worker never sees the node.

For every block the tool asks the node for `getblock <height> 1` (transaction count and `valuePools`) and compares
the stored rows with it. The rules, per block and sink:

| Rule | Holds when |
|---|---|
| `rows` | there is one stored transaction per transaction of the block, none missing and none twice (and, in the Delta lake, one block row) |
| `shielded` | shielded outputs minus shielded inputs equal the node's change of the Sprout, Sapling, Orchard and Ironwood pools together |
| `transparent` | transparent outputs minus transparent inputs equal the node's change of the transparent pool |
| `fees` | no non-coinbase transaction has a negative fee (total input minus total output) |
| `totals` | every row's total input and total output equal the sums of its inputs and outputs |
| `stored-fee` | (Delta lake) every stored fee equals total input minus total output, 0 for a coinbase |
| `dataset` | for the blocks of this repository's dataset, every transaction's total input, total output and fee equal the dataset's expected values |

`shielded` and `transparent` are not evaluated in a block that fails `rows` or has a row with a missing value:
their sums would be incomplete.

Files written to `--report-dir` (default `report/check-<start>-<end>`): `check.json` (validated against
`manifest/check-report.schema.json`) and `failing-blocks.csv` (`height`, `sink`, `rule`, `detail`).

| Member | Content |
|---|---|
| `results.check_format` | 1 |
| `results.tool`, `results.range` | As above. |
| `results.sinks` | The sinks checked. |
| `results.rules` | Per sink and rule: the blocks where the rule was evaluated (`checked`) and where it failed (`failed`). |
| `results.stored` | Per sink: `blocks`, `transactions` and `coinjoin_transactions` (flagged coinjoin). |
| `results.examples` | Per sink and rule, up to ten failing blocks with the detail. |
| `results.verdict` | `pass` or `fail`. |
| `results_sha256` | The checksum of `results`. |
| `run` | `started_utc`, `seconds`, `node`, `python`, `graphsense_lib`, `raw_keyspace`. |

| Exit status | Meaning |
|---:|---|
| 0 | Every rule held in every block of the range. |
| 1 | At least one rule failed in at least one block. |
| 2 | The command line was wrong. |
| 3 | The check could not run: node, configuration, or the stored data cannot be read. |

## Captures

A capture (`compare-range --keep-capture`) is a directory:

| File | Content |
|---|---|
| `capture.json` | Written last: format, range, number of blocks and previous transactions, the node's version, time, checksums of the two indexes. |
| `blocks.tsv` | One line per block: height, hash, sha256 and length of the content. |
| `transactions.tsv` | One line per previous transaction: txid, sha256 and length of the content. |
| `blocks/<height>.json.gz` | The bytes of the `result` member of `getblock <height> 2`, as the node sent them. |
| `transactions/<xx>/<txid>.json.gz` | The same for `getrawtransaction <txid> 1`. |

Files are gzip-compressed without a time or file name in the header. Every file is checked against its index when
it is read. Two captures of the same range taken at different times differ in `confirmations` only, which no
accounting reads; the summary's `results` do not depend on it.
