"""Item 109's script (book_caps_item109.py): invented chains and books only, the store never read."""

from __future__ import annotations

import copy
import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import bs
from cherrypick.core import calendar as cal

from cherrypick.backtest.study.chains import Chain

_spec = importlib.util.spec_from_file_location(
    "book_caps_item109", Path(__file__).resolve().parents[1] / "scripts" / "book_caps_item109.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

EXPIRY = "2024-07-19"


def chain(fwd: float, session: str, expiry: str = EXPIRY, vol: float = 0.20, half: float = 0.01) -> Chain:
    """A two-sided Black-76 chain at r = 0, strikes 1 apart from 60 to 140, a cent either side."""
    years = max(Chain([], session, expiry).years, 1 / 365)
    rows = []
    for k in range(60, 141):
        for r in ("C", "P"):
            p = bs.price(fwd, k, years, vol, r, 0.0) if session != expiry else bs.intrinsic(fwd, k, r)
            rows.append((float(k), "CALL" if r == "C" else "PUT", max(p - half, 0.0), max(p, 0.0) + half))
    return Chain(rows, session, expiry)


def sessions_between(a: str, b: str) -> list[str]:
    d, out = date.fromisoformat(a), []
    while d <= date.fromisoformat(b):
        if cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_the_shipped_values_are_the_declared_ones():
    got = m.shipped()
    assert got["spread_risk"] == 0.35 and got["theta"] == 0.003 and got["risk_pct"] == 0.02


def test_a_moved_manage_value_is_refused():
    rules = copy.deepcopy(m.manage_rules())
    rules["portfolio"]["theta"]["max_pct_nlv"] = 0.004
    with pytest.raises(SystemExit, match="theta"):
        m.shipped(rules)


def test_every_judged_arm_caps_something_and_all_is_the_shipped_set():
    assert all(m.ARMS[a] for a in m.JUDGED)
    assert m.ARMS["all"] == {
        "spread_risk": 0.35,
        "theta": 0.003,
        "stress": 0.20,
        "volexp": 0.10,
        "expiry": 0.20,
    }


def test_coverage_counts_a_monthly_only_when_every_session_45_to_20_days_out_is_stored():
    exps = ["2017-01-20", "2017-02-17"]
    full = {("AAA", e): set(m.coverage_days(e)) for e in exps}
    gap = dict(full)
    gap[("BBB", exps[0])] = set(m.coverage_days(exps[0])[1:])
    gap[("BBB", exps[1])] = set(m.coverage_days(exps[1]))
    cov = m.coverage(gap, exps)
    assert cov["AAA"] == 1.0 and cov["BBB"] == 0.5
    assert m.universe({"AAA": 0.95, "BBB": 0.5, "TQQQ": 1.0}) == ["AAA"]


def test_the_entry_is_the_first_session_at_45_days_and_none_when_the_path_starts_late():
    s = sessions_between("2024-06-01", EXPIRY)
    entry = m.entry_session(s, EXPIRY)
    assert m.kc.days_between(entry, EXPIRY) <= 45
    assert m.kc.days_between(s[s.index(entry) - 1], EXPIRY) > 45
    assert m.entry_session([x for x in s if m.kc.days_between(x, EXPIRY) < 30], EXPIRY) is None


def test_the_condor_sells_20_delta_and_picks_the_width_whose_credit_is_nearest_a_third():
    c = chain(100.0, "2024-06-04")
    built = m.condor(c)
    (sp, _), (lp, _), (sc, _), (lc, _) = built["legs"]
    assert sp["strike"] - lp["strike"] == lc["strike"] - sc["strike"] == built["width"]
    assert abs(abs(c.delta(sp)) - 0.20) < 0.05 and abs(c.delta(sc) - 0.20) < 0.05
    share = built["credit"] / 100.0 / built["width"]
    assert 0.15 <= share <= 0.45
    others = []
    for w in range(1, 11):
        lp2, lc2 = c.by["P"].get(sp["strike"] - w), c.by["C"].get(sc["strike"] + w)
        if lp2 and lc2:
            cr = m._mid(sp) + m._mid(sc) - m._mid(lp2) - m._mid(lc2)
            others.append(abs(cr / w - 1 / 3))
    assert abs(share - 1 / 3) == pytest.approx(min(others), abs=1e-9)


def test_a_condor_whose_credit_falls_under_the_band_is_skipped():
    assert m.condor(chain(100.0, "2024-06-04", vol=0.02)) in (
        "credit outside band",
        "no width",
        "no short leg",
    )


def test_the_exit_rule_stops_first_then_targets_then_times_out():
    r = m.shipped()
    assert m.exit_rule(100.0, 300.0, 200.0, 40, r) == "stop"
    assert m.exit_rule(100.0, 300.0, 50.0, 40, r) == "target"
    assert m.exit_rule(100.0, 300.0, 120.0, 21, r) == "time"
    assert m.exit_rule(100.0, 300.0, 120.0, 30, r) is None
    assert m.exit_rule(100.0, 300.0, 200.0, 40, r, stop=False) is None
    # A credit over half the width: the stop is half of what is left to lose.
    assert (
        m.exit_rule(200.0, 300.0, 249.0, 40, r) is None and m.exit_rule(200.0, 300.0, 251.0, 40, r) == "stop"
    )


def test_a_quiet_path_takes_its_target_and_books_the_costs():
    s = [x for x in sessions_between("2024-06-04", EXPIRY)]
    path = [(x, chain(100.0, x)) for x in s]
    betas = {"beta": {x: 1.0 for x in s}, "beta3y": {x: 1.0 for x in s}}
    t = m.trade("SPY", EXPIRY, path, betas, m.shipped())
    assert t["exit_kind"] in ("target", "time")
    assert t["net"] == pytest.approx(t["credit"] - t["exit_cost"] - t["open_costs"] - t["close_costs"])
    first = t["series"][t["entry"]]
    assert first["theta"] > 0  # a short condor collects decay
    assert first["stress:beta"] < 0 and first["volexp:beta"] < 0
    assert max(t["series"]) <= t["exit"]


def test_beta_reads_the_returns_before_the_session_and_skips_a_split():
    days = sessions_between("2022-01-03", "2023-06-30")
    bench = {d: 100.0 * (1.0 + 0.001 * ((i % 7) - 3)) * (1 + 0.0001 * i) for i, d in enumerate(days)}
    name, level = [], 50.0
    for i, d in enumerate(days):
        if i:
            level *= 1 + 2 * (bench[d] / bench[days[i - 1]] - 1)
        name.append((d, level / (4 if i >= 300 else 1)))  # a 4-for-1 split on day 300
    rets = m.returns_against(name, bench)
    assert all(r > -0.45 for _, r, _ in rets)
    assert m.beta_at(rets, days[-1], 252) == pytest.approx(2.0, abs=1e-6)
    assert m.beta_at(rets, days[100], 252) is None


def cand(sym: str, entry: str, exit_: str, *, max_loss=300.0, net=50.0, stress=-200.0) -> dict:
    """An invented candidate: marked at its credit every session, so net liq moves only at its exit."""
    credit = 150.0
    row = {
        "mark": credit,
        "theta": 2.0,
        "remaining": max_loss,
        "stress:beta": stress,
        "volexp:beta": stress / 4,
    }
    return {
        "symbol": sym,
        "expiry": "2024-07-19",
        "entry": entry,
        "exit": exit_,
        "credit": credit,
        "width": 450.0,
        "max_loss": max_loss,
        "open_costs": 5.0,
        "close_costs": 5.0,
        "exit_cost": credit - net - 10.0,
        "net": net,
        "series": {s: dict(row) for s in sessions_between(entry, exit_)},
    }


def test_the_spread_risk_cap_admits_until_the_book_would_cross_it():
    cands = [cand(s, "2024-06-04", "2024-06-21") for s in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    r = m.shipped()
    free = m.book(copy.deepcopy(cands), {}, r)
    capped = m.book(copy.deepcopy(cands), {"spread_risk": 0.25}, r)
    # 2% of $100,000 is six contracts of $300, $1,800 held each: five trades (9%) fit under 25%,
    # and only two (3.6%) under 5%.
    assert len(free["taken"]) == 5 and len(capped["taken"]) == 5
    tight = m.book(copy.deepcopy(cands), {"spread_risk": 0.05}, r)
    assert len(tight["taken"]) == 2 and tight["binds"] == ["2024-06-04"]


def test_the_stress_cap_refuses_a_trade_that_would_lose_too_much():
    cands = [cand(s, "2024-06-04", "2024-06-21", stress=-1500.0) for s in ("AAA", "BBB")]
    out = m.book(cands, {"stress": 0.10}, m.shipped())
    # Six contracts lose $9,000 at the shock: one trade fits under $10,000, two do not.
    assert len(out["taken"]) == 1


def test_net_liq_ends_at_the_start_plus_every_trade_net():
    cands = [cand("AAA", "2024-06-04", "2024-06-21"), cand("BBB", "2024-06-24", "2024-07-12")]
    out = m.book(cands, {}, m.shipped())
    last = out["daily"][max(out["daily"])]["nlv"]
    assert last == pytest.approx(m.SPEC["start_nlv"] + sum(t["net"] for t in out["taken"]))


def test_identical_books_have_a_tail_ratio_of_one():
    weeks = [{"date": f"w{i:03}", "ret": ((i * 37) % 11 - 5) / 100.0, "risk": 0.3} for i in range(60)]
    c = m.compare(weeks, weeks, m.RULE, resamples=50)
    assert c["tail_ratio"] == pytest.approx(1.0)


def test_a_book_with_a_smaller_tail_per_risk_reads_under_one():
    base = [{"date": f"w{i:03}", "ret": ((i * 37) % 11 - 5) / 100.0, "risk": 0.3} for i in range(60)]
    arm = [{**w, "ret": w["ret"] * (0.5 if w["ret"] < 0 else 1.0)} for w in base]
    assert m.compare(arm, base, m.RULE, resamples=50)["tail_ratio"] < 1.0


def test_holm_adjusts_upward_and_keeps_order():
    adj = m.holm({"a": 0.01, "b": 0.02, "c": 0.5})
    assert adj["a"] == pytest.approx(0.03) and adj["b"] == pytest.approx(0.04) and adj["c"] == 0.5


def test_the_judge_holds_no_verdict_under_its_floors_and_reads_the_holdout_only_after_a_pass():
    good = {"tail_ratio": 0.5, "t": 4.0, "return_t": 0.0, "n": 500, "binds": ["a", "b", "c"]}
    thin = {**good, "n": 10}
    weak = {**good, "tail_ratio": 0.9}
    out = m.judge(
        {
            "x": {"design": good, "holdout": {**good, "tail_ratio": 0.95}},
            "y": {"design": thin, "holdout": good},
            "z": {"design": weak, "holdout": good},
        },
        m.RULE,
    )
    assert out["verdict"] == {"x": "pass", "y": "no_verdict", "z": "fail"}
    assert out["arms"]["z"]["parts"][1] == {"verdict": "not_read"}


def test_a_large_drag_on_return_fails_an_arm_whose_tail_passes():
    good = {"tail_ratio": 0.5, "t": 4.0, "return_t": -3.0, "n": 500, "binds": ["a", "b", "c"]}
    assert m.judge({"x": {"design": good, "holdout": good}}, m.RULE)["verdict"] == {"x": "fail"}


def test_the_chain_plans_smokes_fetches_then_studies():
    assert [s["step"] for s in m.CHAIN] == ["plan", "smoke", "fetch-chains", "study"]
