"""The config-history job, against real git in a temporary home.

What it must never do: commit data, state or logs (the allow-list decides, not the job), fail the
supervisor (a missing repository is "not set up", a failed push is reported and retried), or hang on
a credential prompt.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import config_backup as cb
from cherrypick.orchestrator import jobspec

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "config").mkdir(parents=True)
    (h / "data").mkdir()
    (h / "config.json").write_text('{\n  "modules": {}\n}\n', encoding="utf-8")
    (h / "config" / "meic.json").write_text("{}\n", encoding="utf-8")
    (h / "data" / "ledger.db").write_text("not config", encoding="utf-8")
    monkeypatch.setattr(cb._home, "home", lambda: h)
    monkeypatch.setattr(cfgmod, "state_file", lambda name: tmp_path / "state" / name)
    return h


def _tracked(root: Path) -> set[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True).stdout
    return set(out.split())


def _commits(root: Path) -> int:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--count", "HEAD"], capture_output=True, text=True
    )
    return int(out.stdout.strip() or 0)


def test_settings_are_off_unless_literally_switched_on():
    assert cfgmod.config_backup_settings({}) == {"enabled": False, "interval_minutes": 15, "push": True}
    assert cfgmod.config_backup_settings({"config_backup": {"enabled": "true"}})["enabled"] is False
    assert cfgmod.config_backup_settings({"config_backup": {"enabled": True, "interval_minutes": 0}}) == {
        "enabled": True,
        "interval_minutes": 1,
        "push": True,
    }


def test_a_home_that_is_not_a_repository_is_not_set_up_not_a_failure(home):
    result = cb.run()
    assert result["ok"] is True and "not set up" in result["skipped"]


def test_init_tracks_config_only(home):
    result = cb.init()
    assert result["ok"] and result["created"]
    assert _tracked(home) == {".gitignore", "README.md", "config.json", "config/meic.json"}
    assert "data/ledger.db" not in _tracked(home)


def test_a_pass_commits_a_change_and_nothing_when_nothing_changed(home):
    cb.init()
    before = _commits(home)
    assert cb.run()["commit"] is None and _commits(home) == before
    (home / "config" / "meic.json").write_text('{"quantity": 2}\n', encoding="utf-8")
    (home / "data" / "ledger.db").write_text("changed data", encoding="utf-8")
    result = cb.run()
    assert result["ok"] and result["commit"] and result["changed"] == ["config/meic.json"]
    assert _commits(home) == before + 1


def test_it_pushes_to_a_remote_and_a_failed_push_is_retried(home, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    assert cb.init(remote=str(remote))["first_pass"]["pushed"] is True

    # Break the remote: the commit still lands locally, the pass reports the push error.
    subprocess.run(["git", "-C", str(home), "remote", "set-url", "origin", str(tmp_path / "missing.git")])
    (home / "config.json").write_text('{\n  "modules": {"meic": {}}\n}\n', encoding="utf-8")
    failed = cb.run()
    assert failed["ok"] is False and failed["commit"] and failed["pushed"] is False and failed["push_error"]
    assert cb.last_state()["ok"] is False

    # Fix it: the next pass pushes the commit it could not push before, with nothing new to commit.
    subprocess.run(["git", "-C", str(home), "remote", "set-url", "origin", str(remote)])
    retried = cb.run()
    assert retried["ok"] and retried["commit"] is None and retried["pushed"] is True
    head = subprocess.run(
        ["git", "-C", str(home), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout
    remote_head = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "main"], capture_output=True, text=True
    ).stdout
    assert head == remote_head


def test_init_leaves_an_existing_repository_alone(home):
    cb.init()
    (home / ".gitignore").write_text("# mine\n*\n!/config.json\n", encoding="utf-8")
    again = cb.init()
    assert again["created"] is False
    assert (home / ".gitignore").read_text(encoding="utf-8").startswith("# mine")


def test_the_switch_is_written_into_the_suite_config(tmp_path, monkeypatch):
    monkeypatch.setattr("cherrypick.orchestrator.configedit.backup_dir", lambda: tmp_path / "backups")
    path = tmp_path / "config.json"
    path.write_text('{\n  "_note": "kept",\n  "modules": {}\n}\n', encoding="utf-8")
    cb.set_enabled(True, path=path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["config_backup"]["enabled"] is True and doc["_note"] == "kept"
    cb.set_enabled(False, path=path)
    assert json.loads(path.read_text(encoding="utf-8"))["config_backup"]["enabled"] is False


def test_the_scheduler_runs_it_only_when_switched_on(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path)
    now = datetime(2026, 9, 30, 12, 0, tzinfo=ZoneInfo("America/New_York"))

    def job(cfg):
        jobs, errors = jobspec.derive_jobs(cfg, pythonw="pythonw", launcher="run.py", now=now)
        assert not errors
        return {j.id: j for j in jobs}["config-backup"]

    off = job({"modules": {}})
    assert off.enabled is False and "config_backup.enabled" in off.enabled_reason
    on = job({"modules": {}, "config_backup": {"enabled": True, "interval_minutes": 5}})
    assert on.enabled is True and on.interval_seconds == 300 and on.argv[-1] == "config-backup"


def test_the_shipped_template_ships_it_off():
    template = json.loads(
        (Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8")
    )
    assert cfgmod.config_backup_settings(template)["enabled"] is False
