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


def _recent_sessions(count):
    """`count` completed trading days ending at the last one before today."""
    from cherrypick.core import calendar as _cal

    day = _cal.previous_trading_day(datetime.now(tz=_ET).date())
    out = []
    for _ in range(count):
        out.append(day.isoformat())
        day = _cal.previous_trading_day(day)
    return list(reversed(out))


def test_backfill_skips_a_symbol_that_is_both_deep_and_current(tmp_path):
    """Short-circuiting needs BOTH halves. Depth alone was the whole test until 2026-08-25."""
    pre = [("TNA", day, 70.0) for day in _recent_sessions(5)]
    conn, fake = _run_backfill(tmp_path, [], wanted=5, pre_rows=pre)
    assert fake.subscribed is None  # no candle subscription at all


def test_backfill_refetches_a_deep_series_that_stopped_updating(tmp_path):
    """The hole the count could never see, and the one that matters.

    VIX held 1,380 completed rows and nothing after 2026-08-14. The deficit check read
    "1380 >= 270, satisfied" on every single connection while the series it protects had stopped a
    week earlier -- and a percentile, an SMA and a z-score are all computed over the TAIL, so a stale
    end is exactly the part that decides the answer. Depth is not currency.
    """
    pre = [("TNA", f"2026-08-{d:02d}", 70.0) for d in (3, 4, 5, 6, 7)]  # plenty, and all stale
    conn, fake = _run_backfill(tmp_path, [], wanted=5, pre_rows=pre)
    assert fake.subscribed is not None, "a series that stopped updating must be refetched"
    assert fake.subscribed[0] == ["TNA"]


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


def test_a_bar_with_no_real_close_is_dropped_not_stored_as_zero():
    """Zero is not a price, and it is the most dangerous wrong one available here: it survives every
    null check downstream, sits below any real value in a percentile, and drags an SMA silently.

    Every symbol backfilled before 2026-08-25 carries exactly one such row, always the first date of
    its window -- 34 across the cache, the feed's leading partial bar written through as a price.
    """
    rows = streamcache.summary_backfill_rows(
        [
            {"date": "2026-01-02", "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
            {"date": "2026-01-05", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5},
            {"date": "2026-01-06", "open": 10.5, "high": 12.0, "low": 10.0, "close": None},
            {"date": "2026-01-07", "open": 11.0, "high": 12.0, "low": 10.0, "close": -3.0},
            {"date": "2026-01-08", "open": 11.0, "high": 12.0, "low": 10.0, "close": 11.5},
        ],
        today="2026-01-09",
    )

    assert [r["trade_date"] for r in rows] == ["2026-01-05", "2026-01-08"]
    assert all(r["day_close"] > 0 for r in rows)
    # The chain skips the dropped dates rather than carrying a zero forward as a prior close.
    assert rows[1]["prev_day_close"] == 10.5


def test_the_producer_drains_stored_closes_that_are_not_prices(tmp_path):
    """A repair the producer runs because the cache has exactly one writer by invariant -- a
    maintenance script would be a second.

    The backlog is the feed's leading partial bar written through as 0.0, one row per symbol. Seven
    symbols held fewer rows than a 252-session window is wide, so their zero sat inside the range a
    percentile actually reads.
    """
    conn = streamcache.connect(tmp_path / "cache.db")
    for symbol, day, close in [
        ("TNA", "2026-05-14", 0.0),
        ("TNA", "2026-05-15", 70.0),
        ("SKEW", "2025-02-21", 0.0),
        ("VIX", "2021-02-14", -1.0),
        ("VIX", "2021-02-16", 22.0),
    ]:
        conn.execute(
            "INSERT INTO stream_summary (symbol, trade_date, day_close, updated_at) VALUES (?,?,?,?)",
            (symbol, day, close, time.time()),
        )
    conn.commit()

    removed = streamcache.purge_nonpositive_closes(conn)

    assert removed == 3
    left = {(r[0], r[1]) for r in conn.execute("SELECT symbol, trade_date FROM stream_summary")}
    assert left == {("TNA", "2026-05-15"), ("VIX", "2021-02-16")}
    assert streamcache.purge_nonpositive_closes(conn) == 0, "idempotent -- a fixed backlog, then nothing"


def test_the_producer_fills_a_missing_close_from_the_next_sessions_prev_close(tmp_path):
    """SPX's Summary never delivers `day_close_price`, so every session it wrote since 2026-07-29
    held open/high/low and a null close while SPY and VIX stayed complete. The close is not lost —
    the NEXT session's `prev_day_close` is the same exchange-official number — but a consumer
    reading `day_close` alone saw the series stop at 2026-07-28, which is exactly the trap a
    regime read walked into. Filling the hole is what makes the plain read correct.
    """
    conn = streamcache.connect(tmp_path / "cache.db")
    rows = [
        # SPX: Mon/Tue/Wed consecutive; the first two closes are recoverable from the next row.
        ("SPX", "2026-09-14", None, None),
        ("SPX", "2026-09-15", None, 7500.0),
        ("SPX", "2026-09-16", None, 7550.0),
        # a session is MISSING between these two, so 09-17's prev close belongs to 09-16, not 09-15
        ("XSP", "2026-09-15", None, None),
        ("XSP", "2026-09-17", None, 760.0),
        # a real close is never overwritten
        ("VIX", "2026-09-15", 22.0, None),
        ("VIX", "2026-09-16", None, 99.0),
    ]
    for symbol, day, close, prev in rows:
        conn.execute(
            "INSERT INTO stream_summary (symbol, trade_date, day_close, prev_day_close, updated_at) "
            "VALUES (?,?,?,?,?)",
            (symbol, day, close, prev, time.time()),
        )
    conn.commit()

    filled = streamcache.fill_closes_from_next_prev(conn)

    got = {
        (r[0], r[1]): r[2] for r in conn.execute("SELECT symbol, trade_date, day_close FROM stream_summary")
    }
    assert got[("SPX", "2026-09-14")] == 7500.0
    assert got[("SPX", "2026-09-15")] == 7550.0
    assert got[("SPX", "2026-09-16")] is None, "the newest session has no later row to carry it"
    assert got[("XSP", "2026-09-15")] is None, "a gap in the series is not a close for the day before it"
    assert got[("VIX", "2026-09-15")] == 22.0, "a confirmed close is never replaced"
    assert filled == 2
    assert streamcache.fill_closes_from_next_prev(conn) == 0, "idempotent -- a fixed backlog, then nothing"


def _summary(conn, rows):
    for symbol, day, o, h, lo, c, prev in rows:
        conn.execute(
            "INSERT INTO stream_summary (symbol, trade_date, day_open, day_high, day_low, "
            "day_close, prev_day_close, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (symbol, day, o, h, lo, c, prev, time.time()),
        )
    conn.commit()


def test_daily_bars_resolves_a_close_the_live_producer_never_wrote(tmp_path):
    """The live SPX shape: open/high/low and prev_day_close, never day_close. Reading `day_close`
    alone yields no bars at all, which is how a regime read saw the series stop at 2026-07-28."""
    conn = streamcache.connect(tmp_path / "cache.db")
    _summary(
        conn,
        [
            ("SPX", "2026-09-14", 100.0, 105.0, 99.0, None, None),
            ("SPX", "2026-09-15", 101.0, 106.0, 100.0, None, 104.0),
            ("SPX", "2026-09-16", 102.0, 107.0, 101.0, None, 105.5),
            ("SPX", "2026-09-17", 103.0, 108.0, 102.0, None, 106.5),
        ],
    )
    bars = streamcache.daily_bars(conn, "SPX", through="2026-09-17")
    assert [(b["session"], b["close"]) for b in bars] == [
        ("2026-09-14", 104.0),
        ("2026-09-15", 105.5),
        ("2026-09-16", 106.5),
    ], "the through session is read for its prior close but is a partial bar, never returned"
    assert bars[0]["high"] == 105.0 and bars[0]["low"] == 99.0


def test_daily_bars_prefers_the_rows_own_close_and_skips_a_gap(tmp_path):
    conn = streamcache.connect(tmp_path / "cache.db")
    _summary(
        conn,
        [
            # 09-15 carries its own close; the next row's prev must not override it
            ("SPX", "2026-09-15", 101.0, 106.0, 100.0, 105.0, None),
            ("SPX", "2026-09-16", 102.0, 107.0, 101.0, None, 999.0),
            # 09-17 is missing, so 09-18's prev close belongs to 09-17, not to 09-16
            ("SPX", "2026-09-18", 104.0, 109.0, 103.0, None, 108.0),
            ("SPX", "2026-09-21", 105.0, 110.0, 104.0, None, 109.0),
        ],
    )
    got = {b["session"]: b["close"] for b in streamcache.daily_bars(conn, "SPX", through="2026-09-21")}
    assert got["2026-09-15"] == 105.0, "a confirmed close outranks the chained one"
    assert "2026-09-16" not in got, "across a gap that prior close belongs to the missing session"
    assert got["2026-09-18"] == 109.0


def test_daily_bars_drops_a_row_that_cannot_make_a_true_range(tmp_path):
    """A bar with no high or low cannot produce a true range, so it is not a bar."""
    conn = streamcache.connect(tmp_path / "cache.db")
    _summary(
        conn,
        [
            ("SPX", "2026-09-15", 101.0, None, None, 105.0, None),
            ("SPX", "2026-09-16", 102.0, 107.0, 101.0, 106.0, 105.0),
            ("SPX", "2026-09-17", 103.0, 108.0, 102.0, None, 106.0),
        ],
    )
    assert [b["session"] for b in streamcache.daily_bars(conn, "SPX", through="2026-09-17")] == ["2026-09-16"]


def test_recency_counts_a_close_carried_on_the_next_rows_prev_close(tmp_path):
    """Both routes to a close, exactly as overview's `_close_history` reads them.

    `day_close` dates its own row; `prev_day_close` dates the PRECEDING row, and it is the only route
    for a session the live producer wrote, because that producer stops at the bell and never fills
    `day_close`. Counting only the first made every live-written underlying look permanently stale --
    SPX answered 2026-07-28 while holding a current row for every session since -- which would have
    refetched its candles on every connection forever and filled nothing, since those dates are
    present and the insert skips them.
    """
    conn = streamcache.connect(tmp_path / "cache.db")
    for day, close, prev in [
        ("2026-08-20", 100.0, None),  # backfilled: dates itself
        ("2026-08-21", None, 101.0),  # live-written: carries 08-20's settle... on THIS row
        ("2026-08-24", None, 102.0),  # ...and 08-21's settle here
    ]:
        conn.execute(
            "INSERT INTO stream_summary (symbol, trade_date, day_close, prev_day_close, updated_at) "
            "VALUES (?,?,?,?,?)",
            ("SPX", day, close, prev, time.time()),
        )
    conn.commit()

    latest = streamcache.latest_summary_date(conn, "SPX", today="2026-08-25")

    assert latest == "2026-08-21", (
        "08-24's prev_day_close carries 08-21's close, so the series reaches 08-21 -- not 08-20, "
        "which is all day_close alone can see"
    )
