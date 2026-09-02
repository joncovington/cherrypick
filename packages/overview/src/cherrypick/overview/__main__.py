"""CLI for cherrypick-overview. Driven by the supervisor by subprocess, never by import.

Verbs:
    build          Build and write one session's morning fact pack (and its render). Also refreshes
                   the stream request registration, best-effort, so the breadth symbols stay declared.
    render         Re-render one session's markdown from its fact pack.
    score-history  Recompute the deployment score across stored history and report what its zones
                   would have separated. Read-only research over the cache; decides nothing.
    request        (Re)write state/stream_requests/overview.json without building anything.

Every verb prints one JSON envelope to stdout and exits 0 unless the artifact itself could not be
written -- the same posture as review: a missing report is a hole worth seeing, not an alarm worth
waking anything for.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import backtest as _backtest
from . import facts as _facts
from . import render as _render
from . import symbols as _symbols


def _cmd_build(session: str | None) -> dict:
    request = _symbols.register()
    pack = _facts.build(session)
    facts_path = _facts.write(pack)
    render_path = _render.write(pack["session"])
    deployment = pack.get("deployment") or {}
    return {
        "ok": True,
        "session": pack["session"],
        "phase": (pack.get("phase") or {}).get("phase"),
        "deployment_score": deployment.get("score"),
        "deployment_zone": deployment.get("zone"),
        "facts": facts_path,
        "render": render_path,
        "stream_request": request,
    }


def _cmd_render(session: str | None) -> dict:
    session = session or _facts.default_session()
    path = _render.write(session)
    if path is None:
        return {"ok": False, "session": session, "reason": "no fact pack for session"}
    return {"ok": True, "session": session, "render": path}


def _cmd_score_history(session: str | None) -> dict:
    result = _backtest.build(session)
    path = _backtest.write(result)
    zones = result.get("zones") or {}
    return {
        "ok": result["sessions_joined"] > 0,
        "session": result["session"],
        "sessions_scored": result["sessions_scored"],
        "sessions_joined": result["sessions_joined"],
        "score_distribution": result["score_distribution"],
        # The zone summary without the per-session series, which belongs in the artifact.
        "zones": {zone: {k: v for k, v in stats.items() if k != "series"} for zone, stats in zones.items()},
        "artifact": path,
        "reason": None if result["sessions_joined"] else result["unscored_reason"],
    }


def _cmd_request() -> dict:
    path = _symbols.register()
    if path is None:
        return {"ok": False, "reason": "could not write stream request"}
    return {"ok": True, "stream_request": path, "symbols": list(_symbols.ALL_SYMBOLS)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cherrypick.overview", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for verb in ("build", "render", "score-history"):
        p = sub.add_parser(verb)
        p.add_argument("--session", default=None, help="YYYY-MM-DD; default today's ET trading day")
    sub.add_parser("request")
    args = parser.parse_args(argv)

    if args.command == "build":
        result = _cmd_build(args.session)
    elif args.command == "render":
        result = _cmd_render(args.session)
    elif args.command == "score-history":
        result = _cmd_score_history(args.session)
    else:
        result = _cmd_request()
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
