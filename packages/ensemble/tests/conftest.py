"""Session-wide test setup: a managed home that is never real (autouse, never opt-in)."""

import pytest


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("ENSEMBLE_DB_PATH", raising=False)
    monkeypatch.delenv("ENSEMBLE_CONFIG", raising=False)
    return home
