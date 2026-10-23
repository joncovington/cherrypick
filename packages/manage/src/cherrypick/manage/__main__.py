"""CLI for the daily position-management evaluation.

    python -m cherrypick.manage evaluate --snapshot PATH [--rules PATH]
    python -m cherrypick.manage reevaluate [--session YYYY-MM-DD] [--rules PATH]
    python -m cherrypick.manage rules [--rules PATH]
    python -m cherrypick.manage check-roll --trade ID --close LEG ... --open LEG ...

`evaluate` reads a broker snapshot (written by the orchestrator's `manage` verb, the only caller that
touches the broker), stores it, and writes the evaluation beside it. `reevaluate` re-runs a stored
snapshot -- the latest by default -- under the current rules, so a changed threshold can be judged on
yesterday's positions. `rules` prints the effective ruleset and anything in the override file that
could not be applied. Every command prints one JSON object.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cherrypick.core.jsonio import write_json_atomic

from cherrypick.manage import evaluate as _evaluate
from cherrypick.manage import paths as _paths
from cherrypick.manage import rules as _rules
from cherrypick.manage import stream_request as _stream_request


def _run(snapshot: dict, rules_path: str | None) -> dict:
    rules, warnings, source = _rules.load(Path(rules_path) if rules_path else None)
    result = _evaluate.evaluate(snapshot, rules, warnings)
    result["rules_source"] = source
    session = result["session"]
    write_json_atomic(_paths.snapshot_path(session), snapshot)
    target = _paths.eval_path(session)
    write_json_atomic(target, result)
    # The held legs, declared to the streamer so the console can mark them live until the next run.
    # Only from the newest evaluation: re-running a past session must not bring its positions back.
    newest = max((p.stem[5:] for p in target.parent.glob("eval-*.json")), default=session)
    if session >= newest:
        _stream_request.register(result)
    counts: dict[str, int] = {}
    for account in result["accounts"]:
        for action, n in account["counts"].items():
            counts[action] = counts.get(action, 0) + n
    return {
        "ok": result["ok"],
        "session": session,
        "written": str(target),
        "counts": counts,
        "rule_warnings": warnings,
        "error": result.get("error"),
    }


def earnings_trades_seen(session: str, folder: Path | None = None) -> dict:
    """The earnings trades the newest stored evaluation BEFORE `session` found, keyed as
    `evaluate.earnings_trade` keys them (underlying and short expiry), with their report. Carried
    into the snapshot so a trade is still known as one the day after its report, when the broker may
    already show next quarter's date; a trade whose short has expired is dropped. Stored in the
    snapshot, so `reevaluate` reads the same record and gives the same answer twice. An evaluation
    with no trades (a day the broker could not be read) or one that cannot be read is passed over, so
    one failed day does not make every earnings trade forget its report."""
    folder = _paths.data_dir() if folder is None else folder
    earlier = sorted((p for p in folder.glob("eval-*.json") if p.stem[5:] < session), reverse=True)
    doc: dict = {}
    for path in earlier:
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(account.get("trades") for account in found.get("accounts") or []):
            doc = found
            break
    seen = {}
    for account in doc.get("accounts") or []:
        for t in account.get("trades") or []:
            et = t.get("earnings_trade")
            if et and et.get("key") and et["key"].split("|", 1)[1] >= session:
                seen[et["key"]] = {
                    "report": et.get("report"),
                    "time": et.get("time_of_day"),
                    "opened": et.get("opened"),
                }
    return seen


def cmd_evaluate(args) -> dict:
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    if "earnings_trades_seen" not in snapshot and snapshot.get("session"):
        snapshot["earnings_trades_seen"] = earnings_trades_seen(snapshot["session"])
    return _run(snapshot, args.rules)


def cmd_reevaluate(args) -> dict:
    if args.session:
        path = _paths.snapshot_path(args.session)
    else:
        found = sorted(_paths.data_dir().glob("snapshot-*.json"))
        if not found:
            return {"ok": False, "error": "no stored snapshot"}
        path = found[-1]
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    return _run(snapshot, args.rules)


def cmd_rules(args) -> dict:
    rules, warnings, source = _rules.load(Path(args.rules) if args.rules else None)
    return {"ok": not warnings, "source": source, "warnings": warnings, "rules": rules}


def cmd_check_roll(args) -> dict:
    """Judge a proposed roll of one trade against the stored snapshot (latest by default)."""
    from datetime import date

    from cherrypick.manage import rollcheck as _rollcheck

    path = _paths.snapshot_path(args.session) if args.session else None
    if path is None:
        found = sorted(_paths.data_dir().glob("snapshot-*.json"))
        if not found:
            return {"ok": False, "error": "no stored snapshot; run `run.py manage` first"}
        path = found[-1]
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    rules, _warnings, _source = _rules.load(Path(args.rules) if args.rules else None)
    result = _evaluate.evaluate(snapshot, rules)
    wanted = args.trade.upper()
    for account in result["accounts"]:
        for trade in account["trades"]:
            if str(trade.get("trade_id", "")).upper() == wanted:
                metrics = (snapshot.get("metrics") or {}).get(trade["underlying"]) or {}
                # Shares held on the name cover its calls, whichever trade the shares were grouped in.
                shares = sum(
                    leg["quantity"]
                    for t in account["trades"]
                    if t["underlying"] == trade["underlying"]
                    for leg in t["legs"]
                    if not leg.get("right") and leg["quantity"] > 0
                )
                verdict = _rollcheck.check(
                    trade,
                    [_rollcheck.parse_leg(x) for x in args.close],
                    [_rollcheck.parse_leg(x) for x in args.open],
                    session=date.fromisoformat(result["session"]),
                    spot=trade["spot"],
                    rules=rules,
                    net_liq=account.get("net_liq"),
                    earnings_date=metrics.get("earnings_date"),
                    shares=shares,
                )
                return {"ok": True, "session": result["session"], "trade": trade["trade_id"], **verdict}
    ids = [t.get("trade_id") for a in result["accounts"] for t in a["trades"]]
    return {"ok": False, "error": f"no trade {args.trade!r} in the {result['session']} snapshot; have {ids}"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_eval = sub.add_parser("evaluate")
    p_eval.add_argument("--snapshot", required=True)
    p_eval.add_argument(
        "--rules", default=None, help="override file (default ~/.cherrypick/config/manage.json)"
    )
    p_re = sub.add_parser("reevaluate")
    p_re.add_argument("--session", default=None)
    p_re.add_argument("--rules", default=None)
    p_rules = sub.add_parser("rules")
    p_rules.add_argument("--rules", default=None)
    p_check = sub.add_parser("check-roll")
    p_check.add_argument("--trade", required=True, help="trade id as the page shows it, e.g. GLD or SOFI#2")
    p_check.add_argument(
        "--close", action="append", default=[], help="a held leg to close, '-2 2026-10-23 425C @0.14'"
    )
    p_check.add_argument(
        "--open", action="append", default=[], help="a leg to open, '-2 2026-11-20 415C @2.57'"
    )
    p_check.add_argument("--session", default=None)
    p_check.add_argument("--rules", default=None)
    args = parser.parse_args()
    commands = {
        "evaluate": cmd_evaluate,
        "reevaluate": cmd_reevaluate,
        "rules": cmd_rules,
        "check-roll": cmd_check_roll,
    }
    try:
        result = commands[args.command](args)
    except (OSError, ValueError) as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    json.dump(result, sys.stdout, indent=2, default=str)
    print()
    sys.exit(0 if result.get("ok", True) is not False else 1)


if __name__ == "__main__":
    main()
