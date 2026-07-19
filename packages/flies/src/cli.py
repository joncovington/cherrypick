"""Command-line surface for cherrypick-flies.

Subcommands:
    once      run one iteration of every enabled arm against a snapshot (JSON on stdin or --snapshot)
    settle    cash-settle a session's books at the settlement print
    status    print the current books

The snapshot is supplied by the caller rather than fetched here, keeping this package's decision path
free of network I/O — the same split MEIC uses between `paper_loop.py` (fetch) and `paper.py` (decide).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import book as bookmod  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402


def load_config(path: str | None = None) -> dict:
    """Explicit path, then FLIES_CONFIG, then the managed home, then the repo, then the example.

    The managed-home entry (`~/.cherrypick/config/flies.json`) matters: it is where the suite keeps
    per-module config and where `cherrypick doctor` looks. Without it, doctor reports the module as
    unconfigured while the module happily runs off its in-repo copy — the two disagreeing about
    where configuration lives is exactly how a machine ends up running settings nobody can find.
    """
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    candidates = [
        path,
        os.environ.get("FLIES_CONFIG"),
        os.path.join(home, "config", "flies.json"),
        os.path.join(_HERE, "..", "config.json"),
        os.path.join(_HERE, "..", "config.example.json"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            with open(c, encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit("no config found — copy config.example.json to config.json")


def enabled_arms(config: dict) -> list[str]:
    arms = config.get("arms", {})
    return [a for a in engine.ARMS if arms.get(a, {}).get("enabled", True) and a in arms]


def _read_snapshot(args) -> dict:
    if args.snapshot:
        with open(args.snapshot, encoding="utf-8") as f:
            return json.load(f)
    return json.load(sys.stdin)


def cmd_once(args) -> int:
    config = load_config(args.config)
    snapshot = _read_snapshot(args)
    conn = dbmod.connect(args.db)
    out = [bookmod.process_snapshot(snapshot, config, conn, arm) for arm in enabled_arms(config)]
    print(json.dumps({"ok": True, "books": out}, indent=2, default=str))
    return 0


def cmd_settle(args) -> int:
    config = load_config(args.config)
    conn = dbmod.connect(args.db)
    out = [bookmod.settle_book(conn, args.date, arm, args.symbol, args.price, config)
           for arm in enabled_arms(config)]
    print(json.dumps({"ok": True, "books": out}, indent=2, default=str))
    return 0


def cmd_status(args) -> int:
    conn = dbmod.connect(args.db)
    q = "SELECT * FROM fly_books"
    params: list = []
    if args.date:
        q += " WHERE trade_date = ?"
        params.append(args.date)
    q += " ORDER BY id DESC LIMIT 50"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    print(json.dumps({"ok": True, "books": rows}, indent=2, default=str))
    return 0


def cmd_dashboard(args) -> int:
    import dashboard
    return dashboard.serve(dashboard.resolve_port(args.port), args.db, args.open)


def cmd_section(args) -> int:
    import section
    print(json.dumps(section.build_section(args.db, args.date, args.arm), indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="flies", description="0DTE net-credit butterfly paper module")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="command", required=True)

    p_once = sub.add_parser("once", help="one iteration of every enabled arm")
    p_once.add_argument("--snapshot", help="snapshot JSON file (default: stdin)")
    p_once.set_defaults(func=cmd_once)

    p_settle = sub.add_parser("settle", help="cash-settle a session's books")
    p_settle.add_argument("--date", required=True)
    p_settle.add_argument("--symbol", required=True)
    p_settle.add_argument("--price", type=float, required=True)
    p_settle.set_defaults(func=cmd_settle)

    p_status = sub.add_parser("status", help="print books")
    p_status.add_argument("--date")
    p_status.set_defaults(func=cmd_status)

    p_dash = sub.add_parser("dashboard", help="serve the read-only dashboard (loopback)")
    p_dash.add_argument("--port", type=int)
    p_dash.add_argument("--open", action="store_true")
    p_dash.set_defaults(func=cmd_dashboard)

    p_section = sub.add_parser("section", help="emit a cherrypick.core.viz card payload")
    p_section.add_argument("--json", action="store_true", help="accepted for symmetry; always JSON")
    p_section.add_argument("--date")
    p_section.add_argument("--arm")
    p_section.set_defaults(func=cmd_section)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
