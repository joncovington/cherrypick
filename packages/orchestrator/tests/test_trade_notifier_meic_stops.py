"""Per-wing stop notifications for the `meic_ic` schema.

Unit lane: an in-memory MEIC `ic_trades` DB. A MEIC iron condor can lose one wing well before the whole
position closes — a single wing hitting its stop sets put/call_stop_cost but leaves exit_time NULL, so
it is a distinct event from the eventual exit and is watermarked per (id, wing). These tests pin that a
stop fires once per wing, that a both-wing stop fires twice, that a second wing stopping later fires
again, and that neither first activation nor legacy state (written before the feature) backfills the
session's existing stops as a burst.
"""

import sqlite3

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit

_COLS = ("id", "symbol", "risk_profile", "put_strike", "call_strike", "wing_width", "net_credit",
         "quantity", "status", "exit_time", "exit_reason", "pnl", "put_stop_cost", "call_stop_cost")


class _Recorder:
    """Stand-in notifier that records instead of sending — no network on a test path."""

    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, body):
        self.sent.append((key, body))


def _row(**kw):
    kw.setdefault("symbol", "SPX")
    kw.setdefault("risk_profile", "moderate")
    kw.setdefault("put_strike", 6000)
    kw.setdefault("call_strike", 6100)
    kw.setdefault("wing_width", 20)
    kw.setdefault("net_credit", 3.0)
    kw.setdefault("quantity", 1)
    kw.setdefault("status", "open")
    return tuple(kw.get(c) for c in _COLS)


def _conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, "
        "put_strike REAL, call_strike REAL, wing_width REAL, net_credit REAL, quantity INTEGER, "
        "status TEXT, exit_time TEXT, exit_reason TEXT, pnl REAL, put_stop_cost REAL, "
        "call_stop_cost REAL)"
    )
    conn.executemany(
        f"INSERT INTO ic_trades ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})", rows
    )
    conn.commit()
    return conn


def test_new_put_stop_fires_once_with_wing_detail():
    """A wing stop is the operator-facing event the account owner asked to hear about — it names the
    side, the strike, the cost paid to close it, and the profile."""
    state = tn._meic_seed(_conn([_row(id=1, status="open")]))  # seeded with no stops
    stopped = _conn([_row(id=1, status="partial", put_stop_cost=1.98)])
    n = _Recorder()
    counts = tn._meic_process(stopped, state, n, "meic")

    assert counts["stops_notified"] == 1
    key, body = n.sent[0]
    assert key == "trade.meic.stop.1:put"
    assert "PUT wing 6000P" in body and "stopped @ $1.98" in body and "[moderate]" in body

    again = tn._meic_process(stopped, state, _Recorder(), "meic")
    assert again["stops_notified"] == 0, "the per-wing watermark must keep a stop to a single push"


def test_both_wings_stopped_fires_two_distinct_pushes():
    state = tn._meic_seed(_conn([_row(id=1, status="open")]))
    both = _conn([_row(id=1, status="stopped", put_stop_cost=1.9, call_stop_cost=2.1)])
    n = _Recorder()
    counts = tn._meic_process(both, state, n, "meic")

    assert counts["stops_notified"] == 2
    assert sorted(k for k, _ in n.sent) == ["trade.meic.stop.1:call", "trade.meic.stop.1:put"]


def test_second_wing_stopping_later_fires_again():
    """One wing stops, then later the other — the surviving wing is a separate event on the same id,
    so the per-(id, wing) watermark must let the second one through."""
    state = tn._meic_seed(_conn([_row(id=1, status="open")]))
    put_only = _conn([_row(id=1, status="partial", put_stop_cost=1.9)])
    tn._meic_process(put_only, state, _Recorder(), "meic")

    both = _conn([_row(id=1, status="partial", put_stop_cost=1.9, call_stop_cost=2.1)])
    n = _Recorder()
    counts = tn._meic_process(both, state, n, "meic")

    assert counts["stops_notified"] == 1
    assert n.sent[0][0] == "trade.meic.stop.1:call"


def test_stop_and_exit_are_independent():
    """A wing stop and the later whole-IC close are two events on one id — the stop must not suppress
    the exit, nor the exit re-announce the stop."""
    state = tn._meic_seed(_conn([_row(id=1, status="open")]))
    stopped = _conn([_row(id=1, status="partial", put_stop_cost=1.9)])
    c1 = tn._meic_process(stopped, state, _Recorder(), "meic")
    assert c1["stops_notified"] == 1 and c1["exits_notified"] == 0

    closed = _conn([_row(id=1, status="closed", put_stop_cost=1.9, exit_time="2026-07-22T16:00",
                         exit_reason="expired", pnl=-50.0)])
    n = _Recorder()
    c2 = tn._meic_process(closed, state, n, "meic")
    assert c2["stops_notified"] == 0 and c2["exits_notified"] == 1
    assert any("EXIT" in b for _, b in n.sent)


def test_first_activation_does_not_backfill_existing_stops():
    conn = _conn([_row(id=1, status="partial", put_stop_cost=1.9)])
    state = tn._meic_seed(conn)
    n = _Recorder()
    counts = tn._meic_process(conn, state, n, "meic")
    assert n.sent == [] and counts["stops_notified"] == 0


def test_legacy_state_without_stop_watermark_seeds_instead_of_bursting():
    """State written before this feature has no notified_stop_keys. The first run must adopt the
    current stops (like a seed), not replay every open partial as a burst of pushes."""
    conn = _conn([_row(id=1, status="partial", put_stop_cost=1.9),
                  _row(id=2, status="partial", call_stop_cost=2.1)])
    state = {"last_entry_id": 2, "notified_exit_ids": []}  # legacy: no stop watermark
    n = _Recorder()
    counts = tn._meic_process(conn, state, n, "meic")

    assert n.sent == [] and counts["stops_notified"] == 0
    assert set(state["notified_stop_keys"]) == {"1:put", "2:call"}
