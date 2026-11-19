"""The Builder's strategy selector: a named structure and a few settings in, its legs and expiry rule
out, as ordinary automation keys the engine already reads. Pure.

A strategy is a starting point, never a recommendation: what it writes is legs (L1, L2, ... in the
written spelling, `arms.written_leg`) placed by the engine's own strike rules, and an expiry rule, all
of which the person edits afterwards. Nothing here is a trading rule of its own; `tests/test_strategies.py`
resolves every strategy at every setting through `config.load_arms`.

Settings (each strategy takes the ones it lists):

    direction   short | long        sold for a credit, or bought for a debit
    right       put | call          which side, where the strategy has one
    dte         days                the expiry: the nearest listed at least this many days out (0: today's)
    expiries    all | weekly | monthly   weekly and all: any listed expiry; monthly: standard monthlies
    place_by    delta | pct | sd | points   how the strategy's first strikes sit from the spot
    target      number              that delta (0.16), percent (2), standard deviations (1) or points (10)
    width       points              a spread's or a wing's distance from the leg it is placed from
    width2      points              a broken-wing butterfly's far wing
    ratio       2..10               a ratio spread's sold contracts for each bought

Where a strategy reaches a leg that needs a second expiry (a calendar, a diagonal) or a stock leg
(covered call, stock), it is listed with `available: false` and the reason; the engine has neither yet.
"""

from __future__ import annotations

from . import arms as arms_mod

PLACE_BY = ("delta", "pct", "sd", "points")
EXPIRIES = ("all", "weekly", "monthly")

#: Every setting's default, type and bounds, for the form.
SETTINGS: dict[str, dict] = {
    "direction": {"type": "enum", "options": ["short", "long"], "default": "short"},
    "right": {"type": "enum", "options": ["put", "call"], "default": "put"},
    "dte": {"type": "int", "min": 0, "max": 400, "default": 45},
    "expiries": {"type": "enum", "options": list(EXPIRIES), "default": "monthly"},
    "place_by": {"type": "enum", "options": list(PLACE_BY), "default": "delta"},
    "target": {"type": "number", "above": 0, "default": 0.16},
    "width": {"type": "number", "above": 0, "default": 5},
    "width2": {"type": "number", "above": 0, "default": 10},
    "ratio": {"type": "int", "min": 2, "max": arms_mod.MAX_LEG_QUANTITY, "default": 2},
}

#: A sensible target for each way of placing, so switching `place_by` does not leave a delta of 10.
TARGET_DEFAULTS = {"delta": 0.16, "pct": 2, "sd": 1, "points": 10}

_WHEN = ["dte", "expiries"]
_PLACE = ["place_by", "target"]
_TWO_EXPIRIES = "needs named expiries: a leg on a second expiry"


def _s(label: str, about: str, settings: list[str], **more) -> dict:
    return {"label": label, "about": about, "settings": settings, **more}


STRATEGIES: dict[str, dict] = {
    "option": _s("Option", "One option.", ["direction", "right", *_WHEN, *_PLACE]),
    "vertical": _s("Vertical", "Two options of one right, one sold and one bought further out.",
                   ["direction", "right", *_WHEN, *_PLACE, "width"]),
    "butterfly": _s("Butterfly", "Long: two sold at the body, one bought each side, the wings equal.",
                    ["direction", "right", *_WHEN, *_PLACE, "width"], defaults={"direction": "long"}),
    "broken_wing": _s("Broken-wing butterfly", "Long: a butterfly with the far wing wider.",
                      ["direction", "right", *_WHEN, *_PLACE, "width", "width2"],
                      defaults={"direction": "long"}),
    "ratio": _s("Ratio spread", "One bought, more sold further out.",
                ["right", *_WHEN, *_PLACE, "width", "ratio"]),
    "straddle": _s("Straddle", "A put and a call at the strike nearest the spot.", ["direction", *_WHEN]),
    "strangle": _s("Strangle", "A put below the spot and a call above.", ["direction", *_WHEN, *_PLACE]),
    "iron_condor": _s("Iron condor", "A put spread and a call spread around the spot.",
                      ["direction", *_WHEN, *_PLACE, "width"]),
    "iron_fly": _s("Iron fly", "A put and a call at the spot, a wing bought each side.",
                   ["direction", *_WHEN, "width"]),
    "jade_lizard": _s("Jade lizard", "A sold put, and a call spread above the spot.",
                      [*_WHEN, *_PLACE, "width"]),
    "calendar": _s("Calendar", "The same strike on two expiries.", [], available=False, why=_TWO_EXPIRIES),
    "diagonal": _s("Diagonal", "Two strikes on two expiries.", [], available=False, why=_TWO_EXPIRIES),
    "covered": _s("Covered call · Stock", "Shares with a call sold against them.", [], available=False,
                  why="needs stock legs"),
    "custom": _s("Custom", "Any legs: starts from one, and you add the rest.", list(_WHEN)),
}  # fmt: skip


class Refused(ValueError):
    """A strategy or a setting the selector cannot build from."""


def _settings(name: str, given: dict) -> dict:
    s = STRATEGIES.get(name)
    if s is None:
        raise Refused(f"strategy: one of {', '.join(STRATEGIES)}")
    if s.get("available") is False:
        raise Refused(f"{s['label']} {s['why']}")
    out = {}
    for key in s["settings"]:
        spec = SETTINGS[key]
        default = s.get("defaults", {}).get(key, spec["default"])
        v = given.get(
            key, TARGET_DEFAULTS.get(given.get("place_by", "delta")) if key == "target" else default
        )
        if spec["type"] == "enum" and v not in spec["options"]:
            raise Refused(f"{key}: one of {', '.join(spec['options'])}")
        if spec["type"] in ("int", "number"):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise Refused(f"{key}: a number")
            if spec["type"] == "int" and int(v) != v:
                raise Refused(f"{key}: a whole number")
            if "min" in spec and v < spec["min"] or "max" in spec and v > spec["max"]:
                raise Refused(f"{key}: {spec.get('min')} to {spec.get('max')}")
            if "above" in spec and v <= spec["above"]:
                raise Refused(f"{key}: more than {spec['above']}")
        out[key] = v
    return out


def expiry_rule(dte: int, expiries: str) -> dict:
    """The expiry rule for `dte` days out: today's for 0, else the nearest listed expiry at least that
    many days out, monthlies only where asked."""
    if dte == 0:
        return {"kind": "dte", "days": 0}
    rule = {"kind": "at_least", "days": int(dte)}
    if expiries == "monthly":
        rule["monthly"] = True
    return rule


def _out(sign: int, place_by: str, target: float) -> dict:
    """A strike `target` away from the spot on the `sign` side (-1 below, +1 above), by `place_by`."""
    if place_by == "delta":
        return {"rule": "delta", "target": target}
    if place_by == "pct":
        return {"rule": "spot", "pct": sign * target, "match": "nearest"}
    if place_by == "sd":
        return {"rule": "std_dev", "sd": sign * target, "match": "nearest"}
    return {"rule": "spot", "points": sign * target, "match": "nearest"}


def _from(leg: str, points: float) -> dict:
    return {"rule": "from_leg", "leg": leg, "points": points, "match": "nearest"}


def _leg(n: int, action: str, side: str, strike: dict, qty: int = 1) -> dict:
    return {"name": f"L{n}", "action": action, "qty": qty, "side": side, "strike": strike}


def _legs(name: str, s: dict) -> list[dict]:
    sell, buy = ("STO", "BTO") if s.get("direction", "short") == "short" else ("BTO", "STO")
    right = s.get("right", "put")
    away = -1 if right == "put" else 1  # further out of the money
    place = lambda sign: _out(sign, s.get("place_by", "delta"), s.get("target", 0.16))  # noqa: E731
    w = s.get("width")
    if name == "option":
        return [_leg(1, sell, right, place(away))]
    if name == "vertical":
        return [_leg(1, sell, right, place(away)), _leg(2, buy, right, _from("L1", away * w))]
    # A butterfly is long when its wings are bought: the body sold, two contracts.
    if name == "butterfly":
        body, wing = ("BTO", "STO") if s.get("direction") == "short" else ("STO", "BTO")
        return [_leg(1, body, right, place(away), qty=2), _leg(2, wing, right, _from("L1", -away * w)),
                _leg(3, wing, right, _from("L1", away * w))]  # fmt: skip
    if name == "broken_wing":
        body, wing = ("BTO", "STO") if s.get("direction") == "short" else ("STO", "BTO")
        return [_leg(1, body, right, place(away), qty=2), _leg(2, wing, right, _from("L1", -away * w)),
                _leg(3, wing, right, _from("L1", away * s["width2"]))]  # fmt: skip
    if name == "ratio":
        return [
            _leg(1, "BTO", right, place(away)),
            _leg(2, "STO", right, _from("L1", away * w), qty=s["ratio"]),
        ]
    if name == "straddle":
        return [_leg(1, sell, "put", {"rule": "spot", "points": 0, "match": "nearest"}),
                _leg(2, sell, "call", {"rule": "same_as", "leg": "L1"})]  # fmt: skip
    if name == "strangle":
        return [_leg(1, sell, "put", place(-1)), _leg(2, sell, "call", place(1))]
    if name == "iron_condor":
        return [_leg(1, sell, "put", place(-1)), _leg(2, buy, "put", _from("L1", -w)),
                _leg(3, sell, "call", place(1)), _leg(4, buy, "call", _from("L3", w))]  # fmt: skip
    if name == "iron_fly":
        return [_leg(1, sell, "put", {"rule": "spot", "points": 0, "match": "nearest"}),
                _leg(2, sell, "call", {"rule": "same_as", "leg": "L1"}),
                _leg(3, buy, "put", _from("L1", -w)), _leg(4, buy, "call", _from("L2", w))]  # fmt: skip
    if name == "jade_lizard":
        return [
            _leg(1, "STO", "put", place(-1)),
            _leg(2, "STO", "call", place(1)),
            _leg(3, "BTO", "call", _from("L2", w)),
        ]
    # custom: one leg to start from
    return [_leg(1, "STO", "put", {"rule": "delta", "target": 0.16})]


def build(name: str, given: dict | None = None) -> dict:
    """{"strategy", "settings", "legs", "expiry"} for strategy `name` with `given` settings (the rest
    at their defaults). Raises Refused, naming the setting."""
    s = _settings(name, given or {})
    return {
        "strategy": name,
        "settings": s,
        "legs": [arms_mod.written_leg(leg) for leg in _legs(name, s)],
        "expiry": expiry_rule(s["dte"], s["expiries"]),
    }


def schema() -> dict:
    """What the selector offers: each strategy (label, about, the settings it takes, whether it can be
    built yet and why not) and every setting's type, default and bounds."""
    return {
        "strategies": {
            k: {
                "label": v["label"],
                "about": v["about"],
                "settings": v["settings"],
                "available": v.get("available", True),
                "why": v.get("why"),
            }
            for k, v in STRATEGIES.items()
        },  # fmt: skip
        "settings": SETTINGS,
        "target_defaults": TARGET_DEFAULTS,
    }
