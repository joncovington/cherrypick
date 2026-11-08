"""The scorecard's stats table and insights, on invented series and trades."""

import math

import pytest

from cherrypick.backtest import scorecard as sc


def _series(*rows):
    return [{"session": d, "net": n, "trades": t} for d, n, t in rows]


# +100, -50 (peak 100 on 01-02, low 50), -30 (cum 20), +200 (cum 220, recovered 01-10), -10 (to the end).
SERIES = _series(
    ("2026-01-02", 100.0, 1),
    ("2026-01-05", -50.0, 1),
    ("2026-01-06", 0.0, 0),
    ("2026-01-07", -30.0, 1),
    ("2026-01-10", 200.0, 1),
    ("2026-02-20", -10.0, 1),
)


def test_stats_table():
    s = sc.scorecard_stats(SERIES)
    assert s["total_pl"] == 210.0
    assert s["max_drawdown"] == 80.0
    assert s["return_on_dd"] == pytest.approx(210 / 80, abs=0.01)
    assert s["high_pl"] == 220.0
    assert s["low_pl"] == 0.0  # never below the start
    assert s["profit_factor"] == pytest.approx(300 / 90, abs=0.01)
    assert s["profit_factor_basis"] == "sessions"
    assert (s["sessions"], s["traded_sessions"], s["trades"]) == (6, 5, 5)


def test_sharpe_and_sortino_are_annualised_from_sessions():
    s = sc.scorecard_stats(SERIES)
    nets = [100, -50, 0, -30, 200, -10]
    mean = sum(nets) / 6
    sd = math.sqrt(sum((x - mean) ** 2 for x in nets) / 5)
    down = math.sqrt(sum(min(x, 0) ** 2 for x in nets) / 6)
    assert s["sharpe"] == pytest.approx(mean / sd * math.sqrt(252), abs=0.001)
    assert s["sortino"] == pytest.approx(mean / down * math.sqrt(252), abs=0.001)


def test_low_pl_goes_negative_when_the_run_starts_losing():
    s = sc.scorecard_stats(_series(("2026-01-02", -40.0, 1), ("2026-01-05", 10.0, 1)))
    assert s["low_pl"] == -40.0 and s["high_pl"] == 0.0 and s["max_drawdown"] == 40.0


def test_profit_factor_reads_trades_when_given():
    trades = [{"net_pnl": 30.0}, {"net_pnl": -10.0}, {"net_pnl": None}]
    s = sc.scorecard_stats(SERIES, trades)
    assert s["profit_factor"] == 3.0 and s["profit_factor_basis"] == "trades" and s["trades"] == 2


def test_no_losses_means_no_profit_factor():
    assert sc.scorecard_stats(_series(("2026-01-02", 5.0, 1)))["profit_factor"] is None


def test_drawdown_episodes_in_calendar_days():
    eps = sc.drawdown_episodes(SERIES)
    assert eps[0] == {"start": "2026-01-02", "depth": 80.0, "end": "2026-01-10", "recovered": True, "days": 8}
    assert eps[1] == {
        "start": "2026-01-10",
        "depth": 10.0,
        "end": "2026-02-20",
        "recovered": False,
        "days": 41,
    }


def test_a_run_losing_from_its_first_session_starts_its_drawdown_there():
    eps = sc.drawdown_episodes(_series(("2026-03-02", -5.0, 1), ("2026-03-09", 10.0, 1)))
    assert eps == [{"start": "2026-03-02", "depth": 5.0, "end": "2026-03-09", "recovered": True, "days": 7}]


def test_drawdown_insights():
    lines = {x["key"]: x for x in sc.insights(SERIES)}
    assert lines["longest_drawdown"]["days"] == 41
    assert "not recovered" in lines["longest_drawdown"]["text"]
    assert lines["long_drawdowns"]["count"] == 1
    assert lines["avg_drawdown"]["avg_days"] == 24.5 and lines["avg_drawdown"]["avg_depth"] == 45.0
    assert "fees" not in lines and "wide_spreads" not in lines  # no trades, no cost lines


def test_a_run_that_never_draws_down_has_no_drawdown_lines():
    assert sc.insights(_series(("2026-01-02", 5.0, 1), ("2026-01-05", 5.0, 1))) == []


def test_fee_insight_and_its_share_of_gross():
    trades = [{"gross_pnl": 100.0, "fees": 20.0, "settlement": 5.0, "slippage": 4.0, "net_pnl": 75.0}] * 2
    fee = {x["key"]: x for x in sc.insights(SERIES, trades)}["fees"]
    assert fee["fees"] == 50.0 and fee["share_of_gross"] == 0.25 and fee["flag"] is True
    assert "$50" in fee["text"] and "25%" in fee["text"] and "already in the fill prices" in fee["text"]


def test_wide_spread_insight_flags_trades_over_the_multiple_of_the_median():
    trades = [{"slippage": 2.0, "session": f"2026-01-{i:02d}"} for i in range(1, 20)]
    trades += [{"slippage": 6.5, "session": "2026-01-25"}, {"slippage": 6.0, "session": "2026-01-26"}]
    wide = {x["key"]: x for x in sc.insights(SERIES, trades)}["wide_spreads"]
    assert wide["count"] == 1 and wide["of"] == 21 and wide["sessions"] == ["2026-01-25"]
    # 6.0 is exactly 3x the median 2.0, not over it.


def test_wide_spread_insight_needs_enough_trades():
    trades = [{"slippage": 1.0}] * (sc.MIN_SLIPPAGE_TRADES - 2) + [{"slippage": 50.0}]
    assert "wide_spreads" not in {x["key"] for x in sc.insights(SERIES, trades)}


def test_scorecard_shape():
    out = sc.scorecard(SERIES)
    assert set(out) == {"stats", "insights"} and all(
        {"key", "text", "flag"} <= set(x) for x in out["insights"]
    )
