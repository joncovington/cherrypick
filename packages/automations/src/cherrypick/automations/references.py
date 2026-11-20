"""Each automation's backtest reference: the one Builder run its paper is set against.

The config's `references` block, `{automation: run}`, names for an automation the Builder run the
operator pinned (`builder-<automation>-<YYMMDDHHMM>`, the result `automation_backtest.py` wrote). The
automation page reads the reference from that run (`backtest read automation-reference`), so the
yardstick moves only when the operator pins another run, never when a new run lands. An automation the
block does not name has no reference.

The block is separate from the automations themselves on purpose: pinning a run changes nothing an
automation trades, so no block hash, backtest label or replay moves. Nothing the paper loop reads comes
from here.

`check` is the Builder's pin, checked: the automation must exist, and the run must be one of that
automation's own Builder runs by name. Whether the run's file exists is the backtest package's to say;
the orchestrator asks it before writing. Writes nothing.
"""

from __future__ import annotations

import re

from . import config as config_mod

#: A Builder run's name: `builder-<automation>-<YYMMDDHHMM>`.
RUN = re.compile(r"^builder-(?P<name>[a-z0-9][a-z0-9-]{1,47})-\d{10}$")


def block(config: dict) -> dict:
    """The `references` block as the file holds it, `_`-prefixed notes kept. Raises ValueError when it is
    not an object of {automation: run}."""
    raw = config.get("references")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("references: an object of {automation: run}")
    for name, run in raw.items():
        if str(name).startswith("_"):
            continue
        m = RUN.match(run) if isinstance(run, str) else None
        if m is None or m.group("name") != name:
            raise ValueError(f"references.{name}: one of {name}'s Builder runs, builder-{name}-YYMMDDHHMM")
    return dict(raw)


def references(config: dict) -> dict[str, str]:
    """Each automation's pinned run, notes dropped."""
    return {k: v for k, v in block(config).items() if not str(k).startswith("_")}


def check(config: dict, req: dict) -> dict:
    """The pin `{name, run}` (`run` null to unpin) checked: its problems and the `references` block a save
    would write, the file's notes kept."""
    name, run = req.get("name"), req.get("run")
    if not isinstance(name, str) or not name:
        return {"ok": False, "error": "name: the automation"}
    if run is not None and not isinstance(run, str):
        return {"ok": False, "error": "run: a Builder run's name, or null to unpin"}
    problems: list[str] = []
    try:
        current = block(config)
    except ValueError as exc:
        return {"ok": True, "problems": [str(exc)], "block": None}
    try:
        arms = config_mod.load_arms(config)
    except ValueError as exc:
        return {"ok": True, "problems": [f"the automations do not load: {exc}"], "block": None}
    # Unpinning an automation since removed from the config clears its stale pin.
    if name not in arms and not (run is None and name in current):
        problems.append(f"no automation named {name}")
    if run is not None:
        m = RUN.match(run)
        if m is None or m.group("name") != name:
            problems.append(f"{run} is not one of {name}'s Builder runs")
    if problems:
        return {"ok": True, "problems": problems, "block": None}
    after = {k: v for k, v in current.items() if k != name}
    if run is not None:
        after[name] = run
    return {"ok": True, "problems": [], "block": after, "name": name, "run": run}
