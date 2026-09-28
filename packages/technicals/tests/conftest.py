import pytest


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    """Autouse, as in every package: a test must never read or write the real home."""
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("TECHNICALS_DATA_DIR", raising=False)
    return home
