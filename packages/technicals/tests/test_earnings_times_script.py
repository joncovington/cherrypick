"""The Nasdaq earnings-times fetch (scripts/fetch_earnings_times.py): what it keeps from a response.

Each case is the shape the page sent on 2026-10-17.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_earnings_times.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_earnings_times", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fet = _module()


def test_the_three_time_values_map_to_bmo_amc_and_none():
    body = {
        "data": {
            "rows": [
                {"symbol": "PG", "time": "time-pre-market"},
                {"symbol": "WDC", "time": "time-after-hours"},
                {"symbol": "CB", "time": "time-not-supplied"},
                {"symbol": "BRK/B", "time": "time-pre-market"},
                {"symbol": "", "time": "time-pre-market"},
            ]
        }
    }
    assert fet.parse(body) == {"PG": "bmo", "WDC": "amc", "CB": None, "BRK.B": "bmo"}


def test_an_empty_or_odd_response_is_no_rows():
    assert fet.parse({}) == {}
    assert fet.parse({"data": None}) == {}
    assert fet.parse({"data": {"rows": None}}) == {}


def test_yahoo_rows_are_kept_on_their_new_york_date_for_the_dates_asked_for():
    utc = UTC
    rows = [
        ("WMT", datetime(2026, 11, 19, 13, 30, tzinfo=utc), "BMO"),  # 08:30 ET
        ("NVDA", datetime(2026, 11, 19, 21, 0, tzinfo=utc), "AMC"),  # 16:00 ET
        ("BRK-B", datetime(2026, 11, 19, 13, 30, tzinfo=utc), "BMO"),
        ("MUFG", datetime(2026, 11, 19, 21, 0, tzinfo=utc), "TNS"),  # not supplied
        ("LATE", datetime(2026, 11, 20, 2, 0, tzinfo=utc), "AMC"),  # 21:00 ET on the 19th
        ("OFF", datetime(2026, 11, 25, 13, 30, tzinfo=utc), "BMO"),  # a date not asked for
        ("NAIVE", datetime(2026, 11, 19, 13, 30), "BMO"),  # no zone: dropped
        ("NONE", None, "BMO"),
        ("", datetime(2026, 11, 19, 13, 30, tzinfo=utc), "BMO"),
    ]
    got = fet.parse_yahoo(rows, {"2026-11-19", "2026-11-20"})
    assert got == {
        "2026-11-19": {"WMT": "bmo", "NVDA": "amc", "BRK.B": "bmo", "MUFG": None, "LATE": "amc"},
        "2026-11-20": {},  # asked for, answered, nothing listed
    }


def test_a_timed_yahoo_row_beats_an_untimed_one_for_the_same_name():
    at = datetime(2026, 11, 18, 13, 30, tzinfo=UTC)
    assert fet.parse_yahoo([("X", at, "BMO"), ("X", at, "TNS")], {"2026-11-18"}) == {
        "2026-11-18": {"X": "bmo"}
    }
    assert fet.parse_yahoo([("X", at, "TNS"), ("X", at, "BMO")], {"2026-11-18"}) == {
        "2026-11-18": {"X": "bmo"}
    }


def test_the_dates_asked_for_are_weekdays_only():
    got = fet.weekdays(date(2026, 10, 17), 4)  # a Saturday
    assert got == [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
