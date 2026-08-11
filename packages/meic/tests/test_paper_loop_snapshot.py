"""The snapshot seam: paper_loop.run_iteration decides everything the pure engine ever
sees, and was almost untested. These tests drive one iteration with every subprocess
mocked and assert the snapshot handed to paper.process_symbol carries the market data
the gates consume — including the R4 range/ATR feeds and their fail-open None."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import paper_loop  # noqa: E402


@pytest.fixture
def loop_env(monkeypatch, tmp_path):
    """One-iteration harness: canned tt.py responses, no subprocesses, snapshot captured."""
    captured: dict = {}
    responses = {
        "get_vix1d": {"ok": True, "last": 16.5},
        "get_gex": {"ok": True, "net_gex": 1.0, "gex_positive": True},
        "get_atr": {"ok": True, "symbol": "SPX", "atr": 55.0, "days": 5},
        "get_intraday_range": {
            "ok": True,
            "symbol": "SPX",
            "day_open": 6035.0,
            "day_high": 6050.0,
            "day_low": 6020.0,
            "range_points": 30.0,
            "range_pct": 0.00497,
        },
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
    monkeypatch.setattr(paper_loop, "_build_candidates", lambda *a, **k: ([{"wing_width": 5}], {}, None))
    monkeypatch.setattr(paper_loop, "_eod_report_path", lambda day: tmp_path / "eod.md")

    def capture(snapshot, db_path, mode, extra_profiles=None):
        captured["snapshot"] = snapshot
        captured["extra_profiles"] = extra_profiles
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
    # day_open rides the same get_intraday_range call, no extra round trip — feeds the regime
    # trend dimension (spot vs. today's open).
    assert snap["day_open"] == 6035.0


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
    assert snap["day_open"] is None


def test_symbol_without_a_price_is_skipped_not_processed(loop_env, monkeypatch):
    monkeypatch.setattr(paper_loop, "_fetch_overview", lambda s: (None, 0.42, 0.61))
    paper_loop.run_iteration(_CFG, force=True)
    assert "snapshot" not in loop_env["captured"]


def test_iteration_logs_duration_and_open_count(loop_env, monkeypatch):
    """loop_log.duration_ms existed as a column and get_step_timing could read it, but nothing
    in run_iteration ever wrote one -- the load ceiling that governs how many streams/positions
    the loop can carry was invisible until it was already missing ticks. Pin that the
    log_loop_action call now always carries both."""
    calls = []
    monkeypatch.setattr(paper_loop, "_subrun", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(paper_loop, "_open_count", lambda: 7)

    paper_loop.run_iteration(_CFG, force=True)

    log_calls = [c for c in calls if "log_loop_action" in c]
    assert len(log_calls) == 1
    cmd = log_calls[0]
    assert "--duration_ms" in cmd
    duration = int(cmd[cmd.index("--duration_ms") + 1])
    assert duration >= 0
    assert "--open_trades" in cmd
    assert cmd[cmd.index("--open_trades") + 1] == "7"


def test_gate_block_logs_the_full_per_stream_outcome_map_not_the_collapsed_string(loop_env, monkeypatch):
    """_fmt_symbol collapses skips to 'all <reason>' / 'N skip' for the human-readable log line —
    that collapse must not be the only record. gate_block carries the FULL per-profile map so a
    zero-entry session is auditable per stream, not one undifferentiated blank."""
    calls = []
    monkeypatch.setattr(paper_loop, "_subrun", lambda cmd: calls.append(cmd))

    def capture(snapshot, db_path, mode, extra_profiles=None):
        return {
            "ok": True,
            "symbol": snapshot["symbol"],
            "results": {
                "control": [{"entry": "skipped", "reason": "iv_rank_below_floor"}],
                "open": [{"entry": "skipped", "reason": "regime_atr_elevated"}],
                "width-5": [{"entry": "filled", "net_credit": 1.5}],
            },
        }

    monkeypatch.setattr(paper_loop.paper, "process_symbol", capture)

    paper_loop.run_iteration(_CFG, force=True)

    block_calls = [c for c in calls if "gate_block" in c]
    assert len(block_calls) == 1
    cmd = block_calls[0]
    assert cmd[cmd.index("--symbol") + 1] == "SPX"
    payload = json.loads(cmd[cmd.index("--reasoning") + 1])
    assert payload == {
        "control": "iv_rank_below_floor",
        "open": "regime_atr_elevated",
        "width-5": "FILL $1.5",
    }


def test_gate_block_is_not_logged_when_there_are_no_outcomes(loop_env, monkeypatch):
    """A symbol with no price is skipped before any profile is evaluated (see
    test_symbol_without_a_price_is_skipped_not_processed) — no outcomes means no gate_block row,
    not an empty one."""
    calls = []
    monkeypatch.setattr(paper_loop, "_subrun", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(paper_loop, "_fetch_overview", lambda s: (None, 0.42, 0.61))

    paper_loop.run_iteration(_CFG, force=True)

    assert not [c for c in calls if "gate_block" in c]


def test_iteration_logs_duration_even_when_open_count_fails(loop_env, monkeypatch):
    """_open_count is a best-effort read (its own subprocess call) -- a failure there must not
    cost the iteration its duration instrumentation, which is the more load-bearing of the two."""
    calls = []
    monkeypatch.setattr(paper_loop, "_subrun", lambda cmd: calls.append(cmd))

    def boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(paper_loop, "_open_count", boom)

    paper_loop.run_iteration(_CFG, force=True)

    log_calls = [c for c in calls if "log_loop_action" in c]
    assert len(log_calls) == 1
    cmd = log_calls[0]
    assert "--duration_ms" in cmd
    assert "--open_trades" not in cmd


# --------------------------------------------------------------------------- iteration regime


def _iteration_regime_calls(calls):
    return [c for c in calls if "save_iteration_regime" in c]


def test_iteration_regime_is_written_on_a_tick_that_entered_nothing(loop_env, monkeypatch):
    """The denominator. Every regime row in ic_trades is conditioned on having entered, so without
    this the recorded regime distribution is censored by the very gates it would be used to judge —
    a refused tick left no trace of what it refused."""
    calls = []
    monkeypatch.setattr(paper_loop, "_subrun", lambda cmd: calls.append(cmd))

    def refuse(snapshot, db_path, mode, extra_profiles=None):
        return {
            "ok": True,
            "symbol": snapshot["symbol"],
            "results": {"control": [{"entry": "skipped", "reason": "iv_rank_floor"}]},
        }

    monkeypatch.setattr(paper_loop.paper, "process_symbol", refuse)
    paper_loop.run_iteration(_CFG, force=True)

    written = _iteration_regime_calls(calls)
    assert len(written) == 1
    cmd = written[0]
    assert cmd[cmd.index("--symbol") + 1] == "SPX"
    assert cmd[cmd.index("--entries_n") + 1] == "0"
    assert cmd[cmd.index("--blocked_n") + 1] == "1"
    assert cmd[cmd.index("--underlying_price") + 1] == "6040.0"

    payload = json.loads(cmd[cmd.index("--regime") + 1])
    # Tagged from the snapshot alone — no structure needed, which is what lets a refused tick carry
    # a regime at all.
    assert payload["vol_implied_bucket"] == "normal"  # iv_rank 0.42, between the 0.30/0.60 cuts
    assert payload["vol_realized_value"] == pytest.approx(55.0 / 6040.0)
    assert payload["trend_bucket"] in ("flat", "up_from_open", "down_from_open")


def test_iteration_regime_counts_fills_and_blocks_separately(loop_env, monkeypatch):
    calls = []
    monkeypatch.setattr(paper_loop, "_subrun", lambda cmd: calls.append(cmd))

    def mixed(snapshot, db_path, mode, extra_profiles=None):
        return {
            "ok": True,
            "symbol": snapshot["symbol"],
            "results": {
                "control": [{"entry": "filled", "net_credit": 1.8}],
                "open": [{"entry": "filled", "net_credit": 1.9}],
                "width-5": [{"entry": "skipped", "reason": "credit_floor"}],
                "width-10": [{"decision": {"action": "hold"}}],  # neither a fill nor a refusal
            },
        }

    monkeypatch.setattr(paper_loop.paper, "process_symbol", mixed)
    paper_loop.run_iteration(_CFG, force=True)

    cmd = _iteration_regime_calls(calls)[0]
    assert cmd[cmd.index("--entries_n") + 1] == "2"
    assert cmd[cmd.index("--blocked_n") + 1] == "1"


def test_iteration_regime_carries_no_structure_dimensions(loop_env, monkeypatch):
    """skew and center_offset describe the structure we chose, so on a refused tick they would be
    'unknown' 100% of the time — a column degenerate by construction, the exact thing
    regime_coverage exists to flag."""
    calls = []
    monkeypatch.setattr(paper_loop, "_subrun", lambda cmd: calls.append(cmd))
    paper_loop.run_iteration(_CFG, force=True)

    payload = json.loads(
        _iteration_regime_calls(calls)[0][_iteration_regime_calls(calls)[0].index("--regime") + 1]
    )
    assert not [k for k in payload if k.startswith(("skew", "center_offset"))]


def test_iteration_regime_failure_never_breaks_the_iteration(loop_env, monkeypatch):
    """Telemetry is best-effort: a lost row is a lost observation, never a lost iteration."""

    def boom(cmd):
        if "save_iteration_regime" in cmd:
            raise RuntimeError("db locked")

    monkeypatch.setattr(paper_loop, "_subrun", boom)
    paper_loop.run_iteration(_CFG, force=True)  # must not raise
    assert loop_env["captured"]["snapshot"]["symbol"] == "SPX"
