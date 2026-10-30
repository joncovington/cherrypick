"""Hypothesis 27's study (scripts/share_ladder_item27.py): each arm's change to item 26's once-3,
the measures and the verdict. Invented bars only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("share_ladder_item27", SCRIPTS / "share_ladder_item27.py")
s27 = importlib.util.module_from_spec(_spec)
sys.modules["share_ladder_item27"] = s27
_spec.loader.exec_module(s27)
sl = s27.sl
B = sl.Bar


def _bars(ohlc, start=date(2026, 1, 1), step=1):
    return [B((start + timedelta(days=step * i)).isoformat(), *x) for i, x in enumerate(ohlc)]


def _walk(n, seed=7):
    rng, px, out = random.Random(seed), 100.0, []
    for _ in range(n):
        o = px * (1 + rng.gauss(0, 0.03))
        c = o * (1 + rng.gauss(0, 0.04))
        out.append(
            (o, max(o, c) * (1 + abs(rng.gauss(0, 0.02))), min(o, c) * (1 - abs(rng.gauss(0, 0.03))), c)
        )
        px = c
    return _bars(out)


def test_the_base_reproduces_item_26s_once_3():
    bars = _walk(800)
    s, e = bars[1].start, bars[-1].start
    ours = s27.simulate(bars, s, e, "once-3")
    theirs = sl.simulate(bars, s, e, "once", 3)
    assert ours.opened == theirs.opened and ours.closed == theirs.closed
    assert ours.pnl == pytest.approx(theirs.pnl)
    assert sorted(ours.holds) == sorted(theirs.holds)


def test_decay_target_steps_down_with_age():
    assert s27.target(100, 30, decay=True, cost=0.0) == pytest.approx(104)
    assert s27.target(100, 31, decay=True, cost=0.0) == pytest.approx(100)
    assert s27.target(100, 121, decay=True, cost=0.0) == pytest.approx(95)
    assert s27.target(100, 500, decay=False, cost=0.0) == pytest.approx(104)


def _stuck(days_flat: int, recover_to: float):
    """A lot bought at 93 on day 1, the price flat at 96 (under 96.72) for `days_flat` days, then one
    session reaching `recover_to`."""
    return _bars(
        [(100, 100, 100, 100), (95, 95, 93, 94)]
        + [(96, 96.5, 95.5, 96)] * days_flat
        + [(96, recover_to, 95, 96)]
    )


def test_decay_sells_a_stuck_lot_at_breakeven_where_the_base_holds_it():
    bars = _stuck(40, 99.0)[:-1]  # never reaches 96.72; does pass 93 after day 30
    s, e = bars[1].start, bars[-1].start
    assert s27.simulate(bars, s, e, "once-3", cost=0.0).closed == 0
    run = s27.simulate(bars, s, e, "decay", cost=0.0)
    assert run.closed == 1 and run.holds == [31]


def test_time_stop_sells_at_the_open_after_120_days_and_not_before():
    bars = _stuck(130, 96.0)
    s, e = bars[1].start, bars[-1].start
    run = s27.simulate(bars, s, e, "time-120", cost=0.0)
    assert run.closed == 1 and run.holds == [121]
    early = s27.simulate(bars[:100], s, bars[99].start, "time-120", cost=0.0)
    assert early.closed == 0


def test_averaging_down_sells_the_whole_position_at_its_average_cost_plus_four():
    bars = _bars(
        [
            (100, 100, 100, 100),
            (95, 95, 93, 90),  # lot 1 at 93
            (90, 90, 86.0, 87),  # lot 2 at 93 x 0.93 = 86.49
            (87, 93.5, 87, 93),  # average 89.75 -> target 93.34: sells both
        ]
    )
    run = s27.simulate(bars, bars[1].start, bars[-1].start, "avg-5", cost=0.0)
    assert run.opened == 2 and run.closed == 2 and run.open_at_end == 0
    base = s27.simulate(bars, bars[1].start, bars[-1].start, "once-3", cost=0.0)
    assert base.closed == 0  # each lot's own +4% (96.72, 89.95 then 92.84 ...) — lot 1 still open
    assert base.open_at_end >= 1


def test_trend_refuses_entries_under_the_counterparts_200_session_average():
    bars = _bars([(100, 100, 100, 100)] * 210 + [(95, 95, 93, 94)])
    rising = _bars([(10 + i * 0.1,) * 4 for i in range(211)])
    falling = _bars([(50 - i * 0.1,) * 4 for i in range(211)])
    s, e = bars[-1].start, bars[-1].start
    assert s27.simulate(bars, s, e, "trend", under=rising).opened == 1
    assert s27.simulate(bars, s, e, "trend", under=falling).opened == 0
    assert s27.simulate(bars, s, e, "trend", under=rising[:100]).opened == 0  # too short: no entry


def test_confirm_buys_after_the_first_up_close_and_lapses_after_five():
    up = _bars([(100, 100, 100, 100), (95, 95, 92, 92), (92, 93, 91, 91), (91, 94, 91, 93), (94, 95, 93, 94)])
    run = s27.simulate(up, up[1].start, up[-1].start, "confirm", cost=0.0)
    assert run.opened == 1  # up close on day 4 (93 > 91), bought at day 5's open, 94
    down = _bars(
        [(100, 100, 100, 100), (95, 95, 92, 92)]
        + [(92 - i, 92 - i, 91 - i, 91 - i) for i in range(6)]
        + [(86, 90, 86, 89), (89, 89, 89, 89)]
    )
    assert s27.simulate(down, down[1].start, down[-1].start, "confirm", cost=0.0).opened == 0


def _cell(longest, pct=10.0, dd=50.0, closed=40):
    return {"longest_days": longest, "pct_year": pct, "max_drawdown_pct": dd, "closed": closed}


def _names(n, arm_longest, arm_pct=10.0, holdout_longest=None):
    out = {}
    for i in range(n):
        base = {w: _cell(1000 + 50 * i) for w in ("design", "half1", "half2", "holdout", "stress")}
        a = {w: _cell(arm_longest + 10 * (i % 3), arm_pct) for w in ("design", "half1", "half2", "stress")}
        a["holdout"] = _cell(holdout_longest or arm_longest, arm_pct)
        out[f"N{i}"] = {"once-3": base} | {arm: a for arm in s27.ARMS}
    return out


def test_verdict_needs_shorter_holds_without_giving_up_return():
    assert all(v["verdict"] == "pass" for v in s27.judge(_names(10, 300)).values())
    assert all(v["verdict"] == "fail" for v in s27.judge(_names(10, 300, arm_pct=5.0)).values())  # -5 pts
    assert all(v["verdict"] == "fail" for v in s27.judge(_names(10, 1500)).values())  # longer
    assert all(v["verdict"] == "fail" for v in s27.judge(_names(10, 300, holdout_longest=5000)).values())
    assert all(v["verdict"] == "no verdict" for v in s27.judge(_names(5, 300)).values())
