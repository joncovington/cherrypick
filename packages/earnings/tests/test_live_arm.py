"""The per-day arm for live earnings orders (live_arm.py, 2026-11-06): an opening live order needs
today's arm record, written only on a literal YES with the live switch on; yesterday's record or a
time past the disarm refuses; a closing order is never held back. Fake clocks in ET, never the host
date."""

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from cherrypick.core import live as core_live

from cherrypick.earnings import live_arm, tt

ET = ZoneInfo("America/New_York")
DAY = datetime(2026, 11, 9, 10, 0, tzinfo=ET)


@pytest.fixture(autouse=True)
def _clean_record():
    core_live.disarm("earnings")
    yield
    core_live.disarm("earnings")


def test_arming_needs_a_literal_yes_and_the_live_switch():
    assert live_arm.arm("yes", live_enabled=True, now=DAY)["ok"] is False
    assert live_arm.arm(None, live_enabled=True, now=DAY)["ok"] is False
    assert live_arm.arm("YES", live_enabled=False, now=DAY)["ok"] is False
    assert core_live.arm_record_date("earnings") is None
    out = live_arm.arm("YES", live_enabled=True, now=DAY)
    assert out["ok"] is True and out["armed_for"] == "2026-11-09"
    assert core_live.arm_record_date("earnings") == "2026-11-09"


def test_arming_past_the_disarm_time_is_refused():
    late = DAY.replace(hour=17, minute=5)
    assert live_arm.arm("YES", live_enabled=True, now=late)["ok"] is False


def test_the_arm_holds_for_its_own_day_only_and_until_the_disarm():
    assert "arming is per-day" in live_arm.refusal(DAY)
    live_arm.arm("YES", live_enabled=True, now=DAY)
    assert live_arm.refusal(DAY) is None
    assert live_arm.refusal(DAY.replace(hour=16, minute=59)) is None
    assert "disarm" in live_arm.refusal(DAY.replace(hour=17, minute=0))
    assert "arming is per-day" in live_arm.refusal(DAY.replace(day=10))
    live_arm.disarm()
    assert live_arm.refusal(DAY) is not None


def test_an_unarmed_live_open_is_refused_and_a_close_goes_through(monkeypatch):
    reached = []

    async def _fake_get_account(account_number=None):
        reached.append(True)
        raise RuntimeError("stop at the account")

    monkeypatch.setattr(tt, "_get_account", _fake_get_account)
    monkeypatch.setattr(tt, "_live_trading_enabled", lambda: True)
    monkeypatch.setattr(tt, "kill_refusal", lambda spec, config, db_path=None: None)

    def run(action):
        spec = {
            "legs": [{"symbol": "AAA_C", "instrument_type": "Equity Option", "action": action, "quantity": 1}]
        }
        args = type(
            "Args", (), {"live": True, "order": json.dumps(spec), "account_number": None, "wait": 0}
        )()
        return asyncio.run(tt.cmd_execute_trade(args))

    out = run("Sell to Open")
    assert out["ok"] is False and "not armed" in out["error"] and reached == []
    run("Buy to Close")
    assert reached == [True]
