"""scripts/sync_stage_watchlists.py's plan: a write to the broker account, so its refusals are tested."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "sync_stage_watchlists.py"
spec = importlib.util.spec_from_file_location("sync_stage_watchlists", SCRIPT)
ssw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ssw)

DOC = {
    "session": "2026-10-16",
    "lists": {"Early Breakdown": [{"symbol": "TSM"}, {"symbol": "BRK.B"}], "Early Breakout": []},
}


def test_a_new_list_is_created_in_the_brokers_spelling_under_our_group():
    p = ssw.plan(DOC, {}, date(2026, 10, 19))
    eb = next(x for x in p["lists"] if x["name"] == "cherrypick early breakdown")
    assert eb["action"] == "create" and eb["entries"] == ["TSM", "BRK/B"]
    assert ssw.body(eb["name"], eb["entries"])["group-name"] == "cherrypick"


def test_a_list_our_rule_leaves_empty_is_left_as_it_was():
    p = ssw.plan(DOC, {}, date(2026, 10, 19))
    assert next(x for x in p["lists"] if x["name"] == "cherrypick early breakout")["action"] == "skip"


def test_a_same_named_list_a_person_made_is_never_replaced():
    mine = {"cherrypick early breakdown": {"group_name": None, "symbols": ["AAPL"]}}
    p = ssw.plan(DOC, mine, date(2026, 10, 19))
    assert next(x for x in p["lists"] if x["name"] == "cherrypick early breakdown")["action"] == "refuse"


def test_ours_is_replaced_whole_and_an_unchanged_one_left_alone():
    ours = {"cherrypick early breakdown": {"group_name": "cherrypick", "symbols": ["TSM", "PANW"]}}
    item = next(
        x for x in ssw.plan(DOC, ours, date(2026, 10, 19))["lists"] if x["name"].endswith("breakdown")
    )
    assert (item["action"], item["add"], item["remove"]) == ("replace", ["BRK/B"], ["PANW"])
    same = {"cherrypick early breakdown": {"group_name": "cherrypick", "symbols": ["TSM", "BRK/B"]}}
    item = next(
        x for x in ssw.plan(DOC, same, date(2026, 10, 19))["lists"] if x["name"].endswith("breakdown")
    )
    assert item["action"] == "none"


def test_stale_or_missing_lists_write_nothing():
    assert ssw.plan(DOC, {}, date(2026, 10, 21))["ok"] is False  # five days old
    assert ssw.plan(None, {}, date(2026, 10, 19))["ok"] is False
