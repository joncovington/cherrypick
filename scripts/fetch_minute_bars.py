"""Fetch the one-minute stock bars edge-hypotheses item 95 (the ladder's paper rules at minute
resolution) is replayed on.

**Source: Alpaca's market-data API** (`data.alpaca.markets/v2/stocks/bars`), a free account's
historical bars: the consolidated tape (`feed=sip`) from 2016, extended hours included (04:00-20:00
ET), unadjusted (`adjustment=raw`): the prices the fund traded at, as paper sees them, so a cent is
a cent (split-adjusted, SOXL's 2016 prices are about $1.70 and every cent rounding and the spread
would be fifteen times too coarse; the replay applies each split itself). The free plan
refuses the last 15 minutes of the tape, so a fetch ends before then. Personal use: stored under
`~/.cherrypick/data/research/item95/`, never the repo, never redistributed.

**The key** is the user's own, in the OS keyring only (`core.auth.credentials`, service
`cherrypick-alpaca`): `key set` prompts for the key id and secret with hidden input, `key status`
says whether both are there. Neither is ever printed, logged or written to a file.

**Two feeds, two stores** (`--feed`, `FEEDS`): `sip`, the consolidated tape 04:00-20:00 ET, into
`research/item95/minute_bars.db`; `boats`, Blue Ocean's overnight session 20:00-04:00 ET (served
from 2024-09 for these funds), into `research/item96/overnight_bars.db`. Same schema in each.

**What is stored**: `bars` (symbol, start as New York local time
`YYYY-MM-DDTHH:MM`, open, high, low, close, volume, trades, vwap), one row per minute that traded;
a minute with no trade has no row, as the API serves it. `fetched` records each (symbol, month)
fetched whole, so a re-run skips them; the current month is always fetched again.

    python scripts/fetch_minute_bars.py key set
    python scripts/fetch_minute_bars.py key status
    python scripts/fetch_minute_bars.py fetch --symbols SOXL,TQQQ --from 2016-01 [--to 2026-11]
    python scripts/fetch_minute_bars.py fetch --feed boats --symbols SOXL,TQQQ --from 2024-09
    python scripts/fetch_minute_bars.py status [--feed boats]
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
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home, progress
from cherrypick.core.auth.credentials import CredentialError, CredentialStore, prompt_and_store

NY = ZoneInfo("America/New_York")
SERVICE = "cherrypick-alpaca"
KEY_ID, SECRET = "alpaca_key_id", "alpaca_secret"
URL = "https://data.alpaca.markets/v2/stocks/bars"
PAGE = 10_000
MIN_GAP_S = 0.35  # the free plan allows 200 requests a minute; stay well under it
TAPE_DELAY = timedelta(minutes=20)  # the free plan refuses the last 15 minutes of the tape


#: feed: (the study folder its store sits in, the store's file name)
FEEDS = {"sip": ("item95", "minute_bars.db"), "boats": ("item96", "overnight_bars.db")}


def store_dir(feed: str = "sip") -> Path:
    d = home.home() / "data" / "research" / FEEDS[feed][0]
    d.mkdir(parents=True, exist_ok=True)
    return d


def connect(path: Path | None = None, feed: str = "sip") -> sqlite3.Connection:
    conn = sqlite3.connect(path or store_dir(feed) / FEEDS[feed][1])
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bars (symbol TEXT, start TEXT, open REAL, high REAL, low REAL,
            close REAL, volume REAL, trades INTEGER, vwap REAL, PRIMARY KEY (symbol, start));
        CREATE TABLE IF NOT EXISTS fetched (symbol TEXT, month TEXT, fetched_on TEXT, rows INTEGER,
            PRIMARY KEY (symbol, month));
        """
    )
    return conn


# --------------------------------------------------------------------------- the key
def credentials() -> CredentialStore:
    return CredentialStore(SERVICE)


def key_status() -> dict:
    try:
        store = credentials()
        missing = [k for k in (KEY_ID, SECRET) if not store.get_secret(k)]
    except CredentialError as exc:
        return {"readable": False, "error": str(exc)}
    return {"readable": True, "key_id": KEY_ID not in missing, "secret": SECRET not in missing}


def headers() -> dict:
    store = credentials()
    key_id, secret = store.get_secret(KEY_ID), store.get_secret(SECRET)
    if not key_id or not secret:
        raise SystemExit("no Alpaca key in the keyring: run `python scripts/fetch_minute_bars.py key set`")
    return {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"}


# --------------------------------------------------------------------------- months
def months(first: str, last: str) -> list[str]:
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_span(month: str, now: datetime) -> tuple[datetime, datetime]:
    """The month's first instant and its end (UTC), the end held back by the free plan's delay."""
    y, m = map(int, month.split("-"))
    start = datetime(y, m, 1, tzinfo=NY)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=NY)
    return start.astimezone(UTC), min(end.astimezone(UTC), now - TAPE_DELAY)


def local_minute(ts: str) -> str:
    """An API bar time (UTC, RFC 3339) as New York local `YYYY-MM-DDTHH:MM`."""
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return t.astimezone(NY).strftime("%Y-%m-%dT%H:%M")


def rows_from(payload: dict, symbol: str) -> list[tuple]:
    out = []
    for b in (payload.get("bars") or {}).get(symbol) or []:
        out.append(
            (
                symbol,
                local_minute(b["t"]),
                b["o"],
                b["h"],
                b["l"],
                b["c"],
                b.get("v"),
                b.get("n"),
                b.get("vw"),
            )
        )
    return out


# --------------------------------------------------------------------------- fetch
_last_call = [0.0]


def _get(params: dict, hdrs: dict) -> dict:
    wait = MIN_GAP_S - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(params)}", headers=hdrs)
    for attempt in range(5):
        _last_call[0] = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            body = exc.read()[:300].decode("utf-8", "replace")
            raise SystemExit(f"Alpaca refused ({exc.code}): {body}") from None
        except urllib.error.URLError:
            time.sleep(5 * (attempt + 1))
    raise SystemExit("Alpaca did not answer after 5 tries")


def fetch_month(conn, symbol: str, month: str, hdrs: dict, now: datetime, feed: str = "sip") -> int:
    start, end = month_span(month, now)
    if end <= start:
        return 0
    rows, token = [], None
    while True:
        params = {
            "symbols": symbol,
            "timeframe": "1Min",
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "limit": PAGE,
            "adjustment": "raw",
            "feed": feed,
            "sort": "asc",
        }
        if token:
            params["page_token"] = token
        payload = _get(params, hdrs)
        rows += rows_from(payload, symbol)
        token = payload.get("next_page_token")
        if not token:
            break
    with conn:
        conn.execute("DELETE FROM bars WHERE symbol = ? AND start LIKE ?", (symbol, f"{month}-%"))
        conn.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn.execute(
            "INSERT OR REPLACE INTO fetched VALUES (?,?,?,?)",
            (symbol, month, date.today().isoformat(), len(rows)),
        )
    return len(rows)


def cmd_fetch(args) -> int:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    now = datetime.now(UTC)
    last = args.to or now.astimezone(NY).strftime("%Y-%m")
    wanted = months(args.first, last)
    current = now.astimezone(NY).strftime("%Y-%m")
    conn = connect(feed=args.feed)
    hdrs = headers()
    done = {(s, m) for s, m in conn.execute("SELECT symbol, month FROM fetched") if m != current}
    todo = [(s, m) for s in symbols for m in wanted if (s, m) not in done]
    counts = {"rows": 0, "months": 0, "skipped": len(symbols) * len(wanted) - len(todo)}
    with progress.Reporter(f"fetch_minute_bars_{args.feed}") as rep:
        rep.split(
            "months",
            [s for s in symbols for _ in wanted],
            unit="months",
            done=[s for s in symbols for m in wanted if (s, m) in done],
        )
        for symbol, month in todo:
            rep.at("months", f"{symbol} {month}")
            counts["rows"] += fetch_month(conn, symbol, month, hdrs, now, args.feed)
            counts["months"] += 1
            rep.advance("months", key=symbol, current=f"{symbol} {month}")
    print(json.dumps(counts))
    return 0


def cmd_status(args) -> int:
    conn = connect(feed=args.feed)
    out = {
        s: {"first": a, "last": b, "rows": n}
        for s, a, b, n in conn.execute(
            "SELECT symbol, min(start), max(start), count(*) FROM bars GROUP BY symbol"
        )
    }
    print(json.dumps({"key": key_status(), "bars": out}, indent=1))
    return 0


def cmd_key(args) -> int:
    if args.action == "set":
        print("Paste the key ID, then the secret, from Alpaca's dashboard (paper account > API Keys).")
        written = prompt_and_store(credentials(), (KEY_ID, SECRET))
        print(json.dumps({"written": written, **key_status()}))
    else:
        print(json.dumps(key_status()))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("key")
    k.add_argument("action", choices=("set", "status"))
    k.set_defaults(fn=cmd_key)
    f = sub.add_parser("fetch")
    f.add_argument("--symbols", required=True)
    f.add_argument("--from", dest="first", required=True, help="first month, YYYY-MM")
    f.add_argument("--to", default=None, help="last month, YYYY-MM (default: this month)")
    f.add_argument("--feed", choices=tuple(FEEDS), default="sip")
    f.set_defaults(fn=cmd_fetch)
    st = sub.add_parser("status")
    st.add_argument("--feed", choices=tuple(FEEDS), default="sip")
    st.set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
