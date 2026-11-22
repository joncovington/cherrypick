"""Item 103's script (leaps_diagonal_item103.py): the year-long call with gated 45-day calls sold against it.
Invented chains and closes only, the store never read. Each rule is shown firing and failing, because a check
that cannot fire reads as coverage it is not."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "leaps_diagonal_item103.py"
_spec = importlib.util.spec_from_file_location("leaps_diagonal_item103", _PATH)
m = importlib.util.module_from_spec(_spec)
sys.modules["leaps_diagonal_item103"] = m  # its dataclasses look their module up by name
_spec.loader.exec_module(m)


# ------------------------------------------------------------------------------------------------
# Invented markets.


class FakeChain:
    """A chain whose calls carry their own delta: {strike: (bid, ask, delta)}."""

    def __init__(self, calls: dict, forward: float | None = None):
        self.forward = forward
        calls_by = {
            k: {"strike": k, "right": "C", "bid": b, "ask": a, "d": d} for k, (b, a, d) in calls.items()
        }
        self.by = {"C": calls_by, "P": {}}

    def delta(self, q):
        return q.get("d")


def weekdays(start: date, n: int) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


LONG_EXP = "2023-01-20"
SHORT_EXPS = ["2022-02-18", "2022-03-18", "2022-04-14", "2022-05-20", "2022-06-17", "2022-07-15",
              "2022-08-19", "2022-09-16", "2022-10-21", "2022-11-18"]  # fmt: skip


def long_chain(spot: float) -> FakeChain:
    # the 80 strike is the 0.80 call; deeper and shallower strikes beside it
    return FakeChain({70: (spot - 60, spot - 59.8, 0.88), 80: (spot - 75, spot - 74.8, 0.80),
                      90: (spot - 88, spot - 87.8, 0.70)}, forward=spot)  # fmt: skip


def short_chain(spot: float, settle: bool = False) -> FakeChain:
    if settle:  # the expiry session: intrinsic, two-sided
        return FakeChain({k: (max(spot - k, 0.0), max(spot - k, 0.0) + 0.05, 0.5) for k in (110, 120)}, spot)
    return FakeChain({110: (1.95, 2.05, 0.30), 120: (0.98, 1.02, 0.20)}, forward=spot)


def market(closes: dict[str, float] | None = None, ratio: float = 1.1, splits=(), sessions=None) -> m.Market:
    sessions = sessions or weekdays(date(2022, 1, 3), 240)
    closes = closes or {}
    px = {s: closes.get(s, 100.0) for s in sessions}
    settle_days = {max(x for x in sessions if x <= e) for e in SHORT_EXPS}

    def chain_at(session, exp):
        if exp == LONG_EXP:
            return long_chain(px[session])
        if exp in SHORT_EXPS and session <= exp:
            return short_chain(
                px[session], settle=session in settle_days and session == max(x for x in sessions if x <= exp)
            )
        return None

    return m.Market("TQQQ", sessions, px, chain_at, lambda s: [LONG_EXP], SHORT_EXPS + [LONG_EXP],
                    lambda s: ratio, lambda s: 0.04, split_days=list(splits))  # fmt: skip


# ------------------------------------------------------------------------------------------------
# The declaration as data.


def test_the_declared_rule_is_complete_and_parses_without_import():
    assert kit_rule.read_rule(_PATH) == m.RULE
    assert m.RULE["delta"] is True and m.RULE["splits"] == ["2024-01-01"]


def test_the_chain_probes_the_listing_then_plans_samples_smokes_fetches_and_studies():
    steps = [s["step"] for s in m.CHAIN]
    assert steps == ["listing", "fetch-listing", "plan", "fetch-sample", "smoke", "fetch-chains", "study"]
    assert m.CHAIN[3]["task_args"]["limit"] == 20


# ------------------------------------------------------------------------------------------------
# Expiries.


def test_the_long_is_the_listed_expiry_nearest_360_in_the_band_the_earlier_on_a_tie():
    assert m.pick_long_expiry("2022-01-03", ["2022-12-16", "2023-01-20", "2023-06-16"]) == "2022-12-16"
    assert m.pick_long_expiry("2022-01-03", ["2022-12-29", "2023-01-06"]) == "2022-12-29"  # 360 +-3: earlier
    assert m.pick_long_expiry("2022-01-03", ["2022-07-15", "2024-01-19"]) is None  # 193 and 746 days


def test_the_short_is_nearest_45_days_and_never_past_the_cap():
    exps = ["2022-02-11", "2022-02-18", "2022-03-04"]
    assert m.pick_short_expiry("2022-01-03", exps, "2023-01-01") == "2022-02-18"  # 46 days
    assert m.pick_short_expiry("2022-01-03", exps, "2022-02-15") == "2022-02-11"  # the cap removes 02-18
    assert m.pick_short_expiry("2022-01-03", exps, "2022-02-01") is None
    assert m.short_cap("2023-01-20") == "2022-12-05"  # 46 days before


def test_the_listing_probe_asks_about_every_expiry_a_day_of_its_month_can_pick():
    exps = ["2022-08-19", "2022-09-16", "2022-09-23", "2023-07-21", "2023-08-18"]
    got = m.listing_candidates("2022-01-03", exps)
    # 228 days out is too near, 592 too far (540 + 31); the 09-23 weekly is never a long
    assert got == ["2022-09-16", "2023-07-21"]


# ------------------------------------------------------------------------------------------------
# Quotes and strikes.


def test_the_width_bar_takes_fifteen_percent_of_the_mid_or_six_cents():
    assert m.width_ok({"bid": 1.00, "ask": 1.15})
    assert not m.width_ok({"bid": 1.00, "ask": 1.20})
    assert m.width_ok({"bid": 0.10, "ask": 0.16})  # 6 cents
    assert not m.width_ok({"bid": 0.0, "ask": 0.05})  # no bid
    assert not m.width_ok({"bid": 1.0, "ask": 0.9})  # crossed


def test_the_long_strike_is_nearest_080_the_higher_on_a_tie_and_never_a_wide_quote():
    ch = FakeChain({70: (30, 30.2, 0.85), 80: (22, 22.2, 0.80), 90: (15, 15.2, 0.75)})
    assert m.pick_long_strike(ch) == 80
    tie = FakeChain({70: (30, 30.2, 0.85), 90: (15, 15.2, 0.75)})
    assert m.pick_long_strike(tie) == 90
    wide = FakeChain({80: (20, 26, 0.80), 90: (15, 15.2, 0.75)})
    assert m.pick_long_strike(wide) == 90


def test_the_short_strike_is_above_the_long_and_the_forward_nearest_its_delta():
    ch = FakeChain({95: (6, 6.2, 0.45), 110: (2, 2.05, 0.30), 120: (1, 1.02, 0.20)}, forward=100)
    assert m.pick_short_strike(ch, 0.30, above=80) == 110
    assert m.pick_short_strike(ch, 0.20, above=80) == 120
    assert m.pick_short_strike(ch, 0.30, above=115) == 120  # never at or under the long's strike
    tie = FakeChain({110: (2, 2.05, 0.35), 120: (1, 1.02, 0.25)}, forward=100)
    assert m.pick_short_strike(tie, 0.30, above=80) == 110  # nearer the money on a tie


def test_the_gate_opens_only_with_vix_over_vix3m():
    assert m.gate_open(1.01) is True
    assert m.gate_open(1.0) is False
    assert m.gate_open(0.9) is False
    assert m.gate_open(None) is None


# ------------------------------------------------------------------------------------------------
# The replay.


def test_a_gated_arm_sells_only_while_the_gate_is_open_and_the_long_alone_sells_nothing():
    open_ = m.simulate(market(ratio=1.2), m.ARMS["inv30"])
    shut = m.simulate(market(ratio=0.9), m.ARMS["inv30"])
    ungated = m.simulate(market(ratio=0.9), m.ARMS["all30"])
    alone = m.simulate(market(ratio=1.2), m.ARMS["long"])
    assert open_.counts.get("shorts_sold", 0) > 0
    assert shut.counts.get("shorts_sold", 0) == 0 and shut.counts["gate_closed_sessions"] > 0
    assert ungated.counts.get("shorts_sold", 0) > 0
    assert alone.counts.get("shorts_sold", 0) == 0 and alone.counts["entries"] == 1


def test_one_short_at_a_time_each_held_to_its_expiry_before_the_next():
    run = m.simulate(market(), m.ARMS["inv30"])
    for a, b in zip(run.shorts, run.shorts[1:], strict=False):
        assert b["sold"] >= a["closed"]
        assert a["closed"] <= a["exp"]
    assert all(s["strike"] == 110 for s in run.shorts)
    assert all(s["exp"] <= m.short_cap(LONG_EXP) for s in run.shorts)


def test_a_short_under_its_strike_expires_and_one_over_it_is_bought_back():
    flat = m.simulate(market(), m.ARMS["inv30"])
    assert {s["how"] for s in flat.shorts if "how" in s} == {"expired"}
    sessions = weekdays(date(2022, 1, 3), 240)
    up = {s: 100.0 if s < "2022-02-10" else 125.0 for s in sessions}
    rally = m.simulate(market(closes=up), m.ARMS["inv30"])
    first = rally.shorts[0]
    assert first["how"] == "bought back"


def test_the_short_bought_back_over_its_strike_costs_its_intrinsic():
    sessions = weekdays(date(2022, 1, 3), 240)
    up = {s: 100.0 if s < "2022-02-10" else 125.0 for s in sessions}
    alone = m.simulate(market(closes=up), m.ARMS["long"])
    arm = m.simulate(market(closes=up), m.ARMS["inv30"])
    rows_a = {r["date"]: r["net"] for r in m.weekly_rows(arm, lambda s: 0.0)}
    rows_l = {r["date"]: r["net"] for r in m.weekly_rows(alone, lambda s: 0.0)}
    # over the first short's life the arm trails the long alone by about the intrinsic less the credit
    span = [d for d in rows_a if d <= "2022-02-18"]
    gap = sum(rows_a[d] - rows_l[d] for d in span)
    assert -1500 < gap < -1200  # 15 x 100 intrinsic less a $200 credit, less costs


def test_a_long_open_on_a_split_is_dropped_its_weeks_left_out_and_the_next_bought_that_day():
    sessions = weekdays(date(2022, 1, 3), 240)
    run = m.simulate(market(splits=[sessions[60]], sessions=sessions), m.ARMS["inv30"])
    assert run.counts["split_drops"] == 1 and run.counts["entries"] == 2
    assert run.cycles[0]["dropped"] and run.cycles[1]["opened"] == sessions[60]
    rows = m.weekly_rows(run, lambda s: 0.0)
    assert rows and not [r for r in rows if r["start"] < sessions[60]]  # the dropped cycle's weeks are gone
    assert m.week_ledger(run)["dropped"] > 0
    whole = m.weekly_rows(m.simulate(market(sessions=sessions), m.ARMS["inv30"]), lambda s: 0.0)
    assert [r for r in whole if r["start"] < sessions[60]]  # and without the split they are measured


def test_the_long_closes_45_days_out_and_the_next_is_bought_the_session_after():
    sessions = weekdays(date(2022, 1, 3), 270)
    run = m.simulate(market(sessions=sessions), m.ARMS["long"])
    closed = run.cycles[0]["closed"]
    assert m._cal(closed, LONG_EXP) <= 45 < m._cal(sessions[sessions.index(closed) - 1], LONG_EXP)


def test_a_week_with_an_unquoted_leg_is_counted_never_zero():
    base = market()
    real = base.chain_at

    def gappy(session, exp):
        return None if session == "2022-03-04" and exp == LONG_EXP else real(session, exp)

    base.chain_at = gappy
    run = m.simulate(base, m.ARMS["long"])
    led = m.week_ledger(run)
    assert led["unpriced"] == 2  # the week into it and the week out of it
    assert all(
        r["date"] != "2022-03-04" and r["start"] != "2022-03-04" for r in m.weekly_rows(run, lambda s: 0.0)
    )


def test_weekly_rows_carry_the_kits_fields_and_the_financed_delta():
    run = m.simulate(market(), m.ARMS["inv30"])
    rows = m.weekly_rows(run, lambda s: 0.04)
    r = rows[3]
    assert set(r) >= {"date", "net", "bp", "days", "rate", "financed", "name"}
    assert r["days"] == 7 and r["rate"] == 0.04
    assert r["financed"] == ck.financed_delta(100 * (0.80 - 0.30), 100.0, 100.0, r["bp"], 0.04, 7)


def test_the_paired_difference_is_the_arm_less_the_long_alone_on_the_same_weeks():
    a = [
        {"name": "TQQQ", "date": "2022-01-07", "net": 50.0},
        {"name": "TQQQ", "date": "2022-01-14", "net": 5.0},
    ]
    b = [{"name": "TQQQ", "date": "2022-01-07", "net": 20.0}]
    assert m.paired(a, b) == [{"name": "TQQQ", "date": "2022-01-07", "diff": 30.0}]


# ------------------------------------------------------------------------------------------------
# The verdict.


def _rows(n_design: int, n_hold: int, net, start="2020-01-10"):
    out, d = [], date.fromisoformat(start)
    names = m.NAMES
    i = 0
    while len(out) < n_design + n_hold:
        day = d.isoformat()
        if (len([r for r in out if r["date"] < "2024-01-01"]) >= n_design) and day < "2024-01-01":
            d = date(2024, 1, 5)
            continue
        if len([r for r in out if r["date"] < "2024-01-01"]) < n_design and day >= "2024-01-01":
            break
        for name in names:
            out.append({"name": name, "date": day, "net": net(i, name, day), "bp": 1000.0, "days": 7,
                        "rate": 0.0, "financed": 0.0})  # fmt: skip
            i += 1
        d += timedelta(days=7)
    return out


def _wiggle(i):
    return ((i * 37) % 11) - 5


def _block(arm_net, long_net, shorts=40, coverage=(), stress_net=None):
    # 200 design weeks a name from 2020-01-10 run into 2023, so both design halves hold weeks
    rows = {a: _rows(400, 200, arm_net) for a in m.JUDGED}
    base = _rows(400, 200, lambda i, n, d: long_net(i, n, d) + ((i * 13) % 7) - 3)
    pairs = {a: m.paired(rows[a], base) for a in m.JUDGED}
    st = _rows(400, 200, stress_net) if stress_net else None
    pairs_s = {a: m.paired(st, base) if st else pairs[a] for a in m.JUDGED}
    return m.judge(m.RULE, rows, pairs, pairs_s, dict.fromkeys(m.JUDGED, shorts), list(coverage))


def test_an_arm_that_beats_the_long_and_clears_the_kits_bars_passes_when_the_holdout_agrees():
    block = _block(lambda i, n, d: 50 + _wiggle(i), lambda i, n, d: 10 + _wiggle(i))
    assert block["verdict"] == dict.fromkeys(m.JUDGED, ck.PASS)
    json.dumps(block)  # the block is JSON


def test_an_arm_that_trails_the_long_fails_the_paired_test_and_leaves_the_holdout_unread():
    block = _block(lambda i, n, d: 50 + _wiggle(i), lambda i, n, d: 60 + _wiggle(i))
    arm = block["arms"]["inv30"]
    assert arm["verdict"] == ck.FAIL and "paired" in arm["parts"][0]["why"]
    assert arm["parts"][1]["verdict"] == kit_rule.NOT_READ


def test_a_diagonal_that_loses_fails_the_kits_bars_however_it_pairs():
    block = _block(lambda i, n, d: -5 + _wiggle(i), lambda i, n, d: -40 + _wiggle(i))
    assert block["arms"]["inv30"]["verdict"] == ck.FAIL
    assert "kit" in block["arms"]["inv30"]["parts"][0]["why"]


def test_too_few_short_calls_or_a_year_under_the_coverage_floor_is_no_verdict():
    good = (lambda i, n, d: 50 + _wiggle(i), lambda i, n, d: 10 + _wiggle(i))
    assert _block(*good, shorts=9)["verdict"]["inv30"] == ck.NO_VERDICT
    assert _block(*good, coverage=["SOXL 2020: no quotes"])["verdict"]["inv20"] == ck.NO_VERDICT


def test_an_arm_that_wins_on_one_name_only_fails():
    block = _block(lambda i, n, d: (80 if n == "TQQQ" else 5) + _wiggle(i), lambda i, n, d: 10 + _wiggle(i))
    assert block["arms"]["inv30"]["verdict"] == ck.FAIL
    assert block["arms"]["inv30"]["parts"][0]["extra"]["names"] is False


def test_an_arm_that_loses_in_one_design_half_fails():
    block = _block(
        lambda i, n, d: (90 if d < "2022-01-01" else 5) + _wiggle(i), lambda i, n, d: 10 + _wiggle(i)
    )
    assert block["arms"]["inv30"]["parts"][0]["extra"]["halves"] is False
    assert block["verdict"]["inv30"] == ck.FAIL


def test_an_arm_that_loses_at_the_stress_fails():
    block = _block(lambda i, n, d: 50 + _wiggle(i), lambda i, n, d: 10 + _wiggle(i),
                   stress_net=lambda i, n, d: 5 + _wiggle(i))  # fmt: skip
    assert block["arms"]["inv30"]["parts"][0]["extra"]["stress"] is False
    assert block["verdict"]["inv30"] == ck.FAIL


def test_a_passing_design_with_a_losing_holdout_fails():
    block = _block(lambda i, n, d: (50 if d < "2024-01-01" else -30) + _wiggle(i),
                   lambda i, n, d: 10 + _wiggle(i))  # fmt: skip
    assert block["arms"]["inv30"]["parts"][0]["verdict"] == ck.PASS
    assert block["verdict"]["inv30"] == ck.FAIL


# ------------------------------------------------------------------------------------------------
# The plan.


def test_the_skeleton_re_enters_the_session_after_a_close_and_on_a_split_day():
    sessions = weekdays(date(2022, 1, 3), 300)
    listed = lambda s: [LONG_EXP] if s < "2022-12-01" else ["2023-12-15"]  # noqa: E731
    sk = m.skeleton(sessions, listed, [sessions[50]])
    assert sk[0][2] == sessions[50] and sk[1][0] == sessions[50]
    i_close = sessions.index(sk[1][2])
    assert sk[2][0] == sessions[i_close + 1]


def test_the_plan_requests_each_long_through_its_close_and_each_short_to_its_expiry():
    sessions = weekdays(date(2022, 1, 3), 270)  # past the long's 45-day close
    reqs = m.path_requests("TQQQ", sessions, lambda s: [LONG_EXP], SHORT_EXPS + [LONG_EXP], [])
    by = {r["expiration"]: r for r in reqs}
    assert by[LONG_EXP]["start"] == sessions[0]
    assert m._cal(by[LONG_EXP]["through"], LONG_EXP) <= 45
    first = by["2022-02-18"]
    assert first["through"] == "2022-02-18" and first["start"] == sessions[0]
    assert all(r["symbol"] == "TQQQ" for r in reqs)
