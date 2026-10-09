"""Daemon stop/start/restart handed to the supervisor (2026-10-09).

Under the Windows service the daemons run in session 0 and the desktop cannot stop them:
`run.py restart gex-recorder` failed with Access is denied, and the failed restart left the recorder
held. These pin the hand-off, its allow-list, and the hold that a failed restart must not leave.
"""

from __future__ import annotations

import json

from cherrypick import cli
from cherrypick.orchestrator import daemonreq, holds, proc, supervisor


def test_a_queued_request_is_taken_once_and_only_for_a_known_daemon():
    good = daemonreq.request("restart", "gex-recorder")
    bad_name = daemonreq.request("restart", "cmd.exe")
    bad_verb = daemonreq.request("rm", "gex-recorder")
    taken = daemonreq.take({"gex-recorder", "streamer"})
    assert taken == [{"id": good, "verb": "restart", "name": "gex-recorder"}]
    assert daemonreq.take({"gex-recorder"}) == []  # removed when taken
    for refused in (bad_name, bad_verb):
        rec = json.loads(daemonreq.result_path(refused).read_text(encoding="utf-8"))
        assert rec["ok"] is False and rec["error"].startswith("refused")


def test_the_caller_gets_the_filed_result_and_the_file_is_cleaned_up():
    req = daemonreq.request("restart", "gex-recorder")
    daemonreq.result_path(req).parent.mkdir(parents=True, exist_ok=True)
    daemonreq.result_path(req).write_text(json.dumps({"ok": True, "new_pid": 42}), encoding="utf-8")
    assert daemonreq.await_result(req, timeout=1, sleep=lambda s: None) == {"ok": True, "new_pid": 42}
    assert not daemonreq.result_path(req).exists()


def test_an_unclaimed_request_is_withdrawn_at_the_deadline():
    req = daemonreq.request("stop", "gex-recorder")
    rec = daemonreq.await_result(req, timeout=0, sleep=lambda s: None)
    assert rec["ok"] is False and "withdrawn" in rec["error"]
    assert daemonreq.take({"gex-recorder"}) == []  # a later supervisor does not act on it


def test_the_supervisor_runs_a_request_as_a_direct_child_and_refuses_the_rest(monkeypatch):
    spawned = []
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)))
    monkeypatch.setattr(proc, "daemons", lambda cfg: {"gex-recorder": (None, {}, False)})
    good = daemonreq.request("restart", "gex-recorder")
    daemonreq.request("restart", "notepad")
    supervisor.Supervisor({})._serve_daemon_requests({})
    ((argv, kw),) = spawned
    assert argv[2:6] == ["restart", "gex-recorder", "--direct", "--result"]
    assert argv[6] == str(daemonreq.result_path(good))
    assert kw["creationflags"] == supervisor.CREATE_NO_WINDOW


def test_every_supervisor_pass_serves_the_queue(monkeypatch):
    served = []
    monkeypatch.setattr(supervisor.jobspec, "derive_jobs", lambda cfg, **kw: ({}, {}))
    monkeypatch.setattr(supervisor.Supervisor, "_serve_daemon_requests", lambda self, cfg: served.append(cfg))
    supervisor.Supervisor({"timezone": "America/New_York"}).pass_once()
    assert len(served) == 1


def test_the_cli_hands_a_daemon_action_to_a_live_service_supervisor(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_via_supervisor", lambda cfg: True)
    monkeypatch.setattr(daemonreq, "request", lambda verb, name: calls.append((verb, name)) or "r1")
    monkeypatch.setattr(daemonreq, "await_result", lambda req_id: {"ok": True, "id": req_id})
    monkeypatch.setattr(proc, "restart_daemon", lambda *a, **k: calls.append("direct") or {"ok": True})
    assert cli._daemon_action({}, "restart", "gex-recorder", direct=False) == {
        "ok": True,
        "id": "r1",
        "via": "supervisor",
    }
    assert calls == [("restart", "gex-recorder")]
    cli._daemon_action({}, "restart", "gex-recorder", direct=True)  # the supervisor's own child
    assert calls[-1] == "direct"


def test_no_service_means_the_cli_acts_directly(monkeypatch):
    monkeypatch.setattr(cli, "_via_supervisor", lambda cfg: False)
    monkeypatch.setattr(daemonreq, "request", lambda *a: (_ for _ in ()).throw(AssertionError("queued")))
    monkeypatch.setattr(proc, "stop_daemon", lambda cfg, name: {"ok": True, "name": name})
    assert cli._daemon_action({}, "stop", "streamer", direct=False) == {"ok": True, "name": "streamer"}


def test_a_restart_that_cannot_stop_the_daemon_does_not_leave_it_held(monkeypatch):
    monkeypatch.setattr(
        proc, "daemons", lambda cfg: {"gex-recorder": (None, {"status_argv": [], "stop_argv": []}, False)}
    )
    monkeypatch.setattr(proc, "daemon_status", lambda root, spec: {"running": True, "pid": 7})
    monkeypatch.setattr(proc, "_run", lambda root, argv, timeout=30: {"code": 1, "error": "Access is denied"})
    monkeypatch.setattr(proc, "STOP_TIMEOUT_S", 0.05)
    monkeypatch.setattr(proc, "POLL_S", 0.01)
    rec = proc.restart_daemon({}, "gex-recorder", ensure=lambda *a, **k: {})
    assert rec["ok"] is False and rec["held"] is False
    assert holds.is_held("gex-recorder") is None
