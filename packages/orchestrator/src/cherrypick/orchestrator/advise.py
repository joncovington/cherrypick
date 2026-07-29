"""Per-session parameter advice — Tier 1 of the agentic layer (architecture A: agent advises,
loop decides).

`cherrypick advise` runs an out-of-band Claude call over the day's **deterministic** artifacts
(module EOD reports, the suite digest, report/calibrate JSON, the shared market calendar) and
asks for bounded parameter proposals for the NEXT session. The proposals pass the
`cherrypick.core.advice` validator against the module's `advice_bounds` manifest, and the
orchestrator — never the agent — writes the artifact to `state/advice/<module>-<session>.json`.
A paper loop reads it once at its own session start and re-validates with the same core code;
absent, stale, or invalid advice means baseline behavior.

Cloned from `eod_insight.py`'s proven fencing, with one deliberate tightening: **no tools at
all** (eod_insight may WebSearch for its forward-looking prose; advice must be a function of
the deterministic inputs only, so the same inputs can be replayed against a proposal later).

Guardrails, all load-bearing:
  - **Feature-detected + opt-in twice.** `advise.enabled` AND `advise.modules.<name>.enabled`
    must both be true, and `claude` must be on PATH. Default off everywhere.
  - **Bounded by construction.** Every proposal must name a param in the module's
    `advice_bounds` (closed ranges / enumerated choices); one violation rejects the whole
    artifact (the rejects are still written, for audit). The loop re-checks with the same code.
  - **Single-session TTL.** The artifact names its target session and expires at that
    session's end (ET); the validator enforces both at read time.
  - **Best-effort, off the reliability path.** Any failure returns an error envelope; the
    watchdog never waits on or alerts about advice. Kill switch: flip the config, or simply
    delete the advice file.

Paper loops only; nothing here can touch a live order path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from cherrypick.core import advice as core_advice

from . import calibrate as calibrate_mod
from . import config as cfgmod
from . import report as report_mod
from . import timeutil
from .eod_insight import _gather_inputs, _upcoming_calendar, build_input
from .util import CREATE_NO_WINDOW

ADVISOR = "claude -p / cherrypick-advise-v1"

# Everything is denied — advice must be a pure function of the deterministic inputs on stdin,
# so a proposal can always be interrogated against exactly what the advisor saw.
_DISALLOWED_TOOLS = ["Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task"]

_SYSTEM = (
    "You are a quantitative trading-parameter advisor for the cherrypick PAPER-trading suite. You "
    "understand 0DTE and defined-risk options mechanics (GEX/dealer positioning, IV rank vs realized, "
    "theta/gamma near expiry, credit-to-width, per-side stops, IV crush, fee drag). You are asked for "
    "BOUNDED parameter proposals for the next paper session, and your proposals pass a deterministic "
    "validator: any parameter not in the declared advice_bounds manifest, or any value outside its "
    "closed range / choice list, rejects the ENTIRE proposal set. Propose only what the evidence in the "
    "provided reports supports; an empty proposal list is a perfectly good answer and is preferred over "
    "speculation. All data is paper/simulated; this is research, never financial advice. "
    "Do not use any tools. Output ONLY the JSON object requested — no prose, no code fences."
)


def _claude_available() -> str | None:
    """Path to the `claude` executable, or None. Injectable seam for tests."""
    return shutil.which("claude")


def _next_session(cfg: dict[str, Any], day: str) -> str:
    """The session the advice targets: the next NYSE trading day after `day` (advice generated
    post-close applies to tomorrow's loop, which reads it at session start)."""
    try:
        from cherrypick.core import calendar as _cal

        d = date.fromisoformat(day)
        for _ in range(10):
            d = d + timedelta(days=1)
            if d.weekday() < 5 and d not in set(_cal.nyse_holidays(d.year)):
                return d.isoformat()
    except Exception:
        pass
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _expires_at(session: str, tz_name: str = "America/New_York") -> str:
    """End of the target session day in ET — the artifact is dead the moment its session is over."""
    d = date.fromisoformat(session)
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=ZoneInfo(tz_name)).isoformat()


def _context_blocks(cfg: dict[str, Any], day: str) -> str:
    """The deterministic inputs, as one delimited text block: the day's module EOD reports and
    suite digest (eod_insight's gathering, reused), the cross-module report JSON, the calibrate
    readings JSON, and the shared market calendar. Every piece is best-effort — a missing input
    shrinks the context, never blocks the run. Injectable seam for tests."""
    parts = [build_input(_gather_inputs(cfg, day))]
    try:
        rep = report_mod.run(cfg, session=day)
        parts.append(
            "===== cross-module report JSON (session "
            + day
            + ") =====\n"
            + json.dumps(rep, indent=2, default=str)[:20000]
        )
    except Exception:
        pass
    try:
        cal = calibrate_mod.run(cfg)
        parts.append(
            "===== calibration readings JSON =====\n" + json.dumps(cal, indent=2, default=str)[:20000]
        )
    except Exception:
        pass
    upcoming = _upcoming_calendar(day)
    if upcoming:
        parts.append(upcoming)
    return "\n\n".join(p for p in parts if p)


def _prompt(module: str, session: str, bounds: dict[str, Any]) -> str:
    return (
        f"Below are the cherrypick suite's deterministic paper-trading artifacts. Propose parameter "
        f"values for the `{module}` module's PAPER session of {session} (the next trading day).\n\n"
        f"You may ONLY propose parameters from this advice_bounds manifest, and every value must lie "
        f"inside its closed range (min/max, inclusive) or choice list:\n"
        f"{json.dumps(bounds, indent=2)}\n\n"
        "Rules:\n"
        "- Propose a parameter only when the evidence in the reports clearly supports moving it for "
        "this specific session (regime, calendar events, cost drag, stop behavior). Otherwise leave "
        "it out — an empty list is a good answer.\n"
        "- One short, concrete rationale per proposal, citing the evidence (e.g. 'FOMC tomorrow "
        "13:30 blackout; VIX1D ratio elevated').\n"
        "- Never propose loosening into the aggressive end of a range without explicit supporting "
        "evidence from the reports.\n\n"
        "Output ONLY this JSON object (no prose, no code fences):\n"
        '{"proposals": [{"param": "<name>", "value": <number-or-choice>, "rationale": "<short>"}]}'
    )


def _run_claude(prompt: str, stdin_text: str, model: str | None, timeout: int) -> dict:
    """Headless Claude call, all tools denied. Injectable seam so tests never call the real CLI."""
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "text",
        "--disallowed-tools",
        *_DISALLOWED_TOOLS,
        "--append-system-prompt",
        _SYSTEM,
    ]
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": f"claude invocation failed: {exc}"}
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "claude nonzero exit").strip()[:300]}
    text = (r.stdout or "").strip()
    return {"ok": True, "text": text} if text else {"ok": False, "error": "empty output"}


def _parse_proposals(text: str) -> list | None:
    """The advisor's JSON, tolerantly located (models sometimes fence or preface despite
    instructions) but strictly parsed. None when no valid proposals object can be found."""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
    except ValueError:
        return None
    proposals = obj.get("proposals") if isinstance(obj, dict) else None
    return proposals if isinstance(proposals, list) else None


def run(cfg: dict[str, Any] | None = None, day: str | None = None) -> dict[str, Any]:
    """Generate, validate, and write advice artifacts for every advise-enabled module.
    Best-effort per module; returns a per-module result map."""
    cfg = cfg or cfgmod.load_config()
    st = cfgmod.advise_settings(cfg)
    day = day or timeutil.now_et(cfg.get("timezone", "America/New_York")).strftime("%Y-%m-%d")
    if not st["enabled"]:
        return {"ok": False, "day": day, "skipped": "disabled"}
    if not _claude_available():
        return {"ok": False, "day": day, "skipped": "claude_not_found"}
    enabled = {name: m for name, m in st["modules"].items() if m.get("enabled")}
    if not enabled:
        return {"ok": False, "day": day, "skipped": "no_modules_enabled"}

    session = _next_session(cfg, day)
    context = _context_blocks(cfg, day)
    if not context.strip():
        return {"ok": False, "day": day, "skipped": "no_inputs"}

    results: dict[str, Any] = {}
    for name, mcfg in enabled.items():
        bounds = mcfg.get("advice_bounds") or {}
        if not bounds:
            results[name] = {"ok": False, "skipped": "no_advice_bounds"}
            continue
        res = _run_claude(_prompt(name, session, bounds), context, st.get("model"), st["timeout_seconds"])
        if not res.get("ok"):
            results[name] = {"ok": False, "error": res.get("error")}
            continue
        proposals = _parse_proposals(res["text"])
        if proposals is None:
            results[name] = {"ok": False, "error": "advisor output was not the requested JSON"}
            continue
        artifact = {"session": session, "expires_at": _expires_at(session), "proposals": proposals}
        verdict = core_advice.validate(artifact, bounds, session)
        # The artifact is written either way — admitted proposals when the set passed, empty
        # proposals plus the recorded rejections when it didn't (reject-all, but auditable).
        path = core_advice.advice_path(cfgmod.STATE_DIR, name, session)
        core_advice.write(
            path,
            name,
            session,
            verdict["proposals"],
            ADVISOR,
            _expires_at(session),
            rejected=verdict["rejected"],
        )
        results[name] = {
            "ok": verdict["ok"],
            "session": session,
            "artifact": cfgmod.portable_path(path),
            "proposals": len(verdict["proposals"]),
            "rejected": len(verdict["rejected"]),
            "reason": verdict["reason"],
        }
    return {
        "ok": any(r.get("ok") for r in results.values()),
        "day": day,
        "session": session,
        "modules": results,
    }
