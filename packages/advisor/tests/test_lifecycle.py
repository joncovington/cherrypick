"""Admission, the cap, the queue, expiry, and the nightly artifact — the whole loop, no AI.

Every assertion here is about a rule that has to hold whether or not a model ever answers: the
deterministic half of this package is the half that must not be able to go wrong.
"""

from __future__ import annotations

import json
from datetime import date

import fakes
import pytest
from cherrypick.core import advice as core_advice

from cherrypick.advisor import bounds, clock, enact, experiments, paths, settings, store

# Derived from the clock, never a literal date — see fakes.anchor_session for what a hardcoded one
# cost this suite. FRIDAY keeps its name for the readers of the assertions below; what it means is
# "the next trading day", which is where an artifact issued on SESSION lands.
SESSION = fakes.anchor_session()
FRIDAY = fakes.next_session(SESSION)
MEIC_BOUNDS = {
    "stop_trigger_ratio": {"min": 0.85, "max": 0.95},
    "entry_price_strategy": {"choices": ["mid", "auto"]},
}


@pytest.fixture
def home(tmp_home):
    fakes.seed_suite(tmp_home, SESSION)
    fakes.write_config(tmp_home, "meic", fakes.advice_block(MEIC_BOUNDS))
    fakes.write_suite_config(tmp_home, {
        "enabled": True,
        "modules": {"meic": {"enabled": True}, "flies": {"enabled": False},
                    "earnings": {"enabled": False}},
    })
    return tmp_home


@pytest.fixture
def conn():
    connection = store.connect()
    yield connection
    connection.close()


def _reply(*props, observations=("noted",)):
    return {"observations": list(observations), "flags": [], "proposals": list(props),
            "malformed": []}


def _adjustment(params, module="meic", **extra):
    return {"kind": "bounded_adjustment", "module": module, "params": params,
            "rationales": {}, "raw": {"kind": "bounded_adjustment"}, **extra}


# --------------------------------------------------------------------------- admission


def test_an_in_bounds_proposal_becomes_an_active_experiment(home, conn):
    result = experiments.admit_reply(
        conn, session=SESSION, slot="deep",
        reply=_reply(_adjustment({"stop_trigger_ratio": 0.9}, sessions=15)),
    )
    assert result["rejected"] == []
    admitted = result["admitted"][0]
    assert admitted["status"] == "active"
    row = store.experiment(conn, admitted["experiment_id"])
    assert json.loads(row["params_json"]) == {"stop_trigger_ratio": 0.9}
    assert row["base_profile"] == "control"
    assert [e["event"] for e in store.events(conn, row["id"])] == ["created", "activated"]


def test_one_bad_param_rejects_the_whole_proposal(home, conn):
    """Reject-all, inherited from core.advice: partial admission would let an aggressive value ride
    in behind an innocuous one."""
    result = experiments.admit_reply(
        conn, session=SESSION, slot="deep",
        reply=_reply(_adjustment({"stop_trigger_ratio": 0.9, "entry_price_strategy": "market"})),
    )
    assert result["admitted"] == []
    assert "reject-all" in result["rejected"][0]["reason"]
    assert store.experiments(conn) == []


def test_the_same_validator_the_loop_uses_agrees(home):
    """Not a reimplementation: assert admission's answer against core.advice.validate directly."""
    checked = experiments.check_params("meic", {"stop_trigger_ratio": 0.99}, FRIDAY)
    direct = core_advice.validate(
        {"module": "meic", "session": FRIDAY, "expires_at": clock.end_of_session_iso(FRIDAY),
         "proposals": [{"param": "stop_trigger_ratio", "value": 0.99}]},
        MEIC_BOUNDS, FRIDAY,
    )
    assert checked["ok"] is False and direct["ok"] is False
    assert checked["rejected"][0]["reason"] == direct["rejected"][0]["reason"]


def test_a_module_with_no_advice_block_refuses_everything(home, conn):
    fakes.write_suite_config(home, {"enabled": True, "modules": {"flies": {"enabled": True}}})
    result = experiments.admit_reply(
        conn, session=SESSION, slot="deep",
        reply=_reply(_adjustment({"fee_buffer": 0.1}, module="flies")),
    )
    assert "module_advice_disabled" in result["rejected"][0]["reason"]


def test_a_module_the_advisor_is_not_allowed_near_refuses_too(home, conn):
    """Two switches must agree: the suite's `advisor.modules.<m>` and the module's own block."""
    fakes.write_config(home, "earnings", {"advice": {"enabled": True, "bounds": {
        "iron_fly.profit_target_pct": {"min": 0.15, "max": 0.6}}}})
    result = experiments.admit_reply(
        conn, session=SESSION, slot="deep",
        reply=_reply(_adjustment({"iron_fly.profit_target_pct": 0.3}, module="earnings")),
    )
    assert "advisor.modules.earnings is off" in result["rejected"][0]["reason"]


def test_creative_proposals_are_recorded_and_never_run(home, conn):
    result = experiments.admit_reply(conn, session=SESSION, slot="deep", reply=_reply(
        {"kind": "creative", "module": "meic", "title": "a new arm", "text": "…",
         "spec_json": {"wing_width": 15}, "raw": {"kind": "creative"}},
    ))
    assert result["admitted"][0]["propose_only"] is True
    assert store.experiments(conn) == []


# --------------------------------------------------------------------------- cap and queue


def test_over_the_cap_a_good_spec_queues_rather_than_being_refused(home, conn):
    for _ in range(2):
        experiments.admit_reply(conn, session=SESSION, slot="deep",
                                reply=_reply(_adjustment({"stop_trigger_ratio": 0.9})))
    statuses = [e["status"] for e in store.experiments(conn, module="meic")]
    assert statuses == ["active", "queued"]


def test_killing_the_active_experiment_activates_the_queued_one(home, conn):
    first = experiments.admit_reply(conn, session=SESSION, slot="deep", reply=_reply(
        _adjustment({"stop_trigger_ratio": 0.9})))["admitted"][0]["experiment_id"]
    second = experiments.admit_reply(conn, session=SESSION, slot="deep", reply=_reply(
        _adjustment({"stop_trigger_ratio": 0.88})))["admitted"][0]["experiment_id"]

    killed = experiments.kill(conn, first, session=SESSION)
    assert killed["activated"] == [second]
    assert store.experiment(conn, first)["status"] == "killed"
    assert store.experiment(conn, second)["status"] == "active"


def test_a_requested_length_is_honored_but_clamped(home, conn):
    resolved = settings.load()
    for requested, expected in ((3, resolved["experiment_sessions_min"]),
                                (99, resolved["experiment_sessions_max"]),
                                (None, resolved["experiment_sessions"])):
        out = experiments.admit_spec(conn, session=SESSION, module="meic",
                                     params={"stop_trigger_ratio": 0.9}, sessions=requested)
        assert store.experiment(conn, out["experiment_id"])["expires_after_sessions"] == expected


# --------------------------------------------------------------------------- tune


def test_tune_only_reaches_the_advisors_own_active_experiments(home, conn):
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]

    for target in ("control", "exp-does-not-exist", "advised:control"):
        refused = experiments.tune(conn, session=SESSION, experiment_id=target,
                                   params={"stop_trigger_ratio": 0.92})
        assert refused["reason"].startswith("not_an_advisor_experiment")

    ok = experiments.tune(conn, session=SESSION, experiment_id=experiment_id,
                          params={"stop_trigger_ratio": 0.92})
    assert ok["ok"] is True
    assert json.loads(store.experiment(conn, experiment_id)["params_json"]) == {
        "stop_trigger_ratio": 0.92
    }
    assert "tuned" in [e["event"] for e in store.events(conn, experiment_id)]


def test_a_tune_that_leaves_bounds_is_refused_and_changes_nothing(home, conn):
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    experiments.tune(conn, session=SESSION, experiment_id=experiment_id,
                     params={"stop_trigger_ratio": 2.0})
    assert json.loads(store.experiment(conn, experiment_id)["params_json"]) == {
        "stop_trigger_ratio": 0.9
    }


# --------------------------------------------------------------------------- enact


def test_enact_writes_the_next_sessions_artifact_and_the_loop_can_read_it(home, conn):
    experiments.admit_spec(conn, session=SESSION, module="meic",
                           params={"stop_trigger_ratio": 0.9})
    result = enact.run(conn, SESSION)

    assert result["target_session"] == FRIDAY
    written = [m for m in result["enacted"] if m["module"] == "meic"][0]
    assert written["written"] is True and written["admitted"] == 1

    # The round trip that matters: what enact wrote, read back by the exact call the loop makes.
    loaded = core_advice.load(paths.state_dir(), "meic", FRIDAY, MEIC_BOUNDS)
    assert loaded["ok"] is True
    assert loaded["proposals"][0]["param"] == "stop_trigger_ratio"
    assert loaded["proposals"][0]["value"] == 0.9


def test_advice_is_written_for_the_next_TRADING_day(home, conn):
    """Friday's run lands on Monday, and a holiday is skipped — the NYSE calendar, not +1 day.

    The Friday is found forward from the anchor rather than written down, so the weekend skip is
    genuinely exercised on a session that has not expired. The holiday pair below IS pinned, and
    correctly so: those are facts about the NYSE calendar rather than about today, and
    `clock.next_session` is a pure calendar call with no expiry in it to rot.
    """
    friday = SESSION
    while date.fromisoformat(friday).weekday() != 4:
        friday = clock.next_session(friday)

    experiments.admit_spec(conn, session=friday, module="meic", params={"stop_trigger_ratio": 0.9})
    monday = enact.run(conn, friday)["target_session"]
    assert monday == clock.next_session(friday)
    assert date.fromisoformat(monday).weekday() == 0, "a +1 day walk would have landed on Saturday"

    thanksgiving_eve = "2026-11-25"
    assert clock.next_session(thanksgiving_eve) == "2026-11-27"  # Thursday is the holiday


def test_the_artifact_expires_at_the_end_of_the_session_it_names(home, conn):
    experiments.admit_spec(conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9})
    enact.run(conn, SESSION)
    artifact = json.loads(paths.advice_path("meic", FRIDAY).read_text(encoding="utf-8"))
    assert artifact["session"] == FRIDAY
    assert artifact["expires_at"].startswith(f"{FRIDAY}T23:59:59")


def test_a_bounds_tightening_tonight_applies_tomorrow_morning(home, conn):
    """The experiment is not touched; the artifact simply stops being admitted, and says why."""
    experiments.admit_spec(conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9})
    fakes.write_config(home, "meic", fakes.advice_block(
        {"stop_trigger_ratio": {"min": 0.85, "max": 0.87}}))

    enact.run(conn, SESSION)
    artifact = json.loads(paths.advice_path("meic", FRIDAY).read_text(encoding="utf-8"))
    assert artifact["proposals"] == []
    assert artifact["rejected"][0]["param"] == "stop_trigger_ratio"
    # Written anyway: the loop reads an admissible artifact carrying nothing, i.e. baseline —
    # exactly what an absent file would have produced — and this file is the only record that the
    # advisor tried and the tightened bounds said no.
    loaded = core_advice.load(paths.state_dir(), "meic", FRIDAY,
                              {"stop_trigger_ratio": {"min": 0.85, "max": 0.87}})
    assert loaded["proposals"] == []


def _record_decision(home, module, session, params):
    """Stand in for the module's loop: write the decision file it would have recorded."""
    path = home / "data" / module / "advice_active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"day": session, "params": params or None}), encoding="utf-8")


def test_issuing_an_artifact_does_not_by_itself_cost_a_session(home, conn):
    """The 2026-08-25 correction. Writing an artifact is not evidence a loop applied it, and two
    experiments spent four sessions on artifacts that never reached one."""
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    enact.run(conn, SESSION)
    assert store.experiment(conn, experiment_id)["sessions_run"] == 0


def test_a_session_counts_once_the_loop_is_shown_to_have_applied_it(home, conn):
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    enact.run(conn, SESSION)  # issues FRIDAY's artifact
    _record_decision(home, "meic", FRIDAY, {"stop_trigger_ratio": 0.9})

    result = enact.run(conn, FRIDAY)  # the following evening scores it

    assert store.experiment(conn, experiment_id)["sessions_run"] == 1
    assert [c for c in result["counted"] if c["module"] == "meic"][0]["enacted"] is True


def test_a_session_the_loop_never_applied_costs_nothing(home, conn):
    """meic and earnings, 2026-08-25: a live, valid artifact and a loop that recorded
    `advice_disabled` against it. The session bought no evidence, so it buys no count."""
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    enact.run(conn, SESSION)
    _record_decision(home, "meic", FRIDAY, None)

    result = enact.run(conn, FRIDAY)

    assert store.experiment(conn, experiment_id)["sessions_run"] == 0
    scored = [c for c in result["counted"] if c["module"] == "meic"][0]
    assert scored["enacted"] is False


def test_counting_a_session_twice_is_refused(home, conn):
    """The evening pass is re-run after a failed AI call. A counter that advanced each time would
    reintroduce the overcount from the other direction."""
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    enact.run(conn, SESSION)
    _record_decision(home, "meic", FRIDAY, {"stop_trigger_ratio": 0.9})
    enact.run(conn, FRIDAY)
    enact.run(conn, FRIDAY)
    assert store.experiment(conn, experiment_id)["sessions_run"] == 1


def test_a_rejected_artifact_that_reached_the_loop_still_costs_a_session(home, conn):
    """A bounds refusal is a real outcome the experiment paid for; only a delivery failure is free.
    Counting only admissions would let a bounds change silently extend an experiment."""
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    fakes.write_config(home, "meic", fakes.advice_block(
        {"stop_trigger_ratio": {"min": 0.85, "max": 0.87}}))
    enact.run(conn, SESSION)
    assert json.loads(paths.advice_path("meic", FRIDAY).read_text(encoding="utf-8"))["proposals"] == []
    _record_decision(home, "meic", FRIDAY, None)  # the loop correctly ran baseline

    enact.run(conn, FRIDAY)

    assert store.experiment(conn, experiment_id)["sessions_run"] == 1


def test_enact_is_a_no_op_when_nothing_is_running(home, conn):
    result = enact.run(conn, SESSION)
    assert all(m["written"] is False for m in result["enacted"])
    assert not paths.advice_path("meic", FRIDAY).exists()


def test_a_module_that_stops_accepting_advice_gets_no_artifact(home, conn):
    experiments.admit_spec(conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9})
    fakes.write_config(home, "meic", fakes.advice_block(MEIC_BOUNDS, enabled=False))
    result = enact.run(conn, SESSION)
    assert [m for m in result["enacted"] if m["module"] == "meic"][0]["written"] is False
    assert not paths.advice_path("meic", FRIDAY).exists()


def test_re_running_enact_rewrites_the_same_artifact(home, conn):
    experiments.admit_spec(conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9})
    enact.run(conn, SESSION)
    first = paths.advice_path("meic", FRIDAY).read_text(encoding="utf-8")
    enact.run(conn, SESSION)
    second = paths.advice_path("meic", FRIDAY).read_text(encoding="utf-8")
    assert json.loads(first)["proposals"] == json.loads(second)["proposals"]
    assert len(list(paths.advice_path("meic", FRIDAY).parent.iterdir())) == 1


# --------------------------------------------------------------------------- expiry and verdicts


def test_an_experiment_that_runs_its_course_expires_with_a_computed_verdict(home, conn):
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}, sessions=5
    )["experiment_id"]
    store.update_experiment(conn, experiment_id, sessions_run=5)

    result = enact.run(conn, SESSION)
    assert result["concluded"][0]["experiment_id"] == experiment_id

    row = store.experiment(conn, experiment_id)
    assert row["status"] == "expired"
    verdict = json.loads(row["verdict_json"])
    assert verdict["computed_by"] == "cherrypick.advisor.verdicts"
    assert verdict["pairs"][0]["advised_tag"] == "advised:control"
    # Two trades in the seeded ledger is nowhere near the promotion gate, and the verdict says so
    # rather than passing or failing it.
    assert verdict["underpowered"] is True
    assert not paths.advice_path("meic", FRIDAY).exists(), "an expired experiment stops issuing"


def test_expiry_lets_the_queue_move_up(home, conn):
    first = experiments.admit_spec(conn, session=SESSION, module="meic",
                                   params={"stop_trigger_ratio": 0.9}, sessions=5)["experiment_id"]
    second = experiments.admit_spec(conn, session=SESSION, module="meic",
                                    params={"stop_trigger_ratio": 0.88})["experiment_id"]
    store.update_experiment(conn, first, sessions_run=5)
    enact.run(conn, SESSION)
    assert store.experiment(conn, second)["status"] == "active"


def test_the_models_recommendation_sits_beside_the_numbers_never_instead_of_them(home, conn):
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    experiments.record_verdict_recommendation(
        conn, session=SESSION, experiment_id=experiment_id, recommendation="promote",
        rationale="advised book leads on every session",
    )
    verdict = json.loads(store.experiment(conn, experiment_id)["verdict_json"])
    assert verdict["recommendation"] == {"value": "promote",
                                         "rationale": "advised book leads on every session",
                                         "by": "model", "session": SESSION}
    assert verdict["pairs"] and verdict["computed_by"] == "cherrypick.advisor.verdicts"


def test_a_dismissed_proposal_keeps_its_row(home, conn):
    result = experiments.admit_reply(conn, session=SESSION, slot="deep", reply=_reply(
        {"kind": "creative", "module": "meic", "title": "overnight gaps", "raw": {}},
    ))
    proposal_id = result["admitted"][0]["proposal_id"]
    assert experiments.dismiss(conn, proposal_id)["ok"] is True
    assert store.rows(conn, "SELECT status FROM proposals WHERE id = ?", (proposal_id,))[0][
        "status"] == "dismissed"
    assert experiments.dismiss(conn, 9999)["ok"] is False


# --------------------------------------------------------------------------- bounds resolution


def test_each_modules_bounds_shape_resolves_to_the_same_contract(home):
    fakes.write_config(home, "flies", fakes.advice_block(
        {"fee_buffer": {"min": 0.05, "max": 0.25}}, base_key="base_arm", base="control"))
    fakes.write_config(home, "earnings", {"advice": {"enabled": True, "bounds": {
        "iron_fly.profit_target_pct": {"min": 0.15, "max": 0.6}}}})

    assert bounds.resolve("flies")["base_profile"] == "control"
    assert bounds.resolve("earnings")["base_profile"] == "strat_test"
    assert bounds.split_param("earnings", "iron_fly.profit_target_pct") == (
        "iron_fly", "profit_target_pct")
    assert bounds.split_param("meic", "stop_trigger_ratio") == (None, "stop_trigger_ratio")
    assert bounds.advised_tag("earnings", "strat_test", "iron_fly") == "advised:strat_test:iron_fly"
    assert bounds.advised_tag("meic", "control") == "advised:control"


def test_a_killed_experiment_still_records_what_it_measured(home, conn):
    """Stopping an experiment spends no MORE sessions; it does not unspend the ones already run.

    `expire_due` computes a verdict even when the model never ran, on the stated grounds that a
    result is a fact about the ledger. The same is true of a kill — and until 2026-08-26 this path
    stored only a status and a reason, leaving seven concluded experiments with no verdict at all,
    three of them after five or six live sessions. The evidence was bought and never read.
    """
    import json

    eid = experiments.admit_reply(conn, session=SESSION, slot="deep", reply=_reply(
        _adjustment({"stop_trigger_ratio": 0.9})))["admitted"][0]["experiment_id"]

    experiments.kill(conn, eid, session=SESSION, reason="not separating from control")

    row = store.experiment(conn, eid)
    assert row["status"] == "killed"
    assert row["verdict_json"], "a killed experiment must still say what it measured"
    body = json.loads(row["verdict_json"])
    assert "underpowered" in body, "the computed numbers, not just a note"
    assert body["killed_reason"] == "not separating from control", (
        "the reason sits BESIDE the numbers, never instead of them"
    )


def test_killing_an_already_concluded_experiment_does_not_rewrite_its_verdict(home, conn):
    """A second kill must not overwrite the verdict the first one computed -- re-judging a
    concluded experiment against a later ledger is the drift `verdict_for` exists to prevent."""
    import json

    eid = experiments.admit_reply(conn, session=SESSION, slot="deep", reply=_reply(
        _adjustment({"stop_trigger_ratio": 0.9})))["admitted"][0]["experiment_id"]
    experiments.kill(conn, eid, session=SESSION, reason="first")
    first = json.loads(store.experiment(conn, eid)["verdict_json"])

    again = experiments.kill(conn, eid, session=SESSION, reason="second")

    assert again["reason"] == "already concluded"
    assert json.loads(store.experiment(conn, eid)["verdict_json"]) == first
