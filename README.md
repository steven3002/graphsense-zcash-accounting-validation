# GraphSense Zcash accounting validation

This repository lets you check, on your own machine, that graphsense-lib 2.16.4 accounts Zcash transactions
correctly where graphsense-lib 2.16.3 did not.
It runs both releases, unmodified, on 47 real mainnet transactions that are stored here, and compares what each
release reports with values derived from the node's own data.
The outcome is a pass or a fail, a machine-readable report, and one checksum to compare with the published one.

This is an independent project. It is not part of the GraphSense project and is not endorsed by it.

## Background in four lines

graphsense-lib up to 2.16.3 read the value that enters or leaves the Sprout and Sapling pools of a Zcash
transaction, but not the value of the Orchard and Ironwood pools. Transaction totals and fees were wrong whenever
a transaction had a non-zero Orchard or Ironwood balance. [graphsense-lib #126](https://github.com/graphsense/graphsense-lib/issues/126)
describes the problem; [pull request #163](https://github.com/graphsense/graphsense-lib/pull/163) corrected it, and
2.16.4 is the first release with the correction.

## Run it

You need a copy of this repository, network access for the installation step, and one of the three set-ups below.
The validation itself runs offline.

```
git clone https://github.com/steven3002/graphsense-zcash-accounting-validation.git
cd graphsense-zcash-accounting-validation
```

### A. With uv (recommended)

[uv](https://docs.astral.sh/uv/) provides a suitable Python by itself, so this works whatever Python your system has.
If you do not have uv: `pip install uv`, or see
[its installation page](https://docs.astral.sh/uv/getting-started/installation/) for other ways.

```
uv run gs-zec-validate.py install
uv run gs-zec-validate.py run
```

### B. With Python and pip

For Python 3.10, 3.11, 3.12 or 3.13 with the `venv` module and pip. (On Debian and Ubuntu the package
`python3-venv` provides them.)

```
python3 gs-zec-validate.py install --installer pip
python3 gs-zec-validate.py run
```

### C. With Docker

```
docker build -t gs-zec-validate .
docker run --rm --network none gs-zec-validate
```

### What the two steps do

`install` creates two Python virtual environments in `.envs/`: one with graphsense-lib 2.16.3 ("before"), one with
graphsense-lib 2.16.4 ("after"). Both are built from the hash-locked files in `locks/`; only ready-made wheels are
accepted, nothing is compiled. It downloads about 14 MB and uses about 45 MB of disk with uv and 105 to 150 MB
with pip. If uv finds no Python it can use, it first downloads one (about 35 MB). In measurements on Linux x86-64
it usually took a few seconds with uv and 13 to 25 seconds with pip. It can take longer: a few installs with pip
took more than a minute. This is the only step that uses the network.

`install` prints a few lines that say what it installed and verified. On a terminal, one further line changes in
place while it works and shows what is being done at the moment. `install --verbose` also prints the interpreter
and every command it runs, and shows no changing line.

`run` checks the evidence against its checksums, lets both graphsense-lib releases process it, compares the
results, writes `report/report.json` and `report/report.md`, and prints a summary. It takes a few seconds.

## What a pass looks like

The last lines of a passing run:

```
----- gs-zec-validate confirmation -----
tool        gs-zec-validate 1.0.0
dataset     1.0.0 (manifest sha256 9b945a5e1a4468502704c9ba39b95f646959aaf4028553fc5c5c58b66443fdac)
before      graphsense-lib 2.16.3 (v2.16.3, commit c02cbdb544fe), code checksum 4bb3029346d707d1… — pinned code
after       graphsense-lib 2.16.4 (v2.16.4, commit 411b987a0f24), code checksum 675521888b904da1… — pinned code
cases       47; after matches the expected values on 47; before differs on 13, 13 of them explained
results     sha256 2aa7d4024145c55e9a2829ffb393d41e250c0b07fee76ed68904cb5f93670805
published   sha256 2aa7d4024145c55e9a2829ffb393d41e250c0b07fee76ed68904cb5f93670805 — identical
verdict     PASS (exit status 0)
python      3.12.3 (cpython) on Linux x86_64; environments: before 3.12.3, after 3.12.3
----------------------------------------
```

The exit status is 0. The "results" checksum does not depend on the machine or on the Python version (it was the
same in every run on Linux x86-64 with Python 3.10, 3.11, 3.12 and 3.13); it is also stored in `RESULTS.sha256`.
Only the last line of the block differs from machine to machine.

Above that block the run prints one line per transaction and one line per check. For the transaction used as the
worked example in pull request #163:

```
case        transaction    shape                               expected fee       old fee       new fee  old                new
1687194/4   bd84ece1…6517  cross-pool,fully-shielded-fee,sap…         1,000        89,000         1,000  wrong (explained)  ok
```

## What a failure looks like

Any failed check gives a non-zero exit status and a line that names the case and the field. For example, if the
old release is put where the corrected one is expected:

```
  [FAIL] Totals: the after revision reports the expected total input, total output and fee (141 checked, 27 failed)
           - case 1687143/1 (8454f559…b5ae) total_input: expected 10001000, after reports 0
           - case 1687143/1 (8454f559…b5ae) fee: expected 1000, after reports -10000000
           - case 1687194/4 (bd84ece1…6517) total_output: expected 88000, after reports 0
           - case 1687194/4 (bd84ece1…6517) fee: expected 1000, after reports 89000
```

and the run ends with `verdict     FAIL for a NON-CANONICAL pair of revisions (exit status 1)`: a run that does not
use the two pinned releases says so.

| Exit status | Meaning |
|---:|---|
| 0 | Every required check passed. |
| 1 | The validation ran and at least one required check failed. |
| 2 | The command line was wrong. |
| 3 | The validation could not run: the manifest, the evidence, the environments or the revisions are not as required. |
| 4 | The report could not be written. |

There is no "warning but success". [docs/REVIEWING.md](docs/REVIEWING.md) shows how to make it fail on purpose.

## What is validated

For each of the 47 transactions, the tool compares what graphsense-lib 2.16.4 reports with the expected value of:

- the signed balance of each shielded pool (Sprout, Sapling, Orchard, Ironwood);
- the shielded inputs and outputs GraphSense derives from those balances, and the input and output counts;
- the transparent input total (after GraphSense has resolved the previous outputs) and the transparent output total;
- total input, total output and fee.

It also runs graphsense-lib 2.16.3 on the same evidence and checks that every value it gets wrong is wrong by
exactly the Orchard and Ironwood value it never read, and that it is right everywhere else.

The dataset covers shielding into and unshielding from Sapling, Orchard and Ironwood, fully shielded transactions
that pay their fee from a pool, transactions that move value between two and three pools, Sprout joinsplits,
transparent-only transactions and coinbase transactions. [docs/DATASET.md](docs/DATASET.md) lists every case.

The expected values do not come from GraphSense. They are taken from the node's integer fields, recomputed on every
run by a short reference calculation that follows the Zcash protocol specification
(`gs_zec_validate/reference.py`), and checked against the node's own per-block totals.
[docs/METHOD.md](docs/METHOD.md) explains each step. To see the derivation for one transaction:

```
uv run gs-zec-validate.py explain 1687194/4
```

## What is not validated

A pass means: these 47 transactions, these two graphsense-lib releases, this evidence. It does not mean that all
of GraphSense's Zcash accounting is correct. In particular this repository does not check:

- transactions outside the dataset, or how many transactions on the chain were affected;
- anything after the ingest step: GraphSense's Spark transformation, its databases, clustering;
- the effect of the correction on coinjoin detection (the optional layer below only reports the flag);
- value entering the Sprout pool, which has not been possible on mainnet since 2020 and does not occur in the dataset;
- other currencies, and graphsense-lib releases other than the two pinned ones (but see "Other revisions").

It has been run on Linux x86-64 only. macOS, Windows and ARM are expected to work but are not proven.

## Where the evidence comes from

`evidence/` holds ten Zcash mainnet blocks as a node returns them (`getblock <height> 2`) and the 43 earlier
transactions whose transparent outputs they spend (`getrawtransaction <txid> 1`).

- Seven blocks are the mainnet test fixtures that pull request #163 added to graphsense-lib (captured from
  Zebra 6.3.0). They are reused here byte for byte.
- All ten blocks and the 43 transactions were captured from Zebra 6.4.2 on 2026-10-06. For the seven reused blocks
  the validation runs on both captures and requires the same result.

`manifest/manifest.json` pins every file by checksum, together with the two code revisions and every expected
value. [docs/EVIDENCE.md](docs/EVIDENCE.md) describes the captures and how to check them against your own node or
a public block explorer.

## Options

**The transformer layer.** GraphSense's own transformer turns the exporter's output into the row it stores
(`total_input`, `total_output`, `fee`, `coinjoin`). Including it needs a larger dependency set (35 to 41 MB more
to download; `.envs/` grows to about 200 MB with uv and to 450 to 530 MB with pip), so it is a separate, optional
layer:

```
uv run gs-zec-validate.py install --with-transformer
uv run gs-zec-validate.py run --with-transformer
```

**Your own node.** To compare the packaged evidence with a node you run (it needs a transaction index):

```
uv run gs-zec-validate.py verify-node --rpc-url http://127.0.0.1:8232 --rpc-cookie-file /path/to/.cookie
```

**Other revisions.** `--after-python` and `--before-python` take the interpreter of another environment in which
some graphsense-lib is installed; `--after-src` and `--before-src` take a directory that contains the
`graphsenselib` package of a source checkout. A run that does not use the two pinned revisions is marked
NON-CANONICAL in the report and is not compared with the published checksum.

## More documentation

| Page | Content |
|---|---|
| [docs/REVIEWING.md](docs/REVIEWING.md) | Checklist for confirming reproducibility, and how to make the validation fail on purpose |
| [docs/METHOD.md](docs/METHOD.md) | Formulas, sign conventions, where expected values come from, limits |
| [docs/DATASET.md](docs/DATASET.md) | Every case with its shapes, expected values and the old release's values |
| [docs/EVIDENCE.md](docs/EVIDENCE.md) | How the evidence was captured and how to verify it yourself |
| [docs/MANIFEST.md](docs/MANIFEST.md) | Every field of the manifest, and its versioning rule |
| [docs/REPORT.md](docs/REPORT.md) | The report format, the checks, the checksum |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to work on this repository |

## Licence

MIT, see [LICENSE](LICENSE). The reused graphsense-lib test fixtures and the two graphsense-lib release files in
`vendor/` are MIT-licensed by their authors, see [NOTICE.md](NOTICE.md).
