"""Probe DXLink intraday candles for a live chart, and report what a chart would have to handle.

Before building a moving /ES chart in the console, this asks the feed rather than assuming:

  - which symbol the bars come back labelled with (dxFeed drops a multiplier of 1, so `{=1m}` may
    arrive as `{=m}`, and an exact-match filter would then discard every bar);
  - how much history the subscribe burst delivers, and which snapshot flags mark its end;
  - how often the bar in progress is updated live, and what happens at a bar boundary;
  - whether any bar arrives with a missing price (the SDK turns a missing OHLC into 0).

Extended hours are ON (`extended_trading_hours=True`): the SDK's default appends `tho=true`, which
is regular hours only, and an overnight futures chart would then show nothing after the close.

Read-only against the broker: one DXLink session for the length of the run, no orders, nothing
written. Network-reaching, so it lives in `scripts/` beside the other probes, never in a package. The
contract comes from the futures map `refresh_futures_contracts.py` writes; never assemble one by hand.

    python scripts/probe_candles.py [--product ES | --symbol SPX] [--interval 5m --interval 1m] [--hours 3]
                                    [--watch 120] [--aggregation 10] [--history-max 30]

How much intraday history exists is a question this answers too: request years with `--hours`, raise
`--history-max` so a long burst is not cut off by the probe's own timer, and read `history_first_date`
against the requested start and `history_sessions`. `SNAP_END` in `flags_seen` says the feed finished
the snapshot; without it, the burst was cut short and the depth is a lower bound. `--watch 0` skips the
live phase (on a weekend there is nothing to watch).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

ET = ZoneInfo("America/New_York")
# The history burst is front-loaded; this much silence after the first bar means it is over.
HISTORY_QUIET_S = 3.0
HISTORY_MAX_S = 30.0
FLAG_NAMES = {0x01: "TX_PENDING", 0x02: "REMOVE", 0x04: "SNAP_BEGIN", 0x08: "SNAP_END", 0x10: "SNAP_SNIP"}


def _contract(product: str) -> str:
    fmap = json.loads((_home.state_dir() / "futures_contracts.json").read_text(encoding="utf-8"))
    return fmap["contracts"][product][0]["streamer_symbol"]


def _et(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=ET).strftime("%m-%d %H:%M")


def _et_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=ET).strftime("%Y-%m-%d")


def _flags(value: int) -> list[str]:
    return [name for bit, name in FLAG_NAMES.items() if value & bit]


def _num(value) -> float | None:
    return None if value is None else float(value)


class _Series:
    def __init__(self, requested: str) -> None:
        self.requested = requested
        self.labels: Counter = Counter()
        self.flags: Counter = Counter()
        self.history: dict[int, dict] = {}
        self.live_updates: list[tuple[float, int]] = []  # (monotonic receipt, bar time)
        self.zero_or_missing = 0


async def probe(
    session,
    symbol: str,
    intervals: list[str],
    hours: float,
    watch_s: float,
    aggregation: float,
    history_max_s: float = HISTORY_MAX_S,
) -> dict:
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Candle

    series = {iv: _Series(f"{symbol}{{={iv}}}") for iv in intervals}

    # Route by the period attribute as the feed spells it: `m` and `1m` are the same bar width.
    def _period_key(label: str) -> str | None:
        inner = label.split("{", 1)[1].rstrip("}") if "{" in label else ""
        period = next((p[1:] for p in inner.split(",") if p.startswith("=")), None)
        if period is None:
            return None
        return period if period[0].isdigit() else f"1{period}"

    start = datetime.now(tz=UTC) - timedelta(hours=hours)
    phase = "history"
    async with DXLinkStreamer(session) as streamer:
        for iv in intervals:
            await streamer.subscribe_candle(
                [symbol],
                interval=iv,
                start_time=start,
                extended_trading_hours=True,
                refresh_interval=aggregation,
            )
        # get_event, not listen(): a wait_for timeout around a listen() generator's __anext__ cancels
        # the generator itself, and every later call then raises StopAsyncIteration.
        t0 = time.monotonic()
        got_any = False
        watch_until = None
        while True:
            now = time.monotonic()
            if phase == "history" and now - t0 > history_max_s:
                phase, watch_until = "live", now + watch_s
            if phase == "live" and now >= watch_until:
                break
            timeout = HISTORY_QUIET_S if phase == "history" else max(0.1, watch_until - now)
            try:
                ev = await asyncio.wait_for(streamer.get_event(Candle), timeout=timeout)
            except TimeoutError:
                if phase == "history" and got_any:
                    phase, watch_until = "live", time.monotonic() + watch_s
                    took = time.monotonic() - t0
                    print(f"-- history burst over after {took:.1f}s; watching live for {watch_s:.0f}s")
                continue
            label = str(ev.event_symbol)
            iv = _period_key(label)
            s = series.get(iv) if iv else None
            if s is None:
                continue
            got_any = True
            s.labels[label] += 1
            flags = int(ev.event_flags or 0)
            for name in _flags(flags):
                s.flags[name] += 1
            bar = {
                "o": _num(ev.open),
                "h": _num(ev.high),
                "l": _num(ev.low),
                "c": _num(ev.close),
                "v": _num(ev.volume),
                "n": ev.count,
            }
            if not bar["o"] or not bar["c"]:
                s.zero_or_missing += 1
            if phase == "history":
                if not flags & 0x02:
                    s.history[int(ev.time)] = bar
            else:
                s.live_updates.append((time.monotonic(), int(ev.time)))
                print(
                    f"  {iv:>3} {_et(int(ev.time))} ET  o={bar['o']} h={bar['h']} l={bar['l']} c={bar['c']} "
                    f"v={bar['v']} flags={_flags(flags) or '-'}"
                )
        for iv in intervals:
            try:
                await streamer.unsubscribe_candle(symbol, interval=iv, extended_trading_hours=True)
            except Exception as exc:  # noqa: BLE001 — teardown is best-effort
                print(f"unsubscribe {iv} failed: {exc}")

    out = {}
    for iv, s in series.items():
        times = sorted(s.history)
        gaps = [b - a for (a, _), (b, _) in zip(s.live_updates, s.live_updates[1:], strict=False)]
        out[iv] = {
            "requested": s.requested,
            "labels_seen": dict(s.labels),
            "flags_seen": dict(s.flags),
            "history_bars": len(times),
            "history_first_et": _et(times[0]) if times else None,
            "history_last_et": _et(times[-1]) if times else None,
            "history_first_date": _et_date(times[0]) if times else None,
            "history_last_date": _et_date(times[-1]) if times else None,
            "history_sessions": len({_et_date(t) for t in times}),
            "history_last_bar": s.history[times[-1]] if times else None,
            "live_updates": len(s.live_updates),
            "live_distinct_bars": sorted({_et(t) for _, t in s.live_updates}),
            "live_gap_s": (
                {"median": round(statistics.median(gaps), 2), "max": round(max(gaps), 2)} if gaps else None
            ),
            "bars_with_zero_or_missing_price": s.zero_or_missing,
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", default="ES", help="futures product code in the futures map (default ES)")
    ap.add_argument("--symbol", default=None, help="streamer symbol to probe (e.g. SPX); overrides --product")
    ap.add_argument("--interval", action="append", help="candle width, repeatable (default 5m and 1m)")
    ap.add_argument("--hours", type=float, default=3.0, help="history to request (default 3)")
    ap.add_argument("--watch", type=float, default=120.0, help="seconds to watch live updates (default 120)")
    ap.add_argument(
        "--aggregation",
        type=float,
        default=0.1,
        help="the feed's acceptAggregationPeriod in seconds (default 0.1; the console's feed uses 10)",
    )
    ap.add_argument(
        "--history-max",
        type=float,
        default=HISTORY_MAX_S,
        help=f"seconds to wait for the history burst before calling it over (default {HISTORY_MAX_S:g})",
    )
    args = ap.parse_args(argv)

    store = CredentialStore(SHARED_SERVICE)
    missing = store.missing_secrets()
    if missing:
        print(json.dumps({"ok": False, "reason": "credentials_missing", "missing": list(missing)}))
        return 1
    symbol = args.symbol or _contract(args.product)
    intervals = args.interval or ["5m", "1m"]
    start = datetime.now(tz=UTC) - timedelta(hours=args.hours)
    print(f"probing {symbol} {intervals}, {args.hours:g}h of history (from {start:%Y-%m-%d}), ", end="")
    print(f"extended hours on, aggregation {args.aggregation:g}s")
    session = SessionManager(store).get_session()
    result = asyncio.run(
        probe(session, symbol, intervals, args.hours, args.watch, args.aggregation, args.history_max)
    )
    out = {"ok": True, "symbol": symbol, "requested_from": f"{start:%Y-%m-%d}", "series": result}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
