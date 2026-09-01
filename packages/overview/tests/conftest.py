import pytest


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    """Autouse, and deliberately not something a test opts into -- the suite learned this the hard
    way when tests that skipped the opt-in fixture wrote into the real trading home mid-session."""
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("OVERVIEW_DATA_DIR", raising=False)
    return home
