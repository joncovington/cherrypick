"""The Builder page's form and its "Today, live" read, both from the engine's own tables.

**The schema** (`schema`, `cli builder-schema`) is what the form offers, read from the tables the
engine validates against, so a field the page shows is one `arms.validate` accepts and nothing is
spelled twice: the five blocks and their keys (`enrolment.BLOCKS`), the entry kinds, the expiry kinds
and their keys (`arms.EXPIRY_KEYS`), the strike rules, their keys and match modes (`arms.STRIKE_KEYS`,
`strikes`), the gates with their parameters from each gate function's signature and the stage each
reads (`gates.REGISTRY`, `gates.CORE_STAGE`), the exits (`arms.EXIT_KEYS`), the limits, the sizes and
their progression (`sizing`), core's signal kinds and their parameters (`core.signals.KINDS`), and the
event labels (`events`). A signal parameter's type is core's validator, which the schema cannot
state, so the form takes the value as typed and the module's check says what is wrong.

**Today, live** (`today`, `cli builder-today`) decides one automation once, now, as the paper loop
would: the snapshot `feed.snapshot_for` builds from the stream cache and the paper ledger, opened
read-only, then `paper.decide_all` on a copy of the automation switched on. It returns the engine's
own answer: the entry and the legs it would take, or the first failing check, with every check it
read. Nothing is written: no decision row, no register row, no replay record, no order.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any

from cherrypick.core import signals

from . import arms as arms_mod
from . import enrolment, events, gates, sizing
from . import strikes as st

ENTRY_KINDS: dict[str, dict[str, bool]] = {
    "time": {"at": True, "window": False},
    "orb": {"minutes": True, "direction": True, "latest": True, "single_shot": False},
    "trigger": {"trigger": True, "bar_minutes": False, "volume": False},
}
ORB_DIRECTIONS = ("above", "below")
#: A trigger's two forms and their keys ({key: required}, `core.entrychecks.validate_trigger`): a fixed
#: time for `within` minutes, or a signal's first firing inside a window (`within` minutes from it, or
#: null for until the window closes).
TRIGGER_FORMS: dict[str, dict[str, bool]] = {
    "at": {"at": True, "within": True},
    "signal": {"signal": True, "window": True, "within": True, "daily": False},
}


def _gate(name: str, fn) -> dict:
    params = list(inspect.signature(fn).parameters.values())[1:]
    return {
        "gate": name,
        "stage": gates.CORE_STAGE[name],
        "runs": gates.REGISTRY[name][0],
        "params": [
            {
                "name": p.name,
                "required": p.default is inspect.Parameter.empty,
                "default": None if p.default is inspect.Parameter.empty else p.default,
            }
            for p in params
        ],
    }


def schema() -> dict:
    """Every choice the Builder's form offers, from the engine's tables."""
    return {
        "ok": True,
        "blocks": {b: list(keys) for b, keys in enrolment.BLOCKS.items()},
        "entry": {k: dict(v) for k, v in ENTRY_KINDS.items()},
        "orb_directions": list(ORB_DIRECTIONS),
        "trigger_forms": {k: dict(v) for k, v in TRIGGER_FORMS.items()},
        "signals": {k: sorted(kind.params) for k, kind in sorted(signals.KINDS.items())},
        "signal_rules": sorted(signals._RULES),
        "weekdays": [0, 1, 2, 3, 4],
        "event_labels": sorted({*events.RULE_LABELS, *events.STORE_LABELS, *events.SYMBOL_LABELS}),
        "expiry": {k: dict(v) for k, v in arms_mod.EXPIRY_KEYS.items()},
        "strike_rules": {
            k: {
                "keys": dict(v),
                "matches": []
                if k in st.GEX_RULES or k == "same_as"
                else list(st.VALUE_MATCHES if k in st.VALUE_RULES else st.MATCHES),
                "default_match": st.VALUE_RULES.get(k) if k in st.VALUE_RULES else st.PRICE_RULES.get(k),
            }
            for k, v in arms_mod.STRIKE_KEYS.items()
        },  # fmt: skip
        "gates": [_gate(n, fn) for n, (_, fn) in gates.REGISTRY.items()],
        "exits": sorted(arms_mod.EXIT_KEYS),
        "limits": sorted(arms_mod.LIMIT_KEYS),
        "sizes": {k: sorted(v) for k, v in sizing.SIZE_KEYS.items()},
        "progression": sorted(sizing.PROGRESSION_KEYS),
    }


def resolve(config: dict, name: str, automation: dict | None) -> tuple[dict | None, list[str]]:
    """The automation as the loop would run it: the saved one by `name`, or `automation` laid over the
    config as a save would write it (`drafts.draft`'s resolution). (arm, problems)."""
    import copy

    from . import config as config_mod
    from . import drafts

    if not isinstance(name, str) or not drafts.NAME.match(name):
        return None, [f"name: lower case letters, digits and hyphens ({drafts.NAME.pattern})"]
    if automation is None:
        found = config_mod.load_arms(config).get(name)
        return (found, []) if found is not None else (None, [f"no automation {name!r}"])
    if not isinstance(automation, dict):
        return None, ["automation: an object"]
    entry = {k: copy.deepcopy(v) for k, v in automation.items() if k not in ("name", "register")}
    overrides = {**config_mod.arm_overrides(config), name: entry}
    try:
        return config_mod.load_arms(drafts._with(config, overrides))[name], []
    except ValueError as exc:
        mine = [p for p in str(exc).split("; ") if p.split(":")[0].split(".")[0] == name]
        return None, mine or [str(exc)]


def _check(c) -> dict:
    name, passed, value = c
    return {"check": name, "passed": bool(passed), "value": value}


def today(config: dict, arm: dict, when: datetime, *, conn, cache_path: str, evts) -> dict:
    """One automation decided now, as paper would decide it, on a read-only snapshot. `arm` is the
    resolved automation (saved, or a draft the module's check returned)."""
    from . import config as config_mod
    from . import feed, paper

    live = {**arm, "enabled": True}
    sym = live["symbol"].upper()
    spec = config_mod.symbols(config, {live["name"]: live}).get(sym)
    if spec is None:
        return {
            "ok": True,
            "name": live["name"],
            "state": "no_symbol",
            "reason": f"no stream settings for {sym}",
        }
    window = feed.entry_window(live)
    minute = when.hour * 60 + when.minute
    params = config_mod.defaults(config)
    snap, info = feed.snapshot_for(cache_path, conn, spec, sym, when, params, [live], events=evts)
    out: dict[str, Any] = {
        "ok": True,
        "name": live["name"],
        "at": when.isoformat(timespec="minutes"),
        "window": [
            f"{window[0] // 60:02d}:{window[0] % 60:02d}",
            f"{window[1] // 60:02d}:{window[1] % 60:02d}",
        ],
        "in_window": window[0] <= minute <= window[1],
    }
    if snap is None:
        return {**out, "state": "no_snapshot", "reason": info.get("refusal"), "detail": info.get("detail")}
    (d,) = paper.decide_all({live["name"]: live}, snap, costs=config) or (None,)
    if d is None:
        return {**out, "state": "not_decided", "reason": "the engine took no decision for it"}
    res = {**out, "state": "would_enter" if d.enter else "would_skip", "reason": d.reason,
           "checks": [_check(c) for c in d.checks], "spot": snap.spot}  # fmt: skip
    if d.enter and d.entry is not None:
        e = d.entry
        res["entry"] = {
            "expiry": e.expiry,
            "quantity": e.quantity,
            "legs": [{"name": g.name, "right": g.right, "side": g.side, "strike": g.strike} for g in e.legs],
            "prices": e.prices,
            "max_loss": e.max_loss,
            "max_profit": e.max_profit,
        }
    return res
