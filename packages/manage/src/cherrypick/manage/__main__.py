"""CLI for the daily position-management evaluation.

    python -m cherrypick.manage evaluate --snapshot PATH [--rules PATH]
    python -m cherrypick.manage reevaluate [--session YYYY-MM-DD] [--rules PATH]
    python -m cherrypick.manage rules [--rules PATH]

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


def _run(snapshot: dict, rules_path: str | None) -> dict:
    rules, warnings, source = _rules.load(Path(rules_path) if rules_path else None)
    result = _evaluate.evaluate(snapshot, rules, warnings)
    result["rules_source"] = source
    session = result["session"]
    write_json_atomic(_paths.snapshot_path(session), snapshot)
    target = _paths.eval_path(session)
    write_json_atomic(target, result)
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


def cmd_evaluate(args) -> dict:
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
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
    args = parser.parse_args()
    try:
        result = {"evaluate": cmd_evaluate, "reevaluate": cmd_reevaluate, "rules": cmd_rules}[args.command](
            args
        )
    except (OSError, ValueError) as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    json.dump(result, sys.stdout, indent=2, default=str)
    print()
    sys.exit(0 if result.get("ok", True) is not False else 1)


if __name__ == "__main__":
    main()
