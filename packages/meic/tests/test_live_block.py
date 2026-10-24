"""The live block (2026-10-14): `live.arm`, live-only overrides, `follows_live` arms, the warnings that
say when a live-only value sits at the top level, and the migration that moves it."""

from __future__ import annotations

import json

import pytest

from cherrypick.meic import live_loop, live_migrate, paper

TOP = {
    "entry_window_start": "10:00",
    "entry_window_end": "14:30",
    "wing_widths_by_symbol": {"SPX": [5, 10], "DEFAULT": [2, 5, 10]},
    "min_iv_rank": 0.3,
    "per_side_stop_management": True,
}
EXAMPLE = {
    "entry_window_start": "10:00",
    "entry_window_end": "14:30",
    "max_open_bp_dollars": None,
    "wing_widths_by_symbol": {"SPX": [5, 10], "DEFAULT": [2, 5, 10]},
}
REGISTRY = {
    "control": {"min_iv_rank": 0.0, "paper_entry_window_start": "09:45", "entry_window_end": "15:30"},
    "capped": {"max_open_bp_dollars": 5000, "wing_width_points": 10},
    "live-shadow": {"enabled": True, "follows_live": True},
}


def _cfg(**live):
    return {**TOP, "live": live}


# --------------------------------------------------------------------------- live_params


def test_without_a_live_block_live_trades_the_top_level_unchanged():
    """The compatibility promise: a config with no new keys trades exactly what it did."""
    assert paper.live_params(TOP, REGISTRY) == paper._merged_params(TOP, {})
    # live.symbol pins the symbol list (2026-10-15) and changes nothing else
    pinned = paper.live_params(_cfg(symbol="SPX"), REGISTRY)
    assert pinned.pop("symbols") == ["SPX"]
    assert pinned == paper._merged_params(_cfg(symbol="SPX"), {})


def test_a_follows_live_arm_trades_only_lives_symbol(monkeypatch):
    """live on XSP: live-shadow scans XSP alone, while control keeps every symbol the loop scans."""
    monkeypatch.setattr(paper, "load_profiles", lambda: REGISTRY)
    cfg = {
        **_cfg(symbol="xsp", wing_widths_by_symbol={"XSP": [2]}, max_open_bp_dollars=500),
        "symbols": ["SPX", "XSP"],
    }
    shadow = paper._merged_params(cfg, REGISTRY["live-shadow"])
    assert shadow["symbols"] == ["XSP"] and paper._profile_widths_for_symbol(shadow, "XSP") == [2]
    assert paper._merged_params(cfg, REGISTRY["control"])["symbols"] == ["SPX", "XSP"]


def test_each_arm_declares_its_own_roster_for_the_same_index_lint(monkeypatch):
    from cherrypick.meic import stream_request

    registry = {**REGISTRY, "control": {**REGISTRY["control"], "symbols": ["SPX"]}}
    monkeypatch.setattr(paper, "load_profiles", lambda: registry)
    cfg = {**_cfg(symbol="XSP"), "symbols": ["SPX", "XSP"]}
    rosters = stream_request.arm_rosters(cfg, registry)
    assert rosters["control"] == ["SPX"] and rosters["live-shadow"] == ["XSP"]
    assert rosters["capped"] == ["SPX", "XSP"]  # an arm that pins nothing scans both -- the lint's case


def test_live_arm_is_merged_onto_the_top_level():
    params = paper.live_params(_cfg(arm="control"), REGISTRY)
    assert params["min_iv_rank"] == 0.0 and params["per_side_stop_management"] is True


def test_live_overrides_only_narrow():
    # start: the later of the two wins, whichever side sets it, and paper's key follows it
    p = paper.live_params(_cfg(arm="control", entry_window_start="12:30"), REGISTRY)
    assert p["entry_window_start"] == p["paper_entry_window_start"] == "12:30"
    p = paper.live_params(_cfg(arm="control", entry_window_start="09:30"), REGISTRY)
    assert p["entry_window_start"] == "09:45"
    # end: the earlier wins
    assert (
        paper.live_params(_cfg(arm="control", entry_window_end="14:00"), REGISTRY)["entry_window_end"]
        == "14:00"
    )
    assert (
        paper.live_params(_cfg(arm="control", entry_window_end="16:00"), REGISTRY)["entry_window_end"]
        == "15:30"
    )
    # cap: the smaller wins; an uncapped arm takes live's
    assert (
        paper.live_params(_cfg(arm="capped", max_open_bp_dollars=500), REGISTRY)["max_open_bp_dollars"] == 500
    )
    assert (
        paper.live_params(_cfg(arm="capped", max_open_bp_dollars=9000), REGISTRY)["max_open_bp_dollars"]
        == 5000
    )
    assert paper.live_params(_cfg(max_open_bp_dollars=500), REGISTRY)["max_open_bp_dollars"] == 500


def test_live_widths_replace_the_arms_and_beat_its_scalar():
    p = paper.live_params(_cfg(arm="capped", wing_widths_by_symbol={"SPX": [5]}), REGISTRY)
    assert paper._profile_widths_for_symbol(p, "SPX") == [5]


def test_live_overrides_never_reach_a_paper_arm():
    cfg = _cfg(max_open_bp_dollars=500, wing_widths_by_symbol={"SPX": [5]}, entry_window_start="12:30")
    control = paper._merged_params(cfg, REGISTRY["control"])
    assert control.get("max_open_bp_dollars") is None
    assert paper._profile_widths_for_symbol(control, "SPX") == [5, 10]
    assert control["paper_entry_window_start"] == "09:45"


# --------------------------------------------------------------------------- follows_live


def test_a_follows_live_arm_is_live_plus_its_own_keys(monkeypatch):
    monkeypatch.setattr(paper, "load_profiles", lambda: REGISTRY)
    cfg = _cfg(arm="control", max_open_bp_dollars=500, entry_window_start="12:30")
    shadow = paper._merged_params(cfg, REGISTRY["live-shadow"])
    live = paper.live_params(cfg, REGISTRY)
    assert {k for k in set(shadow) | set(live) if shadow.get(k) != live.get(k)} <= {"enabled", "follows_live"}
    minus = paper._merged_params(cfg, {"follows_live": True, "min_iv_rank": 0.1})
    assert minus["min_iv_rank"] == 0.1 and minus["max_open_bp_dollars"] == 500


# --------------------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    ("arm", "fragment"), [("nope", "not a configured arm"), ("live-shadow", "follows live itself")]
)
def test_a_bad_live_arm_is_refused_by_readiness(monkeypatch, arm, fragment):
    monkeypatch.setattr(paper, "load_profiles", lambda: REGISTRY)
    unmet = live_loop.readiness(_cfg(arm=arm, symbol="SPX", gate0_confirmed="me"), designated="1234")
    assert any(fragment in u for u in unmet), unmet


def test_a_good_live_arm_is_not_refused(monkeypatch):
    """The guard above, shown not to fire on a valid arm."""
    monkeypatch.setattr(paper, "load_profiles", lambda: REGISTRY)
    cfg = {**_cfg(arm="control", symbol="SPX", gate0_confirmed="me"), "enable_live_trading": True}
    assert live_loop.readiness(cfg, designated="1234") == []


def test_a_bad_live_arm_does_not_crash_a_paper_shadow(monkeypatch):
    monkeypatch.setattr(paper, "load_profiles", lambda: REGISTRY)
    shadow = paper._merged_params(_cfg(arm="nope", max_open_bp_dollars=500), REGISTRY["live-shadow"])
    assert shadow["max_open_bp_dollars"] == 500 and shadow["min_iv_rank"] == 0.3


def test_a_live_arm_with_equalize_credit_is_refused_by_readiness(monkeypatch):
    """`equalize_credit` is paper-only until the pair shape has been tried with real orders
    (2026-10-20). A live arm that sets it would order per-side legs whose widths differ -- a shape the
    live order path has never carried -- so readiness refuses it, exactly like a follows_live arm."""
    registry = {
        **REGISTRY,
        "equalized": {"equalize_credit": True, "wing_widths_by_symbol": {"SPX": [25, 35]}},
    }
    monkeypatch.setattr(paper, "load_profiles", lambda: registry)
    cfg = {**_cfg(arm="equalized", symbol="SPX", gate0_confirmed="me"), "enable_live_trading": True}
    unmet = live_loop.readiness(cfg, designated="1234")
    assert any("equalize_credit is paper-only" in u for u in unmet), unmet


@pytest.mark.parametrize("arm", sorted(paper.LIVE_REFUSED_ARMS))
def test_a_named_refused_arm_is_refused_even_without_equalize_credit(monkeypatch, arm):
    """The name block holds on its own: the arm is given a clean, otherwise-valid definition (no
    `equalize_credit`), so only the name can trip it."""
    registry = {**REGISTRY, arm: dict(REGISTRY["control"])}
    monkeypatch.setattr(paper, "load_profiles", lambda: registry)
    cfg = {**_cfg(arm=arm, symbol="SPX", gate0_confirmed="me"), "enable_live_trading": True}
    unmet = live_loop.readiness(cfg, designated="1234")
    assert any("is refused for live" in u for u in unmet), unmet
    # the same definition under another name passes, so the refusal above is the name's
    registry["renamed"] = registry[arm]
    assert live_loop.readiness({**cfg, "live": {**cfg["live"], "arm": "renamed"}}, designated="1234") == []


def test_equalize_credit_at_the_top_level_is_refused_without_a_live_arm():
    """The no-arm path still checks the top level: a `live` block that turns `equalize_credit` on
    (or a top-level default) must not slip live into the pair shape by omission of `live.arm`."""
    cfg = {**TOP, "equalize_credit": True}
    assert "equalize_credit is paper-only" in (paper.live_arm_problem(cfg, REGISTRY) or "")


def test_equalize_credit_on_a_paper_arm_is_not_refused(monkeypatch):
    """The guard above, shown not to fire on an arm that opts in for paper: the flag is legitimate on
    a registry arm, so only the live seam refuses it."""
    registry = {**REGISTRY, "paper-equalized": {"equalize_credit": True}}
    monkeypatch.setattr(paper, "load_profiles", lambda: registry)
    # No `live.arm`, and the machine's top level does not carry the flag -- so live is unaffected and
    # the paper arm is free to trade the pair shape.
    assert paper.live_arm_problem({**TOP, "live": {}}, registry) is None


# --------------------------------------------------------------------------- warnings


def test_a_live_only_key_at_the_top_level_warns():
    cfg = {**TOP, "max_open_bp_dollars": 500}
    warnings = paper.live_config_warnings(cfg, REGISTRY, EXAMPLE)
    assert any("max_open_bp_dollars" in w and "live_migrate" in w for w in warnings)


def test_the_shipped_defaults_at_the_top_level_do_not_warn():
    assert paper.live_config_warnings({**TOP, "max_open_bp_dollars": None}, REGISTRY, EXAMPLE) == []


def test_a_live_block_with_no_follower_warns():
    no_shadow = {k: v for k, v in REGISTRY.items() if k != "live-shadow"}
    warnings = paper.live_config_warnings(_cfg(max_open_bp_dollars=500), no_shadow, EXAMPLE)
    assert any("follows_live" in w for w in warnings)
    assert paper.live_config_warnings(_cfg(max_open_bp_dollars=500), REGISTRY, EXAMPLE) == []


# --------------------------------------------------------------------------- migration


def _tonight():
    """The shape this was written for: live sizing put at the top level, paper arms unpinned."""
    cfg = {**TOP, "max_open_bp_dollars": 500, "wing_widths_by_symbol": {"SPX": [5], "DEFAULT": [2, 5, 10]}}
    cfg["entry_window_start"] = "12:30"
    reg = {"profiles": {"control": dict(REGISTRY["control"]), "live-shadow": {"enabled": True}}}
    return cfg, reg


def test_the_migration_moves_live_keys_and_restores_the_defaults():
    cfg, reg = _tonight()
    p = live_migrate.plan(cfg, reg, EXAMPLE, ["live-shadow"])
    new = p["config"]
    assert new["live"]["max_open_bp_dollars"] == 500 and new["max_open_bp_dollars"] is None
    assert new["live"]["wing_widths_by_symbol"]["SPX"] == [5] and new["wing_widths_by_symbol"]["SPX"] == [
        5,
        10,
    ]
    assert new["live"]["entry_window_start"] == "12:30" and new["entry_window_start"] == "10:00"
    assert p["registry"]["profiles"]["live-shadow"]["follows_live"] is True
    # live trades what it traded before the move
    assert p["live_changes"] == []


def test_the_migration_names_the_paper_arms_it_moves():
    """control left the cap and widths unset, so it was trading tonight's live values; the plan
    must say it goes back to the defaults rather than doing so silently."""
    cfg, reg = _tonight()
    changes = live_migrate.plan(cfg, reg, EXAMPLE, ["live-shadow"])["paper_arm_changes"]
    moved = {(c["arm"], c["key"]) for c in changes}
    assert ("control", "max_open_bp_dollars") in moved and ("control", "wing_widths_by_symbol") in moved
    assert not any(c["arm"] == "live-shadow" for c in changes)


def test_the_migration_will_not_overwrite_a_live_value():
    cfg, reg = _tonight()
    cfg["live"] = {"max_open_bp_dollars": 800}
    p = live_migrate.plan(cfg, reg, EXAMPLE, [])
    assert p["config"]["live"]["max_open_bp_dollars"] == 800 and p["config"]["max_open_bp_dollars"] == 500
    assert any("max_open_bp_dollars" in c for c in p["conflicts"])


def test_the_migration_is_a_dry_run_unless_applied(tmp_path, monkeypatch, capsys):
    cfg, reg = _tonight()
    cfg_path, reg_path, ex_path = (
        tmp_path / "meic.json",
        tmp_path / "meic.risk.json",
        tmp_path / "example.json",
    )
    for path, doc in ((cfg_path, cfg), (reg_path, reg), (ex_path, EXAMPLE)):
        path.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(live_migrate._paths, "config_path", lambda: cfg_path)
    monkeypatch.setattr(live_migrate._paths, "risk_profiles_path", lambda: reg_path)
    monkeypatch.setattr(live_migrate._paths, "example_config_path", lambda: ex_path)

    assert live_migrate.main(["--follows-live", "live-shadow"]) == 0
    assert json.loads(cfg_path.read_text(encoding="utf-8")) == cfg
    assert not list(tmp_path.glob("*.bak-*"))

    assert live_migrate.main(["--follows-live", "live-shadow", "--apply"]) == 0
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["live"]["max_open_bp_dollars"] == 500
    assert len(list(tmp_path.glob("*.bak-pre-live-migrate-*"))) == 2
    capsys.readouterr()
