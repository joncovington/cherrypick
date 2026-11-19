"""A structure's generic shape, drawn from its rule alone: no session, no chain, no price. Pure.

Each leg sits where its strike rule puts it, spot being 0:

- a leg placed by points (`spot`, `from_leg`, `same_as`, `match_width` on such legs) at its distance in
  points, labelled `-5`, `+10`: drawn to scale against the others;
- any other at a position that keeps the legs in order (`delta` by its distance in standard deviations,
  `expected_move` and `std_dev` by their multiple, a percent shift, the previous close, the open, the
  opening range, the entry's level, a GEX pick), labelled by its rule (`Δ0.30`, `Δ0.30 +15`, `1 EM`).
  Then the order is true and the spacing is not, and `exact` is false.

The payoff is each leg's intrinsic value times its `side` (contracts, signed) at expiry, shifted by a
nominal premium so the shape reads as a trade would: a structure that can only lose at expiry before
its premium (sold premium) is lifted by part of its worst loss, one that can only gain (bought premium)
lowered by part of its best gain, and one that does both left where it is. That premium is a drawing
aid, never a price: the y axis is labelled in words (max profit, max loss), not dollars.
"""

from __future__ import annotations

from statistics import NormalDist

from . import pricing
from .model import Leg

#: The drawing's unit for a leg not placed in points: one standard deviation, one expected move, one
#: percent are each this many points apart, and a range level half of it.
UNIT = 10.0
#: How much of the worst (or best) intrinsic value the nominal premium is.
PREMIUM_SHARE = 0.3
#: The margin either side of the outer legs (and the spot), as a share of their span, at least a unit.
MARGIN = 0.6


def _fmt(points: float) -> str:
    v = round(points, 2)
    if v == 0:
        return "0"
    s = f"{abs(v):g}"
    return f"+{s}" if v > 0 else f"−{s}"


def _shift(rule: dict) -> tuple[float, str, bool]:
    """(position shift, label suffix, in points) for a price rule's `points` or `pct`."""
    if rule.get("pct") is not None:
        pct = float(rule["pct"])
        return pct * UNIT, f" {pct:+g}%", False
    pts = float(rule.get("points") or 0.0)
    return pts, "" if pts == 0 else f" {_fmt(pts)}", True


def _place(rule: dict, right: str, placed: dict[str, dict]) -> dict:
    """{at, label, exact} for one leg's strike rule against the legs placed before it."""
    kind = rule.get("rule")
    sign = 1.0 if right == "C" else -1.0
    if kind == "spot":
        at, suffix, exact = _shift(rule)
        return {"at": at, "label": _fmt(at) if exact else f"spot{suffix}", "exact": exact}
    if kind in ("from_leg", "same_as"):
        ref = placed.get(rule.get("leg", ""))
        if ref is None:
            return {"at": 0.0, "label": "?", "exact": False}
        if kind == "same_as":
            return dict(ref)
        d, suffix, exact = _shift(rule)
        at = ref["at"] + d
        if ref["exact"] and exact:
            return {"at": at, "label": _fmt(at), "exact": True}
        return {"at": at, "label": f"{ref['label']}{suffix}", "exact": False}
    if kind == "match_width":
        base = placed.get(rule.get("from", ""))
        a, b = (placed.get(n) for n in rule.get("width_of") or ["", ""])
        if base is None or a is None or b is None:
            return {"at": 0.0, "label": "?", "exact": False}
        at = base["at"] + abs(a["at"] - b["at"])
        if base["exact"] and a["exact"] and b["exact"]:
            return {"at": at, "label": _fmt(at), "exact": True}
        return {"at": at, "label": f"{base['label']} + width", "exact": False}
    if kind == "delta":
        t = min(max(abs(float(rule.get("target") or 0.5)), 0.01), 0.99)
        return {"at": sign * NormalDist().inv_cdf(1 - t) * UNIT, "label": f"Δ{t:.2f}", "exact": False}
    if kind == "premium":
        return {"at": sign * UNIT, "label": f"${float(rule.get('target') or 0):g} premium", "exact": False}
    if kind == "expected_move":
        n = float(rule.get("times") or 0)
        return {"at": n * UNIT, "label": f"{n:+g} EM", "exact": False}
    if kind == "std_dev":
        n = float(rule.get("sd") or 0)
        return {"at": n * UNIT, "label": f"{n:+g} SD", "exact": False}
    if kind in ("prev_close", "open", "entry_level"):
        d, suffix, _ = _shift(rule)
        name = {"prev_close": "prev close", "open": "open", "entry_level": "entry"}[kind]
        return {"at": d, "label": f"{name}{suffix}", "exact": False}
    if kind == "opening_range":
        level = rule.get("level", "mid")
        d, suffix, _ = _shift(rule)
        base = {"high": UNIT / 2, "low": -UNIT / 2}.get(level, 0.0)
        return {"at": base + d, "label": f"OR {level}{suffix}", "exact": False}
    # A GEX pick: the strike itself, near the spot.
    return {"at": 0.0, "label": "GEX pick", "exact": False}


def sketch(arm: dict) -> dict:
    """The generic shape of `arm`'s structure: each leg's position and label, the payoff curve at
    those positions as [at, value] points, the ticks to label, and how to read the extremes."""
    placed: dict[str, dict] = {}
    legs_out: list[dict] = []
    legs: list[Leg] = []
    for spec in arm["legs"]:
        p = _place(spec.get("strike") or {}, spec["right"], placed)
        placed[spec["name"]] = p
        legs.append(Leg(spec["name"], spec["right"], spec["side"], p["at"]))
        legs_out.append({"name": spec["name"], "right": spec["right"], "side": spec["side"], **p})
    if not legs:
        return {"legs": [], "curve": [], "ticks": [], "exact": True, "top": None, "bottom": None}
    xs = [g.strike for g in legs] + [0.0]
    span = max(xs) - min(xs)
    pad = max(span * MARGIN, UNIT)
    lo, hi = min(xs) - pad, max(xs) + pad
    raw = pricing.payoff_curve(legs, 0.0, lo, hi)
    best, worst = max(v for _, v in raw), min(v for _, v in raw)
    premium = PREMIUM_SHARE * -worst if best <= 0 else -PREMIUM_SHARE * best if worst >= 0 else 0.0
    curve = [[round(x, 3), round(v + premium, 3)] for x, v in raw]
    left_flat = curve[0][1] == curve[1][1]
    right_flat = curve[-1][1] == curve[-2][1]
    top_end = max(curve, key=lambda p: p[1])
    bottom_end = min(curve, key=lambda p: p[1])

    def bounded(point) -> bool:
        return not ((point is curve[0] and not left_flat) or (point is curve[-1] and not right_flat))

    ticks: dict[float, str] = {0.0: "0 · spot"}
    for g in legs_out:
        key = round(g["at"], 3)
        if key != 0.0:
            ticks.setdefault(key, g["label"])
    return {
        "legs": [{**g, "at": round(g["at"], 3)} for g in legs_out],
        "curve": curve,
        "ticks": [{"at": k, "label": v} for k, v in sorted(ticks.items())],
        "exact": all(g["exact"] for g in legs_out),
        "top": "max profit" if bounded(top_end) else "profit, unlimited",
        "bottom": "max loss" if bounded(bottom_end) else "loss, unlimited",
    }
