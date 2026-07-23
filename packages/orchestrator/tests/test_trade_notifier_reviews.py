"""Per-symbol earnings entry-review notifications (trade_notifier)._earnings_* review path.

Unit lane: an in-memory earnings paper DB with an `entry_reviews` table; asserts the id-watermark reader,
the bullet-format message the account owner asked for, and that a DB predating the feature is guarded.
"""
import sqlite3

import pytest

from cherrypick.orchestrator import trade_notifier as tn

pytestmark = pytest.mark.unit

_COLS = ("scan_date", "symbol", "timing", "price", "volume", "winrate", "winrate_sample",
         "iv_rv_ratio", "term_structure", "market_cap", "best_tier", "selected", "reason", "profile")


def _row(**kw):
    kw.setdefault("scan_date", "2026-07-16")
    kw.setdefault("profile", "strat_test")
    return tuple(kw.get(c) for c in _COLS)


def _conn_with_reviews(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE entry_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_date TEXT, symbol TEXT, "
        "timing TEXT, price REAL, volume REAL, winrate REAL, winrate_sample INTEGER, iv_rv_ratio REAL, "
        "term_structure REAL, market_cap REAL, expected_move REAL, best_tier TEXT, selected INTEGER, "
        "reason TEXT, profile TEXT)"
    )
    conn.executemany(
        f"INSERT INTO entry_reviews ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})", rows
    )
    conn.commit()
    return conn


_ISRG = _row(symbol="ISRG", timing="AMC", price=402.05, volume=2702779, winrate=0.75, winrate_sample=12,
             iv_rv_ratio=1.47, term_structure=-0.019, market_cap=142391166303, best_tier="accepted",
             selected=1, reason="opened iron_fly, iron_condor")
_NFLX = _row(symbol="NFLX", winrate=0.60, winrate_sample=8, best_tier="rejected", selected=0,
             reason="screen_rejected (7 strategies evaluated)")


def test_new_reviews_respects_id_watermark():
    conn = _conn_with_reviews([_ISRG, _NFLX])
    assert [r["symbol"] for r in tn._earnings_new_reviews(conn, set())] == ["ISRG", "NFLX"]
    # After id 1 is notified, only id 2 is new.
    assert [r["symbol"] for r in tn._earnings_new_reviews(conn, {1})] == ["NFLX"]


def test_fmt_review_matches_requested_bullet_layout():
    conn = _conn_with_reviews([_ISRG])
    msg = tn._fmt_earnings_review(tn._earnings_new_reviews(conn, set())[0])
    assert "ISRG" in msg and "chosen" in msg and "opened iron_fly, iron_condor" in msg
    assert "• Price: $402.05" in msg
    assert "• Volume: 2,702,779" in msg
    assert "• Winrate: 75.0% over last 12 earnings" in msg
    assert "• IV/RV Ratio: 1.47" in msg
    assert "• Term Structure: -0.019" in msg
    assert "• Market Cap: 142,391,166,303" in msg


def test_rejected_review_reads_as_rejected_and_omits_missing_fields():
    conn = _conn_with_reviews([_NFLX])
    msg = tn._fmt_earnings_review(tn._earnings_new_reviews(conn, set())[0])
    assert "NFLX" in msg and "rejected" in msg and "screen_rejected" in msg
    assert "Price" not in msg  # None fields are omitted, not shown as n/a


def test_reviews_guarded_when_table_absent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert tn._earnings_new_reviews(conn, set()) == []
    assert tn._all_review_ids(conn) == []
