# Third-party material in this repository

This repository is an independent project. It is not part of the GraphSense project and is not endorsed by it.
It contains the following files that come from graphsense-lib, unchanged.

## Reused test fixtures

- Files: the seven files in `evidence/graphsense-lib-fixtures/`
  (`zcash_block_600000.json.gz`, `zcash_block_1687194.json.gz`, `zcash_block_2501409.json.gz`,
  `zcash_block_3442130.json.gz`, `zcash_block_3442400.json.gz`, `zcash_block_3463373.json.gz`,
  `zcash_block_3479000.json.gz`).
- Origin: <https://github.com/graphsense/graphsense-lib>, directory `tests/ingest/resources/`, at tag `v2.16.4`
  (commit `411b987a0f246943d4ee70421f43b17a3cba8f8c`). They were added there by pull request #163.
- The files are byte-for-byte copies. `manifest/manifest.json` records the sha256 and the git blob id of each one;
  the blob ids are the same as in the origin repository.

## graphsense-lib release files

- Files: `vendor/graphsense_lib-2.16.3-py3-none-any.whl` and `vendor/graphsense_lib-2.16.4-py3-none-any.whl`.
- Origin: the release files published on PyPI, <https://pypi.org/project/graphsense-lib/2.16.3/#files> and
  <https://pypi.org/project/graphsense-lib/2.16.4/#files>. Their sha256 checksums are in `manifest/manifest.json`
  and can be compared with the ones PyPI shows.
- They are the two code revisions this repository compares. They are carried here so that the comparison still
  works if a release is ever removed from PyPI.

## Logic reproduced from graphsense-lib and graphsense-spark

No code of either project is copied into this repository's own modules. Two pieces of logic are written again so
that the operator commands compute what GraphSense computes:

- `gs_zec_validate/capture.py`, `_split`: the block-range splitting of graphsense-lib's `split_blockrange`
  (`ingest/source.py`), so that `compare-range` processes a range in the pieces graphsense-lib's ingest uses.
- `gs_zec_validate/relations.py`: the relation estimate and the clustering input set of graphsense-spark's
  `Transformator.plainAddressRelations` and `Transformator.plainAddressCluster` (Scala, in the `spark/` directory of
  the graphsense-lib repository), written in Python; the module cites the Scala lines.

## Licence of that material

graphsense-lib is published under the MIT licence:

```
MIT License

Copyright (c) 2022 GraphSense

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

graphsense-spark (the `spark/` directory of graphsense-lib) is published under the same licence terms, with the
copyright line "Copyright (c) 2021 AIT Austrian Institute of Technology" (`spark/LICENSE`).

Everything else in this repository is covered by `LICENSE` (MIT, Copyright (c) 2026 Steven Hert).
The evidence files in `evidence/zebra-6.4.2/` are public Zcash mainnet data as a node returns it.
