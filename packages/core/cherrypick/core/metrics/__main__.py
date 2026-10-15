"""`python -m cherrypick.core.metrics` — the shared calibration-reading CLI.

A JSON-in/JSON-out bridge over `ledgers.READERS` + `profiles.group_by_tag` +
`calibration_reading`, for a read-only TypeScript surface (the console) that cannot import Python
directly -- same pattern as `cherrypick.core.auth` and `cherrypick.core.calendar`'s own
`__main__.py`. No new metric logic: this is the same normalise-then-summarize path
`orchestrator.report`/`calibrate` already run, pointed at one schema's ledger and grouped by its
own profile tag.

Commands:
    read --db PATH --schema {meic_ic|earnings|fly_book|dc_week|pmcc|curve_vx|bwb_132}
         [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--era ERA]

    summary --db PATH --schema {meic_ic|earnings|fly_book|dc_week|pmcc|curve_vx|bwb_132}
            [--session YYYY-MM-DD] [--symbol SYMBOL] [--arm TAG]
            [--width {day|week|month|year|all}]

`--era` keeps only records stamped with that era (a schema in `ledgers.ERA_SCHEMAS`; any other
schema is refused rather than silently unfiltered). An unstamped row never matches. `ALL`, or no
flag, applies no era filter.

`read` output: {"ok": true, "schema": ..., "n_records": N,
                "groups": {tag: {"reading": <calibration_reading>,
                                 "session_nets": [[session, net], ...],
                                 "trade_nets": [net, ...]}}}
where `tag` is the record's profile, or `<profile>@<experiment_id>` for an advised row stamped
with the experiment it ran under (see cmd_read).

`summary` output: the same normalisation at arbitrary window widths, for the console's
Reports summary page -- every slice it renders from one subprocess, so the cards cannot
disagree. The slices' WHERE is the resolved window (the summary filters, not the CLI):

    {"ok": true, "schema": ...,
     "window":      {"from", "to"}               # the resolved window's session bounds
     "hero":        {"trades", "sessions", "net", "gross", "fees", "wins",
                     "win_pct", "capital", "capital_coverage"},
     "return":      {"value", "coverage"}        # sum(net)/sum(capital), UNROUNDED numerator
     "by_arm":      {tag: {hero-without-capital + "return"}},
     "daily":       [[session, net, trades, wins], ...],
     "facets":      {"sessions": [...], "symbols": [...], "arms": [...]},
     "capture":     {"value", "n"}              # only where max_profit coverage is FULL
     }

`return`/`capture` follow `calibration_reading`'s own honesty rules: `return.value` is None
when no row in scope carries a positive capital (unknown capital is not free capital, exactly
`return_on_capital`'s refusal); the numerator is the UNROUNDED net sum while every displayed
net is the cent-rounded key, because a ratio of rounded halves is not the ratio of the sums.
`capture` reuses `capture_rate` verbatim and is None below FULL `max_profit` coverage -- a
partial-coverage ratio would silently compare a smaller, unstated sub-sample.

Every slice's rows are the SAME rows (one reader pass, one WHERE), so the hero, the by-arm
roll-up and the daily path add up to the same population by construction. All figures are
this module's own math (`calibration_reading`, `capture_rate`); the CLI adds only grouping,
filtering and the single unrounded-numerator ratio.

An unknown schema or an unreadable db returns {"ok": false, "error": ...} rather than a traceback
crossing the subprocess boundary -- the console renders `error` on the card head, per its own
"never silent" data rule.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from cherrypick.core import ledgers
from cherrypick.core.profiles import group_by_tag

from . import calibration_reading, capture_rate, session_nets_dated


def cmd_read(args) -> dict:
    reader = ledgers.READERS.get(ledgers.canonical_schema(args.schema))
    if reader is None:
        return {"ok": False, "error": f"unknown schema {args.schema!r}"}
    try:
        conn = ledgers.connect_ro(args.db)
        try:
            records = reader(conn, args.start, args.end)
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": f"cannot read {args.db!r}: {exc}"}
    era = getattr(args, "era", None)
    if era and era != "ALL":
        if ledgers.canonical_schema(args.schema) not in ledgers.ERA_SCHEMAS:
            why = f"schema {args.schema!r} records carry no era; --era cannot scope it"
            return {"ok": False, "error": why}
        records = [r for r in records if r.get("era") == era]

    def summarize(group: list) -> dict:
        return {
            "reading": calibration_reading(group),
            "session_nets": [list(pair) for pair in session_nets_dated(group)],
            # An unrecorded net (n/r) is no trade's result; the reading's `unscored` counts it.
            "trade_nets": [round(r["net_pnl"], 2) for r in group if r.get("net_pnl") is not None],
        }

    # One group per EXPERIMENT for stamped advised rows (2026-09-16): the `advised:<base>` tag
    # names an arm, and every experiment on that base reuses it in turn, so grouping by tag
    # alone pooled three experiments' rows into one line. Rows carrying an experiment_id group
    # under `<tag>@<experiment_id>`; rows written before the stamp existed (experiment_id None)
    # stay under the bare tag, which the console labels as unstamped history rather than
    # inferring their experiment from dates. Display grouping only: `arm` on the record is
    # untouched, so calibrate/report/verdicts see exactly what they always did.
    for r in records:
        exp = r.get("experiment_id")
        r["group_tag"] = f"{r['arm']}@{exp}" if exp else r["arm"]
    groups = group_by_tag(records, tag_key="group_tag", summarize=summarize)
    return {"ok": True, "schema": args.schema, "era": era, "n_records": len(records), "groups": groups}


# -- summary: the same normalisation, sliced for the console's Reports summary page ------------

WINDOW_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _hero(records: list[dict]) -> dict:
    """The window's headline block over ONE population of records. `net`/`gross`/`fees` are the
    cent-rounded sums (the displayed keys); the ratio in `cmd_summary` divides the UNROUNDED
    sums -- a ratio of rounded halves is not the ratio of the sums. `sessions` counts distinct
    non-empty session stamps; `capital` the cent-rounded sum over the rows that carry a positive
    capital (rounded per row by the schema reader itself, so the total IS the displayed one);
    `capital_coverage` the `n/n` that keeps a partial denominator visible."""
    nets = [r["net_pnl"] for r in records]
    n = len(nets)
    wins = sum(1 for v in nets if v > 0)
    sessions = {r["session"] for r in records if r.get("session")}
    capitals = [r["capital"] for r in records if r.get("capital")]
    return {
        "trades": n,
        "sessions": len(sessions),
        "net": round(sum(nets), 2),
        "gross": round(sum(r["gross_pnl"] for r in records), 2),
        "fees": round(sum(r["cost"] for r in records), 2),
        "wins": wins,
        "win_pct": round(wins / n * 100, 1) if n else None,
        "capital": round(sum(capitals), 2) if capitals else None,
        "capital_coverage": f"{len(capitals)}/{n}" if n else "0/0",
    }


def cmd_summary(args) -> dict:
    """One schema's ledger, sliced for the summary page. The slices' WHERE is the resolved
    window: `--session` scopes exactly to that session; otherwise `--width` ends at (and
    includes) `--session` when given, else at the reader's LATEST session, reaching back
    `WINDOW_DAYS` days (ALL: unbounded, which for a schema with no session stamps would be the
    whole ledger -- same convention as `read`). Filters narrow WITHIN the window; the facets
    ride out to drive the page's own selects from what is in scope, never a hand-kept list."""
    reader = ledgers.READERS.get(ledgers.canonical_schema(args.schema))
    if reader is None:
        return {"ok": False, "error": f"unknown schema {args.schema!r}"}
    try:
        conn = ledgers.connect_ro(args.db)
        try:
            records = reader(conn, args.start, args.end)
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": f"cannot read {args.db!r}: {exc}"}

    sessions = sorted({r["session"] for r in records if r.get("session")})
    symbols = sorted({r["symbol"] for r in records if r.get("symbol")})
    arms = sorted({r["arm"] for r in records if r.get("arm")})
    if not sessions:
        return {
            "ok": True,
            "schema": args.schema,
            "window": {"from": None, "to": None},
            "hero": _hero([]),
            "return": {"value": None, "coverage": "0/0"},
            "by_arm": {},
            "daily": [],
            "facets": {"sessions": [], "symbols": [], "arms": []},
            "capture": {"value": None, "n": 0},
        }

    latest = sessions[-1]
    anchor = args.session if args.session and args.session in sessions else latest
    width = args.width if args.width in WINDOW_DAYS else "all"

    if width == "day":
        in_window = [r for r in records if r.get("session") == anchor]
    elif width == "all":
        in_window = [r for r in records if r.get("session")]
    else:
        from datetime import date, timedelta

        # Inclusive calendar days back from the anchor, so "week" is seven sessions' reach and
        # "day" IS the anchor session alone. The bridge passes --start/--end (the suite epoch's
        # bound, for the schemas that carry no era stamp) and the CLI resolves the window on
        # SESSIONS: the dates that exist, never wall-clock days that may hold none.
        end = date.fromisoformat(anchor)
        floor = end - timedelta(days=WINDOW_DAYS[width] - 1)
        in_window = [
            r for r in records if r.get("session") and floor <= date.fromisoformat(r["session"]) <= end
        ]

    def apply(rs: list[dict]) -> list[dict]:
        out = rs
        if args.symbol:
            out = [r for r in out if r.get("symbol") == args.symbol]
        if args.arm:
            out = [r for r in out if r.get("arm") == args.arm]
        return out

    rows = apply(in_window)
    # ROR's own honesty rule, at the window's width: a schema whose rows carry no positive
    # capital (flies) reads None here, and the page renders "n/r" -- a ratio the population
    # cannot support is not a zero. The numerator is the UNROUNDED net sum; the denominator the
    # cent-rounded capital sum (per-row rounding happens in the schema reader, so the sum IS the
    # displayed one). `return_on_capital` itself would round the ratio to 4dp and reuse its own
    # half-rounded nets; the page's gauge needs the raw figure -- same division, before the
    # display rounding.
    capitals = [r["capital"] for r in rows if r.get("capital")]
    if capitals and sum(capitals) > 0:
        ror = round(sum(r["net_pnl"] for r in rows) / sum(capitals) * 100, 4)
        ret = {"value": ror, "coverage": f"{len(capitals)}/{len(rows)}"}
    else:
        ret = {"value": None, "coverage": f"{len(capitals)}/{len(rows)}" if rows else "0/0"}

    by_arm: dict[str, dict] = {}
    for arm in arms:
        arm_rows = [r for r in rows if r.get("arm") == arm]
        if not arm_rows:
            continue
        h = _hero(arm_rows)
        arm_caps = [r["capital"] for r in arm_rows if r.get("capital")]
        arm_total = sum(arm_caps)
        h["return"] = (
            round(sum(r["net_pnl"] for r in arm_rows) / arm_total * 100, 4) if arm_total > 0 else None
        )
        by_arm[arm] = h

    by_session: dict[str, list[dict]] = {}
    for r in rows:
        s = r.get("session")
        if s:
            by_session.setdefault(s, []).append(r)
    # DICTS, not position: the console's bridge maps them one-to-one into its typed daily rows,
    # so a key added later is additive for every consumer rather than a shape change.
    daily = [
        {
            "session": s,
            "net": round(sum(r["net_pnl"] for r in rs), 2),
            "trades": len(rs),
            "wins": sum(1 for r in rs if r["net_pnl"] > 0),
        }
        for s, rs in sorted(by_session.items())
    ]

    # `capture_rate` verbatim, wrapped in calibration_reading's OWN full-coverage gate (the rule
    # lives at its call site there, not inside capture_rate): below FULL max_profit coverage the
    # value is None with the coverage count still riding out -- a partial-coverage ratio would
    # silently compare a smaller, unstated sub-sample against the group's headline.
    cap = {"value": None, "n": 0}
    if rows:
        mp = sum(1 for r in rows if r.get("max_profit"))
        cap = capture_rate(rows) if mp == len(rows) else {"value": None, "n": mp}

    return {
        "ok": True,
        "schema": args.schema,
        "window": {
            # The resolved window's own bounds -- the sessions in it, not calendar guesses, so a
            # page states the window with the reader's own voice. "day" is [anchor, anchor];
            # wider windows are [first session, anchor]. Null from an empty window (all filters
            # removed every row): the page says so rather than drawing a zero.
            "from": min((r["session"] for r in rows if r.get("session")), default=None),
            "to": max((r["session"] for r in rows if r.get("session")), default=None) or None,
        },
        "hero": _hero(rows),
        "return": ret,
        "by_arm": by_arm,
        "daily": daily,
        "facets": {"sessions": sessions, "symbols": symbols, "arms": arms},
        "capture": cap,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.metrics", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rd = sub.add_parser("read")
    rd.add_argument("--db", required=True)
    rd.add_argument(
        "--schema", required=True, choices=sorted(set(ledgers.READERS) | set(ledgers.SCHEMA_ALIASES))
    )
    rd.add_argument("--start", default=None)
    rd.add_argument("--end", default=None)
    rd.add_argument("--era", default=None, help="one stamped era (ERA_SCHEMAS only), or ALL")
    sm = sub.add_parser("summary")
    sm.add_argument("--db", required=True)
    sm.add_argument(
        "--schema", required=True, choices=sorted(set(ledgers.READERS) | set(ledgers.SCHEMA_ALIASES))
    )
    sm.add_argument("--session", default=None, help="the session to anchor the window on")
    sm.add_argument("--symbol", default=None, help="narrow to one symbol (facets stay whole-ledger)")
    sm.add_argument("--arm", default=None, help="narrow to one arm (facets stay whole-ledger)")
    sm.add_argument(
        "--width",
        default="all",
        choices=[*WINDOW_DAYS, "all"],
        help="how far back from the anchor session the window reaches",
    )
    args = ap.parse_args(argv)
    fn = {"read": cmd_read, "summary": cmd_summary}[args.cmd]
    result = fn(args)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
