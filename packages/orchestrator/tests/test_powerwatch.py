"""The power watch: every channel hears about battery power, the moment it starts and every 15 min.

2026-10-08: a power outage ran the laptop down with live trading armed and nothing said so. Each
test below is one rule `decide` keeps; together they pin "alert now, repeat every 15 min, escalate
when low, say so once when power returns, and never read 'cannot tell' as either".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cherrypick.core import power

from cherrypick.orchestrator import powerwatch

RULES = powerwatch.settings({})
T0 = datetime(2026, 10, 8, 9, 0, tzinfo=timezone(timedelta(hours=-4)))
ON_BATTERY = {"known": True, "on_battery": True, "percent": 80, "seconds_left": 3 * 3600}
ON_AC = {"known": True, "on_battery": False, "percent": 97, "seconds_left": None}


def _at(minutes):
    return T0 + timedelta(minutes=minutes)


def test_the_first_reading_on_battery_alerts_at_once():
    alert, state = powerwatch.decide({}, ON_BATTERY, T0, RULES)
    assert alert["level"] == "WARNING" and "charge 80%" in alert["message"]
    assert state == {"on_battery_since": T0.isoformat(), "last_alert_at": T0.isoformat()}


def test_it_repeats_every_15_minutes_and_not_sooner():
    _, state = powerwatch.decide({}, ON_BATTERY, T0, RULES)
    sent = 1
    for minute in range(1, 46):
        alert, state = powerwatch.decide(state, ON_BATTERY, _at(minute), RULES)
        sent += alert is not None
        if minute in (15, 30, 45):
            assert alert is not None, minute
        elif minute not in (15, 30, 45):
            assert alert is None, minute
    assert sent == 4  # 0, 15, 30, 45


@pytest.mark.parametrize(
    "reading",
    [
        {**ON_BATTERY, "percent": 20},
        {**ON_BATTERY, "percent": 55, "seconds_left": 25 * 60},
    ],
)
def test_it_turns_critical_when_low_on_charge_or_time(reading):
    alert, _ = powerwatch.decide({}, reading, T0, RULES)
    assert alert["level"] == "CRITICAL"


def test_power_returning_is_said_once_with_how_long_it_ran_on_battery():
    _, state = powerwatch.decide({}, ON_BATTERY, T0, RULES)
    alert, state = powerwatch.decide(state, ON_AC, _at(42), RULES)
    assert alert["title"] == "Power restored" and "42 min" in alert["message"]
    assert state == {}
    assert powerwatch.decide(state, ON_AC, _at(43), RULES) == (None, {})


def test_cannot_tell_is_never_read_as_plugged_in_or_unplugged():
    _, state = powerwatch.decide({}, ON_BATTERY, T0, RULES)
    assert powerwatch.decide(state, dict(power.UNKNOWN), _at(20), RULES) == (None, state)


def test_an_armed_live_session_is_named():
    alert, _ = powerwatch.decide({}, ON_BATTERY, T0, RULES, live_armed=True)
    assert "LIVE TRADING IS ARMED" in alert["message"]


def test_run_sends_to_every_channel(tmp_path, monkeypatch):
    sent = []

    class FakeNotifier:
        def __init__(self, cfg):
            self.channels = cfg["channels"]

        def notify(self, level, key, title, message):
            sent.append((self.channels, level, title))

    import cherrypick.notify.notifier as notifier

    monkeypatch.setattr(notifier, "Notifier", FakeNotifier)
    monkeypatch.setattr(powerwatch.cfgmod, "state_file", lambda name: tmp_path / name)
    monkeypatch.setattr(powerwatch, "_live_armed", lambda cfg, today: False)
    out = powerwatch.run({"notify": {"channels": ["log"]}}, reading=ON_BATTERY, now=T0)
    assert out["alert"]["level"] == "WARNING"
    assert sent == [(["log", "desktop", "discord", "slack"], "WARNING", "On battery power")]


def test_the_windows_fields_parse_into_a_reading():
    assert power.parse(0, 9, 64, 5400) == {
        "known": True,
        "on_battery": True,
        "percent": 64,
        "seconds_left": 5400,
    }
    assert power.parse(1, 9, 97, 0xFFFFFFFF)["seconds_left"] is None
    assert power.parse(255, 9, 97, 0)["known"] is False
    assert power.parse(1, 128, 255, 0xFFFFFFFF)["percent"] is None  # a desktop with no battery
