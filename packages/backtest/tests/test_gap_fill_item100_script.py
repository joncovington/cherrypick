"""Item 100's script (gap_fill_item100.py): invented bars and rows only, the store never read."""

from __future__ import annotations

import importlib.util
import math
import random
from datetime import date, timedelta
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "gap_fill_item100", Path(__file__).resolve().parents[1] / "scripts" / "gap_fill_item100.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

RULE = {**m.FORECAST_RULE, "horizon": 3, "vol_window": 3, "min_n": 5, "holdout_min_n": 3}


def _bars(closes: list[float], *, start: date = date(2015, 1, 5), spread: float = 0.5) -> list[dict]:
    out, d = [], start
    for c in closes:
        out.append({"d": d.isoformat(), "h": c + spread, "l": c - spread, "c": c})
        d += timedelta(days=1)
    return out


def test_the_rule_is_complete_and_the_chain_is_one_study_row():
    r = m.FORECAST_RULE
    assert r["kind"] == "forecast" and r["splits"] == ["2022-01-01"] and r["halves"] < r["splits"][0]
    assert all(v is not None for v in r.values())
    assert [s["step"] for s in m.CHAIN] == ["study"]


def test_a_gap_is_a_low_over_the_prior_high_or_a_high_under_the_prior_low():
    prev = {"h": 100.0, "l": 99.0}
    assert m.gap_of(prev, {"h": 101.0, "l": 100.2}, 0.001) == "up"
    assert m.gap_of(prev, {"h": 101.0, "l": 100.05}, 0.001) is None  # under the minimum size
    assert m.gap_of(prev, {"h": 98.5, "l": 98.0}, 0.001) == "down"
    assert m.gap_of(prev, {"h": 100.5, "l": 99.5}, 0.001) is None


def test_an_up_gap_that_trades_back_to_the_prior_high_is_filled():
    bars = _bars([100, 101, 100, 101, 100])
    bars.append({"d": "2015-01-10", "h": 104.0, "l": 102.0, "c": 103.5})  # gap over 100.5
    bars += _bars([103, 101.2, 102], start=date(2015, 1, 11))  # 101.2 - 0.5 = 100.7: not yet
    rows, _ = m.build_rows("QQQ", bars, RULE)
    gap = next(r for r in rows if r["date"] == "2015-01-10")
    assert gap["gap"] == "up" and gap["z"] > 0 and gap["fill"] is False
    bars[-1] = {"d": "2015-01-13", "h": 101.0, "l": 100.4, "c": 100.8}
    rows, _ = m.build_rows("QQQ", bars, RULE)
    gap = next(r for r in rows if r["date"] == "2015-01-10")
    assert gap["fill"] is True


def test_rows_without_a_full_history_or_horizon_or_across_a_hole_are_dropped():
    bars = _bars([100 + (i % 3) for i in range(12)])
    rows, dropped = m.build_rows("QQQ", bars, RULE)
    assert dropped["history"] == 3 and dropped["horizon"] == 3 and len(rows) == 6
    bars[6]["d"] = (date.fromisoformat(bars[5]["d"]) + timedelta(days=9)).isoformat()
    for i in range(7, 12):
        bars[i]["d"] = (date.fromisoformat(bars[i - 1]["d"]) + timedelta(days=1)).isoformat()
    rows, dropped = m.build_rows("QQQ", bars, RULE)
    assert dropped["hole"] > 0 and len(rows) < 6


def test_the_baseline_is_the_share_of_controls_reaching_the_same_distance():
    assert m.reach_share([0.5, 1.0, 1.5, 2.0], 1.0) == 0.75
    assert m.reach_share([0.5, 1.0], 3.0) == 0.0
    assert m.reach_share([], 1.0) is None
    controls = [{"symbol": "QQQ", "date": "2015-01-01", "gap": None, "m_below": x, "m_above": 9.0}
                for x in (0.5, 1.0, 1.5, 2.0)]  # fmt: skip
    events = [
        {"symbol": "QQQ", "date": "2015-02-01", "gap": "up", "z": 1.0, "fill": True},
        {"symbol": "QQQ", "date": "2015-02-02", "gap": "up", "z": 1.8, "fill": False},
    ]
    x = m.excess(controls + events)
    # fills 1 and 0 against baselines 0.75 and 0.25: excess (0.25 - 0.25) / 2 = 0
    assert x["n"] == 2 and math.isclose(x["excess"], 0.0) and math.isclose(x["baseline"], 0.5)
    # a down gap reads the controls' excursion above, not below
    down = {"symbol": "QQQ", "date": "2015-02-03", "gap": "down", "z": 5.0, "fill": True}
    assert math.isclose(m.excess([*controls, down])["excess"], 0.0)


def _rows(n_days: int, fill_rate: float, seed: int, start: date = date(2011, 1, 3)) -> list[dict]:
    """Invented rows: every fund's controls reach z with probability exp(-z), and every fifth session
    is a gap at z 1 filled with `fill_rate`."""
    rng = random.Random(seed)
    out = []
    for i in range(n_days):
        d = (start + timedelta(days=i)).isoformat()
        for s in ("QQQ", "SPY"):
            if i % 5 == 0:
                out.append({"symbol": s, "date": d, "gap": "up", "z": 1.0, "fill": rng.random() < fill_rate})
            else:
                out.append({"symbol": s, "date": d, "gap": None,
                            "m_below": rng.expovariate(1.0), "m_above": rng.expovariate(1.0)})  # fmt: skip
    return out


def test_a_gap_that_fills_far_more_than_its_baseline_passes_and_reads_the_holdout():
    rule = {**RULE, "min_n": 50, "holdout_min_n": 20, "halves": "2014-01-01", "splits": ["2018-01-01"]}
    rows = _rows(3000, 0.9, seed=1)  # baseline exp(-1) ~ 0.37
    v = m.judge(rows, rule, resamples=200)
    assert v["design"]["verdict"] == "pass" and v["verdict"] == {"excess_fill": "pass"}
    assert v["holdout"]["verdict"] == "pass" and v["holdout"]["excess"] > 0


def test_a_gap_that_fills_at_its_baseline_fails_and_leaves_the_holdout_unread():
    rule = {**RULE, "min_n": 50, "holdout_min_n": 20, "halves": "2014-01-01", "splits": ["2018-01-01"]}
    rows = _rows(3000, math.exp(-1), seed=2)
    v = m.judge(rows, rule, resamples=200)
    assert v["design"]["verdict"] == "fail" and v["holdout"]["verdict"] == "not read"
    assert v["design"]["checks"]["effect"] is False


def test_too_few_design_events_is_no_verdict():
    rule = {**RULE, "min_n": 10_000}
    v = m.judge(_rows(200, 0.9, seed=3), rule, resamples=10)
    assert v["verdict"] == {"excess_fill": "no verdict"} and v["holdout"]["verdict"] == "not read"
