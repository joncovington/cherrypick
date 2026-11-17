"""Session-wide test setup: a managed home that is never real (autouse, never opt-in)."""

import pytest


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    for var in ("AUTOMATIONS_DB_PATH", "ENSEMBLE_DB_PATH", "AUTOMATIONS_CONFIG", "ENSEMBLE_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture(autouse=True)
def no_vendor_licence(monkeypatch):
    """No test asks the real backtest package about the vendor licence: it reads as not allowing new
    work unless a test says otherwise."""
    from cherrypick.automations import licence

    monkeypatch.setattr(licence, "allows_new_work", lambda run=None: False)
