"""The idea search's files and driver (#1294): the plan names the run and fixes the split, the driver
writes a result beside its run, Declare drafts only a surviving testable idea, and the held-out part
is read once per declared idea, every use logged."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from test_ideasearch import _world

from cherrypick.backtest import ideafiles, ideasearch, licensing, paths

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("idea_search", SCRIPTS / "idea_search.py")
drv = importlib.util.module_from_spec(_spec)
sys.modules["idea_search"] = drv
_spec.loader.exec_module(drv)

RUN = "builder-mine-2611161730"
OUT = "ideas-mine-2611181000"


@pytest.fixture
def world(tmp_path, monkeypatch):
    own, lic = tmp_path / "own", tmp_path / "lic"
    monkeypatch.setattr(paths, "automations_dir", lambda licensed: lic if licensed else own)
    sessions, bars, vix, trades = _world()
    own.mkdir(parents=True)
    doc = {"name": RUN, "kind": "explore", "symbol": "SPY", "source": "dolt-chains", "licensed": False,
           "sessions": [sessions[0], sessions[-1]], "trades": trades,
           "generated_at": "2026-11-16T17:30:00"}  # fmt: skip
    (own / f"{RUN}.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(drv.ab, "stock_daily", lambda symbol: bars)
    monkeypatch.setattr(drv.volindex, "closes", lambda index: vix)
    monkeypatch.setattr(drv.ens_config, "load_config", lambda: {})
    monkeypatch.setattr(drv.events, "load_calendar", lambda path=None: drv.events.EventCalendar())
    return {"own": own, "lic": lic, "sessions": sessions}


def _search(world) -> dict:
    plan = ideafiles.plan("mine")
    assert drv.main(["run", "--name", "mine", "--run", plan["run"], "--split", plan["split"],
                     "--out", OUT, "--draws", "300"]) == 0  # fmt: skip
    return ideafiles.search_result("mine", OUT)


def test_the_plan_names_the_newest_long_enough_run_and_fixes_its_split(world):
    plan = ideafiles.plan("mine")
    s = world["sessions"]
    assert plan["run"] == RUN and plan["split"] == ideasearch.split_date(s[0], s[-1])
    short = {"name": "builder-mine-2611171730", "symbol": "SPY", "trades": [{"session": s[0], "net": 1}],
             "generated_at": "2026-11-17T17:30:00"}  # fmt: skip
    (world["own"] / "builder-mine-2611171730.json").write_text(json.dumps(short), encoding="utf-8")
    assert ideafiles.plan("mine")["run"] == RUN, "a run too short to search is passed over"
    assert ideafiles.plan("other")["ok"] is False


def test_the_driver_writes_a_result_beside_its_run_with_its_features(world):
    out = _search(world)
    assert out["ok"] and out["run"] == RUN and out["licensed"] is False
    assert (world["own"] / f"{OUT}.json").exists()
    assert out["survivors"] >= 1 and out["rows"][0]["survives"]
    stored = json.loads((world["own"] / f"{OUT}.json").read_text(encoding="utf-8"))
    assert set(stored["features"]) == set(world["sessions"])
    assert "features" not in out, "the read leaves the per-session features out"
    brief = ideafiles.searches("mine")["searches"]
    assert [b["name"] for b in brief] == [OUT] and "rows" not in brief[0]


def test_declare_drafts_only_a_surviving_testable_idea_once(world):
    out = _search(world)
    top = out["rows"][0]["id"]
    loser = next(r["id"] for r in out["rows"] if not r["survives"])
    assert ideafiles.declare("mine", OUT, loser)["error"].startswith("only an idea that survived")
    made = ideafiles.declare("mine", OUT, top)
    assert made["ok"] and made["row"]["status"] == "declared" and out["split"] in made["row"]["rule"]
    assert ideafiles.declare("mine", OUT, top)["error"] == "this idea is declared already"
    again = ideafiles.search_result("mine", OUT)
    assert again["declared"] == [top] and again["rows"][0]["declaration"]["idea_search"]["idea"] == top


def test_the_held_out_part_is_read_once_per_declared_idea_and_logged(world):
    out = _search(world)
    top = out["rows"][0]["id"]
    assert ideafiles.holdout("mine", OUT, top, {})["error"] == "declare the idea before its held-out test"
    ideafiles.declare("mine", OUT, top)
    read = ideafiles.holdout("mine", OUT, top, {})
    assert read["ok"] and read["verdict"] == "passed" and read["uses"] == 1
    assert ideafiles.holdout("mine", OUT, top, {})["error"] == "this idea has had its one held-out test"
    after = ideafiles.search_result("mine", OUT)
    assert after["held_out_used"] == 1 and after["rows"][0]["held_out_read"]["verdict"] == "passed"


def test_a_licensed_run_is_searched_and_tested_only_while_the_licence_allows(world, monkeypatch):
    src = world["own"] / f"{RUN}.json"
    world["lic"].mkdir(parents=True)
    src.rename(world["lic"] / f"{RUN}.json")
    monkeypatch.setattr(licensing, "status", lambda s: {"active": False})
    plan = ideafiles.plan("mine")
    assert plan["licensed"] is True
    assert drv.main(["run", "--name", "mine", "--run", RUN, "--split", plan["split"], "--out", OUT]) == 2
    monkeypatch.setattr(licensing, "status", lambda s: {"active": True})
    assert drv.main(["run", "--name", "mine", "--run", RUN, "--split", plan["split"], "--out", OUT,
                     "--draws", "300"]) == 0  # fmt: skip
    assert (world["lic"] / f"{OUT}.json").exists(), "a result from a licensed run is licensed"
    top = ideafiles.search_result("mine", OUT)["rows"][0]["id"]
    assert ideafiles.declare("mine", OUT, top)["ok"], "a declaration reads no licensed figure"
    monkeypatch.setattr(licensing, "status", lambda s: {"active": False})
    assert "licence" in ideafiles.holdout("mine", OUT, top, {})["error"]


def test_a_split_outside_the_run_is_refused(world):
    assert drv.main(["run", "--name", "mine", "--run", RUN, "--split", "2030-01-01", "--out", OUT]) == 1
