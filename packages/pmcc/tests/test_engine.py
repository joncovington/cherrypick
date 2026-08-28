"""Leg selection (delta-band long, ATM short), worksheet math, and the settlement decomposition's
cash equivalence."""

import pytest

from cherrypick.pmcc import engine


def _entry(strike, root="TQQQ", expiration="2026-08-28"):
    return {
        "strike_price": float(strike),
        "streamer_symbol": f".{root}{strike:g}C{expiration}",
        "occ_symbol": f"{root:<6}260828C{int(strike * 1000):08d}",
        "option_type": "call",
    }


def _chain(strikes):
    return [_entry(s) for s in strikes]


def _quotes(spec):
    """{strike: (bid, ask)} keyed onto the fixture chain's streamer symbols."""
    out = {}
    for strike, (bid, ask) in spec.items():
        out[_entry(strike)["streamer_symbol"]] = {
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2.0,
            "age_seconds": 1.0,
        }
    return out


SPOT = 70.60


def _snapshot(long_spec, short_spec, greeks=None):
    long_chain = _chain(long_spec)
    short_chain = _chain(short_spec)
    return {
        "ok": True,
        "symbol": "TQQQ",
        "spot": SPOT,
        "short_expiration": "2026-08-28",
        "long_expiration": "2026-09-04",
        "short_dte": 7,
        "long_dte": 16,
        "short_chain": short_chain,
        "long_chain": long_chain,
        "quotes": {},
        "greeks": greeks or {},
    }


def test_long_selection_picks_delta_nearest_band_midpoint():
    # Band is [0.85, 0.90], midpoint 0.875. 45 has delta 0.99 (outside), 50 has 0.88 (inside,
    # nearest midpoint), 55 has 0.86 (inside but farther from midpoint).
    quotes = _quotes({45.0: (25.55, 25.75), 50.0: (20.60, 20.80), 55.0: (15.90, 16.10)})
    chain = _chain([45.0, 50.0, 55.0])
    greeks = {
        chain[0]["streamer_symbol"]: {"delta": 0.99},
        chain[1]["streamer_symbol"]: {"delta": 0.88},
        chain[2]["streamer_symbol"]: {"delta": 0.86},
    }
    pick = engine.select_long(chain, quotes, greeks, SPOT, {})
    assert pick["ok"]
    assert pick["strike"] == 50.0
    assert pick["selected_by"] == "delta"


def test_long_selection_fallback_disabled_by_config_skips_no_delta_strike():
    quotes = _quotes({45.0: (25.55, 25.75), 50.0: (20.60, 20.80)})
    chain = _chain([45.0, 50.0])
    # No delta on file for either strike, and the fallback is disabled -- neither may be admitted.
    pick = engine.select_long(chain, quotes, {}, SPOT, {"allow_extrinsic_fallback": False})
    assert pick == {"ok": False, "reason": "no_deep_itm_long"}


def test_long_selection_extrinsic_fallback_when_no_greeks():
    # No delta on file for either strike: falls back to the highest strike within the extrinsic
    # bound (45 has 0.05 extrinsic, 50 has 0.10 -- both qualify, 50 is deeper-still-qualifying).
    quotes = _quotes({45.0: (25.55, 25.75), 50.0: (20.60, 20.80)})
    chain = _chain([45.0, 50.0])
    pick = engine.select_long(chain, quotes, {}, SPOT, {})
    assert pick["ok"]
    assert pick["strike"] == 50.0
    assert pick["selected_by"] == "extrinsic"


def test_long_selection_known_delta_outside_band_is_excluded_not_degraded():
    # A present delta OUTSIDE the band disqualifies that strike outright -- a KNOWN delta outside
    # the band is a real disqualification, not a case for the no-delta extrinsic fallback (which
    # exists only for candidates the feed cannot say anything about).
    quotes = _quotes({45.0: (25.55, 25.75)})
    chain = _chain([45.0])
    greeks = {chain[0]["streamer_symbol"]: {"delta": 0.99}}  # outside [0.85, 0.90]
    pick = engine.select_long(chain, quotes, greeks, SPOT, {})
    assert not pick["ok"]
    assert pick["reason"] == "no_deep_itm_long"


def test_long_selection_refuses_when_nothing_deep():
    quotes = _quotes({68.0: (3.30, 3.50)})  # extrinsic 0.80 — not a stock substitute
    pick = engine.select_long(_chain([68.0]), quotes, {}, SPOT, {})
    assert not pick["ok"]
    assert pick["reason"] == "no_deep_itm_long"


def test_short_selection_takes_nearest_strike_below_spot():
    quotes = _quotes({67.0: (4.70, 4.80), 62.0: (8.60, 8.70)})
    pick = engine.select_short(_chain([62.0, 67.0]), quotes, SPOT, {})
    assert pick["ok"]
    assert pick["strike"] == 67.0  # 3.60 away vs 8.60 away
    assert pick["intrinsic"] == pytest.approx(70.60 - 67.0)
    assert pick["tv"] == pytest.approx(4.75 - (70.60 - 67.0))


def test_short_selection_can_land_otm():
    # Spot 70.60: 71 is 0.40 away (OTM), 67 is 3.60 away (ITM) -- 71 wins nearest.
    quotes = _quotes({67.0: (4.70, 4.80), 71.0: (0.90, 1.00)})
    pick = engine.select_short(_chain([67.0, 71.0]), quotes, SPOT, {})
    assert pick["ok"]
    assert pick["strike"] == 71.0
    assert pick["intrinsic"] == 0.0  # OTM: no intrinsic
    assert pick["tv"] == pytest.approx(0.95)  # OTM: mid is entirely time value


def test_short_selection_ties_prefer_itm_side():
    # 68 and 73.20 are both 2.60 away from 70.60 -- ties prefer the strike below spot.
    quotes = _quotes({68.0: (3.00, 3.10), 73.2: (0.20, 0.30)})
    pick = engine.select_short(_chain([68.0, 73.2]), quotes, SPOT, {})
    assert pick["ok"]
    assert pick["strike"] == 68.0


def test_plan_entry_worksheet_with_atm_short():
    # Long 50 @ 20.70 mid (delta 0.88, inside band); short nearest spot at 71 (OTM by 0.40).
    long_entry = _entry(50.0, expiration="2026-09-04")
    quotes = _quotes({71.0: (0.90, 1.00)})
    quotes[long_entry["streamer_symbol"]] = {
        "bid": 20.60,
        "ask": 20.80,
        "mid": 20.70,
        "age_seconds": 1.0,
    }
    snapshot = _snapshot([50.0], [71.0])
    snapshot["long_chain"] = [
        {
            "strike_price": 50.0,
            "streamer_symbol": long_entry["streamer_symbol"],
            "occ_symbol": "TQQQ  260904C00050000",
            "option_type": "call",
        }
    ]
    snapshot["quotes"] = quotes
    snapshot["greeks"] = {long_entry["streamer_symbol"]: {"delta": 0.88}}
    planned = engine.plan_entry(snapshot, {})
    assert planned["ok"], planned
    plan = planned["plan"]
    assert plan["short_strike"] == 71.0
    assert plan["short_intrinsic"] == 0.0
    assert plan["short_tv"] == pytest.approx(0.95)
    assert plan["net_debit"] == pytest.approx(20.70 - 0.95)
    roles = {leg["leg_role"]: leg for leg in plan["legs"]}
    assert roles["long_call"]["action"] == "Buy to Open"
    assert roles["short_call_1"]["action"] == "Sell to Open"
    assert roles["long_call"]["expiration"] == "2026-09-04"


def test_short_time_value_and_position_value():
    assert engine.short_time_value(4.75, 70.60, 67.0) == pytest.approx(1.15)
    assert engine.short_time_value(1.20, 66.00, 67.0) == pytest.approx(1.20)  # OTM: all TV
    value = engine.position_value({"long_call": {"mid": 20.7}, "short_call_1": {"mid": 4.75}})
    assert value == pytest.approx(15.95)
    assert engine.position_value({"long_call": {"mid": 20.7}, "short_call_1": None}) is None


def test_settlement_decomposition_cash_equivalence_short_call():
    # Short call K=67, credit E=4.75, settlement S_f=69.20, cover S_m=68.10.
    leg = {
        "strike": 67.0,
        "option_type": "call",
        "action": "Sell to Open",
        "entry_mid": 4.75,
        "close_value": engine.settle_intrinsic(67.0, "call", 69.20),
    }
    option_pnl = engine.leg_pnl(leg)  # E - (S_f - K)
    assignment = engine.assignment_from(leg, 69.20, 1)
    assert assignment["direction"] == "short"
    assert assignment["basis"] == 69.20
    shares_pnl = engine.share_pnl("short", 100, 69.20, 68.10)
    total = option_pnl * 100 + shares_pnl
    # True cash flow per share: +E (premium), deliver at K, cover at S_m -> E + K - S_m.
    assert total == pytest.approx((4.75 + 67.0 - 68.10) * 100, abs=1e-6)


def test_settlement_decomposition_long_call_exercise():
    # Long call K=50, debit D=20.70, settlement S_f=68.00, sell shares S_m=68.50.
    leg = {
        "strike": 50.0,
        "option_type": "call",
        "action": "Buy to Open",
        "entry_mid": 20.70,
        "close_value": engine.settle_intrinsic(50.0, "call", 68.00),
    }
    option_pnl = engine.leg_pnl(leg)  # (S_f - K) - D
    assignment = engine.assignment_from(leg, 68.00, 1)
    assert assignment["direction"] == "long"
    shares_pnl = engine.share_pnl("long", 100, 68.00, 68.50)
    total = option_pnl * 100 + shares_pnl
    # True cash flow per share: -D, receive shares at K, sell at S_m -> S_m - K - D.
    assert total == pytest.approx((68.50 - 50.0 - 20.70) * 100, abs=1e-6)


def test_otm_expiry_delivers_nothing():
    leg = {"strike": 67.0, "option_type": "call", "action": "Sell to Open"}
    assert engine.assignment_from(leg, 66.50, 1) is None


def test_dividend_guards():
    config = {"dividends": {"TQQQ": {"declared_through": "2026-09-30", "ex_dates": ["2026-09-23"]}}}
    assert engine.dividend_coverage_ok(config, "TQQQ", "2026-09-30")
    assert not engine.dividend_coverage_ok(config, "TQQQ", "2026-10-01")
    assert not engine.dividend_coverage_ok(config, "TNA", "2026-09-01")
    assert engine.ex_date_in_span(config, "TQQQ", "2026-09-21", "2026-09-25") == "2026-09-23"
    assert engine.ex_date_in_span(config, "TQQQ", "2026-09-24", "2026-09-30") is None


def test_settlement_style_refuses_undeclared():
    config = {"settlement_style": {"TQQQ": "physical"}}
    assert engine.settlement_style(config, "TQQQ") == "physical"
    assert engine.settlement_style(config, "TNA") is None
    assert engine.settlement_style({}, "TQQQ") is None


def test_settlement_style_xsp_is_cash():
    config = {"settlement_style": {"TQQQ": "physical", "XSP": "cash"}}
    assert engine.settlement_style(config, "XSP") == "cash"
    assert engine.settlement_style(config, "xsp") == "cash"  # case-insensitive lookup


def test_xsp_worksheet_math_with_cash_settled_short():
    # Same worksheet arithmetic as TQQQ -- settlement style never enters worksheet_metrics/
    # plan_entry, only the settle-time/assignment path (settle_expiring_legs) branches on it.
    long_entry = _entry(4300.0, root="XSP", expiration="2026-09-04")
    short_entry = _entry(4550.0, root="XSP", expiration="2026-08-28")
    quotes = {
        long_entry["streamer_symbol"]: {"bid": 249.0, "ask": 251.0, "mid": 250.0, "age_seconds": 1.0},
        short_entry["streamer_symbol"]: {"bid": 9.0, "ask": 11.0, "mid": 10.0, "age_seconds": 1.0},
    }
    snapshot = {
        "ok": True,
        "symbol": "XSP",
        "spot": 4551.0,
        "short_expiration": "2026-08-28",
        "long_expiration": "2026-09-04",
        "short_dte": 7,
        "long_dte": 16,
        "short_chain": [short_entry],
        "long_chain": [long_entry],
        "quotes": quotes,
        "greeks": {long_entry["streamer_symbol"]: {"delta": 0.88}},
    }
    planned = engine.plan_entry(snapshot, {})
    assert planned["ok"], planned
    plan = planned["plan"]
    assert plan["short_strike"] == 4550.0
    assert plan["short_intrinsic"] == pytest.approx(1.0)  # spot 4551 vs strike 4550
    assert plan["short_tv"] == pytest.approx(9.0)


def test_settle_intrinsic_is_settlement_style_agnostic():
    # settle_intrinsic/leg_pnl handle cash settlement identically to the option-leg half of
    # physical settlement -- "the calendars decomposition with the share term zeroed", not a
    # second model. A cash-settled short expiring ITM just settles to this intrinsic in dollars,
    # no share leg.
    leg = {
        "strike": 4550.0,
        "option_type": "call",
        "action": "Sell to Open",
        "entry_mid": 10.0,
        "close_value": engine.settle_intrinsic(4550.0, "call", 4560.0),
    }
    assert leg["close_value"] == pytest.approx(10.0)
    assert engine.leg_pnl(leg) == pytest.approx(10.0 - 10.0)  # entry credit minus intrinsic paid
    # And assignment_from must never be called for a cash-settled leg -- book.py's call site is
    # gated `if physical:`, so this is a documentation assertion of the contract, not exercised
    # through book.py here.


def test_xsp_index_exchange_fee_is_looked_up_by_symbol():
    # The one place TQQQ and XSP genuinely differ in the fee stack: XSP is a broad-based index
    # option and is priced off cherrypick.core.fees.INDEX_EXCHANGE_FEE_PER_CONTRACT; TQQQ (an ETF)
    # is off that schedule entirely (implicit 0.0). Both currently net to the same number because
    # XSP's listed rate is $0.00/contract under 10 contracts/leg, but they arrive at it through
    # different code paths, which is what this test pins.
    from cherrypick.core import fees as _fees

    assert "XSP" in _fees.INDEX_EXCHANGE_FEE_PER_CONTRACT
    assert "TQQQ" not in _fees.INDEX_EXCHANGE_FEE_PER_CONTRACT
    leg_quotes = [{"bid": 249.0, "ask": 251.0}, {"bid": 9.0, "ask": 11.0}]
    cost_xsp = engine.entry_cost("XSP", leg_quotes, 1, {})
    cost_tqqq = engine.entry_cost("TQQQ", leg_quotes, 1, {})
    assert cost_xsp["fee"] == pytest.approx(cost_tqqq["fee"])  # both 0.0 exchange fee today


# ------------------------------------------ the entry-side spread gate (added 2026-08-28)
#
# `max_leg_spread_pct` existed from the start and was enforced ONLY in
# `management.execution_gate` -- on whether a mark may be ACTED on when closing -- so nothing ever
# measured the spread being PAID at entry. curve and bwb both hold the same parameter at the same
# default and check it in their own `plan_entry`; this module was the outlier, and its CLAUDE.md
# already described a gate the code did not have.
#
# Measured over all 8 recorded entries before landing: seven sat at 0.018-0.072, including the
# deep-ITM long legs the docs worried about, and one sat at 0.293. The gate refuses the anomaly and
# not the strategy.


def _planned(long_quote, short_quote, params=None):
    long_entry = _entry(50.0, expiration="2026-09-04")
    quotes = _quotes({71.0: short_quote})
    quotes[long_entry["streamer_symbol"]] = {**long_quote, "age_seconds": 1.0}
    snapshot = _snapshot([50.0], [71.0])
    snapshot["long_chain"] = [
        {
            "strike_price": 50.0,
            "streamer_symbol": long_entry["streamer_symbol"],
            "occ_symbol": "TQQQ  260904C00050000",
            "option_type": "call",
        }
    ]
    snapshot["quotes"] = quotes
    snapshot["greeks"] = {long_entry["streamer_symbol"]: {"delta": 0.88}}
    return engine.plan_entry(snapshot, params or {})


def test_a_wide_long_leg_refuses_the_entry():
    """The 2026-08-27 XSP fill: the long_call went in $9.95 wide (0.293) against its control twin's
    $0.13, a 76x difference in execution cost on the two arms of one A/B."""
    out = _planned({"bid": 18.0, "ask": 24.0, "mid": 21.0}, (0.90, 1.00))
    assert out["ok"] is False
    assert out["reason"] == "spread_too_wide"
    assert out["detail"]["leg"] == "long_call"
    assert out["detail"]["spread_pct"] > 0.25


def test_a_wide_short_leg_refuses_it_too():
    """The short is the leg being SOLD; paying up there is the same cost wearing the other sign."""
    out = _planned({"bid": 20.60, "ask": 20.80, "mid": 20.70}, (0.60, 1.30))
    assert out["ok"] is False
    assert out["detail"]["leg"] == "short_call_1"


def test_the_spreads_this_module_actually_trades_are_admitted():
    """The seven real entries sat at 0.018-0.072. A gate that refused those would starve the module
    rather than protect it -- the curve lesson from the day before."""
    for pct in (0.018, 0.052, 0.072, 0.24):
        half = 20.70 * pct / 2
        out = _planned(
            {"bid": 20.70 - half, "ask": 20.70 + half, "mid": 20.70}, (0.90, 1.00)
        )
        assert out["ok"] is True, f"{pct} should be admitted: {out}"


def test_the_bound_is_configurable_and_the_default_is_the_suite_one():
    wide = {"bid": 18.0, "ask": 24.0, "mid": 21.0}
    assert _planned(wide, (0.90, 1.00))["ok"] is False          # default 0.25
    assert _planned(wide, (0.90, 1.00), {"max_leg_spread_pct": 0.50})["ok"] is True


def test_an_unpriceable_quote_is_not_silently_treated_as_tight():
    """None is 'unknown', never 'fine'. A malformed quote must not slip through as a pass."""
    assert engine._spread_pct({"bid": None, "ask": 1.0, "mid": 0.5}) is None
    assert engine._spread_pct({"bid": 0.4, "ask": 0.6, "mid": 0}) is None
    assert engine._spread_pct({"bid": 0.4, "ask": 0.6, "mid": 0.5}) == pytest.approx(0.4)
