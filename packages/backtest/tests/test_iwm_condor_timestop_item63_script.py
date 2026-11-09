"""Hypothesis 63's study (packages/backtest/scripts/iwm_condor_timestop_item63.py): item 59's condor with a
7-day time stop, paired against item 59's own exit on the same entries. Invented quotes only (Black-Scholes
prices around a made-up forward), no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import bs

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "iwm_condor_timestop_item63.py"


def _module():
    spec = importlib.util.spec_from_file_location("iwm_condor_timestop_item63", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ts = _module()
ic = ts.ic

EXPIRY = "2024-03-15"
ENTRY = "2024-01-30"  # 45 days out


def rows(forward: float, days: int, vol: float = 0.22, half_spread: float = 0.02, scale: float = 1.0) -> list:
    years = days / 365.0
    out = []
    for k in range(int(forward) - 40, int(forward) + 41):
        for r, name in (("C", "CALL"), ("P", "PUT")):
            m = bs.price(forward, k, years, vol, r, 0.0) * scale
            out.append((float(k), name, round(max(m - half_spread, 0.0), 2), round(m + half_spread, 2)))
    return out


def chain(session=ENTRY, scale=1.0):
    return ic.Chain(rows(200.0, ic.days_between(session, EXPIRY), scale=scale), session, EXPIRY)


def _path(marks: list[tuple[str, float]]) -> list:
    return [(s, chain(s, x)) for s, x in marks]


def test_it_runs_item_59s_own_script_for_entry_strikes_and_fills():
    assert ic.__name__ == "iwm_condor_item59" and ts.DESIGN == ic.DESIGN


def test_the_stop_closes_on_the_first_priced_session_7_days_after_entry():
    # Day 3 and day 6 are neither target nor stop; day 8 is the first session at 7 days or more.
    path = _path([("2024-02-02", 1.1), ("2024-02-05", 1.2), ("2024-02-07", 1.3), ("2024-02-09", 1.0)])
    r = ts.run_stop(chain(), path, EXPIRY, fill="mid", entry_session=ENTRY)
    assert r["how"] == "time stop" and r["exit"] == "2024-02-07" and r["days_held"] == 8


def test_the_target_still_comes_first_when_it_is_reached_inside_7_days():
    path = _path([("2024-02-01", 0.3), ("2024-02-09", 1.0)])
    r = ts.run_stop(chain(), path, EXPIRY, fill="mid", entry_session=ENTRY)
    assert r["how"] == "target" and r["exit"] == "2024-02-01"


def test_an_unpriced_session_on_the_stop_day_passes_to_the_next_priced_one():
    path = _path([("2024-02-06", 1.2), ("2024-02-08", 1.1)])
    del path[0][1].by["C"][ic.pick_condor(chain())[3][0][0]]  # the stop day's long call has no quote
    r = ts.run_stop(chain(), path, EXPIRY, fill="mid", entry_session=ENTRY)
    assert r["exit"] == "2024-02-08"


def test_both_arms_share_the_entry_legs_credit_and_fees():
    # At 21 days the wings are worth pennies; 4x keeps their bids above zero so the mark prices.
    path = _path([("2024-02-07", 1.2), ("2024-02-23", 4.0)])
    pair = ts.pair_trade(chain(), path, EXPIRY, ENTRY, fill="working")
    base, stop = pair["base"], pair["stop7"]
    assert base["legs"] == stop["legs"] and base["credit"] == stop["credit"] and base["cost"] == stop["cost"]
    assert stop["exit"] == "2024-02-07" and base["exit"] == "2024-02-23"  # base waits for 21 days


def _pairs(diffs: list[float], stop_net: float = 30.0, start="2016-01-15") -> list[dict]:
    d0 = date.fromisoformat(start)
    out = []
    for i, d in enumerate(diffs):
        entry = (d0 + timedelta(days=30 * i)).isoformat()
        exit_ = (d0 + timedelta(days=30 * i + 8)).isoformat()
        out.append({
            "entry": entry,
            "base": {"net": stop_net - d, "cost": 8.0, "how": "time", "exit": exit_},
            "stop7": {"net": stop_net, "cost": 8.0, "how": "time stop", "exit": exit_},
        })  # fmt: skip
    return out


def _judged(diffs, stop_net=30.0, skips=None):
    ps = _pairs(diffs, stop_net)
    sk = {a: Counter((skips or {}).get(a, {})) for a in ts.ARMS}
    test = ts.score(ps, sk, ps[0]["entry"], ps[-1]["entry"])
    ts.judge(test)
    return test


def test_a_steady_improvement_with_a_profitable_stop_arm_passes():
    assert _judged([60.0, 40.0] * 35)["verdict"] == "pass"


def test_a_noisy_improvement_fails_the_t_bar():
    r = _judged([300.0, -290.0] * 35)
    assert r["verdict"] == "fail" and not r["checks"]["diff_t_above_bar"]


def test_an_improvement_only_in_the_first_half_fails():
    r = _judged([80.0, 60.0] * 20 + [-5.0, -1.0] * 20)
    assert r["verdict"] == "fail" and not r["checks"]["diff_both_halves_positive"]


def test_a_better_loser_fails():
    # The stop beats the base every time, but the stop arm itself loses money.
    r = _judged([60.0, 40.0] * 35, stop_net=-5.0)
    assert r["verdict"] == "fail" and not r["checks"]["stop7_positive_at_1_5x_costs"]
    assert r["checks"]["diff_t_above_bar"]


def test_fewer_than_50_pairs_is_no_verdict():
    assert _judged([60.0, 40.0] * 20)["verdict"] == "no verdict"


def test_too_many_entries_without_an_exit_mark_in_either_arm_is_no_verdict():
    r = _judged([60.0, 40.0] * 35, skips={"base": {"no exit mark": 5}})
    assert r["verdict"] == "no verdict" and "exit mark" in r["why"]
    assert _judged([60.0, 40.0] * 35, skips={"stop7": {"no exit mark": 3}})["verdict"] == "pass"


def test_the_holdout_is_refused_without_the_flag(capsys):
    assert ts.main(["--end", "2022-03-01"]) == 2
    assert "holdout" in capsys.readouterr().err
