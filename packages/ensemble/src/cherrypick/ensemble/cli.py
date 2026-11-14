"""Command-line surface for cherrypick-ensemble. Every subcommand is read-only and prints JSON.

    status     today's arms and their windows, open positions, today's session rows
    positions  every position with its money laid out to add up
    decisions  the narrative journal (--date), the gate that blocked each refused arm
    summary    each arm's closed trades, net and drawdown, and the arms together
    arms       the arms the config resolves to (shipped, with this machine's changes laid over)
    replay     run a replay record file again and say where the decisions differ

The paper loop's own argv (`python -m cherrypick.ensemble.paper_loop --once|--interval|--status`) is what
the orchestrator drives.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config as config_mod


def _print(obj) -> int:
    print(json.dumps(obj, indent=2, default=str))
    return 0


def cmd_status(args) -> int:
    from . import db, paper_loop, provider

    config = config_mod.load_config(args.config)
    arms = config_mod.load_arms(config)
    conn = db.connect_ro(args.db)
    return _print(paper_loop.run_status(config, arms, conn, cache_path=provider.stream_cache_path(config)))


def cmd_positions(args) -> int:
    from . import db

    conn = db.connect_ro(args.db)
    return _print({"ok": True, "positions": db.positions(conn, args.arm, limit=args.limit)})


def cmd_decisions(args) -> int:
    from . import db

    conn = db.connect_ro(args.db)
    sql, params = "SELECT * FROM ensemble_decisions", []
    if args.date:
        sql += " WHERE trade_date = ?"
        params.append(args.date)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY trade_date DESC, arm, first_ts", params)]
    return _print({"ok": True, "decisions": rows[: args.limit] if args.limit else rows})


def cmd_summary(args) -> int:
    from . import analytics, db

    return _print({"ok": True, **analytics.summary(db.connect_ro(args.db))})


def cmd_arms(args) -> int:
    config = config_mod.load_config(args.config)
    return _print({"ok": True, "arms": config_mod.load_arms(config)})


def cmd_replay(args) -> int:
    from . import events, record

    config = config_mod.load_config(args.config)
    results = record.replay_file(Path(args.file), events.load_calendar(), costs=config)
    differs = [r for r in results if r["recorded"] != r["replayed"]]
    return _print({"ok": True, "ticks": len(results), "differ": differs})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ensemble", description="cherrypick-ensemble (paper-only)")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("positions")
    p.add_argument("--arm")
    p.add_argument("--limit", type=int)
    p.set_defaults(fn=cmd_positions)
    p = sub.add_parser("decisions")
    p.add_argument("--date")
    p.add_argument("--limit", type=int)
    p.set_defaults(fn=cmd_decisions)
    sub.add_parser("summary").set_defaults(fn=cmd_summary)
    sub.add_parser("arms").set_defaults(fn=cmd_arms)
    p = sub.add_parser("replay")
    p.add_argument("file")
    p.set_defaults(fn=cmd_replay)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
