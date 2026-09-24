"""stop_policies.derive -- read-side stop-policy scoring computed from a fully-marked open
position's recorded path (put/call_max_cost, put/call_settle_value, put/call_touch_time), not run
as separate entry streams. Pins: each policy's fire condition and fill price (a real stop's own fill
where it crossed first, else the trigger), the derivable=False guards, the fee schedule a derived
book is charged (opening fee, closing fees, $5 per ITM strike held to settlement), force-closed
sides valued at their real fill, and validate_against_control's reconstruction of the stopping
arms' real mechanism.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import paper, stop_policies  # noqa: E402

OPEN = 6.89
ONE = 1.44
FULL = 2.88


def _expire_side(row, side, underlying):
    n = paper.settlement_itm_strikes(
        row.get("put_strike"),
        row.get("call_strike"),
        row.get("wing_width"),
        underlying,
        put_open=side == "put",
        call_open=side == "call",
    )
    return None if n is None else 5.0 * n


FEES = stop_policies.Fees(
    open=lambda _s: OPEN, one_side=lambda _s: ONE, full_ic=lambda _s: FULL, expire_side=_expire_side
)


def _row(**overrides):
    row = {
        "symbol": "SPX",
        "put_strike": 7600.0,
        "call_strike": 7700.0,
        "wing_width": 10.0,
        "put_credit": 0.9,
        "call_credit": 0.9,
        "net_credit": 1.8,
        "put_max_cost": None,
        "call_max_cost": None,
        "put_settle_value": None,
        "call_settle_value": None,
        "settle_underlying": 7650.0,  # between the shorts: every leg OTM
        "put_touch_time": None,
        "call_touch_time": None,
    }
    row.update(overrides)
    return row


def test_stop_none_never_fires_settles_both_sides_and_pays_the_itm_strikes():
    # Settled at 7703: the 7700 short call is 3.00 ITM, the 7710 long is not -- one ITM strike.
    row = _row(put_settle_value=0.0, call_settle_value=3.0, settle_underlying=7703.0)
    out = stop_policies.derive(row, "stop-none", fees=FEES)
    assert out["derivable"] is True
    assert out["put_fired"] is False and out["call_fired"] is False
    assert out["pnl"] == round(0.9 * 100 + (0.9 - 3.0) * 100, 2)
    assert out["fee"] == OPEN + 5.0  # the opening fee the real book paid, and $5 for one ITM strike


def test_the_settlement_fee_is_per_strike_not_per_contract():
    # Through both put strikes: two ITM strikes, $10, whatever the quantity.
    one = stop_policies.derive(
        _row(quantity=1, put_settle_value=10.0, call_settle_value=0.0, settle_underlying=7580.0),
        "stop-none",
        fees=FEES,
    )
    five = stop_policies.derive(
        _row(quantity=5, put_settle_value=10.0, call_settle_value=0.0, settle_underlying=7580.0),
        "stop-none",
        fees=FEES,
    )
    assert one["fee"] == five["fee"] == OPEN + 10.0


def test_a_leg_settling_exactly_at_its_strike_pays_nothing():
    out = stop_policies.derive(
        _row(put_settle_value=0.0, call_settle_value=0.0, settle_underlying=7600.0), "stop-none", fees=FEES
    )
    assert out["fee"] == OPEN


def test_a_fired_side_is_priced_at_its_trigger_not_the_worst_the_side_got():
    """The fired side is bought back where the threshold was crossed. It used to be priced at the
    running maximum -- the worst moment of the day, not the moment the stop would have fired."""
    thresh = 0.75 * 1.8  # 1.35
    row = _row(put_max_cost=4.0, call_max_cost=0.5, put_settle_value=0.0, call_settle_value=0.0)
    out = stop_policies.derive(row, "stop-0.75-net", fees=FEES)
    assert out["put_fired"] is True and out["call_fired"] is False
    assert out["pnl"] == round((0.9 - thresh) * 100 + 0.9 * 100, 2)
    assert out["fee"] == OPEN + ONE


def test_stop_2_0_side_fires_on_each_sides_own_credit():
    row = _row(
        put_credit=0.5,
        call_credit=1.3,
        net_credit=1.8,
        put_max_cost=1.1,  # 2.0 x 0.5 = 1.0 -> fires, priced at 1.0
        call_max_cost=2.5,  # 2.0 x 1.3 = 2.6 -> does NOT fire
        put_settle_value=0.0,
        call_settle_value=0.2,
    )
    out = stop_policies.derive(row, "stop-2.0-side", fees=FEES)
    assert out["put_fired"] is True and out["call_fired"] is False
    assert out["pnl"] == round((0.5 - 1.0) * 100 + (1.3 - 0.2) * 100, 2)
    assert out["fee"] == OPEN + ONE


def test_both_sides_firing_charges_the_full_ic_close():
    row = _row(put_max_cost=100.0, call_max_cost=100.0, put_settle_value=0.0, call_settle_value=0.0)
    out = stop_policies.derive(row, "stop-0.75-net", fees=FEES)
    assert out["put_fired"] is True and out["call_fired"] is True
    assert out["fee"] == OPEN + FULL


def test_a_real_stop_fill_is_used_wherever_the_real_stop_crossed_first():
    """A width-5 row that really stopped at 0.95 x net and filled past it, at 1.95 on a 1.71 trigger.
    Any threshold between the trigger and the fill crossed on that same tick, so its fill IS 1.95;
    a tighter one crossed earlier, on a tick not recorded, and is priced at its own trigger."""
    row = _row(
        status="stopped",
        stop_trigger_current=0.95,
        put_stop_cost=1.95,
        put_max_cost=1.95,
        call_max_cost=0.2,
        put_settle_value=0.0,
        call_settle_value=0.0,
    )
    at_real = stop_policies.derive(row, ("net", 0.95), fees=FEES)
    looser = stop_policies.derive(row, ("net", 1.05), fees=FEES)  # 1.89: still under the 1.95 fill
    tighter = stop_policies.derive(row, ("net", 0.90), fees=FEES)  # 1.62: crossed earlier
    assert at_real["pnl"] == looser["pnl"] == round((0.9 - 1.95) * 100 + 0.9 * 100, 2)
    assert tighter["pnl"] == round((0.9 - 0.9 * 1.8) * 100 + 0.9 * 100, 2)


def test_strike_touch_keeps_the_running_max_because_nothing_else_is_recorded():
    row = _row(
        put_touch_time="2026-08-07 10:00:00",
        put_max_cost=0.95,
        call_max_cost=0.4,
        put_settle_value=0.0,
        call_settle_value=0.0,
    )
    out = stop_policies.derive(row, "strike-touch", fees=FEES)
    assert out["put_fired"] is True and out["call_fired"] is False
    assert out["pnl"] == round((0.9 - 0.95) * 100 + 0.9 * 100, 2)
    assert out["fee"] == OPEN + ONE


def test_a_force_closed_side_is_valued_at_its_real_force_close_under_every_policy():
    """A force-close is not a stop; it happens whatever the policy. Scoring those sides as held to
    settlement counted the force-close's timing as the stop policy's result."""
    row = _row(
        status="force_closed",
        put_max_cost=0.3,
        call_max_cost=1.6,
        put_settle_value=0.0,
        call_settle_value=0.0,
        put_exit_price=0.1,
        call_exit_price=1.4,
    )
    none = stop_policies.derive(row, "stop-none", fees=FEES)
    assert none["pnl"] == round((0.9 - 0.1) * 100 + (0.9 - 1.4) * 100, 2)
    assert none["fee"] == OPEN + FULL  # both sides closed by the force close, no settlement fee
    # A policy that stopped the call earlier: call at its trigger, put still at its force-close fill.
    stopped = stop_policies.derive(row, ("net", 0.75), fees=FEES)
    assert stopped["pnl"] == round((0.9 - 0.1) * 100 + (0.9 - 1.35) * 100, 2)
    assert stopped["fee"] == OPEN + 2 * ONE


def test_a_force_closed_row_without_its_exits_is_not_derivable():
    row = _row(status="force_closed", put_settle_value=0.0, call_settle_value=0.0)
    assert stop_policies.derive(row, "stop-none", fees=FEES)["derivable"] is False


def test_derivable_false_when_a_fired_sides_max_cost_is_missing():
    row = _row(put_touch_time="2026-08-07 10:00:00", put_max_cost=None, call_settle_value=0.0)
    out = stop_policies.derive(row, "strike-touch", fees=FEES)
    assert out["derivable"] is False and out["pnl"] is None


def test_derivable_false_when_a_held_sides_settle_value_is_missing():
    assert (
        stop_policies.derive(_row(put_settle_value=None, call_settle_value=0.0), "stop-none", fees=FEES)[
            "derivable"
        ]
        is False
    )


def test_derivable_false_when_the_settlement_price_is_missing():
    """Without the price, the settlement fee cannot be known -- never priced as a free settlement."""
    row = _row(put_settle_value=0.0, call_settle_value=0.0, settle_underlying=None)
    assert stop_policies.derive(row, "stop-none", fees=FEES)["derivable"] is False


def test_unreachable_threshold_never_fires_no_error():
    row = _row(put_max_cost=0.2, call_max_cost=0.2, put_settle_value=0.0, call_settle_value=0.0)
    out = stop_policies.derive(row, "stop-2.0-side", fees=FEES)
    assert out["put_fired"] is False and out["call_fired"] is False
    assert out["derivable"] is True


def test_unknown_policy_name_raises_keyerror():
    with pytest.raises(KeyError):
        stop_policies.derive(_row(), "not-a-real-policy", fees=FEES)


# --------------------------------------------------------------------------- validate_against_control


def _arm_row(order_id, arm, real_pnl, status, **overrides):
    row = _row(ic_order_id=order_id, risk_profile=arm, status=status, pnl=real_pnl, stop_trigger_current=0.95)
    row.update(overrides)
    return row


def test_validate_reconstructs_a_real_stop_and_measures_its_overshoot():
    fill = 1.95  # a 1.71 trigger, filled on the tick that landed at 1.95
    real_pnl = round((0.9 - fill) * 100 + 0.9 * 100, 2)
    rows = [
        _arm_row(
            "W-1",
            "width-5",
            real_pnl,
            "stopped",
            put_stop_cost=fill,
            put_max_cost=fill,
            call_max_cost=0.3,
            put_settle_value=None,
            call_settle_value=0.0,
        )
    ]
    result = stop_policies.validate_against_control(rows, fees=FEES)
    assert result["arms"] == ["width-5"]
    assert result["compared"] == 1 and result["mismatches"] == [] and result["ok"] is True
    assert result["fill_over_trigger"]["median"] == round(fill / (0.95 * 1.8), 4)


def test_validate_skips_an_arm_that_never_stopped_even_with_a_trigger_recorded():
    """The advisor-era control records stop_trigger_current = 0.95 on every row and never stops.
    Re-deriving a 0.95 stop over it is what failed 4,678 of 6,043 rows."""
    rows = [
        _arm_row(
            "W-1",
            "width-5",
            round((0.9 - 1.71) * 100 + 90, 2),
            "stopped",
            put_stop_cost=1.71,
            put_max_cost=1.71,
            call_max_cost=0.3,
            call_settle_value=0.0,
        ),
        _arm_row(
            "C-1",
            "control",
            180.0,
            "expired",
            put_max_cost=2.5,
            call_max_cost=0.3,
            put_settle_value=0.0,
            call_settle_value=0.0,
        ),
    ]
    result = stop_policies.validate_against_control(rows, fees=FEES)
    assert result["arms"] == ["width-5"]
    assert result["compared"] == 1 and result["ok"] is True


def test_validate_reconstructs_a_real_expiry_on_a_stopping_arm():
    rows = [
        _arm_row(
            "W-1",
            "width-5",
            round((0.9 - 1.71) * 100 + 90, 2),
            "stopped",
            put_stop_cost=1.71,
            put_max_cost=1.71,
            call_max_cost=0.3,
            call_settle_value=0.0,
        ),
        _arm_row(
            "W-2",
            "width-5",
            180.0,
            "expired",
            put_max_cost=0.5,
            call_max_cost=0.3,
            put_settle_value=0.0,
            call_settle_value=0.0,
        ),
    ]
    result = stop_policies.validate_against_control(rows, fees=FEES)
    assert result["compared"] == 2 and result["ok"] is True


def test_validate_flags_a_real_mismatch():
    rows = [
        _arm_row(
            "W-1",
            "width-5",
            round((0.9 - 1.71) * 100 + 90, 2),
            "stopped",
            put_stop_cost=1.71,
            put_max_cost=1.71,
            call_max_cost=0.3,
            call_settle_value=0.0,
        ),
        _arm_row(
            "W-BAD",
            "width-5",
            999.0,
            "expired",
            put_max_cost=0.5,
            call_max_cost=0.3,
            put_settle_value=0.0,
            call_settle_value=0.0,
        ),
    ]
    result = stop_policies.validate_against_control(rows, fees=FEES)
    assert result["ok"] is False
    assert [m["ic_order_id"] for m in result["mismatches"]] == ["W-BAD"]


def test_validate_excludes_force_closed_rows_and_passes_nothing_when_nothing_is_compared():
    rows = [_arm_row("W-FC", "width-5", 50.0, "force_closed", put_stop_cost=1.71)]
    result = stop_policies.validate_against_control(rows, fees=FEES)
    assert result["compared"] == 0 and result["skipped_force_closed"] == 1
    assert result["ok"] is False  # no rows compared -> not a pass


# --------------------------------------------------------------------------- the ratio grid (#12)
def test_a_raw_spec_scores_the_same_as_the_named_policy_it_matches():
    row = _row(put_max_cost=2.0, call_max_cost=0.2, put_settle_value=2.5, call_settle_value=0.0)
    assert stop_policies.derive(row, "control", fees=FEES) == stop_policies.derive(
        row, ("net", 0.95), fees=FEES
    )


def test_a_stopped_row_censors_every_ratio_above_where_it_actually_stopped():
    """`*_max_cost` stops being recorded the moment a side stops, so a looser threshold's answer
    was never observed. Reporting "did not fire" there would be exactly backwards."""
    row = _row(
        status="stopped", put_max_cost=1.8, call_max_cost=0.1, put_settle_value=2.4, call_settle_value=0.0
    )
    assert stop_policies.censored_above(row) == 1.0
    grid = stop_policies.score_grid(row, fees=FEES, ratios=(0.85, 0.95, 1.0, 1.05, 1.25))
    for ratio in (0.85, 0.95, 1.0):
        assert grid[ratio]["censored"] is False, ratio
        assert grid[ratio]["derivable"] is True and grid[ratio]["put_fired"] is True
    for ratio in (1.05, 1.25):
        assert grid[ratio]["censored"] is True, ratio
        assert grid[ratio]["derivable"] is False and grid[ratio]["pnl"] is None


def test_a_row_that_ran_to_settlement_censors_nothing():
    row = _row(
        status="expired", put_max_cost=1.2, call_max_cost=0.3, put_settle_value=0.0, call_settle_value=0.0
    )
    assert stop_policies.censored_above(row) is None
    grid = stop_policies.score_grid(row, fees=FEES, ratios=stop_policies.GRID_RATIOS)
    assert all(not p["censored"] and p["derivable"] for p in grid.values())


def test_the_grid_fires_less_as_the_threshold_loosens():
    row = _row(
        status="expired", put_max_cost=1.71, call_max_cost=0.2, put_settle_value=2.0, call_settle_value=0.0
    )
    grid = stop_policies.score_grid(row, fees=FEES, ratios=(0.85, 0.95, 1.05))
    assert grid[0.85]["put_fired"] is True
    assert grid[0.95]["put_fired"] is True  # >= threshold, so it fires at exactly 0.95
    assert grid[1.05]["put_fired"] is False  # never reached 1.89


# --------------------------------------------------------------------------- the shadow ledger (#12)
def test_shadow_settle_compares_like_with_like_fees():
    """The real book paid the opening fee and one closing fee; the shadow book pays the opening fee
    too. Until 2026-09-24 the shadow was charged closing fees only, so every stop_cost was high by
    the opening fee -- 274.49 on this row, where holding would really have paid 272.33 more."""
    row = _row(
        status="stopped",
        ic_order_id="ic-1",
        trade_date="2026-08-14",
        risk_profile="width-5",
        put_max_cost=1.8,
        call_max_cost=0.1,
        put_settle_value=0.0,
        call_settle_value=0.0,
        wing_width=20,
        quantity=1,
        pnl=-90.0,
        fees=OPEN + ONE,
    )
    out = stop_policies.shadow_settle(row, fees=FEES)
    assert out["stop_fired"] is True
    assert out["realized_net"] == round(-90.0 - OPEN - ONE, 2)  # -98.33
    assert out["shadow_settle_net"] == round(180.0 - OPEN, 2)  # 173.11: both OTM, the opening fee only
    assert out["stop_cost"] == 271.44
    assert out["capital_at_risk"] == (20 - 1.8) * 100


def test_a_book_that_never_stops_has_no_stop_cost():
    """The advisor-era control never stops, and read a stop cost of $1.5-2.2k a session: the
    opening fee, counted as the stop's price."""
    row = _row(
        status="expired",
        put_max_cost=0.4,
        call_max_cost=0.3,
        put_settle_value=0.0,
        call_settle_value=0.0,
        pnl=180.0,
        fees=OPEN,
    )
    assert stop_policies.shadow_settle(row, fees=FEES)["stop_cost"] == 0.0


def test_max_adverse_excursion_is_measured_in_credit_not_spot():
    row = _row(
        put_max_cost=2.7,
        call_max_cost=0.9,
        put_settle_value=0.0,
        call_settle_value=0.0,
        put_mae_spot=5000.0,
        pnl=0.0,
        fees=0.0,
    )
    assert stop_policies.shadow_settle(row, fees=FEES)["mae_over_credit"] == 1.5


def test_favourable_excursion_is_null_rather_than_zero():
    row = _row(
        put_max_cost=1.0, call_max_cost=0.5, put_settle_value=0.0, call_settle_value=0.0, pnl=10.0, fees=1.0
    )
    assert stop_policies.shadow_settle(row, fees=FEES)["mfe_over_credit"] is None


def test_a_real_stop_recorded_a_rounding_unit_under_its_trigger_still_fired():
    """width-10, 2026-08-11: a 0.95 x 1.625 = 1.54375 trigger, the fill recorded to 4 decimals as
    1.5437. Compared exactly, the stop "never fired" and the side was priced as held to settlement."""
    row = _row(
        net_credit=1.625,
        put_credit=0.8125,
        call_credit=0.8125,
        status="stopped",
        stop_trigger_current=0.95,
        put_stop_cost=1.5437,
        put_max_cost=1.5437,
        call_max_cost=0.4,
        put_settle_value=10.0,
        call_settle_value=0.0,
    )
    out = stop_policies.derive(row, ("net", 0.95), fees=FEES)
    assert out["put_fired"] is True
    assert out["pnl"] == round((0.8125 - 1.5437) * 100 + 0.8125 * 100, 2)
