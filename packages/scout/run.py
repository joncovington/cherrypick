"""Launcher: put src/ on sys.path and delegate to the CLI.

Run from a source checkout as `python run.py serve` or `python run.py watchlist add AAPL`.
"""

import sys
from pathlib import Path

# Source-checkout convenience: put src/ on sys.path so `python run.py` works without an install.
# An installed copy resolves cherrypick.scout from the environment and ignores this.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cherrypick.scout.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
