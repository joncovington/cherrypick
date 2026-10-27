"""Fetch Yahoo's daily closes and adjusted closes for every name the technicals store adjusts.

**Why.** Our adjusted bars (`packages/technicals`, `adjust.py`) were checked against the vendor's
chart captures (`check-vendor`), the only thing that caught an unmodelled split, a spin-off or a
wrong dividend. Those captures stopped on 2026-10-23. Yahoo's adjusted close is an independent
statement of the same adjustment (proportional, splits and cash dividends), so
`python -m cherrypick.technicals check-adjust` compares the two series' shape: their ratio must be
flat between corporate actions, whatever each one's scale.

**What.** Yahoo's public chart endpoint (`query1.finance.yahoo.com/v8/finance/chart/<symbol>`), one
request per name for a year of daily bars with their dividend and split events, paced. The names are
the store's (`symbols.all_symbols()`, less the cash indexes, which have no adjustment). Each name's
year is replaced whole in `market-report/adjusted/yahoo.db` (`paths.yahoo_adjusted`), because every
new dividend restates every earlier adjusted close; a name already fetched today is skipped, so a
rerun after an interruption carries on where it stopped.

**Unofficial and personal-use**, the footing of the sector and revision fetches
(`scripts/fetch_sectors.py`, `scripts/fetch_analyst_revisions.py`): research input for one person's
own report, stored locally and never redistributed. A 403 or 429 stops the run with everything
fetched so far kept; a dropped connection or a 5xx is retried twice with a pause; a name Yahoo does
not know (404) is listed as missing, not failed.

A script, not package code: it reaches the network. `packages/technicals` reads the file read-only.
Run nightly by the orchestrator's `technicals-yahoo-adjusted` job.

    python scripts/fetch_yahoo_adjusted.py [--symbols A B ...] [--limit N] [--force]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta

from cherrypick.technicals import adjust_check, paths

PAUSE_S = 0.6
RETRIES = 2
RETRY_PAUSE_S = 5.0
RANGE = "1y"
URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{}"
    "?range={}&interval=1d&events=div%2Csplits&includeAdjustedClose=true"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def yahoo_symbol(symbol: str) -> str:
    """Yahoo spells a share class with a dash (BRK.B is BRK-B)."""
    return symbol.replace(".", "-")


def _session(ts: int, offset: int) -> str:
    """The exchange-local date of a bar's timestamp (Yahoo stamps a daily bar at the open)."""
    return (datetime.fromtimestamp(int(ts), UTC) + timedelta(seconds=int(offset or 0))).date().isoformat()


def parse(body: dict) -> dict | None:
    """{currency, rows: [(date, close, adjclose)], dividends: [(date, amount)], splits: [(date,
    numerator, denominator)]} from one chart response, or None when it holds no result. A bar with
    either close missing (a halted session) is left out."""
    results = ((body or {}).get("chart") or {}).get("result") or []
    if not results:
        return None
    r = results[0]
    meta = r.get("meta") or {}
    offset = meta.get("gmtoffset") or 0
    stamps = r.get("timestamp") or []
    ind = r.get("indicators") or {}
    closes = ((ind.get("quote") or [{}])[0] or {}).get("close") or []
    adjs = ((ind.get("adjclose") or [{}])[0] or {}).get("adjclose") or []
    rows = {}
    for ts, c, a in zip(stamps, closes, adjs, strict=False):
        if isinstance(c, (int, float)) and isinstance(a, (int, float)) and c > 0 and a > 0:
            rows[_session(ts, offset)] = (float(c), float(a))  # a repeated session keeps the last
    events = r.get("events") or {}
    divs = sorted(
        (_session(e["date"], offset), float(e["amount"]))
        for e in (events.get("dividends") or {}).values()
        if isinstance(e, dict) and "date" in e and isinstance(e.get("amount"), (int, float))
    )
    splits = sorted(
        (_session(e["date"], offset), float(e["numerator"]), float(e["denominator"]))
        for e in (events.get("splits") or {}).values()
        if isinstance(e, dict) and "date" in e and e.get("numerator") and e.get("denominator")
    )
    return {
        "currency": meta.get("currency"),
        "rows": sorted((d, c, a) for d, (c, a) in rows.items()),
        "dividends": divs,
        "splits": splits,
    }


def save(conn: sqlite3.Connection, symbol: str, parsed: dict, now: datetime) -> None:
    """Replace one name's year whole: its adjusted closes are restated by every later dividend."""
    rows = parsed["rows"]
    with conn:
        conn.execute("DELETE FROM closes WHERE symbol = ?", (symbol,))
        conn.executemany(
            "INSERT INTO closes (symbol, date, close, adjclose) VALUES (?, ?, ?, ?)",
            [(symbol, d, c, a) for d, c, a in rows],
        )
        conn.execute(
            "INSERT OR REPLACE INTO series (symbol, fetched_at, fetched_on, currency, first_date, "
            "last_date, dividends, splits) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                symbol,
                now.isoformat(timespec="seconds"),
                now.astimezone().date().isoformat(),
                parsed.get("currency"),
                rows[0][0] if rows else None,
                rows[-1][0] if rows else None,
                json.dumps(parsed["dividends"]),
                json.dumps(parsed["splits"]),
            ),
        )


def due(names: list[str], conn: sqlite3.Connection, today: date, force: bool = False) -> list[str]:
    """The names not already fetched today (all of them with `force`)."""
    if force:
        return list(names)
    done = {
        r[0] for r in conn.execute("SELECT symbol FROM series WHERE fetched_on = ?", (today.isoformat(),))
    }
    return [s for s in names if s not in done]


def fetch(symbol: str) -> dict:
    url = URL.format(urllib.parse.quote(yahoo_symbol(symbol)), RANGE)
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as resp:
        return json.loads(resp.read())


def fetch_with_retries(symbol: str, sleep=time.sleep) -> dict:
    """One name's response; a dropped connection, a timeout or a 5xx is asked again RETRIES times."""
    for attempt in range(RETRIES + 1):
        try:
            return fetch(symbol)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == RETRIES:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == RETRIES:
                raise
        sleep(RETRY_PAUSE_S * (attempt + 1))
    raise AssertionError("unreachable")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--symbols", nargs="+", help="these names only (default: every stored name)")
    ap.add_argument("--limit", type=int, default=None, help="ask for at most this many names")
    ap.add_argument("--force", action="store_true", help="fetch names already fetched today too")
    args = ap.parse_args(argv)
    path = paths.yahoo_adjusted()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = adjust_check.connect_yahoo(path)
    now = datetime.now(UTC)
    names = sorted(args.symbols) if args.symbols else adjust_check.default_symbols()
    todo = due(names, conn, now.astimezone().date(), args.force)[: args.limit]
    failed: list[str] = []
    missing: list[str] = []
    saved = 0
    stopped = False
    started = time.monotonic()
    for k, sym in enumerate(todo):
        if k:
            time.sleep(PAUSE_S)
        try:
            parsed = parse(fetch_with_retries(sym))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                missing.append(sym)
                continue
            failed.append(f"{sym}: HTTP {exc.code}")
            if exc.code in (403, 429):
                stopped = True
                break
            continue
        except Exception as exc:  # noqa: BLE001 -- one name failing is a gap, not the run
            failed.append(f"{sym}: {type(exc).__name__}: {str(exc)[:100]}")
            continue
        if not parsed or not parsed["rows"]:
            missing.append(sym)
            continue
        save(conn, sym, parsed, now)
        saved += 1
    conn.close()
    report = {
        "ok": not failed,
        "stopped": stopped,
        "asked": len(todo),
        "saved": saved,
        "missing": missing[:40],
        "missing_count": len(missing),
        "failed": failed[:20],
        "written": str(path),
        "seconds": round(time.monotonic() - started),
    }
    print(json.dumps(report, indent=1))
    # A refusal stops the run and fails it; scattered failures fail it only when they are most of it.
    return 1 if stopped or len(failed) >= max(1, len(todo) // 2) else 0


if __name__ == "__main__":
    sys.exit(main())
