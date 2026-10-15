"""expected_move_butterfly: the skew-read side, the symmetric strikes, the hold-to-expiry exit, its
max loss, and its dated start in strat_test."""

import datetime as dt
import json
import types
from datetime import date, datetime
from pathlib import Path

import pytest

from cherrypick.earnings import management, rank_strategies, scanner, sizing
from cherrypick.earnings import strat_test_harness as harness
from cherrypick.earnings.strategies import expected_move_butterfly as emb

CUTS = {"rr25_put_rich_max": -0.116, "rr25_call_rich_min": -0.055}


# --------------------------------------------------------------------------- the side


def test_rr25_is_the_risk_reversal_over_atm_iv():
    assert emb.rr25(0.50, 0.60, 0.55) == pytest.approx(-0.1 / 0.55)
    assert emb.rr25(None, 0.60, 0.55) is None
    assert emb.rr25(0.50, 0.60, 0.0) is None


def test_a_rich_put_wing_buys_the_call_fly_and_a_rich_call_wing_the_put_fly():
    """Contrarian to the published smirk studies, as the history found it."""
    assert emb.side_for(-0.20, CUTS) == "call"
    assert emb.side_for(0.02, CUTS) == "put"
    assert emb.side_for(-0.08, CUTS) is None
    assert emb.side_for(None, CUTS) is None


def _example_block() -> dict:
    """The block the repo ships, never a developer's deployed config."""
    path = Path(__file__).resolve().parents[1] / "config" / "config.example.json"
    return json.loads(path.read_text(encoding="utf-8"))["strategies"]["expected_move_butterfly"]


def test_the_shipped_cuts_are_the_studys():
    block = _example_block()
    assert {k: block[k] for k in CUTS} == CUTS


def test_a_neutral_or_missing_read_is_a_hard_fail():
    config = _example_block()
    base = {"price": 100, "expected_move_pct": 0.05, "front_expiration_days": 3, "chain_complete": True}
    neutral = emb.apply_tiering({**base, "rr25": -0.08}, config)["reject_reasons"]
    missing = emb.apply_tiering(base, config)["reject_reasons"]
    called = emb.apply_tiering({**base, "rr25": -0.20}, config)["reject_reasons"]
    assert "direction_signal_neutral" in neutral
    assert "rr25_unverified" in missing
    assert "direction_signal_neutral" not in called and "rr25_unverified" not in called


def test_the_criteria_never_rank_it_as_a_symbols_best():
    """No term_structure, no skew_abs: the agent's single-best path cannot pick a paper-only book."""
    assert scanner.compute_composite_score({"rr25": -0.2, "iv_rv_ratio": 1.5}) is None


# --------------------------------------------------------------------------- the strikes


def test_wings_are_symmetric_listed_strikes_or_refused():
    strikes = [95.0, 96.0, 97.0, 97.5, 98.0, 99.0, 100.0, 101.0, 102.0, 103.0, 104.0]
    assert emb.symmetric_legs(strikes, 96.31, 3.51, 2.98) == (97.0, 100.0, 103.0)
    # 97.5 has no 102.5 mirror, so the exact 2.5 target is never built; 2 and 3 tie, narrower wins.
    assert emb.symmetric_legs(strikes, 96.31, 3.51, 2.5) == (98.0, 100.0, 102.0)
    assert emb.symmetric_legs([100.0, 105.0], 96.31, 3.51, 2.98) is None


# SCHW 2026-10-13, the trade that started this: spot 96.31, EM ~3.51, the Oct 16 call mids.
CALL_MIDS = {
    95.0: 2.95, 96.0: 2.19, 97.0: 1.66, 97.5: 1.485, 98.0: 1.28, 99.0: 0.95, 100.0: 0.70,
    101.0: 0.475, 102.0: 0.35, 103.0: 0.255, 104.0: 0.155, 105.0: 0.105,
}  # fmt: skip


def _stub_broker(monkeypatch, *, call_iv=0.50, put_iv=0.60):
    exp = date(2026, 10, 16)
    monkeypatch.setattr(
        scanner, "fetch_quote_and_expirations", lambda s: {"ok": True, "price": 96.31, "expirations": [exp]}
    )
    monkeypatch.setattr(
        scanner,
        "fetch_front_back_atm_entries",
        lambda *a: {
            "ok": True,
            "front_call": {"mid": 2.20, "iv": 0.55},
            "front_put": {"mid": 1.93, "iv": 0.55},
            "back_call": {"mid": 2.20, "iv": 0.55},
        },
    )
    monkeypatch.setattr(
        scanner,
        "select_side",
        lambda *a: {"ok": True, "call_iv": call_iv, "put_iv": put_iv, "skew": call_iv - put_iv},
    )
    entries = [
        {"symbol": f"SCHW  261016C{int(k * 1000):08d}", "strike_price": k, "option_type": "C", "mid": m}
        for k, m in CALL_MIDS.items()
    ]
    monkeypatch.setattr(scanner, "call_tt", lambda args: {"ok": True, "chain": {str(exp): entries}})


def test_the_order_reproduces_the_schw_example(monkeypatch):
    _stub_broker(monkeypatch)
    config = {"strategies": {"expected_move_butterfly": {**CUTS}}}
    order = emb.fetch_expected_move_butterfly_order("SCHW", date(2026, 10, 15), "Before market open", config)
    assert order["ok"], order
    assert order["side"] == "call"
    assert (order["lower_strike"], order["body_strike"], order["upper_strike"]) == (97.0, 100.0, 103.0)
    assert order["net_debit"] == pytest.approx(0.52, abs=0.01)
    assert [leg["quantity"] for leg in order["order"]["legs"]] == [1, 2, 1]
    assert [leg["action"] for leg in order["order"]["legs"]] == ["Buy to Open", "Sell to Open", "Buy to Open"]
    assert order["order"]["price_effect"] == "Debit"


def test_a_neutral_read_builds_no_order(monkeypatch):
    _stub_broker(monkeypatch, call_iv=0.55, put_iv=0.59)  # rr25 -0.073: between the cuts
    config = {"strategies": {"expected_move_butterfly": {**CUTS}}}
    order = emb.fetch_expected_move_butterfly_order("SCHW", date(2026, 10, 15), "Before market open", config)
    assert not order["ok"] and order["error"] == "direction_signal_neutral"


def test_max_loss_is_the_debit():
    assert sizing.per_contract_max_loss({"strategy": "expected_move_butterfly", "net_debit": 0.52}, {}) == (
        pytest.approx(52.0)
    )
    assert sizing.per_contract_max_loss({"strategy": "expected_move_butterfly"}, {}) is None


# --------------------------------------------------------------------------- the exit

FLY = [
    {"symbol": "SCHW  261016C00097000", "action": "Buy to Open", "quantity": 1},
    {"symbol": "SCHW  261016C00100000", "action": "Sell to Open", "quantity": 2},
    {"symbol": "SCHW  261016C00103000", "action": "Buy to Open", "quantity": 1},
]


def _quotes(lower_bid, body_ask, upper_bid):
    return {
        FLY[0]["symbol"]: {"bid": lower_bid, "ask": lower_bid + 0.05, "mid": lower_bid + 0.025},
        FLY[1]["symbol"]: {"bid": body_ask - 0.05, "ask": body_ask, "mid": body_ask - 0.025},
        FLY[2]["symbol"]: {"bid": upper_bid, "ask": upper_bid + 0.05, "mid": upper_bid + 0.025},
    }


def _position(quantity=1):
    legs = [{**leg, "quantity": leg["quantity"] * quantity} for leg in FLY]
    return {"entry_credit": -0.52 * quantity, "legs_json": json.dumps(legs), "quantity": quantity}


CFG = {"strategies": {"expected_move_butterfly": {"take_profit_width_frac": 0.5}}}


def test_take_profit_fires_at_half_the_width_at_the_far_touch():
    # far-touch value = 3.20 - 2 x 1.60 + 1.50 = 1.50, exactly half of the 3-wide wing
    assert emb.evaluate_position(_position(), _quotes(3.20, 1.60, 1.50), CFG)["action"] == "close_all"
    assert emb.evaluate_position(_position(), _quotes(3.20, 1.61, 1.50), CFG)["action"] == "hold"


def test_take_profit_is_per_contract_whatever_the_quantity():
    """The scaled legs price the whole position; the trigger is per contract."""
    assert emb.evaluate_position(_position(2), _quotes(3.20, 1.60, 1.50), CFG)["action"] == "close_all"
    assert emb.evaluate_position(_position(2), _quotes(3.20, 1.61, 1.50), CFG)["action"] == "hold"


def test_a_first_morning_loser_holds_rather_than_taking_the_pead_close():
    """Not in management.OVERNIGHT: the reaction-day exit lost most in the study."""
    assert "expected_move_butterfly" not in management.OVERNIGHT
    trade = {
        "order_id": "E1",
        "strategy": "expected_move_butterfly",
        "symbol": "SCHW",
        "expiration": "2026-10-16",
        "entry_credit": -0.52,
        "legs_json": json.dumps(FLY),
        "quantity": 1,
    }
    snapshot = {"ok": True, "quotes": _quotes(0.40, 0.20, 0.01), "spot": 96.0, "source": "stream"}
    now = datetime(2026, 10, 15, 10, 0, tzinfo=management.ET)
    decision = management.evaluate(
        trade, snapshot, {**CFG, "management": {}}, now=now, sessions_held=1, is_first_check_of_day=True
    )
    assert decision.action == "hold" and decision.reason == "working"


# --------------------------------------------------------------------------- the dated start

_CAL = [{"symbol": "AAA", "date": "2026-10-20", "timing": "After market close"}]


def _drive(monkeypatch, today):
    class _Today(dt.date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(harness, "_date", _Today)
    monkeypatch.setattr(rank_strategies, "_ensure_dolt_running", lambda: True)
    monkeypatch.setattr(rank_strategies, "_verify_tastytrade_connection", lambda: True)
    monkeypatch.setattr(harness, "_capture_market_context", lambda day: None)
    monkeypatch.setattr(harness, "_save_entry_review", lambda *a, **k: None)
    monkeypatch.setattr(
        scanner, "_load_config", lambda *a, **k: {"strategies": {}, "strat_test_portfolio": "per_strategy"}
    )
    monkeypatch.setattr(scanner, "fetch_entry_window_calendar", lambda config, **k: list(_CAL))
    accepted = [{"name": "expected_move_butterfly", "reject_reasons": [], "accepted": True, "criteria": {}}]
    monkeypatch.setattr(harness, "_parallel_scan", lambda *a, **k: [(_CAL[0], accepted, None)])
    monkeypatch.setattr(harness, "_log_scan_row", lambda *a, **kw: None)
    monkeypatch.setattr(harness, "_past_entry_window", lambda config: False)


def test_before_its_start_it_opens_nothing(monkeypatch):
    _drive(monkeypatch, dt.date(2026, 10, 13))
    monkeypatch.setitem(
        harness._ORDER_FNS,
        "expected_move_butterfly",
        lambda *a: pytest.fail("built an order before the start"),
    )
    result = harness.cmd_run_entries(types.SimpleNamespace(date=None))
    assert result["opened"] == []
    assert any(s["reason"] == "strategy_not_started" for s in result["skipped"])


def test_from_its_start_the_order_build_is_reached(monkeypatch):
    """The guard, shown to stop firing on its date."""
    _drive(monkeypatch, dt.date(2026, 10, 14))
    reached = []
    monkeypatch.setitem(
        harness._ORDER_FNS,
        "expected_move_butterfly",
        lambda *a: reached.append(1) or {"ok": False, "error": "stub"},
    )
    result = harness.cmd_run_entries(types.SimpleNamespace(date=None))
    assert reached == [1]
    assert not any(s["reason"] == "strategy_not_started" for s in result["skipped"])
