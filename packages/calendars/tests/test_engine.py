"""The pure engine: EM targeting, strike intersection, structure math, and the SPX fee stack."""

import pytest
from cherrypick.core import fees as _fees

from cherrypick.calendars import engine


def _entry(strike, otype, expiration, tag):
    return {
        "strike_price": float(strike),
        "streamer_symbol": f".{tag}{otype[0].upper()}{strike:g}",
        "occ_symbol": f"SPXW  {tag}{otype[0].upper()}{strike:08.0f}",
        "option_type": otype,
    }


def _snapshot(*, spot=6500.0, front_strikes=None, back_strikes=None, quotes=None):
    """A synthetic entry snapshot. Front chain lists 5-point strikes, back chain whatever the test
    says (defaults identical), quotes default to a flat surface with the front ATM straddle priced
    so EM = 0.85 * (20 + 20) = 34."""
    front_strikes = front_strikes or [spot + 5 * i for i in range(-20, 21)]
    back_strikes = back_strikes or front_strikes
    front, back = [], []
    for s in front_strikes:
        front += [_entry(s, "put", "2026-08-21", "F"), _entry(s, "call", "2026-08-21", "F")]
    for s in back_strikes:
        back += [_entry(s, "put", "2026-08-24", "B"), _entry(s, "call", "2026-08-24", "B")]
    quote_map = {}
    for e in front:
        quote_map[e["streamer_symbol"]] = {"bid": 19.8, "ask": 20.2, "mid": 20.0}
    for e in back:
        quote_map[e["streamer_symbol"]] = {"bid": 24.7, "ask": 25.3, "mid": 25.0}
    if quotes:
        quote_map.update(quotes)
    return {
        "symbol": "SPX",
        "spot": spot,
        "front_expiration": "2026-08-21",
        "back_expiration": "2026-08-24",
        "front": front,
        "back": back,
        "quotes": quote_map,
        "greeks": {},
    }


def test_plan_entry_targets_spot_plus_minus_em():
    snapshot = _snapshot()
    planned = engine.plan_entry(snapshot, {"em_factor": 0.85})
    assert planned["ok"]
    plan = planned["plan"]
    assert plan["em"] == pytest.approx(0.85 * 40.0)  # 34 points
    # Nearest 5-point strikes to 6500 ± 34.
    assert plan["sides"]["put"]["strike"] == 6465.0
    assert plan["sides"]["call"]["strike"] == 6535.0
    assert plan["sides"]["put"]["debit"] == pytest.approx(5.0)
    legs = {leg["leg_role"]: leg for leg in plan["sides"]["put"]["legs"]}
    assert legs["front_put"]["action"] == "Sell to Open"
    assert legs["back_put"]["action"] == "Buy to Open"
    assert legs["front_put"]["expiration"] == "2026-08-21"
    assert legs["back_put"]["expiration"] == "2026-08-24"


def test_plan_entry_uses_the_strike_intersection():
    # Back chain lists only 25-point strikes: the chosen strike must exist in BOTH chains,
    # so the put lands on 6475 (nearest shared strike to 6466), not front-only 6465.
    snapshot = _snapshot(back_strikes=[6500.0 + 25 * i for i in range(-8, 9)])
    planned = engine.plan_entry(snapshot, {})
    assert planned["ok"]
    assert planned["plan"]["sides"]["put"]["strike"] == 6475.0
    assert planned["plan"]["sides"]["call"]["strike"] == 6525.0


def test_plan_entry_refuses_when_no_shared_strike():
    snapshot = _snapshot(back_strikes=[7000.0])
    planned = engine.plan_entry(snapshot, {})
    assert planned == {"ok": False, "reason": "no_intersection_strike", "detail": "put"}


def test_plan_entry_refuses_without_atm_quotes():
    snapshot = _snapshot()
    # Kill every front call quote: no straddle, no EM.
    snapshot["quotes"] = {sym: q for sym, q in snapshot["quotes"].items() if not sym.startswith(".FC")}
    planned = engine.plan_entry(snapshot, {})
    assert planned["ok"] is False
    assert planned["reason"] == "no_em_quotes"


def test_plan_entry_refuses_a_calendar_priced_at_a_credit():
    snapshot = _snapshot()
    # Back cheaper than front — a torn read, not free money.
    for sym, quote in snapshot["quotes"].items():
        if sym.startswith(".B"):
            quote.update({"bid": 9.8, "ask": 10.2, "mid": 10.0})
    planned = engine.plan_entry(snapshot, {})
    assert planned["ok"] is False
    assert planned["reason"] == "non_positive_debit"


def test_combo_value_is_none_on_any_missing_leg():
    marks = {"front_put": {"mid": 20.0}, "back_put": {"mid": 25.0}}
    assert engine.combo_value(marks) == 5.0
    marks["back_put"] = None
    assert engine.combo_value(marks) is None


def test_settle_intrinsic():
    assert engine.settle_intrinsic(6400.0, "put", 6350.0) == 50.0
    assert engine.settle_intrinsic(6400.0, "put", 6450.0) == 0.0
    assert engine.settle_intrinsic(6600.0, "call", 6650.0) == 50.0
    assert engine.settle_intrinsic(6600.0, "call", 6550.0) == 0.0


def test_leg_pnl_sign_convention():
    short = {"action": "Sell to Open", "entry_mid": 20.0, "close_value": 12.0}
    long_ = {"action": "Buy to Open", "entry_mid": 25.0, "close_value": 30.0}
    assert engine.leg_pnl(short) == 8.0
    assert engine.leg_pnl(long_) == 5.0
    assert engine.leg_pnl({"action": "Sell to Open", "entry_mid": 20.0, "close_value": None}) is None


def test_entry_cost_carries_the_spx_exchange_fee():
    quotes = [{"bid": 19.8, "ask": 20.2}, {"bid": 24.7, "ask": 25.3}]
    cost = engine.entry_cost("SPX", quotes, 1, {})
    # Fee side: 2 legs x ($1 commission + $0.10 clearing + $0.02 ORF + $0.60 SPX) + TAF on 1 sell.
    assert cost["fee"] == pytest.approx(_fees.ic_open_fee("SPX", 1, legs=2, sell_legs=1, ndigits=4), abs=0.01)
    assert cost["fee"] > 3.4
    # Slippage: 12.5% of each 0.40/0.60 spread, x100.
    assert cost["slippage"] == pytest.approx((0.4 * 0.125 + 0.6 * 0.125) * 100, abs=0.01)
    assert cost["total"] == pytest.approx(cost["fee"] + cost["slippage"], abs=0.01)


def test_close_cost_has_no_commission_but_keeps_the_exchange_fee():
    quotes = [{"bid": 19.8, "ask": 20.2}, {"bid": 24.7, "ask": 25.3}]
    close = engine.close_cost("SPX", quotes, 1, {}, sell_legs=1)
    open_ = engine.entry_cost("SPX", quotes, 1, {})
    assert close["fee"] == pytest.approx(open_["fee"] - 2.0, abs=0.01)  # $1/contract open-only x2


def test_settlement_fee_is_per_itm_symbol():
    assert engine.settlement_fee(0) == 0.0
    assert engine.settlement_fee(1) == 5.0
    assert engine.settlement_fee(2) == 10.0
