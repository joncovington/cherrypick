#!/usr/bin/env python3
"""Launcher: put src/ on sys.path and delegate to the CLI.

Run from a source checkout as `python run.py gex --symbol SPX` or `python run.py dashboard --serve`.
The umbrella (Cherrypick) invokes `python run.py gex --symbol <sym> --json` to embed a live GEX card.
"""

import sys
from pathlib import Path

# Source-checkout convenience: put src/ on sys.path so `python run.py` works without an install.
# An installed copy resolves cherrypick.gex from the environment and ignores this.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cherrypick.gex.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
