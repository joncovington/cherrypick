"""Record every optionable name's 50-session median dollar volume from the broker's daily candles.

**Why.** The security list's liquidity rank and the liquidity verdict both need each stock's dollar
volume (median close x volume over 50 sessions). Until 2026-10-19 they read it from the local Dolt
clone, so neither -- nor the income lists built on them -- could run without Dolt. tastytrade's
daily candles carry the same figure when asked for extended hours: their volume is then the
consolidated one, within about 0.05% of Dolt's a session on AAPL, SPY and KO. The SDK's default is
regular hours only, a median 0.40 of Dolt's volume, so `extended_trading_hours=True` is passed and
must stay. `packages/technicals` reads the file this writes
(`dollarvolume.py`); every reader reads this one source, Dolt or no Dolt.

A script, not package code, because it reaches the broker; read-only against it, with the shared
credential.

**Which names.** Every symbol in Nasdaq Trader's directory with option volume at OCC in the last
20 sessions (`securities.optionable`, ~6,000), plus every name the technicals store or a saved
screener list holds (the liquidity verdict judges those). Names held illiquid are fetched too: a
candle request costs one subscription in a batch, not a call per name, and the security list ranks
every optionable name.

**Sessions.** Each candle is dated as the producer dates a daily candle (its UTC day). The run's own
session is dropped even after the close, so an evening file reaches the previous session -- what
the 05:30 Dolt pull held at the same hour. ~100 calendar days are requested, about 70 sessions.

**Batches.** `BATCH` names per DXLink connection, the most the feed accepts in one candle
subscription; a batch is done when every name in it has sent the end of its history (`SNAP_END`) or
the feed has gone quiet. 100 names took under a second; all ~5,600 about 140 s (2026-10-19). The
file is written only when at least `MIN_COVERAGE` of the names wanted have a median, so a broken
night keeps yesterday's file rather than a thin one. Files older than `KEEP_FILES` sessions are
removed.

    python scripts/fetch_dollar_volume.py [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from cherrypick.core import jsonio as _jsonio
from cherrypick.core import streamcache
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager
from cherrypick.technicals import dollarvolume, paths, securities, symbols

NY = ZoneInfo("America/New_York")
# The feed refuses a candle subscription much past this ("subscription size for event type 'Candle'
# is too big" at 200 names, 2026-10-19); 100 worked.
BATCH = 100
LOOKBACK_DAYS = 100
QUIET_S = 5.0
MAX_BATCH_S = 120.0
MIN_COVERAGE = 0.9
KEEP_FILES = 30
SNAP_END = 0x08
REMOVE = 0x02


def wanted_names(today: str) -> list[str]:
    """The names to fetch, in the store's spelling (BRK.B). The cash indexes have no volume."""
    from cherrypick.technicals import liquidity

    names = {*securities.optionable(today), *symbols.all_symbols(), *liquidity.screener_names()}
    return sorted(names - set(symbols.INDEXES))


def feed_symbol(name: str) -> str:
    """The broker's spelling: BRK.B is BRK/B, ABR$D is ABR/PR/D."""
    return securities.vendor_spelling(name)


def _sdk() -> tuple[type, type]:
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Candle

    return DXLinkStreamer, Candle


async def fetch_batch(
    session, names: list[str], start: datetime, sdk: tuple[type, type] | None = None
) -> tuple[dict[str, list], set[str]]:
    """({name: [(session date, close, volume)]}, the names whose history ended) for one batch.
    `sdk` is (streamer class, candle event class), the SDK's unless a test passes its own."""
    streamer_cls, Candle = sdk or _sdk()
    back = {feed_symbol(n): n for n in names}
    bars: dict[str, list] = {n: [] for n in names}
    ended: set[str] = set()
    async with streamer_cls(session) as streamer:
        # Extended hours: the consolidated volume. The default (regular hours) is a fraction of it.
        await streamer.subscribe_candle(
            list(back), interval="1d", start_time=start, extended_trading_hours=True
        )
        deadline = time.monotonic() + MAX_BATCH_S
        while time.monotonic() < deadline and len(ended) < len(names):
            # get_event per wait: a wait_for timeout around a listen() generator cancels the generator.
            try:
                ev = await asyncio.wait_for(streamer.get_event(Candle), timeout=QUIET_S)
            except TimeoutError:
                break
            name = back.get(str(ev.event_symbol or "").split("{", 1)[0])
            if name is None:
                continue
            flags = int(ev.event_flags or 0)
            if flags & SNAP_END:
                ended.add(name)
            stamp = streamcache.to_float(ev.time)
            if flags & REMOVE or stamp is None:
                continue
            day = datetime.fromtimestamp(stamp / 1000.0, tz=UTC).date().isoformat()
            bars[name].append((day, streamcache.to_float(ev.close), streamcache.to_float(ev.volume)))
    return bars, ended


async def fetch(
    session, names: list[str], start: datetime, sdk: tuple[type, type] | None = None
) -> tuple[dict[str, list], set[str], list[str]]:
    bars: dict[str, list] = {}
    ended: set[str] = set()
    failed: list[str] = []
    for k in range(0, len(names), BATCH):
        batch = names[k : k + BATCH]
        try:
            got, done = await fetch_batch(session, batch, start, sdk)
        except Exception as exc:  # noqa: BLE001 -- the batch is reported and the run goes on
            failed.append(f"{batch[0]}..{batch[-1]}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        bars.update(got)
        ended |= done
    return bars, ended, failed


def build_doc(bars: dict[str, list], ended: set[str], today: str) -> dict:
    """The file: each name's median and sessions read, the newest session any median reached."""
    names = {}
    through = None
    for name, rows in bars.items():
        got = dollarvolume.median_dollar_volume(rows, today)
        if got is None:
            continue
        median, sessions = got
        names[name] = {"median": round(median), "sessions": sessions}
        last = max(d for d, c, _ in rows if d < today and c and c > 0)
        through = last if through is None or last > through else through
    return {
        "session": today,
        "through": through,
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": "tastytrade daily candles (consolidated volume)",
        "rule": {"sessions": dollarvolume.SESSIONS, "lookback_days": LOOKBACK_DAYS},
        "requested": len(bars),
        "history_ended": len(ended & set(bars)),
        "names": names,
    }


def prune(keep: int = KEEP_FILES) -> list[str]:
    root = paths.dollar_volume_dir()
    files = sorted(root.glob("????-??-??.json"))
    gone = files[:-keep] if len(files) > keep else []
    for f in gone:
        f.unlink()
    return [f.stem for f in gone]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--limit", type=int, help="fetch at most this many names (a trial; never written)")
    args = ap.parse_args(argv)

    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1
    today = datetime.now(NY).date().isoformat()
    names = wanted_names(today)
    if args.limit is not None:
        names = names[: args.limit]
    if not names:
        print(json.dumps({"ok": False, "reason": "no names: no symbol directory or OCC volume yet"}))
        return 1
    started = time.monotonic()
    start = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    bars, ended, failed = asyncio.run(fetch(SessionManager(store).get_session(), names, start))
    doc = build_doc(bars, ended, today)
    coverage = len(doc["names"]) / len(names)
    report = {
        "wanted": len(names),
        "with_median": len(doc["names"]),
        "coverage": round(coverage, 4),
        "history_ended": doc["history_ended"],
        "through": doc["through"],
        "failed": failed,
        "seconds": round(time.monotonic() - started, 1),
    }
    if args.limit is not None:
        print(json.dumps({"ok": True, "trial": True, **report}, indent=1))
        return 0
    if coverage < MIN_COVERAGE:
        why = f"coverage {coverage:.0%} under {MIN_COVERAGE:.0%}; not written"
        print(json.dumps({"ok": False, "reason": why, **report}, indent=1))
        return 1
    out = paths.dollar_volume_dir() / f"{today}.json"
    path = _jsonio.write_json_atomic(out, doc, indent=None, default=None)
    print(json.dumps({"ok": True, "file": str(path), "pruned": prune(), **report}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
