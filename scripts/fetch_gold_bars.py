"""Record 1-hour and daily bars of gold futures and GLD from the broker's candle feed.

**Why.** The engulf work (#1171) reads 4-hour bars of gold futures, built from 1-hour ones, and daily
bars of GLD. The feed keeps about 9.5 months of 1-hour bars (probed 2026-11-14: `/GC:XCEC` and GLD
both clipped at 2026-02-02) and daily bars back to 2000 for the continuous future and 2004 for GLD.
Nothing else in the suite saves them, so this does, a day at a time. One-minute bars are not needed:
the rule's finest bar is the hour.

**What.** Three series, each at `1h` and `1d`, in one SQLite file, `gold_bars.db` in the market-files
data folder, keyed (symbol, interval, bar start in epoch ms), with the session day, the ET time, the
contract each bar came from and its volume:

- `/GC`: the futures map's active GC month (`state/futures_contracts.json`), never assembled here, as
  the Market Profile recorder does for /ES. A refetched bar replaces the one held only when it comes
  from the same contract, so after a roll the restatement window cannot overwrite the old month's bars
  with the new one's prices. Its history starts the day it is first recorded: a month that has just
  become active traded thinly, at another price, before it was the front.
- `/GC:XCEC`: the feed's continuous front-month series, stored under that name. It gives the depth
  `/GC` lacks (the EMA's warm-up, the study's history). It is not adjusted at a roll, so a roll is a
  real jump inside it, and nothing in it says which bar a roll fell on.
- `GLD`: the fund.

**Hours.** Every request includes extended hours: the 1-hour bars carry a future's night and the
fund's pre- and post-market. The feed's daily candle is the official session either way: GLD's matched
Yahoo's open, high, low and close on all 53 sessions from 2026-09-01 (checked 2026-11-14). Asked
without extended hours, the feed served GLD's daily bars only from 2022-12-29 and two sessions late.

**Session day.** The feed stamps a daily candle at midnight UTC of its session (19:00 or 20:00 ET the
evening before), so a daily bar's `et_day` is that UTC date; an hourly bar's is its ET date.

**What is never stored.** A bar with a missing or zero price, a REMOVE event, a bar still forming (its
start plus its width is later than now).

**Depth.** A (symbol, interval) the file has never held is fetched from `DEPTH_DAYS` back, which the
feed cuts to what it keeps; `--full` does that for all of them. `/GC` is the exception: it never goes
deeper than the restatement window, for the reason above. After that only the last `RESTATE_DAYS` are
refetched. The summary says the earliest bar received and whether the feed flagged
the burst SNAP_SNIP (clipped).

**Exit.** Non-zero on a failed fetch, missing credentials, an unreadable futures map, or any series
coming back with no bars at all (all three trade every session), so the job retries.

Read-only against the broker. A script, not package code, because it reaches the network. Run after
GLD's extended hours end at 20:00 ET, so the day's last hourly bars are finished.

    python scripts/fetch_gold_bars.py [--full]
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
#: stored name -> what it is fetched from: a product code in the futures map (`map:`), or its own
#: streamer symbol.
SERIES = {"/GC": "map:GC", "/GC:XCEC": "/GC:XCEC", "GLD": "GLD"}
SYMBOLS = tuple(SERIES)
WIDTH_MS = {"1h": 3_600_000, "1d": 86_400_000}
#: Further back than the feed gives, so the feed's own clip decides the depth.
DEPTH_DAYS = {"1h": 400, "1d": 365 * 27}
RESTATE_DAYS = 5
# The candle burst is front-loaded: stop once it has gone quiet, never wait past the cap.
QUIET_GAP_S = 3.0
MAX_WAIT_S = 300.0
REMOVE = 0x02
SNAP_SNIP = 0x10

SCHEMA = """
CREATE TABLE IF NOT EXISTS gold_bars (
    symbol TEXT NOT NULL, interval TEXT NOT NULL, ts_ms INTEGER NOT NULL, contract TEXT NOT NULL,
    et_day TEXT NOT NULL, et_time TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume REAL NOT NULL, fetched_at REAL NOT NULL,
    PRIMARY KEY (symbol, interval, ts_ms))
"""


def db_path() -> Path:
    return _home.data_dir("market-files") / "gold_bars.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    if path is None:
        path = db_path()
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    return conn


def contracts(fmap: dict) -> dict[str, str]:
    """The streamer symbol each stored name is fetched from. A future is the map's active month (its
    only one when just one is listed); raises KeyError when the map has none, so the run fails rather
    than guessing one."""
    out = {}
    for name, source in SERIES.items():
        if not source.startswith("map:"):
            out[name] = source
            continue
        product = source[4:]
        listed = fmap["contracts"][product]
        active = [c for c in listed if c.get("active_month")] or listed[:1]
        if not active or not active[0].get("streamer_symbol"):
            raise KeyError(f"no contract for {product} in the futures map")
        out[name] = active[0]["streamer_symbol"]
    return out


def read_futures_map() -> dict:
    return json.loads((_home.state_dir() / "futures_contracts.json").read_text(encoding="utf-8"))


def latest(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """The newest bar start held per (symbol, interval)."""
    rows = conn.execute("SELECT symbol, interval, MAX(ts_ms) FROM gold_bars GROUP BY symbol, interval")
    return {(s, i): ts for s, i, ts in rows}


def start_for(
    held_ts: int | None, interval: str, now: datetime, full: bool = False, deep: bool = True
) -> datetime:
    """The feed's full depth for a series never held (or `full`), else the restatement window behind
    the newest bar held. A series that may not go deep (`deep` false: a futures-map month) starts at
    most the restatement window back."""
    if full or held_ts is None:
        if not deep:
            return now - timedelta(days=RESTATE_DAYS)
        return now - timedelta(days=DEPTH_DAYS[interval])
    return datetime.fromtimestamp(held_ts / 1000.0, tz=UTC) - timedelta(days=RESTATE_DAYS)


def period_key(label: str) -> str | None:
    """The bar width a candle label names, as `1h` / `1d`. dxFeed drops a multiplier of 1, so an hour
    arrives as `{=h}` and a day as `{=d}`; a label with no period is not a candle we asked for."""
    inner = label.split("{", 1)[1].rstrip("}") if "{" in label else ""
    period = next((p[1:] for p in inner.split(",") if p.startswith("=")), None)
    if not period:
        return None
    return period if period[0].isdigit() else f"1{period}"


def session_day(ts: int, interval: str) -> str:
    """A daily candle's session is the UTC date of its stamp (midnight UTC); an hourly bar's is its ET
    date."""
    if interval == "1d":
        return datetime.fromtimestamp(ts / 1000.0, tz=UTC).date().isoformat()
    return datetime.fromtimestamp(ts / 1000.0, tz=_ET).date().isoformat()


async def fetch(session, starts: dict[str, datetime], interval: str, sdk=None):
    """Candles per streamer symbol for one interval: ({symbol: {ts_ms: bar}}, {symbols clipped}). Each
    symbol is asked from its own start, extended hours included. REMOVE events and priceless candles
    are dropped; a missing volume is 0."""
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
                syms, interval=interval, start_time=start, extended_trading_hours=True
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
            vol = streamcache.to_float(getattr(event, "volume", None))
            px["volume"] = vol if vol is not None and vol > 0 else 0.0
            bars.setdefault(base, {})[ts] = px
    return bars, clipped


def store_bars(
    conn: sqlite3.Connection, symbol: str, interval: str, contract: str, bars: dict[int, dict], now: datetime
) -> list[int]:
    """Upsert the finished bars; returns the starts offered. A bar still forming is left for the next
    run. A bar already held from another contract is kept as it is (the roll rule, above)."""
    now_ms = now.timestamp() * 1000.0
    done = sorted(ts for ts in bars if ts + WIDTH_MS[interval] <= now_ms)
    fetched = time.time()
    rows = []
    for ts in done:
        b = bars[ts]
        et = datetime.fromtimestamp(ts / 1000.0, tz=_ET)
        rows.append(
            (
                symbol,
                interval,
                ts,
                contract,
                session_day(ts, interval),
                et.strftime("%H:%M"),
                b["open"],
                b["high"],
                b["low"],
                b["close"],
                b["volume"],
                fetched,
            )
        )
    conn.executemany(
        "INSERT INTO gold_bars (symbol, interval, ts_ms, contract, et_day, et_time, open, high, low, close, "
        "volume, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(symbol, interval, ts_ms) DO UPDATE SET et_day=excluded.et_day, "
        "et_time=excluded.et_time, open=excluded.open, high=excluded.high, low=excluded.low, "
        "close=excluded.close, "
        "volume=excluded.volume, fetched_at=excluded.fetched_at "
        "WHERE gold_bars.contract = excluded.contract",
        rows,
    )
    return done


def _et_stamp(ts: int | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts / 1000.0, tz=_ET).strftime("%Y-%m-%d %H:%M")


async def run_interval(
    session, conn: sqlite3.Connection, interval: str, now: datetime, fmap: dict, full: bool = False, sdk=None
) -> list[dict]:
    """One interval's fetch (one DXLink session) for every series, stored as it arrives."""
    feed = contracts(fmap)
    held = latest(conn)
    starts = {
        feed[s]: start_for(
            held.get((s, interval)), interval, now, full, deep=not SERIES[s].startswith("map:")
        )
        for s in SYMBOLS
    }
    bars, clipped = await fetch(session, starts, interval, sdk)
    out = []
    for sym in SYMBOLS:
        got = bars.get(feed[sym], {})
        offered = store_bars(conn, sym, interval, feed[sym], got, now)
        out.append(
            {
                "symbol": sym,
                "interval": interval,
                "contract": feed[sym],
                "received": len(got),
                "stored": len(offered),
                "first": _et_stamp(offered[0] if offered else None),
                "last": _et_stamp(offered[-1] if offered else None),
                "deep": full or (sym, interval) not in held,
                "clipped": feed[sym] in clipped,
            }
        )
    conn.commit()
    return out


def outcome(summary: list[dict]) -> dict:
    """`ok` false when any series came back with no bars: all three trade every session."""
    empty = [f"{r['symbol']} {r['interval']}" for r in summary if r["received"] == 0]
    return {"ok": not empty, "empty": empty}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="refetch the feed's full depth for every series")
    ap.add_argument("--interval", choices=sorted(WIDTH_MS), help="one interval only (default: both)")
    args = ap.parse_args(argv)

    store = CredentialStore(SHARED_SERVICE)
    missing = store.missing_secrets()
    if missing:
        print(json.dumps({"ok": False, "reason": "credentials_missing", "missing": list(missing)}))
        return 1
    try:
        fmap = read_futures_map()
        contracts(fmap)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "reason": f"futures map: {type(exc).__name__}: {exc}"}))
        return 1
    intervals = [args.interval] if args.interval else list(WIDTH_MS)
    conn = connect()
    summary: list[dict] = []
    try:
        try:
            session = SessionManager(store).get_session()

            async def _all():
                # One event loop for every interval: the session's HTTP client is bound to the loop it
                # first ran on, so a second asyncio.run in this process fails.
                for interval in intervals:
                    summary.extend(
                        await run_interval(session, conn, interval, datetime.now(UTC), fmap, args.full)
                    )

            asyncio.run(_all())
        except Exception as exc:  # noqa: BLE001 -- a failed fetch keeps every bar already held
            print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
            return 1
    finally:
        conn.close()
    verdict = outcome(summary)
    print(json.dumps({**verdict, "db": str(db_path()), "series": summary}, indent=2))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
