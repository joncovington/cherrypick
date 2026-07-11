"""cherrypick orchestration package.

cherrypick is an *umbrella orchestrator* that drives sibling trading modules (MEICAgent,
EarningsAgent) in place for unattended PAPER data collection. It never modifies a module's
internals, never touches live trading, and never sits on any module's loop decision path.

The prime directive: a user sets up paper plans, walks away, and trusts that any failure is
either *notified* or, at an absolute floor, *warned through logging*.
"""

import sys as _sys
from pathlib import Path as _Path

# Bootstrap the cherrypick-core submodule (src/_core) onto sys.path so `import cherrypick.core` resolves
# in every entry path — `python run.py`, pytest, and the editable-installed `cherrypick` console script.
# The editable install exposes only src/ (not src/_core) and the packaged wheel excludes the submodule,
# so we self-bootstrap here, before report/calibrate import cherrypick.core. This __init__ sits at
# src/cherrypick/orchestrator/, so src/_core is parents[2]/_core. Mirrors the modules' bootstrap
# (memory: core-import-bootstrap-pattern).
_CORE = _Path(__file__).resolve().parents[2] / "_core"
if _CORE.is_dir() and str(_CORE) not in _sys.path:
    _sys.path.insert(0, str(_CORE))

ROOT_PACKAGE = "cherrypick"
