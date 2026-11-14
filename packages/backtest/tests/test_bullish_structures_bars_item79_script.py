"""Item 79's script (bullish_structures_bars_item79.py): invented trades only, the store never read."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "bullish_structures_bars_item79",
    Path(__file__).resolve().parents[1] / "scripts" / "bullish_structures_bars_item79.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

RATE = 0.04


def trade(structure: str, front: str, entry: str, net: float, capital: float = 400.0, **kw) -> dict:
    exit_ = (date.fromisoformat(entry) + timedelta(days=24)).isoformat()
    return {"symbol": "KO", "structure": structure, "front": front, "entry": entry, "exit": exit_,
            "net": net, "capital": capital, "shares": 0.0, "delta": 0.0, **kw}  # fmt: skip


def test_the_declared_rule_is_complete_and_read_whole_at_the_tail_bars_floor():
    kit_rule.check_rule(m.RULE)
    assert m.RULE["splits"] == [] and m.RULE["min_n"] == 100
    assert [s["step"] for s in m.CHAIN] == ["study"]


def test_a_cycle_is_the_mean_return_on_capital_with_its_delta_financed_on_it():
    a = trade("A", "2016-04-15", "2016-03-01", 40.0, 400.0, shares=20.0)
    b = trade("A", "2016-04-15", "2016-03-02", -10.0, 100.0, symbol="PG", shares=-5.0)
    [row] = m.cycle_rows([a, b], lambda s: RATE)
    assert row["net"] == pytest.approx((0.10 - 0.10) / 2)
    bill = ck.bill_on(1.0, RATE, 24)
    assert row["financed"] == pytest.approx(((20.0 / 400 + bill) + (-5.0 / 100 + bill)) / 2)
    assert row["date"] == "2016-03-01" and row["bp"] == 1.0 and row["names"] == 2


def cycles(structure: str, net: float, first: date, n: int) -> list[dict]:
    out = []
    for i in range(n):
        e = first + timedelta(days=30 * i)
        wobble = 1.0 + 0.3 * ((i % 3) - 1)
        out.append(trade(structure, (e + timedelta(days=45)).isoformat(), e.isoformat(), net * wobble))
    return out


def test_one_read_over_every_cycle_with_the_fresh_ones_reported_and_the_data_rule_binding():
    rows = []
    for k in m.STRUCTURES:
        rows += cycles(k, 40.0 if k == "A" else -10.0, date(2016, 1, 4), 70)
        rows += cycles(k, 40.0 if k == "A" else -10.0, date(2022, 1, 3), 50)
    skips = {"A": {}, "B": {"no exit mark": 1}, "C": {"no exit mark": 60}, "D": {}}
    out = m.judge(rows, skips, lambda s: RATE)
    assert out["verdict"]["A"] == ck.PASS and out["verdict"]["B"] == ck.FAIL
    assert out["arms"]["A"]["n"] == 120
    assert out["verdict"]["C"] == ck.NO_VERDICT  # 60 of 180 entries had no exit mark
    assert out["data_rule"]["B"]["within_rule"] and not out["data_rule"]["C"]["within_rule"]
    assert out["reported"]["A"]["fresh"]["cycles"] == 50
    assert out["reported"]["A"]["read_by_item64"]["cycles"] == 70
    # Fewer cycles than the tail bar's floor is no verdict, never a pass.
    few = m.judge(cycles("A", 40.0, date(2016, 1, 4), 99), {}, lambda s: RATE)
    assert few["verdict"]["A"] == ck.NO_VERDICT


def test_no_exit_share_counts_missing_marks_over_every_entry_that_priced():
    assert m.no_exit_share([{}] * 95, {"no exit mark": 5}) == pytest.approx(0.05)
    assert m.no_exit_share([], {}) == 0.0
