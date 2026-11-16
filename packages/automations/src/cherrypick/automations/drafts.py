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
    return {**base, **copy.deepcopy(overrides.get(name) or {}), "name": name}


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
    entry = {k: copy.deepcopy(v) for k, v in automation.items() if k not in ("name", "register")}
    if current is not None and "register" in current:
        entry["register"] = copy.deepcopy(current["register"])
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
