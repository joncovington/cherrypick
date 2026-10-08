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
    """DXLink kills a socket subscribed too fast (the suite streamer, 2026-08-24)."""
    assert fsg.BATCH <= 200
    assert fsg.SUBSCRIBE_PACE_S >= 0.15
    assert fsg.CHAIN_PAUSE_S >= 0.3
