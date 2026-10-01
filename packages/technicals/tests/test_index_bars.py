"""The cash indexes (SPX): fetched from the broker's daily candles by scripts/fetch_index_bars.py,
landed beside Dolt's bars, charted, and kept out of every stock measure.

The script's gap rule is the one that needs pinning: the feed's daily series skipped 09-28 and 09-29
on 2026-10-01, and a missing session is rebuilt from hourly candles ONLY when the hourly aggregation
reproduces the daily bars it overlaps -- otherwise it stays a gap rather than an invented bar.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.technicals import chart, land, paths, store

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_index_bars.py"
_spec = importlib.util.spec_from_file_location("fetch_index_bars", SCRIPT)
fib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fib)

ET = ZoneInfo("America/New_York")


def _hour(day: str, hh: int, o, h, lo, c):
    ts = datetime.fromisoformat(f"{day}T{hh:02d}:00").replace(tzinfo=ET).timestamp() * 1000
    return {"ts": ts, "open": o, "high": h, "low": lo, "close": c}


def test_missing_sessions_counts_only_finished_trading_days():
    have = {"2026-09-24", "2026-09-25", "2026-09-30"}
    # Sat/Sun are not sessions, and today (10-01) is not finished.
    assert fib.missing_sessions(have, date(2026, 9, 24), date(2026, 10, 1)) == ["2026-09-28", "2026-09-29"]


def test_hourly_candles_aggregate_to_the_day_and_zero_candles_are_not_prices():
    bars = fib.aggregate_hourly(
        [
            _hour("2026-09-28", 9, 7721.7, 7724.15, 7700.0, 7710.0),
            _hour("2026-09-28", 12, 7710.0, 7712.0, 7666.6, 7690.0),
            _hour("2026-09-28", 16, 7690.0, 7695.0, 7680.0, 7683.69),
            _hour("2026-09-28", 20, 0, 0, 0, 0),  # the feed emits these; not part of the session
        ]
    )
    assert bars["2026-09-28"] == {"date": "2026-09-28", "open": 7721.7, "high": 7724.15, "low": 7666.6, "close": 7683.69}


def test_a_gap_is_filled_only_when_hourly_reproduces_the_daily_bars():
    daily = [{"date": "2026-09-25", "open": 7709.86, "high": 7752.07, "low": 7693.08, "close": 7743.41}]
    agreeing = {
        "2026-09-25": {"date": "2026-09-25", "open": 7709.86, "high": 7752.07, "low": 7693.08, "close": 7743.41},
        "2026-09-28": {"date": "2026-09-28", "open": 7721.7, "high": 7724.15, "low": 7666.6, "close": 7683.69},
    }
    filled, verdict = fib.gap_fill(daily, agreeing, ["2026-09-28"])
    assert [b["date"] for b in filled] == ["2026-09-28"] and verdict["agree"] == 1

    disagreeing = {**agreeing, "2026-09-25": {**agreeing["2026-09-25"], "close": 7743.0}}
    filled, verdict = fib.gap_fill(daily, disagreeing, ["2026-09-28"])
    assert filled == [] and verdict["unfilled"] == ["2026-09-28"], "a source that disagrees fills nothing"

    filled, verdict = fib.gap_fill(daily, {"2026-09-28": agreeing["2026-09-28"]}, ["2026-09-28"])
    assert filled == [], "with nothing to check it against, the hourly series is not trusted"


def _index_file(rows):
    path = paths.index_bars()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = fib.connect(path)
    conn.executemany(
        "INSERT INTO index_bars (symbol, date, open, high, low, close, source, fetched_at) VALUES (?,?,?,?,?,?,?,?)",
        [(*r, "daily", 0.0) for r in rows],
    )
    conn.commit()
    conn.close()


def test_spx_lands_from_the_index_file_even_with_no_dolt(monkeypatch):
    _index_file([("SPX", "2026-09-25", 7709.86, 7752.07, 7693.08, 7743.41), ("SPX", "2026-09-28", 7721.7, 7724.15, 7666.6, 7683.69)])

    def refuse(cfg, database):
        raise ConnectionRefusedError("no server")

    monkeypatch.setattr(land, "_connect", refuse)
    report = land.land(wanted=["SPX", "AAA"], today=date(2026, 9, 29))
    assert report["ok"] is False, "Dolt is still down"
    assert report.get("indexes") is None or report["indexes"] == ["SPX"]
    conn = store.connect()
    bars = store.adjusted_bars(conn, "SPX")
    assert [b.close for b in bars] == [7743.41, 7683.69]
    raw = conn.execute("SELECT volume FROM bars WHERE symbol = 'SPX'").fetchall()
    assert all(r[0] is None for r in raw), "an index has no volume: stored null, not 0"


def test_an_index_is_charted_but_never_counted_as_a_stock():
    _index_file([("SPX", f"2026-09-{d:02d}", 7000.0 + d, 7010.0 + d, 6990.0 + d, 7005.0 + d) for d in (22, 23, 24, 25)])
    conn = store.connect()
    land.land_index_bars(conn, ["SPX"])
    conn.commit()
    assert store.stocks(conn, ["SPX", "AAA"]) == ["AAA"], "breadth and every stock measure leave SPX out"
    doc = chart.build(conn, "SPX")
    assert doc is not None and doc["bars"]["volume"] == [None, None, None, None]
    assert doc["rank"] is None, "the 1-10 rank is a decile of stocks"
