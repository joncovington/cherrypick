"""Item 108's script (earnings_span_item108.py): invented trades only, no store read."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "earnings_span_item108", Path(__file__).resolve().parents[1] / "scripts" / "earnings_span_item108.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def t(sym: str, entry: str, net: float, *, span: bool | None = None, expiry: str = "2024-07-19") -> dict:
    out = {"symbol": sym, "entry": entry, "expiry": expiry, "net": net, "net_no_stop": net, "max_loss": 100.0,
           "entry_spread": 0.1}  # fmt: skip
    if span is not None:
        out["span"] = span
    return out


def test_a_report_inside_the_life_spans_and_one_before_the_entry_does_not():
    reports = {"AAA": [["2024-06-20", "Before market open"]], "BBB": [["2024-06-03", "Before market open"]]}
    assert m.spans(t("AAA", "2024-06-04", 0), reports)
    assert not m.spans(t("BBB", "2024-06-04", 0), reports)
    assert not m.spans(t("CCC", "2024-06-04", 0), reports)


def test_a_report_on_the_entry_day_spans_only_after_the_close():
    after = {"AAA": [["2024-06-04", "After market close"]]}
    before = {"AAA": [["2024-06-04", "Before market open"]]}
    assert m.spans(t("AAA", "2024-06-04", 0), after)
    assert not m.spans(t("AAA", "2024-06-04", 0), before)


def test_the_excess_is_within_each_name_weighted_by_its_spanning_count():
    trades = [
        t("AAA", "2024-01-02", -50, span=True),
        t("AAA", "2024-01-02", -50, span=True),
        t("AAA", "2024-02-01", 10, span=False),
        t("BBB", "2024-01-02", 0, span=True),
        t("BBB", "2024-02-01", 0, span=False),
        t("CCC", "2024-01-02", -90, span=True),  # no other trade on CCC: left out
    ]
    out = m.excess_loss(trades)
    # AAA: -0.5 - 0.1 = -0.6 with weight 2; BBB: 0 with weight 1.
    assert out["excess"] == pytest.approx(-0.4)
    assert out["spanning"] == 3 and out["names"] == 2


def test_the_judge_needs_its_floors_and_reads_the_holdout_only_after_a_design_pass():
    good = {"excess": -0.05, "t": -3.0, "spanning": 400, "names": 15}
    assert m.judge(good, {"excess": -0.01, "spanning": 200}, m.RULE)["verdict"] == {"spanning": "pass"}
    assert m.judge({**good, "spanning": 100}, good, m.RULE)["verdict"] == {"spanning": "no_verdict"}
    weak = m.judge({**good, "excess": -0.02}, good, m.RULE)
    assert weak["verdict"] == {"spanning": "fail"}
    assert weak["arms"]["spanning"]["parts"][1] == {"verdict": "not_read"}
    thin = m.judge(good, {"excess": -0.01, "spanning": 50}, m.RULE)
    assert thin["arms"]["spanning"]["parts"][1]["provisional"] is True


def test_the_study_leaves_out_funds_and_trades_before_the_calendar():
    cands = [t("SPY", "2021-01-04", 0), t("AAA", "2019-06-03", 0), t("AAA", "2021-01-04", 0)]
    out = m.study(cands, {}, resamples=10)
    assert out["trades"] == 1


def test_the_bootstrap_gives_a_standard_error_over_months():
    trades = []
    for month in range(1, 13):
        e = f"2021-{month:02}-04"
        trades += [t("AAA", e, -20, span=True), t("AAA", e, 5, span=False)]
        trades += [t("BBB", e, -10 - month, span=True), t("BBB", e, 0, span=False)]
    out = m.bootstrap(trades, m.RULE, resamples=100)
    assert out["excess"] < 0 and out["se"] > 0 and out["t"] < 0
