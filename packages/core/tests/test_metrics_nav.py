"""Daily-series readings (`cherrypick.core.metrics.nav`): the arithmetic, the refusals, the basis."""

import math

import pytest

from cherrypick.core.metrics import nav


def _days(values, start=(2026, 1, 2)):
    from datetime import date, timedelta

    d = date(*start)
    out = []
    for v in values:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append((d.isoformat(), v))
        d += timedelta(days=1)
    return out


def test_drawdown_depth_span_and_mar():
    r = nav.nav_reading(_days([100, 120, 90, 95, 130, 125]))
    assert r["basis"] == "daily" and r["days"] == 5
    assert r["max_drawdown"] == pytest.approx(90 / 120 - 1)
    assert r["drawdown_span"] == {"longest": 2, "open": 1}  # 90, 95 below 120; 125 below 130
    assert r["worst_day"] == pytest.approx(-0.25)
    assert r["mar"] == pytest.approx(r["cagr"] / 0.25, rel=1e-3)


def test_ratios_refuse_below_the_shared_floor():
    r = nav.nav_reading(_days([100, 101, 99, 102]))
    assert r["sortino"] is None and r["psr"] is None and r["min_track_record_days"] is None
    assert r["cvar"] is None and r["skew"] is None


def test_a_year_of_steady_growth_annualises_to_its_rate():
    from datetime import date, timedelta

    start = date(2025, 1, 1)
    navs = [((start + timedelta(days=i)).isoformat(), 1.10 ** (i / 365.25)) for i in range(0, 366)]
    r = nav.nav_reading(navs)
    assert r["cagr"] == pytest.approx(0.10, abs=1e-3)
    assert r["max_drawdown"] == 0.0 and r["mar"] is None  # no drawdown, no ratio -- never infinity


def test_min_track_record_shortens_as_the_edge_grows_and_refuses_without_one():
    noise = [0.01 * math.sin(i * 1.7) for i in range(200)]
    weak = nav.min_track_record([0.0005 + x for x in noise])
    strong = nav.min_track_record([0.003 + x for x in noise])
    assert weak is not None and strong is not None and strong < weak
    assert nav.min_track_record([-0.001 + x for x in noise]) is None


def test_monthly_returns_chain_month_end_to_month_end():
    m = nav.monthly_returns([("2026-01-02", 100), ("2026-01-30", 110), ("2026-02-27", 99)])
    assert m == {"2026-01": pytest.approx(0.10), "2026-02": pytest.approx(-0.10)}


def test_a_wiped_out_series_stops_its_returns_rather_than_divide_by_zero():
    assert nav.daily_returns([("a", 100), ("b", 0), ("c", 50)]) == [-1.0]


def test_equity_reading_is_in_dollars_and_does_not_compound():
    e = nav.equity_reading(_days([0, 50, -30, 20, 10]))
    assert e["net"] == 10 and e["days"] == 5
    assert e["max_drawdown"] == -80  # 50 down to -30
    assert e["worst_day"] == -80
    assert e["drawdown_span"] == {"longest": 3, "open": 3}
    assert "cagr" not in e and "mar" not in e


def test_equity_counts_the_first_sessions_pnl_from_zero():
    e = nav.equity_reading(_days([1.02, 1.02, 1.02]))  # a trade that opened and closed on day one
    assert e["net"] == 1.02 and e["best_day"] == 1.02
