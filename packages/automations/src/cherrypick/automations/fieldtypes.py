"""Each key's type, for the Builder's form: which input a field draws and what it refuses before Check.

A type is a dict: `{"type": T, ...}` with T one of

    int       a whole number; `min`, `max` bound it (inclusive), `or` lists words it also takes
    number    any number; `min`, `max` inclusive, `above` an exclusive lower bound
    time      "HH:MM", Eastern
    bool      true or false
    enum      one of `options`
    leg       the name of a leg placed before this one
    json      an object or a list, typed as JSON (a touch, a trailing stop, an indicator)
    text      words (a symbol)

and `default`, the value the engine reads when the key is absent, where it has one. The form shows a
default as a placeholder and never writes it: a written default would change the block's hash.

The automation's own keys are declared here beside the check that enforces each one (`arms`, `sizing`,
`strikes`, `gates`); `tests/test_fieldtypes.py` probes every declared type against that check, so a type
here that the engine would refuse, or one it would accept that the type forbids, fails there. A core
signal's parameters are read from core's own validators (`signal_types`), not declared.
"""

from __future__ import annotations

from collections.abc import Callable

from cherrypick.core import signals

from . import gates as gates_mod
from . import strikes as st

INT0 = {"type": "int", "min": 0}
INT1 = {"type": "int", "min": 1}
NUM = {"type": "number"}
POS = {"type": "number", "above": 0}
TIME = {"type": "time"}
BOOL = {"type": "bool"}
JSON = {"type": "json"}


def _enum(options, default=None) -> dict:
    out = {"type": "enum", "options": list(options)}
    return out if default is None else {**out, "default": default}


def _d(t: dict, default) -> dict:
    return {**t, "default": default}


EXPIRY = {
    "days": INT0,
    "tolerance": INT0,
    "min": INT0,
    "max": INT0,
    "prefer": _enum(("first", "last"), "first"),
    "monthly": BOOL,
}
#: Each entry kind's keys; `trigger` is the trigger's own (`TRIGGER`).
ENTRY = {
    "time": {"at": TIME, "window": _d(INT1, 5)},
    "orb": {
        "minutes": INT1,
        "direction": _enum(("above", "below")),
        "latest": TIME,
        "single_shot": _d(BOOL, True),
    },
    "trigger": {"bar_minutes": INT1, "volume": {"type": "text"}},
}
#: A trigger's keys (`core.entrychecks.validate_trigger`): `within` is null for until the window closes.
TRIGGER = {"at": TIME, "within": INT1, "open": TIME, "close": TIME, "daily": BOOL, "developing": BOOL}
STRIKE = {
    "target": POS,
    "at_most": NUM,
    "price": _enum(st.PRICES),
    "points": NUM,
    "pct": NUM,
    "leg": {"type": "leg"},
    "from": {"type": "leg"},
    "width_of": JSON,
    "times": NUM,
    "sd": NUM,
    "minutes": INT1,
    "level": _enum(st.OR_LEVELS),
    "strikes": INT1,
    "measure": _enum(st.GEX_MEASURES),
}
#: Each gate's parameters, by what its function reads; a default is the function's own.
GATE_PARAMS = {
    "min": NUM,
    "max": NUM,
    "n": INT1,
    "bars": INT1,
    "k": INT1,
    "k_smooth": INT1,
    "d_smooth": INT1,
    "lookback": INT1,
    "strikes": INT1,
    "kind": _enum(("sma", "ema")),
    "mode": _enum(("nearest", "above", "below")),
    "side": _enum(("call", "put")),
    "arm": {"type": "text"},
}
EXIT = {
    "close_before_expiry_min": INT0,
    "profit_target_pct": POS,
    "touch": JSON,
    "signal": JSON,
    "stop_credit_multiple": POS,
    "stop_debit_pct": {"type": "number", "above": 0, "max": 100},
    "stop_dollars": POS,
    "profit_dollars": POS,
    "trailing_stop": JSON,
    "avoid_events": JSON,
    "max_spread": POS,
    "delta_limit": {"type": "number", "above": 0, "max": 4},
    "close_at_dte": INT0,
}
SIZE = {
    "contracts": INT1,
    "max_risk": POS,
    "minimum": INT0,
    "allocation_pct": {"type": "number", "above": 0, "max": 100},
    "allocation": POS,
    "max_dollars": POS,
    "max_contracts": INT1,
    "progression": JSON,
}
LIMITS = {
    "max_open": INT1,
    "per_day": {"type": "int", "min": 1, "max": 1},
    "every_days": INT1,
    "one_per_expiry": BOOL,
}


def probe(check: Callable[[object], bool]) -> dict:
    """A core validator's type, read by trying values on it: an object (an indicator, which a number may
    stand in for), a flag, a choice of words, a time, a whole number (with its least value, and any word
    it also takes), a number (with its bound at zero), or words."""

    def ok(v) -> bool:
        try:
            return bool(check(v))
        except Exception:  # noqa: BLE001 - a validator that cannot read a value refuses it
            return False

    if ok({}):
        return dict(JSON)
    if ok(True) and ok(False) and not ok(1):
        return dict(BOOL)
    words = [
        v
        for cell in (getattr(check, "__closure__", None) or ())
        if isinstance(cell.cell_contents, tuple)
        for v in cell.cell_contents
        if isinstance(v, str)
    ]
    if words and all(ok(w) for w in words):
        return _enum(words)
    if ok("10:30") and not ok("25:99"):
        return dict(TIME)
    if ok(2) and not ok(2.5):
        least = 0 if ok(0) else 1
        out = {"type": "int", "min": least}
        return {**out, "or": ["session"]} if ok("session") else out
    if ok(2.5):
        if ok(-1):
            return dict(NUM)
        return {"type": "number", "min": 0} if ok(0) else dict(POS)
    if ok("x"):
        return {"type": "text"}
    return dict(JSON)


def signal_types(event_labels=()) -> dict:
    """Each core signal kind's parameters and the rules any kind takes, typed from core's validators and
    marked `required` as core requires them. A candle's pattern is one of core's patterns, and an event
    signal's event one of the event labels."""
    kinds = {
        k: {
            **{p: {**probe(fn), "required": True} for p, fn in kind.params.items()},
            **{p: {**probe(fn), "required": False} for p, fn in signals.OPTIONAL.get(k, {}).items()},
        }
        for k, kind in signals.KINDS.items()
    }
    kinds["candle"]["pattern"] = {**_enum(signals.CANDLES), "required": True}
    if event_labels:
        kinds["event"]["event"] = {**_enum(event_labels), "required": True}
    rules = {r: {**probe(fn), "required": False} for r, fn in signals._RULES.items()}
    return {"kinds": kinds, "rules": rules}


def gate_types() -> dict:
    """Each gate's parameters typed, with the function's default where it has one."""
    import inspect

    out = {}
    for name, (_, fn) in gates_mod.REGISTRY.items():
        params = list(inspect.signature(fn).parameters.values())[1:]
        out[name] = {
            p.name: (
                GATE_PARAMS[p.name]
                if p.default in (inspect.Parameter.empty, None)
                else _d(GATE_PARAMS[p.name], p.default)
            )
            for p in params
        }
    return out


def types(event_labels=()) -> dict:
    """Every typed key the form draws, grouped as the spec holds them."""
    return {
        "expiry": EXPIRY,
        "entry": ENTRY,
        "trigger": TRIGGER,
        "signals": signal_types(event_labels),
        "strike": STRIKE,
        "gates": gate_types(),
        "exit": EXIT,
        "size": SIZE,
        "limits": LIMITS,
    }
