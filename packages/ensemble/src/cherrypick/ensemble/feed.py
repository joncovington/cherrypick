"""Building a `Snapshot` from the stream cache and the loop's own ledger, for one symbol on one tick.

This is the paper side of the one constructor both drivers call (`snapshot.build`): the cache's rows have
the shape the backtest's store rows have, so a decision made here is the decision the backtest would make
on the same market. Nothing here reaches the network, and nothing writes the cache.

What each arm reads decides what is fetched (`reads`): an arm that places a strike by delta needs the
chain's Greeks, one with a GEX gate or strike needs gamma and open interest, one that reads the opening
range or the intraday RSI or stochastic needs the day's spot trail from the open, one that reads the
standard-deviation move needs the VIX and its previous close. A symbol with no such arm is not asked for them.

A refusal is a dict with a `reason` (and a `detail`), never an exception and never a partial snapshot: the
caller records it against every arm that was due.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from cherrypick.core import calendar as _cal

from . import db, provider
from . import indicators as ind
from .model import Chain, OpenPosition, Snapshot, hhmm
from .snapshot import build

OPEN_MINUTE = ind.OPEN_MINUTE
VIX_GATES = ("sd_change", "vix_level", "vix_change_pct")
TRAIL_GATES = ("rsi", "stoch")


def reads(arm: dict) -> dict:
    """What `arm`'s rules read beyond a quote and the spot."""
    gates = [g["gate"] for g in arm.get("gates", [])]
    rules = [leg["strike"]["rule"] for leg in arm["legs"]]
    gex = any(g.startswith("gex_") for g in gates) or "max_abs_gex" in rules
    mas = [g["n"] for g in arm.get("gates", []) if g["gate"] == "above_ma"]
    return {
        "trail": arm["entry"]["kind"] == "orb" or any(g in TRAIL_GATES for g in gates),
        "gex": gex,
        "greeks": gex or "delta" in rules,
        "vix": any(g in VIX_GATES for g in gates),
        "closes": max(mas) if mas else 1,
        "events": bool((arm.get("skip_events") or {}).get("labels")),
    }


def entry_window(arm: dict) -> tuple[int, int]:
    """(first, last) minute at which `arm` may enter: a time entry's window, an opening-range entry's
    range end to its latest minute."""
    e = arm["entry"]
    if e["kind"] == "time":
        at = hhmm(e["at"])
        return at, at + e.get("window", 5) - 1
    return OPEN_MINUTE + e["minutes"], hhmm(e["latest"])


def target_dates(arm: dict, today: date) -> list[date]:
    """The dates `arm`'s expiry rule can name today, for a cache that has not listed the symbol's
    expirations yet (the stream request still has to ask for them)."""
    rule = arm["expiry"]
    if rule["kind"] == "market_days":
        centre = _cal.nth_trading_day(today, rule["days"])
    else:
        centre = today + timedelta(days=rule["days"])
    tol = int(rule.get("tolerance", 0))
    return [centre + timedelta(days=d) for d in range(-tol, tol + 1) if centre + timedelta(days=d) >= today]


def expiries_for(arm: dict, today: date, listed: list[str]) -> list[str]:
    """The expiry `arm` names today among the listed ones; with none listed, every date its rule allows."""
    from .engine import resolve_expiry

    if not listed:
        return [d.isoformat() for d in target_dates(arm, today)]
    skeleton = Snapshot(
        session=today.isoformat(),
        minute=0,
        symbol=arm["symbol"],
        spot=0.0,
        prev_close=0.0,
        day_open=0.0,
        chains={e: Chain(e, []) for e in listed},
        trail=[],
        closes=[],
    )
    e = resolve_expiry(arm, skeleton)
    return [e] if e else []


def trail_complete(trail: list[tuple[int, float]], grace: int) -> bool:
    """The record began within `grace` minutes of the open (a loop started at noon cannot give the
    opening range or the morning's bars)."""
    return bool(trail) and trail[0][0] <= OPEN_MINUTE + grace


def market(cache_path: str, conn, symbol: str, when: datetime, params: dict, *, vix: bool) -> dict:
    """The inputs every snapshot for `symbol` shares, or {"refusal", "detail"}."""
    today, minute = when.date(), when.hour * 60 + when.minute
    spot = provider.read_spot(cache_path, symbol, max_age_seconds=float(params["spot_max_age_seconds"]))
    if spot is None:
        return {"refusal": "no_spot", "detail": symbol}
    bars = provider.daily_bars(cache_path, symbol, today)
    prior = _cal.previous_trading_day(today).isoformat()
    if not bars or bars[-1]["session"] != prior:
        return {"refusal": "no_prev_close", "detail": prior}
    trail = db.trail(conn, symbol, today.isoformat(), through=minute)
    out = {
        "spot": spot,
        "prev_close": float(bars[-1]["close"]),
        "closes": [float(b["close"]) for b in bars],
        "trail": trail,
        "trail_complete": trail_complete(trail, int(params["trail_grace_minutes"])),
        "day_open": provider.day_open(cache_path, symbol, today) or (trail[0][1] if trail else spot),
        "vix": None,
        "vix_prev_close": None,
    }
    if vix:
        out["vix"] = provider.read_spot(
            cache_path, "VIX", max_age_seconds=float(params["spot_max_age_seconds"])
        )
        vbars = provider.daily_bars(cache_path, "VIX", today)
        if vbars and vbars[-1]["session"] == prior:
            out["vix_prev_close"] = float(vbars[-1]["close"])
    return out


def snapshot_for(
    cache_path: str,
    conn,
    spec: dict,
    symbol: str,
    when: datetime,
    params: dict,
    arms: list[dict],
    *,
    events,
    chains: bool = True,
) -> tuple[Snapshot | None, dict]:
    """(snapshot, info) for `symbol` at `when`, built for `arms` (the ones due). With `chains` off only the
    market inputs are read (the cheap check of whether any arm is due). info carries `refusal`/`detail`
    when there is no snapshot, and the quote stats and the expiries read otherwise."""
    today = when.date()
    minute = when.hour * 60 + when.minute
    needs = [reads(a) for a in arms]
    m = market(cache_path, conn, symbol, when, params, vix=any(n["vix"] for n in needs))
    if "refusal" in m:
        return None, m
    quotes: dict[str, list[dict]] = {}
    info: dict = {
        "spot": m["spot"],
        "trail_complete": m["trail_complete"],
        "expiries": [],
        "fresh": 0,
        "rejected": 0,
    }
    if chains:
        listed = provider.listed_expirations(cache_path, symbol)
        wanted = sorted({e for a in arms for e in expiries_for(a, today, listed)})
        greeks = any(n["greeks"] for n in needs)
        for e in wanted:
            got = provider.chain_rows(
                cache_path,
                symbol,
                e,
                root=spec["root"],
                max_age_seconds=float(params["max_quote_age_seconds"]),
                greeks=greeks,
            )
            stats = got.get("quote_stats") or {}
            info["fresh"] += stats.get("fresh", 0)
            info["rejected"] += stats.get("rejected", 0)
            if got["ok"]:
                quotes[e] = got["rows"]
                info["expiries"].append(e)
    snap = build(
        session=today.isoformat(),
        minute=minute,
        symbol=symbol,
        spot=m["spot"],
        prev_close=m["prev_close"],
        day_open=m["day_open"],
        quotes=quotes,
        trail=m["trail"],
        closes=m["closes"],
        vix=m["vix"],
        vix_prev_close=m["vix_prev_close"],
        events=events,
        entries=db.entry_sessions(conn),
        open_positions=open_positions(conn, today),
        close_minute=_cal_close_minute(today),
        with_gex=any(n["gex"] for n in needs),
    )
    return snap, info


def open_positions(conn, today: date) -> list[OpenPosition]:
    """What the engine counts as open: a position expiring today stops counting toward an arm's limit at
    that day's open, as the backtest counts it (item 75's declared reading)."""
    return [
        OpenPosition(p["arm"], p["entry_session"], p["expiration"])
        for p in db.open_positions(conn)
        if p["expiration"] > today.isoformat()
    ]


def _cal_close_minute(today: date) -> int:
    h, m = _cal.session_close_hhmm(today).split(":")
    return int(h) * 60 + int(m)


def position_snapshot(pos: dict, leg_rows: list[dict], spot: float, when: datetime) -> Snapshot:
    """The snapshot `engine.manage` and `close_position` read for an open position: its own legs' quotes,
    the spot, and the clock. No gates run on it, so nothing else is read."""
    today = when.date()
    return build(
        session=today.isoformat(),
        minute=when.hour * 60 + when.minute,
        symbol=pos["symbol"],
        spot=spot,
        prev_close=spot,
        day_open=spot,
        quotes={pos["expiration"]: leg_rows},
        trail=[],
        closes=[],
        close_minute=_cal_close_minute(today),
        with_gex=False,
    )
