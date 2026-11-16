"""The module's old name keeps resolving: `cherrypick.ensemble` imports as the same modules, the config
file, data folder and override variables under the old name are read while only they exist, and the
stream request under the old name is removed so the streamer never unions a stale one."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

from cherrypick.core import home
from cherrypick.core import streamrequests as sr

from cherrypick.automations import config as config_mod
from cherrypick.automations import db, engine, paper_loop, stream_request


def test_the_old_import_name_is_the_same_module():
    import cherrypick.ensemble.engine as old_engine

    from cherrypick.automations import arms
    from cherrypick.ensemble import arms as old_arms

    assert old_engine is engine and old_arms is arms
    assert engine.__spec__.name == "cherrypick.automations.engine"  # its own spec, not the alias's
    # `python -m cherrypick.ensemble.paper_loop` (a suite config written before the rename) finds the code.
    ns = runpy.run_module("cherrypick.ensemble.paper_loop", run_name="not_main")
    assert ns["main"].__code__.co_filename == paper_loop.main.__code__.co_filename


def test_the_old_config_file_is_read_while_only_it_exists(managed_home):
    cfg_dir = managed_home / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "ensemble.json").write_text(json.dumps({"defaults": {"marker": "old"}}), encoding="utf-8")
    assert home.config_path("automations") == cfg_dir / "ensemble.json"
    assert config_mod.load_config()["defaults"]["marker"] == "old"
    (cfg_dir / "automations.json").write_text(json.dumps({"defaults": {"marker": "new"}}), encoding="utf-8")
    assert config_mod.load_config()["defaults"]["marker"] == "new"


def test_the_old_data_folder_is_used_while_only_it_exists(managed_home):
    assert db.default_db_path().replace("\\", "/").endswith("data/automations/paper_trades.db")
    (managed_home / "data" / "ensemble").mkdir(parents=True)
    assert db.default_db_path().replace("\\", "/").endswith("data/ensemble/paper_trades.db")
    (managed_home / "data" / "automations").mkdir(parents=True)
    assert db.default_db_path().replace("\\", "/").endswith("data/automations/paper_trades.db")


def test_the_old_override_variables_resolve(monkeypatch, tmp_path):
    monkeypatch.setenv("ENSEMBLE_DB_PATH", str(tmp_path / "old.db"))
    assert db.resolve_path() == str(tmp_path / "old.db")
    monkeypatch.setenv("AUTOMATIONS_DB_PATH", str(tmp_path / "new.db"))
    assert db.resolve_path() == str(tmp_path / "new.db")
    old_cfg, new_cfg = tmp_path / "old.json", tmp_path / "new.json"
    old_cfg.write_text(json.dumps({"defaults": {"marker": "old"}}), encoding="utf-8")
    new_cfg.write_text(json.dumps({"defaults": {"marker": "new"}}), encoding="utf-8")
    monkeypatch.setenv("ENSEMBLE_CONFIG", str(old_cfg))
    assert config_mod.load_config()["defaults"]["marker"] == "old"
    monkeypatch.setenv("AUTOMATIONS_CONFIG", str(new_cfg))
    assert config_mod.load_config()["defaults"]["marker"] == "new"


def test_writing_the_request_removes_the_old_names_file(managed_home, tmp_path):
    stale = sr.request_path("ensemble")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(json.dumps({"symbols": ["SPX"]}), encoding="utf-8")
    cfg = config_mod.load_config()
    arms = config_mod.load_arms(cfg)
    conn = db.connect(str(tmp_path / "ledger.db"))
    stream_request.write(cfg, arms, conn, str(tmp_path / "ledger.db"), cache_path=str(tmp_path / "cache.db"))
    assert not stale.exists()
    assert sr.request_path("automations").exists()


def test_the_block_is_named_automations_and_its_old_name_resolves():
    off = {"iron-fly-1000": {"enabled": False}}
    on = {"iron-fly-1000": {"enabled": True}}
    assert not config_mod.load_arms({"automations": off})["iron-fly-1000"]["enabled"]
    assert not config_mod.load_arms({"arms": off})["iron-fly-1000"]["enabled"]
    # A file holding both is read by the new name.
    assert config_mod.load_arms({"arms": off, "automations": on})["iron-fly-1000"]["enabled"]
    example_path = Path(__file__).resolve().parents[1] / "config.example.json"
    example = json.loads(example_path.read_text(encoding="utf-8"))
    assert "automations" in example and "arms" not in example
