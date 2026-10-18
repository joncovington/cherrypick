"""`core.quotehistory.RecordedDay`: what a replay builds its snapshots from, read at an instant with
no look-ahead. Session bounds stop at the instant; the GEX surface is the profile at or before it and
refused when stale or thin; the trailing range needs its minimum of prior sessions."""

import sqlite3
from datetime import datetime

from cherrypick.core import clock, quotehistory

DAY = "2026-10-16"


def _ts(hhmm: str, day: str = DAY) -> float:
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=clock.ET).timestamp()


def _history(path, *, prior_days=("2026-10-13", "2026-10-14", "2026-10-15"), profiles=("10:00",), strikes=30):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE gex_quote_samples (id INTEGER PRIMARY KEY, symbol TEXT, trade_date TEXT, ts REAL,
                                        expiration TEXT);
        CREATE TABLE gex_quotes (sample_id INTEGER, side INTEGER, strike REAL, bid REAL, ask REAL, mid REAL,
                                 age_s REAL, delta REAL);
        CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL);
        CREATE TABLE gex_profile_history (symbol TEXT, trade_date TEXT, ts REAL, strike REAL, call_oi REAL,
                                          put_oi REAL, call_vol REAL, put_vol REAL, call_gamma REAL,
                                          put_gamma REAL, call_iv REAL, put_iv REAL);
        CREATE TABLE daily_closes (symbol TEXT, trade_date TEXT, close REAL);
        """
    )
    for i, (hhmm, spot) in enumerate(
        (("09:30", 6000.0), ("10:00", 6010.0), ("10:30", 5990.0), ("11:00", 6005.0))
    ):
        conn.execute("INSERT INTO gex_quote_samples VALUES (?, 'SPX', ?, ?, ?)", (i + 1, DAY, _ts(hhmm), DAY))
        conn.execute("INSERT INTO gex_quotes VALUES (?, 0, 6000, 2.0, 2.2, 2.1, 1.0, -0.5)", (i + 1,))
        conn.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)", (DAY, _ts(hhmm), spot))
    for d, width in zip(prior_days, (20.0, 30.0, 40.0), strict=False):
        conn.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, 6000)", (d, _ts("10:00", d)))
        conn.execute(
            "INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)", (d, _ts("11:00", d), 6000 + width)
        )
        conn.execute("INSERT INTO daily_closes VALUES ('SPX', ?, ?)", (d, 6000 + width))
    for hhmm in profiles:
        for k in range(strikes):
            strike = 5950 + 5 * k
            conn.execute(
                "INSERT INTO gex_profile_history "
                "VALUES ('SPX', ?, ?, ?, 100, 120, 0, 0, 0.01, 0.012, 0.2, 0.2)",
                (DAY, _ts(hhmm), strike),
            )
    conn.commit()
    conn.close()
    return path


def test_bounds_see_nothing_after_the_instant(tmp_path):
    day = quotehistory.RecordedDay("spx", DAY, history_db=_history(tmp_path / "h.db"))
    assert len(day.samples) == 4 and day.prev_close == 6040.0
    at_ten = day.bounds(_ts("10:00"))
    assert (at_ten["day_open"], at_ten["day_high"], at_ten["day_low"]) == (6000.0, 6010.0, 6000.0)
    # 10:30's low is not yet known at 10:00, and is at 10:30.
    assert day.bounds(_ts("10:30"))["day_low"] == 5990.0
    assert day.bounds(_ts("09:00")) == {}
    assert [w.timestamp() for w in day.instants()] == [s["ts"] for s in day.samples]


def test_trailing_range_needs_its_minimum_of_prior_sessions(tmp_path):
    full = quotehistory.RecordedDay("SPX", DAY, history_db=_history(tmp_path / "a.db"))
    assert full.trailing_range == 30.0
    short = quotehistory.RecordedDay(
        "SPX", DAY, history_db=_history(tmp_path / "b.db", prior_days=("2026-10-15",))
    )
    assert short.trailing_range is None


def test_gex_uses_the_profile_at_or_before_and_refuses_stale_or_thin(tmp_path):
    day = quotehistory.RecordedDay("SPX", DAY, history_db=_history(tmp_path / "h.db"))
    before, stats = day.gex(_ts("09:30"), 6000.0, 20)
    assert (
        before["ok"] is False and stats["refused"] == "profile_stale" and stats["profile_age_seconds"] is None
    )
    fresh, stats = day.gex(_ts("10:03"), 6010.0, 20)
    assert fresh.get("ok", True) and stats["profile_age_seconds"] == 180.0 and "refused" not in stats
    assert fresh == quotehistory.profile_surface(day.profiles[0][1], 6010.0)
    stale, stats = day.gex(_ts("10:07"), 6010.0, 20)  # 420 s old, past the 360 s rule
    assert stale["ok"] is False and stats["refused"] == "profile_stale"
    thin, stats = day.gex(_ts("10:03"), 6010.0, 31)
    assert thin["ok"] is False and stats["refused"] == "insufficient_coverage"


def test_leg_symbols_read_as_the_streamer_writes_them():
    assert quotehistory.leg_symbol("SPX", "2026-10-16", "P", 5800.0) == ".SPXW261016P5800"
    assert quotehistory.leg_symbol("SPX", "2026-10-16", "C", 5802.5) == ".SPXW261016C5802.5"


def test_an_absent_file_is_an_empty_day(tmp_path):
    day = quotehistory.RecordedDay("SPX", DAY, history_db=tmp_path / "none.db")
    assert day.samples == [] and day.profiles == [] and day.bounds(_ts("10:00")) == {}
    assert not (tmp_path / "none.db").exists()
