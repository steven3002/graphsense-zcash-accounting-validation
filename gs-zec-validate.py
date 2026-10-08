#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = []
# ///
"""gs-zec-validate: compare the old and the corrected GraphSense Zcash accounting against packaged evidence.

Start it with uv, which picks or downloads a suitable Python:

    uv run gs-zec-validate.py install
    uv run gs-zec-validate.py run

or with any Python from 3.10 on:

    python3 gs-zec-validate.py install
    python3 gs-zec-validate.py run
"""

import sys

sys.dont_write_bytecode = True  # keep the checkout free of __pycache__

if sys.version_info < (3, 10):  # noqa: UP036  (older Pythons must reach this message)
    sys.exit("gs-zec-validate needs Python 3.10 or newer to start.")

# The package sits next to this script; the type checker does not see it.
from gs_zec_validate.cli import main  # ty: ignore[unresolved-import]

if __name__ == "__main__":
    sys.exit(main())
