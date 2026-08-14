"""Session-wide test setup: a managed home that is never real."""

import pytest


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    """Point `CHERRYPICK_HOME` at a temporary directory for every test in the suite.

    Autouse, and deliberately not something a test opts into — flies learned this on 2026-07-20,
    when three tests that skipped the opt-in fixture wrote into the real trading home mid-session
    and the day never settled. Opting in to isolation puts the real home one forgotten argument
    away. `CALENDARS_DB_PATH` and `CALENDARS_CONFIG` are cleared for the same reason: an operator's
    shell may carry them, and they resolve to real files.
    """
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("CALENDARS_DB_PATH", raising=False)
    monkeypatch.delenv("CALENDARS_CONFIG", raising=False)
    return home
