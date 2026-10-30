"""The store registry: every vendor store declared with the code that rebuilds it, and the inventory
naming any store nobody declared."""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from test_fetch import _settings

from cherrypick.backtest import __main__ as cli
from cherrypick.backtest import paths, readout, stores

REPO = Path(__file__).resolve().parents[3]


def test_every_declared_store_names_its_builder_and_a_script_builder_is_committed():
    files = [e["file"] for e in stores.STORES]
    assert len(files) == len(set(files))
    for e in stores.STORES:
        assert e["holds"] and e["built_by"] and e["used_by"], e["file"]
        script = stores.script_of(e)
        if script:
            path = REPO / script
            assert path.is_file(), f"{e['file']} is built by {script}, which is not in the repo"
            tracked = subprocess.run(
                ["git", "-C", str(REPO), "ls-files", "--error-unmatch", script], capture_output=True
            )
            assert tracked.returncode == 0, f"{script} exists but is not committed"


def _db(path: Path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (session TEXT, v REAL)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [("2024-01-02", 1.0), ("2024-03-05", 2.0)])
    con.commit()
    con.close()


def test_inventory_shapes_declared_stores_flags_missing_and_names_the_undeclared(tmp_path):
    _db(tmp_path / "SPX-60s.db")
    _db(tmp_path / "mystery.db")
    out = stores.inventory({}, vendor_dir=tmp_path, cache_path=tmp_path / "cache.json")
    by = {s["file"]: s for s in out["stores"]}
    assert by["SPX-60s.db"]["present"] and by["SPX-60s.db"]["tables"]["t"] == {
        "rows": 2,
        "span": ["2024-01-02", "2024-03-05"],
    }
    assert by["flow.db"]["present"] is False
    assert [u["file"] for u in out["undeclared"]] == ["mystery.db"]


def test_a_shape_is_cached_until_the_file_changes(tmp_path, monkeypatch):
    _db(tmp_path / "SPX-60s.db")
    calls = []
    real = stores._shape
    monkeypatch.setattr(stores, "_shape", lambda p: calls.append(p.name) or real(p))
    cache = tmp_path / "c" / "cache.json"
    stores.inventory({}, vendor_dir=tmp_path, cache_path=cache)
    stores.inventory({}, vendor_dir=tmp_path, cache_path=cache)
    assert calls == ["SPX-60s.db"]
    con = sqlite3.connect(tmp_path / "SPX-60s.db")
    con.execute("INSERT INTO t VALUES ('2024-06-03', 3.0)")
    con.commit()
    con.close()

    os.utime(tmp_path / "SPX-60s.db", (1, 2_000_000_000))
    out = stores.inventory({}, vendor_dir=tmp_path, cache_path=cache)
    assert calls == ["SPX-60s.db", "SPX-60s.db"]
    assert {s["file"]: s for s in out["stores"]}["SPX-60s.db"]["tables"]["t"]["rows"] == 3


def test_the_cache_is_banked_store_by_store_so_a_cut_off_read_keeps_what_it_counted(tmp_path, monkeypatch):
    # Two changed stores; the read dies on the second (the bridge's timeout, in life). The first
    # store's count is on disk, so the next read counts only the second. Before 2026-10-28 the cache
    # was written once at the end, and a read that never reached the end banked nothing.

    _db(tmp_path / "SPX-60s.db")
    _db(tmp_path / "flow.db")
    cache = tmp_path / "cache.json"
    calls = []
    real = stores._shape

    def shape(p):
        calls.append(p.name)
        if p.name == "flow.db" and len(calls) == 2:
            raise KeyboardInterrupt  # the read cut off mid-count
        return real(p)

    monkeypatch.setattr(stores, "_shape", shape)
    try:
        stores.inventory({}, vendor_dir=tmp_path, cache_path=cache)
    except KeyboardInterrupt:
        pass
    assert list(json.loads(cache.read_text(encoding="utf-8"))) == ["SPX-60s.db"]
    out = stores.inventory({}, vendor_dir=tmp_path, cache_path=cache)
    assert calls == ["SPX-60s.db", "flow.db", "flow.db"]
    assert sorted(json.loads(cache.read_text(encoding="utf-8"))) == ["SPX-60s.db", "flow.db"]
    assert {s["file"]: s for s in out["stores"]}["flow.db"]["tables"]["t"]["rows"] == 2


def test_without_recount_a_changed_store_keeps_its_last_count_marked_pending_and_nothing_is_written(
    tmp_path, monkeypatch
):
    # The console's read (2026-10-30): a cold count of the largest stores outlasts the bridge's 30 s,
    # so the read never counts. A changed store comes back with its last count, marked pending, and
    # the cache is left for the `inventory` verb to update.
    _db(tmp_path / "SPX-60s.db")
    _db(tmp_path / "flow.db")
    cache = tmp_path / "cache.json"
    stores.inventory({}, vendor_dir=tmp_path, cache_path=cache)
    before = cache.read_text(encoding="utf-8")
    con = sqlite3.connect(tmp_path / "SPX-60s.db")
    con.execute("INSERT INTO t VALUES ('2024-06-03', 3.0)")
    con.commit()
    con.close()
    os.utime(tmp_path / "SPX-60s.db", (1, 2_000_000_000))
    _db(tmp_path / "mystery.db")
    calls = []
    monkeypatch.setattr(stores, "_shape", lambda p: calls.append(p.name) or {"tables": {}})
    out = stores.inventory({}, vendor_dir=tmp_path, cache_path=cache, recount=False)
    assert calls == []
    assert cache.read_text(encoding="utf-8") == before
    by = {s["file"]: s for s in out["stores"]}
    assert by["SPX-60s.db"]["pending"] is True and by["SPX-60s.db"]["tables"]["t"]["rows"] == 2
    assert "pending" not in by["flow.db"]
    (u,) = out["undeclared"]
    assert u["file"] == "mystery.db" and u["tables"] == {} and u["pending"] is True
    assert out["recounted"] == []


def test_the_overview_never_counts_a_store(monkeypatch):
    # The guard on the console's read path: an overview that counted would be cut off by the bridge.
    s = _settings()
    paths.vendor_dir(s).mkdir(parents=True, exist_ok=True)
    _db(paths.vendor_dir(s) / "SPX-convex-stack.db")
    monkeypatch.setattr(stores, "_shape", lambda p: pytest.fail(f"the overview counted {p.name}"))
    inv = readout.overview(s)["inventory"]
    assert {x["file"]: x for x in inv["stores"]}["SPX-convex-stack.db"]["pending"] is True


def test_the_inventory_verb_counts_what_changed_and_says_which(monkeypatch, capsys):
    s = _settings()
    paths.vendor_dir(s).mkdir(parents=True, exist_ok=True)
    _db(paths.vendor_dir(s) / "flow.db")
    monkeypatch.setattr(cli.settings, "load", lambda _c: s)
    assert cli.main(["inventory"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "recounted": ["flow.db"], "pending": []}
    assert cli.main(["inventory"]) == 0
    assert json.loads(capsys.readouterr().out)["recounted"] == []
