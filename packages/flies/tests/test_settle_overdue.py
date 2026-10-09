"""`live_loop --settle-overdue`: a past session's live book settles at THAT session's official close,
never a provisional price (2026-10-08; the owner chose official-print-only)."""

from __future__ import annotations

import pytest
from test_live_scaffold import _open_entry_row, _settle_cfg

from cherrypick.flies import db as dbmod
from cherrypick.flies import live_loop

PAST = "2026-10-06"
TODAY = "2026-10-08"


@pytest.fixture
def live_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return dbmod.connect(dbmod.live_db_path())


def _past_fly(conn, pid="E1", entry="filled"):
    dbmod.save_position(
        conn,
        {
            **_open_entry_row(entry_fill_status=entry),
            "position_id": pid,
            "book_id": f"{PAST}:gex:SPX",
            "trade_date": PAST,
            "kind": "fly",
            "net": 0.25,
            "debit": 0.80,
        },
    )


def test_a_past_book_settles_at_that_sessions_official_close(live_conn):
    _past_fly(live_conn)
    asked = []

    def close(symbol, session):
        asked.append((symbol, session))
        return 7494.0

    out = live_loop.settle_overdue(_settle_cfg(), live_conn, cache_path="unused", today=TODAY, close_fn=close)
    assert asked == [("SPX", PAST)] and [s["session"] for s in out["settled"]] == [PAST]
    pos = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert pos["status"] == "settled" and pos["settlement_price"] == 7494.0
    assert pos["settlement_source"] == "yahoo_daily"
    assert live_loop.overdue_settlement(live_conn, TODAY) == []


def test_no_official_close_leaves_it_open_and_never_guesses(live_conn, monkeypatch):
    from cherrypick.flies import provider as providermod

    _past_fly(live_conn)
    monkeypatch.setattr(providermod, "read_spot", lambda *a, **k: 7000.0)  # a provisional price exists
    out = live_loop.settle_overdue(
        _settle_cfg(), live_conn, cache_path="unused", today=TODAY, close_fn=lambda s, d: None
    )
    assert out["settled"] == [] and out["left"] == [{"session": PAST, "reason": "no_official_close"}]
    assert live_conn.execute("SELECT status FROM fly_positions").fetchone()["status"] == "open"


def test_a_session_with_a_pending_entry_is_left_for_a_person(live_conn):
    _past_fly(live_conn)
    _past_fly(live_conn, pid="E2", entry="pending")
    out = live_loop.settle_overdue(
        _settle_cfg(), live_conn, cache_path="unused", today=TODAY, close_fn=lambda s, d: 7494.0
    )
    assert out["settled"] == [] and out["left"] == [{"session": PAST, "reason": "pending_entries"}]
