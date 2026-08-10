"""The study-arm digest: `meic_ic` rows tagged with a study profile (risk_profile prefix, e.g.
`gex-`) are excluded from the normal per-trade push and instead accumulate into a periodic
per-symbol summary. Unit lane: an in-memory MEIC `ic_trades` DB, same shape as
test_trade_notifier_meic_stops.py.

Built for the wing-width study (retired 2026-08-05); the mechanism is prefix-driven and outlived it,
so these exercise it through the GEX arms that use it now.
"""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import trade_notifier as tn


def _et_epoch(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("America/New_York")).timestamp()


pytestmark = pytest.mark.unit

_COLS = (
    "id",
    "symbol",
    "risk_profile",
    "put_strike",
    "call_strike",
    "wing_width",
    "net_credit",
    "quantity",
    "status",
    "entry_time",
    "exit_time",
    "exit_reason",
    "pnl",
    "fees",
    "put_stop_cost",
    "call_stop_cost",
)


class _Recorder:
    def __init__(self):
        self.sent = []
        self.embeds = []

    def notify(self, level, key, title, body, embed=None):
        self.sent.append((key, body))
        self.embeds.append(embed)


def _row(**kw):
    kw.setdefault("symbol", "XSP")
    kw.setdefault("risk_profile", "gex-open")
    kw.setdefault("put_strike", 590)
    kw.setdefault("call_strike", 600)
    kw.setdefault("wing_width", 2)
    kw.setdefault("net_credit", 0.5)
    kw.setdefault("quantity", 1)
    kw.setdefault("status", "open")
    return tuple(kw.get(c) for c in _COLS)


def _conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, "
        "put_strike REAL, call_strike REAL, wing_width REAL, net_credit REAL, quantity INTEGER, "
        "status TEXT, entry_time TEXT, exit_time TEXT, exit_reason TEXT, pnl REAL, fees REAL, "
        "put_stop_cost REAL, call_stop_cost REAL)"
    )
    conn.executemany(f"INSERT INTO ic_trades ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})", rows)
    conn.commit()
    return conn


_PREFIXES = ("gex-",)


def test_study_entry_lands_in_digest_not_per_trade():
    conn = _conn([_row(id=1, status="open")])
    state = tn._meic_seed(_conn([]))
    n = _Recorder()
    counts = tn._meic_process(conn, state, n, "meic", summary_prefixes=_PREFIXES, now=1000.0)

    assert counts["entries_notified"] == 1
    assert n.sent == [], "a study entry must never fire a per-trade push"
    assert state["pending_summary"]["XSP"]["entries"] == ["gex-open"]
    assert state["last_entry_id"] == 1, "the watermark must advance even though it's digest-routed"


def test_ladder_entry_still_fires_per_trade():
    conn = _conn([_row(id=1, status="open", risk_profile="conservative")])
    state = tn._meic_seed(_conn([]))
    n = _Recorder()
    counts = tn._meic_process(conn, state, n, "meic", summary_prefixes=_PREFIXES, now=1000.0)

    assert counts["entries_notified"] == 1
    assert len(n.sent) == 1 and "ENTRY" in n.sent[0][1]
    assert state.get("pending_summary", {}) == {}


def test_no_flush_before_interval_elapses():
    conn = _conn([_row(id=1, status="open")])
    state = tn._meic_seed(_conn([]))
    n = _Recorder()
    tn._meic_process(
        conn, state, n, "meic", summary_prefixes=_PREFIXES, summary_interval_minutes=15, now=1000.0
    )
    assert n.sent == []

    # Ten minutes later — still under the 15-minute interval, still no push.
    n2 = _Recorder()
    tn._meic_process(
        conn, state, n2, "meic", summary_prefixes=_PREFIXES, summary_interval_minutes=15, now=1000.0 + 10 * 60
    )
    assert n2.sent == []
    assert state["pending_summary"]["XSP"]["entries"] == ["gex-open"]


def test_flush_after_interval_emits_one_digest_and_clears_bucket():
    conn = _conn(
        [
            _row(id=1, symbol="XSP", risk_profile="gex-open", status="open"),
            _row(id=2, symbol="XSP", risk_profile="gex-blocked", status="open"),
            _row(id=3, symbol="QQQ", risk_profile="gex-blocked", status="open"),
        ]
    )
    state = tn._meic_seed(_conn([]))
    n = _Recorder()
    start = _et_epoch(2026, 7, 28, 13, 1)
    tn._meic_process(conn, state, n, "meic", summary_prefixes=_PREFIXES, now=start)
    assert n.sent == []

    closed = _conn(
        [
            _row(
                id=1,
                symbol="XSP",
                risk_profile="gex-open",
                status="closed",
                exit_time="2026-07-28T13:00",
                exit_reason="expired",
                pnl=25.0,
                fees=1.5,
            ),
            _row(id=2, symbol="XSP", risk_profile="gex-blocked", status="open"),
            _row(id=3, symbol="QQQ", risk_profile="gex-blocked", status="open"),
        ]
    )
    n2 = _Recorder()
    later = start + 15 * 60
    counts = tn._meic_process(
        closed, state, n2, "meic", summary_prefixes=_PREFIXES, summary_interval_minutes=15, now=later
    )

    assert counts["summary_pushed"] is True
    assert len(n2.sent) == 1
    key, body = n2.sent[0]
    assert key.startswith("trade.meic.summary.")
    assert "MEIC digest" in body and "ET —" in body
    assert "XSP:" in body and "QQQ:" in body
    assert "1 exit net +$24" in body  # 25.0 pnl - 1.5 fees, rounded
    assert "day 1 trades net +$24" in body
    assert state["pending_summary"] == {}, "the bucket must clear after a flush"


def test_digest_counts_arms_and_carries_a_card():
    """Repeated arms are counted ('open×3'), never listed one label per entry, and the flush pushes
    a Discord card whose per-symbol field carries the same figures as the plain line."""
    rows = [
        _row(id=i, symbol="SPX", risk_profile=arm, status="open")
        for i, arm in enumerate(["open", "open", "open", "width-5", "width-5", "width-10"], start=1)
    ]
    state = tn._meic_seed(_conn([]))
    n = _Recorder()
    start = _et_epoch(2026, 8, 10, 12, 36)
    tn._meic_process(_conn(rows), state, n, "meic", summary_prefixes=("",), now=start)

    n2 = _Recorder()
    tn._meic_process(
        _conn(rows), state, n2, "meic", summary_prefixes=("",), summary_interval_minutes=10, now=start + 600
    )
    assert len(n2.sent) == 1
    _, body = n2.sent[0]
    assert "6 entries (open×3 width-10 width-5×2)" in body
    assert body.count("open") == 1, "each arm appears once with a count, not once per entry"

    embed = n2.embeds[0]
    assert embed["color"] == tn.COLOR_DIGEST
    assert [f["name"] for f in embed["fields"]] == ["SPX"]
    assert "6 entries (open×3 width-10 width-5×2)" in embed["fields"][0]["value"]


def test_empty_window_flushes_nothing():
    """No study trades at all this run — the interval elapsing with an empty bucket must not push a
    heartbeat digest."""
    conn = _conn([])
    state = tn._meic_seed(conn)
    n = _Recorder()
    tn._meic_process(conn, state, n, "meic", summary_prefixes=_PREFIXES, now=1000.0)
    n2 = _Recorder()
    counts = tn._meic_process(
        conn, state, n2, "meic", summary_prefixes=_PREFIXES, summary_interval_minutes=15, now=1000.0 + 15 * 60
    )
    assert n2.sent == []
    assert counts["summary_pushed"] is False


def test_summary_mode_routes_every_profile_to_digest():
    """notify.trade_summary.mode='summary' resolves to the empty prefix, which matches every
    risk_profile — a profile the prefix list would have pushed per-trade lands in the digest."""
    conn = _conn([_row(id=1, status="open", risk_profile="conservative")])
    state = tn._meic_seed(_conn([]))
    n = _Recorder()
    counts = tn._meic_process(conn, state, n, "meic", summary_prefixes=("",), now=1000.0)

    assert counts["entries_notified"] == 1
    assert n.sent == []
    assert state["pending_summary"]["XSP"]["entries"] == ["conservative"]


def test_watermark_advances_once_per_row_across_both_paths():
    """A study exit and a ladder exit in the same tick must each advance notified_exit_ids exactly
    once — no double-count between the digest path and the per-trade path."""
    conn = _conn(
        [
            _row(
                id=1,
                symbol="XSP",
                risk_profile="gex-open",
                status="closed",
                exit_time="2026-07-28T13:00",
                pnl=10.0,
                fees=1.0,
            ),
            _row(
                id=2,
                symbol="XSP",
                risk_profile="conservative",
                status="closed",
                exit_time="2026-07-28T13:05",
                pnl=-5.0,
                fees=1.0,
            ),
        ]
    )
    state = {"last_entry_id": 2, "notified_exit_ids": []}
    n = _Recorder()
    counts = tn._meic_process(conn, state, n, "meic", summary_prefixes=_PREFIXES, now=1000.0)

    assert counts["exits_notified"] == 2
    assert state["notified_exit_ids"] == [1, 2]
    assert len(n.sent) == 1  # only the ladder exit fires a per-trade push
    again = tn._meic_process(conn, state, _Recorder(), "meic", summary_prefixes=_PREFIXES, now=1001.0)
    assert again["exits_notified"] == 0, "both ids must be watermarked, not just the notified one"
