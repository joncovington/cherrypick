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


def disabled_reason(config: dict[str, Any]) -> str | None:
    """Why this config accepts no advice, or None if it does.

    One string per distinct cause, because collapsing them cost four sessions of two experiments.
    On 2026-08-25 meic and earnings both recorded a bare ``advice_disabled`` against live, valid
    artifacts while three sibling modules from the same batch applied theirs — and "the flag is
    off", "the bounds are empty" and "there is no advice block at all" were indistinguishable in
    the one field the advisor can read. It could not diagnose which, and said so.

    The wording matches ``advisor.bounds._off`` on purpose: the advisor decides whether to WRITE an
    artifact from its side of the same question, and two sides answering it in different words is
    how a mismatch stays invisible.
    """
    acfg = config.get("advice")
    if not acfg:
        return "advice_disabled: no advice block in config"
    if not acfg.get("enabled"):
        return "advice_disabled: advice.enabled is false"
    if not acfg.get("bounds"):
        return "advice_disabled: advice.bounds is empty"
    return None


def session_decision(
    state_dir: Path | str,
    module: str,
    session: str,
    config: dict[str, Any],
    decision_path: Path | str,
    *,
    base_key: str | None = "base_book",
    log: Any = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Today's advice decision, derived ONCE per session and replayed for the rest of it.

    This is the read-once rule, and it is a safety property rather than an optimisation. A module's
    loop is a series of short `--once` processes, so "read the artifact at session start" only means
    anything if the first tick RECORDS what it decided and every later tick replays that record.
    Without it, an artifact landing at 11:00 — or a config flag flipped intraday — would change what
    an already-open book is being managed under, halfway through the session that book is evidence
    for. Three modules had written this out identically; it belongs beside the `load` it wraps.

    `base_key` is the config key naming the book the advised twin shadows, and it varies on purpose:
    flies calls its books arms (`base_arm`), calendars and pmcc call them books (`base_book`). It is
    carried through to the returned dict, because that shape is already persisted on disk and read
    by each module's own loop. `None` omits it — earnings names no base book, because its advice is
    keyed by strategy (`iron_condor.profit_target_pct`) and each strategy carries its own.

    A decision from a previous day is discarded rather than replayed. A write failure is swallowed:
    the decision still governs the process that made it, and the next `--once` simply re-derives the
    same one.

    **A baseline decision is never made sticky.** The read-once rule exists to stop advice starting
    or changing under an already-open book; a decision that admitted no params has nothing to
    protect, and persisting one lets any process that reached this function with an unreadable or
    advice-less config decide the whole session for every process after it. That is not
    hypothetical — on 2026-08-25 a 01:05 ET forced meic iteration and an 03:03 earnings entry pass
    each wrote `advice_disabled` hours before the market-open iteration that would have applied a
    valid artifact, and the open iteration dutifully replayed it. Both experiments lost their most
    informative session to a file written before the session began.

    `persist=False` is the same guard from the caller's side, for a process that is deriving a
    decision it has no business fixing for the day — a replay of a past date, or an iteration forced
    outside the trading window. It still gets a correct decision to run under; it just does not get
    to be the one that recorded it.
    """
    acfg = config.get("advice") or {}
    base = {} if base_key is None else {base_key: acfg.get(base_key, "control")}
    path = Path(decision_path)

    decision = None
    if path.exists():
        try:
            decision = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            decision = None
        if decision is not None and decision.get("day") != session:
            decision = None  # yesterday's decision; today re-derives its own
    if decision is not None:
        return decision

    off = disabled_reason(config)
    if off is None:
        result = load(state_dir, module, session, acfg.get("bounds") or {})
        params = {p["param"]: p["value"] for p in result["proposals"]} or None
        decision = {
            "day": session,
            **base,
            "params": params,
            "reason": result["reason"],
            "derived_at": datetime.now(timezone.utc).isoformat(),
            "proposals": result["proposals"],
            "rejected": result.get("rejected") or [],
        }
        if log is not None:
            for proposal in result["proposals"]:
                log(
                    f"advice applied: {proposal['param']}={proposal['value']!r} — "
                    f"{proposal.get('rationale', '')}"
                )
            if not result["proposals"]:
                log(f"advice: baseline ({result['reason'] or 'no proposals'})")
    else:
        decision = {
            "day": session,
            **base,
            "params": None,
            "reason": off,
            "derived_at": datetime.now(timezone.utc).isoformat(),
        }

    # `off` decisions are deliberately not recorded: see the docstring. `derived_at` rides on the
    # ones that are, because "when was the day's decision fixed" is the question that diagnosed the
    # 08-25 loss, and it should be answerable from the data rather than from a file mtime.
    if persist and off is None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(decision, indent=2), encoding="utf-8")
        except OSError:
            pass  # the decision still applies to this process; the next --once re-derives it
    return decision
