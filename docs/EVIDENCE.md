# The evidence

What the files in `evidence/` are, how they were made, and how to check them without trusting this repository.

## What is stored

| Directory | Files | Content | Captured from |
|---|---:|---|---|
| `evidence/graphsense-lib-fixtures/` | 7 | blocks 600000, 1687194, 2501409, 3442130, 3442400, 3463373, 3479000 | Zebra 6.3.0 (reused files, see below) |
| `evidence/zebra-6.4.2/block_<height>.json.gz` | 10 | the same seven blocks and blocks 1687119, 1687143, 3438010 | Zebra 6.4.2, 2026-10-06 |
| `evidence/zebra-6.4.2/tx_<txid>.json.gz` | 43 | every earlier transaction whose transparent outputs the ten blocks spend | Zebra 6.4.2, 2026-10-06 |

Each file is the gzip-compressed `result` member of one JSON-RPC answer of a Zcash mainnet node:
`getblock "<height>" 2` for a block, `getrawtransaction "<txid>" 1` for a transaction.
Together the files are about 870 kB.

The three blocks without transparent inputs (1687119, 1687143, 3438010) need no previous transactions. The other
seven need 5, 7, 4, 2, 1, 22 and 2 of them.

## The reused files

The seven files in `evidence/graphsense-lib-fixtures/` are the mainnet test fixtures that
[pull request #163](https://github.com/graphsense/graphsense-lib/pull/163) added to graphsense-lib, in
`tests/ingest/resources/`. They are copied byte for byte from tag `v2.16.4`
(commit `411b987a0f246943d4ee70421f43b17a3cba8f8c`). The manifest records the git blob id of each file; it is the
same id the file has in the graphsense-lib repository, so anyone with a clone of graphsense-lib can compare:

```
git -C /path/to/graphsense-lib ls-tree v2.16.4 tests/ingest/resources/
```

Three things to know about them:

- **Node version.** The description of pull request #163 says they were captured from Zebra 6.3.0. The files agree
  with that: they show the byte order of Orchard and Ironwood anchors that Zebra printed before version 6.4.0.
- **Capture time.** It was not recorded. The gzip headers of the files carry times on 2026-09-12.
- **Encoding.** They are not the node's bytes. They were written again by a JSON library: the separators are
  `", "` and `": "`, and 46 decimal numbers are spelled differently, for example `3.28e-05` where the node prints
  `0.0000328`. Every number denotes the same value. This matters because both graphsense-lib releases read some
  amounts in their decimal form; the check below shows it makes no difference.

## The new captures

Everything in `evidence/zebra-6.4.2/` was captured for this dataset on 2026-10-06 between 10:32:48 and 10:33:01 UTC
from a Zebra 6.4.2 mainnet node (`"subversion": "/Zebra:6.4.2/"`, protocol version 170160) that was at the chain
tip. The manifest records the capture time and the request of every file.

How a file was made:

1. One JSON-RPC request, for example
   `{"jsonrpc":"2.0","id":1,"method":"getblock","params":["1687143",2]}`.
2. From the answer `{"jsonrpc":"2.0","id":1,"result":<R>}` the bytes of `<R>` were cut out. They were not parsed
   and written again, so the file holds exactly what the node sent.
3. Those bytes were gzip-compressed with a header that carries no time and no file name.

## Two captures of the same block must agree

For the seven reused blocks there are two captures, made by two Zebra versions weeks apart. On every run the tool
compares them leaf by leaf and gives both to GraphSense. The only differences allowed are the two the manifest
lists under `capture_differences_allowed`:

| Member | Rule | Why |
|---|---|---|
| `confirmations` (anywhere) | ignored | It grows with every new block. GraphSense does not read it. |
| `anchor` inside `orchard` and `ironwood` | byte-reversed | Zebra prints these anchors in the opposite byte order since 6.4.0. GraphSense does not read them. |

Between the two captures, 47 `confirmations` values and 13 anchors differ in this way; nothing else differs. Both
graphsense-lib releases return identical records for both captures (check *Each revision returns the same records
for both captures of a block*).

## Integrity

The manifest pins two checksums per file: `sha256` of the file as stored, and `content_sha256` of the decompressed
content. The second one is what GraphSense is given; it does not depend on the compression. `run` verifies both
for all 60 files before it does anything else, and stops with exit status 3 if a file is missing or differs.

`.gitattributes` marks `*.gz` and `*.whl` as binary, so a checkout on any system leaves them unchanged.

## Check the evidence yourself

### Against your own node

If you run a Zcash node with a transaction index, this compares all 53 items with it (54 requests, one at a
time: one `getinfo` and one per item):

```
uv run gs-zec-validate.py verify-node --rpc-url http://127.0.0.1:8232 --rpc-cookie-file /path/to/.cookie
```

For a node with a user name and password instead of a cookie file, use `--rpc-user NAME` and put the password in
the environment variable `GS_ZEC_VALIDATE_RPC_PASSWORD`. The password is never printed or stored.

The command reruns the reference calculation on your node's answers and requires the manifest's expected values,
block hashes, transaction ids and value-pool changes. It also compares the full answers with the packaged
captures and reports how many other members differ; another node version may print additional members, so those do
not fail the check. Against the Zebra 6.4.2 node the captures were made from, it reports no difference at all.

To fetch a single item by hand:

```
curl -s --user "$RPC_USER:$RPC_PASSWORD" -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"getblock","params":["1687143",2]}' http://127.0.0.1:8232/
```

### Against a public block explorer

On 2026-10-06 every value of the dataset was compared with two public sources that are not the node the captures
came from:

- **Cipherscan**, `https://api.mainnet.cipherscan.app/api/tx/<txid>` and `.../api/block/<height>`.
  For all 47 transactions it shows the same block hash and height, the same Sapling, Orchard and Ironwood
  balances, the same transparent input and output totals, the same value for each of the 95 previous outputs, and
  the same output values; for all ten blocks the same hash, merkle root and transaction list. Its fee is the same
  for 40 transactions. For the 7 transactions with Sprout JoinSplits it shows a fee of 0, because it does not
  expose Sprout value.
- **Blockchair**, `https://api.blockchair.com/zcash/raw/transaction/<txid>`. Its decoded form of the raw
  transaction shows the same transaction id, inputs, output values, Sapling, Orchard and Ironwood balances and
  Sprout `vpub` values for all 47 transactions.

  Blockchair's own fee figure (`https://api.blockchair.com/zcash/dashboards/transaction/<txid>`) is the same for
  the 34 transactions without Orchard or Ironwood value, including the 7 Sprout ones. For the 13 transactions
  with Orchard or Ironwood value it differs, because that figure does not take those two pools into account: for
  the worked example `bd84ece1…6517` it shows 89,000 instead of 1,000. Compare the decoded transaction, not that
  figure.

So every balance, total and previous-output value was confirmed by at least one independent source, and every fee
by at least one. To look at one transaction yourself, open the first URL with a transaction id from
[DATASET.md](DATASET.md).

## What the evidence does not prove by itself

The tool checks that the files are the ones the manifest pins and that they are consistent with each other and
with the node's block totals. It does not recompute block hashes or transaction ids and does not verify proofs or
signatures. Whether the files show the real chain is what the two checks above are for.
