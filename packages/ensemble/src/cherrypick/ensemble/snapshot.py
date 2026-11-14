"""Building a `Snapshot`: the one constructor both drivers call. The backtest hands it rows read from the
vendor store, the paper loop rows read from the stream cache; what they hand over has the same shape
(quote dicts), so the same function makes the same snapshot from the same market.
"""

from __future__ import annotations

from cherrypick.core import gex as core_gex

from .model import Chain, OpenPosition, Snapshot


def gex_series(rows: list[dict], expiry: str, spot: float) -> list[dict]:
    """The per-strike series of `core.gex.compute_gex_profile` from quote rows carrying `gamma` and `oi`.
    Empty when no strike has data."""
    entries, greeks, oi = [], {}, {}
    for r in rows:
        sym = f"{expiry}{r['right']}{r['strike']}"
        entries.append({"strike_price": r["strike"], "streamer_symbol": sym, "option_type": r["right"]})
        greeks[sym] = {"gamma": r.get("gamma") or 0.0, "iv": r.get("iv") or 0.0}
        oi[sym] = r.get("oi") or 0
    res = core_gex.compute_gex_profile(entries, greeks, oi, {}, spot)
    return res["series"] if res.get("ok") else []


def build(
    *,
    session: str,
    minute: int,
    symbol: str,
    spot: float,
    prev_close: float,
    day_open: float,
    quotes: dict[str, list[dict]],
    trail: list[tuple[int, float]],
    closes: list[float],
    vix: float | None = None,
    vix_prev_close: float | None = None,
    events=None,
    entries: dict[str, list[str]] | None = None,
    open_positions: list[OpenPosition] | None = None,
    close_minute: int = 960,
    with_gex: bool = True,
    gex_strike_cap: int | None = None,
) -> Snapshot:
    """`quotes` maps an expiry (ISO) to its quote rows. GEX is built per expiry unless `with_gex` is off
    (a driver whose arms read none skips the cost)."""
    chains = {e: Chain(e, rows) for e, rows in quotes.items()}
    gex = {e: gex_series(rows, e, spot) for e, rows in quotes.items()} if with_gex else {}
    return Snapshot(
        session=session,
        minute=minute,
        symbol=symbol,
        spot=spot,
        prev_close=prev_close,
        day_open=day_open,
        chains=chains,
        trail=list(trail),
        closes=list(closes),
        vix=vix,
        vix_prev_close=vix_prev_close,
        gex=gex,
        events=events,
        entries=dict(entries or {}),
        open_positions=list(open_positions or []),
        listed_expiries=sorted(quotes),
        close_minute=close_minute,
        gex_strike_cap=gex_strike_cap,
    )
