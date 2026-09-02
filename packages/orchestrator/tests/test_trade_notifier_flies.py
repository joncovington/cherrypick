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

_COLS = (
    "position_id",
    "symbol",
    "arm",
    "entry_mode",
    "kind",
    "side",
    "center",
    "wing_width",
    "far_width",
    "roll_debit",
    "net",
    "floor_dollars",
    "entry_time",
    "completing_direction",
    "completed_at",
    "exit_time",
    "pnl",
    "pinned",
    "status",
)


class _Recorder:
    """Stand-in notifier that records instead of sending — no network on a test path."""

    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, body, embed=None):
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
        "entry_mode TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL, far_width REAL, "
        "roll_debit REAL, net REAL, floor_dollars REAL, entry_time TEXT, completing_direction TEXT, "
        "completed_at TEXT, exit_time TEXT, pnl REAL, pinned INTEGER, status TEXT)"
    )
    conn.executemany(
        f"INSERT INTO fly_positions ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})", rows
    )
    conn.commit()
    return conn


_SPREAD = _row(
    position_id="P1",
    entry_mode="legged",
    kind="short_vertical",
    side="put",
    center=6000,
    net=2.55,
    completing_direction="up",
    status="open",
)
_FLY = _row(
    position_id="P1",
    entry_mode="legged",
    kind="fly",
    side="put",
    center=6000,
    net=1.05,
    floor_dollars=98.11,
    completed_at="2026-07-20T13:12:00",
    status="open",
)
_SETTLED = _row(
    position_id="P1",
    entry_mode="legged",
    kind="fly",
    side="put",
    center=6000,
    net=1.05,
    floor_dollars=98.11,
    completed_at="2026-07-20T13:12:00",
    pnl=505.0,
    pinned=1,
    status="settled",
)


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
    bought = _row(
        position_id="P2", entry_mode="outright", kind="fly", side="put", center=6875, net=-0.20, status="open"
    )
    n = _Recorder()
    tn._flies_process(_conn([bought]), {}, n, "flies")
    assert "bought for $0.20 debit" in n.sent[0][1]


def test_bwb_roll_entry_reads_as_a_broken_wing_fly_not_a_short_spread():
    """A bwb_roll position buys the whole broken-wing butterfly in one order for a net credit —
    there is nothing left to complete. Before this fix it fell into the generic branch and read as
    a `short {side} spread ... completes ?`, which is exactly what a `bwb` arm should never say."""
    bwb = _row(
        position_id="P3",
        entry_mode="bwb_roll",
        kind="bwb",
        side="call",
        center=7740,
        wing_width=5,
        far_width=20,
        net=2.09,
        status="open",
    )
    n = _Recorder()
    tn._flies_process(_conn([bwb]), {}, n, "flies")
    body = n.sent[0][1]
    assert "broken-wing fly 7740 w5/20" in body
    assert "bought for $2.09 credit" in body
    assert "short" not in body and "completes" not in body


def test_debit_first_entry_reads_as_a_long_spread_paid_for_with_a_debit():
    """debit_first buys a debit vertical and completes later by SELLING a credit spread — the
    mirror image of `legged`. The generic branch got both the sign (net is negative, a debit) and
    the direction ("short" instead of "long") wrong for this mode."""
    long_leg = _row(
        position_id="P4",
        entry_mode="debit_first",
        kind="long_vertical",
        side="call",
        center=6100,
        net=-1.40,
        completing_direction="down",
        status="open",
    )
    n = _Recorder()
    tn._flies_process(_conn([long_leg]), {}, n, "flies")
    body = n.sent[0][1]
    assert "long call spread 6100" in body
    assert "bought for $1.40 debit" in body
    assert "down to complete" in body
    assert "sell the credit side" in body
    assert "short" not in body


def test_bwb_completion_reads_as_a_roll_not_a_generic_spread_completion():
    """A bwb completes by rolling its wide far wing IN to match the near wing (book.py: buy the
    near-width wing, sell the held far wing) — a different mechanism from legged/debit_first, which
    complete by trading the OTHER side. The message must say "rolled", not the generic
    "for $X net credit" phrasing that implies a fresh spread was sold to close it out."""
    rolled = _row(
        position_id="P6",
        entry_mode="bwb_roll",
        kind="fly",
        side="call",
        center=7740,
        wing_width=5,
        far_width=20,
        roll_debit=0.35,
        net=1.74,
        floor_dollars=140.00,
        completed_at="2026-08-05T11:52:00",
        status="open",
    )
    n = _Recorder()
    tn._flies_process(_conn([rolled]), {}, n, "flies")
    completion = next(b for k, b in n.sent if ".completion." in k)
    assert "rolled the wide wing in for $0.35 debit" in completion
    assert "$1.74 net credit" in completion
    assert "floor $140.00 after fees" in completion


def test_settled_bwb_that_never_rolled_still_reads_as_a_broken_wing_fly():
    """A bwb settling without ever rolling into a symmetric fly keeps kind == 'bwb', not 'fly' — the
    exit formatter must not fall back to labeling it a short spread either."""
    settled_bwb = _row(
        position_id="P5",
        entry_mode="bwb_roll",
        kind="bwb",
        side="call",
        center=7740,
        wing_width=5,
        far_width=20,
        net=2.09,
        pnl=-45.0,
        status="settled",
    )
    n = _Recorder()
    tn._flies_process(_conn([settled_bwb]), {}, n, "flies")
    exit_msg = next(b for k, b in n.sent if ".exit." in k)
    assert "broken-wing fly 7740" in exit_msg
    assert "short" not in exit_msg


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


def test_live_wrapper_makes_events_unmistakably_live():
    """The live ledger reuses the same formatters, so without the wrapper a real-money fill
    would read as 'Flies paper ENTRY' — the exact mislabeling paper↔live isolation forbids."""

    class _Inner:
        def __init__(self):
            self.sent = []

        def notify(self, level, key, title, message, embed=None):
            self.sent.append((level, key, title, message))

    inner = _Inner()
    tn._LiveNotifier(inner).notify(
        "INFO",
        "trade.flies.entry.P1",
        "Paper entry",
        _msg := ("\U0001f7e2 Flies paper ENTRY — SPX short put spread 6000 w5 credit $2.55"),
    )
    level, key, title, message = inner.sent[0]
    assert key.startswith("live.")  # separate dedup keyspace from the paper events
    # Regression (2026-07-30, flies' first live fill): the message body's "paper"->"LIVE" rewrite
    # was mirrored nowhere for the title, so a real fill announced itself as "LIVE: Paper entry" —
    # startswith("LIVE:") alone doesn't catch that. The title must be exactly "LIVE: entry", not
    # carry the schema formatters' hardcoded "Paper" word through under the live prefix.
    assert title == "LIVE: entry"
    assert "Paper" not in title
    assert " paper " not in message and " LIVE " in message
    assert message.startswith("\U0001f6a8")


def test_live_wrapper_strips_paper_from_every_schema_title():
    """The same wrapper is shared across meic/earnings/flies (trade_notifier.py:_SCHEMAS), whose
    stage titles are "Paper entry"/"Paper exit"/"Paper stop"/"Paper settled" — every shape must
    come out clean, not just flies' "entry"."""

    class _Inner:
        def __init__(self):
            self.sent = []

        def notify(self, level, key, title, message, embed=None):
            self.sent.append(title)

    inner = _Inner()
    wrapped = tn._LiveNotifier(inner)
    for raw_title in ("Paper entry", "Paper exit", "Paper stop", "Paper settled"):
        wrapped.notify("INFO", "trade.x", raw_title, "msg")
    assert inner.sent == ["LIVE: entry", "LIVE: exit", "LIVE: stop", "LIVE: settled"]
