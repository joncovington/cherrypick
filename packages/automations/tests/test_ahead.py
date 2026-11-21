"""automations.ahead: an automation's weekdays, event skips and event exits on the sessions ahead."""

from datetime import date

from cherrypick.automations import ahead
from cherrypick.automations.events import EventCalendar

ARM = {
    "symbol": "SPY",
    "weekdays": [0, 2, 4],
    "skip_events": {"labels": ["CPI"], "through_expiry": False},
    "exit": {"avoid_events": {"labels": ["CPI"], "before_close_min": 30}},
    "gates": [{"gate": "vix_level", "max": 20}],
}
CAL = EventCalendar({"CPI": ["2026-12-10"]}, {"CPI": [("2026-01-01", "2026-12-31")]})


def _by_day(doc):
    return {s["session"]: [g["label"] for g in s["gates"]] for s in doc["sessions"]}


def test_weekdays_skips_and_the_session_before_an_avoided_event():
    days = _by_day(ahead.ahead(ARM, CAL, date(2026, 12, 7)))
    assert days["2026-12-07"] == []  # Monday
    assert days["2026-12-08"] == [ahead.OFF_WEEKDAY]  # Tuesday
    assert days["2026-12-09"] == ["Before:CPI"]  # Wednesday, the session before CPI
    assert days["2026-12-10"] == [ahead.OFF_WEEKDAY, "CPI"]  # Thursday, CPI


def test_a_label_the_calendar_does_not_cover_is_unknown():
    doc = ahead.ahead(ARM, EventCalendar(), date(2026, 12, 7))
    assert all("CPI" in s["unknown"] for s in doc["sessions"])


def test_only_gates_a_trade_can_sit_through_keep_a_record():
    doc = ahead.ahead(ARM, CAL, date(2026, 12, 7), session_nets=[])
    assert [r["label"] for r in doc["records"]] == ["Before:CPI"]


def test_an_every_weekday_automation_has_no_weekday_gate():
    assert ahead.date_gates({"weekdays": [0, 1, 2, 3, 4]}) == []


def test_filter_gates_in_the_builders_words():
    (v,) = ahead.value_gates(ARM)
    assert v["name"] == "VIX level"
    assert v["effect"] == "Enters only when VIX is at most 20."
    assert v["depends_on"] == "VIX now, in index points"
