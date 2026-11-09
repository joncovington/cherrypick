"""The command-line arm (2026-11-08): `--arm --confirm YES` for a person, `--install-task` for the
slash command, and the refusal both share while the module is switched off in the suite config.

Shown to fail: break `cherrypick.core.live.arm_from_cli` (skip the confirm test, or skip the
module-off test) and the matching tests below go red.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest
from cherrypick.core import home as _home
from cherrypick.core import live as _live

from cherrypick.flies import live_loop as ll


def _suite_config(enabled):
    path = _home.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"modules": {"flies": {"enabled": enabled}}}), encoding="utf-8")


@pytest.fixture
def supervised(managed_home, monkeypatch):
    """A live supervisor heartbeat, and no real first-tick spawn."""
    state = _home.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / "supervisor.last.json").write_text(
        json.dumps({"ts": datetime.now(UTC).isoformat(), "pid": os.getpid()}), encoding="utf-8"
    )
    monkeypatch.setattr(ll.subprocess, "Popen", lambda argv, **kw: None)


def _run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["live_loop", *argv])
    return ll.main()


def _armed():
    return _live.arm_record_path("flies").exists()


def test_arm_without_confirm_prints_the_disclaimer_and_writes_nothing(supervised, monkeypatch, capsys):
    _suite_config(True)
    assert _run(monkeypatch, "--arm") == 1
    out = capsys.readouterr().out
    assert _live.disclaimer_text().strip() in out
    assert "--confirm YES" in out and "Nothing was armed" in out
    assert not _armed()


@pytest.mark.parametrize("word", ["yes", "Y", "YES ", "NO", ""])
def test_arm_with_a_wrong_confirmation_writes_nothing(supervised, monkeypatch, capsys, word):
    _suite_config(True)
    assert _run(monkeypatch, "--arm", "--confirm", word) == 1
    assert "Nothing was armed" in capsys.readouterr().out
    assert not _armed()


def test_arm_refuses_while_the_module_is_off(supervised, monkeypatch, capsys):
    _suite_config(False)
    assert _run(monkeypatch, "--arm", "--confirm", "YES") == 1
    out = capsys.readouterr().out
    assert "flies is retired and switched off" in out and "packages/flies/README.md" in out
    assert not _armed()


def test_arm_refuses_with_no_suite_config_at_all(supervised, monkeypatch, capsys):
    assert _run(monkeypatch, "--arm", "--confirm", "YES") == 1
    assert not _armed()


def test_arm_with_yes_writes_todays_record_and_disarm_removes_it(supervised, monkeypatch, capsys):
    _suite_config(True)
    assert _run(monkeypatch, "--arm", "--confirm", "YES") == 0
    out = capsys.readouterr().out
    assert _live.disclaimer_text().strip() in out
    assert _armed()
    rec = json.loads(_live.arm_record_path("flies").read_text(encoding="utf-8"))
    assert rec["confirmation"] == "literal-YES" and rec["date"] == ll.arm_stamp_date()
    assert _run(monkeypatch, "--disarm") == 0
    assert not _armed()


def test_install_task_alias_refuses_while_the_module_is_off(supervised, monkeypatch, capsys):
    _suite_config(False)
    assert _run(monkeypatch, "--install-task") == 1
    assert "flies is retired and switched off" in capsys.readouterr().out
    assert not _armed()


def test_install_task_alias_does_not_prompt_when_the_module_is_on(supervised, monkeypatch, capsys):
    _suite_config(True)
    assert _run(monkeypatch, "--install-task") == 0
    out = capsys.readouterr().out
    assert _live.disclaimer_text().strip() not in out
    assert _armed()


def test_the_old_uninstall_spelling_still_disarms(supervised, monkeypatch):
    _suite_config(True)
    assert _run(monkeypatch, "--install-task") == 0
    assert _run(monkeypatch, "--uninstall-task") == 0
    assert not _armed()


def test_the_suite_switch_is_read_from_the_suite_config(managed_home):
    _suite_config(False)
    assert _live.suite_module_enabled("flies") is False
    _suite_config(True)
    assert _live.suite_module_enabled("flies") is True
