"""The arms: one configured variant each, pure data. A new arm is a new entry here (or in the machine's
config, which `load_arms` reads over these) and, if it needs a gate or strike rule the registries lack,
one function there.

An arm's keys (all validated by `validate`; an unknown key is refused with its path, because a misspelt key
that defaulted silently would change what is measured):

    name, symbol, enabled
    expiry        {"kind": "dte"|"market_days"|"calendar", "days": N, "tolerance": D}
    entry         {"kind": "time", "at": "HH:MM", "window": minutes}
                  {"kind": "orb", "minutes": 60, "direction": "above", "latest": "HH:MM", "single_shot": bool}
                  {"kind": "trigger", "trigger": <core.entrychecks trigger>, "bar_minutes": N}
                  (a `core.signals` firing on the trail cut into N-minute bars, or a fixed time; an
                  event signal reads the event calendar's dates, `engine.events_for`; a trigger with
                  `"daily": true` reads completed daily bars and takes no `bar_minutes`)
    weekdays      [0..4], Monday = 0
    skip_events   {"labels": [...], "through_expiry": bool}
    legs          [{"name", "right": "C"|"P", "side": 1|-1, "strike": <strikes rule>}] in placement order
    gates         [{"gate": <gates.REGISTRY name>, ...}]
    size          {"contracts": N} or {"max_risk": dollars}
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
                  "trailing_stop": {"dollars": d, "activate_dollars": a},
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

from . import gates as gates_mod


def _trigger_problems(
    where: str, entry: dict, keys: frozenset = frozenset({"kind", "trigger", "bar_minutes"})
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
    if signals.needs_volume(trigger["signal"]):
        return [f"{where}.trigger.signal: reads bar volume, and this module's bars carry none"]
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
        "limits": {"max_open": 1, "per_day": 1},
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
        "limits": {"max_open": 1, "per_day": 1},
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
        "limits": {"max_open": 1, "per_day": 1},
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
        "limits": {"max_open": 2, "per_day": 1},
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
        "limits": {"max_open": 2, "per_day": 1, "every_days": 14, "one_per_expiry": True},
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
        "limits": {"max_open": 1, "per_day": 1},
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
        "limits": {"max_open": 2, "per_day": 1},
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
    if arm["expiry"].get("kind") not in ("dte", "market_days", "calendar"):
        p.append(f"{name}.expiry.kind: {arm['expiry'].get('kind')!r}")
    if arm["entry"].get("kind") not in ("time", "orb", "trigger"):
        p.append(f"{name}.entry.kind: {arm['entry'].get('kind')!r}")
    if arm["entry"].get("kind") == "trigger":
        p.extend(_trigger_problems(f"{name}.entry", arm["entry"]))
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
    size = arm["size"]
    if not ({"contracts"} == set(size) or set(size) in ({"max_risk"}, {"max_risk", "minimum"})):
        p.append(f"{name}.size: {size!r}")
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
            f"{name}.exit.signal", arm["exit"]["signal"], frozenset({"trigger", "bar_minutes"})
        )
    return p


def _num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _trailing_ok(v) -> bool:
    return (
        isinstance(v, dict)
        and set(v) <= {"dollars", "activate_dollars"}
        and _num(v.get("dollars"))
        and v["dollars"] > 0
        and (_num(v.get("activate_dollars", 0)) and v.get("activate_dollars", 0) >= 0)
    )


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
    problems = [x for a in arms.values() for x in validate(a)]
    if problems:
        raise ValueError("; ".join(problems))
    return arms
