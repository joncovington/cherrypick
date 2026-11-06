"""Small correctness guards: refuse a malformed order or quote, protect a recorded number.

One test per guard. Each was run against the code before the guard and failed there; the failure
each one showed is in the pull request that added it.
"""

import argparse
import json
from datetime import date, datetime, timezone

import pytest
from cherrypick.core import clock as _clock

from cherrypick.earnings import db, db_paper, paper_loop, scanner, sizing, tt
from cherrypick.earnings.strategies import iron_condor, iron_fly

EXP = date(2026, 11, 20)


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


# --------------------------------------------------------------------------- 1. order structure


def _entry(option_type, strike, mid):
    return {
        "symbol": f"X{option_type}{int(strike)}",
        "option_type": option_type,
        "strike_price": float(strike),
        "mid": mid,
    }


def _stub_builder(monkeypatch, entries, probe_mid=2.0):
    monkeypatch.setattr(
        scanner, "fetch_quote_and_expirations", lambda s: {"ok": True, "price": 100.0, "expirations": [EXP]}
    )
    monkeypatch.setattr(scanner, "select_front_expiration", lambda *a, **k: (EXP, None))
    monkeypatch.setattr(scanner, "call_tt", lambda args: {"ok": True, "chain": {str(EXP): entries}})
    monkeypatch.setattr(scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(
        scanner,
        "fetch_front_back_atm_entries",
        lambda *a, **k: {
            "ok": True,
            "front_call": {"mid": probe_mid},
            "front_put": {"mid": probe_mid},
        },
    )


_CONFIG = {"strategies": {"iron_fly": {}, "iron_condor": {}}}


def _fly(**_):
    return iron_fly.fetch_iron_fly_order("XYZ", EXP, "After market close", _CONFIG)


def _condor():
    return iron_condor.fetch_iron_condor_order("XYZ", EXP, "After market close", _CONFIG)


def test_iron_fly_refuses_a_wing_inside_its_short_strike(monkeypatch):
    # An irregular grid: the only call other than the 100 short is BELOW it, the only put ABOVE it.
    entries = [
        _entry("C", 100, 3.0),
        _entry("P", 100, 3.0),
        _entry("C", 90, 0.5),
        _entry("P", 110, 0.5),
    ]
    _stub_builder(monkeypatch, entries)
    result = _fly()
    assert result["ok"] is False
    assert "wing" in result["error"]


def test_iron_condor_refuses_a_wing_inside_its_short_strike(monkeypatch):
    entries = [
        _entry("C", 103, 1.0),
        _entry("P", 97, 1.0),
        _entry("C", 90, 0.5),
        _entry("P", 110, 0.5),
    ]
    _stub_builder(monkeypatch, entries)
    result = _condor()
    assert result["ok"] is False
    assert "wing" in result["error"]


def test_iron_fly_refuses_a_net_debit(monkeypatch):
    # Wings are properly outside but cost more than the straddle brings in.
    entries = [
        _entry("C", 100, 3.0),
        _entry("P", 100, 3.0),
        _entry("C", 120, 8.0),
        _entry("P", 80, 8.0),
    ]
    _stub_builder(monkeypatch, entries)
    result = _fly()
    assert result["ok"] is False
    assert "credit" in result["error"]


def test_iron_condor_refuses_a_net_debit(monkeypatch):
    entries = [
        _entry("C", 103, 1.0),
        _entry("P", 97, 1.0),
        _entry("C", 115, 3.0),
        _entry("P", 85, 3.0),
    ]
    _stub_builder(monkeypatch, entries)
    result = _condor()
    assert result["ok"] is False
    assert "credit" in result["error"]


def test_a_well_formed_iron_fly_and_condor_still_build(monkeypatch):
    fly_entries = [
        _entry("C", 100, 3.0),
        _entry("P", 100, 3.0),
        _entry("C", 118, 0.5),
        _entry("P", 82, 0.5),
    ]
    _stub_builder(monkeypatch, fly_entries)
    fly = _fly()
    assert fly["ok"] is True and fly["credit"] == 5.0

    condor_entries = [
        _entry("C", 103, 1.0),
        _entry("P", 97, 1.0),
        _entry("C", 109, 0.2),
        _entry("P", 91, 0.2),
    ]
    _stub_builder(monkeypatch, condor_entries)
    assert _condor()["ok"] is True


# --------------------------------------------------------------------------- 2. REST quotes

_TRADE = {
    "symbol": "XYZ",
    "expiration": "2026-11-20",
    "legs_json": json.dumps([{"symbol": "XYZ_C", "action": "Sell to Open", "quantity": 1}]),
}


def _rest(monkeypatch, bid, ask, mid="auto"):
    if mid == "auto":
        mid = None if bid is None or ask is None else (bid + ask) / 2
    monkeypatch.setattr(scanner, "fetch_quote_and_expirations", lambda s: {"ok": True, "price": 100.0})
    monkeypatch.setattr(
        scanner,
        "fetch_quotes_by_symbol",
        lambda *a, **k: {"XYZ_C": {"bid": bid, "ask": ask, "mid": mid, "iv": None, "delta": None}},
    )
    return paper_loop._rest_snapshot(_TRADE)


@pytest.mark.parametrize(
    "bid, ask",
    [(2.0, 1.0), (0.0, 0.0), (-0.1, 0.5)],
    ids=["crossed", "zero_zero", "negative_bid"],
)
def test_rest_snapshot_refuses_a_crossed_or_empty_quote(monkeypatch, bid, ask):
    snap = _rest(monkeypatch, bid, ask)
    assert snap["ok"] is False
    assert snap["reason"] == "unusable_leg_quotes"
    assert "quotes" not in snap


def test_rest_snapshot_still_prices_a_clean_quote(monkeypatch):
    snap = _rest(monkeypatch, 1.0, 1.2)
    assert snap["ok"] is True
    assert snap["quotes"]["XYZ_C"]["mid"] == pytest.approx(1.1)


def test_a_quote_without_a_usable_book_has_no_mid_never_zero():
    assert tt._quote_fields(2.0, 1.0) == {"bid": None, "ask": None, "mid": None}
    assert tt._quote_fields(0.0, 0.0) == {"bid": None, "ask": None, "mid": None}
    assert tt._quote_fields(None, 1.0) == {"bid": None, "ask": 1.0, "mid": None}
    assert tt._quote_fields(1.0, 1.2) == {"bid": 1.0, "ask": 1.2, "mid": 1.1}


# --------------------------------------------------------------------------- 3. second close


def _closed_twice(mod, tmp_path, monkeypatch, name):
    monkeypatch.setattr(mod, "DB_PATH", tmp_path / name)
    mod.cmd_init_db(_ns())
    trade = {
        "order_id": "O1",
        "symbol": "XYZ",
        "strategy": "iron_fly",
        "expiration": "2026-11-20",
        "entry_credit": 3.0,
        "legs_json": "[]",
    }
    mod.cmd_save_trade(_ns(data=json.dumps(trade)))
    first = mod.cmd_save_close(_ns(data=json.dumps({"order_id": "O1", "exit_debit": 1.0, "pnl": 200.0})))
    second = mod.cmd_save_close(_ns(data=json.dumps({"order_id": "O1", "exit_debit": 9.0, "pnl": -600.0})))
    conn = mod._conn()
    try:
        row = conn.execute("SELECT exit_debit, pnl FROM trades WHERE order_id = 'O1'").fetchone()
    finally:
        conn.close()
    return first, second, row


@pytest.mark.parametrize("mod, name", [(db_paper, "paper.db"), (db, "live.db")], ids=["paper", "live"])
def test_a_second_close_is_refused_and_overwrites_nothing(mod, name, tmp_path, monkeypatch):
    first, second, row = _closed_twice(mod, tmp_path, monkeypatch, name)
    assert first["ok"] is True
    assert second["ok"] is False
    assert "already closed" in second["error"]
    assert (row["exit_debit"], row["pnl"]) == (1.0, 200.0)


# --------------------------------------------------------------------------- 4. paper sizing

_ORDER = {"strategy": "atm_calendar", "debit": 3.12}
_STRAT = {"max_risk_per_trade_pct": 0.05}


def test_paper_sizing_uses_paper_capital_when_the_live_flag_is_on():
    config = {
        "enable_live_trading": True,
        "available_capital_paper_mode": 100000,
        "risk_pct_multiplier": 1.0,
        "max_contracts_per_leg": 20,
    }
    result = sizing.compute_position_size(_ORDER, _STRAT, config)
    assert result["ok"] is True
    assert result["risk_budget"] == pytest.approx(100000 * 0.05)


def test_live_sizing_is_still_available_to_a_caller_that_asks_for_it():
    config = {
        "enable_live_trading": True,
        "_live_nlv": 50000,
        "available_capital_paper_mode": 999999,
        "max_contracts_per_leg": 20,
    }
    result = sizing.compute_position_size(_ORDER, _STRAT, config, live=True)
    assert result["risk_budget"] == pytest.approx(50000 * 0.05)


# --------------------------------------------------------------------------- 5. ET session dates


def test_session_span_counts_sessions_in_et_not_host_local_time(monkeypatch):
    """A host ten hours ahead of ET sees these two ET-morning timestamps on different local days."""

    class _AheadHost(date):
        @classmethod
        def fromtimestamp(cls, ts):
            return datetime.fromtimestamp(float(ts) + 10 * 3600, timezone.utc).date()

    monkeypatch.setattr(db_paper, "_date", _AheadHost)
    opened = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc).timestamp()  # 08:00 ET Monday
    until = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc).timestamp()  # 10:00 ET Monday
    assert db_paper.session_span(opened, until) == 0


def test_the_scan_and_expiration_dates_follow_the_et_clock(monkeypatch):
    # 23:30 ET on Tuesday 3 Nov is already Wednesday on a host east of ET, and on UTC.
    fixed = datetime(2026, 11, 3, 23, 30, tzinfo=_clock.ET)
    monkeypatch.setattr(_clock, "now_et", lambda: fixed)
    assert scanner.et_today() == date(2026, 11, 3)

    monkeypatch.setattr(
        scanner, "fetch_quote_and_expirations", lambda s: {"ok": True, "price": 100.0, "expirations": [EXP]}
    )
    monkeypatch.setattr(scanner, "select_front_expiration", lambda *a, **k: (EXP, None))
    monkeypatch.setattr(scanner, "select_back_expiration", lambda *a, **k: date(2026, 12, 18))
    leg = {"mid": 3.0, "iv": 0.5, "delta": 0.5}
    monkeypatch.setattr(
        scanner,
        "fetch_front_back_atm_entries",
        lambda *a, **k: {"ok": True, "front_call": leg, "front_put": leg, "back_call": leg},
    )
    monkeypatch.setattr(
        scanner,
        "compute_expected_move_and_term_structure",
        lambda *a, **k: {"term_structure": 0.0, "expected_move_dollars": 5.0},
    )
    monkeypatch.setattr(scanner, "fetch_liquidity_criteria", lambda *a, **k: {})
    monkeypatch.setattr(scanner, "compute_winrate", lambda *a, **k: {"ok": False})
    got = iron_fly.fetch_price_and_term_structure("XYZ", EXP, "After market close", {"strategies": {}})
    assert got["criteria"]["front_expiration_days"] == (EXP - date(2026, 11, 3)).days
