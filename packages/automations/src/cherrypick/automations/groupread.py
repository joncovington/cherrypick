"""Each automation group read as one unit: what it earned and how far it fell against its allocation, the
most risk it held at once against its cap, the entries its cap or a member's share refused, and how
closely its members' results move together. Pure over the ledger's rows and the config.

- **The group's line** is `analytics`' line over its members' closed trades, stacked as sized, with the
  running net after each one (`curve`), so the console's performance and costs pages can show a group as
  they show an automation. Membership is the config's now: an automation moved between groups takes its
  history with it.
- **Against its allocation**: the net and the drawdown as a share of the group's `allocation`, None
  without one.
- **Risk at once** is each position's `capital` (its maximum loss at the mid, the measure the cap reads),
  summed over the positions open on a session: from the session it entered through the session it
  closed, or still open. The peak is read at the sessions something entered, where the sum can rise. A
  position closed earlier in the session another entered counts with it, so the figure is an upper
  bound at a session's grain. The same is read per member against its share of the cap.
- **Refused**: the sessions a member missed with `group_risk` as its last reason, by which cap bound
  (the group's or the member's share, the one with less room in the check's own reading). As with the
  entry funnel, a session counts by its last try.
- **Together**: `core.portfolio` over the members' net per session (by the session each trade closed):
  each pair's correlation both ways, the effective number of members, and how much combining cut the
  drawdown. Every figure carries the periods behind it and `thin` under `core.portfolio.MIN_PERIODS`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from cherrypick.core import portfolio as core_portfolio

from . import analytics

NO_GROUP = None


def line(rows: list[dict]) -> dict:
    """`analytics`' line with the running net after each closed trade, in exit order."""
    ordered = sorted(rows, key=lambda r: (r["exit_session"] or "", r["entry_session"], r["arm"]))
    run, curve = 0.0, []
    for r in ordered:
        run += float(r["net_pnl"])
        curve.append({"x": r["exit_session"], "y": round(run, 2)})
    return {**analytics._line(ordered), "curve": curve}


def peak_risk(positions: list[dict]) -> dict | None:
    """The most `capital` held at once over `positions`, at a session's grain, and the session."""
    starts = sorted({p["entry_session"] for p in positions if p.get("entry_session")})
    best: tuple[float, str] | None = None
    for s in starts:
        held = sum(
            float(p.get("capital") or 0.0)
            for p in positions
            if p["entry_session"] <= s
            and (p.get("status") == "open" or not p.get("exit_session") or s <= p["exit_session"])
        )
        if best is None or held > best[0]:
            best = (round(held, 2), s)
    return None if best is None else {"peak": best[0], "session": best[1]}


def bound_by(detail: str | None) -> str:
    """Which cap a `group_risk` refusal hit: `cap` (the group's) or `share` (the member's), from the
    check's own reading {key: [held, limit]}; `unknown` where the detail does not say."""
    try:
        how = (json.loads(detail) if detail else {}).get("value") or {}
        read = how.get("group") or {}
    except (ValueError, AttributeError):
        return "unknown"
    room = {k: float(v[1]) - float(v[0]) for k, v in read.items() if isinstance(v, list) and len(v) == 2}
    if not room:
        return "unknown"
    # The tighter cap bound; on a tie the group's, which binds every member.
    key = min(room, key=lambda k: (room[k], k != "group_max_risk"))
    return "cap" if key == "group_max_risk" else "share"


def _share(v: float | None, of: float | None) -> float | None:
    return round(v / of, 4) if v is not None and of else None


def _together(closed: list[dict], members: list[str]) -> dict | None:
    series: dict[str, dict[str, float]] = {m: {} for m in members}
    for r in closed:
        s = series.get(r["arm"])
        if s is not None and r.get("exit_session"):
            s[r["exit_session"]] = s.get(r["exit_session"], 0.0) + float(r["net_pnl"])
    traded = {k: v for k, v in series.items() if v}
    if len(traded) < 2:
        return None
    start = min(s for v in traded.values() for s in v)
    p = core_portfolio.portfolio(traded, start=start)
    return {
        "pairs": p["correlation"],
        "effective_members": p["effective_arms"],
        "members_counted": p["shared"]["arms"],
        "shared_periods": p["shared"]["periods"],
        "thin": p["shared"]["thin"],
        "drawdown": {k: p["drawdown"][k] for k in ("combined", "sum_of_arms", "benefit")},
        "min_periods": p["min_periods"],
    }


def read(
    arms: Mapping[str, dict],
    switches: Mapping[str, dict],
    positions: list[dict],
    register: list[dict],
) -> dict:
    """Every group as a unit, and the automations in no group as one line beside them.

    `arms`: the config's automations as loaded (each with its `group`); `switches`: the `groups` block;
    `positions`: every ledger position; `register`: every session-register row."""
    by_group: dict[str | None, list[str]] = {}
    for name, arm in sorted(arms.items()):
        by_group.setdefault(arm.get("group"), []).append(name)
    closed = [p for p in positions if p.get("status") == "closed" and p.get("net_pnl") is not None]
    out = []
    for g in sorted(k for k in by_group if k is not None):
        members = by_group[g]
        spec = switches.get(g, {"enabled": True})
        mine = set(members)
        ln = line([r for r in closed if r["arm"] in mine])
        alloc = spec.get("allocation")
        cap = spec.get("max_risk")
        shares = spec.get("shares") or {}
        risk = peak_risk([p for p in positions if p["arm"] in mine])
        member_risk = []
        for m in members:
            pr = peak_risk([p for p in positions if p["arm"] == m])
            limit = round(cap * shares[m] / 100.0, 2) if cap is not None and m in shares else None
            member_risk.append(
                {
                    "name": m,
                    "share": shares.get(m),
                    "peak": None if pr is None else pr["peak"],
                    "session": None if pr is None else pr["session"],
                    "limit": limit,
                    "of_limit": _share(None if pr is None else pr["peak"], limit),
                }
            )
        refused = {"cap": 0, "share": 0, "unknown": 0}
        latest = []
        for r in sorted(register, key=lambda r: str(r["entry_session"]), reverse=True):
            if r["arm"] in mine and r["outcome"] == "missed" and r.get("refusal") == "group_risk":
                by = bound_by(r.get("detail"))
                refused[by] += 1
                if len(latest) < 10:
                    latest.append({"session": str(r["entry_session"]), "automation": r["arm"], "by": by})
        out.append(
            {
                "name": g,
                "enabled": bool(spec.get("enabled", True)),
                "allocation": alloc,
                "max_risk": cap,
                "shares": shares,
                "members": members,
                "line": ln,
                "against_allocation": None
                if not alloc
                else {"net": _share(ln["net"], alloc), "drawdown": _share(ln["drawdown"], alloc)},
                "risk": None if risk is None else {**risk, "cap": cap, "of_cap": _share(risk["peak"], cap)},
                "member_risk": member_risk,
                "refused": {**refused, "latest": latest},
                "together": _together(closed, members),
            }
        )
    loose = by_group.get(NO_GROUP, [])
    mine = set(loose)
    return {
        "groups": out,
        "ungrouped": {"members": loose, "line": line([r for r in closed if r["arm"] in mine])},
    }
