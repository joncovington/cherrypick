"""The driver over end-of-day chains: one tick a session at 15:59, positions held across sessions and
settled at their expiry's close, and an arm the source cannot price (its entry is not at the close)
counted, never moved."""

from __future__ import annotations

import copy
from datetime import date, timedelta

from cherrypick.core import calendar as cal
from market import CALENDAR
from market import rows as market_rows

from cherrypick.automations import arms, driver, history

START = date(2026, 3, 2)  # a Monday
EXPIRY = "2026-03-20"  # a Friday 18 days out


def sessions(n=20):
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


def eod_history(days, *, spot=300.0, skip=(), moves=None):
    bars, chains = [], {}
    earlier = [START - timedelta(days=k) for k in range(10, 0, -1)]
    prior = [
        {"session": d.isoformat(), "open": spot, "high": spot, "low": spot, "close": spot}
        for d in earlier
        if cal.is_trading_day(d)
    ]
    for s in days:
        px = (moves or {}).get(s, spot)
        bars.append({"session": s, "open": px, "high": px, "low": px, "close": px})
        if s in skip:
            continue
        years = max((date.fromisoformat(EXPIRY) - date.fromisoformat(s)).days, 0.25) / 365
        chains[s] = {EXPIRY: market_rows(px, years, 0.18, step=1.0, width=40.0)}
    return history.DailyChainHistory("GLD", prior + bars, lambda s: chains.get(s, {}))


def test_the_tick_is_the_last_minute_before_the_close():
    assert history.DailyChainHistory.tick("2026-03-02") == 959
    assert history.DailyChainHistory.tick("2026-11-27") == 779  # the day after Thanksgiving closes at 13:00
    h = eod_history(sessions(3), skip=(sessions(3)[1],))
    assert h.minutes(sessions(3)[0]) == [959] and h.minutes(sessions(3)[1]) == []


def test_an_entry_the_source_cannot_price_is_counted_not_moved():
    days = sessions()
    # One session the source holds nothing for: no data, which is not the same as an unpriceable entry.
    book = driver.run({"gld": gld()}, eod_history(days, skip=(days[3],)), days, events=CALENDAR, vix_daily={})
    assert book.trades == []
    assert book.why["gld-call-spread-20d"] == {"needs_intraday": len(days) - 1, "no_data": 1}


def test_a_close_entry_is_held_across_sessions_and_exits_on_its_expiry_day():
    days = sessions()
    # In the money by its expiry day, so the close prices (an unpriced close settles instead: an
    # end-of-day source has no later minute to retry in).
    h = eod_history(days, moves={EXPIRY: 320.0})
    book = driver.run({"gld": gld("15:59", 1)}, h, days, events=CALENDAR, vix_daily={})
    assert book.why["gld-call-spread-20d"]["entered"] == 1 and not book.open
    (trade,) = book.trades
    assert trade["session"] == days[0] and trade["expiry"] == EXPIRY
    assert (trade["exit_kind"], trade["exit_session"], trade["exit_minute"]) == ("time", EXPIRY, 959)


def test_an_expiry_the_source_skipped_settles_at_that_days_close():
    days = sessions()
    # The run's sessions skip the expiry day itself (its bar is held, its chain is not): the position
    # settles at that day's close on the next session the run reads.
    run_days = [d for d in days if d != EXPIRY]
    book = driver.run(
        {"gld": gld("15:59", 1, exit_rules={})}, eod_history(days, skip=(EXPIRY,)), run_days, events=CALENDAR,
        vix_daily={},
    )  # fmt: skip
    (trade,) = book.trades
    assert (trade["exit_kind"], trade["exit_session"]) == ("settlement", EXPIRY)
