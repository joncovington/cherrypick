"""How many contracts an entry takes: the arm's `size` rule, its progression over the arm's own closed
trades, and the caps of the automation group it belongs to. Pure functions over the decision's data.

Risk is the structure's maximum loss at the mid, whole-position dollars (per-share loss x 100 x count),
the measure `max_risk` has always read; an allocation and a group cap are budgets of that same measure.

A `size` rule is one of:

    {"contracts": N}
    {"max_risk": dollars, "minimum": n}       the most contracts whose risk fits, never fewer than `minimum`
    {"allocation_pct": p, "allocation": dollars, "max_dollars": x, "max_contracts": n, "minimum": n}
        the most contracts whose risk fits p% of the allocation, that budget capped at `max_dollars`
        and the count at `max_contracts`; `allocation` may be left to the group (`config.groups`),
        which gives each member its share of the group's allocation

with an optional `"progression": {"after": "loss"|"win", "step": n|"double", "floor": a, "ceiling": b}` on
any of them: after each consecutive loss (or win) among the arm's closed trades, latest first, the count
steps by n contracts (or doubles) from the rule's count, and is held between `floor` and `ceiling`. The
first trade the other way, or one that nets exactly zero, ends the run and the count is the rule's again.
A trade's outcome is its net P&L at the verdict fill, the ledger's `net_pnl`.

A group's caps (`budget`, set on each member by `config.apply_groups`, never written by hand) are hard:
the count is cut to fit the group's open risk under `group_max_risk` and the member's under
`member_max_risk`, and no `minimum` lifts it back over them.
"""

from __future__ import annotations

import math

from . import pricing

SIZE_KEYS = {
    "contracts": frozenset({"contracts", "progression"}),
    "max_risk": frozenset({"max_risk", "minimum", "progression"}),
    "allocation_pct": frozenset(
        {"allocation_pct", "allocation", "max_dollars", "max_contracts", "minimum", "progression"}
    ),
}
PROGRESSION_KEYS = frozenset({"after", "step", "floor", "ceiling"})
#: The keys `config.apply_groups` sets on a member as its `budget`.
BUDGET_KEYS = frozenset({"group", "allocation", "group_max_risk", "member_max_risk", "members"})


def _whole(v, least: int = 1) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= least


def _positive(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def kind_of(rule: dict) -> str | None:
    """Which kind a rule is: the one of `SIZE_KEYS` whose key it states."""
    found = [k for k in SIZE_KEYS if k in rule]
    return found[0] if len(found) == 1 else None


def problems(where: str, rule) -> list[str]:
    """A `size` rule's problems, each with its path."""
    if not isinstance(rule, dict):
        return [f"{where}: an object"]
    kind = kind_of(rule)
    if kind is None:
        return [f"{where}: one of {sorted(SIZE_KEYS)}"]
    extra = set(rule) - SIZE_KEYS[kind]
    if extra:
        return [f"{where}: unknown key {sorted(extra)[0]!r} for {kind}"]
    p = []
    if kind == "contracts" and not _whole(rule["contracts"]):
        p.append(f"{where}.contracts: a whole number of at least 1")
    if kind == "max_risk" and not _positive(rule["max_risk"]):
        p.append(f"{where}.max_risk: dollars, more than 0")
    if kind == "allocation_pct":
        pct = rule["allocation_pct"]
        if not (_positive(pct) and pct <= 100):
            p.append(f"{where}.allocation_pct: a percent, more than 0 and at most 100")
        for k in ("allocation", "max_dollars"):
            if k in rule and not _positive(rule[k]):
                p.append(f"{where}.{k}: dollars, more than 0")
        if "max_contracts" in rule and not _whole(rule["max_contracts"]):
            p.append(f"{where}.max_contracts: a whole number of at least 1")
    if "minimum" in rule and not _whole(rule["minimum"], 0):
        p.append(f"{where}.minimum: a whole number, 0 or more")
    if "progression" in rule:
        p += _progression_problems(f"{where}.progression", rule["progression"])
    return p


def _progression_problems(where: str, prog) -> list[str]:
    if not isinstance(prog, dict):
        return [f"{where}: an object of after, step, floor, ceiling"]
    extra = set(prog) - PROGRESSION_KEYS
    if extra:
        return [f"{where}: unknown key {sorted(extra)[0]!r}"]
    missing = sorted(PROGRESSION_KEYS - set(prog))
    if missing:
        return [f"{where}.{missing[0]}: required"]
    p = []
    if prog["after"] not in ("loss", "win"):
        p.append(f"{where}.after: 'loss' or 'win'")
    step = prog["step"]
    if step != "double" and not (isinstance(step, int) and not isinstance(step, bool) and step != 0):
        p.append(f"{where}.step: a whole number of contracts (not 0), or 'double'")
    if not _whole(prog["floor"]):
        p.append(f"{where}.floor: a whole number of at least 1")
    if not _whole(prog["ceiling"]):
        p.append(f"{where}.ceiling: a whole number of at least 1")
    elif _whole(prog["floor"]) and prog["ceiling"] < prog["floor"]:
        p.append(f"{where}.ceiling: at least the floor")
    return p


def risk_per_contract(loss_per_share: float) -> float:
    return loss_per_share * pricing.MULTIPLIER


def base(rule: dict, loss_per_share: float, *, allocation: float | None = None) -> int:
    """The rule's own count, before any progression or group cap. `allocation` is the member's share of
    its group's allocation, read when the rule states none."""
    if "contracts" in rule:
        return int(rule["contracts"])
    per = risk_per_contract(loss_per_share)
    if "max_risk" in rule:
        budget = float(rule["max_risk"])
        cap = None
    else:
        alloc = rule.get("allocation", allocation)
        if alloc is None:
            return 0
        budget = float(alloc) * float(rule["allocation_pct"]) / 100.0
        if "max_dollars" in rule:
            budget = min(budget, float(rule["max_dollars"]))
        cap = rule.get("max_contracts")
    n = int(math.floor(budget / per + 1e-9)) if per > 0 else 0
    if cap is not None:
        n = min(n, int(cap))
    return max(n, int(rule.get("minimum", 0)))


def run_length(results: list[float], after: str) -> int:
    """How many of the latest closed trades, back from the last, are all losses (`after` "loss") or all
    wins; a trade the other way, or one at exactly zero, ends the run."""
    n = 0
    for net in reversed(results):
        if (net < 0) if after == "loss" else (net > 0):
            n += 1
        else:
            break
    return n


def progressed(count: int, prog: dict, results: list[float]) -> int:
    """`count` stepped once per trade of the current run, held between floor and ceiling."""
    k = run_length(results, prog["after"])
    if prog["step"] == "double":
        # Doubling past the ceiling is the ceiling; stop before the power grows without bound.
        n = count
        for _ in range(k):
            if n >= prog["ceiling"]:
                break
            n *= 2
    else:
        n = count + int(prog["step"]) * k
    return max(int(prog["floor"]), min(int(prog["ceiling"]), n))


def open_risk(open_positions, members) -> float:
    """The open risk of the positions whose arm is in `members`."""
    names = set(members)
    return sum(float(p.risk or 0.0) for p in open_positions if p.arm in names)


def group_room(budget: dict, arm_name: str, open_positions) -> tuple[float | None, dict]:
    """The dollars of risk the arm may still add under its group's caps (None: no cap), and what was
    read: {cap: (open, limit)}."""
    room: float | None = None
    read: dict = {}
    for key, members in (("group_max_risk", budget["members"]), ("member_max_risk", [arm_name])):
        limit = budget.get(key)
        if limit is None:
            continue
        held = round(open_risk(open_positions, members), 2)
        read[key] = (held, float(limit))
        left = float(limit) - held
        room = left if room is None else min(room, left)
    return room, read


def count(
    arm: dict, loss_per_share: float, *, results: list[float] | None = None, open_positions=()
) -> tuple[int, dict]:
    """The entry's contract count and how it was reached: {"base", "progressed"?, "group"?}. 0 means no
    entry; `why` names the step that left none ("size" or "group_risk")."""
    rule = arm["size"]
    budget = arm.get("budget") or {}
    n = base(rule, loss_per_share, allocation=budget.get("allocation"))
    how: dict = {"base": n}
    if n < 1:
        how["why"] = "size"
        return 0, how
    if "progression" in rule:
        n = progressed(n, rule["progression"], list(results or []))
        how["progressed"] = n
    if budget:
        room, read = group_room(budget, arm["name"], open_positions)
        if room is not None:
            per = risk_per_contract(loss_per_share)
            fits = int(math.floor(max(room, 0.0) / per + 1e-9)) if per > 0 else n
            how["group"] = {k: list(v) for k, v in read.items()}
            if fits < n:
                n = fits
                how["cut_to"] = n
            if n < 1:
                how["why"] = "group_risk"
                return 0, how
    return n, how
