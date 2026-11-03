"""The watchdog's read of the account-alert service (2026-11-02): fills only REST saw, and a REST
read failing for minutes. File-only, like every check on the tick."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cherrypick.core.live import account_alerts as _aa

from cherrypick.orchestrator import watchdog as wd
from cherrypick.orchestrator.watchdog import OK, WARN

pytestmark = pytest.mark.unit

NOW = datetime.now(timezone.utc)
TODAY = NOW.astimezone().date().isoformat()


def _status(monkeypatch, **fields):
    base = {"day": TODAY, "stream_missed_fills": [], "rest_failures": 0, "last_rest_ok_at": NOW.isoformat()}
    monkeypatch.setattr(_aa, "read_status", lambda: {**base, **fields})


def _by_key(findings):
    return {f.key: f for f in findings}


def test_a_healthy_service_is_ok(monkeypatch):
    _status(monkeypatch)
    out = _by_key(wd._check_account_alerts(True, NOW))
    assert out["account_alerts.stream"].status == OK and out["account_alerts.rest"].status == OK


def test_fills_only_rest_saw_warn_once_per_fill(monkeypatch):
    """Shown to fail without the check: the stream dropped a fill and nothing said so."""
    _status(monkeypatch, stream_missed_fills=["4242", "4243"])
    stream = _by_key(wd._check_account_alerts(True, NOW))["account_alerts.stream"]
    assert stream.status == WARN and "2 fill(s)" in stream.message
    assert stream.members == ("missed:4242", "missed:4243")


def test_a_rest_read_failing_for_minutes_warns(monkeypatch):
    stale = (NOW - timedelta(minutes=10)).isoformat()
    _status(monkeypatch, rest_failures=12, last_rest_ok_at=stale, last_rest_error="BrokerCallTimeout: 30 s")
    rest = _by_key(wd._check_account_alerts(True, NOW))["account_alerts.rest"]
    assert rest.status == WARN and "BrokerCallTimeout" in rest.message
    # one failure between good reads is not a warning
    _status(monkeypatch, rest_failures=1)
    assert _by_key(wd._check_account_alerts(True, NOW))["account_alerts.rest"].status == OK


def test_outside_the_session_or_a_stale_day_says_nothing(monkeypatch):
    _status(monkeypatch, stream_missed_fills=["1"])
    assert wd._check_account_alerts(False, NOW) == []
    _status(monkeypatch, day="2026-01-02", stream_missed_fills=["1"])
    assert wd._check_account_alerts(True, NOW) == []
