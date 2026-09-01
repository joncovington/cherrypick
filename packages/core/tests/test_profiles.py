"""Tests for cherrypick.core.profiles — dual-source registry + the generalized merge engine."""

import json

import pytest

from cherrypick.core import profiles


# --------------------------------------------------------------------------- load_profiles
def test_load_profiles_inline():
    cfg = {"profiles": {"a": {"x": 1}, "b": {"x": 2}}, "other": 9}
    assert profiles.load_profiles(cfg) == {"a": {"x": 1}, "b": {"x": 2}}


def test_load_profiles_inline_missing_returns_empty():
    assert profiles.load_profiles({}) == {}
    assert profiles.load_profiles(None) == {}


def test_load_profiles_external_file(tmp_path):
    f = tmp_path / "config.risk.json"
    f.write_text(json.dumps({"active_profile": "a", "profiles": {"a": {"x": 1}}}))
    assert profiles.load_profiles(external_path=f) == {"a": {"x": 1}}


# --------------------------------------------------------------------------- select_profile
def test_select_profile_returns_copy():
    profs = {"a": {"x": 1}}
    got = profiles.select_profile(profs, "a")
    assert got == {"x": 1}
    got["x"] = 99
    assert profs["a"]["x"] == 1  # returned a copy, source unmutated


def test_select_profile_unknown_raises():
    with pytest.raises(ValueError, match="unknown profile 'z'"):
        profiles.select_profile({"a": {}}, "z")


# --------------------------------------------------------------------------- attribution_tag
def test_attribution_tag_returns_name_when_set():
    assert profiles.attribution_tag("aggressive") == "aggressive"


def test_attribution_tag_none_uses_default_sentinel():
    assert profiles.attribution_tag(None) == "unassigned"
    assert profiles.attribution_tag(None) == profiles.UNTAGGED


def test_attribution_tag_empty_and_whitespace_treated_as_untagged():
    assert profiles.attribution_tag("") == "unassigned"
    assert profiles.attribution_tag("   ") == "unassigned"


def test_attribution_tag_strips_surrounding_whitespace():
    assert profiles.attribution_tag("  balanced ") == "balanced"


def test_attribution_tag_custom_untagged_sentinel():
    # EarningsAgent's schema stores a non-null "default" sentinel, not NULL.
    assert profiles.attribution_tag(None, untagged="default") == "default"
    assert profiles.attribution_tag("balanced", untagged="default") == "balanced"


# --------------------------------------------------------------------------- compare_profiles
def _count(group):
    return {"n": len(group), "pnl": sum(r["pnl"] for r in group)}


def test_compare_profiles_groups_by_tag_and_summarizes():
    rows = [
        {"risk_profile": "aggressive", "pnl": 10},
        {"risk_profile": "conservative", "pnl": -5},
        {"risk_profile": "aggressive", "pnl": 3},
    ]
    table = profiles.compare_profiles(rows, tag_key="risk_profile", summarize=_count)
    assert table == {
        "aggressive": {"n": 2, "pnl": 13},
        "conservative": {"n": 1, "pnl": -5},
    }


def test_compare_profiles_summarize_called_once_per_group():
    calls = []

    def summarize(group):
        calls.append(len(group))
        return len(group)

    rows = [{"p": "a"}, {"p": "a"}, {"p": "b"}]
    profiles.compare_profiles(rows, tag_key="p", summarize=summarize)
    assert sorted(calls) == [1, 2]  # one call per group, never on the whole set


def test_compare_profiles_untagged_rows_group_under_sentinel():
    rows = [{"risk_profile": None, "pnl": 1}, {"risk_profile": "aggressive", "pnl": 2}]
    table = profiles.compare_profiles(rows, tag_key="risk_profile", summarize=_count)
    assert table["unassigned"] == {"n": 1, "pnl": 1}
    assert table["aggressive"] == {"n": 1, "pnl": 2}


def test_compare_profiles_custom_untagged_sentinel():
    rows = [{"profile": None, "pnl": 1}]
    table = profiles.compare_profiles(rows, tag_key="profile", summarize=_count, untagged="default")
    assert set(table) == {"default"}


def test_compare_profiles_preserves_first_seen_order():
    rows = [{"p": "z"}, {"p": "a"}, {"p": "z"}]
    table = profiles.compare_profiles(rows, tag_key="p", summarize=len)
    assert list(table) == ["z", "a"]  # first-seen order, not sorted


def test_compare_profiles_empty_rows():
    assert profiles.compare_profiles([], tag_key="risk_profile", summarize=_count) == {}


_GOOD = {"sample": 40, "win_rate": 0.65, "days": 20, "net_pnl": 500.0}
_THIN = {"sample": 3, "win_rate": 0.9, "days": 2, "net_pnl": 50.0}


# --------------------------------------------------------------------------- qualify_readings
def test_qualify_readings_returns_per_tag_qualification_only():
    # The direct regression test for the bug this replaces: a fully-qualifying reading must NOT
    # produce anything resembling a promotion -- the shape has nowhere to put one.
    out = profiles.qualify_readings({"control": _GOOD, "time_window": _THIN})
    assert out == {
        "control": {
            "qualified": True,
            "checks": {
                "sample": {"value": 40, "threshold": 20, "pass": True},
                "win_rate": {"value": 0.65, "threshold": 0.60, "pass": True},
                "days": {"value": 20, "threshold": 14, "pass": True},
            },
        },
        "time_window": {
            "qualified": False,
            "checks": {
                "sample": {"value": 3, "threshold": 20, "pass": False},
                "win_rate": {"value": 0.9, "threshold": 0.60, "pass": True},
                "days": {"value": 2, "threshold": 14, "pass": False},
            },
        },
    }
    for entry in out.values():
        assert "recommendation" not in entry
        assert "eligible" not in entry
        assert "beats_champion" not in entry


def test_qualify_readings_rule_override_applies_per_tag():
    out = profiles.qualify_readings({"control": _GOOD}, rule={"min_win_rate": 0.70})
    assert out["control"]["checks"]["win_rate"]["threshold"] == 0.70
    assert out["control"]["checks"]["win_rate"]["pass"] is False
    assert out["control"]["qualified"] is False


def test_qualify_readings_empty_readings_returns_empty_dict():
    assert profiles.qualify_readings({}) == {}


# --------------------------------------------------------------------------- find_identical_readings
def _full_reading(**over):
    base = {
        "sample": 13,
        "win_rate": 0.1538,
        "days": 1,
        "net_pnl": 5.63,
        "sharpe": 0.008,
        "max_drawdown": 169.98,
    }
    base.update(over)
    return base


def test_find_identical_readings_groups_byte_identical_arms():
    # Found live 2026-08-14: gex-open/gex-blocked read identical in every field despite naming
    # opposite gate conditions.
    readings = {
        "gex-open": _full_reading(),
        "gex-blocked": _full_reading(),
        "distinct-arm": _full_reading(net_pnl=99.0),
    }
    collisions = profiles.find_identical_readings(readings)
    assert collisions == [
        {
            "tags": ["gex-open", "gex-blocked"],
            "fields": {
                "sample": 13,
                "win_rate": 0.1538,
                "days": 1,
                "net_pnl": 5.63,
                "sharpe": 0.008,
                "max_drawdown": 169.98,
            },
        }
    ]


def test_find_identical_readings_no_collision_is_empty_list():
    readings = {"a": _full_reading(net_pnl=1.0), "b": _full_reading(net_pnl=2.0)}
    assert profiles.find_identical_readings(readings) == []


def test_find_identical_readings_ignores_readings_with_any_unmeasured_field():
    """Two zero-sample arms both read win_rate/sharpe as None -- an unmeasured field can't
    certify two readings are the same, so this must never report a false collision."""
    empty = {"sample": 0, "win_rate": None, "days": 0, "net_pnl": 0.0, "sharpe": None, "max_drawdown": 0.0}
    readings = {"a": empty, "b": dict(empty)}
    assert profiles.find_identical_readings(readings) == []


def test_find_identical_readings_skips_empty_or_missing_readings():
    readings = {"a": _full_reading(), "b": {}, "c": None}
    assert profiles.find_identical_readings(readings) == []


def test_find_identical_readings_supports_three_way_collisions():
    readings = {"a": _full_reading(), "b": _full_reading(), "c": _full_reading()}
    collisions = profiles.find_identical_readings(readings)
    assert len(collisions) == 1
    assert collisions[0]["tags"] == ["a", "b", "c"]


def test_find_identical_readings_custom_fields():
    readings = {"a": {"net_pnl": 5.0, "days": 9}, "b": {"net_pnl": 5.0, "days": 1}}
    assert profiles.find_identical_readings(readings, fields=("net_pnl",)) == [
        {"tags": ["a", "b"], "fields": {"net_pnl": 5.0}}
    ]


# --------------------------------------------------------------------------- merge_profile (flat / MEIC)
def test_merge_profile_flat_override_skips_underscore_and_leaves_base():
    base = {"min_iv_rank": 0.3, "max_ics": 4, "force_close_time": "15:45"}
    profile = {"min_iv_rank": 0.2, "max_ics": 6, "_note": "comment"}
    merged = profiles.merge_profile(base, profile)
    assert merged == {"min_iv_rank": 0.2, "max_ics": 6, "force_close_time": "15:45"}
    assert "_note" not in merged  # underscore comment skipped
    assert base["min_iv_rank"] == 0.3  # base not mutated


# --------------------------------------------------------------------------- merge_profile (Earnings shape)
def test_merge_profile_reserved_keys_skipped():
    base = {"risk_pct_multiplier": 1.0, "tier_floor": "Tier 2"}
    profile = {"description": "balanced book", "risk_pct_multiplier": 0.6, "tier_floor": "Tier 1"}
    merged = profiles.merge_profile(base, profile, reserved_keys=("description",))
    assert merged == {"risk_pct_multiplier": 0.6, "tier_floor": "Tier 1"}
    assert "description" not in merged


def test_merge_profile_nested_namespace_deep_merges_and_skips_unknown():
    base = {
        "risk_pct_multiplier": 1.0,
        "strategies": {"iron_fly": {"a": 1, "b": 2}, "double_calendar": {"a": 1}},
    }
    profile = {
        "risk_pct_multiplier": 0.6,
        "strategy_overrides": {
            "iron_fly": {"b": 99, "c": 3},  # merges over existing strategy
            "ghost_strategy": {"z": 1},  # not in base -> skipped
        },
    }
    merged = profiles.merge_profile(
        base,
        profile,
        reserved_keys=("description",),
        nested_namespaces={"strategy_overrides": "strategies"},
    )
    assert merged["risk_pct_multiplier"] == 0.6
    assert merged["strategies"]["iron_fly"] == {"a": 1, "b": 99, "c": 3}
    assert merged["strategies"]["double_calendar"] == {"a": 1}  # untouched
    assert "ghost_strategy" not in merged["strategies"]
    # base untouched
    assert base["strategies"]["iron_fly"] == {"a": 1, "b": 2}
    assert base["risk_pct_multiplier"] == 1.0


def test_merge_profile_namespace_key_not_treated_as_top_level_override():
    base = {"strategies": {"x": {"a": 1}}}
    profile = {"strategy_overrides": {"x": {"a": 2}}}
    merged = profiles.merge_profile(base, profile, nested_namespaces={"strategy_overrides": "strategies"})
    assert "strategy_overrides" not in merged  # consumed as a namespace, not copied
    assert merged["strategies"]["x"] == {"a": 2}


# --------------------------------------------------------------------------- merge_profile (validation)
def test_merge_profile_validate_rejects_unknown_key():
    base = {"known": 1}
    with pytest.raises(KeyError, match="typo_key"):
        profiles.merge_profile(base, {"typo_key": 5}, validate=True)


def test_merge_profile_validate_exempts_reserved_underscore_and_namespaces():
    base = {"known": 1, "strategies": {"x": {"a": 1}}}
    profile = {
        "known": 2,
        "_note": "c",
        "description": "d",
        "strategy_overrides": {"x": {"a": 9}},
    }
    merged = profiles.merge_profile(
        base,
        profile,
        reserved_keys=("description",),
        nested_namespaces={"strategy_overrides": "strategies"},
        validate=True,
    )
    assert merged["known"] == 2  # validated + applied
    assert merged["strategies"]["x"] == {"a": 9}  # namespace merged, not validated as top-level
