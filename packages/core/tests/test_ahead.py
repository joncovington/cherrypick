"""core.ahead: the next sessions, each gate that fires there, and the record on each kind of day."""

from datetime import date

from cherrypick.core import ahead

GATE = {"label": "X", "name": "Ex day", "effect": "No entries.", "depends_on": None}


def _on(days, unknown=()):
    def on(label, day):
        if day.isoformat() in unknown:
            return None
        return day.isoformat() in days

    return on


def test_sessions_skip_the_weekend_and_a_holiday_and_name_it():
    doc = ahead.sessions_ahead(date(2026, 11, 21), [], _on(set()), n=4)
    days = [s["session"] for s in doc["sessions"]]
    assert days == ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27"]
    friday = doc["sessions"][3]
    assert friday["closed_before"] == ["2026-11-26"]
    assert friday["early_close"] is True and friday["close"] == "13:00"
    assert doc["sessions"][0]["closed_before"] == []


def test_a_gate_fires_only_on_its_days_and_unknown_is_not_quiet():
    doc = ahead.sessions_ahead(date(2026, 11, 23), [GATE], _on({"2026-11-24"}, unknown={"2026-11-25"}), n=3)
    s = doc["sessions"]
    assert s[0]["gates"] == [] and s[0]["unknown"] == []
    assert [g["name"] for g in s[1]["gates"]] == ["Ex day"]
    assert s[2]["gates"] == [] and s[2]["unknown"] == ["X"]


def test_no_record_without_nets_and_none_for_a_gate_that_opts_out():
    assert ahead.sessions_ahead(date(2026, 11, 23), [GATE], _on(set()), n=1)["records"] is None
    doc = ahead.sessions_ahead(
        date(2026, 11, 23), [{**GATE, "record": False}], _on(set()), n=1, session_nets=[]
    )
    assert doc["records"] == []


def _nets(k_hit, k_rest):
    hit = [(f"2026-01-{d:02d}", 10.0) for d in range(1, k_hit + 1)]
    rest = [(f"2026-02-{d:02d}", -5.0) for d in range(1, k_rest + 1)]
    return hit + rest, {s for s, _ in hit}


def test_the_record_is_counted_but_not_read_below_the_floor():
    nets, days = _nets(3, 20)
    (rec,) = ahead.sessions_ahead(date(2026, 11, 23), [GATE], _on(days), n=1, session_nets=nets)["records"]
    assert rec["sessions"] == 3 and rec["other_sessions"] == 20
    assert rec["readable"] is False and rec["to_go"] == ahead.MIN_SESSIONS - 3
    assert rec["net"] is None and rec["mean"] is None and rec["other_mean"] is None


def test_the_record_reads_once_both_sides_reach_the_floor():
    n = ahead.MIN_SESSIONS
    nets, days = _nets(n, n)
    (rec,) = ahead.sessions_ahead(date(2026, 11, 23), [GATE], _on(days), n=1, session_nets=nets)["records"]
    assert rec["readable"] is True and rec["to_go"] == 0
    assert rec["net"] == 10.0 * n and rec["mean"] == 10.0 and rec["other_mean"] == -5.0


def test_an_unanswered_session_is_counted_apart():
    nets, days = _nets(2, 2)
    unknown = {nets[-1][0]}
    (rec,) = ahead.sessions_ahead(date(2026, 11, 23), [GATE], _on(days, unknown), n=1, session_nets=nets)[
        "records"
    ]
    assert (rec["sessions"], rec["other_sessions"], rec["unknown_sessions"]) == (2, 1, 1)


def test_calendar_labels():
    assert ahead.calendar_on("EarlyClose", date(2026, 11, 27)) is True
    assert ahead.calendar_on("TripleWitching", date(2026, 12, 18)) is True
    assert ahead.calendar_on("FOMC", date(2026, 12, 9)) is True
    assert ahead.calendar_on("FOMC", date(2099, 1, 28)) is None
    assert ahead.calendar_on("Nope", date(2026, 12, 9)) is None


def test_value_gates_pass_through():
    v = {"name": "VIX", "effect": "No entries above 25.", "depends_on": "VIX at the time"}
    assert ahead.sessions_ahead(date(2026, 11, 23), [], _on(set()), value_gates=[v], n=1)["value_gates"] == [
        v
    ]
