"""Item 104's script (soxl_turbo_item104.py): the put-spread-financed year-long call on the video's checklist.
Invented chains and closes only, the store never read. Each rule is shown firing and failing, because a check
that cannot fire reads as coverage it is not."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "soxl_turbo_item104.py"
_spec = importlib.util.spec_from_file_location("soxl_turbo_item104", _PATH)
m = importlib.util.module_from_spec(_spec)
sys.modules["soxl_turbo_item104"] = m
_spec.loader.exec_module(m)


class FakeChain:
    """Quotes carrying their own delta and implied vol: {right: {strike: (bid, ask, delta, iv)}}."""

    def __init__(self, legs: dict, forward: float | None):
        self.forward = forward
        self.by = {
            r: {
                k: {"strike": k, "right": r, "bid": b, "ask": a, "d": d, "v": v}
                for k, (b, a, d, v) in q.items()
            }
            for r, q in legs.items()
        }
        self.by.setdefault("C", {})
        self.by.setdefault("P", {})

    def delta(self, q):
        return q.get("d")

    def iv(self, q):
        return q.get("v")


def year_chain(spot: float = 100.0) -> FakeChain:
    return FakeChain(
        {
            "P": {
                60: (2.0, 2.1, -0.08, 1.3),
                70: (3.0, 3.1, -0.11, 1.3),
                80: (5.0, 5.1, -0.15, 1.3),
                100: (20.0, 20.4, -0.30, 1.2),
            },  # fmt: skip
            "C": {
                90: (30.0, 30.4, 0.62, 1.1),
                100: (25.0, 25.4, 0.51, 1.1),
                150: (12.0, 12.2, 0.31, 1.1),
                200: (6.0, 6.1, 0.20, 1.1),
            },  # fmt: skip
        },
        forward=spot,
    )


def weekdays(start: date, n: int) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


EXP = "2023-01-20"


def market(gate_rows=None, sessions=None, splits=()) -> m.Market:
    sessions = sessions or weekdays(date(2022, 1, 3), 280)
    closes = {s: 100.0 for s in sessions}
    rows = gate_rows if gate_rows is not None else {s: dict.fromkeys(("rv", "rv_iv", "stretch", "skew"), True)
                                                    for s in sessions}  # fmt: skip
    return m.Market("SOXL", sessions, closes, lambda d, e: year_chain() if e == EXP else None,
                    lambda d: [EXP], rows, split_days=list(splits))  # fmt: skip


# ------------------------------------------------------------------------------------------------
# The declaration as data.


def test_the_declared_rule_is_complete_and_parses_without_import():
    assert kit_rule.read_rule(_PATH) == m.RULE
    assert m.RULE["splits"] == ["2024-01-01"] and m.RULE["delta"] is True


def test_the_chain_probes_plans_samples_smokes_fetches_and_studies():
    assert [s["step"] for s in m.CHAIN] == [
        "listing", "fetch-listing", "plan", "fetch-sample", "smoke", "fetch-chains", "study"
    ]  # fmt: skip


def test_four_arms_are_judged_and_the_ungated_two_reported():
    assert set(m.JUDGED) | set(m.REPORTED) == set(m.ARMS)
    assert all(m.ARMS[a][1] != "any" for a in m.JUDGED)
    assert m.GATES["partial"] == ("stretch", "skew") and len(m.GATES["full"]) == 4


# ------------------------------------------------------------------------------------------------
# The checklist.


def _days(n: int) -> list[str]:
    return weekdays(date(2019, 1, 2), n)


def test_realised_vol_is_annualised_and_none_without_enough_closes():
    days = _days(30)
    adj = {d: 100.0 * (1.01 if i % 2 else 1.0) for i, d in enumerate(days)}
    rv = m.rv_series(days, adj, 20)
    assert rv[days[19]] is None and rv[days[20]] is not None
    assert 0.15 < rv[days[25]] < 0.17  # alternating 1% moves: about 1% a day x sqrt(252)


def test_the_stretch_reads_the_close_against_its_200_session_average():
    days = _days(260)
    adj = {d: 100.0 if i < 230 else 60.0 for i, d in enumerate(days)}
    gap = m.sma_gap(days, adj, 200)
    assert gap[days[198]] is None
    assert gap[days[240]] < -0.30
    rows = m.checklist(days, adj, {}, {})
    assert rows[days[240]]["stretch"] is True and rows[days[100 + 120]]["stretch"] is False


def test_realised_vol_under_its_median_fires_only_after_the_storm_calms():
    days = _days(320)
    adj, px = {}, 100.0
    for i, d in enumerate(days):
        move = 0.01 + 0.0003 * i if i < 200 else 0.002  # a storm that builds, then a calm
        px *= 1 + move if i % 2 else 1 - move
        adj[d] = px
    rows = m.checklist(days, adj, {}, {})
    assert rows[days[150]]["rv"] is False  # still stormy: not under the year's median
    assert rows[days[300]]["rv"] is True


def test_realised_under_implied_compares_the_45_day_atm_vol():
    days = _days(40)
    adj = {d: 100.0 * (1.01 if i % 2 else 1.0) for i, d in enumerate(days)}
    rows = m.checklist(days, adj, {days[30]: 0.30, days[31]: 0.10}, {})
    assert rows[days[30]]["rv_iv"] is True and rows[days[31]]["rv_iv"] is False
    assert rows[days[32]]["rv_iv"] is None  # no implied vol read


def test_skew_must_top_its_prior_years_90th_percentile_with_enough_readings():
    days = _days(300)
    adj = dict.fromkeys(days, 100.0)
    skews = {d: 0.05 + 0.0001 * (i % 50) for i, d in enumerate(days)}
    skews[days[290]] = 0.40
    rows = m.checklist(days, adj, {}, skews)
    assert rows[days[290]]["skew"] is True and rows[days[289]]["skew"] is False
    few = m.checklist(days, adj, {}, {d: v for d, v in skews.items() if d >= days[200]})
    assert few[days[290]]["skew"] is None  # 90 prior readings, under 126


def test_the_skew_reading_is_the_quarter_delta_put_less_the_call():
    ch = FakeChain({"P": {80: (5, 5.1, -0.25, 1.40), 90: (8, 8.1, -0.35, 1.30)},
                    "C": {110: (6, 6.1, 0.25, 1.05), 120: (4, 4.1, 0.18, 1.10)}}, forward=100)  # fmt: skip
    assert math.isclose(m.skew(ch, 0.25), 0.35)
    assert m.skew(FakeChain({"P": {}, "C": {}}, None), 0.25) is None


def test_the_atm_vol_is_the_mean_of_call_and_put_at_the_strike_nearest_the_forward():
    ch = FakeChain({"P": {100: (5, 5.1, -0.5, 0.80), 95: (3, 3.1, -0.4, 0.90)},
                    "C": {100: (5, 5.1, 0.5, 0.60), 95: (8, 8.1, 0.6, 0.70)}}, forward=99)  # fmt: skip
    assert math.isclose(m.atm_iv(ch), 0.70)


def test_a_gate_opens_on_all_its_items_and_stays_shut_on_any_false_or_unread():
    row = {"rv": False, "rv_iv": None, "stretch": True, "skew": True}
    assert m.gate_open(row, "partial") is True
    assert m.gate_open(row, "full") is False
    assert m.gate_open({"stretch": True, "skew": None}, "partial") is None
    assert m.gate_open({}, "any") is True


def test_the_percentile_interpolates():
    assert m.percentile([1, 2, 3, 4, 5], 0.9) == 4.6


# ------------------------------------------------------------------------------------------------
# The structure.


def test_the_spread_picks_each_leg_by_its_delta_and_the_convex_drops_the_short_call():
    ch = year_chain()
    assert m.pick_legs(ch, "spread") == {"sp": 80, "lp": 70, "lc": 100, "sc": 150}
    assert m.pick_legs(ch, "convex") == {"sp": 80, "lp": 70, "lc": 100}


def test_no_leg_on_a_wide_quote_and_no_structure_without_its_long_put():
    ch = year_chain()
    ch.by["P"][70]["ask"] = 4.0  # wide: the 60 put is the nearest 0.10 left
    assert m.pick_legs(ch, "spread")["lp"] == 60
    ch.by["P"] = {80: ch.by["P"][80]}
    assert m.pick_legs(ch, "spread") is None


def test_the_maximum_loss_is_the_put_spreads_width_less_the_entry_cash():
    assert m.max_loss({"sp": 80, "lp": 70}, 150.0) == 850.0  # a $150 credit
    assert m.max_loss({"sp": 80, "lp": 70}, -300.0) == 1300.0  # a $300 debit


# ------------------------------------------------------------------------------------------------
# The replay.


def test_a_shut_gate_opens_nothing_and_the_ungated_arm_opens_at_once():
    shut = {
        s: dict.fromkeys(("rv", "rv_iv", "stretch", "skew"), False) for s in weekdays(date(2022, 1, 3), 280)
    }
    assert m.simulate(market(gate_rows=shut), "spread-partial").counts.get("entries", 0) == 0
    run = m.simulate(market(gate_rows=shut), "spread-any")
    assert run.counts["entries"] >= 1 and run.cycles[0]["opened"] == "2022-01-03"


def test_a_position_closes_45_days_out_and_its_capital_is_its_max_loss():
    run = m.simulate(market(), "spread-full")
    c = run.cycles[0]
    assert m.i103._cal(c["closed"], EXP) <= 45
    assert c["capital"] == m.max_loss(c["strikes"], run.cycles and _entry_cash(c))


def _entry_cash(c):
    ch = year_chain()
    cash = 0.0
    for role, right, qty, _ in m.STRUCTURES["spread"]:
        q = ch.by[right][c["strikes"][role]]
        cash += -qty * 100 * m.i103.mid(q) - m.i103.slip(q, 1.0) - m.i103.open_fee("SOXL", qty < 0, 1.0)
    return cash


def test_a_flat_market_loses_about_the_costs_on_the_weekly_rows():
    run = m.simulate(market(), "convex-full")
    rows = m.i103.weekly_rows(run, lambda s: 0.0)
    assert rows and abs(sum(r["net"] for r in rows)) < 60  # unchanged marks: slippage and fees only
    assert all(r["bp"] > 0 for r in rows)


def test_a_split_drops_the_open_position_and_its_weeks():
    sessions = weekdays(date(2022, 1, 3), 280)
    run = m.simulate(market(sessions=sessions, splits=[sessions[40]]), "spread-any")
    assert run.counts["split_drops"] == 1 and run.cycles[0]["dropped"]
    assert not [r for r in m.i103.weekly_rows(run, lambda s: 0.0) if r["start"] < sessions[40]]


# ------------------------------------------------------------------------------------------------
# The verdict.


def _rows(net, n_design=300, n_hold=120):
    out, d, i = [], date(2020, 1, 10), 0
    while sum(r["date"] < "2024-01-01" for r in out) < n_design:
        for name in m.NAMES:
            out.append({"name": name, "date": d.isoformat(), "net": net(i, name, d.isoformat()), "bp": 1000.0,
                        "days": 7, "rate": 0.0, "financed": 0.0})  # fmt: skip
            i += 1
        d += timedelta(days=7)
    d = date(2024, 1, 5)
    while sum(r["date"] >= "2024-01-01" for r in out) < n_hold:
        for name in m.NAMES:
            out.append({"name": name, "date": d.isoformat(), "net": net(i, name, d.isoformat()), "bp": 1000.0,
                        "days": 7, "rate": 0.0, "financed": 0.0})  # fmt: skip
            i += 1
        d += timedelta(days=7)
    return out


def _w(i):
    return ((i * 37) % 11) - 5


def _block(net, entries=20, coverage=(), stress=None):
    rows = {a: _rows(net) for a in m.JUDGED}
    rows_s = {a: _rows(stress or net) for a in m.JUDGED}
    return m.judge(m.RULE, rows, rows_s, dict.fromkeys(m.JUDGED, entries), list(coverage))


def test_an_arm_that_earns_on_every_bar_passes_when_the_holdout_agrees():
    block = _block(lambda i, n, d: 30 + _w(i))
    assert block["verdict"] == dict.fromkeys(m.JUDGED, ck.PASS)
    json.dumps(block)


def test_a_losing_arm_fails_and_leaves_the_holdout_unread():
    block = _block(lambda i, n, d: -10 + _w(i))
    arm = block["arms"]["spread-full"]
    assert arm["verdict"] == ck.FAIL and arm["parts"][1]["verdict"] == kit_rule.NOT_READ


def test_too_few_positions_or_a_year_under_the_floor_is_no_verdict():
    good = lambda i, n, d: 30 + _w(i)  # noqa: E731
    assert _block(good, entries=7)["verdict"]["convex-partial"] == ck.NO_VERDICT
    assert _block(good, coverage=["TQQQ 2020: no quotes"])["verdict"]["spread-partial"] == ck.NO_VERDICT


def test_an_arm_that_earns_on_one_name_or_one_half_or_not_under_stress_fails():
    one_name = _block(lambda i, n, d: (70 if n == "SOXL" else -20) + _w(i))
    assert one_name["arms"]["spread-full"]["parts"][0]["extra"]["names"] is False
    one_half = _block(lambda i, n, d: (70 if d < "2022-01-01" else -20) + _w(i))
    assert one_half["arms"]["spread-full"]["parts"][0]["extra"]["halves"] is False
    stressed = _block(lambda i, n, d: 30 + _w(i), stress=lambda i, n, d: -5 + _w(i))
    assert stressed["verdict"]["convex-full"] == ck.FAIL
    assert stressed["arms"]["convex-full"]["parts"][0]["extra"]["stress"] is False


def test_a_passing_design_with_a_losing_holdout_fails():
    block = _block(lambda i, n, d: (30 if d < "2024-01-01" else -30) + _w(i))
    assert block["arms"]["spread-partial"]["parts"][0]["verdict"] == ck.PASS
    assert block["verdict"]["spread-partial"] == ck.FAIL


# ------------------------------------------------------------------------------------------------
# The plan.


def test_the_plan_requests_each_sessions_year_long_monthly_to_its_close_and_its_45_day_expiry():
    sessions = weekdays(date(2022, 1, 3), 280)
    reqs = m.path_requests("SOXL", sessions, lambda d: [EXP], ["2022-02-18", "2022-03-18", EXP])
    by = {r["expiration"]: r for r in reqs}
    assert by[EXP]["start"] == sessions[0] and m.i103._cal(by[EXP]["through"], EXP) <= 45
    assert by["2022-02-18"]["start"] == sessions[0]
    assert by["2022-02-18"]["through"] < "2022-02-18"  # read only while it is the 45-day expiry
