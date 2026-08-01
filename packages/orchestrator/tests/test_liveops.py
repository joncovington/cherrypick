"""Live-ops readiness view (orchestrator.liveops) + its serve-only hub card.

Unit lane: files + keyring only — these tests never touch a broker, and the keyring is avoided
entirely by configuring modules without a keyring_service.
"""

import json

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import liveops

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(cfgmod, "STATE_DIR", state)
    monkeypatch.setattr(cfgmod, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))  # home config dir = tmp_path/config
    for name in ("meic", "earn"):
        (tmp_path / name).mkdir()
    # Known-module defaults now resolve a keyring service for "meic" — stub the keyring
    # surfaces so unit tests never touch the real credential store.
    monkeypatch.setattr(liveops.accounts, "keyring_store", lambda cfg, name: None)
    monkeypatch.setattr(
        liveops.accounts, "onboarding_status", lambda cfg: {"ok": True, "shared": {}, "modules": []}
    )
    cfg = {
        "modules": {
            "meic": {"enabled": True, "path": str(tmp_path / "meic")},
            "earn": {"enabled": True, "path": str(tmp_path / "earn")},
        }
    }
    return tmp_path, cfg


def test_kill_switches_read_per_module(env):
    tmp_path, cfg = env
    (tmp_path / "meic" / "config.json").write_text(json.dumps({"enable_live_trading": True}))
    (tmp_path / "earn" / "config.json").write_text(json.dumps({"enable_live_trading": False}))
    out = liveops.run(cfg)
    assert out["ok"] is True
    by_name = {m["module"]: m for m in out["modules"]}
    assert by_name["meic"]["live_enabled"] is True
    assert by_name["earn"]["live_enabled"] is False
    # No keyring_service configured -> no designated account, and no keyring was touched.
    assert by_name["meic"]["designated"] is None


def test_missing_config_is_unknown_not_off(env):
    tmp_path, cfg = env
    out = liveops.run(cfg)
    by_name = {m["module"]: m for m in out["modules"]}
    # A reassuring default of 'off' would hide a module whose config we simply couldn't read.
    assert by_name["meic"]["live_enabled"] is None
    assert by_name["meic"]["config_source"] is None


def test_home_config_wins_over_repo_config(env):
    tmp_path, cfg = env
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "earn.json").write_text(json.dumps({"enable_live_trading": True}))
    (tmp_path / "earn" / "config.json").write_text(json.dumps({"enable_live_trading": False}))
    out = liveops.run(cfg)
    by_name = {m["module"]: m for m in out["modules"]}
    # The migrated home config is what the module actually reads, so the view must read it too.
    assert by_name["earn"]["live_enabled"] is True
    assert "earn.json" in by_name["earn"]["config_source"]


def test_halt_flag_presence_is_the_signal(env):
    tmp_path, cfg = env
    out = liveops.run(cfg)
    assert out["halt_flag"]["present"] is False
    assert liveops.HALT_FLAG_NAME in out["halt_flag"]["path"]
    liveops.halt_flag_path().write_text("")  # touch — contents are deliberately ignored
    assert liveops.run(cfg)["halt_flag"]["present"] is True


def test_liveops_card_is_serve_only():
    from cherrypick.orchestrator import dashboard

    card = dashboard._liveops_card_html()
    # The card composes both halves of live ops: kill switches + the paper↔live/live-book check.
    assert "data-cp-liveops" in card and "/api/liveops" in card
    assert "data-cp-reconcile" in card and "/api/reconcile" in card


def test_kill_switches_read_flies_nested_convention(env):
    """flies gates live trading via nested `live.enabled`, not the top-level `enable_live_trading`
    meic/earnings use. Before config.live_trading_enabled checked both, this view -- and the halt
    banner built on it -- reported flies as PAPER ONLY even while its live loop was armed."""
    tmp_path, cfg = env
    (tmp_path / "earn" / "config.json").write_text(
        json.dumps({"live": {"enabled": True, "gate0_confirmed": "jon 2026-07-30"}})
    )
    out = liveops.run(cfg)
    by_name = {m["module"]: m["live_enabled"] for m in out["modules"]}
    assert by_name["earn"] is True


def test_halt_status_flags_any_module_live_enabled(env):
    tmp_path, cfg = env
    (tmp_path / "meic" / "config.json").write_text(json.dumps({"enable_live_trading": True}))
    (tmp_path / "earn" / "config.json").write_text(json.dumps({"enable_live_trading": False}))
    out = liveops.halt_status(cfg)
    assert out["ok"] is True and out["present"] is False and out["any_live_enabled"] is True
    by_name = {m["module"]: m["live_enabled"] for m in out["modules"]}
    assert by_name == {"meic": True, "earn": False}


def test_halt_status_no_module_live_enabled(env):
    tmp_path, cfg = env
    (tmp_path / "meic" / "config.json").write_text(json.dumps({"enable_live_trading": False}))
    (tmp_path / "earn" / "config.json").write_text(json.dumps({"enable_live_trading": False}))
    assert liveops.halt_status(cfg)["any_live_enabled"] is False


def test_set_halt_creates_and_clears_the_flag(env):
    assert liveops.halt_flag_path().exists() is False
    out = liveops.set_halt(True)
    assert out["ok"] is True and out["present"] is True
    assert liveops.halt_flag_path().exists() is True
    out = liveops.set_halt(False)
    assert out["ok"] is True and out["present"] is False
    assert liveops.halt_flag_path().exists() is False


def test_set_halt_is_idempotent(env):
    liveops.set_halt(True)
    liveops.set_halt(True)  # touching an already-present flag doesn't error
    assert liveops.halt_flag_path().exists() is True
    liveops.set_halt(False)
    liveops.set_halt(False)  # deleting an already-absent flag doesn't error
    assert liveops.halt_flag_path().exists() is False
