"""Tests for the read-only analytics layer."""

import pytest

import analytics
import db as dbmod
import fly


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def position(conn, position_id, *, day="2026-07-20", arm="gex", symbol="SPX", kind="fly",
             entry_mode="legged", center=6000.0, width=5.0, net=1.05, credit=2.55, fees=6.89,
             gross=105.0, pnl=98.11, status="settled", window=None, best_debit=None,
             latency=None, spot_at_completion=None, underlying=6000.0, risk_free=1):
    dbmod.save_position(conn, {
        "position_id": position_id, "book_id": f"{day}:{arm}:{symbol}", "trade_date": day,
        "arm": arm, "entry_mode": entry_mode, "symbol": symbol, "kind": kind, "side": "put",
        "center": center, "wing_width": width, "quantity": 1, "net": net, "credit": credit,
        "fees": fees, "gross_pnl": gross, "pnl": pnl, "status": status, "entry_window": window,
        "best_completing_debit": best_debit, "completion_latency_min": latency,
        "spot_at_completion": spot_at_completion, "underlying_at_entry": underlying,
        "risk_free": risk_free, "entry_time": f"{day}T12:00:00",
    })


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
    position(conn, "P1", day="2026-07-20")   # Monday
    position(conn, "P2", day="2026-07-24")   # Friday, same trading week
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


def test_by_entry_window_groups_untagged_separately(conn):
    position(conn, "P1", window="09:45-10:15", pnl=40.0)
    position(conn, "P2", window=None, pnl=10.0)
    rows = {r["window"]: r for r in analytics.by_entry_window(conn)}
    assert rows["09:45-10:15"]["net_pnl"] == 40.0
    assert rows["unwindowed"]["net_pnl"] == 10.0


# --------------------------------------------------------------------------- completion counterfactual
def test_counterfactual_separates_never_offered_from_buffer_too_tight(conn):
    """The distinction the whole counterfactual exists for. These two look identical in the P&L and
    call for opposite fixes: one says the market never got there, the other says our gate cost us
    the fly."""
    # completed
    position(conn, "P1", kind="fly", credit=2.55, best_debit=1.50, latency=23.0)
    # the market never offered a debit below the credit at all
    position(conn, "P2", kind="short_vertical", credit=2.55, best_debit=2.60)
    # the debit did beat the credit — just not by enough to clear the buffer
    position(conn, "P3", kind="short_vertical", credit=2.10, best_debit=2.02)
    # never priced (e.g. missing quotes all session)
    position(conn, "P4", kind="short_vertical", credit=2.00, best_debit=None)

    stats = analytics.completion_stats(conn)
    assert stats["legged_entries"] == 4
    assert stats["completed"] == 1
    assert stats["completion_rate"] == 0.25
    assert stats["never_offered"] == 1
    assert stats["buffer_too_tight"] == 1
    assert stats["counterfactual_unknown"] == 1


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


# --------------------------------------------------------------------------- arm divergence
def _iteration(conn, ts, centers, day="2026-07-20"):
    for arm, center in centers.items():
        dbmod.record_iteration(conn, iteration_ts=ts, trade_date=day, symbol="SPX", arm=arm,
                               center=center, center_reason="atm", underlying_price=6000.0)


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
    assert max(curve["pnl"]) == pytest.approx(1.05 * 100 + 500 - 6.89)


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


# --------------------------------------------------------------------------- session timeline
def _legged(conn, position_id, *, day="2026-07-20", arm="gex", center=6000.0, credit=2.55,
            debit=1.50, entry="T12:00:00", completed=None, latency=None, spot_at_completion=None,
            underlying=6000.0):
    """A legged position, optionally completed — the case the timeline has to rewind correctly."""
    open_fee = fly.vertical_open_fee("SPX", 1)
    dbmod.save_position(conn, {
        "position_id": position_id, "book_id": f"{day}:{arm}:SPX", "trade_date": day, "arm": arm,
        "entry_mode": "legged", "symbol": "SPX", "kind": "fly" if completed else "short_vertical",
        "side": "put", "center": center, "wing_width": 5.0, "quantity": 1,
        "net": credit - debit if completed else credit, "credit": credit,
        "debit": debit if completed else None,
        "fees": open_fee * 2 if completed else open_fee,
        "entry_time": f"{day}{entry}", "completed_at": f"{day}{completed}" if completed else None,
        "completion_latency_min": latency, "spot_at_completion": spot_at_completion,
        "underlying_at_entry": underlying, "status": "open",
    })


def _tick(conn, ts, *, day="2026-07-20", arm="gex", center=6000.0, spot=6000.0):
    dbmod.record_iteration(conn, iteration_ts=f"{day}{ts}", trade_date=day, symbol="SPX", arm=arm,
                           center=center, center_reason="atm", underlying_price=spot)


def test_timeline_rewinds_a_completed_fly_to_the_vertical_it_used_to_be(conn):
    """The one thing the replay must not get wrong.

    Before completion the position was a short vertical carrying full defined risk; only afterwards
    is it a fly. Replaying from the stored row without rewinding would draw the morning as though
    every fly existed from the moment its credit spread was sold — asserting exactly the
    per-position floor honesty rule 3 refuses to claim loosely.
    """
    _legged(conn, "P1", entry="T12:00:00", completed="T12:30:00", latency=30.0,
            spot_at_completion=6012.0)
    _tick(conn, "T12:15:00")
    _tick(conn, "T12:45:00")
    open_fee = fly.vertical_open_fee("SPX", 1)

    ticks = analytics.session_timeline(conn, "2026-07-20")["ticks"]
    before, after = ticks[0]["settle_now"]["gex"], ticks[1]["settle_now"]["gex"]
    # short put spread at its short strike: no payoff, one fee stack, the credit still at risk
    assert before == pytest.approx(2.55 * 100 - open_fee, abs=0.01)
    # a fly at its centre: full wing, and now two fee stacks
    assert after == pytest.approx(1.05 * 100 + 500 - 2 * open_fee, abs=0.01)


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
    _legged(conn, "P1", entry="T10:00:00", completed="T10:21:00", latency=21.0,
            underlying=6000.0, spot_at_completion=6014.0)
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
    dbmod.record_snapshot(conn, iteration_ts=f"{day}{ts}", trade_date=day, symbol=symbol,
                          status=status, quotes_fresh=fresh, quotes_rejected=rejected,
                          underlying_price=spot)


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
