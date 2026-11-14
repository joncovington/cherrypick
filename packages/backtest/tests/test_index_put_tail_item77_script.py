"""Item 77's re-read of item 57 under the three bars: buying power, the cycle mean, the judge. Invented
trades only; the store is never read."""

from __future__ import annotations

import importlib.util
import random
from datetime import date, timedelta
from pathlib import Path

from cherrypick.backtest.study import checks as ck

_spec = importlib.util.spec_from_file_location(
    "index_put_tail_item77", Path(__file__).resolve().parents[1] / "scripts" / "index_put_tail_item77.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_cash_secured_holds_the_strike_less_the_premium():
    assert m.cash_bp(500.0, 120.0) == 49_880.0


def test_margin_is_the_reg_t_naked_put_with_spx_at_15_percent():
    # SPX at 6000, a put 300 out of the money: 15% x 600,000 - 30,000 = 60,000, above the 10% floor.
    assert m.margin_bp("SPX", 6000.0, 5700.0, 400.0) == 60_400.0
    # A fund at 20%: 20% x 50,000 - 2,500 = 7,500, above 10% x 47,500.
    assert m.margin_bp("SPY", 500.0, 475.0, 30.0) == 7_530.0
    # Far out of the money the floor binds: 20% x 50,000 - 10,000 = 0 < 10% x 40,000.
    assert m.margin_bp("QQQ", 500.0, 400.0, 5.0) == 4_005.0


def test_a_short_put_is_long_delta():
    d = m.short_put_delta(100.0, 95.0, 7 / 365, 0.2)
    assert 0 < d < 50


def _row(sym, expiry, net, financed=0.0, hedged=0.0, rate=0.04, days=7):
    arm = {"bp": 1.0, "net": net, "financed": financed, "hedged": hedged}
    return {"symbol": sym, "expiry": expiry, "entry": expiry, "days": days, "rate": rate, "cash": dict(arm),
            "margin": dict(arm)}  # fmt: skip


def test_a_cycle_is_the_equal_weight_mean_over_the_names_present():
    rows = [_row("SPX", "2024-01-05", 0.01), _row("SPY", "2024-01-05", 0.03), _row("SPX", "2024-01-12", 0.02)]
    cs = m.cycles(rows, "cash")
    assert [c["expiry"] for c in cs] == ["2024-01-05", "2024-01-12"]
    assert abs(cs[0]["value"] - 0.02) < 1e-12 and cs[0]["names"] == 2 and cs[1]["names"] == 1


def test_cycles_a_year_follow_the_window():
    weeks = [{"expiry": (date(2020, 1, 3) + timedelta(days=7 * i)).isoformat()} for i in range(156)]
    assert m.per_year(weeks) == 52


def _weekly(n, draw, seed=1):
    rng = random.Random(seed)
    d0 = date(2016, 3, 4)
    return [_row("SPX", (d0 + timedelta(days=7 * i)).isoformat(), *draw(rng)) for i in range(n)]


def test_a_put_that_beats_the_bill_and_its_delta_with_a_mild_tail_passes():
    rows = _weekly(300, lambda r: (r.gauss(0.004, 0.002), r.gauss(0.0005, 0.0005)))
    blocks = m.judge({"cash": m.bars(m.cycles(rows, "cash"))})
    assert blocks["cash"]["verdict"] == ck.PASS


def test_a_put_whose_rare_crash_outweighs_a_year_fails_the_tail_bar():
    # +0.3% a week, and about one week in 40 a -12% loss: the mean is positive, a bad year is not earned back.
    rows = _weekly(400, lambda r: (-0.12 if r.random() < 0.025 else 0.003, 0.0), seed=4)
    blocks = m.judge({"cash": m.bars(m.cycles(rows, "cash"))})
    assert blocks["cash"]["verdict"] == ck.FAIL and "tail" in blocks["cash"]["failed"]


def test_a_t_of_2_on_the_cycle_floor_is_under_holms_strictest_bar_for_two_arms():
    # Why the judge has no Holm step: the smallest passing t on the floor already clears 0.025.
    from cherrypick.core.stats import t_cdf

    assert 1.0 - t_cdf(m.T_BAR, m.MIN_CYCLES - 1) < 0.025


def test_each_arm_is_judged_on_its_own_three_bars():
    p, f = {"verdict": ck.PASS}, {"verdict": ck.FAIL}
    out = m.judge(
        {"cash": {"capital": f, "tail": p, "delta": p}, "margin": {"capital": p, "tail": p, "delta": p}}
    )
    assert out["cash"]["verdict"] == ck.FAIL and out["cash"]["failed"] == ["capital"]
    assert out["margin"]["verdict"] == ck.PASS
