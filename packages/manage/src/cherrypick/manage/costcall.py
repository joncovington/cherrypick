"""The cost-strike call: does the call at a wheel cycle's cost bid enough to rest an order on?

A public webinar's rule: with assigned shares and no call written, rest a sell order on the call at
the strike the cycle has cost you, for about 2% of the stock's price. This shows the cases where that
order would be worth resting; it places nothing and sizes nothing (a reading, like `attention.py`).

For each running, reconciled cycle that holds assigned shares with no short call open:

- **effective cost** -- the wheel basis (`wheel.py`): the shares' cost once every cash flow in the
  cycle is counted, so the assignment strike less the premiums banked, per share, with the cycle's
  fees and dividends in it as they always are;
- **expiry** -- the earliest standard monthly `dte_min` to `dte_max` days out, else the earliest
  monthly up to `fallback_max` days out;
- **strike** -- the lowest strike listed on that expiry at or above the effective cost;
- **bid** -- that call's bid, from the stream cache's quote where the cache holds a fresh one, else
  from the chain the snapshot read;
- **pays** -- the bid is at least `min_bid_pct` (2%) of the stock's price.

A row is `pays`, `below` (quoted, under the bar) or `no quote`, which says why (no chain read, no
listed strike at or above the cost, a chain window that stops short of it, no bid). A missing quote is
never read as a bid under the bar. `None` is not zero throughout.

Pure: cycles, prices and the snapshot's `cost_call` block in, rows out.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from cherrypick.core import calendar as _calendar
from cherrypick.core.streamcache import occ_to_streamer_symbol

MULTIPLIER = 100
EPS = 1e-9


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def pick_expiry(
    expirations: list[str] | None, session: date, cfg: dict[str, Any]
) -> tuple[str | None, str | None]:
    """(expiry, window): the earliest standard monthly `dte_min`..`dte_max` days out ("standard"),
    else the earliest up to `fallback_max` ("fallback"); (None, None) when none is listed."""
    lo, hi, far = int(cfg["dte_min"]), int(cfg["dte_max"]), int(cfg["fallback_max"])
    monthlies = []
    for raw in expirations or []:
        try:
            day = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if _calendar.is_monthly_expiry(day):
            monthlies.append(((day - session).days, day.isoformat()))
    for last, window in ((hi, "standard"), (far, "fallback")):
        found = sorted(m for m in monthlies if lo <= m[0] <= last)
        if found:
            return found[0][1], window
    return None, None


def cost_strike(cost: float, strikes: list[float]) -> float | None:
    """The lowest listed strike at or above `cost`."""
    above = [k for k in strikes if k >= cost - EPS]
    return min(above) if above else None


def eligible(cycle: dict[str, Any]) -> str | None:
    """None when the cycle holds assigned shares, reconciled and costed, with no short call open;
    else why it is not one."""
    if cycle.get("end") is not None or cycle.get("status") != "open":
        return "the cycle is not running"
    if not cycle.get("reconciled"):
        return "the cycle does not reconcile"
    if (cycle.get("shares") or 0) < MULTIPLIER - EPS:
        return "no round lot held"
    if (cycle.get("assignments") or 0) < 1:
        return "the shares were not delivered by an assignment"
    if not cycle.get("basis_known"):
        return "the shares' basis is not known"
    shorts = [o for o in cycle.get("open_options") or [] if (o.get("quantity") or 0) < 0 and _is_call(o)]
    if shorts:
        return "a call is already written"
    basis = _num(cycle.get("wheel_basis"))
    if basis is None or basis <= 0:
        return "no positive wheel basis"
    return None


def _is_call(option: dict[str, Any]) -> bool:
    from cherrypick.manage import wheel as _wheel

    return (_wheel.occ(str(option.get("symbol") or "")) or {}).get("right") == "C"


def _quote(option: dict[str, Any], quotes: dict[str, Any]) -> dict[str, Any] | None:
    """The call's bid and ask: the stream cache's fresh quote, else the snapshot chain's own."""
    sym = occ_to_streamer_symbol(str(option.get("symbol") or "")) or str(option.get("symbol") or "").strip()
    cached = quotes.get(sym)
    if isinstance(cached, dict) and _num(cached.get("bid")) is not None:
        return {"bid": _num(cached["bid"]), "ask": _num(cached.get("ask")), "source": "stream cache"}
    if _num(option.get("bid")) is not None:
        return {"bid": _num(option["bid"]), "ask": _num(option.get("ask")), "source": "snapshot chain"}
    return None


def row_for(
    cycle: dict[str, Any],
    spot: float | None,
    chains: dict[str, Any] | None,
    quotes: dict[str, Any] | None,
    session: date,
    cfg: dict[str, Any],
    read_strikes: float | None = None,
) -> dict[str, Any]:
    """One eligible cycle's reading. `chains` is the snapshot's `cost_call` entry for the name:
    `expirations` (the listed ones read) and `chains`, {expiry: {"options": [...], "error": ...}}."""
    cost = float(cycle["wheel_basis"])
    row: dict[str, Any] = {
        "underlying": cycle["underlying"],
        "shares": cycle["shares"],
        "contracts": int(cycle["shares"] // MULTIPLIER),
        "effective_cost": round(cost, 4),
        "spot": spot,
        "min_bid_pct": float(cfg["min_bid_pct"]),
        "expiration": None,
        "dte": None,
        "window": None,
        "strike": None,
        "symbol": None,
        "bid": None,
        "ask": None,
        "bid_pct": None,
        "credit": None,
        "source": None,
        "status": "no quote",
        "pays": False,
        "why": None,
    }
    if spot is None or spot <= 0:
        row["why"] = f"no {cycle['underlying']} price to take {float(cfg['min_bid_pct']):.0%} of"
        return row
    if not chains:
        row["why"] = "the snapshot read no chain for the name"
        return row
    expiry, window = pick_expiry(list(chains.get("chains") or {}), session, cfg)
    if expiry is None:
        row["why"] = f"no standard monthly {cfg['dte_min']}-{cfg['fallback_max']} days out was read" + (
            f" ({chains['error']})" if chains.get("error") else ""
        )
        return row
    row.update(expiration=expiry, window=window, dte=(date.fromisoformat(expiry) - session).days)
    chain = chains["chains"][expiry] or {}
    options = [o for o in chain.get("options") or [] if o.get("right", "C") == "C" and _num(o.get("strike"))]
    strikes = sorted({float(o["strike"]) for o in options})
    if not strikes:
        row["why"] = chain.get("error") or f"no listed call strikes read for {expiry}"
        return row
    strike = cost_strike(cost, strikes)
    if strike is None:
        row["why"] = (
            f"no listed strike at or above the {cost:.2f} cost (the read chain tops out at {strikes[-1]:g})"
        )
        return row
    # The chain is read `read_strikes` strikes either side of the price. Full on the low side with the cost
    # below it, a lower listed strike may have been cut off, so the cost strike is not known.
    if read_strikes and cost < strikes[0] - EPS and sum(1 for k in strikes if k <= spot) >= read_strikes:
        row["why"] = (
            f"the read chain starts at {strikes[0]:g}, over the {cost:.2f} cost: the cost strike is not in it"
        )
        return row
    option = next(o for o in options if abs(float(o["strike"]) - strike) < EPS)
    row["strike"] = strike
    row["symbol"] = occ_to_streamer_symbol(str(option.get("symbol") or "")) or None
    q = _quote(option, quotes or {})
    if q is None:
        row["why"] = f"no bid for the {expiry} {strike:g} call"
        return row
    bid = q["bid"]
    row.update(bid=bid, ask=q["ask"], source=q["source"], bid_pct=round(bid / spot, 5))
    row["credit"] = round(bid * MULTIPLIER * row["contracts"], 2)
    row["pays"] = bid >= float(cfg["min_bid_pct"]) * spot - EPS
    row["status"] = "pays" if row["pays"] else "below"
    row["why"] = (
        f"the {expiry} {strike:g} call bids {bid:.2f}, {bid / spot:.1%} of {spot:.2f}"
        f" ({'at or over' if row['pays'] else 'under'} the {float(cfg['min_bid_pct']):.0%} bar)"
    )
    return row


def reading(
    cycles: list[dict[str, Any]] | None,
    metrics: dict[str, Any] | None,
    cost_call: dict[str, Any] | None,
    session: date,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """Every eligible wheel cycle's cost-strike call, `pays` first. Off with `attention.cost_call.enabled`
    false (rows empty, `enabled` false)."""
    cfg = rules["attention"]["cost_call"]
    out: dict[str, Any] = {
        "enabled": bool(cfg.get("enabled")),
        "min_bid_pct": float(cfg["min_bid_pct"]),
        "dte_min": int(cfg["dte_min"]),
        "dte_max": int(cfg["dte_max"]),
        "fallback_max": int(cfg["fallback_max"]),
        "rows": [],
        "names": [],
        "taken_at": (cost_call or {}).get("taken_at"),
        "error": (cost_call or {}).get("error"),
    }
    if not out["enabled"]:
        return out
    for cycle in cycles or []:
        if eligible(cycle) is not None:
            continue
        name = str(cycle["underlying"]).upper()
        spot = _num(((metrics or {}).get(name) or {}).get("last"))
        entry = ((cost_call or {}).get("names") or {}).get(name)
        out["rows"].append(
            row_for(
                cycle,
                spot,
                entry,
                (cost_call or {}).get("quotes"),
                session,
                cfg,
                _num((cost_call or {}).get("window_strikes")),
            )
        )
    order = {"pays": 0, "no quote": 1, "below": 2}
    out["rows"].sort(key=lambda r: (order[r["status"]], r["underlying"]))
    out["names"] = sorted({r["underlying"] for r in out["rows"]})
    return out


def legs(result_reading: dict[str, Any] | None) -> list[str]:
    """The streamer symbols of the cost-strike calls a reading names, for `stream_request`."""
    return sorted({str(r["symbol"]) for r in (result_reading or {}).get("rows") or [] if r.get("symbol")})
