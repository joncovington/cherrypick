"""Tests for the unified cross-module paper P&L report (orchestrator.report).

Read-only lane: builds tiny temp paper DBs in each of the two wired schemas and asserts the report
computes net-of-cost P&L, win rates, and the per-profile breakdown, and that a missing/unknown DB is
reported rather than fatal.
"""

import sqlite3

import pytest

from cherrypick.orchestrator import report

pytestmark = pytest.mark.unit


def _meic_db(path, rows):
    """rows: (symbol, risk_profile, pnl, fees, exit_time)."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, "
        "pnl REAL, fees REAL, exit_time TEXT)"
    )
    conn.executemany(
        "INSERT INTO ic_trades (symbol, risk_profile, pnl, fees, exit_time) VALUES (?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    conn.close()


def _earnings_db(path, rows):
    """rows: (symbol, profile, strategy, pnl, entry_cost, exit_cost, closed_at)."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades (order_id INTEGER PRIMARY KEY, symbol TEXT, profile TEXT, strategy TEXT, "
        "pnl REAL, entry_cost REAL, exit_cost REAL, closed_at REAL)"
    )
    conn.executemany(
        "INSERT INTO trades (symbol, profile, strategy, pnl, entry_cost, exit_cost, closed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _cfg(tmp_path, meic_db="paper.db", earnings_db="paper.db", meic_dir="meic", earnings_dir="earn"):
    (tmp_path / meic_dir).mkdir(exist_ok=True)
    (tmp_path / earnings_dir).mkdir(exist_ok=True)
    return {
        "modules": {
            "meic": {
                "enabled": True,
                "path": str(tmp_path / meic_dir),
                "paper": {"paper_db": meic_db, "trade_schema": "meic_ic"},
            },
            "earnings": {
                "enabled": True,
                "path": str(tmp_path / earnings_dir),
                "paper": {"paper_db": earnings_db, "trade_schema": "earnings"},
            },
        }
    }


def test_report_unifies_pnl_net_of_costs_across_modules(tmp_path):
    cfg = _cfg(tmp_path)
    # MEIC: net = pnl - fees. Two closed trades: +100-5=95 (win), -40-5=-45 (loss). One open (skipped).
    _meic_db(
        tmp_path / "meic" / "paper.db",
        [
            ("SPX", "conservative", 100.0, 5.0, "2026-07-10T15:45"),
            ("SPX", "aggressive", -40.0, 5.0, "2026-07-10T15:46"),
            ("SPX", "conservative", 999.0, 5.0, None),  # open -> excluded
        ],
    )
    # Earnings: net = pnl - entry_cost - exit_cost. One closed: 60-4-3=53 (win). One open (skipped).
    _earnings_db(
        tmp_path / "earn" / "paper.db",
        [
            ("AAPL", "balanced", "iron_fly", 60.0, 4.0, 3.0, 1_700_000_000.0),
            ("MSFT", "balanced", "iron_fly", 20.0, 2.0, None, None),  # open -> excluded
        ],
    )

    out = report.run(cfg)
    assert out["ok"] is True

    meic = out["modules"]["meic"]
    assert meic["ok"] and meic["trades"] == 2
    assert meic["net_pnl"] == 50.0  # 95 + (-45)
    assert meic["wins"] == 1 and meic["losses"] == 1
    assert meic["by_profile"]["conservative"]["net_pnl"] == 95.0
    assert meic["by_profile"]["aggressive"]["net_pnl"] == -45.0

    earn = out["modules"]["earnings"]
    assert earn["ok"] and earn["trades"] == 1
    assert earn["net_pnl"] == 53.0
    assert earn["by_profile"]["balanced"]["trades"] == 1

    # Suite total spans both modules: 50 + 53 = 103 over 3 trades.
    assert out["suite"]["trades"] == 3
    assert out["suite"]["net_pnl"] == 103.0


def test_report_untagged_rows_group_under_module_sentinel(tmp_path):
    cfg = _cfg(tmp_path)
    _meic_db(tmp_path / "meic" / "paper.db", [("SPX", None, 10.0, 1.0, "t")])  # NULL -> unassigned
    _earnings_db(tmp_path / "earn" / "paper.db", [("AAPL", None, "x", 5.0, 0.0, 0.0, 1.0)])  # -> default
    out = report.run(cfg)
    assert "unassigned" in out["modules"]["meic"]["by_profile"]
    assert "default" in out["modules"]["earnings"]["by_profile"]


def test_report_missing_db_is_reported_not_fatal(tmp_path):
    cfg = _cfg(tmp_path)
    _meic_db(tmp_path / "meic" / "paper.db", [("SPX", "conservative", 10.0, 1.0, "t")])
    # earnings DB never created
    out = report.run(cfg)
    assert out["ok"] is True
    assert out["modules"]["meic"]["ok"] is True
    assert out["modules"]["earnings"]["ok"] is False
    assert "not found" in out["modules"]["earnings"]["reason"]
    # suite still reflects the module that could be read
    assert out["suite"]["trades"] == 1


def test_report_empty_db_read_failure_is_not_fatal(tmp_path):
    cfg = _cfg(tmp_path)
    # A DB file with no ic_trades table -> reader raises sqlite3.Error -> reported, not fatal.
    (tmp_path / "meic" / "paper.db").write_bytes(b"")
    _earnings_db(tmp_path / "earn" / "paper.db", [("AAPL", "balanced", "x", 5.0, 0.0, 0.0, 1.0)])
    out = report.run(cfg)
    assert out["ok"] is True
    assert out["modules"]["meic"]["ok"] is False
    assert out["modules"]["earnings"]["ok"] is True


def test_report_unknown_schema_reported(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["modules"]["meic"]["paper"]["trade_schema"] = "mystery"
    out = report.run(cfg)
    assert out["modules"]["meic"]["ok"] is False
    assert "unknown schema" in out["modules"]["meic"]["reason"]


def test_report_session_filter_restricts_to_one_day(tmp_path):
    cfg = _cfg(tmp_path)
    _meic_db(
        tmp_path / "meic" / "paper.db",
        [
            ("SPX", "conservative", 100.0, 0.0, "2026-07-10T15:45"),  # session 2026-07-10
            ("SPX", "conservative", -30.0, 0.0, "2026-07-11T15:45"),  # session 2026-07-11
        ],
    )
    # Earnings epoch's local session comes from the same helper the reader uses, so the assertion
    # is tz-independent (1.7e9 is 2023-11-14, distinct from either MEIC day above).
    ep = 1_700_000_000.0
    earnings_session = report._session_from_epoch(ep)
    _earnings_db(tmp_path / "earn" / "paper.db", [("AAPL", "balanced", "iron_fly", 60.0, 0.0, 0.0, ep)])

    # No filter -> all-time, all three closed trades.
    assert report.run(cfg)["suite"]["trades"] == 3

    # Filter to the MEIC 07-10 session only.
    day = report.run(cfg, session="2026-07-10")
    assert day["session"] == "2026-07-10"
    assert day["modules"]["meic"]["trades"] == 1
    assert day["modules"]["meic"]["net_pnl"] == 100.0
    assert day["modules"]["earnings"]["trades"] == 0
    assert day["suite"]["trades"] == 1

    # Filter to the earnings session -> only the earnings trade.
    eday = report.run(cfg, session=earnings_session)
    assert eday["modules"]["earnings"]["trades"] == 1
    assert eday["modules"]["meic"]["trades"] == 0


def test_eod_digest_markdown_cites_report_numbers(tmp_path, monkeypatch):
    from cherrypick.orchestrator import config as cfgmod
    from cherrypick.orchestrator import eod_digest

    # Pin the logs home to tmp so the module-eod-file pointer check is hermetic (module logs now live
    # under LOGS_DIR/<name>, not in the module checkout).
    monkeypatch.setattr(cfgmod, "LOGS_DIR", tmp_path / "logs")

    cfg = _cfg(tmp_path)
    _meic_db(tmp_path / "meic" / "paper.db", [("SPX", "conservative", 100.0, 0.0, "2026-07-10T15:45")])
    _earnings_db(tmp_path / "earn" / "paper.db", [])  # table exists, no rows

    md = eod_digest.build_markdown(cfg, "2026-07-10")
    assert "Suite EOD Digest 2026-07-10" in md
    assert "$100.00" in md  # the MEIC net for that session, surfaced in the suite total + table
    # The conversational snapshot renders from the same numbers (non-flat: MEIC closed a winning trade).
    assert "## Snapshot" in md
    assert "the suite closed **1** trade" in md
    # No module has written its own paper-eod / analysis file in the logs home -> the pointer says so.
    assert "no paper-eod-2026-07-10.md yet" in md
    assert "no eod-analysis-2026-07-10.md yet" in md

    # Once MEIC writes both files to the logs home, the digest links to each.
    meic_logs = cfgmod.module_logs_dir("meic")
    meic_logs.mkdir(parents=True, exist_ok=True)
    (meic_logs / "paper-eod-2026-07-10.md").write_text("x", encoding="utf-8")
    (meic_logs / "eod-analysis-2026-07-10.md").write_text("y", encoding="utf-8")
    md2 = eod_digest.build_markdown(cfg, "2026-07-10")
    assert str(meic_logs / "paper-eod-2026-07-10.md") in md2
    assert str(meic_logs / "eod-analysis-2026-07-10.md") in md2


def test_eod_digest_snapshot_flat_session(tmp_path, monkeypatch):
    from cherrypick.orchestrator import config as cfgmod
    from cherrypick.orchestrator import eod_digest

    monkeypatch.setattr(cfgmod, "LOGS_DIR", tmp_path / "logs")
    cfg = _cfg(tmp_path)
    _meic_db(tmp_path / "meic" / "paper.db", [])  # no trades either module
    _earnings_db(tmp_path / "earn" / "paper.db", [])

    md = eod_digest.build_markdown(cfg, "2026-07-10")
    assert "## Snapshot" in md
    assert "Flat suite session" in md
