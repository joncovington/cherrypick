"""Record the time of day (before the open / after the close) of every announcement in the next month,
from Nasdaq's public earnings calendar -- the earnings calendar's third timing source.

**Why.** The earnings calendar (earnings-screener-plan (archived)) takes its time of day from the broker
(market-metrics `time-of-day`) and the local Dolt calendar. Between them about a third of the very-
and somewhat-liquid names reporting in the next 30 days were untimed on 2026-10-16. Nasdaq's
calendar agreed with both on all 378 names they timed, and timed 64 of the 206 they did not.

**What.** One request per weekday from today through `DAYS` calendar days ahead -- the page Nasdaq's
own site loads (`api.nasdaq.com/api/calendar/earnings?date=`), undocumented, so a browser's headers
and a two-second pace. Each row's `time` is `time-pre-market`, `time-after-hours` or
`time-not-supplied`; kept as `bmo` / `amc` / null. A 403 or 429 stops Nasdaq's part of the run
rather than pressing on.

**The fallback is Yahoo's earnings calendar.** Every date Nasdaq did not answer is read from Yahoo's
calendar through yfinance (`Calendars.get_earnings_calendar`), paged by market cap down to
`YAHOO_MIN_MARKET_CAP` at the same pace. Its `Timing` is `BMO`, `AMC` or `TNS` (not supplied), kept
as `bmo` / `amc` / null, on the New York date of the event's start. yfinance is imported only here;
without it the fallback is skipped and listed in `failed`.

Written to `market-report/earnings/times/<today>.json` as `{fetched_at, dates: {date: {symbol:
time}}, sources: {date: 'nasdaq'|'yahoo'}, failed}`; a file without `sources` is all Nasdaq's. A
date neither source answered is left out and the calendar falls back to the broker and Dolt. A run
that got no date at all writes nothing, so the last good file stays the one read. The exit is 0
when every date was answered by one source or the other.

A script, not package code: it reaches the network. `packages/technicals` and the manage snapshot
read the file.

    python scripts/fetch_earnings_times.py [--days 30]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from cherrypick.technicals import paths

DAYS = 30
PAUSE_S = 2.0
URL = "https://api.nasdaq.com/api/calendar/earnings?date={}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
TIMES = {"time-pre-market": "bmo", "time-after-hours": "amc"}
ET = ZoneInfo("America/New_York")
YAHOO_TIMES = {"BMO": "bmo", "AMC": "amc"}
YAHOO_PAGE = 100
YAHOO_MAX_PAGES = 40
YAHOO_MIN_MARKET_CAP = 300e6


def parse(body: dict) -> dict[str, str | None]:
    """{symbol: 'bmo' | 'amc' | None} from one date's response; a symbol Nasdaq spells with a slash
    (BRK/B) is kept in the suite's dotted form."""
    rows = ((body or {}).get("data") or {}).get("rows") or []
    out = {}
    for r in rows:
        sym = str(r.get("symbol") or "").strip().upper().replace("/", ".")
        if sym:
            out[sym] = TIMES.get(r.get("time"))
    return out


def weekdays(start: date, days: int) -> list[date]:
    return [start + timedelta(days=k) for k in range(days + 1) if (start + timedelta(days=k)).weekday() < 5]


def fetch_day(d: date) -> dict:
    req = urllib.request.Request(URL.format(d.isoformat()), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def parse_yahoo(rows, wanted: set[str]) -> dict[str, dict[str, str | None]]:
    """{date: {symbol: 'bmo' | 'amc' | None}} from Yahoo calendar rows `(symbol, start, timing)`, for
    the dates in `wanted`: the New York date of the event's start (a tz-aware datetime), a symbol
    Yahoo spells with a dash (BRK-B) in the suite's dotted form. A row without a start is dropped.
    Where Yahoo lists a name twice on one date, a timed row beats an untimed one."""
    out: dict[str, dict[str, str | None]] = {d: {} for d in wanted}
    for symbol, start, timing in rows:
        sym = str(symbol or "").strip().upper().replace("-", ".")
        if not sym or not isinstance(start, datetime) or start.tzinfo is None:
            continue
        day = start.astimezone(ET).date().isoformat()
        if day not in out:
            continue
        when = YAHOO_TIMES.get(str(timing or "").upper())
        if when or sym not in out[day]:
            out[day][sym] = when
    return out


def fetch_yahoo(first: date, last: date) -> list[tuple]:
    """Every `(symbol, start, timing)` row on Yahoo's earnings calendar from `first` through `last`,
    largest companies first, down to YAHOO_MIN_MARKET_CAP, a page at a time at PAUSE_S."""
    import yfinance as yf  # noqa: PLC0415 -- only the fallback needs it

    cal = yf.Calendars(start=first, end=last + timedelta(days=1))
    rows: list[tuple] = []
    for page in range(YAHOO_MAX_PAGES):
        if page:
            time.sleep(PAUSE_S)
        df = cal.get_earnings_calendar(
            market_cap=YAHOO_MIN_MARKET_CAP,
            filter_most_active=False,
            limit=YAHOO_PAGE,
            offset=page * YAHOO_PAGE,
        )
        if df is None or df.empty:
            break
        for symbol, r in df.iterrows():
            start = r.get("Event Start Date")
            rows.append(
                (symbol, start.to_pydatetime() if hasattr(start, "to_pydatetime") else start, r.get("Timing"))
            )
        if len(df) < YAHOO_PAGE:
            break
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=DAYS)
    args = ap.parse_args(argv)
    today = datetime.now(ET).date()
    wanted = weekdays(today, args.days)
    doc: dict = {"fetched_at": None, "dates": {}, "sources": {}, "failed": []}
    started = time.monotonic()
    for k, d in enumerate(wanted):
        if k:
            time.sleep(PAUSE_S)
        try:
            doc["dates"][d.isoformat()] = parse(fetch_day(d))
            doc["sources"][d.isoformat()] = "nasdaq"
        except urllib.error.HTTPError as exc:
            doc["failed"].append(f"{d}: HTTP {exc.code}")
            if exc.code in (403, 429):
                break
        except Exception as exc:  # noqa: BLE001 -- one date failing is a gap, not the run
            doc["failed"].append(f"{d}: {type(exc).__name__}: {str(exc)[:100]}")
    missing = [d for d in wanted if d.isoformat() not in doc["dates"]]
    if missing:
        try:
            rows = fetch_yahoo(missing[0], missing[-1])
            # Nothing at all over weeks of dates is a broken read, not a quiet month.
            if not rows:
                raise ValueError("no rows for the whole window")
            got = parse_yahoo(rows, {d.isoformat() for d in missing})
            for day, names in got.items():
                doc["dates"][day] = names
                doc["sources"][day] = "yahoo"
        except Exception as exc:  # noqa: BLE001 -- the fallback failing leaves the dates to the calendar's others
            doc["failed"].append(f"yahoo: {type(exc).__name__}: {str(exc)[:100]}")
    doc["dates"] = dict(sorted(doc["dates"].items()))
    doc["sources"] = dict(sorted(doc["sources"].items()))
    doc["fetched_at"] = datetime.now(UTC).isoformat()
    path = None
    if doc["dates"]:
        folder = paths.earnings_times_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{today.isoformat()}.json"
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    timed = sum(1 for rows in doc["dates"].values() for t in rows.values() if t)
    listed = sum(len(rows) for rows in doc["dates"].values())
    complete = len(doc["dates"]) == len(wanted)
    report = {
        "ok": complete,
        "dates": len(doc["dates"]),
        "by_source": {s: list(doc["sources"].values()).count(s) for s in ("nasdaq", "yahoo")},
        "listed": listed,
        "timed": timed,
        "failed": doc["failed"],
        "written": str(path) if path else None,
        "seconds": round(time.monotonic() - started),
    }
    print(json.dumps(report, indent=1))
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
