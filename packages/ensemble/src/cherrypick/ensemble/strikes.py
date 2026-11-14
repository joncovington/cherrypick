"""Strike rules: one leg's strike from a rule dict, the snapshot and the legs already chosen.

A rule is `{"rule": <kind>, ...}`. `chosen` maps a leg name to its strike, so a leg can be placed against
an earlier one. Every function returns a listed strike or None; None means the arm has no trade today.

    delta        {"target": 0.30, "at_most": False}   |delta| nearest the target; with at_most, the
                 nearest at or below it (a long put "-0.10 delta or higher")
    spot         {"points": -5} or {"pct": -0.05}, "round": nearest|down|up   from the underlying's spot
    prev_close   {"points": 0.01}                      from the previous close
    from_leg     {"leg": "short_put", "points": -10} or {"pct": -0.5}, "round": exact|nearest|down|up
    same_as      {"leg": "short_put"}
    match_width  {"from": "short_call", "width_of": ["short_put", "long_put"]}  from + the width between
    max_abs_gex  {"strikes": 6}   the largest |net gex| among the N strikes nearest spot
"""

from __future__ import annotations

from .model import Chain, Snapshot

ROUND_DEFAULT = "nearest"


def _delta_pick(chain: Chain, right: str, target: float, at_most: bool) -> float | None:
    best, best_gap = None, None
    for r in chain.rows:
        if r["right"] != right or r.get("delta") is None or not r.get("ask"):
            continue
        d = abs(r["delta"])
        if at_most and d > target + 1e-12:
            continue
        gap = abs(d - target)
        if (
            best_gap is None
            or gap < best_gap - 1e-12
            or (abs(gap - best_gap) <= 1e-12 and r["strike"] < best)
        ):
            best, best_gap = r["strike"], gap
    return best


def _shifted(base: float, rule: dict) -> float:
    if rule.get("pct") is not None:
        return base * (1.0 + rule["pct"] / 100.0)
    return base + rule.get("points", 0.0)


def pick(rule: dict, right: str, snap: Snapshot, chain: Chain, chosen: dict[str, float]) -> float | None:
    kind = rule["rule"]
    if kind == "delta":
        return _delta_pick(chain, right, rule["target"], bool(rule.get("at_most")))
    if kind in ("spot", "prev_close"):
        base = snap.spot if kind == "spot" else snap.prev_close
        return chain.nearest_strike(_shifted(base, rule), mode=rule.get("round", ROUND_DEFAULT))
    if kind == "from_leg":
        ref = chosen.get(rule["leg"])
        if ref is None:
            return None
        target = _shifted(ref, rule)
        mode = rule.get("round", "exact")
        if mode == "exact":
            return target if (float(target) in chain.strikes) else None
        return chain.nearest_strike(target, mode=mode)
    if kind == "same_as":
        ref = chosen.get(rule["leg"])
        return ref if ref is not None and float(ref) in chain.strikes else None
    if kind == "match_width":
        base = chosen.get(rule["from"])
        a, b = (chosen.get(n) for n in rule["width_of"])
        if base is None or a is None or b is None:
            return None
        target = base + abs(a - b)
        return target if float(target) in chain.strikes else chain.nearest_strike(target, mode="nearest")
    if kind == "max_abs_gex":
        rows = snap.gex.get(chain.expiry) or []
        near = sorted(rows, key=lambda s: (abs(s["strike"] - snap.spot), s["strike"]))[: rule["strikes"]]
        if not near:
            return None
        top = max(near, key=lambda s: (s["abs_gex"], -s["strike"]))
        return top["strike"] if float(top["strike"]) in chain.strikes else None
    raise ValueError(f"unknown strike rule {kind!r}")
