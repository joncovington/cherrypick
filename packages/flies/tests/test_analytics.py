"""Tests for the read-only analytics layer."""

import pytest

from cherrypick.flies import analytics, fly
from cherrypick.flies import db as dbmod


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def position(
    conn,
    position_id,
    *,
    day="2026-07-20",
    arm="gex",
    symbol="SPX",
    kind="fly",
    entry_mode="legged",
    center=6000.0,
    width=5.0,
    net=1.05,
    credit=2.55,
    fees=6.89,
    gross=105.0,
    pnl=98.11,
    status="settled",
    window=None,
    best_debit=None,
    latency=None,
    spot_at_completion=None,
    underlying=6000.0,
    risk_free=1,
    floor_dollars=None,
    regime=None,
):
    dbmod.save_position(
        conn,
        {
            "position_id": position_id,
            "book_id": f"{day}:{arm}:{symbol}",
            "trade_date": day,
            "arm": arm,
            "entry_mode": entry_mode,
            "symbol": symbol,
            "kind": kind,
            "side": "put",
            "center": center,
            "wing_width": width,
            "quantity": 1,
            "net": net,
            "credit": credit,
            "fees": fees,
            "gross_pnl": gross,
            "pnl": pnl,
            "status": status,
            "entry_window": window,
            "best_completing_debit": best_debit,
            "completion_latency_min": latency,
            "spot_at_completion": spot_at_completion,
            "underlying_at_entry": underlying,
            "risk_free": risk_free,
            "floor_dollars": floor_dollars,
            "entry_time": f"{day}T12:00:00",
            # Regime tags, threaded the same way `window` is. Pass e.g.
            # regime={"gex_bucket": "pinning", "gex_concentration": 0.71}; keys are prefixed
            # "entry_" to match what book.regime_columns writes.
            **{f"entry_{k}": v for k, v in (regime or {}).items()},
        },
    )


# --------------------------------------------------------------------------- period stats
def test_summary_nets_gross_against_fees(conn):
    position(conn, "P1", gross=105.0, fees=6.89, pnl=98.11)
    position(conn, "P2", gross=-20.0, fees=6.89, pnl=-26.89)
    stats = analytics.stats_for_period(conn)
    assert stats["trades"] == 2
    assert stats["gross_pnl"] == 85.0
    assert stats["fees"] == 13.78
    assert stats["net_pnl"] == pytest.approx(71.22)
    assert stats["wins"] == 1 and stats["losses"] == 1
    assert stats["win_rate"] == 0.5


def test_open_positions_are_not_counted_as_results(conn):
    """An open credit spread is not a result yet. Counting it would flatter whichever arm happens to
    be holding something when the report runs."""
    position(conn, "P1", status="settled", pnl=98.11)
    position(conn, "P2", status="open", pnl=None, gross=None)
    assert analytics.stats_for_period(conn)["trades"] == 1


def test_fee_drag_is_reported_against_gross(conn):
    position(conn, "P1", gross=100.0, fees=25.0, pnl=75.0)
    assert analytics.stats_for_period(conn)["fee_drag_pct"] == 25.0


def test_date_range_filters(conn):
    position(conn, "P1", day="2026-07-20", pnl=100.0)
    position(conn, "P2", day="2026-07-21", pnl=50.0)
    assert analytics.stats_for_period(conn, "2026-07-21", "2026-07-21")["net_pnl"] == 50.0


# --------------------------------------------------------------------------- the series guarantee
def test_series_sums_to_the_period_total(conn):
    """The consistency guarantee the dashboard relies on: summing any granularity over a range must
    equal stats_for_period for that range. Both share one WHERE clause so this cannot silently drift."""
    for i, day in enumerate(["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-27"]):
        position(conn, f"P{i}", day=day, pnl=10.0 * (i + 1), gross=12.0 * (i + 1))

    total = analytics.stats_for_period(conn)["net_pnl"]
    for granularity in analytics.GRANULARITIES:
        series = analytics.pnl_series(conn, granularity)
        assert sum(b["net_pnl"] for b in series) == pytest.approx(total), granularity


def test_weekly_buckets_start_on_monday(conn):
    """SQLite's %W starts weeks on Sunday, which would split a trading week across two buckets — so
    the bucket key is computed in Python."""
    position(conn, "P1", day="2026-07-20")  # Monday
    position(conn, "P2", day="2026-07-24")  # Friday, same trading week
    series = analytics.pnl_series(conn, "weekly")
    assert len(series) == 1 and series[0]["bucket"] == "2026-07-20"


def test_cumulative_pnl_accumulates(conn):
    position(conn, "P1", day="2026-07-20", pnl=10.0)
    position(conn, "P2", day="2026-07-21", pnl=15.0)
    series = analytics.pnl_series(conn, "daily")
    assert [b["cumulative_pnl"] for b in series] == [10.0, 25.0]


def test_unknown_granularity_is_rejected(conn):
    with pytest.raises(ValueError):
        analytics.pnl_series(conn, "hourly")


# --------------------------------------------------------------------------- breakdowns
def test_by_arm_ranks_by_net(conn):
    position(conn, "P1", arm="gex", pnl=100.0)
    position(conn, "P2", arm="control", pnl=-50.0)
    position(conn, "P3", arm="time_window", pnl=25.0)
    assert [r["arm"] for r in analytics.by_arm(conn)] == ["gex", "time_window", "control"]


# --------------------------------------------------------------------------- symbol scope
# The book moved SPX -> XSP (both eras remain in the ledger, see CLAUDE.md); every rollup that groups
# only by arm/mode/window/date would otherwise silently blend the two symbols' rows under one arm,
# which is exactly the kind of unstated book-mixing the module's honesty rules exist to forbid.
def test_stats_for_period_narrows_to_one_symbol(conn):
    position(conn, "S1", symbol="SPX", pnl=100.0)
    position(conn, "S2", symbol="XSP", pnl=10.0)
    assert analytics.stats_for_period(conn)["net_pnl"] == 110.0
    assert analytics.stats_for_period(conn, symbol="XSP")["net_pnl"] == 10.0
    assert analytics.stats_for_period(conn, symbol="SPX")["net_pnl"] == 100.0
    assert analytics.stats_for_period(conn, symbol="ALL")["net_pnl"] == 110.0


def test_pnl_series_and_daily_pnl_respect_symbol(conn):
    position(conn, "S1", day="2026-07-20", symbol="SPX", pnl=100.0)
    position(conn, "S2", day="2026-07-20", symbol="XSP", pnl=10.0)
    series = analytics.pnl_series(conn, "daily", symbol="XSP")
    assert len(series) == 1 and series[0]["net_pnl"] == 10.0
    daily = analytics.daily_pnl(conn, symbol="SPX")
    assert len(daily) == 1 and daily[0]["net_pnl"] == 100.0


def test_by_arm_symbol_filter_excludes_the_other_symbols_rows(conn):
    """An arm's SPX-era and XSP-era rows must not blend into one comparison row unless ALL is asked
    for -- narrowing by symbol is how the honesty rule that governs arm-vs-arm comparisons extends to
    a book that has changed underlyings mid-study."""
    position(conn, "P1", arm="gex", symbol="SPX", pnl=100.0)
    position(conn, "P2", arm="gex", symbol="XSP", pnl=10.0)
    assert analytics.by_arm(conn)[0]["net_pnl"] == 110.0
    assert analytics.by_arm(conn, symbol="XSP")[0]["net_pnl"] == 10.0
    assert analytics.by_arm(conn, symbol="SPX")[0]["net_pnl"] == 100.0


def test_by_entry_mode_and_window_and_fee_drag_respect_symbol(conn):
    position(conn, "P1", arm="gex", symbol="SPX", entry_mode="legged", window="10:30-11:00", pnl=100.0)
    position(conn, "P2", arm="gex", symbol="XSP", entry_mode="legged", window="10:30-11:00", pnl=10.0)
    assert analytics.by_entry_mode(conn, symbol="XSP")[0]["net_pnl"] == 10.0
    assert analytics.by_entry_window(conn, symbol="XSP")[0]["net_pnl"] == 10.0
    assert analytics.fee_drag(conn, symbol="XSP")[0]["net_pnl"] == 10.0


def test_arm_comparison_exclusions_respect_symbol(conn):
    position(conn, "L1", arm="gex", symbol="XSP", entry_mode="legged", pnl=100.0)
    position(conn, "O1", arm="gex", symbol="XSP", entry_mode="outright", pnl=-90.0)
    position(conn, "O2", arm="gex", symbol="SPX", entry_mode="outright", pnl=-5.0)
    exclusions = analytics.arm_comparison_exclusions(conn, symbol="XSP")
    assert exclusions["trades"] == 1
    assert exclusions["net_pnl"] == -90.0


def test_trade_log_respects_symbol(conn):
    position(conn, "P1", symbol="SPX", pnl=100.0)
    position(conn, "P2", symbol="XSP", pnl=10.0)
    rows = analytics.trade_log(conn, symbol="XSP")
    assert len(rows) == 1 and rows[0]["position_id"] == "P2"


def test_completion_stats_and_trend_respect_symbol(conn):
    position(conn, "F1", symbol="XSP", kind="fly", entry_mode="legged")
    position(conn, "F2", symbol="SPX", kind="short_vertical", entry_mode="legged", best_debit=0.0, credit=1.0)
    stats = analytics.completion_stats(conn, symbol="XSP")
    assert stats["legged_entries"] == 1 and stats["completed"] == 1
    trend = analytics.completion_trend(conn, symbol="SPX")
    assert len(trend) == 1 and trend[0]["legged_entries"] == 1 and trend[0]["completed"] == 0


# --------------------------------------------------------------------------- arm comparison scope
def test_arm_comparison_excludes_entry_modes_only_one_arm_traded(conn):
    """The arms differ by centring/timing/width, never by entry mode. gex was the only arm ever to
    take an outright fly, so counting those made 'gex vs control' partly a legged-vs-outright
    comparison and charged gex with a cost no other arm could incur."""
    position(conn, "L1", arm="gex", entry_mode="legged", pnl=100.0)
    position(conn, "L2", arm="control", entry_mode="legged", pnl=60.0)
    position(conn, "O1", arm="gex", entry_mode="outright", pnl=-90.0)

    by_arm = {r["arm"]: r for r in analytics.by_arm(conn)}
    assert by_arm["gex"]["net_pnl"] == 100.0, "the outright loss must not be charged to gex"
    assert by_arm["gex"]["trades"] == 1
    assert by_arm["control"]["net_pnl"] == 60.0


def test_book_totals_stay_whole_when_the_comparison_is_filtered(conn):
    """Rule 6: a negative result is the finding, not something to remove. The book really did pay for
    those flies, so the headline P&L must still include them even though the ranking does not."""
    position(conn, "L1", arm="gex", entry_mode="legged", pnl=100.0)
    position(conn, "O1", arm="gex", entry_mode="outright", pnl=-90.0)

    assert analytics.stats_for_period(conn)["net_pnl"] == 10.0  # whole book
    assert sum(r["net_pnl"] for r in analytics.by_arm(conn)) == 100.0  # comparison only
    modes = {r["entry_mode"]: r for r in analytics.by_entry_mode(conn)}
    assert set(modes) == {"legged", "outright"}, "entry-mode breakdown still reports both"


def test_exclusions_are_reported_not_silently_dropped(conn):
    """A ranking that sums below the book total with nothing explaining the gap is the failure this
    filter is meant to avoid, not to introduce."""
    position(conn, "L1", arm="gex", entry_mode="legged", pnl=100.0)
    position(conn, "O1", arm="gex", entry_mode="outright", pnl=-90.0)
    position(conn, "O2", arm="gex", entry_mode="outright", pnl=-10.0)

    ex = analytics.arm_comparison_exclusions(conn)
    assert ex["trades"] == 2
    assert ex["net_pnl"] == -100.0
    assert ex["excluded_modes"] == ["outright"]
    assert [r["arm"] for r in ex["by_arm"]] == ["gex"]


def test_unfiltered_arm_view_is_still_reachable(conn):
    position(conn, "L1", arm="gex", entry_mode="legged", pnl=100.0)
    position(conn, "O1", arm="gex", entry_mode="outright", pnl=-90.0)

    unfiltered = {r["arm"]: r for r in analytics.by_arm(conn, entry_modes=None)}
    assert unfiltered["gex"]["net_pnl"] == 10.0 and unfiltered["gex"]["trades"] == 2
    assert analytics.arm_comparison_exclusions(conn, entry_modes=None)["trades"] == 0


def test_fee_drag_inherits_the_comparison_scope(conn):
    """fee_drag is per-arm too, so it must compare like for like or it re-imports the same skew."""
    position(conn, "L1", arm="gex", entry_mode="legged", gross=110.0, fees=10.0, pnl=100.0)
    position(conn, "O1", arm="gex", entry_mode="outright", gross=-83.0, fees=7.0, pnl=-90.0)

    gex = {r["arm"]: r for r in analytics.fee_drag(conn)}["gex"]
    assert gex["trades"] == 1 and gex["fees"] == 10.0


def test_by_entry_window_groups_untagged_separately(conn):
    position(conn, "P1", window="09:45-10:15", pnl=40.0)
    position(conn, "P2", window=None, pnl=10.0)
    rows = {r["window"]: r for r in analytics.by_entry_window(conn)}
    assert rows["09:45-10:15"]["net_pnl"] == 40.0
    assert rows["unwindowed"]["net_pnl"] == 10.0


# --------------------------------------------------------------------------- completion counterfactual
def _completion_refusal(conn, position_id, reason, *, day="2026-07-20", arm="gex", symbol="SPX"):
    dbmod.record_decision(
        conn,
        trade_date=day,
        arm=arm,
        symbol=symbol,
        mode="completion",
        reason=reason,
        accepted=False,
        position_id=position_id,
        when=f"{day}T12:05:00",
    )


def test_counterfactual_separates_never_offered_from_our_own_gates(conn):
    """The distinction the whole counterfactual exists for. These look identical in the P&L and
    call for opposite fixes: one says the market never got there, the others say our gate cost us
    the fly — and the two gates are not interchangeable either."""
    # completed
    position(conn, "P1", kind="fly", credit=2.55, best_debit=1.50, latency=23.0)
    # the market never offered a debit below the credit at all
    position(conn, "P2", kind="short_vertical", credit=2.55, best_debit=2.60)
    # the debit did beat the credit — just not by enough to clear the fee buffer
    position(conn, "P3", kind="short_vertical", credit=2.10, best_debit=2.02)
    _completion_refusal(conn, "P3", "completing_debit_too_high")
    # never priced (e.g. missing quotes all session)
    position(conn, "P4", kind="short_vertical", credit=2.00, best_debit=None)
    # cleared the buffer, but the post-fee floor missed min_floor_dollars
    position(conn, "P5", kind="short_vertical", credit=2.10, best_debit=1.60)
    _completion_refusal(conn, "P5", "floor_below_minimum_after_fees")

    stats = analytics.completion_stats(conn)
    assert stats["legged_entries"] == 5
    assert stats["completed"] == 1
    assert stats["completion_rate"] == 0.2
    assert stats["never_offered"] == 1
    assert stats["buffer_blocked"] == 1
    assert stats["floor_blocked"] == 1
    assert stats["counterfactual_unknown"] == 1


def test_floor_blocked_wins_when_a_position_saw_both_refusals(conn):
    """A position is refused many times as quotes move, so it typically carries buffer refusals from
    earlier in the session AND a floor refusal from its best moment. Reaching the floor gate at all
    means the buffer was already cleared, so the floor is the honest verdict — the opposite reading
    would blame the buffer for a miss the buffer did not cause."""
    position(conn, "P1", kind="short_vertical", credit=2.10, best_debit=1.60)
    _completion_refusal(conn, "P1", "completing_debit_too_high")
    _completion_refusal(conn, "P1", "floor_below_minimum_after_fees")
    _completion_refusal(conn, "P1", "completing_debit_too_high")

    stats = analytics.completion_stats(conn)
    assert stats["floor_blocked"] == 1
    assert stats["buffer_blocked"] == 0


def test_a_miss_the_market_never_offered_is_never_blamed_on_our_gates(conn):
    """best_debit >= credit means no threshold of ours could have helped. Even with a buffer refusal
    journaled, it must stay in never_offered rather than becoming an actionable-looking gate miss."""
    position(conn, "P1", kind="short_vertical", credit=2.55, best_debit=2.60)
    _completion_refusal(conn, "P1", "completing_debit_too_high")

    stats = analytics.completion_stats(conn)
    assert stats["never_offered"] == 1
    assert stats["buffer_blocked"] == 0 and stats["floor_blocked"] == 0


def test_completion_latency_is_summarized(conn):
    position(conn, "P1", kind="fly", latency=23.0, underlying=6000.0, spot_at_completion=6006.0)
    position(conn, "P2", kind="fly", latency=63.0, underlying=6000.0, spot_at_completion=5991.5)
    stats = analytics.completion_stats(conn)
    assert stats["median_latency_min"] == 43.0
    assert stats["min_latency_min"] == 23.0 and stats["max_latency_min"] == 63.0
    assert stats["median_spot_move"] == pytest.approx(7.25)


def test_completion_stats_on_an_empty_book(conn):
    stats = analytics.completion_stats(conn)
    assert stats["legged_entries"] == 0 and stats["completion_rate"] is None


def test_completion_trend_is_per_session(conn):
    """Rule 4's number on a date axis: one row per session, outrights excluded (they never leg in)."""
    position(conn, "P1", day="2026-07-20", kind="fly")
    position(conn, "P2", day="2026-07-20", kind="short_vertical")
    position(conn, "P3", day="2026-07-21", kind="fly")
    position(conn, "P4", day="2026-07-21", kind="fly", entry_mode="outright")  # not a leg-in
    trend = analytics.completion_trend(conn)
    assert [t["day"] for t in trend] == ["2026-07-20", "2026-07-21"]
    assert trend[0] == {"day": "2026-07-20", "legged_entries": 2, "completed": 1, "completion_rate": 0.5}
    assert trend[1]["legged_entries"] == 1 and trend[1]["completion_rate"] == 1.0


def test_completion_trend_empty_book(conn):
    assert analytics.completion_trend(conn) == []


# --------------------------------------------------------------------------- arm divergence
def _iteration(conn, ts, centers, day="2026-07-20"):
    for arm, center in centers.items():
        dbmod.record_iteration(
            conn,
            iteration_ts=ts,
            trade_date=day,
            symbol="SPX",
            arm=arm,
            center=center,
            center_reason="atm",
            underlying_price=6000.0,
        )


def test_identical_centers_report_full_agreement(conn):
    """The case that would quietly invalidate the experiment: if the arms always agree, no amount of
    data separates them, and the module needs to say so rather than accumulate for a month."""
    _iteration(conn, "T1", {"gex": 6000.0, "control": 6000.0, "time_window": 6000.0})
    _iteration(conn, "T2", {"gex": 6005.0, "control": 6005.0, "time_window": 6005.0})
    div = analytics.arm_divergence(conn, "2026-07-20")
    assert div["all_agree_rate"] == 1.0
    assert all(p["agreement_rate"] == 1.0 for p in div["pairs"])


def test_divergent_centers_report_the_true_rate(conn):
    _iteration(conn, "T1", {"gex": 6005.0, "control": 6000.0})
    _iteration(conn, "T2", {"gex": 6000.0, "control": 6000.0})
    div = analytics.arm_divergence(conn, "2026-07-20")
    assert div["iterations"] == 2
    assert div["all_agree_rate"] == 0.5
    assert div["pairs"][0]["arms"] == "control vs gex"
    assert div["pairs"][0]["agreement_rate"] == 0.5


def test_iterations_with_one_arm_are_not_counted(conn):
    """A lone arm cannot agree or disagree with anything; including it would dilute the rate."""
    _iteration(conn, "T1", {"gex": 6000.0})
    assert analytics.arm_divergence(conn, "2026-07-20")["iterations"] == 0


def test_recording_the_same_iteration_twice_does_not_inflate_the_denominator(conn):
    _iteration(conn, "T1", {"gex": 6005.0, "control": 6000.0})
    _iteration(conn, "T1", {"gex": 6005.0, "control": 6000.0})
    assert analytics.arm_divergence(conn, "2026-07-20")["iterations"] == 1


# --------------------------------------------------------------------------- payoff curve
def test_payoff_curve_of_a_credit_fly_is_green_everywhere(conn):
    position(conn, "P1", kind="fly", net=1.05, fees=6.89, status="open")
    curve = analytics.payoff_curve(conn, "2026-07-20", "gex")
    assert curve["empty"] is False
    assert min(curve["pnl"]) >= 0
    assert curve["floor"]["floor_holds"] is True
    # Peak is at the centre, where the upper wing leg is (by convention) the one ITM side of the
    # exactly-at-the-money boundary -- a $5 exercise fee even at the best price on the curve.
    assert max(curve["pnl"]) == pytest.approx(1.05 * 100 + 500 - 6.89 - 5.00)


def test_payoff_curve_of_an_open_vertical_dips_negative(conn):
    position(conn, "P1", kind="short_vertical", net=2.55, fees=3.44, status="open")
    curve = analytics.payoff_curve(conn, "2026-07-20", "gex")
    assert min(curve["pnl"]) < 0
    assert curve["floor"]["unbounded_below"] is True


def test_payoff_curve_of_an_empty_day_is_empty_not_an_error(conn):
    curve = analytics.payoff_curve(conn, "2026-07-20", "gex")
    assert curve["ok"] is True and curve["empty"] is True


# --------------------------------------------------------------------------- overview
def test_session_overview_bundles_the_today_view(conn):
    position(conn, "P1", kind="fly", status="open", risk_free=1)
    overview = analytics.session_overview(conn, "2026-07-20")
    assert overview["date"] == "2026-07-20"
    assert overview["open_count"] == 1 and overview["risk_free_count"] == 1
    assert "completion" in overview and "divergence" in overview and "journal" in overview


def test_max_possible_loss_aggregate(conn):
    # Regression (2026-07-30): the dashboard's "total possible maximum loss" figure -- every
    # open position's own worst case (defined risk / 0 for a fly), net of trading fees AND the
    # worst-case exercise-assignment fee, as if every leg finished ITM.
    # An open short vertical with real downside (negative floor) drags the total down...
    position(conn, "P1", kind="short_vertical", status="open", floor_dollars=-360.0)
    # ...a fly whose floor is already positive (can't become a loss) contributes nothing...
    position(conn, "P2", kind="fly", status="open", floor_dollars=85.0)
    # ...and a SETTLED position (its outcome is already realized, not a "possible" future loss)
    # is excluded even though its floor was negative.
    position(conn, "P3", kind="short_vertical", status="settled", floor_dollars=-200.0)
    overview = analytics.session_overview(conn, "2026-07-20")
    assert overview["max_possible_loss"] == -360.0


def _book(conn, *, day="2026-07-20", arm="gex", symbol="XSP", net_cash=10.0):
    dbmod.save_book(
        conn,
        {
            "book_id": f"{day}:{arm}:{symbol}",
            "trade_date": day,
            "arm": arm,
            "symbol": symbol,
            "credit_collected": net_cash,
            "debits_paid": 0.0,
            "fees": 0.0,
            "net_cash": net_cash,
            "worst": net_cash,
            "worst_at": None,
            "floor_holds": 1,
            "band_low": None,
            "band_high": None,
            "unbounded_below": 0,
            "status": "open",
        },
    )


def test_session_overview_narrows_every_figure_to_the_selected_arm_and_symbol(conn):
    """Switching either selector must not leave one card (books/positions) telling a different
    story than another (the derived counts, stats, completion) -- the whole Today view has to
    agree on the same scope."""
    position(conn, "P1", arm="gex", symbol="XSP", kind="fly", status="open", risk_free=1, pnl=None)
    position(conn, "P2", arm="control", symbol="SPX", kind="fly", status="open", risk_free=0, pnl=None)
    _book(conn, arm="gex", symbol="XSP")
    _book(conn, arm="control", symbol="SPX")

    everything = analytics.session_overview(conn, "2026-07-20")
    assert everything["open_count"] == 2

    xsp_only = analytics.session_overview(conn, "2026-07-20", symbol="XSP")
    assert xsp_only["open_count"] == 1
    assert xsp_only["risk_free_count"] == 1
    assert {p["position_id"] for p in xsp_only["positions"]} == {"P1"}
    assert {b["symbol"] for b in xsp_only["books"]} == {"XSP"}

    gex_only = analytics.session_overview(conn, "2026-07-20", arm="gex")
    assert {p["position_id"] for p in gex_only["positions"]} == {"P1"}


def test_positions_for_day_and_books_for_day_accept_a_symbol_filter(conn):
    position(conn, "P1", symbol="XSP")
    position(conn, "P2", symbol="SPX")
    _book(conn, symbol="XSP")
    _book(conn, symbol="SPX")
    assert {p["position_id"] for p in analytics.positions_for_day(conn, "2026-07-20", symbol="XSP")} == {"P1"}
    books = analytics.books_for_day(conn, "2026-07-20", symbol="XSP")
    assert {b["symbol"] for b in books} == {"XSP"}


def test_completion_stats_accepts_an_arm_filter(conn):
    position(conn, "F1", arm="gex", kind="fly", entry_mode="legged")
    position(
        conn, "F2", arm="control", kind="short_vertical", entry_mode="legged", best_debit=0.0, credit=1.0
    )
    gex_only = analytics.completion_stats(conn, arm="gex")
    assert gex_only["legged_entries"] == 1 and gex_only["completed"] == 1


# --------------------------------------------------------------------------- session timeline
def _legged(
    conn,
    position_id,
    *,
    day="2026-07-20",
    arm="gex",
    center=6000.0,
    credit=2.55,
    debit=1.50,
    entry="T12:00:00",
    completed=None,
    latency=None,
    spot_at_completion=None,
    underlying=6000.0,
):
    """A legged position, optionally completed — the case the timeline has to rewind correctly."""
    open_fee = fly.vertical_open_fee("SPX", 1)
    dbmod.save_position(
        conn,
        {
            "position_id": position_id,
            "book_id": f"{day}:{arm}:SPX",
            "trade_date": day,
            "arm": arm,
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": "fly" if completed else "short_vertical",
            "side": "put",
            "center": center,
            "wing_width": 5.0,
            "quantity": 1,
            "net": credit - debit if completed else credit,
            "credit": credit,
            "debit": debit if completed else None,
            "fees": open_fee * 2 if completed else open_fee,
            "entry_time": f"{day}{entry}",
            "completed_at": f"{day}{completed}" if completed else None,
            "completion_latency_min": latency,
            "spot_at_completion": spot_at_completion,
            "underlying_at_entry": underlying,
            "status": "open",
        },
    )


def _tick(conn, ts, *, day="2026-07-20", arm="gex", center=6000.0, spot=6000.0):
    dbmod.record_iteration(
        conn,
        iteration_ts=f"{day}{ts}",
        trade_date=day,
        symbol="SPX",
        arm=arm,
        center=center,
        center_reason="atm",
        underlying_price=spot,
    )


def test_timeline_rewinds_a_completed_fly_to_the_vertical_it_used_to_be(conn):
    """The one thing the replay must not get wrong.

    Before completion the position was a short vertical carrying full defined risk; only afterwards
    is it a fly. Replaying from the stored row without rewinding would draw the morning as though
    every fly existed from the moment its credit spread was sold — asserting exactly the
    per-position floor honesty rule 3 refuses to claim loosely.
    """
    _legged(conn, "P1", entry="T12:00:00", completed="T12:30:00", latency=30.0, spot_at_completion=6012.0)
    _tick(conn, "T12:15:00")
    _tick(conn, "T12:45:00")
    open_fee = fly.vertical_open_fee("SPX", 1)

    ticks = analytics.session_timeline(conn, "2026-07-20")["ticks"]
    before, after = ticks[0]["settle_now"]["gex"], ticks[1]["settle_now"]["gex"]
    # short put spread at its short strike: no payoff, one fee stack, the credit still at risk
    assert before == pytest.approx(2.55 * 100 - open_fee, abs=0.01)
    # a fly at its centre: full wing, two fee stacks, and a $5 exercise fee -- at exactly the
    # money the upper wing leg is (by convention) the one ITM side of that boundary.
    assert after == pytest.approx(1.05 * 100 + 500 - 2 * open_fee - 5.00, abs=0.01)


def test_timeline_excludes_a_position_that_had_not_been_opened_yet(conn):
    _legged(conn, "P1", entry="T13:00:00")
    _tick(conn, "T12:00:00")
    _tick(conn, "T14:00:00")
    ticks = analytics.session_timeline(conn, "2026-07-20")["ticks"]
    assert ticks[0]["settle_now"] == {}
    assert "gex" in ticks[1]["settle_now"]


def test_timeline_spans_carry_latency_and_the_drift_that_bought_it(conn):
    """The 2026-07-20 finding — completions arrived only after 10-21 points of drift — is a shape
    over time, and this is the pairing that lets it be seen rather than inferred."""
    _legged(
        conn,
        "P1",
        entry="T10:00:00",
        completed="T10:21:00",
        latency=21.0,
        underlying=6000.0,
        spot_at_completion=6014.0,
    )
    _tick(conn, "T10:30:00")
    timeline = analytics.session_timeline(conn, "2026-07-20")
    span = timeline["spans"][0]
    assert span["latency_min"] == 21.0 and span["drift"] == 14.0
    assert [e["kind"] for e in timeline["events"]] == ["entry", "completion"]
    assert timeline["waiting"] == []


def test_timeline_lists_spreads_still_waiting_to_complete(conn):
    """The branch that carries full defined risk. On a time axis it is visible while it is still
    happening, rather than only once settlement resolves it."""
    _legged(conn, "P1", entry="T10:00:00")
    _tick(conn, "T10:30:00")
    waiting = analytics.session_timeline(conn, "2026-07-20")["waiting"]
    assert [w["position_id"] for w in waiting] == ["P1"]


def test_timeline_of_a_day_with_no_iterations_is_empty_not_an_error(conn):
    timeline = analytics.session_timeline(conn, "2026-07-20")
    assert timeline["ticks"] == [] and timeline["arms"] == []


def test_timeline_keeps_each_arm_separate(conn):
    _tick(conn, "T12:00:00", arm="gex", center=6005.0)
    _tick(conn, "T12:00:00", arm="control", center=6000.0)
    tick = analytics.session_timeline(conn, "2026-07-20")["ticks"][0]
    assert tick["centers"] == {"gex": 6005.0, "control": 6000.0}


# --------------------------------------------------------------------------- feed / data quality
def _snap(conn, ts, *, day="2026-07-20", symbol="SPX", status="ok", fresh=40, rejected=0, spot=6000.0):
    dbmod.record_snapshot(
        conn,
        iteration_ts=f"{day}{ts}",
        trade_date=day,
        symbol=symbol,
        status=status,
        quotes_fresh=fresh,
        quotes_rejected=rejected,
        underlying_price=spot,
    )


def test_data_quality_counts_refusals_by_reason(conn):
    """The barren-session forensic: was a quiet day the strategy finding nothing, or the feed giving
    us nothing? The split by reason is what answers it."""
    _snap(conn, "T12:00:00", status="ok", fresh=40)
    _snap(conn, "T12:02:00", status="no_fresh_quotes", fresh=None, rejected=12)
    _snap(conn, "T12:04:00", status="no_fresh_quotes", fresh=None, rejected=9)
    _snap(conn, "T12:06:00", status="no_spot_price", fresh=None, rejected=None)
    feed, summary = analytics.data_quality(conn, "2026-07-20")
    assert summary["ticks"] == 4 and summary["ok"] == 1 and summary["refused"] == 3
    assert summary["by_reason"] == {"no_fresh_quotes": 2, "no_spot_price": 1}
    assert summary["ok_rate"] == 0.25
    assert [f["status"] for f in feed] == ["ok", "no_fresh_quotes", "no_fresh_quotes", "no_spot_price"]


def test_data_quality_is_idempotent_on_a_retick(conn):
    """A re-run of the same tick must not inflate the denominator — UNIQUE(iteration_ts, symbol)."""
    _snap(conn, "T12:00:00", status="ok")
    _snap(conn, "T12:00:00", status="ok")
    _, summary = analytics.data_quality(conn, "2026-07-20")
    assert summary["ticks"] == 1


def test_timeline_carries_the_feed(conn):
    _snap(conn, "T12:00:00", status="ok", fresh=40)
    _snap(conn, "T12:02:00", status="no_fresh_quotes", fresh=None, rejected=7)
    tl = analytics.session_timeline(conn, "2026-07-20")
    assert tl["feed_summary"]["refused"] == 1
    assert [f["status"] for f in tl["feed"]] == ["ok", "no_fresh_quotes"]


def test_data_quality_on_an_empty_day_is_not_an_error(conn):
    feed, summary = analytics.data_quality(conn, "2026-07-20")
    assert feed == [] and summary["ticks"] == 0 and summary["ok_rate"] is None


def test_entry_structure_label_covers_known_modes_and_falls_back_for_unknown():
    assert analytics._entry_structure_label("legged", "put") == "short put"
    assert analytics._entry_structure_label("legged", "call") == "short call"
    assert analytics._entry_structure_label("outright", "put") == "fly"
    # An entry_mode this map wasn't written for must surface as itself, not be guessed at as a
    # short vertical (the old ternary's default) or crash.
    assert analytics._entry_structure_label("debit_first", "put") == "debit put"
    assert analytics._entry_structure_label("bwb_roll", "put") == "bwb put"
    # An entry_mode this map wasn't written for at all must surface as itself, never crash.
    assert analytics._entry_structure_label("some_future_mode", "put") == "some_future_mode"


def _debit_first(
    conn,
    position_id,
    *,
    day="2026-07-20",
    arm="gex",
    center=6000.0,
    credit=2.55,
    debit=1.50,
    entry="T12:00:00",
    completed=None,
    latency=None,
    spot_at_completion=None,
    underlying=6000.0,
):
    """A debit_first position, optionally completed -- the mirror of _legged, side=call."""
    open_fee = fly.vertical_open_fee("SPX", 1)
    dbmod.save_position(
        conn,
        {
            "position_id": position_id,
            "book_id": f"{day}:{arm}:SPX",
            "trade_date": day,
            "arm": arm,
            "entry_mode": "debit_first",
            "symbol": "SPX",
            "kind": "fly" if completed else "long_vertical",
            "side": "call",
            "center": center,
            "wing_width": 5.0,
            "quantity": 1,
            "net": credit - debit if completed else -debit,
            "credit": credit if completed else None,
            "debit": debit,
            "fees": open_fee * 2 if completed else open_fee,
            "entry_time": f"{day}{entry}",
            "completed_at": f"{day}{completed}" if completed else None,
            "completion_latency_min": latency,
            "spot_at_completion": spot_at_completion,
            "underlying_at_entry": underlying,
            "status": "open",
        },
    )


def test_timeline_rewinds_a_completed_debit_first_fly_to_the_long_vertical_it_used_to_be(conn):
    """debit_first's counterpart of test_timeline_rewinds_a_completed_fly_to_the_vertical_it_used_to_be
    -- before completion the position was a LONG vertical (a paid-for debit spread), not a fly."""
    _debit_first(
        conn, "P1", entry="T12:00:00", completed="T12:30:00", latency=30.0, spot_at_completion=6000.0
    )
    _tick(conn, "T12:15:00", spot=5995.0)  # the long vertical's own strike -- payoff 0, mirrors legged's
    _tick(conn, "T12:45:00", spot=6000.0)  # the completed fly at its centre -- full wing
    open_fee = fly.vertical_open_fee("SPX", 1)

    ticks = analytics.session_timeline(conn, "2026-07-20")["ticks"]
    before, after = ticks[0]["settle_now"]["gex"], ticks[1]["settle_now"]["gex"]
    # long vertical at its own (center - w) strike: no payoff yet, one fee stack, no ITM legs.
    assert before == pytest.approx(-1.50 * 100 - open_fee, abs=0.01)
    # a fly at its centre: full wing, two fee stacks, one ITM leg by the same boundary convention.
    assert after == pytest.approx(1.05 * 100 + 500 - 2 * open_fee - 5.00, abs=0.01)


def _bwb(
    conn,
    position_id,
    *,
    day="2026-07-20",
    arm="gex",
    center=6000.0,
    credit=1.1,
    far_width=10.0,
    roll_debit=0.3,
    entry="T12:00:00",
    rolled=None,
    latency=None,
    spot_at_roll=None,
    underlying=6000.0,
):
    """A bwb_roll position, optionally rolled -- the mirror of _legged/_debit_first, side=put."""
    open_fee = fly.fly_open_fee("SPX", 1)
    roll_fee = fly.vertical_open_fee("SPX", 1)
    dbmod.save_position(
        conn,
        {
            "position_id": position_id,
            "book_id": f"{day}:{arm}:SPX",
            "trade_date": day,
            "arm": arm,
            "entry_mode": "bwb_roll",
            "symbol": "SPX",
            "kind": "fly" if rolled else "bwb",
            "side": "put",
            "center": center,
            "wing_width": 5.0,
            "far_width": far_width,
            "quantity": 1,
            "net": credit - roll_debit if rolled else credit,
            "credit": credit,
            "roll_debit": roll_debit if rolled else None,
            "fees": open_fee + roll_fee if rolled else open_fee,
            "entry_time": f"{day}{entry}",
            "completed_at": f"{day}{rolled}" if rolled else None,
            "rolled_at": f"{day}{rolled}" if rolled else None,
            "completion_latency_min": latency,
            "roll_latency_min": latency,
            "spot_at_completion": spot_at_roll,
            "spot_at_roll": spot_at_roll,
            "underlying_at_entry": underlying,
            "status": "open",
        },
    )


def test_timeline_rewinds_a_rolled_bwb_to_the_broken_wing_it_used_to_be(conn):
    """bwb_roll's counterpart of the legged/debit_first rewind tests -- before the roll the
    position was a genuine BWB with real tail risk, not the symmetric fly it becomes after."""
    _bwb(conn, "P1", entry="T12:00:00", rolled="T12:30:00", latency=30.0, spot_at_roll=6000.0)
    _tick(conn, "T12:15:00", spot=6005.0)  # the bwb's own near-wing strike -- payoff 0, no ITM legs
    _tick(conn, "T12:45:00", spot=6000.0)  # the rolled fly at its centre -- full wing
    open_fee = fly.fly_open_fee("SPX", 1)
    roll_fee = fly.vertical_open_fee("SPX", 1)

    ticks = analytics.session_timeline(conn, "2026-07-20")["ticks"]
    before, after = ticks[0]["settle_now"]["gex"], ticks[1]["settle_now"]["gex"]
    # bwb at its own near-wing strike: no payoff yet, one fee stack, no ITM legs.
    assert before == pytest.approx(1.1 * 100 - open_fee, abs=0.01)
    # the rolled fly at its centre: full wing, two fee stacks, one ITM leg by the same convention.
    assert after == pytest.approx((1.1 - 0.3) * 100 + 500 - open_fee - roll_fee - 5.00, abs=0.01)


def test_timeline_prices_a_bwb_that_has_not_rolled_yet(conn):
    """An un-rolled bwb keeps kind='bwb' on its own row, so the rewind's pre-roll branch never
    fires and its state comes from the base dict — which has to carry `far_width` anyway, since
    that is the width `fly.position_pnl`'s bwb branch reads.

    Regression: it did not, so every tick holding an open bwb raised KeyError('far_width'), and
    because one exception fails the whole /api/data payload a single un-rolled bwb blanked every
    panel on the dashboard — including the arms that had nothing to do with it.
    """
    _bwb(conn, "P1", entry="T12:00:00")  # never rolled: still a broken wing, tail still live
    _tick(conn, "T12:15:00", spot=6005.0)  # its near-wing strike -- payoff 0, nothing ITM
    _tick(conn, "T12:45:00", spot=5980.0)  # past the far wing -- the negative tail, fully realized
    open_fee = fly.fly_open_fee("SPX", 1)

    ticks = analytics.session_timeline(conn, "2026-07-20")["ticks"]
    at_wing, in_tail = ticks[0]["settle_now"]["gex"], ticks[1]["settle_now"]["gex"]
    assert at_wing == pytest.approx(1.1 * 100 - open_fee, abs=0.01)
    # tail = wing_width - far_width = -5, and all three put strikes finish strictly ITM down here.
    assert in_tail == pytest.approx(1.1 * 100 - 500 - open_fee - 3 * 5.00, abs=0.01)
    # The tail is the whole point of rule 3: this must not read as a bounded, floor-protected fly.
    assert in_tail < 0


# --------------------------------------------------------------------------- regime conditioning
def test_by_regime_groups_on_the_stored_bucket(conn):
    position(conn, "P1", pnl=100.0, regime={"gex_bucket": "pinning", "gex_concentration": 0.75})
    position(conn, "P2", pnl=50.0, regime={"gex_bucket": "pinning", "gex_concentration": 0.68})
    position(conn, "P3", pnl=-30.0, regime={"gex_bucket": "thin", "gex_concentration": 0.20})
    rows = {r["bucket"]: r for r in analytics.by_regime(conn, "gex")}
    assert rows["pinning"]["trades"] == 2 and rows["pinning"]["net_pnl"] == 150.0
    assert rows["thin"]["trades"] == 1 and rows["thin"]["net_pnl"] == -30.0
    # The measured range is carried so a threshold can be judged against what actually occurred.
    assert rows["pinning"]["value_min"] == 0.68 and rows["pinning"]["value_max"] == 0.75


def test_by_regime_rebuckets_the_recorded_float_at_analysis_time(conn):
    """The whole reason the float is stored: re-cutting a threshold must not require re-running
    sessions, which for regime data is impossible (there is no backfill path)."""
    position(conn, "P1", pnl=100.0, regime={"gex_bucket": "thin", "gex_concentration": 0.35})
    position(conn, "P2", pnl=-40.0, regime={"gex_bucket": "thin", "gex_concentration": 0.90})
    # Both stored as "thin"; a 0.5 cut separates them without touching the ledger.
    assert len(analytics.by_regime(conn, "gex")) == 1
    rebucketed = {r["bucket"]: r for r in analytics.by_regime(conn, "gex", bucket_edges=[0.5])}
    assert rebucketed["<0.5"]["net_pnl"] == 100.0
    assert rebucketed[">=0.5"]["net_pnl"] == -40.0


def test_by_regime_keeps_untagged_rows_visible(conn):
    """Pre-2026-07-31 rows carry no regime and cannot be backfilled. Dropping them would make
    coverage look better than it is; they get their own bucket instead."""
    position(conn, "P1", pnl=10.0, regime={"gex_bucket": "thin", "gex_concentration": 0.2})
    position(conn, "P2", pnl=20.0)  # no regime at all
    buckets = {r["bucket"] for r in analytics.by_regime(conn, "gex")}
    assert buckets == {"thin", "untagged"}


def test_by_regime_rejects_an_unknown_dimension(conn):
    with pytest.raises(ValueError, match="unknown dimension"):
        analytics.by_regime(conn, "moon_phase")


def test_regime_coverage_flags_a_degenerate_dimension(conn):
    """The honesty guard. A dimension whose every tagged row lands in one bucket produces a table
    that looks like a result and contains no contrast -- which is exactly what entry_gex_bucket was
    ('thin' 60/60) before the classifier was windowed, with nothing in the read layer saying so."""
    position(conn, "P1", pnl=10.0, regime={"gex_bucket": "thin", "skew_bucket": "put_skew"})
    position(conn, "P2", pnl=20.0, regime={"gex_bucket": "thin", "skew_bucket": "flat"})
    position(conn, "P3", pnl=30.0)  # untagged
    cov = analytics.regime_coverage(conn)
    assert cov["settled_trades"] == 3
    assert cov["dimensions"]["gex"]["degenerate"] is True
    assert cov["dimensions"]["gex"]["tagged"] == 2
    assert cov["dimensions"]["gex"]["untagged"] == 1
    assert cov["dimensions"]["skew"]["degenerate"] is False


def test_by_regime_reads_the_completion_phase_too(conn):
    """Entry and completion regimes can differ -- that difference is the point of storing both."""
    position(conn, "P1", pnl=10.0, regime={"vol_bucket": "high"})
    conn.execute("UPDATE fly_positions SET completion_vol_bucket = 'low' WHERE position_id = 'P1'")
    conn.commit()
    assert analytics.by_regime(conn, "vol", phase="entry")[0]["bucket"] == "high"
    assert analytics.by_regime(conn, "vol", phase="completion")[0]["bucket"] == "low"
