"""Record the time of day (before the open / after the close) of every announcement in the next month,
from Nasdaq's public earnings calendar -- the earnings calendar's third timing source.

**Why.** The earnings calendar (docs/earnings-screener-plan.md) takes its time of day from the broker
(market-metrics `time-of-day`) and the local Dolt calendar. Between them about a third of the very-
and somewhat-liquid names reporting in the next 30 days were untimed on 2026-10-16. Nasdaq's
calendar agreed with both on all 378 names they timed, and timed 64 of the 206 they did not.

**What.** One request per weekday from today through `DAYS` calendar days ahead -- the page Nasdaq's
own site loads (`api.nasdaq.com/api/calendar/earnings?date=`), undocumented, so a browser's headers
and a two-second pace. Each row's `time` is `time-pre-market`, `time-after-hours` or
`time-not-supplied`; kept as `bmo` / `amc` / null. Written to
`market-report/earnings/times/<today>.json` as `{fetched_at, dates: {date: {symbol: time}}, failed}`.
A failed date is listed in `failed` and left out; the calendar falls back to its other sources.
A 403 or 429 stops the run rather than pressing on.

A script, not package code: it reaches the network. `packages/technicals` reads the file.

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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=DAYS)
    args = ap.parse_args(argv)
    today = datetime.now(ZoneInfo("America/New_York")).date()
    doc: dict = {"fetched_at": None, "dates": {}, "failed": []}
    started = time.monotonic()
    for k, d in enumerate(weekdays(today, args.days)):
        if k:
            time.sleep(PAUSE_S)
        try:
            doc["dates"][d.isoformat()] = parse(fetch_day(d))
        except urllib.error.HTTPError as exc:
            doc["failed"].append(f"{d}: HTTP {exc.code}")
            if exc.code in (403, 429):
                break
        except Exception as exc:  # noqa: BLE001 -- one date failing is a gap, not the run
            doc["failed"].append(f"{d}: {type(exc).__name__}: {str(exc)[:100]}")
    doc["fetched_at"] = datetime.now(UTC).isoformat()
    folder = paths.earnings_times_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{today.isoformat()}.json"
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    timed = sum(1 for rows in doc["dates"].values() for t in rows.values() if t)
    listed = sum(len(rows) for rows in doc["dates"].values())
    report = {
        "ok": not doc["failed"],
        "dates": len(doc["dates"]),
        "listed": listed,
        "timed": timed,
        "failed": doc["failed"],
        "written": str(path),
        "seconds": round(time.monotonic() - started),
    }
    print(json.dumps(report, indent=1))
    return 0 if doc["dates"] else 1


if __name__ == "__main__":
    sys.exit(main())
