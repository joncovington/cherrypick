"""CLI for the daily position-management evaluation.

    python -m cherrypick.manage evaluate --snapshot PATH [--rules PATH]
    python -m cherrypick.manage remark --overlay PATH [--rules PATH]
    python -m cherrypick.manage reevaluate [--session YYYY-MM-DD] [--rules PATH]
    python -m cherrypick.manage rules [--rules PATH]
    python -m cherrypick.manage check-roll --trade ID --close LEG ... --open LEG ...

`evaluate` reads a broker snapshot (written by the orchestrator's `manage` verb, the only caller that
touches the broker), stores it, and writes the evaluation beside it. `remark` moves the session's stored
snapshot to an overlay of newer quotes (the orchestrator's `manage-marks`, once a minute: `remark.py`),
evaluates that, and stores the overlay, never the snapshot. `reevaluate` re-runs a stored snapshot --
the latest by default -- under the current rules, moved to its stored overlay when one stands, so a
changed threshold can be judged on yesterday's positions. `rules` prints the effective ruleset and
anything in the override file that could not be applied. Every command prints one JSON object.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cherrypick.core.jsonio import write_json_atomic

from cherrypick.manage import entrydelta as _entrydelta
from cherrypick.manage import evaluate as _evaluate
from cherrypick.manage import paths as _paths
from cherrypick.manage import remark as _remark
from cherrypick.manage import rules as _rules
from cherrypick.manage import stream_request as _stream_request
from cherrypick.manage import tradeids as _tradeids


def _run(snapshot: dict, rules_path: str | None, *, store: bool = True) -> dict:
    """Evaluate `snapshot` and write the evaluation. `store` keeps the snapshot itself too, as a new
    full snapshot, which retires the session's overlay (it was read against the one it replaces)."""
    rules, warnings, source = _rules.load(Path(rules_path) if rules_path else None)
    result = _evaluate.evaluate(snapshot, rules, warnings)
    result["rules_source"] = source
    result["remark"] = snapshot.get("remark")
    session = result["session"]
    if store:
        write_json_atomic(_paths.snapshot_path(session), snapshot)
        _paths.marks_path(session).unlink(missing_ok=True)
        # Only a full snapshot adds to the trade IDs: a re-mark or a re-run reads the ones it carries.
        if result.get("ok") and "trade_ids" in snapshot:
            trades = [t for a in result["accounts"] for t in a["trades"]]
            _tradeids.record(_paths.trade_ids_path(), snapshot["trade_ids"], trades, session)
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
    # Only a full snapshot records a delta at entry: it is the one whose underlying price is read
    # after the fill. A remark or a re-run reads the records the stored snapshot already carries.
    if "entry_deltas" not in snapshot and snapshot.get("session"):
        rules, _, _ = _rules.load(Path(args.rules) if args.rules else None)
        snapshot["entry_deltas"] = _entrydelta.record(snapshot, rules, _paths.entry_deltas_path())
    # The trade IDs as they stand, carried so a re-run of this snapshot assigns the same ones.
    if "trade_ids" not in snapshot:
        snapshot["trade_ids"], _ = _tradeids.load(_paths.trade_ids_path())
    return _run(snapshot, args.rules)


def _stored(session: str | None) -> dict | None:
    """A session's stored snapshot (the latest by default), moved to its stored overlay when one was
    read against it; None when there is no snapshot."""
    if session:
        path = _paths.snapshot_path(session)
    else:
        found = sorted(_paths.data_dir().glob("snapshot-*.json"))
        if not found:
            return None
        path = found[-1]
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    marks = _paths.marks_path(snapshot["session"])
    if marks.exists():
        overlay = json.loads(marks.read_text(encoding="utf-8"))
        if overlay.get("base_generated_at") == snapshot.get("generated_at"):
            return _remark.apply(snapshot, overlay)
    return snapshot


def cmd_remark(args) -> dict:
    """The session's stored snapshot moved to the overlay's quotes, evaluated; the overlay is stored
    beside the evaluation, the snapshot is left as it is."""
    overlay = json.loads(Path(args.overlay).read_text(encoding="utf-8"))
    base = _paths.snapshot_path(str(overlay.get("session")))
    if not base.exists():
        return {"ok": False, "error": f"no stored snapshot for {overlay.get('session')}"}
    snapshot = json.loads(base.read_text(encoding="utf-8"))
    try:
        moved = _remark.apply(snapshot, overlay)
    except _remark.StaleOverlay as exc:
        return {"ok": False, "stale": True, "error": str(exc)}
    out = _run(moved, args.rules, store=False)
    write_json_atomic(_paths.marks_path(snapshot["session"]), overlay)
    return {**out, "remark": moved["remark"]}


def cmd_reevaluate(args) -> dict:
    snapshot = _stored(args.session)
    if snapshot is None:
        return {"ok": False, "error": "no stored snapshot"}
    return _run(snapshot, args.rules, store=False)


def cmd_rules(args) -> dict:
    rules, warnings, source = _rules.load(Path(args.rules) if args.rules else None)
    return {"ok": not warnings, "source": source, "warnings": warnings, "rules": rules}


def cmd_check_roll(args) -> dict:
    """Judge a proposed roll of one trade against the stored snapshot (latest by default)."""
    from datetime import date

    from cherrypick.manage import rollcheck as _rollcheck

    snapshot = _stored(args.session)
    if snapshot is None:
        return {"ok": False, "error": "no stored snapshot; run `run.py manage` first"}
    rules, _warnings, _source = _rules.load(Path(args.rules) if args.rules else None)
    result = _evaluate.evaluate(snapshot, rules)
    wanted = args.trade.upper()
    # A trade by its stable ID; a bare name stands for its one trade when it has only one.
    on_name = [
        t for a in result["accounts"] for t in a["trades"] if str(t.get("underlying", "")).upper() == wanted
    ]
    if len(on_name) == 1:
        wanted = str(on_name[0].get("trade_id", "")).upper()
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


def cmd_model(args) -> dict:
    """Model proposed legs on a name against the stored snapshot (latest by default): the payoff
    before and after, the legs' greeks and worst case, and the caps it would use. Sends nothing."""
    from cherrypick.manage import model as _model

    snapshot = _stored(args.session)
    if snapshot is None:
        return {"ok": False, "error": "no stored snapshot; run `run.py manage` first"}
    rules, _warnings, _source = _rules.load(Path(args.rules) if args.rules else None)
    result = _evaluate.evaluate(snapshot, rules)
    return _model.model(result, snapshot, rules, args.underlying, args.leg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_eval = sub.add_parser("evaluate")
    p_eval.add_argument("--snapshot", required=True)
    p_eval.add_argument(
        "--rules", default=None, help="override file (default ~/.cherrypick/config/manage.json)"
    )
    p_mark = sub.add_parser("remark")
    p_mark.add_argument(
        "--overlay", required=True, help="the quotes, as the orchestrator's manage-marks read them"
    )
    p_mark.add_argument("--rules", default=None)
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
    p_model = sub.add_parser("model")
    p_model.add_argument("--underlying", required=True, help="the name, as the page shows it, e.g. SOXL")
    p_model.add_argument(
        "--leg", action="append", default=[], help="a leg to add, '-1 2026-12-18 46P @ 1.05' (price optional)"
    )
    p_model.add_argument("--session", default=None)
    p_model.add_argument("--rules", default=None)
    args = parser.parse_args()
    commands = {
        "evaluate": cmd_evaluate,
        "remark": cmd_remark,
        "reevaluate": cmd_reevaluate,
        "rules": cmd_rules,
        "check-roll": cmd_check_roll,
        "model": cmd_model,
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
