"""`cherrypick study-times` -- how long each study spends at each stage, and where the waiting is (#1090).

A study's rows ran for about a tenth of the time between its first row queued and its last finished
(the 2026-11-14 review), and the rest is hand steps the queue cannot see: a failure noticed, a fix
written and merged, a row queued again, a verdict recorded. This read puts dates on them, so a
process change can be judged before and after it lands. Times only: no study figure is read.

Per register row (an `itemN` study), from the register's own git history and the research queue:

- `declared`: the first commit that added the item's `### N.` heading to the plan (the register
  began after many items were declared), else the first register commit that holds the row;
- `script`: the first commit of the row's first script (followed across moves);
- `queued`: the study's first queue row created;
- `fetched`: its last fetch row finished `done`;
- `studied`: its last `study*` row finished `done`;
- `recorded`: the first register commit where the row's status turned to a verdict (none for a row
  backfilled with its verdict already in place).

And the queue's own split for the study's rows: hours waiting (created to started), running, and
failed until queued again (a failed or stopped row's finish to the next row of the same step's
creation). A negative span is a real order, not an error: a script shared from an earlier item
predates the declaration, a fetch can be added after a study ran. Deterministic; the git reads are
done by the caller and passed in.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
VERDICTS = ("passed", "failed", "no_verdict")
STAGES = ("declared", "script", "queued", "fetched", "studied", "recorded")
#: The spans reported between stages, each (name, from, to).
SPANS = (
    ("declare_to_script", "declared", "script"),
    ("script_to_queue", "script", "queued"),
    ("queue_to_fetched", "queued", "fetched"),
    ("fetched_to_studied", "fetched", "studied"),
    ("studied_to_recorded", "studied", "recorded"),
    ("declare_to_recorded", "declared", "recorded"),
)


def when(v) -> datetime | None:
    """A time as aware UTC: a git ISO date with its offset, or a queue time (ET, no offset)."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        t = v
    else:
        t = datetime.fromisoformat(str(v))
    if t.tzinfo is None:
        t = t.replace(tzinfo=ET)
    return t.astimezone(UTC)


def _hours(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 3600, 1)


def register_dates(history: Iterable[tuple[str, dict]]) -> dict[int, dict[str, datetime]]:
    """From the register's commits, oldest first, as (commit date, register): each row's first
    appearance (`declared`) and the first commit where its status turned to a verdict (`recorded`).
    A row that first appears already holding a verdict was backfilled when the register began: its
    recording happened before, at a date this history cannot give, so it has no `recorded`."""
    out: dict[int, dict[str, datetime]] = {}
    backfilled: set[int] = set()
    for date, reg in history:
        t = when(date)
        for row in reg.get("hypotheses", []):
            rid = row.get("id")
            if not isinstance(rid, int):
                continue
            verdict = row.get("status") in VERDICTS
            if rid not in out:
                out[rid] = {"declared": t}
                if verdict:
                    backfilled.add(rid)
            if verdict and rid not in backfilled and "recorded" not in out[rid]:
                out[rid]["recorded"] = t
    return out


def _is_fetch(task: dict) -> bool:
    return str(task.get("kind") or "").startswith("fetch")


def queue_split(tasks: list[dict]) -> dict[str, float]:
    """One study's rows: hours waiting (created to started), running (started to finished, rows
    that ran), and failed until queued again (a failed or stopped row's finish to the creation of
    the next row of the same step)."""
    waiting = running = requeue = 0.0
    by_step: dict[str, list[dict]] = {}
    for t in sorted(tasks, key=lambda t: t["id"]):
        by_step.setdefault(t.get("step") or "", []).append(t)
        c, s, f = when(t.get("created_at")), when(t.get("started_at")), when(t.get("finished_at"))
        if c and s:
            waiting += (s - c).total_seconds()
        if s and f and t.get("status") in ("done", "failed", "stopped"):
            running += (f - s).total_seconds()
    for rows in by_step.values():
        for a, b in zip(rows, rows[1:], strict=False):
            fa, cb = when(a.get("finished_at")), when(b.get("created_at"))
            if a.get("status") in ("failed", "stopped") and fa and cb and cb > fa:
                requeue += (cb - fa).total_seconds()
    return {k: round(v / 3600, 1) for k, v in (("waiting_h", waiting), ("running_h", running),
            ("failed_until_requeued_h", requeue))}  # fmt: skip


def study_stages(row: dict, reg_dates: dict[str, datetime], script_date, tasks: list[dict]) -> dict:
    """One register row's stage dates, the spans between them in hours, and its queue split."""
    mine = [t for t in tasks if t.get("study") == row.get("study")]
    done = [t for t in mine if t.get("status") == "done"]
    fetch_done = [when(t.get("finished_at")) for t in done if _is_fetch(t)]
    study_done = [when(t.get("finished_at")) for t in done if str(t.get("step") or "").startswith("study")]
    created = [when(t.get("created_at")) for t in mine if t.get("created_at")]
    stages = {
        "declared": reg_dates.get("declared"),
        "script": when(script_date),
        "queued": min(created) if created else None,
        "fetched": max(filter(None, fetch_done), default=None),
        "studied": max(filter(None, study_done), default=None),
        "recorded": reg_dates.get("recorded"),
    }
    # A study with no fetch of its own reads stores already held: its fetch stage is its queueing.
    if stages["fetched"] is None and stages["studied"] is not None:
        stages["fetched"] = stages["queued"]
    spans = {name: _hours(stages[a], stages[b]) for name, a, b in SPANS}
    return {
        "id": row.get("id"),
        "study": row.get("study"),
        "status": row.get("status"),
        "stages": {k: (v.isoformat() if v else None) for k, v in stages.items()},
        "spans_h": spans,
        "queue": queue_split(mine) if mine else None,
    }


def report(
    reg: dict,
    history: Iterable[tuple[str, dict]],
    script_dates: dict[str, str | None],
    tasks: list[dict],
    *,
    plan_dates: dict[int, str | None] | None = None,
    since_id: int = 18,
) -> dict:
    """Every non-legacy row from `since_id` on (the queue began with item 17's), and the median of
    each span and queue figure over the rows that have it. `plan_dates` (the commit that first added
    an item's `### N.` heading to the plan) date a declaration better than the register, which was
    begun after many items were declared; the register's first appearance is the fallback."""
    dates = register_dates(history)
    items = []
    for row in reg.get("hypotheses", []):
        rid = row.get("id")
        if row.get("legacy") or not isinstance(rid, int) or rid < since_id:
            continue
        d = dict(dates.get(rid, {}))
        if (plan_dates or {}).get(rid):
            d["declared"] = when(plan_dates[rid])
        scripts = row.get("scripts") or []
        items.append(study_stages(row, d, script_dates.get(scripts[0]) if scripts else None, tasks))
    medians = {}
    for name, _, _ in SPANS:
        vals = [i["spans_h"][name] for i in items if i["spans_h"][name] is not None]
        medians[name] = {"median_h": round(statistics.median(vals), 1) if vals else None, "n": len(vals)}
    for key in ("waiting_h", "running_h", "failed_until_requeued_h"):
        vals = [i["queue"][key] for i in items if i["queue"]]
        medians[key] = {"median_h": round(statistics.median(vals), 1) if vals else None, "n": len(vals)}
    # A median over recorded rows only would read the slowest recordings as fast: the studies still
    # waiting for theirs are named beside it.
    waiting = [i["id"] for i in items if i["stages"]["studied"] and not i["stages"]["recorded"]
               and i["status"] == "declared"]  # fmt: skip
    return {"items": items, "medians": medians, "studied_not_recorded": waiting}
