"""Command-line surface for cherrypick-weeklyput. Every subcommand is read-only and prints JSON.

    status     the calendar (today's entry, the next cycle), open puts, today's cycle rows
    positions  every put with its money laid out to add up
    cycles     one row per (arm, expiry): each name's net and the cycle's equal-weight mean in bp
    summary    each arm's forward read: finished cycles, mean, spread, worst, per name, dollars
    marks      one put's mark path (--position)
    calendar   the expiries this module sells into between two dates, with their entry sessions

The paper loop's own argv (`python -m cherrypick.weeklyput.paper_loop --once|--interval|--status`)
is what the orchestrator drives. The console reads `status`, `cycles` and `summary`: keep their
JSON shapes stable.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from datetime import date

_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

from cherrypick.core import home as _core_home  # noqa: E402


def load_config(path: str | None = None) -> dict:
    return _core_home.load_module_config("weeklyput", _PKG_ROOT, path)


def _print(obj) -> int:
    print(json.dumps(obj, indent=2, default=str))
    return 0


def cmd_status(args) -> int:
    from cherrypick.weeklyput import db, paper_loop, provider

    config = load_config(args.config)
    conn = db.connect_ro(args.db)
    return _print(paper_loop.run_status(config, conn, cache_path=provider.stream_cache_path(config)))


def cmd_positions(args) -> int:
    from cherrypick.weeklyput import db

    conn = db.connect_ro(args.db)
    return _print({"ok": True, "positions": db.positions(conn, args.arm, limit=args.limit)})


def cmd_cycles(args) -> int:
    from cherrypick.weeklyput import analytics, db

    conn = db.connect_ro(args.db)
    return _print({"ok": True, "cycles": analytics.cycles(conn, args.arm)})


def cmd_summary(args) -> int:
    from cherrypick.weeklyput import analytics, db

    conn = db.connect_ro(args.db)
    return _print({"ok": True, **analytics.summary(conn, args.arm), "breaks": db.measurement_breaks(conn)})


def cmd_marks(args) -> int:
    from cherrypick.weeklyput import db

    conn = db.connect_ro(args.db)
    return _print(
        {"ok": True, "position": db.position(conn, args.position), "marks": db.marks(conn, args.position)}
    )


def cmd_calendar(args) -> int:
    from cherrypick.weeklyput import clock

    first, last = date.fromisoformat(args.start), date.fromisoformat(args.end)
    return _print(
        {
            "ok": True,
            "cycles": [
                {"expiration": e.isoformat(), "entry_session": clock.entry_session(e).isoformat()}
                for e in clock.weekly_expiries(first, last)
            ],
        }
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="weeklyput", description="cherrypick-weeklyput read side")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("positions")
    p.add_argument("--arm")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(fn=cmd_positions)
    p = sub.add_parser("cycles")
    p.add_argument("--arm")
    p.set_defaults(fn=cmd_cycles)
    p = sub.add_parser("summary")
    p.add_argument("--arm")
    p.set_defaults(fn=cmd_summary)
    p = sub.add_parser("marks")
    p.add_argument("--position", required=True)
    p.set_defaults(fn=cmd_marks)
    p = sub.add_parser("calendar")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.set_defaults(fn=cmd_calendar)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
