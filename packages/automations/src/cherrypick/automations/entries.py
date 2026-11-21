"""How often each automation enters, and where the sessions it did not enter stopped.

Pure. The session register (`ensemble_entries`) holds one row per arm-day the automation was due:
`entered`, `missed` with the last reason a tick in its window gave, or `pending` while the window is
open. Each reason belongs to the step of `engine.decide` that gave it, and the steps run in order, so a
session that stopped at one step passed every step before it on that last try. The funnel counts, for
each step, the sessions that reached it, and lists the causes of the ones that stopped there.

The backtest driver gives each session's last reason in the same words (`summary.reasons` of a run), so
a run's reasons go through the same `funnel` and the two read side by side, step by step and cause by
cause (`side_by_side`).

A session's last reason is its last tick's. One that passed the gates at 10:00 and found no price at
10:04 counts as stopped at the structure: the funnel says where the last try stopped, not the furthest
any try got.
"""

from __future__ import annotations

from collections.abc import Mapping

from . import gates

#: The steps in `engine.decide`'s order, each with the words for a session that passed it. `due` holds a
#: reason no step names, as stopped before any step.
STEPS = (
    ("due", "Gave a known reason"),
    ("data", "Had its data"),
    ("trigger", "Its entry fired"),
    ("limits", "Within its limits and calendar"),
    ("gates", "Passed its market gates"),
    ("structure", "Found its structure"),
    ("size", "Sized within its budget"),
)

#: Inputs that were not there: the feed, the loop's own record, or a store a backtest reads.
DATA = frozenset(
    {
        "no_spot",
        "no_prev_close",
        "no_chain",
        "no_fresh_quote",
        "no_fresh_quotes",
        "no_snapshot",
        "no_tick_in_window",
        "trail_incomplete",
        "no_volume",
        "event_dates_unknown",
        "stale_bars",
        "no_data",
        "needs_intraday",
    }
)
TRIGGER = frozenset({"symbol", "weekday", "entry_not_due", "no_breakout"})
LIMITS = frozenset(
    {"entered_today", "max_open", "every_days", "no_expiry", "no_far_expiry", "one_per_expiry"}
)
STRUCTURE = frozenset({"unpriced", "unrealistic_price", "undefined_risk"})
SIZE = frozenset({"size", "group_risk"})


def step(reason: str) -> str:
    """The step of the decision that gave `reason`."""
    if reason in DATA:
        return "data"
    if reason in TRIGGER:
        return "trigger"
    if reason in LIMITS or reason.startswith("event:"):
        return "limits"
    if reason.startswith("gate:"):
        name = reason[len("gate:") :]
        if name not in gates.REGISTRY:
            return "due"
        return "gates" if gates.stage_of({"gate": name}) == "market" else "structure"
    if reason in STRUCTURE or reason.startswith("strike:"):
        return "structure"
    if reason in SIZE:
        return "size"
    return "due"


def funnel(reasons: Mapping[str, int], pending: int = 0) -> dict:
    """The funnel of `reasons` ({last reason or `entered`: sessions}). `due` is every decided session;
    each step's `reached` the sessions that got to it, `stopped` those whose last try ended there, with
    their causes, most first, and `passed` the rest (the last step's is what entered). Shares are of
    `due`."""
    counts = {str(k): int(v) for k, v in reasons.items() if int(v) > 0}
    due = sum(counts.values())
    entered = counts.get("entered", 0)
    by_step: dict[str, list[tuple[str, int]]] = {k: [] for k, _ in STEPS}
    for reason, n in counts.items():
        if reason != "entered":
            by_step[step(reason)].append((reason, n))

    def share(n: int) -> float | None:
        return round(n / due, 4) if due else None

    steps, left = [], due
    for key, label in STEPS:
        causes = sorted(by_step[key], key=lambda c: (-c[1], c[0]))
        stopped = sum(n for _, n in causes)
        steps.append(
            {
                "step": key,
                "label": label,
                "reached": left,
                "reached_share": share(left),
                "stopped": stopped,
                "passed": left - stopped,
                "passed_share": share(left - stopped),
                "causes": [{"reason": r, "count": n, "share": share(n)} for r, n in causes],
            }
        )
        left -= stopped
    # The step that lost the most sessions; on a tie the earlier step, which the later ones depend on.
    worst = max((s for s in steps if s["stopped"]), key=lambda s: s["stopped"], default=None)
    return {
        "due": due,
        "pending": int(pending),
        "entered": entered,
        "missed": due - entered,
        "rate": share(entered),
        "steps": steps,
        "most_lost": None
        if worst is None
        else {
            "step": worst["step"],
            "label": worst["label"],
            "stopped": worst["stopped"],
            "share": share(worst["stopped"]),
        },
    }


def from_register(rows: list[dict]) -> dict[str, dict]:
    """Each arm's reason counts and pending days from its register rows."""
    out: dict[str, dict] = {}
    for r in rows:
        a = out.setdefault(str(r["arm"]), {"reasons": {}, "pending": 0, "first": None, "last": None})
        session = str(r["entry_session"])
        if r["outcome"] == "pending":
            a["pending"] += 1
            continue
        key = "entered" if r["outcome"] == "entered" else str(r.get("refusal") or "no_tick_in_window")
        a["reasons"][key] = a["reasons"].get(key, 0) + 1
        a["first"] = session if a["first"] is None or session < a["first"] else a["first"]
        a["last"] = session if a["last"] is None or session > a["last"] else a["last"]
    return out


def side_by_side(paper: dict, run: dict | None) -> list[dict]:
    """One row per cause either side gave, by step in the decision's order: its share of paper's due
    sessions and of the run's. A side that never gave a cause reads 0 there; no run reads None."""
    rows: dict[str, dict] = {}
    for side, f in (("paper", paper), ("run", run)):
        if f is None:
            continue
        for s in f["steps"]:
            for c in s["causes"]:
                row = rows.setdefault(c["reason"], {"reason": c["reason"], "step": s["step"]})
                row[side] = c["share"]
    order = {k: i for i, (k, _) in enumerate(STEPS)}
    out = []
    for row in rows.values():
        row.setdefault("paper", 0.0 if paper["due"] else None)
        row.setdefault("run", None if run is None else 0.0)
        out.append(row)
    return sorted(out, key=lambda r: (order[r["step"]], -(r["paper"] or 0), r["reason"]))


def view(arms: Mapping[str, dict], rows: list[dict]) -> list[dict]:
    """Every automation the config resolves to, and any the register names that it no longer does, each
    with its funnel and the span of sessions it covers."""
    per = from_register(rows)
    out = []
    for name in sorted(set(arms) | set(per)):
        a, p = arms.get(name), per.get(name) or {"reasons": {}, "pending": 0, "first": None, "last": None}
        out.append(
            {
                "name": name,
                "group": (a or {}).get("group"),
                "symbol": (a or {}).get("symbol"),
                "enabled": bool((a or {}).get("enabled", False)),
                "configured": a is not None,
                "first": p["first"],
                "last": p["last"],
                "funnel": funnel(p["reasons"], p["pending"]),
            }
        )
    return out


def one(name: str, rows: list[dict], run_reasons: Mapping[str, int] | None, *, days: int = 30) -> dict:
    """One automation's funnel, the run's beside it (from its `summary.reasons`), the causes side by
    side, and its latest `days` register rows, each with its step."""
    mine = [r for r in rows if r["arm"] == name]
    p = from_register(mine).get(name) or {"reasons": {}, "pending": 0, "first": None, "last": None}
    paper = funnel(p["reasons"], p["pending"])
    run = funnel(run_reasons) if run_reasons else None
    latest = sorted(mine, key=lambda r: str(r["entry_session"]), reverse=True)[:days]
    return {
        "name": name,
        "first": p["first"],
        "last": p["last"],
        "paper": paper,
        "run": run,
        "causes": side_by_side(paper, run),
        "days": [
            {
                "session": str(r["entry_session"]),
                "outcome": r["outcome"],
                "reason": r.get("refusal"),
                "step": None
                if r["outcome"] != "missed"
                else step(str(r.get("refusal") or "no_tick_in_window")),
            }
            for r in latest
        ],
    }
