"""One firing rule. The same recorded days, read three ways, give the same entry minute:

- a signal study: `core.signals.firings` over every session's bars at once (as
  `packages/backtest/scripts/signal_study.py` reads the recorder);
- core's trigger: `core.entrychecks.entry_due` over the whole day;
- the engine as paper and the backtest tick it: `engine.trigger_due` minute by minute, on the snapshot
  each tick would build (earlier sessions' trail plus today's up to the minute).

A seeded indicator, a cross that carries its arming across the close, a gap and a prior-session break
all read the earlier sessions; the engine agrees only because the snapshot carries them."""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime

import pytest
from cherrypick.core import entrychecks
from cherrypick.core import signals as sg
from market import spx_snapshot

from cherrypick.ensemble import arms, engine
from cherrypick.ensemble.engine import ET

DAYS = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16"]
TODAY = DAYS[-1]
WIDTH = 5
WINDOW = {"open": "09:30", "close": "15:45"}


def trail(day_index: int) -> list[tuple[int, float]]:
    """A deterministic wandering spot for one session, minute by minute, 09:30 to 16:00."""
    base = 6000.0 + 12.0 * day_index
    return [
        (m, round(base + 9.0 * math.sin((m + 37 * day_index) / 23.0) + 4.0 * math.sin(m / 7.0), 2))
        for m in range(570, 961)
    ]


TRAILS = {d: trail(i) for i, d in enumerate(DAYS)}

SIGNALS = {
    "seeded rsi cross": {
        "kind": "cross",
        "a": {"name": "rsi", "n": 14},
        "b": 55.0,
        "direction": "above",
        "seed": True,
    },
    "unseeded rsi cross": {
        "kind": "cross",
        "a": {"name": "rsi", "n": 14},
        "b": 55.0,
        "direction": "above",
        "seed": False,
    },
    "seeded close cross": {
        "kind": "cross",
        "a": {"name": "close"},
        "b": 6050.0,
        "direction": "above",
        "seed": True,
    },
    "gap up": {"kind": "gap", "direction": "up", "pct": 0.05},
    "prior session high": {"kind": "prior_session", "side": "high"},
}


def study_first(spec) -> int | None:
    """The signal study's first firing today inside the window."""
    bars = [b for d in DAYS for b in sg.from_trail(d, TRAILS[d], WIDTH)]
    lo, hi = 570, 945
    return next(
        (f.minute for f in sg.firings(spec, bars) if f.session == TODAY and lo <= f.minute <= hi), None
    )


def engine_first(trigger, *, prior=True) -> tuple[int | None, int | None]:
    """(the first minute the engine says due, the firing minute it reports), ticking each minute."""
    entry = {"kind": "trigger", "trigger": trigger, "bar_minutes": WIDTH}
    for minute in range(571, 946):
        snap = spx_snapshot(session=TODAY, minute=minute, tr=[p for p in TRAILS[TODAY] if p[0] <= minute])
        if prior:
            snap = dataclasses.replace(snap, prior={d: TRAILS[d] for d in DAYS[:-1]})
        due, fired = engine.trigger_due(entry, snap)
        if due:
            return minute, fired
    return None, None


@pytest.mark.parametrize("name", list(SIGNALS))
def test_study_core_and_the_engine_agree_on_the_entry_minute(name):
    trigger = {"signal": SIGNALS[name], "window": WINDOW, "within": None}
    expected = study_first(SIGNALS[name])
    assert expected is not None, f"{name}: the fixture should fire today"
    bars = [b for d in DAYS for b in sg.from_trail(d, TRAILS[d], WIDTH)]
    whole_day = entrychecks.entry_due(
        trigger, now=datetime.fromisoformat(TODAY).replace(hour=15, minute=45, tzinfo=ET), bars=bars
    )
    assert whole_day.fired == expected
    due_at, fired = engine_first(trigger)
    assert (due_at, fired) == (expected, expected), name


def test_without_the_earlier_sessions_a_seeded_signal_would_disagree():
    """The reason the snapshot carries them: today's bars alone leave a seeded indicator cold."""
    spec = SIGNALS["seeded rsi cross"]
    trigger = {"signal": spec, "window": WINDOW, "within": None}
    assert engine_first(trigger, prior=False)[1] != study_first(spec)


def test_an_arm_with_an_intraday_signal_reads_the_prior_trail_and_a_daily_one_does_not():
    entry = {
        "kind": "trigger",
        "trigger": {"signal": SIGNALS["gap up"], "window": WINDOW, "within": None},
        "bar_minutes": 5,
    }
    arm = {"entry": entry, "exit": {}}
    assert arms.reads_prior_trail(arm)
    daily = {**entry, "trigger": {**entry["trigger"], "daily": True}}
    daily.pop("bar_minutes")
    assert not arms.reads_prior_trail({"entry": daily, "exit": {}})
    assert not arms.reads_prior_trail({"entry": {"kind": "time", "at": "10:00"}, "exit": {}})


def test_paper_reads_the_earlier_sessions_from_its_recorded_trail(tmp_path):
    """The feed gives the engine the trail the loop recorded on up to PRIOR_SESSIONS trading days before
    today; a day the loop did not run is absent, never filled in."""
    from datetime import date

    from cherrypick.ensemble import db, feed

    conn = db.connect(str(tmp_path / "paper.db"))
    for d in ("2026-11-12", "2026-11-13", "2026-11-16"):  # 11-11 not recorded; 11-16 is today
        for m, s in TRAILS[d][:3]:
            db.record_spot(conn, "SPX", d, m, s)
    got = feed.prior_trails(conn, "SPX", date(2026, 11, 16))
    assert sorted(got) == ["2026-11-12", "2026-11-13"]
    assert got["2026-11-13"] == TRAILS["2026-11-13"][:3]
    conn.close()
