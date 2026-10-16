# Repairing the Zcash history in GraphSense: operator guide

For operators of GraphSense who ingested Zcash with graphsense-lib 2.16.3 or older. It says which history must be
ingested again, how to do it in the Cassandra raw keyspace and in the Delta lake, why the derived data must then be
rebuilt from scratch, what changes in the data, and how to check the result.

Every statement rests on a test described in [the appendix](#appendix-how-the-statements-were-tested), on source code
(graphsense-lib 2.17.0, including graphsense-spark in its `spark/` directory, unless another version or project is
named; file and line given), on a changelog (quoted), or on a statement of GraphSense's maintainer (linked). What was
not run here says so.

## Contents

1. [What was wrong and what fixes it](#1-what-was-wrong-and-what-fixes-it)
2. [Which graphsense-lib to use](#2-which-graphsense-lib-to-use)
3. [What is affected](#3-what-is-affected)
4. [Before you start](#4-before-you-start)
5. [Re-ingestion](#5-re-ingestion)
6. [Re-transformation: a full rebuild](#6-re-transformation-a-full-rebuild)
7. [Representative before-and-after results](#7-representative-before-and-after-results)
8. [What changes in your data](#8-what-changes-in-your-data)
9. [Checks](#9-checks)
10. [Parser outputs and stored fields](#10-parser-outputs-and-stored-fields)
11. [A positive shielded balance is not necessarily an unshielding](#11-a-positive-shielded-balance-is-not-necessarily-an-unshielding)
12. [The documented environment, and reporting a problem](#12-the-documented-environment-and-reporting-a-problem)
13. [What this guide does not cover](#13-what-this-guide-does-not-cover)
- [Appendix: how the statements were tested](#appendix-how-the-statements-were-tested)

## 1. What was wrong and what fixes it

A Zcash transaction can move value into or out of four shielded pools: Sprout, Sapling, Orchard (from NU5) and
Ironwood (from NU6.3). graphsense-lib up to 2.16.3 read the Sprout and Sapling values, but not the value of the
Orchard and Ironwood bundles. Wherever a transaction had a non-zero Orchard or Ironwood balance, that leg was missing
from its inputs or outputs, and its total input, total output and fee were wrong
([graphsense-lib #126](https://github.com/graphsense/graphsense-lib/issues/126)). graphsense-lib 2.16.4 corrected the
parser ([pull request #163](https://github.com/graphsense/graphsense-lib/pull/163)).

GraphSense creates its shielded inputs and outputs while it ingests. Rows ingested before the correction keep their
old values until they are ingested again, and everything derived from them keeps them until it is rebuilt.

The [README](../README.md) of this repository shows on 47 mainnet transactions that 2.16.4 reports the values the
node's own data gives, and that 2.16.3 does not.

## 2. Which graphsense-lib to use

| Release | Orchard and Ironwood values | Notes |
|---|---|---|
| up to 2.15.0 | not read | Ingest fails from NU6.3 on: "failed every block from activation onward with `Unknown fields in transaction response: ironwood`" (graphsense-lib CHANGELOG, 2.15.1). |
| 2.15.1 to 2.16.3 | not read | Ironwood bundles are accepted and ignored. |
| 2.16.4 (2026-09-22) | read | First release with the correction. |
| 2.17.0 (2026-09-30) | read, identical to 2.16.4 | |
| **2.18.1** (2026-10-15; 2.18.0 the same day) | read, identical to 2.16.4 | Latest release on 2026-10-15. Adds the three changes below. |

**Use 2.18.1.** Its Zcash ingest code is that of graphsense-lib `develop` at `ea65eb6` (no file under
`src/graphsenselib/ingest`, `db`, `utils`, `schema` or `spark` differs between the two), and on the five windows of
section 7 2.18.1 and `develop` give every transaction exactly the row 2.16.4 gives, as 2.17.0 does (A3). The code that
decides what a re-ingestion does to rows already stored (`ingest/dump.py`, `ingest/delta/sink.py`,
`ingest/transform.py`, `db/utxo.py`, `utils/locking.py`) is byte for byte that of 2.17.0, with which section 5 was
tested (A1). For full transforms use graphsense-spark `spark-v26.09.1` or later (graphsense-lib's CHANGELOG, 2.17.0,
"Upgrade notes"). A later release can be compared with 2.16.4 on any range of your node before you use it
(`compare-range --after-python`, [section 9.2](#92-what-the-correction-changes-in-a-range)).

What 2.18.0 adds to the UTXO ingest (graphsense-lib CHANGELOG, 2.18.0):

- A node answer that lacks a required amount raises an error instead of counting as zero: "Hardening only; no node
  emits these shapes and the parsed output is unchanged on all mainnet ZEC blocks."
- With `resolve_inputs_via_cassandra: true` (the default is `false`), the stored `fee` is recomputed after the inputs
  are resolved ([#183](https://github.com/graphsense/graphsense-lib/issues/183)), and the Delta lake can be written
  ([#189](https://github.com/graphsense/graphsense-lib/issues/189)). With 2.17.0 that setting cannot write to the lake
  (`ArrowTypeError`), and once it could, the lake would keep a fee computed before the inputs were resolved; a
  Cassandra-only ingest is affected by neither, since the failing write is the lake's and the Cassandra raw keyspace
  has no fee column.

So: **with 2.18.1, either input resolution; with 2.17.0, the default** (`resolve_inputs_via_cassandra: false`;
inputs are resolved through the node's `getrawtransaction`). The Cassandra resolver was not run here.

## 3. What is affected

| Pool | Value on the chain from | Read by graphsense-lib from |
|---|---|---|
| Sprout | block 0 | always |
| Sapling | block 419,200 | always |
| Orchard | **block 1,687,104** (NU5) | 2.16.4 |
| Ironwood | **block 3,428,143** (NU6.3) | 2.16.4 |

The activation heights are those the node reports in `getblockchaininfo`.

- Sprout and Sapling values are not changed by the correction. On the five windows of section 7 (261,198
  transactions) every difference between the two releases is exactly an Orchard or Ironwood leg, and re-ingesting
  200 blocks before NU5 with 2.17.0 changed no stored value (A1, step 8).
- Orchard is used after 3,428,143 as well. **Every repair therefore starts at 1,687,104**: a re-ingestion from
  3,428,143 leaves the Orchard values of the blocks before it wrong (A1, step 15).

How much of the history carries Orchard or Ironwood value, from block 1,687,104 to block 3,518,201 (A4):

| | 1,687,104 – 3,428,142 | 3,428,143 – 3,518,201 | Whole range |
|---|---:|---:|---:|
| Blocks | 1,741,039 | 90,059 | 1,831,098 |
| Blocks with Orchard or Ironwood value | 559,134 | 59,035 | 618,169 |
| Transactions | 7,350,116 | 697,923 | 8,048,039 |
| Transactions with Orchard or Ironwood value | 1,204,721 | 319,883 | 1,524,604 |
| Orchard: transactions | 1,204,721 | 39,981 | 1,244,702 |
| Orchard: zat out of the pool | 1,732,940,505,687,584 | 331,044,087,914,049 | 2,063,984,593,601,633 |
| Orchard: zat into the pool | 2,099,063,847,027,562 | 0 | 2,099,063,847,027,562 |
| Ironwood: transactions | 0 | 305,705 | 305,705 |
| Ironwood: zat out of the pool | 0 | 83,137,783,870,225 | 83,137,783,870,225 |
| Ironwood: zat into the pool | 0 | 494,395,701,504,533 | 494,395,701,504,533 |

Ironwood value appears from 3,428,143 on, as it must. From that block on no transaction moved value into Orchard, and
39,981 transactions moved 331,044,087,914,049 zat out of it. Every block's figures equal the node's own change of the
Orchard and Ironwood pools (A4).

## 4. Before you start

- **Install graphsense-lib 2.18.1** with the `ingest` extra: `pip install 'graphsense-lib[ingest]==2.18.1'`. Where
  you run the full transform, also the `transformation` extra and a Java runtime.
- **Keep the node synchronised.** Re-ingestion resolves transparent inputs with `getrawtransaction`, as the regular
  ingest does.
- **Pause the regular ingest job** for the keyspace and the lake while you re-ingest. A re-ingestion takes the same
  locks as the regular ingest (the raw keyspace's name for Cassandra, `delta_ingest_local_<path>_zec` for a local lake;
  `CassandraSink.lock_name`, `DeltaDumpWriter.lock_name`); whichever starts second waits one second and then gives
  up with exit status 911 (`utils/locking.py`, `create_lock`; `ingest/cli.py`).
- **Plan memory.** The exporter keeps every output of every transaction it fetches in a cache bounded by its number
  of entries, not by size; graphsense-lib commit `9656a781` says "the cache can grow to tens of GB" on Zcash. Here a
  re-ingestion of 5,000 recent blocks reached 2.1 GB (A6). Several shorter runs keep it smaller.
- **Record what you want to compare later**: the coinjoin counts and cluster summary of
  [section 9.4](#94-coinjoin-flags-and-clusters-before-and-after-the-rebuild).

## 5. Re-ingestion

All commands are graphsense-lib's own. `-e prod` stands for your environment.

### 5.1 Cassandra raw keyspace

```
graphsense-cli ingest from-node -e prod -c zec --sinks cassandra --start-block 1687104
```

Without `--end-block` the run continues to 150 blocks below the node's tip (graphsense-lib's reorg back-off for
Zcash, `config/config.py`), so it also ingests the blocks that arrived since the last regular run.

What it does to existing rows (A1, steps 3, 11, 15 and 16):

- Cassandra writes are upserts. Every transaction keeps its row and its `tx_id`: the run numbers transactions on from
  the highest `tx_id` of the block before `--start-block` (`ingest/transform.py` line 225;
  `db/utxo.py` lines 109-139). Transactions with an Orchard or Ironwood value get their corrected `total_input`,
  `total_output`, inputs and outputs, and the per-block summaries in `block_transactions` change with them. No row is
  added or removed.
- Running it again changes nothing.
- It needs the `block_transactions` row of the block before `--start-block`, which a keyspace ingested from block 0
  has. Without it graphsense-lib refuses: `Refusing to start tx_id allocation at 0 — this would overwrite existing
  tx_ids` (A1, step 13).
- **Let the last run end at the keyspace's highest block or later.** Every run rewrites the raw keyspace's
  `summary_statistics` row (`no_blocks`, `no_txs`) from its own end (`ingest/dump.py` line 486). After a run that
  ends earlier, that row describes a shorter keyspace until a run ends at the highest block again (A1, steps 10 and
  11). `graphsense-cli monitoring get-summary` reads it.

### 5.2 Delta lake

```
graphsense-cli ingest from-node -e prod -c zec --sinks delta --write-mode overwrite --start-block 1600000
```

The lake is divided into partitions of 100,000 blocks. Of the three write modes, only `overwrite` replaces rows that
are already there, and it replaces **whole partitions**: the first chunk written to a partition replaces everything
the partition held (`ingest/delta/sink.py` lines 254-266). Therefore:

- **Start at 1,600,000**, the first block of the partition that contains 1,687,104. In overwrite mode graphsense-lib
  refuses any start that is not a multiple of 100,000 (`ingest/dump.py` lines 302-311). Do not start at 1,687,104
  with `--ignore-overwrite-safechecks`: that deletes blocks 1,600,000 to 1,687,103 from the lake (A1, step 7).
- **Run to the end of the lake's data**: without `--end-block`, or with the lake's highest block. A run that stops
  inside a partition leaves the partition holding only the blocks written so far (A1, step 14). If a run stops, start
  again at the first block of the partition it stopped in.
- `--write-mode append` refuses a range the lake already holds: `Start block (…) must be higher than the highest block
  already written` (`ingest/dump.py` line 412; A1, step 5).
- `--write-mode merge` inserts only rows the lake does not have and leaves every existing row as it is
  (`when_not_matched_insert_all`, `ingest/delta/sink.py` line 347). It does not apply the correction (A1, step 12).

### 5.3 Both sinks in one run

```
graphsense-cli ingest from-node -e prod -c zec --sinks cassandra --sinks delta --write-mode overwrite --start-block 1600000
```

The write mode applies to the lake; Cassandra is upserted as in 5.1. Starting at 1,600,000 also re-ingests the
87,104 blocks before NU5 into Cassandra, which changes nothing there: they get the same values again (in the lake,
A1 step 8) and keep their `tx_id`s (5.1). Both sinks in one run were exercised on samples (A1, steps 14 and 15, and
A6); *not run here on the full range*.

### 5.4 If your raw keyspace is loaded from the Delta lake

`graphsense-cli transformation delta-to-raw` refuses a raw keyspace that already holds data, for every UTXO chain
(`transformation/cli.py`, `run_transformation`). After repairing the lake, load a fresh raw keyspace
(`--raw-keyspace <new name> --create-schema`) and transform from that. *Not run here.*

### 5.5 Splitting the work

- Consecutive ranges, one after the other, give the same rows as one run, as long as Delta ranges start at partition
  boundaries and the last Cassandra run ends at the highest block (sections 5.1 and 5.2).
- Two Cassandra runs on disjoint ranges can run at the same time with `--no-lock` (otherwise the second waits for the
  keyspace's lock). On a sample this gave exactly the rows of one run; the `summary_statistics` row comes from
  whichever run ends last (A1, step 16). *Parallel writes to the Delta lake were not tried.*

### 5.6 Time

Re-ingestion with 2.17.0 into both sinks (A6):

| Range | Blocks | Transactions | Seconds | Blocks per second |
|---|---:|---:|---:|---:|
| 1,686,904 – 1,687,403 (NU5 activation, including keyspace creation) | 500 | 2,939 | 33 | 15 |
| 1,750,000 – 1,750,499 (mid-2022, blocks up to 2 MB) | 500 | 4,474 | 52 | 9.6 |
| 3,505,000 – 3,509,999 (October 2026) | 5,000 | 27,893 | 135 | 37 |

Scaled to the whole range: at 9.6 blocks per second the 412,896 blocks from 1,687,104 to 2,099,999 (the large blocks
of 2022 and early 2023, which took 80% of the time of the whole-range scan in A4) take 43,010 seconds, and at 37 blocks
per second the 1,418,202 blocks from 2,100,000 to 3,518,201 take 38,330 seconds: about 23 hours for both sinks on a
machine like this one. This is arithmetic on three short measurements, not a measured run of the whole range;
splitting it into consecutive runs (section 5.5) does not change the total.

### 5.7 How to confirm a range was re-ingested with the corrected code

Run this repository's `check` command on it ([section 9.1](#91-the-stored-rows-agree-with-the-node)). Rows written by
2.16.3 or older fail its `shielded` rule in every block whose Orchard and Ironwood changes do not cancel out, which
is all but 15 of the 618,169 blocks with such value (section 9.1); rows written by 2.16.4 or later pass. In a
sample where 100 blocks had deliberately been left out of the re-ingestion, the rule failed in exactly those of
them that carry such value (A1, step 14).

## 6. Re-transformation: a full rebuild

### 6.1 Why a value refresh is not enough

The transformed keyspace aggregates the raw rows: totals and transaction counts per address and per cluster,
relation estimates summed over all transactions between two addresses or clusters, and clusters as connected
components of co-spent inputs. The corrected rows change what goes into these aggregates
([section 8](#8-what-changes-in-your-data)), and nothing in GraphSense recomputes the aggregates of history that is
already transformed:

- graphsense-lib's delta updater only moves forward. It refuses to start at or before the last block it or a full
  transform processed: "This would corrupt the state of balances" (`deltaupdate/deltaupdater.py`,
  `find_import_range`, lines 75-106).
- A relation estimate or a total is a sum over transactions, and a cluster can merge or split; one transaction's
  contribution cannot be swapped in place.

The maintainer said the same on #126: the coinjoin and cluster effect "makes the re-transform a full cluster rebuild
rather than a value refresh" ([comment of 2026-09-04](https://github.com/graphsense/graphsense-lib/issues/126#issuecomment-5539032754));
graphsense-lib's CHANGELOG for 2.16.4: "Treat the re-transform as a full cluster rebuild with possible attribution
shifts."

### 6.2 Commands

```
graphsense-cli transformation raw-to-transformed -e prod -c zec --version spark-v26.09.1
```

This creates a fresh keyspace `zec_transformed_<YYYYMMDD>` and runs graphsense-spark's job on the raw keyspace
(`transformation/cli.py`, `run_full_transform`; marked ALPHA in 2.17.0). The keyspace's `data_configuration` is passed
to the job, with graphsense-lib's defaults for what it does not set; for Zcash these are bucket size 5000, address
prefix length 4 and **coinjoin filtering on** (`config/config.py`, `get_default_data_configuration`; passed as
`--coinjoin-filtering`; graphsense-spark's `NetworkDefaults.scala` has the same), which keeps coinjoin transactions
out of clustering ([section 8.4](#84-clusters)). If that configuration sets a value that differs from these defaults,
the command stops unless you add `--override-defaults` (graphsense-lib CHANGELOG, 2.17.0, "Upgrade notes").

`transformation cluster` and the delta updater work on the transformed keyspace your configuration names
(`transformed_keyspace_name` of the environment's `zec` keyspace; `db/factory.py`, `DbFactory.from_config`;
`transformation/cli.py`, `run_clustering`), not on the newest one. When the new keyspace is complete, set
`transformed_keyspace_name` to its name. Then, if you use graphsense-lib's newer clustering (the `fresh_*` tables),
run once:

```
graphsense-cli transformation cluster -e prod -c zec
```

From then on the delta updater continues from the new keyspace's last block
(`graphsense-cli delta-update update -e prod -c zec`). GraphSense's REST service chooses the newest populated
`zec_transformed_*` keyspace by itself only when its configuration names no transformed keyspace
(`db/asynchronous/cassandra.py`, `find_latest_transformed_keyspace`); otherwise point it at the new keyspace as well.

Run here: `raw-to-transformed --local` on two raw keyspaces holding the same 400 blocks, one ingested by 2.16.3 and
one by 2.17.0 (A2); results in [section 8](#8-what-changes-in-your-data). *`transformation cluster` and the delta
updater were not run here.*

## 7. Representative before-and-after results

Produced with this repository's `compare-range` command ([section 9.2](#92-what-the-correction-changes-in-a-range)),
which runs graphsense-lib 2.16.3 and 2.16.4, unmodified, on the same node data: GraphSense's exporter with input
resolution, then its transformer, which makes the row GraphSense stores. The machine-readable summaries are in
[`../results/`](../results/); each names the code checksums of both releases.

| Window | Transactions | Changed | Negative fees | Zero fees | Orchard newly accounted (zat) | Ironwood newly accounted (zat) |
|---|---:|---:|---|---|---|---|
| 1,687,104 – 1,697,103 | 66,032 | 113 | 12 → 0 | 51 → 13 | 5,626,530,258 out, 575,585,808,052 in | none |
| 2,000,000 – 2,000,999 | 13,532 | 6,727 | 2,741 → 0 | 386 → 2 | 4,717,665,619,545 out, 4,749,470,805,370 in | none |
| 3,300,000 – 3,309,999 | 68,389 | 10,314 | 4,489 → 0 | 1,922 → 0 | 8,863,854,509,414 out, 10,038,211,623,973 in | none |
| 3,428,143 – 3,438,142 | 44,455 | 18,714 | 3,326 → 0 | 11,879 → 0 | 120,261,449,510,218 out, 0 in | 3,760,573,702,127 out, 136,498,771,401,768 in |
| 3,508,000 – 3,517,999 | 68,790 | 31,989 | 9,748 → 0 | 15,233 → 0 | 2,616,596,254,014 out, 0 in | 9,466,673,423,837 out, 14,517,897,589,055 in |

| Window | Coinjoin-flagged, before → after | Flags changed | Clustering input sets changed | Relation estimates changed | Their total change (zat) |
|---|---|---:|---:|---|---:|
| 1,687,104 – 1,697,103 | 1 → 1 | 0 | 0 with filter, 0 without | 0 of 612,594 | 0 |
| 2,000,000 – 2,000,999 | 1 → 1 | 0 | 0 with filter, 0 without | 1 of 31,154 | 423,415 |
| 3,300,000 – 3,309,999 | 2 → 2 | 0 | 0 with filter, 0 without | 11 of 129,928 | 2,395,722,144 |
| 3,428,143 – 3,438,142 | 1 → 1 | 0 | 0 with filter, 0 without | 6 of 88,293 | 9,037,410,925 |
| 3,508,000 – 3,517,999 | 4 → 4 | 0 | 0 with filter, 0 without | 6 of 117,280 | 240,893,228 |

- "Changed": transactions whose total input, total output or fee differs. Every difference is exactly the Orchard
  and Ironwood legs the old release did not read (`differences_explained` in each summary: 0 unexplained).
- "Newly accounted": the value of the legs added, out of the pool (positive balances, new shielded inputs) and into
  the pool (negative balances, new shielded outputs).
- With graphsense-lib 2.17.0, 2.18.1 and `develop` (`ea65eb6`) in the place of 2.16.4, every transaction of the five
  windows gets exactly the row 2.16.4 gives (equal `records_sha256`, A3).
- These are five windows, 41,000 blocks, chosen to cover the start of each range, heavy Orchard use (the 50,000
  blocks from 2,000,000 moved more Orchard value than any other 50,000 of the range, A4) and recent blocks. They are
  representative, not a census; the census is [section 3](#3-what-is-affected).

To reproduce a window against your own node:

```
uv run gs-zec-validate.py install --with-transformer
uv run gs-zec-validate.py compare-range --start 3428143 --end 3438142 --rpc-url http://127.0.0.1:8232 --rpc-cookie-file <your node's .cookie>
```

The last block it prints names the results checksum and, for one of the five windows, `published … — identical`.

## 8. What changes in your data

### 8.1 Transaction accounting

For a transaction with a non-zero Orchard or Ironwood balance (`ingest/rpc_utxo.py` lines 656-699):

| Field | Change |
|---|---|
| inputs | one more shielded input for each of the two pools whose balance is positive (value left the pool) |
| outputs | one more shielded output for each whose balance is negative (value entered the pool) |
| input count, output count | up by the number of legs added |
| total input | up by the positive balances |
| total output | up by the negated negative balances |
| fee (Delta lake only) | total input minus total output, now with every pool; 0 for a coinbase |

An Orchard or Ironwood unshielding gets its missing input and loses its negative fee. A fully shielded transaction
gets its fee, which the old release recorded as 0. A transaction that moved value between Sapling and Orchard gets
its Orchard leg and loses the inflated fee the Sapling leg alone gave. In the windows of section 7 no non-coinbase
transaction kept a negative fee, and zero fees remained only in the two earlier windows (13 and 2): 14 of these 15
transactions carry no Orchard or Ironwood value, and for the fifteenth, `7cf153fe…e769` (block 2,000,133, with an
Orchard leg), the node's own data gives a fee of 0 as well (A3).

A coinbase transaction that pays part of the reward into Orchard or Ironwood gets a larger total output
(177 at NU6.3 activation and 420 in the recent window; none in the three earlier windows).

### 8.2 Relation estimates

For each transaction, the full transform estimates the value from each sending address to each receiving address as
`round(inValue / totalInput' * outValue)` (graphsense-spark `Transformator.plainAddressRelations`, lines 199-254).
`totalInput'` is the sum of **all** inputs of the transaction, shielded ones included, adjusted for addresses that
appear on both sides. Hence:

- A new shielded input (a positive Orchard or Ironwood balance) enlarges the denominator and **lowers every
  estimate** of that transaction's sending and receiving pairs. This needs at least one transparent sender, one
  transparent receiver and such an input in the same transaction.
- A new shielded output changes no estimate: shielded outputs have no address and are not in the formula.
- Coinbase estimates do not change.

The relation tables hold, for each pair of addresses, the sum of these estimates over all transactions, so a pair
changes by the difference of its affected transactions. In the windows of section 7, 0, 1, 11, 6 and 6 of the
per-transaction estimates changed, every one of them to a lower estimate, 11,674,449,712 zat less in all. In A2, GraphSense's own Spark job run on 400 blocks once
from old and once from corrected rows changed exactly one relation, from 1,200,000 to 704,464 zat.

### 8.3 Coinjoin classification

graphsense-lib flags a transaction as coinjoin with `is_coinjoin` (`ingest/utxo.py` lines 742-777): at least two
inputs and three outputs; `int((outputs + 1) / 2)` participants, no more than the inputs and no more than the
distinct input addresses; the most frequent output value (the lowest of several tied ones) occurring exactly once per
participant; that value not 546 or 2730.
The corrected parser adds its legs before these counts are taken, so a leg can change the result:

- a shielded input raises the input count and adds one more distinct input "address" (shielded inputs have none,
  and together they count as one);
- a shielded output raises the output count, and so the number of participants, and adds a value to the histogram
  of output values.

Either can turn the flag on or off. In the windows of section 7 no flag changed (1, 1, 2, 1 and 4 flagged
transactions, before and after). Over the whole affected range exactly two transactions change their flag, both from
false to true and both in block 2,623,855 (`c2ca8fef…937a`, `dd0e1fb5…e3e2`): each has two transparent outputs of
20,020,000 zat and gains an Orchard output, and three outputs of which two are equal look to `is_coinjoin` like a
coinjoin of two participants (A4).

### 8.4 Clusters

The full transform clusters only inputs with exactly one address (graphsense-spark
`Transformation.computeRegularInputs`, `size(address) === 1`); graphsense-lib's newer clustering uses the addresses of
all inputs (`multi_input_address_set`, `utils/utxo.py` line 106). Shielded inputs have no address in either. A
corrected row therefore changes no transaction's set of input addresses, and **cluster membership can change only
through a coinjoin flag that changes**, and only while coinjoin filtering is on (the Zcash default): a transaction
that stops being flagged starts joining its input addresses, one that becomes flagged stops.

In every window of section 7 every transaction's clustering input set was the same before and after, with and
without coinjoin filtering. Over the whole affected range only the two transactions of section 8.3 leave clustering
(with coinjoin filtering on): their input addresses lose the link these two transactions gave them, which changes a
cluster only where no other transaction links the same addresses (A4; a full transform of the whole chain was not
run here). In A2 the full transform built 1,161 clusters from the old and from the corrected rows, with every
address in the same cluster.

One more change in the address and cluster tables: the `coinbase` pseudo-address (id 0, graphsense-spark
`Transformation.computeAddressIds`), whose spent value is the sum of the coinbase transactions' total outputs, grows
by the coinbase value paid into Orchard and Ironwood (A2: 53,551,549,812 → 55,052,129,735 zat over 400 blocks).

### 8.5 What does not change

The transparent side of every transaction (inputs, outputs, addresses, values), the number of transactions and
blocks, the `tx_id`s, the Sprout and Sapling legs, and the address ids (numbered from the transparent outputs alone,
`Transformation.computeAddressIds`).

## 9. Checks

### 9.1 The stored rows agree with the node

This repository's `check` command reads what your GraphSense stored, with your own graphsense-lib installation and
configuration, and compares it block by block with your node
([OPERATOR-TOOLS.md](OPERATOR-TOOLS.md#check)):

```
uv run gs-zec-validate.py check --gs-python <your graphsense environment>/bin/python --gs-config ~/.graphsense.yaml \
    --gs-env prod --sink cassandra --sink delta --start 1687104 --end <height> \
    --rpc-url http://127.0.0.1:8232 --rpc-cookie-file <your node's .cookie>
```

| Rule | After the repair | A failure means |
|---|---|---|
| `rows`: one stored transaction per transaction of the block | pass | rows missing or stored twice, for example a Delta partition cut short ([5.2](#52-delta-lake)) |
| `shielded`: shielded outputs minus shielded inputs equal the node's change of the Sprout, Sapling, Orchard and Ironwood pools | pass | the block still holds rows of the old release |
| `transparent`: transparent outputs minus transparent inputs equal the node's change of the transparent pool | pass | inputs unresolved or wrong |
| `fees`: no non-coinbase transaction has a negative fee | pass | old rows: an unshielding without its Orchard or Ironwood input |
| `totals`: total input and total output equal the sums of the stored inputs and outputs | pass | inconsistent rows |
| `stored-fee` (Delta lake): the stored fee equals total input minus total output | pass | for example the stale fee of [#183](https://github.com/graphsense/graphsense-lib/issues/183) |
| `dataset`: the transactions of this repository's dataset have their expected values | pass | wrong values on known transactions |

The node data used is `getblock <height> 1`: the transaction count and `valuePools[*].valueDeltaZat`. Exit status 0
means every rule held in every block; `failing-blocks.csv` lists every block that failed.

The comparison is per block, so it cannot see old rows in a block whose Orchard and Ironwood changes add up to zero.
From 1,687,104 to 3,518,201 that is the case in 15 of the 618,169 blocks with such value (1,770,640, 1,868,366,
2,178,131, 2,214,625, 2,552,518, 2,572,347, 2,702,112, 2,743,275, 2,819,495, 2,915,621, 2,934,971, 3,077,277,
3,165,170, 3,515,602 and 3,515,696: in each, transactions moved as much value into a pool as others moved out of it;
A4). A range with old rows therefore fails unless every block in it that carries Orchard or Ironwood value is one
of these; `compare-range` compares transaction by transaction.

Observed on rows of 2.16.3 (A1): the `shielded` rule failed in 17 of 500 blocks at NU5 activation, in 332 of 500 at
NU6.3 activation and in 343 of 500 recent blocks, and `fees` in 1, 127 and 268 of them. After the re-ingestion every
rule passed in both sinks.

The same comparison by hand for one block (Cassandra):

```
SELECT txs FROM <raw keyspace>.block_transactions WHERE block_id_group = <height / 100> AND block_id = <height>;
SELECT total_input, total_output, inputs, outputs FROM <raw keyspace>.transaction WHERE tx_id_group = <tx_id / 25000> AND tx_id = <tx_id>;
```

(integer divisions; 100 and 25,000 are the bucket sizes in the raw keyspace's `configuration` table). Shielded
inputs and outputs have `address_type` 12 and no address. Their difference over the block, outputs minus inputs,
must equal the sum of the four shielded pools' `valueDeltaZat` in `getblock <height> 1`.

### 9.2 What the correction changes in a range

`compare-range` (section 7) shows, for any range of your node, the transactions that change and how, and the
coinjoin, clustering and relation effects, between the old and the corrected graphsense-lib.
`changed-transactions.csv` lists every changed transaction. `--after-python` runs the graphsense-lib you
re-ingest with in the place of 2.16.4.

### 9.3 What the re-ingestion will touch

```
uv run gs-zec-validate.py scan --start 1687104 --end <height> --rpc-url http://127.0.0.1:8232 --rpc-cookie-file <your node's .cookie>
```

lists the blocks whose transactions carry an Orchard or Ironwood value (`blocks.csv`), with totals. It does not
resolve inputs and so is cheaper than `compare-range`. It also lists the transactions whose coinjoin flag can change
at all: both releases evaluate `is_coinjoin` with every transparent input given an address of its own, which can only
make the flag easier to set ([OPERATOR-TOOLS.md](OPERATOR-TOOLS.md#scan)). A transaction with an Orchard or Ironwood
value for which both are false cannot change its flag; the others are listed, for `compare-range` on their blocks.

### 9.4 Coinjoin flags and clusters before and after the rebuild

1. Before re-ingesting, record the coinjoin count of the affected range (the `flagged coinjoin` line of `check`) and
   the transformed keyspace's summary:

   ```
   SELECT * FROM <transformed keyspace>.summary_statistics;
   ```

2. After re-ingesting, run `check` again. The coinjoin count changes exactly where flags changed: over 1,687,104 –
   3,518,201 it rises by 2 (the two transactions of section 8.3), elsewhere it stays the same; `scan` and
   `compare-range` name the transactions of any range.
3. After the rebuild, compare the new keyspace's `summary_statistics` with the old one. A difference in
   `no_clusters` comes from the blocks added since the old transform and from transactions whose flag changed; where
   no flag changed, the correction changed no cluster. For a transaction whose flag changed (over 1,687,104 –
   3,518,201: `c2ca8fef…937a` and `dd0e1fb5…e3e2`), look up the clusters of its input addresses in both keyspaces:

   ```
   SELECT address_id FROM <transformed keyspace>.address_ids_by_address_prefix WHERE address_prefix = '<first 4 characters of the address>' AND address = '<address>';
   SELECT cluster_id FROM <transformed keyspace>.address WHERE address_id_group = <address_id / 5000> AND address_id = <address_id>;
   ```

   (integer division; 4 and 5,000 are the address prefix length and bucket size of section 6.2; graphsense-spark
   `Transformator.withAddressPrefix`, `TransformHelpers.withIdGroup`).

## 10. Parser outputs and stored fields

| | Corrected parser (2.16.4 and later) | Cassandra raw keyspace | Delta lake | Read by the full transform |
|---|---|---|---|---|
| shielded inputs and outputs, one per pool leg | yes | yes, in `inputs`/`outputs`, `address_type` 12, no address | yes, in `inputs`/`outputs`, `type` `shielded` | in `totalInput`; never as an address |
| `total_input`, `total_output` | yes | yes | yes | the inputs' values rather than the total; `total_output` for coinbase |
| `fee` | yes | no column | yes | no |
| `input_count`, `output_count` | yes | no column (`block_transactions` holds `no_inputs`, `no_outputs`) | yes | no |
| `coinjoin` | set by the transformer | yes | yes | yes, for clustering |
| `sapling_value_balance`, `orchard_value_balance`, `ironwood_value_balance` | yes, in zatoshi, positive when value left the pool | **no** | **no** | no |

The three per-pool fields are what a separate storage-schema change on GraphSense's side would have stored. On #126 the
maintainer asked for them and wrote that the Delta sink ignores them "until I add the fields to the parquet schema.
That's a separate change on my side" ([comment of 2026-09-04](https://github.com/graphsense/graphsense-lib/issues/126#issuecomment-5539032754)).
On #182 the maintainer then decided against it: "that's intentional for now. Nothing downstream reads them, and the
shielded inputs/outputs already carry the amounts, so we won't add them to the lake"
([comment of 2026-10-01](https://github.com/graphsense/graphsense-lib/issues/182#issuecomment-5929275081)). Neither the
parquet schema (`schema/resources/parquet/utxo.py`) nor the Cassandra schema (`schema/resources/raw_utxo_schema.sql`)
declares them, in 2.17.0, in 2.18.1 and on `develop` (`ea65eb6`).

So a repair stores every amount, but **not which pool it belongs to**: the shielded legs of Sapling, Orchard and
Ironwood are identical rows. If you need the pool of a shielded leg, read it from the node (the transaction's
`valueBalanceZat`, `orchard.valueBalanceZat`, `ironwood.valueBalanceZat`); `compare-range` and `scan` report the
per-pool values of any range.

## 11. A positive shielded balance is not necessarily an unshielding

A pool's balance is positive when value **left** the pool, and GraphSense records it as a shielded input of that
amount. That is an unshielding only if transparent outputs receive more than the transparent inputs provide; otherwise
the value paid the fee or moved into another pool. The shapes and the four transactions are those of this
repository's dataset ([DATASET.md](DATASET.md), cases 1687143/1, 2501409/1, 3479000/1 and 1687194/4):

| Transaction | Balances | Transparent side | What happened | Old release |
|---|---|---|---|---|
| `8454f559…b5ae` (block 1,687,143) | Orchard +10,001,000 | no input, outputs 10,000,000 | unshielding; fee 1,000 | input 0, fee −10,000,000 |
| `224daf9f…7c5a` (block 2,501,409) | Orchard +1,000 | none | fully shielded; the balance is the fee, 1,000 | fee 0 |
| `af9eae20…9b82` (block 3,479,000) | Ironwood +40,000 | none | fully shielded; the balance is the fee, 40,000 | fee 0 |
| `bd84ece1…6517` (block 1,687,194) | Sapling +89,000, Orchard −88,000 | none | 88,000 moved from Sapling to Orchard; fee 1,000 | fee 89,000 |

A shielded input is therefore no evidence of a transfer to a transparent address.

## 12. The documented environment, and reporting a problem

Everything in this guide was run in this environment:

| | |
|---|---|
| graphsense-lib | 2.16.3 and 2.17.0 from PyPI with the `ingest` extra (and `transformation` for A2); 2.16.3 and 2.16.4 as pinned by this repository (`install --with-transformer`) for A3 and A4; in the place of 2.16.4 in A3, 2.17.0 and 2.18.1 from PyPI (with `transformation`) and `develop` at `ea65eb6` (source tree) |
| graphsense-spark | `spark-v26.09.1` (`graphsense-spark-assembly-26.09.1.jar`), Spark local mode, pyspark 3.5.9, OpenJDK 17.0.20.1 |
| Node | Zebra 6.4.2, mainnet, synchronised to the tip |
| Machine | Ubuntu 24.04, Linux x86-64, 2 vCPUs, 7.7 GiB of memory shared with the node; Python 3.12.3 |
| Cassandra | 4.1.4 (the `cassandra:4.1.4` container image graphsense-lib's own tests use), one node, `SimpleStrategy`, replication 1 |
| Delta lake | local file system; deltalake 1.6.6, pyarrow 26.0.0 |
| Input resolution | the default, through the node's `getrawtransaction` |

**A newer node.** graphsense-lib checks the node's answers strictly and raises on any field it does not know, at
block, transaction, input, output and bundle level (`validate_rpc_fields`, used throughout `ingest/rpc_utxo.py`). A
node release for a later network upgrade may add fields; re-ingestion would then stop with `Unknown fields in …
response`, as 2.15.0 did at NU6.3. Zebra 6.4.2 refuses to run from height 3,540,768 (`ESTIMATED_RELEASE_HEIGHT`
3,444,000 plus 84 days of 1,152 blocks, `zebrad/src/components/sync/end_of_support.rs`; "early November 2026" in its
release notes), so a node upgrade is due in any case. Before re-ingesting with a newer node, run `scan` or
`compare-range` on recent blocks: they run graphsense-lib's own parser and show such an error at once.

**Reporting a suspected regression.** Open an issue in this repository with the command you ran and its output,
your graphsense-lib version (`graphsense-cli version`), your node's version (`getinfo`) and the block or transaction.
`compare-range --keep-capture <dir>` keeps the node data of a range, so the problem can be reproduced without your
node; attach it if it is small.

## 13. What this guide does not cover

- GraphSense's own production re-ingestion, cluster rebuild and release timing.
- Clustering algorithms, identity enrichment, viewing keys, wallet fingerprinting.
- Other currencies.

## Appendix: how the statements were tested

All tests ran in the environment of section 12, on 2026-10-15.

### A1. Re-ingestion into Cassandra and a Delta lake

Samples were first ingested with graphsense-lib 2.16.3, then re-ingested with 2.17.0; every stored row was dumped
before and after each step with graphsense-lib's own database access and the `deltalake` package, and compared. All
ingests ran with `--no-lock --no-exchange-rates --no-staleness-check` (no lock files, no exchange-rate provider).
2.18.1, released while this guide was written, has the same code for everything these steps exercise
(`ingest/dump.py`, `ingest/delta/sink.py`, `ingest/transform.py`, `db/utxo.py`, `utils/locking.py`,
`config/config.py`); its parser gives the same rows (A3).

| Step | Command (2.17.0 unless noted) | Observed |
|---|---|---|
| 1 | 2.16.3: `--sinks cassandra --sinks delta --write-mode overwrite --ignore-overwrite-safechecks --start-block 1686904 --end-block 1687403 --create-schema` | 500 blocks, 2,939 transactions |
| 2 | `check` on both sinks | `shielded` failed in 17 blocks, `fees` in 1, `dataset` in 2 of 3 blocks |
| 3 | `--sinks cassandra --start-block 1687104 --end-block 1687403` | the same 2,939 rows and tx_ids; 18 transactions corrected; summary statistics unchanged; then `check`: Cassandra passes every rule |
| 5 | `--sinks delta --write-mode append --start-block 1687104 …` | refused: `must be higher than the highest block already written (1,687,403)` |
| 6 | `--sinks delta --write-mode overwrite --start-block 1687104 …` | refused: `must be a multiple of partition_batch_size (100,000)` |
| 7 | the same with `--ignore-overwrite-safechecks` | the partition then held only 1,687,104 – 1,687,403: 2,252 transactions of the 200 earlier blocks deleted |
| 8 | `--sinks delta --write-mode overwrite --ignore-overwrite-safechecks --start-block 1686904 --end-block 1687403` | 2,939 transactions back, 18 corrected, none before 1,687,104 changed; then `check`: both sinks pass |
| 10 | `--sinks cassandra --start-block 1687104 --end-block 1687203` | `summary_statistics`: `no_blocks` 1,687,404 → 1,687,204, `no_txs` 2,939 → 2,483 |
| 11 | `--sinks cassandra --start-block 1687204 --end-block 1687403` | `summary_statistics` back to 1,687,404 and 2,939; no value or tx_id changed |
| 12 | Delta only: 2.16.3 overwrite, then 2.17.0 `--write-mode merge` on 1,687,104 – 1,687,403 | no row changed |
| 13 | 2.16.3 on 3,500,000 – 3,500,499; then 2.17.0 both sinks overwrite from 3,500,000 | refused by Cassandra: `block_transactions[3499999] is missing … Refusing to start tx_id allocation at 0`; nothing written |
| 14 | 2.16.3 on 3,499,900 – 3,500,499; 2.17.0 both sinks overwrite 3,500,000 – 3,500,299; `check`; then 3,500,000 – 3,500,499; `check` | after the short run: Cassandra 1,063 corrected, `summary_statistics` at 3,500,300; Delta partition 35 holds only 3,500,000 – 3,500,299 (1,564 transactions gone), partition 34 untouched; `check` fails `rows` exactly in 3,500,300 – 3,500,499. After the full run: 3,500,000 – 3,500,499 pass in both sinks; the 100 lead-in blocks, not re-ingested, fail `shielded` in the 70 of them that carry Orchard or Ironwood value |
| 15 | 2.16.3 on 3,427,943 – 3,428,442; 2.17.0 Cassandra from 3,428,143, Delta overwrite from 3,427,943; `check` | Delta passes; Cassandra fails only in blocks before 3,428,143: their Orchard values are not repaired by a run from 3,428,143 |
| 16 | Cassandra 3,428,143 – 3,428,442 reset to 2.16.3, then two 2.17.0 runs at the same time (`--no-lock`) on 3,428,143 – 3,428,292 and 3,428,293 – 3,428,442 | exactly the rows of one sequential run |

The queries of sections 9.1 and 9.4 and `graphsense-cli monitoring get-summary` were run as written against these
keyspaces and the two of A2: for block 1,687,194 the stored shielded outputs minus inputs are 88,000 − 89,000 =
−1,000, and the node's Sprout, Sapling, Orchard and Ironwood changes add up to −1,000. With this version of the tool,
from a clean copy of the repository in a clean container and with graphsense-lib 2.17.0 installed with pip as section
4 says (2.18.1 reads the stored rows with the same code: `db/analytics.py` and `db/cassandra.py` differ only in how
statements are written), `check` gave the same results again: every rule passes on the rows of step 8; on the keyspace
of step 14, `shielded` fails in 70 blocks and `fees` in 54; on the old rows of step 13, `shielded` fails in 343 of 500
blocks and `fees` in 268.

### A2. Full transform of the old and the corrected rows

Blocks 3,509,200 – 3,509,599 (400 blocks, 2,276 transactions) were ingested into one raw keyspace by 2.16.3 and into
another by 2.17.0, and each was transformed with `graphsense-cli transformation raw-to-transformed --local
--target-keyspace <name>` (graphsense-spark `spark-v26.09.1`, Spark local mode, one core, 1 GiB driver memory,
`spark.sql.maxPlanStringLength=1m` as in graphsense-lib's own Spark tests). The job needs an exchange rate for every
block date; one row with EUR and USD at 1.0 was inserted for 2026-10-07 (fiat values enter no zatoshi figure).

| | Old rows | Corrected rows |
|---|---:|---:|
| address relations | 1,394 | 1,394 |
| addresses | 1,484 | 1,484 |
| clusters | 1,161 | 1,161 |
| relations whose estimate differs | 1 (1,200,000 → 704,464 zat) | |
| addresses whose cluster differs | 0 | |
| addresses whose totals differ | `coinbase` only (spent 53,551,549,812 → 55,052,129,735) | |

`compare-range`'s relation estimates for the same blocks equal Spark's on all 1,394 pairs of both keyspaces, once
Spark's join with `address_ids` is taken into account: in a keyspace that begins at block 3,509,200, senders funded
earlier have no address id and drop out of Spark's sums. In a keyspace ingested from block 0 every input address has
an id, and `compare-range` computes exactly what the full transform does.

### A3. Representative windows

`compare-range` on the five windows of section 7, from the node (Zebra 6.4.2), with graphsense-lib 2.16.3 and 2.16.4
as pinned by this repository, then again from the kept node data with 2.17.0, with 2.18.1 and with `develop`
(`ea65eb6`) in the place of 2.16.4. The node's answers were captured on 2026-10-15 between 02:05 and 02:40 UTC
(`--keep-capture`); the published summaries were made from those captures with this version of the tool (1.1.0). With
2.17.0, 2.18.1 and `develop` every transaction of every window gets the same row as with 2.16.4 (equal
`records_sha256` of the after side, 0 unexplained differences); with 2.18.1 the validation of this repository also
reports the expected values on all 47 transactions.

Every changed transaction (67,857, of them 597 coinbase) was also recomputed from the captured node answers with this
repository's reference calculation (`gs_zec_validate/reference.py`, which shares no code with graphsense-lib):
2.16.4's total input, total output, fee and input and output counts equal the calculation with all four pools for
every one of them, and 2.16.3's equal the calculation with Sprout and Sapling only.

### A4. The whole affected range

`scan` from 1,687,104 to 3,518,201 (150 blocks below the node's tip when it started), in segments of 50,000 blocks.
The segments ran one after the other on 2026-10-15 between 06:25 and 16:20 UTC, 35,203 seconds in all; the 412,896
blocks before 2,100,000 (2022 and early 2023) took 80% of it. Their summaries were added up into
[`results/scan-1687104-3518201.json`](../results/scan-1687104-3518201.json): every member of `results` is the same in
every segment, a sum, or the list of candidates in block order, because `scan` handles every block on its own. One
`scan` of 1,687,104 – 1,699,999 and two scans of its halves added up give the same results checksum as that segment.
The table of section 3 splits the transaction count at 3,428,143 with the node's per-block counts, which add up to
the scan's counts of the segments around that block. Of the segments, 2,000,000 – 2,049,999 moved the most Orchard
value (494,171,194,499,682 zat out of and into the pool); 1,700,000 – 1,749,999 had the most Orchard transactions
(215,762, moving 268,836,046,262 zat).

Block by block, the Orchard and Ironwood values `scan` reports (graphsense-lib 2.16.4's parser) equal the node's
change of the two pools (`getblock <height> 1`, `valuePools`) in all 1,831,098 blocks. 15 blocks carry such value with
a net change of zero (section 9.1). Of the 100 lead-in blocks of A1 step 14 (3,499,900 – 3,499,999), 70 carry such
value, all with a non-zero change.

The 1,253 transactions of the coinjoin screen (in 1,240 blocks, all before 3,428,143) were decided with GraphSense's
own exporter, input resolution and transformer, run through `compare-range`'s worker on the node's answers for those
blocks: two flags change, both in block 2,623,855 (section 8.3). `compare-range` on that block and on three other
candidate blocks gives the same flags; on block 2,623,855 it reports `coinjoin 0 -> 2 flagged`, two clustering input
sets changed with the coinjoin filter and none without, and no relation estimate changed.

### A5. Relation estimates against graphsense-lib's delta updater

For every transaction of the five windows, the estimates of `compare-range` were compared with those of
graphsense-lib 2.17.0's own delta updater function (`dbdelta_from_utxo_transaction`) on the corrected rows: 979,249
pairs, the same pairs on both sides, equal values except 148. Each of those is an exact half that Spark rounds up and
the delta updater rounds to even, for example 2,362,694.5 in transaction `8f8a58f5…7d92` (block 1,695,490, no shielded
value): 2,362,695 against 2,362,694. The full transform is Spark's, so `compare-range` follows Spark.

### A6. Throughput

Re-ingestion with 2.17.0 into both sinks of a fresh keyspace and lake, timed with `/usr/bin/time`: the table of
section 5.6. The node, Cassandra and the ingest shared the machine.
