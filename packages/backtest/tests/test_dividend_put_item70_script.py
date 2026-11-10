"""Hypothesis 70's study (packages/backtest/scripts/dividend_put_item70.py): its screen, its strikes, its
replay and its verdict. Invented names and chains only, priced flat at 30% vol, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("dividend_put_item70", SCRIPTS / "dividend_put_item70.py")
dv = importlib.util.module_from_spec(_spec)
sys.modules["dividend_put_item70"] = dv  # its dataclasses look their module up by name
_spec.loader.exec_module(dv)

DAY = date(2025, 9, 2)
QUARTERLY = {DAY - timedelta(days=d): 0.50 for d in (20, 110, 200, 290)}  # $2.00 a year
STATEMENTS = [(date(2024, 12, 31), 400.0, 100.0)]  # EPS 4.00
C50 = {DAY: 50.0}


# --- the screen -----------------------------------------------------------------------------------


def test_a_payer_with_earnings_and_a_covered_dividend_passes_and_carries_its_figures():
    r = dv.screen_row(C50, QUARTERLY, STATEMENTS, [], DAY)
    assert r["state"] == "pass"
    assert r["ttm"] == pytest.approx(2.0) and r["yield"] == pytest.approx(0.04)
    assert (
        r["fwd"] == pytest.approx(2.0)
        and r["payout"] == pytest.approx(0.5)
        and r["pe"] == pytest.approx(12.5)
    )


def test_the_forward_dividend_is_the_latest_payment_at_the_run_rate():
    raised = {**QUARTERLY, DAY - timedelta(days=20): 0.60}
    assert dv.forward_dividend(raised, DAY) == pytest.approx(2.40)
    assert dv.screen_row(C50, raised, STATEMENTS, [], DAY)["ttm"] == pytest.approx(2.10)


def test_a_dividend_older_than_a_year_or_not_yet_ex_does_not_count():
    old = {DAY - timedelta(days=400): 0.5, DAY + timedelta(days=5): 0.5}
    assert dv.screen_row(C50, old, STATEMENTS, [], DAY)["state"] == "no_dividend"


def test_a_payout_at_or_over_100_percent_fails():
    thin = [(date(2024, 12, 31), 200.0, 100.0)]  # EPS 2.00 against $2.00 paid
    assert dv.screen_row(C50, QUARTERLY, thin, [], DAY)["state"] == "payout"


def test_a_loss_fails():
    loss = [(date(2024, 12, 31), -10.0, 100.0)]
    assert dv.screen_row(C50, QUARTERLY, loss, [], DAY)["state"] == "no_earnings"


def test_a_statement_inside_the_filing_lag_is_not_yet_known():
    recent = [(DAY - timedelta(days=dv.STATEMENT_LAG - 1), 400.0, 100.0)]
    assert dv.screen_row(C50, QUARTERLY, recent, [], DAY)["state"] == "unknown"
    lagged = [(DAY - timedelta(days=dv.STATEMENT_LAG), 400.0, 100.0)]
    assert dv.screen_row(C50, QUARTERLY, lagged, [], DAY)["state"] == "pass"


def test_a_split_after_the_statement_makes_the_name_unknown():
    assert dv.screen_row(C50, QUARTERLY, STATEMENTS, [date(2025, 3, 3)], DAY)["state"] == "unknown"
    assert dv.screen_row(C50, QUARTERLY, STATEMENTS, [date(2024, 6, 3)], DAY)["state"] == "pass"


def test_the_list_keeps_cheap_names_by_yield_and_stops_at_its_size(monkeypatch):
    rows = {
        "RICH": {"state": "pass", "yield": 0.09, "pe": 40.0},  # highest yield, dearest P/E: cut
        "A": {"state": "pass", "yield": 0.05, "pe": 10.0},
        "B": {"state": "pass", "yield": 0.06, "pe": 12.0},
        "C": {"state": "pass", "yield": 0.03, "pe": 30.0},
        "OUT": {"state": "payout", "yield": 0.20},
    }
    assert dv.screen_list(rows) == ["B", "A"]  # median P/E of the four passing is 21
    monkeypatch.setattr(dv, "SCREEN_N", 1)
    assert dv.screen_list(rows) == ["B"]


LONG_DIVS = {DAY - timedelta(days=20 + 91 * i): 0.50 for i in range(30)}  # $2.00 a year for 7 years


def _history(price: float, months: int = dv.NORM_MONTHS) -> dict[date, float]:
    return {**dict.fromkeys(dv.months_before(DAY, months), price), DAY: 50.0}


def test_the_target_is_the_forward_dividend_at_the_names_own_usual_yield():
    norm = dv.norm_yield(_history(40.0), LONG_DIVS, [], DAY)
    assert norm == pytest.approx(0.05, rel=0.02)  # $2.00 on $40
    r = dv.screen_row(_history(40.0), LONG_DIVS, STATEMENTS, [], DAY)
    assert r["target"] == pytest.approx(2.0 / norm) and r["target"] < r["close"]
    assert dv.screen_row(_history(60.0), LONG_DIVS, STATEMENTS, [], DAY)["target"] > 50.0


def test_the_usual_yield_needs_enough_months_and_skips_a_split_year():
    assert dv.norm_yield(_history(40.0, dv.NORM_MIN - 1), LONG_DIVS, [], DAY) is None
    assert dv.norm_yield(_history(40.0, dv.NORM_MIN), LONG_DIVS, [], DAY) is not None
    split = dv.months_before(DAY, dv.NORM_MONTHS)[-12] - timedelta(days=3)
    assert dv.norm_yield(_history(40.0, dv.NORM_MIN), LONG_DIVS, [split], DAY) is None
    assert dv.target_price(0.0, 0.05) is None and dv.target_price(2.0, None) is None


# --- the strikes ----------------------------------------------------------------------------------


def _rows(spot: float, day: date, exp: date, wide: set[float] = frozenset()) -> list:
    t = (exp - day).days / 365
    rows = []
    for k in range(20, 81):
        for cp in "CP":
            px = dv.pcr.black76(cp, spot, float(k), t, 0.30, 1.0)
            half = 0.05 if k in wide else 0.01  # a 0.10 width fails the bar on these cheap puts
            rows.append([float(k), cp, round(max(px - half, 0.0), 2), round(px + half, 2)])
    return rows


EXP = date(2025, 10, 17)  # the October monthly, 45 days from DAY


def _slice(spot=50.0, wide=frozenset()):
    dv.pcr.SYMBOL = "AAPL"
    return dv.pcr.Slice(DAY, EXP, _rows(spot, DAY, EXP, set(wide)), spot)


def test_y_strike_takes_the_highest_put_at_or_under_the_target_and_never_above_the_close():
    sl = _slice()
    assert dv.pick_strike("y-strike", sl, 50.0, 43.6, dv.FRAC) == (43.0, "")
    assert dv.pick_strike("y-strike", sl, 50.0, 70.0, dv.FRAC) == (50.0, "")  # target over the close


def test_y_strike_does_not_fall_back_when_its_strike_fails_the_spread_bar():
    sl = _slice(wide={43.0})
    assert dv.pick_strike("y-strike", sl, 50.0, 43.6, dv.FRAC) == (None, "spread")


def test_y_net_counts_the_credit_toward_the_target():
    sl = _slice()
    k, why = dv.pick_strike("y-net", sl, 50.0, 43.8, dv.FRAC)
    assert why == "" and k > 43.0
    sell = dv._leg(sl, k, dv.FRAC)[0]
    assert k - sell <= 43.8
    above = dv._leg(sl, k + 1, dv.FRAC)[0]
    assert (k + 1) - above > 43.8  # the next strike up misses the target


def test_y_net_goes_down_past_a_wide_strike():
    sl = _slice()
    k, _ = dv.pick_strike("y-net", sl, 50.0, 43.8, dv.FRAC)
    k2, why = dv.pick_strike("y-net", _slice(wide={k}), 50.0, 43.8, dv.FRAC)
    assert why == "" and k2 < k


def test_d16_takes_the_put_nearest_minus_16_delta():
    sl = _slice()
    k, _ = dv.pick_strike("d16-tp50", sl, 50.0, None, dv.FRAC)
    best = min(abs(sl.delta("P", float(x)) + 0.16) for x in range(20, 50))
    assert abs(sl.delta("P", k) + 0.16) == pytest.approx(best)


def test_a_yield_arm_without_a_target_takes_nothing():
    assert dv.pick_strike("y-strike", _slice(), 50.0, None, dv.FRAC) == (None, "no_target")


# --- the replay -----------------------------------------------------------------------------------


def scenario(spots, listed=((2025, 9),), divs=None):
    days, d = [], DAY
    while d <= EXP + timedelta(days=5):
        if dv.cal.is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    closes = {d: float(spots(i)) for i, d in enumerate(days)}
    chains = {d.isoformat(): {EXP.isoformat(): _rows(closes[d], d, EXP)} for d in days if d < EXP}
    dv.pcr.SYMBOL = "AAPL"
    nd = dv.NameData("TEST", chains, closes, [], divs or {}, set(listed))
    nd.targets = dict.fromkeys(days, 45.0)
    return nd


def run(nd, arm):
    return dv.run_arm(nd, arm, DAY, DAY, max(nd.days), dv.FRAC)


def test_an_arm_enters_only_in_a_listed_month():
    trades, stats = run(scenario(lambda i: 50.0), "y-strike")
    assert len(trades) == 1 and stats["entries"] == 1
    trades, stats = run(scenario(lambda i: 50.0, listed=((2025, 10),)), "y-strike")
    assert trades == [] and "entries" not in stats


def test_a_held_put_expires_above_its_strike_and_is_assigned_below_it():
    up, _ = run(scenario(lambda i: 50.0), "y-strike")
    assert up[0]["how"] == "expired" and up[0]["strike"] == 45.0 and up[0]["net"] > 0
    down, _ = run(scenario(lambda i: 50.0 if i == 0 else 40.0), "y-strike")
    assert down[0]["how"] == "assigned" and down[0]["net"] < 0


def test_tp50_buys_back_at_half_the_credit_and_the_held_arm_does_not():
    rally = scenario(lambda i: 50.0 + i)
    tp, _ = run(rally, "d16-tp50")
    held, _ = run(rally, "d16-hold")
    assert tp[0]["how"] == "tp50"
    assert held[0]["how"] == "expired"


def test_the_edge_takes_off_the_delta_equivalent_stock_with_its_dividends():
    t = {"net": 100.0, "pos_delta": 0.2, "spot_entry": 50.0, "spot_exit": 49.0, "divs": 0.5}
    assert dv.trade_edge(t) == pytest.approx(100.0 - 0.2 * 100 * (-1.0 + 0.5))
    assert dv.trade_edge({**t, "divs": 0.0}) == pytest.approx(100.0 + 20.0)
    assert dv.name_edge([], 1.0) == 0.0


def test_a_trade_carries_the_dividends_ex_inside_it():
    nd = scenario(lambda i: 50.0, divs={DAY + timedelta(days=10): 0.5, DAY + timedelta(days=200): 0.5})
    trades, _ = run(nd, "y-strike")
    assert trades[0]["divs"] == pytest.approx(0.5)


# --- the requests ---------------------------------------------------------------------------------


def test_requests_name_each_listed_names_monthlies_once_and_skip_held_paths():
    screens = {"design": {"days": {"2025-09-02": {"list": ["KO", "VZ"]}}, "bill": {}}}
    exps = dv.entry_expiries(date(2025, 9, 2))
    assert all(dv.is_monthly(e) for e in exps) and date(2025, 10, 17) in exps
    reqs = dv.path_requests({("KO", "2025-10-17")}, screens)
    keys = {(r["symbol"], r["expiration"]) for r in reqs}
    assert ("KO", "2025-10-17") not in keys and ("VZ", "2025-10-17") in keys
    assert len(keys) == len(reqs)


# --- the verdict ----------------------------------------------------------------------------------


def _window(arm_edge, base_edge, n=25, trades=200):
    names = [f"N{i}" for i in range(n)]
    edge = {a: {} for a in dv.ARMS}
    for i, s in enumerate(names):
        for a in dv.ARMS:
            edge[a][s] = (arm_edge if a in dv.JUDGED else base_edge) + 0.01 * (i % 3)
    return {"edge": edge, "stress": edge, "trades": dict.fromkeys(dv.ARMS, trades),
            "traded": dict.fromkeys(dv.ARMS, n)}  # fmt: skip


def test_an_arm_that_beats_the_base_in_both_windows_passes():
    v = dv.judge(_window(2.0, 0.5), _window(1.0, 0.2))
    assert all(x["verdict"] == "pass" for x in v.values())


def test_an_arm_below_the_base_fails_and_one_that_fails_the_holdout_fails():
    assert dv.judge(_window(0.2, 0.5), _window(1.0, 0.2))["y-strike"]["verdict"] == "fail"
    assert dv.judge(_window(2.0, 0.5), _window(-1.0, 0.2))["y-strike"]["verdict"] == "fail"


def test_a_positive_paired_gap_on_a_losing_arm_fails_on_its_own_median():
    v = dv.judge(_window(-0.5, -2.0), _window(1.0, 0.2))["y-net"]
    assert v["checks"]["paired_mean_positive"] and not v["checks"]["own_median_positive"]
    assert v["verdict"] == "fail"


def test_too_few_names_or_trades_is_no_verdict():
    assert (
        dv.judge(_window(2.0, 0.5, n=dv.MIN_NAMES - 1), _window(1.0, 0.2))["y-strike"]["verdict"]
        == "no verdict"
    )
    few = _window(2.0, 0.5, trades=dv.MIN_TRADES - 1)
    assert dv.judge(few, _window(1.0, 0.2))["y-strike"]["verdict"] == "no verdict"
    assert dv.judge(_window(2.0, 0.5), _window(1.0, 0.2, trades=10))["y-strike"]["verdict"] == "no verdict"
