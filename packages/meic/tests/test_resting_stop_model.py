"""The replay's model of a stop-limit resting at the broker (`stop_resting`, 2026-11-02).

Opt-in per arm: an arm without it is priced exactly as before. With it, a side's stop triggers off
the spread's NBBO ask (short ask less long bid), as the broker's does, and fills at one of two
declared bounds -- the trigger, or the tick's ask -- unless the ask is already past the order's
limit, where it pays that ask, as the loop chasing it would.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_paper_engine import MODERATE, _params  # noqa: E402

from cherrypick.meic import paper  # noqa: E402

TRADE = {
    "put_symbol": "SP",
    "call_symbol": "SC",
    "long_put_symbol": "LP",
    "long_call_symbol": "LC",
    "net_credit": 1.80,
    "status": "open",
    "put_credit": 0.80,
    "call_credit": 1.00,
    "stop_trigger_current": 2.0,  # a $3.60 stop on each side
    "wing_width": 10,
}
CALLS = {"SC": {"bid": 0.05, "ask": 0.10, "mid": 0.075}, "LC": {"bid": 0.0, "ask": 0.05, "mid": 0.025}}


def _quotes(sp_bid, sp_ask, lp_bid, lp_ask):
    return {
        "SP": {"bid": sp_bid, "ask": sp_ask, "mid": (sp_bid + sp_ask) / 2},
        "LP": {"bid": lp_bid, "ask": lp_ask, "mid": (lp_bid + lp_ask) / 2},
        **CALLS,
    }


# The put spread's ask is 4.00 - 0.30 = 3.70, past the $3.60 trigger, while its mid plus the
# 0.125 haircut is 3.45 + 0.0625 = 3.51, short of it: the broker's stop fires, the loop's does not.
CROSSED_AT_THE_ASK = _quotes(3.60, 4.00, 0.30, 0.40)


def _decide(quotes, **arm):
    return paper.evaluate_open_trade(TRADE, quotes, {**_params(MODERATE), **arm}, force_close=False)


def test_an_arm_without_a_resting_stop_is_priced_as_before():
    assert _decide(CROSSED_AT_THE_ASK)["action"] == "hold"


def test_a_resting_stop_triggers_off_the_spreads_ask():
    d = _decide(CROSSED_AT_THE_ASK, stop_resting=True)
    assert d["action"] == "stop_put"
    assert d["put_exit_price"] == pytest.approx(3.70), "the conservative bound: the tick's ask"
    assert d["put_cost_now"] == pytest.approx(3.5125), "the mark stays the loop's own"


def test_the_trigger_bound_fills_at_the_stop():
    d = _decide(CROSSED_AT_THE_ASK, stop_resting=True, stop_resting_fill="trigger")
    assert d["action"] == "stop_put" and d["put_exit_price"] == pytest.approx(3.60)


def test_a_stop_the_market_ran_through_pays_the_ask_in_either_bound():
    """The limit is 3.60 + 0.36 = 3.96; an ask of 5.20 is past it."""
    gapped = _quotes(5.30, 5.60, 0.40, 0.50)
    for fill in ("tick", "trigger"):
        d = _decide(gapped, stop_resting=True, stop_resting_fill=fill)
        assert d["action"] == "stop_put" and d["put_exit_price"] == pytest.approx(5.20)


def test_a_resting_stop_holds_below_its_trigger():
    assert _decide(_quotes(2.90, 3.10, 0.30, 0.40), stop_resting=True)["action"] == "hold"


def test_a_resting_stop_never_fires_with_stops_off():
    d = _decide(CROSSED_AT_THE_ASK, stop_resting=True, per_side_stop_management=False)
    assert d["action"] == "hold"


def test_the_limit_is_capped_at_the_wing():
    """A trigger near the wing leaves no room above it: an ask at the wing is still a fill, never
    'ran through'."""
    trade = {**TRADE, "stop_trigger_current": 5.4}  # 9.72 on 1.80; uncapped, its limit would be 10.69
    arm = {**_params(MODERATE), "stop_resting": True, "stop_resting_fill": "trigger"}
    d = paper.evaluate_open_trade(trade, _quotes(9.80, 9.95, 0.10, 0.20), arm, force_close=False)
    assert d["action"] == "stop_put" and d["put_exit_price"] == pytest.approx(9.72), "inside the 10.00 cap"
    # An ask of 10.20 is past the 10-wide cap: the order could not have filled, so it pays the ask,
    # where an uncapped 10.69 limit would have filled it at the trigger.
    d = paper.evaluate_open_trade(trade, _quotes(10.10, 10.30, 0.10, 0.20), arm, force_close=False)
    assert d["action"] == "stop_put" and d["put_exit_price"] == pytest.approx(10.20)
