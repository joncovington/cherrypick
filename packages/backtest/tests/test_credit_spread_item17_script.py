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


def row(tid, kind, of, setup, sym, entry, expiry, rets: dict, side="put") -> dict:
    return {"id": tid, "kind": kind, "of": of, "setup": setup, "side": side, "symbol": sym,
            "entry": entry, "expiry": expiry, "settle_flag": None,
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


def build(
    n_per_half: int, effect: float, *, seed=1, setup="trend", symbols=1, prefer=None, halves=("H1", "H2"),
    h1_last="2022-09-01", hold=40,
):  # fmt: skip
    """Signal trades in both halves, each with five baselines. `effect` lifts the signal's ret in
    d20/s1 (or, with `prefer`, in each symbol's preferred cell), in the halves named by `halves`."""
    rng = random.Random(seed)
    side = "call" if setup.endswith("-short") else "put"
    cells = [c for c, _, _ in cs.CELLS["steps"]]
    rows = []
    for half, (a, b) in {"H1": ("2020-02-01", h1_last), "H2": ("2022-11-01", "2025-05-01")}.items():
        lift = effect if half in halves else 0.0
        for i, entry in enumerate(sessions(n_per_half, a, b)):
            sym = f"S{i % symbols}"
            good = prefer(sym) if prefer else "d20/s1"
            expiry = (date.fromisoformat(entry) + timedelta(days=hold)).isoformat()
            tid = f"{setup}:{sym}:{half}:{i}"
            rets = {c: rng.gauss(-0.02, 0.05) + (lift if c == good else 0.0) for c in cells}
            rows.append(row(tid, "trade", None, setup, sym, entry, expiry, rets, side))
            for k in range(5):
                brets = {c: rng.gauss(-0.02, 0.05) for c in cells}
                rows.append(row(f"{tid}#b{k}", "baseline", tid, setup, sym, entry, expiry, brets, side))
    return rows


def test_an_effect_in_one_direction_only_fails():
    # d20/s1 is the best cell in both halves, but beats its baselines only in H2: in H1 the
    # baselines carry the same lift, so the excess there is zero.
    rows = build(130, 0.10, seed=7)
    mid = cs.midpoint(START, END)
    for r in rows:
        if r["kind"] == "baseline" and r["entry"] < mid:
            r["cells"]["d20/s1"]["ret"] += 0.10
    t = cs.analyse(rows, START, END)["tests"]["trend|put|steps|pooled"]
    h1_to_h2, h2_to_h1 = t["directions"]
    assert h1_to_h2["pooled_cell"] == h2_to_h1["pooled_cell"] == "d20/s1"
    assert h1_to_h2["t"] > 2 and not (h2_to_h1["t"] is not None and h2_to_h1["t"] > 2)
    assert t["verdict"] == "fail"


def test_the_pick_never_sees_the_score_half():
    rows = build(130, 0.10, seed=8)
    before = cs.analyse(rows, START, END)["tests"]["trend|put|steps|pooled"]["directions"][0]
    for r in rows:  # make another cell dazzling, but only in the score half of H1 -> H2
        if r["entry"] >= "2022-11-01" and r["kind"] == "trade":
            r["cells"]["d40/s5"]["ret"] = 5.0
    after = cs.analyse(rows, START, END)["tests"]["trend|put|steps|pooled"]["directions"][0]
    assert before["pooled_cell"] == after["pooled_cell"] == "d20/s1"
    assert before["pooled_cell_n"] == after["pooled_cell_n"] == 130


def test_the_pick_half_is_purged_at_the_boundary():
    # H1 entries run to 2022-10-09 and hold 60 days: the last ones expire after H2's first entry.
    rows = build(130, 0.10, seed=9, h1_last="2022-10-09", hold=60)
    d = cs.analyse(rows, START, END)["tests"]["trend|put|steps|pooled"]["directions"]
    mid = cs.midpoint(START, END)
    first_h2 = min(r["entry"] for r in rows if r["entry"] >= mid)
    want = sum(1 for r in rows if r["kind"] == "trade" and r["entry"] < mid and r["expiry"] >= first_h2)
    assert want > 0 and d[0]["purged_at_boundary"] == want
    assert d[0]["pooled_cell_n"] == 130 - want and d[0]["n"] == 130  # score half untouched
    last_h1 = max(r["expiry"] for r in rows if r["kind"] == "trade" and r["entry"] < mid)
    want2 = sum(
        1 for r in rows if r["kind"] == "trade" and r["entry"] >= "2022-11-01" and r["entry"] <= last_h1
    )
    assert d[1]["purged_at_boundary"] == want2


def test_p_is_students_t_on_cluster_count_and_stricter_than_normal():
    import math

    assert cs.p_one(2.1, 6) == pytest.approx(1 - cs.core_stats.t_cdf(2.1, 5))
    assert cs.p_one(2.1, 6) > 0.5 * math.erfc(2.1 / math.sqrt(2))
    assert cs.p_one(2.1, 100000) == pytest.approx(0.5 * math.erfc(2.1 / math.sqrt(2)), rel=1e-3)
    assert cs.p_one(None, 10) == 1.0 and cs.p_one(3.0, 1) == 1.0


def test_scored_rows_record_their_priced_baselines():
    rows = build(130, 0.10, seed=10)
    for r in rows:
        if r["kind"] == "baseline" and r["id"].endswith("#b0"):
            del r["cells"]["d20/s1"]
    d = cs.analyse(rows, START, END)["tests"]["trend|put|steps|pooled"]["directions"][0]
    assert d["baselines_priced"] == {4: 130}
    assert all(x["baselines_priced"] == 4 for x in d["rows"])


def test_a_call_side_setup_runs_through_the_tests():
    out = cs.analyse(build(130, 0.10, seed=11, setup="reversion-short"), START, END)
    assert out["tests"]["reversion-short|call|steps|pooled"]["verdict"] == "pass"
    assert out["tests"]["trend|put|steps|pooled"]["verdict"] == "no verdict"


def test_least_bad_names_the_best_worse_half():
    out = cs.analyse(build(130, 0.10, seed=12), START, END)
    lb = cs.least_bad(out["tests"])
    assert lb["test"].startswith("trend|put|steps") and lb["cells"] == ["d20/s1", "d20/s1"]
    assert cs.least_bad({}) is None


def test_holm_runs_on_run_tests_own_32_with_no_verdicts_at_p_1():
    tests = cs.analyse(build(130, 0.10, seed=13), START, END)["tests"]
    assert len(tests) == 32
    nv = [v for v in tests.values() if v["verdict"] == "no verdict"]
    assert len(nv) == 30 and all(v["p"] == 1.0 for v in nv)
    holm = cs.core_stats.holm({k: v["p"] for k, v in tests.items()}, alpha=0.05)
    assert {k: v["holm_ok"] for k, v in tests.items()} == holm
    assert sum(holm.values()) == 2  # pooled and per-symbol on the one priced setup-side


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
EXPIRY = "2021-04-16"


def stores(tmp_path, sym: str, bars, *, splits=(), chains=(), empty=()):
    """A synthetic history.db and eod_chains.db for one symbol. `chains`: (entry, forward) pairs."""
    import sqlite3

    from cherrypick.backtest import chainfetch

    hist = tmp_path / f"{sym}-history.db"
    h = sqlite3.connect(hist)
    h.executescript(
        "CREATE TABLE bars (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL);"
        "CREATE TABLE splits (symbol TEXT, ex_date TEXT, to_factor REAL, for_factor REAL);"
        "CREATE TABLE dividends (symbol TEXT, ex_date TEXT, amount REAL);"
    )
    h.executemany("INSERT INTO bars VALUES (?, ?, 0, 0, 0, ?, 0)", [(sym, d, c) for d, c in bars])
    h.executemany("INSERT INTO splits VALUES (?, ?, 2, 1)", [(sym, d) for d in splits])
    h.commit()
    h.close()
    db = tmp_path / f"{sym}-eod_chains.db"
    c = sqlite3.connect(db)
    c.executescript(chainfetch.SCHEMA)
    for entry, fwd in chains:
        days = (date.fromisoformat(EXPIRY) - date.fromisoformat(entry)).days
        rows = chain_rows(fwd, 0.30, days, [float(k) for k in range(50, 161)])
        chainfetch.write_chain(c, (sym, entry, EXPIRY), [
            {"strike": k, "right": r, "bid": b, "ask": a} for k, r, b, a in rows])  # fmt: skip
    for entry in empty:
        chainfetch.write_chain(c, (sym, entry, EXPIRY), [])
    c.close()
    return hist, db


def run_name(hist, db, sym, entries, *, end=END, expiry=EXPIRY):
    t = {"kind": "trade", "of": None, "setup": "trend", "side": "put", "symbol": sym, "expiry": expiry}
    trades = [{**t, "id": e, "entry": e} for e in entries]
    job = {"symbol": sym, "trades": trades, "chains_db": str(db), "history_db": str(hist),
           "start": START, "end": end, "reports": {sym: []}, "quarters": {}}  # fmt: skip
    return cs.price_name(job)


def test_price_name_reads_synthetic_stores_and_counts_drops(tmp_path):
    entry = "2021-03-10"
    hist, db = stores(
        tmp_path, "XYZ", [(entry, 100.0), (EXPIRY, 92.0)], chains=[(entry, 100.0)], empty=["2021-03-11"]
    )
    out = run_name(hist, db, "XYZ", [entry, "2021-03-11", "2021-03-12"])
    assert [p["id"] for p in out["priced"]] == [entry]
    assert out["priced"][0]["settle_flag"] is None
    cell = out["priced"][0]["cells"]["d30/s1"]
    assert cell["net"] < cell["credit"] * 100  # settled below the short at 92
    assert out["empty"] == {"trend/trade: empty chain": 1}
    assert out["drops"]["trend/trade: no chain in the store"] == 1
    assert any("45-60" in k for k in out["drops"])  # no 45-60 chain held: reported-only drop


def test_price_name_drops_a_split_a_stray_forward_and_out_of_window_trades(tmp_path):
    a, b = "2021-03-10", "2021-03-12"
    hist, db = stores(
        tmp_path, "XYZ", [(a, 100.0), (b, 100.0), (EXPIRY, 100.0)], splits=["2021-03-20"],
        chains=[(a, 100.0), (b, 100.0)],
    )  # fmt: skip
    out = run_name(hist, db, "XYZ", [a])
    assert out["drops"]["trend/trade: excluded: split inside the trade"] == 1 and not out["priced"]
    hist, db = stores(tmp_path, "FWD", [(a, 100.0), (EXPIRY, 100.0)], chains=[(a, 110.0)])
    out = run_name(hist, db, "FWD", [a])
    assert out["drops"]["trend/trade: parity forward more than 5% from the close"] == 1
    hist, db = stores(tmp_path, "WIN", [(a, 100.0), (EXPIRY, 100.0)], chains=[(a, 100.0)])
    assert run_name(hist, db, "WIN", [a], end="2021-04-01")["drops"] == {"trend/trade: after the window": 1}
    out = run_name(hist, db, "WIN", [a], expiry="2021-05-21")
    assert out["drops"] == {"trend/trade: expiry not the declared monthly": 1}


def test_price_name_flags_an_earlier_expiry_bar_and_ended_data(tmp_path):
    a = "2021-03-10"
    hist, db = stores(tmp_path, "GAP", [(a, 100.0), ("2021-04-14", 100.0)], chains=[(a, 100.0)])
    out = run_name(hist, db, "GAP", [a])
    assert out["priced"][0]["settle_flag"] == cs.FILLED_EARLIER
    assert out["drops"][f"trend/trade: flagged, kept: {cs.FILLED_EARLIER}"] == 1
    hist, db = stores(tmp_path, "END", [(a, 100.0), ("2021-04-05", 100.0)], chains=[(a, 100.0)])
    out = run_name(hist, db, "END", [a])
    assert out["priced"][0]["settle_flag"] == cs.DATA_ENDED and out["priced"][0]["cells"]


def test_leg_bars_are_judged_per_leg():
    tight = {"bid": 1.00, "ask": 1.05}
    wide = {"bid": 0.10, "ask": 0.30}
    assert cs.leg_clears(tight) and not cs.leg_clears(wide) and not cs.leg_clears({"bid": 0, "ask": 0.05})
    short = {"strike": 95.0, "right": "P", **tight}
    long_ = {"strike": 90.0, "right": "P", **wide}
    prices = cs.legbars.structure_prices([(short, -1), (long_, 1)])
    assert not cs.settle("XYZ", short, long_, prices, 100.0)["liquid"]


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
