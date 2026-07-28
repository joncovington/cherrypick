"""The hub's first charts: the suite equity card (inline timeseries off report.run's
daily series, VIX overlaid, epoch marked) and the calibration-progression card."""

import sqlite3

import pytest

from cherrypick.orchestrator import dashboard

pytestmark = pytest.mark.unit


def _pnl(daily, suite=None, epoch=None):
    return {
        "daily": daily,
        "suite": suite or {"net_pnl": 25.0, "net_pnl_2x_slippage": 10.0},
        "data_epoch": epoch,
    }


_DAILY = [
    {"session": "2026-07-21", "net_pnl": -10.0, "trades": 2, "by_module": {"meic": -10.0}},
    {"session": "2026-07-22", "net_pnl": 15.0, "trades": 1, "by_module": {"meic": 5.0, "flies": 10.0}},
    {"session": "2026-07-24", "net_pnl": 20.0, "trades": 3, "by_module": {"meic": 20.0}},
]


def test_equity_card_builds_cumulative_series(monkeypatch):
    monkeypatch.setattr(dashboard, "_vix_by_session", lambda cfg: {})
    card = dashboard._equity_card_payload({}, _pnl(_DAILY))
    assert card["ok"] is True
    ts = card["timeseries"]
    assert ts["labels"] == ["2026-07-21", "2026-07-22", "2026-07-24"]
    suite_line = ts["series"][0]
    assert suite_line["name"] == "suite"
    assert suite_line["values"] == [-10.0, 5.0, 25.0]  # cumulative, not per-day
    names = [s["name"] for s in ts["series"][1:]]
    assert "meic" in names and "flies" in names
    meic_line = next(s for s in ts["series"] if s["name"] == "meic")
    assert meic_line["values"] == [-10.0, -5.0, 15.0]
    assert "overlay" not in ts  # no VIX rows -> no fabricated overlay


def test_equity_card_overlays_vix_with_gaps(monkeypatch):
    monkeypatch.setattr(dashboard, "_vix_by_session",
                        lambda cfg: {"2026-07-21": 15.0, "2026-07-24": 18.5})
    card = dashboard._equity_card_payload({}, _pnl(_DAILY))
    # The un-captured middle session is None — the renderer BREAKS the line there.
    assert card["timeseries"]["overlay"]["values"] == [15.0, None, 18.5]


def test_equity_card_marks_the_epoch(monkeypatch):
    monkeypatch.setattr(dashboard, "_vix_by_session", lambda cfg: {})
    card = dashboard._equity_card_payload({}, _pnl(_DAILY, epoch={"date": "2026-07-22"}))
    assert card["timeseries"]["markers"] == [{"label": "epoch", "at": "2026-07-22"}]


def test_equity_card_without_history_is_an_honest_error(monkeypatch):
    monkeypatch.setattr(dashboard, "_vix_by_session", lambda cfg: {})
    card = dashboard._equity_card_payload({}, _pnl([]))
    assert card["ok"] is False


def test_vix_by_session_reads_market_context(tmp_path):
    (tmp_path / "meic").mkdir()
    db = tmp_path / "meic" / "p.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE market_context (context_date TEXT PRIMARY KEY, vix REAL)")
    conn.execute("INSERT INTO market_context VALUES ('2026-07-21', 15.5)")
    conn.commit()
    conn.close()
    cfg = {"modules": {"meic": {"enabled": True, "path": str(tmp_path / "meic"),
                                "paper": {"paper_db": "p.db"}}}}
    assert dashboard._vix_by_session(cfg) == {"2026-07-21": 15.5}


def test_vix_by_session_degrades_without_the_table(tmp_path):
    (tmp_path / "meic").mkdir()
    sqlite3.connect(tmp_path / "meic" / "p.db").close()
    cfg = {"modules": {"meic": {"enabled": True, "path": str(tmp_path / "meic"),
                                "paper": {"paper_db": "p.db"}}}}
    assert dashboard._vix_by_session(cfg) == {}


# --------------------------------------------------------------------- calibration card
def _module_view(checks, eligible=False, verdict="hold"):
    return {
        "name": "meic",
        "calibration": {
            "ok": True,
            "ladder": ["conservative"],
            "profiles": {"conservative": {
                "reading": {},
                "recommendation": {"checks": checks, "eligible": eligible,
                                   "recommendation": verdict},
            }},
        },
    }


def test_calibration_card_renders_every_check_as_progress():
    checks = {
        "sample": {"value": 12, "threshold": 20, "pass": False},
        "win_rate": {"value": 0.7, "threshold": 0.6, "pass": True},
        "slippage_survival": {"value": 42.0,
                              "threshold": "net > 0 at 2x slippage over the full sample",
                              "pass": True},
    }
    out = dashboard._calibration_progress_html([_module_view(checks)])
    assert "progress toward promotion" in out
    assert "sample" in out and "12 / 20" in out
    assert "win_rate" in out and "0.70 / 0.60" in out
    # Non-numeric threshold renders as a chip, not a fabricated bar.
    assert "slippage_survival" in out and "PASS" in out


def test_calibration_card_empty_without_ladders():
    views = [{"name": "flies", "calibration": {"ok": True, "ladder": []}}]
    assert dashboard._calibration_progress_html(views) == ""


def test_static_render_embeds_the_inline_equity_card():
    model = {
        "overall": "OK", "modules": [], "logs": [], "tasks": [],
        "modules_installed": [], "config_summary": {}, "sections": [], "embeds": [],
        "suite": {}, "eod": None, "active_findings": [], "notify_channels": [],
        "equity_card": {"ok": True, "metrics": [],
                        "timeseries": {"labels": ["2026-07-21"],
                                       "series": [{"name": "suite", "values": [1.0]}]}},
    }
    static = dashboard._render_html(model, serve=False)
    assert 'class="cpdata"' in static          # payload baked into the page
    assert "renderTimeseries" in static        # renderer shipped inline
    assert 'data-endpoint="/api/section/' not in static  # and no card polls
