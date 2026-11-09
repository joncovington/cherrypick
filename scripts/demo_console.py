#!/usr/bin/env python
"""Serve the console on a demo home built by scripts/demo_home.py. The only supported way to do it.

    python scripts/demo_console.py --home <dir> [--keepalive]

The demo must never see a real credential, session or quote, so this launcher refuses anything that is
not a generated demo home, refuses the port of the real console, and starts the server with the OS
keyring switched off on both the Python and the Node side. It stops what it started (its own pids only)
when you press Ctrl-C.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MARKER = ".demo-home"
DEFAULT_PORT = 5070
NODE_STUB = REPO / "scripts" / "demo_console_nokeyring.mjs"
SERVER = REPO / "packages" / "console" / "server" / "dist" / "index.js"
SECRETISH = re.compile(r"(TOKEN|SECRET|OAUTH|CLIENT_ID|BROKER|PASSWORD|CREDENTIAL)", re.I)


class Refused(SystemExit):
    """A guard said no. Carries the reason as its message."""


def _port_of(home: Path) -> int | None:
    try:
        doc = json.loads((home / "config" / "console.json").read_text(encoding="utf-8"))
        port = doc["serve"]["port"]
        return int(port) if isinstance(port, int) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def real_home() -> Path:
    return Path.home() / ".cherrypick"


def check(home: Path, real: Path | None = None, env_home: str | None = None) -> int:
    """Return the port to serve on, or raise Refused. Touches nothing but reads of console.json."""
    real = (real if real is not None else real_home()).resolve()
    home = home.resolve()
    if home == real or real in home.parents or home in real.parents:
        raise Refused(f"refusing: {home} is, or contains, the real home")
    if env_home is not None and env_home != "" and Path(env_home).expanduser().resolve() == real:
        raise Refused("refusing: CHERRYPICK_HOME names the real home")
    if not (home / MARKER).is_file():
        raise Refused(f"refusing: {home} has no {MARKER} marker; build it with scripts/demo_home.py")
    port = _port_of(home)
    if port is None:
        raise Refused("refusing: the demo home names no console port")
    real_port = _port_of(real)
    if port == DEFAULT_PORT or (real_port is not None and port == real_port):
        raise Refused(f"refusing: port {port} is the real console's")
    return port


def demo_env(base: dict[str, str], home: Path) -> dict[str, str]:
    """The server's environment: this home, no keyring on either side, nothing credential-shaped inherited."""
    env = {k: v for k, v in base.items() if not SECRETISH.search(k)}
    env["CHERRYPICK_HOME"] = str(home)
    env["CHERRYPICK_PYTHON"] = sys.executable
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    stub = f"--import {NODE_STUB.as_uri()}"
    env["NODE_OPTIONS"] = f"{base.get('NODE_OPTIONS', '')} {stub}".strip()
    return env


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", required=True, help="a home built by scripts/demo_home.py")
    ap.add_argument(
        "--keepalive", action="store_true", help="also keep the heartbeats fresh so System reads healthy"
    )
    args = ap.parse_args(argv)
    home = Path(args.home).resolve()
    port = check(home, env_home=os.environ.get("CHERRYPICK_HOME"))
    if not SERVER.is_file():
        raise SystemExit(
            "the console server is not built: pnpm --dir packages/console --filter @console/server build"
        )
    env = demo_env(dict(os.environ), home)
    children: list[subprocess.Popen] = []
    try:
        if args.keepalive:
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(REPO / "scripts" / "demo_home.py"),
                        "--out",
                        str(home),
                        "--keepalive",
                    ],
                    env=env,
                )
            )
        children.append(subprocess.Popen(["node", str(SERVER)], env=env, cwd=SERVER.parent))
        print(f"demo console on http://127.0.0.1:{port}/ (Ctrl-C to stop)", flush=True)
        while all(c.poll() is None for c in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for c in children:
            if c.poll() is None:
                c.terminate()
        for c in children:
            try:
                c.wait(timeout=10)
            except subprocess.TimeoutExpired:
                c.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
