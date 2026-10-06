"""Command-line surface for cherrypick-contango.

Subcommands (all read-only):
    status     each arm's account, holding and today's decision; today's regime read
    nav        the per-arm daily NAV series (contango_sessions)
    stints     every holding stint with its money laid out to add up
    metrics    each arm's daily-NAV tear sheet, buy-and-hold of the risk fund, and the path the
               arm's own rule should have produced (analytics.py) -- what the console reads

The paper loop's own argv (`python -m cherrypick.contango.paper_loop --once|--interval|--status`)
is what the orchestrator drives; this CLI is the human read side. The historical replay lives
outside the package, in `scripts/contango_replay.py`, because it reads another package's store.
"""

from __future__ import annotations

import argparse
import json
import pathlib

_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

from cherrypick.core import home as _core_home  # noqa: E402


def load_config(path: str | None = None) -> dict:
    return _core_home.load_module_config("contango", _PKG_ROOT, path)


def _print(obj) -> int:
    print(json.dumps(obj, indent=2, default=str))
    return 0


def cmd_status(args) -> int:
    from cherrypick.contango import db, paper_loop, provider

    config = load_config(args.config)
    conn = db.connect_ro(args.db)
    return _print(paper_loop.run_status(config, conn, cache_path=provider.stream_cache_path(config)))


def cmd_nav(args) -> int:
    from cherrypick.contango import db

    conn = db.connect_ro(args.db)
    return _print({"ok": True, "sessions": db.sessions(conn, args.arm, args.limit)})


def cmd_stints(args) -> int:
    from cherrypick.contango import db

    conn = db.connect_ro(args.db)
    return _print({"ok": True, "positions": db.positions(conn, args.arm)})


def cmd_metrics(args) -> int:
    from cherrypick.contango import analytics, db, provider

    config = load_config(args.config)
    conn = db.connect_ro(args.db)
    out = analytics.metrics(conn, config, technicals_path=provider.technicals_db_path(config))
    return _print({"ok": True, **out})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="contango", description="cherrypick-contango read side")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("nav")
    p.add_argument("--arm")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(fn=cmd_nav)
    p = sub.add_parser("stints")
    p.add_argument("--arm")
    p.set_defaults(fn=cmd_stints)
    sub.add_parser("metrics").set_defaults(fn=cmd_metrics)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
