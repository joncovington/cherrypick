"""The Dolt sql-server as a managed daemon (2026-10-09).

The supervisor's `ensure-dolt` job only ever started it, and under the Windows service nothing on the
desktop could stop it. It is now `dolt-server` in `proc.daemons`, driven by `run.py dolt-server-*`, so
status/start/stop/restart work like the streamer's and go through the supervisor under the service.
"""

from __future__ import annotations

import json

import pytest

from cherrypick import cli
from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import holds, proc

DOLT_MODULE = {"earnings": {"paper": {"dolt_service": {"data_dir": "~/dolt"}, "dolt_port": 3306}}}


@pytest.fixture
def with_dolt(monkeypatch):
    monkeypatch.setattr(cfgmod, "enabled_modules", lambda cfg: DOLT_MODULE)


def test_a_module_declaring_dolt_makes_it_a_managed_daemon(with_dolt):
    root, spec, producer = proc.daemons({})[proc.DOLT_DAEMON]
    assert spec["stop_argv"][-1] == "dolt-server-stop" and spec["status_argv"][-1] == "dolt-server-status"
    assert producer is False


def test_no_module_declaring_dolt_means_no_dolt_daemon(monkeypatch):
    monkeypatch.setattr(cfgmod, "enabled_modules", lambda cfg: {"flies": {"paper": {}}})
    assert proc.DOLT_DAEMON not in proc.daemons({})


def test_stop_refuses_an_unknown_owner_and_anything_not_dolt():
    never = lambda pid: pytest.fail("terminated")  # noqa: E731
    # no owner found: refused before any name lookup could be wrong about it
    assert cli._stop_dolt_pid(None, terminate=never, name_of=lambda pid: "dolt.exe")["ok"] is False
    assert cli._stop_dolt_pid(77, terminate=never, name_of=lambda pid: "postgres.exe")["ok"] is False
    assert cli._stop_dolt_pid(77, terminate=never, name_of=lambda pid: None)["ok"] is False


def test_stop_ends_a_dolt_process_and_says_when_it_could_not():
    ended = []
    rec = cli._stop_dolt_pid(
        5064, terminate=lambda pid: ended.append(pid) or True, name_of=lambda pid: "dolt.exe"
    )
    assert rec == {"ok": True, "stopped_pid": 5064} and ended == [5064]
    assert (
        cli._stop_dolt_pid(5064, terminate=lambda pid: False, name_of=lambda pid: "dolt.exe")["ok"] is False
    )


def _run(capsys, action):
    try:
        cli.cmd_dolt_server({}, action)
    except SystemExit:
        pass
    return json.loads(capsys.readouterr().out)


def test_status_start_and_stop_follow_the_port(with_dolt, monkeypatch, capsys):
    started = []
    monkeypatch.setattr(cli, "_start_dolt", lambda data_dir: started.append(data_dir) or True)
    monkeypatch.setattr(cli, "port_owner_pid", lambda port: 5064)
    monkeypatch.setattr(cli.watchdog, "_dolt_reachable", lambda host, port: False)
    assert _run(capsys, "status")["running"] is False
    assert _run(capsys, "start")["ok"] is True and len(started) == 1
    assert _run(capsys, "stop") == {"ok": True, "running": False, "detail": "not running"}
    monkeypatch.setattr(cli.watchdog, "_dolt_reachable", lambda host, port: True)
    up = _run(capsys, "status")
    assert up["running"] is True and up["pid"] == 5064
    assert _run(capsys, "start")["detail"] == "already running" and len(started) == 1


def test_the_keep_alive_does_not_undo_a_stop(with_dolt, monkeypatch, capsys):
    """`ensure-dolt` runs every five minutes; a held dolt-server (stopped on purpose, or mid-restart)
    must stay down rather than be started again behind the stop."""
    started = []
    monkeypatch.setattr(cli, "_start_dolt", lambda data_dir: started.append(data_dir) or True)
    monkeypatch.setattr(cli.watchdog, "_dolt_reachable", lambda host, port: False)
    holds.hold(proc.DOLT_DAEMON, by="test")
    cli._ensure_dolt({})
    assert started == [] and "held" in json.loads(capsys.readouterr().out)["dolt"]["earnings"]["detail"]
    holds.release(proc.DOLT_DAEMON)
    cli._ensure_dolt({})
    assert len(started) == 1


def test_dolt_sql_reaches_the_server_by_address_never_by_its_info_file():
    """A bare `dolt` reads `.dolt/sql-server.info` and, on Windows against a service-started server,
    deletes it (dolt 2.4 treats Access Denied as a dead PID). The wrapper names the server."""
    argv = cli.dolt_sql_argv({"host": "127.0.0.1", "port": 3306}, "options", "select 1")
    assert argv[0] == "dolt" and argv[-3:] == ["sql", "-q", "select 1"]
    globals_ = argv[1 : argv.index("sql")]
    for flag, value in (("--host", "127.0.0.1"), ("--port", "3306"), ("--use-db", "options")):
        assert globals_[globals_.index(flag) + 1] == value
    assert "--no-tls" in globals_
