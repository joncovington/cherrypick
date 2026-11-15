"""What the research side does once the vendor licence has ended (#1091) and the forward retest job's
gaps (#1085): a month queued once, refusals reported, vendor work stopped, paper judged alone."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from cherrypick.orchestrator import forwardcheck as fc
from cherrypick.orchestrator import hypotheses as hy
from cherrypick.orchestrator import researchqueue as rq
from cherrypick.orchestrator import vendorlicence

ENDED = {"active": False, "terminated_on": "2026-12-01", "purge_by": "2026-12-31", "phase": "keeping"}
ACTIVE = {"active": True, "terminated_on": None}

VENDOR = {
    "script": "packages/backtest/scripts/x_item90.py",
    "steps": [{"step": "study", "kind": "script", "args": ["study"], "output": "{results}/f-{stamp}.json"}],
}
PUBLIC = {
    "script": "scripts/public_item91.py",
    "steps": [{"step": "study", "kind": "script", "args": ["study"], "output": "{results}/f-{stamp}.json"}],
}


def _reg():
    return {
        "hypotheses": [
            {"id": 90, "study": "item90", "status": "passed", "retest_forward": VENDOR},
            {"id": 91, "study": "item91", "status": "passed", "retest_forward": PUBLIC},
        ]
    }


def _plan(**kw):
    return hy.forward_plan(_reg(), date(2026, 12, 2), results_for=lambda s: Path("/res") / s, **kw)


def test_a_month_already_in_the_queue_is_not_queued_again():
    p = _plan(queued={"item90/forward-2026-12-study"})
    assert p["already_queued"] == ["item90"] and set(p["chains"]) == {"item91"}


def test_after_the_licence_vendor_studies_stop_and_public_ones_go_on():
    p = _plan(licence_ended=True)
    assert p["licence_ended"] == ["item90"] and set(p["chains"]) == {"item91"}
    assert hy.uses_vendor(VENDOR) and not hy.uses_vendor(PUBLIC)
    assert hy.uses_vendor({"script": "scripts/x.py", "steps": [{"step": "f", "kind": "fetch-oi"}]})


def test_an_unreadable_licence_is_unknown_never_ended():
    assert vendorlicence.ended(None) is False and vendorlicence.ended(ACTIVE) is False
    assert vendorlicence.ended(ENDED) is True


def test_the_licence_is_read_from_the_backtests_status():
    class Done:
        returncode, stdout = 0, json.dumps({"enabled": True, "license": ENDED})

    assert vendorlicence.status(run=lambda *a, **k: Done()) == ENDED

    class Broken:
        returncode, stdout = 1, ""

    assert vendorlicence.status(run=lambda *a, **k: Broken()) is None


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "backtest_stores", lambda: None)
    req = tmp_path / "r.json"
    req.write_text(json.dumps({"requests": []}), encoding="utf-8")
    return tmp_path / "q.db", req


def test_a_fetch_is_refused_once_the_licence_has_ended(queue):
    db, req = queue
    with pytest.raises(rq.Refused, match="licence ended"):
        rq.add("item90", "fetch-chain-paths", {"requests": str(req)}, path=db, licence=lambda: ENDED)
    assert rq.add("item90", "fetch-chain-paths", {"requests": str(req)}, path=db, licence=lambda: ACTIVE)


def test_queued_fetches_are_cancelled_once_with_why_and_a_failed_read_cancels_nothing(queue):
    db, req = queue
    rq.add("item90", "fetch-chain-paths", {"requests": str(req)}, path=db, licence=lambda: ACTIVE)
    conn = rq.connect(db)
    assert rq.cancel_lapsed(conn, lambda: None) == []
    gone = rq.cancel_lapsed(conn, lambda: ENDED)
    conn.close()
    assert [r["step"] for r in gone] == ["fetch-chain-paths"]
    [row] = rq.rows(db)
    assert row["status"] == "cancelled" and "licence ended on 2026-12-01" in row["note"]


SPEC = {
    "module": "weeklyput",
    "schema": "weeklyput_put",
    "arm": "u",
    "measure": "cycle_mean",
    "field": "x",
    "since": "2026-01-01",
    "min_obs": 5,
    "reference": {"file": "{home}/gone.json", "mean": "m", "t": "t", "n": "n"},
    "kill_z": -2.0,
    "drift_z": -1.0,
}


def test_once_the_reference_is_purged_the_paper_arm_is_judged_alone():
    good = {str(i): v for i, v in enumerate([3.0, 4.0, 5.0, 4.0, 3.5, 4.5])}
    bad = {str(i): v for i, v in enumerate([-3.0, -4.0, -5.0, -4.0, -3.5, -4.5])}
    assert fc.judge(SPEC, good, None)["state"] == "collecting"  # licensed: a missing file is a local gap
    out = fc.judge(SPEC, good, None, licence_ended=True)
    assert out["state"] == "confirm" and out["basis"].startswith("paper alone")
    assert fc.judge(SPEC, bad, None, licence_ended=True)["state"] == "kill"


def test_the_board_marks_a_purged_result_not_reproducible():
    reg = {"hypotheses": [{"id": 90, "study": "item90", "status": "passed", "results": ["{home}/gone.json"]}]}
    out = hy.board(reg, [], read=lambda p: (None, None), licence_ended=True)
    [res] = out["rows"][0]["results"]
    assert res["not_reproducible"] is True and out["licence_ended"] is True
    assert "not_reproducible" not in hy.board(reg, [], read=lambda p: (None, None))["rows"][0]["results"][0]


def test_a_closed_results_gate_takes_a_licensed_results_figures_out_and_leaves_its_words():
    results = ["{home}/lic.json", "{home}/own.json"]
    reg = {"hypotheses": [{"id": 91, "study": "item91", "status": "failed", "results": results}]}
    data = {"verdict": "fail", "grid": {"rows": [{"setting": 1, "mean": 2.5}]}}
    out = hy.board(reg, [], read=lambda p: (data, "2026-11-15T10:00:00"), notes=lambda p: "mean 2.5")
    out = hy.withhold_licensed(out, lambda p: p.endswith("lic.json"))
    lic, own = out["rows"][0]["results"]
    assert lic["notes"] is None and lic["grid"] is None and lic["withheld"] is True
    assert lic["verdicts"] == [{"arm": None, "verdict": "fail"}] and lic["present"] is True
    assert own["notes"] == "mean 2.5" and own["grid"] is not None and "withheld" not in own
    assert out["withheld"] is True
