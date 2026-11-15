"""Fetch the bars item 88 reads: daily bars of its ETFs from the broker, hourly gold futures from Yahoo.

Item 88 (research/edge-hypotheses-plan.md) judges the sweep-and-engulf candle rule on daily bars of a
declared list of liquid, weekly-optioned ETFs, and reports a 4-hour gold replication. This writes both
inputs into one SQLite file under the home (`data/public/item88/bars.db`), never the repo:

- `etf_daily`: the broker's daily candles for each fund in `ETFS`, as far back as the feed serves,
  asked with extended hours (the feed's daily candle is still the official session: GLD's matched
  Yahoo's open, high, low and close on all 53 sessions checked on 2026-11-14, while a request without
  extended hours came back shallow and late). A daily candle is stamped at midnight UTC of its session,
  so `day` is that UTC date.
- `yahoo_gc_1h`: Yahoo's hourly `GC=F` bars, the front month as Yahoo rolls it (silently), for the
  span before the broker's own hourly bars begin (the broker keeps about 9.5 months; the dated
  contracts the gold bar recorder keeps cover the rest).

A bar with a missing or zero price is dropped. Each run replaces the file's rows for what it fetched,
so a rerun is the same fetch. Read-only against the broker. A script, not package code, because it
reaches the network; the study (`packages/backtest/scripts/engulf_item88.py`) reads the file only.

Exit non-zero on missing credentials, a failed fetch, or any fund or the Yahoo series coming back
empty, so the research queue retries.

    python scripts/fetch_item88_bars.py [--skip-yahoo]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.core import streamcache
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

#: The declared funds (item 88): US equity index, international, bonds, metals, oil and sectors, each
#: with weekly options. Today's list, so a hindsight label (declared).
ETFS = (
    "SPY", "QQQ", "IWM", "DIA", "EFA", "EEM", "TLT", "GLD",
    "SLV", "USO", "XLE", "XLF", "XLK", "XLU", "XLV", "XLI",
)  # fmt: skip
START = datetime(1999, 1, 1, tzinfo=UTC)
QUIET_GAP_S = 5.0
MAX_WAIT_S = 600.0
REMOVE = 0x02
YAHOO_SYMBOL = "GC=F"

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS etf_daily (symbol TEXT NOT NULL, day TEXT NOT NULL, ts_ms INTEGER NOT NULL, "
    "open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL, "
    "PRIMARY KEY (symbol, day))",
    "CREATE TABLE IF NOT EXISTS yahoo_gc_1h (ts_ms INTEGER PRIMARY KEY, open REAL NOT NULL, "
    "high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS fetched (what TEXT PRIMARY KEY, at REAL NOT NULL, rows INTEGER NOT NULL, "
    "first TEXT, last TEXT)",
)


def db_path() -> Path:
    return _home.data_dir("public") / "item88" / "bars.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    if path is None:
        path = db_path()
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    for ddl in SCHEMA:
        conn.execute(ddl)
    return conn


def utc_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).date().isoformat()


async def fetch_daily(session, symbols, sdk=None) -> dict[str, dict[int, dict]]:
    """Daily candles per symbol, extended hours on; REMOVE events and priceless candles dropped."""
    if sdk is None:
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Candle
    else:
        DXLinkStreamer, Candle = sdk
    bars: dict[str, dict[int, dict]] = {}
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe_candle(
            list(symbols), interval="1d", start_time=START, extended_trading_hours=True
        )
        started = time.monotonic()
        while time.monotonic() - started < MAX_WAIT_S:
            try:
                event = await asyncio.wait_for(streamer.get_event(Candle), timeout=QUIET_GAP_S)
            except TimeoutError:
                if bars:
                    break
                continue
            label = str(event.event_symbol or "")
            base = label.split("{", 1)[0]
            if base not in symbols:
                continue
            stamp = streamcache.to_float(event.time)
            if stamp is None:
                continue
            ts = int(stamp)
            if int(event.event_flags or 0) & REMOVE:
                bars.get(base, {}).pop(ts, None)
                continue
            px = {k: streamcache.to_float(getattr(event, k, None)) for k in ("open", "high", "low", "close")}
            if not all(v is not None and v > 0 for v in px.values()):
                continue
            vol = streamcache.to_float(getattr(event, "volume", None))
            px["volume"] = vol if vol is not None and vol > 0 else 0.0
            bars.setdefault(base, {})[ts] = px
    return bars


def store_daily(conn: sqlite3.Connection, symbol: str, bars: dict[int, dict], today: str) -> list[str]:
    """Replace the symbol's rows with the finished sessions fetched (today's is still forming)."""
    rows = sorted(
        (symbol, utc_day(ts), ts, b["open"], b["high"], b["low"], b["close"], b["volume"])
        for ts, b in bars.items()
        if utc_day(ts) < today
    )
    conn.execute("DELETE FROM etf_daily WHERE symbol = ?", (symbol,))
    conn.executemany("INSERT INTO etf_daily VALUES (?,?,?,?,?,?,?,?)", rows)
    return [r[1] for r in rows]


def fetch_yahoo_hourly() -> list[tuple]:
    """Yahoo's hourly GC=F bars over the longest span it serves (730 days)."""
    import yfinance as yf

    frame = yf.Ticker(YAHOO_SYMBOL).history(period="730d", interval="1h", auto_adjust=False)
    out = []
    for stamp, r in frame.iterrows():
        px = (float(r.Open), float(r.High), float(r.Low), float(r.Close))
        if all(v > 0 for v in px):
            out.append((int(stamp.timestamp() * 1000), *px, float(r.Volume or 0.0)))
    return out


def store_yahoo(conn: sqlite3.Connection, rows: list[tuple], now_ms: int) -> list[tuple]:
    """Replace the series with the finished hours fetched (an hour still forming is left out)."""
    done = [r for r in rows if r[0] + 3_600_000 <= now_ms]
    conn.execute("DELETE FROM yahoo_gc_1h")
    conn.executemany("INSERT INTO yahoo_gc_1h VALUES (?,?,?,?,?,?)", done)
    return done


def record(conn: sqlite3.Connection, what: str, n: int, first: str | None, last: str | None) -> None:
    conn.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?)", (what, time.time(), n, first, last))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-yahoo", action="store_true", help="fetch the funds only")
    args = ap.parse_args(argv)
    store = CredentialStore(SHARED_SERVICE)
    missing = store.missing_secrets()
    if missing:
        print(json.dumps({"ok": False, "reason": "credentials_missing", "missing": list(missing)}))
        return 1
    now = datetime.now(UTC)
    today = now.date().isoformat()
    conn = connect()
    summary: dict = {"funds": {}, "empty": []}
    try:
        try:
            session = SessionManager(store).get_session()
            bars = asyncio.run(fetch_daily(session, ETFS))
        except Exception as exc:  # noqa: BLE001 -- a failed fetch keeps the rows already held
            print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
            return 1
        for sym in ETFS:
            days = store_daily(conn, sym, bars.get(sym, {}), today) if bars.get(sym) else []
            if not days:
                summary["empty"].append(sym)
                continue
            record(conn, f"etf_daily:{sym}", len(days), days[0], days[-1])
            summary["funds"][sym] = {"sessions": len(days), "first": days[0], "last": days[-1]}
        if not args.skip_yahoo:
            try:
                rows = store_yahoo(conn, fetch_yahoo_hourly(), int(now.timestamp() * 1000))
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"ok": False, "reason": f"yahoo: {type(exc).__name__}: {exc}"}))
                return 1
            if not rows:
                summary["empty"].append(YAHOO_SYMBOL)
            else:
                first, last = utc_day(rows[0][0]), utc_day(rows[-1][0])
                record(conn, "yahoo_gc_1h", len(rows), first, last)
                summary["yahoo_gc_1h"] = {"hours": len(rows), "first": first, "last": last}
        conn.commit()
    finally:
        conn.close()
    ok = not summary["empty"]
    print(json.dumps({"ok": ok, "db": str(db_path()), **summary}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
