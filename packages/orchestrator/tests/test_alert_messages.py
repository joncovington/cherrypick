"""What reaches Discord is short and plain (owner's request, 2026-10-09): the wording found unclear in
that day's alerts, the start and crash announcements, and no home-folder paths in any push."""

from __future__ import annotations

import json
import os

from cherrypick.notify import notifier
from cherrypick.orchestrator import reconcile, supervisor
from cherrypick.orchestrator import watchdog as wd


def test_a_push_never_carries_the_home_folder():
    home = os.path.expanduser("~")
    sep = chr(92)  # a backslash
    msg = "; ".join(
        [
            home + sep + ".cherrypick" + sep + "x.pid",  # a Windows path
            home.replace(sep, "/") + "/y",  # forward slashes
            home.replace(sep, sep * 2) + sep * 2 + "z",  # a repr()'d path
        ]
    )
    out = notifier._portable(msg)
    assert home not in out and "~" in out


def test_long_job_lists_are_cut_to_a_readable_head():
    names = [f"job-{i}" for i in range(25)]
    assert wd._first_few(names) == ", ".join(names[:8]) + " and 17 more"
    assert wd._first_few(["a", "b"]) == "a, b"


def test_reconcile_pushes_one_plain_line_not_the_report():
    timeout = {
        "broker": {"reachable": False, "detail": "TimeoutExpired: Command ['C:\\Py\\pythonw.exe'] timed out"}
    }
    msg = reconcile.summary(timeout)
    assert "did not answer within 35 s" in msg and "pythonw" not in msg and len(msg) < 160
    drift = {"broker": {"reachable": True, "accounts": [{"account": "****1234", "open_positions": [1, 2]}]}}
    assert reconcile.summary(drift) == "****1234: 2 open position(s) in a paper-only account"


def test_the_start_announcement_says_what_matters():
    info = {
        "service": True,
        "uptime_s": 140,
        "previous": "crashed (PermissionError)",
        "code": "c8516825",
        "jobs": 74,
        "dns_ok": True,
        "halt": True,
        "armed": [],
    }
    level, title, message = supervisor.startup_message(info)
    assert title == "Supervisor started after a reboot" and level == "WARNING"  # the halt is worth seeing
    assert "as a Windows service" in message and "2 min after boot" in message
    assert "previous one crashed (PermissionError)" in message and "74 jobs scheduled" in message
    assert "HALT SET" in message
    quiet = supervisor.startup_message({**info, "uptime_s": 86400, "halt": False, "previous": None})
    assert quiet[0] == "INFO" and quiet[1] == "Supervisor started" and "nothing armed today" in quiet[2]
    offline = supervisor.startup_message({**info, "halt": False, "dns_ok": False})
    assert offline[0] == "WARNING" and "NO NETWORK" in offline[2]


def test_a_crash_is_announced_once_per_quarter_hour(monkeypatch, tmp_path):
    sent = []

    class Note:
        def __init__(self, cfg):
            pass

        def notify(self, level, key, title, message, **kw):
            sent.append((level, key, message))

    import cherrypick.notify as notify_pkg

    monkeypatch.setattr(notify_pkg, "Notifier", Note)
    monkeypatch.setattr(supervisor.cfgmod, "state_file", lambda name: tmp_path / name)
    supervisor._announce_crash(PermissionError(5, "Access is denied"), {})
    supervisor._announce_crash(PermissionError(5, "Access is denied"), {})
    assert len(sent) == 1 and sent[0][:2] == ("CRITICAL", "supervisor.crashed")
    assert "No jobs start until it is back" in sent[0][2]
    marker = json.loads((tmp_path / "supervisor.crash_notified.json").read_text(encoding="utf-8"))
    assert marker["at_epoch"] > 0


def test_a_requested_restart_is_remembered_for_the_next_start(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.cfgmod, "state_file", lambda name: tmp_path / name)
    supervisor._note_exit("was restarted on request")
    assert supervisor._take_last_exit() == "was restarted on request"
    assert supervisor._take_last_exit() is None  # read once


def test_notify_itself_redacts_before_anything_is_written_or_pushed(monkeypatch):
    home = os.path.expanduser("~")
    logged = []
    monkeypatch.setattr(
        notifier.Notifier, "_write_log", lambda self, lvl, key, title, msg: logged.append(msg)
    )
    notifier.Notifier({"channels": ["log"]}).notify("WARNING", "backup", "t", f"{home}/x.pid missing")
    assert logged == ["~/x.pid missing"]
