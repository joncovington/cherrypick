"""Unit tests for the risk-profile / arm-stream system: config.risk.json and profile switching."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# The registry after the 2026-08-07 arms cutover: the four active streams (control/open/width-5/
# width-10) plus every retired/disabled profile kept for historical record (the four-tier ladder,
# and the GEX study pair). See config.risk.json's _arms_cutover_note for the full rationale.
ACTIVE_STREAMS = {"control", "open", "width-5", "width-10"}
LADDER = {"conservative", "moderate", "aggressive", "very-aggressive"}
RETIRED_STUDY_ARMS = {"gex-open", "gex-blocked"}
UNCAPPED_SAMPLING_STREAMS = {"open", "width-5", "width-10"}
EXPERIMENT_PREFIXES = {"small", "medium", "large", "explore", "width", "gex"}
STUDY_ARM_PREFIXES = ("width-", "gex-")
# Keys that describe the registry entry itself (documentation, enable switch), never a gate value
# — excluded whenever a test compares a profile's gate keys against config.json or another profile.
META_KEYS = {"enabled"}


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


def _gates(profile: dict) -> dict:
    """A profile dict stripped to just its gate overrides — no `_`-prefixed documentation keys
    and no `enabled` (a registry-membership switch, never a value config.json could echo)."""
    return {k: v for k, v in profile.items() if not k.startswith("_") and k not in META_KEYS}


def test_config_risk_json_valid_structure(sample_risk_profiles):
    """Verify config.risk.json has required top-level keys."""
    assert "_description" in sample_risk_profiles
    assert "active_profile" in sample_risk_profiles
    assert "profiles" in sample_risk_profiles
    assert isinstance(sample_risk_profiles["profiles"], dict)


def test_active_profile_points_at_control(sample_risk_profiles):
    """active_profile is read only by the live agent loop's /set-risk-profile (never by the
    paper loop, which evaluates every enabled profile every tick) — it must point at a real,
    enabled profile in the registry, and after the 2026-08-07 cutover that is `control`."""
    assert sample_risk_profiles["active_profile"] == "control"
    assert sample_risk_profiles["profiles"]["control"].get("enabled", True) is True


def test_config_risk_json_has_the_expected_profiles(sample_risk_profiles):
    """The registry holds the four active streams plus whichever retired/historical profiles are
    kept for the record. Any name outside that set must be a recognized experiment prefix."""
    names = set(sample_risk_profiles["profiles"].keys())
    assert ACTIVE_STREAMS <= names
    for extra in names - ACTIVE_STREAMS - LADDER - RETIRED_STUDY_ARMS:
        assert extra.split("-")[0] in EXPERIMENT_PREFIXES, f"unexpected profile {extra!r}"


def test_registry_is_the_active_streams_plus_the_historical_record(sample_risk_profiles):
    """What the registry holds, pinned exactly: the four active streams, the four retired ladder
    tiers, and the two retired GEX-study arms. Adding or removing a profile should be a
    deliberate edit here, not a silent one."""
    assert set(sample_risk_profiles["profiles"]) == ACTIVE_STREAMS | LADDER | RETIRED_STUDY_ARMS


def test_only_the_active_streams_are_enabled(sample_risk_profiles):
    """Every ladder tier and every retired study arm must be `enabled: false` — kept for history,
    not competing with the active streams for the same ticks."""
    profiles = sample_risk_profiles["profiles"]
    for name in ACTIVE_STREAMS:
        assert profiles[name].get("enabled", True) is True, f"{name} must be enabled"
    for name in LADDER | RETIRED_STUDY_ARMS:
        assert profiles[name].get("enabled") is False, f"{name} must be disabled"


# --------------------------------------------------------------------------- control (the reference book)


def test_control_matches_config_json_defaults(sample_risk_profiles, sample_config):
    """control's own gate keys must equal config.json's defaults exactly — it is the reference
    book / champion (see calibrate.py's champion/challenger surface), and its whole point is
    that it changes nothing from today's deployed policy."""
    control_gates = _gates(sample_risk_profiles["profiles"]["control"])
    for gate, value in control_gates.items():
        assert sample_config.get(gate) == value, (
            f"Gate {gate}: config.json={sample_config.get(gate)}, control={value}"
        )


def test_conservative_profile_matches_config_defaults(sample_risk_profiles, sample_config):
    """conservative is retired (enabled: false) but its VALUES are kept as the historical record
    of the ladder's base rung, and must still equal config.json's defaults (unchanged from before
    the 2026-08-07 cutover) — if someone edits conservative's numbers without updating config.json
    (or vice versa), this is the regression guard that catches it."""
    conservative_gates = _gates(sample_risk_profiles["profiles"]["conservative"])
    for gate, value in conservative_gates.items():
        assert sample_config.get(gate) == value, (
            f"Gate {gate}: config.json={sample_config.get(gate)}, conservative={value}"
        )


def test_control_and_conservative_carry_the_same_gate_values(sample_risk_profiles):
    """control is conservative's successor under the name the arm registry actually references —
    their gate values (excluding overlap_scope/window keys conservative never declared) must
    agree, or `control` silently stopped meaning 'today's deployed policy'."""
    profiles = sample_risk_profiles["profiles"]
    conservative_gates = _gates(profiles["conservative"])
    control_gates = _gates(profiles["control"])
    for key, value in conservative_gates.items():
        assert control_gates.get(key) == value, key


# --------------------------------------------------------------------------- open + width pair


def test_open_is_control_with_every_study_gate_relaxed(sample_risk_profiles):
    """open is the permissive superset every gate-relief question is answered from by splitting
    its own recorded rows — pin the specific relaxations that make it so, rather than just
    trusting the _note prose."""
    open_arm = sample_risk_profiles["profiles"]["open"]
    assert open_arm["min_iv_rank"] == 0.0
    assert open_arm["min_call_otm_pct"] < 0.0035
    assert open_arm["min_put_otm_pct"] < 0.003
    assert open_arm["late_entry_bias_enabled"] is False
    assert open_arm["regime_vix_pause_threshold"] > 25  # sentinel-high, not null (see _gate_thresholds_note)
    assert open_arm["regime_vix1d_ratio_pause_threshold"] > 1.3
    assert open_arm["regime_atr_pause_threshold_pct"] is None  # null-safe gate, genuinely off
    assert open_arm["regime_gex_block_negative"] is False
    assert open_arm["regime_gex_require_positive"] is False
    assert open_arm["regime_gex_min_flip_distance_pct"] is None
    assert open_arm["per_side_stop_management"] is False  # held to settlement/force-close
    assert open_arm["overlap_scope"] == "none"
    assert open_arm["paper_entry_window_start"] == "09:45"
    assert open_arm["entry_window_end"] == "15:30"


def test_open_gate_thresholds_never_crash_evaluate_entry():
    """The gate-relief thresholds open declares (sentinel-high VIX/VIX1D, null ATR/flip-distance)
    must actually clear evaluate_entry without raising — a regression pin for the exact null-
    threshold TypeError class regime_atr_pause_threshold_pct had before the 2026-08-07 fix."""
    from cherrypick.meic import paper

    base = paper.load_base_config()
    open_arm = paper.load_profiles()["open"]
    params = paper._merged_params(base, open_arm)
    snapshot = {
        "symbol": "SPX",
        "date": "2026-08-07",
        "now_et": "10:00",
        "expiration": "2026-08-07",
        "dte": 0,
        "underlying_price": 7500.0,
        "iv_rank": 0.05,  # below every real IV floor -- must not be refused by open
        "vix": 40.0,  # above every real VIX threshold -- must not be refused by open
        "vix1d_ratio": 2.0,
        "atr_5day": 500.0,  # absurdly high -- must not be refused by open (gate is off)
        "intraday_range_pct": 0.05,
        "session_quality": "open_volatile",
        "gex": {"ok": True, "gex_positive": False, "gamma_flip": 7000.0, "spot": 7500.0},
        "candidates": [],
        "leg_quotes": {},
    }
    entered, reason, chosen = paper.evaluate_entry(snapshot, params, [])  # must not raise
    assert reason != "regime_atr_elevated"
    assert reason != "regime_vix_elevated"
    assert reason != "regime_vix1d_ratio_elevated"
    assert reason != "regime_gex_negative"
    assert reason != "iv_rank_below_floor"


def test_width_arms_differ_from_open_only_in_wing_width(sample_risk_profiles):
    """The one-variable invariant: width-5 and width-10 must be IDENTICAL to `open` except for
    wing_widths_by_symbol (the structural difference under test) and per_side_stop_management
    (deliberately kept ON, unlike `open`'s hold-to-expiry, so the pair measures a pure width
    effect rather than a width-times-stop-policy confound — see width-5's _note)."""
    profiles = sample_risk_profiles["profiles"]
    open_arm = profiles["open"]
    allowed_diff = {"_note", "wing_widths_by_symbol", "per_side_stop_management"}
    for name in ("width-5", "width-10"):
        arm = profiles[name]
        # Documentation keys (_note, _gate_thresholds_note, ...) don't count against the invariant.
        keys = {k for k in set(open_arm) | set(arm) if not k.startswith("_")}
        differing = {k for k in keys if open_arm.get(k) != arm.get(k)}
        assert differing <= allowed_diff, f"{name} diverges from open beyond the width pin: {differing}"
        assert arm["per_side_stop_management"] is True  # unlike open
        assert open_arm["per_side_stop_management"] is False


def test_width_arms_pin_exactly_one_wing_each(sample_risk_profiles):
    profiles = sample_risk_profiles["profiles"]
    assert profiles["width-5"]["wing_widths_by_symbol"]["SPX"] == [5]
    assert profiles["width-10"]["wing_widths_by_symbol"]["SPX"] == [10]


def test_width_arms_differ_from_each_other_only_in_wing_width(sample_risk_profiles):
    profiles = sample_risk_profiles["profiles"]
    w5, w10 = profiles["width-5"], profiles["width-10"]
    keys = set(w5) | set(w10)
    differing = {k for k in keys if w5.get(k) != w10.get(k)}
    assert differing == {"_note", "wing_widths_by_symbol"}, differing


def test_uncapped_sampling_streams_never_bind_on_concurrency(sample_risk_profiles):
    """open/width-5/width-10 must run comfortably past the measured entry cadence (~115-172
    ticks/session) so max_concurrent_ics never becomes the binding constraint — an identical cap
    that BINDS still produces stream-dependent entry counts (exit speed feeds entry capacity)."""
    profiles = sample_risk_profiles["profiles"]
    for name in UNCAPPED_SAMPLING_STREAMS:
        p = profiles[name]
        assert p["max_concurrent_ics"] >= 500, name
        assert p["daily_ic_trade_target"] >= 500, name
        assert p["max_concurrent_ics"] == p["daily_ic_trade_target"], name  # equal -> neither binds first


# --------------------------------------------------------------------------- retired ladder (historical record)


def test_moderate_profile_relaxes_gates_appropriately(sample_risk_profiles):
    """Verify moderate profile relaxes gates in the expected direction (historical record)."""
    conservative = sample_risk_profiles["profiles"]["conservative"]
    moderate = sample_risk_profiles["profiles"]["moderate"]

    assert moderate["min_iv_rank"] < conservative["min_iv_rank"]
    assert moderate["min_iv_rank"] == 0.22
    assert moderate["min_credit_pct_of_width"] < conservative["min_credit_pct_of_width"]
    assert moderate["min_credit_pct_of_width"] == 0.12
    assert moderate["late_entry_bias_start_time"] < conservative["late_entry_bias_start_time"]
    assert moderate["late_entry_bias_start_time"] == "11:00"
    assert moderate["stop_trigger_ratio"] < conservative["stop_trigger_ratio"]
    assert moderate["stop_trigger_ratio"] == 0.93
    assert moderate["daily_ic_trade_target"] == conservative["daily_ic_trade_target"] == 200


def test_aggressive_profile_relaxes_additional_gates(sample_risk_profiles):
    """Verify aggressive profile adds delta/OTM relaxation with position-size offsets (historical record)."""
    moderate = sample_risk_profiles["profiles"]["moderate"]
    aggressive = sample_risk_profiles["profiles"]["aggressive"]

    assert aggressive["min_iv_rank"] < moderate["min_iv_rank"]
    assert aggressive["min_iv_rank"] == 0.20
    assert aggressive["min_credit_pct_of_width"] < moderate["min_credit_pct_of_width"]
    assert aggressive["min_credit_pct_of_width"] == 0.10
    assert aggressive["max_call_delta_entry"] > moderate["max_call_delta_entry"]
    assert aggressive["max_call_delta_entry"] == 0.22
    assert aggressive["min_call_otm_pct"] < moderate["min_call_otm_pct"]
    assert aggressive["min_put_otm_pct"] < moderate["min_put_otm_pct"]
    assert aggressive["max_concurrent_ics"] == moderate["max_concurrent_ics"] == 99
    assert aggressive["stop_trigger_ratio"] < moderate["stop_trigger_ratio"]
    assert aggressive["stop_trigger_ratio"] == 0.90
    assert aggressive["daily_ic_trade_target"] == moderate["daily_ic_trade_target"] == 200


def test_very_aggressive_profile_relaxes_regime_gates(sample_risk_profiles):
    """Verify very-aggressive profile relaxes regime gates (VIX/ATR) with extreme offsets (historical record)."""
    aggressive = sample_risk_profiles["profiles"]["aggressive"]
    very_aggressive = sample_risk_profiles["profiles"]["very-aggressive"]

    assert very_aggressive["min_iv_rank"] <= aggressive["min_iv_rank"]
    assert very_aggressive["max_call_delta_entry"] >= aggressive["max_call_delta_entry"]
    assert very_aggressive["regime_vix_pause_threshold"] > aggressive["regime_vix_pause_threshold"]
    assert very_aggressive["regime_vix_pause_threshold"] == 30
    assert very_aggressive["regime_atr_pause_threshold_pct"] > aggressive["regime_atr_pause_threshold_pct"]
    assert very_aggressive["regime_atr_pause_threshold_pct"] == 0.020
    assert (
        very_aggressive["regime_vix1d_ratio_pause_threshold"]
        > aggressive["regime_vix1d_ratio_pause_threshold"]
    )
    assert very_aggressive["regime_vix1d_ratio_pause_threshold"] == 1.40
    assert very_aggressive["max_concurrent_ics"] == 99
    assert very_aggressive["stop_trigger_ratio"] == 0.85
    assert very_aggressive["daily_ic_trade_target"] == 200


def test_ladder_profiles_have_required_gate_keys(sample_risk_profiles):
    """The ladder profiles are complete presets — every required gate key present (historical record)."""
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
        profile_gates = _gates(profile)
        for key in required_keys:
            assert key in profile_gates, f"Profile {profile_name} missing required key: {key}"


def test_ladder_derived_thresholds_scale_with_each_tier():
    """The low-IV relief ceiling/floor and late-entry-bias ceiling must DERIVE from each tier, not
    repeat one absolute (historical record: the ladder itself is retired, but its own internal
    consistency is still worth guarding since the values remain the base rung's documented history)."""
    from cherrypick.meic import paper

    base, profiles = paper.load_base_config(), paper.load_profiles()
    prev = None
    for tier in ["conservative", "moderate", "aggressive", "very-aggressive"]:
        p = paper._merged_params(base, profiles[tier])
        relief_max, relief_floor = paper._low_iv_relief_max(p), paper._low_iv_relief_floor(p)
        assert relief_floor < p["min_credit_pct_of_width"], tier
        assert relief_max == pytest.approx(p["min_iv_rank"] + 0.05), tier
        assert paper._late_entry_bias_max(p) == pytest.approx(p["min_iv_rank"] + 0.15), tier
        assert p["daily_ic_trade_target"] == 200, tier
        if prev is not None:
            assert relief_max < prev, "relief ceiling must loosen down the ladder"
        prev = relief_max


def test_ladder_credit_floor_is_monotonic_at_every_iv_level():
    """The effective credit floor must never invert down the ladder (historical record)."""
    from cherrypick.meic import paper

    base, profiles = paper.load_base_config(), paper.load_profiles()
    tiers = ["conservative", "moderate", "aggressive", "very-aggressive"]
    merged = {t: paper._merged_params(base, profiles[t]) for t in tiers}
    for iv_rank in (0.16, 0.21, 0.24, 0.28, 0.32, 0.40, 0.60):
        floors = []
        for t in tiers:
            p = merged[t]
            if iv_rank < p["min_iv_rank"]:
                continue
            floors.append(
                (
                    t,
                    paper._low_iv_relief_floor(p)
                    if iv_rank <= paper._low_iv_relief_max(p)
                    else p["min_credit_pct_of_width"],
                )
            )
        for (t_strict, f_strict), (t_loose, f_loose) in zip(floors, floors[1:], strict=False):
            assert f_strict >= f_loose, (
                f"iv_rank {iv_rank}: {t_strict} floor {f_strict:.3f} < {t_loose} {f_loose:.3f}"
            )


def test_profile_progression_is_monotonic(sample_risk_profiles):
    """Verify the retired tier progression is consistently more relaxed (historical record)."""
    profiles_ordered = [
        sample_risk_profiles["profiles"]["conservative"],
        sample_risk_profiles["profiles"]["moderate"],
        sample_risk_profiles["profiles"]["aggressive"],
        sample_risk_profiles["profiles"]["very-aggressive"],
    ]
    iv_ranks = [p["min_iv_rank"] for p in profiles_ordered]
    assert iv_ranks == sorted(iv_ranks, reverse=True)
    credit_floors = [p["min_credit_pct_of_width"] for p in profiles_ordered]
    assert credit_floors == sorted(credit_floors, reverse=True)
    daily_targets = [p["daily_ic_trade_target"] for p in profiles_ordered]
    assert daily_targets == sorted(daily_targets)


def test_config_json_stale_values_fixed(sample_config):
    """Verify that stale values in CLAUDE.md documentation have been fixed in config.json."""
    assert sample_config["min_credit_pct_of_width"] == 0.15, (
        "min_credit_pct_of_width should be 0.15 (not 0.20 as docs said)"
    )
    assert sample_config["max_concurrent_ics"] == 99, "max_concurrent_ics should be 99 (control's cap)"


# --------------------------------------------------------------------------- shared shape checks


def test_all_profiles_have_description_note(sample_risk_profiles):
    """Verify every profile has a _note field explaining its purpose."""
    for profile_name, profile in sample_risk_profiles["profiles"].items():
        assert "_note" in profile, f"Profile {profile_name} missing _note field"
        assert isinstance(profile["_note"], str)
        assert len(profile["_note"]) > 20, f"Profile {profile_name} _note is too short"


def test_profile_gate_values_are_valid_types(sample_risk_profiles):
    """Verify gate values in profiles are the correct types."""
    for profile_name, profile in sample_risk_profiles["profiles"].items():
        float_gates = [
            "min_iv_rank",
            "min_credit_pct_of_width",
            "low_iv_min_credit_pct_of_width",
            "low_iv_credit_floor_iv_rank_max",
            "max_call_delta_entry",
            "max_call_delta_entry_open_volatile",
            "max_call_delta_entry_late",
            "min_call_otm_pct",
            "min_put_otm_pct",
            "late_entry_bias_iv_rank_max",
            "regime_vix1d_ratio_pause_threshold",
            "stop_trigger_ratio",
        ]
        # regime_atr_pause_threshold_pct is deliberately null on the uncapped-sampling streams
        # (a genuine "gate off", not a numeric threshold) -- checked for type only when present
        # AND non-null.
        for gate in ["regime_atr_pause_threshold_pct"]:
            if profile.get(gate) is not None:
                assert isinstance(profile[gate], (int, float)), f"{profile_name}.{gate} should be numeric"

        for gate in float_gates:
            if gate in profile:
                assert isinstance(profile[gate], (int, float)), f"{profile_name}.{gate} should be numeric"

        int_gates = ["regime_vix_pause_threshold", "max_concurrent_ics", "daily_ic_trade_target"]
        for gate in int_gates:
            if gate in profile:
                assert isinstance(profile[gate], int), f"{profile_name}.{gate} should be int"

        bool_gates = ["late_entry_bias_enabled"]
        for gate in bool_gates:
            if gate in profile:
                assert isinstance(profile[gate], bool), f"{profile_name}.{gate} should be bool"

        str_gates = ["late_entry_bias_start_time"]
        for gate in str_gates:
            if gate in profile:
                assert isinstance(profile[gate], str), f"{profile_name}.{gate} should be str"


def test_profile_gate_values_in_reasonable_ranges(sample_risk_profiles):
    """Verify gate values are in reasonable ranges (sanity check)."""
    for name, profile in sample_risk_profiles["profiles"].items():
        if "min_iv_rank" in profile:
            assert 0.0 <= profile["min_iv_rank"] <= 1.0
        if "min_credit_pct_of_width" in profile:
            assert 0.0 <= profile["min_credit_pct_of_width"] <= 1.0
        if "max_call_delta_entry" in profile:
            assert 0.0 <= profile["max_call_delta_entry"] <= 1.0
        if "min_call_otm_pct" in profile:
            assert 0.0 <= profile["min_call_otm_pct"] <= 1.0
        if "stop_trigger_ratio" in profile:
            assert 0.5 <= profile["stop_trigger_ratio"] <= 1.5
        if profile.get("regime_vix_pause_threshold") is not None:
            assert profile["regime_vix_pause_threshold"] > 0

        # Max concurrent ICs: 1-99 for a book-semantics profile (the retired ladder/GEX arms and
        # `control`), up to 999 for the deliberately-uncapped sampling streams.
        if "max_concurrent_ics" in profile:
            ceiling = 999 if name in UNCAPPED_SAMPLING_STREAMS else 99
            assert 1 <= profile["max_concurrent_ics"] <= ceiling, name

        if "daily_ic_trade_target" in profile:
            assert profile["daily_ic_trade_target"] >= 0


def test_experiment_profiles_pin_symbol_and_wings(sample_risk_profiles):
    """Experiment cells that DO declare `symbols` must pin the axes they vary. Study-arm-prefixed
    profiles (width-*/gex-*) are symbol-agnostic by design and exempt — checked separately."""
    profiles = sample_risk_profiles["profiles"]
    for name in set(profiles) - ACTIVE_STREAMS - LADDER:
        if name.startswith(STUDY_ARM_PREFIXES):
            continue
        p = profiles[name]
        if "symbols" not in p:
            continue
        assert isinstance(p.get("symbols"), list) and p["symbols"], f"{name} must pin `symbols`"
        wbs = p.get("wing_widths_by_symbol")
        assert isinstance(wbs, dict) and wbs, f"{name} must set `wing_widths_by_symbol`"
        for sym in p["symbols"]:
            assert sym in wbs and wbs[sym], f"{name} missing wings for {sym}"


def test_retired_width_study_arms_are_gone(sample_risk_profiles):
    """The 2026-07-28 wing-width study's original arms (width-2/width-adaptive/etc — distinct
    from the 2026-08-07 width-5/width-10 pair) must not linger in the registry."""
    profiles = sample_risk_profiles["profiles"]
    retired_names = {n for n in profiles if n.startswith("width-") and n not in {"width-5", "width-10"}}
    assert not retired_names, retired_names


# --------------------------------------------------------------------------- GEX study (retired 2026-08-07)


def test_gex_study_arms_differ_in_exactly_one_key(sample_risk_profiles):
    """The invariant the whole (retired) GEX experiment rested on — kept as a historical-record
    guard so the two arms' kept values don't silently drift apart from each other."""
    profiles = sample_risk_profiles["profiles"]
    assert {"gex-open", "gex-blocked"} <= set(profiles)
    open_arm, blocked = profiles["gex-open"], profiles["gex-blocked"]

    keys = {k for k in set(open_arm) | set(blocked) if not k.startswith("_") and k not in META_KEYS}
    differing = {k for k in keys if open_arm.get(k) != blocked.get(k)}
    assert differing == {"regime_gex_block_negative"}, f"arms diverge beyond the treatment: {differing}"
    assert open_arm["regime_gex_block_negative"] is False
    assert blocked["regime_gex_block_negative"] is True


def test_gex_study_arms_are_forced_sampling(sample_risk_profiles):
    """Both retired arms must still face the GATE as their only binding constraint (historical record)."""
    for name in ("gex-open", "gex-blocked"):
        p = sample_risk_profiles["profiles"][name]
        assert p["stagger_entries"] is True
        assert p["min_minutes_between_entries"] == 0
        assert p["overlap_scope"] == "shorts"
        assert p["max_concurrent_ics"] == 99, f"{name} must run uncapped (each trade a sample)"
        assert p["daily_ic_trade_target"] >= 200, f"{name}'s daily cap must never bind"


def test_gex_gate_family_is_documented_in_the_example_config():
    """The three GEX gates were readable by the engine but appeared in no config file, so they could
    only be found by reading paper.py (audit 2026-08-06). A gate nobody can discover is a gate nobody
    can tune — and these are the ones open/control now toggle (formerly gex-open/gex-blocked)."""
    import re

    example = Path(__file__).resolve().parents[1] / "config.example.json"
    cfg = json.loads(re.sub(r"^\s*//.*$", "", example.read_text(encoding="utf-8"), flags=re.M))
    assert cfg["regime_gex_block_negative"] is True  # the live baseline
    assert cfg["regime_gex_require_positive"] is False  # opt-in strict variant
    assert cfg["regime_gex_min_flip_distance_pct"] is None  # opt-in magnitude variant, off


def test_documenting_those_gates_did_not_change_what_they_do():
    """The example ships the engine's own defaults, so copying it must leave behaviour untouched —
    otherwise 'documenting' a gate silently retunes every fresh install."""
    import re

    from cherrypick.meic import paper

    example = Path(__file__).resolve().parents[1] / "config.example.json"
    documented = json.loads(re.sub(r"^\s*//.*$", "", example.read_text(encoding="utf-8"), flags=re.M))
    stripped = {k: v for k, v in documented.items() if not k.startswith("regime_gex_")}
    snapshot = {
        "symbol": "SPX",
        "iv_rank": 0.5,
        "vix": 15,
        "gex": {"ok": True, "gex_positive": False},
        "underlying_price": 6300.0,
        "atr_5day": 10.0,
        "session": "prime",
        "now_min": 660,
        "date": "2026-08-06",
        "time": "11:00",
    }
    assert paper.evaluate_entry(snapshot, documented, [], 0)[:2] == (False, "regime_gex_negative")
    assert paper.evaluate_entry(snapshot, stripped, [], 0)[:2] == (False, "regime_gex_negative")
