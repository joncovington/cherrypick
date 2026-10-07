"""Derived flow: one scored row per order in a QuikOptions capture, from the site's tables and the broker.

**Why this exists.** The site says where each fill sat (bid, ask, middle) and calls it bullish or
bearish. That is a lean, not a fact: a put bought on the ask may close a short, a deep in-the-money
trade is mostly stock replacement, two prints at one millisecond may be one hedged order, and premium
overstates in-the-money trades. This script turns the day's lists into **derived flows** — one row
per order — and scores each on four factors the suite can check (docs/quikoptions-plan.md, Phase 6).

A derived flow is an outright, a sweep or a spread from the capture, with prints sharing a symbol and
a timestamp grouped (they are one order) and spreads printed together grouped (a roll). Each gets:

- **read**: bought / sold from where the fill sat (edge at or past +/-0.5), or the site's own signs for
  a spread (price for the side, delta checked against it: the same sign for calls, opposite for
  puts); `unread` when the fill is too near the middle or a spread's signs do not fit. The view
  (bullish / bearish) combines that with call or put, or a spread's signed delta. Unread flows are
  listed, never ranked.
- **exposure**: delta, from the volatility the trade's own price implies at the session's close (the
  broker's), and **delta dollars** — contracts x 100 x |delta| x the stock's close — the stock-
  equivalent size of the bet. Ranked on this, with premium beside it.
- **flags**: sweep, opening (the site's Openings list), volume over open interest, paired prints,
  roll, near max (a spread at or past 90% of its width: closing, most likely), deep ITM (|delta| >=
  0.85), lottery (|delta| <= 0.10), 7 days or less, earnings event (expiring within 30 days after
  the next earnings date: a bet on the event), before an ex-dividend date (a deep in-the-money call
  there is a dividend trade, not a bet), label and edge disagreeing.

The score, 0-100 and signed by the view, is four factors multiplied — each a named constant here,
provisional until the outcome record says otherwise:

    Size        delta dollars on a fixed log scale, $100K -> 0.05 to $50M -> 1.0 (fixed, so days compare)
    Conviction  0.3 + 0.7 x |edge|, +0.1 for a sweep; 0.7 for a spread; halved where the site's own
                fill label disagrees with the edge
    Purity      1.0 for |delta| 0.2-0.75; 0.75 just outside; 0.5 lottery; 0.3 deep ITM, a roll, paired
                opposite prints, or near max; 0 a deep in-the-money call before an ex-dividend date;
                x0.75 for a sold single-leg option (often income or an overwrite)
    Opening     1.0 on the Openings list, 0.85 when the day's volume passed the open interest it
                started with, else 0.6 — and the next morning, from the change in open interest:
                1.0 opened, 0.6 mixed, 0.15 closed

**Three moments.** `score` runs after the capture (16:50 ET, scheduled): it reads each name's official
close, each contract's starting open interest and day volume, and each name's next earnings date from
the broker (read-only market data, the shared login), and writes `hot-options/<session>.flow.json`.
`confirm` runs the next trading morning: each contract's open interest again, so each flow is
`opened`, `closed` or `mixed`, and a confirmed score sits beside the first. Every `score` run also
records the outcome for the sessions 1 and 5 trading days back — each name's close today against its
close then — so the weights can be judged on evidence before anyone leans on them.

A script, not package code: it reaches the broker. It writes only `.flow.json` files beside the
captures; a failure leaves the capture and every earlier day as they were.

    python scripts/quikoptions_flow.py score   [--session YYYY-MM-DD]
    python scripts/quikoptions_flow.py confirm [--session YYYY-MM-DD]   # default: the last session
    python scripts/quikoptions_flow.py show    [--session YYYY-MM-DD]   # print the table, offline
    python scripts/quikoptions_flow.py audit   --rank N --tape bought|sold|middle [--note ...]
    python scripts/quikoptions_flow.py audit   --report                  # agreement with the tape
    python scripts/quikoptions_flow.py review                            # the fixed test, once ready
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FLOW_VERSION = 1

# --- the score's constants: provisional, each to be judged against the outcome record -------------
READ_EDGE = 0.5  # an edge at or past this reads as bought (+) or sold (-)
SIZE_FLOOR, SIZE_CEILING = 1e5, 5e7  # delta dollars mapped to 0.05 .. 1.0 on a log scale
SPREAD_CONVICTION = 0.7
SWEEP_BONUS = 0.1
SITE_OPPOSITE = 0.5  # conviction multiplier when the site's own sentiment is the opposite view
SITE_NEUTRAL = 0.75  # ... and when the site calls it neutral while the edge reads it
DELTA_CHECK = 0.10  # the broker's delta and the model's further apart than this: flagged
CLOSE_CHECK = 0.005  # the broker's close and Dolt's further apart than this share: flagged
GREEKS_WAIT_S = 150  # how long scoring waits for the streamer to bring the day's greeks
DEEP_ITM, LOTTERY = 0.85, 0.10
PURE_LOW, PURE_HIGH = 0.2, 0.75
NEAR_MAX = 0.9  # a spread priced at or past this share of its width
OPENING_PRIOR = {"list": 1.0, "volume": 0.85, "unknown": 0.6}
OPENING_CONFIRMED = {"opened": 1.0, "mixed": 0.6, "closed": 0.15}
CONFIRM_SHARE = 0.5  # open interest moved by at least this share of the trade's size
SHORT_DATED = 7
EVENT_TAIL = 30  # a contract expiring within this many days after earnings is about the event
SOLD_PURITY = 0.75  # a sold single-leg option is often income or an overwrite, not a view
RATE = 0.04  # risk-free rate for the delta model; a placeholder, small against the vol
OUTCOME_LAGS = (1, 5)


# ------------------------------------------------------------------------------------------------
# Option math. Pure.
# ------------------------------------------------------------------------------------------------


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black_scholes(spot: float, strike: float, years: float, vol: float, cp: str) -> tuple[float, float]:
    """(price, delta) for a European option; at expiry or with no vol, intrinsic and a 0/±1 delta."""
    if years <= 0 or vol <= 0:
        itm = spot > strike if cp == "call" else spot < strike
        return max(0.0, spot - strike if cp == "call" else strike - spot), (
            1.0 if cp == "call" else -1.0
        ) if itm else 0.0
    d1 = (math.log(spot / strike) + (RATE + vol * vol / 2) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    if cp == "call":
        return spot * _ncdf(d1) - strike * math.exp(-RATE * years) * _ncdf(d2), _ncdf(d1)
    return strike * math.exp(-RATE * years) * _ncdf(-d2) - spot * _ncdf(-d1), _ncdf(d1) - 1


def implied_vol(spot: float, strike: float, years: float, price: float, cp: str) -> float | None:
    """The volatility at which the model prices the option at `price`; None outside 1%..500%."""
    lo, hi = 0.01, 5.0
    if not (
        black_scholes(spot, strike, years, lo, cp)[0]
        <= price
        <= black_scholes(spot, strike, years, hi, cp)[0]
    ):
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if black_scholes(spot, strike, years, mid, cp)[0] > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def option_delta(spot, strike, days, price, cp, fallback_vol=None) -> tuple[float | None, str]:
    """(delta, how): from the trade's own implied vol; at or under intrinsic, ±1 (deep in the money);
    else the name's 30-day implied vol from the broker; else unknown."""
    if not spot or price is None or strike is None or cp not in ("call", "put"):
        return None, "no spot"
    years = max(days, 0.5) / 365
    vol = implied_vol(spot, strike, years, price, cp)
    if vol is not None:
        return black_scholes(spot, strike, years, vol, cp)[1], "trade"
    intrinsic = max(0.0, spot - strike if cp == "call" else strike - spot)
    if intrinsic > 0 and price <= intrinsic * 1.02:
        return (1.0 if cp == "call" else -1.0), "intrinsic"
    if fallback_vol:
        return black_scholes(spot, strike, years, fallback_vol, cp)[1], "name iv"
    return None, "no vol"


def occ_symbol(symbol: str, expires: str, strike: float, cp: str) -> str:
    """The broker's option symbol: `PCG   270115C00016000`."""
    d = date.fromisoformat(expires)
    return f"{symbol:<6}{d:%y%m%d}{'C' if cp == 'call' else 'P'}{round(strike * 1000):08d}"


_SPREAD = re.compile(r"^(\d{6}) ([\d.]+)/(?:(\d{6}) )?([\d.]+) (\w+)$")


def spread_legs(spread: str, kind: str | None) -> list[dict] | None:
    """The legs of a simple two-leg spread from the site's description: `261009 11/11.5 CS` (a call
    vertical) or `261016 23/261030 22 CSCAL` (a call calendar). None for anything else: a structure
    this cannot name is never guessed at."""
    m = _SPREAD.match(spread or "")
    if not m or (kind or m.group(5)) not in ("CS", "PS", "CSCAL", "PSCAL"):
        return None
    cp = "call" if (kind or m.group(5)).startswith("C") else "put"
    first = datetime.strptime(m.group(1), "%y%m%d").date().isoformat()
    second = datetime.strptime(m.group(3), "%y%m%d").date().isoformat() if m.group(3) else first
    return [
        {"expires": first, "strike": float(m.group(2)), "cp": cp},
        {"expires": second, "strike": float(m.group(4)), "cp": cp},
    ]


# ------------------------------------------------------------------------------------------------
# Building and scoring flows. Pure: a capture and the broker's numbers in, rows out.
# ------------------------------------------------------------------------------------------------


def _leg_text(expires: str, strike: float, cp: str) -> str:
    return f"{date.fromisoformat(expires):%d %b %y} {strike:g}{'C' if cp == 'call' else 'P'}"


SPREAD_KIND = {"CS": "call spread", "PS": "put spread", "CSCAL": "call calendar", "PSCAL": "put calendar"}


def describe(
    kind: str, legs: list[dict], site_text: str | None = None, site_type: str | None = None
) -> tuple[str, str]:
    """(what, kind label) in one order for every flow — date, strike, then the kind: `15 Jan 27
    16C` / `outright`, `09 Oct 26 43.5/45.5C` / `call spread`, `16 Oct 26 23C / 30 Oct 26 23C` /
    `call calendar`, `16 Oct 26 23C / 30 Oct 26 22C` / `call diagonal`. The site's own
    `261009 43.5/45.5 CS` is kept only where its legs cannot be named (decided 2026-10-03: one
    order, one date format, everywhere the table is shown)."""
    if kind != "spread":
        leg = legs[0] if legs else None
        return (_leg_text(leg["expires"], leg["strike"], leg["cp"]) if leg else (site_text or "")), kind
    label = SPREAD_KIND.get(site_type or "", (site_type or "spread").lower())
    if len(legs) != 2:
        return site_text or "", label
    a, b = legs
    if a["expires"] == b["expires"]:
        cp = "C" if a["cp"] == "call" else "P"
        return f"{date.fromisoformat(a['expires']):%d %b %y} {a['strike']:g}/{b['strike']:g}{cp}", label
    if a["strike"] != b["strike"]:
        # The site's CSCAL / PSCAL covers both shapes: two dates at two strikes is a diagonal.
        label = label.replace("calendar", "diagonal")
    return (
        f"{_leg_text(a['expires'], a['strike'], a['cp'])} / {_leg_text(b['expires'], b['strike'], b['cp'])}",
        label,
    )


def _days(session: str, expires: str | None) -> int | None:
    try:
        return (date.fromisoformat(expires) - date.fromisoformat(session)).days
    except (TypeError, ValueError):
        return None


def _read(edge: float | None) -> str | None:
    if edge is None:
        return None
    return "bought" if edge >= READ_EDGE else "sold" if edge <= -READ_EDGE else None


def _single(row: dict, table: str, session: str, market: dict) -> dict:
    sym = row.get("symbol")
    names = market.get("names", {})
    spot = (names.get(sym) or {}).get("close")
    side = row.get("side") or {}
    edge = side.get("edge")
    direction = _read(edge)
    cp = row.get("cp")
    view = None
    if direction and cp in ("call", "put"):
        view = "bullish" if (direction == "bought") == (cp == "call") else "bearish"
    days = _days(session, row.get("expires"))
    model, how = option_delta(
        spot, row.get("strike"), days or 0, row.get("price"), cp, (names.get(sym) or {}).get("iv")
    )
    key = (
        occ_symbol(sym, row["expires"], row["strike"], cp)
        if row.get("expires") and row.get("strike") and cp
        else None
    )
    # The broker's own delta at the close (the streamer's greeks) is the coherent one: the model's
    # mixes the trade's price at the time with the stock's close. The model is the fallback and the
    # cross-check.
    broker = (market.get("greeks") or {}).get(key)
    delta, how = (broker, "broker") if broker is not None else (model, how)
    site = (side.get("sentiment") or "").lower() or None
    contract = (market.get("contracts") or {}).get(key) or {}
    premium = row.get("premium")
    if premium is None and row.get("price") is not None and row.get("size") is not None:
        premium = row["price"] * row["size"] * 100
    return {
        "kind": "sweep" if table == "sweeps" else "outright",
        "symbol": sym,
        "name": row.get("name"),
        "what": row.get("expires")
        and row.get("strike")
        and cp
        and _leg_text(row["expires"], row["strike"], cp),
        "kind_label": "sweep" if table == "sweeps" else "outright",
        "time_et": row.get("time_et"),
        "legs": [
            {
                "symbol": key,
                "expires": row.get("expires"),
                "strike": row.get("strike"),
                "cp": cp,
                "size": row.get("size"),
            }
        ]
        if key
        else [],
        "size": row.get("size"),
        "price": row.get("price"),
        "premium": premium,
        "fill": side.get("fill"),
        "edge": edge,
        "site": site,
        "site_vote": None
        if not view or site is None
        else "agrees"
        if site == view
        else "neutral"
        if site == "neutral"
        else "opposite",
        "direction": direction,
        "view": view,
        "delta": delta,
        "delta_from": how,
        "model_delta": model,
        "spot": spot,
        "days": days,
        "start_oi": contract.get("open_interest"),
        "day_volume": contract.get("volume"),
        "flags": [],
    }


def _spread(row: dict, session: str) -> dict:
    price, delta = row.get("price"), row.get("delta")
    # The capture's rule (fetch_quikoptions.spread_direction): the price's sign is the side, and the
    # delta's sign must fit it — the same sign for a call spread, the opposite for a put spread.
    direction = None
    if price and delta and row.get("cp") in ("call", "put"):
        if ((price > 0) == (delta > 0)) == (row["cp"] == "call"):
            direction = "bought" if price > 0 else "sold"
    view = (
        "bullish"
        if delta and delta > 0 and direction
        else "bearish"
        if delta and delta < 0 and direction
        else None
    )
    legs = spread_legs(row.get("spread"), row.get("type")) or []
    what, kind_label = describe("spread", legs, row.get("spread"), row.get("type"))
    days = _days(session, row.get("expires"))
    width = (
        abs(legs[1]["strike"] - legs[0]["strike"])
        if len(legs) == 2 and legs[0]["expires"] == legs[1]["expires"]
        else None
    )
    return {
        "kind": "spread",
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "what": what,
        "kind_label": kind_label,
        "time_et": row.get("time_et"),
        "legs": [
            {
                **leg,
                "symbol": occ_symbol(row["symbol"], leg["expires"], leg["strike"], leg["cp"]),
                "size": row.get("size"),
            }
            for leg in legs
        ],
        "size": row.get("size"),
        "price": price,
        # A mixed structure's premium is the site's figure and fails price x size x 100
        # (fetch_quikoptions.validate_report), so it never sizes, scores or ranks a flow: it is
        # carried beside the row as `site_premium`, unverified, for display only.
        "premium": abs(row["premium"])
        if row.get("premium") is not None and not row.get("premium_unverified")
        else None,
        "site_premium": abs(row["premium"])
        if row.get("premium") is not None and row.get("premium_unverified")
        else None,
        "fill": None,
        "edge": None,
        "site": None,
        "site_vote": None,
        "direction": direction,
        "view": view,
        "delta": abs(delta) if delta is not None else None,
        "delta_from": "site",
        "spot": (row.get("underlying") or {}).get("last"),
        "days": days,
        "width": width,
        "group": row.get("group"),
        "start_oi": None,
        "day_volume": None,
        "flags": [],
    }


def build_flows(capture: dict, market: dict) -> list[dict]:
    """Every derived flow in a capture, flagged, before scoring. `market` is the broker's numbers:
    {"names": {sym: {close, iv, earnings, ex_dividend}}, "contracts": {occ: {open_interest, volume}}}."""
    session = capture["session"]
    t = capture.get("tables") or {}
    flows = [_single(r, "outrights", session, market) for r in t.get("outrights") or []]
    flows += [_single(r, "sweeps", session, market) for r in t.get("sweeps") or []]
    flows += [_spread(r, session) for r in t.get("spreads") or []]
    openings = {
        (r["symbol"], r.get("expires"), r.get("strike"), r.get("cp")) for r in t.get("openings") or []
    }
    voloi = {(r["symbol"], r.get("expires"), r.get("strike"), r.get("cp")): r for r in t.get("voloi") or []}
    names = market.get("names") or {}

    for f in flows:
        flags = f["flags"]
        if f["kind"] == "sweep":
            flags.append("sweep")
        leg = f["legs"][0] if f["kind"] != "spread" and f["legs"] else None
        key = (f["symbol"], leg["expires"], leg["strike"], leg["cp"]) if leg else None
        if key in openings:
            flags.append("opening")
        elif key in voloi or (
            f["start_oi"] is not None and f["day_volume"] and f["day_volume"] > f["start_oi"]
        ):
            flags.append("volume over OI")
        if f["days"] is not None and f["days"] <= SHORT_DATED:
            flags.append(f"≤{SHORT_DATED}d")
        d = f["delta"]
        if f["kind"] != "spread" and d is not None:
            if abs(d) >= DEEP_ITM:
                flags.append("deep ITM")
            elif abs(d) <= LOTTERY:
                flags.append("lottery")
        if f.get("width") and f["price"] is not None and abs(f["price"]) >= NEAR_MAX * f["width"]:
            flags.append("near max")
        info = names.get(f["symbol"]) or {}
        exp = max((leg["expires"] for leg in f["legs"]), default=None)
        if (
            info.get("earnings")
            and exp
            and session < info["earnings"] <= exp
            and (_days(info["earnings"], exp) or 0) <= EVENT_TAIL
        ):
            flags.append("earnings event")
        if (
            f["kind"] != "spread" and leg and leg["cp"] == "call" and d is not None and d >= DEEP_ITM
            and info.get("ex_dividend") and session < info["ex_dividend"] <= leg["expires"]
        ):  # fmt: skip
            flags.append("before ex-dividend")
        if f["site_vote"] == "opposite":
            flags.append("sentiment opposite")
        elif f["site_vote"] == "neutral":
            flags.append("sentiment neutral")
        if (
            f.get("model_delta") is not None
            and f["delta_from"] == "broker"
            and abs(f["model_delta"] - f["delta"]) > DELTA_CHECK
        ):
            flags.append("delta check")

    # Prints sharing a symbol and a timestamp are one order.
    by_time: dict[tuple, list[dict]] = {}
    for f in flows:
        if f["time_et"] and f["kind"] != "spread":
            by_time.setdefault((f["symbol"], f["time_et"]), []).append(f)
    for group in by_time.values():
        if len(group) > 1:
            opposite = len({g["view"] for g in group}) > 1
            for g in group:
                g["flags"].append("paired prints, opposite" if opposite else "paired prints")
    # Spreads printed together: a roll when their views differ.
    by_group: dict[str, list[dict]] = {}
    for f in flows:
        if f.get("group"):
            by_group.setdefault(f["group"], []).append(f)
    for group in by_group.values():
        roll = len({g["view"] for g in group}) > 1
        for g in group:
            g["flags"].append("roll" if roll else "linked")

    contracts = market.get("contracts") or {}
    for f in flows:
        for leg in f["legs"]:
            leg["start_oi"] = (contracts.get(leg["symbol"]) or {}).get("open_interest")
    for f in flows:
        f["delta_dollars"] = (
            round(f["size"] * 100 * abs(f["delta"]) * f["spot"], 2)
            if f["delta"] is not None and f["spot"] and f["size"]
            else None
        )
    return flows


def size_factor(delta_dollars: float | None, premium: float | None) -> float:
    x = delta_dollars if delta_dollars is not None else (premium or 0) * 0.5
    if x <= 0:
        return 0.05
    span = math.log10(SIZE_CEILING) - math.log10(SIZE_FLOOR)
    return round(min(1.0, max(0.05, 0.05 + 0.95 * (math.log10(x) - math.log10(SIZE_FLOOR)) / span)), 3)


def conviction(f: dict) -> float:
    if f["kind"] == "spread":
        c = SPREAD_CONVICTION if f["direction"] else 0.3
    elif f["edge"] is None:
        c = 0.3
    else:
        c = 0.3 + 0.7 * min(1.0, abs(f["edge"]))
        if f["kind"] == "sweep" and abs(f["edge"]) >= READ_EDGE:
            c = min(1.0, c + SWEEP_BONUS)
    if f["site_vote"] == "opposite":
        c *= SITE_OPPOSITE
    elif f["site_vote"] == "neutral":
        c *= SITE_NEUTRAL
    return round(c, 3)


def purity(f: dict) -> float:
    flags = f["flags"]
    if "before ex-dividend" in flags:
        return 0.0
    if any(x in flags for x in ("roll", "paired prints, opposite", "near max", "deep ITM")):
        return 0.3
    if f["kind"] == "spread":
        return 0.75  # a defined-risk structure: directional, but capped by design
    d = f["delta"]
    if d is None:
        base = 0.5
    elif "lottery" in flags:
        base = 0.5
    else:
        base = 1.0 if PURE_LOW <= abs(d) <= PURE_HIGH else 0.75
    return round(base * SOLD_PURITY, 3) if f["direction"] == "sold" else base


def opening(f: dict) -> float:
    if f.get("confirmed") in OPENING_CONFIRMED:
        return OPENING_CONFIRMED[f["confirmed"]]
    if "opening" in f["flags"]:
        return OPENING_PRIOR["list"]
    if "volume over OI" in f["flags"]:
        return OPENING_PRIOR["volume"]
    return OPENING_PRIOR["unknown"]


def score(f: dict) -> dict:
    """The four factors and the score, signed by the view; None for an unread flow."""
    factors = {
        "size": size_factor(f["delta_dollars"], f["premium"]),
        "conviction": conviction(f),
        "purity": purity(f),
        "opening": opening(f),
    }
    raw = 100 * factors["size"] * factors["conviction"] * factors["purity"] * factors["opening"]
    value = None if f["view"] is None else round(raw if f["view"] == "bullish" else -raw, 1)
    return {"factors": factors, "score": value}


def by_name(flows: list[dict]) -> list[dict]:
    """Per name: net delta dollars of the read flows, weighted by purity (a roll or a deep in-the-money
    trade counts less), the flow count, and the strongest score."""
    out: dict[str, dict] = {}
    for f in flows:
        n = out.setdefault(
            f["symbol"],
            {"symbol": f["symbol"], "flows": 0, "bullish": 0.0, "bearish": 0.0, "unread": 0, "top": 0.0},
        )
        n["flows"] += 1
        if f["view"] is None:
            n["unread"] += 1
            continue
        if f["delta_dollars"]:
            n[f["view"]] += f["delta_dollars"] * f["factors"]["purity"]
        if f["score"] is not None and abs(f["score"]) > abs(n["top"]):
            n["top"] = f["score"]
    for n in out.values():
        n["net"] = round(n["bullish"] - n["bearish"], 2)
        n["bullish"], n["bearish"] = round(n["bullish"], 2), round(n["bearish"], 2)
    return sorted(out.values(), key=lambda n: -abs(n["net"]))


def score_checks(flows: list[dict]) -> dict:
    """What the day's own data can check about the table: does the read agree with the site's own
    sentiment, and does the model's delta agree with the broker's? Recorded every day, so a drift
    in either shows as a number, not an impression."""
    votes = [f["site_vote"] for f in flows if f.get("site_vote")]
    compared = [f for f in flows if f.get("model_delta") is not None and f.get("delta_from") == "broker"]
    off = [f for f in compared if abs(f["model_delta"] - f["delta"]) > DELTA_CHECK]
    singles = [f for f in flows if f["kind"] != "spread"]
    return {
        "site_vote": {k: votes.count(k) for k in ("agrees", "neutral", "opposite")},
        "delta": {
            "broker": sum(1 for f in singles if f.get("delta_from") == "broker"),
            "singles": len(singles),
            "compared": len(compared),
            "off": [
                f"{f['symbol']} {f['what']}: broker {f['delta']:+.2f}, model {f['model_delta']:+.2f}"
                for f in off
            ],
        },
    }


def close_check(closes: dict[str, float], reference: dict[str, float]) -> dict:
    """The broker's closes against an independent source's (Dolt, the next morning)."""
    both = [s for s in closes if closes.get(s) and reference.get(s)]
    off = [
        f"{s}: broker {closes[s]:.2f}, dolt {reference[s]:.2f}"
        for s in both
        if abs(closes[s] / reference[s] - 1) > CLOSE_CHECK
    ]
    return {"compared": len(both), "of": len(closes), "off": off}


def derive_flow(capture: dict, market: dict) -> dict:
    """The day's derived flow document: ranked flows, unread flows, and names."""
    flows = build_flows(capture, market)
    for f in flows:
        f.update(score(f))
    ranked = sorted((f for f in flows if f["score"] is not None), key=lambda f: -abs(f["score"]))
    unread = sorted(
        (f for f in flows if f["score"] is None), key=lambda f: -(f["delta_dollars"] or f["premium"] or 0)
    )
    return {
        "version": FLOW_VERSION,
        "session": capture["session"],
        "flows": ranked,
        "unread": unread,
        "names": by_name(flows),
        "checks": score_checks(flows),
    }


def classify_change(start_oi: float | None, next_oi: float | None, size: float | None) -> str | None:
    """`opened`, `closed` or `mixed` from the change in a contract's open interest against the trade."""
    if start_oi is None or next_oi is None or not size:
        return None
    change = next_oi - start_oi
    if change >= CONFIRM_SHARE * size:
        return "opened"
    if change <= -CONFIRM_SHARE * size:
        return "closed"
    return "mixed"


def oi_published(doc: dict, next_oi: dict[str, float]) -> bool:
    """Whether the overnight open interest is out: at least one of the day's contracts has moved.
    OCC publishes a session's open interest before the next session's open (Monday's, for a
    Friday), and until then the broker still shows the starting figure; every leg unchanged reads
    as "not published", never as a day of `mixed` (2026-10-03: a Saturday check found all 40 legs
    unchanged)."""
    pairs = [
        (leg.get("start_oi"), next_oi.get(leg["symbol"]))
        for f in doc.get("flows", []) + doc.get("unread", [])
        for leg in f.get("legs") or []
    ]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    return any(a != b for a, b in pairs)


def confirm_flows(doc: dict, next_oi: dict[str, float]) -> dict:
    """Each flow's next-morning verdict from every leg's open interest, and a confirmed score beside
    the first. A flow whose legs disagree is `mixed`; one with a leg not read stays unconfirmed."""
    for f in doc["flows"] + doc["unread"]:
        verdicts = [
            classify_change(leg.get("start_oi"), next_oi.get(leg["symbol"]), leg.get("size"))
            for leg in f.get("legs") or []
        ]
        if not verdicts or any(v is None for v in verdicts):
            continue
        f["confirmed"] = verdicts[0] if len(set(verdicts)) == 1 else "mixed"
        f["next_oi"] = [next_oi.get(leg["symbol"]) for leg in f["legs"]]
        rescored = score(f)
        f["confirmed_factors"], f["confirmed_score"] = rescored["factors"], rescored["score"]
    doc["confirmed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return doc


# ------------------------------------------------------------------------------------------------
# The broker. Read-only market data, the shared login.
# ------------------------------------------------------------------------------------------------


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


async def _market(symbols: list[str], options: list[str]) -> dict:
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager
    from tastytrade.market_data import get_market_data_by_type
    from tastytrade.metrics import get_market_metrics

    session = SessionManager(CredentialStore(SHARED_SERVICE)).get_session()
    names: dict[str, dict] = {}
    contracts: dict[str, dict] = {}
    for i in range(0, len(symbols), 100):
        for m in await get_market_data_by_type(session, equities=symbols[i : i + 100]):
            close = _f(m.close) if str(getattr(m, "close_price_type", "")).lower().endswith("final") else None
            names.setdefault(m.symbol, {}).update(
                {
                    "close": close or _f(m.last_mkt) or _f(m.last),
                    "close_basis": "final" if close else "last",
                    "as_of": str(m.summary_date),
                }
            )
    for i in range(0, len(symbols), 50):
        for m in await get_market_metrics(session, symbols[i : i + 50]):
            info = names.setdefault(m.symbol, {})
            info["iv"] = _f(m.implied_volatility_30_day) / 100 if m.implied_volatility_30_day else None
            report = getattr(m.earnings, "expected_report_date", None) if m.earnings else None
            info["earnings"] = str(report) if report else None
    for i in range(0, len(options), 100):
        for m in await get_market_data_by_type(session, options=options[i : i + 100]):
            contracts[m.symbol] = {"open_interest": _f(m.open_interest), "volume": _f(m.volume)}
    return {"names": names, "contracts": contracts}


def legs_db() -> Path:
    from cherrypick.core import home

    return home.data_dir("quikoptions") / "stream-legs.db"


def request_legs(occs: list[str]) -> None:
    """Ask the streamer for these contracts: a tiny database of their streamer symbols, declared as
    a leg source, which the producer re-queries every poll (MEIC's open legs work the same way), so
    no restart. An empty list releases them."""
    import sqlite3

    from cherrypick.core import streamcache, streamrequests

    path = legs_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS legs (symbol TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM legs")
        conn.executemany(
            "INSERT OR IGNORE INTO legs VALUES (?)",
            [(sym,) for sym in (streamcache.occ_to_streamer_symbol(o) for o in occs) if sym],
        )
        conn.commit()
    finally:
        conn.close()
    streamrequests.write_request(
        "quikoptions", (), leg_sources=[streamrequests.leg_source(str(path), "SELECT symbol FROM legs")]
    )


def broker_greeks(occs: list[str], wait_s: float = GREEKS_WAIT_S) -> dict[str, float]:
    """The broker's delta per contract from the streamer's greeks, waiting up to `wait_s` for the
    day's contracts to arrive. Whatever has not arrived is simply absent: the model's delta stands."""
    import time

    from cherrypick.core import home, streamcache

    request_legs(occs)
    wanted = {streamcache.occ_to_streamer_symbol(o): o for o in occs}
    wanted.pop("", None)
    cache = home.data_dir("marketdata") / "stream_cache.db"
    deadline = time.monotonic() + wait_s
    got: dict[str, float] = {}
    while True:
        try:
            conn = streamcache.connect(cache)
            try:
                rows = streamcache.greeks_for(
                    conn, list(wanted), now_ts=time.time(), max_age_seconds=6 * 3600
                )
            finally:
                conn.close()
            got = {wanted[s]: g["delta"] for s, g in rows.items() if g.get("delta") is not None}
        except Exception:  # noqa: BLE001 - no cache, no greeks: the model's delta stands
            got = {}
        if len(got) >= len(wanted) or time.monotonic() > deadline:
            return got
        time.sleep(5)


def dolt_closes(symbols: list[str], session: str) -> dict[str, float]:
    """Each name's close on `session` from the local Dolt stocks clone (pulled each morning), the
    independent source the broker's closes are checked against. Empty when Dolt is unreachable."""
    try:
        import mysql.connector

        conn = mysql.connector.connect(
            host="127.0.0.1", port=3306, user="root", database="stocks", connection_timeout=10
        )
    except Exception:  # noqa: BLE001 - no Dolt here: no check, said so in the record
        return {}
    try:
        cur = conn.cursor()
        out = {}
        for i in range(0, len(symbols), 200):
            chunk = symbols[i : i + 200]
            marks = ", ".join(["%s"] * len(chunk))
            cur.execute(
                f"SELECT act_symbol, close FROM ohlcv WHERE date = %s AND act_symbol IN ({marks})",
                [session, *chunk],
            )
            out.update({sym: float(close) for sym, close in cur.fetchall() if close is not None})
        return out
    finally:
        conn.close()


def _ex_dividends(symbols: list[str], session: str) -> dict[str, str]:
    """The next ex-dividend date per name from the technicals store (the broker's looked stale)."""
    import sqlite3

    from cherrypick.core import home

    path = home.data_dir("technicals") / "eod.db"
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = {}
        for sym in symbols:
            row = conn.execute(
                "select min(ex_date) from dividends where symbol = ? and ex_date > ?", (sym, session)
            ).fetchone()
            if row and row[0]:
                out[sym] = row[0]
        return out
    finally:
        conn.close()


# ------------------------------------------------------------------------------------------------
# The store and the commands.
# ------------------------------------------------------------------------------------------------


def store() -> Path:
    from cherrypick.core import home

    return home.data_dir("quikoptions") / "hot-options"


def flow_path(session: str) -> Path:
    return store() / f"{session}.flow.json"


def _write(path: Path, doc: dict) -> None:
    from cherrypick.core.jsonio import write_json_atomic

    write_json_atomic(path, doc)


def _sessions() -> list[str]:
    return sorted(p.stem for p in store().glob("????-??-??.json"))


def _last_session(before: str | None = None) -> str | None:
    days = [d for d in _sessions() if before is None or d < before]
    return days[-1] if days else None


def _today() -> str:
    return datetime.now(ET).date().isoformat()


def contracts_of(capture: dict) -> list[str]:
    """Every option the flows name, outrights, sweeps and spread legs alike."""
    out = set()
    t = capture.get("tables") or {}
    for r in (t.get("outrights") or []) + (t.get("sweeps") or []):
        if r.get("expires") and r.get("strike") and r.get("cp"):
            out.add(occ_symbol(r["symbol"], r["expires"], r["strike"], r["cp"]))
    for r in t.get("spreads") or []:
        for leg in spread_legs(r.get("spread"), r.get("type")) or []:
            out.add(occ_symbol(r["symbol"], leg["expires"], leg["strike"], leg["cp"]))
    return sorted(out)


def record_outcomes(closes: dict[str, float], today: str) -> list[str]:
    """For the sessions OUTCOME_LAGS trading days back, each name's close today against its close
    then, kept in that session's flow file. Returns the sessions updated."""
    days = [d for d in _sessions() if d < today]
    updated = []
    for lag in OUTCOME_LAGS:
        if len(days) < lag:
            continue
        session = days[-lag]
        path = flow_path(session)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        outcome = doc.setdefault("outcomes", {}).setdefault(f"{lag}d", {"through": today, "returns": {}})
        for name in doc.get("names", []):
            then = (doc.get("closes") or {}).get(name["symbol"])
            now = closes.get(name["symbol"])
            if then and now:
                outcome["returns"][name["symbol"]] = round(now / then - 1, 5)
        _write(path, doc)
        updated.append(session)
    return updated


def wait_for(ready, minutes: float, poll_s: float = 30) -> bool:
    """Poll `ready()` until it holds or `minutes` pass. The schedule fires each job once a day, so a
    machine waking after the evening fires the capture, the score and the post together: each step
    waits for the one before it, bounded, rather than finding nothing and being done for the day."""
    deadline = time.monotonic() + minutes * 60
    while not ready():
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)
    return True


def cmd_score(args) -> int:
    if getattr(args, "wait", 0):
        wait_for(lambda: _last_session() == _today(), args.wait)
    session = args.session or _last_session()
    if getattr(args, "require_today", False) and session != _today():
        # Scheduled: a day with no capture has nothing to score, and re-scoring an earlier day would
        # move its numbers under a post already made.
        print(f"no capture for {_today()} to score (latest {session})")
        return 0
    if session is None:
        print("no capture to score")
        return 1
    capture = json.loads((store() / f"{session}.json").read_text(encoding="utf-8"))
    t = capture.get("tables") or {}
    symbols = sorted({r["symbol"] for name in ("outrights", "sweeps", "spreads") for r in t.get(name) or []})
    days = [d for d in _sessions() if d < session]
    earlier = {d for lag in OUTCOME_LAGS if len(days) >= lag for d in [days[-lag]]}
    for d in earlier:
        try:
            symbols += [
                n["symbol"] for n in json.loads(flow_path(d).read_text(encoding="utf-8")).get("names", [])
            ]
        except (OSError, ValueError):
            pass
    symbols = sorted(set(symbols))
    market = asyncio.run(_market(symbols, contracts_of(capture)))
    market["greeks"] = broker_greeks(contracts_of(capture)) if not args.no_greeks else {}
    for sym, ex in _ex_dividends(symbols, session).items():
        market["names"].setdefault(sym, {})["ex_dividend"] = ex
    doc = derive_flow(capture, market)
    doc["closes"] = {
        s: v.get("close")
        for s, v in market["names"].items()
        if s in {f["symbol"] for f in doc["flows"] + doc["unread"]}
    }
    doc["scored_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    _write(flow_path(session), doc)
    updated = record_outcomes({s: v.get("close") for s, v in market["names"].items()}, session)
    cmd_review(args, quiet=True)  # the Checks card's progress toward the fixed test
    print(
        f"{session}: {len(doc['flows'])} flows ranked, {len(doc['unread'])} unread"
        + (f"; outcomes for {', '.join(updated)}" if updated else "")
    )
    return 0


def cmd_confirm(args) -> int:
    session = args.session or _last_session(before=_today())
    if session is None or not flow_path(session).exists():
        print("no scored session to confirm")
        return 1
    doc = json.loads(flow_path(session).read_text(encoding="utf-8"))
    legs = sorted({leg["symbol"] for f in doc["flows"] + doc["unread"] for leg in f.get("legs") or []})
    market = asyncio.run(_market([], legs))
    next_oi = {s: c.get("open_interest") for s, c in market["contracts"].items()}
    if not oi_published(doc, next_oi):
        print(f"{session}: overnight open interest not published yet (all unchanged); nothing confirmed")
        return 0
    doc = confirm_flows(doc, next_oi)
    reference = dolt_closes(sorted(doc.get("closes") or {}), session)
    doc.setdefault("checks", {})["close"] = (
        close_check(doc.get("closes") or {}, reference)
        if reference
        else {"compared": 0, "note": "Dolt not reachable"}
    )
    _write(flow_path(session), doc)
    if session == _last_session():
        request_legs([])  # the day's contracts are no longer needed
    done = sum(1 for f in doc["flows"] + doc["unread"] if f.get("confirmed"))
    print(f"{session}: {done} of {len(doc['flows']) + len(doc['unread'])} flows confirmed")
    return 0


# ------------------------------------------------------------------------------------------------
# Verification: the hand audit against the tape, and the test fixed before the results.
# ------------------------------------------------------------------------------------------------

# The test, fixed 2026-10-03 before any outcome existed (docs/quikoptions-plan.md, Phase 6). After
# REVIEW_SESSIONS sessions with a 5-session outcome: flows scoring at least STRONG (either sign) must
# move their stock their way more often than all read flows do, and the score must beat both simpler
# reads of the same days — the site's own sentiment weighted by premium, and our delta dollars alone.
REVIEW_SESSIONS = 40
STRONG = 30
REVIEW_LAG = "5d"


def audit_path() -> Path:
    from cherrypick.core import home

    return home.data_dir("quikoptions") / "audit.jsonl"


def audit_entry(doc: dict, rank: int, tape: str, note: str = "") -> dict:
    """One hand check: flow `rank` of a session against what Time & Sales showed (bought, sold, or
    middle). Pure."""
    f = doc["flows"][rank - 1]
    ours = f.get("direction") or "unread"
    return {
        "session": doc["session"],
        "rank": rank,
        "symbol": f["symbol"],
        "what": f.get("what"),
        "ours": ours,
        "tape": tape,
        "agrees": ours == tape,
        "note": note,
    }


def audit_summary(records: list[dict]) -> dict:
    """Agreement of our read with the tape, over every hand check so far."""
    checked = [r for r in records if r.get("tape") in ("bought", "sold", "middle")]
    agree = sum(1 for r in checked if r["agrees"])
    return {
        "checked": len(checked),
        "agree": agree,
        "rate": round(agree / len(checked), 3) if checked else None,
        "disagree": [
            f"{r['session']} #{r['rank']} {r['symbol']} {r['what']}: ours {r['ours']}, tape {r['tape']}"
            for r in checked
            if not r["agrees"]
        ],
    }


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def review(days: list[tuple[dict, dict]]) -> dict:
    """The fixed test over (flow document, capture) pairs that carry a REVIEW_LAG outcome. Hits are a
    view matching the sign of the stock's move; a flat move counts as neither. Pure."""
    groups = {"strong": [0, 0], "all_read": [0, 0], "net_delta_dollars": [0, 0], "site_premium": [0, 0]}

    def tally(group: str, view: int, move: float | None) -> None:
        if move is None or view == 0 or move == 0:
            return
        groups[group][1] += 1
        groups[group][0] += int(view == _sign(move))

    usable = 0
    for doc, capture in days:
        returns = ((doc.get("outcomes") or {}).get(REVIEW_LAG) or {}).get("returns") or {}
        if not returns:
            continue
        usable += 1
        for f in doc.get("flows") or []:
            s = f.get("confirmed_score") if f.get("confirmed_score") is not None else f.get("score")
            if s is None:
                continue
            move = returns.get(f["symbol"])
            tally("all_read", _sign(s), move)
            if abs(s) >= STRONG:
                tally("strong", _sign(s), move)
        for n in doc.get("names") or []:
            tally("net_delta_dollars", _sign(n.get("net") or 0), returns.get(n["symbol"]))
        site: dict[str, float] = {}
        for table in ("outrights", "sweeps"):
            for r in (capture.get("tables") or {}).get(table) or []:
                word = ((r.get("side") or {}).get("sentiment") or "").lower()
                weight = r.get("premium") or (r.get("price") or 0) * (r.get("size") or 0) * 100
                site[r["symbol"]] = site.get(r["symbol"], 0.0) + (
                    weight if word == "bullish" else -weight if word == "bearish" else 0
                )
        for sym, net in site.items():
            tally("site_premium", _sign(net), returns.get(sym))
    rates = {g: (round(h / n, 3) if n else None, n) for g, (h, n) in groups.items()}
    ready = usable >= REVIEW_SESSIONS
    passed = None
    if ready and all(rates[g][0] is not None for g in rates):
        passed = rates["strong"][0] > rates["all_read"][0] and rates["strong"][0] > max(
            rates["net_delta_dollars"][0], rates["site_premium"][0]
        )
    return {
        "sessions": usable,
        "needed": REVIEW_SESSIONS,
        "ready": ready,
        "hit_rates": rates,
        "passed": passed,
    }


def cmd_audit(args) -> int:
    if args.report:
        try:
            records = [
                json.loads(line)
                for line in audit_path().read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except OSError:
            records = []
        summary = audit_summary(records)
        rate = "—" if summary["rate"] is None else f"{summary['rate']:.0%}"
        print(
            f"{summary['checked']} hand checks, our read agreed with the tape on {summary['agree']} ({rate})"
        )
        for line in summary["disagree"]:
            print("  " + line)
        return 0
    if args.rank is None or args.tape is None:
        print("audit needs --rank N and --tape bought|sold|middle (or --report)")
        return 2
    session = args.session or _last_session()
    doc = json.loads(flow_path(session).read_text(encoding="utf-8"))
    entry = {
        **audit_entry(doc, args.rank, args.tape, args.note or ""),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    audit_path().parent.mkdir(parents=True, exist_ok=True)
    with audit_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    records = [
        json.loads(line) for line in audit_path().read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    _write(audit_path().with_name("audit-summary.json"), audit_summary(records))
    print(
        f"recorded: {entry['symbol']} {entry['what']} — ours {entry['ours']}, tape {entry['tape']}"
        + ("" if entry["agrees"] else "  (disagrees)")
    )
    return 0


def cmd_review(_args, quiet: bool = False) -> int:
    days = []
    for session in _sessions():
        try:
            doc = json.loads(flow_path(session).read_text(encoding="utf-8"))
            capture = json.loads((store() / f"{session}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        days.append((doc, capture))
    out = review(days)
    _write(store().parent / "review.json", {**out, "at": datetime.now(UTC).isoformat(timespec="seconds")})
    if quiet:
        return 0
    print(
        f"{out['sessions']} of {out['needed']} sessions with a {REVIEW_LAG} outcome"
        + ("" if out["ready"] else " — too few to judge yet")
    )
    for group, (rate, n) in out["hit_rates"].items():
        print(f"  {group:<18} {'—' if rate is None else f'{rate:.0%}':>5}  over {n}")
    if out["ready"]:
        print("passed" if out["passed"] else "did not pass: the score does not beat the simpler reads")
    return 0


def _money(v) -> str:
    if v is None:
        return "—"
    a = abs(v)
    return f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K"


def cmd_show(args) -> int:
    session = args.session or _last_session()
    doc = json.loads(flow_path(session).read_text(encoding="utf-8"))
    for i, f in enumerate(doc["flows"][: args.top], 1):
        fac = f["factors"]
        conf = f" [{f['confirmed']} → {f['confirmed_score']:+.0f}]" if f.get("confirmed") else ""
        head = f"{i:>2} {f['score']:+5.0f}{conf}  {f['symbol']:<5} {f['kind']:<8} {f['what']:<26}"
        read = f"{f['direction'] or '?':<6} {f['view']:<7} Δ$ {_money(f['delta_dollars']):>7}"
        why = f"S{fac['size']:.2f} C{fac['conviction']:.2f} P{fac['purity']:.2f} O{fac['opening']:.2f}"
        print(f"{head} {read}  {why}  {', '.join(f['flags'])}")
    unread = [
        f"{f['symbol']} {f['what']} ({_money(f['delta_dollars'] or f['premium'])})" for f in doc["unread"]
    ]
    print("unread:", ", ".join(unread) or "none")
    for n in doc["names"][:8]:
        net = ("+" if n["net"] >= 0 else "−") + _money(n["net"])
        print(f"  {n['symbol']:<5} net {net}  ({n['flows']} flows, top {n['top']:+.0f})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("score", cmd_score), ("confirm", cmd_confirm), ("show", cmd_show)):
        p = sub.add_parser(name)
        p.add_argument("--session", default=None)
        if name == "show":
            p.add_argument("--top", type=int, default=15)
        if name == "score":
            p.add_argument(
                "--no-greeks", action="store_true", help="skip the streamer's greeks (the model's delta)"
            )
            p.add_argument(
                "--require-today", action="store_true", help="score only today's capture (the schedule's run)"
            )
            p.add_argument("--wait", type=float, default=0, help="wait up to MIN minutes for today's capture")
        p.set_defaults(fn=fn)
    au = sub.add_parser("audit", help="record a hand check of a flow against Time & Sales, or --report")
    au.add_argument("--session", default=None)
    au.add_argument("--rank", type=int, default=None, help="the flow's rank on the derived flow tab")
    au.add_argument("--tape", choices=["bought", "sold", "middle"], default=None)
    au.add_argument("--note", default=None)
    au.add_argument("--report", action="store_true")
    au.set_defaults(fn=cmd_audit)
    sub.add_parser("review", help="the fixed test of the score against the outcome record").set_defaults(
        fn=cmd_review
    )
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
