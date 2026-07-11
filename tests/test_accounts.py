"""Per-module live-trading account selection (orchestrator.accounts).

Unit lane: list/set/clear with the broker `list_accounts` stubbed and the shared CredentialStore
replaced by an in-memory fake, so no real module checkout, broker, or keyring is touched. Asserts
selection resolves to the right FULL number, the write receives the full number, and only masked forms
ever surface (the full number never appears in any returned value).
"""

import json

import pytest

from cherrypick.orchestrator import accounts
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit

_ACCTS = [
    {"account_number": "5WU111114222", "nickname": "Main", "account_type": "Individual"},
    {"account_number": "5WU222228569", "nickname": None, "account_type": "Individual"},
]


class _FakeStore:
    """In-memory stand-in for cherrypick.core.auth.CredentialStore, keyed by service name."""

    _mem: dict = {}

    def __init__(self, service, legacy_service_names=()):
        self.service = service
        _FakeStore._mem.setdefault(service, {})

    def get_secret(self, key):
        return _FakeStore._mem[self.service].get(key)

    def set_secret(self, key, value):
        _FakeStore._mem[self.service][key] = value

    def delete_secret(self, key):
        _FakeStore._mem[self.service].pop(key, None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    _FakeStore._mem = {}
    (tmp_path / "meic").mkdir()
    (tmp_path / "meic" / "config.json").write_text(
        json.dumps({"enable_live_trading": False}), encoding="utf-8"
    )
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)
    monkeypatch.setattr(accounts, "CredentialStore", _FakeStore)
    monkeypatch.setattr(accounts, "_tt", lambda root, *argv: {"ok": True, "accounts": _ACCTS})
    cfg = {
        "modules": {
            "meic": {
                "enabled": True,
                "path": str(tmp_path / "meic"),
                "keyring_service": "meicagent-test",
                "paper": {"trade_schema": "meic_ic"},
            }
        }
    }
    return tmp_path, cfg


def test_list_accounts_masks_and_marks_none_designated(env):
    _, cfg = env
    out = accounts.list_accounts(cfg, "meic")
    assert out["ok"] is True
    assert [a["account"] for a in out["accounts"]] == ["****4222", "****8569"]
    assert all(a["designated"] is False for a in out["accounts"])
    assert out["designated"] is None
    assert out["live_enabled"] is False
    assert "111114222" not in json.dumps(out) and "222228569" not in json.dumps(out)


def test_set_by_last4_writes_full_number_returns_masked(env):
    _, cfg = env
    out = accounts.set_account(cfg, "meic", "8569")
    assert out["ok"] is True and out["designated"] == "****8569"
    # the FULL number is what the module will read from its keyring
    assert _FakeStore._mem["meicagent-test"]["account_number"] == "5WU222228569"
    # never leak the full number in the returned payload
    assert "222228569" not in json.dumps(out)


def test_set_by_index(env):
    _, cfg = env
    out = accounts.set_account(cfg, "meic", "1")
    assert out["ok"] is True and out["designated"] == "****4222"
    assert _FakeStore._mem["meicagent-test"]["account_number"] == "5WU111114222"


def test_set_then_list_marks_designated(env):
    _, cfg = env
    accounts.set_account(cfg, "meic", "2")
    out = accounts.list_accounts(cfg, "meic")
    assert out["designated"] == "****8569"
    assert [a["designated"] for a in out["accounts"]] == [False, True]


def test_clear_unsets(env):
    _, cfg = env
    accounts.set_account(cfg, "meic", "1")
    assert accounts.clear_account(cfg, "meic")["ok"] is True
    assert "account_number" not in _FakeStore._mem["meicagent-test"]


def test_unresolvable_selector_errors(env):
    _, cfg = env
    assert accounts.set_account(cfg, "meic", "9999")["ok"] is False
    assert accounts.set_account(cfg, "meic", "99")["ok"] is False  # index out of range


def test_missing_keyring_service_degrades_cleanly(env):
    _, cfg = env
    cfg["modules"]["meic"].pop("keyring_service")
    out = accounts.list_accounts(cfg, "meic")
    assert out["ok"] is False and "keyring_service" in out["error"]
