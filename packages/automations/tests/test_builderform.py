"""The Builder's form schema and its "Today, live" read: the schema is the engine's own tables, and a
decision now is paper's, on a read-only snapshot, writing nothing."""

from __future__ import annotations

import inspect

import pytest
from cherrypick.core import signals
from market import CALENDAR
from test_paper_loop import DAY, at, world

from cherrypick.automations import arms as arms_mod
from cherrypick.automations import builderform, drafts, enrolment, gates, sizing


def test_the_schema_is_the_engines_own_tables():
    s = builderform.schema()
    assert s["blocks"] == {b: list(k) for b, k in enrolment.BLOCKS.items()}
    assert {g["gate"] for g in s["gates"]} == set(gates.REGISTRY)
    assert set(s["strike_rules"]) == set(arms_mod.STRIKE_KEYS)
    assert all(s["strike_rules"][k]["keys"] == dict(v) for k, v in arms_mod.STRIKE_KEYS.items())
    assert set(s["expiry"]) == set(arms_mod.EXPIRY_KEYS)
    # Every flag is a key some expiry kind takes, and the check refuses anything but true or false.
    assert s["expiry_flags"] == list(arms_mod.EXPIRY_FLAGS) == ["monthly"]
    for f in s["expiry_flags"]:
        kinds = [k for k, keys in arms_mod.EXPIRY_KEYS.items() if f in keys]
        assert kinds
        for k in kinds:
            rule = {"kind": k, **{x: 1 for x, req in arms_mod.EXPIRY_KEYS[k].items() if req}}
            assert arms_mod._expiry_problems("x", {**rule, f: "true"}) == [f"x.{f}: true or false"]
            assert arms_mod._expiry_problems("x", {**rule, f: True}) == []
    assert set(s["exits"]) == arms_mod.EXIT_KEYS and set(s["limits"]) == arms_mod.LIMIT_KEYS
    assert set(s["sizes"]) == set(sizing.SIZE_KEYS)
    assert set(s["signals"]) == set(signals.KINDS)
    assert "at" in s["signals"]["gap"] and "at" not in s["signals"]["change"]  # core's optional keys too


def test_each_event_label_is_named_with_when_it_falls_and_its_next_rule_day():
    from datetime import date

    from cherrypick.automations import events

    about = builderform.schema(today=date(2026, 11, 18))["event_about"]
    assert set(about) == set(builderform.schema()["event_labels"]) == set(events.ABOUT)
    assert all(a["name"] and a["when"] for a in about.values())
    # A rule label's next day is the rule's own, counted from today inclusive.
    assert about["VIXExpiry"]["next"] == "2026-11-18"
    assert about["OPEX"]["next"] == "2026-11-20"
    assert about["MonthEnd"]["next"] == "2026-11-30"
    assert about["TripleWitching"]["next"] == "2026-12-18"
    assert about["FirstWeekly"]["next"] == "2026-12-04"
    # A symbol's own dates have no next day without a symbol.
    assert about["Earnings"]["next"] is None and about["ExDividend"]["next"] is None


def test_a_next_event_day_stops_where_the_calendar_stops_speaking():
    from datetime import date

    from cherrypick.automations import events

    cal = events.EventCalendar({"PPI": ["2026-12-11"]}, {"PPI": [["2026-11-01", "2026-11-30"]]})
    assert events.next_day(cal, "PPI", date(2026, 11, 18)) is None  # December is not covered
    cal = events.EventCalendar({"PPI": ["2026-12-11"]}, {"PPI": [["2026-11-01", "2026-12-31"]]})
    assert events.next_day(cal, "PPI", date(2026, 11, 18)) == "2026-12-11"


def test_each_gate_lists_its_functions_parameters_and_the_stage_it_reads():
    by = {g["gate"]: g for g in builderform.schema()["gates"]}
    for name, (_, fn) in gates.REGISTRY.items():
        params = list(inspect.signature(fn).parameters)[1:]
        assert [p["name"] for p in by[name]["params"]] == params
        assert by[name]["stage"] == gates.CORE_STAGE[name]
    gex = {p["name"]: p for p in by["gex_ratio"]["params"]}
    assert gex["strikes"]["required"] is True and gex["mode"] == {
        "name": "mode",
        "required": False,
        "default": "nearest",
    }


def test_every_entry_kind_the_schema_offers_is_one_the_engine_accepts():
    spec = drafts.editable({}, "iron-fly-1000")
    entries = {
        "time": {"kind": "time", "at": "10:30"},
        "orb": {"kind": "orb", "minutes": 30, "direction": "below", "latest": "13:00"},
        "trigger": {
            "kind": "trigger",
            "trigger": {
                "signal": {"kind": "gap", "direction": "up", "pct": 0.5},
                "window": {"open": "09:45", "close": "10:00"},
                "within": None,
            },
            "bar_minutes": 5,
        },
    }
    assert set(entries) == set(builderform.schema()["entry"])
    for kind, entry in entries.items():
        arm, problems = builderform.resolve({}, "my-fly", {**spec, "entry": entry})
        assert problems == [] and arm["entry"]["kind"] == kind


def test_an_unsaved_edit_resolves_and_a_bad_one_names_its_problems():
    spec = drafts.editable({}, "iron-fly-1000")
    arm, problems = builderform.resolve({}, "iron-fly-1000", {**spec, "colour": "red"})
    assert arm is None and any("unknown key 'colour'" in p for p in problems)
    arm, problems = builderform.resolve({}, "iron-fly-1000", None)
    assert problems == [] and arm["name"] == "iron-fly-1000"
    assert builderform.resolve({}, "nothing-here", None)[0] is None
    assert builderform.resolve({}, "Bad Name", None)[1][0].startswith("name:")


def _counts(conn):
    tables = ("ensemble_decisions", "ensemble_entries", "ensemble_positions")
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def test_today_is_papers_decision_with_its_legs_and_writes_nothing(tmp_path):
    w = world(tmp_path, keep=())  # the automation is switched off: today still decides it
    before = _counts(w.conn)
    arm = w.arms["iron-fly-1000"]
    assert arm["enabled"] is False
    out = builderform.today(w.config, arm, at(10, 0), conn=w.conn, cache_path=w.cache_path, evts=CALENDAR)
    assert out["state"] == "would_enter" and out["in_window"] is True and out["window"] == ["10:00", "10:04"]
    assert out["entry"]["expiry"] == DAY and len(out["entry"]["legs"]) == 4
    assert out["checks"] and all(c["passed"] for c in out["checks"])
    assert _counts(w.conn) == before
    assert not list(w.replay.glob("*.jsonl"))


def test_today_names_the_check_that_blocks_it(tmp_path):
    w = world(tmp_path, keep=(), spot=6100.0)
    out = builderform.today(
        w.config, w.arms["iron-fly-1000"], at(10, 0), conn=w.conn, cache_path=w.cache_path, evts=CALENDAR
    )
    assert out["state"] == "would_skip" and out["reason"] == "gate:sd_change"
    assert out["checks"][-1]["check"].endswith("sd_change") and out["checks"][-1]["passed"] is False
    assert "entry" not in out


def test_without_a_spot_it_says_there_is_no_snapshot(tmp_path):
    w = world(tmp_path, keep=())
    out = builderform.today(
        w.config,
        w.arms["iron-fly-1000"],
        at(10, 0),
        conn=w.conn,
        cache_path=str(tmp_path / "missing.db"),
        evts=CALENDAR,
    )
    assert out["state"] == "no_snapshot" and out["reason"]


def test_each_trigger_form_the_schema_offers_is_one_core_accepts():
    from cherrypick.core import entrychecks as ec

    forms = builderform.schema()["trigger_forms"]
    ec.validate_trigger({k: {"at": "10:30", "within": 5}[k] for k in forms["at"]})
    sig = {
        "signal": {"kind": "gap", "direction": "up", "pct": 0.5},
        "window": {"open": "09:45", "close": "10:00"},
        "within": None,
        "daily": False,
        "developing": False,
    }
    ec.validate_trigger({k: sig[k] for k in forms["signal"]})


def test_the_command_opens_the_ledger_read_only_and_answers_on_stdout(tmp_path, monkeypatch, capsys):
    import io
    import json

    from cherrypick.automations import cli, db

    w = world(tmp_path, keep=())
    w.conn.close()
    opened = []
    monkeypatch.setattr(db, "connect", lambda *a, **k: pytest.fail("the ledger opened for writing"))
    real_ro = db.connect_ro

    def ro(path=None):
        opened.append(path)
        return real_ro(path)

    monkeypatch.setattr(db, "connect_ro", ro)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"name": "iron-fly-1000"})))
    assert cli.main(["--db", str(tmp_path / "paper.db"), "builder-today"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["name"] == "iron-fly-1000" and opened == [str(tmp_path / "paper.db")]


def test_every_wizard_shape_is_an_automation_the_engine_accepts():
    s = builderform.schema()
    assert set(s["shapes"]) == set(builderform.SHAPES)
    for key, shape in s["shapes"].items():
        name = "wiz-" + key.replace("_", "-")
        arm, problems = builderform.resolve({}, name, shape["spec"])
        assert problems == [], (key, problems)
        assert len(arm["legs"]) == len(builderform.SHAPES[key]["legs"])
    credit = {"profit_target_pct": 50, "stop_credit_multiple": 1, "close_at_dte": 21}
    assert s["shapes"]["put_credit_spread"]["spec"]["exit"] == credit == s["credit_rules"]
    assert s["shapes"]["custom"]["spec"]["exit"] == {"close_at_dte": 21}
    # monthlies, so our own end-of-day chains can backtest every shape
    assert all(sh["spec"]["expiry"].get("monthly") is True for sh in s["shapes"].values())
