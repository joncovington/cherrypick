"""Bounded execution on the earnings entry scan.

The scheduled entry run (`strategy_test_runner.py run_entries`) was killed twice at its 30-minute
external timeout because the Dolt query path has no client-side read timeout and Dolt does not honor
MySQL's server-side ``max_execution_time`` — a cold-starting or compacting server made ``cur.execute()``
block forever. These tests pin the bound that replaces that hang: a per-operation daemon-thread ceiling
(`_run_bounded`), a fail-fast on the calendar fetch, a per-symbol skip on a stalled evaluation, and an
overall wall-clock backstop that stops the scan and writes partial results.
"""

import time
import types

import pytest

import rank_strategies
import scanner
import strategy_test_runner as r


# --------------------------------------------------------------------------- _run_bounded unit tests
def test_run_bounded_returns_the_value_when_fast():
    assert r._run_bounded(lambda a, b: a + b, 5, 2, 3) == 5


def test_run_bounded_propagates_the_real_error():
    def boom():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        r._run_bounded(boom, 5)


def test_run_bounded_raises_optimeout_when_over_budget():
    t = time.time()
    with pytest.raises(r._OpTimeout):
        r._run_bounded(time.sleep, 0.3, 10)
    assert time.time() - t < 3, "must abandon the hung call promptly, not wait for it to finish"


# --------------------------------------------------------------------------- scan-loop integration
_CAL = [{"symbol": "AAA", "date": "2026-07-22", "timing": "After market close"}]


@pytest.fixture
def stub_entry_scan(monkeypatch):
    """Stub every external dependency of cmd_run_entries so the test drives only the timeout logic."""
    monkeypatch.setattr(rank_strategies, "_ensure_dolt_running", lambda: True)
    monkeypatch.setattr(rank_strategies, "_verify_tastytrade_connection", lambda: True)
    monkeypatch.setattr(r, "_capture_market_context", lambda day: None)
    monkeypatch.setattr(r, "_save_entry_review", lambda *a, **k: None)
    monkeypatch.setattr(r, "_write_eod_report", lambda *a, **k: None)
    monkeypatch.setattr(r, "_write_eod_analysis", lambda *a, **k: None)


def _config(**overrides):
    cfg = {"strategies": {}, "strat_test_portfolio": "per_strategy"}
    cfg.update(overrides)
    return cfg


def test_a_hung_symbol_is_skipped_and_the_run_still_succeeds(monkeypatch, stub_entry_scan):
    """One symbol's Dolt evaluation stalling must not fail the whole run — it is skipped and the run
    returns ok, exactly the difference between a clean partial result and a 30-minute external kill."""
    monkeypatch.setattr(scanner, "_load_config", lambda *a, **k: _config(dolt_symbol_timeout_seconds=0.3))
    monkeypatch.setattr(scanner, "fetch_entry_window_calendar", lambda config: list(_CAL))
    monkeypatch.setattr(rank_strategies, "evaluate_symbol", lambda *a, **k: time.sleep(30))

    result = r.cmd_run_entries(types.SimpleNamespace(date=None))

    assert result["ok"] is True
    assert result["opened"] == []
    assert any("evaluate_symbol_timeout" in s["reason"] for s in result["skipped"])


def test_calendar_fetch_timeout_fails_fast_with_a_clear_cause(monkeypatch, stub_entry_scan):
    monkeypatch.setattr(scanner, "_load_config", lambda *a, **k: _config(dolt_calendar_timeout_seconds=0.3))
    monkeypatch.setattr(scanner, "fetch_entry_window_calendar", lambda config: time.sleep(30))

    result = r.cmd_run_entries(types.SimpleNamespace(date=None))

    assert result["ok"] is False
    assert "calendar fetch exceeded" in result["error"]


def test_overall_budget_stops_the_scan_and_returns_partial(monkeypatch, stub_entry_scan):
    """A merely-slow (not hung) Dolt across many names must not push the run into its kill: the
    wall-clock backstop breaks the loop and still returns a result."""
    monkeypatch.setattr(scanner, "_load_config", lambda *a, **k: _config(entry_scan_budget_seconds=-1))
    monkeypatch.setattr(scanner, "fetch_entry_window_calendar", lambda config: list(_CAL))
    called = {"n": 0}

    def _should_not_run(*a, **k):
        called["n"] += 1

    monkeypatch.setattr(rank_strategies, "evaluate_symbol", _should_not_run)

    result = r.cmd_run_entries(types.SimpleNamespace(date=None))

    assert result["ok"] is True
    assert called["n"] == 0, "budget already blown -> no symbol should be evaluated"
    assert any(s["reason"] == "entry_scan_budget_exceeded" for s in result["skipped"])
