"""The arms: one configured variant each, pure data. A new arm is a new entry here (or in the machine's
config, which `load_arms` reads over these) and, if it needs a gate or strike rule the registries lack,
one function there.

An arm's keys (all validated by `validate`; an unknown key is refused with its path, because a misspelt key
that defaulted silently would change what is measured):

    name, symbol, enabled
    expiry        {"kind": "dte"|"market_days"|"calendar", "days": N, "tolerance": D}
    entry         {"kind": "time", "at": "HH:MM", "window": minutes}
                  {"kind": "orb", "minutes": 60, "direction": "above", "latest": "HH:MM", "single_shot": bool}
    weekdays      [0..4], Monday = 0
    skip_events   {"labels": [...], "through_expiry": bool}
    legs          [{"name", "right": "C"|"P", "side": 1|-1, "strike": <strikes rule>}] in placement order
    gates         [{"gate": <gates.REGISTRY name>, ...}]
    size          {"contracts": N} or {"max_risk": dollars}
    limits        {"max_open": N, "every_days": N, "one_per_expiry": bool}
    exit          {} (hold to settlement) with any of "close_before_expiry_min", "profit_target_pct",
                  "touch": {"pct": -0.5} or {"points": -10}
"""

from __future__ import annotations

import copy

from . import gates as gates_mod

TOP_KEYS = {
    "name",
    "symbol",
    "enabled",
    "expiry",
    "entry",
    "weekdays",
    "skip_events",
    "legs",
    "gates",
    "size",
    "limits",
    "exit",
    "note",
}
EXIT_KEYS = {"close_before_expiry_min", "profit_target_pct", "touch"}
STRIKE_RULES = {"delta", "spot", "prev_close", "from_leg", "same_as", "match_width", "max_abs_gex"}


def _leg(name, right, side, **strike):
    return {"name": name, "right": right, "side": side, "strike": strike}


ARMS: dict[str, dict] = {
    "iron-fly-1000": {
        "name": "iron-fly-1000",
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
        "limits": {"max_open": 1},
        "exit": {},
    },
    "iron-condor-1345": {
        "name": "iron-condor-1345",
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
        "limits": {"max_open": 1},
        "exit": {},
    },
    "put-spread-1300": {
        "name": "put-spread-1300",
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
        "limits": {"max_open": 1},
        "exit": {},
    },
    "iron-fly-1dte-1330": {
        "name": "iron-fly-1dte-1330",
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
        "limits": {"max_open": 2},
        "exit": {"profit_target_pct": 15, "touch": {"pct": -0.5}},
    },
    "gld-call-spread-20d": {
        "name": "gld-call-spread-20d",
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
        "limits": {"max_open": 2, "every_days": 14, "one_per_expiry": True},
        "exit": {"close_before_expiry_min": 60},
    },
    "long-put-1030": {
        "name": "long-put-1030",
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
        "limits": {"max_open": 1},
        "exit": {},
    },
    "gld-call-spread-1dte": {
        "name": "gld-call-spread-1dte",
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
        "limits": {"max_open": 2},
        "exit": {"close_before_expiry_min": 15},
    },
    "orb-put-spread": {
        "name": "orb-put-spread",
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
        "limits": {"max_open": 1},
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
    if arm["expiry"].get("kind") not in ("dte", "market_days", "calendar"):
        p.append(f"{name}.expiry.kind: {arm['expiry'].get('kind')!r}")
    if arm["entry"].get("kind") not in ("time", "orb"):
        p.append(f"{name}.entry.kind: {arm['entry'].get('kind')!r}")
    if not all(isinstance(d, int) and 0 <= d <= 4 for d in arm["weekdays"]):
        p.append(f"{name}.weekdays: {arm['weekdays']!r}")
    seen: set[str] = set()
    for i, leg in enumerate(arm["legs"]):
        where = f"{name}.legs[{i}]"
        if leg.get("right") not in ("C", "P") or leg.get("side") not in (1, -1):
            p.append(f"{where}: right/side")
        rule = leg.get("strike", {}).get("rule")
        if rule not in STRIKE_RULES:
            p.append(f"{where}.strike.rule: {rule!r}")
        for ref in (leg["strike"].get(x) for x in ("leg", "from")):
            if ref is not None and ref not in seen:
                p.append(f"{where}: refers to {ref!r} before it is placed")
        seen.add(leg.get("name", ""))
    for i, g in enumerate(arm.get("gates", [])):
        if g.get("gate") not in gates_mod.REGISTRY:
            p.append(f"{name}.gates[{i}]: unknown gate {g.get('gate')!r}")
    size = arm["size"]
    if not ({"contracts"} == set(size) or set(size) in ({"max_risk"}, {"max_risk", "minimum"})):
        p.append(f"{name}.size: {size!r}")
    for k in arm.get("exit", {}):
        if k not in EXIT_KEYS:
            p.append(f"{name}.exit: unknown key {k!r}")
    return p


def load_arms(overrides: dict | None = None) -> dict[str, dict]:
    """The shipped arms with the machine's changes laid over them: `overrides` maps an arm name to a dict
    merged over the shipped arm (top-level keys replaced), or a whole new arm. Every arm is validated;
    any problem raises, so a bad edit never runs as a different arm."""
    arms = copy.deepcopy(ARMS)
    for name, change in (overrides or {}).items():
        if name.startswith("_"):
            continue
        arms[name] = {**arms.get(name, {"name": name}), **change}
    problems = [x for a in arms.values() for x in validate(a)]
    if problems:
        raise ValueError("; ".join(problems))
    return arms
