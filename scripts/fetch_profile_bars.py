"""Record 1-minute bars of the Market Profile symbols from the broker's candle feed.

**Why.** `cherrypick.core.marketprofile` builds a session's profile from intraday bars, and the
console's Intraday chart draws one from the 15-minute candles it streams. Neither keeps anything: the
feed hands back only its newest 8,000 one-minute bars (probed 2026-11-14, both bursts flagged
SNAP_SNIP: SPX back to 10-19, about 19 sessions; /ES back to 11-05, about six days, since a future
trades overnight). Composites and any study of profiles need more than that, so this saves the bars
a day at a time from now on.

**What.** SPX, SPY, NDX, QQQ, /ES and /NQ at 1 minute, extended hours included (a future's overnight
and an ETF's pre- and post-market are kept; the profile reads the regular session and ignores the
rest). Volume is kept: SPY, QQQ and the futures carry it, an index's is 0. One SQLite file,
`profile_bars.db` in the market-files data folder, keyed (symbol, bar start in epoch ms), with the
ET day and hh:mm beside it and the contract each bar came from.

**Futures.** /ES and /NQ are stored under those names, from the contract the futures map
(`state/futures_contracts.json`, `refresh_futures_contracts.py`) marks as the active month, never one
assembled here. A refetched bar replaces the one held only when it comes from the same contract, so
after a roll the restatement window cannot overwrite the old contract's bars with the new one's
prices (they differ by the calendar spread).

**What is never stored.** A bar with a missing or zero price, a REMOVE event, a bar still forming
(its start plus a minute is later than now).

**Depth.** A symbol the file has never held is fetched from `DEPTH_DAYS` back, which the feed's
8,000-bar cap cuts to what it has; `--full` does that for every symbol. After that the last
`RESTATE_DAYS` are refetched, well inside the cap even for a future, so a corrected candle lands.

**Five-minute backfill.** `--backfill-5m` is a one-off, not a job. The feed keeps 5-minute bars further
back than 1-minute ones (probed 2026-11-17: SPY and QQQ from 2026-08-20; /ES capped at 8,000 bars,
about six weeks), so one run saves the weeks before the minute recorder began. It asks the regular
session only: with extended hours the overnight bars use up the 8,000-bar cap by 2026-10-08, and the
profile reads the regular session anyway. They go to their own
table, `profile_bars_5m`, which nothing reads by default, so no 1-minute reader ever sees a 5-minute
bar. From the recorder's first day on, the 1-minute bars hold the same sessions at finer grain.

**Exit.** Non-zero on a failed fetch, missing credentials, an unreadable futures map, or any symbol
coming back with no bars at all (each of the six trades every session), so the job retries.

Read-only against the broker. A script, not package code, because it reaches the network. Run after
the futures' 17:00 ET close, so the day's last bars are finished.

    python scripts/fetch_profile_bars.py [--full | --backfill-5m]
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
from cherrypick.core import progress, streamcache
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

_ET = ZoneInfo("America/New_York")
CASH = ("SPX", "SPY", "NDX", "QQQ")
FUTURES = {"/ES": "ES", "/NQ": "NQ"}  # stored name -> product code in the futures map
SYMBOLS = (*CASH, *FUTURES)
INTERVAL = "1m"
WIDTH_MS = 60_000
# Further back than the feed gives: its 8,000-bar cap is what actually decides the depth.
DEPTH_DAYS = 40
# Two days of a future's minutes is under 3,000 bars, well inside the cap.
RESTATE_DAYS = 2
# The candle burst is front-loaded: stop once it has gone quiet, never wait past the cap.
QUIET_GAP_S = 3.0
MAX_WAIT_S = 300.0
REMOVE = 0x02
SNAP_SNIP = 0x10
# The feed hands back at most this many bars a request. It does not always say so: asked for 40 days
# of SPX on 2026-11-14 it returned exactly 8,000 from 10-19 and ended with SNAP_END, not SNAP_SNIP
# (SNAP_SNIP came only when asked for about 100 days). A symbol that reaches the cap was clipped.
FEED_CAP = 8000
TABLE = "profile_bars"
# The one-off five-minute backfill: its own table, and asked from further back than the feed holds.
BACKFILL_INTERVAL = "5m"
BACKFILL_WIDTH_MS = 300_000
BACKFILL_TABLE = "profile_bars_5m"
BACKFILL_DEPTH_DAYS = 150

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_bars (
    symbol TEXT NOT NULL, ts_ms INTEGER NOT NULL, contract TEXT NOT NULL,
    et_day TEXT NOT NULL, et_time TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume REAL NOT NULL, fetched_at REAL NOT NULL,
    PRIMARY KEY (symbol, ts_ms))
"""


def db_path() -> Path:
    return _home.data_dir("market-files") / "profile_bars.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    if path is None:
        path = db_path()
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.execute(SCHEMA.replace(TABLE, BACKFILL_TABLE, 1))  # the same columns, its own table
    return conn


def contracts(fmap: dict) -> dict[str, str]:
    """The streamer symbol each stored name is fetched from: a cash symbol is its own; a future is the
    map's active-month contract (its only one when just one is listed). Raises KeyError when the map
    has no contract for a product, so the run fails rather than guessing one."""
    out = {s: s for s in CASH}
    for name, product in FUTURES.items():
        listed = fmap["contracts"][product]
        active = [c for c in listed if c.get("active_month")] or listed[:1]
        if not active or not active[0].get("streamer_symbol"):
            raise KeyError(f"no contract for {product} in the futures map")
        out[name] = active[0]["streamer_symbol"]
    return out


def read_futures_map() -> dict:
    return json.loads((_home.state_dir() / "futures_contracts.json").read_text(encoding="utf-8"))


def latest(conn: sqlite3.Connection) -> dict[str, int]:
    """The newest bar start held per symbol."""
    return dict(conn.execute("SELECT symbol, MAX(ts_ms) FROM profile_bars GROUP BY symbol"))


def start_for(held_ts: int | None, now: datetime, full: bool = False) -> datetime:
    """The feed's full depth for a symbol never held (or `full`), else the restatement window behind
    the newest bar held."""
    if full or held_ts is None:
        return now - timedelta(days=DEPTH_DAYS)
    return datetime.fromtimestamp(held_ts / 1000.0, tz=UTC) - timedelta(days=RESTATE_DAYS)


def is_minute(label: str) -> bool:
    """dxFeed drops a multiplier of 1, so a 1-minute candle arrives labelled `{=m}`."""
    return is_interval(label, INTERVAL)


def is_interval(label: str, interval: str) -> bool:
    """Whether a candle label carries the asked width; a 1-minute one may arrive as `{=m}`."""
    inner = label.split("{", 1)[1].rstrip("}") if "{" in label else ""
    period = next((p[1:] for p in inner.split(",") if p.startswith("=")), None)
    return period == interval or (interval == "1m" and period == "m")


async def fetch(
    session, starts: dict[str, datetime], sdk=None, interval: str = INTERVAL, extended: bool = True
):
    """Candles per streamer symbol: ({streamer symbol: {ts_ms: bar}}, {symbols the feed clipped}).
    REMOVE events and priceless candles are dropped; a missing volume is 0. Clipped is the feed's
    SNAP_SNIP flag or a symbol that came back with `FEED_CAP` bars, since the flag is not always sent."""
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
                syms, interval=interval, start_time=start, extended_trading_hours=extended
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
            if base not in starts or not is_interval(label, interval):
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
    # The flag is not always sent; the count is what says the feed stopped at its cap.
    clipped.update(sym for sym, got in bars.items() if len(got) >= FEED_CAP)
    return bars, clipped


def store_bars(
    conn: sqlite3.Connection,
    symbol: str,
    contract: str,
    bars: dict[int, dict],
    now: datetime,
    table: str = TABLE,
    width_ms: int = WIDTH_MS,
) -> list[int]:
    """Upsert the finished bars; returns the starts offered. A bar still forming is left for the next
    run. A bar already held from another contract is kept as it is (see Futures, above)."""
    if table not in (TABLE, BACKFILL_TABLE):
        raise ValueError(f"unknown table {table!r}")
    now_ms = now.timestamp() * 1000.0
    done = sorted(ts for ts in bars if ts + width_ms <= now_ms)
    fetched = time.time()
    rows = []
    for ts in done:
        et = datetime.fromtimestamp(ts / 1000.0, tz=_ET)
        b = bars[ts]
        rows.append(
            (
                symbol,
                ts,
                contract,
                et.date().isoformat(),
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
        f"INSERT INTO {table} (symbol, ts_ms, contract, et_day, et_time, open, high, low, close, volume, "
        "fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(symbol, ts_ms) DO UPDATE SET et_day=excluded.et_day, et_time=excluded.et_time, "
        "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
        "volume=excluded.volume, fetched_at=excluded.fetched_at "
        f"WHERE {table}.contract = excluded.contract",
        rows,
    )
    return done


def _et_stamp(ts: int | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts / 1000.0, tz=_ET).strftime("%Y-%m-%d %H:%M")


async def run(
    session,
    conn: sqlite3.Connection,
    now: datetime,
    fmap: dict,
    full: bool = False,
    sdk=None,
    rep=None,
    backfill: bool = False,
) -> list[dict]:
    """One fetch (one DXLink session) for every symbol, stored as it arrives. `rep` is a progress
    Reporter. `backfill` asks 5-minute bars from as far back as the feed holds, into their own table."""
    feed = contracts(fmap)
    held = latest(conn)
    if backfill:
        starts = {feed[s]: now - timedelta(days=BACKFILL_DEPTH_DAYS) for s in SYMBOLS}
        interval, table, width_ms = BACKFILL_INTERVAL, BACKFILL_TABLE, BACKFILL_WIDTH_MS
    else:
        starts = {feed[s]: start_for(held.get(s), now, full) for s in SYMBOLS}
        interval, table, width_ms = INTERVAL, TABLE, WIDTH_MS
    if rep is not None:
        rep.split("symbols", SYMBOLS, unit="symbols")
        rep.at("symbols", "fetching")
    bars, clipped = await fetch(session, starts, sdk, interval, extended=not backfill)
    out = []
    for sym in SYMBOLS:
        if rep is not None:
            rep.advance("symbols", key=sym, current=sym)
        got = bars.get(feed[sym], {})
        offered = store_bars(conn, sym, feed[sym], got, now, table, width_ms)
        out.append(
            {
                "symbol": sym,
                "contract": feed[sym],
                "received": len(got),
                "stored": len(offered),
                "first": _et_stamp(offered[0] if offered else None),
                "last": _et_stamp(offered[-1] if offered else None),
                "deep": backfill or full or sym not in held,
                "clipped": feed[sym] in clipped,
            }
        )
    conn.commit()
    return out


def outcome(summary: list[dict]) -> dict:
    """`ok` false when any symbol came back with no bars: all six trade every session."""
    empty = [r["symbol"] for r in summary if r["received"] == 0]
    return {"ok": not empty, "empty": empty}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true", help="refetch the feed's full depth for every symbol")
    mode.add_argument(
        "--backfill-5m", action="store_true", help="one-off: save the feed's 5-minute bars to profile_bars_5m"
    )
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
    conn = connect()
    rep = progress.Reporter("fetch_profile_bars")
    try:
        try:
            session = SessionManager(store).get_session()
            summary = asyncio.run(
                run(session, conn, datetime.now(UTC), fmap, args.full, rep=rep, backfill=args.backfill_5m)
            )
        except Exception as exc:  # noqa: BLE001 -- a failed fetch keeps every bar already held
            rep.finish("failed")
            print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
            return 1
    finally:
        conn.close()
    rep.finish("done")
    verdict = outcome(summary)
    print(json.dumps({**verdict, "db": str(db_path()), "series": summary}, indent=2))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
