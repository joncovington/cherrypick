"""The console's Groups pages: every group and every automation with the state it is in, and the check of
a change to them.

A group is a set of like automations switched as one (`config.groups`): its switch, its start, its
allocation and cap and each member's share. Membership is each automation's own `group` key, and each
automation has its own switch (`enabled`) and may have a start. This module owns all of it, so the
orchestrator's writer (`orchestrator.groupedit`) never reads them: it asks `check` and writes what that
returns, the `groups` block whole, each moved automation's `group` and each switched one's `enabled` and
`start`.

**State.** An automation enters on its days only when three things hold: its own switch is on, its group
(if it has one) is on, and its start has come (its own, or its group's when later: `config.apply_groups`).
`view` names, for each automation, the one that does not hold: `off_own`, `off_group`, `waiting` (with the
date), or `on`. An automation in no group answers to its own switch alone.

**Switching on starts at the next session.** An automation or a group switched on by a change is given the
next trading session as its start (or keeps a later one), so a paper record never begins in the middle of a
day. An automation with a forward rule keeps the start its rule reads from, and `check` says so.

`check` lays a proposal over the config and loads it as the loop would (`config.load_arms`), so every rule
the loop applies (a group no automation belongs to, a share for an outsider, shares over 100, a share of
nothing) is refused here by the same code. Writes nothing.
"""

from __future__ import annotations

import copy
from datetime import date, datetime
from zoneinfo import ZoneInfo

from cherrypick.core import calendar as cal
from cherrypick.core import signallib

from . import arms as arms_mod
from . import config as config_mod

#: The keys a group's spec may carry from the page (`config.GROUP_KEYS`).
SPEC_KEYS = config_mod.GROUP_KEYS
STATES = ("on", "waiting", "off_group", "off_own")


def today_et() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def next_session(today: date) -> date:
    """The first trading session after `today`: a switch made today never starts until then."""
    return cal.next_trading_day(today)


def _own_arms(config: dict) -> dict[str, dict]:
    """Each automation as its own config resolves it, before any group switches it off."""
    overrides = config_mod.arm_overrides(config)
    if signallib.refs(overrides):
        overrides, _ = signallib.resolve(overrides, signallib.load())
    return arms_mod.load_arms(overrides)


def _state(own: dict, effective: dict, group_on: bool, today: date) -> tuple[str, str | None]:
    if not own.get("enabled", True):
        return "off_own", None
    if own.get("group") is not None and not group_on:
        return "off_group", None
    start = effective.get("start")
    if start and start > today.isoformat():
        return "waiting", start
    return "on", None


def view(config: dict, today: date | None = None) -> dict:
    """{"today", "next_session", "counts": {state: n}, "groups": [{name, enabled, start?, allocation?,
    max_risk?, shares?, members: [...]}], "ungrouped": [...]}, each automation as {name, enabled (its own
    switch), start (its own), state, from (the date a `waiting` one starts), verdict, symbol, shipped,
    forward}."""
    today = today or today_et()
    own = _own_arms(config)
    effective = config_mod.load_arms(config)
    switches = config_mod.groups(config)
    groups: dict[str, dict] = {}
    ungrouped = []
    counts = dict.fromkeys(STATES, 0)
    for name, arm in sorted(own.items()):
        g = arm.get("group")
        spec = switches.get(g, {"enabled": True}) if g is not None else {"enabled": True}
        state, start_from = _state(arm, effective[name], spec["enabled"], today)
        counts[state] += 1
        row = {
            "name": name,
            "enabled": bool(arm.get("enabled", True)),
            "start": arm.get("start"),
            "state": state,
            "from": start_from,
            "verdict": (arm.get("register") or {}).get("verdict"),
            "symbol": arm.get("symbol"),
            "shipped": name in arms_mod.ARMS,
            "forward": arm.get("forward") is not None,
        }
        if g is None:
            ungrouped.append(row)
            continue
        groups.setdefault(g, {"name": g, **spec, "members": []})["members"].append(row)
    return {
        "today": today.isoformat(),
        "next_session": next_session(today).isoformat(),
        "counts": counts,
        "groups": [groups[g] for g in sorted(groups)],
        "ungrouped": ungrouped,
    }


def _group_name(v) -> bool:
    return isinstance(v, str) and v.strip() == v != "" and not v.startswith("_")


def check(config: dict, proposal: dict, today: date | None = None) -> dict:
    """A proposal `{"groups": {group: spec}, "members": {automation: group or null}, "switches":
    {automation: true|false}}` checked as the loop would load it. `members` and `switches` name only the
    automations that change; `groups` gives every group that will have members, each spec whole (enabled
    required; allocation, max_risk, shares, start optional).

    Returns {"ok": True, "problems", "block": the `groups` block to write, "moves": {automation: group or
    null}, "switches": {automation: {"enabled", "start"?}}, "notes", "after": the view as it would be}.
    `block` keeps the file's notes (`_`-prefixed keys) and drops a group left with no members. A group or an
    automation switched on gets the next session as its start (or keeps a later one). With problems,
    nothing in it is to be written."""
    if not isinstance(proposal, dict):
        return {"ok": False, "error": "proposal: an object with groups, members and switches"}
    specs = proposal.get("groups")
    moves = proposal.get("members") or {}
    flips = proposal.get("switches") or {}
    if not isinstance(specs, dict) or not isinstance(moves, dict) or not isinstance(flips, dict):
        return {"ok": False, "error": "proposal: groups, members and switches are objects"}
    today = today or today_et()
    first = next_session(today).isoformat()

    problems: list[str] = []
    own = _own_arms(config)
    for name, g in moves.items():
        if name not in own:
            problems.append(f"members.{name}: no such automation")
        elif g is not None and not _group_name(g):
            problems.append(f"members.{name}: a group name, or null for none")
    for name, on in flips.items():
        if name not in own:
            problems.append(f"switches.{name}: no such automation")
        elif not isinstance(on, bool):
            problems.append(f"switches.{name}: true or false")
    placed = {n: (moves[n] if n in moves else a.get("group")) for n, a in own.items()}
    used = {g for g in placed.values() if g is not None}
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
        return {
            "ok": True,
            "problems": problems,
            "block": None,
            "moves": {},
            "switches": {},
            "notes": [],
            "after": None,
        }

    notes: list[str] = []
    current = config_mod.groups(config)
    raw = config.get("groups") if isinstance(config.get("groups"), dict) else {}
    block: dict = {k: v for k, v in raw.items() if k.startswith("_")}
    for g in sorted(used):
        kept = raw.get(g) if isinstance(raw.get(g), dict) else {}
        spec = {**{k: v for k, v in kept.items() if k.startswith("_")}, **specs[g]}
        was_on = current.get(g, {"enabled": g in {a.get("group") for a in own.values()}})["enabled"]
        if spec.get("enabled") is True and not was_on and str(spec.get("start") or "") < first:
            spec["start"] = first
            notes.append(f"{g} switches on from {first}, the next session")
        block[g] = spec

    moved = {n: g for n, g in moves.items() if g != own[n].get("group")}
    switched: dict[str, dict] = {}
    for n, on in flips.items():
        if on == bool(own[n].get("enabled", True)):
            continue
        entry: dict = {"enabled": on}
        if on:
            if own[n].get("forward") is not None:
                notes.append(f"{n} keeps its start {own[n].get('start')}: its forward rule reads from it")
            elif str(own[n].get("start") or "") < first:
                entry["start"] = first
                notes.append(f"{n} switches on from {first}, the next session")
        switched[n] = entry

    trial = copy.deepcopy(config)
    trial["groups"] = block
    key = next((k for k in config_mod.REGISTRY_KEYS if isinstance(trial.get(k), dict)), "automations")
    entries = trial.setdefault(key, {})
    for n, g in moved.items():
        entries[n] = {**(entries.get(n) or {}), "group": g}
    for n, entry in switched.items():
        entries[n] = {**(entries.get(n) or {}), **entry}
    try:
        config_mod.load_arms(trial)
    except ValueError as exc:
        problems.extend(str(exc).split("; "))
    if problems:
        return {
            "ok": True,
            "problems": problems,
            "block": None,
            "moves": {},
            "switches": {},
            "notes": [],
            "after": None,
        }
    return {
        "ok": True,
        "problems": [],
        "block": block,
        "moves": moved,
        "switches": switched,
        "notes": notes,
        "after": view(trial, today),
    }
