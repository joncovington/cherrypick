"""The time-spread scanner's term-structure read and prefilter (made-up names and numbers)."""

from datetime import date

from cherrypick.manage import timespreads as ts

SESSION = date(2026, 11, 6)  # a Friday


def metrics(symbol="XYZ", front_iv=0.60, back_iv=0.45, ivr=0.50, cap=50e9, earnings=None, extra=()):
    return {
        "symbol": symbol,
        "iv_rank": ivr,
        "iv_30d": 50.0,
        "hv_30d": 48.0,
        "market_cap": cap,
        "earnings_date": earnings,
        "expiries": [
            ("2026-11-13", front_iv * 1.05),  # 7 days
            ("2026-11-20", front_iv),  # 14 days: as far from the 10.5-day middle as Nov 13
            ("2026-12-18", back_iv),  # 42 days, the monthly
            ("2026-12-24", back_iv * 0.98),  # 48 days, a weekly
            *extra,
        ],
    }


def test_the_monthly_is_the_back_expiry_even_when_a_weekly_sits_nearer_the_middle():
    row = ts.term_row(metrics(), SESSION)
    assert row["back"]["expiration"] == "2026-12-18" and row["back"]["dte"] == 42
    # 7 and 14 days are equally far from the 10.5-day middle: the earlier one is taken.
    assert row["front"]["expiration"] == "2026-11-13"
    assert row["ratio"] == round(0.63 / 0.45, 4)
    assert row["passes"] and row["why"] == []


def test_a_flat_term_structure_fails_the_ratio():
    row = ts.term_row(metrics(front_iv=0.46, back_iv=0.45), SESSION)
    assert not row["passes"] and row["why"] == ["front/back ratio under 1.15"]


def test_earnings_before_the_back_expiry_fail_and_after_it_pass():
    inside = ts.term_row(metrics(earnings="2026-12-01"), SESSION)
    assert "earnings before the back expiry" in inside["why"]
    after = ts.term_row(metrics(earnings="2027-01-20"), SESSION)
    passed = ts.term_row(metrics(earnings="2026-11-04"), SESSION)  # already reported
    assert after["passes"] and passed["passes"]


def test_iv_rank_and_market_cap_floors_and_a_fund_is_kept():
    low = ts.term_row(metrics(ivr=0.20), SESSION)
    small = ts.term_row(metrics(cap=10e9), SESSION)
    fund = ts.term_row(metrics(cap=None), SESSION)
    assert low["why"] == ["IV rank under 30%"]
    assert small["why"] == ["market cap under $20B"]
    assert fund["passes"] and fund["fund"]


def test_no_back_expiry_in_range_is_said():
    m = metrics()
    m["expiries"] = [("2026-11-13", 0.6)]
    row = ts.term_row(m, SESSION)
    assert row["back"] is None and row["ratio"] is None
    assert "no front or back expiry with an IV" in row["why"]


def test_the_shortlist_keeps_the_best_ratios_and_only_passing_names():
    rows = [
        ts.term_row(metrics("AAA", front_iv=0.70), SESSION),
        ts.term_row(metrics("BBB", front_iv=0.60), SESSION),
        ts.term_row(metrics("CCC", front_iv=0.80, ivr=0.10), SESSION),  # fails the IV rank
        ts.term_row(metrics("DDD", front_iv=0.70), SESSION),
    ]
    assert ts.shortlist(rows, {**ts.RULE, "shortlist": 2}) == ["AAA", "DDD"]


def test_the_monthly_test_moves_a_holiday_friday_to_thursday():
    assert ts.is_monthly(date(2026, 12, 18))
    assert not ts.is_monthly(date(2026, 12, 24))
    # April 2025's third Friday was Good Friday (no trading): its monthly was the Thursday before.
    assert ts.is_monthly(date(2025, 4, 17)) and not ts.is_monthly(date(2025, 4, 18))
