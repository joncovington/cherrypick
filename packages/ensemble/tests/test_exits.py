"""The exits in dollars and % of debit, the trailing stop, avoid events and the wide-spread guard, on
the iron fly at 10:00 (an 8.74 credit). The test market holds time to expiry fixed, so the open P&L is
moved by spot and by `expiries` (hours left): at 6000 the fly is worth about +171 an hour out and +274
half an hour out; 5990 half an hour out is about +118."""

from __future__ import annotations

import copy
import dataclasses

import pytest
from market import spx_snapshot

from cherrypick.ensemble import arms, engine, events

A = arms.load_arms()
HOUR = 1.0 / (24 * 365)


def held(exit_rules, *, debit=False):
    arm = copy.deepcopy(A["iron-fly-1000"])
    arm["exit"] = exit_rules
    assert not arms.validate(arm), arms.validate(arm)
    d = engine.decide(arm, spx_snapshot(minute=600))
    assert d.enter, d.checks
    e = d.entry
    if debit:  # the same legs bought: a long iron fly for an 8.74 debit
        e = dataclasses.replace(
            e,
            legs=[dataclasses.replace(g, side=-g.side) for g in e.legs],
            prices={k: -v for k, v in e.prices.items()},
        )
    return engine.Position(e, arm)


def at(pos, *, hours=6.0, spot=6000.0, minute=630, **kw):
    return spx_snapshot(minute=minute, spot=spot, expiries={pos.entry.expiry: hours * HOUR}, **kw)


def test_dollar_targets_read_the_open_pnl_of_the_whole_position():
    pos = held({"stop_dollars": 60, "profit_dollars": 150})
    assert engine.manage(pos, at(pos)) is None
    assert engine.manage(pos, at(pos, spot=5960.0)) == ("stop", -70.0)
    assert engine.manage(pos, at(pos, hours=2)) is None  # +89
    assert engine.manage(pos, at(pos, hours=1)) == ("profit_target", 171.0)


def test_a_debit_stop_is_a_share_of_the_debit_and_never_fires_on_a_credit():
    long_fly = held({"stop_debit_pct": 20}, debit=True)  # 20% of 8.74 = a 1.75 loss
    assert engine.manage(long_fly, at(long_fly, hours=1)) is None  # a 1.71 loss, just short
    kind, loss = engine.manage(long_fly, at(long_fly, hours=0.5))  # 2.74
    assert kind == "stop" and loss >= 0.2 * 8.74 - 1e-9
    short_fly = held({"stop_debit_pct": 1})
    assert engine.manage(short_fly, at(short_fly, spot=5940.0)) is None
    # The credit multiple is the credit's stop and stays silent on a debit.
    long_cm = held({"stop_credit_multiple": 0.01}, debit=True)
    assert engine.manage(long_cm, at(long_cm, hours=0.5)) is None


def test_a_trailing_stop_follows_the_best_pnl_once_it_has_reached_its_trigger():
    pos = held({"trailing_stop": {"dollars": 50, "activate_dollars": 100}})
    assert engine.manage(pos, at(pos, hours=1)) is None and pos.peak_pnl == 171.0
    assert engine.manage(pos, at(pos, hours=1, spot=5995.0)) is None  # +150, 21 off the best
    assert engine.manage(pos, at(pos, hours=0.5, spot=5990.0)) == ("trailing_stop", 118.0)
    # Never reached +200: no trail yet, however far it falls.
    unarmed = held({"trailing_stop": {"dollars": 50, "activate_dollars": 200}})
    assert engine.manage(unarmed, at(unarmed, hours=1)) is None
    assert engine.manage(unarmed, at(unarmed, hours=0.5, spot=5990.0)) is None


def test_a_wide_spread_holds_every_exit_and_leaves_the_peak_where_it_was():
    pos = held({"max_spread": 1.0, "stop_dollars": 60, "trailing_stop": {"dollars": 40}})
    assert engine.manage(pos, at(pos, spot=5960.0, half_spread=0.5)) is None  # 4.00 wide: held
    assert engine.manage(pos, at(pos, spot=5960.0)) == ("stop", -70.0)  # 0.80 wide
    trail = held({"max_spread": 1.0, "trailing_stop": {"dollars": 40}})
    assert engine.manage(trail, at(trail, hours=1)) is None and trail.peak_pnl == 171.0
    assert engine.manage(trail, at(trail, hours=0.5, half_spread=0.5)) is None
    assert trail.peak_pnl == 171.0, "a mark read through a wide spread is no peak"
    # +228 is 46 under a peak the wide quote would have set (274), and above the true one.
    assert engine.manage(trail, at(trail, hours=0.5, spot=5995.0)) is None


FOMC_DAY = "2026-10-28"
EVE = "2026-10-27"


def on(pos, session, minute):
    expiries = {pos.entry.expiry: 6.0 * HOUR}
    snap = spx_snapshot(session=session, minute=minute, expiries=expiries)
    return dataclasses.replace(snap, events=events.EventCalendar())


def test_avoid_events_closes_the_session_before_the_event_at_its_time():
    pos = held({"avoid_events": {"labels": ["FOMC"], "before_close_min": 60}})
    pos.entry = dataclasses.replace(pos.entry, session="2026-10-26", expiry="2026-10-30")
    assert engine.manage(pos, on(pos, "2026-10-26", 900)) is None
    assert engine.manage(pos, on(pos, EVE, 899)) is None
    assert engine.manage(pos, on(pos, EVE, 900)) == ("event", "FOMC")
    # Carried into the event day (the close the day before was missed): out at the first tick.
    assert engine.manage(pos, on(pos, FOMC_DAY, 571)) == ("event", "FOMC")
    # Entered on the event day itself, it is not closed for it.
    today = held({"avoid_events": {"labels": ["FOMC"]}})
    today.entry = dataclasses.replace(today.entry, session=FOMC_DAY, expiry="2026-10-30")
    assert engine.manage(today, on(today, FOMC_DAY, 950)) is None
    # A calendar that cannot answer holds.
    blind = dataclasses.replace(on(pos, EVE, 950), events=None)
    assert engine.manage(pos, blind) is None


@pytest.mark.parametrize(
    ("exit_rules", "message"),
    [
        ({"stop_debit_pct": 0}, "exit.stop_debit_pct"),
        ({"stop_debit_pct": 150}, "exit.stop_debit_pct"),
        ({"stop_dollars": -5}, "exit.stop_dollars"),
        ({"profit_dollars": True}, "exit.profit_dollars"),
        ({"max_spread": 0}, "exit.max_spread"),
        ({"trailing_stop": {"dollars": 0}}, "exit.trailing_stop"),
        ({"trailing_stop": {"dollars": 50, "percent": 5}}, "exit.trailing_stop"),
        ({"avoid_events": {"labels": []}}, "exit.avoid_events"),
        ({"avoid_events": {"labels": ["FOMC"], "before_close_min": 500}}, "exit.avoid_events"),
        ({"avoid_events": {"labels": ["FOMC"], "at": "15:00"}}, "exit.avoid_events"),
    ],
)
def test_a_bad_new_exit_value_is_refused(exit_rules, message):
    arm = copy.deepcopy(A["iron-fly-1000"])
    arm["exit"] = exit_rules
    assert any(message in x for x in arms.validate(arm)), arms.validate(arm)
