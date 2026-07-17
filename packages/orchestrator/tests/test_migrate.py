"""migrate-home: moving config into the home and sweeping regenerable leftovers, never deleting *.db."""

from __future__ import annotations

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import migrate


def test_apply_dry_run_touches_nothing(tmp_path):
    src = tmp_path / "repo" / "config.json"
    src.parent.mkdir(parents=True)
    src.write_text('{"modules":{}}')
    dest = tmp_path / "home" / "config.json"
    log = tmp_path / "repo" / "watchdog.log"
    log.write_text("")
    plan_dict = {
        "moves": [{"name": "orchestrator", "src": str(src), "dest": str(dest), "action": "move"}],
        "deletes": [str(log)],
        "db_review": [],
    }
    res = migrate.apply(plan_dict, dry_run=True)
    assert src.exists() and not dest.exists() and log.exists()  # nothing changed
    assert res["dry_run"] and res["moved"] and res["deleted"]


def test_apply_moves_config_sweeps_logs_and_spares_db(tmp_path):
    src = tmp_path / "repo" / "config.json"
    src.parent.mkdir(parents=True)
    src.write_text('{"modules":{}}')
    dest = tmp_path / "home" / "config" / "meic.json"  # nested dest dir must be created
    log = tmp_path / "repo" / "watchdog.log"
    log.write_text("")
    db = tmp_path / "repo" / "paper_trades.db"
    db.write_text("data")
    plan_dict = {
        "moves": [{"name": "meic", "src": str(src), "dest": str(dest), "action": "move"}],
        "deletes": [str(log)],
        "db_review": [str(db)],
    }
    migrate.apply(plan_dict, dry_run=False)
    assert not src.exists() and dest.exists()  # config moved
    assert dest.read_text() == '{"modules":{}}'
    assert not log.exists()  # log swept
    assert db.exists() and db.read_text() == "data"  # .db never touched


def test_plan_moves_orchestrator_config_when_home_absent(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.json").write_text("{}")
    monkeypatch.setattr(cfgmod, "ROOT", repo)
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    p = migrate.plan({"modules": {}, "dashboard": {}})
    actions = {(m["name"], m["action"]) for m in p["moves"]}
    assert ("orchestrator", "move") in actions
    assert p["deletes"] == [] and p["db_review"] == []


def test_plan_skips_when_home_config_exists(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.json").write_text("{}")
    home = tmp_path / "home"
    (home).mkdir()
    (home / "config.json").write_text("{}")  # already migrated
    monkeypatch.setattr(cfgmod, "ROOT", repo)
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    p = migrate.plan({"modules": {}, "dashboard": {}})
    assert ("orchestrator", "skip-dest-exists") in {(m["name"], m["action"]) for m in p["moves"]}
