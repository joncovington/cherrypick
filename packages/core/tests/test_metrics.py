"""cherrypick.core.metrics — the shared calibration bundle, and the hardened promotion
checks that consume it. Unknowns must read as None/fail-closed, never as a passing zero."""

import pytest

from cherrypick.core import metrics
from cherrypick.core.profiles import qualify_readings


def _rec(net, capital=None, session="2026-07-21", slippage=None):
    return {"net_pnl": net, "capital": capital, "session": session, "slippage": slippage}


# --- return on capital ---------------------------------------------------------


def test_roc_weighs_wide_and_narrow_structures_differently():
    # Same $15 net; 10x the capital -> 1/10th the RoC. The 2-wide == 10-wide bug, stated.
    narrow = metrics.return_on_capital([_rec(15.0, capital=200.0)])
    wide = metrics.return_on_capital([_rec(15.0, capital=2000.0)])
    assert narrow == pytest.approx(0.075)
    assert wide == pytest.approx(0.0075)


def test_roc_unknown_capital_is_none_not_free():
    assert metrics.return_on_capital([_rec(15.0)]) is None
    # Mixed: only the records with capital contribute (their nets over their capital).
    mixed = metrics.return_on_capital([_rec(15.0, capital=300.0), _rec(99.0)])
    assert mixed == pytest.approx(0.05)


# --- sharpe / drawdown ---------------------------------------------------------


def test_sharpe_refuses_thin_or_flat_series():
    assert metrics.sharpe([5.0]) is None
    assert metrics.sharpe([5.0, 5.0, 5.0]) is None  # zero variance is not infinite quality


def test_sharpe_orders_a_volatile_series_below_a_steady_one():
    steady = metrics.sharpe([10.0, 12.0, 11.0, 10.5])
    volatile = metrics.sharpe([50.0, -40.0, 45.0, -11.5])  # same total
    assert steady is not None and volatile is not None
    assert steady > volatile


def test_max_drawdown_is_peak_to_trough_of_the_path():
    assert metrics.max_drawdown([10, -4, -6, 8]) == 10.0
    assert metrics.max_drawdown([1, 2, 3]) == 0.0
    assert metrics.max_drawdown([]) == 0.0


def test_sample_progress_tracks_the_next_unmet_target():
    p = metrics.sample_progress(15)
    assert p["next_target"] == 30 and p["progress"] == 0.5
    assert metrics.sample_progress(150)["progress"] == 1.0


# --- phase-1 expansion (docs/metrics-plan.md): edge, risk-adjusted, tail, duration ----


def test_expectancy_is_mean_per_trade_and_refuses_empty():
    assert metrics.expectancy([20.0, -8.0, 12.0]) == 8.0
    assert metrics.expectancy([]) is None


def test_profit_factor_refuses_one_sided_books():
    assert metrics.profit_factor([30.0, -10.0, 10.0]) == 4.0
    # No losses yet -> UNDEFINED, not infinite; no wins -> unmeasurable, not zero-quality.
    assert metrics.profit_factor([30.0, 10.0]) is None
    assert metrics.profit_factor([-30.0, -10.0]) is None
    assert metrics.profit_factor([]) is None


def test_sortino_punishes_downside_not_upside_volatility():
    # Same mean and same losses; wild UPSIDE swings must not lower the score the way sharpe's
    # symmetric stdev does — that asymmetry is the reason sortino exists.
    steady = [10.0, -5.0, 10.0, -5.0, 10.0]
    upside_wild = [1.0, -5.0, 30.0, -5.0, -1.0]
    s1, s2 = metrics.sortino(steady), metrics.sortino(upside_wild)
    assert s1 is not None and s2 is not None
    sh1, sh2 = metrics.sharpe(steady), metrics.sharpe(upside_wild)
    assert (s2 / s1) > (sh2 / sh1)  # sortino degrades less for upside-only wildness


def test_sortino_refuses_below_two_losses():
    assert metrics.sortino([10.0, 12.0, 11.0]) is None  # lossless: undefined, not infinite
    assert metrics.sortino([10.0, -2.0, 11.0]) is None  # one loss says nothing about shape
    assert metrics.sortino([10.0, -2.0, -3.0, 11.0]) is not None


def test_sqn_scales_with_sample_size_at_the_same_edge():
    small = metrics.sqn([10.0, -5.0, 12.0, -4.0])
    large = metrics.sqn([10.0, -5.0, 12.0, -4.0] * 4)
    assert small is not None and large is not None
    # 4x the sample at the same edge -> ~2x the score (sqrt(4); slightly over, since the
    # sample stdev's n-1 denominator shrinks it as n grows).
    assert small * 1.9 < large < small * 2.4
    assert metrics.sqn([5.0]) is None
    assert metrics.sqn([5.0, 5.0]) is None  # zero variance is not infinite quality


def test_session_nets_pool_by_session_in_order():
    records = [
        _rec(20.0, session="2026-07-22"),
        _rec(-8.0, session="2026-07-21"),
        _rec(5.0, session="2026-07-22"),
        {"net_pnl": 99.0, "session": None},  # unknown day cannot be pooled anywhere
    ]
    assert metrics.session_nets(records) == [-8.0, 25.0]


def test_worst_session_is_the_worst_day_not_the_worst_trade():
    records = [
        _rec(-50.0, session="2026-07-21"),
        _rec(60.0, session="2026-07-21"),  # day nets +10
        _rec(-6.0, session="2026-07-22"),  # day nets -6: the worst DAY
    ]
    assert metrics.worst_session(records) == {"session": "2026-07-22", "net": -6.0}
    assert metrics.worst_session([{"net_pnl": 5.0, "session": None}]) is None


def test_cvar_refuses_below_the_minimum_session_count():
    """A CVaR over six sessions reads as a risk number and is not one — the stamped contract
    (worst 10%, >= 20 sessions) refuses rather than fabricating."""
    nineteen = [10.0] * 18 + [-100.0]
    assert metrics.cvar(nineteen) is None
    twenty = nineteen + [10.0]
    assert metrics.cvar(twenty) == -45.0  # worst 10% of 20 = worst 2: (-100 + 10)/2


def test_cvar_is_the_mean_of_the_tail_not_the_single_worst():
    values = [-100.0, -50.0] + [10.0] * 28  # 30 sessions -> worst 3
    assert metrics.cvar(values) == pytest.approx(round((-100.0 - 50.0 + 10.0) / 3, 2))


def test_drawdown_span_reports_duration_and_keeps_an_open_drawdown_open():
    # Peak after 10, then three sessions spent below it; the recovering session itself sits
    # at a new peak and is not part of the stretch.
    closed = metrics.drawdown_span([10.0, -4.0, -2.0, 1.0, 6.0])
    assert closed == {"longest": 3, "open": 0}
    # Still below peak at the end: the live bleed is reported open, never folded into history.
    bleeding = metrics.drawdown_span([10.0, -4.0, -2.0])
    assert bleeding == {"longest": 2, "open": 2}
    assert metrics.drawdown_span([]) == {"longest": 0, "open": 0}


def test_calibration_reading_carries_the_expansion_report_only():
    records = [
        _rec(20.0, capital=500.0, session="2026-07-21", slippage=4.0),
        _rec(-8.0, capital=500.0, session="2026-07-22", slippage=4.0),
        _rec(-2.0, capital=500.0, session="2026-07-23", slippage=4.0),
        _rec(12.0, capital=500.0, session="2026-07-24", slippage=4.0),
    ]
    r = metrics.calibration_reading(records)
    assert r["expectancy"] == 5.5
    assert r["profit_factor"] == 3.2
    assert r["sortino"] is not None and r["sqn"] is not None
    assert r["worst_session"] == {"session": "2026-07-22", "net": -8.0}
    assert r["cvar"] is None and r["cvar_min_sessions"] == 20  # 4 sessions: refused, stated
    assert r["drawdown_span"] == {"longest": 2, "open": 0}
    # Report-only: the qualification rule set is untouched by the new keys.
    out = qualify_readings({"a": r})
    assert set(out["a"]["checks"]) == {"sample", "win_rate", "days"}


# --- calibration_reading -------------------------------------------------------


def test_calibration_reading_bundles_the_promotion_evidence():
    records = [
        _rec(20.0, capital=500.0, session="2026-07-21", slippage=4.0),
        _rec(-8.0, capital=500.0, session="2026-07-22", slippage=4.0),
        _rec(12.0, capital=500.0, session="2026-07-23", slippage=4.0),
    ]
    r = metrics.calibration_reading(records)
    assert r["sample"] == 3 and r["days"] == 3
    assert r["win_rate"] == pytest.approx(round(2 / 3, 4))
    assert r["net_pnl"] == 24.0
    assert r["net_pnl_2x_slippage"] == 12.0
    assert r["slippage_coverage"] == 3
    assert r["return_on_capital"] == pytest.approx(24.0 / 1500.0)
    assert r["capital_coverage"] == 3
    assert r["max_drawdown"] == 8.0
    assert r["sample_progress"]["next_target"] == 30


def test_calibration_reading_orders_by_session_for_the_drawdown_path():
    # Same records, reversed insertion: drawdown must follow session order, not list order.
    records = [_rec(-8.0, session="2026-07-22"), _rec(20.0, session="2026-07-21")]
    assert metrics.calibration_reading(records)["max_drawdown"] == 8.0


def test_net_pnl_2x_slippage_is_none_when_slippage_was_never_recorded():
    """Found live 2026-08-14: several arms reported net_pnl_2x_slippage identical to net_pnl
    because every record's slippage was None -- sum-of-nothing read as zero cost, not as
    unmeasured. Zero coverage must render None, not a silently-copied net_pnl."""
    records = [_rec(20.0, session="2026-07-21"), _rec(-8.0, session="2026-07-22")]
    r = metrics.calibration_reading(records)
    assert r["slippage_coverage"] == 0
    assert r["net_pnl"] == 12.0
    assert r["net_pnl_2x_slippage"] is None


def test_net_pnl_2x_slippage_is_none_under_partial_coverage():
    records = [
        _rec(20.0, session="2026-07-21", slippage=4.0),
        _rec(-8.0, session="2026-07-22", slippage=None),
    ]
    r = metrics.calibration_reading(records)
    assert r["slippage_coverage"] == 1
    assert r["net_pnl_2x_slippage"] is None


def test_net_pnl_2x_slippage_is_none_when_full_coverage_sums_to_exactly_zero():
    """Same defect, second shape: full coverage but every recorded value is 0.0. A real
    per-trade slippage model essentially never nets to precisely zero across multiple trades,
    so this reads as never-wired-up rather than measured-and-happened-to-be-zero."""
    records = [
        _rec(20.0, session="2026-07-21", slippage=0.0),
        _rec(-8.0, session="2026-07-22", slippage=0.0),
    ]
    r = metrics.calibration_reading(records)
    assert r["slippage_coverage"] == 2
    assert r["net_pnl_2x_slippage"] is None


def test_net_pnl_2x_slippage_is_zero_for_an_empty_group():
    """n=0 is a true zero (no trades, no cost), not a stand-in for missing data."""
    assert metrics.calibration_reading([])["net_pnl_2x_slippage"] == 0.0


def test_return_on_capital_is_none_under_partial_capital_coverage():
    """Previously computed silently from the subset of records that carried capital -- honest
    per-record, but the bundled reading gave no signal that the average excluded some trades.
    calibration_reading now requires full coverage before exposing return_on_capital at all."""
    records = [
        _rec(15.0, capital=300.0, session="2026-07-21"),
        _rec(9.0, capital=None, session="2026-07-22"),
    ]
    r = metrics.calibration_reading(records)
    assert r["capital_coverage"] == 1
    assert r["return_on_capital"] is None
    # the raw function still computes the subset average -- only the bundled reading gates it
    assert metrics.return_on_capital(records) == pytest.approx(0.05)


# --- hardened qualification checks ---------------------------------------------
# Ported from recommend_promotion to qualify_readings 2026-08-01 (champion/challenger revision):
# these checks test the shared _qualify_one logic, which is exactly what qualify_readings exposes
# directly -- no champion/challenger machinery needed to exercise sample/win_rate/days/RoC/slippage
# threshold behavior.


def _good_reading(**over):
    base = {
        "sample": 25,
        "win_rate": 0.7,
        "days": 20,
        "net_pnl": 300.0,
        "net_pnl_2x_slippage": 120.0,
        "slippage_coverage": 25,
        "return_on_capital": 0.05,
    }
    base.update(over)
    return base


def test_default_rule_is_unchanged_by_the_new_keys():
    out = qualify_readings({"conservative": _good_reading()})
    assert out["conservative"]["qualified"] is True
    assert set(out["conservative"]["checks"]) == {"sample", "win_rate", "days"}


def test_min_net_pnl_refuses_a_book_that_wins_often_and_loses_money():
    """The case the base three cannot see, and the reason this check exists (flies' control, gex
    and time_window all read qualified=true on 2026-08-14 while lifetime-negative). A 70%-win
    reading that nets -1,698 must fail on money alone."""
    rule = {"min_net_pnl": 0.0}
    losing = qualify_readings({"control": _good_reading(net_pnl=-1698.61)}, rule=rule)
    assert losing["control"]["qualified"] is False
    assert losing["control"]["checks"]["net_pnl"]["pass"] is False
    # ...and the win rate, which is what let it through before, still passes on its own.
    assert losing["control"]["checks"]["win_rate"]["pass"] is True
    assert qualify_readings({"control": _good_reading()}, rule=rule)["control"]["qualified"] is True


def test_min_net_pnl_is_a_threshold_not_a_hardcoded_sign_test():
    """0.0 admits an exactly-flat book (_check is >=); a module wanting a real margin sets one."""
    flat = {"control": _good_reading(net_pnl=0.0)}
    assert qualify_readings(flat, rule={"min_net_pnl": 0.0})["control"]["qualified"] is True
    assert qualify_readings(flat, rule={"min_net_pnl": 500.0})["control"]["qualified"] is False


def test_min_return_on_capital_gates_when_enabled():
    rule = {"min_return_on_capital": 0.10}
    out = qualify_readings({"conservative": _good_reading()}, rule=rule)
    assert out["conservative"]["qualified"] is False
    assert out["conservative"]["checks"]["return_on_capital"]["pass"] is False
    out2 = qualify_readings({"conservative": _good_reading(return_on_capital=0.12)}, rule=rule)
    assert out2["conservative"]["qualified"] is True


def test_unknown_capital_fails_the_roc_check():
    rule = {"min_return_on_capital": 0.01}
    out = qualify_readings({"conservative": _good_reading(return_on_capital=None)}, rule=rule)
    assert out["conservative"]["checks"]["return_on_capital"]["pass"] is False


def test_slippage_survival_requires_positive_stressed_net():
    rule = {"require_slippage_survival": True}
    dead = qualify_readings({"conservative": _good_reading(net_pnl_2x_slippage=-5.0)}, rule=rule)
    assert dead["conservative"]["qualified"] is False
    alive = qualify_readings({"conservative": _good_reading()}, rule=rule)
    assert alive["conservative"]["qualified"] is True


def test_slippage_survival_requires_full_coverage():
    """A stress test over part of the evidence certifies nothing: 20/25 rows carrying
    slippage must FAIL even with a positive stressed net."""
    rule = {"require_slippage_survival": True}
    partial = qualify_readings({"conservative": _good_reading(slippage_coverage=20)}, rule=rule)
    assert partial["conservative"]["checks"]["slippage_survival"]["pass"] is False
