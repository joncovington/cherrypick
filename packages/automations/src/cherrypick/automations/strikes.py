"""Strike rules: one leg's strike from a rule dict, the snapshot and the legs already chosen.

A rule is `{"rule": <kind>, ...}`. `chosen` maps a leg name to its strike, so a leg can be placed against
an earlier one. Every function returns a listed strike or None; None means the arm has no trade today.

**Match mode.** A rule that aims at a price says how the target meets the listed strikes, in `match`:
`exact` (no trade when that strike is not listed), `nearest` (ties go to the lower), `above` (the lowest
listed at or above) or `below` (the highest at or below). A rule that aims at a value of the option
(delta, premium) matches on that value instead: `nearest`, `above` (the smallest value at or above the
target) or `below` (the largest at or below); ties go to the lower strike. The rules that predate `match`
keep their defaults; a newer rule states it. The old `round` spelling resolves (`down` is `below`, `up`
is `above`), as does delta's `at_most` (`below`).

Price targets (`points` or `pct` shift the target; a negative one is below it):

    spot          {"points": -5} or {"pct": -0.05}        from the underlying's spot      (default nearest)
    prev_close    {"points": 0.01}                         from the previous close         (default nearest)
    open          {"points": 0}                            from the session's open         (default nearest)
    from_leg      {"leg": "short_put", "points": -10}      from an earlier leg's strike    (default exact)
    match_width   {"from": "short_call", "width_of": ["short_put", "long_put"]}
                  from + the width between the two, no shift                             (default nearest)
    expected_move {"times": -1.0}                          spot + times x the expected move
    std_dev       {"sd": -1.0}                             spot + sd x one standard deviation
    opening_range {"minutes": 30, "level": "high"|"low"|"mid"}
                                                           from the range, once it has closed
    entry_level   {}                                       from the spot when the entry fired
    same_as       {"leg": "short_put"}                     the same strike (exact)

The **expected move** is the mid of the at-the-money straddle (the listed strike nearest spot with both
rights quoted, ties to the lower) on the arm's expiry. **One standard deviation** is that straddle over
sqrt(2/pi): for a lognormal price the straddle is worth about 0.8 of a standard deviation, so both need
only quotes, no model greeks.

Value targets:

    delta         {"target": 0.30}                         |delta|                         (default nearest)
    premium       {"target": 1.20, "price": "bid"|"ask"|"mid"}   the option's price a share

Picked from the GEX profile among the `strikes` listed nearest spot (the strike itself, so exact):

    max_abs_gex   {"strikes": 6}                           the largest |net gex|
    gex_max       {"measure": <GEX_MEASURES>, "strikes": 20}
                  net_positive (the largest net gex above zero), net_negative (the most negative),
                  call_gex, put_gex (the largest of each side's size), call_oi, put_oi
"""

from __future__ import annotations

import math

from . import indicators as ind
from .model import Chain, Snapshot, mid

MATCHES = ("exact", "nearest", "above", "below")
VALUE_MATCHES = ("nearest", "above", "below")
#: The old `round` spelling of a match mode.
ROUND_ALIAS = {"exact": "exact", "nearest": "nearest", "down": "below", "up": "above"}
#: Rules that place a strike off a price, and their match default (None: the rule must state it).
PRICE_RULES = {
    "spot": "nearest",
    "prev_close": "nearest",
    "open": "nearest",
    "from_leg": "exact",
    "match_width": "nearest",
    "expected_move": None,
    "std_dev": None,
    "opening_range": None,
    "entry_level": None,
}
#: Rules that place a strike off a value of the option, and their match default.
VALUE_RULES = {"delta": "nearest", "premium": None}
GEX_RULES = ("max_abs_gex", "gex_max")
GEX_MEASURES = ("net_positive", "net_negative", "call_gex", "put_gex", "call_oi", "put_oi")
PRICES = ("bid", "ask", "mid")
OR_LEVELS = ("high", "low", "mid")
#: For a lognormal price the at-the-money straddle is worth sqrt(2/pi) of one standard deviation.
STRADDLE_PER_SD = math.sqrt(2.0 / math.pi)
_CHAIN_MODE = {"nearest": "nearest", "above": "up", "below": "down"}


def match_of(rule: dict) -> str | None:
    """The rule's match mode: stated, in the old spelling, or the rule's default (None when it has none)."""
    if rule.get("match") is not None:
        return rule["match"]
    if rule.get("round") is not None:
        return ROUND_ALIAS.get(rule["round"], rule["round"])
    if rule.get("at_most"):
        return "below"
    kind = rule.get("rule")
    return PRICE_RULES.get(kind) or VALUE_RULES.get(kind)


def _to_listed(chain: Chain, target: float, match: str) -> float | None:
    if match == "exact":
        return float(target) if float(target) in chain.strikes else None
    return chain.nearest_strike(target, mode=_CHAIN_MODE[match])


def _by_value(chain: Chain, right: str, target: float, match: str, value) -> float | None:
    """The strike whose `value(row)` meets `target` by `match`; a row without a value is passed over."""
    best = None
    for r in chain.rows:
        if r["right"] != right:
            continue
        v = value(r)
        if v is None:
            continue
        if match == "above" and v < target - 1e-12:
            continue
        if match == "below" and v > target + 1e-12:
            continue
        gap = abs(v - target)
        if best is None or gap < best[0] - 1e-12 or (abs(gap - best[0]) <= 1e-12 and r["strike"] < best[1]):
            best = (gap, r["strike"])
    return None if best is None else best[1]


def _delta(r: dict) -> float | None:
    return abs(r["delta"]) if r.get("delta") is not None and r.get("ask") else None


def _price(kind: str):
    def value(r: dict) -> float | None:
        bid, ask = r.get("bid"), r.get("ask")
        if not ask or bid is None:
            return None
        return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}[kind]

    return value


def _shifted(base: float, rule: dict) -> float:
    if rule.get("pct") is not None:
        return base * (1.0 + rule["pct"] / 100.0)
    return base + rule.get("points", 0.0)


def expected_move(snap: Snapshot, chain: Chain) -> float | None:
    """The at-the-money straddle's mid: the listed strike nearest spot quoted on both rights (ties to the
    lower). None when no strike is."""

    def quoted(k: float, right: str) -> bool:
        q = chain.get(k, right)
        return bool(q and q.get("ask") and q.get("bid") is not None)

    both = [k for k in chain.strikes if quoted(k, "C") and quoted(k, "P")]
    if not both:
        return None
    k = min(both, key=lambda s: (abs(s - snap.spot), s))
    return mid(chain.get(k, "C")) + mid(chain.get(k, "P"))


def entry_level(snap: Snapshot, fired: int | None) -> float | None:
    """The spot when the entry fired: the snapshot's spot when it fired this minute, else the trail's last
    sample at or before that minute. None when the trail holds none by then."""
    if fired is None:
        return None
    if fired >= snap.minute:
        return snap.spot
    seen = [s for m, s in snap.trail if m <= fired]
    return seen[-1] if seen else None


def _base(kind: str, rule: dict, snap: Snapshot, chain: Chain, chosen: dict, fired: int | None):
    """The price a price rule aims at before its shift; None when it cannot be read today."""
    if kind == "spot":
        return snap.spot
    if kind == "prev_close":
        return snap.prev_close
    if kind == "open":
        return snap.day_open
    if kind == "from_leg":
        return chosen.get(rule["leg"])
    if kind == "match_width":
        base = chosen.get(rule["from"])
        a, b = (chosen.get(n) for n in rule["width_of"])
        return None if base is None or a is None or b is None else base + abs(a - b)
    if kind in ("expected_move", "std_dev"):
        em = expected_move(snap, chain)
        if em is None:
            return None
        if kind == "expected_move":
            return snap.spot + rule["times"] * em
        return snap.spot + rule["sd"] * em / STRADDLE_PER_SD
    if kind == "opening_range":
        rng = ind.opening_range([(m, s) for m, s in snap.trail if m <= snap.minute], rule["minutes"])
        if rng is None:
            return None
        return {"high": rng["high"], "low": rng["low"], "mid": (rng["high"] + rng["low"]) / 2.0}[
            rule["level"]
        ]
    if kind == "entry_level":
        return entry_level(snap, fired)
    raise ValueError(f"unknown strike rule {kind!r}")


def _gex_pick(rule: dict, snap: Snapshot, chain: Chain) -> float | None:
    rows = snap.gex.get(chain.expiry) or []
    near = sorted(rows, key=lambda s: (abs(s["strike"] - snap.spot), s["strike"]))[: rule["strikes"]]
    if rule["rule"] == "max_abs_gex":
        score = {s["strike"]: s["abs_gex"] for s in near}
    elif rule["measure"] == "net_positive":
        score = {s["strike"]: s["net_gex"] for s in near if s["net_gex"] > 0}
    elif rule["measure"] == "net_negative":
        score = {s["strike"]: -s["net_gex"] for s in near if s["net_gex"] < 0}
    else:
        # put_gex is stored negative (core.gex), so a side's size is the measure.
        score = {s["strike"]: abs(s[rule["measure"]]) for s in near if s.get(rule["measure"])}
    if not score:
        return None
    top = max(score, key=lambda k: (score[k], -k))
    return top if float(top) in chain.strikes else None


def pick(
    rule: dict,
    right: str,
    snap: Snapshot,
    chain: Chain,
    chosen: dict[str, float],
    *,
    fired: int | None = None,
) -> float | None:
    """One leg's listed strike, or None. `fired` is the minute the entry fired (`engine.entry_due`'s
    value), which `entry_level` reads."""
    kind = rule["rule"]
    if kind == "same_as":
        ref = chosen.get(rule["leg"])
        return ref if ref is not None and float(ref) in chain.strikes else None
    if kind in GEX_RULES:
        return _gex_pick(rule, snap, chain)
    match = match_of(rule)
    if kind == "delta":
        return _by_value(chain, right, rule["target"], match, _delta)
    if kind == "premium":
        return _by_value(chain, right, rule["target"], match, _price(rule["price"]))
    base = _base(kind, rule, snap, chain, chosen, fired)
    if base is None:
        return None
    return _to_listed(chain, base if kind == "match_width" else _shifted(base, rule), match)
