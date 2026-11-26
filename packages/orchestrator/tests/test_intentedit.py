"""`intentedit`: the one door to a trade's intent, `trades.<key>.intent` in manage.json.

An answer on the position writes exactly that value and nothing else: the key is a stable trade ID (a
wheel or not) or a call's long contract (a pmcc or a diagonal), nothing like a path, and the file's notes
and other entries survive byte for byte. Each refusal is shown to leave the config byte-identical.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from cherrypick.orchestrator import configcli, configedit, intentedit

pytestmark = pytest.mark.unit

MANAGE_PKG = Path(__file__).resolve().parents[2] / "manage"
CFG = {"modules": {"manage": {"enabled": True, "path": str(MANAGE_PKG)}}}

ID = "XYZ-261001-1"
LONG = "XYZ 280121C00040000"

# An older copy: written before the trades block existed.
OLD = """{
  "_comment": "notes a json round trip would erase",
  "positions": {
    "XYZ": {
      "intent": "pmcc"
    }
  }
}
"""

WITH_TRADES = """{
  "_comment": "notes a json round trip would erase",
  "positions": {},
  "trades": {
    "AAA-261001-1": {
      "intent": "wheel"
    },
    "XYZ-261001-1": {
      "note": "mine",
      "intent": "wheel"
    },
    "XYZ-261002-1": {
      "note": "no intent yet"
    }
  }
}
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    (tmp_path / "config").mkdir()
    return tmp_path


def put(home, text):
    path = home / "config" / "manage.json"
    path.write_text(text, encoding="utf-8")
    return path


def req(**over):
    return {"op": "intent_set", "trade": ID, "intent": "wheel", **over}


def backups(home):
    return (
        list((home / "state" / "config-backups").glob("manage.*.json")) if (home / "state").exists() else []
    )


def untouched(home, text, out, code):
    assert out["ok"] is False and out["code"] == code, out
    assert (home / "config" / "manage.json").read_text(encoding="utf-8") == text
    assert backups(home) == []


# --------------------------------------------------------------------------- what is written
def test_a_first_answer_adds_the_block_and_one_entry_and_keeps_the_notes(home):
    put(home, OLD)
    out = intentedit.save(CFG, req())
    assert out["ok"] is True and out["changed"] is True and out["previous"] is None
    new = (home / "config" / "manage.json").read_text(encoding="utf-8")
    assert json.loads(new) == {**json.loads(OLD), "trades": {ID: {"intent": "wheel"}}}
    assert '"_comment": "notes a json round trip would erase"' in new
    assert len(backups(home)) == 1


def test_a_first_answer_works_as_a_real_config_has_it_and_under_a_relocated_home(home, monkeypatch):
    # A real config has no `modules.manage` (manage is not a module), and under CHERRYPICK_HOME the source
    # anchor is the home, where no example ships: the example is found beside the code, or the first
    # answer on a manage.json written before the trades block is refused.
    monkeypatch.setattr(configedit.cfgmod, "ROOT", home)
    put(home, OLD)
    out = intentedit.save({"modules": {}}, req())
    assert out["ok"] is True and out["changed"] is True, out
    new = (home / "config" / "manage.json").read_text(encoding="utf-8")
    assert json.loads(new) == {**json.loads(OLD), "trades": {ID: {"intent": "wheel"}}}


def test_the_manage_example_is_found_beside_the_code_not_only_under_the_home(home, monkeypatch):
    monkeypatch.setattr(configedit.cfgmod, "ROOT", home)
    found = configedit._example_path({"modules": {}}, "manage")
    assert found is not None and found.exists()
    assert found == (MANAGE_PKG / "config.example.json").resolve()


def test_a_changed_answer_moves_one_value_and_returns_the_one_it_replaced(home):
    put(home, WITH_TRADES)
    out = intentedit.save(CFG, req(intent="no_wheel"))
    assert out["ok"] is True and out["previous"] == "wheel" and out["intent"] == "no_wheel"
    new = (home / "config" / "manage.json").read_text(encoding="utf-8")
    assert new == WITH_TRADES.replace(
        '"note": "mine",\n      "intent": "wheel"', '"note": "mine",\n      "intent": "no_wheel"'
    )  # byte for byte, one value


def test_an_entry_with_no_intent_gets_the_key_and_keeps_its_note(home):
    put(home, WITH_TRADES)
    out = intentedit.save(CFG, req(trade="XYZ-261002-1"))
    want = json.loads(WITH_TRADES)
    want["trades"]["XYZ-261002-1"]["intent"] = "wheel"
    assert out["ok"] is True and out["previous"] is None
    assert json.loads((home / "config" / "manage.json").read_text(encoding="utf-8")) == want


def test_the_other_entries_are_never_touched(home):
    put(home, WITH_TRADES)
    intentedit.save(CFG, req(intent="no_wheel"))
    after = json.loads((home / "config" / "manage.json").read_text(encoding="utf-8"))
    assert after["trades"]["AAA-261001-1"] == {"intent": "wheel"}
    assert after["trades"]["XYZ-261002-1"] == {"note": "no intent yet"}


def test_clearing_writes_null_and_a_missing_entry_is_a_no_op(home):
    put(home, WITH_TRADES)
    out = intentedit.save(CFG, req(intent=None))
    assert out["ok"] is True and out["previous"] == "wheel"
    assert (
        json.loads((home / "config" / "manage.json").read_text(encoding="utf-8"))["trades"][ID]["intent"]
        is None
    )
    other = put(home, WITH_TRADES)
    out = intentedit.save(CFG, req(trade="ZZZ-261001-1", intent=None))
    assert out["ok"] is True and out["changed"] is False
    assert other.read_text(encoding="utf-8") == WITH_TRADES


def test_the_same_answer_is_a_no_op_with_no_backup(home):
    put(home, WITH_TRADES)
    out = intentedit.save(CFG, req(intent="wheel"))
    assert out["ok"] is True and out["changed"] is False and out["previous"] == "wheel"
    assert (home / "config" / "manage.json").read_text(encoding="utf-8") == WITH_TRADES
    assert backups(home) == []


def test_an_existing_spelling_of_the_long_is_reused_not_duplicated(home):
    text = '{\n  "trades": {\n    "XYZ   280121C00040000": {\n      "intent": "pmcc"\n    }\n  }\n}\n'
    put(home, text)
    out = intentedit.save(CFG, req(trade="XYZ 280121C00040000", intent="diagonal"))
    assert out["ok"] is True and out["previous"] == "pmcc"
    assert json.loads((home / "config" / "manage.json").read_text(encoding="utf-8")) == {
        "trades": {"XYZ   280121C00040000": {"intent": "diagonal"}}
    }


def test_a_held_spelling_in_another_case_is_reused_too(home):
    put(home, '{\n  "trades": {\n    "xyz 280121c00040000": {\n      "intent": "pmcc"\n    }\n  }\n}\n')
    out = intentedit.save(CFG, req(trade=LONG, intent="diagonal"))
    assert out["ok"] is True and out["previous"] == "pmcc" and out["trade"] == "xyz 280121c00040000"
    assert json.loads((home / "config" / "manage.json").read_text(encoding="utf-8")) == {
        "trades": {"xyz 280121c00040000": {"intent": "diagonal"}}
    }


def test_clearing_a_value_that_is_not_text_still_clears_it(home):
    put(home, '{\n  "trades": {\n    "XYZ-261001-1": {\n      "intent": 5\n    }\n  }\n}\n')
    out = intentedit.save(CFG, req(intent=None))
    assert out["ok"] is True and out["changed"] is True and out["previous"] is None
    assert json.loads((home / "config" / "manage.json").read_text(encoding="utf-8")) == {
        "trades": {"XYZ-261001-1": {"intent": None}}
    }


# --------------------------------------------------------------------------- what may be written
@pytest.mark.parametrize(
    ("trade", "intent", "ok"),
    [
        (ID, "wheel", True),
        (ID, "no_wheel", True),
        (ID, "pmcc", False),
        (ID, "diagonal", False),
        (LONG, "pmcc", True),
        (LONG, "diagonal", True),
        (LONG, "wheel", False),
        (LONG, "no_wheel", False),
    ],
)
def test_a_trade_takes_its_own_intents_and_a_long_its_own(home, trade, intent, ok):
    put(home, OLD)
    out = intentedit.save(CFG, req(trade=trade, intent=intent))
    if ok:
        assert out["ok"] is True
    else:
        untouched(home, OLD, out, "invalid")


@pytest.mark.parametrize(
    "trade",
    [
        "XYZ-open-ceda13",  # provisional: an entry under it would not survive the next run
        "XYZ",
        "XYZ-261001",
        "xyz-261001-1",
        "XYZ-261001-1/intent",
        "../XYZ-261001-1",
        "a/b",
        "__proto__",
        "",
        "XYZ 280121C0004000",
        "XYZ-261001-1\n",  # a trailing newline is not a trade ID
        "XYZ 280121C00040000\n",
        "XYZ-\u0662\u0662\u0662\u0662\u0662\u0662-1",  # digits that are not ASCII
    ],
)
def test_a_key_that_is_neither_a_stable_trade_id_nor_a_call_contract_is_refused(home, trade):
    put(home, OLD)
    for intent in ("wheel", "pmcc"):  # whichever kind of answer it is trying to pass as
        untouched(home, OLD, intentedit.save(CFG, req(trade=trade, intent=intent)), "invalid")


@pytest.mark.parametrize("intent", ["pmcc", "diagonal", "wheel", "no_wheel"])
def test_a_put_contract_is_not_a_long_to_answer_for(home, intent):
    # Only a call has a diagonal: the PMCC view reads `trades` keys by call contract alone.
    put(home, OLD)
    untouched(home, OLD, intentedit.save(CFG, req(trade="XYZ 270319P00013000", intent=intent)), "invalid")


def test_a_key_with_a_slash_is_written_and_changed_under_its_own_name(home):
    # A class share is spelled with a slash; as a JSON-pointer token it must be escaped, or the splice
    # looks for a key that is not there.
    text = '{\n  "trades": {\n    "BRK/B-261001-1": {\n      "intent": "wheel"\n    }\n  }\n}\n'
    put(home, text)
    out = intentedit.save(CFG, req(trade="BRK/B-261001-1", intent="no_wheel"))
    assert out["ok"] is True and out["previous"] == "wheel"
    assert json.loads((home / "config" / "manage.json").read_text(encoding="utf-8")) == {
        "trades": {"BRK/B-261001-1": {"intent": "no_wheel"}}
    }


@pytest.mark.parametrize("intent", ["income", "hedge", "", True, 3, ["wheel"], {"a": 1}])
def test_an_intent_it_does_not_take_is_refused(home, intent):
    put(home, OLD)
    untouched(home, OLD, intentedit.save(CFG, req(intent=intent)), "invalid")


def test_a_trade_that_is_not_text_is_refused(home):
    put(home, OLD)
    for trade in (None, 7, ["XYZ-261001-1"]):
        untouched(home, OLD, intentedit.save(CFG, req(trade=trade)), "invalid")


def test_a_trades_block_that_is_not_an_object_is_refused(home):
    text = '{\n  "trades": []\n}\n'
    put(home, text)
    untouched(home, text, intentedit.save(CFG, req()), "invalid")


def test_a_missing_manage_config_is_not_found(home):
    out = intentedit.save(CFG, req())
    assert out["ok"] is False and out["code"] == "not_found"


def test_a_stale_version_is_a_conflict_and_changes_nothing(home):
    put(home, WITH_TRADES)
    out = intentedit.save(CFG, req(intent="no_wheel", expected_mtime=1))
    untouched(home, WITH_TRADES, out, "conflict")


# --------------------------------------------------------------------------- the guard, shown to fire
def test_an_edit_that_would_change_more_than_the_one_intent_is_refused(home, monkeypatch):
    put(home, WITH_TRADES)
    real = configedit.splice_value

    def sloppy(text, pointer, value):
        return real(text, pointer, value).replace('"note": "no intent yet"', '"note": "changed"')

    monkeypatch.setattr(configedit, "splice_value", sloppy)
    untouched(home, WITH_TRADES, intentedit.save(CFG, req(intent="no_wheel")), "invalid")


def test_an_added_entry_that_would_change_more_than_the_one_intent_is_refused(home, monkeypatch):
    put(home, WITH_TRADES)
    real = configedit.insert_key

    def sloppy(text, pointer, key, value):
        return real(text, pointer, key, value).replace('"positions": {}', '"positions": {"X": 1}')

    monkeypatch.setattr(configedit, "insert_key", sloppy)
    untouched(home, WITH_TRADES, intentedit.save(CFG, req(trade="ZZZ-261001-1")), "invalid")


# --------------------------------------------------------------------------- the console's door
def test_the_console_bridge_reaches_the_op_and_its_refusals_are_data(home, monkeypatch):
    monkeypatch.setattr(configcli.cfgmod, "load_config", lambda: copy.deepcopy(CFG))
    put(home, OLD)
    out = configcli.handle(req())
    assert out["ok"] is True and out["trade"] == ID and out["intent"] == "wheel"
    refused = configcli.handle(req(trade="a/b"))
    assert refused["ok"] is False and refused["code"] == "invalid"
