"""Launcher for the console UI server, keeping the suite's Python-command convention.

Usage:
    python run.py dashboard --serve [--port 5070]

The server itself is Node (server/dist/index.js); this script just locates and
execs it so the orchestrator and the serve-dashboard command never need to know
about the Node toolchain.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_ENTRY = HERE / "server" / "dist" / "index.js"


def main() -> int:
    parser = argparse.ArgumentParser(description="cherrypick console launcher")
    sub = parser.add_subparsers(dest="command", required=True)
    dash = sub.add_parser("dashboard", help="serve the console UI")
    dash.add_argument("--serve", action="store_true", help="start the server (required)")
    dash.add_argument("--port", type=int, default=None, help="override the listen port")
    creds = sub.add_parser("credentials", help="manage the console's broker credential")
    creds.add_argument("action", choices=["set", "show", "clear"])
    args = parser.parse_args()

    node = shutil.which("node")
    if node is None:
        print("node not found on PATH — install Node.js 22+ to run the console", file=sys.stderr)
        return 1
    if not SERVER_ENTRY.exists():
        print(
            "console server is not built. From packages/console run:\n"
            "    pnpm install\n    pnpm build",
            file=sys.stderr,
        )
        return 1

    if args.command == "credentials":
        cli = HERE / "server" / "dist" / "cli" / "credentials-cli.js"
        return subprocess.call([node, str(cli), args.action])

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

    return subprocess.call([node, str(SERVER_ENTRY)])


if __name__ == "__main__":
    raise SystemExit(main())
