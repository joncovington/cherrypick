"""The setups watchlist: which positions it lists, and the context it lists them with."""

from __future__ import annotations

import json

from cherrypick.technicals import chart, watchlist

DATES = [f"2026-08-{d:02d}" for d in range(1, 29)] + [f"2026-09-{d:02d}" for d in range(1, 29)]


def _doc(trades, levels=None, closes=None):
    closes = closes or [100.0 + i for i in range(len(DATES))]
    return {
        "symbol": "ABC",
        "session": DATES[-1],
        "bars": {"date": DATES, "close": closes},
        "trend_short": [None] * (len(DATES) - 1) + [-1],
        "trend_long": [None] * (len(DATES) - 1) + [3],
        "rank": 8,
        "vendor": None if levels is None else {"levels": levels},
        "setups": [{"id": "trend", "name": "Trend following", "trades": trades}],
    }


def _trade(entry, exit_=None, reason=None):
    return {
        "entry_date": entry,
        "entry_price": 100.0,
        "exit_date": exit_,
        "exit_price": None if exit_ is None else 110.0,
        "reason": reason,
        "target": None,
    }


def test_the_vendor_page_label_is_three_way():
    assert [watchlist.page_label(s) for s in (-4, -3, -2, -1, 0, 1, 2, 3, 4)] == [
        "Bearish",
        "Bearish",
        "Bearish",  # -2 is Bearish on the vendor's page, where our five-step label says Mildly Bearish
        "Neutral",  # -1 is Neutral there, Mildly Bearish in ours
        "Neutral",
        "Neutral",
        "Bullish",
        "Bullish",
        "Bullish",
    ]
    assert watchlist.page_label(None) is None


def test_it_lists_every_open_position_and_only_recent_closed_ones():
    old_closed = _trade(DATES[2], DATES[5], "21 EMA")  # exited 50 sessions ago
    recent_exit = _trade(DATES[10], DATES[-3], "21 EMA")  # entered long ago, exited 2 sessions ago
    open_old = _trade(DATES[1])  # open for 54 sessions: still listed
    rows = watchlist.rows(_doc([old_closed, recent_exit, open_old]), {})
    assert [(r["entry_date"], r["status"]) for r in rows] == [(DATES[10], "closed"), (DATES[1], "open")]
    closed, opened = rows
    assert closed["exit_ago"] == 2 and closed["entry_ago"] == len(DATES) - 11
    assert closed["move_pct"] == 10.0  # exit 110 against entry 100
    assert opened["move_pct"] == round(100 * (DATES.index(DATES[-1]) + 100.0) / 100 - 100, 2)
    assert (
        opened["trend_1m_label"] == "Neutral" and opened["trend_6m_label"] == "Bullish" and opened["rs"] == 8
    )


def test_the_nearest_levels_are_only_those_the_vendors_chart_draws():
    levels = [
        {"kind": "support", "value": 150.0, "vendor_view": True},
        {"kind": "support", "value": 154.0, "vendor_view": False},  # nearer, but not on their chart
        {"kind": "resistance", "value": 160.0, "vendor_view": True},
        {"kind": "gapResistance", "value": 156.0, "vendor_view": False},
    ]
    s, r = watchlist.nearest_levels({"levels": levels}, 155.0)
    assert s == {"value": 150.0, "pct": -3.23} and r == {"value": 160.0, "pct": 3.23}
    assert watchlist.nearest_levels(None, 155.0) == (None, None)


def test_one_month_against_spy_is_the_difference_in_points_over_the_same_sessions():
    # Only the session 21 back is at 100, so a month counted from the wrong session is visible.
    closes = [105.0] * len(DATES)
    closes[-22], closes[-1] = 100.0, 110.0  # +10% over the month
    spy = dict.fromkeys(DATES, 510.0)
    spy[DATES[-22]], spy[DATES[-1]] = 500.0, 520.0  # +4% over the same sessions
    assert watchlist.vs_spy(DATES, closes, spy) == 6.0
    assert watchlist.vs_spy(DATES, closes, {}) is None
    assert watchlist.vs_spy(DATES[:10], closes[:10], spy) is None


def test_the_chart_job_writes_the_watchlist_beside_the_charts(tmp_path):
    rows = watchlist.rows(_doc([_trade(DATES[-2])]), {})
    watchlist.write(tmp_path, rows, DATES[-1])
    doc = json.loads((tmp_path / watchlist.FILE).read_text(encoding="utf-8"))
    assert doc["session"] == DATES[-1] and doc["window"] == watchlist.WINDOW and len(doc["rows"]) == 1
    assert chart.write_all.__doc__ and "watchlist" in chart.write_all.__doc__
