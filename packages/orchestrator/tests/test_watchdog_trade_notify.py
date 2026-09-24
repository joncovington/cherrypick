"""The watchdog's view of the trade notifier, which it otherwise calls inside a bare except.

2026-09-24: pmcc's and bwb's formatters raised from 09:31 ET, the notifier's state was never saved,
and every flies event went out ~95 times with no finding anywhere. Both failure shapes are read from
the notifier's own state file, so these tests drive that file rather than a hand-kept list.
"""

import json
import os
import time

import pytest

from cherrypick.orchestrator import trade_notifier as tn
from cherrypick.orchestrator import watchdog as wd

pytestmark = pytest.mark.unit

_WANTS = {"flies": {"paper": {"notify_trades": True}}, "pmcc": {"paper": {"notify_trades": True}}}


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "trade_notify.json"
    monkeypatch.setattr(tn, "_STATE", path)
    monkeypatch.setattr(wd.cfgmod, "enabled_modules", lambda cfg: _WANTS)
    return path


def _by_key(findings):
    return {f.key: f for f in findings}


def test_a_module_whose_pass_raised_is_a_warning_naming_it(state_file):
    state_file.write_text(
        json.dumps(
            {
                "flies": {"notified_entry_ids": ["a"]},
                "pmcc": {
                    "last_error": {
                        "at": "2026-09-24T13:31:00+00:00",
                        "error": "IndexError: No item with that key",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    f = _by_key(wd._check_trade_notify({}))
    assert f["trade_notify.errors"].status == wd.WARN
    assert "pmcc" in f["trade_notify.errors"].title and "flies" not in f["trade_notify.errors"].title
    assert "IndexError: No item with that key" in f["trade_notify.errors"].message
    assert f["trade_notify.saved"].status == wd.OK


def test_a_state_file_that_stopped_moving_is_a_warning(state_file):
    state_file.write_text(json.dumps({"flies": {}}), encoding="utf-8")
    old = time.time() - (wd._TRADE_NOTIFY_STALE_MIN + 5) * 60
    os.utime(state_file, (old, old))
    f = _by_key(wd._check_trade_notify({}))
    assert f["trade_notify.saved"].status == wd.WARN
    assert "re-sends" in f["trade_notify.saved"].message


def test_a_healthy_notifier_reports_ok_twice(state_file):
    state_file.write_text(json.dumps({"flies": {}, "pmcc": {}}), encoding="utf-8")
    f = _by_key(wd._check_trade_notify({}))
    assert f["trade_notify.saved"].status == wd.OK and f["trade_notify.errors"].status == wd.OK


def test_silent_when_no_module_asks_for_trade_notifications(state_file, monkeypatch):
    state_file.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(wd.cfgmod, "enabled_modules", lambda cfg: {"flies": {"paper": {}}})
    assert wd._check_trade_notify({}) == []
