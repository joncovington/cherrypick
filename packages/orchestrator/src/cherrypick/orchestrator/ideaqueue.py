"""The Builder's idea search of one saved automation (#1294): one search queued on the research queue,
its results, and the two writes a person makes from one.

**What "Find ideas" queues.** One `script` row (the queue's own allow-listed kind) running the idea
search driver, `packages/backtest/scripts/idea_search.py run`, over the automation's newest Builder run
long enough to search. Which run, and the split, come from the backtest package's own plan (`backtest
ideas plan`, by subprocess), so the split is fixed in the queued command before the search reads
anything. A second search while one is live is refused, as is an automation with no long enough run.

**What it is not.** The search hands back hypotheses. It never changes an automation, a config, a gate
or a register label, and it never turns anything on. Its result goes beside the run it read, licensed
when that run was; the driver checks the licence again before it reads a licensed run.

**Declare and the held-out test** are the backtest package's (`backtest ideas declare|holdout`): a
surviving, testable idea's register row is drafted into its result, and a declared idea's one held-out
test is read and logged there. Neither writes a config or the register itself; the row is added to
`research/hypotheses.json` by hand.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any

from cherrypick.core.clock import ET

from . import researchqueue
from .util import CREATE_NO_WINDOW, first_json

STUDY = "ideas"
DRIVER = "packages/backtest/scripts/idea_search.py"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
#: An idea's id in a result: one or two conditions, `dim:kind:index`, joined by `&`.
IDEA = re.compile(r"^[a-z_A-Z:-]+:(bin|below|above):\d(&[a-z_A-Z:-]+:(bin|below|above):\d)?$")
LIVE = ("queued", "running")
TIMEOUT_S = 60


def _err(message: str, code: str = "bad_request", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def _backtest(argv: list[str]) -> dict[str, Any] | None:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "cherrypick.backtest", *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return first_json(r.stdout) or None


Backtest = Callable[[list[str]], "dict[str, Any] | None"]


def result_name(name: str, now: datetime) -> str:
    return f"{STUDY}-{name}-{now.astimezone(ET):%y%m%d%H%M}"


def _step(row: dict[str, Any], name: str) -> bool:
    return re.fullmatch(re.escape(name) + r"-\d{10}", str(row.get("step") or "")) is not None


def _name(req: dict[str, Any]) -> str | None:
    name = req.get("name")
    return name if isinstance(name, str) and NAME.match(name) and "--" not in name else None


def queue(
    cfg: dict[str, Any],
    req: dict[str, Any],
    *,
    backtest: Backtest = _backtest,
    add: Callable[..., dict] = researchqueue.add,
    rows: Callable[..., list[dict]] = researchqueue.rows,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Queue one idea search of saved automation `req["name"]` over its newest long enough run."""
    name = _name(req)
    if name is None:
        return _err("automation_ideas needs the 'name' of a saved automation")
    plan = backtest(["ideas", "plan", "--name", name])
    if plan is None:
        return _err("the backtest package could not be asked (is it installed?)", "unavailable")
    if not plan.get("ok"):
        return _err(str(plan.get("error") or "no run to search"), "invalid")
    live = [r for r in rows(study=STUDY) if _step(r, name) and r.get("status") in LIVE]
    if live:
        return _err(
            f"an idea search of {name} is already {live[0]['status']} (task {live[0]['id']})", "conflict"
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
        "search": out,
        "task": row.get("id"),
        "run": plan["run"],
        "split": plan["split"],
        "licensed": bool(plan.get("licensed")),
    }


def searches(
    cfg: dict[str, Any],
    req: dict[str, Any],
    *,
    backtest: Backtest = _backtest,
    rows: Callable[..., list[dict]] = researchqueue.rows,
) -> dict[str, Any]:
    """Automation `req["name"]`'s idea searches: its queue rows, newest first, and its results in brief."""
    name = _name(req)
    if name is None:
        return _err("automation_idea_searches needs an automation's 'name'")
    tasks = [
        {
            "id": r.get("id"),
            "search": f"{STUDY}-{r['step']}",
            "status": r.get("status"),
            "queued_at": r.get("created_at"),
            "finished_at": r.get("finished_at"),
            "note": r.get("note"),
        }
        for r in rows(study=STUDY)
        if _step(r, name)
    ]
    read = backtest(["read", "idea-searches", "--name", name])
    return {
        "ok": True,
        "name": name,
        "tasks": tasks,
        "searches": (read or {}).get("searches") or [],
        "results_unavailable": read is None or not read.get("ok"),
    }


def _one(req: dict[str, Any]) -> tuple[str, str] | dict[str, Any]:
    name = _name(req)
    rid = req.get("id")
    if name is None:
        return _err("needs an automation's 'name'")
    if not isinstance(rid, str) or not re.fullmatch(re.escape(f"{STUDY}-{name}-") + r"\d{10}", rid):
        return _err("needs one of its idea searches as 'id'")
    return name, rid


def search(cfg: dict[str, Any], req: dict[str, Any], *, backtest: Backtest = _backtest) -> dict[str, Any]:
    """One idea search's whole result."""
    one = _one(req)
    if isinstance(one, dict):
        return one
    out = backtest(["read", "idea-search", "--name", one[0], "--id", one[1]])
    if out is None:
        return _err("the backtest package could not be asked (is it installed?)", "unavailable")
    return out if out.get("ok") else _err(str(out.get("error") or "no such search"), "not_found")


def _verb(verb: str, req: dict[str, Any], backtest: Backtest) -> dict[str, Any]:
    one = _one(req)
    if isinstance(one, dict):
        return one
    idea = req.get("idea")
    if not isinstance(idea, str) or not IDEA.match(idea):
        return _err("needs one idea of that search as 'idea'")
    out = backtest(["ideas", verb, "--name", one[0], "--id", one[1], "--idea", idea])
    if out is None:
        return _err("the backtest package could not be asked (is it installed?)", "unavailable")
    return out if out.get("ok") else _err(str(out.get("error") or "refused"), "invalid")


def declare(cfg: dict[str, Any], req: dict[str, Any], *, backtest: Backtest = _backtest) -> dict[str, Any]:
    """Draft one surviving idea's register row into its result, the split already fixed."""
    return _verb("declare", req, backtest)


def holdout(cfg: dict[str, Any], req: dict[str, Any], *, backtest: Backtest = _backtest) -> dict[str, Any]:
    """A declared idea's one held-out test, logged on its result."""
    return _verb("holdout", req, backtest)
