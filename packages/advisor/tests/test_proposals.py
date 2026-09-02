"""Reading a model's reply: tolerant about where the JSON is, strict about what it says."""

from __future__ import annotations

import json

import pytest

from cherrypick.advisor import proposals

CLEAN = {
    "observations": ["control took no stops all session"],
    "flags": [{"module": "flies", "severity": "warn", "text": "completion rate halved"}],
    "proposals": [
        {
            "kind": "bounded_adjustment",
            "module": "meic",
            "params": [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "wider"}],
            "sessions": 15,
            "hypothesis": "fewer stops on trend days",
        },
    ],
}


def test_json_is_found_inside_prose_and_code_fences():
    for wrapper in (
        "Here is my analysis:\n```json\n{body}\n```\nHope that helps.",
        "{body}",
        "Sure!\n\n{body}",
    ):
        raw = wrapper.replace("{body}", json.dumps(CLEAN))
        parsed = proposals.parse(raw)
        assert parsed["observations"] == CLEAN["observations"]
        assert parsed["proposals"][0]["params"] == {"stop_trigger_ratio": 0.9}


def test_rationales_survive_the_normalisation():
    parsed = proposals.parse(json.dumps(CLEAN))
    assert parsed["proposals"][0]["rationales"] == {"stop_trigger_ratio": "wider"}


def test_a_reply_with_no_json_is_a_parse_error_not_a_guess():
    for raw in ("", "   ", "I could not analyse this today.", "{not json at all"):
        with pytest.raises(proposals.ParseError):
            proposals.parse(raw)


def test_both_param_shapes_are_accepted():
    """The prompt asks for a list of {param, value}; models routinely send a plain map. Accepting
    both costs nothing and refusing one produces a rejection that teaches the model nothing."""
    as_map = {
        "proposals": [{"kind": "tune", "experiment_id": "exp-1", "params": {"stop_trigger_ratio": 0.92}}]
    }
    assert proposals.parse(json.dumps(as_map))["proposals"][0]["params"] == {"stop_trigger_ratio": 0.92}


def test_one_bare_param_entry_is_not_read_as_a_map_of_its_own_field_names():
    """A single {param, value} entry sent without the enclosing list. Read as a map it would become
    three params called `param`, `value` and `rationale`, and the bounds check would refuse it with
    `param 'param' not in advice_bounds` — a reason that describes nothing the model did wrong."""
    bare = {
        "proposals": [
            {
                "kind": "tune",
                "experiment_id": "exp-1",
                "params": {"param": "stop_trigger_ratio", "value": 1.2, "rationale": "one lever only"},
            }
        ]
    }
    parsed = proposals.parse(json.dumps(bare))["proposals"][0]
    assert parsed["params"] == {"stop_trigger_ratio": 1.2}
    assert parsed["rationales"] == {"stop_trigger_ratio": "one lever only"}


def test_a_map_whose_keys_merely_resemble_an_entry_is_still_a_map():
    """The disambiguation keys off a STRING `param`, so a module that really did declare bounds on
    keys named `value` or `rationale` keeps the map reading."""
    as_map = {
        "proposals": [{"kind": "tune", "experiment_id": "exp-1", "params": {"value": 3, "rationale": 4}}]
    }
    assert proposals.parse(json.dumps(as_map))["proposals"][0]["params"] == {"value": 3, "rationale": 4}


def test_malformed_proposals_are_kept_with_their_reason():
    """Never silently dropped: a rejection the model sees next session is worth more than a clean
    checkpoint."""
    payload = {
        "proposals": [
            {"kind": "teleport", "module": "meic"},
            {"kind": "experiment_spec", "module": "flies"},  # no name, no params
            {"kind": "verdict", "experiment_id": "exp-1", "recommendation": "maybe"},
            "not even an object",
            {"kind": "tune", "experiment_id": "exp-1", "params": ["oops"]},
        ]
    }
    parsed = proposals.parse(json.dumps(payload))
    assert parsed["proposals"] == []
    reasons = [m["reason"] for m in parsed["malformed"]]
    assert reasons[0].startswith("unknown_kind")
    assert "missing required field(s)" in reasons[1]
    assert "recommendation must be one of" in reasons[2]
    assert reasons[3] == "proposal is not an object"
    assert "params entries must be objects" in reasons[4]


def test_duplicate_params_are_refused_before_they_reach_the_validator():
    payload = {
        "proposals": [
            {
                "kind": "bounded_adjustment",
                "module": "meic",
                "params": [
                    {"param": "stop_trigger_ratio", "value": 0.9},
                    {"param": "stop_trigger_ratio", "value": 0.95},
                ],
            }
        ]
    }
    assert "duplicate param" in proposals.parse(json.dumps(payload))["malformed"][0]["reason"]


def test_flags_without_text_are_dropped_and_defaults_fill_in():
    payload = {"flags": [{"text": "vix spiked"}, {"module": "meic"}]}
    flags = proposals.parse(json.dumps(payload))["flags"]
    assert flags == [{"module": "suite", "severity": "info", "text": "vix spiked"}]


def test_creative_keeps_its_spec_verbatim():
    """A creative idea is only actionable if it arrives ready to paste, and nothing here has to
    understand a module's config shape to keep it."""
    spec = {"name": "width-15", "wing_width": 15}
    payload = {
        "proposals": [
            {
                "kind": "creative",
                "module": "meic",
                "title": "a wider wing arm",
                "text": "…",
                "spec_json": spec,
            }
        ]
    }
    assert proposals.parse(json.dumps(payload))["proposals"][0]["spec_json"] == spec
