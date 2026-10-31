"""Item 31, the earnings module's paper rule (docs/edge-hypotheses-plan.md): the event list and the
coverage pilot. The study itself is not built here yet.

    python scripts/earnings_item31.py plan [--host H] [--port P] [--out FILE]
    python scripts/earnings_item31.py pilot [--out-dir DIR]

`plan` reads Dolt's `earnings_calendar` for the names the suite's liquidity verdict calls `tradable`
and writes the event list: reports with a stated timing ('After market close' or 'Before market
open') from 2020-01-22 to 2026-07-14, each with its entry session (an after-close report enters that
day, a pre-market report the session before). Counts only are printed.

`pilot` is the coverage test the declaration runs before anything else: 30 events a year for 2020-2025
drawn by a seeded hash of name and date (never by outcome), and for each the entry window (15:35 to
15:50 ET at 5 minutes) and one later session (09:40 to 15:55). It writes the request file for the
research queue (`fetch-intraday-chains`, packages/backtest) and, once the store holds the answers,
the coverage report and the basis decision. It reads row counts and whether each required bar came
back with a quote, a delta, an implied volatility and the underlying's price present. **It never
computes a price into a result, a P&L or a return**: a quote is tested for being there, never read.

**The basis rule, as declared:** if at least 90% of the pilot's requests are complete in every year,
the intraday basis is used for the whole study; if not, the end-of-day fallback is, for the whole
study. A request is complete when the vendor answered with rows and every required bar has at least
one contract with a bid, an ask, a delta, a positive implied volatility and an underlying price.

Choices the declaration leaves open, made here and stated:

- *The expiry* of a pilot request is the Friday on or after the reaction date (the Thursday when that
  Friday is a holiday): the weekly the module's own rule reaches for. The universe is the names with
  weekly options, so a request that comes back empty is a coverage miss and counts as one.
- *The later session* is the trading day after the entry session (the reaction session for both
  timings), asked at 09:40 to 15:55 at 5 minutes, ending 5 minutes before an early close.
- *Required bars:* the entry request's 15:35, 15:40 (the entry bar) and 15:50; the later request's
  first and last bar of its window.
- *Early-close entry sessions* (13:00 ET) have no 15:40 bar and are left out of the draw, counted.
- *Names* are the verdict's `names` block (158 on 2026-10-30), not its `reference` funds.
- *Windows* are cut by the report's date.

Everything written goes under the backtest data dir (results/research/item31/), never into the repo:
the inputs and everything derived from them are licensed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal

FIRST_REPORT = date(2020, 1, 22)
LAST_REPORT = date(2026, 7, 14)
DESIGN_END = date(2023, 12, 31)
AFTER_CLOSE, BEFORE_OPEN = "After market close", "Before market open"
TIMINGS = {AFTER_CLOSE: "after_close", BEFORE_OPEN: "before_open"}

SEED = 31
PILOT_YEARS = tuple(range(2020, 2026))
PILOT_PER_YEAR = 30
THRESHOLD = 0.90
INTERVAL = "5m"
ENTRY_WINDOW = ("15:35", "15:50")
ENTRY_BARS = ("15:35", "15:40", "15:50")
LATER_START, LATER_END = "09:40", "15:55"
#: A refusal that says nothing about the vendor's coverage (the run could not ask): the request is
#: not answered, the decision waits for a re-run.
NOT_AN_ANSWER = ("unauthenticated", "session_in_use", "library_missing", "no_key", "transport_not_built",
                 "unknown_vendor")  # fmt: skip


# ------------------------------------------------------------------------------------------------
# Pure: the events.


def entry_session(report: date, timing: str) -> date:
    """After the close: that day. Before the open: the session before."""
    return report if timing == "after_close" else cal.previous_trading_day(report)


def reaction_session(report: date, timing: str) -> date:
    """The first session that trades the news: the next one after an after-close report, the report
    date itself before the open."""
    return cal.next_trading_day(report) if timing == "after_close" else report


def event_row(symbol: str, report: date, timing: str) -> dict:
    entry = entry_session(report, timing)
    return {
        "symbol": symbol,
        "report_date": report.isoformat(),
        "timing": timing,
        "entry_session": entry.isoformat(),
        "reaction_session": reaction_session(report, timing).isoformat(),
        "early_close_entry": cal.is_early_close(entry),
    }


def window_of(report: date) -> str:
    return "design" if report <= DESIGN_END else "holdout"


def summarise(events: list[dict], no_timing: int) -> dict:
    """Counts only: by window and timing, by year, and what is dropped or set aside."""
    by = Counter((window_of(date.fromisoformat(e["report_date"])), e["timing"]) for e in events)
    years = Counter(e["report_date"][:4] for e in events)
    return {
        "events": len(events),
        "names": len({e["symbol"] for e in events}),
        "design": {t: by[("design", t)] for t in TIMINGS.values()},
        "holdout": {t: by[("holdout", t)] for t in TIMINGS.values()},
        "by_year": dict(sorted(years.items())),
        "no_stated_timing_dropped": no_timing,
        "early_close_entry": sum(1 for e in events if e["early_close_entry"]),
    }


# ------------------------------------------------------------------------------------------------
# Pure: the pilot draw and its requests.


def draw_key(seed: int, symbol: str, report_date: str) -> str:
    """The rank of an event in the draw: a hash of the seed, the name and the date, nothing else.
    No price, outcome or later field reaches it."""
    return hashlib.sha256(f"{seed}|{symbol}|{report_date}".encode()).hexdigest()


def pilot_draw(events: list[dict], *, seed: int = SEED, per_year: int = PILOT_PER_YEAR,
               years: tuple[int, ...] = PILOT_YEARS) -> list[dict]:  # fmt: skip
    """`per_year` events from each year by report date: the lowest hashes of (seed, name, date).
    An early-close entry session is not in the pool (no bar at 15:40). A year with fewer events
    gives all it has."""
    pool: dict[int, list[dict]] = defaultdict(list)
    for e in events:
        y = int(e["report_date"][:4])
        if y in years and not e["early_close_entry"]:
            pool[y].append(e)
    out: list[dict] = []
    for y in sorted(pool):
        ranked = sorted(pool[y], key=lambda e: draw_key(seed, e["symbol"], e["report_date"]))
        out.extend(sorted(ranked[:per_year], key=lambda e: (e["report_date"], e["symbol"])))
    return out


def weekly_expiry(reaction: date) -> date:
    """The Friday on or after the reaction session, the Thursday when that Friday is a holiday."""
    fri = reaction + timedelta(days=(4 - reaction.weekday()) % 7)
    return fri if cal.is_trading_day(fri) else cal.previous_trading_day(fri)


def later_window(session: date) -> tuple[str, str]:
    """The later session's window: 09:40 to 15:55, ending 5 minutes before an early close."""
    if cal.is_early_close(session):
        hh, mm = cal.session_close_hhmm(session).split(":")
        end = datetime(2000, 1, 1, int(hh), int(mm)) - timedelta(minutes=5)
        return LATER_START, end.strftime("%H:%M")
    return LATER_START, LATER_END


def requests_for(event: dict) -> list[dict]:
    """The pilot's two requests for an event: the entry window, and the next session's day. Each
    carries the bars it must return to count as complete."""
    expiry = weekly_expiry(date.fromisoformat(event["reaction_session"])).isoformat()
    later = cal.next_trading_day(date.fromisoformat(event["entry_session"]))
    start, end = later_window(later)
    label = f"{event['symbol']} {event['report_date']}"
    base = {"symbol": event["symbol"], "expiration": expiry, "interval": INTERVAL,
            "event": label, "year": int(event["report_date"][:4])}  # fmt: skip
    return [
        {**base, "kind": "entry", "session": event["entry_session"], "start": ENTRY_WINDOW[0],
         "end": ENTRY_WINDOW[1], "required": list(ENTRY_BARS)},
        {**base, "kind": "later", "session": later.isoformat(), "start": start, "end": end,
         "required": [start, end]},
    ]  # fmt: skip


def pilot_requests(events: list[dict], **kw) -> list[dict]:
    return [r for e in pilot_draw(events, **kw) for r in requests_for(e)]


# ------------------------------------------------------------------------------------------------
# Pure: the basis rule.


def basis(per_year: dict, threshold: float = THRESHOLD) -> str:
    """`intraday` when every year has requests and at least `threshold` of them are complete, else
    `end_of_day`. `per_year[year]` is {"requests": n, "complete": k}. One basis for all years."""
    if not per_year:
        return "end_of_day"
    for counts in per_year.values():
        if counts["requests"] == 0 or counts["complete"] / counts["requests"] < threshold:
            return "end_of_day"
    return "intraday"


# ------------------------------------------------------------------------------------------------
# The coverage report: counts and presence, never a price.

_BAR_PRESENT = (
    "SELECT 1 FROM intraday_chain_quotes WHERE symbol = ? AND session = ? AND expiration = ? AND bar = ? "
    "AND bid IS NOT NULL AND ask IS NOT NULL AND delta IS NOT NULL AND iv IS NOT NULL AND iv > 0 "
    "AND underlying IS NOT NULL LIMIT 1"
)


def judge(conn: sqlite3.Connection, req: dict) -> dict:
    """What became of one request: its status, rows and bars as recorded, and for an answered one the
    required bars that did not come back whole. Presence only."""
    sym, day, exp = req["symbol"].upper(), req["session"], req["expiration"]
    row = conn.execute(
        "SELECT status, rows, bars, detail FROM intraday_chain_requests WHERE symbol = ? AND session = ? "
        'AND expiration = ? AND start = ? AND "end" = ? AND interval = ?',
        (sym, day, exp, req["start"], req["end"], req["interval"]),
    ).fetchone()
    if row is None:
        return {"state": "unanswered", "rows": 0, "bars": 0}
    status, rows, bars, detail = row
    if status == "refused":
        reason = (detail or "").split(":")[0] or "unknown"
        state = "unanswered" if reason in NOT_AN_ANSWER else "refused"
        return {"state": state, "rows": 0, "bars": 0, "reason": reason}
    if status == "empty":
        return {"state": "empty", "rows": 0, "bars": 0}
    missing = [
        b for b in req["required"] if conn.execute(_BAR_PRESENT, (sym, day, exp, b)).fetchone() is None
    ]
    return {"state": "incomplete" if missing else "complete", "rows": rows, "bars": bars, "missing": missing}


def coverage(conn: sqlite3.Connection, requests: list[dict]) -> dict:
    """The pilot's coverage by year and kind, the decision, and nothing priced."""
    years: dict[int, dict] = {}
    missing_bars: Counter = Counter()
    reasons: Counter = Counter()
    for req in requests:
        j = judge(conn, req)
        y = years.setdefault(
            req["year"],
            {"requests": 0, "complete": 0, "incomplete": 0, "empty": 0, "refused": 0, "unanswered": 0,
             "rows": 0, "bars": 0, "by_kind": {}},
        )  # fmt: skip
        y["requests"] += 1
        y[j["state"]] += 1
        y["rows"] += j["rows"]
        y["bars"] += j["bars"]
        k = y["by_kind"].setdefault(req["kind"], {"requests": 0, "complete": 0})
        k["requests"] += 1
        k["complete"] += j["state"] == "complete"
        for b in j.get("missing", []):
            missing_bars[f"{req['kind']} {b}"] += 1
        if j.get("reason"):
            reasons[j["reason"]] += 1
    for y in years.values():
        y["share_complete"] = round(y["complete"] / y["requests"], 4) if y["requests"] else None
    unanswered = sum(y["unanswered"] for y in years.values())
    out = {
        "requests": sum(y["requests"] for y in years.values()),
        "unanswered": unanswered,
        "years": {str(k): v for k, v in sorted(years.items())},
        "missing_required_bars": dict(missing_bars),
        "refusal_reasons": dict(reasons),
    }
    if unanswered:
        out.update(
            decided=False,
            basis=None,
            note=f"{unanswered} requests unanswered: fetch them, then run pilot again",
        )
    else:
        out.update(decided=True, basis=basis({y: v for y, v in years.items()}))
    return out


# ------------------------------------------------------------------------------------------------
# Dolt and the files.


def _out_dir(override: str | None = None) -> Path:
    if override:
        d = Path(override)
    else:
        from cherrypick.backtest import paths

        d = paths.results_dir() / "research" / "item31"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _spellings(symbol: str) -> set[str]:
    return {symbol, symbol.replace("/", "."), symbol.replace(".", "/")}


def universe_names() -> tuple[list[str], str]:
    """The `names` the liquidity verdict calls tradable, and the file's `updated_on`."""
    from cherrypick.earnings import universe

    verdict = universe.load()
    if verdict.get("error"):
        raise SystemExit(f"liquidity verdict unavailable: {verdict['error']}")
    names = sorted(s for s, v in verdict["names"].items() if (v or {}).get("verdict") == universe.TRADABLE)
    return names, str(verdict.get("updated_on"))


def dolt_rows(names: list[str], host: str, port: int) -> list[tuple[str, date, str | None]]:
    import mysql.connector

    spelled = {sp: n for n in names for sp in _spellings(n)}
    conn = mysql.connector.connect(
        host=host, port=port, user="root", database="earnings", connection_timeout=10
    )
    try:
        cur = conn.cursor()
        marks = ",".join(["%s"] * len(spelled))
        cur.execute(
            f"SELECT act_symbol, date, `when` FROM earnings_calendar WHERE date BETWEEN %s AND %s "
            f"AND act_symbol IN ({marks})",
            (FIRST_REPORT.isoformat(), LAST_REPORT.isoformat(), *spelled),
        )
        return [(spelled[s], d, w) for s, d, w in cur.fetchall()]
    finally:
        conn.close()


def build_events(rows: list[tuple[str, date, str | None]]) -> tuple[list[dict], int]:
    """(events, rows with no stated timing). One event per name and date."""
    seen: dict[tuple[str, date], str] = {}
    no_timing = 0
    for symbol, d, when in rows:
        timing = TIMINGS.get(when or "")
        if timing is None:
            no_timing += 1
            continue
        seen.setdefault((symbol, d), timing)
    events = [event_row(s, d, t) for (s, d), t in sorted(seen.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
    return events, no_timing


def cmd_plan(args) -> int:
    names, updated = universe_names()
    events, no_timing = build_events(dolt_rows(names, args.host, args.port))
    counts = summarise(events, no_timing)
    counts["universe_names"] = len(names)
    counts["verdict_updated_on"] = updated
    out = Path(args.out) if args.out else _out_dir() / "events.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"generated": datetime.now().isoformat(timespec="seconds"), "counts": counts, "events": events}
        ),
        encoding="utf-8",
    )
    print(json.dumps(counts, indent=2))
    print(f"wrote {out}")
    return 0


def cmd_pilot(args) -> int:
    from cherrypick.backtest import intradayfetch, settings

    d = _out_dir(args.out_dir)
    events = json.loads((d / "events.json").read_text(encoding="utf-8"))["events"]
    requests = pilot_requests(events)
    req_path = d / "pilot-requests.json"
    req_path.write_text(
        json.dumps({"seed": SEED, "per_year": PILOT_PER_YEAR, "requests": requests}), encoding="utf-8"
    )
    print(f"{len(requests)} pilot requests ({len(requests) // 2} events) in {req_path}")
    store = intradayfetch.db_path(settings.load(None))
    if not store.exists():
        print("no intraday store yet: queue the fetch, then run pilot again")
        return 0
    conn = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
    try:
        report = coverage(conn, requests)
    finally:
        conn.close()
    report.update(
        rule=f"intraday if at least {THRESHOLD:.0%} complete in every year, else end_of_day",
        threshold=THRESHOLD,
    )
    (d / "pilot.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("requests", "unanswered", "decided", "basis")}))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan", help="the event list from Dolt")
    pl.add_argument("--host", default="127.0.0.1")
    pl.add_argument("--port", type=int, default=3306)
    pl.add_argument("--out")
    pl.set_defaults(fn=cmd_plan)
    pi = sub.add_parser("pilot", help="the pilot's request file, and its coverage report once fetched")
    pi.add_argument("--out-dir")
    pi.set_defaults(fn=cmd_pilot)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
