"""Hypothesis 71's study (packages/backtest/scripts/leaps_leverage_item71.py): a LEAPS call as leverage,
against the same dollars in a modelled daily-reset fund, a financed delta and plain shares. Invented
Black-Scholes chains and invented fund series only, no store.

Each rule is shown firing, and each guard failing when its invariant is broken on purpose.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cherrypick.pmcc")
pytest.importorskip("numpy")

from cherrypick.core import bs  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("leaps_leverage_item71", SCRIPTS / "leaps_leverage_item71.py")
lv = importlib.util.module_from_spec(_spec)
sys.modules["leaps_leverage_item71"] = lv  # its dataclasses look their module up by name
_spec.loader.exec_module(lv)
i36 = lv.i36

START, END = date(2016, 1, 4), date(2018, 6, 29)


def drift(i, d):
    return 100.0 * 1.0004**i * (1 + 0.004 * math.sin(i / 7))


def flat(i, d):
    return 100.0


def make_market(*, path=drift, vol=0.20, rate=0.01, end=END, **extra):
    """Black-Scholes chains at one vol around an invented close path; every monthly listed."""
    days = i36.sessions_between(date(2015, 11, 2), end)
    closes = {d: path(i, d) for i, d in enumerate(days)}
    sessions = [d for d in days if d >= START]

    def quotes(day, exp):
        if day not in closes or exp < day:
            return {}
        s, t = closes[day], (exp - day).days / 365.0
        out = {}
        for k in range(int(s * 0.3), int(s * 1.8)):
            for right in ("C", "P"):
                mid = max(bs.price(s, float(k), t, vol, right, rate, 0.0), 0.02)
                half = max(0.01, 0.004 * mid)
                out[(float(k), right)] = (max(mid - half, 0.0), mid + half)
        return out

    return i36.Market(
        "QQQ", "QQQ", "physical", "QQQ", "fund", sessions, closes, quotes, lambda d: rate, **extra
    )


@pytest.fixture(scope="module")
def mkt():
    return make_market()


@pytest.fixture(scope="module")
def run90(mkt):
    return lv.simulate(mkt, lv.ARMS["c90-45"], spread=0.0)


# ---------------------------------------------------------------- the declarations


def test_six_arms_four_against_the_fund_two_against_the_financed_delta():
    assert set(lv.ARMS) == {"c90-45", "c80-45", "c90-120", "c90-2y", "c50-45", "c30-45"}
    assert {a.name for a in lv.ARMS.values() if a.rival == "fund"} == {
        "c90-45",
        "c80-45",
        "c90-120",
        "c90-2y",
    }
    assert {a.name for a in lv.ARMS.values() if a.rival == "financed"} == {"c50-45", "c30-45"}
    assert lv.FUNDS == ("XSP(SPY)", "QQQ", "IWM", "GLD", "SLV", "SMH")  # judged; the stocks are reported
    assert lv.FIT_WINDOW[1] < lv.DESIGN[0]  # the fund is fitted before either window


# ---------------------------------------------------------------- expiries and cycles


def test_the_two_year_expiry_is_the_listed_monthly_nearest_730_days():
    day = date(2016, 3, 17)
    exp = lv.two_year_for(day)
    assert 540 <= (exp - day).days <= 900 and exp == lv.clock.standard_monthly(exp.year, exp.month)
    assert abs((exp - day).days - 730) <= 31
    only_jan = lv.two_year_for(day, lambda g, e: e.month == 1)
    assert only_jan.month == 1 and 540 <= (only_jan - day).days <= 900
    asked = []
    lv.two_year_for(day, lambda g, e: asked.append(g) or True)
    assert set(asked) == {date(2016, 3, 1)}  # the month's probe session
    assert lv.two_year_for(day, lambda g, e: False) is None


def test_the_two_year_probes_cover_the_band_from_each_months_first_session():
    reqs = lv.listing_requests("QQQ")
    assert all(r["kind"] == "listing2y" and r["start"] == r["through"] for r in reqs)
    for r in reqs:
        dte = (date.fromisoformat(r["expiration"]) - date.fromisoformat(r["start"])).days
        assert 540 <= dte <= 900 + lv.LISTING_SLACK


def test_a_cycle_closes_at_its_arms_dte_and_the_next_enters_the_session_after():
    sessions = i36.sessions_between(date(2016, 1, 4), date(2019, 6, 28))
    for name, dte in (("c90-45", 45), ("c90-120", 120)):
        cycles = lv.arm_cycles(lv.ARMS[name], sessions)
        for (d0, exp, c), nxt in zip(cycles, cycles[1:], strict=False):
            assert (exp - c).days <= dte < (exp - i36.sessions_between(d0, c)[-2]).days
            assert nxt[0] == lv.i36.cal.next_trading_day(c)
    early = lv.arm_cycles(lv.ARMS["c90-120"], sessions)
    late = lv.arm_cycles(lv.ARMS["c90-45"], sessions)
    assert len(early) > len(late)  # rolling at 120 DTE means more cycles, more spread paid


def test_the_plan_dedupes_paths_shared_across_arms():
    reqs = lv.path_requests("QQQ")
    keys = [(r["expiration"], r["start"], r["through"]) for r in reqs]
    assert len(keys) == len(set(keys))
    # c90-45, c80-45, c50-45 and c30-45 share one skeleton: only one of them names it
    assert not any(r["kind"] in ("long-c80-45", "long-c50-45", "long-c30-45") for r in reqs)
    assert {r["kind"] for r in reqs} == {"long-c90-45", "long-c90-120", "long-c90-2y"}


# ---------------------------------------------------------------- the strike, leverage, financing


def test_the_strike_is_the_delta_nearest_the_target_the_higher_on_a_tie():
    g = {80.0: (0.93, 0.2), 85.0: (0.91, 0.2), 90.0: (0.89, 0.2), 95.0: (0.80, 0.2)}
    assert lv.pick_strike(g, 0.90) == 90.0  # 0.91 and 0.89 tie; the higher strike, the smaller debit
    assert lv.pick_strike(g, 0.80) == 95.0
    assert lv.pick_strike({}, 0.9) is None


def test_leverage_is_dollar_exposure_per_dollar_of_debit():
    assert lv.leverage(0.9, 100.0, 30.0) == pytest.approx(3.0)
    assert lv.leverage(0.9, 100.0, 0.0) is None


def test_the_implied_rate_recovers_the_rate_a_european_pair_was_priced_at():
    s, k, t, r = 100.0, 70.0, 1.0, 0.04
    c, p = bs.price(s, k, t, 0.2, "C", r, 0.0), bs.price(s, k, t, 0.2, "P", r, 0.0)
    assert lv.implied_rate(s, k, t, c, p, 0.0) == pytest.approx(r, abs=1e-6)
    assert lv.implied_rate(s, k, 0.0, c, p, 0.0) is None


# ---------------------------------------------------------------- the fund model


def test_a_one_times_fund_with_no_fee_is_the_underlying():
    assert lv.fund_step(0.01, 0.05, 1.0, 0.003, fee=0.0) == pytest.approx(0.01)


def test_a_levered_fund_pays_bills_plus_spread_on_the_borrowed_part_and_its_fee():
    got = lv.fund_step(0.0, 0.05, 3.0, 0.005, fee=0.0095)
    assert got == pytest.approx(-(2 * 0.055 + 0.0095) / 252)


def test_total_returns_add_back_the_dividend_and_skip_a_split():
    d = [date(2016, 1, 4), date(2016, 1, 5), date(2016, 1, 6), date(2016, 1, 7)]
    closes = {d[0]: 100.0, d[1]: 99.0, d[2]: 49.5, d[3]: 50.0}
    r = lv.total_returns(closes, [(d[1], 1.0)], [d[2]])
    assert r[d[1]] == pytest.approx(0.0)  # fell by the dividend it paid
    assert d[2] not in r  # the split session
    assert r[d[3]] == pytest.approx(50.0 / 49.5 - 1)


def _fund_series(spread, lev, days, under, drag=0.0):
    return {d: lv.fund_step(under[d], 0.02, lev, spread) - drag / 252 for d in days}


def _check_series(drag_in_design=0.0, only=None):
    days = [d for d in i36.sessions_between(date(2011, 1, 3), date(2026, 10, 23))]
    under = {d: 0.0004 + 0.01 * math.sin(i / 3) for i, d in enumerate(days)}
    series = {"U": under}
    for f, lev in (("F2", 2), ("F3", 3)):
        drag = drag_in_design if only in (None, f) else 0.0
        series[f] = {
            d: lv.fund_step(under[d], 0.02, lev, 0.004)
            - (drag / 252 if lv.DESIGN[0] <= d <= lv.DESIGN[1] else 0.0)
            for d in days
        }
    return series


def test_a_three_times_fund_off_the_model_does_not_gate_but_sets_the_handicap():
    """2026-11-10, public bars: the 3x funds tracked the fitted model with 2-3.5 points of noise of
    either sign; they gate through the handicap credited to the fund rival, not the tolerance."""
    pairs = (("U", "F2", 2), ("U", "F3", 3))
    slow = lv.fund_check(
        _check_series(drag_in_design=-0.03, only="F3"), lambda d: 0.02, pairs=pairs, reported=()
    )
    assert slow["ok"]  # the 2x pair still tracks
    assert 0.025 < slow["handicap"] < 0.045  # the model fell about 3 points short of F3 (compounded)
    rich = lv.fund_check(
        _check_series(drag_in_design=0.03, only="F3"), lambda d: 0.02, pairs=pairs, reported=()
    )
    assert rich["ok"] and rich["handicap"] < 0.004  # the model ahead of a real fund credits nothing


def test_a_split_the_files_miss_is_left_out_of_the_fund_check_and_counted():
    days = i36.sessions_between(date(2016, 1, 4), date(2016, 3, 31))
    under = {d: 0.001 for d in days}
    real = {d: lv.fund_step(0.001, 0.0, 2, 0.0) for d in days}
    real[days[10]] = -0.5  # a 2:1 split with no split record
    g = lv.fund_gap(under, real, lambda d: 0.0, 2, 0.0, days[0], days[-1])
    assert g["split_like"] == 1 and g["gap"] == pytest.approx(0.0, abs=1e-9)


def test_the_handicap_clause_fails_an_arm_that_beats_the_fund_by_less():
    t = {"pooled": 0.02, "lower": 0.005, "p": 0.01, "ahead": 5, "n": 6}
    halves = {"2016-18": 0.02, "2019-21": 0.02}
    assert lv.design_verdict(t, halves, 0.01, 0.01, handicap=0.01)["ok"]
    out = lv.design_verdict(t, halves, 0.01, 0.01, handicap=0.03)
    assert not out["ok"] and not out["clauses"]["beats_handicap"]
    assert not lv.holdout_verdict(t, 0.01, handicap=0.03)["ok"]


def test_the_fit_recovers_a_planted_spread_and_the_check_passes():
    out = lv.fund_check(_check_series(), lambda d: 0.02, pairs=(("U", "F2", 2), ("U", "F3", 3)), reported=())
    assert out["spread"] == pytest.approx(0.004, abs=0.0005)
    assert out["ok"]


def test_the_fund_guard_fails_when_the_real_funds_drift_from_the_model_in_the_design_window():
    out = lv.fund_check(_check_series(drag_in_design=0.03), lambda d: 0.02,
                        pairs=(("U", "F2", 2), ("U", "F3", 3)), reported=())  # fmt: skip
    assert out["spread"] == pytest.approx(0.004, abs=0.0005)  # the fit window is untouched
    assert not out["ok"]


# ---------------------------------------------------------------- the replay


def test_the_call_enters_near_its_delta_and_its_leverage_is_recorded(run90):
    c = run90.cycles[0]
    assert c["delta"] == pytest.approx(0.90, abs=0.02)
    assert 2.0 < c["lev"] < 4.5
    assert c["implied_rate"] == pytest.approx(0.01, abs=0.003)  # the chains were priced at 1%
    assert run90.counts["entries"] >= 2 and run90.counts["closed_close_dte"] >= 1


def test_deeper_calls_carry_less_leverage(mkt):
    levs = {
        n: lv.simulate(mkt, lv.ARMS[n], spread=0.0).cycles[0]["lev"] for n in ("c90-45", "c50-45", "c30-45")
    }
    assert levs["c90-45"] < levs["c50-45"] < levs["c30-45"]


def test_every_rival_starts_with_the_calls_debit_and_shares_its_weeks(run90):
    wk = lv.weekly(run90)
    assert wk and all(set(r) == {"call", "fund", "financed", "shares"} for r in wk.values())
    first = run90.cycles[0]
    # the shares rival holds the call's debit in shares bought at entry: a week's return on that
    # capital is the stock's move over the entry price
    m = make_market()
    d0 = date.fromisoformat(first["opened"])
    ends = sorted(w for w in wk if w > d0)
    a, b = ends[0], ends[1]
    assert wk[b]["shares"] == pytest.approx((m.closes[b] - m.closes[a]) / m.closes[d0], rel=1e-6)


def test_on_a_flat_path_at_no_rate_the_rivals_earn_nothing_but_the_fund_pays_its_fee():
    m = make_market(path=flat, rate=0.0)
    r = lv.simulate(m, lv.ARMS["c90-45"], spread=0.0)
    wk = lv.weekly(r)
    first = r.cycles[0]
    mid_cycle = sorted(
        w for w in wk if date.fromisoformat(first["opened"]) < w < date.fromisoformat(first["closed"])
    )
    assert len(mid_cycle) > 10
    for w in mid_cycle[2:6]:
        assert wk[w]["shares"] == pytest.approx(0.0, abs=1e-12)
        assert wk[w]["financed"] == pytest.approx(0.0, abs=1e-12)
        assert -0.0095 / 252 * 6 < wk[w]["fund"] < 0  # a week of the fee, nothing else


def test_the_shares_rival_earns_the_dividend_and_the_call_does_not():
    ex = date(2016, 3, 15)
    with_div = make_market(path=flat, rate=0.0, dividends=[(ex, 1.0)], ex_dates=[ex])
    r = lv.simulate(with_div, lv.ARMS["c90-45"], spread=0.0)
    cap = r.cycles[0]["capital"]
    wk = lv.weekly(r)
    w = min(x for x in wk if x >= ex)
    assert wk[w]["shares"] == pytest.approx((cap / 100.0) * 1.0 / cap, rel=1e-6)  # its shares' dividend


def test_a_split_drops_its_cycle_and_excludes_only_its_weeks():
    split = date(2016, 6, 15)
    m = make_market(split_days=[split])
    r = lv.simulate(m, lv.ARMS["c90-45"], spread=0.0)
    assert r.counts["split_drops"] == 1
    dropped = [c for c in r.cycles if c.get("reason") == "split_drop"]
    assert dropped and dropped[0]["closed"] == split.isoformat()
    ex = lv.split_weeks(m, r)
    assert ex and max(ex) < date(2016, 7, 1) and min(ex) >= date(2016, 1, 1)
    assert not any(w in ex for w in lv.weekly(r, ex))


def test_the_accounting_guard_holds_and_fails_when_a_cost_is_forgotten(mkt, run90):
    assert lv.accounting_check(run90) == []
    broken = lv.simulate(mkt, lv.ARMS["c90-45"], spread=0.0, drop_cost="fees")
    assert lv.accounting_check(broken)


def test_one_and_a_half_times_costs_lowers_the_call_and_the_fund_only(mkt, run90):
    hi = lv.simulate(mkt, lv.ARMS["c90-45"], spread=0.0, scale=1.5)
    a, b = lv.weekly(run90), lv.weekly(hi)
    assert sum(r["call"] for r in b.values()) < sum(r["call"] for r in a.values())
    assert sum(r["financed"] for r in b.values()) == pytest.approx(sum(r["financed"] for r in a.values()))


def test_an_unlisted_expiry_is_a_data_refusal_and_its_weeks_are_missing():
    m = make_market(listed=lambda g, e: False)
    r = lv.simulate(m, lv.ARMS["c90-45"], spread=0.0)
    assert r.counts["refused_no_leap_listed"] > 0 and r.counts["entries"] == 0
    st = lv.judged_status(lv.ARMS["c90-45"], r, set(), "design", *lv.DESIGN)
    assert not st["judged"] and any("missing" in x for x in st["reasons"])


def test_a_wide_quote_is_refused_by_the_rule_not_counted_missing():
    m = make_market()
    base = m.quote_fn

    def wide(day, exp):
        return {k: (b * 0.5, a * 1.5) for k, (b, a) in base(day, exp).items()}

    w = i36.Market("QQQ", "QQQ", "physical", "QQQ", "fund", m.sessions, m.closes, wide, lambda d: 0.01)
    r = lv.simulate(w, lv.ARMS["c90-45"], spread=0.0)
    assert r.counts["refused_spread_too_wide"] > 0
    assert lv.missing(r, set(), *lv.DESIGN) == set()


# ---------------------------------------------------------------- the verdict


def test_every_design_clause_can_fail_on_its_own():
    t = {"pooled": 0.05, "lower": 0.01, "p": 0.01, "ahead": 4, "n": 6}
    halves = {"2016-18": 0.04, "2019-21": 0.06}
    assert lv.design_verdict(t, halves, 0.03, 0.02)["ok"]
    assert not lv.design_verdict({**t, "lower": -0.01}, halves, 0.03, 0.02)["ok"]
    assert not lv.design_verdict({**t, "ahead": 3}, halves, 0.03, 0.02)["ok"]  # 3 of 6 < two thirds
    assert not lv.design_verdict(t, {**halves, "2016-18": -0.01}, 0.03, 0.02)["ok"]
    assert not lv.design_verdict(t, halves, -0.01, 0.02)["ok"]
    assert not lv.design_verdict(t, halves, 0.03, -0.01)["ok"]
    assert not lv.design_verdict(t, halves, 0.03, None)["ok"]


def test_the_holdout_needs_pooled_two_thirds_and_stressed_costs():
    t = {"pooled": 0.02, "lower": -0.01, "p": 0.2, "ahead": 4, "n": 5}
    assert lv.holdout_verdict(t, 0.01)["ok"]
    assert not lv.holdout_verdict({**t, "ahead": 3}, 0.01)["ok"]
    assert not lv.holdout_verdict(t, -0.001)["ok"]


def test_the_bootstrap_bound_is_positive_for_a_planted_edge_and_not_for_noise():
    grid = [date(2016, 1, 8) + timedelta(days=7 * k) for k in range(200)]
    edge = {f"S{j}": {w: 0.002 + 0.001 * math.sin(k + j) for k, w in enumerate(grid)} for j in range(4)}
    noise = {f"S{j}": {w: 0.004 * math.sin(k * 1.3 + j) for k, w in enumerate(grid)} for j in range(4)}
    idx = i36.resample_indices(len(grid), resamples=2000, seed=lv.SEED)
    assert lv.arm_test(edge, idx, grid)["lower"] > 0
    assert lv.arm_test(noise, idx, grid)["lower"] < 0
