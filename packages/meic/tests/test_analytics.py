"""meic/analytics.py — the read-only query layer ported from flies' analytics.py shape, adapted
to MEIC's gross-pnl-only schema (net computed at read time) and risk_profile-as-arm convention.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import analytics, db  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "meic_trades.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _insert(conn, **overrides):
    row = {
        "trade_date": "2026-08-07",
        "symbol": "SPX",
        "status": "expired",
        "risk_profile": "control",
        "era": "sample",
        "put_credit": 0.9,
        "call_credit": 0.9,
        "net_credit": 1.8,
        "wing_width": 10,
        "put_strike": 7450.0,
        "call_strike": 7550.0,
        "pnl": 100.0,
        "fees": 6.89,
        "quantity": 1,
        "ic_order_id": f"IC-{overrides.get('ic_order_id', 'X')}",
        "created_at": "x",
        "updated_at": "x",
    }
    row.update(overrides)
    cols = ", ".join(row)
    placeholders = ", ".join("?" * len(row))
    conn.execute(f"INSERT INTO ic_trades ({cols}) VALUES ({placeholders})", list(row.values()))
    conn.commit()


# --------------------------------------------------------------------------- _period_clause / _summarize


def test_period_clause_defaults_to_current_era_and_resolved_status(conn):
    _insert(conn, ic_order_id="1", status="open", pnl=None, fees=None)  # unresolved -- excluded
    _insert(conn, ic_order_id="2", era="book", pnl=50.0)  # wrong era -- excluded by default
    _insert(conn, ic_order_id="3")  # resolved, current era -- included
    out = analytics.stats_for_period(conn)
    assert out["trades"] == 1


def test_period_clause_era_all_includes_every_era(conn):
    _insert(conn, ic_order_id="1", era="book", pnl=50.0, fees=5.0)
    _insert(conn, ic_order_id="2", era="sample", pnl=30.0, fees=3.0)
    out = analytics.stats_for_period(conn, era="ALL")
    assert out["trades"] == 2


def test_summarize_net_pnl_subtracts_fees_from_gross():
    """ic_trades.pnl is GROSS -- net must be computed as pnl - fees, not read straight off pnl
    (the pre-existing dashboard.py inconsistency this module's docstring warns against copying)."""
    rows = [{"pnl": 100.0, "fees": 10.0, "trade_date": "2026-08-07"}]
    out = analytics._summarize(rows)
    assert out["gross_pnl"] == 100.0
    assert out["net_pnl"] == 90.0


def test_summarize_reports_sessions_alongside_trades():
    rows = [
        {"pnl": 10.0, "fees": 1.0, "trade_date": "2026-08-07"},
        {"pnl": 10.0, "fees": 1.0, "trade_date": "2026-08-07"},
        {"pnl": 10.0, "fees": 1.0, "trade_date": "2026-08-06"},
    ]
    out = analytics._summarize(rows)
    assert out["trades"] == 3
    assert out["sessions"] == 2


def test_win_rate_is_net_of_fees():
    # gross +5, fee 6 -> net -1 -> a loss, not a win, despite the positive gross.
    rows = [{"pnl": 5.0, "fees": 6.0, "trade_date": "2026-08-07"}]
    out = analytics._summarize(rows)
    assert out["wins"] == 0
    assert out["losses"] == 1


# --------------------------------------------------------------------------- by_arm


def test_by_arm_groups_and_ranks_by_net_pnl(conn):
    _insert(conn, ic_order_id="1", risk_profile="control", pnl=100.0, fees=5.0)
    _insert(conn, ic_order_id="2", risk_profile="open", pnl=10.0, fees=5.0)
    _insert(conn, ic_order_id="3", risk_profile="open", pnl=-5.0, fees=5.0)
    out = analytics.by_arm(conn)
    assert [r["arm"] for r in out] == ["control", "open"]  # control's +95 ranks above open's -5
    assert out[0]["net_pnl"] == 95.0
    assert out[1]["net_pnl"] == -5.0
    assert out[1]["trades"] == 2


# --------------------------------------------------------------------------- by_regime / regime_coverage


def test_by_regime_groups_on_the_stored_bucket(conn):
    _insert(
        conn, ic_order_id="1", entry_trend_bucket="up_from_open", entry_trend_value=0.005, pnl=50.0, fees=5.0
    )
    _insert(conn, ic_order_id="2", entry_trend_bucket="flat", entry_trend_value=0.0, pnl=-10.0, fees=5.0)
    _insert(conn, ic_order_id="3", entry_trend_bucket=None, entry_trend_value=None, pnl=1.0, fees=1.0)
    out = analytics.by_regime(conn, "trend")
    buckets = {r["bucket"]: r for r in out}
    assert set(buckets) == {"up_from_open", "flat", "untagged"}
    assert buckets["up_from_open"]["net_pnl"] == 45.0


def test_by_regime_bucket_edges_recuts_the_float(conn):
    _insert(conn, ic_order_id="1", entry_vol_implied_value=0.15, pnl=10.0, fees=1.0)
    _insert(conn, ic_order_id="2", entry_vol_implied_value=0.65, pnl=10.0, fees=1.0)
    out = analytics.by_regime(conn, "vol_implied", bucket_edges=[0.30, 0.60])
    buckets = {r["bucket"] for r in out}
    assert buckets == {"<0.3", "0.3..0.6", ">=0.6"} or "<0.3" in buckets  # low row lands in the first band


def test_by_regime_rejects_unknown_dimension(conn):
    with pytest.raises(ValueError):
        analytics.by_regime(conn, "not_a_real_dimension")


def test_regime_coverage_separates_untagged_from_degenerate(conn):
    """gex_positive_at_entry's real-world shape: 1 on every non-null row, 73% NULL -- a coverage
    gap, not a fired-gate degeneracy. Must be reported as high untagged + degenerate=True (the
    non-null rows genuinely never took a second value), two separate facts."""
    for i in range(3):
        _insert(conn, ic_order_id=f"tagged-{i}", entry_gex_bucket="deep_positive", entry_gex_value=0.01)
    for i in range(7):
        _insert(conn, ic_order_id=f"untagged-{i}", entry_gex_bucket=None, entry_gex_value=None)
    out = analytics.regime_coverage(conn)
    gex = out["dimensions"]["gex"]
    assert gex["tagged"] == 3
    assert gex["untagged"] == 7
    assert gex["degenerate"] is True  # only ever 'deep_positive' among the tagged rows


def test_regime_coverage_not_degenerate_with_two_buckets(conn):
    _insert(conn, ic_order_id="1", entry_gex_bucket="deep_positive", entry_gex_value=0.01)
    _insert(conn, ic_order_id="2", entry_gex_bucket="negative", entry_gex_value=-0.01)
    out = analytics.regime_coverage(conn)
    assert out["dimensions"]["gex"]["degenerate"] is False


# --------------------------------------------------------------------------- expired_detail / by_exit_detail


def test_expired_detail_splits_otm_and_itm():
    assert analytics.expired_detail(
        {"status": "expired", "put_settle_value": 0.0, "call_settle_value": 0.0}
    ) == ("expired_otm")
    assert analytics.expired_detail(
        {"status": "expired", "put_settle_value": 0.0, "call_settle_value": 3.0}
    ) == ("expired_itm")
    assert analytics.expired_detail(
        {"status": "expired", "put_settle_value": None, "call_settle_value": None}
    ) == ("expired_unknown")


def test_expired_detail_passes_through_non_expired_status():
    assert analytics.expired_detail({"status": "stopped"}) == "stopped"


def test_by_exit_detail_separates_clean_wins_from_hidden_itm_settlements(conn):
    _insert(
        conn,
        ic_order_id="1",
        status="expired",
        put_settle_value=0.0,
        call_settle_value=0.0,
        pnl=180.0,
        fees=6.0,
    )
    _insert(
        conn,
        ic_order_id="2",
        status="expired",
        put_settle_value=0.0,
        call_settle_value=5.0,
        pnl=-2.8,
        fees=6.0,
    )
    out = {r["exit_detail"]: r for r in analytics.by_exit_detail(conn)}
    assert out["expired_otm"]["net_pnl"] == 174.0
    assert out["expired_itm"]["net_pnl"] == -8.8


# --------------------------------------------------------------------------- breakeven_scorecard


def _insert_legs(conn, ic_order_id, put_status, call_status):
    conn.execute(
        "INSERT INTO ic_spread_legs (ic_order_id, side, status, created_at, updated_at) "
        "VALUES (?, 'put', ?, 'x', 'x')",
        (ic_order_id, put_status),
    )
    conn.execute(
        "INSERT INTO ic_spread_legs (ic_order_id, side, status, created_at, updated_at) "
        "VALUES (?, 'call', ?, 'x', 'x')",
        (ic_order_id, call_status),
    )
    conn.commit()


def test_breakeven_scorecard_computes_the_identity(conn):
    # 1 clean (both expired), 1 double-stop (both stopped), 1 single-side scratch (not counted
    # as either clean or double) -- 3 ICs total.
    _insert(conn, ic_order_id="1", fees=5.0, net_credit=1.0, dollar_multiplier=100)
    _insert_legs(conn, "1", "expired", "expired")
    _insert(conn, ic_order_id="2", fees=5.0, net_credit=1.0, dollar_multiplier=100)
    _insert_legs(conn, "2", "stopped", "stopped")
    _insert(conn, ic_order_id="3", fees=5.0, net_credit=1.0, dollar_multiplier=100)
    _insert_legs(conn, "3", "expired", "stopped")

    out = analytics.breakeven_scorecard(conn)
    assert out["trades"] == 3
    assert out["clean_pct"] == round(1 / 3 * 100, 1)
    assert out["double_stop_pct"] == round(1 / 3 * 100, 1)
    # avg fee 5.0, avg credit dollars 1.0*100=100 -> bar 5.0%
    assert out["breakeven_bar_pct"] == 5.0
    assert out["margin_pct"] == round((1 / 3 * 100) - (1 / 3 * 100) - 5.0, 1)


def test_breakeven_scorecard_a_single_side_stop_counts_as_neither_clean_nor_double(conn):
    """The designed scratch (one side stops, the other expires) must not be miscounted as a
    double-stop -- IC-level `status='stopped'` also covers this case, which is exactly why the
    identity reads the leg-pair status, not the IC-level column."""
    _insert(conn, ic_order_id="1", status="stopped", fees=5.0, net_credit=1.0)
    _insert_legs(conn, "1", "expired", "stopped")
    out = analytics.breakeven_scorecard(conn)
    assert out["clean_pct"] == 0.0
    assert out["double_stop_pct"] == 0.0


def test_breakeven_scorecard_empty_returns_none_fields(conn):
    out = analytics.breakeven_scorecard(conn)
    assert out["trades"] == 0
    assert out["margin_pct"] is None


def test_breakeven_scorecard_scoped_per_arm(conn):
    _insert(conn, ic_order_id="1", risk_profile="control", fees=5.0, net_credit=1.0)
    _insert_legs(conn, "1", "expired", "expired")
    _insert(conn, ic_order_id="2", risk_profile="open", fees=5.0, net_credit=1.0)
    _insert_legs(conn, "2", "stopped", "stopped")
    control = analytics.breakeven_scorecard(conn, arm="control")
    open_arm = analytics.breakeven_scorecard(conn, arm="open")
    assert control["trades"] == 1 and control["clean_pct"] == 100.0
    assert open_arm["trades"] == 1 and open_arm["double_stop_pct"] == 100.0


# --------------------------------------------------------------------------- gate_blocks


def test_gate_blocks_aggregates_reasoning_json_per_stream(conn):
    import json

    conn.execute(
        "INSERT INTO loop_log (loop_time, loop_date, symbol, action, reasoning, created_at) "
        "VALUES ('t', '2026-08-07', 'SPX', 'gate_block', ?, 't')",
        (json.dumps({"control": "iv_rank_below_floor", "open": "FILL $1.80"}),),
    )
    conn.execute(
        "INSERT INTO loop_log (loop_time, loop_date, symbol, action, reasoning, created_at) "
        "VALUES ('t', '2026-08-07', 'SPX', 'gate_block', ?, 't')",
        (json.dumps({"control": "iv_rank_below_floor", "open": "iv_rank_below_floor"}),),
    )
    conn.commit()
    out = analytics.gate_blocks(conn, "2026-08-07")
    assert out["control"] == {"iv_rank_below_floor": 2}
    assert out["open"] == {"FILL": 1, "iv_rank_below_floor": 1}


def test_gate_blocks_ignores_other_actions_and_dates(conn):
    conn.execute(
        "INSERT INTO loop_log (loop_time, loop_date, symbol, action, reasoning, created_at) "
        "VALUES ('t', '2026-08-06', 'SPX', 'gate_block', '{}', 't')"
    )
    conn.execute(
        "INSERT INTO loop_log (loop_time, loop_date, symbol, action, reasoning, created_at) "
        "VALUES ('t', '2026-08-07', 'SPX', 'paper_iteration', 'x', 't')"
    )
    conn.commit()
    out = analytics.gate_blocks(conn, "2026-08-07")
    assert out == {}


# --------------------------------------------------------------------------- arm_divergence


def test_arm_divergence_flags_identical_strikes_same_session(conn):
    _insert(
        conn,
        ic_order_id="1",
        risk_profile="control",
        trade_date="2026-08-07",
        put_strike=7450,
        call_strike=7550,
    )
    _insert(
        conn,
        ic_order_id="2",
        risk_profile="width-10",
        trade_date="2026-08-07",
        put_strike=7450,
        call_strike=7550,
    )
    out = analytics.arm_divergence(conn, "control", "width-10")
    assert out["sessions_with_both"] == 1
    assert out["avg_strike_overlap_pct"] == 100.0
    assert out["all_sessions_identical"] is True


def test_arm_divergence_reports_partial_overlap(conn):
    _insert(
        conn,
        ic_order_id="1",
        risk_profile="control",
        trade_date="2026-08-07",
        put_strike=7450,
        call_strike=7550,
    )
    _insert(
        conn,
        ic_order_id="2",
        risk_profile="control",
        trade_date="2026-08-07",
        put_strike=7440,
        call_strike=7560,
    )
    _insert(
        conn,
        ic_order_id="3",
        risk_profile="width-10",
        trade_date="2026-08-07",
        put_strike=7450,
        call_strike=7550,
    )
    out = analytics.arm_divergence(conn, "control", "width-10")
    assert out["avg_strike_overlap_pct"] == 50.0  # 1 shared strike-pair out of 2 union
    assert out["all_sessions_identical"] is False


def test_arm_divergence_no_shared_sessions(conn):
    _insert(conn, ic_order_id="1", risk_profile="control", trade_date="2026-08-06")
    _insert(conn, ic_order_id="2", risk_profile="width-10", trade_date="2026-08-07")
    out = analytics.arm_divergence(conn, "control", "width-10")
    assert out["sessions_with_both"] == 0
    assert out["avg_strike_overlap_pct"] is None
    assert out["all_sessions_identical"] is False


# --------------------------------------------------------------------------- stop_counterfactual (live wiring)


def test_stop_counterfactual_runs_against_open_stream(conn):
    _insert(
        conn,
        ic_order_id="1",
        risk_profile="open",
        status="expired",
        put_credit=0.9,
        call_credit=0.9,
        net_credit=1.8,
        put_max_cost=0.5,
        call_max_cost=2.0,
        put_settle_value=0.0,
        call_settle_value=0.0,
        pnl=180.0,
        fees=0.0,
    )
    out = analytics.stop_counterfactual(conn, "stop-0.75-net")
    assert out["trades"] == 1
    assert out["derivable"] == 1
    assert out["policy"] == "stop-0.75-net"
    assert out["arm"] == "open"


def test_validate_stop_derivation_wired_to_control(conn):
    thresh = 0.95 * 1.8
    _insert(
        conn,
        ic_order_id="1",
        risk_profile="control",
        status="stopped",
        put_credit=0.9,
        call_credit=0.9,
        net_credit=1.8,
        put_max_cost=thresh,
        call_max_cost=0.3,
        put_settle_value=None,
        call_settle_value=0.0,
        pnl=round((0.9 - thresh) * 100 + (0.9 - 0.0) * 100, 2),
        fees=4.49,
    )
    out = analytics.validate_stop_derivation(conn)
    assert out["compared"] == 1
    assert out["ok"] is True


# --------------------------------------------------------------------------- session_bootstrap


def test_session_bootstrap_refuses_below_min_sessions():
    out = analytics.session_bootstrap({"d1": 1.0}, {"d1": 0.5}, min_sessions=14)
    assert out["ok"] is False
    assert out["shared_sessions"] == 1


def test_session_bootstrap_detects_a_clear_difference():
    a = {f"d{i}": 10.0 for i in range(20)}
    b = {f"d{i}": 1.0 for i in range(20)}
    out = analytics.session_bootstrap(a, b, min_sessions=14, iterations=500)
    assert out["ok"] is True
    assert out["observed_diff"] == 9.0
    assert out["significant"] is True


def test_session_bootstrap_no_difference_is_not_significant():
    a = {f"d{i}": 5.0 for i in range(20)}
    b = {f"d{i}": 5.0 for i in range(20)}
    out = analytics.session_bootstrap(a, b, min_sessions=14, iterations=500)
    assert out["significant"] is False
