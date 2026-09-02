"""Per-module live-trading account selection (orchestrator.accounts).

Unit lane: list/set/clear with the broker `list_accounts` stubbed and the shared CredentialStore
replaced by an in-memory fake, so no real module checkout, broker, or keyring is touched. Asserts
selection resolves to the right FULL number, the write receives the full number, and only masked forms
ever surface (the full number never appears in any returned value).
"""

import json
from pathlib import Path

import pytest

from cherrypick.orchestrator import accounts
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit

_ACCTS = [
    {"account_number": "5WU111114222", "nickname": "Main", "account_type": "Individual"},
    {"account_number": "5WU222228569", "nickname": None, "account_type": "Individual"},
]


class _FakeStore:
    """In-memory stand-in for cherrypick.core.auth.CredentialStore, keyed by service name.

    Own-service lookups only — legacy_service_names is recorded but not consulted by get_secret.
    Fine for the list/set/clear tests below, which never rely on a fallback; see _FakeStoreWithFallback
    for tests that specifically exercise the shared-service fallback chain."""

    _mem: dict = {}

    def __init__(self, service, legacy_service_names=()):
        self.service = service
        self.legacy_service_names = tuple(legacy_service_names)
        _FakeStore._mem.setdefault(service, {})

    def get_secret(self, key):
        return _FakeStore._mem[self.service].get(key)

    def set_secret(self, key, value):
        _FakeStore._mem[self.service][key] = value

    def delete_secret(self, key):
        _FakeStore._mem[self.service].pop(key, None)


class _FakeStoreWithFallback:
    """Like _FakeStore, but get_secret actually walks legacy_service_names on a miss — the real
    CredentialStore's read-through-legacy behavior, needed to test keyring_store's fallback chain."""

    _mem: dict = {}

    def __init__(self, service, legacy_service_names=()):
        self.service = service
        self.legacy_service_names = tuple(legacy_service_names)
        _FakeStoreWithFallback._mem.setdefault(service, {})

    def get_secret(self, key):
        value = _FakeStoreWithFallback._mem[self.service].get(key)
        if value is not None:
            return value
        for legacy in self.legacy_service_names:
            value = _FakeStoreWithFallback._mem.get(legacy, {}).get(key)
            if value is not None:
                return value
        return None

    def set_secret(self, key, value):
        _FakeStoreWithFallback._mem[self.service][key] = value

    def delete_secret(self, key):
        _FakeStoreWithFallback._mem[self.service].pop(key, None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    _FakeStore._mem = {}
    (tmp_path / "meic").mkdir()
    (tmp_path / "meic" / "config.json").write_text(
        json.dumps({"enable_live_trading": False}), encoding="utf-8"
    )
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)
    # _live_enabled now checks the home config first (~/.cherrypick/config/<module>.json) — sandbox
    # the home so this never reads (or races) the real user's config.
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(accounts, "CredentialStore", _FakeStore)
    monkeypatch.setattr(accounts, "_tt", lambda root, *argv, tool=None: {"ok": True, "accounts": _ACCTS})
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
    # Popping the key no longer disables the surface (known-module defaults fill it in);
    # the explicit null is the deliberate opt-out.
    cfg["modules"]["meic"]["keyring_service"] = None
    out = accounts.list_accounts(cfg, "meic")
    assert out["ok"] is False and "keyring_service" in out["error"]


@pytest.fixture
def fallback_env(tmp_path, monkeypatch):
    """Same shape as `env`, but wired to _FakeStoreWithFallback so the shared-service fallback
    chain is actually exercised (get_secret walks legacy_service_names on a miss)."""
    from cherrypick.core.auth import SHARED_SERVICE

    _FakeStoreWithFallback._mem = {}
    (tmp_path / "flies").mkdir()
    (tmp_path / "flies" / "config.json").write_text(
        json.dumps({"enable_live_trading": False}), encoding="utf-8"
    )
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))  # sandbox _live_enabled's home read
    monkeypatch.setattr(accounts, "CredentialStore", _FakeStoreWithFallback)
    monkeypatch.setattr(accounts, "_tt", lambda root, *argv, tool=None: {"ok": True, "accounts": _ACCTS})
    cfg = {
        "modules": {
            "flies": {
                "enabled": True,
                "path": str(tmp_path / "flies"),
                "keyring_service": "fliesagent-test",
                "paper": {"trade_schema": "fly_book"},
            }
        }
    }
    return cfg, SHARED_SERVICE


def test_keyring_store_falls_back_to_shared_service(fallback_env):
    """The bug this guards: liveops/reconcile/`cherrypick account` all read the module's live
    account through `keyring_store`, but every module's OWN credentials.py resolves through the
    shared login as a hardcoded fallback. Before this fallback was added here too, a module with no
    account_number of its own (the common case — only the shared login is set up) showed "no account
    designated" even though it would in fact trade the shared account once armed, and `reconcile`
    could raise a false DRIFT alert against that very account."""
    cfg, shared = fallback_env
    _FakeStoreWithFallback._mem[shared] = {"account_number": "5WU222228569"}
    store = accounts.keyring_store(cfg, "flies")
    assert accounts._designated_number(store) == "5WU222228569"


def test_keyring_store_own_designation_wins_over_shared(fallback_env):
    cfg, shared = fallback_env
    _FakeStoreWithFallback._mem[shared] = {"account_number": "5WU222228569"}
    _FakeStoreWithFallback._mem["fliesagent-test"] = {"account_number": "5WU111114222"}
    store = accounts.keyring_store(cfg, "flies")
    assert accounts._designated_number(store) == "5WU111114222"


def test_list_accounts_marks_shared_inherited_designation(fallback_env):
    cfg, shared = fallback_env
    _FakeStoreWithFallback._mem[shared] = {"account_number": "5WU222228569"}
    out = accounts.list_accounts(cfg, "flies")
    assert out["designated"] == "****8569"
    assert [a["designated"] for a in out["accounts"]] == [False, True]


def test_reconcile_designated_numbers_includes_shared_fallback(fallback_env):
    from cherrypick.orchestrator import reconcile

    cfg, shared = fallback_env
    _FakeStoreWithFallback._mem[shared] = {"account_number": "5WU222228569"}
    assert reconcile._designated_numbers(cfg) == {"5WU222228569"}


def test_list_accounts_reads_flies_nested_live_convention(fallback_env):
    """flies gates live trading via nested `live.enabled`, not the top-level `enable_live_trading`
    every other module uses. Before `_live_enabled` checked both conventions (via
    config.live_trading_enabled), `cherrypick account --module flies` always reported live_enabled
    as unknown/False regardless of the module's real armed state."""
    cfg, _ = fallback_env
    (Path(cfg["modules"]["flies"]["path"]) / "config.json").write_text(
        json.dumps({"live": {"enabled": True, "gate0_confirmed": "jon 2026-07-30"}}), encoding="utf-8"
    )
    out = accounts.list_accounts(cfg, "flies")
    assert out["live_enabled"] is True
