"""Unit tests for paper_loop._eod_supplement -- the arm scorecard, gate ledger, stop-policy
table, regime coverage, and iteration duration/peak-position sections appended to the EOD
report. Isolated from test_eod_analysis.py's conversational-report tests since this exercises
a different function against a differently-shaped seed (arm-tagged rows, ic_spread_legs pairs,
regime columns, loop_log duration rows).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import db, paper_loop  # noqa: E402


def _ns(**kw):
    return argparse.Namespace(**kw)


def _seed(tmp_path, monkeypatch):
    paper_db = str(tmp_path / "paper_trades.db")
    logs = tmp_path / "logs"
    logs.mkdir()

    monkeypatch.setattr(db, "_DB_PATH", paper_db)
    monkeypatch.setattr(paper_loop, "_PAPER_DB", paper_db)
    monkeypatch.setattr(paper_loop, "_LOG_FILE", logs / "paper_loop.log")

    db.cmd_init_db(_ns())

    # control: one clean-win IC (both legs expired OTM).
    db.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "ic_order_id": "C-1",
                    "trade_date": "2026-08-06",
                    "entry_time": "2026-08-06 11:00:00",
                    "symbol": "SPX",
                    "put_strike": 6000,
                    "call_strike": 6100,
                    "wing_width": 10,
                    "net_credit": 1.8,
                    "put_credit": 0.9,
                    "call_credit": 0.9,
                    "quantity": 1,
                    "underlying_price_entry": 6050.0,
                    "risk_profile": "control",
                    "status": "expired",
                    "exit_reason": "cash_settled_expiration",
                    "pnl": 180.0,
                    "fees": 6.89,
                    "dollar_multiplier": 100,
                    "put_settle_value": 0.0,
                    "call_settle_value": 0.0,
                    "entry_gex_bucket": "positive",
                    "entry_gex_value": 0.4,
                }
            )
        )
    )
    db.cmd_record_leg_exit(
        _ns(
            ic_order_id="C-1",
            side="put",
            status="expired",
            exit_time="2026-08-06 16:00:00",
            exit_reason="cash_settled_expiration",
            exit_price=0.0,
            pnl=90.0,
        )
    )
    db.cmd_record_leg_exit(
        _ns(
            ic_order_id="C-1",
            side="call",
            status="expired",
            exit_time="2026-08-06 16:00:00",
            exit_reason="cash_settled_expiration",
            exit_price=0.0,
            pnl=90.0,
        )
    )

    # open: one IC with a full recorded path (settle values on both sides), fully derivable
    # by every stop policy -- no side ever touched or reached a stop threshold.
    db.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "ic_order_id": "O-1",
                    "trade_date": "2026-08-06",
                    "entry_time": "2026-08-06 11:05:00",
                    "symbol": "SPX",
                    "put_strike": 5990,
                    "call_strike": 6110,
                    "wing_width": 10,
                    "net_credit": 1.6,
                    "put_credit": 0.8,
                    "call_credit": 0.8,
                    "quantity": 1,
                    "underlying_price_entry": 6050.0,
                    "risk_profile": "open",
                    "status": "expired",
                    "exit_reason": "cash_settled_expiration",
                    "pnl": 160.0,
                    "fees": 6.89,
                    "dollar_multiplier": 100,
                    "put_max_cost": 0.1,
                    "call_max_cost": 0.15,
                    "put_settle_value": 0.0,
                    "call_settle_value": 0.0,
                }
            )
        )
    )
    db.cmd_record_leg_exit(
        _ns(
            ic_order_id="O-1",
            side="put",
            status="expired",
            exit_time="2026-08-06 16:00:00",
            exit_reason="cash_settled_expiration",
            exit_price=0.0,
            pnl=80.0,
        )
    )
    db.cmd_record_leg_exit(
        _ns(
            ic_order_id="O-1",
            side="call",
            status="expired",
            exit_time="2026-08-06 16:00:00",
            exit_reason="cash_settled_expiration",
            exit_price=0.0,
            pnl=80.0,
        )
    )

    con = sqlite3.connect(paper_db)
    con.execute(
        "INSERT INTO loop_log (loop_time, loop_date, action, reasoning, open_trades_n, "
        "duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "2026-08-06 11:00:00",
            "2026-08-06",
            "gate_block",
            json.dumps({"control": "FILL $1.80", "open": "FILL $1.60", "width-5": "iv_rank_below_floor"}),
            2,
            850,
            "2026-08-06 11:00:00",
        ),
    )
    con.execute(
        "INSERT INTO loop_log (loop_time, loop_date, action, reasoning, open_trades_n, "
        "duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2026-08-06 11:05:00", "2026-08-06", "paper_iteration", "ok", 5, 1200, "2026-08-06 11:05:00"),
    )
    con.commit()
    con.close()

    return logs


def test_eod_supplement_has_all_five_sections(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    lines = paper_loop._eod_supplement("2026-08-06")
    md = "\n".join(lines)

    assert "## Arm scorecard (breakeven identity)" in md
    assert "## Gate ledger" in md
    assert "## Stop-policy table (derived from `open`)" in md
    assert "## Regime coverage" in md
    assert "## Iteration duration & peak open positions" in md


def test_arm_scorecard_lists_control_with_a_clean_breakeven_margin(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    md = "\n".join(paper_loop._eod_supplement("2026-08-06"))
    assert "| control |" in md
    assert "| open |" in md
    # C-1 is a clean double-OTM expiry: clean_pct 100%, double_stop_pct 0%.
    assert "100.0%" in md


def test_gate_ledger_lists_every_stream_from_the_gate_block_row(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    md = "\n".join(paper_loop._eod_supplement("2026-08-06"))
    assert "| control | FILL x1 |" in md
    assert "| open | FILL x1 |" in md
    assert "| width-5 | iv_rank_below_floor x1 |" in md


def test_stop_policy_table_reports_all_four_derived_policies(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    md = "\n".join(paper_loop._eod_supplement("2026-08-06"))
    for policy in ("stop-none", "stop-0.75-net", "stop-2.0-side", "strike-touch"):
        assert f"| {policy} |" in md
    table = md.split("## Stop-policy table")[1].split("## Regime coverage")[0]
    assert "| control |" not in table


def test_regime_coverage_reports_gex_tagged_and_withholds_degenerate_breakdown(tmp_path, monkeypatch):
    """Both resolved trades are era='sample' but only C-1 carries a gex tag, and it's the only
    tagged row -- degenerate (one bucket). Its by-bucket P&L table must be withheld even though
    the dimension is tagged, per the plan's 'degenerate dimensions' P&L withheld' convention."""
    _seed(tmp_path, monkeypatch)
    md = "\n".join(paper_loop._eod_supplement("2026-08-06"))
    section = md.split("## Regime coverage")[1].split("## Iteration duration")[0]
    assert "| gex | 1 | 1 |" in section  # 1 tagged, 1 untagged (2 resolved trades total)
    assert "**gex** by bucket:" not in section  # degenerate -> withheld


def test_duration_and_peak_positions_reads_loop_log(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    md = "\n".join(paper_loop._eod_supplement("2026-08-06"))
    section = md.split("## Iteration duration & peak open positions")[1]
    assert "Iterations timed: 2" in section
    assert "max 1200ms" in section
    assert "Peak open positions" in section and "5" in section


def test_eod_supplement_handles_a_zero_trade_day(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    lines = paper_loop._eod_supplement("2026-08-07")
    md = "\n".join(lines)
    assert "_No resolved trades today._" in md
    assert "_No gate_block rows logged today" in md
    assert "_`open` entered no trades today" in md
    assert "_No timed iterations today" in md


def test_write_eod_report_includes_the_supplement(tmp_path, monkeypatch):
    logs = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(paper_loop, "_eod_report_path", lambda day: logs / f"paper-eod-{day}.md")
    monkeypatch.setattr(paper_loop, "_run_json", lambda cmd: {"ok": False})
    path = paper_loop._write_eod_report("2026-08-06")
    md = Path(path).read_text(encoding="utf-8")
    assert "## Arm scorecard (breakeven identity)" in md
    # the supplement lands before the footer, not after it.
    assert md.index("## Arm scorecard") < md.index("_Generated ")
