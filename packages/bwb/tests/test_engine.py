from cherrypick.bwb import engine

PARAMS = {
    "strike_increment": 5.0,
    "near_wing_increments": 1,
    "far_wing_increments": 2,
    "credit_floor": 0.0,
    "max_leg_spread_pct": 0.25,
}


def _put(strike, sym):
    return {
        "strike_price": strike,
        "streamer_symbol": sym,
        "occ_symbol": f"SPXW  260918P{int(strike * 1000):08d}",
        "option_type": "put",
    }


def _call(strike, sym):
    return {
        "strike_price": strike,
        "streamer_symbol": sym,
        "occ_symbol": f"SPXW  260918C{int(strike * 1000):08d}",
        "option_type": "call",
    }


def _chain_and_quotes(spot=6500.0):
    # Body target = spot - expected_move. ATM straddle centers on 6500; near=6505, body=6500(approx
    # snapped),far=6490 with $5 increments once the expected move puts body near 6480.
    strikes = [6470, 6475, 6480, 6485, 6490, 6495, 6500, 6505, 6510]
    chain = [_put(s, f"p{s}") for s in strikes] + [_call(s, f"c{s}") for s in strikes]
    quotes = {}
    for s in strikes:
        # puts cheaper further OTM (lower strike); calls cheaper further OTM (higher strike)
        put_mid = max(0.5, (s - 6470) * 0.9)
        call_mid = max(0.5, (6510 - s) * 0.9)
        quotes[f"p{s}"] = {"bid": put_mid - 0.1, "ask": put_mid + 0.1, "mid": put_mid}
        quotes[f"c{s}"] = {"bid": call_mid - 0.1, "ask": call_mid + 0.1, "mid": call_mid}
    # ATM straddle at 6500: call mid + put mid used for expected_move -- kept small (10 each, so
    # expected_move = 0.85*20 = 17) so the resulting body/near/far strikes land inside the listed
    # window above rather than snapping to its edge.
    quotes["c6500"] = {"bid": 9.9, "ask": 10.1, "mid": 10.0}
    quotes["p6500"] = {"bid": 9.9, "ask": 10.1, "mid": 10.0}
    greeks = {f"p{s}": {"delta": -round(1.0 - (s - 6470) / 40.0, 2), "iv": 0.15} for s in strikes}
    return chain, quotes, greeks, strikes


def _snapshot(spot=6500.0):
    chain, quotes, greeks, strikes = _chain_and_quotes(spot)
    return {
        "symbol": "SPX",
        "spot": spot,
        "expiration": "2026-09-18",
        "dte": 7,
        "chain": chain,
        "quotes": quotes,
        "greeks": greeks,
    }


def test_plan_expected_move_ok():
    snap = _snapshot()
    result = engine.plan_expected_move(snap, PARAMS)
    assert result["ok"] is True
    assert result["atm_strike"] == 6500
    assert result["expected_move"] == round(0.85 * (10.0 + 10.0), 4)


def test_plan_expected_move_refuses_without_atm_quote():
    snap = _snapshot()
    del snap["quotes"]["c6500"]
    result = engine.plan_expected_move(snap, PARAMS)
    assert result == {"ok": False, "reason": "no_expected_move"}


def test_select_strikes_orders_far_body_near():
    result = engine.select_strikes(
        6500.0, 20.0, PARAMS, [6470, 6475, 6480, 6485, 6490, 6495, 6500, 6505, 6510]
    )
    assert result["ok"] is True
    assert result["far"] < result["body"] < result["near"]
    assert result["near"] - result["body"] == 5.0
    assert result["body"] - result["far"] == 10.0


def test_select_strikes_refuses_far_wing_increments_below_floor():
    params = {**PARAMS, "far_wing_increments": 1}
    result = engine.select_strikes(6500.0, 20.0, params, [6470, 6480, 6490, 6500, 6510])
    assert result == {"ok": False, "reason": "far_wing_increments_below_floor"}


def test_select_strikes_refuses_no_listed_strikes():
    result = engine.select_strikes(6500.0, 20.0, PARAMS, [])
    assert result == {"ok": False, "reason": "no_strikes_in_window"}


def test_bwb_metrics_shape():
    m = engine.bwb_metrics(
        body_mid=10.0, near_mid=12.0, far_mid=4.0, body_strike=6480, near_strike=6485, far_strike=6470
    )
    assert m["credit"] == round(2 * 10.0 - 12.0 - 4.0, 4)
    assert m["narrow_width"] == 5.0
    assert m["wide_width"] == 10.0
    assert m["max_loss_up"] == round(5.0 - m["credit"], 4)
    assert m["max_loss_down"] == round(10.0 - m["credit"], 4)
    assert m["max_loss"] == max(m["max_loss_up"], m["max_loss_down"])


def test_plan_entry_full_flow():
    snap = _snapshot()
    result = engine.plan_entry(snap, PARAMS)
    assert result["ok"] is True
    plan = result["plan"]
    assert plan["far_strike"] < plan["body_strike"] < plan["near_strike"]
    roles = {leg["leg_role"] for leg in plan["legs"]}
    assert roles == {"near_long", "body_short_1", "body_short_2", "far_long"}
    assert len(plan["legs"]) == 4


def test_plan_entry_refuses_no_expected_move():
    snap = _snapshot()
    del snap["quotes"]["p6500"]
    result = engine.plan_entry(snap, PARAMS)
    assert result == {"ok": False, "reason": "no_expected_move"}


def test_plan_entry_refuses_no_credit():
    snap = _snapshot()
    # Flatten the put skew so the BWB cannot price a credit: identical mids everywhere.
    for sym, _q in snap["quotes"].items():
        if sym.startswith("p") and sym != "p6500":
            snap["quotes"][sym] = {"bid": 0.9, "ask": 1.1, "mid": 1.0}
    result = engine.plan_entry(snap, PARAMS)
    assert result["ok"] is False
    assert result["reason"] in ("no_credit", "no_strikes_in_window")


def test_plan_addon_brackets_the_far_wing():
    snap = _snapshot()
    result = engine.plan_addon(snap, far_strike=6480, params=PARAMS)
    assert result["ok"] is True
    plan = result["plan"]
    assert plan["long_strike"] < plan["short_strike"]
    assert plan["short_strike"] == 6485
    assert plan["long_strike"] == 6475


def test_plan_addon_refuses_when_not_credit():
    snap = _snapshot()
    for sym in ("p6475", "p6485"):
        snap["quotes"][sym] = {"bid": 4.95, "ask": 5.05, "mid": 5.0}
    result = engine.plan_addon(snap, far_strike=6480, params=PARAMS)
    assert result == {"ok": False, "reason": "addon_not_credit", "detail": {"credit": 0.0}}


def test_close_cost_sums_signed_mids():
    items = [{"action": "Sell to Open", "mid": 1.0}, {"action": "Buy to Open", "mid": 0.4}]
    assert engine.close_cost(items) == round(-1.0 + 0.4, 4)


def test_close_cost_none_on_unpriced_leg():
    items = [{"action": "Sell to Open", "mid": None}]
    assert engine.close_cost(items) is None


def test_settle_intrinsic_put():
    assert engine.settle_intrinsic(strike=6500, spot=6480) == 20.0
    assert engine.settle_intrinsic(strike=6500, spot=6520) == 0.0


def test_leg_pnl_sold_and_bought():
    sold = {"action": "Sell to Open", "entry_mid": 2.0, "close_value": 0.5}
    bought = {"action": "Buy to Open", "entry_mid": 2.0, "close_value": 0.5}
    assert engine.leg_pnl(sold) == 1.5
    assert engine.leg_pnl(bought) == -1.5
    assert engine.leg_pnl({"action": "Sell to Open", "entry_mid": 2.0, "close_value": None}) is None


# --------------------------------------------------------------------------- the wall book
def test_plan_wall_entry_builds_the_call_side_mirror():
    """Body at the wall, near wing BELOW toward spot, far wing ABOVE — all calls, net credit.

    The geometry is the put ladder reflected: the risk is a rally through the far wing, so
    max_loss_up carries the wide width and the floor is below the near wing. Spot sits at 6490 so
    the 6500 wall is genuinely above it, and the far call's fixture quote is tightened — the
    fixture's default far-OTM width (0.20 on a 0.50 mid) is wide in percent AND in money, which the
    gate is right to refuse.
    """
    snap = _snapshot(spot=6490.0)
    snap["quotes"]["c6510"] = {"bid": 0.48, "ask": 0.52, "mid": 0.5}
    plan = engine.plan_wall_entry(snap, PARAMS, 6500.0)
    assert plan["ok"], plan
    inner = plan["plan"]
    assert (inner["near_strike"], inner["body_strike"], inner["far_strike"]) == (6495.0, 6500.0, 6510.0)
    assert all(leg["option_type"] == "call" for leg in inner["legs"])
    assert inner["credit"] > 0
    assert inner["narrow_width"] == 5.0 and inner["wide_width"] == 10.0
    assert inner["max_loss_up"] == round(inner["wide_width"] - inner["credit"], 4)


def test_plan_wall_entry_refuses_without_a_wall_rather_than_borrowing_the_em():
    """No EM fallback: a session with no wall reading is a refusal, or the book trades control's
    placement under its own name and the comparison dissolves."""
    snap = _snapshot(spot=6490.0)
    assert engine.plan_wall_entry(snap, PARAMS, None) == {"ok": False, "reason": "call_wall_unavailable"}


def test_plan_wall_entry_refuses_a_wall_at_or_below_spot():
    snap = _snapshot()
    assert engine.plan_wall_entry(snap, PARAMS, 6500.0)["reason"] == "call_wall_not_above_spot"
    # And after SNAPPING: a wall at 6501 snaps to the 6500 body, which is at spot.
    assert engine.plan_wall_entry(snap, PARAMS, 6501.0)["reason"] == "call_wall_not_above_spot"


def test_wall_spread_gate_admits_a_penny_wide_leg_and_refuses_a_wide_one():
    """The curve/calendars rule, entry-side: a 0.00/0.01 far call is a 200% ratio and a one-cent
    width — admitted. Wide in percent AND in money — refused. Verified by dropping the absolute
    floor and watching the penny-wide case refuse."""
    snap = _snapshot(spot=6490.0)
    snap["quotes"]["c6510"] = {"bid": 0.0, "ask": 0.01, "mid": 0.005}
    plan = engine.plan_wall_entry(snap, PARAMS, 6500.0)
    assert plan["ok"], plan

    snap2 = _snapshot(spot=6490.0)
    snap2["quotes"]["c6510"] = {"bid": 0.0, "ask": 0.60, "mid": 0.30}
    refused = engine.plan_wall_entry(snap2, PARAMS, 6500.0)
    assert refused == {
        "ok": False,
        "reason": "spread_too_wide",
        "detail": {"leg": "far", "spread_pct": 2.0},
    }


def test_settle_intrinsic_knows_both_sides():
    """A call settles on spot ABOVE strike. Transposed, the wall book's settlement would book its
    max-loss case as a win."""
    assert engine.settle_intrinsic(6500, 6520, "call") == 20.0
    assert engine.settle_intrinsic(6500, 6480, "call") == 0.0
    assert engine.settle_intrinsic(6500, 6480) == 20.0  # default stays put
    assert engine.settle_intrinsic(6500, 6520, "put") == 0.0
