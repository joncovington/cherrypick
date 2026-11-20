"""The Builder's field types agree with the engine's checks: every value a type allows, the check takes,
and every value it forbids, the check refuses. Keys the engine reads without checking are listed, so a
new key is a decision, not a silence."""

from __future__ import annotations

import pytest
from cherrypick.core import entrychecks as ec
from cherrypick.core import signals

from cherrypick.automations import arms, builderform, fieldtypes, gates, sizing


def samples(t: dict) -> tuple[list, list]:
    """Values the type allows, and values it forbids."""
    kind = t["type"]
    if kind == "int":
        lo, hi = t.get("min", 0), t.get("max")
        good = [lo] + ([lo + 1] if hi is None or lo + 1 <= hi else [])
        bad = [lo - 1, lo + 0.5, str(lo), True] + ([hi + 1] if hi is not None else [])
        return good, bad
    if kind == "number":
        lo = t.get("above", t.get("min", -5))
        hi = t.get("max")
        good = [lo + 0.5] + ([t["min"]] if "min" in t else []) + ([hi] if hi is not None else [])
        bad = ["1", True] + ([t["above"]] if "above" in t else []) + ([t["min"] - 1] if "min" in t else [])
        return good, bad + ([hi + 1] if hi is not None else [])
    if kind == "time":
        return ["10:30", "00:00"], ["25:00", "1030", 1030]
    if kind == "bool":
        return [True, False], ["true", 1]
    if kind == "enum":
        return list(t["options"]), ["not-one-of-them"]
    return [], []


def agree(t: dict, refused) -> None:
    good, bad = samples(t)
    assert good or t["type"] in ("leg", "json", "text")
    for v in good:
        assert not refused(v), f"{t} allows {v!r}, the engine refuses it"
    for v in bad:
        assert refused(v), f"{t} forbids {v!r}, the engine takes it"


def _path(problems: list[str], key: str) -> bool:
    return any(f".{key}:" in p or f".{key} " in p for p in problems)


EXPIRY_BASE = {"days": {"kind": "dte", "days": 1}, "tolerance": {"kind": "dte", "days": 1}}


@pytest.mark.parametrize("key", sorted(fieldtypes.EXPIRY))
def test_expiry_types_agree_with_the_expiry_check(key):
    base = EXPIRY_BASE.get(key, {"kind": "between", "min": 0, "max": 1000})
    agree(fieldtypes.EXPIRY[key], lambda v: _path(arms._expiry_problems("x", {**base, key: v}), key))


STRIKE_BASE = {
    "target": {"rule": "delta"},
    "price": {"rule": "premium", "target": 1.0},
    "points": {"rule": "spot"},
    "pct": {"rule": "spot"},
    "times": {"rule": "expected_move"},
    "sd": {"rule": "std_dev"},
    "minutes": {"rule": "opening_range", "level": "high", "match": "nearest"},
    "level": {"rule": "opening_range", "minutes": 30, "match": "nearest"},
    "strikes": {"rule": "max_abs_gex"},
    "measure": {"rule": "gex_max", "strikes": 5},
}
#: Read by the strike rules, not checked by type: a leg name (checked as placed before), a pair of them,
#: and delta's cap.
STRIKE_UNCHECKED = {"leg", "from", "width_of", "at_most"}


def test_every_strike_key_is_typed_and_either_probed_or_listed_as_unchecked():
    keys = {k for ks in arms.STRIKE_KEYS.values() for k in ks}
    assert keys == set(fieldtypes.STRIKE)
    assert set(STRIKE_BASE) | STRIKE_UNCHECKED == keys and not set(STRIKE_BASE) & STRIKE_UNCHECKED


@pytest.mark.parametrize("key", sorted(STRIKE_BASE))
def test_strike_types_agree_with_the_strike_check(key):
    base = {"match": "nearest", **STRIKE_BASE[key]}
    if base["rule"] in ("max_abs_gex", "gex_max"):
        base.pop("match")
    agree(fieldtypes.STRIKE[key], lambda v: _path(arms._strike_problems("x", {**base, key: v}, False), key))


#: Exit keys the engine reads without a type check (`engine.manage`), and the objects checked whole.
EXIT_UNCHECKED = {
    "close_before_expiry_min",
    "profit_target_pct",
    "touch",
    "signal",
    "trailing_stop",
    "avoid_events",
}


def _arm(**over) -> dict:
    a = {**arms.ARMS["iron-condor-1345"], **over}
    a.pop("register", None)
    return a


def test_every_exit_key_is_typed():
    assert set(fieldtypes.EXIT) == arms.EXIT_KEYS


@pytest.mark.parametrize("key", sorted(arms.EXIT_KEYS - EXIT_UNCHECKED))
def test_exit_types_agree_with_the_exit_check(key):
    agree(fieldtypes.EXIT[key], lambda v: _path(arms.validate(_arm(exit={key: v})), key))


@pytest.mark.parametrize("key", ["max_open", "per_day"])
def test_limit_types_agree_with_the_limit_check(key):
    assert set(fieldtypes.LIMITS) == arms.LIMIT_KEYS
    agree(
        fieldtypes.LIMITS[key],
        lambda v: _path(arms.validate(_arm(limits={"max_open": 1, "per_day": 1, key: v})), key),
    )


SIZE_BASE = {
    "contracts": {},
    "max_risk": {},
    "allocation_pct": {},
    "minimum": {"max_risk": 100},
    "allocation": {"allocation_pct": 10},
    "max_dollars": {"allocation_pct": 10},
    "max_contracts": {"allocation_pct": 10},
}


@pytest.mark.parametrize("key", sorted(SIZE_BASE))
def test_size_types_agree_with_the_size_check(key):
    assert set(fieldtypes.SIZE) == {k for ks in sizing.SIZE_KEYS.values() for k in ks}
    agree(fieldtypes.SIZE[key], lambda v: _path(sizing.problems("x", {**SIZE_BASE[key], key: v}), key))


TRIGGER_BASE = {
    "at": {"at": "10:00", "within": 5},
    "within": {"at": "10:00"},
    "open": None,
    "close": None,
    "daily": {
        "signal": {"kind": "gap", "direction": "up", "pct": 0.5},
        "window": {"open": "09:30", "close": "10:00"},
        "within": None,
    },
    "developing": {
        "signal": {"kind": "gap", "direction": "up", "pct": 0.5},
        "window": {"open": "09:30", "close": "10:00"},
        "within": None,
        "daily": True,
    },
}


def _trigger_refused(key: str, v) -> bool:
    if key in ("open", "close"):
        window = (
            {"open": "09:30", "close": "23:59", key: v} if key == "open" else {"open": "00:00", "close": v}
        )
        t = {"signal": {"kind": "gap", "direction": "up", "pct": 0.5}, "window": window, "within": None}
    else:
        t = {**TRIGGER_BASE[key], key: v}
    try:
        ec.validate_trigger(t)
    except ValueError:
        return True
    return False


@pytest.mark.parametrize("key", sorted(fieldtypes.TRIGGER))
def test_trigger_types_agree_with_cores_trigger_check(key):
    agree(fieldtypes.TRIGGER[key], lambda v: _trigger_refused(key, v))


def test_signal_types_are_read_from_cores_validators():
    t = fieldtypes.signal_types(["FOMC"])
    assert t["kinds"]["gap"] == {
        "direction": {"type": "enum", "options": ["up", "down"], "required": True},
        "pct": {"type": "number", "required": True},
        "at": {"type": "enum", "options": ["open", "first_bar_close"], "required": False},
    }
    assert t["kinds"]["consecutive"]["n"] == {"type": "int", "min": 1, "required": True}
    assert t["kinds"]["new_extreme"]["lookback"] == {
        "type": "int",
        "min": 1,
        "or": ["session"],
        "required": True,
    }
    assert t["kinds"]["cross"]["a"]["type"] == "json" and t["kinds"]["cross"]["b"]["type"] == "json"
    assert t["kinds"]["event"]["event"] == {"type": "enum", "options": ["FOMC"], "required": True}
    assert t["kinds"]["event"]["at"] == {"type": "time", "required": True}
    assert t["rules"]["first_of_day"] == {"type": "bool", "required": False}
    assert t["rules"]["latest"] == {"type": "time", "required": False}
    assert t["rules"]["reset"] == {"type": "number", "min": 0, "required": False}


@pytest.mark.parametrize("kind", sorted(signals.KINDS))
def test_each_probed_signal_type_agrees_with_its_validator(kind):
    t = fieldtypes.signal_types()["kinds"][kind]
    checks = {**signals.KINDS[kind].params, **signals.OPTIONAL.get(kind, {})}
    for p, fn in checks.items():
        if kind == "candle":
            continue

        def refused(v, fn=fn):
            try:
                return not fn(v)
            except Exception:  # noqa: BLE001
                return True

        agree(t[p], refused)


def test_every_gate_parameter_is_typed_with_its_functions_default():
    g = fieldtypes.gate_types()
    assert set(g) == set(gates.REGISTRY)
    assert g["rsi"]["n"] == {"type": "int", "min": 1, "default": 14}
    assert g["gex_ratio"]["mode"]["default"] == "nearest" and "default" not in g["gex_ratio"]["strikes"]
    assert g["above_ma"]["kind"]["options"] == ["sma", "ema"]


def test_the_schema_carries_the_types():
    s = builderform.schema()
    assert s["types"]["entry"]["time"]["at"] == {"type": "time"}
    assert s["types"]["entry"]["time"]["window"] == {"type": "int", "min": 1, "default": 5}
    assert s["types"]["signals"]["kinds"]["event"]["event"]["options"] == s["event_labels"]
