"""Did the artifact the advisor wrote actually reach the loop that was supposed to apply it?

Two facts that had never been reconciled: the advisor WROTE ``state/advice/<module>-<session>.json``,
and the module's loop RECORDED a decision built from it in its own ``advice_active.json``. Until
2026-08-25 nothing compared them, and ``sessions_run`` counted the first while every verdict was
written as though it counted the second.

That gap is not theoretical and it is not cheap. On 2026-08-24 the advisor generated five artifacts
in one batch with zero rejections. Three were applied. Two -- meic and earnings -- were not, and they
were the two whose experiments had their most informative session available: meic's control filled
215 entries, and earnings broke a thirteen-session drought with four iron_condors that all went to
the control target. Both experiments recorded the session as spent. Earnings carries a
kill-at-session-6 rule, so on the counter as it stood, "the parameter produced nothing" and "the
parameter was never applied to a session that had trades" would have concluded identically.

So enactment is a first-class recorded outcome here, with three states rather than two:

* ``enacted`` -- the loop's decision for that session matches the artifact's admitted proposals.
  A reject-all artifact counts: the advisor's intent reached the loop and the bounds refused it,
  which is a real result and one the experiment paid a session for.
* ``not_enacted`` -- an artifact was issued and the loop's record disagrees with it or is absent.
  The session bought no evidence and must not be counted against the experiment's length.
* ``no_artifact`` -- nothing was issued for that module and session; there is nothing to reconcile.

For a session that has passed, the loop's decision file is long overwritten -- it holds one day. The
durable record is the advisor's own **fact packs**, which are write-once per (session, slot) and
already snapshot each module's ``advice_active`` as the model saw it. That is what makes the history
re-derivable rather than merely asserted, and it is why the backfill reads packs rather than
reconstructing intent from ledger rows: a meic experiment whose whole point is that a gate blocks
fills produces the same empty book whether the gate was applied or the artifact was dropped.

This module only reads. It compares two files written by other people and returns what it found.
"""

from __future__ import annotations

import json
import re
from typing import Any

from cherrypick.advisor import bounds as _bounds
from cherrypick.advisor import clock as _clock
from cherrypick.advisor import paths as _paths
from cherrypick.advisor import store as _store

ENACTED = "enacted"
NOT_ENACTED = "not_enacted"
NO_ARTIFACT = "no_artifact"

MODULES = _bounds.MODULES

# `enact` stamps the artifact "cherrypick.advisor/enact-v1 (exp-2026-08-21-meic-1)". The experiment
# id is how a session's outcome is attributed to the experiment that actually paid for it -- which
# matters precisely when an experiment was replaced between the evening that issued the artifact and
# the evening that scores it.
_EXPERIMENT_IN_ADVISOR = re.compile(r"\(([a-z0-9-]+)\)\s*$")

# The evening pack is the one that carries a full session's decision; the intraday slots are read
# only as a fallback, newest first, for a session whose deep pack was never built.
_PACK_SLOTS = ("deep", "close", "pm2", "pm1", "midday", "am2", "am1", "open", "am")


def _artifact_params(artifact: dict[str, Any]) -> dict[str, Any]:
    return {p["param"]: p["value"] for p in artifact.get("proposals") or [] if isinstance(p, dict)}


def experiment_of(artifact: dict[str, Any] | None) -> str | None:
    """The experiment an artifact was issued for, read off the stamp `enact` wrote."""
    if not isinstance(artifact, dict):
        return None
    match = _EXPERIMENT_IN_ADVISOR.search(str(artifact.get("advisor") or ""))
    return match.group(1) if match else None


def recorded_decision(module: str, session: str) -> dict[str, Any] | None:
    """What the module's loop recorded for `session`, or None if it recorded nothing.

    The live decision file holds exactly one day, so it answers only for the current session. For an
    earlier one the answer comes from the fact packs, which are write-once and already carry each
    module's `advice_active` as the model saw it that evening. Preferring the live file keeps today
    correct even before the evening pack is built.
    """
    live = _store.read_json(_paths.module_data_dir(module) / "advice_active.json", default=None)
    if isinstance(live, dict) and live.get("day") == session:
        return live
    for slot in _PACK_SLOTS:
        pack = _store.read_json(_paths.pack_path(session, slot), default=None)
        if not isinstance(pack, dict):
            continue
        recorded = ((pack.get("paper") or {}).get(module) or {}).get("advice_active")
        if isinstance(recorded, dict) and recorded.get("day") == session:
            return recorded
    return None


def reconcile(module: str, session: str) -> dict[str, Any]:
    """One module's enactment outcome for one session.

    The comparison is on the admitted params themselves rather than on a flag, because every way
    this has actually failed produces a decision file that exists and looks ordinary -- the loop
    recorded `advice_disabled` against a live artifact, or an out-of-session process fixed the day's
    decision before the artifact could be read. Only the params tell those apart from a working day.
    """
    artifact = _store.read_json(_paths.advice_path(module, session), default=None)
    recorded = recorded_decision(module, session)

    out: dict[str, Any] = {
        "module": module,
        "session": session,
        "experiment_id": experiment_of(artifact),
        "artifact_written": artifact is not None,
        "artifact_params": _artifact_params(artifact) if artifact else None,
        "artifact_rejected": len(artifact.get("rejected") or []) if artifact else None,
        "decision_recorded": recorded is not None,
        "decision_params": (recorded.get("params") or {}) if recorded else None,
        "decision_reason": recorded.get("reason") if recorded else None,
        "decision_derived_at": recorded.get("derived_at") if recorded else None,
    }

    if artifact is None:
        out["status"] = NO_ARTIFACT
        out["detail"] = "no artifact was issued for this module and session"
        return out
    if recorded is None:
        out["status"] = NOT_ENACTED
        out["detail"] = "the loop recorded no decision for this session"
        return out
    if out["decision_params"] == out["artifact_params"]:
        out["status"] = ENACTED
        out["detail"] = (
            "reject-all artifact, and the loop recorded the baseline it implies"
            if not out["artifact_params"]
            else "the loop applied the artifact's admitted params"
        )
        return out

    out["status"] = NOT_ENACTED
    out["detail"] = (
        f"the loop recorded {out['decision_params']!r} "
        f"against an artifact admitting {out['artifact_params']!r}"
        + (f" (reason: {out['decision_reason']})" if out["decision_reason"] else "")
    )
    return out


def audit(session: str, modules: tuple[str, ...] | list[str] | None = None) -> dict[str, Any]:
    """Every module's enactment outcome for `session`, keyed by module."""
    return {m: reconcile(m, session) for m in (modules or MODULES)}


def record(conn, session: str, modules: tuple[str, ...] | list[str] | None = None) -> dict[str, Any]:
    """Persist the reconciliation so a reader can render it without recomputing it.

    The console shows the advisor's judgements and forms none of its own -- the same rule that keeps
    verdicts on the experiment row rather than re-derived in TypeScript, because a second opinion is
    free to drift from the first. "Did the loop apply this" is a judgement, so it is decided here
    once and stored, not compared again on the other side of the wire.

    Upsert per (session, module): re-running a slot rewrites that session's row rather than
    accumulating, and the row is refreshed on every pack write so the page is current DURING the
    session rather than only after the evening pass has scored it.
    """
    outcomes = audit(session, modules)
    for module, outcome in outcomes.items():
        conn.execute(
            "INSERT INTO enactment (session, module, status, detail, experiment_id,"
            " artifact_params, decision_params, decision_reason, scored_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(session, module) DO UPDATE SET status=excluded.status,"
            " detail=excluded.detail, experiment_id=excluded.experiment_id,"
            " artifact_params=excluded.artifact_params, decision_params=excluded.decision_params,"
            " decision_reason=excluded.decision_reason, scored_at=excluded.scored_at",
            (
                session, module, outcome["status"], outcome["detail"], outcome["experiment_id"],
                json.dumps(outcome["artifact_params"]) if outcome["artifact_params"] is not None else None,
                json.dumps(outcome["decision_params"]) if outcome["decision_params"] is not None else None,
                outcome["decision_reason"], _store.now_iso(),
            ),
        )
    conn.commit()
    return outcomes


def sessions_of(experiment: dict[str, Any], *, through: str | None = None) -> list[str]:
    """Every session an artifact was issued for on this experiment's behalf, oldest first.

    Read off the artifacts themselves rather than off the journal: the artifact carries the
    experiment id it was issued for, and it is the same file the loop read, so an artifact that
    exists is a session the experiment was genuinely in flight for.

    Bounded at `through` (today by default) because `enact` issues the evening BEFORE. Tomorrow's
    artifact is always on disk by the time this runs, and a session that has not happened has no
    enactment outcome — counting it either way would be a guess about the future.
    """
    module = experiment["module"]
    through = through or _clock.session_today()
    found = []
    for path in sorted((_paths.state_dir() / "advice").glob(f"{module}-*.json")):
        session = path.stem[len(module) + 1:]
        if session > through:
            continue
        artifact = _store.read_json(path, default=None)
        if experiment_of(artifact) == experiment["id"]:
            found.append(session)
    return found


def recount(conn, *, apply: bool = False) -> dict[str, Any]:
    """Re-derive `sessions_run` for every active experiment from what the loops actually recorded.

    The counter used to advance when an artifact was ISSUED. Correcting the rule going forward does
    not correct the experiments already carrying an inflated count, and two of them were carrying
    one toward a kill rule — earnings' kill-at-session-6 would have fired on sessions where the
    artifact was written and never applied, recording "the parameter produced nothing" for a
    parameter that was never applied to a session that had trades.

    Evidence is the write-once fact packs (see `recorded_decision`). Where a session has no pack and
    no surviving decision file, nothing can be proved either way: it is reported as `unknown` and
    **kept in the count**. Removing an unprovable session would silently shorten an experiment on
    the strength of missing evidence, which is the same error in the opposite direction.

    Read-only unless `apply=True`. The correction is journaled per experiment, once, with the
    per-session evidence it rests on.
    """
    from cherrypick.advisor import experiments as _experiments  # circular at module scope

    report = []
    for experiment in _store.experiments(conn, status=_experiments.STATUS_ACTIVE):
        rows = []
        for session in sessions_of(experiment):
            outcome = reconcile(experiment["module"], session)
            if outcome["status"] == ENACTED:
                status = ENACTED
            elif recorded_decision(experiment["module"], session) is None and not _has_pack(session):
                status = "unknown"
            else:
                status = NOT_ENACTED
            rows.append({"session": session, "status": status, "detail": outcome["detail"]})

        derived = sum(1 for r in rows if r["status"] in (ENACTED, "unknown"))
        entry = {
            "experiment_id": experiment["id"],
            "module": experiment["module"],
            "sessions_run_recorded": experiment["sessions_run"],
            "sessions_run_derived": derived,
            "sessions": rows,
            "changed": derived != experiment["sessions_run"],
        }
        if apply and entry["changed"]:
            _store.update_experiment(conn, experiment["id"], sessions_run=derived)
            _store.journal(conn, experiment["id"], "recounted", session=None, detail=entry)
        report.append(entry)
    return {"ok": True, "applied": apply, "experiments": report}


def _has_pack(session: str) -> bool:
    return any(_paths.pack_path(session, slot).exists() for slot in _PACK_SLOTS)
