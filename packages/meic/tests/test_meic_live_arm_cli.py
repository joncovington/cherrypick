"""The command-line arm (2026-11-08): `--arm --confirm YES` for a person, `--install-task` for the
slash command. MEIC is live, so unlike flies and bwb it has no module-off refusal; the confirmation
and the disclaimer are the same.

Shown to fail: break `cherrypick.core.live.arm_from_cli` (skip the confirm test) and the tests
below go red.
"""

from __future__ import annotations

import json

import pytest
from cherrypick.core import live as _live
from test_live_arming import _config, _empty_live_ledger

from cherrypick.meic import live_loop, paper


@pytest.fixture
def armable(monkeypatch):
    """A ready ledger, supervisor and account: only the confirmation decides."""
    cfg = _config()
    monkeypatch.setattr(paper, "load_base_config", lambda: cfg)
    _empty_live_ledger()
    monkeypatch.setattr(live_loop, "_spawn_first_tick", lambda: None)
    monkeypatch.setattr(live_loop, "_designated_account", lambda: "1234")
    monkeypatch.setattr(live_loop, "readiness", lambda *a, **k: [])
    monkeypatch.setattr(live_loop, "kill_verdict", lambda *a, **k: {"state": "normal", "reasons": []})
    monkeypatch.setattr(_live, "supervisor_heartbeat_fresh", lambda *a, **k: True)
    yield
    live_loop.uninstall_task()


def test_arm_without_confirm_prints_the_disclaimer_and_writes_nothing(armable, capsys):
    assert live_loop.main(["--arm"]) == 1
    out = capsys.readouterr().out
    assert _live.disclaimer_text().strip() in out
    assert "--confirm YES" in out and "Nothing was armed" in out
    assert live_loop.arm_stamp_date() is None


@pytest.mark.parametrize("word", ["yes", "Y", "YES ", "NO", ""])
def test_arm_with_a_wrong_confirmation_writes_nothing(armable, capsys, word):
    assert live_loop.main(["--arm", "--confirm", word]) == 1
    assert "Nothing was armed" in capsys.readouterr().out
    assert live_loop.arm_stamp_date() is None


def test_arm_with_yes_writes_todays_record_and_disarm_removes_it(armable, capsys):
    assert live_loop.main(["--arm", "--confirm", "YES"]) == 0
    out = capsys.readouterr().out
    assert _live.disclaimer_text().strip() in out
    today = live_loop._pl._now_et().strftime("%Y-%m-%d")
    assert live_loop.arm_stamp_date() == today
    record = json.loads(_live.arm_record_path("meic").read_text(encoding="utf-8"))
    assert record["confirmation"] == "literal-YES"
    assert live_loop.main(["--disarm"]) == 0
    assert live_loop.arm_stamp_date() is None


def test_install_task_alias_arms_without_prompting(armable, capsys):
    assert live_loop.main(["--install-task"]) == 0
    assert _live.disclaimer_text().strip() not in capsys.readouterr().out
    assert live_loop.arm_stamp_date() is not None
    assert live_loop.main(["--uninstall-task"]) == 0
    assert live_loop.arm_stamp_date() is None


def test_a_blocked_arm_still_refuses_after_a_correct_confirmation(armable, monkeypatch, capsys):
    monkeypatch.setattr(_live, "supervisor_heartbeat_fresh", lambda *a, **k: False)
    assert live_loop.main(["--arm", "--confirm", "YES"]) == 1
    assert live_loop.arm_stamp_date() is None
