"""Hypothesis 18's study (scripts/credit_spread_item17.py --item 18): one fixed cell, liquid entries
and liquid baselines only, three bars. Invented rows only, no store.

Each bar is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "credit_spread_item17.py"


def _module():
    spec = importlib.util.spec_from_file_location("credit_spread_item17", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cs = _module()
CELL = cs.ITEM18_CELL
START = cs.ITEM18_START
FEE = 1.3


def res(credit: float, value: float, *, width=5.0, mid=None, settle=0.0, liquid=True) -> dict:
    """A priced cell result in settle()'s shape, at a working `credit`."""
    mid = credit + 0.10 if mid is None else mid
    costs = FEE + settle
    net = (credit - value) * 100 - costs
    max_loss = (width - credit) * 100 + FEE
    return {"width": width, "credit": credit, "net": net, "ret": net / max_loss, "max_loss": max_loss,
            "costs": costs, "slippage": (mid - credit) * 100, "liquid": liquid, "mid": mid,
            "value": value, "fee_open": FEE}  # fmt: skip


def row(tid, kind, of, sym, entry, expiry, cells: dict) -> dict:
    return {"id": tid, "kind": kind, "of": of, "setup": "reversion", "side": "put", "symbol": sym,
            "entry": entry, "expiry": expiry, "settle_flag": None, "cells": cells}  # fmt: skip


def build(n: int, trade, base, *, expiries=15, symbols=10, seed=3, bases=5) -> list[dict]:
    """`n` signal trades over `expiries` monthly clusters, each with `bases` baselines. `trade(i, rng)`
    and `base(i, k, rng)` return the cell result (or a {cell: result} dict) for each."""
    rng = random.Random(seed)
    first = date.fromisoformat("2025-07-07")
    rows = []
    for i in range(n):
        g = i % expiries
        entry = (first + timedelta(days=30 * g)).isoformat()
        expiry = (first + timedelta(days=30 * g + 38)).isoformat()
        sym = f"N{i % symbols}"
        tid = f"t{i}"
        t = trade(i, rng)
        rows.append(row(tid, "trade", None, sym, entry, expiry, t if CELL in t else {CELL: t}))
        for k in range(bases):
            b = base(i, k, rng)
            rows.append(
                row(f"{tid}#b{k}", "baseline", tid, sym, entry, expiry, b if CELL in b else {CELL: b})
            )
    return rows


def winning(i, rng):
    return res(1.0, max(0.0, rng.gauss(0.2, 0.3)))


def losing_base(i, k, rng):
    return res(1.0, max(0.0, rng.gauss(1.5, 0.5)))


# ------------------------------------------------------------------------------------ re-pricing
def test_working_reprice_matches_settle_and_fills_order():
    short = {"strike": 95.0, "right": "P", "bid": 2.0, "ask": 2.3}
    long_ = {"strike": 90.0, "right": "P", "bid": 1.0, "ask": 1.2}
    prices = cs.legbars.structure_prices([(short, -1), (long_, 1)])
    r = cs.settle("XYZ", short, long_, prices, 93.0)
    w = cs.reprice(r, "working")
    assert w["net"] == pytest.approx(r["net"])
    assert w["ret"] == pytest.approx(r["ret"])
    m, n = cs.reprice(r, "mid"), cs.reprice(r, "mid-0.05")
    assert m["slippage"] == 0.0
    assert n["slippage"] == pytest.approx(5.0)
    # The structure's cost is 0.50 a share, so the working price concedes 0.25: below mid - 0.05.
    assert m["net"] > n["net"] > w["net"]
    assert m["ret"] > n["ret"] > w["ret"]


def test_fill_variants_order_their_measures_and_reprice_the_baselines_too():
    rows = build(120, winning, losing_base)
    s = {f: cs.score18(rows, CELL, f, True, START) for f in cs.FILLS}
    assert s["mid"]["own_mean_net"] > s["mid-0.05"]["own_mean_net"] > s["working"]["own_mean_net"]
    assert s["mid"]["trade_summary"]["slippage_per_trade"] == pytest.approx(0.0)
    assert s["mid-0.05"]["baseline_summary"]["slippage_per_trade"] == pytest.approx(5.0)
    assert s["working"]["baseline_summary"]["slippage_per_trade"] == pytest.approx(10.0)
    # Every row shifts by the same fill, so the excess barely moves; it is the baselines that moved too.
    assert s["mid"]["mean_diff"] == pytest.approx(s["working"]["mean_diff"], rel=0.05)


# ------------------------------------------------------------------------------ the declared excess
def test_the_excess_is_net_difference_on_the_trades_own_max_loss_not_item_17s():
    # Baselines on a wider structure: their max loss differs from the trade's, so the two measures part.
    trade = res(1.0, 0.0, width=5.0)
    bases = [res(1.0, 0.5, width=10.0), res(1.0, 0.3, width=10.0)]
    assert bases[0]["max_loss"] != trade["max_loss"]
    rows = build(1, lambda i, rng: trade, lambda i, k, rng: bases[k], bases=2)
    s = cs.score18(rows, CELL, "working", True, START)
    declared = (trade["net"] - (bases[0]["net"] + bases[1]["net"]) / 2) / trade["max_loss"]
    assert s["rows"][0]["diff"] == pytest.approx(declared)
    # Item 17's measure, kept for its one run, is the difference of per-trade returns: not the same.
    item17 = cs.paired_diff(rows[0], rows[1:], CELL)
    assert item17 == pytest.approx(trade["ret"] - (bases[0]["ret"] + bases[1]["ret"]) / 2)
    assert abs(item17 - declared) > 0.01


# ------------------------------------------------------------------------------- liquid filter
def test_the_liquid_filter_drops_trades_and_baselines():
    def trade(i, rng):
        return res(1.0, 0.0, liquid=i % 4 != 0)  # every fourth trade illiquid

    def base(i, k, rng):
        if i % 4 == 1:
            return res(1.0, 0.0, liquid=False)  # every baseline illiquid: the trade drops
        # baseline 0 is illiquid and a big winner: kept, it would cancel the excess
        return res(1.0, 0.0, liquid=False) if k == 0 else res(1.0, 3.0)

    rows = build(40, trade, base, bases=3)
    s = cs.score18(rows, CELL, "working", True, START)
    assert s["drops"]["entry legs do not clear the bars"] == 10
    assert s["drops"]["no liquid baseline"] == 10
    assert s["n"] == 20
    assert all(r["baselines_priced"] == 2 for r in s["rows"])
    win, lose = res(1.0, 0.0)["ret"], res(1.0, 3.0)["ret"]
    assert s["mean_diff"] == pytest.approx(win - lose)
    every = cs.score18(rows, CELL, "working", False, START)
    assert every["n"] == 40
    assert every["mean_diff"] < s["mean_diff"]


def test_entries_before_the_window_and_other_setups_are_not_scored():
    rows = build(10, winning, losing_base)
    rows[0]["entry"] = "2025-06-30"
    rows[6]["setup"] = "trend"
    s = cs.score18(rows, CELL, "working", True, START)
    assert s["n"] == 8


# ------------------------------------------------------------------------------------ the verdict
def test_all_three_bars_fire_on_a_built_in_effect():
    rep = cs.analyse18(build(120, winning, losing_base), START, account=100_000.0)
    assert rep["verdict"] == "pass"
    assert all(rep["bars"].values())
    b13 = rep["descriptive"]["robustness_B13"]
    assert b13["cost_stress"]["pass_stress"] == 1.5
    assert set(b13["neighbours"]) == set(cs.ITEM18_NEIGHBOURS)
    assert "max_size" in b13


def test_no_excess_fails_the_t_bar():
    def same(i, rng):
        return res(1.0, max(0.0, rng.gauss(0.3, 0.6)))

    rep = cs.analyse18(build(150, same, lambda i, k, rng: same(i, rng)), START)
    assert rep["verdict"] == "fail"
    assert not rep["bars"]["excess_t_above_2"]
    assert "robustness_B13" not in rep["descriptive"]


def test_a_losing_cell_beating_worse_baselines_fails_on_its_own_net():
    def trade(i, rng):
        return res(1.0, max(0.0, rng.gauss(1.3, 0.2)))

    rep = cs.analyse18(build(120, trade, lambda i, k, rng: res(1.0, max(0.0, rng.gauss(3.0, 0.3)))), START)
    assert rep["bars"]["excess_t_above_2"]
    assert not rep["bars"]["own_mean_net_positive"]
    assert rep["verdict"] == "fail"


def test_positive_dollars_with_a_negative_return_on_risk_fails():
    # Narrow spreads lose their whole (small) risk; wide ones win a little of their (large) risk.
    def trade(i, rng):
        if i % 2:
            return res(0.9, 1.0, width=1.0)
        return res(1.0, 0.0, width=10.0)

    def base(i, k, rng):
        noise = rng.uniform(0.0, 0.05)
        return res(0.9, 1.0, width=1.0) if i % 2 else res(1.0, 10.0 - noise, width=10.0)

    rep = cs.analyse18(build(120, trade, base), START)
    t = rep["test"]
    assert t["own_mean_net"] > 0
    assert t["own_mean_ret"] < 0
    assert rep["bars"]["excess_t_above_2"]
    assert rep["bars"]["own_mean_net_positive"]
    assert not rep["bars"]["own_mean_ret_positive"]
    assert rep["verdict"] == "fail"


def test_under_100_scored_liquid_trades_is_no_verdict():
    # 130 entries, but 40 illiquid: 90 scored, so no verdict however strong the effect.
    def trade(i, rng):
        r = winning(i, rng)
        r["liquid"] = i >= 40
        return r

    rep = cs.analyse18(build(130, trade, losing_base), START)
    assert rep["test"]["n"] == 90
    assert rep["verdict"] == "no verdict"
    assert rep["descriptive"]["every_entry_liquid_or_not"]["working"]["n"] == 130


def test_the_p_is_students_t_on_expiry_clusters():
    s = cs.score18(build(120, winning, losing_base), CELL, "working", True, START)
    assert s["clusters"] == 15
    assert s["p"] == pytest.approx(cs.p_one(s["t"], 15))


# ------------------------------------------------------------------------------- descriptive
def test_descriptive_blocks_are_reported_beside_the_verdict():
    rep = cs.analyse18(build(120, winning, losing_base), START)
    d = rep["descriptive"]
    assert set(d["liquid_other_fills"]) == {"mid", "mid-0.05"}
    assert set(d["every_entry_liquid_or_not"]) == set(cs.FILLS)
    assert d["planned_mde_B12"]["clusters"] == 15
    assert d["planned_mde_B12"]["mde"] > 0
    assert sum(h["names"] for h in d["name_halves_B9"].values()) == 10
    assert d["top_names_share"]["names"] == 10
    assert 0.4 < d["top_names_share"]["share_of_excess"] < 0.6
    assert "skipped" in d["environment_B8_atr_band_B4"]


def test_top_names_share_counts_the_largest_contributors():
    rows = [{"symbol": s, "diff": d} for s, d in [("A", 5.0), ("B", 3.0), ("C", 1.0), ("D", 1.0)]]
    out = cs.top_names_share(rows, k=2)
    assert out["top"] == ["A", "B"]
    assert out["share_of_excess"] == pytest.approx(0.8)
