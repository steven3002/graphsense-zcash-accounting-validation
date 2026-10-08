"""Loading and checking the evidence manifest (manifest/manifest.json)."""

import hashlib
import json
import os

from . import schema

MANIFEST_PATH = "manifest/manifest.json"
MANIFEST_SCHEMA_PATH = "manifest/manifest.schema.json"


class ManifestError(Exception):
    """The manifest cannot be used: it is missing, malformed, or contradicts itself."""


def _no_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ManifestError(f"duplicate member '{key}' in a JSON object")
        seen[key] = value
    return seen


def _no_floats(text):
    raise ManifestError(f"the number {text} is not an integer; all amounts must be integers in zatoshi")


def read_json(path, what):
    """Read a JSON file strictly: no duplicate members, no non-integer numbers. Returns (document, sha256 of the file)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as error:
        raise ManifestError(f"{what} cannot be read: {path}: {error.strerror}") from None
    try:
        document = json.loads(
            data, object_pairs_hook=_no_duplicate_keys, parse_float=_no_floats, parse_constant=_no_floats
        )
    except ManifestError as error:
        raise ManifestError(f"{what} is invalid: {error}") from None
    except ValueError as error:
        raise ManifestError(f"{what} is not valid JSON: {error}") from None
    return document, text_sha256(data)


def text_sha256(data):
    """Checksum of a text file that does not depend on how a checkout wrote its line ends (CR LF counts as LF)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def load(root):
    """Load the manifest, validate it against its schema and check that it is consistent with itself.

    Returns (manifest, sha256 of the manifest file). Raises ManifestError with a message a reader can act on.
    """
    manifest, checksum = read_json(os.path.join(root, *MANIFEST_PATH.split("/")), "manifest")
    rules, _ = read_json(os.path.join(root, *MANIFEST_SCHEMA_PATH.split("/")), "manifest schema")
    problems = schema.validate(manifest, rules)
    if problems:
        more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
        raise ManifestError(f"manifest does not follow its schema: {problems[0]}{more}")
    problems = consistency_problems(manifest)
    if problems:
        more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
        raise ManifestError(f"manifest is inconsistent: {problems[0]}{more}")
    return manifest, checksum


def consistency_problems(manifest):
    """Rules a schema cannot express. Every transaction of every evidence block must be exactly one case."""
    problems = []
    blocks = {}
    for block in manifest["blocks"]:
        if block["height"] in blocks:
            problems.append(f"block {block['height']} is listed twice")
        blocks[block["height"]] = block
        for entry in block["evidence"]:
            if entry["capture"] not in manifest["captures"]:
                problems.append(f"block {block['height']}: unknown capture '{entry['capture']}'")
    known_previous = [item["txid"] for item in manifest["previous_transactions"]]
    if len(set(known_previous)) != len(known_previous):
        problems.append("a previous transaction is listed twice")
    for item in manifest["previous_transactions"]:
        if item["evidence"]["capture"] not in manifest["captures"]:
            problems.append(f"previous transaction {item['txid']}: unknown capture '{item['evidence']['capture']}'")
    needed = {txid for block in manifest["blocks"] for txid in block["previous_transactions"]}
    for txid in sorted(needed - set(known_previous)):
        problems.append(f"previous transaction {txid} is needed by a block but has no evidence entry")
    for txid in sorted(set(known_previous) - needed):
        problems.append(f"previous transaction {txid} has an evidence entry but no block needs it")

    paths = [entry["path"] for block in manifest["blocks"] for entry in block["evidence"]]
    paths += [item["evidence"]["path"] for item in manifest["previous_transactions"]]
    for path in sorted({path for path in paths if paths.count(path) > 1}):
        problems.append(f"evidence file {path} is listed more than once")

    seen_ids, seen_txids, per_block = set(), set(), {}
    for case in manifest["cases"]:
        label = f"case {case['id']}"
        if case["id"] in seen_ids:
            problems.append(f"{label} is listed twice")
        if case["txid"] in seen_txids:
            problems.append(f"transaction {case['txid']} has more than one case")
        seen_ids.add(case["id"])
        seen_txids.add(case["txid"])
        block = blocks.get(case["block_height"])
        if block is None:
            problems.append(f"{label}: block {case['block_height']} is not an evidence block")
            continue
        if case["block_hash"] != block["hash"]:
            problems.append(f"{label}: block_hash differs from the block entry")
        if case["id"] != f"{case['block_height']}/{case['index']}":
            problems.append(f"{label}: id must be '<block_height>/<index>'")
        per_block.setdefault(case["block_height"], []).append(case["index"])
        for spent in case["previous_outputs"]:
            if spent["txid"] not in block["previous_transactions"]:
                problems.append(
                    f"{label}: previous output {spent['txid']}:{spent['index']} is not among the block's previous transactions"
                )
        differs = case["old_code"]["differs"]
        if differs != any(case["old_code"]["not_read"].values()):
            problems.append(f"{label}: old_code.differs contradicts old_code.not_read")
    for height, block in sorted(blocks.items()):
        indexes = sorted(per_block.get(height, []))
        if indexes != list(range(block["transaction_count"])):
            missing = sorted(set(range(block["transaction_count"])) - set(indexes))
            detail = f"no case for position {missing[0]}" if missing else "a position has more than one case"
            problems.append(
                f"block {height} has {block['transaction_count']} transactions but {len(indexes)} cases ({detail})"
            )
    return problems
