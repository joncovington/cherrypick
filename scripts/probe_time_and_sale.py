"""Probe DXLink TimeAndSale (options prints) for SPX 0DTE, and report what a print recorder would get.

Before the streamer records options prints for a flow-adjusted GEX, this asks the feed rather than
assuming:

  - whether the account is entitled to TimeAndSale on SPXW options at all;
  - whether a time-series subscription (`fromTime`) serves the session's earlier prints, so a
    recorder that starts late, or reconnects, can fill the gap;
  - how many prints carry an aggressor side, and how many must fall back to the quote at the print
    (`bid_price`/`ask_price`);
  - how many are spread legs, invalid ticks, corrections or cancellations, or extended hours -- each
    a print a signed-volume count must handle rather than add.

An entitlement probe answers "is this entitled", not "can this sustain a series": watch a session
before relying on it.

Read-only against the broker: one DXLink session for the length of the run, no orders, nothing
written. Network-reaching, so it lives in `scripts/` beside the other probes, never in a package. The
symbols come from the shared stream cache (read-only): the strikes nearest the cached SPX spot on
the chosen expiration.

    python scripts/probe_time_and_sale.py [--expiration 2026-11-02] [--strikes 6] [--hours 8] [--watch 60]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

ET = ZoneInfo("America/New_York")
HISTORY_QUIET_S = 4.0


def _symbols(expiration: str | None, strikes: int) -> tuple[str, list[str]]:
    db = _home.home() / "data" / "marketdata" / "stream_cache.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        spot = con.execute("SELECT last FROM stream_trades WHERE symbol = 'SPX'").fetchone()[0]
        if expiration is None:
            expiration = con.execute(
                "SELECT MIN(expiration) FROM stream_chain "
                "WHERE underlying_symbol = 'SPX' AND expiration >= ?",
                (datetime.now(tz=ET).strftime("%Y-%m-%d"),),
            ).fetchone()[0]
        rows = con.execute(
            "SELECT streamer_symbol, data_json FROM stream_chain "
            "WHERE underlying_symbol = 'SPX' AND expiration = ?",
            (expiration,),
        ).fetchall()
    finally:
        con.close()
    by_strike: dict[float, list[str]] = {}
    for sym, data in rows:
        by_strike.setdefault(float(json.loads(data)["strike_price"]), []).append(sym)
    nearest = sorted(by_strike, key=lambda k: abs(k - spot))[:strikes]
    return expiration, [s for k in sorted(nearest) for s in by_strike[k]]


async def probe(session, symbols: list[str], hours: float, watch_s: float) -> dict:
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import TimeAndSale

    counts: Counter = Counter()
    aggressor: Counter = Counter()
    kinds: Counter = Counter()
    per_symbol: Counter = Counter()
    first_ms, last_ms = None, None
    contracts = 0
    examples: list[dict] = []
    t0 = time.monotonic()
    async with DXLinkStreamer(session) as streamer:
        # Opens the channel the SDK's way; the time-series add below is the protocol's, which the
        # SDK only exposes for candles.
        await streamer.subscribe(TimeAndSale, [])
        since = round((datetime.now(tz=UTC) - timedelta(hours=hours)).timestamp() * 1000)
        await streamer._websocket.send_json(  # noqa: SLF001 -- no public time-series add for this type
            {
                "type": "FEED_SUBSCRIPTION",
                "channel": streamer._channels["TimeAndSale"],  # noqa: SLF001
                "add": [{"symbol": s, "type": "TimeAndSale", "fromTime": since} for s in symbols],
            }
        )
        deadline = t0 + max(watch_s, 0) + 60
        got_any = False
        while time.monotonic() < deadline:
            try:
                ev = await asyncio.wait_for(streamer.get_event(TimeAndSale), timeout=HISTORY_QUIET_S)
            except TimeoutError:
                if got_any and time.monotonic() - t0 > watch_s:
                    break
                if not got_any and time.monotonic() - t0 > 20:
                    break
                continue
            got_any = True
            counts["events"] += 1
            per_symbol[str(ev.event_symbol)] += 1
            aggressor[str(ev.aggressor_side)] += 1
            kinds[f"type={ev.type}"] += 1
            if ev.spread_leg:
                kinds["spread_leg"] += 1
            if not ev.valid_tick:
                kinds["invalid_tick"] += 1
            if ev.extended_trading_hours:
                kinds["extended_hours"] += 1
            if ev.bid_price is None or ev.ask_price is None:
                kinds["no_quote_at_print"] += 1
            contracts += int(ev.size or 0)
            ms = int(ev.time or 0)
            if ms:
                first_ms = ms if first_ms is None else min(first_ms, ms)
                last_ms = ms if last_ms is None else max(last_ms, ms)
            if len(examples) < 5:
                examples.append(
                    {
                        "symbol": str(ev.event_symbol),
                        "time_et": (
                            datetime.fromtimestamp(ms / 1000, tz=ET).strftime("%m-%d %H:%M:%S")
                            if ms
                            else None
                        ),
                        "price": float(ev.price) if ev.price is not None else None,
                        "size": ev.size,
                        "bid": float(ev.bid_price) if ev.bid_price is not None else None,
                        "ask": float(ev.ask_price) if ev.ask_price is not None else None,
                        "aggressor": str(ev.aggressor_side),
                        "spread_leg": ev.spread_leg,
                        "conditions": ev.exchange_sale_conditions,
                    }
                )

    def _et(ms):
        return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=ET).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "events": counts["events"],
        "contracts": contracts,
        "symbols_with_prints": len(per_symbol),
        "first_print_et": _et(first_ms),
        "last_print_et": _et(last_ms),
        "aggressor_side": dict(aggressor),
        "flags": dict(kinds),
        "examples": examples,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--expiration", default=None, help="SPXW expiration (default: the nearest on or after today)"
    )
    ap.add_argument("--strikes", type=int, default=6, help="strikes nearest spot, both rights (default 6)")
    ap.add_argument("--hours", type=float, default=8.0, help="history to request with fromTime (default 8)")
    ap.add_argument("--watch", type=float, default=60.0, help="seconds to keep listening (default 60)")
    args = ap.parse_args(argv)

    expiration, symbols = _symbols(args.expiration, args.strikes)
    print(f"probing {len(symbols)} SPX {expiration} symbols, history {args.hours}h, watch {args.watch}s")
    session = SessionManager(CredentialStore(SHARED_SERVICE)).get_session()
    result = asyncio.run(probe(session, symbols, args.hours, args.watch))
    print(json.dumps({"expiration": expiration, "symbols": len(symbols), **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
