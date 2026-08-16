"""The daily-history backfill: candle events fill stream_summary's ABSENT dates and nothing else.

The transform + insert-only rules live in streamcache (pure, tested directly); the engine test
drives `_backfill_history` against a fake candle streamer and pins the ownership rule — a row the
live Summary feed wrote is never overwritten, and today's (partial) candle is never written.
"""

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from cherrypick.core import streamcache
from cherrypick.core.streamer import ChainStreamer, _State

_ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------- streamcache helpers
def test_summary_backfill_rows_sorts_chains_and_drops_today():
    rows = streamcache.summary_backfill_rows(
        [
            {"date": "2026-08-14", "open": 71.0, "high": 72.0, "low": 70.0, "close": 71.5},
            {"date": "2026-08-13", "open": 70.0, "high": 71.0, "low": 69.0, "close": 70.5},
            {"date": "2026-08-16", "open": 72.0, "high": 72.5, "low": 71.5, "close": 72.2},  # today
            {"date": "2026-08-13", "open": 70.1, "high": 71.1, "low": 69.1, "close": 70.6},  # dupe: last wins
        ],
        today="2026-08-16",
    )
    assert [r["trade_date"] for r in rows] == ["2026-08-13", "2026-08-14"]
    assert rows[0]["prev_day_close"] is None  # nothing before the series' first bar
    assert rows[0]["day_close"] == 70.6
    assert rows[1]["prev_day_close"] == 70.6  # chained from the prior bar's close


def test_backfill_summary_never_overwrites_a_live_row(tmp_path):
    conn = streamcache.connect(tmp_path / "cache.db")
    # The live Summary listener already wrote 08-14 with its own (slightly different) numbers.
    conn.execute(
        "INSERT INTO stream_summary (symbol, trade_date, day_close, updated_at) VALUES (?, ?, ?, ?)",
        ("TNA", "2026-08-14", 71.44, time.time()),
    )
    conn.commit()
    added = streamcache.backfill_summary(
        conn,
        "TNA",
        [
            {"date": "2026-08-13", "open": 70.0, "high": 71.0, "low": 69.0, "close": 70.5},
            {"date": "2026-08-14", "open": 71.0, "high": 72.0, "low": 70.0, "close": 71.5},
        ],
        today="2026-08-16",
    )
    assert added == 1  # only the absent date
    live = conn.execute(
        "SELECT day_close FROM stream_summary WHERE symbol='TNA' AND trade_date='2026-08-14'"
    ).fetchone()
    assert live["day_close"] == 71.44  # the live row survived untouched


def test_completed_summary_days_counts_closed_past_rows(tmp_path):
    conn = streamcache.connect(tmp_path / "cache.db")
    for day, close in (("2026-08-13", 70.5), ("2026-08-14", 71.5), ("2026-08-16", None)):
        conn.execute(
            "INSERT INTO stream_summary (symbol, trade_date, day_close, updated_at) VALUES (?, ?, ?, ?)",
            ("TNA", day, close, time.time()),
        )
    conn.commit()
    assert streamcache.completed_summary_days(conn, "TNA", today="2026-08-16") == 2


# --------------------------------------------------------------------------- the engine task
class _FakeCandleStreamer:
    """Serves a fixed candle burst then ends the stream (StopAsyncIteration ends the collector)."""

    def __init__(self, events):
        self._events = events
        self.subscribed = None
        self.unsubscribed = []

    async def subscribe_candle(self, symbols, interval, start_time=None, **_):
        self.subscribed = (list(symbols), interval, start_time)

    async def unsubscribe_candle(self, ticker, interval=None, **_):
        self.unsubscribed.append((ticker, interval))

    async def listen(self, _event_type):
        for e in self._events:
            yield e


def _candle(symbol, day, *, o, h, lo, c):
    # Daily candles are stamped at the session start; midnight ET reproduces the SDK's shape.
    stamp = datetime.fromisoformat(day).replace(tzinfo=_ET).timestamp() * 1000
    return SimpleNamespace(event_symbol=f"{symbol}{{=d}}", time=stamp, open=o, high=h, low=lo, close=c)


def _run_backfill(tmp_path, events, *, wanted=5, symbols=("TNA",), pre_rows=()):
    engine = ChainStreamer(
        session_factory=lambda: None,
        db_path=tmp_path / "cache.db",
        symbols=list(symbols),
        history_days_for=lambda s: wanted,
    )
    conn = streamcache.connect(tmp_path / "cache.db")
    for symbol, day, close in pre_rows:
        conn.execute(
            "INSERT INTO stream_summary (symbol, trade_date, day_close, updated_at) VALUES (?, ?, ?, ?)",
            (symbol, day, close, time.time()),
        )
    conn.commit()
    state = _State(conn, list(symbols))
    fake = _FakeCandleStreamer(events)
    asyncio.run(engine._backfill_history(fake, state, object))
    return conn, fake


def test_backfill_fills_absent_dates_from_candles(tmp_path):
    conn, fake = _run_backfill(
        tmp_path,
        [
            _candle("TNA", "2026-08-12", o=69.0, h=70.0, lo=68.5, c=69.5),
            _candle("TNA", "2026-08-13", o=69.5, h=71.0, lo=69.0, c=70.5),
        ],
    )
    assert fake.subscribed is not None
    assert fake.subscribed[0] == ["TNA"]
    assert fake.subscribed[1] == "1d"
    assert fake.unsubscribed == [("TNA", "1d")]
    rows = conn.execute(
        "SELECT trade_date, day_close, prev_day_close FROM stream_summary ORDER BY trade_date"
    ).fetchall()
    assert [(r["trade_date"], r["day_close"]) for r in rows] == [
        ("2026-08-12", 69.5),
        ("2026-08-13", 70.5),
    ]
    assert rows[1]["prev_day_close"] == 69.5


def test_backfill_skips_a_symbol_with_enough_history(tmp_path):
    pre = [("TNA", f"2026-08-{d:02d}", 70.0) for d in (3, 4, 5, 6, 7)]
    conn, fake = _run_backfill(tmp_path, [], wanted=5, pre_rows=pre)
    assert fake.subscribed is None  # deficit check short-circuited: no candle subscription at all


def test_backfill_never_touches_todays_row_or_live_rows(tmp_path):
    today = datetime.now(tz=_ET).date().isoformat()
    conn, _ = _run_backfill(
        tmp_path,
        [
            _candle("TNA", "2026-08-13", o=69.5, h=71.0, lo=69.0, c=70.5),
            _candle("TNA", today, o=72.0, h=72.5, lo=71.5, c=72.2),  # today's partial candle
            _candle("TNA", "2026-08-14", o=70.5, h=72.0, lo=70.0, c=71.5),
        ],
        pre_rows=[("TNA", "2026-08-14", 71.44)],  # a live-written row for a candle date
    )
    rows = {
        r["trade_date"]: r["day_close"]
        for r in conn.execute("SELECT trade_date, day_close FROM stream_summary")
    }
    assert today not in rows  # today belongs to the live Summary listener alone
    assert rows["2026-08-14"] == 71.44  # the live row won
    assert rows["2026-08-13"] == 70.5


def test_backfill_ignores_unknown_candle_symbols(tmp_path):
    conn, _ = _run_backfill(
        tmp_path,
        [_candle("QQQ", "2026-08-13", o=1, h=2, lo=0.5, c=1.5)],  # never requested
    )
    assert conn.execute("SELECT COUNT(*) FROM stream_summary").fetchone()[0] == 0


def test_backfill_failure_never_raises(tmp_path):
    class _Broken(_FakeCandleStreamer):
        async def subscribe_candle(self, *a, **k):
            raise RuntimeError("wire down")

    engine = ChainStreamer(
        session_factory=lambda: None,
        db_path=tmp_path / "cache.db",
        symbols=["TNA"],
        history_days_for=lambda s: 5,
    )
    conn = streamcache.connect(tmp_path / "cache.db")
    state = _State(conn, ["TNA"])
    asyncio.run(engine._backfill_history(_Broken([]), state, object))  # must not raise
