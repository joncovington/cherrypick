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
                    "task_name": "cherrypick-meic-paper-loop",
                },
                "streamer": {"enabled": True},
                "calibration": {"ladder": ["conservative", "moderate", "aggressive"]},
            },
            "earnings": {
                "enabled": True,
                "path": str(tmp_path / "earn"),
                "paper": {"paper_db": "paper.db", "trade_schema": "earnings", "kind": "cherrypick_scheduled"},
            },
        },
        "watchdog": {"task_name": "cherrypick-watchdog", "interval_minutes": 10, "renotify_minutes": 60},
        "trade_notify": {"task_name": "cherrypick-trade-notify", "interval_minutes": 2},
        "notify": {"channels": ["log", "discord"]},
        "dashboard": {
            "output": str(tmp_path / "dash.html"),
            "log_tail_lines": 50,
            "embeds": [
                {"id": "meic", "title": "MEIC", "enabled": True, "kind": "server", "port": 8801},
                {"id": "earn", "title": "Earnings", "enabled": False, "kind": "static"},
            ],
        },
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


def test_build_model_attaches_calibration_and_renders_panel(env):
    _, cfg = env
    m = dashboard.build_model(cfg)
    views = {mv["name"]: mv for mv in m["modules"]}
    # meic has a ladder -> its closed profiles (conservative, aggressive) carry recommendations.
    meic_cal = views["meic"]["calibration"]
    assert meic_cal["ok"] is True
    assert "conservative" in meic_cal["profiles"]
    assert meic_cal["profiles"]["conservative"]["recommendation"] is not None
    # the rendered page shows the calibration section.
    assert "calibration" in dashboard._render_html(m)


def test_calibration_html_variants():
    graduate = {
        "profiles": {
            "conservative": {
                "reading": {"sample": 25, "win_rate": 0.7, "days": 20},
                "recommendation": {"recommendation": "graduate:moderate", "reason": "eligible to graduate"},
            }
        }
    }
    out = dashboard._calibration_html(graduate)
    assert "eligible" in out and "graduate" in out and "conservative" in out

    hold = {
        "profiles": {
            "conservative": {
                "reading": {"sample": 3, "win_rate": 0.5, "days": 1},
                "recommendation": {"recommendation": "hold", "reason": "sample below threshold"},
            }
        }
    }
    assert "hold" in dashboard._calibration_html(hold)

    # off-ladder (recommendation None) and empty -> panel omitted.
    assert dashboard._calibration_html({"profiles": {"x": {"reading": {}, "recommendation": None}}}) == ""
    assert dashboard._calibration_html({}) == ""


def test_calibration_html_escapes_untrusted_text():
    cal = {
        "profiles": {
            "<b>c</b>": {
                "reading": {"sample": 1, "win_rate": None, "days": 1},
                "recommendation": {"recommendation": "hold", "reason": "<script>x</script>"},
            }
        }
    }
    out = dashboard._calibration_html(cal)
    assert "<script>x</script>" not in out and "&lt;script&gt;x" in out
    assert "<b>c</b>" not in out and "&lt;b&gt;c" in out


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


def test_build_model_includes_system_panel(env, monkeypatch):
    from cherrypick.notify import secrets as notify_secrets
    from cherrypick.orchestrator import tasks

    monkeypatch.setattr(tasks, "query_verbose", lambda name: {"exists": False})
    monkeypatch.setattr(notify_secrets, "is_set", lambda channel: False)
    _, cfg = env
    m = dashboard.build_model(cfg)
    # scheduled tasks: every declared task_name shows up, even when not actually registered on this box.
    task_names = {t["name"] for t in m["tasks"]}
    assert task_names == {
        "cherrypick-meic-paper-loop",
        "cherrypick-watchdog",
        "cherrypick-trade-notify",
        "cherrypick-eod-digest",  # on by default (opt out via eod_digest.enabled=false)
    }
    assert all(t["exists"] is False for t in m["tasks"])  # none registered in the test env

    modules = {mv["name"]: mv for mv in m["modules_installed"]}
    assert modules["meic"]["source"].startswith("in-place:")
    assert modules["meic"]["paper_kind"] == "self_healing"
    assert modules["meic"]["streamer_enabled"] is True
    assert modules["earnings"]["streamer_enabled"] is False

    cs = m["config_summary"]
    assert cs["timezone"] == "America/New_York"
    assert cs["modules"]["meic"]["ladder"] == ["conservative", "moderate", "aggressive"]
    assert cs["watchdog"]["interval_minutes"] == 10
    # discord is a supported push channel with no webhook stored -> reported as "not set", never a URL.
    assert cs["notify"]["webhooks"] == {"discord": "not set"}
    for v in cs["notify"]["webhooks"].values():
        assert "http" not in v


def test_system_card_renders_and_never_leaks_webhook_url(env, monkeypatch):
    from cherrypick.orchestrator import tasks

    monkeypatch.setattr(tasks, "query_verbose", lambda name: {"exists": False})
    _, cfg = env
    m = dashboard.build_model(cfg)
    static_html = dashboard._render_html(m, serve=False)
    served_html = dashboard._render_html(m, serve=True)
    for out in (static_html, served_html):
        assert "scheduled tasks" in out and "modules installed" in out and "cherrypick-watchdog" in out
        assert "http://" not in out and "https://" not in out  # never a webhook URL on the page
    # live doctor checks card is serve-only, same gating pattern as the GEX-style sections.
    assert "data-cp-doctor" in served_html and "/api/system" in served_html
    assert "data-cp-doctor" not in static_html


def test_embeds_are_serve_only_iframe_cards(env, monkeypatch):
    from cherrypick.orchestrator import tasks

    monkeypatch.setattr(tasks, "query_verbose", lambda name: {"exists": False})
    _, cfg = env
    m = dashboard.build_model(cfg)
    # only the enabled embed is in the model, with a local /embed/<id> iframe URL (no launch here)
    assert [e["id"] for e in m["embeds"]] == ["meic"]
    assert m["embeds"][0]["url"] == "/embed/meic"
    served = dashboard._render_html(m, serve=True)
    static = dashboard._render_html(m, serve=False)
    assert 'src="/embed/meic"' in served and "embedded module dashboards" in served
    assert "/embed/meic" not in static  # no iframe in the static file render


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


def test_build_model_includes_eod_session_card(env, monkeypatch):
    # Pin "today" (ET) to the MEIC session date seeded in the fixture so the EOD card has data.
    from datetime import datetime, timedelta, timezone

    from cherrypick.orchestrator import timeutil

    monkeypatch.setattr(
        timeutil,
        "now_et",
        lambda tz=None: datetime(2026, 7, 10, 20, 0, 0, tzinfo=timezone(timedelta(hours=-4))),
    )
    _, cfg = env
    m = dashboard.build_model(cfg)
    eod = m["eod"]
    assert eod["session"] == "2026-07-10"
    # Only the MEIC +95 win settled on 2026-07-10; the earnings 1.7e9 close is a different day.
    assert eod["suite"]["trades"] == 1
    assert eod["modules"]["meic"]["net_pnl"] == 95.0
    out = dashboard._render_html(m)
    assert "end of day" in out and "2026-07-10" in out
