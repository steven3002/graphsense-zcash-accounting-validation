"""Evidence files: integrity, decoding, and comparison of two captures of the same thing.

Every evidence file is a gzip-compressed JSON document: the `result` member of one JSON-RPC response of a
Zcash node. The manifest records two checksums per file: of the file as stored, and of the decompressed
content. The second one is what GraphSense is fed, and it does not depend on how the file was compressed.

Evidence is data. It is only ever read with a JSON parser.
"""

import gzip
import hashlib
import json
import os
import zlib
from decimal import Decimal


class IntegrityError(Exception):
    """An evidence file is missing or is not the file the manifest describes."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_verified(root, entry):
    """Return the decompressed content of one evidence file after checking it against its manifest entry."""
    path = os.path.join(root, *entry["path"].split("/"))
    try:
        with open(path, "rb") as handle:
            stored = handle.read()
    except FileNotFoundError:
        raise IntegrityError(f"evidence file missing: {entry['path']}") from None
    except OSError as error:
        raise IntegrityError(f"evidence file unreadable: {entry['path']}: {error.strerror}") from None
    if sha256(stored) != entry["sha256"]:
        raise IntegrityError(
            f"evidence file changed: {entry['path']}: sha256 is {sha256(stored)}, the manifest says {entry['sha256']}"
        )
    try:
        content = gzip.decompress(stored)
    except (OSError, EOFError, ValueError, zlib.error) as error:
        raise IntegrityError(f"evidence file is not valid gzip: {entry['path']}: {error}") from None
    if sha256(content) != entry["content_sha256"] or len(content) != entry["content_bytes"]:
        raise IntegrityError(
            f"evidence content changed: {entry['path']}: content sha256 is {sha256(content)}, the manifest says {entry['content_sha256']}"
        )
    return content


def parse(content):
    """Parse evidence. Decimal numbers stay exact decimals, so they can be compared with their integer form."""
    return json.loads(content, parse_float=Decimal)


def differences(first, second, rules, path="$"):
    """Compare two captures of the same block or transaction, leaf by leaf.

    Returns `(unexpected, allowed)`: two lists of paths. `rules` is the manifest's list of differences that
    are allowed between captures, each one `{"key": ..., "parents": [...] or None, "rule": "ignored" | "byte-reversed"}`:

      ignored        the values may differ freely (the number of confirmations grows with every new block);
      byte-reversed  the values may differ only by the order of their bytes (hex strings).
    """
    unexpected, allowed = [], []
    _walk(first, second, rules, path, None, unexpected, allowed)
    return unexpected, allowed


def _walk(a, b, rules, path, parent, unexpected, allowed):
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            where = f"{path}.{key}"
            if key not in a or key not in b:
                unexpected.append(f"{where}: present in only one capture")
                continue
            rule = next(
                (r for r in rules if r["key"] == key and (r["parents"] is None or parent in r["parents"])), None
            )
            if rule is None:
                _walk(a[key], b[key], rules, where, key, unexpected, allowed)
            elif a[key] != b[key]:
                if rule["rule"] == "ignored" or _byte_reversed(a[key], b[key]):
                    allowed.append(where)
                else:
                    unexpected.append(f"{where}: {a[key]!r} versus {b[key]!r}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            unexpected.append(f"{path}: {len(a)} items versus {len(b)}")
            return
        for index, (x, y) in enumerate(zip(a, b, strict=True)):
            _walk(x, y, rules, f"{path}[{index}]", parent, unexpected, allowed)
    elif not _same_leaf(a, b):
        unexpected.append(f"{path}: {a!r} versus {b!r}")


def _same_leaf(a, b):
    """Numbers are compared by value (1.0E-5 and 0.00001 are the same amount); everything else by type and value."""
    if _is_number(a) and _is_number(b):
        return a == b
    return type(a) is type(b) and a == b


def _is_number(value):
    return isinstance(value, (int, Decimal)) and not isinstance(value, bool)


def _byte_reversed(a, b):
    try:
        return isinstance(a, str) and isinstance(b, str) and bytes.fromhex(a)[::-1] == bytes.fromhex(b)
    except ValueError:
        return False
