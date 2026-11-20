"""The Builder's settings grid of one saved automation (#1294): one grid queued on the research queue,
its results, and the two writes a person makes from one.

**What "Try settings" queues.** One `script` row (the queue's own allow-listed kind) running the grid
driver, `packages/backtest/scripts/exit_grid.py run`, which replays the automation as its newest long
enough Builder run did, and every exit and width setting of the grid beside it, over that run's span
and store. Which run, and the split, come from the backtest package's own plan (`backtest grid plan`,
the idea search's plan, by subprocess), so the split is in the queued command before anything is
replayed. A second grid while one is live is refused, as is an automation with no long enough run. Each
setting is a full replay, so a grid takes queue time, not seconds.

**What it is not.** The grid hands back hypotheses. It never changes an automation, a config, a gate or
a register label, and it never turns anything on. Its result goes beside the run it replayed, licensed
when that run was; the driver checks the licence again before it reads a licensed store.

**Declare and the held-out test** are the backtest package's (`backtest grid declare|holdout`), as the
idea search's are: neither writes a config or the register.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from cherrypick.core.clock import ET

from . import researchqueue
from .ideaqueue import LIVE, Backtest, _backtest, _err, _name, _step

STUDY = "grid"
DRIVER = "packages/backtest/scripts/exit_grid.py"
#: A setting's id in a result: its axis and value (`profit_target_pct=50`, `width=x1.5`, `..=off`).
SETTING = re.compile(r"^[a-z_]+=(off|x?\d+(\.\d+)?)$")


def result_name(name: str, now: datetime) -> str:
    return f"{STUDY}-{name}-{now.astimezone(ET):%y%m%d%H%M}"


def queue(
    cfg: dict[str, Any],
    req: dict[str, Any],
    *,
    backtest: Backtest = _backtest,
    add: Callable[..., dict] = researchqueue.add,
    rows: Callable[..., list[dict]] = researchqueue.rows,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Queue one settings grid of saved automation `req["name"]` over its newest long enough run."""
    name = _name(req)
    if name is None:
        return _err("automation_grid needs the 'name' of a saved automation")
    plan = backtest(["grid", "plan", "--name", name])
    if plan is None:
        return _err("the backtest package could not be asked (is it installed?)", "unavailable")
    if not plan.get("ok"):
        return _err(str(plan.get("error") or "no run to replay"), "invalid")
    live = [r for r in rows(study=STUDY) if _step(r, name) and r.get("status") in LIVE]
    if live:
        return _err(
            f"a settings grid of {name} is already {live[0]['status']} (task {live[0]['id']})", "conflict"
        )
    out = result_name(name, now or datetime.now(ET))
    args = ["run", "--name", name, "--run", str(plan["run"]), "--split", str(plan["split"]), "--out", out]
    try:
        row = add(STUDY, "script", {"path": DRIVER, "args": args}, step=out.removeprefix(f"{STUDY}-"))
    except researchqueue.Refused as exc:
        return _err(str(exc), "invalid")
    return {
        "ok": True,
        "name": name,
        "grid": out,
        "task": row.get("id"),
        "run": plan["run"],
        "split": plan["split"],
        "licensed": bool(plan.get("licensed")),
    }


def grids(
    cfg: dict[str, Any],
    req: dict[str, Any],
    *,
    backtest: Backtest = _backtest,
    rows: Callable[..., list[dict]] = researchqueue.rows,
) -> dict[str, Any]:
    """Automation `req["name"]`'s settings grids: its queue rows, newest first, and its results in brief."""
    name = _name(req)
    if name is None:
        return _err("automation_grids needs an automation's 'name'")
    tasks = [
        {
            "id": r.get("id"),
            "grid": f"{STUDY}-{r['step']}",
            "status": r.get("status"),
            "queued_at": r.get("created_at"),
            "finished_at": r.get("finished_at"),
            "note": r.get("note"),
        }
        for r in rows(study=STUDY)
        if _step(r, name)
    ]
    read = backtest(["read", "settings-grids", "--name", name])
    return {
        "ok": True,
        "name": name,
        "tasks": tasks,
        "grids": (read or {}).get("grids") or [],
        "results_unavailable": read is None or not read.get("ok"),
    }


def _one(req: dict[str, Any]) -> tuple[str, str] | dict[str, Any]:
    name = _name(req)
    rid = req.get("id")
    if name is None:
        return _err("needs an automation's 'name'")
    if not isinstance(rid, str) or not re.fullmatch(re.escape(f"{STUDY}-{name}-") + r"\d{10}", rid):
        return _err("needs one of its settings grids as 'id'")
    return name, rid


def grid(cfg: dict[str, Any], req: dict[str, Any], *, backtest: Backtest = _backtest) -> dict[str, Any]:
    """One settings grid's rows (never its trades)."""
    one = _one(req)
    if isinstance(one, dict):
        return one
    out = backtest(["read", "settings-grid", "--name", one[0], "--id", one[1]])
    if out is None:
        return _err("the backtest package could not be asked (is it installed?)", "unavailable")
    return out if out.get("ok") else _err(str(out.get("error") or "no such grid"), "not_found")


def _verb(verb: str, req: dict[str, Any], backtest: Backtest) -> dict[str, Any]:
    one = _one(req)
    if isinstance(one, dict):
        return one
    setting = req.get("setting")
    if not isinstance(setting, str) or not SETTING.match(setting):
        return _err("needs one setting of that grid as 'setting'")
    out = backtest(["grid", verb, "--name", one[0], "--id", one[1], "--setting", setting])
    if out is None:
        return _err("the backtest package could not be asked (is it installed?)", "unavailable")
    return out if out.get("ok") else _err(str(out.get("error") or "refused"), "invalid")


def declare(cfg: dict[str, Any], req: dict[str, Any], *, backtest: Backtest = _backtest) -> dict[str, Any]:
    """Draft one surviving setting's register row into its result, the split already fixed."""
    return _verb("declare", req, backtest)


def holdout(cfg: dict[str, Any], req: dict[str, Any], *, backtest: Backtest = _backtest) -> dict[str, Any]:
    """A declared setting's one held-out test, logged on its result."""
    return _verb("holdout", req, backtest)
