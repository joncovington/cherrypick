#!/usr/bin/env python3
"""Launcher: put src/ on sys.path and delegate to the read-side CLI.

Run from a source checkout as `python run.py headline` or `python run.py settlement-audit`.

The things that RUN keep their own argv and are unaffected by this file: the paper loop
(`python -m cherrypick.meic.paper_loop --once|--force|--start|--status`), the streamer, the ledger
writer (`...meic.db`) and the broker client (`...meic.tt`). The orchestrator's supervisor drives
those directly, and the paper loop shells out to the last two on every tick.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cherrypick.meic.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
