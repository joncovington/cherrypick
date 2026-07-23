"""Two reliability gaps the flies 2026-07-22 incident exposed:

1. Settlement-overdue: a module past the close with open positions it can't settle (stale feed) logs
   "cannot settle" every 2 min with no alert. The watchdog should WARN, opt-in per module.
2. Partial streamer stall: every underlying's spot froze at 10:05 ET while option quotes streamed to
   20:00, so the global "last event" age never went stale and nothing restarted. The guard must trip
   on a frozen underlying-spot feed even when the global age is fresh.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from cherrypick.orchestrator import watchdog as wd
from cherrypick.orchestrator.watchdog import CRITICAL, OK, WARN  # noqa: F401

pytestmark = pytest.mark.unit

_AFTER_CLOSE = datetime(2026, 7, 22, 17, 0)
_BEFORE_CLOSE = datetime(2026, 7, 22, 10, 0)


# --------------------------------------------------------------------------- Feature 1: settlement
def _run_settle(monkeypatch, status_obj, now_et=_AFTER_CLOSE, is_trading=True, opted_in=True):
    monkeypatch.setattr(wd.cfgmod, "module_root", lambda *a, **k: Path("."))

    class _R:
        returncode = 0
        stdout = json.dumps(status_obj) if status_obj is not None else ""

    monkeypatch.setattr(wd, "_run_module", lambda *a, **k: _R())
    paper = {"status_argv": ["src/paper_loop.py", "--status"]}
    if opted_in:
        paper["settlement_check"] = True
    return wd._check_settlement("flies", {"paper": paper}, now_et, is_trading)


def test_settlement_overdue_warns_with_count_and_reason(monkeypatch):
    out = _run_settle(monkeypatch, {"session_settled": False, "positions_today": 5,
                                     "data_reason": "no price within 300s"})
    assert len(out) == 1
    f = out[0]
    assert f.key == "flies.settle_overdue" and f.status == WARN
    assert "5 open position" in f.message and "no price within 300s" in f.message


def test_settled_reports_ok_so_a_prior_alert_recovers(monkeypatch):
    out = _run_settle(monkeypatch, {"session_settled": True, "positions_today": 5})
    assert out and out[0].status == OK


def test_no_check_unless_opted_in(monkeypatch):
    assert _run_settle(monkeypatch, {"session_settled": False, "positions_today": 5}, opted_in=False) == []


def test_no_check_before_the_close(monkeypatch):
    assert _run_settle(monkeypatch, {"session_settled": False, "positions_today": 5},
                       now_et=_BEFORE_CLOSE) == []


def test_no_check_off_a_trading_day(monkeypatch):
    assert _run_settle(monkeypatch, {"session_settled": False, "positions_today": 5},
                       is_trading=False) == []


def test_silent_when_status_lacks_the_settlement_signal(monkeypatch):
    # A module whose --status doesn't report settlement (e.g. MEIC) must produce no finding either way.
    assert _run_settle(monkeypatch, {"running": True, "pid": 123}) == []


def test_silent_when_status_unreadable(monkeypatch):
    assert _run_settle(monkeypatch, None) == []


# --------------------------------------------------------------------------- Feature 2: partial stall
def test_underlying_stale_age_reader_only_accepts_numbers():
    assert wd._streamer_underlying_stale_age({"underlyings_stale_age_s": 42}) == 42.0
    assert wd._streamer_underlying_stale_age({"underlyings_stale_age_s": None}) is None
    assert wd._streamer_underlying_stale_age({}) is None


def test_stale_detail_names_the_dead_feed():
    assert "underlying spot frozen" in wd._streamer_stale_detail(30, 9999, 240)
    assert "no events for" in wd._streamer_stale_detail(9999, 30, 240)
    both = wd._streamer_stale_detail(9999, 9999, 240)
    assert "no events for" in both and "underlying spot frozen" in both


def _run_streamer(monkeypatch, status_obj, started_ok=True):
    class _R:
        returncode = 0
        stdout = json.dumps(status_obj)

    monkeypatch.setattr(wd, "_run_module", lambda *a, **k: _R())
    monkeypatch.setattr(wd, "_stop_streamer", lambda *a, **k: True)
    calls = {"started": False}
    monkeypatch.setattr(wd, "_start_streamer",
                        lambda *a, **k: (calls.__setitem__("started", True) or started_ok))
    spec = {"status_argv": ["s"], "start_argv": ["r"], "stale_restart_seconds": 240, "auto_restart": True}
    findings = wd._check_streamer_health("streamer", Path("."), spec)
    return findings, calls


def test_frozen_underlying_restarts_even_when_global_age_is_fresh(monkeypatch):
    """The core fix: option quotes keep oldest_event_age_s fresh, but the underlyings froze -> restart."""
    status = {"running": True, "oldest_event_age_s": 30, "underlyings_stale_age_s": 9999}
    findings, calls = _run_streamer(monkeypatch, status)
    assert calls["started"] is True
    assert findings[0].status == WARN and "restarted" in findings[0].title
    assert "underlying spot frozen" in findings[0].message


def test_no_restart_when_both_feeds_fresh(monkeypatch):
    status = {"running": True, "oldest_event_age_s": 30, "underlyings_stale_age_s": 30}
    findings, calls = _run_streamer(monkeypatch, status)
    assert calls["started"] is False
    assert findings[0].status == OK


def test_falls_back_to_global_age_when_underlying_unreported(monkeypatch):
    # An older producer without the new field must still restart on a global silence (backward compat).
    findings, calls = _run_streamer(monkeypatch, {"running": True, "oldest_event_age_s": 9999})
    assert calls["started"] is True
    assert "no events for" in findings[0].message
