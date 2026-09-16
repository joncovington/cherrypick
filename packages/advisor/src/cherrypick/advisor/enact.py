"""The nightly walk: turn active experiments into tomorrow's advice artifacts.

This is the only thing in the suite that writes ``state/advice/<module>-<session>.json``, and it
writes exactly one per module per session, for the **next NYSE trading day** — so Friday's run
lands on Monday and nothing is ever issued for a holiday.

Three properties are load-bearing:

* **It re-validates against the module's CURRENT bounds**, not the ones the experiment was admitted
  under. A human who tightened a range this evening has tightened it by tomorrow morning, without
  having to find and stop the experiment running inside it.
* **It writes the artifact even when everything is rejected.** Reject-all is silent from the loop's
  side — it simply runs baseline — so an artifact with empty `proposals` and a populated `rejected`
  is the only record that the advisor tried and the bounds said no.
* **It runs unconditionally**, including after the AI call failed. An outage must never truncate an
  active A/B sample: the experiment is the measurement, and a missing day in the middle of one is
  worse than no advice at all.

Expiry happens first, so an experiment that has run its course stops issuing on the same pass that
concludes it and the queue moves up immediately.

Ahead of expiry, since 2026-08-25, comes **counting**. ``sessions_run`` used to increment here, at
issue time, on the reasoning that the experiment consumed one of its sessions whether or not the
bounds admitted anything. That reasoning holds for a rejection and fails for a delivery failure, and
the two were indistinguishable in the counter. So the pass now scores the session that just ended
against what the module's loop actually recorded (``enactment``) and increments only for the ones
that reached a loop. A session where the artifact was written and never applied is journaled as
`not_enacted` and costs the experiment nothing -- because it bought it nothing.
"""

from __future__ import annotations

import json
from typing import Any

from cherrypick.core import advice as _advice

from cherrypick.advisor import bounds as _bounds
from cherrypick.advisor import clock as _clock
from cherrypick.advisor import enactment as _enactment
from cherrypick.advisor import experiments as _experiments
from cherrypick.advisor import paths as _paths
from cherrypick.advisor import settings as _settings
from cherrypick.advisor import store as _store

ADVISOR_TAG = "cherrypick.advisor/enact-v1"


def run(
    conn, session: str, *, modules: tuple[str, ...] | list[str] | None = None, cfg: dict | None = None
) -> dict[str, Any]:
    """Conclude what is due, then issue for what remains. Returns one summary per module."""
    target = _clock.next_session(session)
    resolved = _settings.load(cfg)
    selected = tuple(modules or _bounds.MODULES)

    counted = _count_enacted(conn, session, selected)
    concluded = _experiments.expire_due(conn, session, cfg=cfg)

    issued: list[dict[str, Any]] = []
    for module in selected:
        if not _settings.module_enabled(module, resolved):
            issued.append(
                {
                    "module": module,
                    "written": False,
                    "reason": f"module_advice_disabled: advisor.modules.{module} is off",
                }
            )
            continue
        # One module's failure (a malformed bounds rule in ITS config, a ledger it cannot read)
        # must not truncate every other module's active A/B sample -- the pass "runs
        # unconditionally" for exactly that reason. Reported, never raised past this loop.
        try:
            issued.append(_issue(conn, module=module, session=session, target=target))
        except Exception as exc:  # noqa: BLE001 -- isolation is the point
            issued.append(
                {"module": module, "written": False, "reason": f"issue_error: {type(exc).__name__}: {exc}"}
            )

    return {
        "ok": True,
        "session": session,
        "target_session": target,
        "counted": counted,
        "concluded": concluded,
        "enacted": issued,
    }


def _count_enacted(conn, session: str, modules: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """Score the session that just ended: whose artifact reached a loop, and whose did not.

    Attribution is by the experiment id stamped on the artifact rather than by whichever experiment
    is active now, so a session issued under one experiment and scored after it was replaced still
    lands on the one that paid for it.

    Idempotent: the journal is the record, and a session already scored for an experiment is not
    scored again. The evening pass can be re-run -- it is, after a failed AI call -- and re-running
    it must not advance a counter twice.
    """
    # Persist every module's outcome first, including the ones with no artifact and no experiment:
    # the console reads these rows, and a module missing from the table is indistinguishable there
    # from a module that has not been scored.
    recorded = _enactment.record(conn, session, modules)

    scored: list[dict[str, Any]] = []
    for module in modules:
        outcome = recorded[module]
        if outcome["status"] == _enactment.NO_ARTIFACT:
            continue
        experiment_id = outcome["experiment_id"]
        if not experiment_id:
            continue
        experiment = _store.experiment(conn, experiment_id)
        if experiment is None:
            continue
        if _store.has_journal_event(conn, experiment_id, "counted", session=session):
            continue

        enacted = outcome["status"] == _enactment.ENACTED
        # The counter and the `counted` row that makes it idempotent land in ONE transaction. Until
        # 2026-09-12 the counter committed first, so a crash between the two left an advanced
        # counter with no guard, and the next run advanced it again -- the overcount
        # `has_journal_event` was written to remove, back from the other direction.
        if enacted:
            _store.update_experiment(
                conn, experiment_id, sessions_run=experiment["sessions_run"] + 1, commit=False
            )
        _store.journal(
            conn,
            experiment_id,
            "counted",
            session=session,
            detail={
                "enacted": enacted,
                "status": outcome["status"],
                "detail": outcome["detail"],
                "artifact_params": outcome["artifact_params"],
                "decision_params": outcome["decision_params"],
                "decision_reason": outcome["decision_reason"],
                "sessions_run": experiment["sessions_run"] + (1 if enacted else 0),
            },
            commit=False,
        )
        conn.commit()
        scored.append(
            {
                "module": module,
                "experiment_id": experiment_id,
                "session": session,
                "enacted": enacted,
                "detail": outcome["detail"],
            }
        )
    return scored


def reissue(conn, module: str, session: str, *, cfg: dict | None = None) -> dict[str, Any]:
    """Re-issue ONE module's next-session artifact from whatever is active now — the step a kill
    takes after the nightly pass has already written the dead experiment's artifact.

    Deliberately not `run`: that pass also scores the session that just ended, and scoring a
    module mid-day (before its loop has decided) would journal a `counted` row the idempotency
    guard then refuses to correct in the evening. This touches only the artifact.
    """
    if not _settings.module_enabled(module, _settings.load(cfg)):
        return {
            "module": module,
            "written": False,
            "reason": f"module_advice_disabled: advisor.modules.{module} is off",
        }
    return _issue(conn, module=module, session=session, target=_clock.next_session(session))


def _retract_stale(conn, *, module: str, session: str, target: str) -> str | None:
    """Remove an artifact this advisor wrote for `target` when the module no longer has an active
    experiment — otherwise the loop applies a concluded experiment's params tomorrow morning.

    Only an artifact stamped with this advisor's tag is touched; anything else at that path was
    written by a hand or a tool this package does not own. Returns the retracted path, or None.
    """
    path = _paths.advice_path(module, target)
    if not path.exists():
        return None
    try:
        stamp = str((json.loads(path.read_text(encoding="utf-8")) or {}).get("advisor") or "")
    except (OSError, ValueError):
        return None
    if not stamp.startswith(ADVISOR_TAG):
        return None
    path.unlink()
    # "tag (exp-id)": journal against the experiment whose artifact this was, when it still exists.
    experiment_id = stamp[len(ADVISOR_TAG) :].strip(" ()")
    if experiment_id and _store.experiment(conn, experiment_id) is not None:
        _store.journal(
            conn,
            experiment_id,
            "retracted",
            session=session,
            detail={"target": target, "path": str(path), "reason": "experiment no longer active"},
        )
    return str(path)


def _issue(conn, *, module: str, session: str, target: str) -> dict[str, Any]:
    """One module's artifact for `target`, from its (at most one) active experiment."""
    active = _store.experiments(conn, module=module, status=_experiments.STATUS_ACTIVE)
    if not active:
        retracted = _retract_stale(conn, module=module, session=session, target=target)
        return {"module": module, "written": False, "reason": "no active experiment", "retracted": retracted}

    posture = _bounds.resolve(module)
    if not posture["enabled"]:
        # The module stopped accepting advice while an experiment was running. Nothing is written —
        # an absent artifact is the loop's baseline, which is exactly the intended behavior — and
        # the reason is recorded against the experiment so the gap in its sample is explained.
        for experiment in active:
            _store.journal(
                conn,
                experiment["id"],
                "enacted",
                session=session,
                detail={"target": target, "written": False, "reason": posture["reason"]},
            )
        return {"module": module, "written": False, "reason": posture["reason"]}

    # Structurally one per module: each consumer builds exactly one advised book from the artifact.
    # If a cap change ever admits more, the first is enacted and the rest stay queued in effect —
    # recorded here rather than silently merged, because merging two experiments' overlays would
    # produce a book neither of them proposed.
    experiment = active[0]
    deferred = [e["id"] for e in active[1:]]

    params = json.loads(experiment["params_json"] or "{}")
    artifact = _experiments.artifact_for(
        module, target, params, rationale=f"advisor experiment {experiment['id']}"
    )
    checked = _advice.validate(artifact, posture["bounds"], target)

    path = _advice.write(
        _paths.advice_path(module, target),
        module=module,
        session=target,
        proposals=checked["proposals"],
        advisor=f"{ADVISOR_TAG} ({experiment['id']})",
        expires_at=artifact["expires_at"],
        rejected=checked["rejected"],
        experiment_id=experiment["id"],
    )

    # sessions_run is NOT incremented here. Issuing an artifact is not evidence that a loop applied
    # it, and until 2026-08-25 this line could not tell the difference -- which is how two starved
    # experiments came to read 2-of-10 and 4-of-10 on sessions that had bought them nothing. The
    # count now happens in `_count_enacted`, the following evening, against what the loop recorded.
    # A rejection still costs a session: an artifact that reached the loop and was refused by the
    # bounds is a real outcome, and counting only admissions would let a bounds change silently
    # extend an experiment past the length a human agreed to.
    _store.journal(
        conn,
        experiment["id"],
        "enacted",
        session=session,
        detail={
            "target": target,
            "written": True,
            "admitted": len(checked["proposals"]),
            "rejected": checked["rejected"],
            "reason": checked["reason"],
            "path": str(path),
        },
    )

    return {
        "module": module,
        "written": True,
        "path": str(path),
        "experiment_id": experiment["id"],
        "target_session": target,
        "admitted": len(checked["proposals"]),
        "rejected": len(checked["rejected"]),
        "reason": checked["reason"],
        "deferred_experiments": deferred,
    }
