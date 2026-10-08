"""The broker-chain recorder for the income screener lists: which options it asks for.

`scripts/fetch_screener_greeks.py` records, around each row of the vendor's lists, the strikes the
re-created rule will have to choose from. What it asks for is a pure function of the saved lists;
each test breaks one thing about a row and checks the request follows.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_screener_greeks.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_screener_greeks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fsg = _module()

LISTS = {
    "short-puts": {"shortPuts": [{"symbol": "AR", "expiry": "11/06/2026", "strikePrice": 35.0}]},
    "covered-calls": {"coveredCalls": [{"symbol": "AR", "expiry": "11/20/2026", "strikePrice": 45.0}]},
    "credit-spreads": {
        "creditSpreads": [
            {"symbol": "MU", "type": "Put", "expiry": "11/20/2026", "strike": {"sell": 1080.0, "buy": 990.0}},
            {
                "symbol": "AZO",
                "type": "Call",
                "expiry": "11/20/2026",
                "strike": {"sell": 2870.0, "buy": 3150.0},
            },
        ]
    },
}


def test_every_list_contributes_its_own_side_and_expiry():
    got = fsg.anchors(LISTS)
    assert got[("AR", "2026-11-06", "P")] == {35.0}
    assert got[("AR", "2026-11-20", "C")] == {45.0}
    assert got[("MU", "2026-11-20", "P")] == {1080.0, 990.0}
    assert got[("AZO", "2026-11-20", "C")] == {2870.0, 3150.0}


def test_a_row_without_a_numeric_strike_asks_for_nothing():
    bad = {"short-puts": {"shortPuts": [{"symbol": "AR", "expiry": "11/06/2026", "strikePrice": "35"}]}}
    assert fsg.anchors(bad) == {}


def test_the_window_is_the_listed_strikes_either_side_of_the_vendors():
    listed = [float(k) for k in range(20, 51)]
    assert fsg.strike_window(listed, {35.0}, width=2) == [33.0, 34.0, 35.0, 36.0, 37.0]


def test_the_window_stops_at_the_end_of_the_chain():
    assert fsg.strike_window([30.0, 31.0, 32.0], {30.0}, width=2) == [30.0, 31.0, 32.0]


def test_a_strike_the_broker_does_not_list_still_gets_its_neighbours():
    """A vendor strike missing from the broker's chain is itself a finding; record around it."""
    assert fsg.strike_window([30.0, 32.5, 35.0, 37.5], {33.0}, width=1) == [30.0, 32.5, 35.0]


def test_two_anchors_share_one_window():
    listed = [float(k) for k in range(0, 100, 10)]
    assert fsg.strike_window(listed, {10.0, 80.0}, width=1) == [0.0, 10.0, 20.0, 70.0, 80.0, 90.0]


def test_the_subscription_pacing_is_not_loosened():
    """DXLink kills a socket subscribed too fast: this script's first full run (2026-10-07, unpaced)
    was cut off with 'Your subscription rate is too high'."""
    assert fsg.BATCH <= 100
    assert fsg.SUBSCRIBE_PACE_S >= 1.0
    assert fsg.MIN_BATCH_S >= 3.0
    assert fsg.CHAIN_PAUSE_S >= 0.3
    assert fsg.RECONNECT_PAUSE_S >= 60


def test_the_wrapped_feed_error_is_reported_by_its_own_message():
    inner = RuntimeError("Fatal streamer error: Your subscription rate is too high")

    class Group(Exception):
        def __init__(self, *exceptions):
            super().__init__("unhandled errors in a TaskGroup")
            self.exceptions = exceptions

    wrapped = Group(Group(inner))
    assert fsg._innermost(wrapped) == str(inner)


def test_measure_skips_names_the_universe_already_measures_and_uses_its_spelling():
    lists = {
        "short-puts": {"shortPuts": [{"symbol": "BRK/B"}, {"symbol": "AAPL"}]},
        "covered-calls": {"coveredCalls": [{"symbol": "AAPL"}, {"symbol": "SLS"}]},
    }
    assert fsg.measure_names(lists, {"AAPL"}) == ["BRK.B", "SLS"]


# --- legs: the vendor's option against the user's monthly ------------------------------------

MONTHLIES = {"2026-11-20", "2026-12-18", "2027-01-15"}


def _is_monthly(iso):
    return iso in MONTHLIES


def _exp(iso, dte, strikes=((100.0, "C100", "P100"), (105.0, "C105", "P105"))):
    return {"expiration": iso, "dte": dte, "strikes": [list(s) for s in strikes]}


def test_the_cycle_prefers_a_monthly_45_to_60_days_out():
    exps = [_exp("2026-11-06", 30), _exp("2026-11-20", 44), _exp("2026-12-18", 50)]
    assert fsg.cycle_expiry(exps, _is_monthly)["expiration"] == "2026-12-18"


def test_the_cycle_falls_back_to_a_monthly_30_to_60_days_out():
    exps = [_exp("2026-11-06", 30), _exp("2026-11-20", 44), _exp("2026-12-18", 72)]
    assert fsg.cycle_expiry(exps, _is_monthly)["expiration"] == "2026-11-20"


def test_a_weekly_is_never_the_cycle_and_an_empty_range_is_none():
    assert fsg.cycle_expiry([_exp("2026-11-06", 45)], _is_monthly) is None
    assert fsg.cycle_expiry([_exp("2026-11-20", 29), _exp("2026-12-18", 64)], _is_monthly) is None


def test_the_option_symbol_is_the_brokers_padded_occ():
    assert fsg.option_symbol("VPG", "2026-11-20", "C", 105.0) == "VPG   261120C00105000"
    assert fsg.option_symbol("BRK.B", "2026-11-20", "P", 7.5) == "BRK/B 261120P00007500"


def test_a_credit_spread_has_a_short_and_a_long_leg():
    row = {"symbol": "MU", "type": "Put", "expiry": "11/20/2026", "strike": {"sell": 1080.0, "buy": 990.0}}
    legs = fsg.row_legs("credit-spreads", row)
    assert [(leg["role"], leg["right"], leg["strike"]) for leg in legs] == [
        ("short", "P", 1080.0),
        ("long", "P", 990.0),
    ]


def test_plan_skips_held_names_and_takes_the_nearest_monthly_strike():
    lists = {
        "short-puts": {
            "shortPuts": [
                {"symbol": "AAA", "expiry": "11/06/2026", "strikePrice": 103.0},
                {"symbol": "ILQ", "expiry": "11/06/2026", "strikePrice": 10.0},
            ]
        }
    }
    chains = {"AAA": [_exp("2026-11-06", 30), _exp("2026-12-18", 50)]}
    plan = fsg.plan_legs(lists, chains, {"ILQ"}, _is_monthly)
    assert [r["symbol"] for r in plan] == ["AAA"]
    m = plan[0]["monthly"][0]
    assert (m["expiry"], m["strike"], m["option"]) == ("2026-12-18", 105.0, "P105")
    assert plan[0]["vendor"][0]["option"] == "AAA   261106P00103000"


def test_a_name_without_a_cycle_monthly_keeps_its_vendor_leg_only():
    lists = {"short-puts": {"shortPuts": [{"symbol": "AAA", "expiry": "11/06/2026", "strikePrice": 103.0}]}}
    assert fsg.plan_legs(lists, {}, set(), _is_monthly)[0]["monthly"] is None


BARS = {"max_spread_pct": 0.10, "max_spread_abs": 0.05, "min_open_interest": 100, "min_volume": 10}
GOOD = {"bid": 1.00, "ask": 1.05, "open_interest": 500, "volume": 50}


def test_no_recommendation_until_the_bars_are_chosen():
    assert fsg.recommend([GOOD], [GOOD], None) is None


def test_a_liquid_vendor_leg_is_recommended():
    assert fsg.recommend([GOOD], [GOOD], BARS) == "vendor"


def test_each_failing_input_sends_the_trade_to_the_monthly():
    for broken in ({"ask": 1.60}, {"open_interest": 20}, {"volume": 2}, {"bid": 0.0}):
        assert fsg.recommend([{**GOOD, **broken}], [GOOD], BARS) == "monthly", broken


def test_a_spread_passes_only_when_both_legs_do_and_no_monthly_is_none():
    assert fsg.recommend([GOOD, {**GOOD, "volume": 0}], [GOOD, GOOD], BARS) == "monthly"
    assert fsg.recommend([{**GOOD, "volume": 0}], None, BARS) == "none"


def test_a_cheap_option_passes_on_the_absolute_width():
    """$0.05 wide on a $0.20 option is 25% of mid but one nickel: the OR rule lets it through."""
    assert fsg.leg_passes({"bid": 0.18, "ask": 0.23, "open_interest": 500, "volume": 50}, BARS)
