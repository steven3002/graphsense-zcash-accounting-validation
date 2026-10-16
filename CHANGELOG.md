# Changelog

Changes that matter to someone who uses this repository: the tool's behaviour, the dataset, the published
checksum. The tool and the dataset have separate version numbers; [docs/MANIFEST.md](docs/MANIFEST.md) gives the
rule for the dataset's.

## [Unreleased]

## [1.1.0] - 2026-10-16

For operators of GraphSense: a guide to repairing the Zcash history, three commands that run the comparisons and
checks it describes against a node and against GraphSense's stored data, and representative results from mainnet.

- New: [docs/OPERATOR-GUIDE.md](docs/OPERATOR-GUIDE.md), how to re-ingest the affected history into the Cassandra
  raw keyspace and the Delta lake, why the derived data needs a full rebuild, what changes in transaction
  accounting, relation estimates, coinjoin flags and clusters, how to check the result, which stored fields carry
  what, and the environment it was all run in.
- New command `compare-range`: runs graphsense-lib 2.16.3 and 2.16.4 (or other revisions) on the same block range of
  a node and summarises what changes: totals and fees, newly accounted Orchard and Ironwood value, coinjoin flags,
  clustering input sets and relation estimates. Writes `summary.json`, `summary.md` and
  `changed-transactions.csv`; can keep the node's answers and run again from them offline.
- New command `scan`: lists the blocks of a range whose transactions carry Orchard or Ironwood value, and the
  transactions whose coinjoin flag could change with the correction.
- New command `check`: compares the Zcash rows a GraphSense installation stored (Cassandra raw keyspace, Delta lake)
  block by block with the node.
- The output formats of the three commands are versioned and described by JSON Schemas in `manifest/`;
  [docs/OPERATOR-TOOLS.md](docs/OPERATOR-TOOLS.md) documents the commands and every member.
- New: [results/](results/), the summaries of `compare-range` on five mainnet windows and of `scan` over the whole
  affected range.
- `install`, `run`, `explain`, `code-checksum` and `verify-node` behave as in 1.0.1. Dataset 1.0.0 is unchanged.
- Tool 1.1.0. Published results checksum: `5610404f07c82715d0c8722b2b7170b55aa7f06eaf82828ee536c42d06c70dd0`
  (transformer layer: `325ce06572bb93a059763407182cab0d6e7129b814d85d74e42b7401e6b8e10e`). It differs from the checksum of 1.0.1 only because the tool's
  version is part of the results.

## [1.0.1] - 2026-10-08

- The workflow also runs the validation with two later graphsense-lib revisions in the place of 2.16.4: release
  2.17.0, and `develop` after the merges of graphsense-lib pull requests
  [#192](https://github.com/graphsense/graphsense-lib/pull/192) and
  [#193](https://github.com/graphsense/graphsense-lib/pull/193). Both must report the expected values. The README
  lists them.
- The README and `docs/METHOD.md` say on which platforms the workflow runs the validation.
- Tool 1.0.1: every command behaves as in 1.0.0. Only the version number it prints and the checksum below differ.
  Dataset 1.0.0 is unchanged.
- Published results checksum: `74bec033e6f9452d397594d9a97603634a79b362b98b4bc2a3b22f8b98a020cc`
  (transformer layer: `bd7f36020c532bd26609784761207f5d760ba3987123f0de0d37da7c414a249d`).
  It differs from the checksum of 1.0.0 only because the tool's version is part of the results; every other
  member of the results is identical.

## [1.0.0] - 2026-10-08

Initial release.

- Tool 1.0.0: the commands `install`, `run`, `explain`, `code-checksum` and `verify-node`.
- Dataset 1.0.0 (2026-10-06): 10 Zcash mainnet blocks, 47 transactions, 43 previous transactions. Seven blocks are
  the mainnet test fixtures of graphsense-lib (pull request #163), reused unchanged; blocks 1687119, 1687143 and
  3438010 and all previous transactions were captured from Zebra 6.4.2.
- Compared revisions: graphsense-lib 2.16.3 (before) and 2.16.4 (after).
- Published results checksum: `2aa7d4024145c55e9a2829ffb393d41e250c0b07fee76ed68904cb5f93670805`
  (transformer layer: `127a724b17283f083c89a5e64ab639414e77b2f0933378bb681fe7a3b5b2b58d`).
