"""Command-line surface for cherrypick-automations. Every subcommand is read-only and prints JSON.

    status     today's arms and their windows, open positions, today's session rows
    positions  every position with its money laid out to add up
    decisions  the narrative journal (--date), the gate that blocked each refused arm
    summary    each arm's closed trades, net and drawdown, and the arms together
    arms       the arms the config resolves to (shipped, with this machine's changes laid over)
    groups-view   every group (switch, start, budget, shares) and every automation by group, each with
               its own switch (the console's Groups page)
    groups-check  a change to the groups (JSON on stdin: groups, members) checked as the loop loads
               it: its problems, the groups block a save would write and the automations it moves.
               Writes nothing
    replay     run a replay record file again and say where the decisions differ
    builder    every automation block by block, each block's backtest label, its start, forward rule
               and backtest suggestion (the console's Builder page). Config and the registry's cache only
    draft      the Builder's check of one edited automation (JSON on stdin: name, automation): its
               problems, the entry a save would write (always off, its register label kept as stored)
               and its Builder row. Without `automation`, the automation as an editor starts from it.
               Writes nothing
    builder-schema  every choice the Builder's form offers, from the engine's own tables
    builder-today   one automation (JSON on stdin: name, optionally an unsaved automation) decided now
               as paper would, on a read-only snapshot: the entry and legs it would take, or the
               first failing check, with every check. Writes nothing
    replay-day the nightly check: a session's recorded ticks through the backtest's decision path, each
               decision or fill that differs listed; exit 1 when any does (a record-less day is 0)

The paper loop's own argv (`python -m cherrypick.automations.paper_loop --once|--interval|--status`) is what
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


def cmd_groups(args) -> int:
    """Each automation group: its switch and its automations, on or off, with their register verdicts.
    Config only, no ledger or cache read (the console's group panel)."""
    config = config_mod.load_config(args.config)
    return _print({"ok": True, "groups": config_mod.group_status(config_mod.load_arms(config), config)})


def cmd_groups_view(args) -> int:
    """Every group with its switch, start, budget and shares, and every automation by group (its own
    switch beside the group's), for the console's Groups page. Config only."""
    from . import groupedit

    return _print({"ok": True, **groupedit.view(config_mod.load_config(args.config))})


def cmd_groups_check(args) -> int:
    """A change to the groups (JSON on stdin: {groups, members}) checked as the loop would load it: its
    problems, the `groups` block a save would write and the automations it moves. Writes nothing."""
    import sys

    from . import groupedit

    try:
        req = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        return _print({"ok": False, "error": f"request is not valid JSON: {exc}"})
    return _print(groupedit.check(config_mod.load_config(args.config), req))


def cmd_replay(args) -> int:
    from . import events, record

    config = config_mod.load_config(args.config)
    results = record.replay_file(Path(args.file), events.load_calendar(), costs=config)
    differs = [r for r in results if r["recorded"] != r["replayed"]]
    return _print({"ok": True, "ticks": len(results), "differ": differs})


def cmd_builder(args) -> int:
    from cherrypick.core.clock import now_et

    from . import enrolment, paper_loop

    config = config_mod.load_config(args.config)
    arms = config_mod.load_arms(config)
    today = now_et().date()
    suggestions = paper_loop.backtest_suggestions(arms, today)
    rows = [enrolment.builder_view(a, suggestions.get(n)) for n, a in sorted(arms.items())]
    return _print(
        {"ok": True, "date": today.isoformat(), "blocks": list(enrolment.BLOCKS), "automations": rows}
    )


def cmd_draft(args) -> int:
    import sys

    from cherrypick.core.clock import now_et

    from . import drafts

    try:
        req = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        return _print({"ok": False, "error": f"request is not valid JSON: {exc}"})
    if not isinstance(req, dict):
        return _print({"ok": False, "error": "request: an object with name and automation"})
    config = config_mod.load_config(args.config)
    name = req.get("name")
    if "automation" not in req:
        spec = drafts.editable(config, name) if isinstance(name, str) else None
        return _print({"ok": True, "name": name, "automation": spec})
    return _print(drafts.draft(config, name, req["automation"], now_et().date()))


def cmd_builder_schema(args) -> int:
    from . import builderform

    return _print(builderform.schema())


def cmd_builder_today(args) -> int:
    import sqlite3
    import sys

    from cherrypick.core.clock import now_et

    from . import builderform, db, events, provider

    try:
        req = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        return _print({"ok": False, "error": f"request is not valid JSON: {exc}"})
    if not isinstance(req, dict):
        return _print({"ok": False, "error": "request: an object with name and, optionally, automation"})
    config = config_mod.load_config(args.config)
    arm, problems = builderform.resolve(config, req.get("name"), req.get("automation"))
    if arm is None:
        return _print({"ok": True, "name": req.get("name"), "state": "invalid", "problems": problems})
    try:
        conn = db.connect_ro(args.db)
    except (OSError, sqlite3.Error) as exc:  # no ledger yet is an answer, not a crash
        return _print({"ok": True, "name": arm["name"], "state": "no_ledger", "reason": str(exc)})
    try:
        evts = events.load_calendar((config.get("events") or {}).get("path"))
        out = builderform.today(
            config, arm, now_et(), conn=conn, cache_path=provider.stream_cache_path(config), evts=evts
        )
    finally:
        conn.close()
    return _print(out)


def cmd_replay_day(args) -> int:
    from datetime import date

    from cherrypick.core import calendar as cal
    from cherrypick.core.clock import now_et

    from . import events, record

    config = config_mod.load_config(args.config)
    day = date.fromisoformat(args.date) if args.date else now_et().date()
    if not args.date and not cal.is_trading_day(day):
        day = cal.previous_trading_day(day)
    path = record.replay_dir() / f"{day.isoformat()}.jsonl"
    if not path.exists():
        _print({"ok": True, "session": day.isoformat(), "record": False, "differ": []})
        return 0
    calendar = events.load_calendar((config.get("events") or {}).get("path"))
    out = record.compare_day(path, calendar, costs=config)
    _print({"ok": True, "record": True, **out})
    return 1 if out["differ"] else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="automations", description="cherrypick-automations (paper-only)")
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
    sub.add_parser("groups").set_defaults(fn=cmd_groups)
    sub.add_parser("groups-view").set_defaults(fn=cmd_groups_view)
    sub.add_parser("groups-check").set_defaults(fn=cmd_groups_check)
    p = sub.add_parser("replay")
    p.add_argument("file")
    p.set_defaults(fn=cmd_replay)
    sub.add_parser("builder").set_defaults(fn=cmd_builder)
    sub.add_parser("draft").set_defaults(fn=cmd_draft)
    sub.add_parser("builder-schema").set_defaults(fn=cmd_builder_schema)
    sub.add_parser("builder-today").set_defaults(fn=cmd_builder_today)
    p = sub.add_parser("replay-day")
    p.add_argument("--date", help="YYYY-MM-DD; default today, or the last trading day")
    p.set_defaults(fn=cmd_replay_day)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
