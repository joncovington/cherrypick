"""Launcher for the console UI server, keeping the suite's Python-command convention.

Usage:
    python run.py dashboard --serve [--port 5070]

The server itself is Node (server/dist/index.js); this script just locates and
execs it so the supervisor and the /console command never need to know about the
Node toolchain. The supervisor keeps this running as an always-on resident job.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_ENTRY = HERE / "server" / "dist" / "index.js"

# node is a console-subsystem program, and the supervisor launches this script under pythonw (no
# console of its own) — so without this flag every start and every restart of the console pops a
# terminal window on the user's screen. Inherited stdio still works, so a human running this from a
# terminal sees the server's output exactly as before. 0 off-Windows, so the call site stays portable.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _never_throttle(handle: int) -> bool:
    """Opt a process out of Windows power throttling (EcoQoS, Task Manager's "efficiency mode").

    The supervisor runs as a service with nobody logged on, so the console is a windowless
    background process, which is exactly what Windows throttles: on this machine's hybrid CPU the
    same CPU-bound work ran 6.8x slower with EcoQoS on, and every synchronous read in the served
    console ran 4-6x slower than the same code from a terminal (2026-11-02). Setting the control bit
    with the state bit clear tells Windows never to throttle this process. Best effort: False off
    Windows, or where the call is refused, and the console runs as before.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    import ctypes.wintypes as wt

    class _State(ctypes.Structure):
        _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]

    execution_speed = 0x1  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
    state = _State(1, execution_speed, 0)  # version 1; controlled, not throttled
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    process_power_throttling = 4  # PROCESS_INFORMATION_CLASS.ProcessPowerThrottling
    try:
        ok = k32.SetProcessInformation(
            handle, process_power_throttling, ctypes.byref(state), ctypes.sizeof(state)
        )
        return bool(ok)
    except OSError:
        return False


def _child_env() -> dict[str, str]:
    """The environment node runs in, with THIS interpreter's directory first on PATH.

    The server shells out to a bare `python` for everything Python owns (the keyring bridge, the
    config editor, positions, the advisor, module CLIs). The installer puts the suite in a `.venv`
    that is on nobody's PATH, and the supervisor starts this launcher with that venv's interpreter,
    so without this a fresh install's console would reach the system Python, which has none of the
    suite: no credential, no Config page, no positions. Putting the directory of the interpreter
    that is running this script first makes `python` resolve to the one that has the suite (a venv's
    Scripts/ or bin/ holds both python and pythonw).
    """
    env = dict(os.environ)
    # absolute(), not resolve(): a Linux venv's bin/python is a symlink to the BASE interpreter
    # (/usr/bin/python3.14), and resolving all the way through lands in /usr/bin — the very
    # directory this exists to outrank. Windows venvs copy a real launcher in, so resolve() happened
    # to work there; on Linux the venv must be reached THROUGH the symlink (executing
    # .venv/bin/python is what gives a process the venv's site-packages).
    here = str(Path(sys.executable).absolute().parent)
    env["PATH"] = here + os.pathsep + env.get("PATH", "")
    # And name it outright, for the server's `suitePython()`: PATH alone failed where the venv holds
    # only `python3`, or a bare `python` resolved elsewhere (2026-10-08 OS audit). pythonw (the
    # supervisor's windowless interpreter) maps to its python.exe twin -- every bridge reads stdout.
    if not env.get("CHERRYPICK_PYTHON"):
        exe = Path(sys.executable).absolute()
        if exe.stem.lower() == "pythonw" and exe.with_name("python" + exe.suffix).exists():
            exe = exe.with_name("python" + exe.suffix)
        env["CHERRYPICK_PYTHON"] = str(exe)
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description="cherrypick console launcher")
    sub = parser.add_subparsers(dest="command", required=True)
    dash = sub.add_parser("dashboard", help="serve the console UI")
    dash.add_argument("--serve", action="store_true", help="start the server (required)")
    dash.add_argument("--port", type=int, default=None, help="override the listen port")
    creds = sub.add_parser("credentials", help="inspect the suite broker credential (read-only)")
    creds.add_argument("action", choices=["set", "show", "probe", "clear"])
    args = parser.parse_args()

    node = shutil.which("node")
    if node is None:
        print("node not found on PATH — install Node.js 22+ to run the console", file=sys.stderr)
        return 1
    if not SERVER_ENTRY.exists():
        print(
            "console server is not built. From packages/console run:\n    pnpm install\n    pnpm build",
            file=sys.stderr,
        )
        return 1

    if args.command == "credentials":
        cli = HERE / "server" / "dist" / "cli" / "credentials-cli.js"
        return subprocess.call(
            [node, str(cli), args.action], creationflags=CREATE_NO_WINDOW, env=_child_env()
        )

    if not args.serve:
        parser.error("only 'dashboard --serve' is supported")

    if args.port is not None:
        # The Node server reads its port from ~/.cherrypick/config/console.json;
        # a CLI override is passed through as a one-off config on stdin-free env.
        cfg_dir = Path.home() / ".cherrypick" / "config"
        cfg_path = cfg_dir / "console.json"
        cfg = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cfg = {}
        serve = cfg.setdefault("serve", {})
        if serve.get("port") != args.port:
            cfg_dir.mkdir(parents=True, exist_ok=True)
            serve["port"] = args.port
            cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
            print(f"wrote serve.port={args.port} to {cfg_path}")

    # Hand node our stdout/stderr explicitly. Under pythonw with no console, an implicit inherit gave
    # node no usable handles: the supervisor's logs/jobs/console.stderr.log received not one line of
    # node's output in 100+ starts, so every crash's stack trace was lost. None (a stream pythonw
    # could not open) falls back to the old implicit behaviour.
    with subprocess.Popen(
        [node, str(SERVER_ENTRY)],
        creationflags=CREATE_NO_WINDOW,
        env=_child_env(),
        stdout=sys.stdout,
        stderr=sys.stderr,
    ) as server:
        if sys.platform == "win32":
            ok = _never_throttle(int(server._handle))  # type: ignore[attr-defined]
            if sys.stderr is not None:  # None under pythonw when it could not open the stream
                said = "off" if ok else "left to Windows"
                print(f"console: power throttling {said}", file=sys.stderr, flush=True)
        return server.wait()


if __name__ == "__main__":
    raise SystemExit(main())
