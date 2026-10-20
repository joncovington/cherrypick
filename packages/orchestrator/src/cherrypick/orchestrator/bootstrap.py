"""`run.py bootstrap` -- the evening jobs that build the income lists' universe, run once, now.

**Why.** On a fresh install those jobs first run at their own times (18:35 to 18:57 ET), and each
reads the one before: the dollar volume needs the symbol directory and OCC volume the market-files
run lands, the security list needs the dollar volume, the IV-rank fetch reads the security list, and
the liquidity verdict judges by the IV-rank fetch. Until all five have run, the 14:45 income chains
have no names, so a new user waits a day or two for a first list. The installer runs this once, at
the end of an install or an upgrade, so the next session's 14:45 run has its universe.

**The commands are the supervisor's own** (`jobspec.derive_jobs`), in that order, so this can never
drift from what the schedule runs. A job the config has off is skipped and said so; one that fails
is reported and the rest still run (each writes its own file, and a later one reads yesterday's
where today's is missing). Foreground and headless: no window, each with a timeout. It places no
order and touches no ledger.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import CREATE_NO_WINDOW

# In dependency order (see the module note).
BOOTSTRAP_JOBS = (
    "market-files",
    "technicals-dollar-volume",
    "technicals-securities",
    "technicals-iv-rank",
    "technicals-liquidity",
)
TIMEOUT_S = 20 * 60


@dataclass(frozen=True)
class Step:
    job: str
    argv: tuple[str, ...] | None
    cwd: str | None
    skip: str | None  # why it is not run, or None


def plan(jobs: list[Any], python: str) -> list[Step]:
    """The bootstrap steps from the derived jobs: each job's own argv with `python` in place of the
    windowless interpreter, so its report can be read; a missing or disabled job is a skipped step."""
    by_id = {j.id: j for j in jobs}
    steps = []
    for jid in BOOTSTRAP_JOBS:
        j = by_id.get(jid)
        if j is None:
            steps.append(Step(jid, None, None, "not derived from this config"))
        elif not j.enabled:
            steps.append(Step(jid, None, None, j.enabled_reason or "disabled"))
        else:
            steps.append(Step(jid, (python, *tuple(j.argv)[1:]), j.cwd, None))
    return steps


def run(steps: list[Step], runner=None) -> dict[str, Any]:
    """Run each step in turn; {ok, steps: [{job, ok, exit, seconds, skipped, tail}]}. `ok` is true
    when every step that ran exited 0."""
    runner = runner or _subprocess
    out = []
    for s in steps:
        if s.skip is not None:
            out.append({"job": s.job, "skipped": s.skip})
            continue
        t0 = time.monotonic()
        code, tail = runner(s)
        out.append(
            {
                "job": s.job,
                "ok": code == 0,
                "exit": code,
                "seconds": round(time.monotonic() - t0),
                "tail": tail,
            }
        )
    return {"ok": all(r.get("ok", True) for r in out), "steps": out}


def _subprocess(s: Step) -> tuple[int, str]:
    try:
        p = subprocess.run(
            list(s.argv or ()),
            cwd=s.cwd or str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {TIMEOUT_S // 60} min"
    except OSError as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    text = (p.stdout or "") + (p.stderr or "")
    return p.returncode, text.strip()[-400:]
