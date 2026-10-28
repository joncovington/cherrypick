"""Hypothesis 25's study (scripts/skate_expiry_item25.py): its entry rules, its management and its
verdict. Invented chains only, priced flat at 30% vol, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("skate_expiry_item25", SCRIPTS / "skate_expiry_item25.py")
st = importlib.util.module_from_spec(_spec)
sys.modules["skate_expiry_item25"] = st  # its dataclasses look their module up by name
_spec.loader.exec_module(st)

START = date(2025, 3, 5)  # a Wednesday: the 14-day arm's Friday is 16 days out


def _rows(spot: float, day: date, exp: date) -> list:
    t = (exp - day).days / 365
    rows = []
    for k in range(60, 141):
        for cp in "CP":
            px = st.pcr.black76(cp, spot, float(k), t, 0.30, 1.0)
            rows.append([float(k), cp, round(max(px - 0.01, 0.0), 2), round(px + 0.01, 2)])
    return rows


def scenario(spots, *, fund=True, reports=(), splits=()):
    """A name whose close on the i-th session from START is spots(i); the band sits at 95% of it."""
    days, d = [], START
    while len(days) < 40:
        if st.cal.is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    closes = {d: float(spots(i)) for i, d in enumerate(days)}
    exps = st.fridays(START, START + timedelta(days=120))
    chains = {
        d.isoformat(): {
            e.isoformat(): _rows(closes[d], d, e) for e in exps if d < e <= d + timedelta(days=70)
        }
        for d in days
    }
    ratios = {n: dict.fromkeys(days, 0.95) for n in range(1, 50)}
    st.pcr.SYMBOL = "AAPL"
    return st.NameData("TEST", fund, chains, closes, ratios, list(reports), list(splits), {})


def run(nd, arm, entries_through=START):
    return st.run_arm(nd, arm, START, entries_through, max(nd.days), st.FRAC)


# --- the calendar and the entry rules -----------------------------------------------------------------


def test_expiries_are_fridays_or_the_day_before_a_holiday_and_the_arms_take_their_own():
    assert date(2022, 4, 14) in st.fridays(date(2022, 4, 11), date(2022, 4, 15))  # Good Friday
    assert st.is_monthly(date(2022, 4, 14)) and not st.is_monthly(date(2022, 4, 8))
    assert st.in_range("w14-hold", START, date(2025, 3, 21))  # 16 days
    assert not st.in_range("w14-hold", START, date(2025, 3, 28))  # 23 days
    assert not st.in_range("m45-hold", date(2025, 2, 3), date(2025, 3, 28))  # 53 days, not a monthly
    assert st.in_range("m45-hold", date(2025, 2, 3), date(2025, 3, 21))  # 46 days, the monthly


def test_earnings_crossing_or_unknown_refuse_and_only_a_known_clear_quarter_passes():
    a, b = date(2025, 3, 5), date(2025, 3, 21)
    assert st.earnings_state([date(2025, 3, 10)], a, b) == "crosses"
    assert st.earnings_state([date(2025, 1, 30), date(2025, 4, 30)], a, b) == "clear"
    assert st.earnings_state([], a, b) == "unknown"
    assert st.earnings_state([date(2024, 10, 30), date(2025, 4, 30)], a, b) == "unknown"  # a quarter missing
    assert st.earnings_state([date(2025, 1, 30)], a, b) == "clear"  # the next not listed, nor due yet
    assert st.earnings_state([date(2024, 12, 20)], a, b) == "unknown"  # the next is due inside the span


def test_the_spread_bar_and_the_band_strike():
    assert st.spread_ok(1.00, 1.05) and st.spread_ok(2.00, 2.20)
    assert not st.spread_ok(1.00, 1.20) and not st.spread_ok(0.0, 0.05)
    assert st.band_strike(94.6, [90.0, 92.5, 95.0]) == 92.5 and st.band_strike(89.0, [90.0, 95.0]) is None


def test_a_stock_reporting_inside_the_span_a_split_or_an_unknown_date_is_not_entered():
    flat = lambda i: 100  # noqa: E731
    trades, stats = run(scenario(flat, fund=False, reports=[START + timedelta(days=5)]), "w14-hold")
    assert not trades and stats["earnings"] == 1
    trades, stats = run(scenario(flat, fund=False), "w14-hold")
    assert not trades and stats["earnings"] == 1
    trades, stats = run(scenario(flat, splits=[START + timedelta(days=3)]), "w14-hold")
    assert not trades and stats["split"] == 1
    trades, _ = run(scenario(flat, fund=False, reports=[date(2025, 1, 30), date(2025, 4, 30)]), "w14-hold")
    assert len(trades) == 1 and trades[0]["capital"] == 9500.0


# --- management ---------------------------------------------------------------------------------------


def test_hold_buys_back_at_three_cents_once_the_ask_is_there_and_not_before():
    trades, _ = run(scenario(lambda i: 100 if i == 0 else 115), "w14-hold")
    (t,) = trades
    assert t["how"] == "gtc" and t["exit"] <= "2025-03-07" and t["net"] > 0
    trades, _ = run(scenario(lambda i: 100), "w14-hold")
    assert trades[0]["how"] == "gtc" and trades[0]["exit"] >= "2025-03-17"  # flat: only in the last days
    trades, _ = run(scenario(lambda i: 100 if i == 0 else 95.5), "w14-hold")
    assert trades[0]["how"] == "expired"  # just above the strike, the ask never gets to $0.03


def test_tp50_buys_back_at_half_the_credit():
    trades, _ = run(scenario(lambda i: 100 + min(i, 4)), "w14-tp50")
    (t,) = trades
    assert t["how"] == "tp50" and t["net"] > 0
    trades, _ = run(scenario(lambda i: 100 if i == 0 else 94), "w14-tp50")
    assert trades[0]["how"] == "assigned"  # in the money, it never halves


def test_a_tested_put_rolls_net_zero_to_the_next_expiry_as_one_trade():
    # Tested from Friday 03-07; no Friday is 12-16 days out until Wednesday 03-12, then it recovers.
    nd = scenario(lambda i: 93 if 2 <= i <= 5 else 100)
    trades, stats = run(nd, "w14-roll")
    (t,) = trades
    assert t["rolls"] == 1 and stats["rolls"] == 1 and stats["roll_refused"] == 3
    assert t["expiry"] == "2025-03-28" and t["capital"] == 9500.0  # same strike, next Friday in range
    held, _ = run(nd, "w14-hold")
    assert held[0]["rolls"] == 0 and held[0]["expiry"] == "2025-03-21"


def test_no_roll_when_nothing_covers_the_buyback_so_the_put_runs_to_assignment():
    trades, stats = run(scenario(lambda i: 100 if i < 2 else 70), "w14-roll")
    (t,) = trades
    assert t["rolls"] == 0 and t["how"] == "assigned" and stats["roll_refused"] > 0 and t["net"] < 0


def test_at_most_two_rolls():
    trades, stats = run(scenario(lambda i: 100 if i < 2 else 96 - i), "w14-roll")
    assert max(t["rolls"] for t in trades) == 2 and stats["roll_refused"] > 0


# --- the measure and the verdict ----------------------------------------------------------------------


def test_edge_takes_out_the_delta_equivalent_stock_and_no_trades_is_zero():
    t = {"net": 100.0, "pos_delta": 0.2, "spot_entry": 100.0, "spot_exit": 105.0, "capital": 10000.0}
    assert st.name_edge([t], 1.0) == 0.0  # the whole net was the stock's drift
    assert st.name_edge([dict(t, spot_exit=100.0)], 0.5) == 2.0
    assert st.name_edge([], 1.0) == 0.0


def _window(arm_edge, base_edge, n=40, trades=400, stress=None):
    names = [f"N{i:02d}" for i in range(n)]
    edge = {
        a: {s: (arm_edge(i) if a != st.BASE else base_edge(i)) for i, s in enumerate(names)} for a in st.ARMS
    }
    return {"edge": edge, "stress": stress or edge, "trades": dict.fromkeys(st.ARMS, trades)}


GOOD = (lambda i: 2.0 + (i % 5) * 0.2, lambda i: 0.5 + (i % 3) * 0.1)


def test_a_steady_gain_over_the_base_in_both_windows_passes():
    v = st.judge(_window(*GOOD), _window(*GOOD))
    assert all(x["verdict"] == "pass" for x in v.values()), {a: x["checks"] for a, x in v.items()}


def test_no_better_than_the_base_fails():
    v = st.judge(_window(GOOD[1], GOOD[1]), _window(*GOOD))
    assert all(x["verdict"] == "fail" and not x["checks"]["paired_mean_positive"] for x in v.values())


def test_beating_the_base_while_losing_on_its_own_fails():
    v = st.judge(_window(lambda i: -0.5 - (i % 3) * 0.1, lambda i: -3.0 - (i % 4) * 0.2), _window(*GOOD))
    x = v["w14-hold"]
    assert (
        x["checks"]["paired_t_above_bar"]
        and not x["checks"]["own_median_positive"]
        and x["verdict"] == "fail"
    )


def test_failing_the_cost_stress_fails():
    d = _window(*GOOD)
    d["stress"] = {a: {s: -1.0 for s in d["edge"][a]} for a in st.ARMS}
    assert all(x["verdict"] == "fail" for x in st.judge(d, _window(*GOOD)).values())


def test_a_holdout_against_it_fails_a_design_pass():
    v = st.judge(_window(*GOOD), _window(GOOD[1], GOOD[0]))
    assert all(x["verdict"] == "fail" and not x["checks"]["holdout_paired_mean_positive"] for x in v.values())


def test_too_few_names_or_trades_is_no_verdict():
    assert all(x["verdict"] == "no verdict" for x in st.judge(_window(*GOOD, n=20), _window(*GOOD)).values())
    assert all(
        x["verdict"] == "no verdict" for x in st.judge(_window(*GOOD, trades=200), _window(*GOOD)).values()
    )


def test_holm_across_the_five_arms_rejects_what_one_test_alone_would_pass():
    def p_of(t):
        return {"n": 40, "mean": 1.0, "t": t, "positive_share": 0.8}

    out = {a: {"design": dict(p_of(2.1), p=0.02)} for a in st.JUDGED}  # t above 2, but 0.02 > 0.05 / 5
    assert not any(st.core_stats.holm({a: v["design"]["p"] for a, v in out.items()}, st.ALPHA).values())


# --- the fetch --------------------------------------------------------------------------------------------


def test_requests_skip_what_the_store_holds_and_lead_each_expiry_by_its_path():
    reqs = st.path_requests({("AAPL", "2025-10-24")}, ["AAPL"])
    by = {(r["expiration"]): r for r in reqs}
    assert "2025-10-24" not in by
    r = by["2025-10-31"]
    assert r["start"] == "2025-10-13" and r["through"] == "2025-10-31"
    assert by["2026-10-30"]["through"] == "2026-10-22"  # cut at the run end
    m = by["2023-03-17"]  # a monthly a late 2022 roll reaches, from 66 days before
    assert m["start"] == "2023-01-10" and m["through"] == "2023-02-17"


def test_a_name_without_its_weeklies_is_dropped():
    w0, w1 = date(2022, 1, 3), date(2022, 12, 30)
    weeks = [e for e in st.fridays(w0 + timedelta(days=12), w1 + timedelta(days=16)) if not st.is_monthly(e)]
    full = {("X", e.isoformat()): "complete" for e in weeks}
    assert st.weekly_coverage(full, "X", w0, w1) == 1.0
    holed = {**full, **{("X", e.isoformat()): "empty" for e in weeks[:6]}}
    assert st.weekly_coverage(holed, "X", w0, w1) < st.WEEKLY_COVERAGE
