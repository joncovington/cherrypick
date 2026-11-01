"""Hypothesis 46's inputs (scripts/variance_premium_item46.py): the names, the cycles and the path requests.
Invented store keys only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "variance_premium_item46", SCRIPTS / "variance_premium_item46.py"
)
s46 = importlib.util.module_from_spec(_spec)
sys.modules["variance_premium_item46"] = s46
_spec.loader.exec_module(s46)


def test_the_groups_are_the_declared_seven_names_and_do_not_overlap():
    assert s46.GROUPS["equity_index"] == ("SPXW", "SPY", "QQQ", "IWM")
    assert s46.GROUPS["non_equity"] == ("GLD", "SLV", "TLT")
    assert len(set(s46.NAMES)) == len(s46.NAMES) == 7


def test_a_monthly_is_the_third_friday_unless_it_is_a_holiday():
    assert s46.monthly_expiry(2026, 10) == date(2026, 10, 16)
    assert s46.monthly_expiry(2022, 4) == date(2022, 4, 14)  # Good Friday 04-15: the Thursday
    assert s46.monthly_expiry(2025, 4) == date(2025, 4, 17)  # Good Friday 04-18
    assert s46.monthly_expiry(2016, 3) == date(2016, 3, 18)


def test_the_cycles_run_from_the_first_to_the_last_declared_expiry_one_a_month():
    cs = s46.cycles()
    assert cs[0] == s46.FIRST_EXPIRY and cs[-1] == s46.LAST_EXPIRY
    assert len(cs) == 128  # 2016-03 .. 2026-10
    assert len({(d.year, d.month) for d in cs}) == len(cs)
    # A window that ends before its month's monthly leaves that month out.
    assert s46.cycles(date(2026, 9, 1), date(2026, 10, 15))[-1] == date(2026, 9, 18)


def test_each_request_is_the_stores_66_day_path_to_expiry():
    r = s46.path_request("SPY", date(2026, 3, 20))
    assert r == {"symbol": "SPY", "expiration": "2026-03-20", "start": "2026-01-13", "through": "2026-03-20"}
    longest = max(s46.TENORS)
    assert (
        date(2026, 3, 20) - date.fromisoformat(r["start"])
    ).days > longest  # every tenor's entry is inside


def test_every_name_gets_every_cycle():
    reqs = s46.build_requests()
    assert len(reqs) == 7 * 128
    assert {r["symbol"] for r in reqs} == set(s46.NAMES)
    assert reqs[0]["symbol"] == "SPXW" and reqs[-1]["symbol"] == "TLT"


def test_held_counts_only_the_exact_complete_key():
    reqs = s46.build_requests(("SPY",), [date(2026, 3, 20), date(2026, 4, 17)])
    exact = {("SPY", "2026-03-20", "2026-01-13", "2026-03-20")}
    assert s46.held(reqs, exact) == 1
    shorter = {("SPY", "2026-03-20", "2026-03-02", "2026-03-20")}  # an 18-day path is not the same request
    assert s46.held(reqs, shorter) == 0


def test_the_time_split_falls_inside_the_cycles():
    cs = s46.cycles()
    before = [d for d in cs if d < s46.TIME_SPLIT]
    assert 0 < len(before) < len(cs)


# ------------------------------------------------------------------------------------------------
# The coverage step's building blocks, on invented chains priced by Black at a known forward.


def chain(fwd=100.0, vol=0.20, years=30 / 365, strikes=range(70, 131), half=0.05):
    from cherrypick.core import bs

    out = []
    for k in strikes:
        for right in ("C", "P"):
            p = bs.price(fwd, float(k), years, vol, right, 0.0)
            out.append((float(k), right, max(0.0, round(p - half, 2)), round(p + half, 2)))
    return out


def test_a_mid_needs_an_ask_above_zero_and_an_uncrossed_bid():
    assert s46.mid(1.0, 1.2) == 1.1
    assert s46.mid(0.0, 0.05) == 0.025
    assert s46.mid(0.0, 0.0) is None
    assert s46.mid(1.3, 1.2) is None
    assert s46.mid(None, 1.2) is None


def test_the_forward_is_parity_where_call_and_put_meet():
    assert abs(s46.forward(chain(fwd=100.0)) - 100.0) < 0.02
    assert abs(s46.forward(chain(fwd=103.4)) - 103.4) < 0.06
    calls_only = [r for r in chain() if r[1] == "C"]
    assert s46.forward(calls_only) is None


def test_the_entry_is_the_first_trading_day_on_or_after_expiry_minus_the_tenor():
    assert s46.entry_session(date(2026, 10, 16), 30) == date(2026, 9, 16)
    assert s46.entry_session(date(2022, 4, 14), 60) == date(2022, 2, 14)  # a Sunday: the Monday
    assert s46.entry_session(date(2026, 4, 17), 60) == date(2026, 2, 17)  # Presidents' Day: the Tuesday


def test_each_leg_is_picked_at_its_strike_or_delta():
    from cherrypick.core import bs

    y = 30 / 365
    ch = chain(years=y)
    straddle = s46.pick_leg(ch, 100.0, y, "straddle")
    assert [(c["strike"], c["right"]) for c in straddle] == [(100.0, "C"), (100.0, "P")]
    for leg, (right, target) in (("put25", ("P", 0.25)), ("put10", ("P", 0.10)), ("call25", ("C", 0.25))):
        (c,) = s46.pick_leg(ch, 100.0, y, leg)
        assert c["right"] == right
        assert abs(abs(bs.delta(100.0, c["strike"], y, c["iv"], right, 0.0)) - target) < 0.03
    # A put nobody bids is never sold: with every put bid at zero, no put leg and no straddle.
    no_put_bid = [(k, r, 0.0 if r == "P" else b, a) for k, r, b, a in ch]
    assert s46.pick_leg(no_put_bid, 100.0, y, "put25") is None
    assert s46.pick_leg(no_put_bid, 100.0, y, "straddle") is None
    assert s46.pick_leg(no_put_bid, 100.0, y, "call25") is not None


def test_the_realised_variance_term_by_hand():
    # 0.5 x 0.02 x (1 + 4) / 100 x 1e4 = 5 bp
    assert abs(s46.rv_term_bp(0.02, [100.0, 101.0, 99.0]) - 5.0) < 1e-9
    assert s46.rv_term_bp(0.02, [100.0]) is None


def test_a_cycle_counts_once_with_its_names_equal_weighted():
    per_name = {"SPXW": {"2026-09-18": 4.0, "2026-10-16": 2.0}, "SPY": {"2026-09-18": 6.0}}
    assert s46.group_cycle_means(per_name, ("SPXW", "SPY", "QQQ")) == {"2026-09-18": 5.0, "2026-10-16": 2.0}


def test_a_cell_under_eighty_cycles_is_flagged_and_the_first_holm_step_is_stricter():
    few = s46.cell_mde([1.0, 2.0, 3.0] * 26)
    assert few["cycles"] == 78 and not few["enough"]
    many = s46.cell_mde([1.0, 2.0, 3.0] * 27)
    assert many["enough"]
    assert many["mde_bp_holm_first"] > many["mde_bp_holm_last"] > 0


def test_a_cycle_drops_a_missing_entry_and_counts_a_gap():
    expiry = date(2026, 10, 16)
    days = s46.trading_days(date(2026, 10, 9), expiry)
    sessions = {
        d: chain(fwd=100.0 + i, years=max(s46.years_to(d, expiry), 1 / 365))
        for i, d in enumerate(days)
        if d != date(2026, 10, 13)
    }
    terms, notes = s46.cycle_terms(sessions, expiry)
    assert notes[7] == {"gaps": 1}
    assert set(terms[7]) == set(s46.LEGS)
    assert all(v > 0 for v in terms[7].values())
    assert notes[30] == {"drop": "entry_missing"}
    sessions.pop(expiry)
    _, notes = s46.cycle_terms(sessions, expiry)
    assert notes[7] == {"drop": "expiry_missing"}


def test_the_store_reader_translates_the_stores_rights_so_a_forward_exists():
    # The chain store writes CALL/PUT; read raw, no call meets a put and every cycle is "no_forward".
    import sqlite3

    from cherrypick.backtest import chainfetch

    conn = sqlite3.connect(":memory:")
    conn.executescript(chainfetch.SCHEMA)
    req = s46.path_request("SPY", date(2026, 10, 16))
    store_side = {"C": "CALL", "P": "PUT"}
    for k, r, b, a in chain():
        conn.execute(
            "INSERT INTO eod_chain_quotes (symbol, session, expiration, strike, right, bid, ask)"
            " VALUES ('SPY', '2026-10-09', '2026-10-16', ?, ?, ?, ?)",
            (k, store_side[r], b, a),
        )
    conn.execute(
        "INSERT INTO eod_chain_requests (symbol, session, expiration, status, fetched_at)"
        " VALUES ('SPY', '2026-10-09', '2026-10-16', 'complete', 0)"
    )
    sessions = s46._path_sessions(conn, req)
    (rows,) = sessions.values()
    assert {r for _, r, _, _ in rows} == {"C", "P"}
    assert abs(s46.forward(rows) - 100.0) < 0.02
    raw = [(k, store_side[r], b, a) for k, r, b, a in rows]
    assert s46.forward(raw) is None


# ------------------------------------------------------------------------------------------------
# The study step: the trade, the hedge, the statistics and the report. Invented chains only.

EXPIRY = date(2026, 10, 16)


def path(fwds: dict[date, float], vol=0.20):
    """{session: chain} priced at each session's forward and time to EXPIRY."""
    return {d: chain(fwd=f, vol=vol, years=max(s46.years_to(d, EXPIRY), 0.5 / 365)) for d, f in fwds.items()}


def week(step=0.0):
    days = s46.trading_days(date(2026, 10, 9), EXPIRY)
    return {d: 100.0 + i * step for i, d in enumerate(days)}


def test_a_short_calls_hedge_gains_when_the_forward_rises_and_a_short_puts_loses():
    fwds = week(step=1.0)
    sessions = path(fwds)
    y = s46.years_to(date(2026, 10, 9), EXPIRY)
    call = s46.pick_leg(sessions[date(2026, 10, 9)], 100.0, y, "call25")
    put = s46.pick_leg(sessions[date(2026, 10, 9)], 100.0, y, "put25")
    assert s46.hedge(call, sessions, fwds, date(2026, 10, 9), EXPIRY)["pnl"] > 0
    assert s46.hedge(put, sessions, fwds, date(2026, 10, 9), EXPIRY)["pnl"] < 0


def test_a_still_forward_earns_the_hedge_nothing_and_costs_about_the_open_and_the_unwind():
    fwds = week()
    sessions = path(fwds)
    y = s46.years_to(date(2026, 10, 9), EXPIRY)
    straddle = s46.pick_leg(sessions[date(2026, 10, 9)], 100.0, y, "straddle")
    hg = s46.hedge(straddle, sessions, fwds, date(2026, 10, 9), EXPIRY)
    assert hg["pnl"] == 0.0
    assert hg["rebalances"] == len(fwds) - 1
    # an ATM straddle is near delta-flat: the hedge trades almost nothing
    assert 0 <= hg["cost"] < 0.5


def test_settlement_costs_cash_for_spx_and_shares_for_a_fund():
    put = [{"strike": 100.0, "right": "P"}]
    assert s46.settle_fee("SPXW", put, 101.0) == 0.0
    assert s46.settle_fee("SPXW", put, 99.0) == 5.0
    etf = s46.settle_fee("SPY", put, 99.0)
    assert etf > 5.0  # the delivered shares are sold: SEC fee and TAF on top
    call = [{"strike": 100.0, "right": "C"}]
    assert s46.settle_fee("SPY", call, 99.0) == 0.0


def test_a_trade_that_expires_out_of_the_money_keeps_its_premium_less_its_costs():
    fwds = week()
    sessions = path(fwds)
    tr = s46.trade("SPY", sessions, fwds, EXPIRY, 7, "put10")
    assert tr["payoff"] == 0.0
    assert abs(tr["net"] - (tr["premium"] + tr["hedge_pnl"] - tr["costs"])) < 0.01
    assert tr["costs"] > 1.0  # at least the $1 commission
    assert abs(tr["net_bp"] - tr["net"] / (100 * 100.0) * 1e4) < 1e-3  # net is kept to 4 decimals


def test_a_trade_says_why_it_is_dropped():
    fwds = week()
    sessions = path(fwds)
    assert s46.trade("SPY", sessions, fwds, EXPIRY, 30, "put25") == {"drop": "entry_missing"}
    calls_only = dict(sessions)
    calls_only[EXPIRY] = [r for r in sessions[EXPIRY] if r[1] == "C"]
    no_exp = {d: s46.forward(ch) for d, ch in calls_only.items()}
    assert s46.trade("SPY", calls_only, no_exp, EXPIRY, 7, "put25") == {"drop": "no_expiry_forward"}
    gone = {d: ch for d, ch in sessions.items() if d != EXPIRY}
    assert s46.trade("SPY", gone, fwds, EXPIRY, 7, "put25") == {"drop": "expiry_missing"}


def test_spx_pays_its_index_fee_and_a_fund_does_not():
    fwds = week()
    sessions = path(fwds)
    spx = s46.trade("SPXW", sessions, fwds, EXPIRY, 7, "put10")
    spy = s46.trade("SPY", sessions, fwds, EXPIRY, 7, "put10")
    assert abs((spx["costs"] - spy["costs"]) - 0.60) < 1e-6


def test_newey_west_at_lag_zero_is_the_plain_t_and_autocorrelation_shrinks_it():
    import math
    import statistics

    xs = [1.0, 3.0, 2.0, 4.0, 3.0, 5.0, 2.0, 4.0]
    m = statistics.fmean(xs)
    plain = m / math.sqrt(statistics.pvariance(xs) / len(xs))
    assert abs(s46.newey_west_t(xs, 0) - plain) < 1e-12
    runs = [1.0, 1.0, 1.0, 3.0, 3.0, 3.0] * 4
    assert s46.newey_west_t(runs, 2) < s46.newey_west_t(runs, 0)
    assert s46.newey_west_t([1.0, 2.0], 1) is None
    assert s46.nw_lag(30) == 1 and s46.nw_lag(45) == 2 and s46.nw_lag(60) == 2 and s46.nw_lag(7) == 1


def _cell(**over):
    base = {
        "enough": True,
        "p": 1e-6,
        "mean_bp": 2.0,
        "halves": {"before": 1.0, "after": 3.0},
        "stressed_mean_bp": 1.0,
        "names_positive": 4,
    }
    return {**base, **over}


def _family(key, cell):
    cells = {
        f"{g}/{t}/{leg}": _cell(p=0.5, mean_bp=-1.0)
        for g in s46.GROUPS
        for t in s46.TENORS
        for leg in s46.LEGS
    }
    cells[key] = cell
    return cells


def test_the_judge_passes_only_when_every_condition_holds():
    key = "equity_index/30/put25"
    cells = _family(key, _cell())
    assert s46.judge(cells) == "pass" and cells[key]["verdict"] == "pass"
    for broken in (
        {"halves": {"before": -0.1, "after": 3.0}},
        {"halves": {"before": None, "after": 3.0}},
        {"stressed_mean_bp": -0.1},
        {"names_positive": 2},
        {"p": 0.01},  # below 0.025 alone, but not 0.025 / 40
        {"mean_bp": -2.0},
    ):
        cells = _family(key, _cell(**broken))
        assert s46.judge(cells) == "fail", broken
        assert cells[key]["verdict"] == "fail"
    cells = _family(key, _cell(enough=False))
    s46.judge(cells)
    assert cells[key]["verdict"] == "no verdict"
    # the funds need two of three names
    fkey = "non_equity/45/straddle"
    cells = _family(fkey, _cell(names_positive=2))
    assert s46.judge(cells) == "pass"
    nothing = {k: _cell(enough=False) for k in _family(key, _cell())}
    assert s46.judge(nothing) == "no verdict"


def test_a_cell_averages_its_names_within_each_cycle_and_splits_at_the_declared_date():
    per_name = {
        "SPXW": {
            "2020-03-20": {"net_bp": 4.0, "cost_bp": 1.0},
            "2022-03-18": {"net_bp": -2.0, "cost_bp": 1.0},
        },
        "SPY": {"2020-03-20": {"net_bp": 2.0, "cost_bp": 1.0}},
    }
    c = s46.cell_stats(per_name, ("SPXW", "SPY", "QQQ", "IWM"), 30)
    assert c["cycles"] == 2
    assert c["halves"] == {"before": 3.0, "after": -2.0}
    assert c["mean_bp"] == 0.5
    assert c["stressed_mean_bp"] == 0.0
    assert c["by_name"] == {"SPXW": 1.0, "SPY": 2.0}
    assert c["names_positive"] == 2
    assert c["worst_cycle"] == ("2022-03-18", -2.0)


def test_implied_reads_near_the_chains_vol_and_is_above_a_still_path():
    y = 30 / 365
    ch = chain(fwd=100.0, vol=0.20, years=y)
    still = s46.iv_rv(ch, [100.0] * 22, y)
    assert 18.0 < still["implied_vol"] < 22.0
    assert still["realised_vol"] == 0.0 and still["above"]
    wild = s46.iv_rv(ch, [100.0, 110.0, 95.0, 108.0, 92.0, 107.0], y)
    assert not wild["above"]
    assert s46.iv_rv(ch, [100.0], y) is None


def test_the_regime_reads_the_term_structure_and_vixs_own_rank():
    days = s46.trading_days(date(2025, 1, 2), date(2026, 3, 2))
    vix = {d: 10.0 + i * 0.01 for i, d in enumerate(days)}
    vix3m = {d: v + 1.0 for d, v in vix.items()}
    last = days[-1]
    assert s46.regime_at(last, vix, vix3m) == {"contango": True, "tercile": 2}
    vix3m[last] = vix[last] - 1.0
    assert s46.regime_at(last, vix, vix3m)["contango"] is False
    assert s46.regime_at(days[10], vix, vix3m)["tercile"] is None  # under a year of closes


def test_the_report_renders_a_study_with_no_trades():
    cells = s46.cells_for({}, s46.LEGS)
    verdict = s46.judge(cells)
    result = {
        "verdict": verdict,
        "time_split": s46.TIME_SPLIT.isoformat(),
        "cells": {k: {x: v for x, v in c.items() if x != "series"} for k, c in cells.items()},
        "iv_vs_rv": {n: {str(t): s46.iv_rv_summary([]) for t in s46.TENORS} for n in s46.NAMES},
        "beside_a_pass": {},
        "account": s46.ACCOUNT,
    }
    md = s46.report(result)
    assert verdict == "no verdict"
    assert md.startswith("# Item 46") and "**Verdict: no verdict.**" in md
    assert md.count("| 30d |") == 2 * len(s46.LEGS)


def test_a_passing_cell_gets_its_robustness_report_and_renders(monkeypatch):
    import random

    monkeypatch.setattr(s46, "read_cboe", lambda name: {})
    rng = random.Random(46)
    cycles = [c.isoformat() for c in s46.cycles()][:100]
    trades = {}
    for n in s46.GROUPS["equity_index"]:
        for leg in s46.ALL_LEGS:
            trades[(n, 30, leg)] = {
                c: {
                    "net_bp": 3.0 + rng.gauss(0, 2),
                    "cost_bp": 1.0,
                    "net": 30.0 + rng.gauss(0, 20),
                    "costs": 10.0,
                }
                for c in cycles
            }
    cells = s46.cells_for(trades, s46.LEGS)
    s46.judge(cells)
    key = "equity_index/30/put25"
    assert cells[key]["verdict"] == "pass"
    neighbours = s46.cells_for(trades, [leg for leg in s46.ALL_LEGS if leg not in s46.LEGS])
    b = s46.beside_a_pass(key, cells[key], cells, neighbours, trades, s46.ACCOUNT)
    assert b["strike_neighbourhood"]["verdict"] == "plateau"
    assert b["tenor_neighbourhood"]["declared"] == 30.0
    assert b["cost_stress"]["survives"]
    assert set(b["max_size_by_name"]) == set(s46.GROUPS["equity_index"])
    result = {
        "verdict": "pass",
        "time_split": s46.TIME_SPLIT.isoformat(),
        "cells": {k: {x: v for x, v in c.items() if x != "series"} for k, c in cells.items()},
        "iv_vs_rv": {n: {str(t): s46.iv_rv_summary([]) for t in s46.TENORS} for n in s46.NAMES},
        "beside_a_pass": {key: b},
        "account": s46.ACCOUNT,
    }
    md = s46.report(result)
    assert "## Beside the pass: index 25Δ put at 30 days" in md
