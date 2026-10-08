# Method

How the validation works, which rules it applies, and where its limits are.

## 1. What is compared with what

```
evidence files ──► reference calculation (reference.py, no GraphSense) ──► expected values ◄── frozen in the manifest
      │                                                                          ▲
      │                                                                          │ compared, field by field
      ├──► graphsense-lib 2.16.3, unmodified ("before") ──► what the old release reports
      └──► graphsense-lib 2.16.4, unmodified ("after")  ──► what the corrected release reports
```

- The **expected values** are derived from the node's data alone. No GraphSense code takes part.
- Each **GraphSense release** runs in an environment of its own and is given the same evidence through its own
  interfaces. The tool contains no copy of GraphSense's accounting; it feeds evidence in and reads results out.

## 2. The accounting rules

All amounts are integers in zatoshi (1 ZEC = 100,000,000 zatoshi).

The rules are those of the [Zcash Protocol Specification](https://zips.z.cash/protocol/protocol.pdf). Section
titles are given so that they can be found in any version of the document; the word after `#` is the section's
anchor in the PDF.

| Rule | Specification section |
|---|---|
| Transparent inputs add value to a transaction's transparent value pool, transparent outputs remove it. What remains in a non-coinbase transaction is the fee. | Transactions and Treestates (`#transactions`) |
| A JoinSplit's `vpub_old` is treated like an output, its `vpub_new` like an input. | Balance (Sprout) (`#joinsplitbalance`) |
| A positive Sapling balance takes value from the Sapling pool and is treated like an input; a negative one like an output. | Balance and Binding Signature (Sapling) (`#saplingbalance`) |
| The same for the Orchard balance and, from NU6.3, the Ironwood balance. | Balance and Binding Signature (Orchard) (`#orchardbalance`) |
| A pool's chain balance is minus the sum of the transactions' balances for that pool (Sprout: sum of `vpub_old` minus sum of `vpub_new`); the transparent pool is the sum of unspent outputs. | Chain Value Pool Balances (`#chainvaluepoolbalances`) |
| The block subsidy as a function of the height. | Calculating Block Subsidy, Funding Streams, Lockbox Disbursement, and Founders' Reward (`#subsidies`) |
| From NU6 the coinbase transaction must claim exactly subsidy plus fees; before NU6 at most that. | Transaction Consensus Rules (`#txnconsensus`) |

For one transaction, with the node's fields:

```
transparent input   = sum of valueZat of the outputs it spends      (from the previous transactions)
transparent output  = sum of vout[*].valueZat
Sprout balance      = sum of vjoinsplit[*].vpub_newZat - sum of vjoinsplit[*].vpub_oldZat
Sapling balance     = valueBalanceZat
Orchard balance     = orchard.valueBalanceZat
Ironwood balance    = ironwood.valueBalanceZat                       (0 if the transaction has no such member)

fee = transparent input - transparent output + Sprout + Sapling + Orchard + Ironwood balance
      (0 for a coinbase transaction)
```

`gs_zec_validate/reference.py` implements exactly this, in about 150 lines of code plus comments. It imports
nothing but `decimal` from the standard library. The command `explain <case>` prints the calculation for one transaction.

The same arithmetic is what the Zebra node itself uses for consensus: in Zebra 6.4.2,
`Transaction::value_balance` (`zebra-chain/src/transaction.rs`) adds the transparent, Sprout, Sapling, Orchard and
Ironwood balances, and `ValueBalance::remaining_transaction_value` (`zebra-chain/src/value_balance.rs`) is their sum.

## 3. Conventions

- **Sign of a pool balance.** As the node reports it: positive means value left the pool, negative means value
  entered it.
- **How GraphSense represents shielded value.** A positive balance becomes one *shielded input* of that amount, a
  negative balance one *shielded output* of the negated amount. For Sprout, each JoinSplit's `vpub_new` becomes a
  shielded input and each `vpub_old` a shielded output, unless it is zero. These entries have no address.
- **Total input and total output** are GraphSense's `input_value` and `output_value` (stored as `total_input` and
  `total_output`): the transparent total plus the shielded inputs, respectively outputs. Their difference is the fee.
- **Coinbase.** The fee of a coinbase transaction is 0.
- **Sprout.** Sprout has no single balance field. Its expected balance is the sum of `vpub_new` minus the sum of
  `vpub_old`, with the same sign rule as the other pools. GraphSense has no Sprout field either. For the
  corrected release the tool derives what GraphSense attributes to Sprout as *net shielded value minus the three
  balances it exposes*, and it also compares every shielded input and output individually.
- **The old release exposes no per-pool values.** For it the report shows `null` in the pool-balance fields, and
  compares the shielded inputs and outputs, the counts, the totals and the fee.
- **A positive balance is not an unshielding.** It is one only if transparent outputs receive more than the
  transparent inputs provide. Otherwise the value pays the fee or moves to another pool. The shape rules in
  [DATASET.md](DATASET.md) encode this.

## 4. Why the expected values can be trusted

An expected value is never "whatever the corrected code printed". Each one has these sources:

1. **The node's integer fields**, stored in the evidence files and frozen in the manifest.
2. **The reference calculation**, which recomputes every expected value from the evidence on every run. If the
   manifest and the calculation disagree anywhere, the run fails (check *Expected values follow from the evidence*).
3. **The node's own block totals.** Every evidence block carries `valuePools`: the change that block made to each
   value pool, as the node's consensus state recorded it. In Zebra 6.4.2 this change is the difference of two
   stored pool states (`zebra-rpc/src/methods.rs`), each of which was produced by
   `Block::chain_value_pool_change` (`zebra-chain/src/block.rs`): minus the sum of the transactions' value
   balances, computed from the real spent outputs. So for every block

   ```
   change of the Sprout / Sapling / Orchard / Ironwood pool = - (sum of that pool's balances over the block)
   change of the transparent pool                          = outputs created - previous outputs spent
   ```

   and both sides can be compared (check *the transactions' balances add up to the node's value-pool changes*).
   This confirms the sign of every balance and the sum of all previous-output values in a block, by a route that
   does not pass through the fields the balances were read from.

   The `lockbox` entry is set by consensus parameters, not by transactions; it is read, not derived.
   The sum of all six changes is the value the block created. From NU6 (height 2,726,400) consensus requires the
   coinbase to claim exactly subsidy plus fees, so the sum must equal the block subsidy; before NU6 it may be
   smaller. The tool checks the equality from NU6 and "not greater" before (check *the value created equals the
   block subsidy*). On all ten blocks of the dataset the sum equals the subsidy.
4. **Public block explorers.** [EVIDENCE.md](EVIDENCE.md) says which values were compared with which explorer and
   how to repeat it.
5. **Public statements made earlier.** The description of pull request #163 gives the worked example
   `bd84ece1…6517`: 89,000 zatoshi leave Sapling, 88,000 enter Orchard, the fee is 1,000, and the old parser
   reported 89,000. Case `1687194/4` is that transaction.

## 5. The checks on GraphSense's output

Fourteen fields are compared for each transaction. They are grouped into the four checks the comparison is about,
plus one for the transparent side:

| Check | Fields | What a mistake would look like |
|---|---|---|
| **Signs** | Sprout, Sapling, Orchard, Ironwood balance | a balance with the wrong sign, or on the wrong side |
| **Units** | every amount returned | an amount that is not an integer number of zatoshi, or off by a factor of 10^8 |
| **Accumulation** | shielded inputs, shielded outputs, input count, output count | a pool's leg missing, or counted twice |
| **Totals** | total input, total output, fee | any wrong total |
| Resolution | coinbase flag, transparent input total, transparent output total | previous outputs not resolved, or resolved to wrong values |

Units has two parts. In the evidence, every amount that the node prints both as decimal ZEC and as integer zatoshi
must denote the same value (2,691 such pairs). That matters because GraphSense reads either form: both releases
read transparent output values and Sprout values in the decimal form; the old release reads the Sapling balance in
the decimal form too, the corrected one reads the Sapling, Orchard and Ironwood balances in the integer form.
And every amount GraphSense returns must be a plain integer; anything else is reported as text and can never count
as a match.

Which cases exercise which check:

- Signs: each of Sapling, Orchard and Ironwood occurs with a positive and with a negative balance
  (for example `3438010/1` and `3442130/1`; `1687143/1` and `2501409/3`; `3479000/2` and `3463373/6`). Sprout
  occurs with a positive balance only.
- Accumulation: `1687194/4` (Sapling and Orchard), `3479000/3` (Orchard and Ironwood), `3463373/2` (three pools),
  `3442400/1` (two JoinSplits and Sapling).
- Totals and Resolution: every case; `3463373/3` has 15 transparent inputs, `2501409/2` has 45.

The tool's test suite changes one returned value at a time and requires the matching check to fail
(`tests/test_validation.py`).

## 6. The old release: no unexplained difference

For every transaction the reference calculation is run a second time with the Orchard and Ironwood balances left
out. That is what a parser reports which never reads those two balances. The check *Every difference between the
before revision and the expected values is exactly the unread Orchard and Ironwood value* requires that

- the old release's record equals that second calculation in every field, and
- wherever the manifest says the old release differs, its totals or fee really do differ from the expected ones.

So a difference of any other kind fails the run, and so does an old release that is unexpectedly right.
The values the old release reported when the dataset was made are also frozen in the manifest and compared
(check *The before revision reports the values frozen in the manifest*). Those are observations, not truths.

## 7. How GraphSense is run

A small script, `gs_zec_validate/worker.py`, runs inside each of the two environments. It is one self-contained
file; it is everything that executes next to GraphSense.

- It starts a JSON-RPC server on 127.0.0.1 that answers `getblockhash`, `getblock` and `getrawtransaction` from
  the evidence files. The stored bytes are put into the response unchanged; they are not parsed and written again.
- For each evidence block it calls GraphSense's own `BtcBlockExporter(uri, network="zec")
  .export_blocks_and_transactions(height, height)`. So GraphSense's own HTTP client, its parser and its own input
  resolver run, exactly as in an ingest. A new exporter is used for every block, so nothing is carried from one
  block to the next.
- GraphSense resolves transparent inputs by asking for the previous transactions. The report checks that it asked
  for exactly the packaged ones and that no input stayed unresolved, before any fee is compared.
- With `--with-transformer`, the exporter's output is also passed through GraphSense's own
  `TransformerUTXO(...).transform(...)` on the path GraphSense uses without a database. That yields the stored row
  with `total_input`, `total_output`, `fee` and `coinjoin`. The flag is reported and compared with the value frozen
  in the manifest; it has no independent expected value.
- If a release raises an exception on a block, the exception is recorded as that release's result.

**No network.** `run` opens no connection except the one between GraphSense and the server on 127.0.0.1. The
worker installs a Python audit hook before it imports graphsenselib; the hook refuses every connection and every
name lookup through Python's socket module that is not for the loopback address. The report says how many loopback
connections were made and fails if anything else was attempted. Proxy settings are disabled for that process.

## 8. Which code is under test

| Side | Release | Git tag | Commit |
|---|---|---|---|
| before | graphsense-lib 2.16.3 | `v2.16.3` | `c02cbdb544fe25daf4bef5d4a14cb455608253f3` |
| after | graphsense-lib 2.16.4 | `v2.16.4` | `411b987a0f246943d4ee70421f43b17a3cba8f8c` |

2.16.4 is the first release that contains the correction (pull request #163); 2.16.3 is the release before it.
The file that does the parsing, `graphsenselib/ingest/rpc_utxo.py`, has git blob id `1ad7ca79…` in 2.16.3 and
`63f54539…` in 2.16.4. graphsense-lib 2.17.0 ships the same file as 2.16.4.

On every run the tool computes a checksum over all files of the `graphsenselib` package it actually imported and
compares it with the manifest. If it differs, the run stops (exit status 3) unless you asked for another revision.
You can recompute that checksum yourself:

```
uv run gs-zec-validate.py code-checksum vendor/graphsense_lib-2.16.4-py3-none-any.whl
```

prints `675521888b904da1b953afe786334b762098d96df67c8c2392d1467d0d2f4b6f  graphsenselib (353 files)`, and
`4bb3029346d707d19d9c63a53a60ec1c27a11066d0b782d5451fe751e09132c4  graphsenselib (354 files)` for the 2.16.3 file.
The command accepts a release file downloaded from PyPI, the `src` directory of a git checkout of the tag, or the
`site-packages` directory of an environment; all three give the same checksum, because the release files contain
exactly the tagged source. The definition: one line per file, `<sha256 of the file>  <path>`, sorted by path, and
the sha256 of those lines; compiled files are left out.

The sha256 of the two release files themselves is in the manifest and in the lock files, and can be compared with
<https://pypi.org/project/graphsense-lib/2.16.3/#files> and <https://pypi.org/project/graphsense-lib/2.16.4/#files>.

## 9. The two environments

Each environment is built from a hash-locked requirements file in `locks/`. Every package is pinned to one
version, with the checksums of its published files, for all supported platforms and Python versions at once.

The lock files are deliberately **not** graphsense-lib's complete dependency set. graphsense-lib declares 25 direct
dependencies (about 70 packages in all). Several of them have no ready-made wheel outside Linux x86-64 and would need a
C compiler, and none of those is used when Zcash blocks are parsed. The lock files contain the 20 packages that
`graphsenselib.ingest.rpc_utxo` needs to be imported, in exactly the versions a complete installation got on 2026-10-06.
graphsense-lib itself is the unmodified release file. If the code under test needed anything else, the import
would fail; it could not change a number.

You can check that this choice does not matter: install graphsense-lib with all its dependencies in an
environment of your own (`pip install graphsense-lib==2.16.4`) and pass its interpreter with `--after-python`.
The tool recognises the pinned code by its checksum and the results checksum is the same.

`locks/regenerate.sh` shows how the lock files were made. The transformer layer needs about 20 more packages
(pandas, numpy and the Cassandra driver among them); they are in `locks/*-transformer.txt`.

## 10. Limits

- The result holds for the 47 transactions of the dataset and the two pinned releases. It is not a statement
  about every transaction on the chain.
- The expected values rely on the node's data being right. That is cross-checked with the node's block totals,
  with a second Zebra version for seven blocks, and with public explorers, but the tool does not verify proofs or
  signatures and does not recompute transaction ids or block hashes.
- Value entering the Sprout pool (`vpub_old` greater than zero) does not occur in the dataset. It has not been
  possible on mainnet since the Canopy upgrade in 2020.
- The coinjoin flag is `false` for all 47 transactions in both releases. The dataset therefore shows nothing about
  how the correction changes that flag.
- Only GraphSense's Python ingest path is exercised: the exporter, its input resolver and, optionally, the
  transformer. Its Spark transformation and its databases are not.
- Before the first release everything was run on Linux x86-64. The default lock files resolve to ready-made wheels
  for Python 3.10 to 3.13 on Linux (x86-64 and ARM64, glibc and musl), macOS (Intel and Apple silicon) and Windows
  x64, and on Windows ARM64 from Python 3.11. Of those other platforms, the workflow of this repository runs the
  default set-up on macOS (Apple silicon), Windows x64 and Linux ARM64 (glibc) with Python 3.12; the others were
  not run. The transformer layer's lock files do the same, except on Alpine Linux (musl) and Windows ARM64, where
  the Cassandra driver has no ready-made wheel; that layer was run on Linux x86-64 only.
