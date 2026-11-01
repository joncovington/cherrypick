"""The gates-off oracle (`gateoracle`): which gates an arm has on, the arms it builds, attribution of
the bare trigger's trades, the leave-one-out pairing, and the ledger read. Invented ledgers only.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from cherrypick.backtest import gateoracle as go

PAPER = Path(__file__).resolve().parents[2] / "meic" / "src" / "cherrypick" / "meic" / "paper.py"
G = {g.name: g for g in go.MEIC_GATES}


def test_every_listed_reason_and_key_is_spelt_in_meic_paper():
    # The oracle may not import MEIC (the fence), so it spells MEIC's refusal reasons and keys; this
    # holds them to the source. A gate renamed there would otherwise be counted as `path` silently.
    src = PAPER.read_text(encoding="utf-8")
    for g in go.MEIC_GATES:
        assert re.search(rf'["\']{re.escape(g.key)}["\']', src), g.key
        for r in g.reasons:
            assert f'"{r}"' in src, r


def test_a_misspelt_reason_would_fail_the_source_check():
    src = PAPER.read_text(encoding="utf-8")
    assert '"regime_gex_negativ"' not in src  # the check above is a real lookup, not a tautology


@pytest.mark.parametrize(
    ("gate", "params", "on"),
    [
        ("iv-rank", {"min_iv_rank": 0.2}, True),
        ("iv-rank", {"min_iv_rank": 0.0}, False),
        ("vix", {"regime_vix_pause_threshold": 25}, True),
        ("vix", {"regime_vix_pause_threshold": 999}, False),
        ("vix1d", {}, True),  # the code's default 1.30 is on
        ("vix1d", {"regime_vix1d_ratio_pause_threshold": 999}, False),
        ("atr", {"regime_atr_pause_threshold_pct": 0.012}, True),
        ("atr", {"regime_atr_pause_threshold_pct": None}, False),
        ("gex-negative", {}, True),  # default True
        ("gex-negative", {"regime_gex_block_negative": False}, False),
        ("gex-positive", {}, False),
        ("gex-positive", {"regime_gex_require_positive": True}, True),
        ("gex-flip", {"regime_gex_min_flip_distance_pct": 0.3}, True),
        ("gex-flip", {}, False),
        ("stabilization", {"stabilization_entry": True}, True),
        ("stabilization", {}, False),
        ("stabilization-placebo", {"stabilization_placebo_rate": 0.4}, True),
        ("stabilization-placebo", {"stabilization_placebo_rate": 0.0}, False),
    ],
)
def test_whether_a_gate_is_on(gate, params, on):
    assert go.is_on(G[gate], params) is on


def test_the_oracle_builds_open_and_one_leave_out_per_active_gate_only():
    profile = {"_note": "x", "delta_target": 0.16, "regime_vix_pause_threshold": 25}
    params = {**profile, "regime_gex_block_negative": True, "regime_vix1d_ratio_pause_threshold": 999}
    arms = go.oracle_profiles("pm-200", profile, params)
    assert set(arms) == {"pm-200-open", "pm-200-no-vix", "pm-200-no-gex-negative"}
    assert arms["pm-200-open"]["regime_vix_pause_threshold"] == 999
    assert arms["pm-200-open"]["regime_gex_block_negative"] is False
    assert arms["pm-200-no-vix"]["regime_vix_pause_threshold"] == 999
    assert "regime_gex_block_negative" not in arms["pm-200-no-vix"]  # the other gate stays as inherited
    assert all("_note" not in a for a in arms.values())
    assert all(a["delta_target"] == 0.16 for a in arms.values())


def test_attribution_names_the_gate_the_path_or_a_shared_fill():
    fills = [
        {"session": "2024-01-02", "ts": "10:00", "net": -300.0},
        {"session": "2024-01-02", "ts": "11:00", "net": 120.0},
        {"session": "2024-01-02", "ts": "12:00", "net": 80.0},
        {"session": "2024-01-03", "ts": "10:00", "net": 50.0},
        {"session": "2024-01-03", "ts": "13:00", "net": 10.0},
    ]
    gated = {
        ("2024-01-02", "10:00"): {"outcome": "gate_blocked", "block_detail": "regime_gex_negative"},
        ("2024-01-02", "11:00"): {"outcome": "filled", "block_detail": None},
        ("2024-01-02", "12:00"): {"outcome": "cadence_blocked", "block_detail": "entry_spacing_wait"},
        ("2024-01-03", "10:00"): {"outcome": "gate_blocked", "block_detail": "regime_vix_elevated"},
    }
    by = [r["by"] for r in go.attribute(fills, gated)]
    assert by == ["gex-negative", go.BOTH, go.PATH, "vix", go.NO_ROW]


def test_leave_one_out_is_paired_per_session_with_missing_sessions_at_zero():
    by_session = {
        "a": {"d1": 100.0, "d2": -50.0},
        "a-open": {"d1": 40.0, "d2": -90.0, "d3": 30.0},
        "a-no-vix": {"d1": 100.0, "d2": -150.0, "d3": -20.0},
    }
    attributed = [{"net": -100.0, "by": "vix"}, {"net": 20.0, "by": go.PATH}]
    s = go.summary("a", ["vix"], by_session, ["d1", "d2", "d3"], attributed)
    loo = s["gates"]["vix"]["leave_one_out"]
    assert loo["sessions"] == 3 and loo["sessions_differing"] == 2
    assert loo["mean"] == pytest.approx((0.0 - 100.0 - 20.0) / 3)  # removing the gate loses: it helps
    assert s["gates"]["vix"]["blocked_trades"] == 1 and s["gates"]["vix"]["blocked_net"] == -100.0
    assert s["open_vs_base"]["mean"] == pytest.approx((-60.0 - 40.0 + 30.0) / 3)
    assert s["unattributed"][go.PATH] == {"trades": 1, "net": 20.0}
    flat = go.summary("a", ["vix"], {"a": {}, "a-no-vix": {}}, ["d1", "d2"], [])
    assert flat["gates"]["vix"]["leave_one_out"]["t"] is None  # no spread: no t, not a division by zero


def _ledger(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE ic_trades (arm TEXT, ic_order_id TEXT, pnl REAL, fees REAL, exit_time TEXT);"
        "CREATE TABLE entry_attempts (arm TEXT, trade_date TEXT, ts TEXT, outcome TEXT, block_detail TEXT,"
        " ic_order_id TEXT);"
    )
    conn.executemany(
        "INSERT INTO ic_trades VALUES (?, ?, ?, ?, ?)",
        [
            ("a", "o1", 200.0, 10.0, "15:00"),
            ("a-open", "o2", -400.0, 10.0, "15:00"),
            ("a-open", "o3", 90.0, 10.0, None),  # still open at the read: not counted
        ],
    )
    conn.executemany(
        "INSERT INTO entry_attempts VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("a", "2024-01-02", "10:00", "gate_blocked", "regime_gex_negative", None),
            ("a", "2024-01-02", "11:00", "filled", None, "o1"),
            ("a-open", "2024-01-02", "10:00", "filled", None, "o2"),
            ("a-open", "2024-01-02", "12:00", "filled", None, "o3"),
            ("control", "2024-01-02", "10:00", "filled", None, "o9"),
        ],
    )
    conn.commit()
    conn.close()
    return path


def test_the_ledger_read_takes_closed_trades_net_of_fees(tmp_path):
    net, fills, ticks = go.read_session(_ledger(tmp_path / "2024-01-02.db"), ("a", "a-open"))
    assert net == {"a": 190.0, "a-open": -410.0}
    assert fills == [{"session": "2024-01-02", "ts": "10:00", "net": -410.0}]
    assert set(ticks) == {("2024-01-02", "10:00"), ("2024-01-02", "11:00")}
    assert [r["by"] for r in go.attribute(fills, ticks)] == ["gex-negative"]


def _script():
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[3] / "scripts" / "meic_gate_oracle.py"
    spec = importlib.util.spec_from_file_location("meic_gate_oracle", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["meic_gate_oracle"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_script_adds_the_oracle_arms_and_refuses_a_clash_or_an_ungated_arm():
    s = _script()
    reg = {"profiles": {"pm-200": {"regime_vix_pause_threshold": 25}, "other": {}}}
    base = {"regime_gex_block_negative": False, "regime_vix1d_ratio_pause_threshold": 999}
    out, on = s.registry_with_oracle(reg, base, "pm-200")
    assert on == ["vix"]
    assert set(out["profiles"]) == {"pm-200", "other", "pm-200-open", "pm-200-no-vix"}
    with pytest.raises(SystemExit, match="already declares"):
        s.registry_with_oracle(out, base, "pm-200")
    with pytest.raises(SystemExit, match="none of the listed gates"):
        s.registry_with_oracle(reg, base, "other")
    with pytest.raises(SystemExit, match="no 'nope' arm"):
        s.registry_with_oracle(reg, base, "nope")
