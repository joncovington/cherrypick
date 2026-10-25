"""Hypothesis 17's study (scripts/credit_spread_item17.py): strikes, widths, expiry, settlement,
baseline pairing and the declared pass rule.

Synthetic chains and rows only, no store. The pass rule is shown FIRING on a built-in effect and
FAILING without one, because a check that cannot fire reads as coverage it is not.
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
from cherrypick.core import bs  # noqa: E402

START, END = cs.DECLARED_START, cs.DECLARED_END


def chain_rows(forward: float, vol: float, days: int, strikes, half_spread: float = 0.02):
    years = days / 365.0
    rows = []
    for k in strikes:
        for r in ("C", "P"):
            p = bs.price(forward, k, years, vol, r, 0.0)
            rows.append(
                (
                    k,
                    "CALL" if r == "C" else "PUT",
                    max(round(p - half_spread, 2), 0.0),
                    round(p + half_spread, 2),
                )
            )
    return rows


def make_chain(forward=100.0, vol=0.30, days=35, strikes=None):
    strikes = strikes or [float(k) for k in range(60, 141)]
    session = "2021-03-01"
    expiry = (date.fromisoformat(session) + timedelta(days=days)).isoformat()
    return cs.Chain(chain_rows(forward, vol, days, strikes), forward, session, expiry)


# ------------------------------------------------------------------------------- delta and strikes
def test_parity_forward_and_black76_delta_recover_the_model():
    ch = make_chain(forward=100.0)
    assert ch.forward == pytest.approx(100.0, abs=1e-6)
    put = ch.by["P"][90.0]
    iv = ch.iv(put)
    assert iv == pytest.approx(0.30, abs=0.01)
    d = ch.delta(put)
    assert d == pytest.approx(bs.delta(100.0, 90.0, 35 / 365, 0.30, "P", 0.0), abs=0.01)
    call = ch.by["C"][90.0]
    assert ch.delta(call) - d == pytest.approx(1.0, abs=0.02)  # r = 0: call delta - put delta = 1


def test_the_short_is_the_otm_strike_nearest_each_delta():
    ch = make_chain()
    for target in cs.SHORTS:
        for right in ("P", "C"):
            short = cs.pick_short(ch, right, target)
            otm = [q for q in ch.otm(right) if ch.delta(q) is not None]
            want = min(otm, key=lambda q: abs(abs(ch.delta(q)) - target))
            assert short["strike"] == want["strike"]
            assert (short["strike"] < 100) if right == "P" else (short["strike"] > 100)


def test_strike_step_widths_count_listed_strikes_beyond_the_short():
    strikes = [float(k) for k in range(60, 141, 5)]
    ch = make_chain(strikes=strikes)
    short = ch.by["P"][90.0]
    assert cs.long_by_steps(ch, "P", short, 1)["strike"] == 85.0
    assert cs.long_by_steps(ch, "P", short, 5)["strike"] == 65.0
    assert cs.long_by_steps(ch, "P", short, 7) is None
    call = ch.by["C"][110.0]
    assert cs.long_by_steps(ch, "C", call, 2)["strike"] == 120.0


def test_expected_move_widths_and_the_short_own_strike_rule():
    ch = make_chain(days=36)
    short = ch.by["P"][90.0]
    atm = ch.atm_iv()
    assert atm == pytest.approx(0.30, abs=0.01)
    move = atm * (36 / 365) ** 0.5 * 100.0  # about 9.4
    assert cs.long_by_em(ch, "P", short, 0.5, atm)["strike"] == round(90 - 0.5 * move)
    assert cs.long_by_em(ch, "C", ch.by["C"][110.0], 0.75, atm)["strike"] == round(110 + 0.75 * move)
    # On $10 strikes a quarter move rounds back to the short: no trade.
    wide = make_chain(strikes=[float(k) for k in range(50, 151, 10)], days=36)
    cells = cs.price_cells(wide, "P", "XYZ", 100.0)
    assert any(v == "long would be the short's strike" for k, v in cells.items() if "em0.25" in k)


def test_every_cell_prices_on_a_full_chain():
    cells = cs.price_cells(make_chain(), "P", "XYZ", 100.0)
    assert set(cells) == set(cs.ALL_CELLS)
    assert all(isinstance(v, dict) for v in cells.values())
    assert all(v["credit"] < v["width"] for v in cells.values())


def test_expiry_is_the_latest_monthly_in_the_window():
    # 2021-03-01: monthlies 2021-04-16 (46 days) and 2021-03-19 (18); 30-45 has none.
    assert cs.plan.expiry_for("2021-03-01") is None
    assert cs.expiry_in("2021-03-01", 45, 60) == "2021-04-16"
    assert cs.plan.expiry_for("2021-03-10") == "2021-04-16"  # 37 days
    assert cs.expiry_in("2021-02-12", 30, 70) == "2021-04-16"  # latest of two


def test_settlement_is_intrinsic_capped_at_the_width_with_5_per_itm_leg():
    short = {"strike": 95.0, "right": "P", "bid": 2.0, "ask": 2.1}
    long_ = {"strike": 90.0, "right": "P", "bid": 1.0, "ask": 1.1}
    prices = cs.legbars.structure_prices([(short, -1), (long_, 1)])
    fee = cs.fees.ic_open_fee("XYZ", 1, legs=2, sell_legs=1, ndigits=4)
    otm = cs.settle("XYZ", short, long_, prices, 100.0)
    assert otm["net"] == pytest.approx(prices["working"] * 100 - fee)
    part = cs.settle("XYZ", short, long_, prices, 93.0)  # short ITM by 2
    assert part["net"] == pytest.approx((prices["working"] - 2.0) * 100 - fee - 5.0)
    deep = cs.settle("XYZ", short, long_, prices, 80.0)  # both ITM, capped at 5
    assert deep["net"] == pytest.approx((prices["working"] - 5.0) * 100 - fee - 10.0)
    assert deep["max_loss"] == pytest.approx((5.0 - prices["working"]) * 100 + fee)
    assert deep["ret"] == pytest.approx(deep["net"] / deep["max_loss"])


# ------------------------------------------------------------------------------ pairing and rules
def res(ret: float) -> dict:
    return {"width": 5.0, "credit": 1.5, "net": ret * 350, "ret": ret, "max_loss": 350.0,
            "costs": 1.5, "slippage": 2.5, "liquid": True}  # fmt: skip


def row(tid, kind, of, setup, sym, entry, expiry, rets: dict) -> dict:
    return {"id": tid, "kind": kind, "of": of, "setup": setup, "side": "put", "symbol": sym,
            "entry": entry, "expiry": expiry, "stale": False,
            "cells": {c: res(r) for c, r in rets.items()}}  # fmt: skip


def test_baselines_pair_in_the_same_cell_only():
    t = row("t", "trade", None, "trend", "X", "2021-01-04", "2021-02-19", {"d20/s1": 0.2, "d20/s2": 0.1})
    b1 = row("t#b1", "baseline", "t", "trend", "X", "2021-05-03", "2021-06-18", {"d20/s1": 0.0})
    b2 = row(
        "t#b2", "baseline", "t", "trend", "X", "2021-07-01", "2021-08-20", {"d20/s1": -0.1, "d20/s2": 0.1}
    )
    base = cs.index_baselines([t, b1, b2])["t"]
    assert cs.paired_diff(t, base, "d20/s1") == pytest.approx(0.2 - (-0.05))
    assert cs.paired_diff(t, base, "d20/s2") == pytest.approx(0.0)
    assert cs.paired_diff(t, base, "d30/s1") is None


def sessions(n: int, first: str, last: str):
    a, b = date.fromisoformat(first), date.fromisoformat(last)
    step = (b - a).days / n
    return [(a + timedelta(days=int(i * step))).isoformat() for i in range(n)]


def build(n_per_half: int, effect: float, *, seed=1, setup="trend", symbols=1, prefer=None):
    """Signal trades in both halves, each with five baselines. `effect` lifts the signal's ret in
    d20/s1 (or, with `prefer`, in each symbol's preferred cell)."""
    rng = random.Random(seed)
    cells = [c for c, _, _ in cs.CELLS["steps"]]
    rows = []
    for half, (a, b) in {"H1": ("2020-02-01", "2022-09-01"), "H2": ("2022-11-01", "2025-05-01")}.items():
        for i, entry in enumerate(sessions(n_per_half, a, b)):
            sym = f"S{i % symbols}"
            good = prefer(sym) if prefer else "d20/s1"
            expiry = (date.fromisoformat(entry) + timedelta(days=40)).isoformat()
            tid = f"{setup}:{sym}:{half}:{i}"
            rets = {c: rng.gauss(-0.02, 0.05) + (effect if c == good else 0.0) for c in cells}
            rows.append(row(tid, "trade", None, setup, sym, entry, expiry, rets))
            for k in range(5):
                brets = {c: rng.gauss(-0.02, 0.05) for c in cells}
                rows.append(row(f"{tid}#b{k}", "baseline", tid, setup, sym, entry, expiry, brets))
    return rows


def test_the_pass_rule_fires_on_a_built_in_effect():
    out = cs.analyse(build(130, 0.10), START, END)
    t = out["tests"]["trend|put|steps|pooled"]
    assert [d["pooled_cell"] for d in t["directions"]] == ["d20/s1", "d20/s1"]
    assert all(d["n"] == 130 and d["t"] > 2 for d in t["directions"])
    assert t["verdict"] == "pass" and t["holm_ok"]
    assert out["tests"]["trend|put|em|pooled"]["verdict"] == "no verdict"  # no em cells priced
    assert "trend|put|steps|pooled" in out["b13"]
    assert out["b13"]["trend|put|steps|pooled"]["cost_stress"]["survives"]
    assert len(out["tests"]) == 32


def test_the_pass_rule_fails_without_an_effect():
    out = cs.analyse(build(130, 0.0, seed=2), START, END)
    t = out["tests"]["trend|put|steps|pooled"]
    assert t["verdict"] == "fail"
    assert not out["b13"]


def test_an_effect_with_a_losing_rule_still_fails():
    rows = build(130, 0.10, seed=3)
    for r in rows:  # baselines lose more, but the rule itself loses money
        for c in r["cells"].values():
            c["ret"] -= 0.2
            c["net"] = c["ret"] * 350
    t = cs.analyse(rows, START, END)["tests"]["trend|put|steps|pooled"]
    assert all(d["t"] > 2 for d in t["directions"]) and t["verdict"] == "fail" and not t["own_ok"]


def test_under_100_scored_trades_is_no_verdict():
    t = cs.analyse(build(99, 0.10), START, END)["tests"]["trend|put|steps|pooled"]
    assert t["verdict"] == "no verdict" and t["p"] == 1.0


def test_holm_across_the_32_rejects_a_lone_marginal_p():
    tests = {f"k{i}": {"verdict": None, "t_ok": True, "own_ok": True, "p": 1.0} for i in range(32)}
    tests["k0"]["p"] = 0.01  # passes alone at 0.05, not at 0.05 / 32
    cs.apply_holm(tests)
    assert tests["k0"]["verdict"] == "fail"
    tests["k0"].update(verdict=None, p=0.001)
    cs.apply_holm(tests)
    assert tests["k0"]["verdict"] == "pass"


def test_per_symbol_pick_earns_its_place_only_when_it_beats_pooled():
    prefer = lambda s: "d20/s1" if int(s[1:]) % 2 else "d40/s5"  # noqa: E731
    out = cs.analyse(build(200, 0.15, seed=4, symbols=20, prefer=prefer), START, END)
    ps = out["tests"]["trend|put|steps|per_symbol"]
    assert ps["verdict"] == "pass"
    assert all(d["symbols_with_own_cell"] == 20 for d in ps["directions"])
    assert ps["vs_pooled"]["beats"] and ps["earns_place"]
    # One cell for every name: per-symbol picks add nothing over pooled.
    same = cs.analyse(build(200, 0.15, seed=5, symbols=20), START, END)["tests"]["trend|put|steps|per_symbol"]
    assert same["verdict"] == "pass" and not same["vs_pooled"]["beats"] and not same["earns_place"]


def test_per_symbol_needs_8_trades_else_pooled():
    rows = build(130, 0.10, seed=6, symbols=40)  # about 3 trades a symbol a half
    ps = cs.analyse(rows, START, END)["tests"]["trend|put|steps|per_symbol"]
    assert all(d["symbols_with_own_cell"] == 0 for d in ps["directions"])


def test_descriptive_additions_never_touch_the_verdict():
    out = cs.analyse(build(130, 0.10), START, END)
    assert set(out["name_halves"]) == {"name_half_0", "name_half_1"}
    mde = out["tests"]["trend|put|steps|pooled"]["directions"][0]["planned_mde"]
    assert mde["paired"] and mde["mde"] > 0


def test_cluster_t_counts_expiries_not_trades():
    xs = [1.0, 1.2, 0.8, 1.1]
    naive = cs.cluster_t(xs, ["a", "b", "c", "d"])
    lumped = cs.cluster_t(xs + [-1.0], ["a", "a", "a", "a", "b"])
    assert naive is not None and lumped is not None and abs(lumped) < naive


# ------------------------------------------------------------------------------- the store path
def test_price_name_reads_synthetic_stores_and_counts_drops(tmp_path):
    import sqlite3

    from cherrypick.backtest import chainfetch

    hist = tmp_path / "history.db"
    h = sqlite3.connect(hist)
    h.executescript(
        "CREATE TABLE bars (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL);"
        "CREATE TABLE splits (symbol TEXT, ex_date TEXT, to_factor REAL, for_factor REAL);"
        "CREATE TABLE dividends (symbol TEXT, ex_date TEXT, amount REAL);"
    )
    entry, expiry = "2021-03-10", "2021-04-16"
    h.executemany("INSERT INTO bars VALUES ('XYZ', ?, 0, 0, 0, ?, 0)", [(entry, 100.0), (expiry, 92.0)])
    h.commit()
    h.close()
    chains = tmp_path / "eod_chains.db"
    c = sqlite3.connect(chains)
    c.executescript(chainfetch.SCHEMA)
    rows = chain_rows(100.0, 0.30, 37, [float(k) for k in range(60, 141)])
    chainfetch.write_chain(c, ("XYZ", entry, expiry), [
        {"strike": k, "right": r, "bid": b, "ask": a} for k, r, b, a in rows])  # fmt: skip
    chainfetch.write_chain(c, ("XYZ", "2021-03-11", "2021-04-16"), [])
    c.close()
    t = {"kind": "trade", "of": None, "setup": "trend", "side": "put", "symbol": "XYZ", "expiry": expiry}
    trades = [
        {**t, "id": "a", "entry": entry},
        {**t, "id": "b", "entry": "2021-03-11"},  # an empty chain
        {**t, "id": "c", "entry": "2021-03-12"},  # never fetched
    ]
    job = {"symbol": "XYZ", "trades": trades, "chains_db": str(chains), "history_db": str(hist),
           "start": START, "end": END, "reports": {"XYZ": []}, "quarters": {}}  # fmt: skip
    out = cs.price_name(job)
    assert [p["id"] for p in out["priced"]] == ["a"]
    cell = out["priced"][0]["cells"]["d30/s1"]
    assert cell["net"] < cell["credit"] * 100  # settled below the short at 92
    assert out["empty"] == {"trend/trade: empty chain": 1}
    assert out["drops"]["trend/trade: no chain in the store"] == 1
    assert any("45-60" in k for k in out["drops"])  # no 45-60 chain held: reported-only drop


# ------------------------------------------------------------------------------------- licence
def test_the_licence_guard_refuses_a_json_outside_the_backtest_dir(tmp_path, monkeypatch):
    data = tmp_path / "backtest"
    data.mkdir()
    monkeypatch.setattr(cs.home, "data_dir", lambda *a, **k: data)
    chains = data / "vendor" / "thetadata" / "eod_chains.db"
    assert cs.licence_refusal(chains, tmp_path / "elsewhere" / "out.json")
    assert cs.licence_refusal(chains, data / "results" / "out.json") is None
    assert cs.main(["--chains-db", str(chains), "--json", str(tmp_path / "x.json")]) == 2
    assert cs.licence_refusal(tmp_path / "synthetic.db", tmp_path / "x.json") is None
