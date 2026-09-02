"""The Summary listener: option OI keeps flowing to stream_oi, and the UNDERLYING's
session OHLC — which the old oi-only branch silently dropped for cash indices — lands
in stream_summary per (symbol, trade_date), accumulating the daily series the
intraday-range gates and the true-range ATR read."""

import asyncio
from types import SimpleNamespace

from cherrypick.core import streamcache
from cherrypick.core.streamer import ChainStreamer, _State


def _engine(tmp_path, symbols=("SPX",)):
    return ChainStreamer(
        session_factory=lambda: None,
        db_path=tmp_path / "cache.db",
        symbols=list(symbols),
    )


class _FakeStreamer:
    """Yields a fixed list of events then ends the stream (the async-for exits)."""

    def __init__(self, events):
        self._events = events

    async def listen(self, _event_type):
        for e in self._events:
            yield e


def _summary_event(symbol, *, oi=None, o=None, h=None, lo=None, c=None, prev=None):
    return SimpleNamespace(
        event_symbol=symbol,
        open_interest=oi,
        day_open_price=o,
        day_high_price=h,
        day_low_price=lo,
        day_close_price=c,
        prev_day_close_price=prev,
    )


def _run_summary(tmp_path, events, symbols=("SPX",)):
    engine = _engine(tmp_path, symbols)
    conn = streamcache.connect(tmp_path / "cache.db")
    state = _State(conn, list(symbols))
    asyncio.run(engine._listen_summary(_FakeStreamer(events), state, object))
    conn.commit()
    return conn


def test_underlying_summary_lands_in_stream_summary(tmp_path):
    conn = _run_summary(
        tmp_path,
        [
            _summary_event("SPX", o=6000.0, h=6050.0, lo=5980.0, c=6040.0, prev=5995.0),
        ],
    )
    row = conn.execute("SELECT * FROM stream_summary WHERE symbol='SPX'").fetchone()
    assert row is not None
    assert row["day_high"] == 6050.0
    assert row["day_low"] == 5980.0
    assert row["prev_day_close"] == 5995.0
    # A cash index has no OI: nothing lands in stream_oi, and that must not be an error.
    assert conn.execute("SELECT COUNT(*) FROM stream_oi").fetchone()[0] == 0


def test_option_summary_still_feeds_stream_oi_only(tmp_path):
    conn = _run_summary(
        tmp_path,
        [
            _summary_event(".SPXW260728C6000", oi=1234, h=12.0, lo=8.0),
        ],
    )
    oi = conn.execute("SELECT open_interest FROM stream_oi").fetchone()
    assert oi["open_interest"] == 1234
    # Option OHLC is not the underlying's session range — never written to stream_summary.
    assert conn.execute("SELECT COUNT(*) FROM stream_summary").fetchone()[0] == 0


def test_repeated_underlying_summaries_upsert_the_same_day_row(tmp_path):
    conn = _run_summary(
        tmp_path,
        [
            _summary_event("SPX", h=6050.0, lo=5980.0, prev=5995.0),
            _summary_event("SPX", h=6070.0, lo=5975.0, prev=5995.0),  # range widened intraday
        ],
    )
    rows = conn.execute("SELECT * FROM stream_summary WHERE symbol='SPX'").fetchall()
    assert len(rows) == 1
    assert rows[0]["day_high"] == 6070.0
    assert rows[0]["day_low"] == 5975.0


def test_summary_event_with_neither_oi_nor_range_is_ignored(tmp_path):
    conn = _run_summary(tmp_path, [_summary_event("SPX")])
    assert conn.execute("SELECT COUNT(*) FROM stream_summary").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM stream_oi").fetchone()[0] == 0


def test_stream_summary_table_in_shared_ddl(tmp_path):
    conn = streamcache.connect(tmp_path / "fresh.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stream_summary)")}
    assert cols == {
        "symbol",
        "trade_date",
        "day_open",
        "day_high",
        "day_low",
        "day_close",
        "prev_day_close",
        "updated_at",
    }


# ------------------------------------------------- a confirmed close is never erased (2026-08-27)
#
# SPX and XSP kept receiving Summary events until ~20:07 ET with `day_close_price` cleared, and the
# upsert copied that null straight over the close the 16:00 event had confirmed. 22 consecutive
# sessions of SPX closes were lost that way (2026-07-29 onward), freezing `daily_closes` — the
# suite's only multi-year series — at 2026-07-28 while every other symbol stayed current. Symbols
# whose last event of the day landed earlier (VIX at 10:08, SPY at 16:15) were untouched, which is
# precisely why a month-long gap in the index everything is priced against stayed invisible.


def test_a_later_event_without_a_close_does_not_erase_the_confirmed_one(tmp_path):
    conn = _run_summary(
        tmp_path,
        [
            # 16:00 — the settling print.
            _summary_event("SPX", o=6000.0, h=6050.0, lo=5980.0, c=6040.0, prev=5995.0),
            # 20:07 — the evening event, session fields cleared. A close does not un-happen.
            _summary_event("SPX", o=None, h=6050.0, lo=5980.0, c=None, prev=5995.0),
        ],
    )
    row = conn.execute("SELECT * FROM stream_summary WHERE symbol='SPX'").fetchone()
    assert row["day_close"] == 6040.0
    assert row["day_open"] == 6000.0


def test_a_later_event_still_updates_a_value_it_actually_carries(tmp_path):
    """COALESCE must preserve, not freeze: the session high genuinely widens through the day."""
    conn = _run_summary(
        tmp_path,
        [
            _summary_event("SPX", h=6050.0, lo=5980.0, prev=5995.0),
            _summary_event("SPX", h=6075.0, lo=5960.0, prev=5995.0),
        ],
    )
    row = conn.execute("SELECT * FROM stream_summary WHERE symbol='SPX'").fetchone()
    assert row["day_high"] == 6075.0
    assert row["day_low"] == 5960.0


def test_a_close_that_arrives_late_is_still_written(tmp_path):
    """The ordinary case: nothing has a close until the session settles."""
    conn = _run_summary(
        tmp_path,
        [
            _summary_event("SPX", h=6050.0, lo=5980.0, c=None, prev=5995.0),
            _summary_event("SPX", h=6050.0, lo=5980.0, c=6040.0, prev=5995.0),
        ],
    )
    row = conn.execute("SELECT * FROM stream_summary WHERE symbol='SPX'").fetchone()
    assert row["day_close"] == 6040.0
