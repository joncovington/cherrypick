"""Command-line surface for cherrypick-bwb.

Subcommands (all read-only):
    status      open positions, the target expiration
    worksheet   the live per-position worksheet
    fires       per-arm add-on fire counts — the real effective sample per arm
    triggers    trigger-tick coverage for a session
    headline    per-arm results through the analytics layer
    replay      the read-side threshold replay over bwb_trigger_ticks (see replay.py)
    addon-replay  the add-on scored as its own trade, with no fly (see addon_replay.py)
    iv-premium  implied vol at entry against realised vol to expiry, per window (see iv_premium.py)
    regime-split  each arm's closed results cut by the VIX9D/VIX gate at entry (see entry_regime.py)

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
    print(
        json.dumps({"ok": True, "coverage": analytics.trigger_coverage(conn, session)}, indent=2, default=str)
    )
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
        conn,
        entry_session=args.entry_session,
        structure_signature=args.structure_signature,
        thresholds=thresholds,
    )
    if args.validate:
        result["validation"] = replay.validate_against_real(
            conn, entry_session=args.entry_session, structure_signature=args.structure_signature
        )
    print(json.dumps({"ok": True, **result}, indent=2, default=str))
    return 0


def cmd_addon_replay(args) -> int:
    from cherrypick.bwb import addon_replay, db

    conn = db.connect(args.db)
    result = addon_replay.run(conn, load_config(args.config))
    if not args.trades:
        result.pop("trades")
    print(json.dumps({"ok": True, **result}, indent=2, default=str))
    return 0


def cmd_regime_split(args) -> int:
    from cherrypick.bwb import db, entry_regime

    conn = db.connect(args.db)
    result = entry_regime.split(conn, vix9d_vix_max=args.vix9d_vix_max)
    print(json.dumps({"ok": True, **result}, indent=2, default=str))
    return 0


def cmd_iv_premium(args) -> int:
    from cherrypick.bwb import db, iv_premium

    conn = db.connect(args.db)
    result = iv_premium.run(conn, sample_seconds=args.sample_seconds)
    if not args.windows:
        result.pop("windows")
    print(json.dumps({"ok": True, **result}, indent=2, default=str))
    return 0


def addon_missed_plan(conn, position_ids: list[str]) -> list[dict]:
    """For each named position, whether it can be marked missed and why not. Pure over the ledger."""
    out = []
    for pid in position_ids:
        row = conn.execute("SELECT * FROM bwb_positions WHERE position_id = ?", (pid,)).fetchone()
        if row is None:
            out.append({"position_id": pid, "ok": False, "reason": "no such position"})
        elif row["status"] != "open":
            out.append({"position_id": pid, "ok": False, "reason": f"status {row['status']}"})
        elif row["addon_fired_at"]:
            out.append({"position_id": pid, "ok": False, "reason": "add-on already fired"})
        elif row["addon_missed_at"]:
            out.append({"position_id": pid, "ok": False, "reason": "already marked missed"})
        else:
            out.append({"position_id": pid, "ok": True, "arm": row["arm"], "symbol": row["symbol"]})
    return out


def cmd_addon_missed(args) -> int:
    """Skip a position's add-on for good, with the reason -- for a trigger that may have been met
    while it went unmeasured. Dry run unless --apply."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from cherrypick.bwb import db

    conn = db.connect(args.db)
    plan = addon_missed_plan(conn, args.position_id)
    if not args.apply or not all(p["ok"] for p in plan):
        print(json.dumps({"ok": all(p["ok"] for p in plan), "applied": False, "plan": plan}, indent=2))
        return 0 if all(p["ok"] for p in plan) else 1
    now = datetime.now(ZoneInfo("America/New_York"))
    stamp = now.isoformat(timespec="seconds")
    for p in plan:
        db.save_position(
            conn,
            {"position_id": p["position_id"], "addon_missed_at": stamp, "addon_missed_reason": args.reason},
        )
        db.record_decision(
            conn,
            trade_date=now.date().isoformat(),
            arm=p["arm"],
            symbol=p["symbol"],
            mode="addon",
            reason="addon_missed",
            accepted=False,
            detail=f"{p['position_id']}: {args.reason}",
        )
    print(json.dumps({"ok": True, "applied": True, "marked": [p["position_id"] for p in plan]}, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bwb", description="SPX daily-laddered BWB / 1-3-2 paper module")
    ap.add_argument("--config")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="open positions, target expiration").set_defaults(func=cmd_status)
    sub.add_parser("worksheet", help="the live per-position worksheet").set_defaults(func=cmd_worksheet)
    sub.add_parser("fires", help="per-arm add-on fire counts").set_defaults(func=cmd_fires)
    p_missed = sub.add_parser(
        "addon-missed", help="skip positions' add-ons for good, with the reason (dry run unless --apply)"
    )
    p_missed.add_argument("--position-id", action="append", required=True)
    p_missed.add_argument("--reason", required=True)
    p_missed.add_argument("--apply", action="store_true")
    p_missed.set_defaults(func=cmd_addon_missed)
    p_trig = sub.add_parser("triggers", help="trigger-tick coverage for a session")
    p_trig.add_argument("--date")
    p_trig.set_defaults(func=cmd_triggers)
    sub.add_parser("headline", help="per-arm results through the analytics layer").set_defaults(
        func=cmd_headline
    )
    p_replay = sub.add_parser("replay", help="read-side threshold replay over bwb_trigger_ticks")
    p_replay.add_argument("--entry-session", dest="entry_session", required=True)
    p_replay.add_argument("--structure-signature", dest="structure_signature", required=True)
    p_replay.add_argument("--thresholds", help="JSON overrides for delta_trigger/bounce_pullback/flip_buffer")
    p_replay.add_argument(
        "--validate", action="store_true", help="also validate base thresholds against reality"
    )
    p_replay.set_defaults(func=cmd_replay)
    p_addon = sub.add_parser("addon-replay", help="the add-on scored as its own trade, with no fly")
    p_addon.add_argument("--trades", action="store_true", help="include every trade, not just the totals")
    p_addon.set_defaults(func=cmd_addon_replay)
    p_ivp = sub.add_parser("iv-premium", help="implied vol at entry against realised vol to expiry")
    p_ivp.add_argument("--windows", action="store_true", help="include every window, not just the summary")
    p_ivp.add_argument("--sample-seconds", dest="sample_seconds", type=float, default=300)
    p_ivp.set_defaults(func=cmd_iv_premium)
    p_reg = sub.add_parser(
        "regime-split", help="each arm's closed results cut by the VIX9D/VIX gate at entry"
    )
    p_reg.add_argument("--vix9d-vix-max", dest="vix9d_vix_max", type=float, default=1.0)
    p_reg.set_defaults(func=cmd_regime_split)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
