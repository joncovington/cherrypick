"""The anchor task's probe (`cherrypick ensure-supervisor`) — the alerting floor of last resort.

A crash-looping supervisor takes the watchdog (and thus all alerting) down with it, so the probe
must: never double-start a live daemon, restart a dead one, and escalate ONE CRITICAL after three
consecutive failed probes — then stay quiet until a probe succeeds again.
"""

from __future__ import annotations

import json

import pytest

from cherrypick import cli
from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import supersnap


@pytest.fixture
def probe(monkeypatch, fake_notifier):
    """Wire cmd_ensure_supervisor with a controllable liveness answer and a recorded spawn."""
    calls = {"spawns": 0, "alive": False}
    monkeypatch.setattr(supersnap, "supervisor_alive", lambda hb=None: calls["alive"])

    def fake_spawn():
        calls["spawns"] += 1
        return True

    monkeypatch.setattr(cli, "_spawn_supervisor_detached", fake_spawn)
    monkeypatch.setattr(cli, "Notifier", lambda notify_cfg: fake_notifier)
    calls["notifier"] = fake_notifier
    return calls


def _state():
    return json.loads(cfgmod.state_file("ensure_supervisor.json").read_text(encoding="utf-8"))


def test_live_supervisor_is_a_noop(probe):
    probe["alive"] = True
    cli.cmd_ensure_supervisor({})
    assert probe["spawns"] == 0
    assert probe["notifier"].sent == []


def test_dead_supervisor_is_restarted(probe):
    cli.cmd_ensure_supervisor({})
    assert probe["spawns"] == 1
    assert _state()["failures"] == 1
    assert probe["notifier"].sent == []  # one failure is not an incident yet


def test_third_consecutive_failure_escalates_once(probe):
    for _ in range(4):
        cli.cmd_ensure_supervisor({})
    assert probe["spawns"] == 4
    sent = probe["notifier"].sent
    assert len(sent) == 1  # exactly one CRITICAL, not one per probe
    assert sent[0]["level"] == "CRITICAL" and sent[0]["key"] == "supervisor.down"


def test_success_resets_the_streak_and_the_notice(probe):
    for _ in range(3):
        cli.cmd_ensure_supervisor({})
    assert len(probe["notifier"].sent) == 1
    probe["alive"] = True
    cli.cmd_ensure_supervisor({})
    assert _state() == {"failures": 0, "notified": False}
    probe["alive"] = False
    for _ in range(3):
        cli.cmd_ensure_supervisor({})
    assert len(probe["notifier"].sent) == 2  # a NEW streak earns a new escalation


def test_a_wedged_supervisor_is_ended_alone_then_replaced_and_announced(probe, monkeypatch):
    # 2026-10-08 audit: alive but not beating, it held the lock and every restart refused.
    from cherrypick.orchestrator import supervisor

    ended = []
    monkeypatch.setattr(supersnap, "wedged_supervisor_pid", lambda hb, **kw: 4242)
    monkeypatch.setattr(supervisor, "_terminate_pid", lambda pid: ended.append(pid) or True)
    monkeypatch.setattr("cherrypick.core.looplock.pid_alive", lambda pid: False)
    cli.cmd_ensure_supervisor({})
    assert ended == [4242] and probe["spawns"] == 1
    assert any("hung" in str(n) for n in probe["notifier"].sent)


def test_a_supervisor_that_is_merely_down_is_never_terminated(probe, monkeypatch):
    from cherrypick.orchestrator import supervisor

    ended = []
    monkeypatch.setattr(supersnap, "wedged_supervisor_pid", lambda hb, **kw: None)
    monkeypatch.setattr(supervisor, "_terminate_pid", lambda pid: ended.append(pid) or True)
    cli.cmd_ensure_supervisor({})
    assert ended == [] and probe["spawns"] == 1
