"""``GET /api/payoff`` -- pure leg-list -> payoff computation. The only broker-shaped call anywhere on
this path is `metrics_service.get_risk_free_rate` (cached daily), needed for POP's Black-Scholes
drift term; if it's unavailable (no credentials, a hiccup) POP/expected-move are simply omitted from
the response rather than failing the whole computation, since the payoff curve itself needs nothing
from the broker at all.
"""

from __future__ import annotations

import json
from datetime import date as _date

from fastapi import APIRouter, HTTPException, Query, Request

from ..analytics import describe as _describe
from ..analytics import payoff as _payoff
from ..analytics import pop as _pop
from ..analytics import trend as _trend
from ..services import cache as _cache_mod
from ..services import metrics_service

router = APIRouter()


def _parse_legs(raw: str) -> tuple[list[_payoff.Leg], list[dict]]:
    """Parsed `Leg` objects plus the raw dicts (which may carry per-leg bid/ask for the combo
    spread check -- fields `Leg` itself has no reason to hold)."""
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"legs is not valid JSON: {exc}") from exc
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "legs must be a non-empty JSON array")

    legs = []
    for item in items:
        try:
            legs.append(
                _payoff.Leg(
                    kind=item["kind"],
                    quantity=int(item["quantity"]),
                    price=float(item["price"]),
                    strike=(float(item["strike"]) if item.get("strike") is not None else None),
                    delta=item.get("delta"),
                    gamma=item.get("gamma"),
                    theta=item.get("theta"),
                    vega=item.get("vega"),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, f"invalid leg {item!r}: {exc}") from exc
    return legs, items


def _trend_1m_from_cache(app, symbol: str) -> str | None:
    """The provisional 1M trend label from already-cached candles -- a cache-only read, so a
    symbol with no candle history (or a fresh install without SPX cached) degrades to None/warn
    rather than triggering a broker fetch from inside the payoff route."""
    try:
        bars = _cache_mod.read_candles(app.state.cache_db, symbol, "1d")
    except Exception:
        return None
    if not bars:
        return None
    closes = [b["c"] for b in bars]
    return _trend.price_ma_count(closes, *_trend.DEFAULT_PARAMS["price_ma_count"]["1m"])


async def _earnings_inside(app, symbol: str, exp_date) -> bool | None:
    """Whether an earnings report lands inside the expiration; None (-> warn) when unknowable."""
    if exp_date is None:
        return None
    try:
        metrics_ttl = app.state.cfg.get("refresh", {}).get("metrics_ttl_seconds", 900)
        metrics = await metrics_service.get_metrics(
            app.state.cache_db, app.state.broker_session, [symbol], metrics_ttl
        )
    except Exception:
        return None
    earnings = (metrics.get(symbol) or {}).get("earnings") or {}
    raw = earnings.get("expected_report_date")
    if not raw:
        return None
    try:
        report = _date.fromisoformat(str(raw))
    except ValueError:
        return None
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).date() <= report <= exp_date


@router.get("/api/payoff")
async def get_payoff(
    request: Request,
    legs: str = Query(..., description="JSON array of leg objects"),
    spot: float = Query(...),
    dte: float | None = Query(None, description="days to expiration, for POP/expected move"),
    iv: float | None = Query(None, description="implied volatility (decimal), for POP/expected move"),
    symbol: str | None = Query(None, description="underlying symbol, for strategy text"),
    expiration: str | None = Query(None, description="ISO expiration date, for strategy text"),
) -> dict:
    parsed, raw_items = _parse_legs(legs)
    result = {
        "ok": True,
        "curve": _payoff.payoff_curve(parsed),
        "breakevens": _payoff.breakevens(parsed),
        "max_profit": _payoff.max_profit(parsed),
        "max_loss": _payoff.max_loss(parsed),
        "net_greeks": _payoff.net_greeks(parsed),
        "pop": None,
        "expected_move": None,
        "raw_return": None,
        "annualized_return": None,
        "pow": None,
        "model_greeks": None,
        "explanation": None,
        "greeks_text": None,
        "suggestion": None,
        "checklist": None,
        "projected_yield_12m": None,
        "dividend_yield": None,
        "score": None,
    }
    app_sym = request.app
    symbol_up = symbol.strip().upper() if symbol else None

    # Credit and the return metrics need only the payoff engine's own numbers. Option legs only --
    # a covered call's stock leg is a debit (buying/holding 100 shares) that would otherwise swamp
    # the option premium and zero out the return metrics; the reference platform's own "Static
    # Yield" is the option income relative to the position's max risk, not the whole basket's net
    # cash flow (KWEB 2026-08-03: $73.50 option credit / $2,789.50 max risk = 2.64%, matching the
    # displayed static yield -- the stock leg only ever affects max_risk, never this numerator).
    credit = -sum(leg.quantity * leg.price for leg in parsed if leg.kind != "stock") * 100
    loss = result["max_loss"]
    if credit > 0 and not loss["unbounded"] and loss["value"] is not None and dte:
        max_risk = abs(loss["value"])
        result["raw_return"] = _describe.raw_return(credit, max_risk)
        result["annualized_return"] = _describe.annualized_return(credit, max_risk, dte)

    exp_date = None
    if expiration:
        try:
            exp_date = _date.fromisoformat(expiration)
        except ValueError:
            exp_date = None

    if dte is not None and iv is not None and dte > 0 and iv > 0:
        t = dte / 365.0
        app = request.app
        try:
            r = await metrics_service.get_risk_free_rate(app.state.cache_db, app.state.broker_session)
        except Exception:
            r = 0.0  # POP without a fresh rate is still a reasonable estimate; zero is a mild one
        result["pop"] = _pop.pop(parsed, spot, iv, t, r)
        result["expected_move"] = _pop.expected_move(spot, iv, t)
        result["pow"] = _describe.prob_worthless(parsed, spot, iv, t, r)
        result["model_greeks"] = _describe.bs_greeks(parsed, spot, iv, t, r)
        if symbol and result["model_greeks"]:
            result["greeks_text"] = _describe.greeks_explanation(
                symbol.strip().upper(), result["model_greeks"]
            )
        result["score"] = _describe.score(
            result["pop"], parsed, result["max_profit"], result["max_loss"]
        )

    result["explanation"] = _describe.strategy_explanation(parsed, spot, result["pop"], exp_date)

    short_puts = [lg for lg in parsed if lg.kind == "put" and lg.quantity < 0 and lg.strike]
    if symbol and exp_date and credit > 0 and len(parsed) == 1 and len(short_puts) == 1:
        result["suggestion"] = _describe.short_put_suggestion(
            symbol.strip().upper(), short_puts[0].strike, exp_date, credit, spot
        )

    # The strategy checklist: the income variant for a lone short option (plus optional stock --
    # the covered-call shape), the directional variant otherwise. Trend rows are cache-only reads.
    spread_pct = _describe.combo_spread_pct(raw_items)
    earnings_inside = await _earnings_inside(app_sym, symbol_up, exp_date) if symbol else None
    has_weeklies = None
    if symbol:
        # Weekly-cadence check for the liquidity grade (user rule: high liquidity must always
        # have weekly expirations). The expirations map is TTL-cached, so this is cheap.
        try:
            from ..services import chain_service as _chain_service

            expirations = await _chain_service.get_expirations(
                app_sym.state.cache_db, app_sym.state.broker_session, app_sym.state.cfg, symbol_up
            )
            has_weeklies = _describe.has_weekly_cadence(list(expirations["expirations"]))
        except Exception:
            has_weeklies = None
    option_legs = [lg for lg in parsed if lg.kind != "stock"]
    stock_legs = [lg for lg in parsed if lg.kind == "stock"]
    is_income = len(option_legs) == 1 and option_legs[0].quantity < 0
    is_covered_call = (
        is_income and option_legs[0].kind == "call" and len(stock_legs) == 1 and stock_legs[0].quantity > 0
    )
    if is_covered_call and symbol_up and result["annualized_return"] is not None:
        try:
            metrics_ttl = app_sym.state.cfg.get("refresh", {}).get("metrics_ttl_seconds", 900)
            metrics = await metrics_service.get_metrics(
                app_sym.state.cache_db, app_sym.state.broker_session, [symbol_up], metrics_ttl
            )
            result["dividend_yield"] = (metrics.get(symbol_up) or {}).get("dividend_yield")
        except Exception:
            result["dividend_yield"] = None
        result["projected_yield_12m"] = _describe.projected_yield_12m(
            result["annualized_return"], result["dividend_yield"]
        )
    if is_income:
        result["checklist"] = {
            "kind": "income",
            "items": _describe.checklist(
                result["pow"], result["annualized_return"], earnings_inside, spread_pct, has_weeklies
            ),
        }
    else:
        strategy_dir = _describe.direction(parsed, spot)
        stock_trend = _trend_1m_from_cache(app_sym, symbol_up) if symbol else None
        market_trend = _trend_1m_from_cache(app_sym, "SPX")
        result["checklist"] = {
            "kind": "directional",
            "items": _describe.checklist_directional(
                strategy_dir, stock_trend, market_trend, earnings_inside, spread_pct, has_weeklies
            ),
        }
    return result
