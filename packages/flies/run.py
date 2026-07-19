#!/usr/bin/env python3
"""Launcher: put src/ on sys.path and delegate to the CLI.

Run from a source checkout as `python run.py once --snapshot snap.json` or
`python run.py settle --date 2026-07-20 --symbol SPX --price 6875.42`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
