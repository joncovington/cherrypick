"""`python -m cherrypick.core.compare` — every arm beside its control, as JSON for the console.

    python -m cherrypick.core.compare --json [--scope era|all] [--session YYYY-MM-DD] [--module M ...]

Without `--json` it prints a plain table. Read-only over every ledger and the advisor's store. A
failure never crosses the subprocess boundary as a traceback: it is `{"ok": false, "error": ...}`
on stdout and exit code 1, so the console can say what broke.
"""

from __future__ import annotations

import argparse
import json
import sys

from cherrypick.core import compare as _compare


def _money(x) -> str:
    return "—" if x is None else f"{x:+,.2f}"


def _table(doc: dict) -> str:
    lines = [f"compare  session {doc['session']}  scope {doc['scope']}  ({doc['level']:.0%} intervals)"]
    for m in doc["modules"]:
        era = m.get("era") or {}
        head = f"\n{m['module']} [{m['status']}]"
        if m["status"] != "ok":
            lines.append(f"{head} {m.get('reason') or ''}")
            continue
        if doc["scope"] == "era":
            head += f"  era from {era.get('era_stamp') or era.get('start') or 'the first row'}"
        lines.append(head)
        for a in m["arms"]:
            vs = a["vs_control"]
            pair = ""
            if vs and vs["sessions"]:
                ci = vs["ci"] or [None, None]
                pair = (
                    f"  vs {a['control']}: {_money(vs['mean_diff'])}/session over {vs['sessions']}"
                    f" [{_money(ci[0])}, {_money(ci[1])}] sign p {vs['sign_test_p']}"
                    f" more {vs['sessions_to_exclude_zero']}"
                )
            lines.append(
                f"  {a['arm']:<42} {a['trades']:>5} tr {a['sessions']:>3} s  net {_money(a['net']):>13}"
                f"  dd {a['max_drawdown']:>10,.2f}{pair}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m cherrypick.core.compare", description=__doc__.splitlines()[0]
    )
    ap.add_argument("--json", action="store_true", help="print the JSON document")
    ap.add_argument("--scope", choices=("era", "all"), default="era")
    ap.add_argument("--session", help="as of this session (default today)")
    ap.add_argument("--module", action="append", choices=sorted(_compare.MODULE_SCHEMAS))
    args = ap.parse_args(argv)
    try:
        doc = _compare.compare(args.module, session=args.session, scope=args.scope)
    except Exception as exc:  # noqa: BLE001 -- a bridge reports, it never tracebacks
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    if args.json:
        json.dump({"ok": True, **doc}, sys.stdout, separators=(",", ":"), default=str)
        sys.stdout.write("\n")
    else:
        print(_table(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
