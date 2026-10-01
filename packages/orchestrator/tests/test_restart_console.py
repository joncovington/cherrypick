"""`cherrypick restart-console` — `restart console` under its old name.

It used to find a PID itself (the registry, else the port's listener) and kill the tree. Since
2026-10-01 the supervisor restarts the console (`orchestrator.proc`): this asks, waits and reports,
and kills nothing. The port is read only to REPORT a stray listener -- the 2026-08-13 case, a
registry PID and a different real listener -- whose reclaim is the supervisor's own stuck-port path.
"""

from __future__ import annotations

import json
import types

import pytest

from cherrypick import cli
from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import supervisor


def _state():
    return json.loads(cfgmod.state_file("restart_console.last.json").read_text(encoding="utf-8"))


@pytest.fixture
def killed(monkeypatch):
    """Records what cmd_restart_console tried to terminate, without touching a real process."""
    calls: list[int] = []
    monkeypatch.setattr(supervisor, "_terminate_tree", lambda pid: calls.append(pid) or True)
    return calls


def test_restart_console_asks_the_supervisor_and_never_kills_itself(killed, monkeypatch):
    """Since 2026-10-01 the supervisor stops and relaunches; this command only asks and reports."""
    asked = []
    monkeypatch.setattr(
        cli._proc, "restart_job", lambda name: asked.append(name) or {"ok": True, "old_pid": 1, "new_pid": 2}
    )
    monkeypatch.setattr(cli, "_console_port", lambda cfg: 5070)
    monkeypatch.setattr(cli, "_find_listening_pid", lambda port: None)

    cli.cmd_restart_console({})

    assert asked == ["console"] and killed == []
    rec = _state()
    assert rec["ok"] is True and rec["new_pid"] == 2


def test_a_stray_port_listener_is_reported_never_killed(killed, monkeypatch):
    """The 2026-08-13 case -- a different process on the port than the registry's. Reported, so `ps`
    and the duplicate check can judge it; reclaiming the port is the supervisor's, not a guess here."""
    monkeypatch.setattr(cli._proc, "restart_job", lambda name: {"ok": True, "old_pid": 1, "new_pid": 2})
    monkeypatch.setattr(cli, "_console_port", lambda cfg: 5070)
    monkeypatch.setattr(cli, "_find_listening_pid", lambda port: 12868)

    cli.cmd_restart_console({})

    assert killed == []
    assert _state()["port_listener"] == {"port": 5070, "pid": 12868}


def test_a_refused_restart_is_reported_and_exits_nonzero(killed, monkeypatch):
    monkeypatch.setattr(cli._proc, "restart_job", lambda name: {"ok": False, "error": "supervisor down"})
    monkeypatch.setattr(cli, "_console_port", lambda cfg: 5070)
    monkeypatch.setattr(cli, "_find_listening_pid", lambda port: None)
    with pytest.raises(SystemExit) as exit_:
        cli.cmd_restart_console({})
    assert exit_.value.code == 1 and _state()["ok"] is False and killed == []


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
    # The implementation is deliberately Windows-only and short-circuits to None on POSIX before
    # reaching the parser -- so without pinning the platform, this test proved the parser on
    # Windows and proved the short-circuit on CI's Linux runner, passing locally and failing there.
    # Pin the platform so the PARSER is what runs everywhere; the stubbed netstat never executes.
    monkeypatch.setattr(cli.os, "name", "nt")
    output = (
        "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       800\n"
        "  TCP    127.0.0.1:5070         0.0.0.0:0              LISTENING       12868\n"
        "  TCP    127.0.0.1:50700        0.0.0.0:0              LISTENING       999\n"
    )
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=output))

    # The trailing space in the match keeps ":5070" from matching the ":50700" row above it.
    assert cli._find_listening_pid(5070) == 12868


def test_find_listening_pid_returns_none_when_nothing_matches(monkeypatch):
    # Same platform pin, or on POSIX this asserts the short-circuit rather than the no-match branch.
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=""))
    assert cli._find_listening_pid(5070) is None


def test_find_listening_pid_is_a_noop_off_windows(monkeypatch):
    monkeypatch.setattr(cli.os, "name", "posix")
    assert cli._find_listening_pid(5070) is None
