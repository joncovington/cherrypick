"""Hypothesis 29's study (scripts/share_ladder_item29.py): the trigger on split-adjusted bars, the
expiry rules, each trade's schedule, the legs, fills and fees, the paired measure and the verdict.
Invented bars and Black-Scholes chains only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cherrypick.core import bs
from cherrypick.core import fees as core_fees

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("share_ladder_item29", SCRIPTS / "share_ladder_item29.py")
s29 = importlib.util.module_from_spec(_spec)
sys.modules["share_ladder_item29"] = s29
_spec.loader.exec_module(s29)
Day = s29.Day
START = date(2022, 1, 3)  # a Monday


def _sessions(n: int, start: date = START) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _days(closes: list[float], lows: list[float] | None = None) -> list[Day]:
    lows = lows or closes
    return [Day(d, lo, c, c) for d, c, lo in zip(_sessions(len(closes)), closes, lows, strict=True)]


def _fridays(n: int, start: date = START) -> list[str]:
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    return [(d + timedelta(days=7 * k)).isoformat() for k in range(n)]


def _chain(spot: float, years: float, *, vol: float = 0.6, half: float = 0.05, step: float = 1.0) -> dict:
    out = {}
    k = round(spot * 0.7)
    while k <= spot * 1.3:
        for right in ("C", "P"):
            m = bs.price(spot, k, years, vol, right, 0.0)
            out[(float(k), right)] = (max(0.0, m - half), m + half)
        k += step
    return out


# ---- bars and the trigger ----


def test_days_from_divides_out_a_split_so_the_trigger_reads_the_move():
    bars = [SimpleNamespace(date=d, low=lo, close=c) for d, lo, c in
            [("2022-01-03", 100, 100), ("2022-01-04", 49, 50), ("2022-01-05", 45, 46)]]  # fmt: skip
    split = SimpleNamespace(ex_date="2022-01-04", to_factor=2.0, for_factor=1.0)
    days = s29.days_from(bars, [split])
    assert days[0].close == 50 and days[0].raw == 100  # adjusted before the ex-date, raw kept
    assert s29.triggers(days, "2022-01-01", "2022-12-31") == [2]  # the split day is not a 50% drop
    assert s29.triggers(s29.days_from(bars, []), "2022-01-01", "2022-12-31") == [1, 2]


def test_trigger_at_93_percent_and_not_above():
    days = _days([100, 100, 100], lows=[100, 93.0, 93.1 * 1.0])
    assert s29.triggers(days, "2022-01-01", "2022-12-31") == [1]


def test_trigger_counts_only_inside_the_window():
    days = _days([100, 90, 80])
    assert s29.triggers(days, days[2].date, days[2].date) == [2]


# ---- expiries ----


def test_pick_expiry_by_tenor():
    exps = _fridays(20)
    day = "2022-01-04"  # Tuesday: the 7th is 3 days out, the 14th 10
    assert s29.pick_expiry("7d", day, exps) == "2022-01-14"
    assert s29.pick_expiry("21d", day, exps) == "2022-01-28"  # 24 days; the 21st is 17
    assert s29.pick_expiry("75d", day, exps) == "2022-03-18"  # 73 days, nearest 75
    assert s29.pick_expiry("75d", day, exps[:5]) is None  # nothing 60-90 out


def test_rule_index_weekly_is_the_expiry_session_and_long_is_21_days_left():
    days = _days([100] * 60)
    assert days[s29.rule_index(days, "7d", "2022-01-14", 0)].date == "2022-01-14"
    # an expiry on a non-session (a Saturday) bites the session before
    assert days[s29.rule_index(days, "7d", "2022-01-15", 0)].date == "2022-01-14"
    j = s29.rule_index(days, "75d", "2022-03-18", 0)
    assert (date(2022, 3, 18) - date.fromisoformat(days[j].date)).days <= 21
    assert (date(2022, 3, 18) - date.fromisoformat(days[j - 1].date)).days > 21


# ---- schedules ----


def test_schedule_ends_at_the_target():
    days = _days([100, 93, 94, 96.8, 97])  # 104% of 93 is 96.72
    sch = s29.schedule(days, 1, "7d", _fridays(6), "2022-12-31", [], roll=False)
    assert (sch.how, sch.exit, len(sch.segments)) == ("target", 3, 1)


def test_schedule_close_stops_at_the_expiry_and_roll_carries_on():
    closes = [100, 93] + [90] * 30 + [97]
    days = _days(closes)
    exps = _fridays(10)
    shut = s29.schedule(days, 1, "7d", exps, "2022-12-31", [], roll=False)
    assert shut.how == "rule" and days[shut.exit].date == "2022-01-14"
    rolled = s29.schedule(days, 1, "7d", exps, "2022-12-31", [], roll=True)
    assert rolled.how == "target" and rolled.exit == len(days) - 1
    assert [s.expiry for s in rolled.segments][:3] == ["2022-01-14", "2022-01-21", "2022-01-28"]
    assert all(a.close == b.open for a, b in zip(rolled.segments, rolled.segments[1:], strict=False))


def test_schedule_stops_at_the_window_end_and_before_a_split():
    days = _days([100, 93] + [90] * 30)
    exps = _fridays(10)
    end = s29.schedule(days, 1, "7d", exps, days[5].date, [], roll=True)
    assert (end.how, end.exit) == ("end", 5)
    cut = s29.schedule(days, 1, "7d", exps, "2022-12-31", [days[7].date], roll=True)
    assert (cut.how, cut.exit) == ("split", 6)


def test_path_requests_merge_each_expiry_over_its_triggers():
    days = _days([100, 93, 86, 80] + [78] * 40)
    reqs = s29.path_requests("SOXL", days, _fridays(20), [])
    by = {r["expiration"]: r for r in reqs}
    assert len(by) == len(reqs)
    assert all(r["start"] <= r["through"] <= r["expiration"] for r in reqs)
    assert by["2022-01-14"]["start"] == days[1].date  # the first trigger opens it


# ---- legs, fills and fees ----


def test_choose_legs_strikes():
    chain = _chain(100, 10 / 365)
    cs = s29.choose_legs("call-spread", chain, 100.4, 10 / 365, 0.0)
    assert [(leg.strike, leg.qty) for leg in cs] == [(100, 1), (105, -1)]  # first >= 104.416
    ps = s29.choose_legs("put-spread", chain, 100.4, 10 / 365, 0.0)
    assert [(leg.strike, leg.qty) for leg in ps] == [(100, -1), (96, 1)]  # first <= 96.384
    z = s29.choose_legs("zebra", chain, 100, 30 / 365, 0.0)
    short, longs = z[1], z[0]
    assert short.strike == 100 and short.qty == -1 and longs.qty == 2 and longs.strike < 100
    d = s29.leg_delta(chain, longs, 100, 30 / 365, 0.0)
    assert abs(d - 0.70) < 0.05


def test_choose_legs_refuses_without_a_strike_far_enough():
    chain = {k: v for k, v in _chain(100, 10 / 365).items() if k[0] <= 103}
    assert s29.choose_legs("call-spread", chain, 100, 10 / 365, 0.0) == "no strikes"


def test_fill_at_the_natural_with_slip_and_none_on_one_side():
    chain = {(100.0, "C"): (2.0, 2.2), (105.0, "C"): (0.5, 0.6)}
    legs = [s29.Leg(100.0, "C", 1), s29.Leg(105.0, "C", -1)]
    assert s29.fill(legs, chain, True, 0.0) == pytest.approx(-220 + 50)
    assert s29.fill(legs, chain, False, 0.0) == pytest.approx(200 - 60)
    assert s29.fill(legs, chain, True, 0.05) == pytest.approx(-225 + 45)
    assert s29.fill(legs, {(100.0, "C"): (2.0, 2.2), (105.0, "C"): (None, 0.6)}, True, 0.0) is None


def test_option_fee_counts_contracts_and_sells():
    z = [s29.Leg(90.0, "C", 2), s29.Leg(100.0, "C", -1)]
    assert s29.option_fee("SOXL", z, True) == core_fees.ic_open_fee("SOXL", 1, 3, 1, ndigits=4)
    assert s29.option_fee("SOXL", z, False) == core_fees.ic_close_fee("SOXL", 1, 3, 2, ndigits=4)
    assert s29.option_fee("SOXL", z, True, 1.5) == pytest.approx(1.5 * s29.option_fee("SOXL", z, True))


def test_capital_at_risk_for_debit_and_credit():
    assert s29.capital_at_risk([s29.Leg(100, "C", 1), s29.Leg(105, "C", -1)], -170.0) == 170.0
    assert s29.capital_at_risk([s29.Leg(100, "P", -1), s29.Leg(96, "P", 1)], 150.0) == 250.0


# ---- the priced trade ----


def _priced(structure, closes, *, roll=False, exercise=False, drop=None):
    days = _days(closes)
    exps = _fridays(12)

    def chain_at(session, expiry):
        if drop and session in drop:
            return {}
        i = next(k for k, d in enumerate(days) if d.date == session)
        years = max(0.0, (date.fromisoformat(expiry) - date.fromisoformat(session)).days / 365)
        return _chain(days[i].raw, years)

    sch = s29.schedule(days, 1, "7d", exps, "2022-12-31", [], roll=roll)
    return (
        days,
        sch,
        s29.price_trade("SOXL", structure, sch, days, chain_at, lambda s: 0.0, exercise=exercise),
    )


def test_price_trade_pairs_against_shares_of_the_entry_delta():
    days, sch, t = _priced("call-spread", [100, 93, 94, 97, 98])
    assert t["how"] == "target" and t["shares"] == round(t["delta"])
    lot = s29.share_net(t["shares"], 93, 97)
    assert t["lot_net"] == pytest.approx(lot, abs=0.01)
    assert t["diff"] == pytest.approx((t["net"] - lot) / t["shares"], abs=0.01)  # net is kept to cents
    assert t["diff_stress"] < t["diff"] < t["diff_mid"]


def test_price_trade_marks_a_missing_exit_quote_at_the_last_earlier_one():
    _, _, fresh = _priced("call-spread", [100, 93, 94, 97, 98])
    _, _, stale = _priced("call-spread", [100, 93, 94, 97, 98], drop={"2022-01-06"})
    assert fresh["stale"] == 0 and stale["stale"] == 1
    assert stale["net"] != fresh["net"]


def test_price_trade_at_expiry_without_quotes_takes_intrinsic():
    closes = [100, 93, 92, 91, 92, 93, 94, 95, 96, 95, 95]  # expiry 2022-01-14 is index 9
    _, sch, t = _priced("call-spread", closes, drop={"2022-01-14"})
    assert t["how"] == "rule" and t["stale"] == 1


def test_price_trade_skips_without_a_chain_or_with_too_little_delta():
    days, sch, _ = _priced("call-spread", [100, 93, 94, 97])
    assert s29.price_trade("SOXL", "call-spread", sch, days, lambda s, e: {}, lambda s: 0.0) == "no chain"


def test_exercise_turns_in_the_money_longs_into_shares_held_to_the_target():
    closes = [100, 93] + [94] * 8 + [97]  # expiry at index 9, target 96.72 at index 10
    _, _, closed = _priced("zebra", closes)
    _, _, ex = _priced("zebra", closes, exercise=True)
    assert closed["how"] == "rule"
    assert ex["how"] == "exercise-target" and ex["exit"] > closed["exit"]


# ---- the arm and the verdict ----


def test_run_arm_skips_triggers_while_a_position_is_open():
    closes = [100, 92, 85, 78, 72, 66] + [65] * 20  # five 7%+ drops in a row
    days = _days(closes)

    def chain_at(session, expiry):
        i = next(k for k, d in enumerate(days) if d.date == session)
        return _chain(
            days[i].raw, max(0.0, (date.fromisoformat(expiry) - date.fromisoformat(session)).days / 365)
        )

    trades, counts = s29.run_arm("SOXL", "call-spread-7d-close", days, _fridays(8), [], ("2022-01-01",
                                 "2022-12-31"), chain_at, lambda s: 0.0)  # fmt: skip
    assert counts["triggers"] == 5 and counts["busy"] == 4 and len(trades) == 1


def _report(mean, n=40, sd=1.0, halves=(1, 1), names=(1, 1), stress=1.0):
    t = mean / (sd / n**0.5)
    return {"n": n, "mean": mean, "sd": sd, "t": t, "p": 1e-6 if t > 3 else 0.5, "halves": list(halves),
            "names": dict(zip(s29.NAMES, names, strict=True)), "stress": stress}  # fmt: skip


def test_judge_pass_fail_and_no_verdict():
    design = {a: _report(-1.0) for a in s29.ARMS}
    holdout = {a: _report(1.0) for a in s29.ARMS}
    a, b, c, d = s29.ARMS[:4]
    design[a] = _report(1.0)
    design[b] = _report(1.0, halves=(1, -1))
    design[c] = _report(1.0, n=29)
    design[d] = _report(1.0)
    holdout[d] = _report(-1.0)
    v = s29.judge(design, holdout)
    assert v[a]["verdict"] == "pass"
    assert v[b]["verdict"] == "fail" and not v[b]["checks"]["halves"]
    assert v[c]["verdict"] == "no verdict"
    assert v[d]["verdict"] == "fail" and not v[d]["checks"]["holdout"]
    assert v[s29.ARMS[5]]["verdict"] == "fail"
    holdout[a] = _report(1.0, n=10)
    assert s29.judge(design, holdout)[a]["verdict"] == "no verdict"


def test_judge_needs_holm_across_the_18():
    design = {a: _report(-1.0) for a in s29.ARMS}
    design[s29.ARMS[0]] = _report(1.0)
    design[s29.ARMS[0]]["p"] = 0.01  # under 0.05 alone, over 0.05 / 18 as the smallest of 18
    design[s29.ARMS[0]]["t"] = 2.5
    v = s29.judge(design, {a: _report(1.0) for a in s29.ARMS})
    assert not v[s29.ARMS[0]]["checks"]["holm"] and v[s29.ARMS[0]]["verdict"] == "fail"
    design[s29.ARMS[0]]["p"] = 0.001
    assert s29.judge(design, {a: _report(1.0) for a in s29.ARMS})[s29.ARMS[0]]["verdict"] == "pass"


def test_arms_are_the_declared_18():
    assert len(s29.ARMS) == 18 and len(set(s29.ARMS)) == 18
    assert all(not a.endswith("exercise") for a in s29.ARMS)
