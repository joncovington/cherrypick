"""An experiment that cannot move its book from the base is refused, and one that never did is
concluded as having measured nothing.

2026-10-12: bwb's `delta-trigger-early` put `delta_trigger` on `control`, which has no trigger. Its
eleven advised rows matched control's exactly, and its verdict was on course to read as a pass or
fail of a parameter that never acted. Two halves pin the fix:

* admission and the nightly enact refuse a param the module declares cannot act on the base (a
  bounds rule's `arms`, read by `core.advice`, the validator the loop also calls);
* a verdict whose advised book never diverged from its base -- or whose params are all declared
  inert on it -- concludes `no_exposure`, ahead of `underpowered` and any qualification read.
"""

from __future__ import annotations

import json

import fakes
import pytest
from fakes import MEIC_DDL, insert, make_db

from cherrypick.advisor import clock, enact, experiments, paths, store, verdicts

SESSION = fakes.anchor_session()
TARGET = fakes.next_session(SESSION)
INERT_ON_CONTROL = {
    "stop_trigger_ratio": {"min": 0.85, "max": 0.95},
    "trigger_param": {"min": 0.1, "max": 0.9, "arms": ["delta"]},
}


@pytest.fixture
def home(tmp_home):
    fakes.seed_suite(tmp_home, SESSION)
    fakes.write_config(tmp_home, "meic", fakes.advice_block(INERT_ON_CONTROL))
    fakes.write_suite_config(
        tmp_home,
        {"enabled": True, "modules": {"meic": {"enabled": True}, "flies": {"enabled": False}}},
    )
    return tmp_home


@pytest.fixture
def conn():
    connection = store.connect()
    yield connection
    connection.close()


def _admit(conn, params):
    return experiments.admit_spec(conn, session=SESSION, module="meic", params=params, name="probe")


# --------------------------------------------------------------------------- admission and enact


def test_a_param_that_cannot_act_on_the_base_is_refused_at_admission(home, conn):
    result = _admit(conn, {"trigger_param": 0.3})
    assert result["ok"] is False
    assert "cannot act on base arm 'control'" in result["rejected"][0]["reason"]
    assert store.experiments(conn) == []


def test_the_same_param_is_admitted_on_a_base_it_acts_on(home, conn):
    fakes.write_config(home, "meic", fakes.advice_block(INERT_ON_CONTROL, base="delta"))
    assert _admit(conn, {"trigger_param": 0.3})["ok"] is True


def test_reject_all_holds_when_one_param_is_inert(home, conn):
    """An inert param beside a live one is still refused whole: half an overlay is not what was
    proposed, and the live half would be measured under the inert half's name."""
    result = _admit(conn, {"stop_trigger_ratio": 0.9, "trigger_param": 0.3})
    assert result["ok"] is False


def test_tune_checks_against_the_experiments_own_base(home, conn):
    admitted = _admit(conn, {"stop_trigger_ratio": 0.9})
    out = experiments.tune(
        conn, session=SESSION, experiment_id=admitted["experiment_id"], params={"trigger_param": 0.3}
    )
    assert out["ok"] is False and "cannot act on base arm 'control'" in out["rejected"][0]["reason"]


def test_enact_refuses_a_running_experiment_once_its_param_is_declared_inert(home, conn):
    """The `delta-trigger-early` case: admitted before the declaration existed. Tonight's
    re-validation against current bounds refuses it, and the artifact says why."""
    undeclared = {k: {kk: vv for kk, vv in v.items() if kk != "arms"} for k, v in INERT_ON_CONTROL.items()}
    fakes.write_config(home, "meic", fakes.advice_block(undeclared))
    assert _admit(conn, {"trigger_param": 0.3})["ok"] is True

    fakes.write_config(home, "meic", fakes.advice_block(INERT_ON_CONTROL))
    enact.run(conn, SESSION, modules=["meic"])
    artifact = json.loads(paths.advice_path("meic", TARGET).read_text(encoding="utf-8"))
    entry = artifact["experiments"][0]
    assert entry["base"] == "control"
    assert entry["proposals"] == []
    assert "cannot act on base arm 'control'" in entry["rejected"][0]["reason"]


# --------------------------------------------------------------------------- verdicts


def _ledger(home, rows):
    db = make_db(home / "data" / "meic" / "paper_trades.db", MEIC_DDL)
    insert(db, "ic_trades", rows)


def _row(tag, pnl, oid, day):
    return {
        "trade_date": day,
        "symbol": "SPX",
        "risk_profile": tag,
        "net_credit": 2.4,
        "wing_width": 20,
        "quantity": 1,
        "pnl": pnl,
        "fees": 6.0,
        "status": "closed",
        "exit_time": f"{day}T20:10:00",
        "ic_order_id": oid,
        "created_at": f"{day}T14:31:00",
    }


def _experiment(params):
    return {
        "id": "exp-probe",
        "module": "meic",
        "base_profile": "control",
        "name": "probe",
        "tag": "advised:probe",
        "params_json": json.dumps(params),
        "created_session": SESSION,
        "sessions_run": 2,
    }


def test_a_twin_identical_to_its_base_concludes_no_exposure(tmp_home):
    fakes.write_config(tmp_home, "meic", fakes.advice_block(INERT_ON_CONTROL))
    day2 = clock.next_session(TARGET)
    _ledger(
        tmp_home,
        [
            _row("control", 120.0, "c1", TARGET),
            _row("advised:probe", 120.0, "a1", TARGET),
            _row("control", -300.0, "c2", day2),
            _row("control", 75.0, "c3", day2),  # the base may hold rows the twin does not
            _row("advised:probe", -300.0, "a2", day2),
        ],
    )
    body = verdicts.for_experiment(_experiment({"stop_trigger_ratio": 0.9}))
    assert body["pairs"][0]["exposure"]["diverged"] is False
    assert body["pairs"][0]["exposure"]["advised_rows"] == 2
    assert body["no_exposure"]["reason"] == "never_diverged"
    assert body["conclusion"] == "no_exposure"


def test_one_differing_row_is_exposure(tmp_home):
    fakes.write_config(tmp_home, "meic", fakes.advice_block(INERT_ON_CONTROL))
    _ledger(
        tmp_home,
        [_row("control", 120.0, "c1", TARGET), _row("advised:probe", 119.0, "a1", TARGET)],
    )
    body = verdicts.for_experiment(_experiment({"stop_trigger_ratio": 0.9}))
    exposure = body["pairs"][0]["exposure"]
    assert exposure["diverged"] is True and exposure["diverged_sessions"] == [TARGET]
    assert body["no_exposure"] is None
    assert body["conclusion"] == "underpowered"


def test_a_twin_on_another_session_is_not_a_match(tmp_home):
    fakes.write_config(tmp_home, "meic", fakes.advice_block(INERT_ON_CONTROL))
    _ledger(
        tmp_home,
        [
            _row("control", 120.0, "c1", clock.next_session(TARGET)),
            _row("advised:probe", 120.0, "a1", TARGET),
        ],
    )
    assert verdicts.for_experiment(_experiment({"stop_trigger_ratio": 0.9}))["no_exposure"] is None


def test_no_closed_advised_rows_is_no_evidence_either_way(tmp_home):
    fakes.write_config(tmp_home, "meic", fakes.advice_block(INERT_ON_CONTROL))
    _ledger(tmp_home, [_row("control", 120.0, "c1", TARGET)])
    body = verdicts.for_experiment(_experiment({"stop_trigger_ratio": 0.9}))
    assert body["pairs"][0]["exposure"]["diverged"] is None
    assert body["no_exposure"] is None and body["conclusion"] == "underpowered"


def test_params_declared_inert_on_the_base_conclude_no_exposure_without_rows(tmp_home):
    fakes.write_config(tmp_home, "meic", fakes.advice_block(INERT_ON_CONTROL))
    _ledger(tmp_home, [_row("control", 120.0, "c1", TARGET)])
    body = verdicts.for_experiment(_experiment({"trigger_param": 0.3}))
    assert body["no_exposure"]["reason"] == "declared"
    assert body["no_exposure"]["params"] == ["trigger_param"]
    assert body["conclusion"] == "no_exposure"
