"""Strategies retired from strat_test by date (2026-10-12, effective 2026-10-15).

`atm_calendar` and `double_calendar` open nothing from 2026-10-15; their screen rows are still written
and the refusal is an execution-stage drop, so "retired" never reads as "nothing qualified".
"""

import datetime as dt
import types

import pytest

from cherrypick.earnings import rank_strategies, scanner
from cherrypick.earnings import strat_test_harness as r

_CAL = [{"symbol": "AAA", "date": "2026-10-20", "timing": "After market close"}]


def test_retirement_binds_from_its_session_and_not_before():
    for name in ("atm_calendar", "double_calendar"):
        assert not r.strategy_retired(name, "2026-10-14")
        assert r.strategy_retired(name, "2026-10-15")
        assert r.strategy_retired(name, "2026-11-02")
    assert not r.strategy_retired("broken_wing_butterfly", "2026-10-23")
    assert r.strategy_retired("broken_wing_butterfly", "2026-10-26")
    assert not r.strategy_retired("iron_fly", "2027-01-04")


def test_the_retired_strategies_are_all_still_registered():
    """Retired from the harness, not removed from the registry: the screen keeps scoring them and
    their open rows still resolve their exits by name."""
    assert r.STRAT_TEST_RETIRED == {
        "atm_calendar": "2026-10-15",
        "double_calendar": "2026-10-15",
        "broken_wing_butterfly": "2026-10-26",
    }
    names = {s["name"] if isinstance(s, dict) else s for s in rank_strategies.STRATEGY_REGISTRY}
    assert set(r.STRAT_TEST_RETIRED) <= names


def _drive(monkeypatch, today):
    rows = []

    class _Today(dt.date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(r, "_date", _Today)
    monkeypatch.setattr(rank_strategies, "_ensure_dolt_running", lambda: True)
    monkeypatch.setattr(rank_strategies, "_verify_tastytrade_connection", lambda: True)
    monkeypatch.setattr(r, "_capture_market_context", lambda day: None)
    monkeypatch.setattr(r, "_save_entry_review", lambda *a, **k: None)
    monkeypatch.setattr(
        scanner,
        "_load_config",
        lambda *a, **k: {
            "strategies": {name: {} for name in rank_strategies._REGISTRY_BY_NAME},
            "strat_test_portfolio": "per_strategy",
        },
    )
    monkeypatch.setattr(scanner, "fetch_entry_window_calendar", lambda config, **k: list(_CAL))
    monkeypatch.setattr(
        r,
        "_scan_stream",
        lambda *a, **k: [
            (
                _CAL[0],
                [{"name": "atm_calendar", "reject_reasons": [], "accepted": True, "criteria": {}}],
                None,
            )
        ],
    )
    monkeypatch.setattr(r, "_log_scan_row", lambda *a, **kw: rows.append((a, kw)))
    monkeypatch.setattr(r, "_past_entry_window", lambda config: False)
    return rows


def test_a_retired_strategy_is_dropped_at_execution_with_its_screen_row_kept(monkeypatch):
    rows = _drive(monkeypatch, dt.date(2026, 10, 19))
    monkeypatch.setitem(
        r._ORDER_FNS, "atm_calendar", lambda *a: pytest.fail("built an order for a retired strategy")
    )
    result = r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert result["opened"] == []
    assert any(s["reason"] == "strategy_retired" for s in result["skipped"])
    assert [kw.get("stage") for _, kw in rows] == ["screen", "execution"]


def test_before_the_boundary_the_strategy_still_builds_its_order(monkeypatch):
    """The guard, shown to fire only from its date: on 10-14 the order build is reached."""
    _drive(monkeypatch, dt.date(2026, 10, 14))
    reached = []
    monkeypatch.setitem(
        r._ORDER_FNS, "atm_calendar", lambda *a: reached.append(1) or {"ok": False, "error": "stub"}
    )
    result = r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert reached == [1]
    assert not any(s["reason"] == "strategy_retired" for s in result["skipped"])
