"""Needs a look: every short option held, measured against its strike, and what expires soon.

A pure reading of the snapshot's positions and market metrics (no history). For each short option leg
(a short put or call, alone or a spread's short leg; the live loops' 0DTE legs left out, as the
evaluation leaves them out):

- **distance** -- how far the underlying is from the strike on the side that keeps the option out of
  the money, as a share of the strike: (spot - strike) / strike for a put, (strike - spot) / strike for
  a call. Negative is in the money.
- **time value** -- the leg's mid less its intrinsic value.
- **level**, the first that holds:
  - `early exercise` -- a call in the money with a dividend going ex before its expiry and less time
    value than the dividend, or any short in the money with time value under `near_zero_extrinsic`.
    The holder is likely to exercise; a cash-settled index root never is (European).
  - `in the money` -- past the strike.
  - `near` -- out of the money by `near_pct` of the strike or less (3%).
  Anything further out is not listed.

**Expiring** lists every option leg held, long or short, with `expiring_days` or fewer left (7).

**Intent** lists one item per trade manage still asks what it is for (`intent_items`): a naked short put
not yet marked a wheel or not, a call diagonal not yet marked a PMCC or not. Each says which trade, its
legs, what it is asked and manage's suggestion with the evidence. It is a reading of the evaluation's own
`intent_ask`, judges nothing, and is not posted as an alert: the orchestrator's needs-a-look alerts read
`items` only.

The thresholds are the ruleset's `attention` block (user, 2026-11-04). It judges nothing and fires no
rule: it is the list a person reads, and the orchestrator posts a name the first time it appears in a
day. `None` is never zero: an unpriced leg or a missing spot leaves its figures null and the leg is
listed only if its strike alone says so (never).
"""

from __future__ import annotations

from datetime import date
from typing import Any

EPS = 1e-9
SEVERITY = {"early exercise": 0, "in the money": 1, "near": 2}


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _label(leg: dict[str, Any]) -> str:
    k = _f(leg.get("strike"))
    ks = "?" if k is None else f"{k:g}"
    return f"{leg.get('expiration') or '?'} {ks}{leg.get('right') or '?'}"


def intent_items(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One item per trade whose intent question is open (`trade["intent_ask"]["asked"]`), in a stable order:
    puts before diagonals, then by name and trade. `scope` says where an answer is written, and `suggested`
    and `evidence` are the ask's own, never applied."""
    out = []
    for t in trades:
        ask = t.get("intent_ask")
        if not ask or not ask.get("asked"):
            continue
        legs = []
        for leg in t.get("legs") or []:
            if leg.get("right") not in ("C", "P"):
                continue  # shares and the like are not options to label
            qty = _f(leg.get("quantity")) or 0.0
            legs.append(f"{'-' if qty < 0 else '+'}{abs(qty):g} {_label(leg)}")
        out.append(
            {
                "trade_id": t.get("trade_id"),
                "underlying": t.get("underlying"),
                "kind": ask["kind"],
                "shape": t.get("shape"),
                "legs": legs,
                "scope": ask["scope"],
                "suggested": ask.get("suggested"),
                "evidence": list(ask.get("evidence") or []),
                "note": ask.get("note"),
            }
        )
    out.sort(key=lambda i: (i["kind"] != "wheel", str(i["underlying"]), str(i["trade_id"])))
    return out


def reading(
    underlyings: list[dict[str, Any]] | None,
    metrics: dict[str, Any] | None,
    session: date,
    rules: dict[str, Any],
) -> dict[str, Any]:
    cfg = rules["attention"]
    near_pct = float(cfg["near_pct"])
    near_zero = float(cfg["near_zero_extrinsic"])
    days_ahead = int(cfg["expiring_days"])
    zero_dte = {str(s).upper() for s in ((rules.get("exclude") or {}).get("zero_dte_underlyings") or [])}
    cash_roots = {str(s).upper() for s in ((rules.get("expiry") or {}).get("cash_settled_roots") or [])}
    metrics = metrics or {}
    items: list[dict[str, Any]] = []
    expiring: list[dict[str, Any]] = []
    today = session.isoformat()
    for u in underlyings or []:
        name = str(u.get("underlying") or "").upper()
        m = metrics.get(name) or {}
        spot = _f(m.get("last"))
        xd = str(m.get("ex_dividend_date") or "")[:10]
        div = _f(m.get("dividend_rate_per_share"))
        for leg in u.get("legs") or []:
            right = leg.get("right")
            qty = _f(leg.get("quantity")) or 0.0
            exp = str(leg.get("expiration") or "")[:10]
            k = _f(leg.get("strike"))
            if right not in ("C", "P") or not exp or k is None or qty == 0.0 or exp < today:
                continue
            if name in zero_dte and exp == today:
                continue
            dte = (date.fromisoformat(exp) - session).days
            mid = _f(leg.get("mid")) if leg.get("priced", True) else None
            if spot is None:
                distance = intrinsic = None
            else:
                distance = (spot - k) / k if right == "P" else (k - spot) / k
                intrinsic = max(0.0, k - spot) if right == "P" else max(0.0, spot - k)
            extrinsic = None if mid is None or intrinsic is None else mid - intrinsic
            base = {
                "underlying": name,
                "symbol": leg.get("symbol"),
                "leg": _label(leg),
                "right": right,
                "strike": k,
                "expiration": exp,
                "dte": dte,
                "quantity": qty,
                "spot": spot,
                "distance": None if distance is None else round(distance, 4),
                "mid": mid,
                "extrinsic": None if extrinsic is None else round(extrinsic, 4),
            }
            if dte <= days_ahead:
                expiring.append(base)
            if qty > 0 or distance is None:
                continue
            itm = distance < -EPS
            level, why = None, None
            if itm and name not in cash_roots and extrinsic is not None:
                if right == "C" and xd and today < xd <= exp and div is not None and extrinsic < div - EPS:
                    level = "early exercise"
                    why = (
                        f"in the money; ex-dividend {xd} before expiry, "
                        f"time value {extrinsic:.2f} < dividend {div:.2f}"
                    )
                elif extrinsic < near_zero - EPS:
                    level = "early exercise"
                    why = f"in the money with {extrinsic:.2f} of time value (under {near_zero:.2f})"
            if level is None and itm:
                level, why = "in the money", f"{-distance:.1%} through the strike"
            if level is None and distance <= near_pct + EPS:
                level, why = "near", f"{distance:.1%} from the strike (alert at {near_pct:.0%})"
            if level is not None:
                items.append({**base, "level": level, "why": why})
    items.sort(key=lambda i: (SEVERITY[i["level"]], i["distance"], i["underlying"]))
    expiring.sort(key=lambda i: (i["dte"], i["underlying"], i["leg"]))
    return {
        "near_pct": near_pct,
        "expiring_days": days_ahead,
        "items": items,
        "expiring": expiring,
        "names": sorted({i["underlying"] for i in items}),
    }
