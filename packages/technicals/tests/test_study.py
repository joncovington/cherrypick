"""The historical study's guards (docs/signal-log-plan.md, Phase 1), each shown to fail when broken."""

from __future__ import annotations

import math
from statistics import median

from cherrypick.technicals import history, setups, store, study, universe

# --------------------------------------------------------------------------------- the universe


def test_membership_uses_only_the_sessions_before():
    """A name's dollar volume jumps from $10k to $100M a day on session 60. It may qualify only once
    the median over the 50 sessions BEFORE a session reaches $20M -- never on the strength of that
    session itself."""
    closes = [10.0] * 150
    volumes = [1_000.0] * 60 + [10_000_000.0] * 90
    ok, basis = universe.membership(closes, volumes)
    dollar = [c * v for c, v in zip(closes, volumes, strict=True)]
    expected = [False] + [
        i - 1 >= universe.WINDOW - 1
        and median(dollar[i - universe.WINDOW : i]) >= universe.MIN_DOLLAR_VOLUME
        and closes[i - 1] >= universe.MIN_PRICE
        for i in range(1, 150)
    ]
    assert ok == expected
    first = ok.index(True)
    assert median(dollar[first - universe.WINDOW : first]) >= universe.MIN_DOLLAR_VOLUME
    assert median(dollar[first - 1 - universe.WINDOW : first - 1]) < universe.MIN_DOLLAR_VOLUME
    assert basis[first] == median(dollar[first - universe.WINDOW : first])


def test_a_price_under_five_dollars_the_session_before_keeps_a_name_out():
    closes = [20.0] * 80
    volumes = [5_000_000.0] * 80  # $100M a day
    closes[70] = 4.0
    ok, _ = universe.membership(closes, volumes)
    assert ok[70] is True and ok[71] is False and ok[72] is True


def test_the_rolling_median_matches_the_textbook_one():
    vals = [float((i * 37) % 101) for i in range(300)]
    got = universe.rolling_median(vals, 50)
    assert got[:49] == [None] * 49
    assert all(got[i] == median(vals[i - 49 : i + 1]) for i in range(49, 300))


# -------------------------------------------------------------------------------------- scoring


def _name(opens, highs=None, lows=None, closes=None, atr=1.0, member=None, symbol="ABC", ended=False):
    n = len(opens)
    closes = closes or list(opens)
    highs = highs or [c + 0.5 for c in closes]
    lows = lows or [c - 0.5 for c in closes]
    r = setups.readings(highs, lows, closes, [None] * n)
    r = r.__class__(**{**vars(r), "atr14": [atr] * n})
    return study.Name(
        symbol=symbol,
        dates=[f"2020-01-{d + 1:02d}" if d < 31 else f"2020-02-{d - 30:02d}" for d in range(n)],
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        member=member or [True] * n,
        dollar_volume=[1e9] * n,
        spreads=[None] * n,
        readings=r,
        ended=ended,
    )


def test_fills_are_at_the_next_open_never_the_signal_close():
    """Signal at bar 2 (close 100), next open 104; exit signal at bar 5, next open 110. A long earns
    110 - 104 = 6, so 6 R at an ATR of 1; filling at the closes would read 9."""
    opens = [100.0, 100.0, 100.0, 104.0, 105.0, 107.0, 110.0, 111.0]
    closes = [100.0, 100.0, 100.0, 105.0, 106.0, 109.0, 111.0, 111.0]
    nm = _name(opens, closes=closes)
    sc = study.score(nm, "trend", setups.Trade(2, 5, "21 EMA"))
    assert math.isclose(sc["r_gross"], 6.0) and sc["hold"] == 3
    short = study.score(nm, "trend-short", setups.Trade(2, 5, "21 EMA"))
    assert math.isclose(short["r_gross"], -6.0)


def test_an_open_position_is_not_scored_unless_the_name_has_stopped_trading():
    opens = [100.0] * 6
    assert study.score(_name(opens), "trend", setups.Trade(2)) is None
    sc = study.score(_name(opens, ended=True), "trend", setups.Trade(2))
    assert sc["ended"] is True


def test_costs_come_off_in_r_and_shorts_pay_borrow():
    opens = [100.0] * 40
    nm = _name(opens)
    nm.spreads = [0.02] * 40  # a 2% spread: half on each fill, 1% of 100 twice
    long_ = study.score(nm, "trend", setups.Trade(2, 30, "21 EMA"))
    assert math.isclose(long_["cost_r"], 2.0)
    short = study.score(nm, "trend-short", setups.Trade(2, 30, "21 EMA"))
    days = 28  # 2020-01-04 to 2020-02-01
    assert math.isclose(short["cost_r"], 2.0 + study.BORROW_RATE * days / 365 * 100.0)


def test_the_tuned_setups_count_only_in_their_holdout():
    nm = _name([100.0] * 5, symbol="CSCO")  # one of the names the rules were tuned on
    late = study.TUNING_END.replace("2022", "2024")
    nm.dates = ["2020-01-01", study.TUNING_END, late, late, late]
    assert study.counted(nm, "pullback", 1) is True  # on the last day before the cut
    assert study.counted(nm, "pullback", 2) is False  # after it, on a tuning name
    assert study.counted(nm, "trend", 2) is True  # never tuned: counts everywhere
    nm.symbol = "NOTANAME"
    assert study.counted(nm, "breakout-short", 2) is True  # after the cut, but a fresh name
    nm.member = [True, True, False, True, True]
    assert study.counted(nm, "trend", 2) is False  # out of the universe that day


# ------------------------------------------------------------------------------------- baseline


def _rows():
    return [
        {"symbol": "AAA", "setup": "trend", "date": "2020-01-02", "counted": True},
        {"symbol": "BBB", "setup": "reversion-short", "date": "2020-01-03", "counted": True},
        {"symbol": "CCC", "setup": "trend", "date": "2020-01-02", "counted": False},
    ]


def test_the_baseline_replays_exactly_and_moves_with_its_seed(monkeypatch):
    by_date = {
        "2020-01-02": [f"N{k}" for k in range(60)] + ["AAA"],
        "2020-01-03": [f"N{k}" for k in range(60)],
    }
    by_name = {
        "AAA": [f"2020-03-{d:02d}" for d in range(1, 29)],
        "BBB": [f"2020-04-{d:02d}" for d in range(1, 29)],
    }
    a = study.draw_plan(_rows(), by_date, by_name)
    b = study.draw_plan(_rows(), by_date, by_name)
    assert a == b
    drawn = [d for v in a.values() for d in v]
    assert len(drawn) == 2 * (study.DRAWS_SAME_DATE + study.DRAWS_SAME_NAME)  # the uncounted row draws none
    assert all(sym != "AAA" for sym, v in a.items() for key, kind, *_ in v if key == 0 and kind == "date")
    monkeypatch.setattr(study, "SEED", study.SEED + 1)
    assert study.draw_plan(_rows(), by_date, by_name) != a


# ---------------------------------------------------------------------------------- statistics


def test_corwin_schultz_has_its_closed_form_on_a_steady_range():
    """With the same high/low ratio k on both days, alpha = ln k, so S = 2(k - 1)/(1 + k)."""
    highs, lows = [102.0] * 5, [100.0] * 5
    s = study.corwin_schultz(highs, lows)
    assert s[0] is None and math.isclose(s[3], 2 * 0.02 / 2.02)


def test_holm_steps_down_and_stops_at_the_first_failure():
    got = study.holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.5})
    assert got == {"a": True, "c": False, "b": False, "d": False}


def test_the_test_reads_the_edge_in_calendar_time():
    """Three entries on one day (+1 R each against baseline) and one on another (-1): per entry the
    edge is +0.5, per session it is 0 -- the calendar-time figure is the one reported."""
    rows = [{"date": "d1", "r": 1.0, "base": 0.0}] * 3 + [{"date": "d2", "r": -1.0, "base": 0.0}]
    t = study.test(rows)
    assert t["edge_r"] == 0.0 and t["edge_r_per_entry"] == 0.5 and t["sessions"] == 2


# ------------------------------------------------------------------------- the history store


def test_the_history_store_matches_the_nightly_store_and_a_double_dividend_is_caught(tmp_path):
    rows = [("ABC", f"2024-01-{d:02d}", 50.0 + d, 51.0 + d, 49.0 + d, 50.5 + d, 1e6) for d in range(1, 29)]
    hist, eod = history.connect(tmp_path / "h.db"), store.connect(tmp_path / "e.db")
    for c in (hist, eod):
        store.upsert_bars(c, rows)
        store.upsert_dividends(c, [("ABC", "2024-01-15", 0.5)])
        c.commit()
    good = history.compare_overlap(hist, eod, ["ABC"])
    assert good["prices"] > 0 and good["agree"] == good["prices"] and not good["disagree"]
    store.upsert_dividends(hist, [("ABC", "2024-01-20", 0.5)])  # a dividend the nightly store lacks
    hist.commit()
    assert history.compare_overlap(hist, eod, ["ABC"])["disagree"].get("ABC", 0) > 0


def test_a_jump_no_split_explains_is_listed():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    bars = [("ABC", f"2024-01-{d:02d}", 10.0, 10.0, 10.0, 10.0, 1e6) for d in range(1, 10)]
    bars.append(("ABC", "2024-01-10", 30.0, 30.0, 30.0, 30.0, 1e6))  # tripled overnight
    store.upsert_bars(conn, bars)
    assert history.unexplained_jumps(conn, "ABC") == [("2024-01-10", 3.0)]
    store.upsert_splits(conn, [("ABC", "2024-01-10", 1.0, 3.0)])
    assert history.unexplained_jumps(conn, "ABC") == []


# ------------------------------------------------------------- suspected corporate actions


def _raw(closes, opens=None):
    from cherrypick.technicals.adjust import Bar

    opens = opens or closes
    return [
        Bar(f"2024-01-{d + 1:02d}", o, max(o, c), min(o, c), c, 1e6)
        for d, (o, c) in enumerate(zip(opens, closes, strict=True))
    ]


def test_an_unrecorded_split_is_suspected_and_a_crash_is_not():
    from datetime import date as _d

    # 2:1 with no split recorded: the open gaps to exactly half the prior close.
    split = _raw([100.0, 100.0, 50.0, 50.5], opens=[100.0, 100.0, 50.0, 50.2])
    assert history.suspected_actions(split, []) == ["2024-01-03"]
    # The same move with the split on record is explained.
    assert history.suspected_actions(split, [_d(2024, 1, 3)]) == []
    # A crash: down 55% on the close, but it opened 38% lower -- not a clean ratio.
    crash = _raw([100.0, 100.0, 45.0, 44.0], opens=[100.0, 100.0, 62.0, 45.0])
    assert history.suspected_actions(crash, []) == []


def test_a_position_through_a_suspect_and_an_entry_in_its_shadow_are_left_out():
    nm = _name([100.0] * 300)
    nm.suspects = [150]
    nm.shadow = [150 <= k <= 150 + study.SHADOW for k in range(300)]
    assert study.spans_suspect(nm, 140, setups.Trade(140, 155, "21 EMA")) is True
    assert study.spans_suspect(nm, 100, setups.Trade(100, 120, "21 EMA")) is False
    assert study.spans_suspect(nm, 140, setups.Trade(140, 148, "21 EMA")) is False  # exits at the open of 149
    assert study.spans_suspect(nm, 140, setups.Trade(140, 149, "21 EMA")) is True  # fills at the open of 150
    assert study.counted(nm, "trend", 200) is False and study.counted(nm, "trend", 280) is True


def test_peaks_skip_only_names_that_could_never_qualify(tmp_path):
    """A name whose single best day is under the floor can never qualify; one that touched it once
    is kept (its median may still fall short -- membership decides that, not this)."""
    conn = history.connect(tmp_path / "h.db")
    store.upsert_bars(conn, [("SMALL", "2024-01-02", 10, 10, 10, 10.0, 1e6)])  # $10M
    store.upsert_bars(
        conn, [("ONCE", "2024-01-02", 10, 10, 10, 10.0, 1e6), ("ONCE", "2024-01-03", 10, 10, 10, 10.0, 3e6)]
    )
    conn.commit()
    assert history.names_peaking_at_least(conn, universe.MIN_DOLLAR_VOLUME) == ["ONCE"]
    history.merge_peaks(conn, {"SMALL": 5e7})  # a later window's bigger day raises the peak
    history.merge_peaks(conn, {"ONCE": 1.0})  # and a smaller one never lowers it
    assert history.names_peaking_at_least(conn, universe.MIN_DOLLAR_VOLUME) == ["ONCE", "SMALL"]
