"""Command-line surface for cherrypick-ladder. Every subcommand is read-only and prints JSON.

    status     today's reference closes, each arm's next buy level, open lots
    lots       every lot with its money laid out to add up (--arm, --open, --closed)
    summary    each arm's forward read: lots, net, holds, what is still open
    equity     each arm by session: realized net, and realized plus open lots marked at the bid
    shadow     each armed day's live dry-run buys against paper's arm that day (--arm, --since,
               --live-db); both ledgers read only

The paper loop's own argv (`python -m cherrypick.ladder.paper_loop --once|--interval|--status`) is
what the orchestrator drives. Keep these JSON shapes stable: the console will read them.
"""

from __future__ import annotations

import argparse
import json
import pathlib

_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

from cherrypick.core import home as _core_home  # noqa: E402


def load_config(path: str | None = None) -> dict:
    return _core_home.load_module_config("ladder", _PKG_ROOT, path)


def _print(obj) -> int:
    print(json.dumps(obj, indent=2, default=str))
    return 0


def cmd_status(args) -> int:
    from cherrypick.ladder import db, paper_loop, provider

    config = load_config(args.config)
    conn = db.connect_ro(args.db)
    return _print(paper_loop.run_status(config, conn, cache_path=provider.stream_cache_path(config)))


def cmd_lots(args) -> int:
    from cherrypick.ladder import db

    status = "open" if args.open else "closed" if args.closed else None
    conn = db.connect_ro(args.db)
    return _print({"ok": True, "lots": db.positions(conn, args.arm, status=status, limit=args.limit)})


def cmd_summary(args) -> int:
    from cherrypick.ladder import analytics, db

    conn = db.connect_ro(args.db)
    return _print({"ok": True, **analytics.summary(conn, args.arm), "breaks": db.measurement_breaks(conn)})


def cmd_equity(args) -> int:
    from cherrypick.ladder import analytics, db

    conn = db.connect_ro(args.db)
    return _print({"ok": True, **analytics.equity(conn, args.arm), "breaks": db.measurement_breaks(conn)})


def cmd_shadow(args) -> int:
    from cherrypick.ladder import db, live_db, live_gates, shadow

    block = live_gates.live_block(load_config(args.config))
    arm = args.arm or str(block.get("arm") or "").strip() or live_gates.DEFAULT_ARM
    funds = block.get("symbols") if isinstance(block.get("symbols"), list) else []
    live = live_db.connect_ro(args.live_db)
    paper = db.connect_ro(args.db)
    try:
        return _print(shadow.report(live, paper, arm=arm, funds=funds, since=args.since))
    finally:
        live.close()
        paper.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ladder", description="cherrypick-ladder read side")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("lots")
    p.add_argument("--arm")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--open", action="store_true")
    g.add_argument("--closed", action="store_true")
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(fn=cmd_lots)
    p = sub.add_parser("summary")
    p.add_argument("--arm")
    p.set_defaults(fn=cmd_summary)
    p = sub.add_parser("equity")
    p.add_argument("--arm")
    p.set_defaults(fn=cmd_equity)
    p = sub.add_parser("shadow")
    p.add_argument("--arm", help="the paper arm to compare (default: live.arm)")
    p.add_argument("--since", help="first day, YYYY-MM-DD")
    p.add_argument("--live-db", dest="live_db", help="the live ledger (default: live_trades.db)")
    p.set_defaults(fn=cmd_shadow)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
