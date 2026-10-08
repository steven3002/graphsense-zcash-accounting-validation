# Confirming reproducibility

This page is for someone who was handed this repository and asked: do the results reproduce?
It takes about ten minutes. Nothing here needs trust in the author's machine.

## The checklist

1. Get a fresh copy of the repository and install the two environments, as the [README](../README.md) describes
   (set-up A, B or C).
2. Run the validation:

   ```
   uv run gs-zec-validate.py run
   ```

   (With set-up B the command is `python3 gs-zec-validate.py run`; with set-up C it is
   `docker run --rm --network none gs-zec-validate`.)
3. Check three things in the block the run prints last:
   - `verdict     PASS (exit status 0)`;
   - both `before` and `after` end in `pinned code`;
   - the `results` checksum is

     ```
     74bec033e6f9452d397594d9a97603634a79b362b98b4bc2a3b22f8b98a020cc
     ```

     and the `published` line says `identical`.
4. Optional: make it fail on purpose (next section), so you have seen that a pass is not automatic.
5. Paste the confirmation block into your statement. It contains the versions, the checksums and the exit status,
   and nothing that identifies your machine.

The checksum covers everything the validation decided: every expected value, what each release reported for every
transaction, the outcome of every check, and the checksums of the manifest, the evidence files and the code of both
releases. It does not cover the time, the platform or the Python version. So two people who both see this checksum
have reproduced the same result in full. [REPORT.md](REPORT.md) defines it and shows how to recompute it from
`report/report.json` without this tool.

## Make it fail on purpose

Each experiment changes one thing, shows the failure, and puts the thing back. Run them from the repository
directory, after a successful run. The commands are for a POSIX shell.

### 1. The old release where the corrected one is expected

```
uv run gs-zec-validate.py run --after-python .envs/before/bin/python
```

(On Windows the interpreter is `.envs\before\Scripts\python.exe`.)

Expected: exit status 1; the verdict reads `FAIL for a NON-CANONICAL pair of revisions`; exactly 13 cases show `MISMATCH` in the last
column, and they are the 13 cases whose "old" column says `wrong (explained)`; the other 34 cases show `ok`.
This is the central experiment: the validation tells the corrected accounting from the old one, on exactly the
transactions where they differ.

### 2. An expected value that is off by one zatoshi

```
cp manifest/manifest.json manifest.json.saved
python3 -c "p='manifest/manifest.json'; s=open(p).read(); open(p,'w').write(s.replace('\"fee\": 30000', '\"fee\": 30001', 1))"
uv run gs-zec-validate.py run
mv manifest.json.saved manifest/manifest.json
```

Expected: exit status 1, and failures that name the case and the field:

```
case 3463373/2 (6f37465a…04c8) fee: the manifest says 30001, the evidence (graphsense-lib-fixtures) gives 30000
case 3463373/2 (6f37465a…04c8) fee: the manifest says 30001, the evidence (zebra-6.4.2) gives 30000
case 3463373/2 (6f37465a…04c8) fee: expected 30001, after reports 30000
```

The first two lines are the reference calculation refusing the changed value, once for each capture of the block;
the third is GraphSense disagreeing with it.

### 3. One changed byte of evidence

```
cp evidence/zebra-6.4.2/block_1687143.json.gz block.saved
printf 'x' >> evidence/zebra-6.4.2/block_1687143.json.gz
uv run gs-zec-validate.py run
mv block.saved evidence/zebra-6.4.2/block_1687143.json.gz
```

Expected: exit status 3 and one line,
`gs-zec-validate: error: evidence file changed: evidence/zebra-6.4.2/block_1687143.json.gz: sha256 is …`.
Nothing is given to GraphSense when the evidence is not what the manifest pins.

After the three experiments, `uv run gs-zec-validate.py run` passes again with the checksum above.

## If you want to look deeper

- **Where does an expected value come from?** `uv run gs-zec-validate.py explain 1687194/4` prints the derivation
  for one transaction from the evidence files. `gs_zec_validate/reference.py` is the whole calculation; it does
  not import graphsenselib.
- **Is the evidence real?** [EVIDENCE.md](EVIDENCE.md) shows how to compare it with your own node
  (`verify-node`) or a block explorer.
- **Is the code under test really the released code?** The run compares a checksum of every file of the imported
  `graphsenselib` package with the manifest. [METHOD.md](METHOD.md) shows how to recompute that checksum from the
  PyPI release file and from the git tag.
- **Does it use the network?** `run` does not. The process that hosts GraphSense refuses every connection that is
  not to 127.0.0.1, and the report counts the connections. You can also run it with networking disabled
  (set-up C does: `--network none`).
- **The tool's own tests:** `uv run --locked pytest`, `uv run --locked ruff check .`,
  `uv run --locked ruff format --check .`, `uv run --locked ty check`.

## What a confirmation does and does not say

It says that the comparison of graphsense-lib 2.16.3 and 2.16.4 on this dataset gives the published result on your
machine. It does not say that all of GraphSense's Zcash accounting is correct; the README lists what is outside
this validation.
