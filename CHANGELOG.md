# Changelog

Changes that matter to someone who uses this repository: the tool's behaviour, the dataset, the published
checksum. The tool and the dataset have separate version numbers; [docs/MANIFEST.md](docs/MANIFEST.md) gives the
rule for the dataset's.

## [Unreleased]

- The workflow also runs the validation with two later graphsense-lib revisions in the place of 2.16.4: release
  2.17.0, and `develop` after the merges of graphsense-lib pull requests
  [#192](https://github.com/graphsense/graphsense-lib/pull/192) and
  [#193](https://github.com/graphsense/graphsense-lib/pull/193). The README lists them. The tool, the dataset and
  the published checksum are unchanged.

## [1.0.0] - 2026-10-08

Initial release.

- Tool 1.0.0: the commands `install`, `run`, `explain`, `code-checksum` and `verify-node`.
- Dataset 1.0.0 (2026-10-06): 10 Zcash mainnet blocks, 47 transactions, 43 previous transactions. Seven blocks are
  the mainnet test fixtures of graphsense-lib (pull request #163), reused unchanged; blocks 1687119, 1687143 and
  3438010 and all previous transactions were captured from Zebra 6.4.2.
- Compared revisions: graphsense-lib 2.16.3 (before) and 2.16.4 (after).
- Published results checksum: `2aa7d4024145c55e9a2829ffb393d41e250c0b07fee76ed68904cb5f93670805`
  (transformer layer: `127a724b17283f083c89a5e64ab639414e77b2f0933378bb681fe7a3b5b2b58d`).
