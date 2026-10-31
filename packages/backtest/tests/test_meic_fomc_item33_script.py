"""Hypothesis 33's study (scripts/meic_fomc_item33.py): the calendar parse, the permutation test, the
halves, the holdout reading, the no-verdict floor and the unlock check. Invented sessions only, no store
and no real net.

Each guard is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("meic_fomc_item33", SCRIPTS / "meic_fomc_item33.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

START, END = "2022-05-16", "2025-06-30"
H_START, H_END = "2025-07-01", "2026-10-12"


def weekdays(a: str, b: str) -> list[str]:
    d, end, out = date.fromisoformat(a), date.fromisoformat(b), []
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def book(a=START, b=END, fed_every=33, fed_shift=-400.0, seed=1, only_half=None, mid=None):
    """Noisy invented session nets and the Fed days among them: every `fed_every`th session is a Fed
    day and takes `fed_shift`; `only_half` ('first'/'second') confines the shift to one half."""
    rng = random.Random(seed)
    days = weekdays(a, b)
    mid = mid or st.midpoint(a, b)
    nets, fed = {}, set()
    for i, d in enumerate(days):
        nets[d] = rng.gauss(30.0, 150.0)
        if i % fed_every == 5:
            fed.add(d)
            if only_half is None or (only_half == "first") == (d < mid):
                nets[d] += fed_shift
    return nets, fed


# ------------------------------------------------------------------------------------------------
# The calendar.

PAGE = """
<h4><a>2023 FOMC Meetings</a></h4>
<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>Jan/Feb</strong></div>
<div class="fomc-meeting__date col-lg-1">31-1</div></div>
<div class="row"><div class="x"><strong>March</strong></div>
<div class="fomc-meeting__date col-lg-1">21-22*</div></div>
<div class="row"><div class="x"><strong>August</strong></div>
<div class="fomc-meeting__date col-lg-2">22 (notation vote)</div></div>
<h4><a>2022 FOMC Meetings</a></h4>
<div class="row"><div class="x"><strong>Dec/Jan</strong></div>
<div class="fomc-meeting__date col-lg-1">30-4</div></div>
<div class="row"><div class="x"><strong>June</strong></div>
<div class="fomc-meeting__date col-lg-1">14-15*</div></div>
"""


def test_the_decision_day_is_the_last_day_of_the_meeting():
    got = st.parse_fomc(PAGE)
    assert "2023-02-01" in got["scheduled"] and "2023-03-22" in got["scheduled"]
    assert "2022-06-15" in got["scheduled"]
    assert "2023-01-31" not in got["scheduled"] and "2023-03-21" not in got["scheduled"]


def test_a_year_end_straddle_decides_in_the_next_year():
    assert "2023-01-04" in st.parse_fomc(PAGE)["scheduled"]


def test_a_notation_vote_is_unscheduled_not_a_decision_day():
    got = st.parse_fomc(PAGE)
    assert got["unscheduled"] == ["2023-08-22"]
    assert "2023-08-22" not in got["scheduled"]


def test_cpi_dates_and_a_hand_saved_list_parse():
    assert st.parse_cpi("<td>Jan. 12, 2023</td><td>February 14, 2023</td><td>Sept. 13, 2023</td>") == [
        "2023-01-12",
        "2023-02-14",
        "2023-09-13",
    ]
    assert st.read_dates("2023-01-12  # a CPI day\n\n2023-02-14\n") == ["2023-01-12", "2023-02-14"]
    with pytest.raises(ValueError):
        st.read_dates("not a date")


# ------------------------------------------------------------------------------------------------
# The permutation test.


def test_the_permutation_p_is_small_when_fed_days_lose_and_large_when_they_do_not():
    nets, fed = book(fed_shift=-400.0)
    assert len(fed) >= 20
    assert st.permutation_p(nets, fed) < 0.01
    nets, fed = book(fed_shift=0.0)
    assert st.permutation_p(nets, fed) > 0.05


def test_fed_days_that_gain_get_a_p_near_one():
    nets, fed = book(fed_shift=+400.0)
    assert st.permutation_p(nets, fed) > 0.99


def test_the_permutation_is_seeded_and_ten_thousand_draws():
    nets, fed = book(fed_shift=-60.0)
    assert st.DRAWS == 10_000 and st.permutation_p(nets, fed) == st.permutation_p(nets, fed)
    assert st.permutation_p(nets, fed, seed=1) != st.permutation_p(nets, fed, seed=2)
    # the smallest p it can state is one draw in 10,001: the observed labelling counts as a draw
    nets, fed = book(fed_shift=-5000.0)
    assert st.permutation_p(nets, fed) == pytest.approx(1 / 10_001)


def test_a_wrong_label_draw_would_show_here():
    """The test draws as many days as there are Fed days. Draw a different count and a real loss reads
    differently: the guard is the count, shown by breaking it."""
    nets, fed = book(fed_shift=-400.0)
    days = sorted(nets)
    k_wrong = len(fed) // 3
    rng = random.Random(st.SEED)
    values = [nets[d] for d in days]
    total, n, obs = sum(values), len(values), st.gap(nets, fed)
    hits = 0
    for _ in range(2000):
        s = sum(rng.sample(values, k_wrong))
        hits += s / k_wrong - (total - s) / (n - k_wrong) <= obs
    # with too few days drawn the null spreads wider, so the same real loss looks less rare
    assert (hits + 1) / 2001 > st.permutation_p(nets, fed)


def test_no_p_without_fed_days_or_without_other_sessions():
    nets, _ = book()
    assert st.permutation_p(nets, set()) is None
    assert st.permutation_p(nets, set(nets)) is None


# ------------------------------------------------------------------------------------------------
# One window's judgement.


def test_a_real_fed_day_loss_passes_every_design_condition():
    nets, fed = book(fed_shift=-400.0)
    j = st.judge(nets, fed, START, END)
    assert j["fed_days"] >= 20 and all(j["conditions"].values()) and j["passes"]
    assert j["gap"] < 0 and j["fed_mean"] < 0


def test_no_effect_fails_on_the_permutation_p():
    nets, fed = book(fed_shift=0.0)
    j = st.judge(nets, fed, START, END)
    assert not j["conditions"]["permutation_p_under_alpha"] and not j["passes"]


def test_fed_days_below_the_others_but_above_zero_fail_the_zero_bar():
    nets = {d: 100.0 for d in weekdays(START, END)}
    fed = set(sorted(nets)[5::33])
    for d in fed:
        nets[d] = 40.0
    j = st.judge(nets, fed, START, END)
    assert j["conditions"]["fed_below_other"] and not j["conditions"]["fed_mean_below_zero"]
    assert not j["passes"]


def test_a_loss_in_one_half_only_fails_the_halves():
    nets, fed = book(fed_shift=-900.0, only_half="first")
    j = st.judge(nets, fed, START, END)
    assert j["conditions"]["fed_mean_below_zero"] and j["conditions"]["permutation_p_under_alpha"]
    assert not j["conditions"]["below_other_each_half"] and not j["passes"]
    assert j["halves"][0] < -500 and j["halves"][1] > -200  # the first half carries it
    nets, fed = book(fed_shift=-900.0, only_half="second")
    assert not st.judge(nets, fed, START, END)["conditions"]["below_other_each_half"]


def test_a_half_with_no_fed_day_cannot_pass():
    nets = {d: 10.0 for d in weekdays(START, END)}
    fed = {d for d in nets if d < "2023-06-01"}
    for d in fed:
        nets[d] = -500.0
    j = st.judge(nets, fed, START, END)
    assert j["halves"][1] is None and not j["conditions"]["below_other_each_half"]


def test_fed_dates_outside_the_run_are_not_counted():
    nets, fed = book()
    j = st.judge(nets, fed | {"2031-01-29", "2022-05-14"}, START, END)
    assert j["fed_days"] == len(fed)


def test_the_gate_effect_is_the_net_with_the_fed_days_removed():
    nets, fed = book(fed_shift=-400.0)
    j = st.judge(nets, fed, START, END)
    assert j["net_ungated"] == pytest.approx(sum(nets.values()))
    assert j["net_gated"] == pytest.approx(sum(v for d, v in nets.items() if d not in fed))
    assert j["gate_effect"] == pytest.approx(-sum(nets[d] for d in fed))
    assert j["gate_effect"] > 0  # skipping losing days raises the net


def test_the_neighbour_window_excludes_fed_days_and_reads_five_either_side():
    days = weekdays("2024-01-01", "2024-03-29")
    nets = {d: 0.0 for d in days}
    fed = {days[20]}
    nets[days[20]] = -100.0
    near = st.neighbours(nets, fed)
    assert near["near_sessions"] == 10 and near["gap"] == -100.0
    for d in days[15:20] + days[21:26]:
        nets[d] = 50.0
    assert st.neighbours(nets, fed)["near_mean"] == 50.0


def test_without_the_worst_fed_day_one_meeting_cannot_carry_it():
    nets, fed = book(fed_shift=0.0)
    worst = sorted(fed)[3]
    nets[worst] -= 20_000.0
    j = st.judge(nets, fed, START, END)
    ww = st.without_worst(nets, fed)
    assert ww["dropped"] == worst and ww["gap"] > j["gap"]
    assert st.without_worst(nets, {worst})["dropped"] is None


def test_cpi_and_unscheduled_days_are_reported_against_the_rest():
    nets = {d: 10.0 for d in weekdays("2024-01-01", "2024-02-29")}
    nets["2024-01-10"] = -30.0
    got = st.labelled(nets, {"2024-01-10"})
    assert got["days"] == 1 and got["mean"] == -30.0 and got["gap"] == -40.0
    assert st.labelled(nets, set())["gap"] is None


# ------------------------------------------------------------------------------------------------
# The verdict.


def passing_design():
    nets, fed = book(fed_shift=-400.0)
    return st.judge(nets, fed, START, END)


def holdout(shift):
    nets, fed = book(H_START, H_END, fed_every=33, fed_shift=shift, seed=5)
    return st.judge(nets, fed, H_START, H_END)


def test_design_pass_and_a_lower_holdout_fed_mean_is_a_pass():
    assert st.verdict(passing_design(), holdout(-400.0)) == "PASS"


def test_design_pass_and_a_higher_holdout_fed_mean_is_a_fail():
    h = holdout(+400.0)
    assert h["gap"] > 0
    assert st.verdict(passing_design(), h) == "FAIL"


def test_a_design_pass_without_a_holdout_is_no_verdict():
    assert st.verdict(passing_design(), None) == "NO VERDICT"
    empty = st.judge({d: 1.0 for d in weekdays(H_START, H_END)}, set(), H_START, H_END)
    assert empty["gap"] is None and st.verdict(passing_design(), empty) == "NO VERDICT"


def test_a_design_fail_stays_a_fail_whatever_the_holdout_shows():
    nets, fed = book(fed_shift=0.0)
    design = st.judge(nets, fed, START, END)
    assert not design["passes"]
    assert st.verdict(design, holdout(-5000.0)) == "FAIL"


def test_fewer_than_twenty_fed_days_is_no_verdict_even_for_a_striking_loss():
    nets, fed = book(fed_every=45, fed_shift=-2000.0)
    assert 0 < len(fed) < st.MIN_FED_DAYS
    design = st.judge(nets, fed, START, END)
    assert design["passes"]  # the loss is plain, so only the floor can stop it
    assert st.verdict(design, holdout(-400.0)) == "NO VERDICT"
    nets, fed = book(fed_every=33, fed_shift=-2000.0)
    assert (
        len(fed) >= st.MIN_FED_DAYS and st.verdict(st.judge(nets, fed, START, END), holdout(-400.0)) == "PASS"
    )


# ------------------------------------------------------------------------------------------------
# Provisional.


def test_locked_history_refuses_unless_asked_for_provisionally():
    with pytest.raises(SystemExit):
        st.check_unlock(False, False)
    assert st.check_unlock(False, True) is True


def test_unlocked_history_is_not_provisional_whatever_the_flag():
    assert st.check_unlock(True, False) is False
    assert st.check_unlock(True, True) is False


# ------------------------------------------------------------------------------------------------
# The stored runs (invented rows in the results database's own shape).


def memory_db():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE runs (run_id TEXT, created_at REAL, module TEXT, arms TEXT, window_name TEXT, "
        "start TEXT, end TEXT, cadence INTEGER)"
    )
    c.execute("CREATE TABLE session_results (run_id TEXT, arm TEXT, session TEXT, net REAL)")
    return c


def test_the_run_is_the_newest_with_the_arm_over_exactly_the_window():
    c = memory_db()
    rows = [
        ("old", 1, "meic", '["control", "live-1400"]', "design", START, END, 60),
        ("new", 2, "meic", '["control", "live-1400"]', "design", START, END, 60),
        ("other-arm", 3, "meic", '["control", "d16"]', "design", START, END, 60),
        ("short", 4, "meic", '["control", "live-1400"]', "design", START, "2023-03-08", 60),
        ("flies", 5, "flies", '["control", "live-1400"]', "design", START, END, 60),
        ("cadence", 6, "meic", '["control", "live-1400"]', "design", START, END, 15),
        ("hold", 7, "meic", '["control", "live-1400"]', "holdout", H_START, H_END, 60),
    ]
    c.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)", rows)
    assert st.find_run(c, "live-1400", "design", START, END) == "new"
    assert st.find_run(c, "live-1400", "holdout", H_START, H_END) == "hold"
    assert st.find_run(c, "pm-200", "design", START, END) is None


def test_run_nets_reads_one_arm_of_one_run_and_skips_empty_nets():
    c = memory_db()
    c.executemany(
        "INSERT INTO session_results VALUES (?,?,?,?)",
        [("r", "live-1400", "2024-01-02", 5.0), ("r", "live-1400", "2024-01-03", None),
         ("r", "d16", "2024-01-02", 9.0), ("q", "live-1400", "2024-01-02", 7.0)],
    )  # fmt: skip
    assert st.run_nets(c, "r", "live-1400") == {"2024-01-02": 5.0}


# ------------------------------------------------------------------------------------------------
# The report.


def result_doc(shift=-400.0, provisional=True, cpi_status="unavailable: HTTP Error 403"):
    nets, fed = book(fed_shift=shift)
    d = st.judge(nets, fed, START, END)
    d.update(
        span=[START, END],
        run_id="r1",
        near=st.neighbours(nets, fed),
        without_worst=st.without_worst(nets, fed),
        cpi=None,
        unscheduled=st.labelled(nets, set()),
    )
    hn, hf = book(H_START, H_END, fed_shift=shift, seed=5)
    h = st.judge(hn, hf, H_START, H_END)
    h.update(
        span=[H_START, H_END],
        run_id="r2",
        near=st.neighbours(hn, hf),
        without_worst=st.without_worst(hn, hf),
        cpi=None,
        unscheduled=st.labelled(hn, set()),
    )
    return {
        "item": 33,
        "arm": "live-1400",
        "provisional": provisional,
        "windows": {"design": d, "holdout": h},
        "verdict": st.verdict(d, h),
        "calendar": {
            "fetched": "2026-10-30T10:00:00",
            "sources": {"fomc": st.FED_URL, "cpi": st.CPI_URL},
            "cpi_status": cpi_status,
            "scheduled": 56,
            "unscheduled": ["2023-08-22"],
            "untouched_check": ["2026-12-09"],
        },
    }


def test_the_report_states_the_verdict_the_checks_and_what_was_not_read():
    text = st.report(result_doc())
    assert "**Verdict: pass.** Provisional." in text
    assert "| One-sided permutation p | under 0.05 |" in text
    assert "CPI days not read" in text and "2026-12-09" in text
    assert "Verdict: fail" in st.report(result_doc(shift=0.0))


def test_a_report_without_a_stored_run_says_so():
    doc = result_doc()
    doc["windows"]["holdout"] = {"span": [H_START, H_END], "run_id": None}
    doc["verdict"] = "NO VERDICT"
    text = st.report(doc)
    assert "No stored run for the arm over this window." in text and "Verdict: no verdict" in text
