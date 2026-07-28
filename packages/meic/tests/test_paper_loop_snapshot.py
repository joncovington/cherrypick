"""The snapshot seam: paper_loop.run_iteration decides everything the pure engine ever
sees, and was almost untested. These tests drive one iteration with every subprocess
mocked and assert the snapshot handed to paper.process_symbol carries the market data
the gates consume — including the R4 range/ATR feeds and their fail-open None."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import paper_loop  # noqa: E402


@pytest.fixture
def loop_env(monkeypatch, tmp_path):
    """One-iteration harness: canned tt.py responses, no subprocesses, snapshot captured."""
    captured: dict = {}
    responses = {
        "get_vix1d": {"ok": True, "last": 16.5},
        "get_gex": {"ok": True, "net_gex": 1.0, "gex_positive": True},
        "get_atr": {"ok": True, "symbol": "SPX", "atr": 55.0, "days": 5},
        "get_intraday_range": {"ok": True, "symbol": "SPX", "day_high": 6050.0,
                               "day_low": 6020.0, "range_points": 30.0, "range_pct": 0.00497},
    }

    def fake_run_json(cmd):
        for verb, resp in responses.items():
            if verb in cmd:
                return dict(resp)
        return {"ok": False}

    monkeypatch.setattr(paper_loop, "_run_json", fake_run_json)
    monkeypatch.setattr(paper_loop, "_subrun", lambda cmd: None)
    monkeypatch.setattr(paper_loop, "_fetch_vix", lambda: 15.0)
    monkeypatch.setattr(paper_loop, "_fetch_overview", lambda s: (6040.0, 0.42, 0.61))
    monkeypatch.setattr(paper_loop, "_build_candidates",
                        lambda *a, **k: ([{"wing_width": 5}], {}, None))
    monkeypatch.setattr(paper_loop, "_eod_report_path", lambda day: tmp_path / "eod.md")

    def capture(snapshot, db_path, mode):
        captured["snapshot"] = snapshot
        return {"ok": True, "symbol": snapshot["symbol"], "results": {}}

    monkeypatch.setattr(paper_loop.paper, "process_symbol", capture)
    return {"captured": captured, "responses": responses}


_CFG = {"symbols": ["SPX"], "regime_atr_lookback_days": 5}


def test_snapshot_carries_the_range_and_atr_feeds(loop_env):
    paper_loop.run_iteration(_CFG, force=True)
    snap = loop_env["captured"]["snapshot"]
    assert snap["symbol"] == "SPX"
    assert snap["underlying_price"] == 6040.0
    assert snap["iv_rank"] == 0.42
    assert snap["vix"] == 15.0
    assert snap["vix1d_ratio"] == round(16.5 / 15.0, 3)
    # The R4 feeds, previously hardcoded None / absent:
    assert snap["atr_5day"] == 55.0
    assert snap["intraday_range_pct"] == 0.00497


def test_snapshot_fails_open_when_the_feeds_are_not_ready(loop_env):
    """Streamer down / warming up: get_atr and get_intraday_range answer ok=False and the
    snapshot must carry None — the gates stay inactive rather than reading a fabricated
    zero (a zero range would silently PASS the range gates)."""
    loop_env["responses"]["get_atr"] = {"ok": False, "error": "insufficient history"}
    loop_env["responses"]["get_intraday_range"] = {"ok": False, "error": "no summary row"}
    paper_loop.run_iteration(_CFG, force=True)
    snap = loop_env["captured"]["snapshot"]
    assert snap["atr_5day"] is None
    assert snap["intraday_range_pct"] is None


def test_symbol_without_a_price_is_skipped_not_processed(loop_env, monkeypatch):
    monkeypatch.setattr(paper_loop, "_fetch_overview", lambda s: (None, 0.42, 0.61))
    paper_loop.run_iteration(_CFG, force=True)
    assert "snapshot" not in loop_env["captured"]
