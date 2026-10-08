"""The IV-rank fetch's calendar fields: the dates the planned earnings filter reads.

`scripts/fetch_iv_rank.py` keeps the broker's expected report date and ex-dividend date from the raw
market-metrics item, because the SDK's model drops the first. Each test feeds one shape the broker
actually sends (seen 2026-10-07) and checks what is recorded.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_iv_rank.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_iv_rank", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fir = _module()


def test_a_confirmed_report_date_and_ex_date_are_kept():
    raw = {
        "earnings": {"expected-report-date": "2026-10-29", "estimated": False},
        "dividend-ex-date": "2026-08-10",
    }
    assert fir.calendar_fields(raw) == {
        "earnings_date": "2026-10-29",
        "earnings_estimated": False,
        "ex_dividend_date": "2026-08-10",
    }


def test_an_estimated_date_says_so():
    raw = {"earnings": {"expected-report-date": "2026-11-18", "estimated": True}}
    assert fir.calendar_fields(raw)["earnings_estimated"] is True


def test_the_brokers_1970_placeholder_is_no_date():
    """A name that pays no dividend comes back as 1970-01-01; read as a date it sits in every span."""
    assert fir.calendar_fields({"dividend-ex-date": "1970-01-01"})["ex_dividend_date"] is None


def test_a_name_with_no_earnings_block_records_nothing_rather_than_a_guess():
    assert fir.calendar_fields({}) == {
        "earnings_date": None,
        "earnings_estimated": None,
        "ex_dividend_date": None,
    }
