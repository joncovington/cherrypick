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
        "era": analytics.CURRENT_ERA,
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
    _insert(conn, ic_order_id="2", era=analytics.CURRENT_ERA, pnl=30.0, fees=3.0)
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


def test_stop_counterfactual_runs_against_the_substrate_stream(conn):
    _insert(
        conn,
        ic_order_id="1",
        risk_profile="control",
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
    assert out["arm"] == "control"


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


# --------------------------------------------------------------------------- session denominators


def _tag(conn, oid, date, value, bucket="normal"):
    _insert(
        conn,
        ic_order_id=oid,
        trade_date=date,
        entry_vol_realized_bucket=bucket,
        entry_vol_realized_value=value,
    )


def test_regime_coverage_counts_sessions_not_just_rows(conn):
    """Rows are not draws. Under uncapped sampling this book takes hundreds of entries per session,
    so a row count says nothing about how many independent observations are behind a dimension."""
    for i in range(5):
        _tag(conn, f"a{i}", "2026-08-07", 0.0120 + i * 0.00001)
    for i in range(5):
        _tag(conn, f"b{i}", "2026-08-10", 0.0135 + i * 0.00001)

    dim = analytics.regime_coverage(conn)["dimensions"]["vol_realized"]
    assert dim["tagged"] == 10
    assert dim["sessions"] == 2


def test_regime_coverage_collapses_effective_n_for_a_daily_scale_dimension(conn):
    """5-day ATR is recomputed per tick but only really moves between days, so its effective n is
    the session count. This is the accounting that made 967 rows of vol_realized read as n=2."""
    for i in range(20):
        _tag(conn, f"a{i}", "2026-08-07", 0.0120 + i * 0.0000001)  # ~flat within the day
    for i in range(20):
        _tag(conn, f"b{i}", "2026-08-10", 0.0135 + i * 0.0000001)

    dim = analytics.regime_coverage(conn)["dimensions"]["vol_realized"]
    assert dim["daily_scale"] is True
    assert dim["effective_n"] == 2 and dim["tagged"] == 40


def test_regime_coverage_keeps_row_count_for_an_intraday_dimension(conn):
    """A dimension that genuinely moves within the session keeps its rows as the effective n —
    daily_scale is measured from the data, never declared per dimension."""
    for i in range(20):
        _tag(conn, f"a{i}", "2026-08-07", 0.010 + i * 0.0005)  # wide intraday swing
    for i in range(20):
        _tag(conn, f"b{i}", "2026-08-10", 0.011 + i * 0.0005)

    dim = analytics.regime_coverage(conn)["dimensions"]["vol_realized"]
    assert dim["daily_scale"] is False
    assert dim["effective_n"] == 40


def test_regime_coverage_never_claims_daily_scale_from_one_session(conn):
    """With a single session there is no between-session movement to compare against; claiming
    daily-scale off that would be reading a zero denominator as evidence."""
    for i in range(10):
        _tag(conn, f"a{i}", "2026-08-07", 0.0120)

    dim = analytics.regime_coverage(conn)["dimensions"]["vol_realized"]
    assert dim["sessions"] == 1
    assert dim["daily_scale"] is False


def test_underpowered_is_keyed_on_sessions_not_rows(conn):
    """Rows inside one session share that session's market, so a threshold cut on two days of
    intraday-varying data is still a cut on two days — degenerate and underpowered are different
    findings calling for opposite responses (re-cut the float vs collect more sessions)."""
    for i in range(500):
        _tag(conn, f"a{i}", "2026-08-07", 0.010 + i * 0.00001)

    dim = analytics.regime_coverage(conn)["dimensions"]["vol_realized"]
    assert dim["tagged"] == 500 and dim["effective_n"] == 500
    assert dim["underpowered"] is True  # 1 session, despite 500 rows


def test_underpowered_clears_once_enough_sessions_accumulate(conn):
    for d in range(analytics.MIN_EFFECTIVE_N):
        _tag(conn, f"a{d}", f"2026-08-{d + 1:02d}", 0.010 + d * 0.001)

    dim = analytics.regime_coverage(conn)["dimensions"]["vol_realized"]
    assert dim["sessions"] == analytics.MIN_EFFECTIVE_N
    assert dim["underpowered"] is False


def test_by_regime_reports_sessions_per_bucket(conn):
    """A bucket of 600 rows drawn from one day is one draw dressed as six hundred, and the trades
    count alone cannot show that."""
    _tag(conn, "a1", "2026-08-07", 0.012, bucket="normal")
    _tag(conn, "a2", "2026-08-07", 0.013, bucket="normal")
    _tag(conn, "b1", "2026-08-10", 0.014, bucket="normal")
    _tag(conn, "c1", "2026-08-07", 0.020, bucket="high")

    rows = {r["bucket"]: r for r in analytics.by_regime(conn, "vol_realized")}
    assert rows["normal"]["trades"] == 3 and rows["normal"]["sessions"] == 2
    assert rows["high"]["trades"] == 1 and rows["high"]["sessions"] == 1


def test_min_effective_n_matches_the_experiment_session_bar():
    """Both constants answer 'how many sessions before this book may draw a conclusion'. They are
    kept equal deliberately — two constants for one question is how they start disagreeing — and
    analytics.py cannot import experiment.py (it pulls in paths/config; this is a pure read layer),
    so the coupling is pinned here instead."""
    from cherrypick.meic import experiment

    assert analytics.MIN_EFFECTIVE_N == experiment.MIN_SESSIONS_FOR_INTERVAL


def test_daily_rollup_fills_the_columns_nothing_ever_wrote(conn):
    """`daily_summary` declared fourteen numeric columns and no writer set any of them — its two
    writers touch only session_init_at, ai_day_summary and closing_nlv, and both are called from the
    agent-driven /eod-report, which the automated paper loop does not run. Paper held zero rows,
    live held eight rows of zeros, and the console rendered those zeros as a card.

    Pinned against this module's OWN definitions rather than recomputed here: `_RESOLVED` decides
    what counts, and a win is a resolved trade whose P&L clears its own fees. A gross-positive,
    fee-negative trade is a LOSS, and that is asserted explicitly because it is the case a
    hand-rolled roll-up gets wrong.
    """
    # Two clear wins, one gross-positive/fee-negative (a loss), one stop, one cancelled.
    _insert(conn, ic_order_id="W1", trade_date="2026-08-11", status="expired", pnl=100.0, fees=6.89)
    _insert(conn, ic_order_id="W2", trade_date="2026-08-11", status="expired", pnl=80.0, fees=6.89)
    _insert(conn, ic_order_id="F1", trade_date="2026-08-11", status="expired", pnl=3.0, fees=6.89)
    _insert(conn, ic_order_id="S1", trade_date="2026-08-11", status="stopped", pnl=-50.0, fees=6.89)
    _insert(conn, ic_order_id="C1", trade_date="2026-08-11", status="cancelled", pnl=None, fees=None)

    roll = analytics.daily_rollup(conn, "2026-08-11", era="ALL")

    assert roll["total_entries"] == 5, "a placed-and-cancelled entry still happened"
    assert roll["entries_filled"] == 4, "cancelled rows never reach _RESOLVED"
    assert roll["entries_cancelled"] == 1
    assert roll["entries_stopped"] == 1
    assert roll["entries_expired"] == 3
    assert roll["gross_pnl"] == pytest.approx(133.0)
    assert roll["fees"] == pytest.approx(27.56)
    assert roll["net_pnl"] == pytest.approx(105.44)
    # W1 and W2 only: F1 is gross-positive and fee-negative.
    assert roll["win_count"] == 2
    assert roll["win_rate_pct"] == pytest.approx(50.0)


def test_daily_rollup_is_era_scoped_like_every_other_reader(conn):
    """A roll-up written during one sampling era must never be a blend of two — the pre-cutover
    ledger had an order-of-magnitude different selection intensity."""
    _insert(
        conn,
        ic_order_id="A",
        trade_date="2026-08-11",
        status="expired",
        pnl=10.0,
        fees=1.0,
        era=analytics.CURRENT_ERA,
    )
    _insert(conn, ic_order_id="B", trade_date="2026-08-11", status="expired", pnl=999.0, fees=1.0, era="book")

    assert analytics.daily_rollup(conn, "2026-08-11", era=analytics.CURRENT_ERA)[
        "gross_pnl"
    ] == pytest.approx(10.0)
    assert analytics.daily_rollup(conn, "2026-08-11", era="ALL")["gross_pnl"] == pytest.approx(1009.0)


# --------------------------------------------------------------------------- the stop curve (#12)


def test_stop_grid_scores_the_whole_curve_from_one_recorded_path(conn):
    """One session yields the SHAPE of the stop curve, not one sampled point — which is the whole
    argument for deriving it read-side rather than running a 15-session bounded experiment per
    threshold."""
    _insert(
        conn,
        ic_order_id="1",
        risk_profile="control",
        status="expired",
        put_max_cost=1.71,
        call_max_cost=0.2,  # 1.71 / 1.8 = exactly 0.95x
        put_settle_value=2.0,
        call_settle_value=0.0,
        pnl=-20.0,
        fees=0.0,
    )
    out = analytics.stop_grid(conn)
    assert out["arm"] == "control" and out["trades"] == 1
    assert [p["ratio"] for p in out["curve"]] == list(analytics_grid_ratios())

    by_ratio = {p["ratio"]: p for p in out["curve"]}
    # Nothing censored: `open` holds to settlement, so every threshold is answerable.
    assert all(p["censored"] == 0 for p in out["curve"])
    # A tighter threshold stops more often than a looser one over the same path.
    assert by_ratio[0.85]["stop_out_rate"] >= by_ratio[1.25]["stop_out_rate"]
    assert by_ratio[1.25]["stop_out_rate"] == 0.0  # 1.71 never reached 1.25 x 1.8 = 2.25
    # Capital is known on every row, so the curve is reportable on max risk.
    assert by_ratio[0.95]["on_max_risk"] is not None


def analytics_grid_ratios():
    from cherrypick.meic import stop_policies

    return stop_policies.GRID_RATIOS


def test_stop_grid_reports_censored_points_instead_of_folding_them_into_totals(conn):
    """On an arm that really stops, the path above where it stopped was never observed. Those
    points must be counted as censored, never summed as "did not fire"."""
    _insert(
        conn,
        ic_order_id="1",
        risk_profile="width-5",
        status="stopped",
        put_max_cost=1.8,
        call_max_cost=0.1,  # stopped at 1.0x net credit
        put_settle_value=0.0,
        call_settle_value=0.0,
        pnl=-90.0,
        fees=4.49,
    )
    out = analytics.stop_grid(conn, arm="width-5")
    by_ratio = {p["ratio"]: p for p in out["curve"]}
    assert by_ratio[0.95]["censored"] == 0 and by_ratio[0.95]["derivable"] == 1
    for ratio in (1.05, 1.10, 1.15, 1.20, 1.25):
        assert by_ratio[ratio]["censored"] == 1, ratio
        assert by_ratio[ratio]["derivable"] == 0, ratio
        assert by_ratio[ratio]["net_pnl"] == 0.0  # nothing summed into it


def test_stop_session_rollup_names_what_the_stop_cost_per_session(conn):
    _insert(
        conn,
        ic_order_id="1",
        risk_profile="control",
        status="expired",
        trade_date="2026-08-13",
        put_max_cost=1.8,
        call_max_cost=0.1,
        put_settle_value=0.0,
        call_settle_value=0.0,
        pnl=180.0,
        fees=0.0,
    )
    _insert(
        conn,
        ic_order_id="2",
        risk_profile="control",
        status="expired",
        trade_date="2026-08-14",
        put_max_cost=0.2,
        call_max_cost=0.2,
        put_settle_value=0.0,
        call_settle_value=0.0,
        pnl=180.0,
        fees=0.0,
    )
    rows = analytics.stop_session_rollup(conn)
    assert [r["session"] for r in rows] == ["2026-08-13", "2026-08-14"]
    for r in rows:
        # `open` never stops, so realized and shadow are the same book and the stop cost nothing.
        assert r["stop_cost"] == 0.0
        assert r["shadow_pnl_without_stop"] == r["realized_pnl_with_stop"]


# --------------------------------------------------------------------------- control_fired (#6)


def test_control_fired_tags_the_sessions_control_sat_out(conn):
    """The asymmetry proposal #6 is about: control's stricter iv_rank floor can leave it dark while
    the looser arms trade, so those sessions have no same-session baseline."""
    # 08-13: control traded alongside width-5.
    _insert(conn, ic_order_id="1", risk_profile="control", trade_date="2026-08-13")
    _insert(conn, ic_order_id="2", risk_profile="width-5", trade_date="2026-08-13")
    # 08-14: control gated out entirely; width-5 and open still traded.
    _insert(conn, ic_order_id="3", risk_profile="width-5", trade_date="2026-08-14")
    _insert(conn, ic_order_id="4", risk_profile="open", trade_date="2026-08-14")

    out = analytics.control_fired(conn)
    assert out["n_sessions"] == 2
    assert out["n_control_fired"] == 1 and out["n_control_dark"] == 1

    by_session = {s["session"]: s for s in out["sessions"]}
    assert by_session["2026-08-13"]["control_fired"] is True
    assert by_session["2026-08-13"]["unbaselined_arms"] == []
    assert by_session["2026-08-14"]["control_fired"] is False
    assert by_session["2026-08-14"]["control_fills"] == 0
    assert by_session["2026-08-14"]["unbaselined_arms"] == ["open", "width-5"]


def test_control_fired_buckets_rather_than_excludes(conn):
    """A dark session is a real session with a real result. It has to come back in the list so a
    caller can group on it — dropping it would decide the answer by choosing the sample."""
    _insert(conn, ic_order_id="1", risk_profile="width-5", trade_date="2026-08-14")
    out = analytics.control_fired(conn)
    assert out["n_sessions"] == 1, "the dark session is still reported, not filtered away"
    assert out["sessions"][0]["by_arm"] == {"width-5": 1}


# --------------------------------------------------------------------------- the GEX gate counterfactual


def test_gex_gate_counterfactual_splits_the_book_on_the_recorded_flag(conn):
    """`regime_gex_block_negative` refuses an entry when net GEX is confirmed negative, so the
    permissive book's own rows carrying gex_positive_at_entry=0 ARE the entries it would have
    refused. Same book, same tape, same stop policy: no cross-arm confound to argue about."""
    _insert(conn, ic_order_id="1", gex_positive_at_entry=0, pnl=100.0, fees=10.0)
    _insert(conn, ic_order_id="2", gex_positive_at_entry=0, pnl=-500.0, fees=10.0)
    _insert(conn, ic_order_id="3", gex_positive_at_entry=1, pnl=200.0, fees=10.0)

    out = analytics.gex_gate_counterfactual(conn)

    assert out["refused_by_the_gate"]["trades"] == 2
    assert out["refused_by_the_gate"]["net_pnl"] == -420.0
    assert out["allowed_by_the_gate"]["net_pnl"] == 190.0


def test_gex_gate_counterfactual_reports_an_untagged_denominator(conn):
    """A row with no recorded flag belongs to neither side. Folding it into 'allowed' would make the
    gate look better every time the tag was missing."""
    _insert(conn, ic_order_id="1", gex_positive_at_entry=None, pnl=100.0, fees=10.0)
    out = analytics.gex_gate_counterfactual(conn)
    assert out["untagged_trades"] == 1
    assert out["refused_by_the_gate"]["trades"] == 0
    assert out["allowed_by_the_gate"]["trades"] == 0


def test_gex_gate_counterfactual_isolates_the_session_the_result_rests_on(conn):
    """The gate is insurance, and insurance is judged on its tail rather than its mean. On the live
    ledger one session (2026-08-20, -129,344.54 across 306 refused entries) is the entire pooled
    result: without it the refused entries netted +123,618.21. A caller printing only the total
    would read 'the gate roughly breaks even' and miss that its whole case is one day."""
    _insert(conn, ic_order_id="1", trade_date="2026-08-18", gex_positive_at_entry=0, pnl=1000.0, fees=10.0)
    _insert(conn, ic_order_id="2", trade_date="2026-08-20", gex_positive_at_entry=0, pnl=-5000.0, fees=10.0)

    out = analytics.gex_gate_counterfactual(conn)

    assert out["refused_by_the_gate"]["net_pnl"] == -4020.0
    assert out["worst_session"]["session"] == "2026-08-20"
    assert out["refused_excluding_worst_session"]["net_pnl"] == 990.0, "the sign must flip"
    assert [s["session"] for s in out["by_session"]] == ["2026-08-18", "2026-08-20"]


def test_gex_gate_counterfactual_reads_across_the_open_to_control_rename(conn):
    """`open` was renamed `control` at the 2026-08-21 cutover and the registry records them as one
    continuous stream. Reading only the current era would drop two thirds of the evidence."""
    _insert(
        conn,
        ic_order_id="1",
        risk_profile="open",
        era="sample",
        gex_positive_at_entry=0,
        pnl=100.0,
        fees=10.0,
    )
    _insert(
        conn,
        ic_order_id="2",
        risk_profile="control",
        era=analytics.CURRENT_ERA,
        gex_positive_at_entry=0,
        pnl=100.0,
        fees=10.0,
    )

    out = analytics.gex_gate_counterfactual(conn)

    assert out["refused_by_the_gate"]["trades"] == 2
    assert out["eras_present"] == ["advisor", "sample"]
    assert (
        analytics.gex_gate_counterfactual(conn, era=analytics.CURRENT_ERA)["refused_by_the_gate"]["trades"]
        == 1
    )


# --------------------------------------------------------------------------- the settlement audit


def test_settlement_audit_reproduces_a_plain_expiry_from_the_convention(conn):
    """Requested by the advisor on 08-17, 08-18, 08-19, 08-20 and 08-21 and never run. The concern
    was that this module's rate of exact full-credit capture might mean the marking convention is
    wrong — in which case every arm comparison resting on it is wrong the same way."""
    # 7480/7520 shorts, 10-wide, settling at 7526 → call 6 ITM, put worthless.
    _insert(
        conn,
        ic_order_id="1",
        exit_reason="expired_settlement",
        settle_underlying=7526.0,
        put_strike=7480.0,
        call_strike=7520.0,
        wing_width=10.0,
        net_credit=0.58,
        put_credit=0.30,
        call_credit=0.28,
        pnl=(0.58 - 6.0) * 100,
    )

    out = analytics.settlement_audit(conn)

    assert out["reproduced"] == 1
    assert out["mismatched"] == []


def test_settlement_audit_names_a_row_that_does_not_match_its_own_convention(conn):
    _insert(
        conn,
        ic_order_id="1",
        exit_reason="expired_settlement",
        settle_underlying=7526.0,
        put_strike=7480.0,
        call_strike=7520.0,
        wing_width=10.0,
        net_credit=0.58,
        put_credit=0.30,
        call_credit=0.28,
        pnl=58.0,
    )  # booked as full credit

    out = analytics.settlement_audit(conn)

    assert out["reproduced"] == 0
    assert out["mismatched"][0]["recorded_pnl"] == 58.0
    assert out["mismatched"][0]["modelled_pnl"] == pytest.approx(-542.0)


def test_settlement_audit_charges_a_stopped_side_its_stop_not_its_intrinsic(conn):
    """A partially stopped fill is the larger population (3,412 rows on the live ledger) and it is
    scored differently: the stopped side paid its stop, the survivor pays settlement."""
    _insert(
        conn,
        ic_order_id="1",
        exit_reason="stopped+expired_settlement",
        settle_underlying=7526.0,
        put_strike=7480.0,
        call_strike=7520.0,
        wing_width=10.0,
        net_credit=0.58,
        put_credit=0.30,
        call_credit=0.28,
        put_stop_cost=0.90,
        pnl=(0.30 - 0.90) * 100 + (0.28 - 6.0) * 100,
    )

    assert analytics.settlement_audit(conn)["mismatched"] == []


def test_settlement_audit_flags_a_side_that_settled_with_no_price(conn):
    """The defect this audit actually found: `_settlement_value` scores a None underlying at zero
    intrinsic, which is FULL CREDIT — the most favorable outcome available, on exactly the fills
    whose outcome nobody could see. 90 such rows on the live ledger, all pre-2026-08-04."""
    _insert(
        conn,
        ic_order_id="1",
        exit_reason="expired_settlement",
        settle_underlying=None,
        net_credit=0.58,
        put_credit=0.30,
        call_credit=0.28,
        pnl=58.0,
    )

    out = analytics.settlement_audit(conn)

    assert out["unpriced_settlements"] == 1
    assert out["reproduced"] == 0, "an unpriced row must not be counted as reproduced"


def test_settlement_audit_does_not_cry_wolf_on_a_force_close_or_a_double_stop(conn):
    """Neither needs a settlement price. A force-closed position was bought back at quotes (the
    non-cash-settled path), and a fill with both sides stopped had nothing left to settle."""
    _insert(
        conn, ic_order_id="1", exit_reason="force_close_physical_settlement", settle_underlying=None, pnl=10.0
    )
    _insert(
        conn,
        ic_order_id="2",
        exit_reason="stopped+expired_settlement",
        settle_underlying=None,
        put_stop_cost=0.5,
        call_stop_cost=0.5,
        pnl=-40.0,
    )

    assert analytics.settlement_audit(conn)["unpriced_settlements"] == 0


def test_settlement_audit_refuses_two_settlement_prices_on_one_session(conn):
    """Every fill on one (session, symbol) shares an expiration and a settlement. More than one
    price means the loop settled across iterations at drifting spot, and no arm comparison on that
    session is sound — so this is reported before anything else is worth reading."""
    _insert(conn, ic_order_id="1", exit_reason="expired_settlement", settle_underlying=7526.0)
    _insert(conn, ic_order_id="2", exit_reason="expired_settlement", settle_underlying=7527.5)

    out = analytics.settlement_audit(conn)

    assert out["one_price_per_session"] is False
    assert out["sessions_with_multiple_prices"]["2026-08-07/SPX"] == [7526.0, 7527.5]


def test_settlement_audit_bounds_how_much_the_answer_depends_on_the_price(conn):
    """There is no official settlement print stored to compare against, so the useful question is
    not 'is it exact' but 'how much could it matter'. On the live ledger 2026-08-20 — the session
    the whole negative-GEX reading rests on — moves $14,300 per point and $1,430 per tenth, against
    a result of -129,344: about 1%."""
    _insert(
        conn,
        ic_order_id="1",
        exit_reason="expired_settlement",
        settle_underlying=7500.0,
        put_strike=7480.0,
        call_strike=7520.0,
        wing_width=10.0,
        quantity=1,
    )

    row = analytics.settlement_audit(conn)["by_session"][0]

    assert row["settle_price"] == 7500.0
    assert row["within_1pt"] == 0  # both shorts are 20 points away
    assert row["pnl_swing_1pt"] == 0.0  # ...so a point of error changes nothing here


def test_headline_reports_open_capital_at_risk(conn):
    """Same formula core.ledgers._meic_closed's _capital() uses for closed trades -- (wing width -
    credit) x multiplier x quantity -- applied to still-open rows instead. Closed trades and
    resolved-but-not-open statuses must not contribute; only 'open'/'partial' do."""
    _insert(
        conn,
        ic_order_id="open-1",
        status="open",
        wing_width=10.0,
        net_credit=2.0,
        quantity=3,
        exit_reason=None,
    )
    _insert(
        conn,
        ic_order_id="open-2",
        status="partial",
        wing_width=5.0,
        net_credit=1.0,
        quantity=1,
        exit_reason=None,
    )
    # A closed trade with a much larger capital figure -- if this leaked in, the test would pass
    # for the wrong reason (a huge number that happens to still be "at risk of being wrong").
    _insert(conn, ic_order_id="closed-1", status="expired", wing_width=50.0, net_credit=1.0, quantity=10)

    out = analytics.headline(conn)

    # open-1: (10 - 2) * 100 * 3 = 2400.  open-2: (5 - 1) * 100 * 1 = 400.  Total 2800.
    assert out["open_capital_at_risk"] == 2800.0


def test_headline_open_capital_at_risk_is_zero_with_nothing_open(conn):
    _insert(conn, ic_order_id="closed-1", status="expired")

    assert analytics.headline(conn)["open_capital_at_risk"] == 0.0
