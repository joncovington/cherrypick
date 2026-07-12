#!/usr/bin/env python3
"""Run the cherrypick CLI from a source checkout: `python run.py <command>`.

Named `run.py` (not `cherrypick.py`) on purpose: a root module named `cherrypick.py` would shadow the
`src/cherrypick` namespace package (a regular module outranks a PEP 420 namespace package on the path).
For an installed copy (`pipx install cherrypick`) use the `cherrypick` console script instead. This
launcher is what the OS scheduler tasks invoke; it puts src/ on sys.path and delegates to
cherrypick.cli.main.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cherrypick.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
