"""The driver over intraday chains held across sessions (`history.IntradayChainHistory`): a multi-day
automation entered inside the session, its exits read at every stored bar of every session it is held,
an expiry stored at five minutes read at its latest bar up to one bar old, and a position settled at
its expiry's daily close."""

from __future__ import annotations

import copy
from datetime import date, timedelta

from cherrypick.core import calendar as cal
from market import CALENDAR
from market import rows as market_rows

from cherrypick.automations import arms, driver, history

START = date(2026, 3, 2)  # a Monday
EXPIRY = "2026-03-20"  # a Friday 18 days out
OPEN, CLOSE = 570, 960


def sessions(n=15):
    out, d = [], START
    while len(out) < n:
        if cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def gld(entry_at="12:30", window=5, exit_rules=None):
    arm = copy.deepcopy(arms.load_arms()["gld-call-spread-20d"])
    arm["gates"] = []
    arm["entry"] = {"kind": "time", "at": entry_at, "window": window}
    if exit_rules is not None:
        arm["exit"] = exit_rules
    return arm


def prior_bars(spot):
    earlier = [START - timedelta(days=k) for k in range(10, 0, -1)]
    return [
        {"session": d.isoformat(), "open": spot, "high": spot, "low": spot, "close": spot}
        for d in earlier
        if cal.is_trading_day(d)
    ]


def intraday_history(days, *, spot=300.0, path=None, every=1, settle=None):
    """GLD at `spot` (or `path(session, minute)`) each minute; one expiry stored every `every` minutes."""
    px = path or (lambda s, m: spot)
    bars = prior_bars(spot)
    for s in days:
        close = settle if (settle is not None and s == EXPIRY) else px(s, CLOSE - 1)
        bars.append({"session": s, "open": px(s, OPEN), "high": spot, "low": spot, "close": close})

    def quotes(session, expiry):
        if expiry != EXPIRY or session > EXPIRY:
            return {}
        out = {}
        for m in range(OPEN, CLOSE, every):
            left = (date.fromisoformat(EXPIRY) - date.fromisoformat(session)).days + (CLOSE - m) / 1440
            out[m] = market_rows(px(session, m), max(left, 0.05) / 365, 0.18, step=1.0, width=40.0)
        return out

    def trail(session):
        return {m: px(session, m) for m in range(OPEN, CLOSE)}, "test"

    return history.IntradayChainHistory(
        "GLD",
        bars,
        expiries=lambda s: [EXPIRY] if s <= EXPIRY else [],
        quotes=quotes,
        spot=lambda s: trail(s),
        vix_daily={},
        sessions=days,
    )


def test_an_entry_inside_the_session_on_a_later_expiry_is_entered():
    days = sessions(3)
    book = driver.run({"gld": gld()}, intraday_history(days), days, events=CALENDAR, vix_daily={})
    assert book.why["gld-call-spread-20d"]["entered"] == 1, "end-of-day chains would count needs_intraday"
    (pos,) = book.open
    assert (pos.entry.session, pos.entry.minute, pos.entry.expiry) == (days[0], 750, EXPIRY)


def test_an_exit_is_read_at_the_bar_it_comes_due_on_a_later_session():
    days = sessions()
    rally = days[3]

    def path(s, m):  # flat until 11:07 on the fourth session, then far higher
        return 330.0 if (s > rally or (s == rally and m >= 667)) else 300.0

    rules = {"profit_dollars": 100}
    book = driver.run(
        {"gld": gld("15:59", 1, exit_rules=rules)}, intraday_history(days, path=path), days,
        events=CALENDAR, vix_daily={},
    )  # fmt: skip
    (trade,) = book.trades
    assert trade["session"] == days[0]
    assert (trade["exit_kind"], trade["exit_session"], trade["exit_minute"]) == ("profit_target", rally, 667)


def test_a_five_minute_expiry_reads_its_latest_bar_up_to_one_bar_old():
    days = sessions(2)
    h = intraday_history(days, every=5)
    assert h.minutes(days[0])[:2] == [570, 571], "the trail sets the ticks"
    assert h.listed(days[0], 574) == [EXPIRY] and h.rows(days[0], EXPIRY, 574) == h.rows(days[0], EXPIRY, 570)
    assert h.rows(days[0], EXPIRY, 959) == h.rows(days[0], EXPIRY, 955)
    assert h.rows(days[0], EXPIRY, 569) == [] and h.listed(days[0], 569) == []
    # A gap in the bars is not bridged: past one bar's spacing the expiry reads as unstored.
    gappy = intraday_history(days, every=5)
    gappy._quotes = lambda s, e: {
        m: r for m, r in intraday_history(days, every=5)._quotes(s, e).items() if m != 600
    }
    assert gappy.rows(days[0], EXPIRY, 600) == [], "595 is a whole bar old at 600"
    assert gappy.rows(days[0], EXPIRY, 604) == [] and gappy.listed(days[0], 604) == []
    assert gappy.rows(days[0], EXPIRY, 605) != []


def test_a_position_held_to_expiry_settles_at_its_daily_close_and_the_spot_source_is_kept():
    days = [d for d in sessions() if d <= EXPIRY]
    h = intraday_history(days, settle=320.0)
    book = driver.run({"gld": gld("15:59", 1, exit_rules={})}, h, days, events=CALENDAR, vix_daily={})
    (trade,) = book.trades
    assert (trade["exit_kind"], trade["exit_session"]) == ("settlement", EXPIRY)
    assert set(h.spot_sources.values()) == {"test"} and set(h.spot_sources) == set(days)
    assert h.prior_trails(days[2], 2).keys() == {days[0], days[1]}
