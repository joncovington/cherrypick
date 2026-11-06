"""Record intraday bars of the VIX term structure from the broker's candle feed.

**Why.** DXLink keeps only about three months of 5-minute candles and about ten months of hourly ones
(probed 2026-11-05: 5m back to 2026-08-10, 1h back to 2026-01-26, both bursts ending with the
SNAP_SNIP flag, i.e. clipped by the feed). Nothing else in the suite saves intraday index bars --
`fetch_index_bars.py` saves daily ones -- so a study of how the curve moves inside a session has only
what is saved from here on. This script saves it, a session at a time.

**What.** VIX1D, VIX9D, VIX, VIX3M, VIX6M, VIX1Y, VVIX and SPX, at 5 minutes and 1 hour, regular hours
only (`extended_trading_hours=False`). One SQLite file under the market-files data folder, keyed
(symbol, interval, bar start in epoch ms), with the ET day and ET hh:mm beside it.

**What is never stored.** A bar with a missing or zero price (the SDK turns a missing price into 0), a
REMOVE event, a bar still in progress (its start plus its width is later than now). A refetched bar
replaces the one held, so a corrected candle lands.

**Depth.** A (symbol, interval) the file has never held is fetched as far back as the feed gives
(`DEPTH_DAYS`); `--full` does that for all of them. After that only the last `RESTATE_DAYS` are
refetched. The summary says the earliest bar received and whether the feed flagged the burst
SNAP_SNIP (clipped).

Read-only against the broker. A script, not package code, because it reaches the network. Run after
the indexes' final prints (16:15 ET).

    python scripts/fetch_vol_curve_bars.py [--full]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home
from cherrypick.core import streamcache
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

_ET = ZoneInfo("America/New_York")
SYMBOLS = ("VIX1D", "VIX9D", "VIX", "VIX3M", "VIX6M", "VIX1Y", "VVIX", "SPX")
WIDTH_MS = {"5m": 5 * 60_000, "1h": 60 * 60_000}
# How far back the feed gives, with a margin: it clips to about 3 months of 5m and 10 months of 1h.
DEPTH_DAYS = {"5m": 120, "1h": 400}
RESTATE_DAYS = 5
# The candle burst is front-loaded: stop once it has gone quiet, never wait past the cap.
QUIET_GAP_S = 3.0
MAX_WAIT_S = 300.0
REMOVE = 0x02
SNAP_SNIP = 0x10

SCHEMA = """
CREATE TABLE IF NOT EXISTS vol_curve_bars (
    symbol TEXT NOT NULL, interval TEXT NOT NULL, ts_ms INTEGER NOT NULL,
    et_day TEXT NOT NULL, et_time TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (symbol, interval, ts_ms))
"""


def db_path() -> Path:
    return _home.data_dir("market-files") / "vol_curve_bars.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    if path is None:
        path = db_path()
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    return conn


def latest(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """The newest bar start held per (symbol, interval)."""
    rows = conn.execute("SELECT symbol, interval, MAX(ts_ms) FROM vol_curve_bars GROUP BY symbol, interval")
    return {(s, i): ts for s, i, ts in rows}


def start_for(held_ts: int | None, interval: str, now: datetime, full: bool = False) -> datetime:
    """Where to ask from: the feed's full depth for a series never held (or `full`), else the
    restatement window behind the newest bar held."""
    if full or held_ts is None:
        return now - timedelta(days=DEPTH_DAYS[interval])
    return datetime.fromtimestamp(held_ts / 1000.0, tz=UTC) - timedelta(days=RESTATE_DAYS)


def period_key(label: str) -> str | None:
    """The bar width a candle label names, as `5m` / `1h`. dxFeed drops a multiplier of 1, so a
    one-hour bar arrives as `{=h}`; a label with no period is not a candle we asked for."""
    inner = label.split("{", 1)[1].rstrip("}") if "{" in label else ""
    period = next((p[1:] for p in inner.split(",") if p.startswith("=")), None)
    if not period:
        return None
    return period if period[0].isdigit() else f"1{period}"


async def fetch(session, starts: dict[str, datetime], interval: str, sdk=None):
    """Candles per symbol for one interval: ({symbol: {ts_ms: bar}}, {symbols the feed clipped}).
    A symbol is requested from its own start. REMOVE events and priceless candles are dropped."""
    if sdk is None:
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Candle
    else:
        DXLinkStreamer, Candle = sdk
    bars: dict[str, dict[int, dict]] = {}
    clipped: set[str] = set()
    async with DXLinkStreamer(session) as streamer:
        by_start: dict[datetime, list[str]] = {}
        for sym, start in starts.items():
            by_start.setdefault(start, []).append(sym)
        for start, syms in by_start.items():
            await streamer.subscribe_candle(
                syms, interval=interval, start_time=start, extended_trading_hours=False
            )
        started = time.monotonic()
        while time.monotonic() - started < MAX_WAIT_S:
            # get_event per wait: a wait_for timeout around a listen() generator cancels the generator.
            try:
                event = await asyncio.wait_for(streamer.get_event(Candle), timeout=QUIET_GAP_S)
            except TimeoutError:
                if bars:
                    break
                continue
            label = str(event.event_symbol or "")
            base = label.split("{", 1)[0]
            if base not in starts or period_key(label) != interval:
                continue
            flags = int(event.event_flags or 0)
            if flags & SNAP_SNIP:
                clipped.add(base)
            stamp = streamcache.to_float(event.time)
            if stamp is None:
                continue
            ts = int(stamp)
            if flags & REMOVE:
                bars.get(base, {}).pop(ts, None)
                continue
            px = {k: streamcache.to_float(getattr(event, k, None)) for k in ("open", "high", "low", "close")}
            if not all(v is not None and v > 0 for v in px.values()):
                continue
            bars.setdefault(base, {})[ts] = px
    return bars, clipped


def store_bars(
    conn: sqlite3.Connection, symbol: str, interval: str, bars: dict[int, dict], now: datetime
) -> list[int]:
    """Upsert the finished bars; returns their starts. A bar whose start plus width is later than
    `now` is still forming and is left for the next run."""
    now_ms = now.timestamp() * 1000.0
    done = sorted(ts for ts in bars if ts + WIDTH_MS[interval] <= now_ms)
    fetched = time.time()
    rows = []
    for ts in done:
        et = datetime.fromtimestamp(ts / 1000.0, tz=_ET)
        b = bars[ts]
        rows.append(
            (
                symbol,
                interval,
                ts,
                et.date().isoformat(),
                et.strftime("%H:%M"),
                b["open"],
                b["high"],
                b["low"],
                b["close"],
                fetched,
            )
        )
    conn.executemany(
        "INSERT INTO vol_curve_bars (symbol, interval, ts_ms, et_day, et_time, open, high, low, close, "
        "fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(symbol, interval, ts_ms) DO UPDATE SET et_day=excluded.et_day, "
        "et_time=excluded.et_time, open=excluded.open, high=excluded.high, low=excluded.low, "
        "close=excluded.close, fetched_at=excluded.fetched_at",
        rows,
    )
    return done


def _et_stamp(ts: int | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts / 1000.0, tz=_ET).strftime("%Y-%m-%d %H:%M")


async def run(session, conn: sqlite3.Connection, now: datetime, full: bool = False, sdk=None) -> list[dict]:
    """One fetch per interval (one DXLink session, one event loop), stored as it arrives."""
    held = latest(conn)
    out = []
    for interval in WIDTH_MS:
        starts = {s: start_for(held.get((s, interval)), interval, now, full) for s in SYMBOLS}
        deep = {s for s in SYMBOLS if full or (s, interval) not in held}
        bars, clipped = await fetch(session, starts, interval, sdk)
        for sym in SYMBOLS:
            got = bars.get(sym, {})
            written = store_bars(conn, sym, interval, got, now)
            out.append(
                {
                    "symbol": sym,
                    "interval": interval,
                    "received": len(got),
                    "written": len(written),
                    "first": _et_stamp(written[0] if written else None),
                    "last": _et_stamp(written[-1] if written else None),
                    "earliest_received": _et_stamp(min(got) if got else None),
                    "deep": sym in deep,
                    "clipped": sym in clipped,
                }
            )
        conn.commit()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="refetch the feed's full depth for every series")
    args = ap.parse_args(argv)

    store = CredentialStore(SHARED_SERVICE)
    missing = store.missing_secrets()
    if missing:
        print(json.dumps({"ok": False, "reason": "credentials_missing", "missing": list(missing)}))
        return 1
    conn = connect()
    try:
        try:
            session = SessionManager(store).get_session()
            summary = asyncio.run(run(session, conn, datetime.now(UTC), args.full))
        except Exception as exc:  # noqa: BLE001 -- a failed fetch keeps every bar already held
            print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
            return 1
    finally:
        conn.close()
    empty = [f"{r['symbol']} {r['interval']}" for r in summary if r["received"] == 0]
    print(json.dumps({"ok": not empty, "empty": empty, "db": str(db_path()), "series": summary}, indent=2))
    return 0 if not empty else 1


if __name__ == "__main__":
    sys.exit(main())
