"""Leg selection, worksheet math, rolls, and the settlement decomposition's cash equivalence."""

import pytest

from cherrypick.pmcc import engine


def _entry(strike, root="TNA", expiration="2026-08-28"):
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
        "symbol": "TNA",
        "spot": SPOT,
        "short_expiration": "2026-08-28",
        "long_expiration": "2026-09-04",
        "short_dte": 9,
        "long_dte": 16,
        "short_chain": short_chain,
        "long_chain": long_chain,
        "quotes": {},
        "greeks": greeks or {},
    }


def test_long_selection_prefers_highest_qualifying_strike():
    # 45 has 0.05 extrinsic, 50 has 0.10, 55 has 0.40 (too much) — the highest qualifying is 50.
    quotes = _quotes({45.0: (25.55, 25.75), 50.0: (20.60, 20.80), 55.0: (15.90, 16.10)})
    greeks = {}
    pick = engine.select_long(_chain([45.0, 50.0, 55.0]), quotes, greeks, SPOT, {})
    assert pick["ok"]
    assert pick["strike"] == 50.0
    assert pick["selected_by"] == "extrinsic"


def test_long_selection_delta_gate_and_degrade():
    quotes = _quotes({45.0: (25.55, 25.75), 50.0: (20.60, 20.80)})
    chain = _chain([45.0, 50.0])
    # A present-but-low delta refuses the strike; the deeper one (no greeks) still qualifies.
    greeks = {chain[1]["streamer_symbol"]: {"delta": 0.90, "iv": 0.5, "vega": None}}
    pick = engine.select_long(chain, quotes, greeks, SPOT, {})
    assert pick["strike"] == 45.0
    assert pick["selected_by"] == "extrinsic"
    # With a qualifying delta on file, the selection records the delta path.
    greeks = {chain[1]["streamer_symbol"]: {"delta": 0.99, "iv": 0.5, "vega": None}}
    pick = engine.select_long(chain, quotes, greeks, SPOT, {})
    assert pick["strike"] == 50.0
    assert pick["selected_by"] == "delta"


def test_long_selection_refuses_when_nothing_deep():
    quotes = _quotes({68.0: (3.30, 3.50)})  # extrinsic 0.80 — not a stock substitute
    pick = engine.select_long(_chain([68.0]), quotes, {}, SPOT, {})
    assert not pick["ok"]
    assert pick["reason"] == "no_deep_itm_long"


def test_short_selection_takes_deepest_meeting_yield_floor():
    # Long: 50 @ 20.70 mid, extrinsic 0.10. Shorts: 62 offers thin TV, 67 rich TV — with the
    # floor at 1.2%/wk both clear? 62: tv = mid - 8.60; make mid 8.65 -> tv 0.05, net -0.05: fails.
    # 67: mid 4.75, intrinsic 3.60, tv 1.15, net 1.05, capital 20.70-4.75=15.95,
    # weekly = (1.05/15.95)*(7/9) = 5.1% -> passes; deepest passing wins.
    quotes = _quotes({62.0: (8.60, 8.70), 67.0: (4.70, 4.80)})
    pick = engine.select_short(
        _chain([62.0, 67.0]), quotes, SPOT, 50.0, 20.70, 0.10, 9, {"target_weekly_yield_min": 0.012}
    )
    assert pick["ok"]
    assert pick["strike"] == 67.0
    assert pick["tv"] == pytest.approx(4.75 - 3.60, abs=1e-6)
    assert pick["weekly_yield"] == pytest.approx((1.05 / 15.95) * (7 / 9), rel=1e-4)


def test_short_selection_yield_unreachable_records_best():
    quotes = _quotes({67.0: (3.65, 3.75)})  # mid 3.70, tv 0.10, net 0.0 after long extrinsic
    pick = engine.select_short(
        _chain([67.0]), quotes, SPOT, 50.0, 20.70, 0.10, 9, {"target_weekly_yield_min": 0.012}
    )
    assert not pick["ok"]
    assert pick["reason"] == "yield_unreachable"
    assert pick["best_yield"] == pytest.approx(0.0, abs=1e-6)


def test_plan_entry_worksheet_matches_the_example():
    # The user's worksheet: TNA 70.60, short 67 @ 4.75 -> intrinsic 3.60, TV 1.15,
    # protection 5.1%.
    quotes = _quotes({67.0: (4.70, 4.80)})
    quotes.update(
        {
            _entry(50.0, expiration="2026-09-04")["streamer_symbol"]: {
                "bid": 20.60,
                "ask": 20.80,
                "mid": 20.70,
                "age_seconds": 1.0,
            }
        }
    )
    snapshot = _snapshot([50.0], [67.0])
    # Long chain fixtures were built with the short's expiration in the streamer symbol; rebuild
    # the long side so quotes key onto it.
    snapshot["long_chain"] = [
        {
            "strike_price": 50.0,
            "streamer_symbol": _entry(50.0, expiration="2026-09-04")["streamer_symbol"],
            "occ_symbol": "TNA   260904C00050000",
            "option_type": "call",
        }
    ]
    snapshot["quotes"] = quotes
    planned = engine.plan_entry(snapshot, {"target_weekly_yield_min": 0.012})
    assert planned["ok"], planned
    plan = planned["plan"]
    assert plan["short_strike"] == 67.0
    assert plan["total_premium"] == 4.75
    assert plan["short_intrinsic"] == pytest.approx(3.60)
    assert plan["short_tv"] == pytest.approx(1.15)
    assert plan["downside_protection_pct"] == pytest.approx((70.60 - 67.0) / 70.60, abs=1e-6)
    assert plan["net_debit"] == pytest.approx(20.70 - 4.75)
    assert plan["breakeven"] == pytest.approx(50.0 + 15.95)
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


def test_plan_roll_constraints_and_credit():
    position = {"long_strike": 50.0, "net_debit": 15.95}
    short_leg = {"streamer_symbol": ".OLD", "strike": 67.0}
    chain = _chain([48.0, 60.0, 64.0])
    quotes = _quotes({60.0: (6.30, 6.40), 64.0: (2.80, 2.90)})
    quotes[".OLD"] = {"bid": 1.50, "ask": 1.60, "mid": 1.55, "age_seconds": 1.0}
    # Spot breached to 66: eligible new strikes must be in (50, 66). 48 is below the long — never
    # eligible. 60: mid 6.35, intrinsic 6.0, tv 0.35 -> weekly (0.35/15.95)*(7/9) ≈ 1.7% passes.
    snapshot = {
        "spot": 66.0,
        "expiration": "2026-09-04",
        "dte": 9,
        "chain": chain,
        "quotes": quotes,
        "greeks": {},
    }
    roll = engine.plan_roll(snapshot, position, short_leg, {"target_weekly_yield_min": 0.012})
    assert roll["ok"], roll
    assert roll["new_leg"]["strike"] == 60.0
    assert roll["net_roll_credit"] == pytest.approx(6.35 - 1.55)


def test_plan_roll_unreachable():
    position = {"long_strike": 50.0, "net_debit": 15.95}
    short_leg = {"streamer_symbol": ".OLD", "strike": 67.0}
    chain = _chain([60.0])
    quotes = _quotes({60.0: (6.00, 6.02)})  # mid 6.01, intrinsic 6.0, tv 0.01 -> fails the floor
    quotes[".OLD"] = {"bid": 1.50, "ask": 1.60, "mid": 1.55, "age_seconds": 1.0}
    snapshot = {
        "spot": 66.0,
        "expiration": "2026-09-04",
        "dte": 9,
        "chain": chain,
        "quotes": quotes,
        "greeks": {},
    }
    roll = engine.plan_roll(snapshot, position, short_leg, {"target_weekly_yield_min": 0.012})
    assert not roll["ok"]
    assert roll["reason"] == "roll_unreachable"


def test_dividend_guards():
    config = {"dividends": {"TNA": {"declared_through": "2026-09-30", "ex_dates": ["2026-09-23"]}}}
    assert engine.dividend_coverage_ok(config, "TNA", "2026-09-30")
    assert not engine.dividend_coverage_ok(config, "TNA", "2026-10-01")
    assert not engine.dividend_coverage_ok(config, "TQQQ", "2026-09-01")
    assert engine.ex_date_in_span(config, "TNA", "2026-09-21", "2026-09-25") == "2026-09-23"
    assert engine.ex_date_in_span(config, "TNA", "2026-09-24", "2026-09-30") is None


def test_settlement_style_refuses_undeclared():
    config = {"settlement_style": {"TNA": "physical"}}
    assert engine.settlement_style(config, "TNA") == "physical"
    assert engine.settlement_style(config, "TQQQ") is None
    assert engine.settlement_style({}, "TNA") is None
