"""A group's suggested allocation: the most its members can have at risk at once, from their own rules.

No price, no session, no ledger. Each member's bound is its structure's worst outcome per contract times its
contracts and the most structures it may hold at once (`limits.max_open`):

- **A dollar cap** (`size.max_risk`): that cap is the most one structure may risk.
- **Contracts** (`size.contracts`): the structure's widest outcome at expiry, before any premium, from
  its legs placed as the shape places them (`sketch.legs_at`). A defined-risk structure loses at most its
  widest wing less the credit and a debit structure at most its width, so the width is a bound on either.
  It counts only when it depends on the distances between the legs and not on where the shape put a leg
  it cannot place without a session (a delta, a percent): the legs are placed at two drawing units and the
  two widths must agree. A wing set by a percent, a long option alone, or an unbounded side has no bound.
- **A percent of the allocation** (`size.allocation_pct`): sized by the allocation itself, so it cannot
  suggest one.

A member with no bound is listed with why, and the group's suggestion is then None: a total that left a
member out would understate the money the group ties up. A retired member is not counted. The suggestion
is what a person may take; nothing here writes it, and the loop never reads it.
"""

from __future__ import annotations

from . import pricing, sketch

#: Dollars per point of an index or fund option: every symbol these automations trade.
MULTIPLIER = 100
#: The second drawing unit `legs_at` is placed at, to see whether a width depends on the unit.
PROBE_UNIT = 37.0


def _width(arm: dict, unit: float) -> float | None:
    """The structure's widest outcome per share at expiry before premium, at drawing unit `unit`."""
    profit, loss = pricing.payoff_extremes(sketch.legs_at(arm, unit), 0.0)
    if profit is None or loss is None:
        return None
    return max(abs(profit), abs(loss))


def risk_bound(arm: dict) -> tuple[float | None, str | None]:
    """(dollars, None) for the most `arm` can have at risk at once, or (None, why)."""
    size = arm.get("size") or {}
    max_open = int((arm.get("limits") or {}).get("max_open") or 1)
    if "max_risk" in size:
        return round(float(size["max_risk"]) * max_open, 2), None
    if "allocation_pct" in size:
        return None, "sized as a percent of the allocation itself"
    if "contracts" not in size:
        return None, "its size names no contracts and no dollar cap"
    a, b = _width(arm, sketch.UNIT), _width(arm, PROBE_UNIT)
    if a is None or b is None:
        return None, "its loss has no bound before a premium is paid"
    if abs(a - b) > 1e-9:
        return None, "a wing is placed by delta or percent, so its width is set by the session"
    if a <= 0:
        return None, "its risk is the premium it pays, known only once it is placed"
    return round(a * MULTIPLIER * int(size["contracts"]) * max_open, 2), None


def suggested(members: dict[str, dict]) -> dict:
    """{"allocation": dollars or None, "members": [{"name", "risk", "why"}]} over the members not
    retired, each with its bound (`risk_bound`)."""
    rows = []
    for name, arm in sorted(members.items()):
        if arm.get("retired"):
            continue
        risk, why = risk_bound(arm)
        rows.append({"name": name, "risk": risk, "why": why})
    known = [r["risk"] for r in rows if r["risk"] is not None]
    total = round(sum(known), 2) if rows and len(known) == len(rows) else None
    return {"allocation": total, "members": rows}
