"""The arms: one configured variant each, pure data. A new arm is a new entry here (or in the machine's
config, which `load_arms` reads over these) and, if it needs a gate or strike rule the registries lack,
one function there.

An arm's keys (all validated by `validate`; an unknown key is refused with its path, because a misspelt key
that defaulted silently would change what is measured):

    name, symbol, enabled
    group         the automation group it belongs to (a set of like automations, switched as one:
                  `config.groups`), or null for none; the shipped eight are the `ensemble` group
    register      {"item": N, "verdict": "passed"|"failed"|"no_verdict", "blocks": {block: hash}}: the
                  research register's verdict on it, a label read from the register, never from a
                  result file; `blocks` records each block as the study ran it (`enrolment`)
    start         ISO date: the first session paper may enter it (`enrolment.joins`)
    retired       false, or the ISO date it was retired: a retired automation is off whatever its
                  `enabled` says (`load_arms`), and a group or a switch never turns it on; reinstating it
                  (false) leaves it off until it is switched on
    forward       the forward-check rule its paper record is read by (`enrolment` docstring)
    expiry        {"kind": "dte"|"market_days"|"calendar", "days": N, "tolerance": D}
                  {"kind": "at_least", "days": N}: the nearest listed expiry N or more calendar days out
                  {"kind": "between", "min": N, "max": M, "prefer": "first"|"last"}: a listed expiry N
                  to M calendar days out, the nearest (`first`, the default) or the farthest
                  either with "monthly": true takes only standard monthlies (`calendar.monthly_expiry`)
    far_expiry    a calendar's or a diagonal's second expiry, for the legs with "expiry": "far": any
                  `expiry` rule (a listed expiry after the near one), or {"kind": "after_near", "days":
                  N, "monthly": bool}: the first listed expiry N or more calendar days after the near
                  one. With a far leg: at least one leg stays near, every far leg is bought, and
                  `exit.close_before_expiry_min` is required (`_far_problems`)
    entry         {"kind": "time", "at": "HH:MM", "window": minutes}
                  {"kind": "orb", "minutes": 60, "direction": "above", "latest": "HH:MM", "single_shot": bool}
                  {"kind": "trigger", "trigger": <core.entrychecks trigger>, "bar_minutes": N}
                  (a `core.signals` firing on the trail cut into N-minute bars, or a fixed time; an
                  event signal reads the event calendar's dates, `engine.events_for`; a trigger with
                  `"daily": true` reads completed daily bars and takes no `bar_minutes`)
    weekdays      [0..4], Monday = 0
    skip_events   {"labels": [...], "through_expiry": bool}
    legs          [{"name", "action": "BTO"|"STO", "qty": n, "side": "put"|"call", "strike": <strikes
                  rule>}] in placement order (each rule's keys and its `match` mode: `strikes`
                  docstring); `qty` is the leg's contracts per structure, 1 to `MAX_LEG_QUANTITY`. The
                  older spelling, {"name", "right": "C"|"P", "side": signed contracts, "strike"}, is read
                  for good; a leg is one or the other (`internal_leg`). Each leg's `name` is its own: a
                  later leg refers to an earlier one by it, whichever expiry either sits on. Either
                  spelling takes "expiry": "near" (the default) or "far" (`far_expiry`)
    gates         [{"gate": <gates.REGISTRY name>, ...}]
    size          {"contracts": N}, {"max_risk": dollars, "minimum": n} or
                  {"allocation_pct": p, "allocation": dollars, "max_dollars": x, "max_contracts": n},
                  any with "progression": {"after", "step", "floor", "ceiling"} (`sizing` docstring)
    limits        {"max_open": N, "per_day": 1, "every_days": N, "one_per_expiry": bool}: `max_open` and
                  `per_day` required, no default (`per_day` is 1: the entry register keeps one row per
                  arm and day)
    exit          {} (hold to settlement) with any of "close_before_expiry_min", "profit_target_pct",
                  "touch": {"pct": -0.5} or {"points": -10},
                  "signal": {"trigger": <core.entrychecks trigger>, "bar_minutes": N} (the entry
                  trigger's shape, read on the position's underlying; `engine.manage`),
                  "stop_credit_multiple": m (a credit entry's loss at m times its credit),
                  "stop_debit_pct": p (a debit entry's loss at p% of its debit),
                  "stop_dollars": x, "profit_dollars": x (open P&L, whole-position dollars),
                  "trailing_stop": {"dollars": d, "activate_dollars": a}
                  or {"pct": p, "activate_pct": a} (% of the trade's max profit),
                  "avoid_events": {"labels": [...], "before_close_min": n} (a forced close the
                  session before a listed event),
                  "max_spread": x (no exit while the structure's bid/ask is wider than $x a share),
                  "delta_limit": x (the structure's delta per unit, either way),
                  "close_at_dte": n (calendar days to expiry)
"""

from __future__ import annotations

import copy

from cherrypick.core import entrychecks as ec
from cherrypick.core import signals

from . import enrolment, sizing
from . import gates as gates_mod
from . import strikes as st


def _trigger_problems(
    where: str, entry: dict, keys: frozenset = frozenset({"kind", "trigger", "bar_minutes", "volume"})
) -> list[str]:
    """A trigger's problems (an entry's, or a `signal` exit's): core's validation of the trigger and its
    bar width."""
    if not isinstance(entry, dict):
        return [f"{where}: a trigger is an object"]
    extra = set(entry) - keys
    if extra:
        return [f"{where}: unknown key {sorted(extra)[0]!r}"]
    try:
        ec.validate_trigger(entry.get("trigger"), f"{where}.trigger")
    except ValueError as exc:
        return [str(exc)]
    trigger = entry["trigger"]
    if "signal" not in trigger:
        return [] if "bar_minutes" not in entry else [f"{where}.bar_minutes: a fixed time reads no bars"]
    reads = signals.needs_volume(trigger["signal"])
    if "volume" in entry:
        v = entry["volume"]
        if not reads:
            return [f"{where}.volume: the signal reads no volume"]
        if not (isinstance(v, str) and v.strip()):
            return [f"{where}.volume: the symbol whose volume weighs the bars"]
        if trigger.get("daily"):
            return [f"{where}.volume: a daily signal's bars carry no volume yet"]
        if _relative_volume_n(trigger["signal"]) > PRIOR_SESSIONS:
            return [f"{where}.trigger.signal: relative volume over more than {PRIOR_SESSIONS} sessions"]
    elif reads:
        return [f"{where}.trigger.signal: reads bar volume; declare `volume`, whose volume weighs the bars"]
    if trigger.get("daily"):
        return [] if "bar_minutes" not in entry else [f"{where}.bar_minutes: a daily signal reads daily bars"]
    bars = entry.get("bar_minutes")
    if not (isinstance(bars, int) and not isinstance(bars, bool) and bars > 0):
        return [f"{where}.bar_minutes: a signal trigger needs a positive number of minutes"]
    return []


TOP_KEYS = {
    "name",
    "symbol",
    "enabled",
    "group",
    "register",
    "start",
    "forward",
    "expiry",
    "far_expiry",
    "entry",
    "weekdays",
    "skip_events",
    "legs",
    "gates",
    "size",
    "limits",
    "exit",
    "note",
    "retired",
}
VERDICTS = ("passed", "failed", "no_verdict")
#: The most contracts one leg may hold per structure, either way (a 1-2-1 fly's middle is 2).
MAX_LEG_QUANTITY = 10

LIMIT_KEYS = {"max_open", "per_day", "every_days", "one_per_expiry"}

EXIT_KEYS = {
    "close_before_expiry_min",
    "profit_target_pct",
    "touch",
    "signal",
    "stop_credit_multiple",
    "stop_debit_pct",
    "stop_dollars",
    "profit_dollars",
    "trailing_stop",
    "avoid_events",
    "max_spread",
    "delta_limit",
    "close_at_dte",
}
#: Each strike rule's keys beyond `rule`, `match` and `round` ({key: required}).
_SHIFT = {"points": False, "pct": False}
STRIKE_KEYS = {
    "delta": {"target": True, "at_most": False},
    "premium": {"target": True, "price": True},
    "spot": _SHIFT,
    "prev_close": _SHIFT,
    "open": _SHIFT,
    "from_leg": {"leg": True, **_SHIFT},
    "same_as": {"leg": True},
    "match_width": {"from": True, "width_of": True},
    "expected_move": {"times": True, **_SHIFT},
    "std_dev": {"sd": True, **_SHIFT},
    "opening_range": {"minutes": True, "level": True, **_SHIFT},
    "entry_level": _SHIFT,
    "max_abs_gex": {"strikes": True},
    "gex_max": {"measure": True, "strikes": True},
}
STRIKE_RULES = set(STRIKE_KEYS)
EXPIRY_KEYS = {
    "dte": {"days": True, "tolerance": False},
    "market_days": {"days": True, "tolerance": False},
    "calendar": {"days": True, "tolerance": False},
    "at_least": {"days": True, "monthly": False},
    "between": {"min": True, "max": True, "prefer": False, "monthly": False},
}
#: The expiry keys that are true or false.
EXPIRY_FLAGS = ("monthly",)


def _whole(v, least: int = 0) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= least


def _number(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _expiry_problems(where: str, rule) -> list[str]:
    """An expiry rule's problems: its kind, its keys, whole day counts and a window that is not empty."""
    if not isinstance(rule, dict) or rule.get("kind") not in EXPIRY_KEYS:
        return [f"{where}.kind: {(rule or {}).get('kind') if isinstance(rule, dict) else rule!r}"]
    keys = EXPIRY_KEYS[rule["kind"]]
    p = [f"{where}: unknown key {k!r}" for k in sorted(set(rule) - set(keys) - {"kind"})]
    p += [f"{where}: missing {k!r}" for k, req in keys.items() if req and k not in rule]
    for k in ("days", "tolerance", "min", "max"):
        if k in rule and not _whole(rule[k]):
            p.append(f"{where}.{k}: a whole number of days, 0 or more")
    if rule["kind"] == "between" and _whole(rule.get("min")) and _whole(rule.get("max")):
        if rule["min"] > rule["max"]:
            p.append(f"{where}: min is after max")
    if rule.get("prefer") not in (None, "first", "last"):
        p.append(f"{where}.prefer: first or last")
    for k in EXPIRY_FLAGS:
        if k in rule and not isinstance(rule[k], bool):
            p.append(f"{where}.{k}: true or false")
    return p


#: The far expiry's rule: any `expiry` rule (counted from the session), or `after_near`: the first
#: listed expiry at least `days` calendar days after the near one.
AFTER_NEAR_KEYS = {"days": True, "monthly": False}


def _far_problems(name: str, arm: dict) -> list[str]:
    """`far_expiry` and the legs on it: a far leg needs the rule, the rule needs a far leg, at least one
    leg stays near, every far leg is bought (a sold one's risk is not bounded by the strikes, so the
    engine could not size it), and the position closes before its near expiry
    (`close_before_expiry_min`): a far leg cannot be settled with the near ones."""
    legs = [g for g in (internal_leg(raw)[0] for raw in arm.get("legs") or []) if g is not None]
    far = [g for g in legs if g.get("expiry") == "far"]
    rule = arm.get("far_expiry")
    p: list[str] = []
    if rule is not None:
        if isinstance(rule, dict) and rule.get("kind") == "after_near":
            p += [
                f"{name}.far_expiry: unknown key {k!r}"
                for k in sorted(set(rule) - set(AFTER_NEAR_KEYS) - {"kind"})
            ]
            if not _whole(rule.get("days"), 1):
                p.append(f"{name}.far_expiry.days: a whole number of days, 1 or more")
            if "monthly" in rule and not isinstance(rule["monthly"], bool):
                p.append(f"{name}.far_expiry.monthly: true or false")
        else:
            p.extend(_expiry_problems(f"{name}.far_expiry", rule))
        if not far:
            p.append(f"{name}.far_expiry: no leg is on the far expiry")
    if not far:
        return p
    if rule is None:
        p.append(f"{name}.far_expiry: required, a leg is on the far expiry")
    if len(far) == len(legs):
        p.append(f"{name}.legs: at least one leg on the near expiry")
    for g in far:
        if isinstance(g.get("side"), int) and g["side"] < 0:
            p.append(f"{name}.legs: {g.get('name')!r} is sold on the far expiry; a far leg is bought")
    if (arm.get("exit") or {}).get("close_before_expiry_min") is None:
        p.append(
            f"{name}.exit.close_before_expiry_min: required with a far leg, to close before the near expiry"
        )
    return p


def _strike_problems(where: str, rule, daily_entry: bool) -> list[str]:
    """A strike rule's problems: its kind, its keys, the match mode it takes, and its values' types."""

    if not isinstance(rule, dict) or rule.get("rule") not in STRIKE_KEYS:
        return [f"{where}.rule: {(rule or {}).get('rule') if isinstance(rule, dict) else rule!r}"]
    kind, keys = rule["rule"], STRIKE_KEYS[rule["rule"]]
    p = [f"{where}: unknown key {k!r}" for k in sorted(set(rule) - set(keys) - {"rule", "match", "round"})]
    p += [f"{where}: missing {k!r}" for k, req in keys.items() if req and k not in rule]
    if "points" in rule and "pct" in rule:
        p.append(f"{where}: points or pct, not both")
    for k in ("points", "pct", "target", "times", "sd"):
        if k in rule and not _number(rule[k]):
            p.append(f"{where}.{k}: a number")
    if "match" in rule and "round" in rule:
        p.append(f"{where}: match or round, not both")
    if "round" in rule and rule["round"] not in st.ROUND_ALIAS:
        p.append(f"{where}.round: {rule['round']!r}")
    if kind in st.GEX_RULES or kind == "same_as":
        if "match" in rule or "round" in rule:
            p.append(f"{where}: {kind} places the listed strike itself and takes no match mode")
    else:
        allowed = st.VALUE_MATCHES if kind in st.VALUE_RULES else st.MATCHES
        default = st.VALUE_RULES.get(kind) if kind in st.VALUE_RULES else st.PRICE_RULES.get(kind)
        m = st.match_of(rule)
        if default is None and "match" not in rule:
            p.append(f"{where}.match: required for {kind} ({', '.join(allowed)})")
        elif m not in allowed:
            p.append(f"{where}.match: {m!r} ({', '.join(allowed)})")
        if kind in st.VALUE_RULES and "round" in rule:
            p.append(f"{where}: {kind} takes match, not round")
    if kind in ("delta", "premium") and "target" in rule and _number(rule["target"]) and rule["target"] <= 0:
        p.append(f"{where}.target: above zero")
    if kind == "premium" and "price" in rule and rule["price"] not in st.PRICES:
        p.append(f"{where}.price: {', '.join(st.PRICES)}")
    if kind == "opening_range":
        if "minutes" in rule and not _whole(rule["minutes"], 1):
            p.append(f"{where}.minutes: a whole number of at least 1")
        if "level" in rule and rule["level"] not in st.OR_LEVELS:
            p.append(f"{where}.level: {', '.join(st.OR_LEVELS)}")
    if kind in st.GEX_RULES and "strikes" in rule and not _whole(rule["strikes"], 1):
        p.append(f"{where}.strikes: a whole number of at least 1")
    if kind == "gex_max" and "measure" in rule and rule["measure"] not in st.GEX_MEASURES:
        p.append(f"{where}.measure: {', '.join(st.GEX_MEASURES)}")
    if kind == "entry_level" and daily_entry:
        p.append(f"{where}: entry_level reads the minute the entry fired, which a daily trigger has not")
    return p


#: A leg's two spellings. Written (the Builder's, the shipped automations', and what `written_leg`
#: converts an older one to): the order that opens it, its contracts and Put or Call, the page's own
#: words. Older (still read, for good: configs, backups, replay records and ledgers hold it): `side` is
#: the contracts signed (+ bought, - sold) and `right` is "C" or "P". Every leg is read into the older
#: form (`internal_leg`), which is what the engine, the ledger and the block hash see, so an automation
#: hashes the same in either spelling and none loses its backtest label for being converted.
ACTIONS = {"BTO": 1, "STO": -1}
LEG_SIDES = {"put": "P", "call": "C"}
WRITTEN_LEG_KEYS = {"name", "action", "qty", "side", "strike", "expiry"}
OLDER_LEG_KEYS = {"name", "right", "side", "strike", "expiry"}
#: Which of the automation's expiries a leg sits on: `near` (its `expiry`, the default) or `far` (its
#: `far_expiry`). Read as `near` when absent, and kept only as `far`, so a one-expiry automation's legs
#: (and its block hash) are as they were.
LEG_EXPIRIES = ("near", "far")


def _quantity_ok(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= MAX_LEG_QUANTITY


def internal_leg(leg, where: str = "leg") -> tuple[dict | None, list[str]]:
    """`leg` in the form the engine reads ({name, right, side: signed contracts, strike}), from either
    spelling, or (None, problems). A leg is one spelling or the other: `action` with an integer
    `side`, or `right` beside `action`, is refused rather than guessed."""
    if not isinstance(leg, dict):
        return None, [f"{where}: a leg is an object"]
    if "action" in leg or "qty" in leg:
        p = [f"{where}: unknown key {k!r}" for k in sorted(set(leg) - WRITTEN_LEG_KEYS)]
        if "right" in leg:
            p.append(f"{where}: a leg is action/qty/side or the older side/right, not both")
        if leg.get("action") not in ACTIONS:
            p.append(f"{where}.action: {' or '.join(ACTIONS)}")
        if leg.get("side") not in LEG_SIDES:
            p.append(f"{where}.side: {' or '.join(LEG_SIDES)}")
        qty = leg.get("qty", 1)
        if not _quantity_ok(qty):
            p.append(f"{where}.qty: a whole number of contracts, 1 to {MAX_LEG_QUANTITY}")
        p += _leg_expiry_problems(where, leg)
        if p:
            return None, p
        signed = ACTIONS[leg["action"]] * qty
        return _far({"name": leg.get("name", ""), "right": LEG_SIDES[leg["side"]], "side": signed,
                     "strike": leg.get("strike")}, leg), []  # fmt: skip
    p = [f"{where}: unknown key {k!r}" for k in sorted(set(leg) - OLDER_LEG_KEYS)]
    side = leg.get("side")
    if leg.get("right") not in ("C", "P"):
        p.append(f"{where}: right/side (action BTO|STO, qty, side put|call)")
    elif not (isinstance(side, int) and not isinstance(side, bool) and _quantity_ok(abs(side))):
        p.append(
            f"{where}: right/side (side: contracts, + bought, - sold, 1 to {MAX_LEG_QUANTITY} either way)"
        )
    p += _leg_expiry_problems(where, leg)
    if p:
        return None, p
    return _far(
        {"name": leg.get("name", ""), "right": leg["right"], "side": side, "strike": leg.get("strike")}, leg
    ), []


def _leg_expiry_problems(where: str, leg: dict) -> list[str]:
    if "expiry" in leg and leg["expiry"] not in LEG_EXPIRIES:
        return [f"{where}.expiry: {' or '.join(LEG_EXPIRIES)}"]
    return []


def _far(out: dict, leg: dict) -> dict:
    """`out` with `"expiry": "far"` when `leg` sits on the far expiry; a near leg carries no key."""
    if leg.get("expiry") == "far":
        out["expiry"] = "far"
    return out


def written_leg(leg) -> dict:
    """`leg` in the written spelling (action, qty, side), from either; a leg that is neither comes back
    as it was, for the check to name."""
    got, problems = internal_leg(leg)
    if got is None or problems:
        return leg
    side = got["side"]
    return _far({"name": got["name"], "action": "STO" if side < 0 else "BTO", "qty": abs(side),
                 "side": "call" if got["right"] == "C" else "put", "strike": got["strike"]}, got)  # fmt: skip


def internal_legs(arm: dict) -> list:
    """Every leg of `arm` in the engine's form; one that cannot be read stays as it was (the check
    names it)."""
    out = []
    for leg in arm.get("legs") or []:
        got, _ = internal_leg(leg)
        out.append(got if got is not None else leg)
    return out


def _leg(name, right, side, **strike):
    """A shipped leg, written in the page's spelling: `side` here is the signed contracts."""
    return written_leg({"name": name, "right": right, "side": side, "strike": strike})


ARMS: dict[str, dict] = {
    "iron-fly-1000": {
        "name": "iron-fly-1000",
        "group": "ensemble",
        "register": {
            "item": 75,
            "verdict": "failed",
            "blocks": {
                "structure": "b60faefc74ce",
                "entry": "6f9f3168afcd",
                "gates": "9c2d48c2fc7f",
                "exit": "f39c6ec3b186",
                "size": "07dddd120c3d",
            },
        },
        "symbol": "SPX",
        "enabled": True,
        "note": "0DTE iron fly, short strikes at the listed strike nearest the previous close, 10 wide",
        "expiry": {"kind": "dte", "days": 0},
        "entry": {"kind": "time", "at": "10:00", "window": 5},
        "weekdays": [0, 2, 4],
        "skip_events": {"labels": ["PMI-Mfg", "PMI-Svcs", "Sentiment"], "through_expiry": False},
        "legs": [
            _leg("short_put", "P", -1, rule="prev_close", points=0.01, round="nearest"),
            _leg("short_call", "C", -1, rule="same_as", leg="short_put"),
            _leg("long_put", "P", 1, rule="from_leg", leg="short_put", points=-10),
            _leg("long_call", "C", 1, rule="from_leg", leg="short_call", points=10),
        ],
        "gates": [
            {"gate": "sd_change", "min": -1.1, "max": 1.1},
            {"gate": "expected_move_pct", "min": 0.3},
            {"gate": "mid_price", "max": 9.65},
            {"gate": "bid_ask", "max": 2.0},
        ],
        "size": {"max_risk": 250, "minimum": 1},
        "limits": {"max_open": 1, "per_day": 1},
        "exit": {},
    },
    "iron-condor-1345": {
        "name": "iron-condor-1345",
        "group": "ensemble",
        "register": {
            "item": 75,
            "verdict": "passed",
            "blocks": {
                "structure": "9452f5c99f07",
                "entry": "ce7039862191",
                "gates": "f7c01fdee99c",
                "exit": "f39c6ec3b186",
                "size": "8a8bf8cf3a5e",
            },
        },
        "symbol": "SPX",
        "enabled": True,
        "note": "0DTE iron condor, short put 5 below spot, short call 10 above it, 5-point wings",
        "expiry": {"kind": "dte", "days": 0},
        "entry": {"kind": "time", "at": "13:45", "window": 5},
        "weekdays": [0, 1, 3, 4],
        "skip_events": {"labels": [], "through_expiry": False},
        "legs": [
            _leg("short_put", "P", -1, rule="spot", points=-5, round="nearest"),
            _leg("long_put", "P", 1, rule="from_leg", leg="short_put", points=-5),
            _leg("short_call", "C", -1, rule="from_leg", leg="short_put", points=10),
            _leg("long_call", "C", 1, rule="from_leg", leg="short_call", points=5),
        ],
        "gates": [
            {"gate": "vix_level", "max": 20},
            {"gate": "above_ma", "kind": "ema", "n": 10},
            {"gate": "reward_risk", "min": 95},
        ],
        "size": {"contracts": 2},
        "limits": {"max_open": 1, "per_day": 1},
        "exit": {},
    },
    "put-spread-1300": {
        "name": "put-spread-1300",
        "group": "ensemble",
        "register": {
            "item": 75,
            "verdict": "no_verdict",
            "blocks": {
                "structure": "2c293415ad24",
                "entry": "82d9f7085ca2",
                "gates": "91348319510b",
                "exit": "f39c6ec3b186",
                "size": "8a8bf8cf3a5e",
            },
        },
        "symbol": "SPX",
        "enabled": True,
        "note": "0DTE put spread 10 wide, only when the opening-range arm holds nothing today",
        "expiry": {"kind": "dte", "days": 0},
        "entry": {"kind": "time", "at": "13:00", "window": 5},
        "weekdays": [0, 1, 4],
        "skip_events": {"labels": ["PMI-Svcs"], "through_expiry": False},
        "legs": [
            _leg("short_put", "P", -1, rule="spot", pct=-0.05, round="down"),
            _leg("long_put", "P", 1, rule="from_leg", leg="short_put", points=-10),
        ],
        "gates": [
            {"gate": "no_position_today", "arm": "orb-put-spread"},
            {"gate": "sd_change", "min": -0.5, "max": 2},
            {"gate": "rsi", "max": 70, "n": 14, "bars": 5},
            {"gate": "stoch", "min": 50, "k": 14, "k_smooth": 3, "d_smooth": 3, "bars": 5},
            {"gate": "gex_ratio", "strikes": 50, "mode": "nearest", "min": 1e-9},
            {"gate": "bid_ask", "max": 2.0},
        ],
        "size": {"contracts": 2},
        "limits": {"max_open": 1, "per_day": 1},
        "exit": {},
    },
    "iron-fly-1dte-1330": {
        "name": "iron-fly-1dte-1330",
        "group": "ensemble",
        "register": {
            "item": 75,
            "verdict": "failed",
            "blocks": {
                "structure": "6b38b077a81c",
                "entry": "019d05b96ddf",
                "gates": "0e13a9b583a9",
                "exit": "c1511297c7b0",
                "size": "76a8c81dc46d",
            },
        },
        "symbol": "SPX",
        "enabled": True,
        "note": "iron fly to the next market day on the largest-gex strike, wings 0.5% out",
        "expiry": {"kind": "market_days", "days": 1},
        "entry": {"kind": "time", "at": "13:30", "window": 5},
        "weekdays": [0, 1, 2, 3, 4],
        "skip_events": {"labels": ["PMI-Mfg", "PMI-Svcs"], "through_expiry": True},
        "legs": [
            _leg("short_put", "P", -1, rule="max_abs_gex", strikes=6),
            _leg("short_call", "C", -1, rule="same_as", leg="short_put"),
            _leg("long_put", "P", 1, rule="from_leg", leg="short_put", pct=-0.5, round="down"),
            _leg(
                "long_call",
                "C",
                1,
                rule="match_width",
                **{"from": "short_call"},
                width_of=["short_put", "long_put"],
            ),
        ],
        "gates": [
            {"gate": "gap_pct", "min": -0.75},
            {"gate": "above_ma", "kind": "sma", "n": 30},
            {"gate": "gex_max_otm", "strikes": 20, "side": "call", "max": 0.5},
            {"gate": "gex_max_otm", "strikes": 20, "side": "put", "max": 0.5},
            {"gate": "reward_risk", "min": 200},
        ],
        "size": {"contracts": 1},
        "limits": {"max_open": 2, "per_day": 1},
        "exit": {"profit_target_pct": 15, "touch": {"pct": -0.5}},
    },
    "gld-call-spread-20d": {
        "name": "gld-call-spread-20d",
        "group": "ensemble",
        "register": {
            "item": 75,
            "verdict": "failed",
            "blocks": {
                "structure": "5041cce75187",
                "entry": "f5f4691249dc",
                "gates": "1bd818e6386d",
                "exit": "d4824db8e2a1",
                "size": "2810045e08b1",
            },
        },
        "symbol": "GLD",
        "enabled": True,
        "note": "call debit spread about 20 days out, long 30 delta, 15 dollars wide",
        "expiry": {"kind": "calendar", "days": 20, "tolerance": 3},
        "entry": {"kind": "time", "at": "12:30", "window": 5},
        "weekdays": [0, 1, 2, 3, 4],
        "skip_events": {"labels": [], "through_expiry": False},
        "legs": [
            _leg("long_call", "C", 1, rule="delta", target=0.30),
            _leg("short_call", "C", -1, rule="from_leg", leg="long_call", points=15, round="nearest"),
        ],
        "gates": [{"gate": "reward_risk", "min": 450}],
        "size": {"contracts": 2},
        "limits": {"max_open": 2, "per_day": 1, "every_days": 14, "one_per_expiry": True},
        "exit": {"close_before_expiry_min": 60},
    },
    "long-put-1030": {
        "name": "long-put-1030",
        "group": "ensemble",
        "register": {
            "item": 75,
            "verdict": "passed",
            "blocks": {
                "structure": "b80e7dd96d57",
                "entry": "85c46f17a9ad",
                "gates": "311a7fcc18e6",
                "exit": "f39c6ec3b186",
                "size": "85ae4efc7b24",
            },
        },
        "symbol": "SPX",
        "enabled": True,
        "note": "0DTE long put at -0.10 delta or nearer zero, on a down-VIX up-index morning",
        "expiry": {"kind": "dte", "days": 0},
        "entry": {"kind": "time", "at": "10:30", "window": 5},
        "weekdays": [2, 3],
        "skip_events": {"labels": ["CPI", "OPEX"], "through_expiry": False},
        "legs": [_leg("long_put", "P", 1, rule="delta", target=0.10, at_most=True)],
        "gates": [
            {"gate": "vix_change_pct", "max": 0},
            {"gate": "change_pct", "min": 0},
        ],
        "size": {"max_risk": 300, "minimum": 1},
        "limits": {"max_open": 1, "per_day": 1},
        "exit": {},
    },
    "gld-call-spread-1dte": {
        "name": "gld-call-spread-1dte",
        "group": "ensemble",
        "register": {
            "item": 75,
            "verdict": "no_verdict",
            "blocks": {
                "structure": "81e234cb2aac",
                "entry": "38b534b7217e",
                "gates": "589547a2165d",
                "exit": "7a95eff72239",
                "size": "831a8620c40a",
            },
        },
        "symbol": "GLD",
        "enabled": True,
        "note": "call debit spread to the next market day, long 50 delta, 10 dollars wide",
        "expiry": {"kind": "market_days", "days": 1},
        "entry": {"kind": "time", "at": "12:20", "window": 5},
        "weekdays": [0, 1, 2, 3, 4],
        "skip_events": {"labels": ["FOMC", "CPI", "QuarterEnd"], "through_expiry": True},
        "legs": [
            _leg("long_call", "C", 1, rule="delta", target=0.50),
            _leg("short_call", "C", -1, rule="from_leg", leg="long_call", points=10, round="nearest"),
        ],
        "gates": [
            {"gate": "change_pct", "min": 0, "max": 0.7},
            {"gate": "reward_risk", "min": 200},
        ],
        "size": {"contracts": 3},
        "limits": {"max_open": 2, "per_day": 1},
        "exit": {"close_before_expiry_min": 15},
    },
    "orb-put-spread": {
        "name": "orb-put-spread",
        "group": "ensemble",
        "register": {
            "item": 75,
            "verdict": "failed",
            "blocks": {
                "structure": "74e437b6240d",
                "entry": "7cac6853f969",
                "gates": "75373db258e6",
                "exit": "49703ffd491a",
                "size": "a0616a0be36a",
            },
        },
        "symbol": "SPX",
        "enabled": True,
        "note": "0DTE put spread 20 wide at -0.35 delta on the first break above the 60-minute opening high",
        "expiry": {"kind": "dte", "days": 0},
        "entry": {"kind": "orb", "minutes": 60, "direction": "above", "latest": "13:00", "single_shot": True},
        "weekdays": [0, 1, 4],
        "skip_events": {"labels": ["FOMC", "MonthEnd", "FirstWeekly"], "through_expiry": False},
        "legs": [
            _leg("short_put", "P", -1, rule="delta", target=0.35),
            _leg("long_put", "P", 1, rule="from_leg", leg="short_put", points=-20),
        ],
        "gates": [
            {"gate": "gex_ratio", "strikes": 16, "mode": "nearest", "min": -1.5},
            {"gate": "gex_ratio", "strikes": 2, "mode": "nearest", "min": -1.5},
            {"gate": "gex_put_total", "strikes": 4, "mode": "above", "max": -90_000_000},
            {"gate": "mid_price", "min": 0.15},
            {"gate": "bid_ask", "max": 2.0},
        ],
        "size": {"contracts": 1},
        "limits": {"max_open": 1, "per_day": 1},
        "exit": {"touch": {"points": -10}},
    },
}


def validate(arm: dict) -> list[str]:
    """Problems with an arm, each naming its path. Empty when the arm is well formed."""
    p: list[str] = []
    name = arm.get("name", "?")
    for k in arm:
        if k not in TOP_KEYS:
            p.append(f"{name}: unknown key {k!r}")
    missing = [k for k in ("name", "symbol", "expiry", "entry", "weekdays", "legs", "size") if k not in arm]
    if missing:
        return p + [f"{name}: missing {k!r}" for k in missing]
    p.extend(_expiry_problems(f"{name}.expiry", arm["expiry"]))
    p.extend(_far_problems(name, arm))
    # A name, or null for no group: a change laid over a shipped automation takes it out of its group.
    g = arm.get("group")
    if g is not None and not (isinstance(g, str) and g.strip() == g != ""):
        p.append(f"{name}.group: a name, or null for none")
    retired = arm.get("retired", False)
    if retired is not False and not enrolment._iso(retired):
        p.append(f"{name}.retired: false, or the ISO date it was retired")
    reg = arm.get("register")
    if reg is not None and not (
        isinstance(reg, dict)
        and {"item", "verdict"} <= set(reg) <= {"item", "verdict", "blocks"}
        and isinstance(reg["item"], int)
        and not isinstance(reg["item"], bool)
        and reg["verdict"] in VERDICTS
    ):
        p.append(f"{name}.register: {{item: N, verdict: {'|'.join(VERDICTS)}}}")
    p.extend(enrolment.problems(arm))
    if arm["entry"].get("kind") not in ("time", "orb", "trigger"):
        p.append(f"{name}.entry.kind: {arm['entry'].get('kind')!r}")
    if arm["entry"].get("kind") == "trigger":
        p.extend(_trigger_problems(f"{name}.entry", arm["entry"]))
    if not all(isinstance(d, int) and 0 <= d <= 4 for d in arm["weekdays"]):
        p.append(f"{name}.weekdays: {arm['weekdays']!r}")
    seen: set[str] = set()
    for i, raw in enumerate(arm["legs"]):
        where = f"{name}.legs[{i}]"
        leg, leg_problems = internal_leg(raw, where)
        p.extend(leg_problems)
        if leg is None:
            seen.add(raw.get("name", "") if isinstance(raw, dict) else "")
            continue
        if leg.get("name", "") in seen:
            p.append(f"{where}.name: {leg.get('name')!r} is already a leg; each leg needs its own name")
        daily = arm["entry"].get("kind") == "trigger" and bool(
            (arm["entry"].get("trigger") or {}).get("daily")
        )
        p.extend(_strike_problems(f"{where}.strike", leg.get("strike"), daily))
        spec = leg.get("strike") if isinstance(leg.get("strike"), dict) else {}
        refs = [spec.get("leg"), spec.get("from")]
        if spec.get("rule") == "match_width" and isinstance(spec.get("width_of"), list):
            if len(spec["width_of"]) != 2:
                p.append(f"{where}.strike.width_of: two leg names")
            refs += spec["width_of"]
        for ref in refs:
            if ref is not None and ref not in seen:
                p.append(f"{where}: refers to {ref!r} before it is placed")
        seen.add(leg.get("name", ""))
    for i, g in enumerate(arm.get("gates", [])):
        if g.get("gate") not in gates_mod.REGISTRY:
            p.append(f"{name}.gates[{i}]: unknown gate {g.get('gate')!r}")
            continue
        try:
            ec.validate([gates_mod.check_of(g)], gates_mod.CONDITIONS, f"{name}.gates[{i}]")
        except ValueError as exc:
            p.append(str(exc).replace(f"{name}.gates[{i}][0]", f"{name}.gates[{i}]"))
    lim = arm.get("limits")
    if not isinstance(lim, dict):
        p.append(f"{name}.limits: required, with max_open and per_day")
    else:
        extra = set(lim) - LIMIT_KEYS
        if extra:
            p.append(f"{name}.limits: unknown key {sorted(extra)[0]!r}")
        mo = lim.get("max_open")
        if not (isinstance(mo, int) and not isinstance(mo, bool) and mo >= 1):
            p.append(f"{name}.limits.max_open: required, a whole number of at least 1")
        if lim.get("per_day") != 1 or isinstance(lim.get("per_day"), bool):
            p.append(
                f"{name}.limits.per_day: required, and 1 (the entry register keeps one row per arm and day)"
            )
    p += sizing.problems(f"{name}.size", arm["size"])
    for k in arm.get("exit", {}):
        if k not in EXIT_KEYS:
            p.append(f"{name}.exit: unknown key {k!r}")
    ex = arm.get("exit", {})
    for key, ok in (
        ("stop_credit_multiple", lambda v: _num(v) and v > 0),
        ("stop_debit_pct", lambda v: _num(v) and 0 < v <= 100),
        ("stop_dollars", lambda v: _num(v) and v > 0),
        ("profit_dollars", lambda v: _num(v) and v > 0),
        ("max_spread", lambda v: _num(v) and v > 0),
        ("trailing_stop", _trailing_ok),
        ("avoid_events", _avoid_ok),
        ("delta_limit", lambda v: _num(v) and 0 < v <= 4),
        ("close_at_dte", lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0),
    ):
        if key in ex and not ok(ex[key]):
            p.append(f"{name}.exit.{key}: {ex[key]!r}")
    if "signal" in arm.get("exit", {}):
        p += _trigger_problems(
            f"{name}.exit.signal", arm["exit"]["signal"], frozenset({"trigger", "bar_minutes", "volume"})
        )
    return p


def _num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _trailing_ok(v) -> bool:
    """In dollars (`dollars`, `activate_dollars`) or in percent of the trade's max profit (`pct` above 0
    and at most 100, `activate_pct` 0 or more), never the two mixed."""
    if not isinstance(v, dict):
        return False
    if set(v) <= {"dollars", "activate_dollars"}:
        return (
            _num(v.get("dollars"))
            and v["dollars"] > 0
            and (_num(v.get("activate_dollars", 0)) and v.get("activate_dollars", 0) >= 0)
        )
    if set(v) <= {"pct", "activate_pct"}:
        return (
            _num(v.get("pct"))
            and 0 < v["pct"] <= 100
            and (_num(v.get("activate_pct", 0)) and v.get("activate_pct", 0) >= 0)
        )
    return False


def _avoid_ok(v) -> bool:
    if not isinstance(v, dict) or not set(v) <= {"labels", "before_close_min"}:
        return False
    labels, n = v.get("labels"), v.get("before_close_min", 60)
    return (
        isinstance(labels, list)
        and bool(labels)
        and all(isinstance(x, str) and x for x in labels)
        and isinstance(n, int)
        and not isinstance(n, bool)
        and 0 <= n <= 390
    )


#: Completed sessions a daily signal is given: room for a 200-day average and its warm-up.
DAILY_HISTORY = 300


def reads_daily(arm: dict) -> bool:
    """Whether the arm's entry trigger or its signal exit reads daily bars (`"daily": true`)."""
    entry = arm.get("entry") or {}
    exit_signal = (arm.get("exit") or {}).get("signal") or {}
    return any(
        isinstance(t, dict) and bool(t.get("daily"))
        for t in (
            (entry.get("trigger") if entry.get("kind") == "trigger" else None),
            exit_signal.get("trigger"),
        )
    )


#: Earlier sessions an intraday signal is given beside today's, so it reads them as a signal study over
#: the whole history does: a seeded indicator is warm at the open, arming carries across the close, and
#: a gap or prior-session signal has the prior session. Paper keeps about seven sessions of trail.
PRIOR_SESSIONS = 5


def _triggers(arm: dict) -> list[dict]:
    entry = arm.get("entry") or {}
    out = [entry.get("trigger")] if entry.get("kind") == "trigger" else []
    out.append(((arm.get("exit") or {}).get("signal") or {}).get("trigger"))
    return [t for t in out if isinstance(t, dict)]


def _relative_volume_n(spec: dict) -> int:
    srcs = [spec.get(k) for k in ("a", "b")]
    n = max([0, *(s["n"] for s in srcs if isinstance(s, dict) and s.get("name") == "relative_volume")])
    return max([n, *(_relative_volume_n(o) for o in spec.get("only_when", ()))])


def volume_symbols(arm: dict) -> set[str]:
    """The symbols whose one-minute volume the arm's signals read (`volume` on its trigger entry or its
    signal exit), upper-cased."""
    out = set()
    e = arm.get("entry") or {}
    if e.get("kind") == "trigger" and e.get("volume"):
        out.add(str(e["volume"]).upper())
    sig = (arm.get("exit") or {}).get("signal") or {}
    if sig.get("volume"):
        out.add(str(sig["volume"]).upper())
    return out


def reads_prior_trail(arm: dict) -> bool:
    """Whether an intraday signal trigger (entry or signal exit, not daily) reads earlier sessions."""
    return any("signal" in t and not t.get("daily") for t in _triggers(arm))


def load_arms(overrides: dict | None = None) -> dict[str, dict]:
    """The shipped arms with the machine's changes laid over them: `overrides` maps an arm name to a dict
    merged over the shipped arm (top-level keys replaced), or a whole new arm. Every arm is validated;
    any problem raises, so a bad edit never runs as a different arm."""
    arms = copy.deepcopy(ARMS)
    for name, change in (overrides or {}).items():
        if name.startswith("_"):
            continue
        arms[name] = {**arms.get(name, {"name": name}), **change}
    for a in arms.values():
        # Either spelling in, the engine's out: what the loop, the ledger and the block hash read.
        if isinstance(a.get("legs"), list):
            a["legs"] = internal_legs(a)
    problems = [x for a in arms.values() for x in validate(a)]
    if problems:
        raise ValueError("; ".join(problems))
    for a in arms.values():
        if a.get("retired"):
            a["enabled"] = False  # retired is off, whatever `enabled` says
    return arms
