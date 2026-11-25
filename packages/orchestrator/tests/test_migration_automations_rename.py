"""`v0.14.0-01-automations-rename` on a home that ran `main` before the rename was finished: the module
under its old name in every place the old shims read. Dry run, apply, a second apply that finds nothing, and the shapes it
refuses to guess about, each leaving the home as it was."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cherrypick.orchestrator.migrations import automations_rename as mig

SUITE = (
    "{\r\n"
    '  "_comment": "a note a round trip would keep, but not this spacing",\r\n'
    '  "modules": {\r\n'
    '    "meic": {"enabled": true, "path": "../meic"},\r\n'
    '    "ensemble": {\r\n'
    '      "_comment": "the old block",\r\n'
    '      "enabled": true,\r\n'
    '      "path": "../ensemble",\r\n'
    '      "paper": {\r\n'
    '        "status_argv": ["-m", "cherrypick.ensemble.paper_loop", "--status"],\r\n'
    '        "once_argv": ["-m", "cherrypick.ensemble.paper_loop", "--once"],\r\n'
    '        "paper_db": "~/.cherrypick/data/ensemble/paper_trades.db",\r\n'
    '        "trade_schema": "ensemble_trade",\r\n'
    '        "log": "ensemble_paper.log"\r\n'
    "      }\r\n"
    "    },\r\n"
    '    "weeklyput": {"enabled": false}\r\n'
    "  }\r\n"
    "}\r\n"
)

MODULE = (
    "{\r\n"
    '  "_groups_note": "kept as written",\r\n'
    '  "groups": {"ensemble": {"enabled": false}, "testing": {"enabled": true}},\r\n'
    '  "automations": {\r\n'
    '    "iron-fly-1000": {"enabled": false},\r\n'
    '    "mine": {"group": "ensemble", "enabled": true},\r\n'
    '    "other": {"group": "testing"}\r\n'
    "  }\r\n"
    "}\r\n"
)

REGISTER = {
    "hypotheses": [
        {
            "id": 75,
            "scripts": ["packages/backtest/scripts/ensemble_item75.py"],
            "forward": [{"module": "automations", "schema": "ensemble_trade", "arm": "a"}],
        },
        {"id": 1, "forward": {"module": "meic", "schema": "meic_ic"}},
    ]
}


def _ledger(path: Path, prefix: str = "ensemble", rows: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(
        f"""
        CREATE TABLE {prefix}_positions (position_id TEXT PRIMARY KEY, arm TEXT, status TEXT);
        CREATE INDEX idx_{prefix}_positions_status ON {prefix}_positions(status);
        CREATE TABLE {prefix}_legs (id INTEGER PRIMARY KEY, position_id TEXT, status TEXT,
                                     UNIQUE (position_id, id));
        CREATE INDEX idx_{prefix}_legs_status ON {prefix}_legs(status);
        CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, note TEXT);
        """
    )
    for i in range(rows):
        con.execute(f"INSERT INTO {prefix}_positions VALUES (?, 'a', 'open')", (f"p{i}",))
        con.execute(f"INSERT INTO {prefix}_legs (position_id, status) VALUES (?, 'open')", (f"p{i}",))
    con.commit()
    con.close()


@pytest.fixture
def home(tmp_path) -> Path:
    h = tmp_path / "home"
    (h / "config").mkdir(parents=True)
    (h / "config.json").write_bytes(SUITE.encode())
    (h / "config" / "ensemble.json").write_bytes(MODULE.encode())
    _ledger(h / "data" / "ensemble" / "paper_trades.db")
    (h / "data" / "research").mkdir(parents=True)
    (h / "data" / "research" / "hypotheses.json").write_text(json.dumps(REGISTER, indent=2), encoding="utf-8")
    (h / "state" / "stream_requests").mkdir(parents=True)
    (h / "state" / "stream_requests" / "ensemble.json").write_text("{}", encoding="utf-8")
    (h / "state" / "ensemble.heartbeat").write_text("x", encoding="utf-8")
    (h / "logs" / "ensemble").mkdir(parents=True)
    (h / "logs" / "ensemble" / "ensemble_paper.log").write_text("history", encoding="utf-8")
    return h


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _tables(db: Path) -> dict[str, str]:
    con = sqlite3.connect(db)
    out = dict(con.execute("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())
    con.close()
    return out


def test_a_dry_run_says_what_it_would_do_and_changes_nothing(home):
    before = _snapshot(home)
    out = mig.apply(home, dry_run=True)
    assert out.startswith("would: ")
    for part in (
        "ensemble.json -> automations.json",
        "ensemble -> automations",
        "2 tables and 2 indexes",
        "modules.ensemble -> modules.automations",
        "group ensemble -> default",
        "register",
    ):
        assert part in out, part
    assert _snapshot(home) == before


def test_it_moves_every_old_name_and_keeps_the_rows(home):
    mig.apply(home, dry_run=False)
    assert not (home / "config" / "ensemble.json").exists() and not (home / "data" / "ensemble").exists()
    db = home / "data" / "automations" / "paper_trades.db"
    assert _tables(db) == {
        "automations_positions": "table",
        "automations_legs": "table",
        "measurement_breaks": "table",
        "idx_automations_positions_status": "index",
        "idx_automations_legs_status": "index",
    }
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM automations_positions").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM automations_legs").fetchone()[0] == 2
    con.close()
    assert not (home / "state" / "stream_requests" / "ensemble.json").exists()
    assert not (home / "state" / "ensemble.heartbeat").exists()
    assert (home / "logs" / "ensemble" / "ensemble_paper.log").read_text(encoding="utf-8") == "history"


def test_the_suite_config_gets_the_new_block_in_its_place_and_nothing_else(home):
    mig.apply(home, dry_run=False)
    raw = (home / "config.json").read_bytes()
    assert b"\r\n" in raw and b"\r\r\n" not in raw, "line endings kept"
    doc = json.loads(raw)
    assert list(doc["modules"]) == ["meic", "automations", "weeklyput"]
    block = doc["modules"]["automations"]
    assert block["path"] == "../automations" and block["_comment"] == "the old block"
    assert block["paper"] == {
        "status_argv": ["-m", "cherrypick.automations.paper_loop", "--status"],
        "once_argv": ["-m", "cherrypick.automations.paper_loop", "--once"],
        "paper_db": "~/.cherrypick/data/automations/paper_trades.db",
        "trade_schema": "automations_trade",
        "log": "automations_paper.log",
    }
    # Only the renamed spellings moved: the file is the old one with those words replaced.
    expected = (
        SUITE.replace('"ensemble": {', '"automations": {')
        .replace("../ensemble", "../automations")
        .replace("cherrypick.ensemble.", "cherrypick.automations.")
        .replace("data/ensemble/", "data/automations/")
        .replace("ensemble_trade", "automations_trade")
        .replace("ensemble_paper.log", "automations_paper.log")
    )
    assert raw.decode() == expected


def test_the_group_and_its_members_become_default(home):
    mig.apply(home, dry_run=False)
    raw = (home / "config" / "automations.json").read_bytes().decode()
    assert raw == MODULE.replace('"ensemble": {"enabled"', '"default": {"enabled"').replace(
        '"group": "ensemble"', '"group": "default"'
    )


def test_the_register_names_the_new_schema(home):
    mig.apply(home, dry_run=False)
    reg = json.loads((home / "data" / "research" / "hypotheses.json").read_text(encoding="utf-8"))
    assert reg["hypotheses"][0]["forward"][0]["schema"] == "automations_trade"
    assert reg["hypotheses"][0]["scripts"] == ["packages/backtest/scripts/ensemble_item75.py"], "names kept"
    assert reg["hypotheses"][1]["forward"]["schema"] == "meic_ic"


def test_a_second_run_finds_nothing(home):
    mig.apply(home, dry_run=False)
    after = _snapshot(home)
    assert mig.apply(home, dry_run=False) == "nothing to rename"
    assert mig.apply(home, dry_run=True) == "nothing to rename"
    assert _snapshot(home) == after


def test_a_fresh_home_has_nothing_to_rename(tmp_path):
    assert mig.apply(tmp_path / "none", dry_run=False) == "nothing to rename"


def test_empty_new_tables_made_by_newer_code_are_dropped_first(home):
    db = home / "data" / "ensemble" / "paper_trades.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE automations_positions (position_id TEXT PRIMARY KEY, arm TEXT, status TEXT)")
    con.commit()
    con.close()
    out = mig.apply(home, dry_run=False)
    assert "1 empty new tables dropped first" in out
    con = sqlite3.connect(home / "data" / "automations" / "paper_trades.db")
    assert con.execute("SELECT COUNT(*) FROM automations_positions").fetchone()[0] == 2
    con.close()


def test_two_sets_of_rows_stop_it_with_the_ledger_untouched(home):
    db = home / "data" / "ensemble" / "paper_trades.db"
    _ledger(db.with_name("other.db"), prefix="automations", rows=1)
    con = sqlite3.connect(db)
    con.execute("ATTACH DATABASE ? AS o", (str(db.with_name("other.db")),))
    con.execute("CREATE TABLE automations_positions AS SELECT * FROM o.automations_positions")
    con.commit()
    con.close()
    db.with_name("other.db").unlink()
    with pytest.raises(mig.RenameError, match="both ensemble_positions and automations_positions hold rows"):
        mig.apply(home, dry_run=False)
    tables = _tables(home / "data" / "automations" / "paper_trades.db")
    assert "ensemble_positions" in tables and "idx_ensemble_positions_status" in tables


def test_a_config_with_both_blocks_is_refused(home):
    (home / "config.json").write_text(
        json.dumps({"modules": {"ensemble": {"enabled": True}, "automations": {"enabled": True}}}),
        encoding="utf-8",
    )
    with pytest.raises(mig.RenameError, match="both ensemble and automations"):
        mig.apply(home, dry_run=False)


def test_a_file_with_both_groups_is_refused(home):
    (home / "config" / "ensemble.json").write_text(
        json.dumps({"groups": {"ensemble": {"enabled": False}, "default": {"enabled": True}}}),
        encoding="utf-8",
    )
    with pytest.raises(mig.RenameError, match="both an ensemble and a default group"):
        mig.apply(home, dry_run=False)


def test_where_both_folders_exist_the_new_one_is_kept_and_the_old_named(home):
    _ledger(home / "data" / "automations" / "paper_trades.db", prefix="automations", rows=3)
    out = mig.apply(home, dry_run=False)
    assert "kept ensemble beside automations" in out
    assert (home / "data" / "ensemble" / "paper_trades.db").exists()
    con = sqlite3.connect(home / "data" / "automations" / "paper_trades.db")
    assert con.execute("SELECT COUNT(*) FROM automations_positions").fetchone()[0] == 3
    con.close()
