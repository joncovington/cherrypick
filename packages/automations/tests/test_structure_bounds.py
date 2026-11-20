"""A mid the structure cannot be worth is a quote that cannot fill, never a price: no entry takes it, no
exit reads it, and no close fills at it. Each refusal is paired with the same market made sane, so the
bound is shown to be the reason (working-rules)."""

from __future__ import annotations

import pytest
from market import spx_snapshot

from cherrypick.automations import arms, engine, pricing
from cherrypick.automations.model import Leg

A = arms.load_arms()


def fly_legs(k=6000.0, w=10.0):
    return [
        Leg("short_put", "P", -1, k),
        Leg("short_call", "C", -1, k),
        Leg("long_put", "P", 1, k - w),
        Leg("long_call", "C", 1, k + w),
    ]


def test_the_bounds_are_the_payoff_range():
    assert pricing.value_bounds(fly_legs()) == (-10.0, 0.0)
    assert pricing.value_bounds([Leg("long_call", "C", 1, 100.0)]) == (0.0, None)
    assert pricing.value_bounds([Leg("short_call", "C", -1, 100.0)]) == (None, 0.0)
    assert pricing.value_bounds([Leg("long_put", "P", 1, 100.0)]) == (0.0, 100.0)
    assert pricing.within_bounds(fly_legs(), -10.0) and pricing.within_bounds(fly_legs(), -10.004)
    assert not pricing.within_bounds(fly_legs(), -10.05)
    assert not pricing.within_bounds(fly_legs(), 0.8)


def _shift(snap, legs, by):
    """Move each sold leg's quote by `by` a share, so the structure's mid moves by 2x that."""
    for leg in legs:
        if leg.side < 0:
            q = snap.chains[snap.session].get(leg.strike, leg.right)
            q["bid"] += by
            q["ask"] += by


def test_an_entry_on_a_credit_above_the_width_is_refused():
    arm = A["iron-fly-1000"]
    sane = engine.decide(arm, spx_snapshot())
    assert sane.enter, sane.checks
    credit = sane.entry.prices["mid"]
    bad = spx_snapshot()
    _shift(bad, sane.entry.legs, (10.5 - credit) / 2)
    d = engine.decide(arm, bad)
    assert d.reason == "unrealistic_price"
    assert d.checks[-1][2] == pytest.approx(10.5)


def test_a_close_past_the_bounds_is_held_and_no_exit_reads_it():
    arm = dict(A["iron-fly-1000"], exit={"stop_dollars": 1})
    entry = engine.decide(arm, spx_snapshot()).entry
    pos = engine.Position(entry, arm)
    later = spx_snapshot(minute=700)
    held = engine._credit_now(pos, later.chains[later.session])
    assert held is not None
    assert engine.close_position(pos, "time", later) is not None
    _shift(later, entry.legs, (-10.5 - held) / -2)
    assert engine._credit_now(pos, later.chains[later.session]) is None
    assert engine.manage(pos, later) is None
    assert engine.close_position(pos, "time", later) is None
