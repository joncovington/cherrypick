"""Hypothesis 36's study (scripts/pmcc_item36.py): pmcc's own rule at real quotes, replayed here on
invented Black-Scholes chains only, no store. Every rule, arm and baseline is shown to fire, and each
guard is shown to fail by breaking its invariant.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cherrypick.pmcc")
pytest.importorskip("numpy")

from cherrypick.core import bs  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("pmcc_item36", SCRIPTS / "pmcc_item36.py")
s36 = importlib.util.module_from_spec(_spec)
sys.modules["pmcc_item36"] = s36
_spec.loader.exec_module(s36)

START, END = date(2016, 1, 4), date(2017, 2, 28)


def calm(i, d):
    return 100.0 * 1.0003**i * (1 + 0.004 * math.sin(i / 7))


def make_market(
    name="QQQ", *, path=calm, vol=0.20, style="physical", kind="fund", end=END, hook=None, **extra
):
    """A market whose chains are Black-Scholes at one vol around an invented close path."""
    days = s36.sessions_between(date(2015, 11, 2), end)
    closes = {d: path(i, d) for i, d in enumerate(days)}
    sessions = [d for d in days if d >= START]

    def quotes(day, exp):
        if day not in closes or exp < day:
            return {}
        s, t = closes[day], (exp - day).days / 365.0
        out = {}
        for k in range(int(s * 0.3), int(s * 1.8)):
            for right in ("C", "P"):
                mid = bs.price(s, float(k), t, vol, right, 0.01, 0.0)
                mid = max(mid, 0.02)
                half = max(0.01, 0.004 * mid)
                out[(float(k), right)] = (max(mid - half, 0.0), mid + half)
        return hook(day, exp, out) if hook else out

    return s36.Market(name, name, style, name, kind, sessions, closes, quotes, lambda d: 0.01, **extra)


@pytest.fixture(scope="module")
def mkt():
    return make_market()


@pytest.fixture(scope="module")
def runs(mkt):
    cache = {}

    def get(name, market=mkt, **kw):
        key = (id(market), name, tuple(sorted(kw.items())))
        if key not in cache:
            cache[key] = s36.simulate(market, s36.SPECS[name], **kw)
        return cache[key]

    return get


# ---------------------------------------------------------------- the calendar and the declarations


def test_week_ends_are_the_last_session_of_each_iso_week():
    sessions = s36.sessions_between(date(2016, 3, 21), date(2016, 4, 8))
    assert s36.week_ends(sessions) == [date(2016, 3, 24), date(2016, 4, 1), date(2016, 4, 8)]  # Good Friday


def test_the_long_is_the_standard_monthly_nearest_360_days():
    exp = s36.leap_for(date(2016, 1, 4))
    assert exp == date(2016, 12, 16) or exp == date(2017, 1, 20)
    assert 240 <= (exp - date(2016, 1, 4)).days <= 540
    assert exp == s36.clock.standard_monthly(exp.year, exp.month)


def test_long_cycles_close_at_45_dte_and_reenter_the_next_session():
    sessions = s36.sessions_between(date(2016, 1, 4), date(2018, 6, 29))
    cycles = s36.long_cycles(sessions)
    assert len(cycles) >= 2
    for (_, exp, close), nxt in zip(cycles, cycles[1:], strict=False):
        assert (exp - close).days <= 45 < (exp - sessions[sessions.index(close) - 1]).days
        assert nxt[0] == sessions[sessions.index(close) + 1]


def test_the_monthly_short_is_the_listed_expiry_nearest_37_days():
    friday = date(2016, 3, 18)
    assert s36.monthly_expiries(friday, None)[0] == friday + timedelta(days=35)
    assert all(30 <= (e - friday).days <= 45 for e in s36.monthly_expiries(friday, None))
    assert s36.monthly_expiries(friday, friday + timedelta(days=20)) == []  # never past the long


def test_the_gate_chain_is_the_friday_expiry_about_30_days_out():
    friday = date(2016, 3, 18)
    assert s36.gate_expiry(friday) == friday + timedelta(days=28)
    assert s36.gate_expiry(date(2016, 3, 14)) is not None


def test_each_arm_runs_pmcc_own_parameters():
    p = {n: s36.spec_params(s36.SPECS[n]) for n in s36.SPECS}
    assert p["shield"]["early_roll_decay"] == 0.85 and p["shield"]["breach_roll"]
    assert p["shield_hold"]["early_roll_decay"] is None and not p["shield_hold"]["breach_roll"]
    assert p["shield_guard"]["assignment_roll"] and not p["shield_hold"].get("assignment_roll")
    for n in ("shield", "shield_hold", "shield_guard", "long-alone"):
        assert (p[n]["long_delta_min"], p[n]["long_delta_max"]) == (0.88, 0.92)
        assert p[n]["stop_loss_frac"] == 0.30 and p[n]["account_policy"] == "ira"
        assert not p[n]["allow_extrinsic_fallback"]
    assert p["hold-30d"]["short_delta_target"] == 0.30 and p["hold-30d"]["short_delta_min"] == 0.25
    assert p["hold-30d-monthly"]["short_delta_max"] == 0.35
    assert p["long-alone-nostop"]["stop_loss_frac"] is None
    assert "short_delta_target" not in p["shield"]  # pmcc's own 0.70 default stands


# ---------------------------------------------------------------- the baselines


def test_long_alone_buys_the_88_to_92_delta_year_long_and_rolls_it_at_45_dte(mkt, runs):
    r = runs("long-alone")
    assert r.counts["entries"] >= 2 and r.counts["shorts_sold"] == 0
    assert r.counts["closed_long_roll_due"] >= 1
    day = date.fromisoformat(r.cycles[0]["day"])
    assert (s36.leap_for(date.fromisoformat(r.cycles[0]["opened"])) - day).days <= 45
    first = mkt.sessions[0]
    exp = s36.leap_for(first)
    g = mkt.greeks(first, exp, "C")
    chosen = [k for k, (d, _v) in g.items() if 0.88 <= d <= 0.92]
    assert chosen  # the band holds strikes, and pmcc's selector picked inside it
    assert s36.accounting_check(r) == []


def test_the_stop_closes_a_long_down_30_percent_and_the_nostop_rival_holds(mkt):
    def crash(i, d):
        return 100.0 if i < 60 else 62.0

    m = make_market(path=crash, end=date(2016, 9, 30))
    stopped = s36.simulate(m, s36.SPECS["long-alone"])
    rival = s36.simulate(m, s36.SPECS["long-alone-nostop"])
    assert stopped.counts["closed_stop_loss"] == 1
    assert rival.counts["closed_stop_loss"] == 0 and not rival.cycles


def test_the_put_benchmark_sells_a_20_delta_year_put_held_to_45_dte_with_no_stop(mkt, runs):
    r = runs("put-benchmark")
    assert r.counts["entries"] >= 2 and r.counts["closed_long_roll_due"] >= 1
    first = mkt.sessions[0]
    g = mkt.greeks(first, s36.leap_for(first), "P")
    near = min(abs(abs(d) - 0.20) for d, _v in g.values())
    assert near < 0.03

    def crash(i, d):
        return 100.0 if i < 60 else 55.0

    m = make_market(path=crash, end=date(2016, 9, 30))
    crashed = s36.simulate(m, s36.SPECS["put-benchmark"])
    assert not crashed.cycles and crashed.counts["closed_stop_loss"] == 0  # no stop on the put


def test_put_capital_is_the_cash_secured_strike(mkt, runs):
    r = runs("put-benchmark")
    held = [s for s in r.snaps if s["has_pos"]]
    assert held and all(s["cap"] > 5000 for s in held)  # a strike near 90 or below, in dollars


# ---------------------------------------------------------------- the arms


def test_shield_hold_sells_a_070_delta_weekly_short_above_the_long_and_rolls_it_on_expiry(mkt, runs):
    r = runs("shield_hold")
    assert r.counts["shorts_sold"] > 30 and r.counts["expiry_rolls"] > 25
    assert not [k for k in r.counts if k.startswith("early_rolls")]  # shield_hold holds to Friday
    sold = [s for s in r.shorts]
    assert all(date.fromisoformat(s["exp"]).weekday() in (3, 4) for s in sold)
    assert s36.accounting_check(r) == []


def test_the_weekly_short_delta_is_pmcc_own_band(mkt):
    day = mkt.sessions[10]
    exp = s36.clock.short_expiration(day, {}, cap=None)["short_expiration"]
    entries, quotes, greeks = mkt.view(day, date.fromisoformat(exp))
    pick = s36.engine.select_short_by_delta(entries, quotes, greeks, mkt.closes[day], {})
    assert pick["ok"] and 0.65 <= pick["delta"] <= 0.78 and abs(pick["delta"] - 0.70) < 0.04


def dip(i, d):
    return 100.0 if i < 70 else 95.5 + 0.05 * (i - 70)


def test_shield_rolls_early_on_a_breach_and_shield_hold_does_not():
    m = make_market(path=dip, end=date(2016, 6, 30))
    shield = s36.simulate(m, s36.SPECS["shield"])
    hold = s36.simulate(m, s36.SPECS["shield_hold"])
    assert shield.counts["early_rolls_breach"] >= 1
    assert not [k for k in hold.counts if k.startswith("early_rolls")]


def test_shield_rolls_early_when_85_percent_of_the_extrinsic_has_decayed():
    def rally(i, d):
        return 100.0 if i < 70 else 100.0 + 0.9 * (i - 70)

    m = make_market(path=rally, vol=0.10, end=date(2016, 6, 30))
    shield = s36.simulate(m, s36.SPECS["shield"])
    assert shield.counts["early_rolls_decayed"] >= 1


def rally6(i, d):
    return 100.0 if i < 70 else min(100.0 + 3.0 * (i - 70), 108.0)


def test_shield_guard_rolls_an_exposed_in_the_money_short_on_a_physical_symbol_only():
    phys = make_market(path=rally6, vol=0.08, end=date(2016, 6, 30))
    cash = make_market(path=rally6, vol=0.08, style="cash", end=date(2016, 6, 30))
    guard = s36.simulate(phys, s36.SPECS["shield_guard"])
    hold = s36.simulate(phys, s36.SPECS["shield_hold"])
    assert hold.counts["assignment_exposed_sessions"] >= 1  # measured by shield_hold, never acted on
    assert guard.counts["early_rolls_assignment_guard"] >= 1
    cg = s36.simulate(cash, s36.SPECS["shield_guard"])
    ch = s36.simulate(cash, s36.SPECS["shield_hold"])
    assert not [k for k in cg.counts if k.startswith("early_rolls")]
    assert [s["comps"] for s in cg.snaps if s["priced"]] == [s["comps"] for s in ch.snaps if s["priced"]]


def test_hold_30d_sells_a_025_to_035_delta_short_which_sits_out_of_the_money(mkt, runs):
    r = runs("hold-30d")
    assert r.counts["shorts_sold"] > 30
    day = mkt.sessions[10]
    exp = date.fromisoformat(s36.clock.short_expiration(day, {})["short_expiration"])
    entries, quotes, greeks = mkt.view(day, exp)
    pick = s36.engine.select_short_by_delta(
        entries, quotes, greeks, mkt.closes[day], s36.spec_params(s36.SPECS["hold-30d"])
    )
    assert 0.25 <= pick["delta"] <= 0.35 and pick["strike"] > mkt.closes[day]


def test_hold_30d_monthly_sells_one_short_a_cycle_held_to_its_expiry(mkt, runs):
    monthly, weekly = runs("hold-30d-monthly"), runs("hold-30d")
    assert 0 < monthly.counts["shorts_sold"] < weekly.counts["shorts_sold"] / 3
    assert monthly.counts["expiry_rolls"] >= 3
    for life in monthly.shorts:
        assert 30 <= (date.fromisoformat(life["exp"]) - date.fromisoformat(life["sold"])).days <= 45


def test_hold_vrp_sells_only_when_implied_is_above_realised_and_on_a_friday():
    quiet = make_market(vol=0.45, end=date(2016, 6, 30))  # implied well above the calm realised
    wild = make_market(path=lambda i, d: 100.0 * (1.03 if i % 2 else 0.97), vol=0.05, end=date(2016, 6, 30))
    open_ = s36.simulate(quiet, s36.SPECS["hold-vrp"])
    shut = s36.simulate(wild, s36.SPECS["hold-vrp"])
    assert open_.counts["shorts_sold"] > 5 and open_.counts["gate_closed_weeks"] == 0
    ends = {d.isoformat() for d in s36.week_ends(quiet.sessions)}
    assert all(x["sold"] in ends for x in open_.shorts)  # only on the week's last session
    assert shut.counts["shorts_sold"] == 0 and shut.counts["gate_closed_weeks"] > 5
    assert shut.counts["entries"] >= 1  # the long runs alone


def test_the_vrp_gate_is_strictly_greater_than_with_no_margin(mkt):
    day = [d for d in s36.week_ends(mkt.sessions) if d > date(2016, 3, 1)][0]
    s = mkt.closes[day]
    e = s36.gate_expiry(day)
    calls, puts = mkt.greeks(day, e, "C"), mkt.greeks(day, e, "P")
    both = sorted(set(calls) & set(puts))
    k = both[s36.nearest_strike_index(both, s)]
    iv = (calls[k][1] + puts[k][1]) / 2.0
    mkt.rv = lambda d, returns=20: iv  # equal
    try:
        assert s36.vrp_gate(mkt, day, s) is False
        mkt.rv = lambda d, returns=20: iv - 1e-6
        assert s36.vrp_gate(mkt, day, s) is True
        mkt.rv = lambda d, returns=20: None
        assert s36.vrp_gate(mkt, day, s) is None  # unmeasured is a refusal, counted
    finally:
        del mkt.rv


def test_realised_volatility_is_close_to_close_over_20_sessions(mkt):
    day = mkt.sessions[40]
    got = mkt.rv(day)
    days = [d for d in sorted(mkt.closes) if d <= day][-21:]
    rets = [math.log(mkt.closes[b] / mkt.closes[a]) for a, b in zip(days, days[1:], strict=False)]
    mean = sum(rets) / 20
    assert got == pytest.approx(math.sqrt(sum((r - mean) ** 2 for r in rets) / 19 * 252))
    assert mkt.rv(date(2015, 11, 3)) is None  # fewer than 21 closes


# ---------------------------------------------------------------- refusals


def test_a_short_across_an_ex_date_is_refused_on_a_physical_symbol_and_only_counted_on_a_cash_one():
    ex = [date(2016, 3, d) for d in range(1, 32) if date(2016, 3, d).weekday() == 2]
    phys = make_market(end=date(2016, 6, 30), ex_dates=ex)
    cash = make_market(end=date(2016, 6, 30), style="cash", ex_dates=ex)
    base = make_market(end=date(2016, 6, 30))
    p = s36.simulate(phys, s36.SPECS["shield_hold"])
    c = s36.simulate(cash, s36.SPECS["shield_hold"])
    b = s36.simulate(base, s36.SPECS["shield_hold"])
    assert p.counts["refused_ex_dividend"] >= 3 and p.counts["shorts_sold"] < b.counts["shorts_sold"]
    assert c.counts["refused_ex_dividend"] == 0 and c.counts["would_refuse_ex_date"] >= 3
    assert c.counts["shorts_sold"] == b.counts["shorts_sold"]


def test_a_stock_short_across_a_report_date_is_refused_and_an_etf_is_not():
    rep = [date(2016, 3, 10), date(2016, 3, 24)]
    stock = make_market("AMZN", kind="stock", end=date(2016, 6, 30), report_dates=rep)
    fund = make_market(end=date(2016, 6, 30), report_dates=rep)
    assert s36.simulate(stock, s36.SPECS["shield_hold"]).counts["refused_earnings_span"] >= 2
    assert s36.simulate(fund, s36.SPECS["shield_hold"]).counts["refused_earnings_span"] == 0


def test_the_monthly_arm_takes_the_nearest_expiry_that_clears_and_counts_when_none_does():
    ex = [date(2015, 12, 2) + timedelta(days=7 * k) for k in range(60)]  # an ex-date every week
    m = make_market(end=date(2016, 7, 30), ex_dates=ex)
    r = s36.simulate(m, s36.SPECS["hold-30d-monthly"])
    assert r.counts["refused_monthly_no_clear_expiry"] >= 1 and r.counts["shorts_sold"] == 0


def test_a_wide_leg_is_refused_as_pmcc_refuses_it():
    def wide(day, exp, quotes):
        return {
            k: (max(b - 0.4 * (a + b) / 2, 0.0), a + 0.4 * (a + b) / 2)
            if k[1] == "C" and (exp - day).days < 14
            else (b, a)
            for k, (b, a) in quotes.items()
        }

    m = make_market(end=date(2016, 4, 30), hook=wide)
    r = s36.simulate(m, s36.SPECS["shield_hold"])
    assert r.counts["shorts_sold"] == 0 and r.counts["refused_spread_too_wide"] >= 1


def test_an_arm_with_no_short_chain_is_a_counted_refusal():
    def none(day, exp, quotes):
        return {} if (exp - day).days < 14 else quotes

    m = make_market(end=date(2016, 4, 30), hook=none)
    r = s36.simulate(m, s36.SPECS["shield_hold"])
    assert r.counts["entries"] == 0 and sum(v for k, v in r.counts.items() if k.startswith("refused_")) > 5


def test_a_blocked_expiry_buyback_settles_at_intrinsic_with_the_5_dollar_event():
    blocked = {}

    def hook(day, exp, quotes):
        if exp == day and day == blocked.get("day"):
            return {k: (0.0, 1.0) if k[1] == "C" else v for k, v in quotes.items()}
        return quotes

    m = make_market(path=rally6, end=date(2016, 6, 30), hook=hook)
    probe = s36.simulate(m, s36.SPECS["shield_hold"])
    first = date.fromisoformat(probe.shorts[3]["exp"])
    blocked["day"] = first
    m2 = make_market(path=rally6, end=date(2016, 6, 30), hook=hook)
    r = s36.simulate(m2, s36.SPECS["shield_hold"])
    r15 = s36.simulate(m2, s36.SPECS["shield_hold"], scale=1.5)
    assert r.counts["buyback_blocked"] >= 1 and r.counts["expired_unbought"] >= 1
    last = [s for s in r.snaps if s["priced"]][-1]["comps"]["settlement"]
    last15 = [s for s in r15.snaps if s["priced"]][-1]["comps"]["settlement"]
    assert last == pytest.approx(-5.0 * r.counts["expired_unbought"])
    assert last15 == pytest.approx(1.5 * last)


def test_a_split_inside_a_cycle_drops_the_position_and_excludes_the_cycle_for_every_arm():
    split = date(2016, 6, 1)
    m = make_market(end=date(2016, 9, 30), split_days=[split])
    r = s36.simulate(m, s36.SPECS["shield_hold"])
    excl = s36.split_excluded_weeks(m.sessions, m.split_days)
    assert r.drops and r.drops[0]["day"] == split.isoformat()
    assert excl and all(e not in s36.measured_weeks(r, excl) for e in excl)
    assert s36.split_excluded_weeks(m.sessions, []) == set()


# ---------------------------------------------------------------- costs and the paired measures


def test_costs_scale_by_one_and_a_half(mkt):
    a = s36.simulate(mkt, s36.SPECS["long-alone"])
    b = s36.simulate(mkt, s36.SPECS["long-alone"], scale=1.5)
    ca, cb = a.snaps[-1]["comps"], b.snaps[-1]["comps"]
    assert cb["fees"] == pytest.approx(1.5 * ca["fees"]) and cb["slippage"] == pytest.approx(
        1.5 * ca["slippage"]
    )
    assert ca["slippage"] < 0 and ca["fees"] < 0  # each in its own column, negative


def test_a_weekly_return_is_the_marked_change_over_the_opening_capital(mkt, runs):
    r = runs("shield_hold")
    got = s36.measured_weeks(r)
    prev, cur = next(
        (p, c) for p, c in zip(r.snaps, r.snaps[1:], strict=False) if p["has_pos"] and c["end"] in got
    )
    change = sum(cur["comps"].values()) - sum(prev["comps"].values())
    assert got[cur["end"]]["r"] == pytest.approx(change / prev["cap"])
    assert sum(got[cur["end"]]["comps"].values()) == pytest.approx(got[cur["end"]]["r"])


def test_the_short_contribution_splits_into_premium_mark_fees_slippage_and_settlement(mkt, runs):
    arm, base = runs("shield_hold"), runs("long-alone")
    ca, cb = arm.snaps[-1]["comps"], base.snaps[-1]["comps"]
    assert ca["premium"] > 0 and cb["premium"] == 0.0
    assert ca["fees"] < cb["fees"] and ca["slippage"] < cb["slippage"]


def test_paired_differences_pool_equal_weight_across_symbols():
    d = date(2016, 1, 8)
    wk = lambda v: {"r": v}  # noqa: E731
    a = {d: wk(0.03), d + timedelta(7): wk(0.01)}
    b = {d: wk(0.01), d + timedelta(7): wk(0.01)}
    diff = s36.paired(a, b, date(2016, 1, 1), date(2016, 12, 31))
    assert diff == {d: pytest.approx(0.02), d + timedelta(7): pytest.approx(0.0)}
    per = {"A": {d: 0.02, d + timedelta(7): 0.0}, "B": {d: -0.01}}
    assert s36.pooled_mean(per) == pytest.approx((0.01 + -0.01) / 2)  # not the mean of the three weeks
    assert s36.paired(a, b, date(2016, 1, 1), d) == {d: pytest.approx(0.02)}


def test_funds_ahead_and_two_thirds_round_up():
    per = {"A": {date(2016, 1, 8): 0.1}, "B": {date(2016, 1, 8): 0.1}, "C": {date(2016, 1, 8): -0.1}}
    assert s36.funds_ahead(per) == (2, 3) and s36.two_thirds(2, 3)
    assert not s36.two_thirds(3, 6 + 1) and s36.two_thirds(5, 6) and not s36.two_thirds(0, 0)


def test_max_drawdown_is_peak_to_trough_of_the_cumulative_weekly_return():
    r = {date(2016, 1, 8) + timedelta(7 * i): x for i, x in enumerate([0.1, -0.05, -0.04, 0.02])}
    assert s36.max_drawdown(r) == pytest.approx(-0.09)
    assert s36.max_drawdown({}) == 0.0


def test_the_best_year_is_removed_per_baseline():
    per = {"A": {date(2016, 3, 4): 0.5, date(2017, 3, 3): -0.1, date(2017, 6, 2): -0.1}}
    assert s36.best_year(per) == 2016
    assert s36.pooled_mean(per) > 0 > s36.pooled_mean(s36.without_year(per, 2016))


def test_the_beta_adjusted_alpha_recovers_the_intercept():
    ws = [date(2016, 1, 8) + timedelta(7 * i) for i in range(60)]
    moves = {w: 0.01 * math.sin(i) for i, w in enumerate(ws)}
    diffs = {w: 0.001 + 0.5 * moves[w] for w in ws}
    assert s36.regression_alpha(diffs, moves) == pytest.approx(0.052)
    assert s36.regression_alpha({ws[0]: 0.0}, moves) is None


def grid_and_series(mu, n=300, sd=0.01, seed=1):
    import random

    rng = random.Random(seed)
    grid = [date(2016, 1, 1) + timedelta(7 * i) for i in range(n)]
    return grid, {s: {w: mu + rng.gauss(0, sd) for w in grid} for s in ("A", "B", "C")}


def test_the_bootstrap_separates_a_real_edge_from_noise_and_repeats_with_its_seed():
    grid, edge = grid_and_series(0.004)
    _, noise = grid_and_series(0.0, seed=2)
    idx = s36.resample_indices(len(grid), 800)
    e, z = s36.bootstrap_pooled(edge, grid, idx), s36.bootstrap_pooled(noise, grid, idx)
    assert e["lower"] > 0 and e["p"] < 0.01 and e["mean"] > 0
    assert z["lower"] < 0 and z["p"] > 0.05
    assert s36.bootstrap_pooled(edge, grid, idx) == e
    assert (s36.resample_indices(len(grid), 50) == s36.resample_indices(len(grid), 50)).all()
    assert s36.bootstrap_pooled({}, grid, idx)["mean"] is None


def test_the_pass_rule_has_six_clauses_and_each_one_alone_fails_the_arm():
    good = {
        "mean": 0.01, "lower": 0.002, "funds_ahead": (5, 6),
        "halves": {"a": 0.01, "b": 0.02}, "scaled_1_5": {"mean": 0.005, "funds_ahead": (4, 6)},
        "mean_without_best_year": 0.004,
    }  # fmt: skip
    assert all(s36.design_checks({"x": good})["x"].values())
    breaks = {
        "mean_positive": {"mean": -0.001},
        "lower_bound_positive": {"lower": -0.001},
        "two_thirds_of_funds": {"funds_ahead": (3, 6)},
        "each_half_positive": {"halves": {"a": 0.01, "b": -0.01}},
        "scaled_1_5_positive": {"scaled_1_5": {"mean": -0.001, "funds_ahead": (4, 6)}},
        "best_year_removed_positive": {"mean_without_best_year": -0.001},
    }
    for clause, change in breaks.items():
        checks = s36.design_checks({"x": {**good, **change}})["x"]
        assert not checks[clause] and sum(not v for v in checks.values()) >= 1
    one_bad = s36.design_checks({"x": good, "y": {**good, "mean": -1}})
    assert not all(all(c.values()) for c in one_bad.values())  # both baselines must hold
    holdout = {"mean": 0.01, "funds_ahead": (5, 6), "scaled_1_5_mean": 0.002, "windows_two_thirds": True}
    assert s36.holdout_passes({"x": holdout, "y": holdout})
    for change in (
        {"mean": -1},
        {"funds_ahead": (3, 6)},
        {"scaled_1_5_mean": -1},
        {"windows_two_thirds": False},
    ):
        assert not s36.holdout_passes({"x": holdout, "y": {**holdout, **change}})


def test_holm_across_six_arms_uses_the_larger_baseline_p():
    assert s36.arm_p({"x": {"p": 0.001}, "y": {"p": 0.03}}) == 0.03
    assert s36.arm_p({"x": {"p": 0.001}, "y": {"p": None}}) is None
    p = {"a": 0.001, "b": 0.008, "c": 0.02, "d": 0.2, "e": 0.5, "f": 0.9}
    got = s36.core_stats.holm(p, s36.ALPHA)
    assert got["a"] and got["b"] and not got["c"]  # 0.02 > 0.05 / 4
    only = s36.core_stats.holm({"a": 0.009, **{k: 0.9 for k in "bcdef"}}, s36.ALPHA)
    assert not only["a"]  # 0.009 > 0.05 / 6: six arms, not one


# ---------------------------------------------------------------- minimum data


def fake_run(name, weeks, cycles, lives=(), start=date(2016, 1, 1)):
    r = s36.Run("QQQ", name, 1.0)
    for i in range(weeks + 1):
        comps = dict.fromkeys(s36.COMPONENTS, float(i))
        r.snaps.append(
            {"end": start + timedelta(7 * i), "priced": True, "has_pos": True, "cap": 100.0, "comps": comps,
             "cash": 0.0, "marks": 0.0}
        )  # fmt: skip
    r.cycles = [
        {"day": (start + timedelta(40 * i)).isoformat(), "reason": "long_roll_due"} for i in range(cycles)
    ]
    r.shorts = list(lives)
    return r


def test_a_symbol_is_judged_only_above_every_floor():
    hi = date(2021, 12, 31)
    ok = s36.judged_status(
        fake_run("a", 130, 6), fake_run("b", 130, 6), set(), "design", date(2016, 1, 1), hi
    )
    assert ok["judged"]
    few_cycles = s36.judged_status(
        fake_run("a", 130, 3), fake_run("b", 130, 3), set(), "design", date(2016, 1, 1), hi
    )
    assert not few_cycles["judged"] and "long cycles" in few_cycles["reasons"][0]
    few_weeks = s36.judged_status(
        fake_run("a", 100, 6), fake_run("b", 100, 6), set(), "design", date(2016, 1, 1), hi
    )
    assert not few_weeks["judged"]
    missing = s36.judged_status(
        fake_run("a", 130, 6), fake_run("b", 200, 6), set(), "design", date(2016, 1, 1), hi
    )
    assert not missing["judged"] and any("missing" in x for x in missing["reasons"])  # 35% of weeks unpriced


def test_the_monthly_arm_is_judged_on_priced_cycles():
    lives = [
        {
            "id": i,
            "sold": (date(2016, 1, 1) + timedelta(35 * i)).isoformat(),
            "exp": "2020-01-01",
            "priced": True,
        }
        for i in range(30)
    ]
    arm, base = fake_run("m", 130, 6, lives), fake_run("b", 130, 6)
    assert s36.judged_status(arm, base, set(), "design", date(2016, 1, 1), date(2021, 12, 31), monthly=True)[
        "judged"
    ]
    arm.shorts = lives[:20]
    assert not s36.judged_status(
        arm, base, set(), "design", date(2016, 1, 1), date(2021, 12, 31), monthly=True
    )["judged"]


# ---------------------------------------------------------------- the guards, each shown to fail


def test_accounting_guard_rows_add_up_and_fails_when_a_cost_is_dropped(mkt):
    ok = s36.simulate(mkt, s36.SPECS["shield_hold"])
    assert s36.accounting_check(ok) == []
    broken = s36.simulate(mkt, s36.SPECS["shield_hold"], drop_cost="slippage")
    assert s36.accounting_check(broken)


def test_the_arm_is_long_alone_plus_the_short_and_the_guard_fails_when_it_is_not(mkt, runs):
    base = runs("long-alone")
    assert s36.identity_check(runs("shield_hold", no_short=True), base) == []
    assert s36.identity_check(runs("shield_hold"), base)  # with its short on, it differs


def test_coverage_guard_a_deleted_quote_is_a_counted_refusal_never_a_zero_return():
    gone = {}

    def hook(day, exp, quotes):
        if day == gone.get("day") and exp.year == 2016 and (exp - day).days > 100:
            return {k: v for k, v in quotes.items() if k[1] == "P"}
        return quotes

    m = make_market(end=date(2016, 9, 30), hook=hook)
    clean = s36.simulate(m, s36.SPECS["long-alone"])
    friday = [s["end"] for s in clean.snaps][12]
    gone["day"] = friday
    m2 = make_market(end=date(2016, 9, 30), hook=hook)
    r = s36.simulate(m2, s36.SPECS["long-alone"])
    assert r.counts["unpriced_weeks"] == 1 and s36.coverage_check(r) == []
    got = s36.measured_weeks(r)
    assert friday not in got and (friday + timedelta(7)) not in got
    ledger = s36.week_ledger(r, set(), date(2016, 1, 1), date(2016, 12, 31))
    assert ledger["unpriced"] == 2  # the week ending on it, and the week starting from it
    # break the invariant: a return read off an unpriced mark, and an uncounted hole
    orig = s36.measured_weeks
    s36.measured_weeks = lambda run, excluded=frozenset(): {
        sn["end"]: {"r": 0.0, "comps": {}} for sn in run.snaps
    }
    try:
        assert s36.coverage_check(r)
    finally:
        s36.measured_weeks = orig
    r.counts["unpriced_weeks"] = 0
    assert s36.coverage_check(r)


def test_reproduction_guard_matches_the_recorded_legs_and_fails_with_the_long_band_moved(mkt):
    markets = {"QQQ": mkt}
    params = s36.spec_params(s36.SPECS["shield_hold"])
    rows = []
    for day in mkt.sessions[5:60:6]:
        sexp = date.fromisoformat(s36.clock.short_expiration(day, params)["short_expiration"])
        rows.append({"symbol": "QQQ", "session": day.isoformat(), "long_exp": s36.leap_for(day).isoformat(),
                     "short_exp": sexp.isoformat()})  # fmt: skip
    probe = s36.select_for(markets, params)
    for r in rows:
        long_k, short_k = probe(r)
        r["long"], r["short"] = long_k, short_k
    good = s36.reproduction_check([dict(r) for r in rows], s36.select_for(markets, params))
    assert good["ok"] and good["ratio"] == 1.0
    moved = {**params, "long_delta_min": 0.80, "long_delta_max": 0.84}
    bad = s36.reproduction_check([dict(r) for r in rows], s36.select_for(markets, moved))
    assert not bad["ok"] and bad["ratio"] < 0.8
    assert not s36.reproduction_check([], probe)["ok"]  # nothing to compare is not a pass


def test_wput_guard_matches_a_put_write_index_and_fails_when_the_index_disagrees():
    m = make_market("XSP(SPY)", style="cash", end=date(2016, 12, 30))
    lo, hi = date(2016, 1, 4), date(2016, 12, 30)
    nav, index = 1.0, {}
    for d in s36.week_ends([s for s in m.sessions if lo <= s <= hi]):
        index[d] = nav
        e = s36.clock.weekly_expiration(d, 1)
        if e in m.closes:
            puts = sorted(k for (k, r), q in m.chain(d, e).items() if r == "P")
            k = puts[s36.nearest_strike_index(puts, m.closes[d])]
            q = m.quote(d, e, k, "P")
            nav *= 1 + (q["mid"] - max(k - m.closes[e], 0)) / k + 0.01 * (e - d).days / 365
    ok = s36.wput_check(m, index, lo, hi)
    assert ok["ok"] and abs(ok["diff"]) < 0.005
    first = min(index)
    flat = {d: 1.0 + 0.2 * (d - first).days / 365 for d in index}  # a 20% a year index
    bad = s36.wput_check(m, flat, lo, hi)
    assert not bad["ok"] and abs(bad["diff"]) > 0.02
    assert not s36.wput_check(m, {}, lo, hi)["ok"]
    thin = make_market(
        "XSP(SPY)", style="cash", end=date(2016, 12, 30), hook=lambda d, e, q: {} if d.month % 2 else q
    )
    assert not s36.wput_check(thin, index, lo, hi)["ok"]  # half the weeks have no chain


# ---------------------------------------------------------------- the plan


def test_the_request_set_has_the_four_kinds_and_the_declared_shape():
    reqs = s36.path_requests("QQQ")
    kinds = {k: [r for r in reqs if r["kind"] == k] for k in ("long", "weekly", "monthly", "gate")}
    assert all(kinds.values())
    assert 12 <= len(kinds["long"]) <= 20
    assert 540 <= len(kinds["weekly"]) <= 580
    assert 560 <= len(kinds["gate"]) <= 580 and all(r["start"] == r["through"] for r in kinds["gate"])
    for r in kinds["weekly"][5:20]:
        span = (date.fromisoformat(r["through"]) - date.fromisoformat(r["start"])).days
        assert span == 11 and s36.vendor_calls(r) == 2
    assert 100 <= len(kinds["monthly"]) <= 130
    assert sorted({s36.vendor_calls(r) for r in kinds["monthly"]})[-1] <= 5
    for r in kinds["long"][:3]:
        assert s36.vendor_calls(r) >= 40  # about 315 days in seven-day calls
    total = sum(map(s36.vendor_calls, reqs))
    assert 2700 <= total <= 3200  # the declaration's "about 2,900" gross
    assert len({(r["expiration"], r["start"], r["through"]) for r in reqs}) == len(reqs)
    assert {r["symbol"] for r in reqs} == {"QQQ"}


def test_xsp_is_requested_on_spy_and_pltr_only_from_its_listing():
    assert {r["symbol"] for r in s36.path_requests("XSP(SPY)")} == {"SPY"}
    pltr = s36.path_requests("PLTR")
    assert min(r["start"] for r in pltr) >= "2020-09-30"
    assert 1300 <= sum(map(s36.vendor_calls, pltr)) <= 1800  # about 1,560 gross


def test_the_store_is_subtracted_by_path_and_by_session_coverage():
    reqs = s36.path_requests("QQQ")
    weekly = [r for r in reqs if r["kind"] == "weekly"][30]
    by_path = s36.stored_filter(reqs, set(), [(weekly["expiration"], "2000-01-01", "2030-01-01")])
    assert weekly not in by_path and len(by_path) < len(reqs)
    sessions = s36.sessions_between(
        date.fromisoformat(weekly["start"]), date.fromisoformat(weekly["through"])
    )
    answered = {(d.isoformat(), weekly["expiration"]) for d in sessions}
    by_session = s36.stored_filter(reqs, answered, [])
    assert weekly not in by_session
    assert weekly in s36.stored_filter(reqs, set(list(answered)[:-1]), [])  # one session short: still asked


def test_plan_orders_spy_first_stocks_last_and_reports_counts(tmp_path, monkeypatch, capsys):
    from cherrypick.backtest import chainfetch

    db = tmp_path / "eod_chains.db"
    conn = sqlite3.connect(db)
    conn.executescript(chainfetch.SCHEMA)
    reqs = s36.path_requests("XSP(SPY)")
    one = [r for r in reqs if r["kind"] == "weekly"][100]
    conn.execute(
        "INSERT INTO eod_chain_paths VALUES (?, ?, ?, ?, 'complete', 8, 100, NULL, 0)",
        ("SPY", one["expiration"], one["start"], one["through"]),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(s36, "_chain_db", lambda: db)
    assert s36.main(["plan", "--out-dir", str(tmp_path / "out")]) == 0
    out = json.loads(capsys.readouterr().out)
    order = list(out["per_symbol"])
    assert order[0] == "XSP(SPY)" and order[-5:] == ["AMZN", "TSLA", "AMD", "NVDA", "PLTR"]
    spy = out["per_symbol"]["XSP(SPY)"]
    assert spy["paths"] - spy["paths_net"] == 1 and spy["calls"] - spy["calls_net"] == s36.vendor_calls(one)
    assert out["total"]["paths"] == sum(v["paths"] for v in out["per_symbol"].values())
    assert out["total"]["calls_net"] > 25_000
    written = json.loads((tmp_path / "out" / "chain_requests_SPY.json").read_text())
    assert len(written["requests"]) == spy["paths_net"] and one not in written["requests"]
    from cherrypick.backtest import chainfetch as cf

    assert len(cf.read_path_requests(tmp_path / "out" / "chain_requests_SPY.json")) == spy["paths_net"]


def test_the_sql_quote_reader_returns_bid_ask_by_strike_and_right(tmp_path):
    from cherrypick.backtest import chainfetch

    conn = sqlite3.connect(tmp_path / "c.db")
    conn.executescript(chainfetch.SCHEMA)
    insert = (
        "INSERT INTO eod_chain_quotes VALUES ('SPY', '2016-01-04', '2016-01-15', 200.0, ?, ?, ?, 1, 5, 1, 1)"
    )
    conn.execute(insert, ("call", 1.0, 1.2))
    conn.execute(insert, ("put", 0.5, 0.7))
    got = s36.sql_quote_fn(conn, "SPY")(date(2016, 1, 4), date(2016, 1, 15))
    assert got == {(200.0, "C"): (1.0, 1.2), (200.0, "P"): (0.5, 0.7)}
    assert s36.sql_quote_fn(conn, "SPY")(date(2016, 1, 5), date(2016, 1, 15)) == {}


# ---------------------------------------------------------------- the verdict and the written results


@pytest.fixture(scope="module")
def small_study():
    """analyse() end to end on three invented funds, with the windows and floors shrunk to the sample."""
    mp = pytest.MonkeyPatch()
    mp.setattr(s36, "DESIGN", (date(2016, 1, 1), date(2016, 9, 30)))
    mp.setattr(s36, "HOLDOUT", (date(2016, 10, 1), date(2017, 2, 28)))
    mp.setattr(
        s36,
        "HALVES",
        {"h1": (date(2016, 1, 1), date(2016, 5, 15)), "h2": (date(2016, 5, 16), date(2016, 9, 30))},
    )
    mp.setattr(s36, "WINDOWS_5Y", ((date(2016, 1, 1), date(2017, 2, 28)),))
    mp.setattr(s36, "MIN_CELL_WEEKS", 5)
    mp.setattr(s36, "MIN_WEEKS", {"design": 10, "holdout": 5})
    mp.setattr(s36, "MIN_CYCLES", 0)
    mp.setattr(s36, "MIN_CYCLES_MONTHLY", {"design": 2, "holdout": 0})
    mp.setattr(s36, "MAX_MISSING", 0.9)
    mp.setattr(s36, "MIN_JUDGED_FUNDS", 3)
    mp.setattr(s36, "RESAMPLES", 400)
    names = ["XSP(SPY)", "QQQ", "IWM"]
    markets = {
        n: make_market(
            n,
            style="cash" if n == "XSP(SPY)" else "physical",
            path=lambda i, d, k=k: calm(i, d) * (1 + 0.0005 * k * i / 100),
        )
        for k, n in enumerate(names)
    }
    runs = {
        (n, a, sc): s36.simulate(m, s36.SPECS[a], scale=sc)
        for n, m in markets.items()
        for a in (*s36.ARM_NAMES, *s36.BASELINES, *s36.REPORTED_ONLY)
        for sc in s36.COST_SCALES
    }
    yield s36.analyse(runs, markets, True), s36.analyse(runs, markets, False), runs, markets
    mp.undo()


def test_analyse_returns_a_word_per_arm_and_reads_the_holdout_only_for_passers(small_study):
    res, _, _, _ = small_study
    assert set(res["verdict"]) == set(s36.ARM_NAMES)
    assert set(res["verdict"].values()) <= {"PASS", "FAIL", "NO VERDICT"}
    assert set(res["holdout"]) == {a for a in s36.ARM_NAMES if res["design"][a]["passes"]}
    for d in res["design"].values():
        assert set(d["baselines"]) == set(s36.BASELINES)
        if d["checks"]:
            assert d["baselines"]["long-alone"]["lower"] is not None
            assert isinstance(d["holm_adjusted_p"], float)
    assert "shield_guard" in res["design"] and "XSP(SPY)" not in res["design"]["shield_guard"]["judged_funds"]


def test_a_failed_guard_makes_every_arm_no_verdict(small_study):
    _, bad, _, _ = small_study
    assert set(bad["verdict"].values()) == {"NO VERDICT"} and not bad["holdout"]


def test_fewer_than_four_judged_funds_is_no_verdict(small_study, monkeypatch):
    _, _, runs, markets = small_study
    monkeypatch.setattr(s36, "MIN_JUDGED_FUNDS", 4)
    res = s36.analyse(runs, markets, True)
    assert set(res["verdict"].values()) == {"NO VERDICT"}


def test_results_are_in_the_shape_the_board_reads_and_the_report_renders(small_study, tmp_path):
    res, _, runs, markets = small_study
    result = {
        "verdict": res["verdict"],
        "window": "design ..; holdout ..",
        "run_id": "item36-test",
        "guards": {k: {"ok": True} for k in ("wput", "reproduction", "accounting")} | {"ok": True},
        "symbols": list(markets),
        **{k: v for k, v in res.items() if k != "verdict"},
    }
    text = json.dumps(result, default=str)
    hyp = pytest.importorskip("cherrypick.orchestrator.hypotheses")
    words = hyp.verdicts(json.loads(text))
    assert {w["arm"] for w in words} == set(s36.ARM_NAMES)
    md = s36.report(json.loads(text))
    assert md.startswith("# Item 36") and "## Guards" in md and "## Per symbol, design window" in md
    assert "hold-vrp" in md and "put-benchmark" in md
    path = tmp_path / "results.json"
    path.write_text(text, encoding="utf-8")
    assert s36.main(["report", "--json", str(path)]) == 0
    assert path.with_suffix(".md").read_text(encoding="utf-8") == md


def test_the_rival_checks_are_reported_by_year_with_the_crash_and_between_the_30_delta_arms(small_study):
    r = small_study[0]["rivals"]
    assert 2016 in r["by_year"]["shield_hold vs long-alone"]
    assert set(r["crash"]["shield_hold vs put-benchmark"]) == {"with", "without"}
    assert set(r["pairs"]) == {"hold-30d vs hold-30d-monthly", "hold-vrp vs shield_hold"}


def test_tables_hold_the_reported_columns(small_study):
    res, _, _, _ = small_study
    t = res["tables"]
    cell = t["per_symbol"]["QQQ|shield_hold"]
    assert set(cell["components_a_year"]) == set(s36.COMPONENTS)
    assert cell["drawdown"] <= 0 and cell["weeks"] > 5
    assert t["per_symbol"]["QQQ|long-alone"]["alpha_vs_long_alone"] is None
    assert "measured" in t["ledger"]["QQQ|shield_hold"]
    assert t["counts"]["QQQ|shield_hold"]["shorts_sold"] > 0
