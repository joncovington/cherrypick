from pathlib import Path

import pytest

import strategy_metrics as sm


def test_db_path_for_mode_paper():
    assert sm.db_path_for_mode("paper") == sm.PAPER_DB_PATH
    assert sm.PAPER_DB_PATH.name == "paper_trades.db"


def test_db_path_for_mode_live():
    assert sm.db_path_for_mode("live") == sm.LIVE_DB_PATH
    assert sm.LIVE_DB_PATH.name == "earnings_trades.db"


def test_db_path_for_mode_override_wins_over_mode():
    override = "/tmp/custom.db"
    assert sm.db_path_for_mode("live", override) == Path(override)
    assert sm.db_path_for_mode("paper", override) == Path(override)


def test_db_path_for_mode_unknown_raises():
    with pytest.raises(ValueError):
        sm.db_path_for_mode("bogus")


def test_default_db_path_is_paper():
    # Existing importers (that don't pass --mode) must keep reading the paper DB.
    assert sm.DB_PATH == sm.PAPER_DB_PATH


def _trade(pnl, entry_cost=0.0, exit_cost=0.0, opened_at=0, closed_at=None, entry_context=None,
           entry_iv=None, exit_iv=None):
    return {
        "pnl": pnl, "entry_cost": entry_cost, "exit_cost": exit_cost,
        "opened_at": opened_at, "closed_at": closed_at if closed_at is not None else opened_at + 3600,
        "entry_context": entry_context, "entry_iv": entry_iv, "exit_iv": exit_iv,
    }


def test_net_pnl_subtracts_both_costs():
    t = _trade(pnl=100.0, entry_cost=5.0, exit_cost=3.0)
    assert sm.net_pnl(t) == pytest.approx(92.0)


def test_win_rate_counts_positive_net_pnl():
    trades = [_trade(100), _trade(-50), _trade(20, entry_cost=25)]  # third: net -5, a loss after costs
    assert sm.win_rate(trades) == pytest.approx(1 / 3)


def test_win_rate_empty_is_none():
    assert sm.win_rate([]) is None


def test_profit_factor_basic():
    trades = [_trade(100), _trade(-50), _trade(-25)]
    assert sm.profit_factor(trades) == pytest.approx(100 / 75)


def test_profit_factor_no_losses_is_infinite():
    trades = [_trade(100), _trade(50)]
    assert sm.profit_factor(trades) == float("inf")


def test_profit_factor_no_trades_is_none():
    trades = [_trade(0)]
    assert sm.profit_factor(trades) is None


def test_expectancy_is_mean_net_pnl():
    trades = [_trade(100), _trade(-40), _trade(10)]
    assert sm.expectancy(trades) == pytest.approx((100 - 40 + 10) / 3)


def test_avg_cost_per_trade():
    trades = [_trade(0, entry_cost=10, exit_cost=5), _trade(0, entry_cost=20, exit_cost=0)]
    assert sm.avg_cost_per_trade(trades) == pytest.approx((15 + 20) / 2)


def test_sharpe_requires_at_least_two_trades():
    assert sm.sharpe([_trade(100)]) is None


def test_sharpe_zero_variance_is_none():
    trades = [_trade(50), _trade(50), _trade(50)]
    assert sm.sharpe(trades) is None


def test_sharpe_computes_mean_over_stdev():
    trades = [_trade(100), _trade(200), _trade(300)]
    mean = 200.0
    stdev = (sum((p - mean) ** 2 for p in (100, 200, 300)) / 2) ** 0.5
    assert sm.sharpe(trades) == pytest.approx(mean / stdev)


def test_equity_curve_orders_by_closed_at_and_accumulates():
    trades = [
        _trade(50, opened_at=0, closed_at=200),
        _trade(-20, opened_at=0, closed_at=100),
    ]
    curve = sm.equity_curve(trades)
    assert curve == [(100, -20), (200, 30)]


def test_max_drawdown_finds_peak_to_trough():
    trades = [
        _trade(100, closed_at=1), _trade(-150, closed_at=2), _trade(50, closed_at=3),
    ]
    # equity: 100, -50, 0 -> peak 100, trough -50 -> drawdown 150
    result = sm.max_drawdown(trades)
    assert result["absolute"] == pytest.approx(150.0)


def test_max_drawdown_pct_uses_capital_basis_when_given():
    trades = [_trade(100, closed_at=1), _trade(-150, closed_at=2)]
    result = sm.max_drawdown(trades, capital_basis=1000.0)
    assert result["pct"] == pytest.approx(150.0 / 1000.0)


def test_max_drawdown_empty_trades():
    assert sm.max_drawdown([]) == {"absolute": 0.0, "pct": 0.0}


def test_sample_progress_targets():
    trades = [_trade(0) for _ in range(30)]
    progress = sm.sample_progress(trades)
    assert progress["count"] == 30
    assert progress["directional_met"] is True
    assert progress["significant_met"] is False


def test_regime_buckets_groups_by_iv_and_dispersion_band():
    trades = [
        _trade(0, entry_context={"iv_rv_ratio": 1.2, "dispersion": 0.05}),
        _trade(0, entry_context={"iv_rv_ratio": 1.3, "dispersion": 0.06}),
        _trade(0, entry_context={"iv_rv_ratio": 0.5, "dispersion": 0.25}),
    ]
    buckets = sm.regime_buckets(trades)
    assert buckets["high (>=1.00) / tight (<0.10)"] == 2
    assert buckets["low (<0.75) / wide (>=0.20)"] == 1


def test_regime_buckets_handles_missing_context():
    trades = [_trade(0, entry_context=None)]
    buckets = sm.regime_buckets(trades)
    assert buckets["unknown / unknown"] == 1


def test_core_five_pass_fail_flags():
    # A strategy that clearly beats every benchmark (all wins, no losses at all).
    trades = [_trade(500, entry_cost=10, exit_cost=5) for _ in range(5)]
    cf = sm.core_five(trades, capital_basis=100000)
    assert cf["profit_factor"]["value"] == float("inf")
    assert cf["profit_factor"]["pass"] is True
    assert cf["expectancy"]["pass"] is True
    assert cf["max_drawdown"]["pass"] is True


def test_core_five_expectancy_pass_none_without_costs():
    trades = [_trade(100, entry_cost=0, exit_cost=0)]
    cf = sm.core_five(trades)
    assert cf["expectancy"]["pass"] is None


def test_winrate_backtest_agreement():
    result = sm.winrate_backtest_agreement(0.55, 0.60)
    assert result["agree"] is True
    result2 = sm.winrate_backtest_agreement(0.30, 0.65)
    assert result2["agree"] is False


def test_winrate_backtest_agreement_none_inputs():
    result = sm.winrate_backtest_agreement(None, 0.6)
    assert result["agree"] is None


def test_iv_crush_positive_when_iv_falls():
    t = _trade(0, entry_iv=0.45, exit_iv=0.20)
    assert sm.iv_crush(t) == pytest.approx(0.25)


def test_iv_crush_negative_when_iv_rises():
    t = _trade(0, entry_iv=0.20, exit_iv=0.30)
    assert sm.iv_crush(t) == pytest.approx(-0.10)


def test_iv_crush_none_when_either_side_missing():
    assert sm.iv_crush(_trade(0, entry_iv=None, exit_iv=0.20)) is None
    assert sm.iv_crush(_trade(0, entry_iv=0.45, exit_iv=None)) is None
    assert sm.iv_crush(_trade(0)) is None


def test_avg_iv_crush_averages_only_trades_with_both_sides():
    trades = [
        _trade(0, entry_iv=0.50, exit_iv=0.20),  # crush 0.30
        _trade(0, entry_iv=0.40, exit_iv=0.30),  # crush 0.10
        _trade(0, entry_iv=None, exit_iv=0.20),  # excluded -- missing entry_iv
    ]
    result = sm.avg_iv_crush(trades)
    assert result["sample_count"] == 2
    assert result["avg_crush"] == pytest.approx((0.30 + 0.10) / 2)


def test_avg_iv_crush_no_trades_with_iv_data():
    trades = [_trade(0), _trade(0, entry_iv=0.4)]  # neither has both sides
    result = sm.avg_iv_crush(trades)
    assert result == {"avg_crush": None, "sample_count": 0}


def test_strategy_summary_includes_iv_crush():
    trades = [_trade(100, entry_iv=0.50, exit_iv=0.20)]
    summary = sm.strategy_summary(trades)
    assert summary["iv_crush"]["avg_crush"] == pytest.approx(0.30)
    assert summary["iv_crush"]["sample_count"] == 1
