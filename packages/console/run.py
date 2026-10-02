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
    here = str(Path(sys.executable).resolve().parent)
    env["PATH"] = here + os.pathsep + env.get("PATH", "")
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

    return subprocess.call([node, str(SERVER_ENTRY)], creationflags=CREATE_NO_WINDOW, env=_child_env())


if __name__ == "__main__":
    raise SystemExit(main())
