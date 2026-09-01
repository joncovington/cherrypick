"""CLI for the cross-module review.

    python -m cherrypick.review build [--session YYYY-MM-DD] [--final]
    python -m cherrypick.review backfill [--since YYYY-MM-DD]
    python -m cherrypick.review reconcile [--since YYYY-MM-DD]

`build` defaults to today and writes a `provisional` set. `build --final` defaults to the PRIOR
trading day instead, because that is the session a final pass closes out — it runs the next morning,
once earnings has settled overnight.

Read-only over every module's ledger; writes only into review's own home.
"""

from __future__ import annotations

import argparse
import json
import sys

from cherrypick.review import facts as _facts
from cherrypick.review import reconcile as _reconcile
from cherrypick.review import render as _render


def cmd_build(args) -> dict:
    # `--final` closes out the PRIOR session, never today's — see facts.session_to_finalise for the
    # contract and for what defaulting to today cost. An explicit --session always wins.
    if args.session:
        session = args.session
    elif args.final:
        session = _facts.session_to_finalise()
    else:
        session = _facts.today()
    status = _facts.STATUS_FINAL if args.final else _facts.STATUS_PROVISIONAL
    built = _facts.build(session, status=status)
    target = _facts.write(built)
    rendered = _render.write(session)
    return {
        "ok": True,
        "session": session,
        "status": status,
        "written": str(target),
        "rendered": str(rendered),
    }


def cmd_backfill(args) -> dict:
    sessions: set[str] = set()
    for module in _facts.MODULES:
        sessions.update(_facts.sessions_with_activity(module))
    if args.since:
        sessions = {s for s in sessions if s >= args.since}
    written = []
    for session in sorted(sessions):
        # Backfilled sessions are final by definition: everything that was going to settle has.
        _facts.write(_facts.build(session, status=_facts.STATUS_FINAL))
        _render.write(session)
        written.append(session)
    return {
        "ok": True,
        "sessions": len(written),
        "first": written[0] if written else None,
        "last": written[-1] if written else None,
    }


def cmd_render(args) -> dict:
    session = args.session or _facts.today()
    return {"ok": True, "session": session, "rendered": str(_render.write(session))}


def cmd_reconcile(args) -> dict:
    return _reconcile.run(since=args.since)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build")
    p_build.add_argument(
        "--session", default=None, help="YYYY-MM-DD (default: today, or the prior trading day with --final)"
    )
    p_build.add_argument(
        "--final",
        action="store_true",
        help="mark the set final, not provisional; closes out the PRIOR session",
    )

    p_backfill = sub.add_parser("backfill")
    p_backfill.add_argument("--since", default=None)

    p_rec = sub.add_parser("reconcile")
    p_rec.add_argument("--since", default=None)

    p_render = sub.add_parser("render")
    p_render.add_argument("--session", default=None)

    args = parser.parse_args()
    result = {"build": cmd_build, "backfill": cmd_backfill, "reconcile": cmd_reconcile, "render": cmd_render}[
        args.command
    ](args)
    json.dump(result, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
