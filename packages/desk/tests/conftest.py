"""Shared fixtures. Every test runs against a temp CHERRYPICK_HOME, an in-memory keyring, a broker
session that refuses to open, and a terminal that is not a TTY — so nothing here can reach the real
OS credential store, a broker or a real PIN prompt unless a test says so explicitly."""

import json

import pytest

from cherrypick.desk import config as cfgmod
from cherrypick.desk import keystore, pin
from cherrypick.desk import session as desk_session

REAL_PBKDF2_ITERATIONS = pin.PBKDF2_ITERATIONS
ACCOUNT = "5WT01234"


class FakeKeyring:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}
        self.fail = False

    def _check(self):
        if self.fail:
            raise RuntimeError("keyring backend down")

    def set_password(self, service, key, value):
        self._check()
        self.store[(service, key)] = value

    def get_password(self, service, key):
        self._check()
        return self.store.get((service, key))

    def delete_password(self, service, key):
        self._check()
        self.store.pop((service, key), None)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(cfgmod.EXPERIMENTAL_ENV, raising=False)
    monkeypatch.delenv("CHERRYPICK_DESK_PIN", raising=False)
    # Fast PBKDF2 for tests; test_pin checks the real count separately.
    monkeypatch.setattr(pin, "PBKDF2_ITERATIONS", 1_000)
    monkeypatch.setattr(pin, "_isatty", lambda: False)

    def _no_broker(cfg):
        raise RuntimeError("tests must not open a broker session")

    monkeypatch.setattr(desk_session, "get_session", _no_broker)


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setattr(keystore, "_keyring", lambda: kr)
    return kr


@pytest.fixture
def tty(monkeypatch):
    """Script PIN prompts: `tty.answers = [...]` is consumed in order by getpass."""

    class _Tty:
        answers: list[str] = []
        prompts: list[str] = []

    t = _Tty()
    t.answers = []
    t.prompts = []
    monkeypatch.setattr(pin, "_isatty", lambda: True)

    def _getpass(label):
        t.prompts.append(label)
        if not t.answers:
            raise AssertionError(f"unexpected PIN prompt: {label!r}")
        return t.answers.pop(0)

    monkeypatch.setattr(pin, "_getpass", _getpass)
    return t


def write_config(**over) -> dict:
    cfg = {"enabled": True, "allowed_accounts": ["1234"], "allowed_underlyings": ["XYZ", "BKNG"], **over}
    path = cfgmod.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return cfg


@pytest.fixture
def desk_on(monkeypatch):
    """Both gates open: an enabled desk.json and CHERRYPICK_DESK_EXPERIMENTAL=1."""
    write_config()
    monkeypatch.setenv(cfgmod.EXPERIMENTAL_ENV, "1")


PIN = "correct-horse-pin"


@pytest.fixture
def pin_set():
    pin.set_pin(PIN)
    return PIN
