"""Unit tests for the MEIC conversational EOD analysis report (paper_loop._write_eod_analysis).

No credentials/live connection: seed a temp paper DB through db.py's own writers (init_db, save_trade,
record_leg_exit, save_market_context), point paper_loop's paper-DB/logs at the temp paths, and assert the
generated markdown carries all seven sections, reconciles net-of-fees P&L, classifies 1256 vs equity
options, and renders a clean flat-session path.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import db
import paper_loop


def _ns(**kw):
    return argparse.Namespace(**kw)


def _seed(tmp_path, monkeypatch):
    """A temp paper DB with two ICs (one winning XSP that settled, one losing QQQ stopped on the call
    side) plus a market-context row, and paper_loop pointed at temp DB + logs. Returns the logs dir."""
    paper_db = str(tmp_path / "paper_trades.db")
    logs = tmp_path / "logs"
    logs.mkdir()

    monkeypatch.setattr(db, "_DB_PATH", paper_db)
    monkeypatch.setattr(paper_loop, "_PAPER_DB", paper_db)
    monkeypatch.setattr(paper_loop, "_LOG_FILE", logs / "paper_loop.log")
    monkeypatch.setattr(paper_loop, "_load_config",
                        lambda: {"cash_settled_symbols": ["SPX", "XSP", "NDX", "RUT"]})

    db.cmd_init_db(_ns())
    db.cmd_save_trade(_ns(data=json.dumps({
        "ic_order_id": "A1", "trade_date": "2026-07-16", "entry_time": "2026-07-16 12:05:00",
        "symbol": "XSP", "put_strike": 545, "call_strike": 560, "wing_width": 5, "net_credit": 0.95,
        "quantity": 1, "call_delta_at_entry": 0.14, "put_delta_at_entry": -0.15,
        "long_call_delta_at_entry": 0.07, "long_put_delta_at_entry": -0.08,
        "underlying_price_entry": 552.3, "iv_rank_at_entry": 0.34, "risk_profile": "conservative",
        "status": "expired", "exit_reason": "cash_settled_expiration", "pnl": 95.0, "fees": 4.49,
        "dollar_multiplier": 100})))
    db.cmd_save_trade(_ns(data=json.dumps({
        "ic_order_id": "A2", "trade_date": "2026-07-16", "entry_time": "2026-07-16 12:40:00",
        "symbol": "QQQ", "put_strike": 470, "call_strike": 486, "wing_width": 4, "net_credit": 0.80,
        "quantity": 1, "call_delta_at_entry": 0.16, "put_delta_at_entry": -0.13,
        "long_call_delta_at_entry": 0.09, "long_put_delta_at_entry": -0.07,
        "underlying_price_entry": 478.1, "iv_rank_at_entry": 0.31, "risk_profile": "moderate",
        "status": "closed", "exit_reason": "per_side_stop_call", "pnl": -160.0, "fees": 4.49,
        "dollar_multiplier": 100})))
    db.cmd_record_leg_exit(_ns(ic_order_id="A2", side="call", status="closed",
                               exit_time="2026-07-16 14:10:00", exit_reason="per_side_stop_call",
                               exit_price=1.20, pnl=-160.0))
    db.cmd_save_market_context(_ns(date="2026-07-15", vix=14.8, vix1d=13.0, vix1d_ratio=0.878,
                                   symbols='{"XSP": {"price": 549.0, "iv_rank": 0.30}}'))
    db.cmd_save_market_context(_ns(date="2026-07-16", vix=15.6, vix1d=14.9, vix1d_ratio=0.955,
                                   symbols='{"XSP": {"price": 552.3, "iv_rank": 0.34}}'))
    return logs


def test_analysis_has_all_seven_sections_and_reconciles(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    path = paper_loop._write_eod_analysis("2026-07-16")
    capsys.readouterr()  # swallow db.py _out prints
    md = Path(path).read_text(encoding="utf-8")

    for header in ("## 1. Executive snapshot", "## 2. Position-level detail", "## 3. Trade activity log",
                   "## 4. Risk metrics", "## 5. Market context", "## 6. Tax / accounting notes",
                   "## 7. Notes / journal"):
        assert header in md

    # Net of fees: 95-4.49 + (-160-4.49) = -73.98 for the session.
    assert "-$73.98" in md
    # 1256 vs equity classification lands the two symbols in the right buckets.
    assert "Section 1256" in md and "XSP" in md
    assert "Equity-option treatment" in md and "QQQ" in md
    # Market context surfaces the captured VIX and its move vs the prior session.
    assert "15.6" in md and "+0.80" in md
    # The dominant call-side stop is called out in the journal.
    assert "per_side_stop_call" in md


def test_analysis_flat_session_renders(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    path = paper_loop._write_eod_analysis("2026-07-10")  # no trades that day
    capsys.readouterr()
    md = Path(path).read_text(encoding="utf-8")
    assert "Flat session" in md
    assert "## 7. Notes / journal" in md  # every section still renders


def test_analysis_surfaces_loop_gates_from_loop_log(tmp_path, monkeypatch, capsys):
    """A flat/gated day explains *why* via the loop log — the gate line that lets the AI insight (and a
    human) see it was the GEX/IV regime gates, not a broken run."""
    _seed(tmp_path, monkeypatch)
    con = sqlite3.connect(paper_loop._PAPER_DB)
    reasoning = ("[11:34 ET] VIX 17.9  1D-ratio 0.65  delta 0.16   "
                 "SPX(ivr 0.43): all regime_gex_negative   IWM(ivr 0.26): 5 skip")
    con.execute(
        "INSERT INTO loop_log (loop_time, loop_date, action, reasoning, created_at) VALUES (?, ?, ?, ?, ?)",
        ("2026-07-10 11:34:00", "2026-07-10", "paper_iteration", reasoning, "2026-07-10 11:34:00"),
    )
    con.commit()
    con.close()

    md = Path(paper_loop._write_eod_analysis("2026-07-10")).read_text(encoding="utf-8")
    capsys.readouterr()
    # The per-symbol gate decision is surfaced (prefix stripped), so the flat day is explainable.
    assert "regime_gex_negative" in md
    assert "SPX(ivr 0.43): all regime_gex_negative" in md
    assert "1 loop iteration" in md or "**1**" in md  # iteration count mentioned
    # And it appears in the market-context section too (available on active days).
    assert "Entry gates at the last" in md
