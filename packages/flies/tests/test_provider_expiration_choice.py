"""The expiration this module trades is today's, at every hour of the session.

`stream_chain` keeps rows for expirations the producer has subscribed at any point, so a chain for a
later date sits in the cache indefinitely with quotes that stopped updating days ago. The selector
used to rank candidates by `ABS(JULIANDAY(expiration) - JULIANDAY('now'))` — absolute distance from
the current UTC instant against an expiration's midnight — so past 12:00 UTC (08:00 ET) tomorrow
scored nearer than today. On 2026-08-20 that returned a six-day-old copy of the 08-21 chain from
mid-morning onward: every quote in it was stale, so every tick refused with `no_fresh_quotes` and
the session recorded 212 refusals and zero iterations.
"""

import json
import sqlite3

import pytest
from cherrypick.core import streamcache

from cherrypick.flies.provider import nearest_expiration

TODAY = "2026-08-20"


def _cache(tmp_path, expirations):
    conn = streamcache.connect(tmp_path / "cache.db")
    for exp in expirations:
        conn.execute(
            "INSERT INTO stream_chain (streamer_symbol, expiration, underlying_symbol, data_json, "
            "updated_at) VALUES (?,?,?,?,?)",
            (f".SPX{exp}C7500", exp, "SPX", json.dumps({"strike_price": 7500}), 0.0),
        )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def test_tomorrows_stale_chain_never_wins(tmp_path):
    """The production case: both dates cached, tomorrow's rows long dead."""
    conn = _cache(tmp_path, ["2026-08-21", TODAY])
    assert nearest_expiration(conn, "SPX", TODAY) == TODAY


def test_the_answer_does_not_depend_on_the_time_of_day(tmp_path):
    """The old ordering flipped at 12:00 UTC. Nothing here reads a clock, which is the point."""
    conn = _cache(tmp_path, ["2026-08-21", TODAY])
    assert nearest_expiration(conn, "SPX", TODAY) == TODAY
    assert nearest_expiration(conn, "SPX", TODAY) == TODAY


def test_expired_chains_are_never_returned(tmp_path):
    conn = _cache(tmp_path, ["2026-08-18", "2026-08-19", TODAY, "2026-08-21"])
    assert nearest_expiration(conn, "SPX", TODAY) == TODAY


def test_only_past_chains_cached_returns_nothing(tmp_path):
    """Refusing is correct — the module would rather report thin data than trade a dead chain."""
    conn = _cache(tmp_path, ["2026-08-18", "2026-08-19"])
    assert nearest_expiration(conn, "SPX", TODAY) is None


def test_falls_forward_when_today_is_not_cached(tmp_path):
    """The soonest LIVE expiration, which the engine then refuses as `no_0dte_expiration` — a
    stated refusal rather than a silently mispriced trade."""
    conn = _cache(tmp_path, ["2026-08-21", "2026-08-24"])
    assert nearest_expiration(conn, "SPX", TODAY) == "2026-08-21"


@pytest.mark.parametrize("other", ["XSP", "SPY"])
def test_another_underlying_never_leaks_in(tmp_path, other):
    """SPX and XSP share 0DTE dates with a 10x strike difference between them."""
    conn = _cache(tmp_path, ["2026-08-21"])
    conn.execute(
        "INSERT INTO stream_chain (streamer_symbol, expiration, underlying_symbol, data_json, "
        "updated_at) VALUES (?,?,?,?,?)",
        (f".{other}{TODAY}C750", TODAY, other, json.dumps({"strike_price": 750}), 0.0),
    )
    conn.commit()
    assert nearest_expiration(conn, "SPX", TODAY) == "2026-08-21"
    assert nearest_expiration(conn, other, TODAY) == TODAY
