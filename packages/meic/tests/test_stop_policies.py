"""stop_policies.derive -- read-side stop-policy scoring computed from a fully-marked open
position's recorded path (put/call_max_cost, put/call_settle_value, put/call_touch_time), not run
as separate entry streams. Pins: each policy's fire condition and fill-price proxy, the
derivable=False guard for missing fields, the fee schedule (full IC vs one side vs none), and
validate_against_control's reconstruction of the real 0.95x-net mechanism from recorded fields.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import stop_policies  # noqa: E402

FEE_ONE = 4.49
FEE_FULL = 6.89


def _fee_one_side(_symbol):
    return FEE_ONE


def _fee_full_ic(_symbol):
    return FEE_FULL


def _row(**overrides):
    row = {
        "symbol": "SPX",
        "put_credit": 0.9,
        "call_credit": 0.9,
        "net_credit": 1.8,
        "put_max_cost": None,
        "call_max_cost": None,
        "put_settle_value": None,
        "call_settle_value": None,
        "put_touch_time": None,
        "call_touch_time": None,
    }
    row.update(overrides)
    return row


def test_stop_none_never_fires_and_uses_settle_value():
    row = _row(put_settle_value=0.0, call_settle_value=3.0)
    out = stop_policies.derive(row, "stop-none", fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    assert out["derivable"] is True
    assert out["put_fired"] is False and out["call_fired"] is False
    # put: 0.9 - 0.0 = 0.9 credit kept; call: 0.9 - 3.0 = -2.1 loss; both x100, no fee (no fired side)
    assert out["pnl"] == round((0.9 * 100) + (-2.1 * 100), 2)
    assert out["fee"] == 0.0


def test_stop_0_75_net_fires_on_net_credit_basis():
    thresh = 0.75 * 1.8  # = 1.35
    row = _row(put_max_cost=thresh + 0.1, call_max_cost=0.5, put_settle_value=0.0, call_settle_value=0.0)
    out = stop_policies.derive(row, "stop-0.75-net", fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    assert out["put_fired"] is True  # crossed the net-based threshold
    assert out["call_fired"] is False  # 0.5 never reached 1.35
    assert out["fee"] == FEE_ONE  # exactly one side fired
    put_pnl = round((0.9 - (thresh + 0.1)) * 100, 2)
    call_pnl = round((0.9 - 0.0) * 100, 2)
    # pnl is GROSS (matches ic_trades.pnl's own convention) -- fee is a separate field, not netted in.
    assert out["pnl"] == round(put_pnl + call_pnl, 2)


def test_stop_2_0_side_fires_on_each_sides_own_credit():
    row = _row(
        put_credit=0.5,
        call_credit=1.3,
        net_credit=1.8,
        put_max_cost=1.1,  # 2.0 x 0.5 = 1.0 -> fires
        call_max_cost=2.5,  # 2.0 x 1.3 = 2.6 -> does NOT fire
        put_settle_value=0.0,
        call_settle_value=0.2,
    )
    out = stop_policies.derive(row, "stop-2.0-side", fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    assert out["put_fired"] is True
    assert out["call_fired"] is False
    assert out["fee"] == FEE_ONE


def test_both_sides_firing_charges_the_full_ic_fee():
    row = _row(put_max_cost=100.0, call_max_cost=100.0, put_settle_value=0.0, call_settle_value=0.0)
    out = stop_policies.derive(row, "stop-0.75-net", fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    assert out["put_fired"] is True and out["call_fired"] is True
    assert out["fee"] == FEE_FULL


def test_strike_touch_fires_on_recorded_touch_time_not_a_cost_threshold():
    row = _row(
        put_touch_time="2026-08-07 10:00:00",
        put_max_cost=0.95,  # the fill proxy used once "fired"
        call_max_cost=0.4,
        put_settle_value=0.0,
        call_settle_value=0.0,
    )
    out = stop_policies.derive(row, "strike-touch", fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    assert out["put_fired"] is True  # touched
    assert out["call_fired"] is False  # never touched, regardless of its max_cost
    assert out["fee"] == FEE_ONE


def test_derivable_false_when_a_fired_sides_max_cost_is_missing():
    """A side flagged as fired but with no recorded max_cost (pre-Phase-1e row, or a row that
    never got marked) must report derivable=False, never silently price the fill at 0."""
    row = _row(put_touch_time="2026-08-07 10:00:00", put_max_cost=None, call_settle_value=0.0)
    out = stop_policies.derive(row, "strike-touch", fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    assert out["derivable"] is False
    assert out["pnl"] is None


def test_derivable_false_when_a_held_sides_settle_value_is_missing():
    row = _row(put_settle_value=None, call_settle_value=0.0)
    out = stop_policies.derive(row, "stop-none", fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    assert out["derivable"] is False


def test_unreachable_threshold_never_fires_no_error():
    """A max cost that never reached the threshold must resolve to not-fired cleanly, never
    raise -- the reachability question is answered by the data, not asserted."""
    row = _row(put_max_cost=0.2, call_max_cost=0.2, put_settle_value=0.0, call_settle_value=0.0)
    out = stop_policies.derive(row, "stop-2.0-side", fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    # put_credit/call_credit default to 0.9 -> threshold 2.0*0.9=1.8, well above the 0.2 max reached.
    assert out["put_fired"] is False and out["call_fired"] is False
    assert out["derivable"] is True


def test_unknown_policy_name_raises_keyerror():
    row = _row()
    try:
        stop_policies.derive(row, "not-a-real-policy", fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


# --------------------------------------------------------------------------- validate_against_control


def _control_row(order_id, real_pnl, status, put_credit=0.9, call_credit=0.9, **overrides):
    row = {
        "ic_order_id": order_id,
        "risk_profile": "control",
        "symbol": "SPX",
        "status": status,
        "pnl": real_pnl,
        "put_credit": put_credit,
        "call_credit": call_credit,
        "net_credit": round(put_credit + call_credit, 4),
    }
    row.update(overrides)
    return row


def test_validate_against_control_reconstructs_a_real_stop():
    """A REAL control stop: put_max_cost equals the actual stop fill (the trigger fires on the
    same cost_now _max_cost_updates just recorded as a new high), so re-deriving the 0.95x-net
    policy from the recorded fields must reproduce the real pnl exactly (within the tolerance)."""
    thresh = 0.95 * 1.8  # 1.71
    fill = thresh  # the real trigger fires AT the threshold
    # real_pnl is GROSS (ic_trades.pnl's own convention) -- fee is tracked in a separate column.
    real_pnl = round((0.9 - fill) * 100 + (0.9 - 0.0) * 100, 2)
    rows = [
        _control_row(
            "C-1",
            real_pnl,
            "stopped",
            put_max_cost=fill,
            call_max_cost=0.3,
            put_settle_value=None,  # a real stop row may never reach the settle branch
            call_settle_value=0.0,
        )
    ]
    result = stop_policies.validate_against_control(
        rows, fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic
    )
    assert result["compared"] == 1
    assert result["mismatches"] == []
    assert result["ok"] is True


def test_validate_against_control_reconstructs_a_real_expiry():
    real_pnl = round((0.9 - 0.0) * 100 + (0.9 - 0.0) * 100, 2)  # both OTM, no fee
    rows = [_control_row("C-2", real_pnl, "expired", put_settle_value=0.0, call_settle_value=0.0)]
    result = stop_policies.validate_against_control(
        rows, fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic
    )
    assert result["compared"] == 1
    assert result["ok"] is True


def test_validate_against_control_flags_a_real_mismatch():
    """A genuine derivation bug (or a corrupted row) must be caught, not silently averaged away."""
    rows = [
        _control_row(
            "C-BAD", 999.0, "expired", put_settle_value=0.0, call_settle_value=0.0
        )  # real pnl is nonsense vs the recorded fields
    ]
    result = stop_policies.validate_against_control(
        rows, fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic
    )
    assert result["ok"] is False
    assert len(result["mismatches"]) == 1
    assert result["mismatches"][0]["ic_order_id"] == "C-BAD"


def test_validate_against_control_excludes_force_closed_and_other_profiles():
    rows = [
        _control_row("C-FC", 50.0, "force_closed", put_settle_value=0.0, call_settle_value=0.0),
        {"ic_order_id": "OTHER", "risk_profile": "open", "status": "expired", "pnl": 10.0},
        _control_row("C-OPEN", None, "open"),  # still open -> no recorded pnl
    ]
    result = stop_policies.validate_against_control(
        rows, fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic
    )
    assert result["compared"] == 0
    assert result["skipped_force_closed"] == 1
    assert result["ok"] is False  # no rows compared -> not a pass


# --------------------------------------------------------------------------- the ratio grid (#12)
def test_a_raw_spec_scores_the_same_as_the_named_policy_it_matches():
    """The grid passes (basis, multiple) instead of a name, so a swept ratio needs no POLICIES
    entry. At 0.95 on the net basis that must be control's own policy, byte for byte."""
    row = _row(put_max_cost=2.0, call_max_cost=0.2, put_settle_value=2.5, call_settle_value=0.0)
    named = stop_policies.derive(row, "control", fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    spec = stop_policies.derive(row, ("net", 0.95), fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    assert named == spec


def test_a_stopped_row_censors_every_ratio_above_where_it_actually_stopped():
    """The trap the grid would otherwise walk into. `*_max_cost` stops being recorded the moment a
    side stops, so a looser threshold's answer was never observed. Reporting "did not fire" there
    would be exactly backwards: nothing was watched, so nothing can be concluded."""
    # Stopped with a max cost of 1.80 against a 1.80 net credit -> the path says nothing above 1.0x.
    row = _row(status="stopped", put_max_cost=1.8, call_max_cost=0.1,
               put_settle_value=2.4, call_settle_value=0.0)
    assert stop_policies.censored_above(row) == 1.0

    grid = stop_policies.score_grid(
        row, fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic, ratios=(0.85, 0.95, 1.0, 1.05, 1.25)
    )
    # At or below where it stopped: answerable, and the put side is known to have fired.
    for ratio in (0.85, 0.95, 1.0):
        assert grid[ratio]["censored"] is False, ratio
        assert grid[ratio]["derivable"] is True and grid[ratio]["put_fired"] is True
    # Above it: refused, not answered.
    for ratio in (1.05, 1.25):
        assert grid[ratio]["censored"] is True, ratio
        assert grid[ratio]["derivable"] is False and grid[ratio]["pnl"] is None


def test_a_row_that_ran_to_settlement_censors_nothing():
    """`open` runs with no per-side stop, so its paths are complete and every ratio is answerable.
    That is why the sweep is scored over that arm."""
    row = _row(status="expired", put_max_cost=1.2, call_max_cost=0.3,
               put_settle_value=0.0, call_settle_value=0.0)
    assert stop_policies.censored_above(row) is None
    grid = stop_policies.score_grid(
        row, fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic, ratios=stop_policies.GRID_RATIOS
    )
    assert all(not p["censored"] and p["derivable"] for p in grid.values())


def test_the_grid_fires_less_as_the_threshold_loosens():
    """The curve's basic shape, and the thing the whole sweep is for: a looser stop cannot fire
    more often than a tighter one over the same path."""
    row = _row(status="expired", put_max_cost=1.71, call_max_cost=0.2,
               put_settle_value=2.0, call_settle_value=0.0)  # 1.71 / 1.8 = 0.95x exactly
    grid = stop_policies.score_grid(
        row, fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic, ratios=(0.85, 0.95, 1.05)
    )
    assert grid[0.85]["put_fired"] is True
    assert grid[0.95]["put_fired"] is True    # >= threshold, so it fires at exactly 0.95
    assert grid[1.05]["put_fired"] is False   # never reached 1.89


# --------------------------------------------------------------------------- the shadow ledger (#12)
def test_shadow_settle_prices_the_unstopped_counterfactual_and_names_the_stop_cost():
    row = _row(status="stopped", ic_order_id="ic-1", trade_date="2026-08-14", risk_profile="width-5",
               put_max_cost=1.8, call_max_cost=0.1, put_settle_value=0.0, call_settle_value=0.0,
               wing_width=20, quantity=1, pnl=-90.0, fees=4.49)
    out = stop_policies.shadow_settle(row, fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)

    assert out["stop_fired"] is True
    assert out["realized_net"] == -94.49                      # -90.00 pnl - 4.49 fees
    # Unstopped, both sides settle worthless: the whole 1.80 credit is kept, and expiry costs no fee.
    assert out["shadow_settle_net"] == 180.0
    assert out["stop_cost"] == 274.49                         # holding would have paid this much more
    assert out["capital_at_risk"] == (20 - 1.8) * 100         # (width - credit) x 100 x qty


def test_max_adverse_excursion_is_measured_in_credit_not_spot():
    """stop_trigger_ratio is compared against COST over net credit. The *_mae_spot columns measure
    a different quantity that no threshold in this module reads."""
    row = _row(put_max_cost=2.7, call_max_cost=0.9, put_settle_value=0.0, call_settle_value=0.0,
               put_mae_spot=5000.0, pnl=0.0, fees=0.0)
    out = stop_policies.shadow_settle(row, fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic)
    assert out["mae_over_credit"] == 1.5                      # worst side 2.70 / 1.80 net credit


def test_favourable_excursion_is_null_rather_than_zero():
    """It is not recorded anywhere and the stream cache keeps no quote history to rebuild it from.
    A 0.0 here would be the misleadingly-precise zero this suite has a rule about."""
    row = _row(put_max_cost=1.0, call_max_cost=0.5, put_settle_value=0.0, call_settle_value=0.0,
               pnl=10.0, fees=1.0)
    assert stop_policies.shadow_settle(
        row, fee_one_side=_fee_one_side, fee_full_ic=_fee_full_ic
    )["mfe_over_credit"] is None
