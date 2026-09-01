"""Enactment: did the artifact the advisor wrote reach the loop that was meant to apply it?

Every test here is a shape that actually happened between 2026-08-20 and 2026-08-25, when nothing
compared the two sides and `sessions_run` counted issuance while every verdict was written as though
it counted evidence. Two experiments spent four sessions on artifacts no loop ever read.
"""

from __future__ import annotations

import json

import fakes
import pytest

from cherrypick.advisor import enactment, experiments, paths, store

SESSION = fakes.anchor_session()
NEXT = fakes.next_session(SESSION)
BOUNDS = {"stop_trigger_ratio": {"min": 0.85, "max": 0.95}}


@pytest.fixture
def home(tmp_home):
    fakes.seed_suite(tmp_home, SESSION)
    fakes.write_config(tmp_home, "meic", fakes.advice_block(BOUNDS))
    fakes.write_suite_config(tmp_home, {"enabled": True, "modules": {"meic": {"enabled": True}}})
    return tmp_home


@pytest.fixture
def conn():
    connection = store.connect()
    yield connection
    connection.close()


def _artifact(session, params, *, experiment_id="exp-1", rejected=None):
    path = paths.advice_path("meic", session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "module": "meic",
                "session": session,
                "advisor": f"cherrypick.advisor/enact-v1 ({experiment_id})",
                "expires_at": f"{session}T23:59:59-04:00",
                "proposals": [{"param": k, "value": v, "rationale": "r"} for k, v in params.items()],
                "rejected": rejected or [],
            }
        ),
        encoding="utf-8",
    )


def _decision(home, session, params):
    path = home / "data" / "meic" / "advice_active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"day": session, "params": params}), encoding="utf-8")


def _pack(home, session, params, *, slot="deep"):
    """The write-once fact pack: the durable record of what the loop recorded that evening."""
    path = paths.pack_path(session, slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session": session,
                "slot": slot,
                "paper": {"meic": {"advice_active": {"day": session, "params": params}}},
            }
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- reconcile


def test_matching_params_are_enacted(home):
    _artifact(SESSION, {"stop_trigger_ratio": 0.9})
    _decision(home, SESSION, {"stop_trigger_ratio": 0.9})
    assert enactment.reconcile("meic", SESSION)["status"] == enactment.ENACTED


def test_a_dropped_artifact_is_not_enacted(home):
    """meic and earnings, 2026-08-25: a live, valid artifact and a loop that recorded
    `advice_disabled` against it. This is the case that was invisible."""
    _artifact(SESSION, {"stop_trigger_ratio": 0.9})
    _decision(home, SESSION, None)
    outcome = enactment.reconcile("meic", SESSION)
    assert outcome["status"] == enactment.NOT_ENACTED
    assert "0.9" in outcome["detail"]


def test_a_loop_that_recorded_nothing_is_not_enacted(home):
    """calendars, 2026-08-21 and 2026-08-25: the module simply did not run that session."""
    _artifact(SESSION, {"stop_trigger_ratio": 0.9})
    assert enactment.reconcile("meic", SESSION)["status"] == enactment.NOT_ENACTED


def test_yesterdays_decision_does_not_count_as_todays(home):
    _artifact(SESSION, {"stop_trigger_ratio": 0.9})
    _decision(home, "2000-01-01", {"stop_trigger_ratio": 0.9})
    assert enactment.reconcile("meic", SESSION)["status"] == enactment.NOT_ENACTED


def test_a_reject_all_artifact_that_reached_the_loop_is_enacted(home):
    """The bounds refused it and the loop correctly ran baseline. That is a real outcome the
    experiment paid a session for, not a delivery failure."""
    _artifact(SESSION, {}, rejected=[{"param": "stop_trigger_ratio", "value": 9.0, "reason": "x"}])
    _decision(home, SESSION, None)
    assert enactment.reconcile("meic", SESSION)["status"] == enactment.ENACTED


def test_no_artifact_is_not_a_failure(home):
    _decision(home, SESSION, None)
    assert enactment.reconcile("meic", SESSION)["status"] == enactment.NO_ARTIFACT


def test_the_experiment_is_read_off_the_artifact_not_off_whats_active(home):
    """A session issued under one experiment and scored after it was replaced must land on the one
    that paid for it."""
    _artifact(SESSION, {"stop_trigger_ratio": 0.9}, experiment_id="exp-2026-08-21-meic-1")
    assert enactment.reconcile("meic", SESSION)["experiment_id"] == "exp-2026-08-21-meic-1"


def test_history_comes_from_the_write_once_packs(home):
    """The decision file holds one day. Without the packs no past session is provable either way,
    and a meic experiment whose point is that a gate blocks fills leaves the same empty book whether
    the gate ran or the artifact was dropped, so ledger rows cannot stand in."""
    _artifact(SESSION, {"stop_trigger_ratio": 0.9})
    _decision(home, NEXT, None)  # the live file has moved on to the next session
    _pack(home, SESSION, {"stop_trigger_ratio": 0.9})
    assert enactment.reconcile("meic", SESSION)["status"] == enactment.ENACTED


# --------------------------------------------------------------------------- recount


def test_recount_is_read_only_without_apply(home, conn):
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    store.update_experiment(conn, experiment_id, sessions_run=5)
    _artifact(SESSION, {"stop_trigger_ratio": 0.9}, experiment_id=experiment_id)
    _pack(home, SESSION, None)

    report = enactment.recount(conn)

    assert report["experiments"][0]["sessions_run_derived"] == 0
    assert store.experiment(conn, experiment_id)["sessions_run"] == 5, "recount wrote without --apply"


def test_recount_corrects_an_inflated_counter(home, conn):
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    store.update_experiment(conn, experiment_id, sessions_run=4)
    _artifact(SESSION, {"stop_trigger_ratio": 0.9}, experiment_id=experiment_id)
    _pack(home, SESSION, {"stop_trigger_ratio": 0.9})

    enactment.recount(conn, apply=True)

    assert store.experiment(conn, experiment_id)["sessions_run"] == 1
    assert any(e["event"] == "recounted" for e in store.events(conn, experiment_id))


def test_an_unprovable_session_is_kept_in_the_count(home, conn):
    """No pack and no surviving decision file proves nothing. Dropping it would shorten an
    experiment on the strength of missing evidence, the same error in the other direction."""
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    _artifact(SESSION, {"stop_trigger_ratio": 0.9}, experiment_id=experiment_id)

    report = enactment.recount(conn)

    assert report["experiments"][0]["sessions"][0]["status"] == "unknown"
    assert report["experiments"][0]["sessions_run_derived"] == 1


def test_a_session_that_has_not_happened_is_not_counted(home, conn):
    """`enact` issues the evening BEFORE, so tomorrow's artifact is always on disk. A session that
    has not happened has no enactment outcome; counting it either way is a guess about the future."""
    experiment_id = experiments.admit_spec(
        conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9}
    )["experiment_id"]
    _artifact(NEXT, {"stop_trigger_ratio": 0.9}, experiment_id=experiment_id)

    report = enactment.recount(conn)

    assert report["experiments"][0]["sessions"] == []
    assert report["experiments"][0]["sessions_run_derived"] == 0


# --------------------------------------------------------- carried advice (2026-08-27)
#
# The original three states assumed every module decides every session, and half of them do not.
# calendars enters once a WEEK; earnings only when a name reports. Each spends most sessions with
# nothing to decide while the advice it already applied stays frozen on its open rows and governs
# them every tick. Scored as `not_enacted`, calendars alone raised a WARN four days in five --
# forever -- and a check that cries wolf 80% of the time cannot catch the case it was built for.

CAL_DDL = """
CREATE TABLE dc_positions (
    position_id TEXT PRIMARY KEY, book TEXT, status TEXT, advice_params TEXT
);
"""


def _calendars_ledger(home, rows):
    path = fakes.make_db(home / "data" / "calendars" / "paper_trades.db", CAL_DDL)
    fakes.insert(path, "dc_positions", rows)
    return path


def _cal_artifact(session, params, *, experiment_id="exp-cal-1"):
    path = paths.advice_path("calendars", session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "module": "calendars",
                "session": session,
                "advisor": f"cherrypick.advisor/enact-v1 ({experiment_id})",
                "expires_at": f"{session}T23:59:59-04:00",
                "proposals": [{"param": k, "value": v, "rationale": "r"} for k, v in params.items()],
                "rejected": [],
            }
        ),
        encoding="utf-8",
    )


def test_advice_frozen_on_an_open_position_is_carried_not_dropped(home):
    """calendars' Tuesday: Monday's entry stamped the params, and `effective_params` reads that
    stamp back every tick. Nothing was ignored; there was simply nothing new to decide."""
    _cal_artifact(SESSION, {"time_exit": "fri_noon"})
    _calendars_ledger(
        home,
        [
            {
                "position_id": "p1",
                "book": "advised:control",
                "status": "open",
                "advice_params": json.dumps({"time_exit": "fri_noon"}),
            },
        ],
    )
    outcome = enactment.reconcile("calendars", SESSION)
    assert outcome["status"] == enactment.CARRIED
    assert outcome["carried_params"] == [{"time_exit": "fri_noon"}]


def test_a_module_that_freezes_nothing_still_reports_not_enacted(home):
    """meic is flat overnight and stamps advice on no row, so it CANNOT carry. The check keeps its
    full force there -- this is the property a blanket 'a missing decision is fine' rule would
    have thrown away."""
    _artifact(SESSION, {"stop_trigger_ratio": 0.9})
    assert enactment.reconcile("meic", SESSION)["status"] == enactment.NOT_ENACTED


def test_changed_advice_is_not_carried_by_the_old_stamp(home):
    """The advisor changing its proposal mid-week is a genuine miss until the next entry. The old
    frozen value must never satisfy the new artifact."""
    _cal_artifact(SESSION, {"time_exit": "thu_close"})
    _calendars_ledger(
        home,
        [
            {
                "position_id": "p1",
                "book": "advised:control",
                "status": "open",
                "advice_params": json.dumps({"time_exit": "fri_noon"}),
            },
        ],
    )
    assert enactment.reconcile("calendars", SESSION)["status"] == enactment.NOT_ENACTED


def test_a_closed_position_carries_nothing(home):
    _cal_artifact(SESSION, {"time_exit": "fri_noon"})
    _calendars_ledger(
        home,
        [
            {
                "position_id": "p1",
                "book": "advised:control",
                "status": "closed",
                "advice_params": json.dumps({"time_exit": "fri_noon"}),
            },
        ],
    )
    assert enactment.reconcile("calendars", SESSION)["status"] == enactment.NOT_ENACTED


def test_a_namespaced_artifact_param_matches_the_leaf_it_is_stamped_as(home):
    """earnings admits `iron_condor.profit_target_pct` and stamps `profit_target_pct` on the trade
    row of the strategy it applies to -- the same fact at two scopes."""
    _cal_artifact(SESSION, {"iron_condor.profit_target_pct": 0.35})
    _calendars_ledger(
        home,
        [
            {
                "position_id": "p1",
                "book": "advised:control",
                "status": "open",
                "advice_params": json.dumps({"profit_target_pct": 0.35}),
            },
        ],
    )
    assert enactment.reconcile("calendars", SESSION)["status"] == enactment.CARRIED


def test_a_partially_stamped_artifact_is_not_carried(home):
    """A partial match is not evidence the advice is governing."""
    _cal_artifact(SESSION, {"time_exit": "fri_noon", "profit_target_pct": 0.5})
    _calendars_ledger(
        home,
        [
            {
                "position_id": "p1",
                "book": "advised:control",
                "status": "open",
                "advice_params": json.dumps({"time_exit": "fri_noon"}),
            },
        ],
    )
    assert enactment.reconcile("calendars", SESSION)["status"] == enactment.NOT_ENACTED


def test_a_reject_all_artifact_is_never_carried(home):
    """A reject-all artifact implies a baseline decision the loop must still RECORD; carrying a
    prior session's stamp would hide a loop that stopped recording entirely."""
    _cal_artifact(SESSION, {})
    _calendars_ledger(
        home,
        [
            {
                "position_id": "p1",
                "book": "advised:control",
                "status": "open",
                "advice_params": json.dumps({"time_exit": "fri_noon"}),
            },
        ],
    )
    assert enactment.reconcile("calendars", SESSION)["status"] == enactment.NOT_ENACTED


def test_carry_is_never_claimed_for_a_past_session(home):
    """`carried_by` reads the ledger's OPEN rows, which describe now and prove nothing about last
    week. An artifact whose params happen to match what is open today must not be scored `carried`
    for every past session it was issued for, on evidence that post-dates them."""
    past = "2026-01-05"
    _cal_artifact(past, {"time_exit": "fri_noon"})
    _calendars_ledger(
        home,
        [
            {
                "position_id": "p1",
                "book": "advised:control",
                "status": "open",
                "advice_params": json.dumps({"time_exit": "fri_noon"}),
            },
        ],
    )
    outcome = enactment.reconcile("calendars", past)
    assert outcome["status"] == enactment.NOT_ENACTED
    assert "cannot be proved after the fact" in outcome["detail"]
