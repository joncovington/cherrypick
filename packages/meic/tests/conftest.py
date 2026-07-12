"""Pytest session setup for the meic package.

Several tests (test_paper_engine, test_dashboard, test_risk_profiles, test_tt)
read the package's local config.json when their modules import or their fixtures
run. config.json is gitignored -- developers copy config.example.json and
customize it -- so it is absent in CI and on a fresh clone, and test_paper_engine
loads it at module scope, which aborts collection of the whole session with a
FileNotFoundError.

Provision config.json from the committed example before any test module is
collected (conftest.py imports ahead of the test modules beside it), mirroring
the documented local setup. Never clobber a real config.json a developer already
has; the file stays gitignored either way.
"""

import shutil
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_config = _ROOT / "config.json"
if not _config.exists():
    shutil.copy(_ROOT / "config.example.json", _config)
