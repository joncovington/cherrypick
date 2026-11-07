"""Hypothesis 57's study (scripts/index_put_weekly_item57.py): the weekly calendar, the unhedged trade and
the two-arm judge. Invented chains and cells only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "index_put_weekly_item57", SCRIPTS / "index_put_weekly_item57.py"
)
s57 = importlib.util.module_from_spec(_spec)
sys.modules["index_put_weekly_item57"] = s57
_spec.loader.exec_module(s57)
i46 = s57.i46


# ------------------------------------------------------------------------------------------------
# The calendar.


def test_the_weekly_calendar_is_every_friday_that_is_not_a_monthly():
    weekly = s57.weekly_expiries()
    every = s57.all_expiries(s57.FIRST_EXPIRY, s57.LAST_EXPIRY)
    monthly = set(i46.cycles(date(2016, 3, 1), s57.LAST_EXPIRY))
    assert date(2026, 10, 9) in weekly and date(2026, 10, 16) not in weekly
    assert not monthly & set(weekly)
    assert set(every) - set(weekly) == monthly | s57.overlapping()
    assert len(monthly) == 128 and len(every) == len(weekly) + 128 + len(s57.overlapping())
    assert weekly == sorted(set(weekly))
    assert weekly[0] == date(2016, 3, 4) and weekly[-1] == date(2026, 10, 9)
    assert {d.weekday() for d in weekly} <= {3, 4}  # a Friday, or a Thursday for a holiday Friday


def test_the_monthly_exclusion_is_what_removes_the_monthlies():
    # Break it on purpose: without the exclusion the third Fridays are back in the sample.
    every = s57.all_expiries(s57.FIRST_EXPIRY, s57.LAST_EXPIRY)
    assert date(2026, 10, 16) in every and date(2025, 4, 17) in every
    weekly = s57.weekly_expiries()
    assert date(2026, 10, 16) not in weekly and date(2025, 4, 17) not in weekly


def test_a_holiday_friday_moves_to_the_thursday_and_a_monthly_one_is_still_excluded():
    weekly = s57.weekly_expiries()
    # Good Friday 2021-04-02 is not a monthly week: the Thursday stands in.
    assert date(2021, 4, 1) in weekly and date(2021, 4, 2) not in weekly
    # Good Friday 2022-04-15 is the third Friday: the Thursday is the monthly, so the week is out.
    assert date(2022, 4, 14) not in weekly and date(2022, 4, 15) not in weekly
    assert s57.expiry_for(date(2021, 4, 2)) == date(2021, 4, 1)
    assert s57.expiry_for(date(2021, 4, 9)) == date(2021, 4, 9)
    # New Year's Day 2021 fell on a Friday.
    assert date(2020, 12, 31) in weekly and date(2021, 1, 1) not in weekly


def test_a_week_is_never_listed_twice():
    every = s57.all_expiries(date(2021, 3, 1), date(2021, 4, 30))
    assert len(every) == len(set(every))
    assert [e.isoformat() for e in every[:5]] == [
        "2021-03-05",
        "2021-03-12",
        "2021-03-19",
        "2021-03-26",
        "2021-04-01",
    ]


def test_a_holiday_week_that_shares_a_session_with_a_monthly_trade_is_left_out():
    # Christmas 2020 is a Friday: the Thursday expiry enters on 12-17, inside the monthly of 12-18.
    left_out = s57.overlapping()
    assert date(2020, 12, 24) in left_out and date(2026, 6, 12) in left_out
    weekly = s57.weekly_expiries()
    assert not left_out & set(weekly)
    assert date(2020, 12, 24) not in weekly and date(2020, 12, 31) in weekly
    # Break it on purpose: the weeks before the filter overlap a monthly's trade.
    assert s57.disjoint_from_monthly(s57.not_monthly(s57.FIRST_EXPIRY, s57.LAST_EXPIRY)) == len(left_out) > 0


def test_weekly_trades_are_disjoint_from_every_monthly_seven_day_trade():
    weekly = s57.weekly_expiries()
    assert s57.disjoint_from_monthly(weekly) == 0
    # Break it on purpose: the monthlies themselves overlap, and so does a trade held a day too long.
    monthlies_in_window = s57.all_expiries(s57.FIRST_EXPIRY, s57.LAST_EXPIRY)
    assert s57.disjoint_from_monthly(monthlies_in_window) > 0
    m = date(2026, 10, 16)
    assert s57.overlaps(s57.trade_window(m), s57.trade_window(m))
    late = (s57.trade_window(date(2026, 10, 9))[0], m)  # held through the monthly's week
    assert s57.overlaps(late, s57.trade_window(m))


def test_a_trade_entered_when_a_monthly_expires_does_not_overlap_it():
    monthly, weekly = s57.trade_window(date(2026, 10, 16)), s57.trade_window(date(2026, 10, 23))
    assert weekly[0] == monthly[1]
    assert not s57.overlaps(weekly, monthly) and not s57.overlaps(monthly, weekly)


def test_the_entry_is_seven_days_back_or_the_next_session():
    assert s57.trade_window(date(2026, 10, 9)) == (date(2026, 10, 2), date(2026, 10, 9))
    # Good Friday 2026-04-03 is a holiday, so the 2026-04-10 expiry enters on the Monday.
    assert s57.trade_window(date(2026, 4, 10))[0] == date(2026, 4, 6)
    assert all((e - s57.trade_window(e)[0]).days <= 7 for e in s57.weekly_expiries())


def test_each_request_is_a_ten_day_path_that_holds_the_entry():
    r = s57.path_request("SPY", date(2026, 10, 9))
    assert r == {"symbol": "SPY", "expiration": "2026-10-09", "start": "2026-09-29", "through": "2026-10-09"}
    for e in s57.weekly_expiries():
        assert date.fromisoformat(s57.path_request("SPY", e)["start"]) <= s57.trade_window(e)[0]


def test_every_name_gets_every_weekly_and_the_names_are_the_equity_index_four():
    assert s57.MEMBERS == ("SPXW", "SPY", "QQQ", "IWM")
    reqs = s57.build_requests()
    assert len(reqs) == 4 * len(s57.weekly_expiries())
    assert {r["symbol"] for r in reqs} == set(s57.MEMBERS)


def test_the_forward_read_starts_after_the_thirtieth_and_includes_the_monthlies():
    assert s57.forward_expiries(date(2026, 10, 30)) == []
    got = s57.forward_expiries(date(2026, 11, 20))
    assert got == [date(2026, 11, 6), date(2026, 11, 13), date(2026, 11, 20)]
    assert s57.is_monthly(date(2026, 11, 20)) and not s57.is_monthly(date(2026, 11, 13))


def test_no_replay_corrections_are_recorded_yet():
    assert s57.REPLAY_CORRECTIONS == []


# ------------------------------------------------------------------------------------------------
# The unhedged trade, on invented chains priced by Black.

EXPIRY = date(2026, 10, 9)


def chain(fwd=100.0, vol=0.20, years=7 / 365, strikes=range(70, 131), half=0.05):
    from cherrypick.core import bs

    out = []
    for k in strikes:
        for right in ("C", "P"):
            p = bs.price(fwd, float(k), years, vol, right, 0.0)
            out.append((float(k), right, max(0.0, round(p - half, 2)), round(p + half, 2)))
    return out


def book(fwds: dict[date, float]):
    sessions = {d: chain(fwd=f, years=max(i46.years_to(d, EXPIRY), 0.5 / 365)) for d, f in fwds.items()}
    return sessions, {d: i46.forward(ch) for d, ch in sessions.items()}


def days(step=0.0):
    ds = i46.trading_days(date(2026, 10, 2), EXPIRY)
    return {d: 100.0 + i * step for i, d in enumerate(ds)}


def test_the_hedged_arm_is_item_46s_trade_unchanged():
    sessions, fwds = book(days(step=-0.3))
    both = s57.trade_pair("SPY", sessions, fwds, EXPIRY, "put10")
    assert both["hedged"] == i46.trade("SPY", sessions, fwds, EXPIRY, 7, "put10")


def test_the_unhedged_trade_keeps_the_premium_and_payoff_and_takes_the_hedge_out():
    sessions, fwds = book(days(step=-0.3))
    both = s57.trade_pair("SPY", sessions, fwds, EXPIRY, "put10")
    h, u = both["hedged"], both["unhedged"]
    assert u["premium"] == h["premium"] and u["payoff"] == h["payoff"] and u["strikes"] == h["strikes"]
    assert "hedge_pnl" not in u
    assert abs(u["net"] - (u["premium"] - u["payoff"] - u["costs"])) < 0.011
    assert u["costs"] < h["costs"]  # the hedge's cost is gone
    assert abs((h["net"] - u["net"]) - (h["hedge_pnl"] - (h["costs"] - u["costs"]))) < 0.011
    assert abs(u["net_bp"] - u["net"] / (100 * fwds[date(2026, 10, 2)]) * 1e4) < 1e-3
    assert abs(u["cost_bp"] - u["costs"] / (100 * fwds[date(2026, 10, 2)]) * 1e4) < 1e-3


def test_the_unhedged_arm_does_not_move_with_the_hedge_and_the_hedged_one_does(monkeypatch):
    sessions, fwds = book(days(step=-0.3))
    base = s57.trade_pair("SPY", sessions, fwds, EXPIRY, "put10")
    # Break the hedge on purpose: a hedge that earns 1,000 and costs 3.
    monkeypatch.setattr(i46, "hedge", lambda *a, **k: {"pnl": 1000.0, "cost": 3.0, "rebalances": 1})
    broken = s57.trade_pair("SPY", sessions, fwds, EXPIRY, "put10")
    assert abs(broken["hedged"]["net"] - base["hedged"]["net"]) > 900
    # The unhedged net is the premium less the payoff and the option-side costs, whatever the hedge did.
    assert abs(broken["unhedged"]["net"] - base["unhedged"]["net"]) < 0.011
    assert broken["unhedged"]["net"] != broken["hedged"]["net"]


def test_the_unhedged_net_is_the_hedged_one_less_the_hedge():
    sessions, fwds = book(days(step=-0.3))
    h = s57.trade_pair("SPY", sessions, fwds, EXPIRY, "put10")["hedged"]
    s = i46.entry_session(EXPIRY, 7)
    contracts = i46.pick_leg(sessions[s], fwds[s], i46.years_to(s, EXPIRY), "put10")
    hg = i46.hedge(contracts, sessions, fwds, s, EXPIRY)
    both = s57.trade_pair("SPY", sessions, fwds, EXPIRY, "put10")
    assert abs(both["unhedged"]["net"] - (h["net"] - hg["pnl"] + hg["cost"])) < 0.011


def test_a_trade_pair_says_why_it_is_dropped():
    sessions, fwds = book(days())
    gone = {d: ch for d, ch in sessions.items() if d != EXPIRY}
    gone_f = {d: i46.forward(ch) for d, ch in gone.items()}
    assert s57.trade_pair("SPY", gone, gone_f, EXPIRY, "put10") == {"drop": "expiry_missing"}
    early = {d: ch for d, ch in sessions.items() if d != date(2026, 10, 2)}
    assert s57.trade_pair("SPY", early, {d: fwds[d] for d in early}, EXPIRY, "put10") == {
        "drop": "entry_missing"
    }


def test_a_put_expiring_in_the_money_loses_its_payoff_in_both_arms():
    sessions, fwds = book(days(step=-1.5))  # the forward falls through the strike
    both = s57.trade_pair("SPY", sessions, fwds, EXPIRY, "put10")
    assert both["unhedged"]["payoff"] > 0
    assert both["unhedged"]["net"] < both["unhedged"]["premium"]


# ------------------------------------------------------------------------------------------------
# The judge: item 46's conditions over the two declared arms.


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


def _arms(arm, cell):
    other = "unhedged" if arm == "hedged" else "hedged"
    return {arm: cell, other: _cell(p=0.5, mean_bp=-1.0)}


def test_an_arm_passes_only_when_every_condition_holds():
    for arm in s57.ARMS:
        cells = _arms(arm, _cell())
        words = s57.judge_arms(cells)
        assert words[arm] == "pass" and s57.study_word(words) == "pass"
        for broken in (
            {"halves": {"before": -0.1, "after": 3.0}},
            {"halves": {"before": 1.0, "after": None}},
            {"stressed_mean_bp": -0.1},
            {"names_positive": 2},  # three of four is the bar
            {"mean_bp": -2.0},
            {"p": 0.02},  # under 0.025 alone, over its Holm step across two
        ):
            words = s57.judge_arms(_arms(arm, _cell(**broken)))
            assert words[arm] == "fail", broken
        assert s57.judge_arms(_arms(arm, _cell(names_positive=3)))[arm] == "pass"


def test_holm_runs_across_the_two_arms_at_a_quarter_of_a_percent_split_in_two():
    # Two small p: the smaller needs 0.0125, the larger 0.025.
    both = {"hedged": _cell(p=0.012), "unhedged": _cell(p=0.024)}
    assert s57.judge_arms(both) == {"hedged": "pass", "unhedged": "pass"}
    # 0.013 misses the first step, and the larger is never reached.
    miss = {"hedged": _cell(p=0.013), "unhedged": _cell(p=0.024)}
    assert s57.judge_arms(miss) == {"hedged": "fail", "unhedged": "fail"}
    # Alone, 0.02 would pass at 0.025: the second arm is what makes it fail.
    assert s57.ALPHA == 0.025


def test_an_arm_under_eighty_cycles_has_no_verdict_and_leaves_the_family_at_two():
    words = s57.judge_arms({"hedged": _cell(enough=False), "unhedged": _cell(p=0.012)})
    assert words == {"hedged": "no verdict", "unhedged": "pass"}
    nothing = s57.judge_arms({"hedged": _cell(enough=False), "unhedged": _cell(enough=False)})
    assert s57.study_word(nothing) == "no verdict"
    assert s57.study_word({"hedged": "fail", "unhedged": "no verdict"}) == "fail"
    assert s57.MIN_CYCLES == 80


def test_the_family_is_exactly_the_two_declared_arms():
    with pytest.raises(ValueError):
        s57.judge_arms({"hedged": _cell()})
    with pytest.raises(ValueError):
        s57.judge_arms({"hedged": _cell(), "unhedged": _cell(), "extra": _cell()})


def test_the_lag_is_one_and_the_halves_split_at_the_declared_date():
    assert i46.nw_lag(s57.TENOR) == 1
    assert i46.TIME_SPLIT == date(2021, 6, 1)
    per_name = {
        "SPXW": {
            "2020-03-20": {"net_bp": 4.0, "cost_bp": 1.0},
            "2022-03-18": {"net_bp": -2.0, "cost_bp": 1.0},
        },
        "SPY": {"2020-03-20": {"net_bp": 2.0, "cost_bp": 1.0}},
    }
    c = i46.cell_stats(per_name, s57.MEMBERS, s57.TENOR)
    assert c["halves"] == {"before": 3.0, "after": -2.0} and c["names_positive"] == 2


def test_a_weekly_is_a_cycle_in_its_own_right():
    per_name = {"SPXW": {"2026-10-02": 4.0}, "SPY": {"2026-10-02": 6.0, "2026-10-09": 1.0}}
    assert i46.group_cycle_means(per_name, s57.MEMBERS) == {"2026-10-02": 5.0, "2026-10-09": 1.0}
    assert timedelta(days=7) == date(2026, 10, 9) - date(2026, 10, 2)
