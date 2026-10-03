"""Derived flow: one scored row per order in a QuikOptions capture, from the site's tables and the broker.

**Why this exists.** The site says where each fill sat (bid, ask, middle) and calls it bullish or
bearish. That is a lean, not a fact: a put bought on the ask may close a short, a deep in-the-money
trade is mostly stock replacement, two prints at one millisecond may be one hedged order, and premium
overstates in-the-money trades. This script turns the day's lists into **derived flows** — one row
per order — and scores each on four factors the suite can check (docs/quikoptions-plan.md, Phase 6).

A derived flow is an outright, a sweep or a spread from the capture, with prints sharing a symbol and
a timestamp grouped (they are one order) and spreads printed together grouped (a roll). Each gets:

- **read**: bought / sold from where the fill sat (edge at or past +/-0.5), or the site's own sign for
  a spread; `unread` when the fill is too near the middle. The view (bullish / bearish) combines that
  with call or put, or a spread's signed delta. Unread flows are listed, never ranked.
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

**Three moments.** `score` runs after the capture (about 16:45 ET): it reads each name's official
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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
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
DISAGREEMENT = 0.5  # conviction multiplier when the site's fill label and its edge disagree
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


def _days(session: str, expires: str | None) -> int | None:
    try:
        return (date.fromisoformat(expires) - date.fromisoformat(session)).days
    except (TypeError, ValueError):
        return None


def _read(edge: float | None) -> str | None:
    if edge is None:
        return None
    return "bought" if edge >= READ_EDGE else "sold" if edge <= -READ_EDGE else None


def _label_side(fill: str | None) -> str | None:
    f = (fill or "").lower()
    return "bought" if "ask" in f else "sold" if "bid" in f else "middle" if "mid" in f else None


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
    delta, how = option_delta(
        spot, row.get("strike"), days or 0, row.get("price"), cp, (names.get(sym) or {}).get("iv")
    )
    key = (
        occ_symbol(sym, row["expires"], row["strike"], cp)
        if row.get("expires") and row.get("strike") and cp
        else None
    )
    contract = (market.get("contracts") or {}).get(key) or {}
    premium = row.get("premium")
    if premium is None and row.get("price") is not None and row.get("size") is not None:
        premium = row["price"] * row["size"] * 100
    label = _label_side(side.get("fill"))
    return {
        "kind": "sweep" if table == "sweeps" else "outright",
        "symbol": sym,
        "name": row.get("name"),
        "what": row.get("expires")
        and f"{date.fromisoformat(row['expires']):%d %b %y} {row['strike']:g}{'C' if cp == 'call' else 'P'}",
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
        "label_disagrees": bool(direction and label and label != direction),
        "direction": direction,
        "view": view,
        "delta": delta,
        "delta_from": how,
        "spot": spot,
        "days": days,
        "start_oi": contract.get("open_interest"),
        "day_volume": contract.get("volume"),
        "flags": [],
    }


def _spread(row: dict, session: str) -> dict:
    price, delta = row.get("price"), row.get("delta")
    direction = None
    if price and delta and (price > 0) == (delta > 0):
        direction = "bought" if price > 0 else "sold"
    view = (
        "bullish"
        if delta and delta > 0 and direction
        else "bearish"
        if delta and delta < 0 and direction
        else None
    )
    legs = spread_legs(row.get("spread"), row.get("type")) or []
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
        "what": row.get("spread"),
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
        "premium": abs(row["premium"]) if row.get("premium") is not None else None,
        "fill": None,
        "edge": None,
        "label_disagrees": False,
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
        if f["label_disagrees"]:
            flags.append("label disagrees")

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
    if f["label_disagrees"]:
        c *= DISAGREEMENT
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


def cmd_score(args) -> int:
    session = args.session or _last_session()
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
    doc = confirm_flows(doc, next_oi)
    _write(flow_path(session), doc)
    done = sum(1 for f in doc["flows"] + doc["unread"] if f.get("confirmed"))
    print(f"{session}: {done} of {len(doc['flows']) + len(doc['unread'])} flows confirmed")
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
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
