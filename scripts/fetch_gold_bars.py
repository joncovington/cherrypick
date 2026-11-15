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
- **Each dated GC contract under its own name** (`/GCZ26:XCEC`), the raw material for a front-month
  series with known roll dates. Each run records the map's active month and the next one in the
  cycle (`CYCLE`, the months gold trades actively), so both sides of the next roll are held. A dated
  contract is its own series, so its whole history is kept, thin early bars included: which contract
  is the front on a given session is a rule of the reader's (the engulf package's), never decided
  here. `--backfill-contracts` records every cycle contract from `BACKFILL_FROM_YEAR` to the next one
  at the feed's full depth (probed 2026-11-14: expired contracts are served to their last session,
  daily back to /GCZ20, which reaches 2014, and hourly within the same ~9.5 months; /GCZ15 and older
  return nothing). A contract the feed has nothing for is listed, never a failure.

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

**Exit.** Non-zero on a failed fetch, missing credentials, an unreadable futures map, or any series a
daily run needs coming back with no bars at all (the three above, the active month and the next all
trade every session), so the job retries.

Read-only against the broker. A script, not package code, because it reaches the network. Run after
GLD's extended hours end at 20:00 ET, so the day's last hourly bars are finished.

    python scripts/fetch_gold_bars.py [--full] [--backfill-contracts] [--interval 1h|1d]
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
#: The months gold futures trade actively (Feb, Apr, Jun, Aug, Oct, Dec), the dated contracts kept.
CYCLE = "GJMQVZ"
#: The first year `--backfill-contracts` asks for; the feed serves nothing for /GCZ15 or older.
BACKFILL_FROM_YEAR = 2016
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


def parse_contract(symbol: str) -> tuple[str, int]:
    """`/GCZ26:XCEC` -> ("Z", 2026). Raises ValueError on anything else."""
    body = symbol.split(":", 1)[0]
    if not (body.startswith("/GC") and len(body) == 6 and body[3].isalpha() and body[4:].isdigit()):
        raise ValueError(f"not a dated GC contract: {symbol!r}")
    return body[3], 2000 + int(body[4:])


def contract_symbol(code: str, year: int) -> str:
    return f"/GC{code}{year % 100:02d}:XCEC"


def next_in_cycle(symbol: str) -> str:
    """The cycle month after a contract's own (which need not be in the cycle itself)."""
    code, year = parse_contract(symbol)
    months = "FGHJKMNQUVXZ"
    for later in months[months.index(code) + 1 :]:
        if later in CYCLE:
            return contract_symbol(later, year)
    return contract_symbol(CYCLE[0], year + 1)


def cycle_contracts(active: str, from_year: int) -> list[str]:
    """Every cycle contract from `from_year`'s first through the one after `active`, oldest first."""
    last = next_in_cycle(active)
    out, sym = [], contract_symbol(CYCLE[0], from_year)
    while True:
        out.append(sym)
        if sym == last:
            return out
        sym = next_in_cycle(sym)


def dated_contracts(fmap: dict, backfill: bool = False) -> list[str]:
    """The dated contracts a run records: the active month and the next, or with `backfill` every cycle
    contract from `BACKFILL_FROM_YEAR` through the next."""
    active = contracts(fmap)["/GC"]
    if backfill:
        return cycle_contracts(active, BACKFILL_FROM_YEAR)
    return [active, next_in_cycle(active)]


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
    session,
    conn: sqlite3.Connection,
    interval: str,
    now: datetime,
    fmap: dict,
    full: bool = False,
    backfill: bool = False,
    sdk=None,
) -> list[dict]:
    """One interval's fetch (one DXLink session), stored as it arrives. A daily run records the three
    series and the active and next contracts; `backfill` records every cycle contract at full depth and
    nothing else. /GC and its own dated contract share one streamer symbol, asked once from the deeper
    start; /GC keeps only the bars from its own start."""
    feed = contracts(fmap)
    held = latest(conn)
    dated = dated_contracts(fmap, backfill)
    # stored name -> (streamer symbol, its own start, whether the run needs bars from it)
    plan: dict[str, tuple[str, datetime, bool]] = {}
    if not backfill:
        for s in SYMBOLS:
            deep = not SERIES[s].startswith("map:")
            plan[s] = (feed[s], start_for(held.get((s, interval)), interval, now, full, deep=deep), True)
    for c in dated:
        plan[c] = (c, start_for(held.get((c, interval)), interval, now, full or backfill), not backfill)
    starts: dict[str, datetime] = {}
    for streamer_sym, start, _need in plan.values():
        starts[streamer_sym] = min(start, starts.get(streamer_sym, start))
    bars, clipped = await fetch(session, starts, interval, sdk)
    out = []
    for name, (streamer_sym, start, need) in plan.items():
        start_ms = start.timestamp() * 1000.0
        got = {ts: b for ts, b in bars.get(streamer_sym, {}).items() if ts >= start_ms}
        offered = store_bars(conn, name, interval, streamer_sym, got, now)
        out.append(
            {
                "symbol": name,
                "interval": interval,
                "contract": streamer_sym,
                "received": len(got),
                "stored": len(offered),
                "first": _et_stamp(offered[0] if offered else None),
                "last": _et_stamp(offered[-1] if offered else None),
                "deep": full or backfill or (name, interval) not in held,
                "clipped": streamer_sym in clipped,
                "required": need,
            }
        )
    conn.commit()
    return out


def outcome(summary: list[dict]) -> dict:
    """`ok` false when a series the run needs came back with no bars (the three series and the active
    and next contracts all trade every session). Every empty series is listed either way; a backfilled
    contract the feed has nothing for is one of them, not a failure."""
    empty = [f"{r['symbol']} {r['interval']}" for r in summary if r["received"] == 0]
    failed = [
        f"{r['symbol']} {r['interval']}" for r in summary if r["received"] == 0 and r.get("required", True)
    ]
    return {"ok": not failed, "empty": empty}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="refetch the feed's full depth for every series")
    ap.add_argument("--interval", choices=sorted(WIDTH_MS), help="one interval only (default: both)")
    ap.add_argument(
        "--backfill-contracts",
        action="store_true",
        help=f"record every {CYCLE} GC contract from {BACKFILL_FROM_YEAR} at full depth, and nothing else",
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
                        await run_interval(
                            session,
                            conn,
                            interval,
                            datetime.now(UTC),
                            fmap,
                            args.full,
                            backfill=args.backfill_contracts,
                        )
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
