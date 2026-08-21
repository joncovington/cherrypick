"""Admission and the experiment lifecycle — where a proposal becomes something that runs.

Admission asks four questions, in this order, and records the answer to whichever one says no:

1. **Does the suite let the advisor near this module?** (`advisor.modules.<m>.enabled`)
2. **Does the module accept advice, and within which bounds?** (its own `advice` block)
3. **Are the proposed values admissible?** — checked with ``cherrypick.core.advice.validate``, the
   *same function the loop re-checks with at session start*. Not a reimplementation of it, not a
   subset of it: a disagreement between the two sides has to be impossible, so there is one
   function and both sides call it.
4. **Is there room?** Over the per-module cap, an otherwise-good spec is admitted as `queued` and
   activates FIFO when a slot frees. Queuing rather than rejecting matters: the idea was fine, the
   timing wasn't, and a rejected idea comes back as a fresh proposal that has lost its history.

Reject-all is inherited from `core.advice` and is the point: one out-of-bounds value invalidates the
whole proposal. Partial admission would let an aggressive value ride in behind innocuous ones.

Everything that happens to an experiment is journaled — created, activated, enacted, tuned, killed,
expired, verdict — because six weeks later "why is this arm running these numbers" has to be
answerable from the record rather than from memory.
"""

from __future__ import annotations

import json
from typing import Any

from cherrypick.core import advice as _advice

from cherrypick.advisor import bounds as _bounds
from cherrypick.advisor import clock as _clock
from cherrypick.advisor import paths as _paths
from cherrypick.advisor import settings as _settings
from cherrypick.advisor import store as _store
from cherrypick.advisor import verdicts as _verdicts

STATUS_QUEUED = "queued"
STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_KILLED = "killed"


def check_params(
    module: str, params: dict[str, Any], target_session: str, *, rationales: dict | None = None
) -> dict[str, Any]:
    """Would these params be admitted for `target_session`? The dry run of what `enact` will write.

    Runs the real artifact through the real validator, so an admission here and the artifact written
    tonight cannot disagree about what is legal. Returns core.advice's own verdict shape plus the
    module's posture.
    """
    posture = _bounds.resolve(module)
    if not posture["enabled"]:
        return {"ok": False, "reason": posture["reason"], "proposals": [], "rejected": [],
                "posture": posture}

    artifact = {
        "module": module,
        "session": target_session,
        "expires_at": _clock.end_of_session_iso(target_session),
        "proposals": [
            {"param": p, "value": v, "rationale": (rationales or {}).get(p, "")}
            for p, v in params.items()
        ],
    }
    result = _advice.validate(artifact, posture["bounds"], target_session)
    return {**result, "posture": posture}


def _active_count(conn, module: str) -> int:
    return len(_store.experiments(conn, module=module, status=STATUS_ACTIVE))


def admit_spec(
    conn,
    *,
    session: str,
    module: str,
    params: dict[str, Any],
    proposal_id: int | None = None,
    name: str | None = None,
    hypothesis: str = "",
    success_metric: str = "",
    sessions: Any = None,
    rationales: dict | None = None,
    cfg: dict | None = None,
) -> dict[str, Any]:
    """Admit one proposal as an experiment. Returns `{"ok", "experiment_id"|None, "reason", ...}`."""
    resolved = _settings.load(cfg)
    if not _settings.module_enabled(module, resolved):
        return {"ok": False, "reason": f"module_advice_disabled: advisor.modules.{module} is off"}

    target = _clock.next_session(session)
    checked = check_params(module, params, target, rationales=rationales)
    if not checked["ok"]:
        return {"ok": False, "reason": checked["reason"], "rejected": checked["rejected"]}

    cap = int(resolved["max_experiments_per_module"])
    over_cap = _active_count(conn, module) >= cap
    status = STATUS_QUEUED if over_cap else STATUS_ACTIVE
    length = _settings.clamp_sessions(sessions, resolved)

    experiment_id = _store.next_experiment_id(conn, session, module)
    _store.insert_experiment(conn, {
        "id": experiment_id,
        "module": module,
        "base_profile": checked["posture"]["base_profile"],
        "name": name,
        "hypothesis": hypothesis,
        "success_metric": success_metric,
        "params_json": json.dumps({p["param"]: p["value"] for p in checked["proposals"]}),
        # What the bounds were when this was admitted. A later human tightening that starts
        # rejecting the overlay then reads as a change, not a mystery.
        "bounds_snapshot_json": json.dumps(checked["posture"]["bounds"]),
        "status": status,
        "created_session": session,
        "expires_after_sessions": length,
        "origin_proposal_id": proposal_id,
    })
    _store.journal(conn, experiment_id, "created", session=session, detail={
        "params": params, "sessions": length, "status": status,
        "queued_because": f"{cap} active experiment(s) already" if over_cap else None,
    })
    if status == STATUS_ACTIVE:
        _store.journal(conn, experiment_id, "activated", session=session)

    return {"ok": True, "experiment_id": experiment_id, "status": status, "sessions": length,
            "reason": None if not over_cap else "queued behind the per-module cap"}


def tune(conn, *, session: str, experiment_id: str, params: dict[str, Any],
         rationale: str = "", rationales: dict | None = None) -> dict[str, Any]:
    """Adjust an experiment the advisor owns. Anything else is refused.

    The advisor may only ever tune its OWN experiments — never a control arm, never a
    human-configured profile. That is structural (the only thing it can emit is an `advised:*`
    overlay) and it is checked here so the refusal is legible rather than merely impossible.
    """
    experiment = _store.experiment(conn, experiment_id)
    if experiment is None or experiment["status"] != STATUS_ACTIVE:
        return {"ok": False, "reason": f"not_an_advisor_experiment: {experiment_id!r}"}

    module = experiment["module"]
    merged = {**json.loads(experiment["params_json"] or "{}"), **params}
    checked = check_params(module, merged, _clock.next_session(session), rationales=rationales)
    if not checked["ok"]:
        return {"ok": False, "reason": checked["reason"], "rejected": checked["rejected"]}

    _store.update_experiment(
        conn, experiment_id,
        params_json=json.dumps({p["param"]: p["value"] for p in checked["proposals"]}),
        bounds_snapshot_json=json.dumps(checked["posture"]["bounds"]),
    )
    _store.journal(conn, experiment_id, "tuned", session=session,
                   detail={"params": params, "rationale": rationale})
    return {"ok": True, "experiment_id": experiment_id}


def record_verdict_recommendation(conn, *, session: str, experiment_id: str, recommendation: str,
                                  rationale: str = "") -> dict[str, Any]:
    """Attach the model's keep/kill/promote to an experiment's computed verdict.

    Stored *beside* the numbers `verdicts.py` computed, never instead of them. If the experiment has
    no computed verdict yet, one is computed now — the recommendation must always sit on top of
    something measured.
    """
    experiment = _store.experiment(conn, experiment_id)
    if experiment is None:
        return {"ok": False, "reason": f"not_an_advisor_experiment: {experiment_id!r}"}

    stored = experiment["verdict_json"]
    if stored:
        body = json.loads(stored)
    else:
        # Same per-module rule the fact pack shows the model — never the library default.
        module_rule = _settings.calibration_rule(experiment["module"]) or None
        body = _verdicts.for_experiment(experiment, rule=module_rule)
    body["recommendation"] = {"value": recommendation, "rationale": rationale, "by": "model",
                              "session": session}
    _store.update_experiment(conn, experiment_id, verdict_json=json.dumps(body))
    _store.journal(conn, experiment_id, "verdict", session=session,
                   detail={"recommendation": recommendation, "rationale": rationale})
    return {"ok": True, "experiment_id": experiment_id, "recommendation": recommendation}


def kill(conn, experiment_id: str, *, session: str | None = None,
         reason: str = "killed by user") -> dict[str, Any]:
    """Stop an experiment now. No artifact is issued for it tonight; a queued one takes its slot."""
    experiment = _store.experiment(conn, experiment_id)
    if experiment is None:
        return {"ok": False, "reason": f"no such experiment {experiment_id!r}"}
    if experiment["status"] in (STATUS_EXPIRED, STATUS_KILLED):
        return {"ok": True, "experiment_id": experiment_id, "status": experiment["status"],
                "reason": "already concluded"}

    _store.update_experiment(conn, experiment_id, status=STATUS_KILLED)
    _store.journal(conn, experiment_id, "killed", session=session, detail={"reason": reason})
    promoted = activate_queued(conn, experiment["module"], session=session)
    return {"ok": True, "experiment_id": experiment_id, "status": STATUS_KILLED,
            "activated": promoted}


def dismiss(conn, proposal_id: int) -> dict[str, Any]:
    """Mark a proposal dismissed by the user. It stays in the record and travels in the deep pack's
    journal, which is the whole point — a dismissal the model cannot see gets re-proposed."""
    ok = _store.set_proposal_status(conn, proposal_id, "dismissed", "dismissed by user")
    return {"ok": ok, "proposal_id": proposal_id,
            "reason": None if ok else f"no such proposal {proposal_id}"}


def activate_queued(conn, module: str, *, session: str | None = None, cfg: dict | None = None) -> list[str]:
    """Fill free slots from the queue, oldest first. Returns the ids that became active."""
    cap = int(_settings.load(cfg)["max_experiments_per_module"])
    activated: list[str] = []
    for candidate in _store.experiments(conn, module=module, status=STATUS_QUEUED):
        if _active_count(conn, module) >= cap:
            break
        _store.update_experiment(conn, candidate["id"], status=STATUS_ACTIVE)
        _store.journal(conn, candidate["id"], "activated", session=session,
                       detail={"reason": "slot freed"})
        activated.append(candidate["id"])
    return activated


def expire_due(
    conn, session: str, *, rule: dict | None = None, cfg: dict | None = None
) -> list[dict[str, Any]]:
    """Conclude every experiment that has run its course: compute the verdict, stop issuing, let the
    queue move up.

    The verdict is computed here even when the model never ran — an experiment's result is a fact
    about the ledger, not something that depends on an AI being reachable that evening.

    The rule is resolved PER EXPERIMENT from the module's own `calibration.rule` (the same block
    the fact pack shows the model) unless an explicit `rule` overrides it. Until 2026-08-20 this
    used the library default while the pack showed the module rule — the model was shown one gate
    and the stored verdict computed against another, the exact 2026-08-14 incident
    `settings.calibration_rule` was written to end.
    """
    concluded = []
    for experiment in _store.experiments(conn, status=STATUS_ACTIVE):
        if experiment["sessions_run"] < experiment["expires_after_sessions"]:
            continue
        module_rule = rule
        if module_rule is None:
            module_rule = _settings.calibration_rule(experiment["module"], cfg) or None
        body = _verdicts.for_experiment(experiment, rule=module_rule)
        _store.update_experiment(conn, experiment["id"], status=STATUS_EXPIRED,
                                 verdict_json=json.dumps(body))
        _store.journal(conn, experiment["id"], "expired", session=session,
                       detail={"sessions_run": experiment["sessions_run"],
                               "underpowered": body["underpowered"]})
        concluded.append({"experiment_id": experiment["id"], "module": experiment["module"],
                          "underpowered": body["underpowered"]})
        activate_queued(conn, experiment["module"], session=session)
    return concluded


# --------------------------------------------------------------------------- admission of a reply


def admit_reply(
    conn,
    *,
    session: str,
    slot: str,
    reply: dict[str, Any],
    model: str | None = None,
    pack_path: str | None = None,
    raw_path: str | None = None,
    cfg: dict | None = None,
) -> dict[str, Any]:
    """Record one checkpoint and everything the model proposed in it.

    Every proposal lands in the `proposals` table whatever happens to it — admitted, rejected with a
    reason, queued, or dismissed later by a human. The rejected ones are shown on the console and
    fed back in the next deep pack, so the model can stop repeating a proposal the bounds will never
    accept.
    """
    checkpoint_id = _store.record_checkpoint(
        conn, session=session, slot=slot, model=model, ok=True, pack_path=pack_path,
        raw_path=raw_path, observations=reply.get("observations"), flags=reply.get("flags"),
    )

    admitted, rejected = [], []

    for bad in reply.get("malformed") or []:
        pid = _store.add_proposal(conn, checkpoint_id=checkpoint_id, module=None, kind="malformed",
                                  payload=bad.get("raw"), status="rejected",
                                  reject_reason=bad.get("reason"))
        rejected.append({"proposal_id": pid, "kind": "malformed", "reason": bad.get("reason")})

    for proposal in reply.get("proposals") or []:
        kind = proposal["kind"]
        module = proposal.get("module")
        pid = _store.add_proposal(conn, checkpoint_id=checkpoint_id, module=module, kind=kind,
                                  payload=proposal.get("raw", proposal), status="proposed")
        outcome = _apply(conn, session=session, slot=slot, proposal=proposal, proposal_id=pid, cfg=cfg)
        if outcome["ok"]:
            _store.set_proposal_status(conn, pid, "admitted")
            admitted.append({"proposal_id": pid, "kind": kind, **outcome})
        else:
            _store.set_proposal_status(conn, pid, "rejected", outcome.get("reason"))
            rejected.append({"proposal_id": pid, "kind": kind, "reason": outcome.get("reason")})

    summary = {"ok": True, "checkpoint_id": checkpoint_id, "session": session, "slot": slot,
               "model": model, "observations": reply.get("observations") or [],
               "flags": reply.get("flags") or [], "admitted": admitted, "rejected": rejected,
               "pack": pack_path, "raw": raw_path}
    # The write-once summary beside the pack and the raw reply. Its existence is what freezes the
    # slot: the record of what the model was shown and said on a given afternoon should not quietly
    # become a different record on a re-run. `--force` overwrites the whole triple, deliberately.
    _store.write_json(_paths.checkpoint_path(session, slot), summary)
    return summary


def _apply(conn, *, session: str, slot: str, proposal: dict[str, Any], proposal_id: int,
           cfg: dict | None) -> dict[str, Any]:
    """Route one typed proposal to its lifecycle action.

    `creative` is the deliberate no-op: full reach on what may be *proposed* — a new arm, a new
    strategy family, a whole new module — and no path at all from a proposal to a running change.
    A human reads it and acts, or does not.
    """
    kind = proposal["kind"]

    if kind == "creative":
        return {"ok": True, "action": "recorded", "propose_only": True}

    if kind == "tune":
        return tune(conn, session=session, experiment_id=proposal["experiment_id"],
                    params=proposal["params"], rationale=proposal.get("rationale", ""),
                    rationales=proposal.get("rationales"))

    if kind == "verdict":
        return record_verdict_recommendation(
            conn, session=session, experiment_id=proposal["experiment_id"],
            recommendation=proposal["recommendation"], rationale=proposal.get("rationale", ""),
        )

    if kind in ("bounded_adjustment", "experiment_spec"):
        if not proposal.get("module"):
            return {"ok": False, "reason": "missing required field(s): module"}
        return admit_spec(
            conn, session=session, module=proposal["module"], params=proposal["params"],
            proposal_id=proposal_id, name=proposal.get("name") or f"{slot}-{kind}",
            hypothesis=proposal.get("hypothesis", ""),
            success_metric=proposal.get("success_metric", ""), sessions=proposal.get("sessions"),
            rationales=proposal.get("rationales"), cfg=cfg,
        )

    return {"ok": False, "reason": f"unknown_kind: {kind!r}"}
