"""Tests for the read-only status dashboard (orchestrator.dashboard).

Unit lane: builds temp watchdog state + logs + paper DBs, asserts the model assembles suite/module
P&L and splits findings, that the HTML render escapes untrusted text and is written atomically, and
that the whole thing reads files only (no broker/network).
"""

import json
import sqlite3

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import dashboard

pytestmark = pytest.mark.unit


def _meic_db(path, rows):  # (symbol, risk_profile, pnl, fees, exit_time)
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


def _earnings_db(path, rows):  # (symbol, profile, strategy, pnl, entry_cost, exit_cost, closed_at)
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


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    for d in (state, logs, tmp_path / "meic", tmp_path / "earn"):
        d.mkdir()
    monkeypatch.setattr(cfgmod, "STATE_DIR", state)
    monkeypatch.setattr(cfgmod, "LOGS_DIR", logs)
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)

    # MEIC net = pnl - fees: 95 (win) + -45 (loss) = 50. Earnings net = 60-4-3 = 53. Suite = 103.
    _meic_db(
        tmp_path / "meic" / "paper.db",
        [("SPX", "conservative", 100.0, 5.0, "2026-07-10T15:45"), ("SPX", "aggressive", -40.0, 5.0, "t")],
    )
    _earnings_db(tmp_path / "earn" / "paper.db", [("AAPL", "balanced", "iron_fly", 60.0, 4.0, 3.0, 1.7e9)])

    (state / "watchdog.last.json").write_text(
        json.dumps(
            {
                "ts": "2026-07-11T00:00:00+00:00",
                "et": "2026-07-10T20:00:00-04:00",
                "overall": "WARN",
                "in_session": False,
                "is_trading_day": True,
                "findings": [
                    {"key": "meic.streamer", "status": "WARN", "title": "Streamer down", "message": "off"},
                    {
                        "key": "earnings.task.entry",
                        "status": "OK",
                        "title": "Earnings entry",
                        "message": "ok",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (logs / "notify.log").write_text(
        json.dumps(
            {
                "ts": "2026-07-11T00:01:00+00:00",
                "kind": "NOTIFY",
                "level": "CRITICAL",
                "key": "x",
                "title": "boom",
                "message": "bad",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    cfg = {
        "timezone": "America/New_York",
        "modules": {
            "meic": {
                "enabled": True,
                "path": str(tmp_path / "meic"),
                "paper": {
                    "paper_db": "paper.db",
                    "trade_schema": "meic_ic",
                    "kind": "self_healing",
                    "log": "logs/paper.log",
                },
            },
            "earnings": {
                "enabled": True,
                "path": str(tmp_path / "earn"),
                "paper": {"paper_db": "paper.db", "trade_schema": "earnings", "kind": "cherrypick_scheduled"},
            },
        },
        "notify": {"channels": ["log", "discord"]},
        "dashboard": {"output": str(tmp_path / "dash.html"), "log_tail_lines": 50},
    }
    return tmp_path, cfg


def test_build_model_assembles_pnl_and_health(env):
    _, cfg = env
    m = dashboard.build_model(cfg)
    assert m["overall"] == "WARN"
    assert m["suite"]["net_pnl"] == 103.0
    assert m["heartbeat_age_min"] is not None
    # active findings = the WARN only (the OK earnings task is excluded)
    assert [f["key"] for f in m["active_findings"]] == ["meic.streamer"]
    # a CRITICAL notify line was tailed from notify.log
    assert any(e["level"] == "CRITICAL" and "boom" in e["text"] for e in m["logs"])


def test_findings_split_by_module(env):
    _, cfg = env
    m = dashboard.build_model(cfg)
    views = {mv["name"]: mv for mv in m["modules"]}
    assert all(f["key"].startswith("meic.") for f in views["meic"]["findings"])
    assert views["meic"]["pnl"]["trades"] == 2
    assert views["earnings"]["pnl"]["net_pnl"] == 53.0


def test_render_html_escapes_untrusted_text(env):
    _, cfg = env
    m = dashboard.build_model(cfg)
    m["active_findings"].append(
        {"key": "meic.x", "status": "WARN", "title": "<script>alert(1)</script>", "message": "x"}
    )
    out = dashboard._render_html(m)
    assert "<html" in out
    assert "PAPER" in out and "meic" in out and "earnings" in out
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)" in out


def test_render_writes_atomically(env):
    tmp_path, cfg = env
    path = dashboard.render(cfg)
    assert path == tmp_path / "dash.html"
    assert path.exists() and "<html" in path.read_text(encoding="utf-8")
    assert not (tmp_path / "dash.html.tmp").exists()


def test_run_returns_summary(env):
    _, cfg = env
    out = dashboard.run(cfg)
    assert out["ok"] is True
    assert out["overall"] == "WARN"
    assert out["suite_net_pnl"] == 103.0


def test_build_model_survives_missing_state(tmp_path, monkeypatch):
    # No watchdog heartbeat, no logs, no paper DBs — must degrade, not crash.
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    monkeypatch.setattr(cfgmod, "STATE_DIR", state)
    monkeypatch.setattr(cfgmod, "LOGS_DIR", logs)
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)
    cfg = {"timezone": "America/New_York", "modules": {}, "notify": {"channels": ["log"]}}
    m = dashboard.build_model(cfg)
    assert m["overall"] == "UNKNOWN"
    assert m["et_clock"]  # live ET fallback populated
    assert "<html" in dashboard._render_html(m)
