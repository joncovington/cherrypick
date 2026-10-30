"""Fetch the bars edge-hypotheses item 26 (the dip-buying share ladder) is replayed on.

**The broker first.** tastytrade's DXLink candles are the source wherever they reach (the user's
rule, 2026-10-29): regular-hours daily candles, and hourly and 5-minute candles with extended
hours. On SOXL (2026-10-29) the feed served daily candles from 2022-12-29, hourly from 2026-01-20
and 5-minute from about five weeks back. Read-only, with the shared credential.

**Yahoo for what the broker does not reach**: the public chart endpoint
(`query1.finance.yahoo.com/v8/finance/chart/<symbol>`), daily from each fund's start with its split
events, and hourly with pre- and after-market hours for the two years it serves. Its prices are
split-adjusted, not dividend-adjusted. Its extended-hours bars carry bad prints the broker's do not
(SOXL's 2026-10-29 16:00 bar: a high of 123 and a low of 68.5 inside a 102-to-89 move), so the
replay prefers the broker's bar wherever both hold one. Unofficial and personal-use, the footing of
`scripts/fetch_yahoo_adjusted.py`: stored locally, never redistributed.

**Which names.** Daily: every long leveraged fund in `technicals`' `leveraged_funds.json` (kind
`leveraged`; inverse and volatility funds are not candidates) and each one's 1x counterpart
(`COUNTERPART`). Intraday: `INTRADAY`, the names the declaration checks the daily replay against.
Which names pass the declaration's history and dollar-volume rule is the replay's decision; this
fetches every candidate.

**Where.** `~/.cherrypick/data/research/item26/bars.db`: `bars` (symbol, source, interval, start,
OHLCV), `splits`, `fetched`. Each (name, source, interval) series is replaced whole; one fetched
today is skipped, so a rerun carries on where it stopped. Daily starts are session dates; intraday
starts are New York wall-clock bar starts. Never in the repo.

A 403 or 429 from Yahoo stops its part of the run with what is stored kept; a dropped connection or
a 5xx is retried twice; a name it does not know (404) is listed as missing.

    python scripts/fetch_ladder_bars.py [--symbols A B ...] [--source broker|yahoo|both] [--force]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import streamcache
from cherrypick.core.home import data_dir

NY = ZoneInfo("America/New_York")
INTRADAY = ("SOXL", "TECL", "TQQQ")

# Broker candles: (interval, extended hours, how far back to ask). The feed answers with what it
# holds from that start.
BROKER_SERIES = (("1d", False, datetime(2008, 1, 1, tzinfo=UTC)),)
BROKER_INTRADAY = (
    ("1h", True, datetime(2023, 1, 1, tzinfo=UTC)),
    ("5m", True, datetime(2025, 1, 1, tzinfo=UTC)),
)
BATCH = 20
QUIET_S = 8.0
MAX_BATCH_S = 180.0
SNAP_END = 0x08
REMOVE = 0x02

PAUSE_S = 0.6
RETRIES = 2
RETRY_PAUSE_S = 5.0
# An explicit span: `range=max` quietly answers with monthly bars.
YAHOO_DAILY = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{}?period1=0&period2={}&interval=1d&events=splits"
)
YAHOO_HOURLY = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=730d&interval=1h&includePrePost=true"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Each leveraged fund's 1x counterpart, where one plainly exists: the leverage rival's check.
COUNTERPART = {
    "SOXL": "SOXX",
    "USD": "SOXX",
    "TECL": "XLK",
    "ROM": "XLK",
    "TQQQ": "QQQ",
    "QLD": "QQQ",
    "UPRO": "SPY",
    "SPXL": "SPY",
    "SSO": "SPY",
    "UDOW": "DIA",
    "DDM": "DIA",
    "TNA": "IWM",
    "URTY": "IWM",
    "UWM": "IWM",
    "MIDU": "MDY",
    "MVV": "MDY",
    "UMDD": "MDY",
    "FAS": "XLF",
    "UYG": "XLF",
    "DPST": "KRE",
    "ERX": "XLE",
    "DIG": "XLE",
    "LABU": "XBI",
    "CURE": "XLV",
    "RXL": "XLV",
    "DRN": "IYR",
    "URE": "IYR",
    "UYM": "XLB",
    "UXI": "XLI",
    "DUSL": "XLI",
    "UTSL": "XLU",
    "UGE": "XLP",
    "RETL": "XRT",
    "NAIL": "ITB",
    "DFEN": "ITA",
    "WEBL": "FDN",
    "PILL": "XPH",
    "TPOR": "IYT",
    "NUGT": "GDX",
    "JNUG": "GDXJ",
    "EDC": "EEM",
    "YINN": "FXI",
    "CHAU": "ASHR",
    "KORU": "EWY",
    "EURL": "VGK",
    "INDL": "INDA",
    "BRZU": "EWZ",
    "MEXX": "EWW",
    "UCO": "USO",
    "BOIL": "UNG",
    "AGQ": "SLV",
    "UGL": "GLD",
    "TMF": "TLT",
    "UBT": "TLT",
    "TYD": "IEF",
    "UST": "IEF",
}

# Each counterpart-mapped fund's leverage today. Two funds on one counterpart at one leverage are
# the same bet (UPRO and SPXL, TNA and URTY, MIDU and UMDD); a study counts the pair once.
LEVERAGE = {
    **dict.fromkeys(("SOXL", "TECL", "TQQQ", "UPRO", "SPXL", "UDOW", "TNA", "URTY", "MIDU", "UMDD"), 3),
    **dict.fromkeys(("FAS", "DPST", "LABU", "CURE", "DRN", "DUSL", "UTSL", "RETL", "NAIL", "DFEN"), 3),
    **dict.fromkeys(("WEBL", "PILL", "TPOR", "EDC", "YINN", "KORU", "EURL", "MEXX", "TMF", "TYD"), 3),
    **dict.fromkeys(("USD", "ROM", "QLD", "SSO", "DDM", "UWM", "MVV", "UYG", "ERX", "DIG", "RXL"), 2),
    **dict.fromkeys(("URE", "UYM", "UXI", "UGE", "NUGT", "JNUG", "CHAU", "INDL", "BRZU", "UCO"), 2),
    **dict.fromkeys(("BOIL", "AGQ", "UGL", "UBT", "UST"), 2),
}


class Blocked(Exception):
    """Yahoo refused the run (403/429): stop its part, keep what is stored."""


def candidates() -> list[str]:
    from cherrypick.technicals.skate import leveraged_funds

    return sorted(s for s, f in leveraged_funds().items() if str(f.get("kind") or "leveraged") == "leveraged")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bars (symbol TEXT, source TEXT, interval TEXT, start TEXT,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (symbol, source, interval, start));
        CREATE TABLE IF NOT EXISTS splits (symbol TEXT, date TEXT, numerator REAL, denominator REAL,
            PRIMARY KEY (symbol, date));
        CREATE TABLE IF NOT EXISTS fetched (symbol TEXT, source TEXT, interval TEXT, fetched_on TEXT,
            rows INTEGER, first TEXT, last TEXT, PRIMARY KEY (symbol, source, interval));
        """
    )
    return conn


def stamp(moment: datetime, interval: str) -> str:
    """A daily bar's session date, or an intraday bar's New York wall-clock start."""
    if interval == "1d":
        return moment.date().isoformat()
    return moment.astimezone(NY).strftime("%Y-%m-%dT%H:%M")


def save(conn, symbol: str, source: str, interval: str, rows: list[tuple], today: str) -> int:
    rows = sorted({r[0]: r for r in rows}.values())  # a repeated start keeps the last
    with conn:
        conn.execute(
            "DELETE FROM bars WHERE symbol = ? AND source = ? AND interval = ?", (symbol, source, interval)
        )
        conn.executemany(
            "INSERT INTO bars (symbol, source, interval, start, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(symbol, source, interval, *r) for r in rows],
        )
        conn.execute(
            "INSERT OR REPLACE INTO fetched (symbol, source, interval, fetched_on, rows, first, last) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                symbol,
                source,
                interval,
                today,
                len(rows),
                rows[0][0] if rows else None,
                rows[-1][0] if rows else None,
            ),
        )
    return len(rows)


# ---- broker ----


async def broker_batch(
    session, names: list[str], interval: str, ext: bool, start: datetime
) -> dict[str, list]:
    """{name: [(start, o, h, l, c, v)]} for one batch of names on one candle series."""
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Candle

    bars: dict[str, list] = {n: [] for n in names}
    ended: set[str] = set()
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe_candle(
            names, interval=interval, start_time=start, extended_trading_hours=ext
        )
        deadline = time.monotonic() + MAX_BATCH_S
        while time.monotonic() < deadline and len(ended) < len(names):
            # get_event per wait: a wait_for timeout around a listen() generator cancels the generator.
            try:
                ev = await asyncio.wait_for(streamer.get_event(Candle), timeout=QUIET_S)
            except TimeoutError:
                break
            name = str(ev.event_symbol or "").split("{", 1)[0]
            if name not in bars:
                continue
            flags = int(ev.event_flags or 0)
            if flags & SNAP_END:
                ended.add(name)
            ms = streamcache.to_float(ev.time)
            vals = [streamcache.to_float(v) for v in (ev.open, ev.high, ev.low, ev.close)]
            if flags & REMOVE or ms is None or not all(v and v > 0 for v in vals):
                continue
            moment = datetime.fromtimestamp(ms / 1000.0, tz=UTC)
            bars[name].append((stamp(moment, interval), *vals, streamcache.to_float(ev.volume) or 0.0))
    return bars


def fetch_broker(conn, daily: list[str], intraday: list[str], today: str, force: bool) -> dict:
    # One event loop for the whole run: the session's HTTP client is bound to the loop it first ran on.
    return asyncio.run(_fetch_broker(conn, daily, intraday, today, force))


async def _fetch_broker(conn, daily: list[str], intraday: list[str], today: str, force: bool) -> dict:
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

    session = SessionManager(CredentialStore(SHARED_SERVICE)).get_session()
    counts = {"ok": 0, "empty": [], "skipped": 0}
    plan = [(n, *s) for s in BROKER_SERIES for n in [daily]] + [
        (n, *s) for s in BROKER_INTRADAY for n in [intraday]
    ]
    for names, interval, ext, start in plan:
        done = {
            s
            for (s,) in conn.execute(
                "SELECT symbol FROM fetched WHERE source = 'broker' AND interval = ? AND fetched_on = ?",
                (interval, today),
            )
        }
        todo = [n for n in names if force or n not in done]
        counts["skipped"] += len(names) - len(todo)
        for k in range(0, len(todo), BATCH):
            got = await broker_batch(session, todo[k : k + BATCH], interval, ext, start)
            for name, rows in got.items():
                if save(conn, name, "broker", interval, rows, today):
                    counts["ok"] += 1
                else:
                    counts["empty"].append(f"{name}/{interval}")
    return counts


# ---- Yahoo ----


def yahoo_parse(body: dict, interval: str) -> dict | None:
    """{rows: [(start, o, h, l, c, v)], splits: [(date, num, den)]}, or None for no result."""
    results = ((body or {}).get("chart") or {}).get("result") or []
    if not results:
        return None
    r = results[0]
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    rows = []
    for i, ts in enumerate(r.get("timestamp") or []):
        vals = [c[i] if i < len(c) else None for c in cols]
        if not all(isinstance(v, (int, float)) and v > 0 for v in vals[:4]):
            continue
        # A daily bar is stamped at the session's open, so its New York date is the session's.
        moment = datetime.fromtimestamp(int(ts), UTC).astimezone(NY)
        rows.append((stamp(moment, interval), *(float(v) for v in vals[:4]), float(vals[4] or 0)))
    splits = sorted(
        (
            datetime.fromtimestamp(int(e["date"]), UTC).astimezone(NY).date().isoformat(),
            float(e["numerator"]),
            float(e["denominator"]),
        )
        for e in (((r.get("events") or {}).get("splits")) or {}).values()
        if isinstance(e, dict) and "date" in e and e.get("numerator") and e.get("denominator")
    )
    return {"rows": rows, "splits": splits}


def yahoo_get(url: str) -> dict | None:
    """One chart response, or None for a 404. Raises Blocked on 403/429."""
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (403, 429):
                raise Blocked(f"HTTP {e.code}") from e
            if e.code < 500 or attempt == RETRIES:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == RETRIES:
                raise
        time.sleep(RETRY_PAUSE_S)
    return None


def fetch_yahoo(conn, daily: list[str], intraday: list[str], today: str, force: bool) -> dict:
    counts = {"ok": 0, "missing": [], "skipped": 0}
    done = {
        (s, i)
        for s, i in conn.execute(
            "SELECT symbol, interval FROM fetched WHERE source = 'yahoo' AND fetched_on = ?", (today,)
        )
    }
    jobs = [(s, "1d") for s in daily] + [(s, "1h") for s in intraday]
    try:
        for symbol, interval in jobs:
            if (symbol, interval) in done and not force:
                counts["skipped"] += 1
                continue
            url = (
                YAHOO_DAILY.format(symbol, int(time.time()))
                if interval == "1d"
                else YAHOO_HOURLY.format(symbol)
            )
            parsed = yahoo_parse(yahoo_get(url) or {}, interval)
            if parsed and interval == "1d":
                with conn:
                    conn.execute("DELETE FROM splits WHERE symbol = ?", (symbol,))
                    conn.executemany(
                        "INSERT INTO splits (symbol, date, numerator, denominator) VALUES (?, ?, ?, ?)",
                        [(symbol, *s) for s in parsed["splits"]],
                    )
            if save(conn, symbol, "yahoo", interval, parsed["rows"] if parsed else [], today):
                counts["ok"] += 1
            else:
                counts["missing"].append(f"{symbol}/{interval}")
            time.sleep(PAUSE_S)
    except Blocked as e:
        counts["stopped"] = str(e)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--symbols", nargs="*", help="names to fetch (default: every candidate and counterpart)")
    ap.add_argument("--source", choices=("broker", "yahoo", "both"), default="both")
    ap.add_argument("--force", action="store_true", help="refetch series already fetched today")
    ap.add_argument("--db", type=Path, default=data_dir("research") / "item26" / "bars.db")
    args = ap.parse_args(argv)

    names = args.symbols or candidates()
    daily = sorted(set(names) | {COUNTERPART[s] for s in names if s in COUNTERPART})
    intraday = [s for s in INTRADAY if not args.symbols or s in args.symbols]
    today = date.today().isoformat()
    conn = connect(args.db)
    out: dict = {"db": str(args.db), "daily": len(daily), "intraday": intraday}
    if args.source in ("broker", "both"):
        out["broker"] = fetch_broker(conn, daily, intraday, today, args.force)
    if args.source in ("yahoo", "both"):
        out["yahoo"] = fetch_yahoo(conn, daily, intraday, today, args.force)
    print(json.dumps(out))
    return 1 if "stopped" in out.get("yahoo", {}) else 0


if __name__ == "__main__":
    sys.exit(main())
