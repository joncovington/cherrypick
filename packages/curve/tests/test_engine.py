from cherrypick.curve import engine

PARAMS = {
    "short_delta_target": 0.30,
    "spread_width": 5.0,
    "min_credit_pct_of_width": 0.10,
    "max_leg_spread_pct": 0.25,
}


def _entry(strike, sym):
    return {
        "strike_price": strike,
        "streamer_symbol": sym,
        "occ_symbol": f"VXX   260918C{int(strike * 1000):08d}",
        "option_type": "call",
    }


def _chain_and_quotes():
    chain = [_entry(20, "s20"), _entry(25, "s25"), _entry(30, "s30"), _entry(35, "s35")]
    quotes = {
        "s20": {"bid": 5.9, "ask": 6.1, "mid": 6.0},
        "s25": {"bid": 2.4, "ask": 2.6, "mid": 2.5},
        "s30": {"bid": 0.9, "ask": 1.1, "mid": 1.0},
        "s35": {"bid": 0.36, "ask": 0.44, "mid": 0.40},
    }
    greeks = {"s20": {"delta": 0.70}, "s25": {"delta": 0.42}, "s30": {"delta": 0.29}, "s35": {"delta": 0.12}}
    return chain, quotes, greeks


def test_select_short_nearest_delta():
    chain, quotes, greeks = _chain_and_quotes()
    result = engine.select_short(chain, quotes, greeks, spot=18.0, params=PARAMS)
    assert result["ok"] is True
    assert result["strike"] == 30  # delta 0.29 is nearest to the 0.30 target


def test_select_short_refuses_without_delta_or_iv():
    chain, quotes, _ = _chain_and_quotes()
    result = engine.select_short(chain, quotes, {}, spot=18.0, params=PARAMS)
    assert result == {"ok": False, "reason": "no_delta_for_selection"}


def test_select_short_falls_back_to_computed_delta_when_iv_present():
    chain, quotes, _ = _chain_and_quotes()
    # No `delta` key anywhere, but IV is on file -- selection should fall back to a Black-Scholes
    # computed delta rather than refuse.
    greeks = {sym: {"iv": 0.85} for sym in ("s20", "s25", "s30", "s35")}
    result = engine.select_short(chain, quotes, greeks, spot=18.0, params=PARAMS, dte_days=35)
    assert result["ok"] is True
    assert result["selected_by"] == "delta_computed"
    assert result["delta"] is not None


def test_select_short_fallback_disabled_by_config_refuses_instead_of_computing():
    chain, quotes, _ = _chain_and_quotes()
    greeks = {sym: {"iv": 0.85} for sym in ("s20", "s25", "s30", "s35")}
    params = dict(PARAMS, allow_delta_computed_fallback=False)
    result = engine.select_short(chain, quotes, greeks, spot=18.0, params=params, dte_days=35)
    assert result == {"ok": False, "reason": "no_delta_for_selection"}


def test_select_short_prefers_real_delta_over_computed():
    chain, quotes, greeks = _chain_and_quotes()
    result = engine.select_short(chain, quotes, greeks, spot=18.0, params=PARAMS)
    assert result["selected_by"] == "delta"


def test_bs_call_delta_matches_known_shape():
    # Deep ITM -> delta near 1; far OTM -> delta near 0; both within Black-Scholes' valid range.
    deep_itm = engine.bs_call_delta(spot=30, strike=10, dte_days=30, iv=0.8)
    far_otm = engine.bs_call_delta(spot=10, strike=40, dte_days=30, iv=0.8)
    assert deep_itm > 0.9
    assert far_otm < 0.1


def test_bs_call_delta_none_on_degenerate_input():
    assert engine.bs_call_delta(spot=18, strike=20, dte_days=30, iv=0.0) is None
    assert engine.bs_call_delta(spot=18, strike=20, dte_days=0, iv=0.5) is None
    assert engine.bs_call_delta(spot=18, strike=20, dte_days=None, iv=0.5) is None


def test_select_long_picks_nearest_strike_at_or_above_width():
    chain, quotes, _ = _chain_and_quotes()
    result = engine.select_long(chain, quotes, short_strike=30, params=PARAMS)
    assert result["ok"] is True
    assert result["strike"] == 35  # 30 + 5 width -> the next listed strike at/above 35


def test_select_long_refuses_no_wing():
    chain, quotes, _ = _chain_and_quotes()
    result = engine.select_long(chain, quotes, short_strike=33, params=PARAMS)
    assert result == {"ok": False, "reason": "no_wing_strike"}


def test_worksheet_metrics():
    m = engine.worksheet_metrics(short_mid=1.0, short_strike=30, long_mid=0.40, long_strike=35)
    assert m["width"] == 5.0
    assert m["credit"] == 0.60
    assert m["max_loss"] == 4.40
    assert round(m["credit_pct_of_width"], 4) == round(0.60 / 5.0, 4)


def test_plan_entry_full_flow():
    chain, quotes, greeks = _chain_and_quotes()
    snapshot = {
        "symbol": "VXX",
        "spot": 18.0,
        "expiration": "2026-09-18",
        "dte": 37,
        "chain": chain,
        "quotes": quotes,
        "greeks": greeks,
    }
    result = engine.plan_entry(snapshot, PARAMS)
    assert result["ok"] is True
    plan = result["plan"]
    assert plan["short_strike"] == 30
    assert plan["long_strike"] == 35
    assert len(plan["legs"]) == 2
    roles = {leg["leg_role"] for leg in plan["legs"]}
    assert roles == {"short_call", "long_call"}


def test_plan_entry_refuses_below_credit_floor():
    chain, quotes, greeks = _chain_and_quotes()
    # Widen the spread requirement so credit_pct_of_width falls under the floor.
    params = {**PARAMS, "spread_width": 15.0, "min_credit_pct_of_width": 0.90}
    snapshot = {
        "symbol": "VXX",
        "spot": 18.0,
        "expiration": "2026-09-18",
        "dte": 37,
        "chain": chain,
        "quotes": quotes,
        "greeks": greeks,
    }
    result = engine.plan_entry(snapshot, params)
    assert result["ok"] is False
    assert result["reason"] in ("credit_below_floor", "no_wing_strike")


def test_spread_close_cost():
    assert engine.spread_close_cost({"mid": 0.50}, {"mid": 0.10}) == 0.40
    assert engine.spread_close_cost(None, {"mid": 0.10}) is None


# --------------------------------------------------------------------------- settlement / fees
def test_settle_intrinsic():
    assert engine.settle_intrinsic(30, 35) == 5.0
    assert engine.settle_intrinsic(30, 25) == 0.0


def test_assignment_from_short_call_itm():
    leg = {"strike": 30, "option_type": "call", "action": "Sell to Open"}
    a = engine.assignment_from(leg, spot=35, quantity=2)
    assert a == {"direction": "short", "shares": 200, "basis": 35.0, "strike": 30, "option_type": "call"}


def test_assignment_from_otm_is_none():
    leg = {"strike": 30, "option_type": "call", "action": "Sell to Open"}
    assert engine.assignment_from(leg, spot=25, quantity=1) is None


def test_physical_settlement_cash_flow_equivalence():
    """The calendars/pmcc decomposition: option leg intrinsic + share leg move must equal the
    plain cash-settlement equivalent (E - (S_f - K)) plus (K - S_m), i.e. the total the short
    seller actually nets from premium-in, deliver-at-strike, buy-back-at-market."""
    strike, credit, settlement_spot, cover_price = 30.0, 1.0, 34.0, 33.0
    leg = {
        "strike": strike,
        "option_type": "call",
        "action": "Sell to Open",
        "entry_mid": credit,
        "close_value": engine.settle_intrinsic(strike, settlement_spot),
    }
    option_pnl = engine.leg_pnl(leg)  # entry - close (a sold leg)
    assignment = engine.assignment_from(leg, settlement_spot, quantity=1)
    # share_pnl scales by share count; dividing back out gives the PER-SHARE move so it lines up
    # with the per-share option_pnl above.
    share_pnl_per_share = (
        engine.share_pnl(assignment["direction"], assignment["shares"], assignment["basis"], cover_price)
        / assignment["shares"]
    )
    total = option_pnl + share_pnl_per_share
    # credit + strike - cover_price, the closed-form identity from the module's own derivation.
    expected = credit + strike - cover_price
    assert round(total, 4) == round(expected, 4)
