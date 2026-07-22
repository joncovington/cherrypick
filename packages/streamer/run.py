#!/usr/bin/env python3
"""Launcher: put src/ on sys.path and delegate to the streamer CLI.

  python run.py            # run the streamer daemon in the foreground
  python run.py --status   # print one JSON health object and exit
  python run.py --stop     # stop a running daemon

The cherrypick orchestrator drives this by subprocess (start / status / stop argv), exactly as it drives
MEIC's streamer today — see docs/streamer-package-plan.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
