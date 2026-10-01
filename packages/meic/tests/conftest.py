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

# ---- before ANY package import: the home is a throwaway, for the whole session -------------------
# Several modules resolve paths ONCE, at import (`db_paper.DB_PATH`, `db.DB_PATH` -- the LIVE ledger --,
# the paper loop's lock and pid files). A function-scoped fixture runs too late to move those: they
# already point at the real home. Measured 2026-09-23: with a per-test fixture in place, a plain run
# still inserted 5 frozen-clock `loop_iterations` rows into the REAL earnings ledger every time. So the
# master override is set here, when pytest imports this conftest and before it imports a single
# test module -- which catches every import-time path, including ones nobody has listed.
import os as _os
import shutil
import tempfile as _tempfile
from pathlib import Path

import pytest

_os.environ["CHERRYPICK_HOME"] = _tempfile.mkdtemp(prefix="cherrypick-test-home-")
for _leaked in (
    "MEIC_DATA_DIR",
    "MEIC_LOGS_DIR",
    "MEIC_CONFIG",
    "MARKETDATA_DATA_DIR",
    "CHERRYPICK_MODULES_HOME",
):
    _os.environ.pop(_leaked, None)
_ROOT = Path(__file__).resolve().parent.parent
_config = _ROOT / "config.json"
if not _config.exists():
    shutil.copy(_ROOT / "config.example.json", _config)


# --------------------------------------------------------------------------- the home is never the real one
@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Every test runs against a throwaway cherrypick home.

    Measured 2026-09-23 by running this suite with `CHERRYPICK_HOME` pointed at an empty directory
    and listing what appeared. Without this fixture a plain `pytest` here writes into the REAL home:
    meic touched its paper ledger, `state/meic.heartbeat` (which the watchdog reads -- a test run
    could make a dead loop look alive) and `state/stream_requests/meic.json` (which the streamer
    reads); earnings inserted `loop_iterations` rows into the real paper ledger at a frozen test
    clock, 221 of them by the time anyone looked. The streamer package's own tests stopped the
    production streamer the same way.

    The master override moves the whole tree; the narrow per-scope overrides are cleared because
    a leaked one wins over it.
    """
    home = tmp_path_factory.mktemp("cherrypick-home")
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    for leaked in ("MEIC_DATA_DIR", "MEIC_LOGS_DIR", "MARKETDATA_DATA_DIR", "CHERRYPICK_MODULES_HOME"):
        monkeypatch.delenv(leaked, raising=False)
    return home


@pytest.fixture(autouse=True)
def _today_is_not_a_quarter_end(monkeypatch):
    """`tt execute_trade --live` refuses opening orders on a quarter end by the real clock, so the
    live-submit tests went red on 2026-09-30, the day that guard landed. Pinned off here; the guard's
    own tests pin it on."""
    from cherrypick.meic import tt

    monkeypatch.setattr(tt, "_quarter_end_today", lambda: False)
