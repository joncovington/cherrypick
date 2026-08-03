"""The screener compute flow: one batched metrics call, a zero-broker-call pre-filter on IV rank and
liquidity, chains fetched only for pre-filter survivors (nearest 30-45 DTE expiration, preferring a
standard monthly), a quote snapshot for a +/-15 strike window around spot, candidate build (0 further
broker calls), and a weighted composite rank.

Spot is read from `candle_service`'s already-cached daily bars rather than a fresh equity quote --
one more reuse of an existing TTL-cached path instead of a new broker round trip.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta

from ..analytics import strategies as _strategies
from ..analytics.payoff import Leg
from ..analytics.pop import pop as _pop
from . import candle_service, chain_service, metrics_service
from .session import BrokerSession

_STRIKE_WINDOW = 15
_DEFAULT_IV = 0.30  # fallback when metrics has no iv_30d for a symbol -- a mild, not-zero estimate


def _is_monthly(expiration: date) -> bool:
    """The 3rd Friday of the month -- the standard monthly options expiration."""
    return expiration.weekday() == 4 and 15 <= expiration.day <= 21


def _pick_expiration(
    expirations: dict[str, list], today: date, dte_min: int, dte_max: int
) -> tuple[date, list[dict], int] | None:
    candidates = []
    for iso, options in expirations.items():
        exp = date.fromisoformat(iso)
        dte = (exp - today).days
        if dte_min <= dte <= dte_max:
            candidates.append((exp, options, dte))
    if not candidates:
        return None
    monthly = [c for c in candidates if _is_monthly(c[0])]
    pool = monthly or candidates
    mid_target = today + timedelta(days=(dte_min + dte_max) / 2)
    pool.sort(key=lambda c: abs((c[0] - mid_target).days))
    return pool[0]


def _iv_rank_frac(info: dict | None) -> float | None:
    """`implied_volatility_index_rank` arrives as a 0..1 decimal fraction string from the SDK
    (verified against a real account -- despite the "rank" name, it is not a 0..100 integer);
    normalized once here so config's 0..100-scale `min_iv_rank` and the UI's percentage display
    agree with what the broker actually returns."""
    if info is None:
        return None
    raw = info.get("iv_rank")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _passes_prefilter(info: dict | None, cfg: dict) -> bool:
    screener_cfg = cfg.get("screener", {})
    iv_frac = _iv_rank_frac(info)
    if iv_frac is None or iv_frac * 100 < screener_cfg.get("min_iv_rank", 25):
        return False
    liquidity = info.get("liquidity_rating") if info else None
    if liquidity is None or liquidity < screener_cfg.get("min_liquidity_rank", 3):
        return False
    return True


async def _strikes_with_quotes(
    conn: sqlite3.Connection, session: BrokerSession, options: list[dict], spot: float
) -> list[dict]:
    """A +/-`_STRIKE_WINDOW`-strike window around spot, quotes attached -- the one quote-fetching
    call per symbol, sized to what a single strategy actually needs rather than the whole chain."""
    strikes = sorted({o["strike"] for o in options})
    below = [s for s in strikes if s <= spot][-_STRIKE_WINDOW:]
    above = [s for s in strikes if s > spot][:_STRIKE_WINDOW]
    window = set(below) | set(above)
    windowed = [dict(o) for o in options if o["strike"] in window]
    quotes = await chain_service.get_quotes(conn, session, [o["symbol"] for o in windowed])
    for option in windowed:
        option["quote"] = quotes.get(option["symbol"])
    return windowed


def _build_candidate(
    strategy: str, windowed: list[dict], spot: float, expected_move: float, cfg: dict, expiration, dte
):
    builder = _strategies.STRATEGIES[strategy]
    if strategy == "covered_call":
        return builder(windowed, spot, expected_move, expiration, dte, spot)
    if strategy in ("put_credit_spread", "call_credit_spread"):
        wing_width_pct = cfg.get("screener", {}).get("wing_width_pct", 0.05)
        return builder(windowed, spot, expected_move, wing_width_pct, expiration, dte)
    return builder(windowed, spot, expected_move, expiration, dte)


async def run_screener(
    conn: sqlite3.Connection,
    session: BrokerSession,
    cfg: dict,
    watchlist_symbols: list[str],
    strategy: str,
    *,
    now: float | None = None,
) -> dict:
    if strategy not in _strategies.STRATEGIES:
        return {"ok": False, "error": f"unknown strategy: {strategy!r}"}

    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now).date()
    screener_cfg = cfg.get("screener", {})
    dte_min, dte_max = screener_cfg.get("target_dte_min", 30), screener_cfg.get("target_dte_max", 45)
    short_delta = screener_cfg.get("short_delta", 0.30)
    metrics_ttl = cfg.get("refresh", {}).get("metrics_ttl_seconds", 900)

    metrics = await metrics_service.get_metrics(conn, session, watchlist_symbols, metrics_ttl, now=now)
    try:
        risk_free_rate = await metrics_service.get_risk_free_rate(conn, session, now=now)
    except Exception:
        risk_free_rate = 0.0

    survivors = [s.upper() for s in watchlist_symbols if _passes_prefilter(metrics.get(s.upper()), cfg)]

    candidates: list[dict] = []
    skipped: list[dict] = []
    for symbol in survivors:
        info = metrics.get(symbol, {})

        candles = await candle_service.get_candles(conn, session, cfg, symbol, now=now)
        if not candles["bars"]:
            skipped.append({"symbol": symbol, "reason": "no candle history"})
            continue
        spot = candles["bars"][-1]["c"]

        expirations_payload = await chain_service.get_expirations(conn, session, cfg, symbol)
        picked = _pick_expiration(expirations_payload["expirations"], today, dte_min, dte_max)
        if picked is None:
            skipped.append({"symbol": symbol, "reason": "no expiration in the target DTE window"})
            continue
        expiration, options, dte = picked

        iv_frac = _iv_rank_frac(info) or 0.0
        sigma = float(info.get("iv_30d") or 0.0) or _DEFAULT_IV
        t = dte / 365.0
        expected_move = spot * sigma * (t**0.5)

        windowed = await _strikes_with_quotes(conn, session, options, spot)
        candidate = _build_candidate(strategy, windowed, spot, expected_move, cfg, expiration, dte)
        if candidate is None:
            skipped.append({"symbol": symbol, "reason": "could not price a candidate"})
            continue

        legs = [
            Leg(kind=leg["kind"], quantity=leg["quantity"], price=leg["price"], strike=leg["strike"])
            for leg in candidate["legs"]
        ]
        candidate_pop = _pop(legs, spot, sigma, t, risk_free_rate)
        return_on_risk = (candidate["credit"] / candidate["max_risk"]) if candidate.get("max_risk") else None

        row = {
            "symbol": symbol,
            "spot": spot,
            "iv_rank": iv_frac,
            "liquidity_rating": info.get("liquidity_rating"),
            "skew_edge": _strategies.directional_edge(windowed, spot, expected_move),
            **candidate,
            "pop": candidate_pop,
            "pop_heuristic": max(0.0, 1 - 2 * short_delta),
            "return_on_risk": return_on_risk,
        }
        row["composite_score"] = _strategies.composite_score(
            return_on_risk or 0.0, candidate_pop, iv_frac, info.get("liquidity_rating")
        )
        candidates.append(row)

    candidates.sort(key=lambda r: r["composite_score"], reverse=True)
    return {
        "ok": True,
        "as_of": now,
        "strategy": strategy,
        "candidates": candidates,
        "skipped": skipped,
    }
