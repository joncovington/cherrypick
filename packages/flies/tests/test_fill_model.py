"""fill_model: the pure measures behind live fill realism and the paper shadow."""

import pytest

from cherrypick.flies import fill_model as fm
from cherrypick.flies import fly


# --------------------------------------------------------------------------- distances
def test_distances_are_signed_toward_the_completing_direction_for_both_sides():
    """The 2026-10-02 live call fly: sold the 7715/7720 call spread, completed buying 7710/7715
    with spot at 7704.43 -- 10.57 points past the centre and 5.57 past the completing long strike,
    both POSITIVE because a call spread completes as spot falls. The put mirror must agree, or a
    distance rule would read every put fill as a miss."""
    call = fm.spot_distances(fly.CALL, 7715.0, 5.0, 7704.43)
    assert call["dist_center"] == pytest.approx(10.57)
    assert call["dist_long"] == pytest.approx(5.57)
    assert call["dist_widths"] == pytest.approx(10.57 / 5)

    put = fm.spot_distances(fly.PUT, 7715.0, 5.0, 7725.57)
    assert put["dist_center"] == pytest.approx(10.57)
    assert put["dist_long"] == pytest.approx(5.57)

    # Spot on the wrong side of the centre is a negative distance, never clipped to zero.
    assert fm.spot_distances(fly.PUT, 7715.0, 5.0, 7710.0)["dist_center"] == pytest.approx(-5.0)


def test_unknown_spot_is_none_not_zero():
    assert set(fm.spot_distances(fly.PUT, 7715.0, 5.0, None).values()) == {None}


def test_moves_normalise_by_the_entry_straddle_in_points():
    straddle = fm.straddle_points(0.004, 7500.0)  # 30 points
    d = fm.spot_distances(fly.CALL, 7500.0, 5.0, 7485.0, straddle_points=straddle)
    assert d["dist_moves"] == pytest.approx(0.5)
    assert fm.straddle_points(None, 7500.0) is None


# --------------------------------------------------------------------------- price
def test_spread_prices_and_gaps_read_each_order_in_its_own_direction():
    # Completion: buy the far strike (bid 2.50 / ask 2.70), sell the centre (bid 1.30 / ask 1.50).
    comp = fm.spread_prices(fm.COMPLETION, 2.50, 2.70, 1.30, 1.50)
    assert comp == {"mid": pytest.approx(1.20), "natural": pytest.approx(1.40)}
    g = fm.gaps(fm.COMPLETION, 1.10, comp)
    assert g["mid_gap"] == pytest.approx(0.10)  # mid sits 0.10 ABOVE a 1.10 debit limit: not reached
    assert g["natural_gap"] == pytest.approx(0.30)

    # Entry: sell the centre, buy the wing -- a credit. Same quotes, opposite direction.
    entry = fm.spread_prices(fm.ENTRY, 1.30, 1.50, 2.50, 2.70)
    assert entry == {"mid": pytest.approx(1.20), "natural": pytest.approx(1.00)}
    g = fm.gaps(fm.ENTRY, 1.25, entry)
    assert g["mid_gap"] == pytest.approx(0.05)  # mid credit 0.05 BELOW the 1.25 asked: not reached
    assert g["natural_gap"] == pytest.approx(0.25)


def test_a_spread_with_a_missing_side_has_no_price():
    assert fm.spread_prices(fm.COMPLETION, None, 2.70, 1.30, 1.50) is None
    assert fm.gaps(fm.COMPLETION, 1.10, None) == {"mid_gap": None, "natural_gap": None}


def test_net_fill_price_comes_from_the_leg_fills_never_the_limit():
    fills = [
        {
            "symbol": "far",
            "action": "Buy to Open",
            "quantity": 1,
            "fill_price": "2.55",
            "filled_at": "2026-10-02T15:24:40+00:00",
        },
        {
            "symbol": "ctr",
            "action": "Sell to Open",
            "quantity": 1,
            "fill_price": "0.35",
            "filled_at": "2026-10-02T15:24:41+00:00",
        },
    ]
    assert fm.net_fill_price(fills, 1) == pytest.approx(2.20)  # under a 2.25 limit: improvement
    assert fm.latest_fill_time(fills) == "2026-10-02T15:24:41+00:00"
    # No parseable price on any leg is None, so the limit can never stand in for a fill price.
    assert fm.net_fill_price([{"action": "Buy to Open", "quantity": 1, "fill_price": None}], 1) is None
    assert fm.net_fill_price(None, 1) is None
    assert fm.latest_fill_time([]) is None


# --------------------------------------------------------------------------- first touch
def test_update_touches_keeps_only_the_first_touch_and_the_extremes():
    rec, changed = fm.update_touches(None, ts="T1", mid_gap=0.12, natural_gap=0.30, dist_widths=0.6)
    assert changed
    assert rec["mid"] == {fm.grid_key(g): "T1" for g in fm.GAP_GRID if g >= 0.12}
    assert rec["dist"] == {fm.grid_key(x): "T1" for x in fm.WIDTH_GRID if x <= 0.6}
    assert rec["best_gap"] == [0.12, "T1"] and rec["max_dist"] == [0.6, "T1"]

    before = {k: dict(v) if isinstance(v, dict) else v for k, v in rec.items()}
    rec2, changed = fm.update_touches(rec, ts="T2", mid_gap=0.04, natural_gap=0.30, dist_widths=0.4)
    assert changed
    assert rec2["mid"][fm.grid_key(0.15)] == "T1"  # first touch stands
    assert rec2["mid"][fm.grid_key(0.05)] == "T2"  # newly reached
    assert rec2["best_gap"] == [0.04, "T2"]
    assert rec2["max_dist"] == [0.6, "T1"]  # a pull-back does not move the furthest distance
    assert rec == before, "the input record must not be mutated"

    _, changed = fm.update_touches(rec2, ts="T3", mid_gap=0.30, natural_gap=0.5, dist_widths=0.1)
    assert not changed


def test_touches_from_a_stored_path_match_folding_the_observations():
    path = [
        {
            "observed_at": "2026-10-02T11:00:00-04:00",
            "limit_price": 1.10,
            "buy_bid": 2.5,
            "buy_ask": 2.7,
            "sell_bid": 1.3,
            "sell_ask": 1.5,
            "spot": 7500.0,
        },
        {
            "observed_at": "2026-10-02T11:00:10-04:00",
            "limit_price": 1.10,
            "buy_bid": 2.3,
            "buy_ask": 2.5,
            "sell_bid": 1.3,
            "sell_ask": 1.5,
            "spot": 7503.0,
        },
    ]
    rec = fm.touches_from_path(path, leg=fm.COMPLETION, side=fly.PUT, center=7495.0, width=5.0)
    assert rec["mid"][fm.grid_key(0.10)] == "2026-10-02T11:00:00-04:00"
    assert rec["mid"][fm.grid_key(-0.10)] == "2026-10-02T11:00:10-04:00"  # mid 1.00 vs 1.10
    assert rec["max_dist"] == [pytest.approx(8 / 5), "2026-10-02T11:00:10-04:00"]
    assert fm.touches_from_path([], leg=fm.COMPLETION, side=fly.PUT, center=7495.0, width=5.0) is None


def test_observation_at_never_uses_a_quote_from_after_the_fill():
    rows = [
        {"observed_at": "2026-10-02T11:00:00-04:00"},
        {"observed_at": "2026-10-02T11:00:20-04:00"},
        {"observed_at": "2026-10-02T11:00:40-04:00"},
    ]
    assert fm.observation_at(rows, "2026-10-02T15:00:30+00:00")["observed_at"] == "2026-10-02T11:00:20-04:00"
    assert fm.observation_at(rows, "2026-10-02T10:59:59-04:00") is None
    # Older than the age bound is no observation at all, not a stale one.
    assert fm.observation_at(rows, "2026-10-02T11:05:00-04:00", max_age_seconds=60) is None


# --------------------------------------------------------------------------- scoring
def test_score_rule_counts_hits_misses_and_false_fills_with_timing():
    k = fm.grid_key(0.10)
    orders = [
        {"filled_at": "2026-10-02T11:01:00-04:00", "touches": {"mid": {k: "2026-10-02T11:00:30-04:00"}}},
        {"filled_at": "2026-10-02T11:05:00-04:00", "touches": {"mid": {}}},
        {"filled_at": None, "touches": {"mid": {k: "2026-10-02T12:00:00-04:00"}}},
        {"filled_at": None, "touches": None},
    ]
    s = fm.score_rule(orders, "mid", 0.10)
    assert (s["hit"], s["miss"], s["false_fill"], s["true_no_fill"]) == (1, 1, 1, 1)
    assert s["agreement"] == 0.5
    assert s["timing_error_s"]["p50"] == -30.0  # the rule fired 30s before the real fill


# --------------------------------------------------------------------------- the shadow
def _settled_row(**over):
    row = {
        "symbol": "SPX",
        "side": fly.PUT,
        "center": 7495.0,
        "wing_width": 5.0,
        "quantity": 1,
        "credit": 1.20,
        "shadow_completion_limit": 1.00,
        "settlement_price": 7495.0,
        "shadow_touches": {"mid": {fm.grid_key(0.10): "2026-10-02T11:30:00-04:00"}},
    }
    row.update(over)
    return row


def test_shadow_completes_at_the_first_touch_and_pays_the_limit():
    out = fm.shadow_outcome(_settled_row(), "mid", 0.10, cutoff="15:30")
    expected = fly.position_pnl(
        {
            "kind": "fly",
            "side": fly.PUT,
            "center": 7495.0,
            "wing_width": 5.0,
            "net": 0.20,
            "quantity": 1,
            "fees": fly.vertical_open_fee("SPX", 1) * 2,
        },
        7495.0,
    )
    assert out["completed"] and out["pnl"] == pytest.approx(round(expected, 2))


def test_shadow_misses_leave_the_short_vertical_and_respect_the_cutoff():
    untouched = fm.shadow_outcome(_settled_row(), "mid", 0.05, cutoff="15:30")
    assert not untouched["completed"]
    late = _settled_row(shadow_touches={"mid": {fm.grid_key(0.10): "2026-10-02T15:45:00-04:00"}})
    assert not fm.shadow_outcome(late, "mid", 0.10, cutoff="15:30")["completed"]
    expected = fly.position_pnl(
        {
            "kind": "short_vertical",
            "side": fly.PUT,
            "center": 7495.0,
            "wing_width": 5.0,
            "net": 1.20,
            "quantity": 1,
            "fees": fly.vertical_open_fee("SPX", 1),
        },
        7495.0,
    )
    assert untouched["pnl"] == pytest.approx(round(expected, 2))


def test_a_row_without_a_shadow_limit_is_not_judged():
    assert fm.shadow_outcome(_settled_row(shadow_completion_limit=None), "mid", 0.1, cutoff="15:30") is None
    assert fm.shadow_outcome(_settled_row(settlement_price=None), "mid", 0.1, cutoff="15:30") is None
