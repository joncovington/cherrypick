"""The command-line arm (2026-11-08): `--arm --confirm YES` for a person, `--install-task` for the
slash command, and the refusal both share while the module is switched off in the suite config.

Shown to fail: break `cherrypick.core.live.arm_from_cli` (skip the confirm test, or skip the
module-off test) and the matching tests below go red.
"""

from __future__ import annotations

import json

import pytest
from cherrypick.core import home as _home
from cherrypick.core import live as _live

from cherrypick.bwb import live_loop


def _suite_config(enabled):
    path = _home.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"modules": {"bwb": {"enabled": enabled}}}), encoding="utf-8")


@pytest.fixture
def supervised(managed_home, monkeypatch):
    """A live supervisor, and no real first-tick spawn."""
    monkeypatch.setattr(_live, "supervisor_heartbeat_fresh", lambda max_age_seconds=90: True)
    monkeypatch.setattr(live_loop, "_spawn_first_tick", lambda: None)


def _armed():
    return _live.arm_record_path("bwb").exists()


def test_arm_without_confirm_prints_the_disclaimer_and_writes_nothing(supervised, capsys):
    _suite_config(True)
    assert live_loop.main(["--arm"]) == 1
    out = capsys.readouterr().out
    assert _live.disclaimer_text().strip() in out
    assert "--confirm YES" in out and "Nothing was armed" in out
    assert not _armed()


@pytest.mark.parametrize("word", ["yes", "Y", "YES ", "NO", ""])
def test_arm_with_a_wrong_confirmation_writes_nothing(supervised, capsys, word):
    _suite_config(True)
    assert live_loop.main(["--arm", "--confirm", word]) == 1
    assert "Nothing was armed" in capsys.readouterr().out
    assert not _armed()


def test_arm_refuses_while_the_module_is_off(supervised, capsys):
    _suite_config(False)
    assert live_loop.main(["--arm", "--confirm", "YES"]) == 1
    out = capsys.readouterr().out
    assert "bwb is retired and switched off" in out and "packages/bwb/README.md" in out
    assert not _armed()


def test_arm_refuses_with_no_suite_config_at_all(supervised, capsys):
    assert live_loop.main(["--arm", "--confirm", "YES"]) == 1
    assert not _armed()


def test_arm_with_yes_writes_todays_record_and_disarm_removes_it(supervised, capsys):
    _suite_config(True)
    assert live_loop.main(["--arm", "--confirm", "YES"]) == 0
    out = capsys.readouterr().out
    assert _live.disclaimer_text().strip() in out
    assert _armed()
    rec = json.loads(_live.arm_record_path("bwb").read_text(encoding="utf-8"))
    assert rec["confirmation"] == "literal-YES" and rec["date"] == live_loop.arm_stamp_date()
    assert live_loop.main(["--disarm"]) == 0
    assert not _armed()


def test_install_task_alias_refuses_while_the_module_is_off(supervised, capsys):
    _suite_config(False)
    assert live_loop.main(["--install-task"]) == 1
    assert "bwb is retired and switched off" in capsys.readouterr().out
    assert not _armed()


def test_install_task_alias_does_not_prompt_when_the_module_is_on(supervised, capsys):
    _suite_config(True)
    assert live_loop.main(["--install-task"]) == 0
    out = capsys.readouterr().out
    assert _live.disclaimer_text().strip() not in out
    assert _armed()


def test_the_old_uninstall_spelling_still_disarms(supervised):
    _suite_config(True)
    assert live_loop.main(["--install-task"]) == 0
    assert live_loop.main(["--uninstall-task"]) == 0
    assert not _armed()


def test_status_carries_the_suite_switch(supervised, capsys):
    _suite_config(False)
    assert live_loop.main(["--status"]) == 0
    assert json.loads(capsys.readouterr().out)["module_enabled"] is False
    _suite_config(True)
    assert live_loop.main(["--status"]) == 0
    assert json.loads(capsys.readouterr().out)["module_enabled"] is True
