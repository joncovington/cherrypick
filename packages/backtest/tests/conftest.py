import keyring
import keyring.errors
import pytest


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    """Autouse, as in every package: a test must never read or write the real home."""
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("BACKTEST_DATA_DIR", raising=False)
    monkeypatch.delenv("BACKTEST_CONFIG", raising=False)
    return home


@pytest.fixture(autouse=True)
def mem_keyring(monkeypatch):
    """Autouse: no test may reach the real keyring, where the operator's vendor key lives."""
    store: dict[tuple[str, str], str] = {}

    def get_password(service, entry):
        return store.get((service, entry))

    def set_password(service, entry, value):
        store[(service, entry)] = value

    def delete_password(service, entry):
        if (service, entry) not in store:
            raise keyring.errors.PasswordDeleteError("not found")
        del store[(service, entry)]

    monkeypatch.setattr(keyring, "get_password", get_password)
    monkeypatch.setattr(keyring, "set_password", set_password)
    monkeypatch.setattr(keyring, "delete_password", delete_password)
    return store
