"""The full set's planner (`fullset`), its stores and its script (`scripts/fetch_full_set.py`)."""

from __future__ import annotations

import ast
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from cherrypick.backtest import fullset, intradayfetch, store, stores

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_full_set.py"


def _script():
    spec = importlib.util.spec_from_file_location("fetch_full_set", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_weekly_expiry_moves_off_a_holiday():
    assert fullset.weekly_expiry(date(2020, 4, 10)) == date(2020, 4, 9)  # Good Friday
    assert fullset.weekly_expiry(date(2020, 4, 17)) == date(2020, 4, 17)


def test_monthlies_are_third_fridays():
    assert fullset.monthlies(date(2020, 1, 1), date(2020, 3, 31)) == [
        date(2020, 1, 17), date(2020, 2, 21), date(2020, 3, 20)]  # fmt: skip


def test_near_requests_are_trading_days_one_to_seven_out():
    doc = fullset.near_requests("SPX", "2020-01-06", "2020-01-06")
    assert doc["store"] == "SPX-near-1m.db"
    exps = [r["expiration"] for r in doc["requests"]]
    assert exps == ["2020-01-07", "2020-01-08", "2020-01-09", "2020-01-10", "2020-01-13"]
    r = doc["requests"][0]
    assert (r["symbol"], r["start"], r["end"], r["interval"]) == ("SPXW", "09:30", "16:00", "1m")
    assert r["strike_range"] == fullset.SPEC["SPX"]["near_strikes"]


def test_near_window_ends_at_an_early_close():
    doc = fullset.near_requests("SPY", "2020-11-27", "2020-11-27")  # day after Thanksgiving
    assert {r["end"] for r in doc["requests"]} == {"13:00"}


def test_mid_expiries_hold_monthlies_to_90_and_weeklies_to_45():
    day = date(2020, 1, 6)
    exps = fullset.mid_expiries(day)
    assert all(8 <= (e - day).days <= 90 for e in exps)
    assert all((e - day).days <= 45 or e in fullset.monthlies(day, e) for e in exps)
    assert date(2020, 3, 20) in exps and date(2020, 2, 28) not in exps


def test_paths_stay_inside_the_span():
    doc = fullset.path_requests("2020-01-01", "2020-06-30")
    assert doc["requests"]
    for r in doc["requests"]:
        assert "2020-01-01" <= r["start"] <= r["through"] <= "2020-06-30"
        assert r["through"] <= r["expiration"] or r["through"] == "2020-06-30"
    roots = {r["symbol"] for r in doc["requests"]}
    assert {"SPXW", "SPX", "TLT"} <= roots


def test_plan_names_every_file_the_chain_reads():
    files = fullset.plan("2020-01-02", "2020-01-03")
    chain = _chain()
    read = {Path(s["task_args"].get("requests") or s["task_args"].get("plan", "")).name
            for s in chain if "task_args" in s} - {""}  # fmt: skip
    assert read == set(files)


def _chain() -> list[dict]:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "CHAIN")
    return list(ast.literal_eval(node.value))


def test_chain_covers_every_underlying_in_order():
    steps = [s["step"] for s in _chain()]
    for part in ("zero", "near", "mid"):
        assert [s.split("-", 1)[1] for s in steps if s.startswith(part + "-")] == list(fullset.SYMBOLS)
    assert steps[0] == "probe"
    zero = [s for s in _chain() if s["kind"] == "fetch-sessions"]
    assert {s["task_args"]["from"] for s in zero} == {fullset.START}
    assert {s["task_args"]["store_tag"] for s in zero} == {"full"}


def test_full_tag_starts_where_the_set_does():
    assert store.TAGS["full"]["from"] == fullset.START


def test_every_set_store_is_declared():
    files = {e["file"] for e in stores.STORES}
    for sym in fullset.SYMBOLS:
        assert {fullset.zero_store(sym), fullset.near_store(sym), fullset.mid_store(sym)} <= files


def test_core_spells_the_same_underlyings():
    from cherrypick.core import sources

    assert sources.FULL_SET == fullset.SYMBOLS
    names = {s.name for s in sources.SOURCES}
    for sym in fullset.SYMBOLS:
        assert {f"vendor-0dte-{sym.lower()}-60s-full", f"vendor-near-{sym.lower()}",
                f"vendor-mid-{sym.lower()}"} <= names  # fmt: skip


def test_a_request_file_may_name_only_a_declared_intraday_store(tmp_path):
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"store": "SPX-near-1m.db", "requests": []}), encoding="utf-8")
    assert intradayfetch.request_store(ok) == "SPX-near-1m.db"
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps({"requests": []}), encoding="utf-8")
    assert intradayfetch.request_store(plain) == intradayfetch.DB_NAME
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"store": "../elsewhere.db", "requests": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        intradayfetch.request_store(bad)


def test_tally_counts_finished_and_refused_of_the_plan():
    mod = _script()
    planned = [("A",), ("B",), ("C",), ("D",)]
    ledger = [("A", "complete"), ("B", "empty"), ("C", "refused"), ("Z", "complete")]
    assert mod.tally(planned, ledger) == {"done": 2, "total": 4, "refused": 1, "share": 0.5}
    up = mod.roll_up({"x": mod.part(1, 2), "y": mod.part(3, 8, 1)})
    assert up == {"done": 4, "total": 10, "refused": 1, "share": 0.4}


def test_estimate_counts_bars_strikes_and_sides():
    mod = _script()
    doc = {"requests": [{"interval": "5m", "start": "09:30", "end": "10:00", "strike_range": 2}]}
    assert mod.estimate(doc) == 7 * 2 * 5
