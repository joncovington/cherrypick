"""Unit tests for the risk-profile system: config.risk.json and profile switching."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# The canonical four-tier risk ladder. config.risk.json may additionally carry
# symbol/wing/credit experiment cells (small-/medium-/large-/explore-) for the paper
# account-size study — those are partial overlays merged onto config.json, not full presets.
LADDER = {"conservative", "moderate", "aggressive", "very-aggressive"}
EXPERIMENT_PREFIXES = {"small", "medium", "large", "explore"}


@pytest.fixture
def sample_risk_profiles():
    """Load the actual config.risk.json from the repo."""
    path = Path(__file__).parent.parent / "config.risk.json"
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def sample_config():
    """Load the actual config.json from the repo."""
    path = Path(__file__).parent.parent / "config.json"
    with open(path) as f:
        return json.load(f)


def test_config_risk_json_valid_structure(sample_risk_profiles):
    """Verify config.risk.json has required top-level keys."""
    assert "_description" in sample_risk_profiles
    assert "active_profile" in sample_risk_profiles
    assert "profiles" in sample_risk_profiles
    assert isinstance(sample_risk_profiles["profiles"], dict)


def test_config_risk_json_has_ladder_profiles(sample_risk_profiles):
    """The four-tier ladder must always be present. Any additional profiles must be recognized
    experiment/exploratory cells (small-/medium-/large-/explore-) for the paper account-size study."""
    names = set(sample_risk_profiles["profiles"].keys())
    assert LADDER <= names
    for extra in names - LADDER:
        assert extra.split("-")[0] in EXPERIMENT_PREFIXES, f"unexpected non-ladder profile {extra!r}"


def test_conservative_profile_matches_config_defaults(sample_risk_profiles, sample_config):
    """Verify conservative profile values match the actual config.json defaults."""
    conservative = sample_risk_profiles["profiles"]["conservative"]

    # Remove the _note key for comparison
    conservative_gates = {k: v for k, v in conservative.items() if not k.startswith("_")}

    # Check each gate value matches config.json
    for gate, value in conservative_gates.items():
        assert sample_config.get(gate) == value, f"Gate {gate}: config.json={sample_config.get(gate)}, conservative={value}"


def test_moderate_profile_relaxes_gates_appropriately(sample_risk_profiles):
    """Verify moderate profile relaxes gates in the expected direction."""
    conservative = sample_risk_profiles["profiles"]["conservative"]
    moderate = sample_risk_profiles["profiles"]["moderate"]

    # IV rank should be lower (more relaxed)
    assert moderate["min_iv_rank"] < conservative["min_iv_rank"]
    assert moderate["min_iv_rank"] == 0.22

    # Credit floor should be lower (more relaxed)
    assert moderate["min_credit_pct_of_width"] < conservative["min_credit_pct_of_width"]
    assert moderate["min_credit_pct_of_width"] == 0.12

    # Late-entry bias start time should be earlier (more entries earlier in day)
    assert moderate["late_entry_bias_start_time"] < conservative["late_entry_bias_start_time"]
    assert moderate["late_entry_bias_start_time"] == "11:00"

    # Stop should be tighter (offset)
    assert moderate["stop_trigger_ratio"] < conservative["stop_trigger_ratio"]
    assert moderate["stop_trigger_ratio"] == 0.93

    # Trade COUNT is deliberately not a ladder axis — the ladder's axis is riskier trades, not more
    # of them — so the daily target is flat across rungs (and <= max_concurrent_ics everywhere).
    assert moderate["daily_ic_trade_target"] == conservative["daily_ic_trade_target"] == 2


def test_aggressive_profile_relaxes_additional_gates(sample_risk_profiles):
    """Verify aggressive profile adds delta/OTM relaxation with position-size offsets."""
    moderate = sample_risk_profiles["profiles"]["moderate"]
    aggressive = sample_risk_profiles["profiles"]["aggressive"]

    # IV and credit floors should be lower (more relaxed)
    assert aggressive["min_iv_rank"] < moderate["min_iv_rank"]
    assert aggressive["min_iv_rank"] == 0.20

    assert aggressive["min_credit_pct_of_width"] < moderate["min_credit_pct_of_width"]
    assert aggressive["min_credit_pct_of_width"] == 0.10

    # Delta should be higher (closer to money, more relaxed)
    assert aggressive["max_call_delta_entry"] > moderate["max_call_delta_entry"]
    assert aggressive["max_call_delta_entry"] == 0.22

    # OTM distances should be smaller (closer to money, more relaxed)
    assert aggressive["min_call_otm_pct"] < moderate["min_call_otm_pct"]
    assert aggressive["min_put_otm_pct"] < moderate["min_put_otm_pct"]

    # Position cap should be lower (offset)
    assert aggressive["max_concurrent_ics"] < moderate["max_concurrent_ics"]
    assert aggressive["max_concurrent_ics"] == 3

    # Stop should be tighter (offset)
    assert aggressive["stop_trigger_ratio"] < moderate["stop_trigger_ratio"]
    assert aggressive["stop_trigger_ratio"] == 0.90

    # Count is not a ladder axis (see moderate test) — flat target, tightened by the position cap.
    assert aggressive["daily_ic_trade_target"] == moderate["daily_ic_trade_target"] == 2


def test_very_aggressive_profile_relaxes_regime_gates(sample_risk_profiles):
    """Verify very-aggressive profile relaxes regime gates (VIX/ATR) with extreme offsets."""
    aggressive = sample_risk_profiles["profiles"]["aggressive"]
    very_aggressive = sample_risk_profiles["profiles"]["very-aggressive"]

    # All Tier 2 relaxations should be present or tighter
    assert very_aggressive["min_iv_rank"] <= aggressive["min_iv_rank"]
    assert very_aggressive["max_call_delta_entry"] >= aggressive["max_call_delta_entry"]

    # Regime gates should be relaxed (thresholds raised to allow more trading)
    assert very_aggressive["regime_vix_pause_threshold"] > aggressive["regime_vix_pause_threshold"]
    assert very_aggressive["regime_vix_pause_threshold"] == 30

    assert very_aggressive["regime_atr_pause_threshold_pct"] > aggressive["regime_atr_pause_threshold_pct"]
    assert very_aggressive["regime_atr_pause_threshold_pct"] == 0.020

    # The VIX1D event-day gate relaxes at the top rung too, matching VIX/ATR (it used to be the one
    # regime gate pinned flat across all four tiers).
    assert very_aggressive["regime_vix1d_ratio_pause_threshold"] > aggressive["regime_vix1d_ratio_pause_threshold"]
    assert very_aggressive["regime_vix1d_ratio_pause_threshold"] == 1.40

    # Offsets should be extreme
    assert very_aggressive["max_concurrent_ics"] == 2  # Smallest position cap
    assert very_aggressive["stop_trigger_ratio"] == 0.85  # Tightest stop

    # Count is not a ladder axis — flat target, held at/below the position cap.
    assert very_aggressive["daily_ic_trade_target"] == 2


def test_ladder_profiles_have_required_gate_keys(sample_risk_profiles):
    """The ladder profiles are complete presets — every required gate key present. (Experiment
    profiles are partial overlays merged onto config.json, validated separately below.)"""
    # NB: the low-IV relief ceiling/floor and the late-entry-bias ceiling are deliberately NOT here.
    # They are no longer per-tier absolutes; they derive from each tier's own min_iv_rank and credit
    # floor via the relative keys in config.json (low_iv_credit_floor_iv_rank_offset,
    # low_iv_credit_relief_multiple, late_entry_bias_iv_rank_offset), so they scale with the ladder
    # instead of every tier repeating conservative's numbers.
    required_keys = {
        "min_iv_rank",
        "min_credit_pct_of_width",
        "max_call_delta_entry",
        "max_call_delta_entry_open_volatile",
        "max_call_delta_entry_late",
        "min_call_otm_pct",
        "min_put_otm_pct",
        "late_entry_bias_enabled",
        "late_entry_bias_start_time",
        "regime_vix_pause_threshold",
        "regime_atr_pause_threshold_pct",
        "regime_vix1d_ratio_pause_threshold",
        "max_concurrent_ics",
        "stop_trigger_ratio",
        "daily_ic_trade_target",
    }

    for profile_name in LADDER:
        profile = sample_risk_profiles["profiles"][profile_name]
        profile_gates = {k: v for k, v in profile.items() if not k.startswith("_")}
        for key in required_keys:
            assert key in profile_gates, f"Profile {profile_name} missing required key: {key}"


def test_ladder_derived_thresholds_scale_with_each_tier():
    """The low-IV relief ceiling/floor and late-entry-bias ceiling must DERIVE from each tier, not
    repeat one absolute. Regression guard: they were flat (0.35 / 0.10 / 0.45) across all four
    tiers, which silently flattened the ladder — whenever iv_rank sat under 0.35, every tier used
    the same 0.10 credit floor (100% of SPX/XSP entries), so conservative traded them on identical
    terms to very-aggressive."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    import paper

    base, profiles = paper.load_base_config(), paper.load_profiles()
    prev = None
    for tier in ["conservative", "moderate", "aggressive", "very-aggressive"]:
        p = paper._merged_params(base, profiles[tier])
        relief_max, relief_floor = paper._low_iv_relief_max(p), paper._low_iv_relief_floor(p)
        # Relief must sit strictly below the tier's own credit floor, or it does nothing at all
        # (which is exactly what happened to aggressive/very-aggressive at a flat 0.10).
        assert relief_floor < p["min_credit_pct_of_width"], tier
        # Ceilings track the tier's own IV floor rather than a shared absolute.
        assert relief_max == pytest.approx(p["min_iv_rank"] + 0.05), tier
        assert paper._late_entry_bias_max(p) == pytest.approx(p["min_iv_rank"] + 0.15), tier
        # Count is not a ladder axis: flat target, never above the hard position cap.
        assert p["daily_ic_trade_target"] <= p["max_concurrent_ics"], tier
        if prev is not None:
            assert relief_max < prev, "relief ceiling must loosen down the ladder"
        prev = relief_max


def test_ladder_credit_floor_is_monotonic_at_every_iv_level():
    """The effective credit floor must never invert down the ladder — a stricter tier must never
    accept thinner credit than a looser one. Regression guard for two failure modes: the relief
    being a flat absolute (which collapsed all four tiers onto 0.10), and a relief multiple strong
    enough that a tier inside its borderline band undercut the next tier's plain floor."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    import paper

    base, profiles = paper.load_base_config(), paper.load_profiles()
    tiers = ["conservative", "moderate", "aggressive", "very-aggressive"]
    merged = {t: paper._merged_params(base, profiles[t]) for t in tiers}
    for iv_rank in (0.16, 0.21, 0.24, 0.28, 0.32, 0.40, 0.60):
        floors = []
        for t in tiers:
            p = merged[t]
            if iv_rank < p["min_iv_rank"]:
                continue  # tier can't trade here at all
            floors.append((t, paper._low_iv_relief_floor(p)
                           if iv_rank <= paper._low_iv_relief_max(p)
                           else p["min_credit_pct_of_width"]))
        for (t_strict, f_strict), (t_loose, f_loose) in zip(floors, floors[1:]):
            assert f_strict >= f_loose, (
                f"iv_rank {iv_rank}: {t_strict} floor {f_strict:.3f} < {t_loose} {f_loose:.3f}")


def test_experiment_profiles_pin_symbol_and_wings(sample_risk_profiles):
    """Experiment cells must pin the axes they vary: a symbol subset, per-symbol wings for each
    declared symbol, and — if they stagger — a daily target + spacing to spread entries."""
    profiles = sample_risk_profiles["profiles"]
    for name in set(profiles) - LADDER:
        p = profiles[name]
        assert isinstance(p.get("symbols"), list) and p["symbols"], f"{name} must pin `symbols`"
        wbs = p.get("wing_widths_by_symbol")
        assert isinstance(wbs, dict) and wbs, f"{name} must set `wing_widths_by_symbol`"
        for sym in p["symbols"]:
            assert sym in wbs and wbs[sym], f"{name} missing wings for {sym}"
        if p.get("stagger_entries"):
            assert "daily_ic_trade_target" in p and "min_minutes_between_entries" in p, \
                f"{name} staggers but lacks daily target / spacing"


def test_all_profiles_have_description_note(sample_risk_profiles):
    """Verify every profile has a _note field explaining its purpose."""
    for profile_name, profile in sample_risk_profiles["profiles"].items():
        assert "_note" in profile, f"Profile {profile_name} missing _note field"
        assert isinstance(profile["_note"], str)
        assert len(profile["_note"]) > 20, f"Profile {profile_name} _note is too short"


def test_profile_gate_values_are_valid_types(sample_risk_profiles):
    """Verify gate values in profiles are the correct types."""
    for profile_name, profile in sample_risk_profiles["profiles"].items():
        # Float gates
        float_gates = [
            "min_iv_rank", "min_credit_pct_of_width", "low_iv_min_credit_pct_of_width",
            "low_iv_credit_floor_iv_rank_max", "max_call_delta_entry",
            "max_call_delta_entry_open_volatile", "max_call_delta_entry_late",
            "min_call_otm_pct", "min_put_otm_pct", "late_entry_bias_iv_rank_max",
            "regime_vix1d_ratio_pause_threshold", "stop_trigger_ratio",
            "regime_atr_pause_threshold_pct"
        ]

        for gate in float_gates:
            if gate in profile:
                assert isinstance(profile[gate], (int, float)), f"{profile_name}.{gate} should be numeric"

        # Int gates
        int_gates = ["regime_vix_pause_threshold", "max_concurrent_ics", "daily_ic_trade_target"]
        for gate in int_gates:
            if gate in profile:
                assert isinstance(profile[gate], int), f"{profile_name}.{gate} should be int"

        # Boolean gates
        bool_gates = ["late_entry_bias_enabled"]
        for gate in bool_gates:
            if gate in profile:
                assert isinstance(profile[gate], bool), f"{profile_name}.{gate} should be bool"

        # String gates
        str_gates = ["late_entry_bias_start_time"]
        for gate in str_gates:
            if gate in profile:
                assert isinstance(profile[gate], str), f"{profile_name}.{gate} should be str"


def test_profile_gate_values_in_reasonable_ranges(sample_risk_profiles):
    """Verify gate values are in reasonable ranges (sanity check)."""
    for profile in sample_risk_profiles["profiles"].values():
        # IV rank should be 0.0-1.0
        if "min_iv_rank" in profile:
            assert 0.0 <= profile["min_iv_rank"] <= 1.0

        # Credit pct should be 0.0-1.0
        if "min_credit_pct_of_width" in profile:
            assert 0.0 <= profile["min_credit_pct_of_width"] <= 1.0

        # Delta should be 0.0-1.0
        if "max_call_delta_entry" in profile:
            assert 0.0 <= profile["max_call_delta_entry"] <= 1.0

        # OTM pct should be 0.0-1.0
        if "min_call_otm_pct" in profile:
            assert 0.0 <= profile["min_call_otm_pct"] <= 1.0

        # Stop ratio should be 0.5-1.5 (between breakeven and max loss)
        if "stop_trigger_ratio" in profile:
            assert 0.5 <= profile["stop_trigger_ratio"] <= 1.5

        # VIX pause threshold should be positive
        if "regime_vix_pause_threshold" in profile:
            assert profile["regime_vix_pause_threshold"] > 0

        # Max concurrent ICs should be 1-10
        if "max_concurrent_ics" in profile:
            assert 1 <= profile["max_concurrent_ics"] <= 10

        # Daily target should be 0+ (0 = ORB only)
        if "daily_ic_trade_target" in profile:
            assert profile["daily_ic_trade_target"] >= 0


def test_profile_progression_is_monotonic(sample_risk_profiles):
    """Verify the tier progression (conservative → moderate → aggressive → very-aggressive) is consistently more relaxed."""
    profiles_ordered = [
        sample_risk_profiles["profiles"]["conservative"],
        sample_risk_profiles["profiles"]["moderate"],
        sample_risk_profiles["profiles"]["aggressive"],
        sample_risk_profiles["profiles"]["very-aggressive"],
    ]

    # IV rank should monotonically decrease (more relaxed)
    iv_ranks = [p["min_iv_rank"] for p in profiles_ordered]
    assert iv_ranks == sorted(iv_ranks, reverse=True), "IV rank should decrease (more relaxed) across tiers"

    # Credit floor should monotonically decrease (more relaxed)
    credit_floors = [p["min_credit_pct_of_width"] for p in profiles_ordered]
    assert credit_floors == sorted(credit_floors, reverse=True), "Credit floor should decrease across tiers"

    # Daily IC target should monotonically increase (more entries)
    daily_targets = [p["daily_ic_trade_target"] for p in profiles_ordered]
    assert daily_targets == sorted(daily_targets), "Daily IC target should increase across tiers"


def test_config_json_stale_values_fixed(sample_config):
    """Verify that stale values in CLAUDE.md documentation have been fixed in config.json."""
    # These were the stale values reported in the plan
    assert sample_config["min_credit_pct_of_width"] == 0.15, "min_credit_pct_of_width should be 0.15 (not 0.20 as docs said)"
    assert sample_config["max_concurrent_ics"] == 4, "max_concurrent_ics should be 4 (not 2 as docs said)"
