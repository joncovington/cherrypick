#!/usr/bin/env python3
"""Launcher: put src/ on sys.path and delegate to the CLI.

Run from a source checkout as `python run.py status` or `python run.py policies`. The paper loop has
its own argv (`python -m cherrypick.calendars.paper_loop --once|--interval N|--settle|--status`),
which is what the orchestrator's supervisor drives.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cherrypick.calendars.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
