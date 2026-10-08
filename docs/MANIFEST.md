# The manifest

`manifest/manifest.json` pins everything a validation run uses: the two code revisions, every evidence file, and
for every transaction the expected values. It is plain JSON, so it can be read with any tool and nothing in it can
be executed. `manifest/manifest.schema.json` is its JSON Schema; the manifest is validated against it on every run.

All amounts are integers in zatoshi. The manifest contains no decimal numbers; a file with one is refused.
The checksum of the manifest itself, and of the lock files it pins, is taken with line ends normalised (CR LF
counts as LF), so it is the same on every system. Evidence and release files are binary and are hashed as they are.
Transaction ids and block hashes are always written in full (64 hexadecimal characters).

## Versions

| Field | Changes when |
|---|---|
| `manifest_schema_version` | the structure of the file changes (a member is added, removed or changes meaning). It is `1`. |
| `dataset.version` | the content changes. It follows semantic versioning: **major** when a case is removed or an expected value changes; **minor** when cases or evidence are added; **patch** when only descriptions change. |

Every dataset version has an entry in `dataset.changelog` and in [CHANGELOG.md](../CHANGELOG.md). A new dataset
version always comes with a new published checksum in `RESULTS.sha256`, because the checksum covers the manifest.

## Top level

| Member | Content |
|---|---|
| `manifest_schema_version` | See above. |
| `dataset` | `name`, `version`, `date`, `network` (always `zcash-mainnet`), `description`, and `changelog`: one entry per dataset version with `version`, `date` and the list of `changes`. |
| `revisions` | The two graphsense-lib revisions, `before` and `after`. |
| `captures` | Where and how evidence was captured, by capture name. |
| `capture_differences_allowed` | The only differences allowed between two captures of the same block. |
| `required_shapes` | Shapes that must each have at least one case. |
| `blocks` | The evidence blocks. |
| `previous_transactions` | The evidence for transparent inputs. |
| `cases` | One entry per transaction of every evidence block. |

## `revisions.before` and `revisions.after`

| Member | Content |
|---|---|
| `role` | Why this revision was chosen. |
| `distribution`, `version` | The PyPI distribution (`graphsense-lib`) and its version. |
| `git` | `repository`, `tag` and `commit` of the release. |
| `wheel` | `filename`, `sha256`, `bytes` and `url` of the release file on PyPI. |
| `package_tree_sha256`, `package_file_count` | Checksum over every file of the `graphsenselib` package, and the number of files. The run compares it with the code it imported. [METHOD.md](METHOD.md) gives the definition. |
| `files` | The source files that do the accounting, each with `sha256` and `git_blob` (its git blob id at the tag). |
| `locks` | `default` and `transformer`: `path` and `sha256` of the hash-locked requirement files for this revision. |

## `captures.<name>`

| Member | Content |
|---|---|
| `description` | What this capture is. |
| `node` | `implementation`, `version` and `version_source` (how the version is known); for new captures also `subversion` and `protocol_version` as the node reported them. |
| `network` | `chain` as the node reports it (`main`). |
| `encoding` | How the stored bytes relate to what the node sent. |
| `captured_from_utc`, `captured_to_utc` | Time span of the capture, where it was recorded. |
| `capture_time_note` | What is known about the time where it was not recorded. |
| `origin` | For reused evidence: `repository`, `tag`, `commit`, `directory`, `license`, `copyright`, `introduced_by`. |

## `capture_differences_allowed[]`

| Member | Content |
|---|---|
| `key` | Name of the JSON member the rule is about. |
| `parents` | Names of the enclosing members in which the rule applies; `null` means anywhere. |
| `rule` | `ignored`: the values may differ freely. `byte-reversed`: hexadecimal strings may differ only in byte order. |
| `reason` | Why the difference is harmless. |

## `blocks[]`

| Member | Content |
|---|---|
| `height`, `hash` | The block. |
| `transaction_count` | Number of transactions; the manifest must have exactly one case for each. |
| `evidence` | One file entry per capture of the block (see "File entries"). |
| `previous_transactions` | Ids of the earlier transactions whose outputs this block spends. GraphSense must ask for exactly these. |
| `expected.value_pool_deltas` | The node's `valuePools[*].valueDeltaZat` for the block: `transparent`, `sprout`, `sapling`, `orchard`, `ironwood`, `lockbox`. |
| `expected.fees` | Sum of the fees of the block's transactions. |
| `expected.subsidy` | Block subsidy at this height. |

## `previous_transactions[]`

| Member | Content |
|---|---|
| `txid` | The transaction. |
| `block_height`, `block_hash` | The block that contains it, as the node reported it. |
| `evidence` | Its file entry. |

## File entries

| Member | Content |
|---|---|
| `capture` | Name of the capture it belongs to. |
| `path` | Path in the repository. |
| `sha256`, `bytes` | Checksum and size of the stored, compressed file. |
| `content_sha256`, `content_bytes` | Checksum and size of the decompressed content, which is what GraphSense is given. |
| `git_blob` | Git blob id of the stored file. For a reused file it equals the blob id in the origin repository. |
| `captured_at_utc` | When the node answered (new captures). |
| `rpc` | `method` and `params` of the request (new captures). |

## `cases[]`

| Member | Content |
|---|---|
| `id` | `<block height>/<position in the block>`, for example `1687194/4`. |
| `txid`, `block_height`, `block_hash`, `index`, `version` | The transaction, its block, its position in the block (from 0) and its transaction version. |
| `shapes` | The shapes it has. [DATASET.md](DATASET.md) gives the rule for each. |
| `description` | One line of plain text. |
| `old_code.differs` | Whether graphsense-lib 2.16.3 is expected to report other values than the expected ones. |
| `old_code.not_read` | The `orchard` and `ironwood` balances the old release never reads; both zero when it does not differ. |
| `old_code.why` | The same in words. |
| `previous_outputs` | The transparent outputs it spends: `txid`, `index`, `value`. |
| `expected` | The correct accounting (next table). |
| `before_observed` | What graphsense-lib 2.16.3 reported when the dataset was made. An observation, not a truth. Same members as `expected` without the four balances; `fee` can be negative. |
| `transformer_observed` | `before_coinjoin` and `after_coinjoin`: the coinjoin flag GraphSense's transformer computes, per release. An observation. |
| `references` | Public statements about this transaction made elsewhere. |

### `expected`

| Member | Content | Source in the evidence |
|---|---|---|
| `is_coinbase` | Whether it is a coinbase transaction. | `vin[*].coinbase` |
| `transparent_input` | Sum of the values of the transparent outputs spent. | `vout[n].valueZat` of the previous transactions |
| `transparent_output` | Sum of the values of the transparent outputs created. | `vout[*].valueZat` |
| `sprout_value_balance` | Positive: value left the Sprout pool. | sum of `vjoinsplit[*].vpub_newZat` minus sum of `vpub_oldZat` |
| `sapling_value_balance` | Positive: value left the Sapling pool. | `valueBalanceZat` |
| `orchard_value_balance` | Positive: value left the Orchard pool. | `orchard.valueBalanceZat` |
| `ironwood_value_balance` | Positive: value left the Ironwood pool. | `ironwood.valueBalanceZat` |
| `shielded_inputs` | Value leaving a pool, one entry per leg, ascending. | each positive balance; each positive `vpub_newZat` |
| `shielded_outputs` | Value entering a pool, one entry per leg, ascending. | each negative balance, negated; each positive `vpub_oldZat` |
| `input_count`, `output_count` | Transparent plus shielded inputs, respectively outputs. | derived |
| `total_input`, `total_output` | Transparent total plus shielded inputs, respectively outputs. | derived |
| `fee` | `total_input` minus `total_output`; 0 for a coinbase transaction. | derived |

Every member of `expected` is recomputed from the evidence on every run and must equal the manifest.

## Rules the schema cannot express

The tool also requires, and reports as an error otherwise:

- no member name twice in one JSON object, and no number that is not an integer;
- every case belongs to an evidence block, and every block has exactly one case per transaction position;
- no transaction and no evidence file listed twice;
- every previous transaction a block names has an evidence entry, and every entry is needed by a block;
- `old_code.differs` is true exactly when a balance in `old_code.not_read` is non-zero.
