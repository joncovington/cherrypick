"""cherrypick.core.metrics — the shared calibration bundle, and the hardened promotion
checks that consume it. Unknowns must read as None/fail-closed, never as a passing zero."""

import pytest

from cherrypick.core import metrics
from cherrypick.core.profiles import recommend_promotion


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


# --- hardened promotion checks -------------------------------------------------

_LADDER = ["conservative", "moderate"]


def _good_reading(**over):
    base = {"sample": 25, "win_rate": 0.7, "days": 20, "net_pnl": 300.0,
            "net_pnl_2x_slippage": 120.0, "slippage_coverage": 25,
            "return_on_capital": 0.05}
    base.update(over)
    return base


def test_default_rule_is_unchanged_by_the_new_keys():
    rec = recommend_promotion(_good_reading(), "conservative", _LADDER)
    assert rec["eligible"] is True
    assert set(rec["checks"]) == {"sample", "win_rate", "days"}


def test_min_return_on_capital_gates_when_enabled():
    rule = {"min_return_on_capital": 0.10}
    rec = recommend_promotion(_good_reading(), "conservative", _LADDER, rule=rule)
    assert rec["eligible"] is False
    assert rec["checks"]["return_on_capital"]["pass"] is False
    rec2 = recommend_promotion(_good_reading(return_on_capital=0.12), "conservative",
                               _LADDER, rule=rule)
    assert rec2["eligible"] is True


def test_unknown_capital_fails_the_roc_check():
    rule = {"min_return_on_capital": 0.01}
    rec = recommend_promotion(_good_reading(return_on_capital=None), "conservative",
                              _LADDER, rule=rule)
    assert rec["checks"]["return_on_capital"]["pass"] is False


def test_slippage_survival_requires_positive_stressed_net():
    rule = {"require_slippage_survival": True}
    dead = recommend_promotion(_good_reading(net_pnl_2x_slippage=-5.0), "conservative",
                               _LADDER, rule=rule)
    assert dead["eligible"] is False
    alive = recommend_promotion(_good_reading(), "conservative", _LADDER, rule=rule)
    assert alive["eligible"] is True


def test_slippage_survival_requires_full_coverage():
    """A stress test over part of the evidence certifies nothing: 20/25 rows carrying
    slippage must FAIL even with a positive stressed net."""
    rule = {"require_slippage_survival": True}
    partial = recommend_promotion(_good_reading(slippage_coverage=20), "conservative",
                                  _LADDER, rule=rule)
    assert partial["checks"]["slippage_survival"]["pass"] is False
