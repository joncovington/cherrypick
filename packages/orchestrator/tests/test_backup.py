"""The nightly backup: what it holds, that a live ledger copies consistently, that only the latest is
kept, that a bad night never replaces a good one, and that restore can never touch the live home."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest

from cherrypick.orchestrator import backup, doctor


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(h))
    (h / "config").mkdir(parents=True)
    (h / "config" / "meic.json").write_text('{"x": 1}', encoding="utf-8")
    (h / "config" / "meic.json.bak-20260901").write_text("old", encoding="utf-8")
    (h / "state").mkdir()
    (h / "state" / "halt-live.flag").write_text("", encoding="utf-8")
    # data/earnings is a Dolt server root AND the module's home: its ledger sits beside .dolt/.
    earn = h / "data" / "earnings"
    (earn / ".dolt" / "noms").mkdir(parents=True)
    (earn / ".dolt" / "noms" / "chunk").write_bytes(b"x" * 1000)
    (earn / "stocks" / ".dolt").mkdir(parents=True)
    _ledger(earn / "paper_trades.db", rows=3)
    (h / "data" / "marketdata").mkdir(parents=True)
    _ledger(h / "data" / "marketdata" / "stream_cache.db", rows=1)
    (h / "data" / "market-report" / "browser-profile").mkdir(parents=True)
    (h / "data" / "market-report" / "browser-profile" / "Cookies").write_text("secret", encoding="utf-8")
    (h / "logs").mkdir()
    (h / "logs" / "x.log").write_text("log", encoding="utf-8")
    return h


def _ledger(path: Path, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (i INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    con.close()


CFG: dict = {}


def test_it_holds_the_ledgers_beside_a_dolt_store_and_nothing_regenerable_or_secret(home):
    files, skipped = backup.collect(home)
    rel = {f.relative_to(home).as_posix() for f in files}
    assert "data/earnings/paper_trades.db" in rel, "the earnings ledger sits beside .dolt/ and must be kept"
    assert {"config/meic.json", "state/halt-live.flag"} <= rel
    assert not any(r.startswith(("data/earnings/.dolt", "data/earnings/stocks/.dolt")) for r in rel)
    assert "data/marketdata/stream_cache.db" not in rel
    assert not any("browser-profile" in r or r.startswith("logs/") or ".bak" in r for r in rel)
    assert skipped["dolt_store"] == 2


def test_a_night_copies_a_live_wal_ledger_consistently_and_verifies(home):
    # A write still in the WAL (not checkpointed) must be in the copy: the backup API reads it.
    con = sqlite3.connect(home / "data" / "earnings" / "paper_trades.db")
    con.execute("INSERT INTO t VALUES (99)")
    con.commit()
    res = backup.run(CFG)
    con.close()
    assert res["ok"] and res["sqlite"] == 1
    with zipfile.ZipFile(backup.latest_path(CFG)) as zf:
        data = zf.read("data/earnings/paper_trades.db")
    p = home.parent / "copy.db"
    p.write_bytes(data)
    assert sqlite3.connect(p).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 4
    assert backup.verify(CFG)["ok"]


def test_only_the_latest_is_kept_and_rewritten(home):
    backup.run(CFG)
    _ledger(home / "data" / "flies" / "paper_trades.db", rows=5)
    backup.run(CFG)
    dest = backup.settings(CFG)["dest"]
    assert sorted(p.name for p in dest.iterdir()) == ["cherrypick-backup.zip"]
    with zipfile.ZipFile(backup.latest_path(CFG)) as zf:
        assert "data/flies/paper_trades.db" in zf.namelist()


def test_a_bad_night_never_replaces_the_good_backup(home):
    assert backup.run(CFG)["ok"]
    good = backup.latest_path(CFG).read_bytes()
    (home / "data" / "earnings" / "paper_trades.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    res = backup.run(CFG)
    assert not res["ok"] and res["kept_previous"]
    assert backup.latest_path(CFG).read_bytes() == good
    assert (backup.settings(CFG)["dest"] / backup.FAILED).exists()


def test_restore_never_writes_into_the_live_home_or_over_files(home, tmp_path):
    backup.run(CFG)
    assert not backup.restore(CFG, str(home / "restored"))["ok"]
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "f").write_text("x", encoding="utf-8")
    assert not backup.restore(CFG, str(busy))["ok"]
    out = backup.restore(CFG, str(tmp_path / "restore-here"))
    assert out["ok"] and (tmp_path / "restore-here" / "data" / "earnings" / "paper_trades.db").exists()


def test_doctor_warns_when_the_backup_is_missing_or_stale(home):
    assert doctor._backup_check(CFG).status == doctor.WARN
    backup.run(CFG)
    assert doctor._backup_check(CFG).status == doctor.OK
    old = time.time() - 40 * 3600
    os.utime(backup.latest_path(CFG), (old, old))
    assert doctor._backup_check(CFG).status == doctor.WARN


def test_a_pid_file_is_never_backed_up(home):
    # Runtime state: restored it would name a dead process; it comes and goes as daemons stop.
    (home / "data" / "marketdata" / "streamer.pid").write_text("123", encoding="utf-8")
    files, _ = backup.collect(home)
    assert "data/marketdata/streamer.pid" not in {f.relative_to(home).as_posix() for f in files}


def test_a_file_that_vanishes_mid_run_is_recorded_not_a_failed_night(home, monkeypatch):
    # 2026-10-08: a daemon's file deleted between the walk and the copy failed the whole night.
    gone = home / "state" / "transient.json"
    gone.write_text("{}", encoding="utf-8")
    real_collect = backup.collect

    def collect_then_delete(root):
        files, skipped = real_collect(root)
        gone.unlink()
        return files, skipped

    monkeypatch.setattr(backup, "collect", collect_then_delete)
    res = backup.run(CFG)
    assert res["ok"], res
    with zipfile.ZipFile(backup.latest_path(CFG)) as zf:
        manifest = json.loads(zf.read(backup.MANIFEST))
    assert manifest["vanished"] == ["state/transient.json"]
