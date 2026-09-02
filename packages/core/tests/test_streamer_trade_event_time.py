"""A trade row records two clocks, and they are not interchangeable.

`updated_at` is when this process RECEIVED the event. `event_at` is when the exchange PRINTED the
trade. They agree on a live tape and come apart on a reconnect: resubscribing replays a snapshot of
the last print, so `updated_at` says "seconds ago" about a price that is hours old.

That difference is not hypothetical. `packages/curve` computed its daily VIX/VIX3M regime basis
from `updated_at` and settled two sessions on the prior day's closes -- 2026-08-31 at 01:21 ET and
2026-09-02 at 02:38 ET, each within a minute of a reconnect, each carrying the prior close to the
cent -- and 09-02 traded through the gate that reading fed.
"""

import asyncio
import time

from cherrypick.core import streamcache
from cherrypick.core.streamer import ChainStreamer, _State


class Trade:
    """The listener selects on the event class only; the payloads below carry the fields."""


class _Event:
    def __init__(self, symbol, price, *, time_ms=None, change=0.0, day_volume=0.0):
        self.event_symbol = symbol
        self.price = price
        self.change = change
        self.day_volume = day_volume
        self.time = time_ms


class _FakeStreamer:
    """Delivers a fixed burst of events, then ends the stream so the listener returns."""

    def __init__(self, events):
        self._events = events

    def listen(self, event_type):
        async def gen():
            for event in self._events:
                yield event

        return gen()


def _drain(tmp_path, events):
    engine = ChainStreamer(
        session_factory=lambda: None,
        db_path=tmp_path / "cache.db",
        symbols=["VIX"],
        window_strike_count=20,
    )
    conn = streamcache.connect(tmp_path / "cache.db")
    state = _State(conn, ["VIX"])
    asyncio.run(engine._listen_trade(_FakeStreamer(events), state, Trade))
    conn.commit()
    return conn


def _row(conn, symbol="VIX"):
    return conn.execute("SELECT * FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()


def test_the_print_time_is_recorded_separately_from_the_receive_time(tmp_path):
    """A reconnect snapshot: received now, printed at yesterday's close.

    Reading `updated_at` for price age answers "34 seconds" about a price from the previous
    session. `event_at` is the column that answers the question actually being asked.
    """
    printed = time.time() - 9 * 3600  # yesterday's close, nine hours back
    conn = _drain(tmp_path, [_Event("VIX", 16.34, time_ms=printed * 1000.0)])

    row = _row(conn)
    assert row["last"] == 16.34
    assert abs(row["updated_at"] - time.time()) < 5, "received now -- the feed is alive"
    assert abs(row["event_at"] - printed) < 1, "but the print is hours old, and the row says so"
    assert row["updated_at"] - row["event_at"] > 8 * 3600, "the two clocks must not be conflated"


def test_an_event_without_a_usable_time_leaves_the_column_null(tmp_path):
    """NULL means "this print's own time was never recorded", which a caller has to handle anyway.

    Falling back to `updated_at` here would be the original defect wearing the new column's name:
    every consumer would read a receive time believing it was a print time, and nothing in the row
    would say otherwise.
    """
    for payload in ([_Event("VIX", 16.34, time_ms=None)], [_Event("VIX", 16.34, time_ms=0)]):
        conn = _drain(tmp_path / str(id(payload)), payload)
        assert _row(conn)["event_at"] is None


def test_a_live_tick_has_both_clocks_agreeing(tmp_path):
    """The ordinary case, pinned so the column cannot quietly start recording something else."""
    printed = time.time() - 0.4
    conn = _drain(tmp_path, [_Event("VIX", 15.20, time_ms=printed * 1000.0)])

    row = _row(conn)
    assert abs(row["updated_at"] - row["event_at"]) < 5


def test_a_later_print_replaces_an_earlier_one(tmp_path):
    """The row is an upsert keyed on symbol, so `event_at` has to move with `last`.

    A stale `event_at` left beside a fresh price would make a live quote look ancient, which fails
    in the opposite direction but is the same bug: the two columns describing different events.
    """
    old = time.time() - 9 * 3600
    new = time.time() - 1.0
    conn = _drain(
        tmp_path,
        [
            _Event("VIX", 16.34, time_ms=old * 1000.0),
            _Event("VIX", 15.20, time_ms=new * 1000.0),
        ],
    )

    row = _row(conn)
    assert row["last"] == 15.20
    assert abs(row["event_at"] - new) < 1, "the row describes one event, not two halves of two"


def test_a_cache_written_before_the_column_existed_still_opens(tmp_path):
    """The additive migration. An older producer's rows keep NULL rather than blocking the open."""
    path = tmp_path / "legacy.db"
    conn = streamcache.connect(path)
    conn.execute("ALTER TABLE stream_trades DROP COLUMN event_at")
    conn.execute(
        "INSERT INTO stream_trades(symbol, last, change, volume, updated_at) VALUES (?,?,?,?,?)",
        ("VIX", 16.34, 0.0, 0.0, time.time()),
    )
    conn.commit()
    conn.close()

    reopened = streamcache.connect(path)
    row = _row(reopened)
    assert row["last"] == 16.34, "the pre-existing row survives the migration"
    assert row["event_at"] is None, "and reads as never-recorded rather than as a receive time"
