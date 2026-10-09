"""`run.py service` -- OPTIONAL: run the supervisor as a Windows service, with nobody logged on.

Off unless chosen (`service.enabled`, default false), and nothing here elevates on its own. The
default posture is unchanged: auto-logon plus the 2-minute anchor task, which only runs while the
user is logged on (`/IT`). This is the path for a machine meant to sit at its login screen.

How it fits together (2026-10-08 design):

- **WinSW** (v2.x, MIT, github.com/winsw/winsw) wraps `python.exe run.py supervise` as the service
  `service.id`. It starts at boot (delayed, so the network is up -- the supervisor's DNS gate covers
  the rest), restarts the supervisor when it EXITS, and stops it through the supervisor's own stop
  file (`supervise --stop`). Only the supervisor is the service; every job stays its child.
- It runs as **the user**, so Credential Manager (the broker login, the webhooks) stays readable. The
  password is asked for by WinSW's `install /p` and stored by Windows; it never sits in the XML.
- A service gets the SYSTEM PATH, not the user's: `node` (the console), `dolt` and `claude` would go
  missing. The XML carries the PATH Windows builds at logon (machine, then user, from the registry) --
  never the PATH of the shell `prepare` happened to run in (`logon_path`).
- The **anchor** stays: WinSW restarts a supervisor that exits, not one that hangs, and the hung check
  lives in `ensure-supervisor` (#135). With nobody logged on the anchor only runs if it is set to run
  whether the user is logged on or not -- one of the printed elevated steps. In service mode the
  anchor never spawns a supervisor of its own: it ends a hung one (the service restarts it) and
  asks the service to start (`sc start`) when it is stopped.

`prepare` writes the files and PRINTS the elevated commands; a person runs them in an administrator
prompt. `status` reads `sc query`. `uninstall` prints the commands that undo it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from cherrypick.core import home as _home

from .util import CREATE_NO_WINDOW

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "id": "cherrypick-supervisor-svc",
    # WinSW-x64.exe as downloaded; copied beside the XML under the service id (WinSW reads the XML
    # named like its own executable).
    "winsw_exe": None,
    "delayed_start": True,
    "restart_delays_seconds": [30, 60, 120],
    "reset_failure_hours": 1,
    "stop_timeout_seconds": 30,
}


def settings(cfg: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULTS, **(cfg.get("service") or {})}


def service_dir() -> Path:
    return _home.home() / "service"


def console_python(exe: str | None = None) -> str:
    """`python.exe` beside the running interpreter (never `pythonw`, whose stdio a service and its
    stop command would lose)."""
    p = Path(exe or sys.executable)
    candidate = p.with_name("python.exe" if p.suffix.lower() == ".exe" else "python")
    return str(candidate) if candidate.exists() else str(p)


def logon_path(read=None, expand=os.path.expandvars) -> str | None:
    """The PATH Windows gives the user at logon: the machine PATH, then the user's, from the registry,
    variables expanded, duplicates dropped. NOT the PATH of whatever shell ran `prepare`: run from Git
    Bash, that one put Git's Unix tools ahead of System32, so `find`, `sort` and `timeout` inside the
    service would have been the wrong programs (caught on the first real prepare, 2026-10-08). None
    when the registry cannot be read. `read(hive, key)` is injected for tests."""
    if read is None:
        if os.name != "nt":
            return None
        import winreg

        def read(hive, key):
            root = winreg.HKEY_LOCAL_MACHINE if hive == "machine" else winreg.HKEY_CURRENT_USER
            try:
                with winreg.OpenKey(root, key) as k:
                    return str(winreg.QueryValueEx(k, "Path")[0])
            except OSError:
                return ""

    machine = read("machine", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    user = read("user", "Environment")
    if not machine and not user:
        return None
    import ntpath  # Windows path rules on any host, so the tests read the same on Linux CI

    seen, out = set(), []
    for part in f"{machine};{user}".split(";"):
        part = expand(part.strip())
        if not part:
            continue
        part = ntpath.normpath(part)  # a doubled or trailing separator is still the same directory
        if part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return ";".join(out)


def build_xml(
    s: dict[str, Any], *, python: str, launcher: str, workdir: str, path_env: str, logdir: str
) -> str:
    """The WinSW v2 service definition. Pure, so its every line is tested."""
    e = lambda v: escape(str(v), {'"': "&quot;"})  # noqa: E731
    failures = "\n".join(
        f'  <onfailure action="restart" delay="{int(d)} sec"/>' for d in s["restart_delays_seconds"]
    )
    return f"""<!-- Written by `run.py service prepare`; rewritten on every prepare. Edit config.json's
     `service` block instead of this file. -->
<service>
  <id>{e(s["id"])}</id>
  <name>cherrypick supervisor</name>
  <description>The cherrypick supervisor; every scheduled job is its child (run.py service)</description>
  <executable>{e(python)}</executable>
  <arguments>"{e(launcher)}" supervise</arguments>
  <workingdirectory>{e(workdir)}</workingdirectory>
  <startmode>Automatic</startmode>
  <delayedAutoStart>{"true" if s["delayed_start"] else "false"}</delayedAutoStart>
{failures}
  <resetfailure>{int(s["reset_failure_hours"])} hour</resetfailure>
  <stopexecutable>{e(python)}</stopexecutable>
  <stoparguments>"{e(launcher)}" supervise --stop</stoparguments>
  <stoptimeout>{int(s["stop_timeout_seconds"])} sec</stoptimeout>
  <env name="PATH" value="{e(path_env)}"/>
  <logpath>{e(logdir)}</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
"""


def parse_sc_query(text: str) -> dict[str, Any]:
    """`sc query <id>` -> {installed, state}. Pure. 1060 is "the service does not exist"."""
    if "1060" in text or "does not exist" in text.lower():
        return {"installed": False, "state": None}
    m = re.search(r"STATE\s*:\s*\d+\s+(\w+)", text)
    return {"installed": bool(m), "state": m.group(1) if m else None}


def query(service_id: str) -> dict[str, Any]:
    if os.name != "nt":
        return {"installed": False, "state": None, "detail": "Windows services exist on Windows only"}
    try:
        r = subprocess.run(
            ["sc", "query", service_id],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"installed": None, "state": None, "detail": f"{type(exc).__name__}: {exc}"}
    return parse_sc_query(r.stdout + r.stderr)


def start(service_id: str) -> bool:
    """`sc start` -- the anchor's way to bring a stopped service back. May be refused without rights;
    False then, and the anchor's failure count escalates as it always has."""
    try:
        r = subprocess.run(
            ["sc", "start", service_id],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 or "1056" in (r.stdout + r.stderr)  # 1056: already running


def elevated_steps(service_exe: str, anchor_task: str, user: str) -> list[str]:
    """What a person runs in an ADMINISTRATOR prompt after `prepare`. Pure."""
    return [
        f'"{service_exe}" install /p',  # asks for the account (.\\{user}) and its password
        f'"{service_exe}" start',
        # The anchor must run with nobody logged on, so the hung-supervisor check survives logoff.
        "powershell -NoProfile -Command \"$c = Get-Credential '" + user + "'; "
        f"Set-ScheduledTask -TaskName '{anchor_task}' -User $c.UserName "
        '-Password $c.GetNetworkCredential().Password"',
    ]


def undo_steps(service_exe: str, anchor_task: str) -> list[str]:
    return [
        f'"{service_exe}" stop',
        f'"{service_exe}" uninstall',
        f"then: python packages/orchestrator/run.py install   (re-registers the anchor '{anchor_task}' as "
        "logged-on only, and starts the supervisor the usual way)",
    ]


def prepare(
    cfg: dict[str, Any], *, launcher: str, workdir: str, anchor_task: str, platform: str | None = None
) -> dict[str, Any]:
    s = settings(cfg)
    if (platform or os.name) != "nt":
        return {"ok": False, "error": "Windows only; on Linux and macOS the cron anchor runs without a login"}
    if not s["enabled"]:
        return {
            "ok": False,
            "error": "service mode is off: set config.json `service.enabled` to true first (it is opt-in)",
        }
    src = Path(os.path.expandvars(os.path.expanduser(str(s["winsw_exe"] or ""))))
    if not s["winsw_exe"] or not src.is_file():
        return {
            "ok": False,
            "error": "WinSW not found: download WinSW-x64.exe (v2.x) from github.com/winsw/winsw/releases "
            "and set config.json `service.winsw_exe` to its path",
        }
    d = service_dir()
    d.mkdir(parents=True, exist_ok=True)
    exe = d / f"{s['id']}.exe"
    shutil.copy2(src, exe)
    logdir = _home.logs_dir() / "service"
    logdir.mkdir(parents=True, exist_ok=True)
    xml = build_xml(
        s,
        python=console_python(),
        launcher=launcher,
        workdir=workdir,
        path_env=logon_path() or os.environ.get("PATH", ""),
        logdir=str(logdir),
    )
    (d / f"{s['id']}.xml").write_text(xml, encoding="utf-8")
    user = os.environ.get("USERNAME", "")
    return {
        "ok": True,
        "service_exe": str(exe),
        "xml": str(d / f"{s['id']}.xml"),
        "run_these_in_an_administrator_prompt": elevated_steps(str(exe), anchor_task, user),
        "then": "reboot once and check `run.py service status` and `run.py doctor` before turning "
        "auto-logon off",
    }


def status(cfg: dict[str, Any]) -> dict[str, Any]:
    s = settings(cfg)
    return {"enabled": bool(s["enabled"]), "id": s["id"], **query(s["id"])}


def uninstall(cfg: dict[str, Any], *, anchor_task: str) -> dict[str, Any]:
    s = settings(cfg)
    exe = service_dir() / f"{s['id']}.exe"
    return {"ok": True, "run_these_in_an_administrator_prompt": undo_steps(str(exe), anchor_task)}
