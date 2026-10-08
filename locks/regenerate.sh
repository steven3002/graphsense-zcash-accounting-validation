#!/bin/sh
# Regenerate the four lock files. Needs uv (https://docs.astral.sh/uv/). Run from the repository root:  sh locks/regenerate.sh
#
# 1. Resolve the complete dependency set of graphsense-lib 2.16.4 as of the cut-off date (2.16.3 declares the same requirements).
# 2. Resolve the two reduced sets (exporter.in, transformer.in) under the constraint that every version equals the one
#    from step 1, for every supported platform and Python version at once, with the checksum of every published file.
# 3. Put the pinned graphsense-lib wheel (its wheel checksum only) in front.
set -eu
cd "$(dirname "$0")"
CUTOFF=2026-10-06T00:00:00Z
BEFORE_WHEEL=0b11632b5fd55fd03f1233410eba742b34afac6aa1a9321854ec5bc6a5b2f32b
AFTER_WHEEL=f60aa5de21faae27e0a04e4f8ea31fdfd5d676e9fedc7e1e03805cecc9f977d7
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo "graphsense-lib==2.16.4" > "$tmp/full.in"
uv pip compile "$tmp/full.in" --universal --python-version 3.10 --exclude-newer "$CUTOFF" --no-header --no-annotate -o "$tmp/full.txt" --quiet
grep -E '^[A-Za-z0-9_.-]+==' "$tmp/full.txt" | grep -v '^graphsense-lib==' > "$tmp/constraints.txt"
for set in exporter transformer; do
  uv pip compile "$set.in" -c "$tmp/constraints.txt" --universal --python-version 3.10 --exclude-newer "$CUTOFF" \
    --generate-hashes --no-header -o "$tmp/$set.txt" --quiet
done
write() {  # <output> <version> <wheel sha256> <resolved third-party file>
  {
    echo "# Hash-locked environment for graphsense-lib $2. Made by locks/regenerate.sh; do not edit by hand."
    echo "# Install with --no-deps: this is on purpose not graphsense-lib's full dependency set (see docs/METHOD.md)."
    echo "graphsense-lib==$2 \\"
    echo "    --hash=sha256:$3"
    sed -e 's|-c .*/constraints.txt|-c (the versions of a full install)|' "$4"
  } > "$1"
}
write before.txt 2.16.3 "$BEFORE_WHEEL" "$tmp/exporter.txt"
write after.txt 2.16.4 "$AFTER_WHEEL" "$tmp/exporter.txt"
write before-transformer.txt 2.16.3 "$BEFORE_WHEEL" "$tmp/transformer.txt"
write after-transformer.txt 2.16.4 "$AFTER_WHEEL" "$tmp/transformer.txt"
echo "wrote before.txt after.txt before-transformer.txt after-transformer.txt"
