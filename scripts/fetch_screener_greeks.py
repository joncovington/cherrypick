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

**One file per list date, never overwritten**: `screener-greeks/<date>.json`. It records TODAY's
lists (ET), never merely the newest on disk: the broker's chain is only that day's answer key on
the day, and on 2026-10-15 the capture landed at 22:46 ET, after the 19:00 run, which found
10-14 already recorded and exited 0 -- the session was lost without a word. So it runs twice: the
evening slot exits quietly when today's lists are not saved yet, and the late slot (`--final`)
records them if they have landed since, or exits 1, so a lost day is a failed job, not a quiet
success. An existing complete file is left as it was.

**Credit-spread candidates** go beside it, in `screener-greeks/candidates/<date>.json`: every name
with liquidity rank 1 on the day's security list, on its ~45-day expiry, the strikes a spread's legs
would come from (see CAND_DTE), so the spread list's selection can be tested on names that missed it.

**`measure`: the spread in regular hours.** The evening quotes are after the close, so they say
nothing about a name's real spread. `measure` runs inside the universe builder's window (10:00 ET to
half an hour before the close) and records, for every name on the latest lists that the universe
does not already measure, the at-the-money call and put of the standard monthly nearest 30 days --
the builder's own `_measure`, so the two sets of readings are taken the same way and read together.
Written to `screener-greeks/measurements/<date>/<HHMM>.json`, the builder's file shape.

**The earnings calendar's suggested spreads** ride the same run (2026-10-17; docs/earnings-screener-
plan.md, step 3): for each strong read reporting in the next week on the latest calendar, the
target strikes are snapped to the listed ones on the spread's expiry, both legs quoted and judged
against the same leg bars, and the spread priced at mid and at the natural fill. Written to
`screener-greeks/earnings-legs/<date>/<HHMM>.json`; a failure there keeps the list readings.

    python scripts/fetch_screener_greeks.py [--date YYYY-MM-DD] [--limit N] [--final]
    python scripts/fetch_screener_greeks.py measure [--force] [--limit N]
    python scripts/fetch_screener_greeks.py rebar     # re-apply the leg bars to saved readings
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

# Credit-spread CANDIDATES (2026-10-16): which names make the spread list is unsolved, and two of its
# likely filters -- the credit at least a third of the width at the list's own structure (short the
# first strike out of the money, long nearest 0.25 delta, ~45 days) and the direction read -- can only
# be tested on names that did NOT make it too. So every name the day's security list gives liquidity
# rank 1 and options is recorded on the expiry nearest 45 days: the first strike out of the money and
# the strikes beyond it out to CAND_REACH of the price, thinned to CAND_STRIKES (the first few kept
# consecutive, where the short leg and its neighbours sit). One side per name, by its direction read;
# both sides when the read is neutral or missing. Written beside the day's file, never into it, so the
# list rows' windows -- and every score computed from them -- are unchanged.
CAND_DTE = 45
CAND_REACH = 0.20
CAND_STRIKES = 16
CAND_NEAR = 4
BULL_READS = {"sb", "bn", "bp", "bi"}  # strong bull, bull, pullback, improving: a put spread
BEAR_READS = {"sbe", "brn", "br", "bd"}  # strong bear, bear, rally, weakening: a call spread
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


def candidate_plan(day: str) -> dict[str, dict]:
    """{symbol: {edge, rights}} from the latest security list on or before `day`: every name with
    options and liquidity rank 1, the side(s) its direction read implies. Empty with no list."""
    root = store_dir() / "vendor-securities"
    files = sorted(f for f in root.glob("????-??-??.json") if f.stem <= day) if root.is_dir() else []
    if not files:
        return {}
    plan: dict[str, dict] = {}
    for r in json.loads(files[-1].read_text(encoding="utf-8")).get("rows") or []:
        sym = r.get("symbol")
        if not isinstance(sym, str) or not r.get("hasOptions") or r.get("liquidityRank") != 1:
            continue
        edge = r.get("directionalEdge")
        rights = ["P"] if edge in BULL_READS else ["C"] if edge in BEAR_READS else ["P", "C"]
        plan[sym] = {"edge": edge, "rights": rights, "securities": files[-1].stem}
    return plan


def nearest_dte(expirations: list[tuple[str, int]], target: int) -> tuple[str, int] | None:
    """The (expiry, days) nearest `target` days out; a tie takes the earlier. Expired ones never."""
    live = [e for e in expirations if e[1] > 0]
    return min(live, key=lambda e: (abs(e[1] - target), e[1])) if live else None


def candidate_strikes(
    listed: list[float],
    spot: float,
    right: str,
    reach: float = CAND_REACH,
    most: int = CAND_STRIKES,
    near: int = CAND_NEAR,
) -> list[float]:
    """The first strike out of the money and those beyond it out to `reach` of the price: all of the
    nearest `near`, then evenly thinned so at most `most` are kept, the farthest always among them."""
    if right == "P":
        otm = sorted((k for k in listed if spot * (1 - reach) <= k <= spot), reverse=True)
    else:
        otm = sorted(k for k in listed if spot <= k <= spot * (1 + reach))
    if len(otm) <= most:
        return sorted(otm)
    head, tail = otm[:near], otm[near:]
    room = most - near
    step = (len(tail) - 1) / (room - 1)
    picked = {tail[round(i * step)] for i in range(room)}
    return sorted(set(head) | picked)


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


async def collect(session, wanted: dict, limit: int | None, plans: dict | None = None) -> tuple:
    """(result, candidates, problem). `result` holds the list rows' windows, exactly as before;
    `candidates` the credit-spread candidates `plans` names, from the same paced session."""
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Greeks, Quote, Summary
    from tastytrade.instruments import NestedOptionChain
    from tastytrade.market_data import get_market_data_by_type

    plans = plans or {}
    symbols = sorted({s for s, _, _ in wanted} | set(plans))[:limit]
    result: dict[str, dict] = {s: {"spot": None, "expiries": {}} for s in symbols}
    cand: dict[str, dict] = {}
    cand_of: dict[str, tuple[str, float, str]] = {}  # streamer symbol -> (sym, strike, right)

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
                    result[rest]["error"] = RATE_LIMITED
                print(f"broker rate limit (429) at {sym}; stopping chain listings", file=sys.stderr)
                break
            result[sym]["error"] = f"chain: {exc}"[:160]
            continue
        chains = chains if isinstance(chains, list) else [chains]
        chain = next((c for c in chains if c.root_symbol == _broker(sym)), chains[0] if chains else None)
        by_exp = {e.expiration_date.isoformat(): e for e in (chain.expirations if chain else [])}
        if sym in plans:
            pick = nearest_dte([(x, e.days_to_expiration) for x, e in by_exp.items()], CAND_DTE)
            spot = result[sym]["spot"]
            c = cand.setdefault(sym, {**plans[sym], "spot": spot, "expiry": None, "dte": None, "strikes": {}})
            if pick is None or not spot:
                c["error"] = "no expiry listed" if pick is None else "no spot"
            else:
                e = by_exp[pick[0]]
                listed = {float(k.strike_price): k for k in e.strikes}
                c.update(expiry=pick[0], dte=pick[1], listed_strikes=sorted(listed))
                for right in plans[sym]["rights"]:
                    for k in candidate_strikes(list(listed), spot, right):
                        ss = listed[k].put_streamer_symbol if right == "P" else listed[k].call_streamer_symbol
                        cand_of[ss] = (sym, k, right)
                        c["strikes"].setdefault(f"{k:g}", {})[right] = {"streamer_symbol": ss}
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
    names = sorted(set(streamer_of) | set(cand_of))
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
    for ss, (sym, k, right) in cand_of.items():
        cand[sym]["strikes"][f"{k:g}"][right].update(events.get(ss, {}))
    # A candidate-only name never enters the list rows' file; its chain error moves with it.
    listed_syms = {s for s, _, _ in wanted}
    for sym in [s for s in result if s not in listed_syms]:
        gone = result.pop(sym)
        if sym in plans:
            c = cand.setdefault(
                sym, {**plans[sym], "spot": gone["spot"], "expiry": None, "dte": None, "strikes": {}}
            )
            if gone.get("error"):
                c["error"] = gone["error"]
    if problem:
        problem = f"options feed stopped after {done}/{len(batches)} batches: {problem}"
    return result, cand, problem


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


RATE_LIMITED = "not fetched: the broker answered 429"


def rate_limit_problem(result: dict) -> str | None:
    """A run the broker rate-limited is INCOMPLETE, so the retry and the next run record it again.
    It used to be stamped complete with the unfetched symbols merely annotated, so every later run
    read "already exists; not overwritten" and the day's file stayed partial for good (2026-10-08).
    A per-symbol chain error (a delisted name) is not transient and does not make a run incomplete:
    it would never complete."""
    n = sum(1 for s in result.values() if s.get("error") == RATE_LIMITED)
    return f"broker rate limit (429): {n} symbol(s) not fetched" if n else None


def _complete(path: Path) -> bool:
    """A recorded file that says it is complete (an unreadable one is not)."""
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")).get("complete", True))
    except (OSError, ValueError):
        return False


def today_et() -> str:
    """The session date the evening runs record: the calendar date in New York."""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


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


# ------------------------------------------------------------------------------------------------
# Legs: the vendor's own option against the monthly the user would trade instead (2026-10-07).
#
# The user's rule: recommend the vendor's strike only when THAT option is liquid -- a tight bid/ask,
# decent open interest and volume -- and otherwise the standard monthly on the user's cycle, 45-60
# days out where one is listed, else 30-60. Weeklies usually quote wider and trade thinner.
#
# The bars (chosen 2026-10-16 from six sessions of 11:30 and 14:30 readings, ~5,700 legs a side):
# the vendor's weekly options are mostly not tradable -- median open interest 7, volume 3 by 14:30,
# spread 22% of mid -- against the cycle monthly's 877, 40 and 10%. Ten percent of mid (or a nickel),
# 100 open and 10 traded: stricter bars barely move who wins, looser ones admit weeklies with a
# single-digit open interest. Under them about 85% of rows go to the monthly. The monthly is not
# always liquid either (far out-of-the-money calls are cheap, so wide in percent: ~1 in 5 covered-call
# monthlies pass), so each row also says whether its monthly passes, never implying it.

CYCLE_PREFERRED = (45, 60)
CYCLE_ALLOWED = (30, 60)
LEG_BARS: dict | None = {
    "max_spread_pct": 0.10,
    "max_spread_abs": 0.05,
    "min_open_interest": 100,
    "min_volume": 10,
}
EPS = 1e-9


def cycle_expiry(expirations: list[dict], is_monthly) -> dict | None:
    """The standard monthly the user trades: inside 45-60 days if one is listed, else inside 30-60
    (the latest there, nearest the preferred range). None when neither range holds a monthly --
    monthlies are 28 or 35 days apart, so after a 35-day gap 30-60 can be empty."""
    monthly = [e for e in expirations if is_monthly(e["expiration"])]
    for lo, hi in (CYCLE_PREFERRED, CYCLE_ALLOWED):
        inside = [e for e in monthly if lo <= e["dte"] <= hi]
        if inside:
            return max(inside, key=lambda e: e["dte"])
    return None


def option_symbol(root: str, expiry_iso: str, right: str, strike: float) -> str:
    """The broker's option symbol: OCC with the root padded to six (`AAPL  261120P00230000`)."""
    yymmdd = expiry_iso[2:4] + expiry_iso[5:7] + expiry_iso[8:10]
    return f"{root.replace('.', '/'):<6}{yymmdd}{right}{round(strike * 1000):08d}"


def row_legs(name: str, row: dict) -> list[dict]:
    """The vendor's option(s) for one row: one leg for a short put or covered call, the short and
    long legs of a credit spread."""
    sym = row["symbol"].replace("/", ".")
    exp = datetime.strptime(row["expiry"], "%m/%d/%Y").date().isoformat()
    if name == "credit-spreads":
        right = "P" if row.get("type") == "Put" else "C"
        strikes = row.get("strike") or {}
        return [
            {"symbol": sym, "role": role, "right": right, "expiry": exp, "strike": float(strikes[side])}
            for role, side in (("short", "sell"), ("long", "buy"))
            if isinstance(strikes.get(side), (int, float))
        ]
    right = "P" if name == "short-puts" else "C"
    k = row.get("strikePrice")
    if not isinstance(k, (int, float)):
        return []
    return [{"symbol": sym, "role": "short", "right": right, "expiry": exp, "strike": float(k)}]


def leg_passes(q: dict, bars: dict) -> bool:
    """A quoted option against the bars: two-sided and tight enough on either leg of the spread
    rule (percent of mid OR absolute), with enough open interest and volume."""
    bid, ask = q.get("bid"), q.get("ask")
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return False
    width, mid = ask - bid, (ask + bid) / 2
    # A cent-priced width compared in floats: 0.23 - 0.18 is 0.0500000000000000017, one nickel.
    tight = width / mid <= bars["max_spread_pct"] + EPS or width <= bars["max_spread_abs"] + EPS
    return (
        tight
        and (q.get("open_interest") or 0) >= bars["min_open_interest"]
        and (q.get("volume") or 0) >= bars["min_volume"]
    )


def recommend(vendor: list[dict], monthly: list[dict] | None, bars: dict | None) -> str | None:
    """'vendor' when every vendor leg passes, else 'monthly' when the user's cycle lists one, else
    'none'; None while the bars are unset (readings only)."""
    if bars is None:
        return None
    if vendor and all(leg_passes(q, bars) for q in vendor):
        return "vendor"
    return "monthly" if monthly else "none"


def monthly_passes(monthly: list[dict] | None, bars: dict | None) -> bool | None:
    """Whether the cycle monthly's own legs pass the bars; None with no monthly or no bars."""
    if bars is None or not monthly:
        return None
    return all(leg_passes(q, bars) for q in monthly)


def apply_bars(rows: list[dict], bars: dict | None) -> None:
    """Judge every row's legs against `bars`, in place: each quoted leg's `passes`, the row's
    `recommendation` and whether its monthly passes. The one place the bars are applied, so a
    reader (the console's screener page) shows the verdict rather than re-deriving it."""
    for r in rows:
        for leg in [*r["vendor"], *(r["monthly"] or [])]:
            q = leg.get("quote")
            leg["passes"] = None if bars is None or not q else leg_passes(q, bars)
        vendor_q = [leg.get("quote") or {} for leg in r["vendor"]]
        monthly_q = [leg.get("quote") or {} for leg in r["monthly"]] if r["monthly"] else None
        r["recommendation"] = recommend(vendor_q, monthly_q, bars)
        r["monthly_passes"] = monthly_passes(monthly_q, bars)


def cmd_rebar(_args) -> int:
    """Re-apply the current bars to every saved leg reading (local files only, no broker). The
    readings taken before the bars were chosen (2026-10-07 to 10-16) carry no verdict; this gives
    them one, and does the same again whenever the bars change."""
    from cherrypick.core.jsonio import write_json_atomic

    changed = 0
    for path in sorted((store_dir() / "screener-greeks" / "legs").glob("*/*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        apply_bars(doc.get("rows") or [], LEG_BARS)
        doc["bars"] = LEG_BARS
        write_json_atomic(path, doc, indent=None, default=None)
        changed += 1
    # The earnings calendar's spreads (2026-10-17), judged by the same bars.
    for path in sorted((store_dir() / "screener-greeks" / "earnings-legs").glob("*/*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        judge_earnings_legs(doc.get("rows") or [], LEG_BARS)
        doc["bars"] = LEG_BARS
        write_json_atomic(path, doc, indent=None, default=None)
        changed += 1
    print(json.dumps({"ok": True, "files": changed, "bars": LEG_BARS}))
    return 0


async def _quote_legs(session, ub, rows: list[dict]) -> None:
    """Fill each leg's broker quote in place: bid, ask, volume, open interest. REST snapshots in the
    builder's batches and pacing."""
    from tastytrade.market_data import get_market_data_by_type

    wanted: dict[str, list[dict]] = {}
    for r in rows:
        for leg in [*r["vendor"], *(r["monthly"] or [])]:
            wanted.setdefault(leg["option"], []).append(leg)
    for batch in ub._batches(list(wanted), ub.TT_QUOTE_BATCH):
        for q in await get_market_data_by_type(session, options=batch):
            for leg in wanted.get(q.symbol, []):
                leg["quote"] = {
                    "bid": _f(q.bid),
                    "ask": _f(q.ask),
                    "volume": _f(q.volume),
                    "open_interest": _f(q.open_interest),
                    "updated_at": ub._iso(q.updated_at),
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
        await asyncio.sleep(ub.TT_PAUSE_S)


def plan_legs(lists: dict[str, dict], chains: dict[str, list], skip: set[str], is_monthly) -> list[dict]:
    """One entry per row of a name not held illiquid: the vendor's legs and the same strikes on the
    user's cycle monthly (the nearest listed strike), or None when the chain holds no such monthly."""
    out = []
    for name, key in LISTS:
        for row in (lists.get(name) or {}).get(key) or []:
            legs = row_legs(name, row)
            if not legs or legs[0]["symbol"] in skip:
                continue
            for leg in legs:
                leg["option"] = option_symbol(leg["symbol"], leg["expiry"], leg["right"], leg["strike"])
            cyc = cycle_expiry(chains.get(legs[0]["symbol"]) or [], is_monthly)
            monthly = None
            if cyc and cyc["strikes"]:
                monthly = []
                for leg in legs:
                    k, call, put = min(
                        cyc["strikes"], key=lambda s, leg=leg: abs(float(s[0]) - leg["strike"])
                    )
                    monthly.append(
                        {
                            "symbol": leg["symbol"],
                            "role": leg["role"],
                            "right": leg["right"],
                            "expiry": cyc["expiration"],
                            "dte": cyc["dte"],
                            "strike": float(k),
                            "option": put if leg["right"] == "P" else call,
                        }
                    )
            out.append({"list": name, "symbol": legs[0]["symbol"], "vendor": legs, "monthly": monthly})
    return out


# ------------------------------------------------------------------------------------------------
# The earnings calendar's suggested spreads (2026-10-17). The calendar (`technicals earnings-calendar`)
# writes target strikes; these are the listed ones and what they trade at in regular hours.

EARNINGS_DAYS = 7  # the calendar's default window: the names a reader is about to act on


def earnings_rows(day) -> tuple[str | None, list[dict]]:
    """(the calendar's session, its rows to price): strong reads with a suggested spread on a known
    expiry, reporting from `day` through EARNINGS_DAYS on, rated very or somewhat liquid, optionable,
    US -- the tab's default view widened to somewhat liquid."""
    from datetime import timedelta

    from cherrypick.core import home as _home

    # technicals' `earnings_calendar.out_dir()`, resolved through core: this runs (and is tested)
    # where the technicals package is not installed.
    folder = _home.data_dir("technicals", env="TECHNICALS_DATA_DIR") / "earnings-calendar"
    files = sorted(folder.glob("????-??-??.json"))
    files = [p for p in files if p.stem <= day.isoformat()]
    if not files:
        return None, []
    doc = json.loads(files[-1].read_text(encoding="utf-8"))
    last = (day + timedelta(days=EARNINGS_DAYS)).isoformat()
    rows = [
        r
        for r in doc.get("rows") or []
        if (r.get("suggestion") or {}).get("expiry")
        and day.isoformat() <= r["date"] <= last
        and r.get("liquidity_rank") in (1, 2)
        and r.get("optionable")
        and r.get("region") == "US"
    ]
    return files[-1].stem, rows


def snap_spread(sug: dict, strikes: list[list]) -> tuple[list[float], list[str]] | None:
    """The suggested spread on listed strikes: ([long, short] strikes, [long, short] option symbols),
    or None when the chain cannot hold it. The calendar's rules, on listed strikes: the at-the-money
    leg is the nearest strike; a debit's short is the nearest strike to its target, at least one
    strike beyond the long; a credit's long is the first strike at or beyond its target (below it
    for puts) -- "just outside the expected move" -- and never the short's own strike."""
    ks = sorted({float(s[0]) for s in strikes})
    by_k = {float(s[0]): s for s in strikes}  # a chain entry: [strike, call symbol, put symbol]
    if not ks:
        return None
    put = sug["right"] == "P"
    col = 2 if put else 1

    def nearest(t: float) -> float:
        return min(ks, key=lambda k: (abs(k - t), k))

    if sug["kind"] == "debit":
        long_ = nearest(sug["long"])
        out = [k for k in ks if (k < long_ if put else k > long_)]
        if not out:
            return None
        short = min(out, key=lambda k: (abs(k - sug["short"]), k))
    else:
        short = nearest(sug["short"])
        beyond = [k for k in ks if (k <= sug["long"] if put else k >= sug["long"]) and k != short]
        beyond = [k for k in beyond if (k < short if put else k > short)]
        if not beyond:
            return None
        long_ = max(beyond) if put else min(beyond)
    return [long_, short], [by_k[long_][col], by_k[short][col]]


def price_spread(kind: str, long_q: dict, short_q: dict, width: float) -> dict:
    """At mid and at the natural fill (pay the ask, sell at the bid), per share, with the width's share
    and the max loss for one spread in dollars. A leg without a two-sided quote prices nothing."""

    def mid(q):
        b, a = q.get("bid"), q.get("ask")
        return (b + a) / 2 if b is not None and a is not None and a >= b >= 0 else None

    ml, ms = mid(long_q), mid(short_q)
    if ml is None or ms is None:
        return {"mid": None, "natural": None, "share_of_width": None, "max_loss": None}
    if kind == "debit":
        at_mid, natural = ml - ms, long_q["ask"] - short_q["bid"]
        loss = at_mid * 100
    else:
        at_mid, natural = ms - ml, short_q["bid"] - long_q["ask"]
        loss = (width - at_mid) * 100
    return {
        "mid": round(at_mid, 4),
        "natural": round(natural, 4),
        "share_of_width": round(at_mid / width, 4) if width else None,
        "max_loss": round(loss, 2),
    }


def plan_earnings_legs(rows: list[dict], chains: dict[str, list]) -> list[dict]:
    """One entry per calendar row whose spread the chain can hold on its expiry. Each carries the
    row's entry session and reaction day, so the forward record can find the reading it entered on
    and the days it marks out on."""
    out = []
    for r in rows:
        sug = r["suggestion"]
        when = {
            "symbol": r["symbol"],
            "date": r["date"],
            "entry_session": r.get("entry_session"),
            "reaction_day": r.get("reaction_day"),
        }
        exp = next((e for e in chains.get(r["symbol"]) or [] if e["expiration"] == sug["expiry"]), None)
        snapped = snap_spread(sug, exp["strikes"]) if exp else None
        if snapped is None:
            out.append({**when, "suggestion": sug, "legs": None, "why": "no listed spread"})
            continue
        (k_long, k_short), (o_long, o_short) = snapped
        out.append(
            {
                **when,
                "suggestion": sug,
                "kind": sug["kind"],
                "right": sug["right"],
                "expiry": sug["expiry"],
                "width": abs(k_short - k_long),
                "legs": [
                    {"role": "long", "strike": k_long, "option": o_long},
                    {"role": "short", "strike": k_short, "option": o_short},
                ],
            }
        )
    return out


MARK_LOOKBACK_DAYS = 12  # entry readings far enough back for a day-2 mark after a long weekend


def marks_due(root: Path, today) -> list[dict]:
    """The forward record's marks due today (step 5): each spread entered on its entry session --
    priced in that session's LAST reading -- marked again on its reaction day (`react`) and the
    session after (`day2`), on the same options. A row with no entry session (its time of day
    unknown) or not priced on it has no record: nothing is assumed."""
    from datetime import date as _date
    from datetime import timedelta

    from cherrypick.core import calendar as cal

    folder = root / "earnings-legs"
    out = []
    for d in sorted(p for p in folder.glob("????-??-??") if p.is_dir()):
        taken = _date.fromisoformat(d.name)
        if not (today - timedelta(days=MARK_LOOKBACK_DAYS)) <= taken < today:
            continue
        files = sorted(d.glob("*.json"))
        if not files:
            continue
        doc = json.loads(files[-1].read_text(encoding="utf-8"))
        for r in doc.get("rows") or []:
            if not r.get("legs") or r.get("entry_session") != d.name or not r.get("reaction_day"):
                continue
            react = _date.fromisoformat(r["reaction_day"])
            which = "react" if today == react else "day2" if today == cal.next_trading_day(react) else None
            if which is None:
                continue
            out.append(
                {
                    "symbol": r["symbol"],
                    "date": r["date"],
                    "calendar_day": doc.get("calendar_day"),
                    "entry_reading": f"{d.name}/{files[-1].name}",
                    "which": which,
                    "kind": r["kind"],
                    "right": r["right"],
                    "expiry": r["expiry"],
                    "width": r["width"],
                    "legs": [{k: leg[k] for k in ("role", "strike", "option")} for leg in r["legs"]],
                }
            )
    return out


def judge_earnings_legs(rows: list[dict], bars: dict | None) -> None:
    """Each quoted leg against the bars and each spread's price, in place."""
    for r in rows:
        if not r.get("legs"):
            continue
        for leg in r["legs"]:
            q = leg.get("quote")
            leg["passes"] = None if bars is None or not q else leg_passes(q, bars)
        r["passes"] = (
            None
            if any(leg.get("passes") is None for leg in r["legs"])
            else all(leg["passes"] for leg in r["legs"])
        )
        long_q, short_q = (r["legs"][0].get("quote") or {}), (r["legs"][1].get("quote") or {})
        r.update(price_spread(r["kind"], long_q, short_q, r["width"]))


async def _quote_options(session, ub, legs: list[dict]) -> None:
    """Fill each leg's broker quote in place, by its `option` symbol, in the builder's batches."""
    from tastytrade.market_data import get_market_data_by_type

    wanted: dict[str, list[dict]] = {}
    for leg in legs:
        wanted.setdefault(leg["option"], []).append(leg)
    for batch in ub._batches(list(wanted), ub.TT_QUOTE_BATCH):
        for q in await get_market_data_by_type(session, options=batch):
            for leg in wanted.get(q.symbol, []):
                leg["quote"] = {
                    "bid": _f(q.bid),
                    "ask": _f(q.ask),
                    "volume": _f(q.volume),
                    "open_interest": _f(q.open_interest),
                    "updated_at": ub._iso(q.updated_at),
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
        await asyncio.sleep(ub.TT_PAUSE_S)


async def _measure_then_legs(
    session, ub, names, chain_cache, lists, today, skip, limit, earnings=(), marks=()
):
    """The spread readings, then the legs, in one event loop. The legs: every row of a name not held
    illiquid, the vendor's option against the monthly on the user's cycle, with chains from this
    run's cache and the universe builder's (it measured the candidates half an hour earlier); a name
    in neither gets its vendor legs only. Then the earnings calendar's suggested spreads (`earnings`,
    its rows), whose names were measured with the rest so their chains are cached. A failure in the
    legs keeps the spread readings; a failure in the earnings spreads keeps both."""
    measured = await ub._measure(session, names, chain_cache, None)
    chains = {**ub._read_json(ub.store_dir() / "chains" / f"{today.isoformat()}.json", {}), **chain_cache}
    legs = plan_legs(lists, chains, skip, ub.is_monthly)[:limit]
    try:
        await _quote_legs(session, ub, legs)
    except Exception as exc:  # noqa: BLE001 -- reported by the caller, after the readings are saved
        return measured, legs, f"{type(exc).__name__}: {exc}"[:300], [], None
    spreads = plan_earnings_legs(list(earnings), chains)
    try:
        await _quote_options(session, ub, [leg for r in [*spreads, *marks] for leg in r["legs"] or []])
    except Exception as exc:  # noqa: BLE001 -- the list readings stand; the earnings run is reported
        return measured, legs, None, spreads, f"{type(exc).__name__}: {exc}"[:300]
    return measured, legs, None, spreads, None


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
    from cherrypick.technicals import liquidity

    # Names held illiquid are not measured between sweeps (technicals `liquidity.skip`).
    names = measure_names(lists, cands | liquidity.skip(today))[: args.limit]
    # The earnings calendar's suggested spreads need their names' chains: measured with the rest
    # (a name the universe already measures has its chain in the builder's file).
    calendar_day, earnings = earnings_rows(today)
    names = sorted({*names, *(r["symbol"] for r in earnings if r["symbol"] not in cands)})
    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1
    root = store_dir() / "screener-greeks"
    marks = marks_due(root, today)
    chain_path = root / "chains" / f"{today.isoformat()}.json"
    chain_cache = ub._read_json(chain_path, {})
    started = datetime.now(UTC)
    try:
        session = SessionManager(store).get_session()
        # One event loop for both steps: the broker session's connection belongs to the loop it was
        # first used in, and a second asyncio.run fails with "Event loop is closed".
        measured, legs, legs_error, spreads, spreads_error = asyncio.run(
            _measure_then_legs(
                session,
                ub,
                names,
                chain_cache,
                lists,
                today,
                liquidity.skip(today),
                args.limit,
                earnings,
                marks,
            )
        )
    except Exception as exc:  # noqa: BLE001 -- keep the chains gathered so far for the next run
        ub._write_json(chain_path, chain_cache)
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:300]}))
        return 1
    ub._write_json(chain_path, chain_cache)
    out = root / "measurements" / today.isoformat() / f"{started.astimezone(et):%H%M}.json"
    ub._write_json(out, {"measured_at": started.isoformat(), "list_date": day, "names": measured})
    usable = sum(1 for row in measured.values() if ub.reading(row, started)["option"])
    if legs_error:
        print(json.dumps({"ok": False, "path": str(out), "legs_error": legs_error}))
        return 1
    apply_bars(legs, LEG_BARS)
    legs_out = root / "legs" / today.isoformat() / f"{started.astimezone(et):%H%M}.json"
    ub._write_json(
        legs_out, {"measured_at": started.isoformat(), "list_date": day, "bars": LEG_BARS, "rows": legs}
    )
    spreads_out = None
    if spreads:
        judge_earnings_legs(spreads, LEG_BARS)
        spreads_out = root / "earnings-legs" / today.isoformat() / f"{started.astimezone(et):%H%M}.json"
        ub._write_json(
            spreads_out,
            {
                "measured_at": started.isoformat(),
                "calendar_day": calendar_day,
                "bars": LEG_BARS,
                "error": spreads_error,
                "rows": spreads,
            },
        )
    marks_out = None
    if marks:
        for m in marks:
            long_q, short_q = (m["legs"][0].get("quote") or {}), (m["legs"][1].get("quote") or {})
            m.update(price_spread(m["kind"], long_q, short_q, m["width"]))
        marks_out = root / "earnings-marks" / today.isoformat() / f"{started.astimezone(et):%H%M}.json"
        ub._write_json(marks_out, {"measured_at": started.isoformat(), "error": spreads_error, "rows": marks})
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(out),
                "names": len(measured),
                "with_option_reading": usable,
                "legs_path": str(legs_out),
                "rows": len(legs),
                "with_monthly": sum(1 for r in legs if r["monthly"]),
                "quoted_vendor_legs": sum(1 for r in legs for leg in r["vendor"] if leg.get("quote")),
                "earnings_path": str(spreads_out) if spreads_out else None,
                "earnings_spreads": len(spreads),
                "earnings_listed": sum(1 for r in spreads if r.get("legs")),
                "earnings_passing": sum(1 for r in spreads if r.get("passes")),
                "earnings_error": spreads_error,
                "earnings_marks_path": str(marks_out) if marks_out else None,
                "earnings_marks": len(marks),
            },
            indent=1,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("mode", nargs="?", choices=("greeks", "measure", "rebar"), default="greeks")
    ap.add_argument("--date", help="greeks: the list date to record (default: today, ET)")
    ap.add_argument(
        "--final",
        action="store_true",
        help="greeks: the night's last chance -- today's lists still unsaved is a failure (exit 1)",
    )
    ap.add_argument("--limit", type=int, help="record at most this many symbols (a trial run)")
    ap.add_argument("--force", action="store_true", help="measure: run outside the measuring window")
    args = ap.parse_args(argv)
    if args.mode == "rebar":
        return cmd_rebar(args)
    if args.mode == "measure":
        return cmd_measure(args)

    day = args.date or today_et()
    lists = load_lists(day)
    if not lists:
        if args.final and not args.date:
            print(
                f"today's screener lists ({day}) were never saved, so their broker chain cannot be "
                "recorded: this session has no answer key. Check the evening chart capture.",
                file=sys.stderr,
            )
            return 1
        print(f"no saved screener lists for {day} yet; nothing to record (the late run tries again)")
        return 0
    out = store_dir() / "screener-greeks" / f"{day}.json"
    cand_path = store_dir() / "screener-greeks" / "candidates" / f"{day}.json"
    # The list rows already recorded, the candidates not (a day recorded before they were, or a run
    # that died between the two): record the candidates alone, leaving the rows' file as it is.
    rows_done = False
    if out.exists() and not args.limit:
        if _complete(out):
            if _complete(cand_path) or not candidate_plan(day):
                print(f"{out.name} already exists; not overwritten")
                return 0
            rows_done = True
            print(f"{out.name} already exists; recording the spread candidates alone")
        else:
            print(f"{out.name} is incomplete; recording again")

    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

    store = CredentialStore(SHARED_SERVICE)
    session = SessionManager(store).get_session()
    from cherrypick.technicals import liquidity

    # The vendor's strike rule is studied on names worth trading: rows of names held illiquid are
    # skipped (decided 2026-10-07), except on a sweep day.
    skip = liquidity.skip()
    wanted = (
        {} if rows_done else {k: v for k, v in anchors(lists).items() if k[0].replace("/", ".") not in skip}
    )
    plans = {s: p for s, p in candidate_plan(day).items() if s.replace("/", ".") not in skip}
    started = time.monotonic()
    result, cand, problem = asyncio.run(collect(session, wanted, args.limit, plans))
    problem = problem or rate_limit_problem(result) or rate_limit_problem(cand)
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
    from cherrypick.core.jsonio import write_json_atomic

    if not rows_done:
        write_json_atomic(out, doc, indent=None, default=None)
    cand_out = store_dir() / "screener-greeks" / "candidates" / out.name
    write_json_atomic(
        cand_out,
        {
            "list_date": day,
            "fetched_at": doc["fetched_at"],
            "rule": {"dte": CAND_DTE, "reach": CAND_REACH, "strikes": CAND_STRIKES, "near": CAND_NEAR},
            "complete": problem is None,
            "problem": problem,
            "symbols": cand,
        },
        indent=None,
        default=None,
    )
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
                "candidates": len(cand),
                "candidate_options": sum(len(legs) for c in cand.values() for legs in c["strikes"].values()),
                "seconds": round(time.monotonic() - started),
                "file": str(out),
            },
            indent=1,
        )
    )
    return 0 if problem is None else 1


if __name__ == "__main__":
    sys.exit(main())
