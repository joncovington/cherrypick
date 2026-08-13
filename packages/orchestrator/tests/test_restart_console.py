"""`cherrypick restart-console` — the dev convenience for killing the console's process tree so the
supervisor replaces it on its next tick.

The one fact everything here protects: the supervisor's registry can lose track of the console's
real listener (observed live 2026-08-13 — the registry held one PID, a different process was
actually bound to the port), so killing only the registry's PID is not enough. The command must fall
back to whoever is genuinely listening, and it must kill the TREE either way, never just the tracked
PID, or a restart turns into a permanent EADDRINUSE outage.
"""

from __future__ import annotations

import json
import types

import pytest

from cherrypick import cli
from cherrypick.orchestrator import config as cfgmod


def _state():
    return json.loads(cfgmod.state_file("restart_console.last.json").read_text(encoding="utf-8"))


@pytest.fixture
def killed(monkeypatch):
    """Records what cmd_restart_console tried to terminate, without touching a real process."""
    calls: list[int] = []
    monkeypatch.setattr(cli.supervisor, "_terminate_tree", lambda pid: calls.append(pid) or True)
    return calls


def test_kills_the_registrys_tracked_pid_when_it_is_alive(killed, monkeypatch):
    jobs = {"jobs": {"console": {"running_pid": 111}}}
    monkeypatch.setattr(cli, "read_json", lambda path, default=None: jobs)
    monkeypatch.setattr(cli, "pid_alive", lambda pid: pid == 111)

    def _no_scan(port):
        raise AssertionError("should not scan the port when the registry pid is alive")

    monkeypatch.setattr(cli, "_find_listening_pid", _no_scan)

    cli.cmd_restart_console({})

    assert killed == [111]
    rec = _state()
    assert rec["ok"] is True and rec["killed_pid"] == 111 and rec["found_via"] == "supervisor registry"


def test_falls_back_to_a_port_scan_when_the_registry_pid_is_stale(killed, monkeypatch):
    """The exact scenario this command exists for: the registry's PID is not the real listener."""
    jobs = {"jobs": {"console": {"running_pid": 26964}}}
    monkeypatch.setattr(cli, "read_json", lambda path, default=None: jobs)
    monkeypatch.setattr(cli, "pid_alive", lambda pid: False)  # the registry's process is gone
    monkeypatch.setattr(cli, "_console_port", lambda cfg: 5070)
    monkeypatch.setattr(cli, "_find_listening_pid", lambda port: 12868 if port == 5070 else None)

    cli.cmd_restart_console({})

    assert killed == [12868]
    rec = _state()
    assert rec["killed_pid"] == 12868 and rec["found_via"] == "port 5070"


def test_falls_back_when_the_registry_has_no_pid_at_all(killed, monkeypatch):
    """running_pid is simply absent -- not stale, never recorded (e.g. right after a supervisor
    restart wiped the registry)."""
    monkeypatch.setattr(cli, "read_json", lambda path, default=None: {"jobs": {"console": {}}})
    monkeypatch.setattr(cli, "pid_alive", lambda pid: False)
    monkeypatch.setattr(cli, "_console_port", lambda cfg: 5070)
    monkeypatch.setattr(cli, "_find_listening_pid", lambda port: 99)

    cli.cmd_restart_console({})

    assert killed == [99]


def test_reports_nothing_to_kill_rather_than_guessing(killed, monkeypatch):
    monkeypatch.setattr(cli, "read_json", lambda path, default=None: {"jobs": {}})
    monkeypatch.setattr(cli, "pid_alive", lambda pid: False)
    monkeypatch.setattr(cli, "_console_port", lambda cfg: 5070)
    monkeypatch.setattr(cli, "_find_listening_pid", lambda port: None)

    cli.cmd_restart_console({})

    assert killed == []
    rec = _state()
    assert rec["ok"] is True and "skipped" in rec


def test_a_failed_terminate_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(cli.supervisor, "_terminate_tree", lambda pid: False)
    jobs = {"jobs": {"console": {"running_pid": 5}}}
    monkeypatch.setattr(cli, "read_json", lambda path, default=None: jobs)
    monkeypatch.setattr(cli, "pid_alive", lambda pid: True)

    cli.cmd_restart_console({})

    rec = _state()
    assert rec["ok"] is False and rec["killed_pid"] == 5 and rec["error"]


# --------------------------------------------------------------------------- _console_port


def test_console_port_reads_serve_port_from_config(tmp_path, monkeypatch):
    from cherrypick.core import home as corehome

    cfg_path = tmp_path / "console.json"
    cfg_path.write_text(json.dumps({"serve": {"port": 6001}}), encoding="utf-8")
    monkeypatch.setattr(corehome, "config_path", lambda pkg=None: cfg_path)

    assert cli._console_port({}) == 6001


def test_console_port_defaults_when_config_is_missing_or_bad(tmp_path, monkeypatch):
    """Mirrors packages/console/shared/src/paths.ts's own contract: unreadable, absent, or
    malformed all mean 'use the default', never a crash."""
    from cherrypick.core import home as corehome

    monkeypatch.setattr(corehome, "config_path", lambda pkg=None: tmp_path / "does-not-exist.json")
    assert cli._console_port({}) == 5070

    bad = tmp_path / "console.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(corehome, "config_path", lambda pkg=None: bad)
    assert cli._console_port({}) == 5070


def test_console_port_ignores_an_out_of_range_value(tmp_path, monkeypatch):
    from cherrypick.core import home as corehome

    cfg_path = tmp_path / "console.json"
    cfg_path.write_text(json.dumps({"serve": {"port": 99999}}), encoding="utf-8")
    monkeypatch.setattr(corehome, "config_path", lambda pkg=None: cfg_path)

    assert cli._console_port({}) == 5070


# --------------------------------------------------------------------------- _find_listening_pid


def test_find_listening_pid_parses_netstat_output(monkeypatch):
    output = (
        "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       800\n"
        "  TCP    127.0.0.1:5070         0.0.0.0:0              LISTENING       12868\n"
        "  TCP    127.0.0.1:50700        0.0.0.0:0              LISTENING       999\n"
    )
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=output))

    # The trailing space in the match keeps ":5070" from matching the ":50700" row above it.
    assert cli._find_listening_pid(5070) == 12868


def test_find_listening_pid_returns_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=""))
    assert cli._find_listening_pid(5070) is None


def test_find_listening_pid_is_a_noop_off_windows(monkeypatch):
    monkeypatch.setattr(cli.os, "name", "posix")
    assert cli._find_listening_pid(5070) is None
