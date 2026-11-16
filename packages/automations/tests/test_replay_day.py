"""The nightly replay (#1202): a paper session's recorded ticks run again through the backtest's decision
path on the snapshots paper kept. An unchanged day replays to the same decisions and fills; a recorded
decision or fill that differs is listed, and the command exits 1."""

from __future__ import annotations

import json

from market import CALENDAR
from test_paper_loop import DAY, at, tick, world

from cherrypick.automations import cli, record


def recorded_day(tmp_path):
    w = world(tmp_path)
    w.replay = record.replay_dir()
    assert tick(w, at(10, 0))["entries"] == {"iron-fly-1000": "entered"}
    path = record.replay_dir() / f"{DAY}.jsonl"
    assert path.exists()
    return w, path


def rewrite(path, change):
    recs = record.read(path)
    change(recs)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


def test_an_unchanged_day_replays_to_the_same_decisions_and_fills(tmp_path):
    w, path = recorded_day(tmp_path)
    out = record.compare_day(path, CALENDAR, costs=w.config)
    assert out["session"] == DAY and out["ticks"] >= 1 and out["decisions"] >= 1
    assert out["differ"] == []


def test_a_fill_that_differs_is_listed_field_by_field(tmp_path):
    w, path = recorded_day(tmp_path)

    def bump(recs):
        for r in recs:
            for d in r["decisions"]:
                if d.get("entry"):
                    d["entry"]["quantity"] += 1
                    d["entry"]["fees_open"] += 1.0

    rewrite(path, bump)
    out = record.compare_day(path, CALENDAR, costs=w.config)
    assert {(x["arm"], x["field"]) for x in out["differ"]} == {
        ("iron-fly-1000", "entry.quantity"),
        ("iron-fly-1000", "entry.fees_open"),
    }


def test_a_decision_that_differs_or_an_arm_decided_on_one_side_only_is_listed(tmp_path):
    w, path = recorded_day(tmp_path)

    def refuse(recs):
        r = recs[-1]
        r["decisions"][0].update(enter=False, reason="gate:mid_price")
        r["decisions"].append({"arm": "ghost", "enter": False, "reason": "weekday", "checks": []})

    rewrite(path, refuse)
    out = record.compare_day(path, CALENDAR, costs=w.config)
    fields = {(x["arm"], x["field"]) for x in out["differ"]}
    assert ("iron-fly-1000", "enter") in fields and ("iron-fly-1000", "reason") in fields
    assert ("ghost", "decided") in fields


def test_the_command_exits_1_on_a_difference_and_0_without_a_record(tmp_path, capsys):
    w, path = recorded_day(tmp_path)
    assert cli.main(["replay-day", "--date", DAY]) == 0
    assert json.loads(capsys.readouterr().out)["differ"] == []
    rewrite(path, lambda recs: recs[-1]["decisions"][0].update(reason="nothing"))
    assert cli.main(["replay-day", "--date", DAY]) == 1
    assert json.loads(capsys.readouterr().out)["differ"][0]["field"] == "reason"
    assert cli.main(["replay-day", "--date", "2026-11-20"]) == 0
    assert json.loads(capsys.readouterr().out)["record"] is False
