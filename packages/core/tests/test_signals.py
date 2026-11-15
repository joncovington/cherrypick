"""core.signals: each kind fires on a series built for it and never on its mirror, the event and state
forms agree, no firing reads a later bar, and each firing rule holds on a series built to break it."""

from __future__ import annotations

import random

import pytest

from cherrypick.core import signals as sg
from cherrypick.core.signals import Bar

DAY1, DAY2, DAY3 = "2026-01-05", "2026-01-06", "2026-01-07"
CLOSE = {"name": "close"}


def bars(closes, session=DAY1, size=5, opens=None):
    """5-minute bars from 09:30; each opens at the previous close (or `opens`), with the bar's range
    the span of its open and close."""
    out = []
    prev = closes[0] if opens is None else opens
    for i, c in enumerate(closes):
        o = prev if i else (closes[0] if opens is None else opens)
        out.append(Bar(session, 570 + i * size, 570 + (i + 1) * size, o, max(o, c), min(o, c), c))
        prev = c
    return out


def mirror(bs, about=100.0):
    return [
        Bar(
            b.session,
            b.start,
            b.end,
            2 * about - b.open,
            2 * about - b.low,
            2 * about - b.high,
            2 * about - b.close,
        )
        for b in bs
    ]


def fired(spec, bs):
    return [f.index for f in sg.firings(spec, bs)]


def cross(level, direction="above", **rules):
    return {"kind": "cross", "a": CLOSE, "b": level, "direction": direction, "seed": True, **rules}


# --- bars ---------------------------------------------------------------------------------------------


def test_from_trail_returns_only_completed_bars_inside_the_session():
    trail = [(560, 1.0), (570, 10.0), (572, 12.0), (574, 9.0), (575, 11.0), (581, 13.0), (960, 99.0)]
    out = sg.from_trail(DAY1, trail, 5)
    assert [(b.start, b.open, b.high, b.low, b.close) for b in out] == [
        (570, 10.0, 12.0, 9.0, 9.0),
        (575, 11.0, 11.0, 11.0, 11.0),
        (580, 13.0, 13.0, 13.0, 13.0),
    ]
    # At 10:44 the 10:40 bar is still forming, so it is not a bar yet.
    assert [b.start for b in sg.from_trail(DAY1, trail, 5, until=584)] == [570, 575]


def test_daily_bars_sort_by_session():
    rows = [
        {"session": DAY2, "open": 2, "high": 3, "low": 1, "close": 2},
        {"session": DAY1, "open": 1, "high": 1, "low": 1, "close": 1},
    ]
    assert [b.session for b in sg.daily(rows)] == [DAY1, DAY2]


# --- kinds --------------------------------------------------------------------------------------------


def test_a_level_cross_fires_where_it_crosses_and_never_on_its_mirror():
    bs = bars([100, 101, 103, 106, 104, 107])  # up through 105, back under, up through again
    assert fired(cross(105), bs) == [3, 5]
    assert fired(cross(105), mirror(bs)) == []
    assert fired(cross(95, "below"), mirror(bs)) == [3, 5]


def test_a_series_already_past_the_level_when_defined_has_not_crossed():
    assert fired(cross(105), bars([110, 111, 112])) == []


def test_a_line_cross_fires_once_the_fast_line_passes_the_slow():
    closes = [110, 108, 106, 104, 102, 100, 102, 105, 109, 114]
    spec = {
        "kind": "cross",
        "a": {"name": "ema", "n": 2},
        "b": {"name": "ema", "n": 4},
        "direction": "above",
        "seed": True,
    }
    bs = bars(closes)
    got = fired(spec, bs)
    assert len(got) == 1 and got[0] >= 6
    assert fired(spec, mirror(bs)) == []


def test_a_range_fires_on_entering_and_on_leaving():
    bs = bars([90, 95, 101, 103, 99, 112, 115])
    enters = {"kind": "range", "a": CLOSE, "low": 100, "high": 110, "direction": "enters", "seed": True}
    assert fired(enters, bs) == [2]
    assert fired({**enters, "direction": "exits"}, bs) == [4]


def test_a_gap_fires_on_the_first_bar_of_the_session_and_not_on_the_mirror():
    bs = bars([100, 100, 100]) + bars([102, 103], session=DAY2, opens=101.5)
    spec = {"kind": "gap", "direction": "up", "pct": 1.0}
    assert fired(spec, bs) == [3]
    assert fired(spec, mirror(bs)) == []
    assert fired({**spec, "direction": "down"}, mirror(bs)) == [3]


def test_a_gap_fill_fires_when_price_trades_back_to_the_prior_close():
    bs = bars([100, 100]) + bars([101, 100.5, 99.8, 101], session=DAY2, opens=101.5)
    assert fired({"kind": "gap_fill", "min_pct": 1.0}, bs) == [4]
    assert fired({"kind": "gap_fill", "min_pct": 2.0}, bs) == []


def test_a_change_from_the_open_fires_once_it_reaches_the_percent():
    bs = bars([100, 100.4, 100.6, 100.2, 100.7], opens=100)
    spec = {"kind": "change", "from": "open", "direction": "up", "pct": 0.5}
    assert fired(spec, bs) == [2, 4]  # back under 0.5% at bar 3 re-arms it
    assert fired({**spec, "first_of_day": True}, bs) == [2]
    assert fired(spec, mirror(bs)) == []


def test_an_opening_range_break_waits_for_the_range_to_finish():
    # Six 5-minute bars make the 30-minute range 99..101; bar 4 dips under it but the range is not done.
    closes = [100, 101, 99.5, 100, 98, 100.5, 100, 98.5, 97]
    bs = bars(closes, opens=100)
    spec = {"kind": "opening_range", "minutes": 30, "side": "below"}
    lo = min(b.low for b in bs[:6])
    first = next(i for i in range(6, len(bs)) if bs[i].close < lo)
    assert fired(spec, bs) == [first]
    assert fired({**spec, "minutes": 32}, bs) == []  # 5-minute bars cannot make a 32-minute range


def test_a_prior_session_high_break_needs_the_prior_session():
    bs = bars([100, 102, 101]) + bars([101, 102.5, 103], session=DAY2, opens=101)
    spec = {"kind": "prior_session", "side": "high"}
    assert fired(spec, bs) == [4]
    assert fired(spec, bs[3:]) == []


def test_new_highs_in_a_row_fire_once_and_again_after_a_pause():
    bs = bars([100, 101, 102, 103, 102.5, 104])
    spec = {"kind": "new_extreme", "side": "high", "lookback": "session"}
    assert fired(spec, bs) == [1, 5]
    assert fired({**spec, "side": "low"}, bs) == []


def test_consecutive_closes():
    bs = bars([100, 99, 100, 101, 102, 101, 102])
    spec = {"kind": "consecutive", "n": 3, "direction": "up", "seed": True}
    assert fired(spec, bs) == [4]
    assert fired(spec, mirror(bs)) == []


# --- firing rules ------------------------------------------------------------------------------------


def test_confirmation_needs_the_state_to_hold_for_that_many_bars():
    poke = bars([100, 106, 104, 104])
    held = bars([100, 106, 107, 104])
    assert fired(cross(105), poke) == [1]
    assert fired(cross(105, confirm=2), poke) == []
    assert fired(cross(105, confirm=2), held) == [2]


def test_reset_rearms_only_past_the_distance():
    bs = bars([100, 106, 104, 106, 99, 106])
    assert fired(cross(105), bs) == [1, 3, 5]
    assert fired(cross(105, reset=3), bs) == [1, 5]


def test_first_of_day_and_latest():
    bs = bars([100, 106, 104, 106, 104, 106])
    assert fired(cross(105, first_of_day=True), bs) == [1]
    assert fired(cross(105, latest="09:45"), bs) == [1]  # bar 1 closes 09:40, bar 3 at 09:50
    assert fired(cross(105, latest="09:35"), bs) == []


def test_only_when_filters_a_firing_without_counting_it():
    bs = bars([100, 106, 104, 106])
    gate = {"kind": "new_extreme", "side": "high", "lookback": "session"}
    # Bar 1 is a new high, bar 3 equals it (not a new high): only bar 1 survives the filter.
    assert fired(cross(105, only_when=[gate]), bs) == [1]
    never = {"kind": "change", "from": "open", "direction": "down", "pct": 50.0}
    assert fired(cross(105, only_when=[never], first_of_day=True), bs) == []


def test_seed_false_starts_every_session_cold():
    bs = bars([100, 101, 102]) + bars([103, 104, 105], session=DAY2)
    spec = {"kind": "cross", "a": {"name": "sma", "n": 3}, "b": 0, "direction": "above", "seed": False}
    assert sg.state(spec, bs) == [None, None, True, None, None, True]
    assert sg.state({**spec, "seed": True}, bs) == [None, None, True, True, True, True]


# --- validation ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"kind": "nope"}, "signal.kind"),
        ({**cross(105), "colour": 1}, "unknown key 'colour'"),
        ({"kind": "gap", "direction": "up"}, "needs 'pct'"),
        ({"kind": "gap", "direction": "sideways", "pct": 1}, "signal.direction"),
        ({"kind": "consecutive", "n": 3, "direction": "up", "seed": True, "reset": 1}, "signal.reset"),
        ({**cross(105), "a": {"name": "rsi"}}, "signal.a: rsi needs 'n'"),
        (
            {**cross(105), "a": {"name": "macd", "fast": 12, "slow": 26, "signal": 9, "output": "x"}},
            "signal.a.output",
        ),
        ({**cross(105), "only_when": [{"kind": "gap"}]}, "signal.only_when[0]"),
        ({**cross(105), "latest": "25:00"}, "signal.latest"),
        ({**cross(105), "confirm": 0}, "signal.confirm"),
    ],
)
def test_validate_refuses_with_the_path(spec, message):
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        sg.validate(spec)


# --- properties over random sessions ----------------------------------------------------------------

SPECS = [
    cross(100),
    cross(100, "below", confirm=2),
    {"kind": "cross", "a": {"name": "rsi", "n": 5}, "b": 60, "direction": "above", "seed": True, "reset": 5},
    {
        "kind": "cross",
        "a": {"name": "ema", "n": 3},
        "b": {"name": "ema", "n": 8},
        "direction": "below",
        "seed": False,
    },
    {
        "kind": "cross",
        "a": {"name": "macd", "fast": 3, "slow": 6, "signal": 3, "output": "line"},
        "b": {"name": "macd", "fast": 3, "slow": 6, "signal": 3, "output": "signal"},
        "direction": "above",
        "seed": True,
    },
    {
        "kind": "cross",
        "a": {"name": "stochastic", "k_period": 5, "k_smooth": 2, "d_smooth": 2, "output": "k"},
        "b": 80,
        "direction": "above",
        "seed": True,
    },
    {"kind": "range", "a": CLOSE, "low": 99, "high": 101, "direction": "exits", "seed": True},
    {"kind": "gap", "direction": "down", "pct": 0.2},
    {"kind": "gap_fill", "min_pct": 0.2},
    {"kind": "change", "from": "prior_close", "direction": "up", "pct": 0.3, "first_of_day": True},
    {"kind": "opening_range", "minutes": 15, "side": "above"},
    {"kind": "prior_session", "side": "low"},
    {"kind": "new_extreme", "side": "high", "lookback": 6},
    {"kind": "consecutive", "n": 2, "direction": "down", "seed": False, "latest": "11:00"},
    cross(100, only_when=[{"kind": "new_extreme", "side": "high", "lookback": "session"}]),
]


def random_bars(seed):
    rnd = random.Random(seed)
    out, price = [], 100.0
    for session in (DAY1, DAY2, DAY3):
        price += rnd.uniform(-1, 1)  # an overnight gap
        closes = []
        for _ in range(24):
            price += rnd.uniform(-0.6, 0.6)
            closes.append(round(price, 2))
        out += bars(closes, session=session, opens=round(price - rnd.uniform(-0.5, 0.5), 2))
    return out


@pytest.mark.parametrize("seed", range(6))
def test_the_event_form_fires_only_where_the_state_holds(seed):
    bs = random_bars(seed)
    for spec in SPECS:
        st = sg.state(spec, bs)
        for f in sg.firings(spec, bs):
            assert st[f.index] is True, (spec, f)


@pytest.mark.parametrize("seed", range(6))
def test_no_firing_reads_a_later_bar(seed):
    """Truncation invariance: what fired on the first k bars is exactly what the whole series says fired
    there. A signal that peeked at the next bar would change its past when the future is cut off."""
    bs = random_bars(seed)
    for spec in SPECS:
        full = sg.firings(spec, bs)
        for k in range(1, len(bs) + 1, 3):
            assert sg.firings(spec, bs[:k]) == [f for f in full if f.index < k], (spec, k)


def test_the_random_specs_fire_at_all():
    """The properties above mean little if nothing fires: most of the specs fire on some seed."""
    hits = sum(any(sg.firings(spec, random_bars(s)) for s in range(6)) for spec in SPECS)
    assert hits >= len(SPECS) - 2
