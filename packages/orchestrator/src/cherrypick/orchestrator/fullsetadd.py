"""Adding an underlying to the full set from the Builder: the data a backtest needs that the set does not
plan, fetched by the research queue.

**`plan`** (`fullset_plan`): what adding the symbol would fetch, from the backtest package's own planner
(`fetch_full_set.py add SYM --dry-run`, by subprocess): requests per part and the sessions it spans.
Writes nothing.

**`add`** (`fullset_add`): records the addition and writes its request files (`fetch_full_set.py add`),
then queues its rows from the script's `ADD_CHAIN` (`studychain.read_chain`), each after the one before,
in study `fullset`. Refused unless new work may use the vendor (`backtestgate.backtest_feature`), and
for more than `CAP_SESSIONS` sessions in one click: an addition starts from the run's window (a year
back by default), and a longer one is the script's, run by hand. Its rows run at `PRIORITY`, just
above the seven's (the set comes before any other queue row; an addition someone is waiting on comes
first within it). With `run` naming a saved automation, its explore backtest is queued after the
addition's last row (`automationrun.queue`'s `after`), so the click runs through to a result.

It places no order, moves no gate and writes no config: an addition is the set's own machine state
(its additions file and request files, under the licensed results) and queue rows.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from typing import Any

from . import automationrun, backtestgate, researchqueue, studychain
from .util import CREATE_NO_WINDOW, first_json

SCRIPT = "packages/backtest/scripts/fetch_full_set.py"
STUDY = "fullset"
#: Sessions one click may add (owner, 2026-11-16: a cap per click, 300 sessions).
CAP_SESSIONS = 300
#: Above the seven's rows (10), below rows queued ahead of the whole set.
PRIORITY = 11
TIMEOUT_S = 120


def _err(message: str, code: str = "bad_request", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def run_script(args: list[str]) -> dict[str, Any] | None:
    """The full-set script's JSON answer, or None when it could not be asked."""
    root = researchqueue.repo_root()
    try:
        r = subprocess.run(
            [sys.executable, str(root / SCRIPT), *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return first_json(r.stdout) or None


def _symbol(req: dict[str, Any]) -> str | None:
    s = req.get("symbol")
    if not isinstance(s, str):
        return None
    s = s.strip().upper()
    return s if researchqueue.ADDED_SYMBOL.match(s) else None


def _script_args(symbol: str, req: dict[str, Any]) -> list[str] | None:
    args = ["add", symbol]
    start = req.get("from")
    if start is not None:
        if not isinstance(start, str) or len(start) != 10:
            return None
        args += ["--from", start]
    return args


def plan(cfg: dict[str, Any], req: dict[str, Any], run: Callable = run_script) -> dict[str, Any]:
    """What adding `req["symbol"]` (from `req["from"]`, default a year back) would fetch."""
    symbol = _symbol(req)
    args = _script_args(symbol, req) if symbol else None
    if args is None:
        return _err("fullset_plan needs an underlying's 'symbol' and, optionally, 'from' (YYYY-MM-DD)")
    doc = run([*args, "--dry-run"])
    if not doc:
        return _err("the backtest package could not be asked (is it installed?)", "unavailable")
    if not doc.get("ok"):
        return _err(str(doc.get("error")), "invalid")
    return {**doc, "cap_sessions": CAP_SESSIONS, "within_cap": doc["requests"]["sessions"] <= CAP_SESSIONS}


def add(
    cfg: dict[str, Any],
    req: dict[str, Any],
    *,
    run: Callable = run_script,
    feature: Callable[[], dict] = backtestgate.backtest_feature,
    queue_rows: Callable[..., dict] = studychain.queue_rows,
    queue_run: Callable[..., dict] = automationrun.queue,
) -> dict[str, Any]:
    """Add `req["symbol"]` to the full set and queue its fetches; with `req["run"]`, that automation's
    explore backtest after them."""
    gate = feature()
    if not gate.get("available"):
        return _err("new work may not use the vendor: " + "; ".join(gate.get("reasons") or []), "unavailable")
    planned = plan(cfg, req, run)
    if not planned.get("ok"):
        return planned
    if not planned["within_cap"]:
        n = planned["requests"]["sessions"]
        return _err(f"{n} sessions is more than one click adds ({CAP_SESSIONS}); start later, or run "
                    f"{SCRIPT} add by hand", "invalid", plan=planned)  # fmt: skip
    symbol, start = planned["symbol"], planned["from"]
    root = researchqueue.repo_root()
    subs = {"symbol": symbol, "sym": symbol.lower().replace(".", "-"), "from": start}
    chain = studychain.read_chain(root / SCRIPT, "ADD_CHAIN", subs)
    rows = studychain.plan_rows(chain, STUDY, SCRIPT, studychain.results_for(STUDY))
    for r in rows:
        r["priority"] = PRIORITY
    try:
        # A row of the same name still live refuses before anything is written. Each row's arguments
        # are checked when it is queued, once the script has written the files they name.
        queue_rows(rows, STUDY, dry_run=True)
    except (researchqueue.Refused, ValueError) as exc:
        return _err(str(exc), "conflict" if "already queued" in str(exc) else "invalid")
    written = run(_script_args(symbol, req))
    if not written or not written.get("ok"):
        return _err(str((written or {}).get("error") or "the addition could not be recorded"), "unavailable")
    try:
        queued = queue_rows(rows, STUDY)
    except (researchqueue.Refused, ValueError) as exc:
        return _err(str(exc), "invalid")
    last = f"{STUDY}/{rows[-1]['step']}"
    out = {"ok": True, "symbol": symbol, "from": start, "through": planned["through"],
           "requests": planned["requests"], "tasks": [a.get("id") for a in queued.get("added") or []],
           "last": last}  # fmt: skip
    if req.get("run"):
        out["run"] = queue_run(cfg, {"name": req["run"], "after": last})
    return out
