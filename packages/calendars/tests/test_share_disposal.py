"""Characterization tests for share disposal — the American-physical-settlement tail.

Written BEFORE `_dispose_shares` moves into `cherrypick.core.settlement`, because coverage of this
function was 20%: the loop body that actually sells the shares barely executed under the existing
suite, so a refactor could have changed it silently. These pin the observable behaviour — what is
selected, what is refused, what is written, what is logged — so the extraction has something to be
wrong against.

They deliberately assert on ledger state rather than on internals, so they keep their meaning once
the mechanics live in core behind an adapter.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest
from cherrypick.core import streamcache

from cherrypick.calendars import db, paper_loop

SYMBOL = "SPX"
FRIDAY = "2026-08-21"
MONDAY = "2026-08-24"


def _cache(tmp_path, *, spot=6500.0, age=0.0):
    path = tmp_path / "stream_cache.db"
    conn = streamcache.connect(path)
    if spot is not None:
        conn.execute(
            "INSERT OR REPLACE INTO stream_trades(symbol, last, change, volume, updated_at)"
            " VALUES (?,?,?,?,?)",
            (SYMBOL, spot, 0.0, 0.0, time.time() - age),
        )
    conn.commit()
    conn.close()
    return str(path)


def _position(conn, position_id="p1", book="path"):
    db.save_position(
        conn,
        {
            "position_id": position_id,
            "week_of": FRIDAY,
            "entry_session": FRIDAY,
            "book": book,
            "side": "call",
            "symbol": SYMBOL,
            "structure": "double_calendar",
            "front_expiration": FRIDAY,
            "back_expiration": MONDAY,
            "strike": 6500.0,
            "quantity": 1,
            "status": "open",
        },
    )


def _assignment(conn, *, position_id="p1", session=FRIDAY, direction="short", shares=100,
                basis=6500.0, leg_role="front_call"):
    db.save_assignment(
        conn,
        {
            "position_id": position_id,
            "leg_role": leg_role,
            "symbol": SYMBOL,
            "assigned_session": session,
            "direction": direction,
            "shares": shares,
            "basis": basis,
            "strike": basis,
            "option_type": "call",
            "status": "open",
        },
    )


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "calendars.db"))
    yield c
    c.close()


def _run(config, conn, cache_path, day=MONDAY):
    return paper_loop._dispose_shares(
        config, conn, cache_path=cache_path, when=datetime(2026, 8, 24, 10, 0), day=day
    )


CONFIG: dict = {"defaults": {"max_quote_age_seconds": 300}}


# --------------------------------------------------------------------------- selection


def test_no_assignments_is_a_no_op(conn, tmp_path):
    assert _run(CONFIG, conn, _cache(tmp_path)) == 0


def test_shares_delivered_on_an_earlier_session_are_disposed(conn, tmp_path):
    _position(conn)
    _assignment(conn, session=FRIDAY)
    assert _run(CONFIG, conn, _cache(tmp_path), day=MONDAY) == 1

    rows = db.assignments_for(conn, "p1")
    assert len(rows) == 1
    assert rows[0]["status"] == "disposed"
    assert rows[0]["disposed_session"] == MONDAY
    assert rows[0]["disposal_price"] == pytest.approx(6500.0)


def test_shares_delivered_tonight_are_left_alone(conn, tmp_path):
    """The weekend-exposure rule: tonight's settlement cannot be sold tonight.

    This interval -- Friday's assignment to Monday's disposal -- is the exposure a cash-settled
    underlying never has, and it is left visible in the ledger rather than netted away.
    """
    _position(conn)
    _assignment(conn, session=MONDAY)
    assert _run(CONFIG, conn, _cache(tmp_path), day=MONDAY) == 0
    assert db.assignments_for(conn, "p1")[0]["status"] == "open"


# --------------------------------------------------------------------------- refusal


def test_a_missing_spot_refuses_rather_than_guessing(conn, tmp_path):
    """The shares stay open and the next tick retries; nothing is invented."""
    _position(conn)
    _assignment(conn, session=FRIDAY)
    assert _run(CONFIG, conn, _cache(tmp_path, spot=None)) == 0

    assert db.assignments_for(conn, "p1")[0]["status"] == "open"
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM dc_management_events WHERE action = 'dispose_shares'")]
    assert len(events) == 1
    assert events[0]["executed"] == 0
    assert events[0]["gate"] == "no_spot"


def test_a_stale_spot_is_refused_like_an_absent_one(conn, tmp_path):
    _position(conn)
    _assignment(conn, session=FRIDAY)
    cache = _cache(tmp_path, spot=6500.0, age=10_000)
    assert _run(CONFIG, conn, cache) == 0
    assert db.assignments_for(conn, "p1")[0]["status"] == "open"


def test_a_refusal_is_retried_on_the_next_tick(conn, tmp_path):
    _position(conn)
    _assignment(conn, session=FRIDAY)
    assert _run(CONFIG, conn, _cache(tmp_path, spot=None)) == 0
    assert _run(CONFIG, conn, _cache(tmp_path, spot=6510.0)) == 1
    assert db.assignments_for(conn, "p1")[0]["status"] == "disposed"


# --------------------------------------------------------------------------- accounting


def test_share_pnl_and_the_executed_event_are_both_recorded(conn, tmp_path):
    _position(conn)
    _assignment(conn, session=FRIDAY, direction="short", shares=100, basis=6500.0)
    assert _run(CONFIG, conn, _cache(tmp_path, spot=6400.0)) == 1

    row = db.assignments_for(conn, "p1")[0]
    # short 100 @ 6500 covered at 6400 -> +10,000 before fees
    assert row["share_pnl"] == pytest.approx(10_000.0)
    assert row["fees"] is not None

    events = [dict(r) for r in conn.execute(
        "SELECT * FROM dc_management_events WHERE action = 'dispose_shares'")]
    assert [e["executed"] for e in events] == [1]


def test_a_long_delivery_prices_the_other_way(conn, tmp_path):
    _position(conn)
    _assignment(conn, session=FRIDAY, direction="long", shares=100, basis=6500.0)
    _run(CONFIG, conn, _cache(tmp_path, spot=6400.0))
    assert db.assignments_for(conn, "p1")[0]["share_pnl"] == pytest.approx(-10_000.0)


def test_every_open_assignment_is_disposed_in_one_pass(conn, tmp_path):
    _position(conn, "p1")
    _position(conn, "p2")
    _assignment(conn, position_id="p1", session=FRIDAY, leg_role="front_call")
    _assignment(conn, position_id="p2", session=FRIDAY, leg_role="front_call")
    assert _run(CONFIG, conn, _cache(tmp_path)) == 2
    assert db.assignments_for(conn, "p1")[0]["status"] == "disposed"
    assert db.assignments_for(conn, "p2")[0]["status"] == "disposed"


def test_the_spot_is_read_once_per_symbol_not_once_per_assignment(conn, tmp_path, monkeypatch):
    """Batched by symbol: the disposal pass must not reopen the shared cache per row."""
    _position(conn, "p1")
    _position(conn, "p2")
    _assignment(conn, position_id="p1", session=FRIDAY)
    _assignment(conn, position_id="p2", session=FRIDAY)

    from cherrypick.calendars import provider

    calls = []
    real = provider.read_spot
    monkeypatch.setattr(
        provider, "read_spot",
        lambda *a, **kw: (calls.append(a[1] if len(a) > 1 else None), real(*a, **kw))[1],
    )
    _run(CONFIG, conn, _cache(tmp_path))
    assert len(calls) == 1, f"expected one spot read for one symbol, got {len(calls)}"
