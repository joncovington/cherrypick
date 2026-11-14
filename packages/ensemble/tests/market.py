"""Invented markets for the engine's tests: Black-Scholes quotes around a made-up spot, a flat-ish trail,
no store, no network."""

from __future__ import annotations

import math

from cherrypick.core import bs

from cherrypick.ensemble import snapshot
from cherrypick.ensemble.events import EventCalendar

ALL_TIME = [("2000-01-01", "2100-01-01")]
CALENDAR = EventCalendar(data={"CPI": [], "Sentiment": []}, covered={"CPI": ALL_TIME, "Sentiment": ALL_TIME})


def rows(spot, years, vol, *, step=5.0, width=300.0, half_spread=0.10, oi_call=1000, oi_put=1000):
    out = []
    k = round((spot - width) / step) * step
    while k <= spot + width:
        for right in ("C", "P"):
            p = bs.price(spot, k, years, vol, right, 0.0)
            d = bs.delta(spot, k, years, vol, right, 0.0)
            d1 = (math.log(spot / k) + vol * vol / 2 * years) / (vol * math.sqrt(years))
            gamma = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) / (spot * vol * math.sqrt(years))
            out.append(
                {
                    "strike": float(k),
                    "right": right,
                    "bid": round(max(p - half_spread, 0.0), 2),
                    "ask": round(p + half_spread, 2),
                    "delta": d,
                    "gamma": gamma,
                    "iv": vol,
                    "oi": oi_call if right == "C" else oi_put,
                }
            )
        k += step
    return out


def trail(start=570, end=600, base=6000.0, wiggle=0.0):
    """One-minute spot, flat at `base` plus a small deterministic wiggle."""
    return [(m, base + wiggle * math.sin(m)) for m in range(start, end + 1)]


def spx_snapshot(
    *,
    session="2026-11-16",
    minute=600,
    spot=6000.0,
    prev_close=6000.0,
    day_open=6000.0,
    vol=0.20,
    expiries=None,
    closes=None,
    vix=16.0,
    vix_prev_close=16.0,
    tr=None,
    entries=None,
    open_positions=None,
    symbol="SPX",
    half_spread=0.10,
    step=5.0,
    width=300.0,
    oi_call=1000,
    oi_put=1000,
    gex_strike_cap=None,
):
    """`expiries` maps an expiry to its years to run (default today's, 6 hours out)."""
    expiries = expiries or {session: 6.0 / (24 * 365)}
    quotes = {
        e: rows(spot, y, vol, step=step, width=width, half_spread=half_spread, oi_call=oi_call, oi_put=oi_put)
        for e, y in expiries.items()
    }
    return snapshot.build(
        session=session,
        minute=minute,
        symbol=symbol,
        spot=spot,
        prev_close=prev_close,
        day_open=day_open,
        quotes=quotes,
        trail=tr if tr is not None else trail(end=minute, base=spot),
        closes=closes if closes is not None else [5800.0 + i for i in range(60)],
        vix=vix,
        vix_prev_close=vix_prev_close,
        events=CALENDAR,
        gex_strike_cap=gex_strike_cap,
        entries=entries,
        open_positions=open_positions,
    )
