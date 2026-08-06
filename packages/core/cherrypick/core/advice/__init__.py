"""cherrypick.core.advice — bounded, expiring, deterministically-validated parameter advice.

The suite's contract for letting an AI advisor influence a paper loop WITHOUT touching the
loop-decision guardrail: the advisor runs out-of-band and proposes parameter values; a
deterministic validator — this module — admits them against a per-module ``advice_bounds``
manifest of closed legal ranges before any loop reads them. The orchestrator validates before
writing the artifact, and the loop re-validates with this same code at session start, so a
disagreement between the two sides is impossible by construction.

The failure posture is the whole point:

- **Absent, stale, expired, or invalid ⇒ baseline.** A loop that finds no admissible advice
  behaves exactly as it does today. Advice can only ever *narrow* into declared ranges, never
  widen behavior.
- **Reject-all on any violation.** One out-of-bounds proposal invalidates the whole artifact
  (empty proposals, every rejection recorded). Partial admission would let an advisor smuggle
  an aggressive value behind innocuous ones and still land the rest.
- **Single-session, never sticky.** The artifact names the one session it is for, and carries
  an ``expires_at`` on top; both must hold at read time.

Artifact (JSON, written by the orchestrator only — the advisor itself never gets a file handle):

    {
      "module": "meic",
      "session": "2026-07-29",
      "generated_at": "...iso...",
      "advisor": "claude -p / eod-advise-v1",
      "expires_at": "...iso...",
      "proposals": [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "..."}],
      "rejected":  [{"param": "...", "value": ..., "reason": "..."}]
    }

Bounds manifest (config, per module):

    {"stop_trigger_ratio": {"min": 0.85, "max": 0.95},       # closed numeric range
     "entry_price_strategy": {"choices": ["mid", "auto"]}}   # enumerated membership

Pure stdlib, no network, no broker.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ADVICE_DIR = "advice"


def advice_path(state_dir: Path | str, module: str, session: str) -> Path:
    """`<state_dir>/advice/<module>-<session>.json` — one artifact per (module, session)."""
    return Path(state_dir) / ADVICE_DIR / f"{module}-{session}.json"


def _check_proposal(p: Any, bounds: dict[str, Any]) -> str | None:
    """None if admissible, else the rejection reason."""
    if not isinstance(p, dict):
        return "proposal is not an object"
    param = p.get("param")
    if not isinstance(param, str) or not param:
        return "missing param name"
    rule = bounds.get(param)
    if rule is None:
        return f"param {param!r} not in advice_bounds"
    value = p.get("value")
    if "choices" in rule:
        if value not in rule["choices"]:
            return f"{param!r} value {value!r} not in declared choices"
        return None
    # Closed numeric range. bool is an int subclass, but True is not the number 1 here.
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return f"{param!r} value {value!r} is not numeric"
    lo, hi = rule.get("min"), rule.get("max")
    if lo is None or hi is None:
        return f"{param!r} bounds rule declares no closed range"
    if not (lo <= value <= hi):
        return f"{param!r} value {value!r} outside [{lo}, {hi}]"
    return None


def validate(
    artifact: Any, bounds: dict[str, Any], session: str, now: datetime | None = None
) -> dict[str, Any]:
    """Deterministic admission check. Returns {"ok", "reason", "proposals", "rejected"} —
    ok False always means empty proposals (reject-all)."""
    now = now or datetime.now(timezone.utc)

    def reject_all(reason: str, rejected: list | None = None) -> dict[str, Any]:
        return {"ok": False, "reason": reason, "proposals": [], "rejected": rejected or []}

    if not isinstance(artifact, dict):
        return reject_all("artifact is not an object")
    if artifact.get("session") != session:
        return reject_all(f"artifact session {artifact.get('session')!r} is not {session!r} (never sticky)")
    try:
        expires = datetime.fromisoformat(str(artifact.get("expires_at")))
    except (TypeError, ValueError):
        return reject_all("expires_at missing or unparseable")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        return reject_all("advice expired")
    proposals = artifact.get("proposals")
    if not isinstance(proposals, list):
        return reject_all("proposals is not a list")

    seen: set[str] = set()
    rejected: list[dict[str, Any]] = []
    admitted: list[dict[str, Any]] = []
    for p in proposals:
        reason = _check_proposal(p, bounds)
        if reason is None and isinstance(p, dict):
            if p.get("param") in seen:
                reason = f"duplicate param {p.get('param')!r}"
            else:
                seen.add(p["param"])
        if reason is not None:
            rejected.append(
                {
                    "param": (p or {}).get("param") if isinstance(p, dict) else None,
                    "value": (p or {}).get("value") if isinstance(p, dict) else p,
                    "reason": reason,
                }
            )
        else:
            admitted.append(
                {"param": p["param"], "value": p["value"], "rationale": str(p.get("rationale") or "")}
            )
    if rejected:
        # One violation rejects the whole artifact: partial admission would let an advisor
        # smuggle an aggressive value behind innocuous ones and still land the rest.
        return reject_all(f"{len(rejected)} proposal(s) violated advice_bounds (reject-all)", rejected)
    return {"ok": True, "reason": None, "proposals": admitted, "rejected": []}


def write(
    path: Path | str,
    module: str,
    session: str,
    proposals: list[dict[str, Any]],
    advisor: str,
    expires_at: str,
    rejected: list[dict[str, Any]] | None = None,
) -> Path:
    """Write the artifact atomically (tmp + replace) — a loop must never read a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "module": module,
        "session": session,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "advisor": advisor,
        "expires_at": expires_at,
        "proposals": proposals,
        "rejected": rejected or [],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load(
    state_dir: Path | str, module: str, session: str, bounds: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """The loop-side read: one call at session start, never per tick. Absent/unreadable/invalid
    all come back {"ok": False, "reason", "proposals": []} — i.e. baseline. The loop logs the
    reason and moves on; it never waits for, retries, or alerts about advice."""
    path = advice_path(state_dir, module, session)
    if not path.exists():
        return {"ok": False, "reason": "absent", "proposals": [], "rejected": []}
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": f"unreadable: {exc}", "proposals": [], "rejected": []}
    return validate(artifact, bounds, session, now=now)
