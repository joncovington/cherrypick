"""``GET /api/payoff`` -- pure leg-list -> payoff computation. The only broker-shaped call anywhere on
this path is `metrics_service.get_risk_free_rate` (cached daily), needed for POP's Black-Scholes
drift term; if it's unavailable (no credentials, a hiccup) POP/expected-move are simply omitted from
the response rather than failing the whole computation, since the payoff curve itself needs nothing
from the broker at all.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Request

from ..analytics import payoff as _payoff
from ..analytics import pop as _pop
from ..services import metrics_service

router = APIRouter()


def _parse_legs(raw: str) -> list[_payoff.Leg]:
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
    return legs


@router.get("/api/payoff")
async def get_payoff(
    request: Request,
    legs: str = Query(..., description="JSON array of leg objects"),
    spot: float = Query(...),
    dte: float | None = Query(None, description="days to expiration, for POP/expected move"),
    iv: float | None = Query(None, description="implied volatility (decimal), for POP/expected move"),
) -> dict:
    parsed = _parse_legs(legs)
    result = {
        "ok": True,
        "curve": _payoff.payoff_curve(parsed),
        "breakevens": _payoff.breakevens(parsed),
        "max_profit": _payoff.max_profit(parsed),
        "max_loss": _payoff.max_loss(parsed),
        "net_greeks": _payoff.net_greeks(parsed),
        "pop": None,
        "expected_move": None,
    }
    if dte is not None and iv is not None and dte > 0 and iv > 0:
        t = dte / 365.0
        app = request.app
        try:
            r = await metrics_service.get_risk_free_rate(app.state.cache_db, app.state.broker_session)
        except Exception:
            r = 0.0  # POP without a fresh rate is still a reasonable estimate; zero is a mild one
        result["pop"] = _pop.pop(parsed, spot, iv, t, r)
        result["expected_move"] = _pop.expected_move(spot, iv, t)
    return result
