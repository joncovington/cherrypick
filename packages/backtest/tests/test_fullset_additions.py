"""Underlyings added to the full set on this machine (`fullsetadd`): planned like the seven into three
shared stores, keyed by symbol, so adding one declares no file; recorded locally, never in the repo."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from cherrypick.backtest import fullset, fullsetadd, intradayfetch, store, stores

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_full_set.py"
TODAY = date(2026, 11, 18)


def _script():
    spec = importlib.util.spec_from_file_location("fetch_full_set_add", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_addition_shares_three_declared_stores_and_spells_its_own_root():
    assert (fullset.zero_store("ABC"), fullset.near_store("ABC"), fullset.mid_store("ABC")) == (
        fullset.ADDED_ZERO, fullset.ADDED_NEAR, fullset.ADDED_MID)  # fmt: skip
    assert fullset.zero_store("SPY") == "SPY-60s-full.db", "the seven keep their own files"
    assert fullset.spec_for("ABC")["root"] == "ABC" and fullset.spec_for("SPX")["root"] == "SPXW"
    declared = {e["file"] for e in stores.STORES}
    assert {fullset.ADDED_ZERO, fullset.ADDED_NEAR, fullset.ADDED_MID} <= declared
    assert {fullset.ADDED_NEAR, fullset.ADDED_MID} <= set(intradayfetch.STORES)
    s = store.tagged({"store": {"strikes_each_side": 20, "request_strike_range": 50, "cadence_seconds": 15,
                                "dir": None}, "vendor": {}}, "added")  # fmt: skip
    assert store.db_path({**s, "store": {**s["store"], "dir": "x"}}, "ABC", 60).name == "added-60s.db"


def test_an_additions_plan_names_the_shared_stores_and_counts_its_sessions():
    files = fullset.plan_added("ABC", "2026-11-02", "2026-11-13")
    assert set(files) == set(fullset.added_files("ABC"))
    assert (
        files["near_ABC.json"]["store"] == fullset.ADDED_NEAR
        and files["mid_ABC.json"]["store"] == fullset.ADDED_MID
    )
    assert {r["symbol"] for r in files["near_ABC.json"]["requests"]} == {"ABC"}
    assert {r["symbol"] for r in files["paths_ABC.json"]["requests"]} == {"ABC"}
    size = fullset.plan_size(files)
    assert size["sessions"] == 10 and size["near_ABC.json"] > 0
    whole = fullset.plan("2026-11-02", "2026-11-13", {"ABC": {"from": "2026-11-09"}})
    assert fullset.plan_size({"oi_ABC.json": whole["oi_ABC.json"]})["sessions"] == 5, (
        "from its own first session"
    )
    assert "near_ABC.json" not in fullset.plan("2026-11-02", "2026-11-13")
    for bad in ("SPY", "abc!", "TOOLONGX"):
        with pytest.raises(ValueError):
            fullset.plan_added(bad, "2026-11-02", "2026-11-13")


def test_adding_again_earlier_extends_it_and_later_changes_nothing(tmp_path):
    doc = fullsetadd.add("ABC", "2026-01-02", TODAY, tmp_path)
    assert doc == {"ABC": {"from": "2026-01-02", "added": "2026-11-18"}}
    assert fullsetadd.add("ABC", "2025-06-02", date(2026, 12, 1), tmp_path)["ABC"] == {
        "from": "2025-06-02", "added": "2026-11-18"}  # fmt: skip
    assert fullsetadd.add("ABC", "2026-03-02", TODAY, tmp_path)["ABC"]["from"] == "2025-06-02"
    for symbol, start in (("QQQ", "2026-01-02"), ("abc", "2026-01-02"), ("ABC", "2019-12-31")):
        with pytest.raises(ValueError):
            fullsetadd.merged({}, symbol, start, TODAY)


def test_the_added_tag_fetches_an_added_symbol_and_refuses_one_never_added(tmp_path, monkeypatch):
    from cherrypick.backtest import fetch

    monkeypatch.setattr(fullsetadd, "load", lambda where=None: {"ABC": {"from": "2025-11-17"}})
    s = {"datasets": [], "store": {"tag": "added"}}
    assert fetch._dataset(s, "abc") == {"symbol": "ABC", "root": "ABC", "dte": 0, "from": "2025-11-17"}
    with pytest.raises(ValueError, match="has not been added"):
        fetch._dataset(s, "XYZ")
    with pytest.raises(ValueError, match="no dataset"):
        fetch._dataset({"datasets": [], "store": {"tag": "full"}}, "ABC")


def test_a_same_day_ledger_gives_each_symbols_span_from_its_complete_sessions(tmp_path):
    db = tmp_path / fullset.ADDED_ZERO
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE vendor_sessions (symbol TEXT, trade_date TEXT, status TEXT, reason TEXT)")
    conn.executemany("INSERT INTO vendor_sessions VALUES (?, ?, ?, NULL)", [
        ("ABC", "2026-11-13", "complete"), ("ABC", "2026-11-16", "complete"),
        ("ABC", "2026-11-17", "refused"),
        ("XYZ", "2026-11-16", "complete")])  # fmt: skip
    conn.commit()
    conn.close()
    spans = stores._shape(db)["tables"]["vendor_sessions"]["symbols"]
    assert spans == {"ABC": {"first": "2026-11-13", "last": "2026-11-16", "sessions": 2},
                     "XYZ": {"first": "2026-11-16", "last": "2026-11-16", "sessions": 1}}  # fmt: skip


def test_progress_counts_an_addition_from_its_own_plan_and_its_rows_in_the_shared_store(tmp_path):
    from cherrypick.backtest import fullsetprogress as fp

    vendor, where = tmp_path / "vendor", tmp_path / "plan"
    vendor.mkdir()
    where.mkdir()
    (where / "oi_plan.json").write_text(json.dumps({"sessions": {"2026-11-16": ["SPXW"]}}), encoding="utf-8")
    for name, body in fullset.plan_added("ABC", "2026-11-13", "2026-11-16").items():
        (where / name).write_text(json.dumps(body), encoding="utf-8")
    fullsetadd.add("ABC", "2026-11-13", TODAY, where)
    conn = sqlite3.connect(vendor / fullset.ADDED_ZERO)
    conn.execute("CREATE TABLE vendor_sessions (symbol TEXT, trade_date TEXT, status TEXT, reason TEXT)")
    conn.executemany("INSERT INTO vendor_sessions VALUES (?, ?, 'complete', NULL)",
                     [("ABC", "2026-11-13"), ("XYZ", "2026-11-16")])  # fmt: skip
    conn.commit()
    conn.close()
    abc = fp.progress(vendor, where)["by_symbol"]["ABC"]
    assert abc["zero"] == {"done": 1, "total": 2, "refused": 0, "share": 0.5}, (
        "another symbol's row is not ABC's"
    )


def test_the_script_adds_an_underlying_or_only_sizes_it(tmp_path, monkeypatch, capsys):
    mod = _script()
    monkeypatch.setattr(mod, "out_dir", lambda: tmp_path)
    tier = {"vendor": {"subscription": "value", "max_concurrent_requests": 4}}
    monkeypatch.setattr(mod._settings, "load", lambda path=None: tier)
    assert mod.main(["add", "abc", "--from", "2026-11-02", "--through", "2026-11-13", "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["ok"] and dry["requests"]["sessions"] == 10 and dry["concurrent"] == 2
    assert list(tmp_path.iterdir()) == [], "a dry run writes nothing"
    assert mod.main(["add", "ABC", "--from", "2026-11-02", "--through", "2026-11-13"]) == 0
    capsys.readouterr()
    assert fullsetadd.load(tmp_path)["ABC"]["from"] == "2026-11-02"
    assert {p.name for p in tmp_path.iterdir()} == {fullsetadd.FILE, *fullset.added_files("ABC")}
    assert mod.main(["add", "SPY"]) == 2
    assert "one of the set's own" in json.loads(capsys.readouterr().out)["error"]


def test_the_add_chain_is_a_literal_naming_the_shared_tag_and_the_additions_files():
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))

    def named(n):
        return isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "ADD_CHAIN" for t in n.targets)

    chain = next(ast.literal_eval(n.value) for n in tree.body if named(n))
    assert chain[0]["kind"] == "fetch-sessions" and chain[0]["task_args"]["store_tag"] == "added"
    files = {Path(s["task_args"].get("requests") or s["task_args"].get("plan") or "").name for s in chain[1:]}
    assert files == {f.replace("ABC", "{symbol}") for f in fullset.added_files("ABC")}
    assert all(s["step"].startswith("add-{sym}-") for s in chain)
