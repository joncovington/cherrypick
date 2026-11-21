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
date), or `on`. An automation in no group answers to its own switch alone. A retired automation
(`arms` docstring: `retired`) is `retired` whatever its switches say, and `check` refuses to switch it on:
it is reinstated on its own page (`cli retire-check`), and stays off until switched on after that.

**Its shape.** Each automation carries the curve of its generic shape (`sketch.sketch`), drawn from its
rule alone, so a page can show the trade beside its name without a session.

**A group's budget.** `view` gives each group the budget its next session carries, the budget today,
any change waiting on a date, and the suggestion `budgets.suggested` makes from its members. Once a group
has run (its start has come), `check` never rewrites the budget it ran on: a different budget becomes a
dated change (`budget_changes`) from the next session, replacing one already waiting, and the same budget
as today's drops a waiting one. A group that has not run yet takes the budget as given.

**A share leaves with its automation.** A share is a member's slice, and the loop refuses one for an
automation outside the group, so an automation moved out of a group takes its share with it: `check`
drops it from that group's budget, today's, every dated change and one waiting alike (membership is the
config's now, so the group's record no longer reads that automation either), and says so.

**Switching on starts at the next session.** An automation or a group switched on by a change is given the
next trading session as its start (or keeps a later one), so a paper record never begins in the middle of a
day. An automation with a forward rule keeps the start its rule reads from, and `check` says so.

`check` lays a proposal over the config and loads it as the loop would (`config.load_arms`), so every rule
the loop applies (a group no automation belongs to switched on, a share for an outsider, shares over 100,
a share of nothing) is refused here by the same code. Writes nothing.
"""

from __future__ import annotations

import copy
from datetime import date, datetime
from zoneinfo import ZoneInfo

from cherrypick.core import calendar as cal
from cherrypick.core import signallib

from . import arms as arms_mod
from . import budgets
from . import config as config_mod
from . import sketch as sketch_mod

#: The keys a group's spec may carry from the page: the file's, less the dated changes this module writes.
SPEC_KEYS = config_mod.GROUP_KEYS - {"budget_changes"}
STATES = ("on", "waiting", "off_group", "off_own", "retired")


def today_et() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def has_run(spec: dict, today: date) -> bool:
    """Whether a group (a `config.groups` row) has begun: switched on with no start, or its start come.
    From then on its budget changes only on a date."""
    start = spec.get("start")
    return bool(spec.get("enabled")) and start is None or (start is not None and start <= today.isoformat())


def _budget_of(spec: dict) -> dict:
    return {k: spec[k] for k in config_mod.BUDGET_KEYS if k in spec}


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
    if own.get("retired"):
        return "retired", None
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
    switches = config_mod.groups(config, today)
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
            "retired": arm.get("retired") or None,
            "shape": _shape(arm),
        }
        if g is None:
            ungrouped.append(row)
            continue
        groups.setdefault(g, {"name": g, **_group_row(spec, today), "members": []})["members"].append(row)
    for g, spec in switches.items():  # a group kept with no automation yet: listed, off
        groups.setdefault(g, {"name": g, **_group_row(spec, today), "members": []})
    for row in groups.values():
        row["suggested"] = budgets.suggested({m["name"]: own[m["name"]] for m in row["members"]})
    return {
        "today": today.isoformat(),
        "next_session": next_session(today).isoformat(),
        "counts": counts,
        "groups": [groups[g] for g in sorted(groups)],
        "ungrouped": ungrouped,
    }


def _shape(arm: dict) -> dict | None:
    """The generic shape's curve and whether it is to scale, for a small drawing; None when it has no legs."""
    try:
        s = sketch_mod.sketch(arm)
    except (KeyError, ValueError, TypeError):
        return None
    return {"curve": s["curve"], "exact": s["exact"]} if s["curve"] else None


def _group_row(spec: dict, today: date) -> dict:
    """A group as the page edits it: its switch and start, the budget its next session carries (what a
    change starts from), and `budget`: {"today", "pending" (a change waiting on a date, or None), "dated"
    (whether a change is dated: the group has run), "history"}."""
    first = next_session(today).isoformat()
    row = {k: v for k, v in spec.items() if k not in ("budget_history", *config_mod.BUDGET_KEYS)}
    row.update(config_mod.budget_at(spec, first))
    later = [b for b in config_mod.history(spec) if b["from"] is not None and b["from"] > today.isoformat()]
    row["budget"] = {
        "today": _budget_of(spec),
        "pending": later[-1] if later else None,
        "dated": has_run(spec, today),
        "history": config_mod.history(spec) if "budget_history" in spec else [],
    }
    return row


def _group_name(v) -> bool:
    return isinstance(v, str) and v.strip() == v != "" and not v.startswith("_")


def _without_shares(budget: dict, names: set[str]) -> dict:
    """`budget` (a spec, a dated change or a `config.groups` row) with the shares of `names` dropped, and
    `shares` dropped whole when none is left."""
    shares = budget.get("shares")
    if not isinstance(shares, dict) or not names & set(shares):
        return budget
    out = {**budget, "shares": {m: v for m, v in shares.items() if m not in names}}
    if not any(not str(m).startswith("_") for m in out["shares"]):
        del out["shares"]
    return out


def _leaving(g: str, kept: dict, current: dict | None, names: set[str]) -> tuple[dict, dict | None]:
    """The file's spec and the loaded row of group `g` with the shares of `names`, its leavers, dropped
    from the budget it began with and from every dated change."""
    if not names:
        return kept, current
    kept = _without_shares(kept, names)
    if isinstance(kept.get("budget_changes"), list):
        kept = {
            **kept,
            "budget_changes": [
                _without_shares(c, names) if isinstance(c, dict) else c for c in kept["budget_changes"]
            ],
        }
    if current is not None:
        current = _without_shares(current, names)
        if "budget_history" in current:
            current = {
                **current,
                "budget_history": [_without_shares(b, names) for b in current["budget_history"]],
            }
    return kept, current


def _dated(g: str, spec: dict, kept: dict, current: dict, first: str, notes: list[str]) -> dict:
    """A group that has run: the budget it began with and the changes before `first` kept as they are, and
    the page's budget, when it differs from what `first` would carry, as a change from `first` (module
    docstring)."""
    want = config_mod._budget(f"groups.{g}", spec)
    out = {k: v for k, v in spec.items() if k not in config_mod.BUDGET_KEYS}
    out.update({k: kept[k] for k in config_mod.BUDGET_KEYS if k in kept})
    changes = [b for b in config_mod.history(current)[1:] if b["from"] < first]
    if want != config_mod.budget_at(
        {"budget_history": [{"from": None, **_budget_of(kept)}, *changes]}, first
    ):
        changes.append({"from": first, **want})
        notes.append(
            f"{g}'s budget changes from {first}, the next session; it ran on the one before until then"
        )
    if changes:
        out["budget_changes"] = changes
    return out


def check(config: dict, proposal: dict, today: date | None = None) -> dict:
    """A proposal `{"groups": {group: spec}, "members": {automation: group or null}, "switches":
    {automation: true|false}}` checked as the loop would load it. `members` and `switches` name only the
    automations that change; `groups` gives every group that will have members, each spec whole (enabled
    required; allocation, max_risk, shares, start optional).

    Returns {"ok": True, "problems", "block": the `groups` block to write, "moves": {automation: group or
    null}, "switches": {automation: {"enabled", "start"?}}, "notes", "after": the view as it would be}.
    `block` keeps the file's notes (`_`-prefixed keys) and every group the proposal gives, one with no
    members included (it must be off); a group the proposal leaves out is deleted. A group or an
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
        elif on and own[name].get("retired"):
            problems.append(f"switches.{name}: retired; reinstate it on its own page first")
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
    current = config_mod.groups(config, today)
    raw = config.get("groups") if isinstance(config.get("groups"), dict) else {}
    block: dict = {k: v for k, v in raw.items() if k.startswith("_")}
    for g in sorted(set(specs) | used):
        kept = raw.get(g) if isinstance(raw.get(g), dict) else {}
        leavers = {n for n, a in own.items() if a.get("group") == g and placed[n] != g}
        held = {
            m
            for b in [kept, *(kept.get("budget_changes") or [])]
            if isinstance(b, dict) and isinstance(b.get("shares"), dict)
            for m in b["shares"]
        }
        for n in sorted(leavers & held):
            notes.append(f"{n}'s share of {g} goes with it: a share is a member's")
        kept, row = _leaving(g, kept, current.get(g), leavers)
        spec = {**{k: v for k, v in kept.items() if k.startswith("_")}, **_without_shares(specs[g], leavers)}
        if row is not None and has_run(row, today):
            spec = _dated(g, spec, kept, row, first, notes)
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
