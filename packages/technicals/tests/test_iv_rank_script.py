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
        "earnings_time": None,
        "ex_dividend_date": "2026-08-10",
    }


def test_the_time_of_day_is_kept_as_sent():
    """META on 2026-10-16: the block carried it; CB's, the same day, did not."""
    meta = {"earnings": {"expected-report-date": "2026-10-21", "estimated": False, "time-of-day": "AMC"}}
    pm = {"earnings": {"expected-report-date": "2026-10-21", "estimated": False, "time-of-day": "BTO"}}
    cb = {"earnings": {"expected-report-date": "2026-10-20", "estimated": False}}
    assert fir.calendar_fields(meta)["earnings_time"] == "AMC"
    assert fir.calendar_fields(pm)["earnings_time"] == "BTO"
    assert fir.calendar_fields(cb)["earnings_time"] is None


def test_an_estimated_date_says_so():
    raw = {"earnings": {"expected-report-date": "2026-11-18", "estimated": True}}
    assert fir.calendar_fields(raw)["earnings_estimated"] is True


def test_the_brokers_1970_placeholder_is_no_date():
    """A name that pays no dividend comes back as 1970-01-01; read as a date it sits in every span."""
    assert fir.calendar_fields({"dividend-ex-date": "1970-01-01"})["ex_dividend_date"] is None


def test_expiry_ivs_keep_the_coming_ten_weeks_and_drop_the_expired_one():
    """CB on 2026-10-16, as sent: the expiry dated that day still listed, at 3,745%."""
    from datetime import date

    raw = {
        "option-expiration-implied-volatilities": [
            {"expiration-date": "2026-10-16", "implied-volatility": "37.456593605"},
            {"expiration-date": "2026-11-20", "implied-volatility": "0.228214815"},
            {"expiration-date": "2026-12-18", "implied-volatility": "0.214303128"},
            {"expiration-date": "2027-01-15", "implied-volatility": "0.22"},
            {"expiration-date": "2026-11-27", "implied-volatility": None},
        ]
    }
    assert fir.expiry_ivs(raw, date(2026, 10, 16)) == {"2026-11-20": 0.228215, "2026-12-18": 0.214303}


def test_expiry_ivs_of_a_row_without_the_list_is_empty():
    from datetime import date

    assert fir.expiry_ivs({}, date(2026, 10, 16)) == {}


def test_calendar_names_are_optionable_stocks_rated_very_or_somewhat_liquid():
    rows = [
        {"symbol": "CB", "type": "Stock", "hasOptions": True, "liquidityRank": 1},
        {"symbol": "TSM", "type": "Depository Receipt", "hasOptions": True, "liquidityRank": 2},
        {"symbol": "THIN", "type": "Stock", "hasOptions": True, "liquidityRank": 3},
        {"symbol": "NOOPT", "type": "Stock", "hasOptions": False, "liquidityRank": 1},
        {"symbol": "SPY", "type": "Exchange Traded Fund", "hasOptions": True, "liquidityRank": 1},
        {"symbol": "UNRATED", "type": "Stock", "hasOptions": True, "liquidityRank": None},
    ]
    assert fir.calendar_names(rows) == ["CB", "TSM"]


def test_a_name_with_no_earnings_block_records_nothing_rather_than_a_guess():
    assert fir.calendar_fields({}) == {
        "earnings_date": None,
        "earnings_estimated": None,
        "earnings_time": None,
        "ex_dividend_date": None,
    }
