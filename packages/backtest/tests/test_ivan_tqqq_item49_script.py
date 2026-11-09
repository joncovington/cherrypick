"""Hypothesis 49's study (packages/backtest/scripts/ivan_tqqq_item49.py): the expiry, strike and tick rules,
the resting order's fill, management (take profit, defence and roll, expiry, splits), the chain's money, the
plan's missing spans and the verdict. Invented bars and Black-Scholes chains only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import bs
from cherrypick.core import fees as core_fees

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("ivan_tqqq_item49", SCRIPTS / "ivan_tqqq_item49.py")
s49 = importlib.util.module_from_spec(_spec)
sys.modules["ivan_tqqq_item49"] = s49
_spec.loader.exec_module(s49)
Day = s49.Day
START = date(2022, 1, 3)  # a Monday


def _sessions(n: int, start: date = START) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _days(closes, opens=None, lows=None, start: date = START) -> list[Day]:
    opens = opens or closes
    lows = lows or [min(o, c) for o, c in zip(opens, closes, strict=True)]
    return [
        Day(d, o, lo, c, c)
        for d, o, lo, c in zip(_sessions(len(closes), start), opens, lows, closes, strict=True)
    ]


def _chain(spot: float, years: float, *, vol: float = 0.6, half: float = 0.02) -> dict:
    out = {}
    for k in range(int(spot * 0.5), int(spot * 1.2) + 1):
        m = bs.price(spot, float(k), years, vol, "P", 0.0)
        out[(float(k), "P")] = (round(max(0.0, m - half), 4), round(m + half, 4))
    return out


def _fridays(n: int, start: date = START) -> list[str]:
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    return [(d + timedelta(days=7 * k)).isoformat() for k in range(n)]


# ---- expiries, rolls and ticks ----


def test_entry_expiry_is_nearest_31_days_inside_24_to_44():
    exps = ["2022-01-21", "2022-01-28", "2022-02-04", "2022-02-11", "2022-02-25"]
    assert s49.pick_expiry("2022-01-03", exps) == "2022-02-04"  # 32 days, nearest 31
    assert s49.pick_expiry("2022-01-03", ["2022-01-21", "2022-03-01"]) is None  # 18 and 57 days


def test_monthly_is_the_third_friday_or_the_thursday_before_a_holiday():
    assert s49.monthlies(["2022-01-14", "2022-01-21", "2022-01-28"]) == ["2022-01-21"]
    # 2022-04-15 was Good Friday: the Thursday is the monthly
    assert s49.monthlies(["2022-04-08", "2022-04-14", "2022-04-22"]) == ["2022-04-14"]
    assert s49.monthlies(["2022-01-14", "2022-01-28"]) == []


def test_roll_target_is_the_first_monthly_28_days_on_and_within_180():
    monthly = ["2022-02-18", "2022-03-18", "2022-04-14", "2022-05-20"]
    assert s49.roll_target("2022-02-18", "2022-02-10", monthly) == "2022-03-18"  # exactly 28 days on
    assert s49.roll_target("2022-02-25", "2022-02-10", monthly) == "2022-04-14"
    assert s49.roll_target("2022-02-25", "2021-10-01", monthly) is None  # over 180 days out


def test_limit_rounds_up_to_the_tick():
    assert s49.round_up("TQQQ", 0.94 * 1.20) == 1.13
    assert s49.round_up("TQQQ", 3.01) == 3.05  # TQQQ's nickel from $3
    assert s49.round_up("QQQ", 3.011) == 3.02  # QQQ pennies throughout
    assert s49.round_up("TQQQ", 1.10) == 1.10


# ---- strikes and the order ----


def test_short_is_the_put_nearest_16_delta_and_dist15_the_put_nearest_85_percent():
    chain = _chain(60.0, 31 / 365)
    k = s49.choose_short(s49.MARIA, chain, 60.0, 31 / 365)
    d = s49.put_delta(chain, k, 60.0, 31 / 365)[0]
    assert abs(d - 0.16) < 0.02 and k < 55
    assert s49.choose_short(replace(s49.MARIA, strike="dist"), chain, 60.0, 31 / 365) == 51.0


def test_spread_long_sits_about_10_percent_of_spot_under_the_short():
    chain = _chain(60.0, 31 / 365)
    legs = s49.legs_for(s49.REPORTED["spread"], chain, 60.0, 31 / 365)
    assert legs[1] == legs[0] - 6.0
    assert s49.collateral(legs) == 600.0
    assert s49.collateral((51.0, None)) == 5100.0


def _order(spot=60.0, dte=31, vol=0.6, arm=s49.MARIA):
    day = Day("2022-01-04", spot, spot, spot, spot)
    exp = (date(2022, 1, 4) + timedelta(days=dte)).isoformat()
    return s49.make_order("TQQQ", arm, 0, day, exp, _chain(spot, dte / 365, vol=vol))


def test_order_is_the_ask_times_the_cushion():
    o = _order()
    ask = _chain(60.0, 31 / 365)[(o.legs[0], "P")][1]
    assert o.limit == s49.round_up("TQQQ", ask * 1.20)
    assert _order(arm=s49.JUDGED["deep"]).limit == s49.round_up("TQQQ", ask * 1.40)


def test_a_gap_fills_at_the_open_above_the_limit():
    o = _order()
    nat, _, at_open = s49.fill_order(o, Day("2022-01-05", 57.0, 57.0, 57.5, 57.5), overnight=False)
    assert at_open and nat > o.limit


def test_an_intraday_fall_fills_at_the_limit_and_overnight_refuses_it():
    o = _order()
    day = Day("2022-01-05", 60.0, 57.0, 59.0, 59.0)  # opens flat, trades down to 57
    nat, m, at_open = s49.fill_order(o, day, overnight=False)
    assert not at_open and nat == o.limit and m > nat
    assert s49.fill_order(o, day, overnight=True) is None


def test_a_quiet_session_does_not_fill():
    o = _order()
    assert s49.fill_order(o, Day("2022-01-05", 60.2, 59.8, 60.1, 60.1), overnight=False) is None


# ---- the loop ----


class _Book:
    """Black-Scholes chains off the days' closes, at one vol."""

    def __init__(self, days, vol=0.6):
        self.spot = {d.date: d.close for d in days}
        self.vol = vol

    def __call__(self, session, expiry):
        years = max(s49._cal(session, expiry), 0) / 365
        if session not in self.spot:
            return {}
        return _chain(self.spot[session], max(years, 1e-6), vol=self.vol)


def _run(arm, days, expiries, split_dates=(), vol=0.6):
    w = (days[0].date, days[-1].date)
    return s49.run_arm("x", arm, days, expiries, list(split_dates), w, _Book(days, vol))


def test_no_order_rests_over_a_weekend_but_atclose_sells_on_a_friday():
    days = _days([60.0] * 7)  # Mon..Tue of the next week
    exps = _fridays(12)
    _, counts = _run(s49.MARIA, days, exps)
    assert counts.get("unfilled") == 5  # Mon-Thu and the next Mon: six sessions with room, no Friday
    sold, counts = _run(replace(s49.JUDGED["atclose"], cap=10), days, exps)
    assert counts["sold-close"] == 6  # every session but the last, Friday included
    assert any(date.fromisoformat(c["entry"]).weekday() == 4 for c in sold)


def test_cap_stops_new_chains():
    days = _days([60.0] * 12)
    exps = _fridays(12)
    _, counts = _run(replace(s49.JUDGED["atclose"], cap=2, tp=None, defence=-1.0), days, exps)
    assert counts["sold-close"] == 2


def test_take_profit_closes_once_the_ask_is_half_the_credit():
    closes = [60.0, 57.0, 66.0, 66.0, 66.0]
    opens = [60.0, 57.0, 66.0, 66.0, 66.0]
    days = _days(closes, opens)
    done, _ = _run(replace(s49.MARIA, cap=1), days, _fridays(12))
    assert done[0]["how"] == "tp" and done[0]["net"] > 0
    held, _ = _run(replace(s49.MARIA, cap=1, tp=None), days, _fridays(12))
    assert held[0]["how"] == "end"


def test_defence_rolls_down_and_out_for_a_credit_and_noroll_ends_the_chain():
    closes = [60.0, 57.0, 52.0, 52.0, 52.0]
    days = _days(closes, list(closes))
    exps = sorted(set(_fridays(30)) | set(s49.monthlies(_fridays(30))))
    rolled, _ = _run(replace(s49.MARIA, cap=1), days, exps)
    c = rolled[0]
    assert c["rolls"] >= 1 and c["legs_n"] >= 2
    ended, _ = _run(replace(s49.JUDGED["noroll"], cap=1), days, exps)
    assert ended[0]["how"] == "defence" and ended[0]["rolls"] == 0


def test_roll_takes_the_lowest_strike_whose_mid_pays_the_buyback():
    chain = _chain(52.0, 60 / 365, vol=0.8)
    paid = chain[(51.0, "P")][1] * 0.6
    legs, credit, m = s49.roll_legs(s49.MARIA, chain, 52.0, paid)
    k = legs[0]
    assert s49.mid(chain[(k, "P")]) >= paid > s49.mid(chain[(k - 1, "P")])
    assert credit == chain[(k, "P")][0]
    assert s49.roll_legs(s49.MARIA, chain, 52.0, 1e6) is None


def test_put_in_the_money_at_expiry_is_assigned_and_sold_at_the_next_open():
    exp = "2022-01-07"

    def state(k):
        return s49.ChainState(
            "x", "TQQQ", "2022-01-03", 0, (k, None), exp, 1.0, k * 100, False, leg_start=exp
        )

    c = state(55.0)
    c.leg_cash = 100.0
    days = _days([60.0, 58.0, 56.0, 54.0, 53.0, 52.0], opens=[60.0, 58.0, 56.0, 54.0, 53.0, 51.0])
    t = [d.date for d in days].index(exp)
    how = s49._expire(c, s49.MARIA, days, t)
    fee = core_fees.assignment_round_trip_fee(100, 55.0, 51.0, direction="long")
    assert how == "assigned" and c.cash["natural"] == pytest.approx(100 * (51.0 - 55.0) - fee)
    c2 = state(50.0)
    assert s49._expire(c2, s49.MARIA, days, t) == "expired" and c2.cash["natural"] == 0.0


def test_a_split_closes_the_chain_the_session_before_and_cancels_an_order():
    days = _days([60.0] * 6)
    exps = _fridays(12)
    done, _ = _run(replace(s49.JUDGED["atclose"], cap=1, tp=None, defence=-1.0), days, exps, [days[3].date])
    assert done[0]["how"] == "split" and done[0]["exit"] == days[2].date


def test_chain_money_sums_its_fills_and_fees():
    days = _days([60.0, 60.0, 60.0])
    done, _ = _run(replace(s49.JUDGED["atclose"], cap=1, tp=None, defence=-1.0), days, _fridays(12))
    c = done[0]
    open_fee = core_fees.ic_open_fee("TQQQ", quantity=1, legs=1, sell_legs=1, ndigits=4)
    close_fee = core_fees.ic_close_fee("TQQQ", quantity=1, legs=1, sell_legs=0, ndigits=4)
    assert c["nets"]["natural"] < c["nets"]["mid"]  # the close pays the ask; the open was at mid
    assert c["nets"]["stress"] < c["nets"]["natural"]
    assert c["share"] == pytest.approx(c["net"] / c["collateral"])
    assert open_fee > close_fee


# ---- the plan ----


def test_missing_spans_are_contiguous_runs_the_store_lacks():
    dates = _sessions(6)
    want = {"2022-02-04": set(dates)}
    have = {"2022-02-04": {dates[2]}}
    got = s49.missing_spans("TQQQ", want, lambda e: have.get(e, set()), dates)
    assert got == [
        {"symbol": "TQQQ", "expiration": "2022-02-04", "start": dates[0], "through": dates[1]},
        {"symbol": "TQQQ", "expiration": "2022-02-04", "start": dates[3], "through": dates[5]},
    ]
    assert s49.missing_spans("TQQQ", want, lambda e: set(dates), dates) == []


def test_stored_sessions_reads_finished_paths_and_requests_not_refusals():
    import sqlite3

    from cherrypick.backtest import chainfetch

    conn = sqlite3.connect(":memory:")
    conn.executescript(chainfetch.SCHEMA)
    dates = _sessions(6)
    rows = [
        ("TQQQ", "2022-02-04", dates[0], dates[2], "complete"),
        ("TQQQ", "2022-02-11", dates[0], dates[5], "refused"),
        ("QQQ", "2022-02-04", dates[0], dates[5], "complete"),
    ]
    conn.executemany("INSERT INTO eod_chain_paths (symbol, expiration, start, through, status, fetched_at) "
                     "VALUES (?, ?, ?, ?, ?, 0)", rows)  # fmt: skip
    conn.execute("INSERT INTO eod_chain_requests (symbol, session, expiration, status, fetched_at) "
                 "VALUES ('TQQQ', ?, '2022-02-04', 'empty', 0)", (dates[4],))  # fmt: skip
    got = s49.stored_sessions(conn, "TQQQ", dates)
    assert got == {"2022-02-04": {dates[0], dates[1], dates[2], dates[4]}}


def test_plan_reaches_each_monthly_from_180_days_before():
    days = _days([60.0] * 300, start=date(2021, 6, 1))
    exps = sorted(set(_fridays(60, date(2021, 6, 1))))
    want = s49.wanted_sessions(days, exps, [(days[0].date, days[-1].date)])
    m = s49.monthlies(exps)[-1]
    earliest = min(want[m])
    assert s49._cal(earliest, m) <= s49.MAX_ROLL_DTE
    assert s49._cal(earliest, m) > 150 or earliest == days[0].date


# ---- statistics and the verdict ----


def test_clustered_t_counts_months_not_chains():
    xs = [0.01, 0.01, 0.01, -0.01, 0.02, 0.0]
    groups = ["a", "a", "a", "b", "c", "c"]
    out = s49.clustered(xs, groups)
    assert out["clusters"] == 3 and out["n"] == 6
    same = s49.clustered(xs, ["a"] * 6)
    assert same["p"] == 1.0  # one cluster: no inference


def _rep(n, mean, t, p, halves=(0.01, 0.01), stress=0.01, limit=0.01):
    return {"n": n, "mean": mean, "t": t, "p": p, "halves": list(halves), "stress": stress, "limit": limit}


def test_design_pass_needs_every_check_and_the_holdout_reads_only_passes():
    reports = {a: _rep(50, -0.01, -1.0, 0.9) for a in s49.JUDGED}
    reports["maria"] = _rep(50, 0.02, 3.5, 0.0005)
    reports["deep"] = _rep(50, 0.02, 3.5, 0.0005, limit=-0.01)  # fails at the limit
    chains = {a: [] for a in s49.ARMS}
    v = s49.judge_design(reports, chains)
    assert v["arms"]["maria"]["verdict"] == "design pass"
    assert v["arms"]["deep"]["verdict"] == "fail" and not v["arms"]["deep"]["checks"]["limit"]
    h = s49.judge_holdout(v, {"maria": {"n": 40, "mean": 0.01}})
    assert h["maria"]["verdict"] == "pass" and h["deep"]["verdict"] == "fail"
    h = s49.judge_holdout(v, {"maria": {"n": 10, "mean": 0.01}})
    assert h["maria"]["verdict"] == "no verdict"


def test_crazy_ivan_claim_needs_both_rivals_beaten():
    verdict = {"maria": {"verdict": "pass"}, "deep": {"verdict": "pass"}}
    good, bad = {"mean": 0.01, "t": 2.5}, {"mean": 0.01, "t": 1.0}
    claim = {
        "maria": {"atclose": good, "noroll": good},
        "deep": {"atclose": good, "noroll": bad},
        "overnight": {"atclose": good, "noroll": good},
        "trend": {"atclose": good, "noroll": good},
    }
    assert s49.claim_holds(verdict, claim) == ["maria"]  # overnight and trend did not pass


def test_the_holdout_is_read_once(tmp_path, monkeypatch):
    monkeypatch.setattr(s49, "_out_dir", lambda: tmp_path)
    arms = {a: {"verdict": "fail", "checks": {}} for a in s49.JUDGED}
    design = {"reports": {}, "verdict": {"arms": arms, "claim": {}}}
    (tmp_path / "results-design.json").write_text(s49.json.dumps(design), encoding="utf-8")
    assert s49.main(["study", "--window", "holdout"]) == 0  # nothing passed: no arm is read
    got = s49.json.loads((tmp_path / "results-holdout.json").read_text(encoding="utf-8"))
    assert got["arms_read"] == [] and got["crazy_ivan"] == []
    with pytest.raises(SystemExit, match="read once"):
        s49.main(["study", "--window", "holdout"])
    with pytest.raises(SystemExit, match="read once"):
        s49.main(["study", "--window", "holdout", "--json", str(tmp_path / "elsewhere.json")])
