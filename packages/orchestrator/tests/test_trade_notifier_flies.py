"""Three-stage trade notifications for the `fly_book` schema (cherrypick-flies).

Unit lane: an in-memory flies paper DB, asserting each stage's watermark and message. The stage that
matters most is COMPLETION — the moment a credit spread becomes a butterfly held for a net credit and
its floor turns into a guarantee. MEIC and earnings only have two stages; this one has three, and the
extra one is the strategy's whole point.
"""

import sqlite3

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit

_COLS = ("position_id", "symbol", "arm", "entry_mode", "kind", "side", "center", "wing_width",
         "net", "floor_dollars", "completing_direction", "completed_at", "pnl", "pinned", "status")


class _Recorder:
    """Stand-in notifier that records instead of sending — no network on a test path."""

    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, body):
        self.sent.append((key, body))


def _row(**kw):
    kw.setdefault("symbol", "SPX")
    kw.setdefault("arm", "gex")
    kw.setdefault("wing_width", 5)
    kw.setdefault("pinned", 0)
    return tuple(kw.get(c) for c in _COLS)


def _conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE fly_positions (position_id TEXT PRIMARY KEY, symbol TEXT, arm TEXT, "
        "entry_mode TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL, net REAL, "
        "floor_dollars REAL, completing_direction TEXT, completed_at TEXT, pnl REAL, "
        "pinned INTEGER, status TEXT)"
    )
    conn.executemany(
        f"INSERT INTO fly_positions ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})", rows
    )
    conn.commit()
    return conn


_SPREAD = _row(position_id="P1", entry_mode="legged", kind="short_vertical", side="put",
               center=6000, net=2.55, completing_direction="up", status="open")
_FLY = _row(position_id="P1", entry_mode="legged", kind="fly", side="put", center=6000,
            net=1.05, floor_dollars=98.11, completed_at="2026-07-20T13:12:00", status="open")
_SETTLED = _row(position_id="P1", entry_mode="legged", kind="fly", side="put", center=6000,
                net=1.05, floor_dollars=98.11, completed_at="2026-07-20T13:12:00",
                pnl=505.0, pinned=1, status="settled")


def test_entry_message_states_which_way_spot_must_move():
    """An open credit spread is a promise, not a position — the operator needs to know what has to
    happen for it to become the risk-free thing it is meant to become."""
    n = _Recorder()
    tn._flies_process(_conn([_SPREAD]), {}, n, "flies")
    body = n.sent[0][1]
    assert "SPX" in body and "short put spread 6000" in body
    assert "credit $2.55" in body
    assert "up to complete" in body


def test_completion_message_leads_with_the_post_fee_floor():
    """A floor stated before fees is marketing. This message quotes the number after them."""
    n = _Recorder()
    tn._flies_process(_conn([_FLY]), {}, n, "flies")
    completion = next(b for k, b in n.sent if ".completion." in k)
    assert "COMPLETED" in completion
    assert "$1.05 net credit" in completion
    assert "floor $98.11 after fees" in completion


def test_outright_entry_reads_as_a_debit_not_a_credit():
    bought = _row(position_id="P2", entry_mode="outright", kind="fly", side="put", center=6875,
                  net=-0.20, status="open")
    n = _Recorder()
    tn._flies_process(_conn([bought]), {}, n, "flies")
    assert "bought for $0.20 debit" in n.sent[0][1]


def test_settlement_message_reports_the_pin():
    n = _Recorder()
    tn._flies_process(_conn([_SETTLED]), {}, n, "flies")
    exit_msg = next(b for k, b in n.sent if ".exit." in k)
    assert "SETTLED" in exit_msg and "(pinned)" in exit_msg and "P&L $+505.00" in exit_msg


def test_each_stage_notifies_once():
    """A position passes through all three stages, and the watermarks must keep each to a single
    push — the notifier runs on both a fast task and every watchdog tick."""
    conn = _conn([_FLY])
    state, n = {}, _Recorder()
    counts = tn._flies_process(conn, state, n, "flies")
    assert counts["entrys_notified"] == 1 and counts["completions_notified"] == 1

    again = tn._flies_process(conn, state, _Recorder(), "flies")
    assert all(v == 0 for v in again.values()), "re-running must not re-notify"


def test_seed_does_not_backfill_pre_existing_positions():
    """First activation adopts the current DB state rather than replaying the session as a burst."""
    conn = _conn([_SETTLED])
    state = tn._flies_seed(conn)
    n = _Recorder()
    counts = tn._flies_process(conn, state, n, "flies")
    assert n.sent == []
    assert all(v == 0 for v in counts.values())
