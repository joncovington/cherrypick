"""Command-line surface for cherrypick-automations. Every subcommand is read-only and prints JSON.

    status     today's arms and their windows, open positions, today's session rows
    positions  every position with its money laid out to add up
    decisions  the narrative journal (--date), the gate that blocked each refused arm
    summary    each arm's closed trades, net and drawdown, and the arms together
    arms       the arms the config resolves to (shipped, with this machine's changes laid over)
    groups-view   every group (switch, start, budget, shares) and every automation by group, each with
               its own switch (the console's Groups page)
    groups-read   every group as one unit: its members' line stacked, net and drawdown against its
               allocation, the most risk held at once against its cap (and each member's against its
               share), the entries the cap or a share refused, and how its members move together
    groups-check  a change to the groups (JSON on stdin: groups, members) checked as the loop loads
               it: its problems, the groups block a save would write and the automations it moves.
               Writes nothing
    entries    each automation's entry funnel from the session register: the sessions it was due, how
               many reached each step of the decision and the causes of the ones that stopped there.
               With --name, one automation's, the run's beside it (--run-reasons: a Builder run's
               summary reasons as JSON) and its latest register rows
    replay     run a replay record file again and say where the decisions differ
    reference-check  the Builder's pin of one Builder run as an automation's backtest reference (JSON on
               stdin: name, run; run null to unpin) checked: its problems and the references block a
               save would write. Writes nothing
    builder    every automation block by block, each block's backtest label, its start, forward rule,
               backtest suggestion and pinned reference run (the console's Builder page). Config and the
               registry's cache only
    draft      the Builder's check of one edited automation (JSON on stdin: name, automation): its
               problems, the entry a save would write (always off, its register label kept as stored)
               and its Builder row. Without `automation`, the automation as an editor starts from it.
               Writes nothing
    legs-convert    this machine's automations stored in the older leg spelling (side/right), each with
               its legs in the written one (action/qty/side), and whether converting them would change
               how any automation loads (it must not). Reads only: the orchestrator writes
    builder-schema  every choice the Builder's form offers, from the engine's own tables
    builder-strategy  a strategy's legs and expiry from its settings (JSON on stdin: strategy, settings),
               and a whole automation from it for the Builder. Writes nothing
    builder-entry   an entry preset's entry and the gates of the filters stacked on it (JSON on stdin:
               entry, settings, filters [{filter, settings}]). Writes nothing
    builder-today   one automation (JSON on stdin: name, optionally an unsaved automation) decided now
               as paper would, on a read-only snapshot: the entry and legs it would take, or the
               first failing check, with every check. Writes nothing
    builder-payoff  the structure one automation (JSON on stdin as builder-today) would take and its
               P&L at expiry, on live quotes or else the last session our own recorder holds. Writes
               nothing
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


def cmd_groups_read(args) -> int:
    """Every group as one unit (`groupread.read`), from the config and the ledger read-only."""
    from . import db, groupread

    config = config_mod.load_config(args.config)
    arms = config_mod.load_arms(config)
    if Path(db.resolve_path(args.db)).exists():
        conn = db.connect_ro(args.db)
        positions = [dict(r) for r in conn.execute("SELECT * FROM ensemble_positions")]
        register = db.entries(conn)
    else:
        positions, register = [], []
    return _print({"ok": True, **groupread.read(arms, config_mod.groups(config), positions, register)})


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


def cmd_reference_check(args) -> int:
    """The Builder's pin of a backtest reference (JSON on stdin: {name, run}, run null to unpin) checked:
    its problems and the `references` block a save would write. Writes nothing."""
    import sys

    from . import references

    try:
        req = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        return _print({"ok": False, "error": f"request is not valid JSON: {exc}"})
    return _print(references.check(config_mod.load_config(args.config), req))


def _register(db_path: str | None) -> list[dict]:
    """Every register row, or none when the ledger does not exist yet. Read-only."""
    from . import db

    if not Path(db.resolve_path(db_path)).exists():
        return []
    return db.entries(db.connect_ro(db_path))


def cmd_entries(args) -> int:
    from . import entries

    rows = _register(args.db)
    if args.name:
        try:
            run = json.loads(args.run_reasons) if args.run_reasons else None
        except json.JSONDecodeError as exc:
            return _print({"ok": False, "error": f"--run-reasons is not valid JSON: {exc}"})
        if run is not None and not isinstance(run, dict):
            return _print({"ok": False, "error": "--run-reasons must be an object of reason: sessions"})
        return _print({"ok": True, **entries.one(args.name, rows, run)})
    arms = config_mod.load_arms(config_mod.load_config(args.config))
    return _print(
        {"ok": True, "steps": [k for k, _ in entries.STEPS], "automations": entries.view(arms, rows)}
    )


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
    from . import references

    try:
        pinned = references.references(config)
    except ValueError:
        pinned = {}
    rows = [
        {**enrolment.builder_view(a, suggestions.get(n)), "reference": pinned.get(n)}
        for n, a in sorted(arms.items())
    ]
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


def cmd_legs_convert(args) -> int:
    from . import drafts

    return _print(drafts.leg_conversion(config_mod.load_config(args.config)))


def cmd_builder_strategy(args) -> int:
    import sys

    from . import builderform

    try:
        req = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        return _print({"ok": False, "error": f"request is not valid JSON: {exc}"})
    if not isinstance(req, dict) or not isinstance(req.get("settings", {}), dict):
        return _print({"ok": False, "error": "request: an object with strategy and, optionally, settings"})
    return _print(builderform.strategy_answer(str(req.get("strategy", "")), req.get("settings") or {}))


def cmd_builder_entry(args) -> int:
    import sys

    from . import builderform

    try:
        req = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        return _print({"ok": False, "error": f"request is not valid JSON: {exc}"})
    filters = req.get("filters", []) if isinstance(req, dict) else None
    if (
        not isinstance(req, dict)
        or not isinstance(req.get("settings", {}), dict)
        or not isinstance(filters, list)
        or not all(isinstance(f, dict) and isinstance(f.get("settings", {}), dict) for f in filters)
    ):
        return _print({"ok": False, "error": "request: an object with entry, settings and filters"})
    return _print(builderform.entry_answer(str(req.get("entry", "")), req.get("settings") or {}, filters))


def cmd_builder_schema(args) -> int:
    from . import builderform

    return _print(builderform.schema())


def cmd_builder_today(args) -> int:
    return _builder_read(args, "today")


def cmd_builder_payoff(args) -> int:
    return _builder_read(args, "payoff")


def _builder_read(args, kind: str) -> int:
    """One automation (JSON on stdin: name, optionally an unsaved automation) read now on a read-only
    snapshot: `builderform.today` or `builderform.payoff`. Writes nothing."""
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
        out = getattr(builderform, kind)(
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
    sub.add_parser("groups-read").set_defaults(fn=cmd_groups_read)
    sub.add_parser("reference-check").set_defaults(fn=cmd_reference_check)
    p = sub.add_parser("entries")
    p.add_argument("--name")
    p.add_argument("--run-reasons", dest="run_reasons")
    p.set_defaults(fn=cmd_entries)
    p = sub.add_parser("replay")
    p.add_argument("file")
    p.set_defaults(fn=cmd_replay)
    sub.add_parser("builder").set_defaults(fn=cmd_builder)
    sub.add_parser("draft").set_defaults(fn=cmd_draft)
    sub.add_parser("builder-schema").set_defaults(fn=cmd_builder_schema)
    sub.add_parser("builder-strategy").set_defaults(fn=cmd_builder_strategy)
    sub.add_parser("builder-entry").set_defaults(fn=cmd_builder_entry)
    sub.add_parser("legs-convert").set_defaults(fn=cmd_legs_convert)
    sub.add_parser("builder-today").set_defaults(fn=cmd_builder_today)
    sub.add_parser("builder-payoff").set_defaults(fn=cmd_builder_payoff)
    p = sub.add_parser("replay-day")
    p.add_argument("--date", help="YYYY-MM-DD; default today, or the last trading day")
    p.set_defaults(fn=cmd_replay_day)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
