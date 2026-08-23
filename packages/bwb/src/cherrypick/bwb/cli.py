"""Command-line surface for cherrypick-bwb.

Subcommands (all read-only):
    status      open positions, the target expiration
    worksheet   the live per-position worksheet
    fires       per-book add-on fire counts — the real effective sample per arm
    triggers    trigger-tick coverage for a session
    headline    per-book results through the analytics layer
    replay      the read-side threshold replay over bwb_trigger_ticks (see replay.py)

The paper loop's own argv (`python -m cherrypick.bwb.paper_loop --once|--interval|--settle|
--status`) is what the orchestrator drives; this CLI is the human read side.
"""

from __future__ import annotations

import argparse
import json
import pathlib

_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

from cherrypick.core import home as _core_home  # noqa: E402


def load_config(path: str | None = None) -> dict:
    return _core_home.load_module_config("bwb", _PKG_ROOT, path)


def cmd_status(args) -> int:
    from cherrypick.bwb import db, paper_loop

    config = load_config(args.config)
    conn = db.connect(args.db)
    print(
        json.dumps(
            paper_loop.run_status(config, conn, cache_path=paper_loop.stream_cache_path(config)),
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_worksheet(args) -> int:
    from cherrypick.bwb import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "worksheet": analytics.worksheet(conn)}, indent=2, default=str))
    return 0


def cmd_fires(args) -> int:
    from cherrypick.bwb import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "fire_counts": analytics.fire_counts(conn)}, indent=2, default=str))
    return 0


def cmd_triggers(args) -> int:
    from cherrypick.bwb import analytics, clock, db

    conn = db.connect(args.db)
    session = args.date or clock.today_iso()
    print(json.dumps({"ok": True, "coverage": analytics.trigger_coverage(conn, session)}, indent=2, default=str))
    return 0


def cmd_headline(args) -> int:
    from cherrypick.bwb import analytics, db

    conn = db.connect(args.db)
    print(json.dumps({"ok": True, "headline": analytics.headline(conn)}, indent=2, default=str))
    return 0


def cmd_replay(args) -> int:
    from cherrypick.bwb import db, replay

    conn = db.connect(args.db)
    thresholds = json.loads(args.thresholds) if args.thresholds else None
    result = replay.replay_thresholds(
        conn, entry_session=args.entry_session, structure_signature=args.structure_signature, thresholds=thresholds
    )
    if args.validate:
        result["validation"] = replay.validate_against_real(
            conn, entry_session=args.entry_session, structure_signature=args.structure_signature
        )
    print(json.dumps({"ok": True, **result}, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bwb", description="SPX daily-laddered BWB / 1-3-2 paper module")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="open positions, target expiration").set_defaults(func=cmd_status)
    sub.add_parser("worksheet", help="the live per-position worksheet").set_defaults(func=cmd_worksheet)
    sub.add_parser("fires", help="per-book add-on fire counts").set_defaults(func=cmd_fires)
    p_trig = sub.add_parser("triggers", help="trigger-tick coverage for a session")
    p_trig.add_argument("--date")
    p_trig.set_defaults(func=cmd_triggers)
    sub.add_parser("headline", help="per-book results through the analytics layer").set_defaults(func=cmd_headline)
    p_replay = sub.add_parser("replay", help="read-side threshold replay over bwb_trigger_ticks")
    p_replay.add_argument("--entry-session", dest="entry_session", required=True)
    p_replay.add_argument("--structure-signature", dest="structure_signature", required=True)
    p_replay.add_argument("--thresholds", help="JSON overrides for delta_trigger/bounce_pullback/flip_buffer")
    p_replay.add_argument("--validate", action="store_true", help="also validate base thresholds against reality")
    p_replay.set_defaults(func=cmd_replay)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
