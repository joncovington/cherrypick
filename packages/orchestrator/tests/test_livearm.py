"""`livearm`: the one door to `live.arm`.

The general config save refuses `/live/arm` in both directions; this op is the single exception, and the
tests pin what keeps it narrow: only that key changes, the typed phrase, an arm with a paper twin, only
when flat and settled, journaled first. Each refusal is shown to leave the config byte-identical.
"""

from __future__ import annotations

import copy
import json

import pytest
from cherrypick.core import live as _live

from cherrypick.orchestrator import configcli, configedit, livearm

pytestmark = pytest.mark.unit

MEIC = """{
  "_comment": "notes a json round trip would erase",
  "enable_live_trading": true,
  "live": {
    "symbol": "SPX",
    "arm": "pm-200",
    "_arm_note": "which arm trades live",
    "max_open_bp_dollars": 3000
  },
  "symbols": ["SPX"]
}
"""

REGISTRY = {
    "profiles": {
        "control": {"enabled": True},
        "pm-200": {"enabled": False, "min_iv_rank": 0.1},
        "pm-1400": {"enabled": False, "min_iv_rank": 0.2},
        "live-shadow": {"enabled": True, "follows_live": True},
        "live-shadow-pm-200": {"enabled": True, "shadow_of": "pm-200"},
        "live-shadow-pm-1400": {"enabled": True, "shadow_of": "pm-1400"},
    }
}

FLAT = {
    "ok": True,
    "date": "2026-11-24",
    "armed_for": None,
    "open_positions": 0,
    "pending_orders": [],
    "session_settled": True,
    "overdue_settlement": [],
    "broker_held": {"unresolved": [], "unrecorded": []},
    "orphaned_orders": [],
    "ledger_unreadable": None,
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "meic.json").write_text(MEIC, encoding="utf-8")
    (tmp_path / "config" / "meic.risk.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
    return tmp_path


def _flat(**over):
    return lambda cfg: {**FLAT, **over}


def _req(**over):
    return {"op": "set_live_arm", "module": "meic", "arm": "pm-1400", "confirm": "CHANGE ARM", **over}


def _text(home):
    return (home / "config" / "meic.json").read_text(encoding="utf-8")


def test_a_change_moves_only_live_arm_and_keeps_the_files_notes(home):
    out = livearm.set_live_arm({}, _req(), _flat())
    assert out["ok"] is True and out["changed"] is True
    assert out["from"] == "pm-200" and out["arm"] == "pm-1400" and out["twin"] == "live-shadow-pm-1400"
    new = _text(home)
    assert new == MEIC.replace('"arm": "pm-200"', '"arm": "pm-1400"')  # byte for byte, one value
    assert json.loads(new)["live"]["max_open_bp_dollars"] == 3000
    assert list((home / "state" / "config-backups").glob("meic.*.json"))


def test_the_change_is_journaled_with_the_arm_it_left_and_the_one_it_took(home):
    livearm.set_live_arm({}, _req(), _flat())
    rows = [r for r in _live.arm_events("meic") if r.get("event") == "live_arm_changed"]
    assert len(rows) == 1
    assert rows[0]["actor"] == "config" and rows[0]["from_arm"] == "pm-200" and rows[0]["to_arm"] == "pm-1400"


def test_the_general_save_still_refuses_live_arm(home):
    old = json.loads(MEIC)
    new = copy.deepcopy(old)
    new["live"]["arm"] = "pm-1400"
    assert configedit.guard_violations("meic", old, new) == ["/live/arm"]


def test_an_arm_the_key_is_missing_from_is_inserted(home):
    (home / "config" / "meic.json").write_text(MEIC.replace('    "arm": "pm-200",\n', ""), encoding="utf-8")
    out = livearm.set_live_arm({}, _req(), _flat())
    assert out["ok"] is True
    assert json.loads(_text(home))["live"]["arm"] == "pm-1400"


@pytest.mark.parametrize(
    ("req", "code"),
    [
        ({"confirm": "change arm"}, "confirm_required"),
        ({"confirm": None}, "confirm_required"),
        ({"confirm": "CHANGE ARM "}, "confirm_required"),
        ({"module": "ladder"}, "unsupported"),
        ({"module": "flies"}, "unsupported"),
        ({"login": "margin"}, "unsupported"),
        ({"arm": ""}, "invalid"),
        ({"arm": 7}, "invalid"),
        ({"arm": "nope"}, "unknown_arm"),
        ({"arm": "live-shadow"}, "unknown_arm"),
        ({"arm": "live-shadow-pm-200"}, "unknown_arm"),
        ({"arm": "control"}, "no_twin"),
    ],
)
def test_a_refusal_changes_nothing(home, req, code):
    calls = []

    def status(cfg):
        calls.append(1)
        return dict(FLAT)

    out = livearm.set_live_arm({}, _req(**req), status)
    assert out["ok"] is False and out["code"] == code
    assert _text(home) == MEIC
    assert not (home / "state" / "config-backups").exists()
    assert not [r for r in _live.arm_events("meic") if r.get("event") == "live_arm_changed"]
    if code in {"confirm_required", "unsupported", "invalid", "unknown_arm", "no_twin"}:
        assert calls == [], "refused before the module was asked anything"


@pytest.mark.parametrize(
    "over",
    [
        {"armed_for": "2026-11-24"},
        {"open_positions": 1},
        {"open_positions": None},
        {"pending_orders": ["LIVE-SPX-1"]},
        {"session_settled": False},
        {"session_settled": None},
        {"overdue_settlement": [{"session": "2026-11-23"}]},
        {"broker_held": {"unresolved": ["ext-1"], "unrecorded": []}},
        {"broker_held": {"unresolved": [], "unrecorded": [{"ext": "x"}]}},
        {"orphaned_orders": [{"order_id": 7}]},
        {"ledger_unreadable": "database is locked"},
    ],
)
def test_it_is_refused_unless_the_loop_is_flat_and_settled(home, over):
    out = livearm.set_live_arm({}, _req(), _flat(**over))
    assert out["ok"] is False and out["code"] == "not_flat" and out["blockers"]
    assert _text(home) == MEIC


def test_a_status_it_cannot_read_is_not_flat(home):
    out = livearm.set_live_arm({}, _req(), lambda cfg: None)
    assert out["ok"] is False and out["code"] == "status_unreadable"
    assert _text(home) == MEIC


def test_a_missing_field_counts_against_the_loop(home):
    bare = {"ok": True, "date": "2026-11-24"}
    out = livearm.set_live_arm({}, _req(), lambda cfg: bare)
    assert out["code"] == "not_flat"
    assert _text(home) == MEIC


def test_the_same_arm_is_a_no_op_that_never_asks_the_module(home):
    def status(cfg):
        raise AssertionError("asked the module about a change that is not one")

    out = livearm.set_live_arm({}, _req(arm="pm-200"), status)
    assert out == {
        "ok": True,
        "changed": False,
        "module": "meic",
        "arm": "pm-200",
        "note": "already the live arm",
    }


def test_a_journal_that_cannot_be_written_stops_the_change(home, monkeypatch):
    monkeypatch.setattr(_live, "record_event", lambda *a, **k: False)
    out = livearm.set_live_arm({}, _req(), _flat())
    assert out["ok"] is False and out["code"] == "journal_unwritable"
    assert _text(home) == MEIC
    assert not (home / "state" / "config-backups").exists()


def test_an_edit_that_would_change_more_than_live_arm_is_refused(home, monkeypatch):
    """The guard, shown to fire: if the splice ever touched another key, nothing is written."""
    real = configedit.splice_value

    def sloppy(text, pointer, value):
        return real(text, pointer, value).replace(
            '"max_open_bp_dollars": 3000', '"max_open_bp_dollars": 9000'
        )

    monkeypatch.setattr(configedit, "splice_value", sloppy)
    out = livearm.set_live_arm({}, _req(), _flat())
    assert out["ok"] is False and out["code"] == "invalid"
    assert _text(home) == MEIC
    assert not [r for r in _live.arm_events("meic") if r.get("event") == "live_arm_changed"]


def test_candidates_are_the_arms_with_a_plain_twin_only():
    profiles = copy.deepcopy(REGISTRY["profiles"])
    profiles["pm-1400"]["follows_live"] = True  # a follower is not an arm live can run
    profiles["live-shadow-control"] = {
        "enabled": True,
        "shadow_of": "control",
        "min_iv_rank": 0,
    }  # an experiment
    assert livearm.candidates(profiles) == {"pm-200": "live-shadow-pm-200"}


def test_options_offer_the_arms_and_say_why_a_change_is_refused_now(home):
    ok = livearm.options({}, _flat())
    assert ok["current"] == "pm-200" and ok["can_change"] is True and ok["blockers"] == []
    assert [a["arm"] for a in ok["arms"]] == ["pm-1400", "pm-200"]
    busy = livearm.options({}, _flat(open_positions=2))
    assert busy["can_change"] is False and "2 position(s) open" in busy["blockers"]
    assert livearm.options({}, lambda cfg: None)["can_change"] is False


def test_the_console_bridge_reaches_the_op_and_its_refusals_are_data(home):
    (home / "config.json").write_text(json.dumps({"modules": {"meic": {}}}), encoding="utf-8")
    out = configcli.handle({"op": "set_live_arm", "module": "meic", "arm": "pm-1400"})
    assert out["ok"] is False and out["code"] == "confirm_required"
    assert _text(home) == MEIC
