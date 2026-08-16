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


# --------------------------------------------------------------------- physical settlement
def _short(option_type, strike, entry_mid):
    return {
        "option_type": option_type,
        "strike": float(strike),
        "action": "Sell to Open",
        "entry_mid": float(entry_mid),
    }


def test_settlement_style_is_declared_never_assumed():
    assert engine.settlement_style({"settlement_style": {"SPY": "physical"}}, "SPY") == "physical"
    assert engine.settlement_style({"settlement_style": {"SPX": "cash"}}, "SPX") == "cash"
    # Declared, and this symbol is not in it — that is an answer, and the answer is no.
    assert engine.settlement_style({"settlement_style": {"SPX": "cash"}}, "SPY") is None
    # A style the module does not implement is a refusal, not a fallback.
    assert engine.settlement_style({"settlement_style": {"SPY": "handwave"}}, "SPY") is None
    # The pre-SPY spelling still reads, including its SPX-cash default when nothing is declared.
    assert engine.settlement_style({"cash_settled_symbols": ["SPX"]}, "SPX") == "cash"
    assert engine.settlement_style({}, "SPX") == "cash"
    assert engine.settlement_style({}, "SPY") is None


def test_only_an_itm_leg_delivers_shares_and_the_direction_follows_the_contract():
    # Short put assigned -> you bought shares. Short call assigned -> you sold them.
    assert engine.assignment_from(_short("put", 780, 3.0), 770.0, 1)["direction"] == "long"
    assert engine.assignment_from(_short("call", 760, 3.0), 770.0, 1)["direction"] == "short"
    # A long option exercised is the mirror of the short being assigned.
    long_call = {"option_type": "call", "strike": 760.0, "action": "Buy to Open", "entry_mid": 4.0}
    long_put = {"option_type": "put", "strike": 780.0, "action": "Buy to Open", "entry_mid": 4.0}
    assert engine.assignment_from(long_call, 770.0, 1)["direction"] == "long"
    assert engine.assignment_from(long_put, 770.0, 1)["direction"] == "short"
    # OTM expires worthless: nothing is delivered.
    assert engine.assignment_from(_short("put", 760, 3.0), 770.0, 1) is None
    assert engine.assignment_from(_short("call", 780, 3.0), 770.0, 1) is None


def test_shares_are_delivered_at_the_settlement_spot_not_the_strike():
    """The decomposition's load-bearing choice. Basis = strike would double-count it against the
    intrinsic the option leg already booked."""
    a = engine.assignment_from(_short("put", 780, 3.0), 770.0, 2)
    assert a["basis"] == 770.0
    assert a["strike"] == 780.0
    assert a["shares"] == 200


@pytest.mark.parametrize("option_type,strike", [("put", 780.0), ("call", 760.0)])
def test_option_leg_plus_share_leg_equals_the_true_physical_cash_flow(option_type, strike):
    """The whole reason physical settlement could be added without restating the option accounting.

    Truth for a short put: +credit, buy 100 at K, sell at the disposal price. For a short call:
    +credit, sell 100 at K, buy back at the disposal price. The model has to reproduce it out of
    an intrinsic-priced option leg plus a share leg based at the settlement spot.
    """
    credit, settle_spot, dispose_spot = 3.0, 770.0, 774.5
    leg = _short(option_type, strike, credit)

    intrinsic = engine.settle_intrinsic(strike, option_type, settle_spot)
    option_dollars = engine.leg_pnl({**leg, "close_value": intrinsic}) * 100
    a = engine.assignment_from(leg, settle_spot, 1)
    model = option_dollars + engine.share_pnl(a["direction"], a["shares"], a["basis"], dispose_spot)

    if option_type == "put":
        truth = 100 * (credit - strike + dispose_spot)
    else:
        truth = 100 * (credit + strike - dispose_spot)
    assert round(model, 6) == round(truth, 6)


def test_cash_settlement_is_the_share_term_going_to_zero():
    """Disposal at the settlement spot leaves exactly the cash-settled answer — which is why the
    two styles can share one settlement path, one derivation and one validation."""
    leg = _short("put", 780, 3.0)
    a = engine.assignment_from(leg, 770.0, 1)
    assert engine.share_pnl(a["direction"], a["shares"], a["basis"], 770.0) == 0.0


def test_an_assignment_costs_the_settlement_event_plus_the_equity_pass_throughs():
    """$5 for the event, as cash settlement pays, and the SEC/TAF pass-throughs on the sell fill —
    the disposal for delivered longs, the assignment itself for delivered shorts."""
    long_shares = {"direction": "long", "shares": 100, "basis": 770.0}
    fee = engine.assignment_fee(long_shares, 774.5)
    assert fee > _fees.ASSIGNMENT_FEE_PER_SETTLEMENT
    assert fee == round(
        _fees.ASSIGNMENT_FEE_PER_SETTLEMENT
        + _fees.stock_trade_fee(100, 774.5, side="sell", ndigits=4),
        2,
    )
    # A short delivery's sell happened at assignment, so its pass-through is priced there.
    short_shares = {"direction": "short", "shares": 100, "basis": 770.0}
    assert engine.assignment_fee(short_shares, 774.5) == round(
        _fees.ASSIGNMENT_FEE_PER_SETTLEMENT
        + _fees.stock_trade_fee(100, 770.0, side="sell", ndigits=4),
        2,
    )
