"""Hypothesis 54's study (packages/backtest/scripts/time_spread_ratio_item54.py): the entry calendar, the
expiries, the requests, the trade's settlement and the declared decision rules. Invented chains and trades
only.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "time_spread_ratio_item54", SCRIPTS / "time_spread_ratio_item54.py"
)
s54 = importlib.util.module_from_spec(_spec)
sys.modules["time_spread_ratio_item54"] = s54
_spec.loader.exec_module(s54)


def test_the_front_is_the_weekly_nearest_eleven_days_and_the_back_the_monthly():
    monday = date(2019, 3, 4)
    assert s54.front_expiry(monday) == date(2019, 3, 15)  # 11 days
    assert s54.back_expiry(monday) == date(2019, 4, 18)  # Good Friday 2019: the Thursday before
    # A holiday Friday moves the weekly to its Thursday (Good Friday 2025).
    assert s54.friday_expiry(date(2025, 4, 14)) == date(2025, 4, 17)


def test_entries_move_off_a_holiday_and_keep_their_cadence():
    got = s54.entry_sessions(date(2019, 1, 7), date(2019, 2, 28), 14)
    # Jan 21 2019 was a holiday (MLK): the next trading day.
    assert got == [date(2019, 1, 7), date(2019, 1, 22), date(2019, 2, 4), date(2019, 2, 19)]


def test_each_trade_asks_for_three_chains_once():
    reqs = s54.requests_for(["AAA"], [date(2019, 3, 4), date(2019, 3, 4)])
    assert [(r["session"], r["expiration"]) for r in reqs] == [
        ("2019-03-04", "2019-03-15"),
        ("2019-03-04", "2019-04-18"),
        ("2019-03-15", "2019-04-18"),
    ]


def test_windows():
    assert s54.window_of(date(2018, 6, 1)) == "design"
    assert s54.window_of(date(2023, 6, 1)) == "holdout"
    assert s54.window_of(date(2015, 6, 1)) is None


def test_settle_charges_the_short_its_intrinsic_and_the_long_its_mid_less_costs():
    row = {
        "short": {"strike": 100.0, "right": "P"},
        "entry_debit": 2.0,
        "entry_quotes": [[3.9, 4.1], [1.9, 2.1]],
    }
    flat = s54.settle(row, 101.0, (3.0, 3.2), {}, "AAA")  # short expires worthless, long worth 3.10
    assert flat["net"] < (3.1 - 2.0) * 100 and flat["net"] > 0
    assert flat["stress_pct"] < flat["net_pct"]
    hit = s54.settle(row, 95.0, (6.0, 6.2), {}, "AAA")  # short worth 5.00 intrinsic, long 6.10
    assert hit["net"] < (6.1 - 5.0 - 2.0) * 100
    assert s54.settle(row, 101.0, (None, 3.2), {}, "AAA") is None


def test_pick_holds_the_calendar_at_the_stock_and_the_diagonal_nearest_its_long_delta():
    rows = [
        {"kind": "put calendar", "long": {"strike": 95.0}},
        {"kind": "put calendar", "long": {"strike": 100.0}},
        {"kind": "call diagonal", "long": {"strike": 90.0}, "long_delta": 0.74},
        {"kind": "call diagonal", "long": {"strike": 95.0}, "long_delta": 0.63},
    ]
    assert s54.pick(rows, "put calendar", 99.0)["long"]["strike"] == 100.0
    assert s54.pick(rows, "call diagonal", 99.0)["long"]["strike"] == 95.0
    assert s54.pick(rows, "put diagonal", 99.0) is None


def test_the_ratio_is_the_at_the_money_put_calendars():
    rows = [
        {"kind": "put calendar", "long": {"strike": 100.0}, "front_iv": 0.6, "back_iv": 0.4},
        {"kind": "put calendar", "long": {"strike": 105.0}, "front_iv": 0.5, "back_iv": 0.5},
    ]
    assert s54.atm_ratio(rows, 101.0) == 1.5
    assert s54.atm_ratio([], 101.0) is None


def trades(n, ratio_effect, window="design", seed=1):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        ratio = 1.0 + rng.random()
        net = ratio_effect * (ratio - 1.5) * 100 + rng.gauss(0, 5)
        out.append(
            {
                "entry": f"2018-{1 + i % 12:02d}-{1 + i % 27:02d}",
                "window": window,
                "kind": "put calendar",
                "ratio": ratio,
                "net_pct": net,
                "stress_pct": net - 1.0,
                "loses_unchanged": i % 4 == 0,
            }
        )
    return out


def test_the_rank_test_fires_on_a_real_relation_and_not_on_noise():
    real = s54.reading(trades(200, 1.0), "ratio-rank")
    noise = s54.reading(trades(200, 0.0), "ratio-rank")
    assert real["estimate"] > 0.5 and real["p"] < 0.001
    assert noise["p"] > 0.01


def test_a_design_pass_needs_holm_the_stress_and_the_trim():
    rows = trades(200, 1.0)
    base = s54.reading(rows, "ratio-rank")
    assert s54.judge_design(rows, True, base, "ratio-rank")["decision"] == "pass"
    assert s54.judge_design(rows, False, base, "ratio-rank")["decision"] == "fail"
    few = rows[:20]
    assert (
        s54.judge_design(few, True, s54.reading(few, "ratio-rank"), "ratio-rank")["decision"] == "no verdict"
    )
    # A top third that only makes money before the stress costs fails there.
    thin = [{**t, "net_pct": 0.5, "stress_pct": -0.5} for t in trades(200, 0.0)]
    top = s54.reading(thin, "top-third")
    assert s54.judge_design(thin, True, top, "top-third")["why"].startswith("not above zero at 1.5x fees")


def test_the_holdout_confirms_only_a_design_pass_with_the_same_sign():
    good = {"n": 100, "estimate": 0.3, "p": 0.01}
    bad = {"n": 100, "estimate": -0.1, "p": 0.8}
    assert s54.judge_holdout({"decision": "pass"}, good)["decision"] == "pass"
    assert s54.judge_holdout({"decision": "pass"}, bad)["decision"] == "fail"
    assert s54.judge_holdout({"decision": "fail"}, good)["decision"] == "not tested"


def test_the_verdict_test_compares_the_loses_unchanged_rows_with_the_rest():
    rows = trades(200, 0.0)
    for t in rows:
        if t["loses_unchanged"]:
            t["net_pct"] -= 20
    r = s54.reading(rows, "verdict")
    assert r["estimate"] > 15 and r["p"] < 0.001 and r["n_loses_unchanged"] == 50


def test_a_study_that_judged_nothing_is_no_verdict_not_failed():
    nothing = {"a": {"decision": "no verdict"}, "b": {"decision": "no verdict"}}
    assert s54.overall_verdict(nothing, []) == "no verdict"
    judged = {"a": {"decision": "no verdict"}, "b": {"decision": "fail"}}
    assert s54.overall_verdict(judged, []) == "failed"
    assert s54.overall_verdict(judged, ["b"]) == "passed"


def stored_chain(dte, iv, spot=100.0):
    """A chain as the vendor store holds it: (strike, right, bid, ask) with CALL and PUT. Priced by
    manage's own model, so it needs manage installed (the calendar test below skips without it)."""
    from cherrypick.manage import pricing

    out = []
    for i in range(17):
        k = 80 + 2.5 * i
        for right, stored in (("C", "CALL"), ("P", "PUT")):
            px = pricing.price(spot, k, dte / 365, iv, right, 0.04)
            if px >= 0.1:
                out.append((k, stored, round(px - 0.02, 2), round(px + 0.02, 2)))
    return out


def test_the_stores_call_and_put_are_read_as_the_builders_c_and_p():
    assert s54.right_of("CALL") == "C" and s54.right_of("PUT") == "P"
    # Invented quotes in the store's spelling; no pricing model needed to read them.
    rows = [(100.0, "CALL", 2.0, 2.2), (100.0, "PUT", 1.9, 2.1), (105.0, "CALL", 0.5, 0.6)]
    seen = {o["right"] for o in s54.view(rows, 7)["options"]}
    assert seen == {"C", "P"}


def test_a_stored_chain_builds_the_calendar_the_ratio_is_read_from():
    pytest.importorskip("cherrypick.manage")  # CI's backtest lane does not install manage
    from cherrypick.manage import rules as _rules
    from cherrypick.manage import timespreads as ts

    entry = {
        "spot": 100.0,
        "front": {"expiration": "2026-11-13", **s54.view(stored_chain(7, 0.6), 7)},
        "back": {"expiration": "2026-12-18", **s54.view(stored_chain(42, 0.45), 42)},
    }
    built, _ = ts.structures(entry, date(2026, 11, 6), _rules.effective(None)[0])
    ratio = s54.atm_ratio(built, 100.0)
    assert ratio is not None and 1.2 < ratio < 1.5  # 0.60 over 0.45
