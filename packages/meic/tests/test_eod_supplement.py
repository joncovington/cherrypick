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


def test_eod_analysis_reports_stops_that_actually_happened(tmp_path, monkeypatch):
    """Regression, 2026-08-11: the exit query filtered `ic_spread_legs.status='closed'`, a value the
    writer has never once written — so it matched nothing on every session ever generated and the
    report fell through to "No side stops fired" unconditionally. It printed that on a day 430 legs
    stopped, and the EOD insight layer read the line and built a whole narrative on it.

    Asserts both halves: the false sentence is gone, AND the side-attribution branch the code already
    carried now actually runs. Written against the real writer and its real output file rather than a
    captured string, because the failure mode was precisely a query that quietly matched nothing —
    a test that could pass on an empty result would reproduce the bug rather than catch it.
    """
    import sqlite3

    db = tmp_path / "paper_trades.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE ic_trades (ic_order_id TEXT, trade_date TEXT, entry_time TEXT, symbol TEXT,
            risk_profile TEXT, status TEXT, pnl REAL, fees REAL, net_credit REAL, quantity INTEGER,
            wing_width REAL, put_strike REAL, call_strike REAL, dollar_multiplier REAL,
            underlying_price_entry REAL, expiration TEXT, era TEXT);
        CREATE TABLE ic_spread_legs (ic_order_id TEXT, side TEXT, status TEXT, exit_time TEXT,
            exit_reason TEXT, exit_price REAL, pnl REAL);
        CREATE TABLE market_context (context_date TEXT, vix REAL, underlying_price REAL);
        """
    )
    for i in range(3):
        con.execute(
            "INSERT INTO ic_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"IC{i}",
                "2026-08-11",
                "2026-08-11 10:00:00",
                "SPX",
                "control",
                "partial",
                -100.0,
                6.89,
                1.5,
                1,
                5.0,
                7700.0,
                7800.0,
                100.0,
                7750.0,
                "2026-08-11",
                "sample",
            ),
        )
        # Put side stopped, call side rode to expiry — the shape of a down-trending day.
        con.execute(
            "INSERT INTO ic_spread_legs VALUES (?,?,?,?,?,?,?)",
            (f"IC{i}", "put", "stopped", "2026-08-11 13:00:00", "stop_trigger", 2.0, -50.0),
        )
        con.execute(
            "INSERT INTO ic_spread_legs VALUES (?,?,?,?,?,?,?)",
            (f"IC{i}", "call", "expired", None, None, None, 25.0),
        )
    con.commit()
    con.close()

    monkeypatch.setattr(paper_loop, "_PAPER_DB", str(db))
    monkeypatch.setattr(paper_loop, "_LOG_FILE", tmp_path / "logs" / "paper_loop.log")
    (tmp_path / "logs").mkdir(exist_ok=True)

    paper_loop._write_eod_analysis("2026-08-11")
    text = (tmp_path / "logs" / "eod-analysis-2026-08-11.md").read_text(encoding="utf-8")

    assert "No side stops fired" not in text
    # The attribution branch that was unreachable for the life of the ledger.
    assert "Put side did most of the stopping" in text
    assert "Put-side stops: 3" in text


def test_paper_eod_carries_the_refusal_ledger(tmp_path, monkeypatch):
    """Refusals have to reach the deterministic file: the EOD insight layer reads these files and
    nothing else, so a refusal recorded only in the database is invisible to the narrative.

    Also asserts the zero-entry line. `control` and `sign` took no trades on 2026-08-11 and a debrief
    counted them as two independent confirmations of the IV-rank gate; they are one policy, and the
    line says what an arm at zero actually contributes.

    Builds its database through the REAL schema (`db.cmd_init_db`) rather than hand-rolled CREATE
    TABLEs. A fixture that invents its own columns is how the "No side stops fired" bug survived its
    own test for the life of the ledger; this file is not going to repeat that.
    """
    import sqlite3

    db_path = tmp_path / "paper_trades.db"
    monkeypatch.setenv("MEIC_DB_PATH", str(db_path))
    monkeypatch.setattr(db, "_DB_PATH", str(db_path))
    db.cmd_init_db(_ns())

    con = sqlite3.connect(db_path)
    for _ in range(7):
        con.execute(
            "INSERT INTO entry_attempts (ts, trade_date, risk_profile, symbol, outcome) "
            "VALUES ('10:00', '2026-08-11', 'open', 'SPX', 'filled')"
        )
    for _ in range(12):
        con.execute(
            "INSERT INTO entry_attempts (ts, trade_date, risk_profile, symbol, outcome, block_detail) "
            "VALUES ('10:00', '2026-08-11', 'control', 'SPX', 'gate_blocked', 'iv_rank_below_floor')"
        )
    con.commit()
    con.close()

    monkeypatch.setattr(paper_loop, "_PAPER_DB", str(db_path))
    monkeypatch.setattr(paper_loop, "_LOG_FILE", tmp_path / "logs" / "paper_loop.log")
    (tmp_path / "logs").mkdir(exist_ok=True)
    paper_loop._write_eod_report("2026-08-11")
    text = (tmp_path / "logs" / "paper-eod-2026-08-11.md").read_text(encoding="utf-8")

    assert "## Entry attempts (the refusal ledger)" in text
    assert "iv_rank_below_floor x12" in text
    assert "Took no entries: control" in text
    assert "Too few entries to read" not in text, "7 fills is a sample; the warning must not fire"
