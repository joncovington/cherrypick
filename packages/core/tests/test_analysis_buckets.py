"""core.analysis.buckets: one engine and one column set for P&L by something known at entry."""

import pytest

from cherrypick.core import analysis
from cherrypick.core.analysis import buckets


def _r(net, session, entry_time=None):
    return {"arm": "control", "net_pnl": net, "session": session, "entry_time": entry_time,
            "slippage": None, "capital": None, "max_profit": None}  # fmt: skip


def test_a_table_has_one_column_set_and_marks_thin_rows():
    records = [_r(10.0, f"2026-10-{d:02d}") for d in (5, 12, 19, 26)] + [_r(-30.0, "2026-10-05")]
    t = buckets.table(records, buckets.weekday, "weekday")
    [mon] = t["rows"]
    assert mon == {
        "bucket": "Mon", "trades": 5, "sessions": 4, "net": 10.0, "win_rate": 0.8,
        "avg_net": 2.0, "best": 10.0, "worst": -30.0, "thin": True,
    }  # fmt: skip


def test_thin_is_counted_in_sessions_not_trades():
    # Twenty trades on one session are one session: thin, however many trades.
    t = buckets.table([_r(1.0, "2026-10-05") for _ in range(20)], buckets.weekday, "weekday")
    assert t["rows"][0]["trades"] == 20 and t["rows"][0]["thin"] is True
    many = [_r(1.0, f"2026-{m:02d}-{d:02d}") for m, d in ((9, 7), (9, 14), (9, 21), (9, 28), (10, 5))]
    assert buckets.table(many, buckets.weekday, "weekday")["rows"][0]["thin"] is False


def test_weekdays_come_in_calendar_order():
    records = [_r(1.0, "2026-10-09"), _r(1.0, "2026-10-05"), _r(1.0, "2026-10-07")]
    rows = buckets.table(records, buckets.weekday, "weekday")["rows"]
    assert [r["bucket"] for r in rows] == ["Mon", "Wed", "Fri"]


def test_hour_reads_the_entry_time_and_counts_what_it_cannot_bucket():
    records = [_r(5.0, "2026-10-05", "2026-10-05T10:30:47-04:00"), _r(5.0, "2026-10-05")]
    t = buckets.table(records, buckets.hour, "hour")
    assert [r["bucket"] for r in t["rows"]] == ["10:00"] and t["skipped"] == 1


def test_unscored_records_join_no_row_and_no_skip_count():
    t = buckets.table([_r(None, "2026-10-05")], buckets.weekday, "weekday")
    assert t == {"rows": [], "skipped": 0}


@pytest.fixture
def calendar(monkeypatch):
    days = {
        "2026-10-14": {
            "known": True,
            "events": [{"label": "CPI", "major": True}, {"label": "Claims", "major": False}],
        },
        "2026-10-15": {"known": True, "events": [{"label": "Claims", "major": False}]},
        "2026-10-16": {"known": False, "events": []},
    }
    monkeypatch.setattr(buckets._events, "day_events", lambda day: days[day.isoformat()])
    buckets._event_bucket.cache_clear()
    yield
    buckets._event_bucket.cache_clear()


def test_event_buckets_name_major_releases_and_never_count_an_unknown_day_as_quiet(calendar):
    records = [_r(1.0, "2026-10-14"), _r(2.0, "2026-10-15"), _r(3.0, "2026-10-16")]
    rows = buckets.table(records, buckets.event, "event")["rows"]
    assert [(r["bucket"], r["net"]) for r in rows] == [("none", 2.0), ("CPI", 1.0), ("unknown", 3.0)]


def test_arm_reading_carries_every_breakdown(calendar):
    out = analysis.arm_reading([_r(1.0, "2026-10-14", "2026-10-14T10:00:00-04:00")])["buckets"]
    assert out["min_sessions"] == buckets.MIN_SESSIONS
    assert set(out) == {"min_sessions", "weekday", "hour", "month", "event"}
