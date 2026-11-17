"""The Builder's backtest of one automation: one explore run queued on the research queue, and its run card.

**What the button queues.** One `script` row (the queue's own allow-listed kind) running the automation
backtest driver, `packages/backtest/scripts/automation_backtest.py run`, for the one saved automation:
over the window its backtest suggestion names and from the source that suggestion found covering it.
The suggestion is the automations module's own (`enrolment.suggestion`, read by subprocess through
`cli builder`), which asks our own stores only, never the vendor's, so nothing here reads licensed
data. An automation without a suggestion (every block backtested) or whose suggestion is not
possible (our data does not cover it) is refused, as is a second run while one is live.

**What it is not.** The run is descriptive: an explore run, no trial and no verdict, so no split is
declared and nothing moves a register label, a gate or a config. It reads stores and writes one result
file (`builder-<automation>-<YYMMDDHHMM>.json`, where the driver puts results). It never turns an
automation on; forward testing stays a separate step.

**The run card** (`runs`) is the automation's queue rows (study `builder`) and the results the backtest
package reads back (`backtest read automation-runs`, by subprocess), each arm's figures as the run
wrote them.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any

from cherrypick.core.clock import ET

from . import config as cfgmod
from . import researchqueue
from .util import CREATE_NO_WINDOW, first_json

STUDY = "builder"
DRIVER = "packages/backtest/scripts/automation_backtest.py"
#: An automation's name as the automations module allows it, and as a queue step it must also be a
#: slug (no double dash), which `researchqueue.add` checks.
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
LIVE = ("queued", "running")
TIMEOUT_S = 60


def _err(message: str, code: str = "bad_request", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def _run_json(argv: list[str], cwd: str | None = None) -> dict[str, Any] | None:
    try:
        r = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return first_json(r.stdout) or None


def module_builder(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """The automations module's Builder read (`cli builder`), or None when it could not be asked."""
    mcfg = (cfg.get("modules") or {}).get("automations") or {}
    return _run_json(
        [cfgmod.python_exe(), "-m", "cherrypick.automations.cli", "builder"],
        cwd=str(cfgmod.module_root(mcfg, "automations")),
    )


def backtest_runs(name: str) -> dict[str, Any] | None:
    """The backtest package's read of this automation's results, or None when it could not be asked."""
    return _run_json([sys.executable, "-m", "cherrypick.backtest", "read", "automation-runs", "--name", name])


def run_name(name: str, now: datetime) -> str:
    return f"{STUDY}-{name}-{now.astimezone(ET):%y%m%d%H%M}"


def _step(row: dict[str, Any], name: str) -> bool:
    return re.fullmatch(re.escape(name) + r"-\d{10}", str(row.get("step") or "")) is not None


def _chain_source(needs: list[dict[str, Any]]) -> str | None:
    """The source the driver should read: the chain need's, when the suggestion found one."""
    for need in needs:
        if need.get("field") == "chain" and need.get("source"):
            return str(need["source"])
    return None


Builder = Callable[[dict[str, Any]], "dict[str, Any] | None"]


def queue(
    cfg: dict[str, Any],
    req: dict[str, Any],
    *,
    builder: Builder = module_builder,
    add: Callable[..., dict] = researchqueue.add,
    rows: Callable[..., list[dict]] = researchqueue.rows,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Queue one explore backtest of saved automation `req["name"]`; refused unless its own backtest
    suggestion says our data covers it."""
    name = req.get("name")
    if not isinstance(name, str) or not NAME.match(name) or "--" in name:
        return _err("automation_backtest needs the 'name' of a saved automation")
    read = builder(cfg)
    if not read:
        return _err("the automations module could not be asked (is it installed?)", "unavailable")
    view = next((a for a in read.get("automations") or [] if a.get("name") == name), None)
    if view is None:
        return _err(f"no saved automation {name!r}", "not_found")
    sug = view.get("suggestion")
    if not sug:
        return _err("every block of this automation is backtested already; nothing to suggest", "invalid")
    if not sug.get("possible"):
        short = [n.get("field") for n in sug.get("needs") or [] if not n.get("covered")]
        return _err(
            f"our data does not cover a backtest of it ({', '.join(map(str, short)) or 'a need'})", "invalid"
        )
    live = [r for r in rows(study=STUDY) if _step(r, name) and r.get("status") in LIVE]
    if live:
        return _err(f"a backtest of {name} is already {live[0]['status']} (task {live[0]['id']})", "conflict")
    start, end = sug["window"]
    stamp = now or datetime.now(ET)
    run = run_name(name, stamp)
    args = [
        "run",
        "--arms",
        name,
        "--symbol",
        str(view["symbol"]),
        "--name",
        run,
        "--from",
        start,
        "--to",
        end,
    ]
    source = _chain_source(sug.get("needs") or [])
    if source:
        args += ["--source", source]
    try:
        row = add(STUDY, "script", {"path": DRIVER, "args": args}, step=run.removeprefix(f"{STUDY}-"))
    except researchqueue.Refused as exc:
        return _err(str(exc), "invalid")
    return {
        "ok": True,
        "name": name,
        "run": run,
        "task": row.get("id"),
        "window": [start, end],
        "source": source,
    }


def runs(
    cfg: dict[str, Any],
    req: dict[str, Any],
    *,
    rows: Callable[..., list[dict]] = researchqueue.rows,
    results: Callable[[str], dict[str, Any] | None] = backtest_runs,
) -> dict[str, Any]:
    """The run card for automation `req["name"]`: its queue rows, newest first, and its results."""
    name = req.get("name")
    if not isinstance(name, str) or not NAME.match(name):
        return _err("automation_runs needs an automation's 'name'")
    tasks = [
        {
            "id": r.get("id"),
            "run": f"{STUDY}-{r['step']}",
            "status": r.get("status"),
            "queued_at": r.get("created_at"),
            "started_at": r.get("started_at"),
            "finished_at": r.get("finished_at"),
            "exit_code": r.get("exit_code"),
            "note": r.get("note"),
        }
        for r in rows(study=STUDY)
        if _step(r, name)
    ]
    read = results(name)
    return {
        "ok": True,
        "name": name,
        "tasks": tasks,
        "runs": (read or {}).get("runs") or [],
        "results_unavailable": read is None or not read.get("ok"),
    }


def backtest_run(name: str, run: str) -> dict[str, Any] | None:
    """The backtest package's whole run card for one result, or None when it could not be asked."""
    argv = [
        sys.executable,
        "-m",
        "cherrypick.backtest",
        "read",
        "automation-run",
        "--name",
        name,
        "--id",
        run,
    ]
    return _run_json(argv)


def run_card(
    cfg: dict[str, Any],
    req: dict[str, Any],
    *,
    read: Callable[[str, str], dict[str, Any] | None] = backtest_run,
) -> dict[str, Any]:
    """One Builder run's whole card: `req["name"]` the automation, `req["run"]` its result's name."""
    name, run = req.get("name"), req.get("run")
    if not isinstance(name, str) or not NAME.match(name):
        return _err("automation_run needs an automation's 'name'")
    if not isinstance(run, str) or not re.fullmatch(re.escape(f"{STUDY}-{name}-") + r"\d{10}", run):
        return _err("automation_run needs one of its Builder runs as 'run'")
    out = read(name, run)
    if out is None:
        return _err("the backtest package could not be asked (is it installed?)", "unavailable")
    if not out.get("ok"):
        return _err(str(out.get("error") or "no such run"), "not_found")
    return out
