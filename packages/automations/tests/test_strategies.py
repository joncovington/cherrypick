"""The Builder's strategy selector: every strategy, at every setting it offers, writes legs and an
expiry the engine loads as they stand."""

from __future__ import annotations

import itertools

import pytest

from cherrypick.automations import builderform, config, sketch, strategies


def _combos(name: str):
    """Every value of the enum settings the strategy takes, the numbers at their defaults."""
    keys = [k for k in strategies.STRATEGIES[name]["settings"] if strategies.SETTINGS[k]["type"] == "enum"]
    for values in itertools.product(*(strategies.SETTINGS[k]["options"] for k in keys)):
        yield dict(zip(keys, values, strict=True))


AVAILABLE = [k for k, v in strategies.STRATEGIES.items() if v.get("available", True)]


@pytest.mark.parametrize("name", AVAILABLE)
def test_every_strategy_at_every_setting_is_an_automation_the_engine_loads(name):
    for given in _combos(name):
        built = strategies.build(name, given)
        spec = builderform.strategy_answer(name, given)["spec"]
        assert spec["legs"] == built["legs"] and spec.get("far_expiry") == built.get("far_expiry")
        arms = config.load_arms({"automations": {"try-it": spec}})
        loaded = arms["try-it"]
        assert len(loaded["legs"]) == len(built["legs"]), (name, given)
        s = sketch.sketch(loaded)
        assert all(g["label"] != "?" for g in s["legs"]), (name, given)


def test_the_legs_are_named_by_id_in_the_written_spelling():
    for name in AVAILABLE:
        legs = strategies.build(name)["legs"]
        assert [g["name"] for g in legs] == [f"L{i + 1}" for i in range(len(legs))], name
        written = {"name", "action", "qty", "side", "strike"}
        assert all(set(g) - {"expiry"} == written for g in legs), name
        assert all(g.get("expiry", "far") == "far" for g in legs), name  # near is never written


def _shape(name, **given):
    return [(g["action"], g["qty"], g["side"]) for g in strategies.build(name, given)["legs"]]


def test_each_structure_is_the_one_its_name_says():
    assert _shape("iron_condor") == [
        ("STO", 1, "put"),
        ("BTO", 1, "put"),
        ("STO", 1, "call"),
        ("BTO", 1, "call"),
    ]
    assert _shape("iron_condor", direction="long") == [
        ("BTO", 1, "put"),
        ("STO", 1, "put"),
        ("BTO", 1, "call"),
        ("STO", 1, "call"),
    ]
    assert _shape("vertical", right="call") == [("STO", 1, "call"), ("BTO", 1, "call")]
    assert _shape("butterfly") == [("STO", 2, "put"), ("BTO", 1, "put"), ("BTO", 1, "put")]
    assert _shape("butterfly", direction="short") == [("BTO", 2, "put"), ("STO", 1, "put"), ("STO", 1, "put")]
    assert _shape("ratio", ratio=3) == [("BTO", 1, "put"), ("STO", 3, "put")]
    assert _shape("jade_lizard") == [("STO", 1, "put"), ("STO", 1, "call"), ("BTO", 1, "call")]


def test_wings_and_spreads_sit_further_out_of_the_money():
    vertical = strategies.build("vertical", {"right": "put", "width": 5})["legs"]
    assert vertical[1]["strike"] == {"rule": "from_leg", "leg": "L1", "points": -5, "match": "nearest"}
    call = strategies.build("vertical", {"right": "call", "width": 5})["legs"]
    assert call[1]["strike"]["points"] == 5
    bwb = strategies.build("broken_wing", {"right": "put", "width": 5, "width2": 10})["legs"]
    assert (bwb[1]["strike"]["points"], bwb[2]["strike"]["points"]) == (5, -10)


def test_placement_by_each_kind_reads_the_target_on_the_right_side():
    def put(by, t):
        return strategies.build("option", {"right": "put", "place_by": by, "target": t})["legs"][0]["strike"]

    assert put("delta", 0.2) == {"rule": "delta", "target": 0.2}
    assert put("pct", 2) == {"rule": "spot", "pct": -2, "match": "nearest"}
    assert put("sd", 1) == {"rule": "std_dev", "sd": -1, "match": "nearest"}
    assert put("points", 10) == {"rule": "spot", "points": -10, "match": "nearest"}
    assert (
        strategies.build("option", {"place_by": "sd"})["legs"][0]["strike"]["sd"] == -1
    )  # its own default target


def test_the_expiry_is_the_nearest_listed_at_least_that_far_out():
    assert strategies.build("vertical", {"dte": 0})["expiry"] == {"kind": "dte", "days": 0}
    assert strategies.build("vertical", {"dte": 30, "expiries": "monthly"})["expiry"] == {
        "kind": "at_least",
        "days": 30,
        "monthly": True,
    }
    assert strategies.build("vertical", {"dte": 7, "expiries": "weekly"})["expiry"] == {
        "kind": "at_least",
        "days": 7,
    }


@pytest.mark.parametrize(
    "name, given, says",
    [
        ("nothing", {}, "strategy: one of"),
        ("calendar", {"target": -1}, "target: 0 or more"),
        ("diagonal", {"far_days": 0}, "far_days: 1 to"),
        ("covered", {}, "needs stock legs"),
        ("vertical", {"width": 0}, "width: more than 0"),
        ("vertical", {"right": "both"}, "right: one of put, call"),
        ("ratio", {"ratio": 1}, "ratio: 2 to"),
        ("option", {"dte": 1.5}, "dte: a whole number"),
    ],
)
def test_a_setting_it_cannot_build_from_is_refused_by_name(name, given, says):
    with pytest.raises(strategies.Refused, match=says):
        strategies.build(name, given)


def test_the_wizard_gets_a_whole_automation_with_the_exits_its_expiry_allows():
    out = builderform.strategy_answer("iron_condor", {"dte": 45})
    assert out["problems"] == [] and out["credit"] is True
    assert out["spec"]["exit"] == builderform.CREDIT_RULES
    assert config.load_arms({"automations": {"w": out["spec"]}})["w"]["legs"]
    near = builderform.strategy_answer("iron_condor", {"dte": 7})["spec"]["exit"]
    assert "close_at_dte" not in near and near["profit_target_pct"] == 50
    debit = builderform.strategy_answer("butterfly", {"dte": 7})
    assert debit["credit"] is False and debit["spec"]["exit"] == {}
    cal = builderform.strategy_answer("calendar", {"dte": 30})
    assert cal["problems"] == [] and cal["credit"] is False
    assert cal["spec"]["exit"] == {"close_before_expiry_min": strategies.CLOSE_BEFORE_NEAR_MIN}
    assert cal["spec"]["far_expiry"] == {"kind": "after_near", "days": 28, "monthly": True}
    assert config.load_arms({"automations": {"c": cal["spec"]}})["c"]["far_expiry"]["days"] == 28


def test_the_schema_offers_every_strategy_and_says_why_one_cannot_be_built():
    s = builderform.schema()["strategy"]
    assert set(s["strategies"]) == set(strategies.STRATEGIES)
    assert s["strategies"]["diagonal"]["available"] is True
    assert s["strategies"]["covered"]["available"] is False and "stock" in s["strategies"]["covered"]["why"]
    assert s["strategies"]["custom"]["available"] is True


def test_a_calendar_sells_near_and_buys_far_at_the_same_strike_and_a_diagonal_further_out():
    cal = strategies.build("calendar", {"right": "call"})
    assert [(g["action"], g["side"], g.get("expiry")) for g in cal["legs"]] == [
        ("STO", "call", None),
        ("BTO", "call", "far"),
    ]
    assert cal["legs"][0]["strike"] == {"rule": "spot", "points": 0, "match": "nearest"}
    assert cal["legs"][1]["strike"] == {"rule": "same_as", "leg": "L1"}
    put = strategies.build("diagonal", {"right": "put", "width": 5})["legs"][1]["strike"]
    assert put == {"rule": "from_leg", "leg": "L1", "points": -5, "match": "nearest"}
    weekly = strategies.build("calendar", {"expiries": "weekly", "far_days": 7})["far_expiry"]
    assert weekly == {"kind": "after_near", "days": 7}
    # Its own target stands until the placing changes; then the placing's own.
    assert strategies.build("calendar")["settings"]["target"] == 0
    assert strategies.build("calendar", {"place_by": "delta"})["settings"]["target"] == 0.16
