"""A live account per broker login (`live_accounts.<login>`, `live_loop --login <name>`).

What must hold:
- the main live account is byte-for-byte what it was: paths, keys, log, argv;
- another login's live account shares no file with main and reads no credential of main's;
- its config is its own entry -- caps, kill criteria, arm and symbol never inherited -- and it acts
  only with its own `enabled` (guard shown to fail by inheriting the caps);
- a margin account under the pattern-day-trader rule opens nothing it might not be able to stop.
"""

from __future__ import annotations

import json

import keyring
import pytest
from cherrypick.core.auth import CLIENT_SECRET, SHARED_SERVICE, CredentialStore
from cherrypick.core.auth import logins as _logins

from cherrypick.meic import credentials as _creds
from cherrypick.meic import db as _db
from cherrypick.meic import live_account, live_loop, paths, stream_request
from cherrypick.meic import session as _session

BASE = {
    "enable_live_trading": True,
    "account_deploy_limit_pct": 50,
    "live": {
        "symbol": "SPX",
        "arm": "pm-200",
        "gate0_confirmed": "someone, a date",
        "max_open_bp_dollars": 900,
        "daily_loss_halt_dollars": 200,
        "kill": {"written": "2026-10-24"},
        "disarm_time": "17:00",
        "entry_window_start": "14:00",
        "entry_window_end": "14:30",
    },
    "live_accounts": {
        "margin": {"enabled": False, "symbol": "XSP", "arm": "pm-200", "max_structures_per_day": 1},
        "_note": "not an entry",
        "Bad Name": {"symbol": "SPX"},
    },
}


@pytest.fixture
def restore():
    saved = (
        paths._live_login,
        _creds.store,
        _session._manager,
        _session.get_session,
        _session.reset_session,
        _db._DB_PATH,
    )
    yield
    (
        paths._live_login,
        _creds.store,
        _session._manager,
        _session.get_session,
        _session.reset_session,
        _db._DB_PATH,
    ) = saved


@pytest.fixture
def mem_keyring(monkeypatch):
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, e: store.get((s, e)))
    monkeypatch.setattr(keyring, "set_password", lambda s, e, v: store.__setitem__((s, e), v))
    return store


def test_main_is_exactly_as_before(restore):
    assert live_account.activate(None) == "main"
    assert paths.live_key() == "meic"
    assert paths.live_db_path() == paths.data_dir() / "meic_trades.db"
    assert paths.live_log_name() == "meic_live.log"
    assert live_loop.login_argv() == []
    assert stream_request.live_request_name() == "meic-live"
    assert live_account.config_view(BASE, None) is BASE


def test_another_login_shares_no_file_with_main(restore, mem_keyring):
    main_db, main_dir = paths.live_db_path(), paths.live_data_dir()
    live_account.activate("margin")
    assert paths.live_key() == "meic.margin"
    assert paths.live_data_dir() == main_dir / "margin" and paths.live_db_path() != main_db
    assert paths.live_log_name() == "meic_live.margin.log"
    assert live_loop.login_argv() == ["--login", "margin"]
    assert stream_request.live_request_name() == "meic-live.margin"
    # Every file kept beside the ledger follows it.
    for path in (
        live_loop._criticals_path(),
        live_loop._once_lock_path(),
        live_loop._orphans_path(),
        live_loop._hold_path(),
    ):
        assert str(path).startswith(str(paths.live_data_dir()))
    assert _db._DB_PATH == str(paths.live_db_path())


def test_another_login_never_reads_main_s_credentials(restore, mem_keyring):
    CredentialStore(SHARED_SERVICE).set_secret(CLIENT_SECRET, "main-secret")
    CredentialStore(SHARED_SERVICE).set_secret("account_number", "MAINACCT1")
    live_account.activate("margin")
    assert _creds.store.get_secret(CLIENT_SECRET) is None
    assert _creds.store.designated_account() is None
    assert _creds.store.service_name == _logins.login_service("margin")
    # Its designation is this module's entry in the login's own service.
    live_account.designation_store("margin").set_secret("account_number", "MARGIN0001")
    assert _creds.store.designated_account() == "MARGIN0001"
    assert ("cherrypick-broker.margin", "meic:account_number") in mem_keyring


def test_the_view_is_the_entry_and_nothing_that_sizes_is_inherited():
    view = live_account.config_view(BASE, "margin")
    live = view["live"]
    assert live["symbol"] == "XSP" and live["max_structures_per_day"] == 1
    for cap in ("max_open_bp_dollars", "daily_loss_halt_dollars", "kill", "gate0_confirmed"):
        assert cap not in live, cap
    # Timing is one clock for every account.
    assert (live["disarm_time"], live["entry_window_start"], live["entry_window_end"]) == (
        "17:00",
        "14:00",
        "14:30",
    )
    assert view["_live_account"] == {"login": "margin", "enabled": False}
    assert view["account_deploy_limit_pct"] == 50  # a percent of its own capacity
    assert BASE["live"]["symbol"] == "SPX"  # the base is untouched


def test_an_entry_s_own_deploy_cap_wins():
    cfg = json.loads(json.dumps(BASE))
    cfg["live_accounts"]["margin"]["account_deploy_limit_pct"] = 30
    assert live_account.config_view(cfg, "margin")["account_deploy_limit_pct"] == 30


def test_only_valid_entries_count_and_a_missing_one_refuses():
    assert list(live_account.entries(BASE)) == ["margin"]
    with pytest.raises(live_account.LiveAccountError):
        live_account.config_view(BASE, "ira2")
    with pytest.raises(live_account.LiveAccountError):
        live_account.config_view(BASE, "Bad Name")


def test_readiness_needs_the_entry_s_own_switch_and_designation(monkeypatch):
    monkeypatch.setattr(live_loop.paper, "live_arm_problem", lambda cfg: None)
    view = live_account.config_view(BASE, "margin")
    unmet = live_loop.readiness(view, designated=None)
    assert "live_accounts.margin.enabled is not true" in unmet
    assert any("gate0_confirmed" in u for u in unmet)
    assert any("--login margin --set" in u for u in unmet)
    view["_live_account"]["enabled"] = True
    view["live"]["gate0_confirmed"] = "someone, a date"
    assert live_loop.readiness(view, designated="X") == []


@pytest.mark.parametrize(
    "status,blocked",
    [
        ({"margin_or_cash": "Margin", "net_liquidating_value": 812.0, "day_trade_count": 0}, False),
        ({"margin_or_cash": "Margin", "net_liquidating_value": 812.0, "day_trade_count": 1}, False),
        ({"margin_or_cash": "Margin", "net_liquidating_value": 812.0, "day_trade_count": 2}, True),
        ({"margin_or_cash": "Margin", "net_liquidating_value": None, "day_trade_count": 2}, True),
        ({"margin_or_cash": "Margin", "net_liquidating_value": 30000.0, "day_trade_count": 3}, False),
        ({"margin_or_cash": "Margin", "is_pattern_day_trader": True, "day_trade_count": 5}, False),
        ({"margin_or_cash": "Cash", "net_liquidating_value": 812.0, "day_trade_count": 3}, False),
        ({"margin_or_cash": "Cash", "is_closing_only": True}, True),
        ({"margin_or_cash": "Cash", "is_frozen": True}, True),
    ],
)
def test_restriction_blockers(status, blocked):
    assert bool(live_loop.restriction_blockers(status, {})) is blocked


def test_the_pdt_reserve_is_configurable():
    status = {"margin_or_cash": "Margin", "net_liquidating_value": 812.0, "day_trade_count": 2}
    assert live_loop.restriction_blockers(status, {"pdt_reserve_day_trades": 1}) == []


def test_a_second_account_on_the_same_index_is_warned(restore, monkeypatch):
    cfg = json.loads(json.dumps(BASE))
    cfg["live_accounts"]["margin"]["enabled"] = True
    monkeypatch.setattr(live_loop.paper, "load_base_config", lambda: cfg)
    live_account.activate(None)
    assert live_loop.same_index_elsewhere(cfg) == ["margin:XSP"]
    paths.set_live_login("margin")
    assert live_loop.same_index_elsewhere(live_account.config_view(cfg, "margin")) == ["main:SPX"]
    verdict = live_loop.arm_verdict({"supervisor_fresh": True, "same_index_elsewhere": ["main:SPX"]})
    assert verdict["ready"] and any("one directional bet" in w for w in verdict["warnings"])
