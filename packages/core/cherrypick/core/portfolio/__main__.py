"""`python -m cherrypick.core.portfolio` -- the portfolio reading of a chosen set of arms, as JSON.

Command:
    read --manifest JSON --start YYYY-MM-DD [--end YYYY-MM-DD]
         [--period day|week] [--weights equal_risk|booked] [--account DOLLARS]

`--manifest` is a JSON list, one entry per arm, of two kinds:

- **A ledger arm** {"key", "db", "schema", "tag", "era"} (`kind` absent or "ledger"): `tag` the
  metrics group tag (`<arm>` or `<arm>@<experiment>`), `era` the module's stamped era or null. Read
  exactly as `python -m cherrypick.core.metrics read` reads it (`ledgers.READERS`, the era filter,
  `session_nets_dated`), so a session net here is the P&L calendar's cell.
- **A backtest arm** {"key", "kind": "backtest", "path", "arm"}: one arm of a backtest result file
  (a Builder run or a study result, `portfolio.results`), its net per exit session at the verdict
  pricing kind. Its span starts at the run's first session, not its first close. Optional
  "licensed": true marks it licensed where the file itself does not say so (the caller knows the
  folder it came from).

Output: {"ok": true, **portfolio(...), "open_risk": openrisk.open_risk(...), "sources": {key: {kind,
licensed, window}}, "licensed": bool, "failures": [{key, error}]}. An arm whose source cannot be read
is named under `failures` and left out, never read as zeros. `open_risk` is the max loss the arms
hold at once (`portfolio.openrisk`), against `--account` when given. `licensed` is true when any arm
read was derived from licensed vendor data: the reading then stays under ~/.cherrypick like the
result it came from.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from cherrypick.core import ledgers
from cherrypick.core import metrics as _metrics

from . import openrisk as _openrisk
from . import portfolio
from . import results as _results


def _arm_records(entry: dict, start: str, end: str) -> list[dict]:
    schema = ledgers.canonical_schema(entry["schema"])
    reader = ledgers.READERS.get(schema)
    if reader is None:
        raise ValueError(f"unknown schema {entry['schema']!r}")
    conn = ledgers.connect_ro(entry["db"])
    try:
        records = reader(conn, start, end)
    finally:
        conn.close()
    era = entry.get("era")
    if era and era != "ALL" and schema in ledgers.ERA_SCHEMAS:
        records = [r for r in records if r.get("era") == era]
    mine = []
    for r in records:
        exp = r.get("experiment_id")
        if (f"{r['arm']}@{exp}" if exp else r["arm"]) == entry["tag"]:
            mine.append(r)
    return mine


def _backtest_trades(entry: dict, start: str, end: str | None) -> tuple[list[dict], dict]:
    doc = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise _results.ResultError("not a backtest result")
    trades = [
        t
        for t in _results.trades_of(doc, entry["arm"])
        if (s := _results.exit_session(t)) is not None and s >= start and (end is None or s <= end)
    ]
    window = _results.window_of(doc)
    source = {
        "kind": "backtest",
        "licensed": _results.licensed(doc) or bool(entry.get("licensed")),
        "window": list(window) if window else None,
    }
    return trades, source


def cmd_read(args) -> dict:
    try:
        manifest = json.loads(args.manifest)
    except ValueError as exc:
        return {"ok": False, "error": f"manifest is not JSON: {exc}"}
    series, sources, span_from, intervals, failures = {}, {}, {}, {}, []
    for entry in manifest:
        try:
            kind = entry.get("kind", "ledger")
            key = entry["key"]
            if kind == "backtest":
                trades, sources[key] = _backtest_trades(entry, args.start, args.end)
                series[key] = _results.session_nets(trades)
                intervals[key] = [_openrisk.backtest_interval(t) for t in trades]
                window = sources[key]["window"]
                if window:
                    span_from[key] = max(window[0], args.start)
            elif kind == "ledger":
                records = _arm_records(entry, args.start, args.end)
                series[key] = dict(_metrics.session_nets_dated(records))
                intervals[key] = [_openrisk.ledger_interval(r) for r in records]
                sources[key] = {"kind": "ledger", "licensed": False, "window": None}
            else:
                raise ValueError(f"unknown manifest kind {kind!r}")
        except (sqlite3.Error, OSError, ValueError, KeyError) as exc:
            failures.append({"key": entry.get("key"), "error": str(exc)})
    reading = portfolio(
        series, start=args.start, end=args.end, period=args.period, weights=args.weights, span_from=span_from
    )
    account = getattr(args, "account", None)
    return {
        "ok": True,
        **reading,
        "open_risk": _openrisk.open_risk(intervals, account=account),
        "sources": sources,
        "licensed": any(s["licensed"] for s in sources.values()),
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.portfolio", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rd = sub.add_parser("read")
    rd.add_argument("--manifest", required=True)
    rd.add_argument("--start", required=True)
    rd.add_argument("--end", default=None, help="default: the latest close any arm made")
    rd.add_argument("--period", choices=("day", "week"), default="day")
    rd.add_argument("--weights", choices=("equal_risk", "booked"), default="equal_risk")
    rd.add_argument(
        "--account", type=float, default=None, help="dollars: open risk is also read as a share of it"
    )
    args = ap.parse_args(argv)
    result = cmd_read(args)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
