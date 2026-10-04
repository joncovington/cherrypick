"""The arm roster: which arms enter, and what each one is when its config block says little.

Every config read in this suite defaults silently, so the failure these guard against is quiet: a
held-long arm resolved from `defaults` alone is a copy of control that still books a plausible P&L,
and an arm the code knows but the config never mentions would widen the stream request unasked.
"""

import copy

from cherrypick.pmcc import db, engine, management, paper_loop


def test_an_undeclared_arm_other_than_control_does_not_enter(config):
    """The fixture declares control alone: the held-long arms stay off until a config names them."""
    arms, _ = paper_loop.session_books(config, "2026-10-05")
    assert arms == ["control"]
    cfg = copy.deepcopy(config)
    cfg["arms"]["shield"] = {"enabled": True}
    arms, _ = paper_loop.session_books(cfg, "2026-10-05")
    assert arms == ["control", "shield"]


def test_a_held_long_arm_keeps_its_rules_with_a_bare_config_block(config):
    """`{"enabled": true}` and nothing else still yields Tom King's rules, over `defaults`' 0.85-0.90
    band that control trades; `shield` alone rolls early."""
    cfg = copy.deepcopy(config)
    cfg["arms"]["shield"] = {"enabled": True}
    cfg["arms"]["shield_hold"] = {"enabled": True}
    shield = engine.merged_params(cfg, "shield")
    hold = engine.merged_params(cfg, "shield_hold")
    for p in (shield, hold):
        assert management.is_held_long(p)
        assert (p["short_rule"], p["long_delta_min"], p["long_delta_max"]) == ("delta", 0.90, 0.95)
        assert p["allow_extrinsic_fallback"] is False
        assert p["stop_loss_frac"] == 0.30
    assert (shield["early_roll_decay"], shield["breach_roll"]) == (0.85, True)
    assert (hold["early_roll_decay"], hold["breach_roll"]) == (None, False)


def test_an_open_held_long_position_keeps_its_rules_after_its_arm_leaves_the_config(config):
    """Management resolves an open row through the same merge, so removing `arms.shield` from the
    config cannot turn its open position into a weekly one that closes at the short's expiry."""
    row = {"position_id": "SLV:shield:2026-10-05", "arm": "shield", "symbol": "SLV"}
    params = management.effective_params(row, config)
    assert management.is_held_long(params)
    assert params["early_roll_decay"] == 0.85


def test_the_config_still_tunes_an_arm(config):
    cfg = copy.deepcopy(config)
    cfg["arms"]["shield"] = {"enabled": True, "stop_loss_frac": 0.25, "short_delta_target": 0.65}
    p = engine.merged_params(cfg, "shield")
    assert (p["stop_loss_frac"], p["short_delta_target"], p["lifecycle"]) == (0.25, 0.65, "held_long")


def test_control_is_untouched_by_the_arm_rules(config):
    assert engine.ARM_RULES["control"] == {}
    assert engine.merged_params(config, "control") == {
        **config["defaults"],
        "enabled": True,
        "arm": "control",
    }


def _breaks(conn):
    return {
        r["key"]: (r["break_date"], r["old_value"], r["new_value"])
        for r in conn.execute("SELECT * FROM measurement_breaks")
    }


def test_the_boundary_is_journaled_once_from_what_the_config_runs(config):
    conn = db.connect(":memory:")
    cfg = copy.deepcopy(config)
    cfg["symbols"] = ["XSP", "QQQ", "GLD", "IWM", "SLV"]
    cfg["defaults"]["max_positions"] = 6
    cfg["arms"]["shield"] = {"enabled": True, "entry_symbols_per_session": 1}
    cfg["arms"]["shield_hold"] = {"enabled": True, "entry_symbols_per_session": 1}
    paper_loop._note_shield_boundary(conn, cfg)
    paper_loop._note_shield_boundary(conn, cfg)  # every tick calls it: idempotent
    rows = _breaks(conn)
    assert set(rows) == {"era", "arms", "symbols", "pacing"}
    assert {r[0] for r in rows.values()} == {paper_loop.SHIELD_FROM}
    assert rows["era"][1:] == ("redesign", "shield")
    assert rows["arms"][2] == "control, shield, shield_hold"
    assert rows["symbols"][2] == "XSP, QQQ, GLD, IWM, SLV; max_positions 6"
    assert rows["pacing"][2] == '{"shield": 1, "shield_hold": 1}'
    assert conn.execute("SELECT COUNT(*) FROM measurement_breaks").fetchone()[0] == 4


def test_a_machine_running_control_alone_journals_no_boundary(config):
    conn = db.connect(":memory:")
    paper_loop._note_shield_boundary(conn, config)
    assert _breaks(conn) == {}
