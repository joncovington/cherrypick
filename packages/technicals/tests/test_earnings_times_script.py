"""The Nasdaq earnings-times fetch (scripts/fetch_earnings_times.py): what it keeps from a response.

Each case is the shape the page sent on 2026-10-17.
"""

from __future__ import annotations

import importlib.util
from datetime import date
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


def test_the_dates_asked_for_are_weekdays_only():
    got = fet.weekdays(date(2026, 10, 17), 4)  # a Saturday
    assert got == [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
