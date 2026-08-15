#!/usr/bin/env python3
"""Run one advisor checkpoint — the second (and last) place AI touches this suite.

Deliberately NOT a package, for the same reason `eod_narrative.py` is not: `packages/*` is what the
trading loops import, so a script the scheduler runs cannot be imported by a loop, no package
acquires an API key or a network dependency, and deleting this file costs the advice and nothing
else. Everything deterministic lives in `cherrypick.advisor`, which this script drives by
subprocess and which contains no AI at all.

Eight checkpoints a day: seven light ones through the session (~9:45 / 10:30 / 11:30 / 12:30 / 13:30
/ 14:30 / 15:30 ET) on a cheap model, and one deep run after the close on the strong one. The light
slots observe and draft; the deep slot designs experiments, passes verdicts over numbers it was
given, and issues tomorrow's advice.

What holds this together:

**The model gets a fact pack on stdin and no tools.** Not a database, not a shell, not a browser.
Every claim it can make traces to a number in the pack.

**The script writes; the agent never does.** The raw reply lands on disk here, and the package
parses and validates it. Nothing the model says reaches a loop except through
`cherrypick.core.advice`, which re-validates it against bounds a human declared.

**Enact runs whether or not the AI did.** An outage must never truncate an active A/B sample, so
the deep slot's final step is unconditional — after a timeout, after a parse failure, after a
missing `claude` binary.

**It can only ever fail to produce advice.** Every failure path prints an envelope and exits 0. The
loops run baseline when advice is absent, which is exactly what `core.advice` guarantees.

Usage:
    python scripts/advisor_checkpoint.py --slot {open,am1,am2,midday,pm1,pm2,close,deep}
                                         [--session YYYY-MM-DD] [--model NAME] [--timeout SECONDS]
                                         [--modules csv] [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Deliberately not imported from cherrypick.advisor: this script must run even when the package is
# not importable in this interpreter (it drives the package by subprocess), and the store layout is
# a published contract rather than an implementation detail.
STORE = Path(
    os.environ.get("ADVISOR_DATA_DIR")
    or Path(os.environ.get("CHERRYPICK_HOME") or (Path.home() / ".cherrypick")) / "data" / "advisor"
)

# The agent gets no tools that can act. It reads what is on stdin and returns text; the script --
# never the agent -- puts anything on disk.
DISALLOWED = ["Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task"]
TIMEOUT_SECONDS = 600
LIGHT_SLOTS = ("open", "am1", "am2", "midday", "pm1", "pm2", "close")
DEEP_SLOT = "deep"

# Windows: keep every child headless. The scheduled parent is pythonw, which has no console, so a
# console-subsystem child launched without this flag creates a brand-new VISIBLE one -- a terminal
# flashing on the user's screen four times a day.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_DATA_LITERACY = """
Things about this data that will mislead you if you do not know them:

- `null` means NOT RECORDED. It never means zero. Do not average nulls as zeros, and never report a
  null as an absence of cost or risk.
- Arms are the experiment. `control`, `width-5`, `gex`, `time_window` and the rest run against the
  same underlying on the same sessions, so comparing them is worth far more than the raw trade
  count suggests. A module-level total averages them and hides the finding.
- A handful of trades on one session is ONE observation, not N. Say so when the sample is thin
  rather than reasoning as though it were not.
- A flies book's floor is only meaningful together with the price band it holds over. Never quote
  one without the other.
- Facts under `live` are READ-ONLY CONTEXT. Nothing you propose can reach a live account — the only
  output this pipeline can produce is a bounded paper advice artifact. If something about live
  posture concerns you, put it in `flags` or `creative`; there is no other channel and inventing
  one produces a rejected proposal.
- `advisor_journal` is your own recent history, including proposals a human DISMISSED. Do not
  re-propose those. Build on the threads instead.
- `arm_readings.<module>.collisions` lists arm tags whose readings are byte-identical across
  sample/win_rate/days/net_pnl/sharpe/max_drawdown — either the same book trading under two names,
  or a config mistake that never differentiated them. Treat colliding tags as ONE data point, not
  two, when reasoning about a module's arms; this list is provided, not something to re-derive by
  eyeballing the readings yourself.
"""

_OUTPUT_CONTRACT = """
Reply with ONE JSON object and nothing else — no prose before or after, no code fence needed:

{
  "observations": ["short factual statements about today"],
  "flags": [{"module": "meic|flies|earnings|suite", "severity": "info|warn|critical", "text": "..."}],
  "proposals": [ ...zero or more of the kinds below... ]
}

Proposal kinds:

- {"kind": "bounded_adjustment", "module": "...", "sessions": 15, "hypothesis": "...",
   "params": [{"param": "...", "value": ..., "rationale": "..."}]}
     A parameter change to run as a paper experiment. Every param MUST be a key in that module's
     `bounds` manifest and every value inside its declared range. ONE out-of-bounds value rejects
     the WHOLE proposal — that is deliberate, so do not pad a good proposal with a speculative one.

- {"kind": "experiment_spec", "module": "...", "name": "...", "hypothesis": "...",
   "success_metric": "...", "sessions": 15, "params": [...]}
     The same thing with a name and a stated success metric. Prefer this when you are testing an
     idea rather than nudging a number.

- {"kind": "tune", "experiment_id": "exp-...", "params": [...], "rationale": "..."}
     Adjust an experiment YOU started and that is currently active. Naming a control arm, a
     human-configured profile, or an unknown id is rejected.

- {"kind": "creative", "module": "...", "title": "...", "text": "...", "spec_json": {...}}
     Anything you cannot express in bounds: a new arm, a new strategy family, a change to how a
     module screens or sizes, even a whole new module. These are PROPOSE-ONLY and never applied
     automatically — a human reads them. Include a ready-to-paste `spec_json` when you can.

- {"kind": "verdict", "experiment_id": "exp-...", "recommendation": "keep|kill|promote",
   "rationale": "..."}
     Your reading of an experiment's numbers. Cite the specific checks in the pack's
     `qualification` / `arm_readings` — the numbers are computed for you, and a recommendation that
     ignores them is worth nothing.

If you have nothing worth proposing, return an empty `proposals` list. A quiet day honestly
described is more useful than a proposal manufactured to fill the slot.
"""

LIGHT_PROMPT = f"""You are the intraday observer for a personal options-trading suite. You are
reading one checkpoint's FACT PACK as JSON on stdin. It is everything you have: no database, no
tools, no way to check anything else.

Your job at this checkpoint is to OBSERVE and FLAG, and to draft at most one or two proposals if
something in today's data genuinely warrants it. Depth belongs to the post-close run, which will see
your drafts — they carry forward, so a half-formed observation now is not wasted.
{_DATA_LITERACY}
{_OUTPUT_CONTRACT}
Keep observations short and specific. Prefer one sentence that names a number to a paragraph that
does not.
"""

DEEP_PROMPT = f"""You are the post-close analyst for a personal options-trading suite. You are
reading today's deep FACT PACK as JSON on stdin. It is everything you have: no database, no tools,
no way to check anything else.

This pack additionally carries `review_today` (the cross-module fact set — PROVISIONAL at this hour,
because the earnings module settles overnight; do not treat its earnings numbers as final),
`review_trend`, `arm_readings` (every arm's reading and its qualification checks), `bounds` (exactly
which parameters each module will accept advice about, and between which values), `experiments_full`,
`advice_audit`, and `advisor_journal`.

Your job:

1. Read the day, per module, leading with anything that needs attention.
2. Judge every ACTIVE experiment against its control, and issue a `verdict` for any that has
   accumulated enough evidence. Cite the qualification checks you were given. An experiment below
   the sample and day thresholds is UNDERPOWERED — say that rather than passing or failing it.
3. Design at most one new experiment per module, strictly inside the provided `bounds`. State a
   hypothesis and a success metric you could later be judged against.
4. Where the interesting idea does not fit in bounds, say it as `creative`. Reach as far as you
   like there — a new arm, a new strategy family, a whole new module — with a ready-to-paste spec.
{_DATA_LITERACY}
{_OUTPUT_CONTRACT}
Be direct where the numbers are clear and explicitly uncertain where they are thin. Do not flatter
the results: a losing arm described plainly is more useful than one softened.
"""


def _session_today() -> str:
    return datetime.now(ET).date().isoformat()


def _is_trading_day(session: str) -> bool:
    """The NYSE calendar, through core when it is importable.

    A weekend/holiday fallback keeps the gate meaningful on a machine where the package is not
    installed — the point of the gate is to never spend a paid call on a day with no session, and a
    slightly coarse gate does that better than no gate.
    """
    try:
        from cherrypick.core import calendar as _calendar

        return _calendar.is_trading_day(date.fromisoformat(session))
    except Exception:  # noqa: BLE001 -- an import failure must not stop the run's own reporting
        return date.fromisoformat(session).weekday() < 5


def _advisor(*argv: str, timeout: int = 300) -> tuple[dict | None, str | None]:
    """Run one `python -m cherrypick.advisor` verb and parse its single JSON object."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "cherrypick.advisor", *argv],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return None, f"advisor {argv[0]} timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    try:
        return json.loads(proc.stdout or "{}"), None
    except ValueError:
        detail = (proc.stderr or proc.stdout or "")[:500]
        return None, f"advisor {argv[0]} returned no JSON: {detail}"


def _run_claude(prompt: str, payload: str, model: str | None, timeout: int) -> tuple[str | None, str | None]:
    exe = shutil.which("claude")
    if not exe:
        return None, "claude not on PATH"
    argv = [exe, "-p", prompt]
    if model:
        # The model name lives in config and travels on argv. No model id is hardcoded anywhere in
        # this suite: changing which model runs a slot must never require a code change.
        argv += ["--model", model]
    argv += ["--disallowed-tools", *DISALLOWED]
    try:
        proc = subprocess.run(
            argv, input=payload, capture_output=True, text=True,
            # UTF-8 explicitly: `text=True` alone decodes with the locale encoding (cp1252 on
            # Windows), which turns every em dash the model emits into mojibake.
            encoding="utf-8", errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return None, f"claude timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001 -- advice is never worth raising over
        return None, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or "")[:500] or f"claude exited {proc.returncode}"
    text = (proc.stdout or "").strip()
    return (text, None) if text else (None, "claude returned nothing")


def _warn(session: str, slot: str, error: str) -> None:
    """Best-effort WARNING through the orchestrator's notifier — never CRITICAL.

    A missing checkpoint is a day without advice, which the loops handle by running baseline. It is
    worth telling someone; it is not worth waking them. Import-guarded so a machine without the
    orchestrator installed loses the notification and keeps the envelope.
    """
    try:
        from cherrypick.notify.notifier import Notifier
        from cherrypick.orchestrator import config as cfgmod

        Notifier(cfgmod.load_config().get("notify")).notify(
            "WARNING",
            "advisor.checkpoint",
            f"Advisor checkpoint failed — {session} {slot}",
            f"{error}\n\nRe-run manually:\n"
            f"python scripts/advisor_checkpoint.py --slot {slot} --session {session} --force",
        )
    except Exception:  # noqa: BLE001
        pass


def _envelope(payload: dict) -> int:
    print(json.dumps(payload, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slot", required=True, choices=[*LIGHT_SLOTS, DEEP_SLOT])
    ap.add_argument("--session", default=None, help="YYYY-MM-DD; default today (ET)")
    ap.add_argument("--model", default=None, help="model name; comes from config via the jobspec")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)
    ap.add_argument("--modules", default=None, help="csv subset of meic,flies,earnings")
    ap.add_argument("--force", action="store_true", help="re-run a slot that is already frozen")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the pack and the prompt; invoke nothing, write nothing")
    args = ap.parse_args()

    session = args.session or _session_today()
    slot = args.slot
    prompt = DEEP_PROMPT if slot == DEEP_SLOT else LIGHT_PROMPT
    result: dict = {"ok": True, "session": session, "slot": slot, "model": args.model}

    # 1. Calendar gate FIRST: never spend a paid call on a day with no session.
    if not _is_trading_day(session):
        return _envelope({**result, "skipped": "not a trading day"})

    # 2. Idempotence: a completed slot is frozen. The record of what the model saw and said on a
    #    given afternoon should not quietly become a different record.
    checkpoint = STORE / "checkpoints" / f"{session}-{slot}.json"
    if checkpoint.exists() and not args.force and not args.dry_run:
        return _envelope({**result, "skipped": "slot already recorded (frozen); pass --force"})

    # 3. Build the pack. Deterministic, in-package, no AI.
    pack_argv = ["factpack", "--slot", slot, "--session", session]
    if args.modules:
        pack_argv += ["--modules", args.modules]
    built, error = _advisor(*pack_argv)
    if error or not (built or {}).get("ok"):
        error = error or (built or {}).get("error") or "factpack failed"
        _warn(session, slot, error)
        return _envelope({**result, "ok": False, "error": error})

    pack_path = Path(built["pack"])
    payload = pack_path.read_text(encoding="utf-8")

    if args.dry_run:
        print(f"--- prompt ({slot}) ---\n{prompt}\n--- pack ({built['bytes']} bytes) ---\n{payload}")
        return 0

    # 4-6. The fenced call, then the package validates whatever came back. Wrapped so that step 7
    #      runs even when this whole stretch fails.
    try:
        reply, error = _run_claude(prompt, payload, args.model, args.timeout)
        if reply is None:
            _warn(session, slot, error or "claude produced nothing")
            result.update({"ok": False, "error": error})
        else:
            raw_path = STORE / "checkpoints" / f"{session}-{slot}.raw.txt"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(reply, encoding="utf-8")

            admit_argv = ["admit", "--slot", slot, "--session", session, "--raw", str(raw_path)]
            if args.model:
                admit_argv += ["--model", args.model]
            admitted, error = _advisor(*admit_argv)
            if error or not (admitted or {}).get("ok"):
                error = error or (admitted or {}).get("error") or "admit failed"
                _warn(session, slot, error)
                result.update({"ok": False, "error": error, "raw": str(raw_path)})
            else:
                result.update({
                    "raw": str(raw_path),
                    "pack": str(pack_path),
                    "admitted": len(admitted.get("admitted") or []),
                    "rejected": len(admitted.get("rejected") or []),
                })
    finally:
        # 7. Deep slot enacts UNCONDITIONALLY. An AI outage must not truncate an active A/B sample:
        #    the experiment is the measurement, and a hole in the middle of one is worse than a day
        #    with no new advice.
        if slot == DEEP_SLOT:
            enact_argv = ["enact", "--session", session]
            if args.modules:
                enact_argv += ["--modules", args.modules]
            enacted, enact_error = _advisor(*enact_argv)
            if enact_error or not (enacted or {}).get("ok"):
                enact_error = enact_error or (enacted or {}).get("error") or "enact failed"
                _warn(session, slot, f"enact failed: {enact_error}")
                result["enact_error"] = enact_error
                result["ok"] = False
            else:
                result["enacted"] = [m for m in enacted.get("enacted") or [] if m.get("written")]
                result["concluded"] = enacted.get("concluded") or []
                result["target_session"] = enacted.get("target_session")

    return _envelope(result)


if __name__ == "__main__":
    sys.exit(main())
