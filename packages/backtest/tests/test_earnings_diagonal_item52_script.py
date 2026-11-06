"""Hypothesis 52's study (scripts/earnings_diagonal_item52.py): the structure, the requests, the delta
strikes, the fills and costs, the control pairing, Holm, the verdict floors, arm C's side, the cheap cut
and the refusal. Invented chains, bars and stores only: no vendor data, no real figure.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import american
from cherrypick.core import calendar as cal

from cherrypick.backtest import chainfetch

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "earnings_diagonal_item52", SCRIPTS / "earnings_diagonal_item52.py"
)
s52 = importlib.util.module_from_spec(_spec)
sys.modules["earnings_diagonal_item52"] = s52
_spec.loader.exec_module(s52)
e31 = s52.e31

SPAN = (date(2020, 1, 1), date(2026, 12, 31))


# ------------------------------------------------------------------------------------------------
# The structure.


def test_the_weekly_is_the_first_listed_friday_within_nine_days_and_the_monthly_fourteen_on():
    report = date(2026, 10, 28)  # a Wednesday, after the close: reaction Thursday 10-29
    cands = s52.grid_candidates(date(2026, 10, 28), date(2026, 10, 29), report)
    assert cands == [date(2026, 10, 30), date(2026, 11, 6)]  # 11-06 is 9 days out: inside
    assert s52.choose_weekly(cands, lambda d: None) == (date(2026, 10, 30), True)
    assert s52.choose_weekly(cands, lambda d: "complete") == (date(2026, 10, 30), False)
    skip = {date(2026, 10, 30): "empty", date(2026, 11, 6): "complete"}
    assert s52.choose_weekly(cands, skip.get) == (date(2026, 11, 6), False)
    assert s52.e47.monthly_expiry(date(2026, 11, 6), s52.MONTHLY_GAP) == date(2026, 11, 20)  # exactly 14
    assert s52.e47.monthly_expiry(date(2026, 11, 13), s52.MONTHLY_GAP) == date(2026, 12, 18)  # 7 days: next


def test_a_friday_after_the_window_is_not_a_candidate_and_a_holiday_friday_is_the_thursday():
    # a Friday after-close report: the reaction is Monday, and the Friday after that is 14 days out
    assert s52.grid_candidates(date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 6)) == [
        date(2026, 11, 13)
    ]
    # Good Friday 2025-04-18: the weekly is the Thursday
    got = s52.grid_candidates(date(2025, 4, 15), date(2025, 4, 16), date(2025, 4, 15))
    assert got[0] == date(2025, 4, 17)


def test_a_report_week_with_no_weekly_is_dropped_and_counted_and_asks_for_nothing():
    event = e31.event_row("ZZZ", date(2026, 10, 28), "after_close")
    reports = {"ZZZ": [date(2026, 10, 28)]}
    empty = {("ZZZ", "2026-10-28", "2026-10-30"): "empty", ("ZZZ", "2026-10-28", "2026-11-06"): "empty"}
    planned = s52.plan([event], reports, SPAN, empty)
    assert planned["counts"]["event_weekly_unlisted"] == 1
    assert not [k for k in planned["chains"] if k[1] >= "2026-10-28"]  # no event chain asked for
    assert planned["oi"] == {"2026-10-28": ["ZZZ"]}  # the open-interest day is still wanted


def test_the_requests_cover_the_event_its_day_two_and_expiry_session_and_the_control():
    event = e31.event_row("ZZZ", date(2026, 10, 28), "after_close")
    reports = {"ZZZ": [date(2026, 10, 28)]}
    planned = s52.plan([event], reports, SPAN, {})
    keys = set(planned["chains"])
    # weekly 10-30 (the day-2 session), monthly 11-20
    assert ("ZZZ", "2026-10-28", "2026-10-30") in keys
    assert ("ZZZ", "2026-10-29", "2026-10-30") in keys
    assert ("ZZZ", "2026-10-30", "2026-10-30") in keys  # day 2, the weekly's last day
    assert {("ZZZ", s, "2026-11-20") for s in ("2026-10-28", "2026-10-29", "2026-10-30")} <= keys
    control = [k for k in keys if k[1] < "2026-10-28"]
    # the control (four weeks earlier, 09-30) asks for its weekly and monthly on the entry and next session
    assert len(control) == 4 and {k[1] for k in control} == {"2026-09-30", "2026-10-01"}
    assert len(keys) == 6 + 4


def test_the_weekly_that_has_expired_is_not_asked_for_on_day_two():
    # a before-open report on a Thursday: reaction Thursday, weekly Friday; day 2 is the Friday
    e = e31.event_row("ZZZ", date(2026, 10, 29), "before_open")
    assert e["reaction_session"] == "2026-10-29"
    only_thursday = {("ZZZ", "2026-10-28", "2026-10-29"): "complete"}
    keys = set(s52.plan([e], {"ZZZ": [date(2026, 10, 29)]}, SPAN, only_thursday)["chains"])
    assert ("ZZZ", "2026-10-30", "2026-10-30") in keys  # Friday: the weekly's last day, still listed
    # a weekly on the reaction day itself: gone by day 2
    cal_friday = e31.event_row("ZZZ", date(2026, 10, 30), "before_open")
    keys = set(s52.plan([cal_friday], {"ZZZ": [date(2026, 10, 30)]}, SPAN, {})["chains"])
    assert ("ZZZ", "2026-11-02", "2026-10-30") not in keys


# ------------------------------------------------------------------------------------------------
# Chains.


def smile(spot: float, base: float, skew: float):
    return lambda k: base + skew * (spot - k) / spot


def make_chain(spot, years, vol_fn, strikes, q=0.0, half_spread=0.02):
    """{(strike, right): (bid, ask)}: American values at the smile, a spread of 2 x half_spread of the
    value (at least a cent a side)."""
    out = {}
    for k in strikes:
        for r in "CP":
            p = american.price(spot, k, years, vol_fn(k), r, 0.0, q)
            h = max(0.01, half_spread * p)
            out[(k, r)] = (round(max(p - h, 0.0), 2), round(p + h, 2))
    return out


def test_the_listed_strike_is_the_one_nearest_the_target_delta_by_the_american_model():
    spot, years = 100.0, 30 / 365
    chain = make_chain(spot, years, smile(spot, 0.4, 0.0), [float(k) for k in range(80, 125)])
    book = s52.DeltaBook(chain, spot, years, 0.0)
    for right in "CP":
        for target in (0.10, 0.20, 0.25, 0.40):
            full = min(
                (abs(book.am(right, k)[0] - target), k) for k in book.strikes(right) if book.am(right, k)
            )
            got = book.nearest(right, target)
            assert got[0] == full[1], (right, target)
    call40, put40 = book.nearest("C", 0.40), book.nearest("P", 0.40)
    assert call40[0] > spot > put40[0]  # a 40-delta call is out of the money, as is the put
    assert book.nearest("C", 0.10)[0] > book.nearest("C", 0.20)[0] > call40[0]


def test_a_dividend_yield_moves_the_call_delta_down_and_is_the_trailing_year_over_the_close():
    divs = [["2025-12-01", 0.5], ["2026-03-01", 0.5], ["2025-01-01", 9.0]]  # the last is over a year old
    assert s52.trailing_yield(divs, date(2026, 6, 1), 100.0) == pytest.approx(0.01)
    spot, years = 100.0, 60 / 365
    chain = make_chain(spot, years, smile(spot, 0.3, 0.0), [float(k) for k in range(80, 125)])
    plain = s52.DeltaBook(chain, spot, years, 0.0).am("C", 100.0)[0]
    yielding = s52.DeltaBook(chain, spot, years, 0.08).am("C", 100.0)[0]
    assert yielding < plain


def test_a_strike_with_no_two_sided_quote_is_never_picked():
    spot, years = 100.0, 30 / 365
    chain = make_chain(spot, years, smile(spot, 0.4, 0.0), [float(k) for k in range(90, 111)])
    best = s52.DeltaBook(chain, spot, years, 0.0).nearest("C", 0.40)[0]
    chain[(best, "C")] = (0.0, 0.05)  # no bid
    assert s52.DeltaBook(chain, spot, years, 0.0).nearest("C", 0.40)[0] != best


def test_the_risk_reversal_is_call_less_put_volatility_at_25_delta_over_the_atm_volatility():
    spot, years = 100.0, 30 / 365
    strikes = [float(k) for k in range(70, 135)]
    put_rich = s52.DeltaBook(make_chain(spot, years, smile(spot, 0.4, 0.5), strikes), spot, years, 0.0)
    call_rich = s52.DeltaBook(make_chain(spot, years, smile(spot, 0.4, -0.5), strikes), spot, years, 0.0)
    flat = s52.DeltaBook(make_chain(spot, years, smile(spot, 0.4, 0.0), strikes), spot, years, 0.0)
    assert put_rich.rr25() < -0.05 and call_rich.rr25() > 0.05 and abs(flat.rr25()) < 0.02


# ------------------------------------------------------------------------------------------------
# Arm C's side, the terciles.


def test_arm_c_takes_the_call_diagonal_when_puts_are_richer_and_the_put_one_when_calls_are():
    cuts = s52.tercile_cuts([-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3])
    assert cuts is not None and cuts[0] < 0 < cuts[1]
    assert s52.c_side(-0.3, cuts) == "C"  # puts richer: the call side
    assert s52.c_side(0.3, cuts) == "P"  # calls richer: the put side
    assert s52.c_side(0.0, cuts) is None  # the middle tercile takes no trade
    assert s52.c_side(None, cuts) is None and s52.c_side(0.3, None) is None
    assert s52.arm_spec("C", "C") == s52.SPECS["A-call"] and s52.arm_spec("C", "P") == s52.SPECS["A-put"]
    assert s52.arm_spec("C", None) is None
    assert s52.tercile_cuts([0.1, 0.2]) is None


# ------------------------------------------------------------------------------------------------
# The fills and the costs.

OPEN_2 = 2 * (1.00 + 0.10 + 0.02) + 1 * 0.00329  # two legs, one sell
CLOSE_2 = 2 * (0.10 + 0.02) + 1 * 0.00329


def test_a_call_diagonal_hand_computed():
    legs = [(1, date(2026, 11, 20), "C", 105.0), (-1, date(2026, 10, 30), "C", 110.0)]
    entries = [(3.00, 3.20), (1.00, 1.10)]
    exits = [
        {"mode": "quote", "quote": (3.50, 3.70), "spot": 100.0},
        {"mode": "quote", "quote": (0.20, 0.30), "spot": 100.0},
    ]
    p = s52.price_position("XYZ", legs, entries, exits)
    assert p["debit"] == pytest.approx(205.0)  # 100 x (3.10 - 1.05)
    assert p["exit_cash"] == pytest.approx(335.0)  # 100 x (3.60 - 0.25)
    # slippage: 0.125 x each spread x 100, four fills: 2.50 + 1.25 + 2.50 + 1.25
    assert p["working"]["slippage"] == pytest.approx(7.5)
    assert p["fees"] == pytest.approx(round(OPEN_2, 4) + round(CLOSE_2, 4))
    assert p["working"]["net"] == pytest.approx(130.0 - 7.5 - p["fees"])
    # stress: half the spread, no cap (4 fills at 10 + 5 + 10 + 5), fees x 1.5
    assert p["stress"]["slippage"] == pytest.approx(30.0)
    assert p["stress"]["net"] == pytest.approx(130.0 - 30.0 - 1.5 * p["fees"])
    assert p["exit_unquoted_legs"] == 0


def test_a_long_leg_with_no_exit_quote_is_sold_at_intrinsic_less_its_entry_spread_and_counted():
    legs = [(1, date(2026, 11, 20), "C", 105.0), (-1, date(2026, 10, 30), "C", 110.0)]
    entries = [(3.00, 3.20), (1.00, 1.10)]
    exits = [
        {"mode": "quote", "quote": None, "spot": 108.0},  # intrinsic 3.00, entry spread 0.20: sold at 2.80
        {"mode": "quote", "quote": (0.20, 0.30), "spot": 108.0},
    ]
    p = s52.price_position("XYZ", legs, entries, exits)
    assert p["exit_unquoted_legs"] == 1
    assert p["exit_cash"] == pytest.approx(100 * (2.80 - 0.25))
    assert p["working"]["slippage"] == pytest.approx(2.5 + 1.25 + 1.25)  # the unquoted leg pays none
    # close fee: both legs still traded out, the long is the one sold
    assert p["fees"] == pytest.approx(round(OPEN_2, 4) + round(CLOSE_2, 4))
    # a long worth less than its spread is floored at zero
    exits[0] = {"mode": "quote", "quote": None, "spot": 100.0}
    assert s52.price_position("XYZ", legs, entries, exits)["exit_cash"] == pytest.approx(-25.0)


def test_b2_hand_computed_with_a_short_put_that_has_no_exit_quote():
    w, m = date(2026, 10, 30), date(2026, 11, 20)
    legs = [(-1, w, "C", 110.0), (-1, w, "P", 90.0), (1, m, "C", 115.0), (1, m, "P", 85.0)]
    entries = [(1.00, 1.10), (0.80, 0.90), (1.50, 1.60), (1.20, 1.30)]
    exits = [
        {"mode": "quote", "quote": (0.10, 0.20), "spot": 100.0},
        {"mode": "quote", "quote": None, "spot": 100.0},  # bought back at 0 + its 0.10 entry spread
        {"mode": "quote", "quote": (1.60, 1.70), "spot": 100.0},
        {"mode": "quote", "quote": (1.00, 1.10), "spot": 100.0},
    ]
    p = s52.price_position("XYZ", legs, entries, exits)
    assert p["debit"] == pytest.approx(90.0)  # 105 + 85 - 155 - 125 = -90 of cash in
    assert p["exit_cash"] == pytest.approx(-15.0 - 10.0 + 165.0 + 105.0)
    assert p["exit_unquoted_legs"] == 1
    assert p["working"]["slippage"] == pytest.approx(5.0 + 3.75)
    open_fee = round(4 * 1.12 + 2 * 0.00329, 4)
    close_fee = round(4 * 0.12 + 2 * 0.00329, 4)
    assert p["fees"] == pytest.approx(open_fee + close_fee)
    assert p["working"]["net"] == pytest.approx(155.0 - 8.75 - open_fee - close_fee)


def test_a_settled_short_pays_intrinsic_with_no_slippage_and_the_settlement_fee_when_in_the_money():
    w, m = date(2026, 10, 30), date(2026, 11, 20)
    legs = [(1, m, "C", 105.0), (-1, w, "C", 110.0)]
    entries = [(3.00, 3.20), (1.00, 1.10)]
    exits = [
        {"mode": "quote", "quote": (3.50, 3.70), "spot": 112.0},
        {"mode": "settle", "spot": 112.0},
    ]
    p = s52.price_position("XYZ", legs, entries, exits)
    assert p["exit_cash"] == pytest.approx(100 * (3.60 - 2.0))
    assert p["working"]["slippage"] == pytest.approx(2.5 + 1.25 + 2.5)
    close_one_leg = round(1 * 0.12 + 1 * 0.00329, 4)  # only the long is traded out, and it is the sell
    assert p["fees"] == pytest.approx(round(OPEN_2, 4) + close_one_leg + 5.0)
    exits[1] = {"mode": "settle", "spot": 100.0}  # out of the money: no settlement fee
    assert s52.price_position("XYZ", legs, entries, exits)["fees"] == pytest.approx(
        round(OPEN_2, 4) + close_one_leg
    )


# ------------------------------------------------------------------------------------------------
# The control pairing, Holm and the verdict.


def trade(net, control, session, half=0, name=0, stress=None, debit=100.0):
    return {
        "net": net, "stress_net": net if stress is None else stress, "debit": debit,
        "control_net": control, "entry_session": session, "time_half": half, "name_half": name,
        "exit_unquoted_legs": 0,
    }  # fmt: skip


def test_the_pair_is_the_trade_less_its_own_control_and_a_missing_control_is_left_out():
    ts = [trade(10.0, 4.0, "a"), trade(6.0, 1.0, "b"), trade(8.0, None, "c")]
    s = s52.arm_stats(ts, bootstrap=False)
    assert s["trades"] == 3 and s["pairs"] == 2
    assert s["pair"]["mean"] == pytest.approx(5.5)  # (6 + 5) / 2
    assert s["net"]["mean"] == pytest.approx(8.0)
    assert s["worst"] == 6.0 and s["without_best_five"] is None  # 3 trades: fewer than six
    assert s["mean_share_of_debit"] == pytest.approx(0.08)


def test_holm_runs_across_all_ten_arms_and_an_arm_under_the_floor_counts_as_one():
    def stat(n, p_net, p_pair=0.5):
        return {"design": {"trades": n, "net": {"p": p_net}, "pair": {"p": p_pair}}}

    stats = {arm: stat(200, 0.5) for arm in s52.ARMS}
    assert len(stats) == 10
    stats["A-call"] = stat(200, 0.004)  # 0.05 / 10 = 0.005: rejected
    stats["B2"] = stat(200, 0.006)  # next step is 0.05 / 9 = 0.00556: not
    stats["C"] = stat(149, 0.0001)  # under the floor: p = 1, and it blocks nothing above it
    p_net, _p_pair, h_net, _h_pair = s52.holm_flags(stats)
    assert p_net["C"] == 1.0
    assert h_net["A-call"] is True and h_net["B2"] is False and h_net["C"] is False


def passing_design():
    return {
        "trades": 150,
        "net": {"mean": 5.0, "t": 2.5}, "pair": {"mean": 3.0, "t": 2.1},
        "net_by_time_half": [1.0, 2.0], "net_by_name_half": [1.0, 2.0],
        "stress_mean": 0.5, "without_best_five": 0.2,
    }  # fmt: skip


def holdout(n, net=1.0, pair=1.0):
    return {"trades": n, "net": {"mean": net}, "pair": {"mean": pair}}


def test_the_floors_150_design_and_100_holdout_trades_make_no_verdict():
    d = passing_design()
    assert s52.verdict({**d, "trades": 149}, holdout(500), True, True)["verdict"] == "no verdict"
    assert s52.verdict(d, holdout(99), True, True)["verdict"] == "no verdict"
    assert s52.verdict(d, holdout(100), True, True)["verdict"] == "pass"
    assert s52.verdict(d, holdout(100, pair=-1.0), True, True)["verdict"] == "fail"
    assert s52.verdict(d, holdout(100, net=0.0), True, True)["verdict"] == "fail"


@pytest.mark.parametrize(
    "change",
    [
        {"net": {"mean": 5.0, "t": 2.0}},  # t must be above 2
        {"pair": {"mean": 3.0, "t": 1.9}},
        {"net_by_time_half": [1.0, -0.1]},
        {"net_by_name_half": [-1.0, 2.0]},
        {"stress_mean": -0.1},
        {"without_best_five": 0.0},
    ],
)
def test_each_design_check_can_fail_the_arm(change):
    out = s52.verdict({**passing_design(), **change}, holdout(500), True, True)
    assert out["verdict"] == "fail"
    assert not all(out["design_checks"].values())


def test_holm_rejection_is_part_of_the_pass_and_the_lenient_reading_is_reported():
    out = s52.verdict(passing_design(), holdout(500), True, False)
    assert out["verdict"] == "fail" and out["pass_if_holm_on_net_only"] is True
    assert s52.verdict(passing_design(), holdout(500), False, True)["pass_if_holm_on_net_only"] is False


# ------------------------------------------------------------------------------------------------
# The cheap cut.


def test_the_cheap_history_leaves_out_five_sessions_either_side_of_every_report_and_the_current_one():
    sessions = e31.sessions_between(date(2025, 1, 1), date(2025, 12, 31))
    series = {d: 0.30 for d in sessions}
    entry = date(2025, 9, 15)
    earlier = date(2025, 5, 14)
    excluded = s52.report_windows([earlier, date(2025, 9, 10), date(2026, 1, 20)], date(2025, 9, 10))
    got = s52.trailing_values(series, entry, excluded)
    assert earlier in excluded
    i = sessions.index(earlier)
    window = set(sessions[i - 5 : i + 6])
    assert window <= excluded and sessions[i - 6] not in excluded and sessions[i + 6] not in excluded
    assert date(2025, 9, 9) in excluded and date(2025, 9, 12) in excluded  # the current report's ramp
    assert date(2026, 1, 20) not in excluded  # a later report is not "earlier"
    before = [d for d in series if d < entry]  # under 252 sessions of 2025 precede the entry
    assert len(got) == len([d for d in before if d not in excluded])
    assert len(got) < len(before)
    far = {d: 0.5 for d in e31.sessions_between(date(2023, 1, 2), date(2025, 9, 15))}
    assert len(s52.trailing_values(far, entry, set())) == s52.HISTORY_SESSIONS


def test_the_cheap_reading_is_the_share_of_history_at_or_below_and_needs_sixty_days():
    hist = [i / 100 for i in range(1, 101)]
    assert s52.cheap_reading(0.25, hist) == pytest.approx(0.25)
    assert s52.cheap_reading(0.0, hist) == 0.0
    assert s52.cheap_reading(0.25, hist[:59]) is None
    assert s52.cheap_reading(None, hist) is None
    assert s52.cheap_reading(0.25, hist[:60]) is not None


def test_the_entry_measure_is_the_latest_day_up_to_two_sessions_before():
    series = {date(2025, 3, 3): 0.3, date(2025, 3, 5): 0.4}  # Mon, Wed
    assert s52.entry_measure(series, date(2025, 3, 6)) == 0.4
    assert s52.entry_measure(series, date(2025, 3, 7)) == 0.4  # two sessions after Wed: the Wed is in reach
    assert s52.entry_measure(series, date(2025, 3, 10)) is None  # Wed is three sessions back


def test_the_monthly_atm_iv_reads_the_standard_monthly_nearest_thirty_days():
    session = date(2025, 3, 3)
    rows = []
    for exp, vol in ((date(2025, 3, 21), 0.50), (date(2025, 4, 18), 0.35), (date(2025, 3, 14), 0.9)):
        for k in (98.0, 100.0, 102.0):
            rows += [(exp, k, "Call", vol), (exp, k, "Put", vol + 0.02)]
    # 03-21 is 18 days out, 04-18 is 46: the nearer to 30 is 03-21 (12 vs 15); 03-14 is not a third Friday
    assert s52.monthly_atm_iv(rows, 100.4, session) == pytest.approx(0.51)
    assert s52.monthly_atm_iv(
        [r for r in rows if r[0] != date(2025, 3, 21)], 100.4, session
    ) == pytest.approx(0.36)
    assert s52.monthly_atm_iv(rows, 130.0, session) is None  # the strikes are not near the close


# ------------------------------------------------------------------------------------------------
# The study end to end on invented stores.

BASE_W, BASE_M = (0.60, 0.35), (0.45, 0.42)  # weekly and monthly vol at the entry and afterwards


def build_world(tmp_path=None):
    """Four design-window names that are kept (put-rich, put-rich, call-rich, flat), one whose report
    week lists no weekly, one under $10 and one with thin open interest."""
    names = {"PUT1": 0.5, "PUT2": 0.5, "CALL1": -0.5, "FLAT": 0.0, "NOWK": 0.0, "CHEAP": 0.0, "THIN": 0.0}
    report = date(2021, 10, 27)
    events = [e31.event_row(n, report, "after_close") for n in names]
    reports = {n: [report] for n in names}
    spot = {n: (8.0 if n == "CHEAP" else 100.0) for n in names}
    conn = sqlite3.connect(":memory:")
    conn.executescript(chainfetch.SCHEMA)
    status = {}
    for n in ("NOWK",):
        for exp in (date(2021, 10, 29), date(2021, 11, 5)):
            chainfetch.write_chain(conn, (n, "2021-10-27", exp.isoformat()), [])
    status = s52._status(conn)
    planned = s52.plan(events, reports, SPAN, status)
    entries = {report, report - timedelta(days=28)}
    for sym, session, exp in planned["chains"]:
        s, x = date.fromisoformat(session), date.fromisoformat(exp)
        monthly = 15 <= x.day <= 21
        vols = BASE_M if monthly else BASE_W
        v = vols[0] if s in entries else vols[1]
        sp = spot[sym]
        # strikes at a grid scaled to the spot
        strikes = [round(sp * (0.7 + 0.01 * i), 1) for i in range(0, 61)]
        ch = make_chain(sp, (x - s).days / 365.0, smile(sp, v, names[sym]), strikes)
        rows = [{"strike": k, "right": r, "bid": b, "ask": a} for (k, r), (b, a) in ch.items()]
        chainfetch.write_chain(conn, (sym, session, exp), rows)
    status = s52._status(conn)
    oi = sqlite3.connect(":memory:")
    oi.executescript(
        "CREATE TABLE oi_days (symbol TEXT, session TEXT, status TEXT);"
        "CREATE TABLE oi_contracts (symbol TEXT, session TEXT, expiration TEXT, right TEXT, strike REAL, "
        "open_interest INTEGER);"
    )
    for n in names:
        oi.execute("INSERT INTO oi_days VALUES (?, '2021-10-27', 'complete')", (n,))
        for exp in ("2021-10-29", "2021-11-19"):
            oi.execute(
                "INSERT INTO oi_contracts VALUES (?, '2021-10-27', ?, 'C', 100.0, ?)",
                (n, exp, 100 if n == "THIN" else 5000),
            )
    days = e31.sessions_between(date(2021, 3, 1), date(2021, 12, 31))
    bars = {n: {d: (spot[n],) * 4 for d in days} for n in names}
    divs = {n: [] for n in names}
    history_days = [d for d in days if d < report][-120:]
    iv_hist = {}
    for n in names:
        series = {d: 0.20 + 0.004 * i for i, d in enumerate(history_days)}
        series[report] = {"PUT1": 0.21, "PUT2": 0.30, "CALL1": 0.50}.get(n, 0.70)  # cheapest first
        iv_hist[n] = series
    return events, reports, conn, oi, status, bars, divs, iv_hist


@pytest.fixture(scope="module")
def world():
    events, reports, conn, oi, status, bars, divs, iv_hist = build_world()
    out = s52.study(conn, oi, events, reports, SPAN, status, bars, divs, iv_hist)
    return out, (events, reports, conn, bars, status)


def test_the_study_drops_and_counts_by_reason(world):
    (trades, recs, info), _ = world
    assert info["drops"] == {"weekly_unlisted": 1, "close_under_10": 1, "open_interest_under_floor": 1}
    assert sorted(r["e"]["symbol"] for r in recs) == ["CALL1", "FLAT", "PUT1", "PUT2"]
    assert {t["symbol"] for t in trades} == {"CALL1", "FLAT", "PUT1", "PUT2"}


def test_every_arm_trades_with_the_declared_legs_and_a_paired_control(world):
    (trades, _recs, _info), _ = world
    by = {(t["symbol"], t["arm"]): t for t in trades}
    for name in ("CALL1", "FLAT", "PUT1", "PUT2"):
        for arm in ("A-call", "A-put", "B1", "B2"):
            t = by[(name, arm)]
            assert t["control_net"] is not None and t["window"] == "design"
            assert "net_day2" in t and "net_expiry" in t
    # B1 is both A diagonals: its debit is the two debits' sum
    t = {a: by[("PUT1", a)] for a in ("A-call", "A-put", "B1")}
    assert t["B1"]["debit"] == pytest.approx(t["A-call"]["debit"] + t["A-put"]["debit"])
    assert t["A-call"]["debit"] > 0 and t["A-call"]["exit_unquoted_legs"] >= 0


def test_arm_c_follows_the_risk_reversal_cut_fitted_on_the_design_window(world):
    (trades, _recs, info), _ = world
    cuts = info["cuts"]["rr25_terciles"]
    assert cuts is not None
    by = {(t["symbol"], t["arm"]): t for t in trades}
    # the put-rich names take the call diagonal, the call-rich name the put diagonal
    assert by[("PUT1", "C")]["net"] == pytest.approx(by[("PUT1", "A-call")]["net"])
    assert by[("CALL1", "C")]["net"] == pytest.approx(by[("CALL1", "A-put")]["net"])


def test_the_cheap_cut_is_the_design_windows_bottom_tercile(world):
    (trades, _recs, info), _ = world
    flags = {t["symbol"]: t["cheap"] for t in trades if t["arm"] == "A-call"}
    assert info["cuts"]["cheap_reading"] is not None
    assert flags["PUT1"] is True and flags["PUT2"] is True
    assert flags["CALL1"] is False and flags["FLAT"] is False


def test_the_back_month_iv_change_shows_the_weekly_falling_more_than_the_monthly(world):
    (_trades, recs, _info), (_events, _reports, conn, bars, _status) = world
    change = s52.back_month_change(recs, conn, bars)["design"]
    assert change["weekly"]["mean_change"] < change["monthly"]["mean_change"] < 0


def test_the_evaluation_judges_ten_arms_and_names_no_verdict_under_the_floor(world):
    (trades, _recs, _info), _ = world
    out = s52.evaluate(trades)
    assert set(out) == set(s52.ARMS) and len(out) == 10
    assert all(v["verdict"] == "no verdict" for v in out.values())  # four names: far under 150
    assert out["A-call"]["design"]["trades"] == 4 and out["A-call|cheap"]["design"]["trades"] == 2


# ------------------------------------------------------------------------------------------------
# The refusal and the request files.


def _patch_inputs(monkeypatch, status_conn, oi_conn=None):
    events = [e31.event_row("ZZZ", date(2026, 10, 28), "after_close")]
    reports = {"ZZZ": [date(2026, 10, 28)]}
    monkeypatch.setattr(s52, "_inputs", lambda: (events, {}, reports, SPAN))
    monkeypatch.setattr(s52, "_stores", lambda: (status_conn, oi_conn))


def test_study_refuses_while_requests_are_unfetched(monkeypatch, capsys):
    conn = sqlite3.connect(":memory:")
    conn.executescript(chainfetch.SCHEMA)
    _patch_inputs(monkeypatch, conn)
    args = type("A", (), {"host": "x", "port": 1, "out_dir": None})()
    assert s52.cmd_study(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["refused"] == "requests not fetched"
    assert out["missing_chain_requests"] == 10 and out["missing_oi_name_days"] == 1


def test_study_still_refuses_for_open_interest_alone(monkeypatch, capsys):
    event = e31.event_row("ZZZ", date(2026, 10, 28), "after_close")
    reports = {"ZZZ": [date(2026, 10, 28)]}
    conn = sqlite3.connect(":memory:")
    conn.executescript(chainfetch.SCHEMA)
    planned = s52.plan([event], reports, SPAN, {})
    for key in planned["chains"]:
        chainfetch.write_chain(conn, key, [{"strike": 100.0, "right": "C", "bid": 1.0, "ask": 1.1}])
    oi = sqlite3.connect(":memory:")
    oi.executescript("CREATE TABLE oi_days (symbol TEXT, session TEXT, status TEXT);")
    _patch_inputs(monkeypatch, conn, oi)
    args = type("A", (), {"host": "x", "port": 1, "out_dir": None})()
    assert s52.cmd_study(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["missing_chain_requests"] == 0 and out["missing_oi_name_days"] == 1


def test_requests_write_what_is_missing_in_the_fetch_chains_format(monkeypatch, tmp_path, capsys):
    conn = sqlite3.connect(":memory:")
    conn.executescript(chainfetch.SCHEMA)
    chainfetch.write_chain(
        conn, ("ZZZ", "2026-10-28", "2026-10-30"), [{"strike": 100.0, "right": "C", "bid": 1.0, "ask": 1.1}]
    )
    _patch_inputs(monkeypatch, conn)
    args = type("A", (), {"out_dir": str(tmp_path)})()
    assert s52.cmd_requests(args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["planned"] == 10 and printed["already_stored"] == 1 and printed["requests"] == 9
    doc = json.loads((tmp_path / "eod-chain-requests.json").read_text(encoding="utf-8"))
    assert len(doc["requests"]) == 9
    assert set(doc["requests"][0]) == {"symbol", "session", "expiration"}
    assert {"symbol": "ZZZ", "session": "2026-10-28", "expiration": "2026-10-30"} not in doc["requests"]
    oi = json.loads((tmp_path / "oi-plan.json").read_text(encoding="utf-8"))
    assert oi == {"sessions": {"2026-10-28": ["ZZZ"]}}


def test_a_request_run_after_an_empty_first_friday_moves_to_the_next():
    event = e31.event_row("ZZZ", date(2026, 10, 28), "after_close")
    reports = {"ZZZ": [date(2026, 10, 28)]}
    before = set(s52.plan([event], reports, SPAN, {})["chains"])
    after = set(s52.plan([event], reports, SPAN, {("ZZZ", "2026-10-28", "2026-10-30"): "empty"})["chains"])
    assert ("ZZZ", "2026-10-29", "2026-10-30") in before
    assert ("ZZZ", "2026-10-29", "2026-10-30") not in after
    assert ("ZZZ", "2026-10-29", "2026-11-06") in after


def test_the_calendar_used_here_is_not_the_host_date():
    # CI runs on UTC: nothing in the study reads today's date
    src = (SCRIPTS / "earnings_diagonal_item52.py").read_text(encoding="utf-8")
    assert "date.today" not in src and "datetime.now" not in src
    assert cal.is_trading_day(date(2026, 10, 28))
