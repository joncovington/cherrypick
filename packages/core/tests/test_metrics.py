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
