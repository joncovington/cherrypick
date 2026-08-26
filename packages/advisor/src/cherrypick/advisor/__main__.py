"""CLI for the AI advisor's deterministic half.

    python -m cherrypick.advisor init-db
    python -m cherrypick.advisor factpack --slot {open,am1,am2,midday,pm1,pm2,close,deep}
                                          [--session YYYY-MM-DD]
    python -m cherrypick.advisor admit --slot S --raw <path> [--session YYYY-MM-DD]
    python -m cherrypick.advisor enact [--session YYYY-MM-DD]
    python -m cherrypick.advisor verdicts [--session YYYY-MM-DD]
    python -m cherrypick.advisor status [--session YYYY-MM-DD]
    python -m cherrypick.advisor kill <experiment_id>
    python -m cherrypick.advisor dismiss <proposal_id>

Every verb prints one JSON object on stdout and nothing else: the callers are
``scripts/advisor_checkpoint.py`` and the console, both of which parse it. Errors come back as
``{"ok": false, "error": ...}`` with a non-zero exit code, so a caller can branch on either.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cherrypick.advisor import clock as _clock
from cherrypick.advisor import enact as _enact
from cherrypick.advisor import enactment as _enactment
from cherrypick.advisor import experiments as _experiments
from cherrypick.advisor import factpack as _factpack
from cherrypick.advisor import paths as _paths
from cherrypick.advisor import proposals as _proposals
from cherrypick.advisor import settings as _settings
from cherrypick.advisor import store as _store
from cherrypick.advisor import verdicts as _verdicts


def _session(args) -> str:
    return args.session or _clock.session_today()


def cmd_init_db(args) -> dict[str, Any]:
    conn = _store.connect()
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    ]
    conn.close()
    return {"ok": True, "db": str(_paths.db_path()), "tables": tables,
            "schema_version": _store.SCHEMA_VERSION}


def cmd_factpack(args) -> dict[str, Any]:
    session = _session(args)
    modules = [m.strip() for m in args.modules.split(",")] if args.modules else None
    path = _factpack.write(session, args.slot, modules)
    return {
        "ok": True,
        "session": session,
        "slot": args.slot,
        "pack": str(path),
        "bytes": path.stat().st_size,
    }


def cmd_admit(args) -> dict[str, Any]:
    """Parse a raw reply and record every proposal in it — admitted, queued or rejected.

    A reply that holds no readable JSON is a failed checkpoint, not a crash: the checkpoint row is
    written with the error so the ok rate stays honest, and the raw text is already on disk.
    """
    session = _session(args)
    raw = Path(args.raw).read_text(encoding="utf-8", errors="replace")
    conn = _store.connect()
    try:
        try:
            reply = _proposals.parse(raw)
        except _proposals.ParseError as exc:
            _store.record_checkpoint(conn, session=session, slot=args.slot, model=args.model,
                                     ok=False, error=str(exc), raw_path=args.raw)
            return {"ok": False, "session": session, "slot": args.slot, "error": str(exc)}

        return _experiments.admit_reply(
            conn, session=session, slot=args.slot, reply=reply, model=args.model,
            pack_path=str(_paths.pack_path(session, args.slot)), raw_path=args.raw,
        )
    finally:
        conn.close()


def cmd_enact(args) -> dict[str, Any]:
    session = _session(args)
    modules = [m.strip() for m in args.modules.split(",")] if args.modules else None
    conn = _store.connect()
    try:
        return _enact.run(conn, session, modules=modules)
    finally:
        conn.close()


def cmd_recount(args) -> dict[str, Any]:
    """Re-derive sessions_run for every active experiment from what the loops actually recorded.

    Read-only without --apply, on purpose: this rewrites the denominator every verdict is judged
    against, and it should be read before it is run.
    """
    conn = _store.connect()
    try:
        return _enactment.recount(conn, apply=bool(args.apply))
    finally:
        conn.close()


def cmd_enactment(args) -> dict[str, Any]:
    """Which modules applied the artifact issued for a session, and which did not."""
    return {"ok": True, "session": _session(args), "modules": _enactment.audit(_session(args))}


def cmd_verdicts(args) -> dict[str, Any]:
    """The computed comparison for every experiment that has one — no model involved."""
    conn = _store.connect()
    try:
        bodies = [
            # Each experiment judged by its own module's configured rule, matching what the fact
            # pack shows the model and what expiry stores.
            _verdicts.for_experiment(e, rule=_settings.calibration_rule(e["module"]) or None)
            for e in _store.experiments(conn)
            if e["status"] in (_experiments.STATUS_ACTIVE, _experiments.STATUS_EXPIRED)
        ]
    finally:
        conn.close()
    return {"ok": True, "session": _session(args), "verdicts": bodies}


def cmd_kill(args) -> dict[str, Any]:
    conn = _store.connect()
    try:
        return _experiments.kill(conn, args.experiment_id, session=_clock.session_today(),
                                 reason=args.reason)
    finally:
        conn.close()


def cmd_dismiss(args) -> dict[str, Any]:
    conn = _store.connect()
    try:
        return _experiments.dismiss(conn, int(args.proposal_id))
    finally:
        conn.close()


def cmd_status(args) -> dict[str, Any]:
    conn = _store.connect()
    session = args.session
    checkpoints = _store.rows(
        conn,
        "SELECT session, slot, model, ok, error, created_at FROM checkpoints"
        + (" WHERE session = ?" if session else "")
        + " ORDER BY session DESC, slot LIMIT 20",
        (session,) if session else (),
    )
    active = _store.experiments(conn, status="active")
    queued = _store.experiments(conn, status="queued")
    conn.close()

    def brief(e: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": e["id"],
            "module": e["module"],
            "base_profile": e["base_profile"],
            "params": json.loads(e["params_json"]),
            "sessions_run": e["sessions_run"],
            "expires_after_sessions": e["expires_after_sessions"],
        }

    return {
        "ok": True,
        "db": str(_paths.db_path()),
        "checkpoints": checkpoints,
        "active": [brief(e) for e in active],
        "queued": [brief(e) for e in queued],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cherrypick.advisor", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="create or migrate advisor.db (idempotent)")
    p_init.set_defaults(func=cmd_init_db)

    p_pack = sub.add_parser("factpack", help="build one deterministic fact pack")
    p_pack.add_argument("--slot", required=True, choices=list(_factpack.SLOTS))
    p_pack.add_argument("--session", help="ISO date; defaults to today (ET)")
    p_pack.add_argument("--modules", help="csv subset of meic,flies,earnings")
    p_pack.set_defaults(func=cmd_factpack)

    p_admit = sub.add_parser("admit", help="parse a raw model reply and record its proposals")
    p_admit.add_argument("--slot", required=True, choices=list(_factpack.SLOTS))
    p_admit.add_argument("--session", help="ISO date; defaults to today (ET)")
    p_admit.add_argument("--raw", required=True, help="path to the raw reply text")
    p_admit.add_argument("--model", help="which model produced it (recorded, never used to decide)")
    p_admit.set_defaults(func=cmd_admit)

    p_enact = sub.add_parser("enact", help="issue the next session's advice for active experiments")
    p_enact.add_argument("--session", help="ISO date; defaults to today (ET)")
    p_enact.add_argument("--modules", help="csv subset of meic,flies,earnings")
    p_enact.set_defaults(func=cmd_enact)

    p_recount = sub.add_parser(
        "recount", help="re-derive sessions_run from what the loops recorded (read-only by default)")
    p_recount.add_argument("--apply", action="store_true", help="write the corrected counts")
    p_recount.set_defaults(func=cmd_recount)

    p_enacted = sub.add_parser(
        "enactment", help="did each module apply the artifact issued for a session?")
    p_enacted.add_argument("--session", help="ISO date; defaults to today (ET)")
    p_enacted.set_defaults(func=cmd_enactment)

    p_verdicts = sub.add_parser("verdicts", help="computed advised-vs-base comparisons")
    p_verdicts.add_argument("--session", help="ISO date; defaults to today (ET)")
    p_verdicts.set_defaults(func=cmd_verdicts)

    p_kill = sub.add_parser("kill", help="stop an experiment tonight")
    p_kill.add_argument("experiment_id")
    p_kill.add_argument("--reason", default="killed by user",
                        help="journaled with the kill — a retired stream keeps its written verdict")
    p_kill.set_defaults(func=cmd_kill)

    p_dismiss = sub.add_parser("dismiss", help="mark a proposal dismissed by the user")
    p_dismiss.add_argument("proposal_id")
    p_dismiss.set_defaults(func=cmd_dismiss)

    p_status = sub.add_parser("status", help="checkpoints, experiments, and what is queued")
    p_status.add_argument("--session", help="ISO date; defaults to every recent session")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except Exception as exc:  # one JSON contract on stdout, success or failure
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    sys.exit(main())
