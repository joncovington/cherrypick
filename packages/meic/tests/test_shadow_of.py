"""Per-arm paper twins (`shadow_of`): a twin is one registry arm plus live's own overrides, pinned to
that arm, so switching `live.arm` moves `follows_live` arms and leaves every twin where it was.

Everything here runs on literal registries and configs: no machine state, no ledger, no broker.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from cherrypick.meic import paper  # noqa: E402

META = {"enabled", "follows_live", "shadow_of"}

REGISTRY = {
    "control": {"enabled": True, "min_iv_rank": 0.0, "per_side_stop_management": False},
    "pm-200": {
        "enabled": False,
        "min_iv_rank": 0.1,
        "entry_window_start": "10:00",
        "per_side_stop_management": True,
    },
    "pm-1400": {
        "enabled": False,
        "min_iv_rank": 0.2,
        "entry_window_start": "14:00",
        "per_side_stop_management": True,
    },
    "live-shadow": {"enabled": True, "follows_live": True},
    "live-shadow-pm-200": {"enabled": True, "shadow_of": "pm-200"},
    "live-shadow-pm-1400": {"enabled": True, "shadow_of": "pm-1400"},
}

CONFIG = {
    "symbols": ["SPX", "XSP"],
    "min_iv_rank": 0.5,
    "max_open_bp_dollars": 4000,
    "live": {"arm": "pm-200", "symbol": "SPX", "max_open_bp_dollars": 3000},
}


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    monkeypatch.setattr(paper, "load_profiles", lambda: copy.deepcopy(REGISTRY))


def _live_arm(config: dict, arm: str) -> dict:
    out = copy.deepcopy(config)
    out["live"]["arm"] = arm
    return out


def _unmarked(params: dict) -> dict:
    """The params less the marker keys and the config's own `live` block, which rides along on every
    merge (it is the config, not a setting the entry logic reads) and names the arm being compared."""
    return {k: v for k, v in params.items() if k not in META and k != "live"}


def check_twin_pinned(twin_name: str, arm: str) -> None:
    """The twin of `arm` is the same dict whichever arm live runs, and equals what live trades when
    live runs `arm`. This is the guarantee the whole per-arm scheme rests on."""
    twin = REGISTRY[twin_name]
    here = _unmarked(paper._merged_params(CONFIG, twin))
    elsewhere = _unmarked(
        paper._merged_params(_live_arm(CONFIG, "pm-1400" if arm == "pm-200" else "pm-200"), twin)
    )
    assert here == elsewhere, f"{twin_name} moved when live.arm changed"
    live_on_arm = _unmarked(paper.live_params(_live_arm(CONFIG, arm), REGISTRY))
    assert here == live_on_arm, f"{twin_name} is not what live trades on {arm}"


def test_a_twin_is_exactly_what_live_trades_on_its_arm_and_does_not_move_with_live_arm():
    check_twin_pinned("live-shadow-pm-200", "pm-200")
    check_twin_pinned("live-shadow-pm-1400", "pm-1400")


def test_the_pinning_check_fails_when_a_twin_follows_live_arm(monkeypatch):
    """The guard, shown to fire: if the pin were dropped and the twin resolved from `live.arm`
    again, it would move with the switch and the check would say so."""
    real = paper._live_resolution
    monkeypatch.setattr(paper, "_live_resolution", lambda config, registry, arm=None: real(config, registry))
    with pytest.raises(AssertionError):
        check_twin_pinned("live-shadow-pm-200", "pm-200")


def test_follows_live_moves_with_the_switch_where_a_twin_does_not():
    shadow = REGISTRY["live-shadow"]
    on_200 = _unmarked(paper._merged_params(CONFIG, shadow))
    on_1400 = _unmarked(paper._merged_params(_live_arm(CONFIG, "pm-1400"), shadow))
    assert on_200 != on_1400
    assert on_200["min_iv_rank"] == 0.1 and on_1400["min_iv_rank"] == 0.2


def test_a_twin_still_takes_the_live_blocks_own_overrides():
    """The pinned symbol and caps are live's, not the arm's: a change there moves every twin, as it
    moves live. Only the arm choice is pinned."""
    twin = REGISTRY["live-shadow-pm-200"]
    base = _unmarked(paper._merged_params(CONFIG, twin))
    smaller = copy.deepcopy(CONFIG)
    smaller["live"]["max_open_bp_dollars"] = 2000
    assert base["max_open_bp_dollars"] == 3000
    assert _unmarked(paper._merged_params(smaller, twin))["max_open_bp_dollars"] == 2000
    assert base["symbols"] == ["SPX"]


def test_a_twin_with_one_override_is_that_arm_with_one_change():
    twin = {"enabled": True, "shadow_of": "pm-200", "min_iv_rank": 0.0}
    merged = _unmarked(paper._merged_params(CONFIG, twin))
    expected = _unmarked(paper.live_params(CONFIG, REGISTRY)) | {"min_iv_rank": 0.0}
    assert merged == expected


def test_twin_for_finds_the_enabled_plain_twin_only():
    assert paper.twin_for("pm-200", REGISTRY) == "live-shadow-pm-200"
    assert paper.twin_for("control", REGISTRY) is None
    off = copy.deepcopy(REGISTRY)
    off["live-shadow-pm-200"]["enabled"] = False
    assert paper.twin_for("pm-200", off) is None
    experiment = copy.deepcopy(REGISTRY)
    experiment["live-shadow-pm-200"]["min_iv_rank"] = 0.0
    assert paper.twin_for("pm-200", experiment) is None


def test_a_clean_registry_has_no_shadow_problems():
    assert paper.shadow_problems(REGISTRY) == []


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda r: r["live-shadow-pm-200"].update(shadow_of="nope"), "is not a configured arm"),
        (lambda r: r["live-shadow-pm-200"].update(shadow_of="live-shadow"), "follower or a twin"),
        (lambda r: r["live-shadow-pm-200"].update(shadow_of="live-shadow-pm-1400"), "follower or a twin"),
        (lambda r: r["live-shadow-pm-200"].update(shadow_of="live-shadow-pm-200"), "names itself"),
        (lambda r: r["live-shadow-pm-200"].update(follows_live=True), "both follows_live and shadow_of"),
        (lambda r: r.update({"second-pm-200": {"enabled": True, "shadow_of": "pm-200"}}), "2 plain twins"),
    ],
)
def test_shadow_problems_name_what_is_wrong(mutate, fragment):
    bad = copy.deepcopy(REGISTRY)
    mutate(bad)
    assert any(fragment in p for p in paper.shadow_problems(bad)), paper.shadow_problems(bad)


def test_two_twins_of_one_arm_are_fine_when_one_carries_an_override_or_is_off():
    ok = copy.deepcopy(REGISTRY)
    ok["pm-200-plus"] = {"enabled": True, "shadow_of": "pm-200", "min_iv_rank": 0.0}
    ok["old-pm-200"] = {"enabled": False, "shadow_of": "pm-200"}
    assert paper.shadow_problems(ok) == []


def test_live_cannot_run_a_twin():
    config = _live_arm(CONFIG, "live-shadow-pm-200")
    assert "itself a shadow of 'pm-200'" in (paper.live_arm_problem(config, REGISTRY) or "")


def test_a_twin_of_a_bad_arm_falls_back_loudly_not_silently(caplog):
    paper._WARNED.clear()
    with caplog.at_level("WARNING"):
        paper._merged_params(CONFIG, {"enabled": True, "shadow_of": "nope"})
    assert any("shadow_of arm" in r.message for r in caplog.records)


def test_warnings_stay_quiet_until_twins_are_adopted_then_ask_for_the_live_arms_twin():
    example: dict = {"max_open_bp_dollars": 4000}  # the shipped default, so the top level is not flagged
    # No twins in the registry: the old reading, one follower is enough.
    plain = {k: v for k, v in REGISTRY.items() if "shadow_of" not in v}
    assert paper.live_config_warnings(CONFIG, plain, example) == []
    # Twins adopted, the live arm has one: quiet.
    assert paper.live_config_warnings(CONFIG, REGISTRY, example) == []
    # Twins adopted, live runs an arm with none: said once per tick until fixed.
    config = _live_arm(CONFIG, "control")
    said = paper.live_config_warnings(config, REGISTRY, example)
    assert any("live.arm 'control' has no enabled twin" in w for w in said)
    # And a live block that shapes live with a twin but no follower is no longer called unmeasured.
    no_follower = {k: v for k, v in REGISTRY.items() if k != "live-shadow"}
    assert not any(
        "no enabled arm has follows_live" in w
        for w in paper.live_config_warnings(CONFIG, no_follower, example)
    )
