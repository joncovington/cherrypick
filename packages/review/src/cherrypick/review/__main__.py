"""CLI for the cross-module review.

    python -m cherrypick.review build [--session YYYY-MM-DD] [--final]
    python -m cherrypick.review backfill [--since YYYY-MM-DD]
    python -m cherrypick.review reconcile [--since YYYY-MM-DD]

Read-only over every module's ledger; writes only into review's own home.
"""

from __future__ import annotations

import argparse
import json
import sys

from cherrypick.review import facts as _facts
from cherrypick.review import reconcile as _reconcile


def cmd_build(args) -> dict:
    session = args.session or _facts.today()
    status = _facts.STATUS_FINAL if args.final else _facts.STATUS_PROVISIONAL
    built = _facts.build(session, status=status)
    target = _facts.write(built)
    return {"ok": True, "session": session, "status": status, "written": str(target)}


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
        written.append(session)
    return {"ok": True, "sessions": len(written), "first": written[0] if written else None,
            "last": written[-1] if written else None}


def cmd_reconcile(args) -> dict:
    return _reconcile.run(since=args.since)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build")
    p_build.add_argument("--session", default=None, help="YYYY-MM-DD (default: today)")
    p_build.add_argument("--final", action="store_true", help="mark the set final, not provisional")

    p_backfill = sub.add_parser("backfill")
    p_backfill.add_argument("--since", default=None)

    p_rec = sub.add_parser("reconcile")
    p_rec.add_argument("--since", default=None)

    args = parser.parse_args()
    result = {"build": cmd_build, "backfill": cmd_backfill, "reconcile": cmd_reconcile}[args.command](args)
    json.dump(result, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
