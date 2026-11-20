"""A store records a band of strikes around the spot; on a day the market runs, a condor's far wing can
leave it while the position is open. Such a leg is read at both ends of what it can be worth (0 to the
ask of the farthest strike held on its side), an exit fires only when it fires on both, and the close
fills at the costly end. Each case is paired with the reading that must not fire (working-rules)."""

from __future__ import annotations

import pytest
from market import spx_snapshot

from cherrypick.automations import arms, engine
from cherrypick.automations.model import Chain

CONDOR = {
    "name": "condor-stop",
    "enabled": True,
    "symbol": "SPX",
    "expiry": {"kind": "dte", "days": 0},
    "legs": [
        {
            "name": "short_put",
            "right": "P",
            "side": -1,
            "strike": {"rule": "delta", "target": 0.16, "match": "nearest"},
        },
        {
            "name": "short_call",
            "right": "C",
            "side": -1,
            "strike": {"rule": "delta", "target": 0.16, "match": "nearest"},
        },
        {
            "name": "long_put",
            "right": "P",
            "side": 1,
            "strike": {"rule": "from_leg", "leg": "short_put", "points": -10},
        },
        {
            "name": "long_call",
            "right": "C",
            "side": 1,
            "strike": {"rule": "from_leg", "leg": "short_call", "points": 10},
        },
    ],
    "entry": {"kind": "time", "at": "10:00", "window": 5},
    "gates": [],
    "exit": {"stop_credit_multiple": 1},
    "size": {"contracts": 1},
    "skip_events": {"labels": [], "through_expiry": False},
    "limits": {"max_open": 1, "per_day": 1},
    "weekdays": [0, 1, 2, 3, 4],
}


def opened(**exit_rule):
    arm = arms.load_arms({"condor-stop": {**CONDOR, "exit": exit_rule or CONDOR["exit"]}})["condor-stop"]
    d = engine.decide(arm, spx_snapshot())
    assert d.enter, d.checks
    return arm, engine.Position(d.entry, arm)


def strikes(pos):
    return {leg.name: leg.strike for leg in pos.entry.legs}


def fallen(pos, spot, *, drop=()):
    """The market at `spot`, its band of strikes ending just below the short call, so both call legs
    lie beyond it; `drop` takes further legs out of the chain."""
    k = strikes(pos)
    snap = spx_snapshot(minute=700, spot=spot, width=k["short_call"] - 5 - spot)
    chain = snap.chains[snap.session]
    gone = {(k[n], "C" if "call" in n else "P") for n in drop}
    snap.chains[snap.session] = Chain(
        chain.expiry, [r for r in chain.rows if (float(r["strike"]), r["right"]) not in gone]
    )
    return snap


def test_the_far_wing_leaves_the_band_and_the_stop_still_fires():
    _, pos = opened()
    snap = fallen(pos, strikes(pos)["short_put"] - 25)
    chain = snap.chains[snap.session]
    assert chain.get(strikes(pos)["short_call"], "C") is None  # the far wing is out of the band
    far = engine._beyond_band(pos.entry.legs, chain)
    assert set(far) == {(strikes(pos)["short_call"], "C"), (strikes(pos)["long_call"], "C")}
    assert engine.manage(pos, snap)[0] == "stop"
    trade = engine.close_position(pos, "stop", snap)
    assert trade is not None
    # the sold far call bought back at its cap, the bought one sold for 0: the costly end
    cap = far[(strikes(pos)["short_call"], "C")]
    put_side = engine._credit_now(pos, engine._Bounded(chain, pos.entry.legs, far, low=True))
    assert trade["exit_mid"] == pytest.approx(put_side, abs=1e-9)
    assert cap >= 0


def test_a_leg_missing_inside_the_band_is_still_no_reading():
    _, pos = opened()
    snap = fallen(pos, strikes(pos)["short_put"] - 25, drop=["long_put"])
    assert engine._beyond_band(pos.entry.legs, snap.chains[snap.session]) is None
    assert engine.manage(pos, snap) is None
    assert engine.close_position(pos, "stop", snap) is None


def _pnls(pos, snap):
    chain = snap.chains[snap.session]
    far = engine._beyond_band(pos.entry.legs, chain)
    out = []
    for low in (True, False):
        now = engine._credit_now(pos, engine._Bounded(chain, pos.entry.legs, far, low=low))
        out.append((pos.entry.prices["mid"] + now) * 100 * pos.entry.quantity)
    return out


def test_an_exit_fires_only_when_both_ends_of_the_range_fire_it():
    _, probe = opened()
    snap = fallen(probe, strikes(probe)["short_put"] - 25)
    low, high = _pnls(probe, snap)
    assert low < high < 0  # the far wing's range moves the reading
    between = -(low + high) / 2  # a stop only the lower reading reaches
    _, pos = opened(stop_dollars=round(between, 2))
    assert engine.manage(pos, snap) is None
    _, pos = opened(stop_dollars=round(-high - 1, 2))  # one both readings reach
    assert engine.manage(pos, snap)[0] == "stop"
