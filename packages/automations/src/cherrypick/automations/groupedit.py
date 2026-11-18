"""The console's Groups page: every group and every automation, and the check of a change to them.

A group is a set of like automations switched as one (`config.groups`): its switch, its start, its
allocation and cap and each member's share. Membership is each automation's own `group` key. This module
owns both shapes, so the orchestrator's writer (`orchestrator.groupedit`) never reads them: it asks
`check` and writes what that returns, the `groups` block whole and each moved automation's `group`.

`view` reads each automation's own switch beside its group's: a group that is on leaves a member that is
itself off, off (a Builder save is always off). Only a group switched off changes a member, to off.

`check` lays a proposal over the config and loads it as the loop would (`config.load_arms`), so every rule
the loop applies (a group no automation belongs to, a share for an outsider, shares over 100, a share of
nothing) is refused here by the same code. Writes nothing.
"""

from __future__ import annotations

import copy

from cherrypick.core import signallib

from . import arms as arms_mod
from . import config as config_mod

#: The keys a group's spec may carry from the page (`config.GROUP_KEYS`).
SPEC_KEYS = config_mod.GROUP_KEYS


def _own_arms(config: dict) -> dict[str, dict]:
    """Each automation as its own config resolves it, before any group switches it off."""
    overrides = config_mod.arm_overrides(config)
    if signallib.refs(overrides):
        overrides, _ = signallib.resolve(overrides, signallib.load())
    return arms_mod.load_arms(overrides)


def view(config: dict) -> dict:
    """{"groups": [{name, enabled, start?, allocation?, max_risk?, shares?, members: [...]}],
    "ungrouped": [...]}, each automation as {name, enabled (its own switch), verdict, symbol, shipped}."""
    own = _own_arms(config)
    switches = config_mod.groups(config)
    groups: dict[str, dict] = {}
    ungrouped = []
    for name, arm in sorted(own.items()):
        row = {
            "name": name,
            "enabled": bool(arm.get("enabled", True)),
            "verdict": (arm.get("register") or {}).get("verdict"),
            "symbol": arm.get("symbol"),
            "shipped": name in arms_mod.ARMS,
        }
        g = arm.get("group")
        if g is None:
            ungrouped.append(row)
            continue
        groups.setdefault(g, {"name": g, **switches.get(g, {"enabled": True}), "members": []})[
            "members"
        ].append(row)
    return {"groups": [groups[g] for g in sorted(groups)], "ungrouped": ungrouped}


def _group_name(v) -> bool:
    return isinstance(v, str) and v.strip() == v != "" and not v.startswith("_")


def check(config: dict, proposal: dict) -> dict:
    """A proposal `{"groups": {group: spec}, "members": {automation: group or null}}` checked as the loop
    would load it. `members` names only the automations that move; `groups` gives every group that will
    have members, each spec whole (enabled required; allocation, max_risk, shares, start optional).

    Returns {"ok": True, "problems": [...], "block": the `groups` block to write, "moves": {automation:
    group or null}}; `block` keeps the file's notes (`_`-prefixed keys) and drops a group left with no
    members. With problems, nothing in it is to be written."""
    if not isinstance(proposal, dict):
        return {"ok": False, "error": "proposal: an object with groups and members"}
    specs, moves = proposal.get("groups"), proposal.get("members") or {}
    if not isinstance(specs, dict) or not isinstance(moves, dict):
        return {"ok": False, "error": "proposal: groups and members are objects"}

    problems: list[str] = []
    own = _own_arms(config)
    for name, g in moves.items():
        if name not in own:
            problems.append(f"members.{name}: no such automation")
        elif g is not None and not _group_name(g):
            problems.append(f"members.{name}: a group name, or null for none")
    after = {n: (moves[n] if n in moves else a.get("group")) for n, a in own.items()}
    used = {g for g in after.values() if g is not None}
    for g in sorted(used - set(specs)):
        problems.append(f"groups.{g}: has members, so say whether it is on")
    for g, spec in specs.items():
        if not _group_name(g):
            problems.append(f"groups.{g}: a group name")
        elif not isinstance(spec, dict) or set(spec) - SPEC_KEYS:
            extra = sorted(set(spec) - SPEC_KEYS) if isinstance(spec, dict) else []
            problems.append(
                f"groups.{g}: " + (f"unknown key {extra[0]!r}" if extra else "an object with enabled")
            )
    if problems:
        return {"ok": True, "problems": problems, "block": None, "moves": {}}

    raw = config.get("groups") if isinstance(config.get("groups"), dict) else {}
    block: dict = {k: v for k, v in raw.items() if k.startswith("_")}
    for g in sorted(used):
        notes = (
            {k: v for k, v in (raw.get(g) or {}).items() if k.startswith("_")}
            if isinstance(raw.get(g), dict)
            else {}
        )
        block[g] = {**notes, **specs[g]}

    moved = {n: g for n, g in moves.items() if g != own[n].get("group")}
    trial = copy.deepcopy(config)
    trial["groups"] = block
    key = next((k for k in config_mod.REGISTRY_KEYS if isinstance(trial.get(k), dict)), "automations")
    entries = trial.setdefault(key, {})
    for n, g in moved.items():
        entries[n] = {**(entries.get(n) or {}), "group": g}
    try:
        config_mod.load_arms(trial)
    except ValueError as exc:
        problems.extend(str(exc).split("; "))
    return {
        "ok": True,
        "problems": problems,
        "block": None if problems else block,
        "moves": {} if problems else moved,
    }
