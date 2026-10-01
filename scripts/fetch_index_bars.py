"""Fetch daily bars for the cash indexes the technicals store charts (SPX), from the broker's feed.

**Why.** The technicals store lands its bars from the local Dolt `stocks` clone, which carries no
cash indexes: SPX, NDX and VIX are its `not_in_dolt`. The stream cache does hold SPX daily rows, but
they are the live producer's and were not clean enough to chart from: on 2026-10-01 a receipt-date
keying bug had left SPX's 09-28 close frozen at 09-25's (fixed and repaired since, but the store this
feeds is matched to the vendor to the cent, and wants a source whose every bar is a finished one).
DXLink's daily candles are that source -- the same series the producer's history backfill uses.

A script, not package code, because it reaches the broker; `packages/technicals` stays network-free
and reads the file this writes. Read-only against the broker.

**When.** 06:00 ET, ahead of the 06:15 landing, so the previous session's candle is final and today's
partial one is simply not yet a bar (`streamcache.summary_backfill_rows` drops it, and any bar with
no real close). A symbol the file has never held is fetched three years back, the depth the vendor's
charts use; after that, the last `RESTATE_DAYS` are refetched so a corrected candle lands.

**Gaps.** The feed's daily series can skip finished sessions: on 2026-10-01 SPX had no daily candle
for 09-28 or 09-29, though its hourly candles for both were there. A missing session is rebuilt from
its hourly candles (open of the first, highest high, lowest low, close of the last) -- but only when
that aggregation reproduces the feed's own daily bar, to the cent, on every day both cover in the
same request. Otherwise the gap stays, and is reported, never invented. `source` says which a bar is.

    python scripts/fetch_index_bars.py [--full]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import calendar as _cal
from cherrypick.core import streamcache
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager
from cherrypick.technicals import paths, symbols

_ET = ZoneInfo("America/New_York")
FIRST_DAYS = 3 * 366 + 30
RESTATE_DAYS = 20
# The candle burst is delivered front-loaded: stop once it has gone quiet, never wait past the cap.
QUIET_GAP_S = 3.0
MAX_WAIT_S = 90.0
# An hourly-built bar must equal the daily bar to within half a cent on every overlapping day.
SAME_TOLERANCE = 0.005

SCHEMA = """
CREATE TABLE IF NOT EXISTS index_bars (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'daily',
    fetched_at REAL NOT NULL,
    PRIMARY KEY (symbol, date))
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or paths.index_bars()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(index_bars)")}
    if "source" not in cols:  # a file written before gap-filling existed
        conn.execute("ALTER TABLE index_bars ADD COLUMN source TEXT NOT NULL DEFAULT 'daily'")
    return conn


def latest(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT symbol, MAX(date) FROM index_bars GROUP BY symbol").fetchall())


def start_for(held: dict[str, str], wanted, today: date, full: bool = False) -> date:
    """The earliest date to ask for: three years for any symbol not yet held (or `full`), else the
    restatement window behind the oldest of the newest bars held."""
    starts = []
    for sym in wanted:
        if full or sym not in held:
            starts.append(today - timedelta(days=FIRST_DAYS))
        else:
            starts.append(date.fromisoformat(held[sym]) - timedelta(days=RESTATE_DAYS))
    return min(starts)


def store_bars(conn: sqlite3.Connection, symbol: str, raw: list[dict], today: str, source: str = "daily") -> int:
    """Normalise with the producer's own rules (sorted, deduped, finished sessions only, no bar
    without a real close) and upsert: a refetched candle replaces the one held."""
    rows = streamcache.summary_backfill_rows(raw, today=today)
    now = time.time()
    conn.executemany(
        "INSERT INTO index_bars (symbol, date, open, high, low, close, source, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(symbol, date) DO UPDATE SET open=excluded.open, high=excluded.high, "
        "low=excluded.low, close=excluded.close, source=excluded.source, fetched_at=excluded.fetched_at",
        [
            (symbol, r["trade_date"], r["day_open"], r["day_high"], r["day_low"], r["day_close"], source, now)
            for r in rows
        ],
    )
    return len(rows)


def missing_sessions(have: set[str], start: date, today: date) -> list[str]:
    """Finished trading sessions from `start` up to (not including) `today` with no bar."""
    out, day = [], start
    while day < today:
        if _cal.is_trading_day(day) and day.isoformat() not in have:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def aggregate_hourly(candles: list[dict]) -> dict[str, dict]:
    """Hourly candles ({ts, open, high, low, close}, ts in epoch ms) into one bar per ET date.
    A candle with no real price (the feed emits zero-valued ones) is not part of any session."""
    by_day: dict[str, list[dict]] = {}
    for c in sorted(candles, key=lambda c: c["ts"]):
        if not all((c.get(k) or 0) > 0 for k in ("open", "high", "low", "close")):
            continue
        day = datetime.fromtimestamp(c["ts"] / 1000.0, tz=_ET).date().isoformat()
        by_day.setdefault(day, []).append(c)
    return {
        day: {
            "date": day,
            "open": cs[0]["open"],
            "high": max(c["high"] for c in cs),
            "low": min(c["low"] for c in cs),
            "close": cs[-1]["close"],
        }
        for day, cs in by_day.items()
    }


def _same(a: dict, b: dict) -> bool:
    return all(
        a.get(k) is not None and b.get(k) is not None and abs(a[k] - b[k]) < SAME_TOLERANCE
        for k in ("open", "high", "low", "close")
    )


def gap_fill(daily: list[dict], hourly: dict[str, dict], gaps: list[str]) -> tuple[list[dict], dict]:
    """The hourly-built bars for `gaps`, and a verdict. Only if the hourly aggregation reproduces
    every daily bar it overlaps (at least one) is any gap filled from it."""
    by_date = {b["date"]: b for b in daily}
    overlap = [d for d in hourly if d in by_date]
    agree = [d for d in overlap if _same(hourly[d], by_date[d])]
    verdict = {"overlap": len(overlap), "agree": len(agree)}
    if not overlap or len(agree) != len(overlap):
        return [], {**verdict, "filled": [], "unfilled": gaps, "reason": "hourly does not reproduce the daily bars"}
    filled = [hourly[d] for d in gaps if d in hourly]
    return filled, {**verdict, "filled": [b["date"] for b in filled], "unfilled": [d for d in gaps if d not in hourly]}


async def fetch(session, wanted: list[str], start: date, interval: str = "1d") -> dict[str, list[dict]]:
    """Candles per symbol, each with its `ts` and dated as the producer dates a daily candle."""
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Candle

    bars: dict[str, list[dict]] = {}
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe_candle(
            wanted, interval=interval, start_time=datetime.combine(start, datetime.min.time(), tzinfo=UTC)
        )
        listener = streamer.listen(Candle).__aiter__()
        deadline = time.monotonic() + MAX_WAIT_S
        while time.monotonic() < deadline:
            try:
                event = await asyncio.wait_for(listener.__anext__(), timeout=QUIET_GAP_S)
            except TimeoutError:
                if bars:
                    break
                continue
            except StopAsyncIteration:
                break
            base = str(event.event_symbol or "").split("{", 1)[0]
            stamp = streamcache.to_float(event.time)
            if base not in wanted or stamp is None:
                continue
            bars.setdefault(base, []).append(
                {
                    "ts": stamp,
                    # The producer's own dating of a daily candle (core.streamer's history backfill).
                    "date": datetime.fromtimestamp(stamp / 1000.0, tz=UTC).date().isoformat(),
                    "open": streamcache.to_float(event.open),
                    "high": streamcache.to_float(event.high),
                    "low": streamcache.to_float(event.low),
                    "close": streamcache.to_float(event.close),
                }
            )
    return bars


async def collect(session, wanted: list[str], start: date, today: date, held: dict[str, set[str]]):
    """Daily candles, then -- only if some finished session has no bar -- the hourly ones that can
    rebuild it. Returns (daily, hourly, gaps by symbol)."""
    daily = await fetch(session, wanted, start)
    gaps = {}
    for sym in wanted:
        have = held.get(sym, set()) | {b["date"] for b in daily.get(sym, []) if (b.get("close") or 0) > 0}
        found = missing_sessions(have, start, today)
        if found:
            gaps[sym] = found
    hourly: dict[str, list[dict]] = {}
    if gaps:
        since = date.fromisoformat(min(g[0] for g in gaps.values())) - timedelta(days=7)
        hourly = await fetch(session, list(gaps), since, "1h")
    return daily, hourly, gaps


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--full", action="store_true", help="refetch three years for every symbol")
    args = ap.parse_args(argv)

    store = CredentialStore(SHARED_SERVICE)
    missing = store.missing_secrets()
    if missing:
        print(json.dumps({"ok": False, "reason": "credentials_missing", "missing": list(missing)}))
        return 1

    wanted = list(symbols.INDEXES)
    today = datetime.now(_ET).date()
    conn = connect()
    try:
        start = start_for(latest(conn), wanted, today, args.full)
        held_dates = {
            sym: {r[0] for r in conn.execute("SELECT date FROM index_bars WHERE symbol = ?", (sym,))} for sym in wanted
        }
        try:
            session = SessionManager(store).get_session()
            # One event loop for both requests: the session binds to the loop it was first used on.
            bars, hourly_raw, gaps = asyncio.run(collect(session, wanted, start, today, held_dates))
        except Exception as exc:  # noqa: BLE001 -- a failed fetch keeps every bar already held
            print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
            return 1
        result = {}
        for sym in wanted:
            daily = bars.get(sym, [])
            rec: dict = {"candles": len(daily), "stored": store_bars(conn, sym, daily, today.isoformat())}
            if gaps.get(sym):
                rec["gaps"] = gaps[sym]
                filled, verdict = gap_fill(daily, aggregate_hourly(hourly_raw.get(sym, [])), gaps[sym])
                store_bars(conn, sym, filled, today.isoformat(), source="hourly")
                rec["gap_fill"] = verdict
            result[sym] = rec
        conn.commit()
        held = latest(conn)
    finally:
        conn.close()
    ok = all(r["stored"] > 0 for r in result.values())
    print(json.dumps({"ok": ok, "from": start.isoformat(), "symbols": result, "latest": held}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
