"""The Builder's check of one automation before it is saved: pure over the config and the shipped library.

`editable(config, name)` is the automation as an editor starts from: the shipped entry with this machine's
changes laid over it, before groups stamp anything and with signal-library references left as written.

`draft(config, name, automation)` lays an edited automation over the config as the `automations` block
would hold it and resolves every automation again (`config.load_arms`), so a save is refused for exactly
the problems that would stop the loop. It returns the entry to write, which is always:

- **off** (`enabled: false`): turning an automation on is its own deliberate step, never a side effect of
  saving it;
- **without a register label it did not have**: `register` records what a study judged, so it is kept as
  stored and never taken from the editor (a copy of a backtested automation under a new name is not
  backtested);
- **retired as it was**: `retired` is set and cleared on the automation's own page (`retire`), so
  it is kept as stored and never taken from the editor; a save never reinstates one;
- named by its key (`name` is the key it is saved under).

Nothing here writes. The orchestrator's config editor writes the entry (`automationedit`), after running
this check by subprocess (`cli draft`).
"""

from __future__ import annotations

import copy
import re
from datetime import date

from . import arms as arms_mod
from . import config as config_mod
from . import enrolment

#: A new automation's name: lower case, digits and hyphens, as the shipped ones are.
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")


def editable(config: dict, name: str) -> dict | None:
    """The automation `name` as an editor starts from it, or None when there is none."""
    overrides = config_mod.arm_overrides(config)
    if name not in arms_mod.ARMS and name not in overrides:
        return None
    base = copy.deepcopy(arms_mod.ARMS.get(name, {"name": name}))
    out = {**base, **copy.deepcopy(overrides.get(name) or {}), "name": name}
    return _written(out)


def _written(spec: dict) -> dict:
    """`spec` with its legs in the written spelling (action, qty, side), whichever it was stored in:
    the editor always shows, and a save always writes, the page's own words."""
    if isinstance(spec.get("legs"), list):
        spec = {**spec, "legs": [arms_mod.written_leg(leg) for leg in spec["legs"]]}
    return spec


def _with(config: dict, overrides: dict) -> dict:
    out = {k: v for k, v in config.items() if k not in config_mod.REGISTRY_KEYS}
    out["automations"] = overrides
    return out


def draft(config: dict, name: str, automation: dict, today: date) -> dict:
    """`{"ok", "name", "problems", "entry", "view", "new"}` for `automation` saved as `name`.
    `problems` empty means the entry can be written; `view` is its Builder row (`enrolment.builder_view`)."""
    if not isinstance(name, str) or not NAME.match(name):
        return {
            "ok": True,
            "name": name,
            "problems": [f"name: lower case letters, digits and hyphens ({NAME.pattern})"],
        }
    if not isinstance(automation, dict):
        return {"ok": True, "name": name, "problems": ["automation: an object"]}
    current = editable(config, name)
    entry = _written(
        {k: copy.deepcopy(v) for k, v in automation.items() if k not in ("name", "register", "retired")}
    )
    for kept in ("register", "retired"):
        if current is not None and kept in current:
            entry[kept] = copy.deepcopy(current[kept])
    entry["enabled"] = False
    overrides = {**config_mod.arm_overrides(config), name: entry}
    try:
        arms = config_mod.load_arms(_with(config, overrides))
    except ValueError as exc:
        mine = [p for p in str(exc).split("; ") if p.split(":")[0].split(".")[0] == name]
        return {"ok": True, "name": name, "problems": mine or [str(exc)], "new": current is None}
    from . import paper_loop

    suggestion = paper_loop.backtest_suggestions({name: arms[name]}, today).get(name)
    return {
        "ok": True,
        "name": name,
        "problems": [],
        "entry": entry,
        "view": enrolment.builder_view(arms[name], suggestion),
        "new": current is None,
    }


def retire(config: dict, name: str, retired: bool, today: date) -> dict:
    """`{"ok", "name", "problems", "entry", "notes"}`: the keys to write to retire `name` (`retired` the
    date, `enabled` false) or reinstate it (`retired` false, `enabled` false: reinstating never switches
    one on). Checked by loading every automation with the change, as the loop would. Writes nothing."""
    if not isinstance(name, str) or not isinstance(retired, bool):
        return {"ok": True, "name": name, "problems": ["request: a name and retired true or false"]}
    current = editable(config, name)
    if current is None:
        return {"ok": True, "name": name, "problems": [f"{name}: no such automation"]}
    was = bool(current.get("retired"))
    if was == retired:
        return {
            "ok": True,
            "name": name,
            "problems": [f"{name}: already {'retired' if was else 'not retired'}"],
        }
    entry = {"retired": today.isoformat() if retired else False, "enabled": False}
    overrides = config_mod.arm_overrides(config)
    trial = {**overrides, name: {**(overrides.get(name) or {}), **entry}}
    try:
        config_mod.load_arms(_with(config, trial))
    except ValueError as exc:
        return {"ok": True, "name": name, "problems": str(exc).split("; ")}
    notes = (
        [f"{name} is off from now; what it holds is still managed to its exits"]
        if retired
        else [f"{name} stays off: switch it on in its group to trade, from the next session"]
    )
    return {"ok": True, "name": name, "problems": [], "entry": entry, "notes": notes}


def leg_conversion(config: dict) -> dict:
    """The machine's automations whose legs are stored in the older spelling, each with its legs
    in the written one, and the proof that converting them changes nothing: every automation loads to
    the same legs and block hashes after as before (so no backtest label moves). `problems` is
    non-empty when it would, and then nothing may be written. Reads only."""
    overrides = config_mod.arm_overrides(config)
    convert = {}
    for name, entry in sorted(overrides.items()):
        legs = entry.get("legs") if isinstance(entry, dict) else None
        if not isinstance(legs, list):
            continue
        written = [arms_mod.written_leg(leg) for leg in legs]
        if written != legs:
            convert[name] = written
    if not convert:
        return {"ok": True, "convert": {}, "problems": []}
    after = {**overrides, **{n: {**overrides[n], "legs": legs} for n, legs in convert.items()}}
    try:
        was = config_mod.load_arms(config)
        now = config_mod.load_arms(_with(config, after))
    except ValueError as exc:
        return {"ok": True, "convert": convert, "problems": [str(exc)]}
    problems = [
        f"{n}: converting would change how it loads"
        for n in sorted(was)
        if was[n]["legs"] != now[n]["legs"]
        or enrolment.block_hashes(was[n]) != enrolment.block_hashes(now[n])
    ]
    return {"ok": True, "convert": convert, "problems": problems}
