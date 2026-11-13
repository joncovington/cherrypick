"""Hypothesis 74's study (packages/backtest/scripts/super_bull_item74.py): the super bull on one expiry and
with its long call a monthly later, against the same delta financed on the same capital. Invented quotes
only (Black-Scholes prices around a made-up forward), no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest
from cherrypick.core import bs

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "super_bull_item74.py"


def _module():
    spec = importlib.util.spec_from_file_location("super_bull_item74", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _module()
ic = m.ic

F, B = "2024-03-15", "2024-04-19"
ENTRY = "2024-02-09"  # 35 days before F


def rows(forward: float, days: int, vol: float = 0.25, half_spread: float = 0.02) -> list:
    years = max(days, 1) / 365.0
    out = []
    for k in range(int(forward) - 40, int(forward) + 41):
        for r, name in (("C", "CALL"), ("P", "PUT")):
            p = bs.price(forward, k, years, vol, r, 0.0)
            out.append((float(k), name, round(max(p - half_spread, 0.0), 2), round(p + half_spread, 2)))
    return out


def chains(session=ENTRY, forward=100.0, **kw) -> dict:
    return {
        tag: ic.Chain(rows(forward, ic.days_between(session, e), **kw), session, e)
        for tag, e in (("F", F), ("B", B))
    }


def test_monthlies_are_third_fridays_or_the_session_before_a_holiday():
    assert m.monthly_expiry(2024, 3) == date(2024, 3, 15)
    assert m.monthly_expiry(2019, 4) == date(2019, 4, 18)  # Good Friday


def test_requests_ask_for_each_monthly_from_100_days_out_and_nothing_not_yet_started():
    reqs = m.requests(names=("XYZ",), first=date(2024, 1, 1), last=date(2024, 6, 30), today=date(2024, 3, 1))
    assert [r["expiration"] for r in reqs] == [
        "2024-01-19",
        "2024-02-16",
        "2024-03-15",
        "2024-04-19",
        "2024-05-17",
    ]
    assert reqs[2]["start"] == "2023-12-06" and reqs[2]["through"] == "2024-03-01"
    # June's path would start 2024-03-13, after `today`: not asked for.
    assert "2024-06-21" not in {r["expiration"] for r in reqs}


@pytest.mark.parametrize("diagonal, tag", [(False, "F"), (True, "B")])
def test_the_legs_are_a_put_spread_under_a_call_spread_with_the_long_call_in_its_month(diagonal, tag):
    c = chains()
    legs = m.pick_legs(c, diagonal)
    (t1, sp, r1, n1), (t2, lp, r2, n2), (t3, lc, r3, n3), (t4, sc, r4, n4) = legs
    assert (t1, r1, n1, t2, r2, n2) == ("F", "P", -1, "F", "P", 1)
    assert (t3, r3, n3) == (tag, "C", 1) and (t4, r4, n4) == ("F", "C", -1)
    assert lp < sp < 100 < lc < sc
    assert abs(c["F"].delta(c["F"].by["P"][sp]) + m.SHORT_PUT) < 0.03
    assert abs(c[tag].delta(c[tag].by["C"][lc]) - m.LONG_CALL) < 0.03


def test_no_legs_when_a_chain_is_missing():
    c = chains()
    assert m.pick_legs({"F": c["F"], "B": None}, diagonal=True) is None
    assert m.pick_legs({"F": c["F"], "B": None}, diagonal=False) is not None


def test_capital_is_the_put_width_less_the_credit_and_near_even_money_is_a_quarter_of_it():
    legs = [("F", 95.0, "P", -1), ("F", 90.0, "P", 1), ("F", 105.0, "C", 1), ("F", 110.0, "C", -1)]
    assert m.capital(legs, 0.20) == 480.0
    assert m.capital(legs, -0.45) == 545.0  # a debit adds to it
    assert m.capital(legs, 5.0) is None
    assert m.max_gain(legs, -0.45) == 455.0
    assert m.near_even(-0.45, 545.0)
    assert not m.near_even(1.50, 350.0)  # a $150 credit on $350: a credit trade, not near even money


def test_financed_holds_the_delta_on_the_capital_and_earns_the_bill_on_the_capital():
    # 30 shares of delta, the forward up 10 (the borrowing and dividend already in it), $500 at 5% a year.
    assert m.financed(30.0, 100.0, 110.0, 500.0, 0.05, 365) == pytest.approx(325.0)
    assert m.financed(30.0, 100.0, 95.0, 500.0, 0.0, 30) == pytest.approx(-150.0)


@pytest.mark.parametrize(
    "gross, gain, days_left, hold, want",
    [
        (499.0, 1000.0, 20, False, None),  # the target is the lesser of $500 (half the gain) and $500 capital
        (500.0, 1000.0, 20, False, "target"),
        (300.0, 600.0, 20, False, "target"),  # half a $600 gain, under the capital
        (-250.0, 1000.0, 20, False, "stop"),  # half the $500 capital
        (-249.0, 1000.0, 20, False, None),
        (10.0, 1000.0, 3, False, "expiry"),
        (500.0, 1000.0, 20, True, None),  # `hold` takes no target and no stop
        (-400.0, 1000.0, 2, True, "expiry"),
    ],
)
def test_exit_reasons(gross, gain, days_left, hold, want):
    assert m.exit_reason(gross, 500.0, gain, days_left, hold=hold) == want


def _path(forward: float, sessions=("2024-02-20", "2024-03-01", "2024-03-13")) -> list:
    return [(s, chains(s, forward)) for s in sessions]


def test_a_rally_takes_profit_a_fall_stops_and_a_flat_market_closes_at_expiry():
    entry = chains()
    legs = m.pick_legs(entry, diagonal=False)
    up = m.run_trade("XYZ", legs, entry, _path(118.0), F, ENTRY, 0.05)
    down = m.run_trade("XYZ", legs, entry, _path(84.0), F, ENTRY, 0.05)
    flat = m.run_trade("XYZ", legs, entry, _path(100.0), F, ENTRY, 0.05)
    assert up["how"] == "target" and up["net"] > 0 and up["exit"] == "2024-02-20"
    assert down["how"] == "stop" and down["net"] < 0
    assert flat["how"] == "expiry" and flat["exit"] == "2024-03-13"
    for r in (up, down, flat):
        assert r["excess"] == pytest.approx(r["net"] - r["financed"])
        assert r["cost"] > 0 and r["net"] == pytest.approx(r["gross"] - r["cost"])
    assert up["financed"] > 0 > down["financed"]


def test_the_diagonal_runs_on_both_months():
    entry = chains()
    legs = m.pick_legs(entry, diagonal=True)
    r = m.run_trade("XYZ", legs, entry, _path(100.0), F, ENTRY, 0.05)
    assert r["how"] == "expiry"
    assert r["calls_roc"] is not None and r["puts_roc"] is not None


def test_a_long_with_no_bid_is_left_to_expire_on_the_close_but_a_short_is_still_bought_back():
    near = chains("2024-03-13")  # two days out: the far wings bid nothing
    legs = [("F", 80.0, "P", 1), ("F", 120.0, "C", 1)]
    assert near["F"].by["P"][80.0]["bid"] == 0
    assert m.price(near, legs, close=True) == 0.0
    # A short with no bid is never dropped: buying it back pays its ask, so the close costs something.
    assert m.price(near, [("F", 80.0, "P", -1)], close=True) < 0


def test_a_trade_with_no_exit_mark_is_named():
    entry = chains()
    legs = m.pick_legs(entry, diagonal=False)
    assert m.run_trade("XYZ", legs, entry, [], F, ENTRY, 0.05) == {"skip": "no exit mark"}


def test_the_lower_bound_is_above_zero_for_a_steady_gain_and_below_for_noise():
    lo, p = m.lower_bound([0.02 + 0.001 * (i % 5) for i in range(60)])
    assert lo > 0 and p < 0.05
    lo, p = m.lower_bound([0.05 if i % 2 else -0.05 for i in range(60)])
    assert lo < 0 and p > 0.05


def _rows(n_cycles: int, net: float, financed: float, cost: float = 5.0) -> list:
    out = []
    for i in range(n_cycles):
        y, mo = 2016 + i // 12, i % 12 + 1
        for arm in m.ARMS:
            jitter = 0.5 * ((i * 7) % 5 - 2)
            out.append(
                {
                    "arm": arm,
                    "front": f"{y}-{mo:02d}-15",
                    "net": net + jitter,
                    "excess": net + jitter - financed,
                    "capital": 500.0,
                    "cost": cost,
                    "how": "target",
                    "financed": financed,
                }
            )
    return out


def _judged(rows):
    skips = {a: {} for a in m.ARMS}
    tests = m.score(rows, skips, "2016-01-01", "2021-12-31")
    m.judge(tests)
    return tests


def test_an_arm_passes_only_when_it_beats_the_financed_delta():
    assert {t["verdict"] for t in _judged(_rows(72, 40.0, 10.0)).values()} == {"pass"}
    behind = _judged(_rows(72, 40.0, 45.0))  # it makes money, but the financed delta makes more
    assert {t["verdict"] for t in behind.values()} == {"fail"}
    assert not behind["A35"]["checks"]["excess_positive"] and behind["A35"]["checks"]["net_positive"]


def test_costs_at_one_and_a_half_times_can_fail_it():
    tight = _judged(_rows(72, 12.0, 0.0, cost=30.0))  # $12 net on $30 of costs: gone at 1.5x
    assert tight["A35"]["checks"]["excess_positive"]
    assert not tight["A35"]["checks"]["net_at_1_5x_costs"]
    assert tight["A35"]["verdict"] == "fail"


def test_too_few_cycles_or_missing_exits_is_no_verdict():
    assert _judged(_rows(40, 40.0, 10.0))["A35"]["verdict"] == "no verdict"
    rows = _rows(72, 40.0, 10.0)
    skips = {a: {"no exit mark": 10} for a in m.ARMS}
    tests = m.score(rows, skips, "2016-01-01", "2021-12-31")
    m.judge(tests)
    assert tests["A35"]["verdict"] == "no verdict"


def test_the_holdout_is_refused_without_the_flag(capsys):
    assert m.main(["study", "--end", "2022-06-30"]) == 2
    assert "holdout" in capsys.readouterr().err


def test_a_flat_smile_has_no_skew():
    assert abs(m.skew(chains()["F"])) < 0.01
