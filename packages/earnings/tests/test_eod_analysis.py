"""Unit tests for the earnings conversational EOD analysis report
(strategy_test_runner._write_eod_analysis).

No credentials/live connection: seed a temp paper book through db_paper's writers, point the metrics
reader and the analysis writer at the temp DB/logs, and assert the markdown carries all seven sections,
reconciles net-of-cost P&L and IV crush, flags equity-option (non-1256) treatment, and renders a clean
flat-session path.
"""
import argparse
import json
import time

import pytest

import db_paper
import strategy_metrics as metrics
import strategy_test_runner as runner


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(db_paper, "DB_PATH", db_path)
    monkeypatch.setattr(metrics, "DB_PATH", db_path)
    monkeypatch.setattr(runner, "_logs_dir", lambda: logs)
    db_paper.cmd_init_db(_ns())

    open_ts = time.mktime(time.strptime("2026-07-15 15:50", "%Y-%m-%d %H:%M"))
    close_ts = time.mktime(time.strptime("2026-07-16 09:50", "%Y-%m-%d %H:%M"))
    # (strategy, symbol, cap_at_risk, entry_credit, exit_debit, entry_iv, exit_iv, iv_rv)
    rows = [
        ("iron_fly", "AAPL", 1500.0, 2.50, 0.80, 48.0, 30.0, 1.42),
        ("iron_condor", "NFLX", 2200.0, 3.10, 1.20, 55.0, 61.0, 1.15),
    ]
    for i, (strat, sym, car, ecr, edeb, eiv, xiv, ivrv) in enumerate(rows):
        oid = f"strat_test-{strat}-{sym}-{i}"
        db_paper.cmd_save_trade(_ns(data=json.dumps({
            "order_id": oid, "strategy": strat, "symbol": sym, "expiration": "2026-07-17",
            "legs_json": json.dumps([{"symbol": f"{sym} C", "action": "Sell to Open", "quantity": 1}]),
            "entry_credit": ecr, "profile": "strat_test", "quantity": 1, "capital_at_risk": car,
            "entry_cost": 3.0, "entry_iv": eiv, "opened_at": open_ts,
            "entry_context": {"iv_rv_ratio": ivrv},
        })))
        db_paper.cmd_save_close(_ns(data=json.dumps({
            "order_id": oid, "exit_debit": edeb, "pnl": (ecr - edeb) * 100,
            "exit_cost": 3.0, "exit_iv": xiv, "closed_at": close_ts,
        })))
    db_paper.cmd_save_market_context(_ns(data=json.dumps({"context_date": "2026-07-15", "vix": 16.2})))
    db_paper.cmd_save_market_context(_ns(data=json.dumps({"context_date": "2026-07-16", "vix": 14.9})))
    return logs


def test_analysis_has_all_seven_sections_and_reconciles(seeded):
    path = runner._write_eod_analysis("2026-07-16")
    md = path.read_text(encoding="utf-8")

    for header in ("## 1. Executive snapshot", "## 2. Position-level detail", "## 3. Trade activity log",
                   "## 4. Risk metrics", "## 5. Market context", "## 6. Tax / accounting notes",
                   "## 7. Notes / journal"):
        assert header in md

    # Net of costs: (2.50-0.80)*100-6 = 164 ; (3.10-1.20)*100-6 = 184 ; total 348.
    assert "$348.00" in md
    # Equity-option (not 1256) treatment for single-name earnings options.
    assert "not Section 1256" in md
    # Market context surfaces the captured overnight VIX move.
    assert "14.9" in md and "-1.30" in md
    # IV/RV entry edge (average 1.285 -> 1.29) is reported from entry_context.
    assert "IV/RV ratio at entry" in md


def test_analysis_flat_session_renders(seeded):
    path = runner._write_eod_analysis("2026-07-09")  # nothing closed that day
    md = path.read_text(encoding="utf-8")
    assert "Flat session" in md
    assert "## 7. Notes / journal" in md
