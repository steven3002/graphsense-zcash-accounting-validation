# Contributing

Thank you for looking at this repository. It is small and has one purpose: a validation result that anyone can
reproduce. Contributions are welcome when they keep that property. The style rules below follow the
[contribution guidelines of `librustzcash`](https://github.com/zcash/librustzcash/blob/main/CONTRIBUTING.md#styleguides),
adapted to a small Python project.

## Table of contents

- [I have a question](#i-have-a-question)
- [Reporting a problem](#reporting-a-problem)
- [Proposing a change](#proposing-a-change)
- [Checks to run](#checks-to-run)
- [Styleguides](#styleguides)
  - [Git usage](#git-usage)
  - [Changelog entries](#changelog-entries)
  - [Coding style](#coding-style)
- [Changing the dataset](#changing-the-dataset)

## I have a question

Read the [README](README.md) and the pages in [docs/](docs/) first. If the answer is not there, open an issue.
A question that the documentation should have answered is a documentation bug; say so.

## Reporting a problem

A good report lets someone else see the problem on their machine:

- the exact commands you ran, starting from a fresh copy;
- the confirmation block the run printed, or the complete error line;
- `report/report.json`, if one was written;
- your operating system, and how you installed the environments (uv, pip or Docker).

If you believe an **expected value** is wrong, say which case and field, and which source you compared it with
(your node, a block explorer, the protocol specification). `gs-zec-validate.py explain <case>` prints how the value
is derived. Do not report a difference between this repository's expected values and graphsense-lib here as a
graphsense-lib bug; report it to graphsense-lib, with the case.

## Proposing a change

- Open an issue first for anything larger than a correction, so the direction can be agreed before work is done.
- Keep a pull request to one purpose. A change to the dataset, a change to the tool and a change to the wording
  are three pull requests.
- A pull request may be opened as a draft. Move it to "ready for review" when the checks below pass.

## Checks to run

All of these must pass before review, on at least one supported Python version (3.10 to 3.13):

```
uv run gs-zec-validate.py install
uv run gs-zec-validate.py run
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
```

The end-to-end tests in `tests/test_e2e.py` need the two environments; without them they are skipped, and a
skipped test is not a passed test.

## Styleguides

### Git usage

**Merge workflow.** Pull requests are merged with merge commits, so that the history of a change stays visible.
If the target branch moved and your branch conflicts with it, rebase your branch and resolve the conflict in the
commit it belongs to.

**Branch history.**

- A commit is one discrete change. There are no "work in progress" or "fix review comments" commits in a branch
  that is ready for review: fold such changes into the commit they belong to.
- A commit that changes behaviour, the dataset or the published checksum changes `CHANGELOG.md` in the same commit.
- A commit that fixes a bug is preceded by a commit that adds a failing test for it, where that is practical.
- Formatting-only changes are separate from functional changes.

**Commit messages.**

- A short title, preferably under 72 characters, in the imperative: "Reject manifests with duplicate members".
- A body that says why the change is made, unless that is obvious from the title.

### Changelog entries

`CHANGELOG.md` is written for users of the repository, not for its developers.

- An entry is needed for a change in what a command does or prints, in an exit status, in the manifest or report
  format, in the dataset, in the pinned revisions, and for every change of the published checksum.
- The entry is part of the commit that makes the change.
- An entry describes the difference from the last release, not from the previous commit.
- Internal refactoring and test-only changes get no entry.
- The `## [Unreleased]` heading stays at the top, also when it is empty.

### Coding style

- **The tool depends on the Python standard library only.** A new dependency of the tool needs a reason that
  outweighs every reviewer having to install and trust it. Development tools (pytest, ruff, ty, jsonschema) are a
  separate group.
- **Amounts are integers in zatoshi.** No floating-point number may enter the manifest, the expected values or
  the report. Convert nothing silently: a value that is not a plain integer is an error or is reported as text.
- **The reference calculation stays independent.** `gs_zec_validate/reference.py` imports nothing from
  graphsenselib and copies nothing from it, and it stays short enough to read in a few minutes. A rule it applies
  cites the section of the Zcash protocol specification it comes from.
- **Nothing of GraphSense is modified or replaced.** `gs_zec_validate/worker.py` feeds evidence in and reads
  results out. It contains no accounting.
- **A mismatch is a result, not an exception.** Checks record failures; they do not stop at the first one.
  Errors that stop a run (a missing file, a wrong checksum) are one line that names the file or the case, never a
  traceback.
- **No required check may pass silently.** There is no "warning but success". A check that could not look at
  anything is `not-applicable`, not `pass`.
- **Serialised data is versioned at the top level.** The manifest and the report each carry a schema version and
  are described by a JSON Schema in `manifest/`. A change to either format changes its schema, its documentation
  page and, if existing files stop being valid, its schema version.
- **Evidence is data.** It is read with a JSON parser and nothing else.
- **Determinism.** Everything in `results` must be identical on every correct run. Times, paths, platform names and
  anything else that varies belongs in `run`.
- Code is formatted with `ruff format` and passes `ruff check` and `ty check` without new ignore comments, unless
  the comment says why.

## Changing the dataset

The dataset is the manifest plus the evidence files. Its rules:

1. **Expected values never come from GraphSense.** They come from the node's integer fields, and the reference
   calculation must reproduce them from the evidence.
2. **A block is added whole.** Every transaction of an evidence block is a case, and every previous transaction
   its transparent inputs spend is added as evidence. Keep additions small.
3. **Evidence is stored as the node sent it:** the bytes of the `result` member of the JSON-RPC answer, not parsed
   and written again, gzip-compressed without a time or file name in the header. Record the node's version and
   the capture time. [docs/EVIDENCE.md](docs/EVIDENCE.md) describes the procedure.
4. **Compare what you add with a second source** before you add it, and say in the pull request which.
5. **Shapes are derived, not chosen.** List the shapes the rules in [docs/DATASET.md](docs/DATASET.md) give; the
   tool rejects any other list.
6. Bump `dataset.version` by the rule in [docs/MANIFEST.md](docs/MANIFEST.md), add a changelog entry, regenerate
   `RESULTS.sha256` from a run that passes every check, and update the checksum where the documentation quotes it.

Changing a pinned revision (for example to a newer graphsense-lib release) is a dataset change as well: it needs
new lock files (`locks/regenerate.sh`), the new release file's checksums in the manifest, and a new published
checksum.

`locks/regenerate.sh` resolves the lock files as of its `CUTOFF` date. Whenever you change that date, change
`exclude-newer` in `pyproject.toml` to the same date (a test fails when the two differ): uv reads `pyproject.toml`
in the directory the tool is started in and applies that setting to `install` as well, where it refuses pinned
files that were published after that date.
