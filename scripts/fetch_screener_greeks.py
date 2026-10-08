"""Record the broker's option chain around every row of the day's income screener lists.

**Why.** The market-report plan works out how the vendor screens its credit-spread, covered-call
and short-put lists (`vendor-screeners/<date>/`, saved by `fetch_vendor_edition.py`), and the end
state is that rule re-created from broker or Dolt data alone. Dolt's chain carries only a few
expiries per name (no weeklies, where most short-put rows sit), so for each row this records, from
the broker: every listed strike for the row's expiry, and the delta, implied volatility, bid/ask
and open interest of the strikes around the one the vendor chose. That is the input the rule has to
reproduce the row from. For the ~35 names a night the chart pages also cover, the vendor's own
chain greeks (the chart capture's `how`) are the answer key to judge broker deltas against.

A script, not package code, because it reaches the broker; read-only (no orders, no account
writes). One DXLink session, options in batches; chain listings a short pause apart.

**One file per list date, never overwritten**: `screener-greeks/<date>.json`. It runs after the
evening capture; a day without saved lists exits quietly, and an existing file is left as it was.

    python scripts/fetch_screener_greeks.py [--date YYYY-MM-DD] [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

LISTS = (
    ("short-puts", "shortPuts"),
    ("covered-calls", "coveredCalls"),
    ("credit-spreads", "creditSpreads"),
)
WINDOW = 4  # strikes recorded either side of each anchor (the vendor's strike)
# The broker publishes no hard numbers, but it limits both paths: REST answers 429, and DXLink kills
# the socket on too fast a subscription rate (the suite streamer, 2026-08-24). So chain listings are
# spaced, the first 429 ends the run with what it has, and option subscriptions go in chunks no
# larger than the suite streamer's, spaced the same way (core.streamer SUBSCRIBE_CHUNK / _PACE_S).
CHAIN_PAUSE_S = 0.3
BATCH = 100
SUBSCRIBE_PACE_S = 0.15
BATCH_TIMEOUT_S = 12.0


def store_dir() -> Path:
    from cherrypick.core import home

    return home.data_dir("market-report")


def _iso(mdy: str) -> str:
    return datetime.strptime(mdy, "%m/%d/%Y").date().isoformat()


def anchors(lists: dict[str, dict]) -> dict[tuple[str, str, str], set[float]]:
    """{(symbol, expiry ISO, 'P'|'C'): strikes the vendor chose} over every row of every list."""
    out: dict[tuple[str, str, str], set[float]] = {}
    for name, key in LISTS:
        for row in (lists.get(name) or {}).get(key) or []:
            sym, exp = row.get("symbol"), row.get("expiry")
            if not (isinstance(sym, str) and exp):
                continue
            if name == "credit-spreads":
                right = "P" if row.get("type") == "Put" else "C"
                ks = [(row.get("strike") or {}).get(side) for side in ("sell", "buy")]
            else:
                right = "P" if name == "short-puts" else "C"
                ks = [row.get("strikePrice")]
            for k in ks:
                if isinstance(k, (int, float)):
                    out.setdefault((sym, _iso(exp), right), set()).add(float(k))
    return out


def strike_window(listed: list[float], chosen: set[float], width: int = WINDOW) -> list[float]:
    """The listed strikes within `width` places of any chosen strike. A chosen strike the broker
    does not list keeps its neighbours by position, so a mismatch is still recorded around it."""
    listed = sorted(listed)
    keep: set[float] = set()
    for k in chosen:
        i = min(range(len(listed)), key=lambda j: abs(listed[j] - k)) if listed else None
        if i is not None:
            keep.update(listed[max(0, i - width) : i + width + 1])
    return sorted(keep)


def load_lists(day: str) -> dict[str, dict]:
    root = store_dir() / "vendor-screeners" / day
    out = {}
    for name, _ in LISTS:
        path = root / f"{name}.json"
        if path.exists():
            out[name] = json.loads(path.read_text(encoding="utf-8"))["body"]
    return out


def _broker(sym: str) -> str:
    return sym.replace(".", "/")


async def collect(session, wanted: dict, limit: int | None) -> dict:
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Greeks, Quote, Summary
    from tastytrade.instruments import NestedOptionChain
    from tastytrade.market_data import get_market_data_by_type

    symbols = sorted({s for s, _, _ in wanted})[:limit]
    result: dict[str, dict] = {s: {"spot": None, "expiries": {}} for s in symbols}

    for i in range(0, len(symbols), 100):
        for q in await get_market_data_by_type(session, equities=[_broker(s) for s in symbols[i : i + 100]]):
            sym = q.symbol.replace("/", ".") if q.symbol.replace("/", ".") in result else q.symbol
            if sym in result:
                close = q.close or q.prev_close
                result[sym]["spot"] = float(close) if close else None

    streamer_of: dict[str, tuple[str, str, float, str]] = {}  # streamer symbol -> (sym, exp, strike, right)
    for n, sym in enumerate(symbols):
        if n:
            await asyncio.sleep(CHAIN_PAUSE_S)
        try:
            chains = await NestedOptionChain.get(session, _broker(sym))
        except Exception as exc:  # noqa: BLE001 -- recorded with its reason, never dropped
            if "429" in str(exc):
                for rest in symbols[n:]:
                    result[rest]["error"] = "not fetched: the broker answered 429"
                print(f"broker rate limit (429) at {sym}; stopping chain listings", file=sys.stderr)
                break
            result[sym]["error"] = f"chain: {exc}"[:160]
            continue
        chains = chains if isinstance(chains, list) else [chains]
        chain = next((c for c in chains if c.root_symbol == _broker(sym)), chains[0] if chains else None)
        by_exp = {e.expiration_date.isoformat(): e for e in (chain.expirations if chain else [])}
        for (s, exp, right), chosen in wanted.items():
            if s != sym:
                continue
            e = by_exp.get(exp)
            if e is None:
                result[sym]["expiries"].setdefault(exp, {})["error"] = "expiry not listed by the broker"
                continue
            listed = {float(k.strike_price): k for k in e.strikes}
            rec = result[sym]["expiries"].setdefault(
                exp, {"listed_strikes": sorted(listed), "dte": e.days_to_expiration, "strikes": {}}
            )
            for k in strike_window(list(listed), chosen):
                ss = listed[k].put_streamer_symbol if right == "P" else listed[k].call_streamer_symbol
                streamer_of[ss] = (sym, exp, k, right)
                rec["strikes"].setdefault(f"{k:g}", {})[right] = {"streamer_symbol": ss}

    events: dict[str, dict] = {}
    names = sorted(streamer_of)
    kinds = ((Greeks, "Greeks"), (Quote, "Quote"), (Summary, "Summary"))
    async with DXLinkStreamer(session) as streamer:
        for i in range(0, len(names), BATCH):
            chunk = names[i : i + BATCH]
            for cls in (Greeks, Quote, Summary):
                await streamer.subscribe(cls, chunk)
                await asyncio.sleep(SUBSCRIBE_PACE_S)
            deadline = time.monotonic() + BATCH_TIMEOUT_S
            await asyncio.gather(
                *(_drain(streamer, cls, label, len(chunk), deadline, events) for cls, label in kinds)
            )
            for cls in (Greeks, Quote, Summary):
                await streamer.unsubscribe(cls, chunk)
                await asyncio.sleep(SUBSCRIBE_PACE_S)

    for ss, (sym, exp, k, right) in streamer_of.items():
        result[sym]["expiries"][exp]["strikes"][f"{k:g}"][right].update(events.get(ss, {}))
    return result


async def _drain(streamer, cls, label: str, wanted: int, deadline: float, events: dict[str, dict]) -> None:
    """Collect one event type for the current batch until every option reported or the deadline."""
    seen: set[str] = set()
    while len(seen) < wanted and time.monotonic() < deadline:
        try:
            ev = await asyncio.wait_for(streamer.get_event(cls), timeout=deadline - time.monotonic())
        except TimeoutError:
            return
        rec = events.setdefault(str(ev.event_symbol), {})
        if label == "Greeks":
            rec.update(delta=_f(ev.delta), iv=_f(ev.volatility), theo=_f(ev.price))
        elif label == "Quote":
            rec.update(bid=_f(ev.bid_price), ask=_f(ev.ask_price))
        else:
            rec.update(open_interest=_f(ev.open_interest))
        seen.add(str(ev.event_symbol))


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def latest_list_day() -> str | None:
    days = sorted(p.name for p in (store_dir() / "vendor-screeners").glob("????-??-??") if p.is_dir())
    return days[-1] if days else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--date", help="the list date to record (default: the latest saved)")
    ap.add_argument("--limit", type=int, help="record at most this many symbols (a trial run)")
    args = ap.parse_args(argv)

    day = args.date or latest_list_day()
    lists = load_lists(day) if day else {}
    if not lists:
        print(f"no saved screener lists for {day or 'any day'}; nothing to record")
        return 0
    out = store_dir() / "screener-greeks" / f"{day}.json"
    if out.exists() and not args.limit:
        print(f"{out.name} already exists; not overwritten")
        return 0

    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

    store = CredentialStore(SHARED_SERVICE)
    session = SessionManager(store).get_session()
    wanted = anchors(lists)
    started = time.monotonic()
    result = asyncio.run(collect(session, wanted, args.limit))
    doc = {
        "list_date": day,
        "fetched_at": datetime.now(UTC).isoformat(),
        "window": WINDOW,
        "symbols": result,
    }
    if args.limit:
        out = out.with_name(f"{day}.trial.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    tmp.replace(out)
    strikes = [
        leg
        for s in result.values()
        for e in s["expiries"].values()
        for legs in (e.get("strikes") or {}).values()
        for leg in legs.values()
    ]
    with_delta = sum(1 for leg in strikes if leg.get("delta") is not None)
    errors = sum(1 for s in result.values() if s.get("error"))
    print(
        json.dumps(
            {
                "ok": True,
                "list_date": day,
                "symbols": len(result),
                "chain_errors": errors,
                "options": len(strikes),
                "with_delta": with_delta,
                "seconds": round(time.monotonic() - started),
                "file": str(out),
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
