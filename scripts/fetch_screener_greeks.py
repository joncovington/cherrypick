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

**`measure`: the spread in regular hours.** The evening quotes are after the close, so they say
nothing about a name's real spread. `measure` runs inside the universe builder's window (10:00 ET to
half an hour before the close) and records, for every name on the latest lists that the universe
does not already measure, the at-the-money call and put of the standard monthly nearest 30 days --
the builder's own `_measure`, so the two sets of readings are taken the same way and read together.
Written to `screener-greeks/measurements/<date>/<HHMM>.json`, the builder's file shape.

    python scripts/fetch_screener_greeks.py [--date YYYY-MM-DD] [--limit N]
    python scripts/fetch_screener_greeks.py measure [--force] [--limit N]
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
# the socket on too fast a subscription rate. The first full run of this script (2026-10-07,
# unpaced: three event types subscribed and unsubscribed per 100-option batch, batches back to
# back) was killed with "Your subscription rate is too high" after ~10k subscriptions. So chain
# listings are spaced and the first 429 ends them; every subscribe or unsubscribe call waits
# SUBSCRIBE_PACE_S, and a batch never takes less than MIN_BATCH_S, which caps the rate at a few
# hundred subscriptions a second at the very worst. A socket killed anyway is reopened once, after
# a pause, at half the rate, from the batch it died in; whatever is still missing is saved as such.
CHAIN_PAUSE_S = 0.3
BATCH = 100
SUBSCRIBE_PACE_S = 1.0
MIN_BATCH_S = 3.0
BATCH_TIMEOUT_S = 12.0
RECONNECT_PAUSE_S = 60.0


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
    batches = [names[i : i + BATCH] for i in range(0, len(names), BATCH)]
    done = 0
    problem = None
    for attempt, pace in enumerate((SUBSCRIBE_PACE_S, 2 * SUBSCRIBE_PACE_S)):
        if attempt:
            print(f"DXLink closed ({problem}); reopening once in {RECONNECT_PAUSE_S:.0f}s", file=sys.stderr)
            await asyncio.sleep(RECONNECT_PAUSE_S)
        try:
            async with DXLinkStreamer(session) as streamer:
                while done < len(batches):
                    chunk, started = batches[done], time.monotonic()
                    for cls, _ in kinds:
                        await streamer.subscribe(cls, chunk)
                        await asyncio.sleep(pace)
                    deadline = time.monotonic() + BATCH_TIMEOUT_S
                    await asyncio.gather(
                        *(_drain(streamer, cls, label, len(chunk), deadline, events) for cls, label in kinds)
                    )
                    for cls, _ in kinds:
                        await streamer.unsubscribe(cls, chunk)
                        await asyncio.sleep(pace)
                    await asyncio.sleep(max(0.0, MIN_BATCH_S - (time.monotonic() - started)))
                    done += 1
            problem = None
            break
        except Exception as exc:  # noqa: BLE001 -- a killed socket keeps what it delivered
            problem = f"{type(exc).__name__}: {_innermost(exc)}"[:200]

    for ss, (sym, exp, k, right) in streamer_of.items():
        result[sym]["expiries"][exp]["strikes"][f"{k:g}"][right].update(events.get(ss, {}))
    if problem:
        problem = f"options feed stopped after {done}/{len(batches)} batches: {problem}"
    return result, problem


def _innermost(exc: BaseException) -> str:
    """The message a TaskGroup wraps: 'Your subscription rate is too high', not 'unhandled errors'."""
    while getattr(exc, "exceptions", None):  # an exception group, by shape
        exc = exc.exceptions[0]
    return str(exc)


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


def _universe_builder():
    """The universe builder, loaded from beside this script: its measurement is reused as it is."""
    import importlib.util

    path = Path(__file__).resolve().with_name("build_stock_universe.py")
    spec = importlib.util.spec_from_file_location("build_stock_universe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def measure_names(lists: dict[str, dict], universe_candidates: set[str]) -> list[str]:
    """Every name on the lists, in the builder's spelling (BRK.B), less the ones it measures anyway."""
    names = {
        row["symbol"].replace("/", ".")
        for name, key in LISTS
        for row in (lists.get(name) or {}).get(key) or []
        if isinstance(row.get("symbol"), str)
    }
    return sorted(names - universe_candidates)


def cmd_measure(args) -> int:
    from zoneinfo import ZoneInfo

    from cherrypick.core import calendar as cal
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

    ub = _universe_builder()
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)
    today = now.date()
    if not args.force and not ub.in_window(now, cal.is_trading_day(today), cal.session_close_hhmm(today)):
        print(
            json.dumps(
                {
                    "ok": True,
                    "skipped": f"outside the measuring window ({ub.WINDOW_START} ET to 30 min before close)",
                }
            )
        )
        return 0
    day = latest_list_day()
    lists = load_lists(day) if day else {}
    if not lists:
        print(json.dumps({"ok": True, "skipped": "no saved screener lists"}))
        return 0
    cands = set(ub._read_json(ub.store_dir() / "candidates.json", {}).get("names") or {})
    names = measure_names(lists, cands)[: args.limit]
    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1
    root = store_dir() / "screener-greeks"
    chain_path = root / "chains" / f"{today.isoformat()}.json"
    chain_cache = ub._read_json(chain_path, {})
    started = datetime.now(UTC)
    try:
        session = SessionManager(store).get_session()
        measured = asyncio.run(ub._measure(session, names, chain_cache, None))
    except Exception as exc:  # noqa: BLE001 -- keep the chains gathered so far for the next run
        ub._write_json(chain_path, chain_cache)
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:300]}))
        return 1
    ub._write_json(chain_path, chain_cache)
    out = root / "measurements" / today.isoformat() / f"{started.astimezone(et):%H%M}.json"
    ub._write_json(out, {"measured_at": started.isoformat(), "list_date": day, "names": measured})
    usable = sum(1 for row in measured.values() if ub.reading(row, started)["option"])
    print(
        json.dumps(
            {"ok": True, "path": str(out), "names": len(measured), "with_option_reading": usable}, indent=1
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("mode", nargs="?", choices=("greeks", "measure"), default="greeks")
    ap.add_argument("--date", help="greeks: the list date to record (default: the latest saved)")
    ap.add_argument("--limit", type=int, help="record at most this many symbols (a trial run)")
    ap.add_argument("--force", action="store_true", help="measure: run outside the measuring window")
    args = ap.parse_args(argv)
    if args.mode == "measure":
        return cmd_measure(args)

    day = args.date or latest_list_day()
    lists = load_lists(day) if day else {}
    if not lists:
        print(f"no saved screener lists for {day or 'any day'}; nothing to record")
        return 0
    out = store_dir() / "screener-greeks" / f"{day}.json"
    if out.exists() and not args.limit:
        try:
            complete = json.loads(out.read_text(encoding="utf-8")).get("complete", True)
        except (OSError, ValueError):
            complete = False
        if complete:
            print(f"{out.name} already exists; not overwritten")
            return 0
        print(f"{out.name} is incomplete; recording again")

    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

    store = CredentialStore(SHARED_SERVICE)
    session = SessionManager(store).get_session()
    wanted = anchors(lists)
    started = time.monotonic()
    result, problem = asyncio.run(collect(session, wanted, args.limit))
    doc = {
        "list_date": day,
        "fetched_at": datetime.now(UTC).isoformat(),
        "window": WINDOW,
        # An incomplete file is kept (the chain listings alone are most of the run) and recorded
        # again by the next run, which overwrites only a file marked incomplete.
        "complete": problem is None,
        "problem": problem,
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
                "ok": problem is None,
                "problem": problem,
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
    return 0 if problem is None else 1


if __name__ == "__main__":
    sys.exit(main())
