"""The OPTIONAL Windows-service mode (`run.py service`, orchestrator/winservice.py). Off unless chosen;
nothing elevates on its own; when on, the anchor never starts a rival supervisor beside the service."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from cherrypick import cli
from cherrypick.orchestrator import doctor, winservice

ON = {"service": {"enabled": True, "winsw_exe": None}}


def _xml(**over):
    s = {**winservice.DEFAULTS, **over}
    return winservice.build_xml(
        s,
        python=r"C:\Py\python.exe",
        launcher=r"C:\repo\packages\orchestrator\run.py",
        workdir=r"C:\repo",
        path_env=r"C:\nvm;C:\Program Files\dolt\bin;C:\a&b",
        logdir=r"C:\home\logs\service",
    )


def test_the_definition_runs_the_supervisor_as_a_restartable_delayed_service():
    root = ET.fromstring(_xml())
    assert root.findtext("id") == "cherrypick-supervisor-svc"
    assert root.findtext("executable").endswith("python.exe")
    assert root.findtext("arguments").endswith('run.py" supervise')
    assert root.findtext("stoparguments").endswith("supervise --stop")
    assert root.findtext("startmode") == "Automatic" and root.findtext("delayedAutoStart") == "true"
    assert [f.get("delay") for f in root.findall("onfailure")] == ["30 sec", "60 sec", "120 sec"]
    # The user's PATH is carried (a service gets the system one): `&` survives as data, not markup.
    (env,) = root.findall("env")
    assert env.get("name") == "PATH" and env.get("value").endswith(r"C:\a&b")
    # No account or password is ever written into the file: `install /p` asks for them.
    assert root.find("serviceaccount") is None and "password" not in _xml().lower().replace("/p", "")


def test_the_definition_follows_the_config_block():
    root = ET.fromstring(
        _xml(id="cp-svc", delayed_start=False, restart_delays_seconds=[5], stop_timeout_seconds=9)
    )
    assert root.findtext("id") == "cp-svc" and root.findtext("delayedAutoStart") == "false"
    assert [f.get("delay") for f in root.findall("onfailure")] == ["5 sec"]
    assert root.findtext("stoptimeout") == "9 sec"


def test_sc_query_reads_installed_and_state():
    running = (
        "SERVICE_NAME: x\n        TYPE               : 10  WIN32_OWN_PROCESS\n"
        "        STATE              : 4  RUNNING\n"
    )
    assert winservice.parse_sc_query(running) == {"installed": True, "state": "RUNNING"}
    missing = "[SC] EnumQueryServicesStatus:OpenService FAILED 1060:\n\nThe specified service does not exist."
    assert winservice.parse_sc_query(missing) == {"installed": False, "state": None}


def test_prepare_refuses_until_chosen_and_until_winsw_is_there():
    off = winservice.prepare({}, launcher="r", workdir="w", anchor_task="a", platform="nt")
    assert not off["ok"] and "opt-in" in off["error"]
    missing = winservice.prepare(ON, launcher="r", workdir="w", anchor_task="a", platform="nt")
    assert not missing["ok"] and "WinSW" in missing["error"]


def test_the_elevated_steps_install_with_a_prompted_password_and_free_the_anchor():
    steps = winservice.elevated_steps(r"C:\h\service\svc.exe", "cherrypick-supervisor", "jonco")
    assert steps[0].endswith("install /p") and "supervise --stop" in steps[1] and steps[2].endswith(" start")
    assert "Set-ScheduledTask -TaskName 'cherrypick-supervisor'" in steps[3]
    assert r"Get-Credential '.\jonco'" in steps[3]  # the service manager refuses a bare account name


@pytest.mark.parametrize(
    "settings,state,expected",
    [
        ({"enabled": False, "id": "s"}, {"installed": False}, None),
        ({"enabled": False, "id": "s"}, {"installed": True}, doctor.WARN),
        ({"enabled": True, "id": "s"}, {"installed": False}, doctor.WARN),
        ({"enabled": True, "id": "s"}, {"installed": True, "state": "STOPPED"}, doctor.FAIL),
        ({"enabled": True, "id": "s"}, {"installed": True, "state": "RUNNING"}, doctor.OK),
    ],
)
def test_doctor_reports_service_mode_only_when_it_is_in_play(settings, state, expected):
    got = doctor.service_check(settings, state)
    assert (got.status if got else None) == expected


def test_in_service_mode_the_anchor_starts_the_service_never_a_rival(monkeypatch):
    spawned, started = [], []
    monkeypatch.setattr(cli, "_spawn_supervisor_detached", lambda: spawned.append(1) or True)
    monkeypatch.setattr(winservice, "start", lambda sid: started.append(sid) or True)
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(winservice, "query", lambda sid: {"installed": True, "state": "STOPPED"})
    assert cli._start_supervisor(ON) and started == ["cherrypick-supervisor-svc"] and spawned == []
    monkeypatch.setattr(winservice, "query", lambda sid: {"installed": False, "state": None})
    assert cli._start_supervisor(ON) and spawned == [1]  # chosen but not installed yet: the usual way
    assert cli._start_supervisor({}) and spawned == [1, 1]  # the default posture


def test_the_service_path_is_the_logon_path_never_the_shells():
    # 2026-10-08: prepare run from Git Bash carried Git's usr/bin ahead of System32.
    reg = {
        "machine": r"%SystemRoot%\system32;%SystemRoot%;C:\Program Files\nodejs"
        + "\\"
        + r";C:\Program Files\Dolt\bin",
        "user": r"C:\Users\u\.local\bin;C:\WINDOWS\system32;C:\Program Files\Dolt" + "\\\\" + "bin;",
    }

    def expand(v):
        return v.replace("%SystemRoot%", r"C:\WINDOWS")

    got = winservice.logon_path(read=lambda hive, key: reg[hive], expand=expand)
    assert got.split(";") == [
        r"C:\WINDOWS\system32",
        r"C:\WINDOWS",
        r"C:\Program Files\nodejs",
        r"C:\Program Files\Dolt\bin",
        r"C:\Users\u\.local\bin",
    ]
    assert winservice.logon_path(read=lambda hive, key: "", expand=expand) is None


def test_prepare_writes_the_definition_with_the_logon_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(winservice, "logon_path", lambda: r"C:\WINDOWS\system32;C:\Program Files\nodejs")
    monkeypatch.setenv("PATH", r"C:\Program Files\Git\usr\bin;C:\WINDOWS\system32")
    exe = tmp_path / "WinSW-x64.exe"
    exe.write_bytes(b"MZ")
    cfg = {"service": {"enabled": True, "winsw_exe": str(exe)}}
    out = winservice.prepare(
        cfg,
        launcher=r"C:\repo\run.py",
        workdir=r"C:\repo",
        anchor_task="cherrypick-supervisor",
        platform="nt",
    )
    assert out["ok"], out
    assert len(out["run_these_in_an_administrator_prompt"]) == 4
    root = ET.parse(out["xml"]).getroot()
    assert (
        root.find("env").get("value") == r"C:\WINDOWS\system32;C:\Program Files\nodejs"
    )  # never the shell's


def test_a_service_that_will_not_start_falls_back_to_the_usual_supervisor_and_says_so(monkeypatch):
    # 2026-10-08, the first real install: a logon failure left the suite with no supervisor at all.
    spawned, sent = [], []

    class Note:
        def notify(self, level, key, title, message, **kw):
            sent.append((level, key))
            return {"log": {"ok": True}}

    monkeypatch.setattr(cli, "_spawn_supervisor_detached", lambda: spawned.append(1) or True)
    monkeypatch.setattr(cli, "Notifier", lambda c: Note())
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(winservice, "query", lambda sid: {"installed": True, "state": "STOPPED"})
    monkeypatch.setattr(winservice, "start", lambda sid: False)
    assert cli._start_supervisor(ON) and spawned == [1]
    assert sent == [("CRITICAL", "service.start_failed")]
