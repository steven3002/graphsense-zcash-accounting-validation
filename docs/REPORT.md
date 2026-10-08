# The report

`run` writes two files into the report directory (`./report` unless `--report-dir` says otherwise):

- `report.json`: the machine-readable report. `manifest/report.schema.json` is its JSON Schema; the tool validates
  the report against it before writing.
- `report.md`: the same content for reading.

## Two kinds of content

| Part of `report.json` | Content | Same on every correct run? |
|---|---|---|
| `results` | What was compared and what came out. | Yes, byte for byte. |
| `results_sha256` | The checksum of `results`. | Yes. |
| `published_results_sha256`, `matches_published` | The checksum in `RESULTS.sha256` and whether the two are equal. | Yes. |
| `transformer_results`, `transformer_results_sha256`, `transformer_matches_published` | The further layer; only with `--with-transformer`. | Yes. |
| `verdict`, `exit_status` | `pass` and 0, or `fail` and 1. | Yes. |
| `run` | Start time, duration, Python version, operating system and machine type, and per environment the Python version, the installed packages and the number of loopback connections. No host name, user name or path. | No. |

## The results checksum

`results_sha256` is the sha256 of `results` written as JSON with sorted keys, the separators `,` and `:`, no other
white space, and ASCII characters only. You can recompute it from a written report without this tool:

```
python3 -c "import json,hashlib; r=json.load(open('report/report.json'))['results']; print(hashlib.sha256(json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest())"
```

or with jq:

```
jq -cjSa .results report/report.json | sha256sum
```

`RESULTS.sha256` in the repository holds the checksum that a run of the two pinned revisions must produce. For
such a run a different checksum is a failure. The file has one line per checksum:

```
2aa7d4024145c55e9a2829ffb393d41e250c0b07fee76ed68904cb5f93670805  results
127a724b17283f083c89a5e64ab639414e77b2f0933378bb681fe7a3b5b2b58d  transformer-results
```

The checksum changes whenever the manifest, the evidence, the tool's version or the wording of a check changes.
It does not depend on the platform, the Python version, the working directory, the time, or on how the two
environments were installed.

## What `results` contains

| Member | Content |
|---|---|
| `tool` | Name and version of this tool. |
| `manifest` | Path, sha256, schema version and dataset version of the manifest that was used. |
| `canonical` | `true` only if both sides ran the pinned code. |
| `revisions.before`, `revisions.after` | `pinned`: version, git tag, commit, release-file checksum and code checksum from the manifest. `found`: the code checksum, file count and per-file checksums of what was actually imported. `is_pinned_code`. `import_error` if the package could not be imported. |
| `evidence` | Every evidence file that was used, with the checksums that were verified. |
| `checks` | Every check with its status (`pass`, `fail`, `not-applicable`), how many items it looked at, and one message per failure. |
| `blocks` | Per block: each pool's change as the node reports it and as the transactions add up to it, the fees, the value created, the subsidy, whether the subsidy rule holds, and in how many places the block's two captures differ in an allowed way. |
| `cases` | Per transaction: see below. |
| `summary` | Counts of cases and compared fields, matching and not, for each side; the cases where the old release differs and how many of those are explained. |
| `verdict` | `pass` if no check failed. |

### A case

```json
{
  "id": "1687194/4",
  "txid": "bd84ece1a1f9930085768a880d99445ee1ee13686c2e5979b30d920e90866517",
  "block_height": 1687194,
  "version": 5,
  "shapes": ["cross-pool", "fully-shielded-fee", "sapling+orchard"],
  "old_code_expected_to_differ": true,
  "fields": {
    "fee": {"expected": 1000, "before": 89000, "after": 1000, "before_matches": false, "after_matches": true},
    "orchard_value_balance": {"expected": -88000, "before": null, "after": -88000, "before_matches": null, "after_matches": true}
  },
  "before": {"exception": null, "matches_frozen": true, "differs_from_expected": true, "explained": true,
             "explanation": "not read by the old revision: Orchard balance -88000"},
  "after": {"exception": null, "matches_expected": true, "mismatches": []}
}
```

(`fields` has 14 members; two are shown.) For every field the report gives the expected value, what each release
reported, and whether each equals the expected value. `null` means the release reports no such value: the old
release has no per-pool balances. If a release raised an exception on the block, the exception is the recorded
result (`exception` with `type` and `message`), and the values are `null`.

The fields: `is_coinbase`, `transparent_input`, `transparent_output`, `sprout_value_balance`,
`sapling_value_balance`, `orchard_value_balance`, `ironwood_value_balance`, `shielded_inputs`, `shielded_outputs`,
`input_count`, `output_count`, `total_input`, `total_output`, `fee`. [MANIFEST.md](MANIFEST.md) defines each.

## The checks

In the order they are reported. A check that could not look at anything is `not-applicable`, never `pass`.

| Id | It fails when |
|---|---|
| `evidence.identity` | an evidence file holds another block or transaction than the manifest names, or a transaction of a block has no case |
| `evidence.captures-agree` | two captures of a block differ outside the allowed differences |
| `evidence.units` | a decimal amount and its integer form in the evidence denote different values |
| `expected.reference` | the reference calculation gives another value than the manifest for any expected field, previous output or unread balance |
| `expected.shapes` | a case's shapes differ from the ones derived from the evidence, or a required shape has no case |
| `expected.block-pools` | a pool's change as the node reports it differs from what the transactions add up to, or from the manifest |
| `expected.block-issuance` | the value a block created is not the block subsidy (from NU6), or exceeds it (before) |
| `revisions.identity` | never: with other code than the pinned one the run stops with exit status 3. It is `not-applicable` when another revision was requested on purpose |
| `graphsense.ran` | a release raised an exception on a block, or could not be imported |
| `graphsense.requests` | GraphSense asked for something other than the block and its packaged previous transactions, a request was not answered, or an input stayed unresolved |
| `graphsense.captures-agree` | a release returned different records for the two captures of a block |
| `graphsense.offline` | the process that hosts GraphSense attempted a connection or name lookup that is not loopback |
| `units` | an amount returned by GraphSense is not an integer |
| `after.pool-balances-exposed` | the after revision's records have no per-pool balance fields |
| `signs` | a pool balance reported by the after revision differs from the expected one |
| `accumulation` | the shielded inputs or outputs, or the counts, reported by the after revision differ |
| `totals` | total input, total output or fee reported by the after revision differ |
| `resolution` | the coinbase flag or a transparent total reported by the after revision differs |
| `before.frozen` | the before revision reports something other than the manifest froze |
| `before.explained` | the before revision differs from the expected values in any way other than by the unread Orchard and Ironwood value, or does not differ where it must |

With `--with-transformer`, `transformer_results` has three more: `transformer.ran`, `transformer.totals` (the stored
rows carry the exporter's totals and fee, and those equal the expected values for the after revision and the frozen
ones for the before revision) and `transformer.coinjoin` (the flag equals the value frozen in the manifest).

## Exit status

| Status | Meaning |
|---:|---|
| 0 | Every required check passed and, for the pinned pair, the checksum equals the published one. |
| 1 | The validation ran and a required check failed. The report is written. |
| 2 | The command line was wrong. |
| 3 | The validation could not run: the manifest is invalid, an evidence file is missing or changed, an environment is missing, or an environment holds other code than the pinned one. No report is written. |
| 4 | The report could not be written. |

For status 3 and 4 the tool prints one line that starts with `gs-zec-validate: error:` and names the file or the
side concerned.

Neither the exit status nor the report depends on the screen output being read: if the output is piped into a
program that stops reading (`run | head`), the run still completes, writes the whole report and ends with the
status of its verdict.

## Runs with other revisions

When `--before-python`, `--after-python`, `--before-src` or `--after-src` makes a side run code that is not the
pinned code, `results.canonical` is `false`, the confirmation block says `NON-CANONICAL RUN` and gives the verdict
as `PASS for a NON-CANONICAL pair of revisions` or `FAIL for a NON-CANONICAL pair of revisions`, and the checksum
is not compared with the published one. The checks still decide the exit status.
