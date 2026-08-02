"""The compact suite-dashboard card (section.build_section): a cherrypick.core.viz payload
read through dashboard.py's own query helpers, so the card can't disagree with the dashboard."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import dashboard, section

DDL = """
CREATE TABLE ic_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
    net_credit REAL, pnl REAL, fees REAL,
    status TEXT DEFAULT 'pending', ic_order_id TEXT UNIQUE NOT NULL,
    risk_profile TEXT
)
"""


def _make_db(path, rows=()):
    conn = sqlite3.connect(path)
    conn.execute(DDL)
    for r in rows:
        conn.execute(
            "INSERT INTO ic_trades (trade_date, symbol, net_credit, pnl, fees, status, "
            "ic_order_id, risk_profile) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            r,
        )
    conn.commit()
    conn.close()
    return path


def test_missing_db_is_a_plain_error_not_a_stray_file(tmp_path):
    missing = tmp_path / "nope.db"
    payload = section.build_section(db_override=str(missing))
    assert payload["ok"] is False
    assert "no paper trades DB yet" in payload["error"]
    assert not missing.exists()


def test_unknown_mode_is_an_error_payload():
    payload = section.build_section(mode="bogus")
    assert payload["ok"] is False
    assert "bogus" in payload["error"]


def test_empty_book_is_ok_not_error(tmp_path):
    db = _make_db(tmp_path / "cherrypick.meic.paper.db")
    payload = section.build_section(db_override=str(db))
    assert payload["ok"] is True
    assert "no trades yet" in payload["title"]
    assert payload["subtitle"].startswith("paper")


def test_payload_metrics_and_timeseries(tmp_path):
    db = _make_db(
        tmp_path / "cherrypick.meic.paper.db",
        rows=[
            # trade_date, symbol, net_credit, pnl, fees, status, ic_order_id, risk_profile
            ("2026-07-20", "XSP", 1.20, 80.0, 9.0, "expired", "IC1", "conservative"),
            ("2026-07-21", "XSP", 1.00, -30.0, 9.0, "stopped", "IC2", "conservative"),
            ("2026-07-21", "SPX", 2.00, None, 0.0, "open", "IC3", "conservative"),
            ("2026-07-21", "XSP", 1.10, None, 0.0, "cancelled", "IC4", "conservative"),  # never counted
        ],
    )
    payload = section.build_section(db_override=str(db))
    assert payload["ok"] is True

    by_label = {m["label"]: m for m in payload["metrics"]}
    # fee-subtracted: (80-9) + (-30-9) = 32; the open/cancelled rows contribute no P&L
    assert by_label["Net P&L"]["value"] == "$32.00"
    assert by_label["Net P&L"]["tone"] == "pos"
    # one win (80-9>0), one loss (-30-9<0); the open trade is unresolved and uncounted
    assert by_label["Win rate"]["value"] == "50% (1/2)"
    assert by_label["Open ICs"]["value"] == "1"
    assert by_label["Trades"]["value"] == "3"

    ts = payload["timeseries"]
    assert ts["labels"] == ["2026-07-20", "2026-07-21"]
    assert ts["series"][0]["values"] == [71.0, 32.0]


def test_symbol_and_profile_filters(tmp_path):
    db = _make_db(
        tmp_path / "cherrypick.meic.paper.db",
        rows=[
            ("2026-07-20", "XSP", 1.20, 80.0, 9.0, "expired", "IC1", "conservative"),
            ("2026-07-20", "SPX", 2.00, 200.0, 10.0, "expired", "IC2", "aggressive"),
        ],
    )
    xsp = section.build_section(db_override=str(db), symbol="xsp")
    assert {m["label"]: m["value"] for m in xsp["metrics"]}["Net P&L"] == "$71.00"
    assert "xsp" in xsp["subtitle"] or "XSP" in xsp["subtitle"]
    aggressive = section.build_section(db_override=str(db), profile="aggressive")
    assert {m["label"]: m["value"] for m in aggressive["metrics"]}["Net P&L"] == "$190.00"
    assert "profile aggressive" in aggressive["subtitle"]


def test_card_totals_match_dashboard_stats(tmp_path):
    """The consistency guarantee: the card's headline equals _stats_for_period minus fees."""
    db = _make_db(
        tmp_path / "cherrypick.meic.paper.db",
        rows=[
            ("2026-07-20", "XSP", 1.20, 80.0, 9.0, "expired", "IC1", None),
            ("2026-07-21", "XSP", 1.00, -30.0, 9.0, "stopped", "IC2", None),
        ],
    )
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    stats = dashboard._stats_for_period(conn)
    fees = conn.execute(
        "SELECT SUM(fees) FROM ic_trades WHERE status NOT IN ('cancelled','pending','partial_entry')"
    ).fetchone()[0]
    conn.close()
    payload = section.build_section(db_override=str(db))
    headline = {m["label"]: m["value"] for m in payload["metrics"]}["Net P&L"]
    assert headline == section.viz.fmt_money(stats["net_pnl"] - fees)
