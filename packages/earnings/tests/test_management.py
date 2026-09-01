"""The managed-exit rules: what happens to a position, and whether we may act on it yet.

Positions used to close unconditionally the morning after entry. They are managed now, so these
assert the rules that replace that sweep — and, as much, the ones that must NOT fire:

  - a loser closes the first morning (post-earnings drift continues, it does not revert),
  - a winner may carry, but never past the session cap,
  - the strategy's own thresholds still own the target and the stop,
  - the same-session backstop cannot preempt a multi-day hold,
  - an exit seen before the execution window is recorded and taken later, not lost.
"""

import json
from datetime import datetime, timedelta

import pytest

from cherrypick.earnings import management

ET = management.ET


def at(hhmm, day="2026-08-12"):
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=ET)


# An iron fly on AAPL: short the 190 straddle, wings at 180/200, entered for 5.00 of credit.
LEGS = [
    {"symbol": "AAPL  260821C00190000", "action": "Sell to Open", "quantity": 1},
    {"symbol": "AAPL  260821P00190000", "action": "Sell to Open", "quantity": 1},
    {"symbol": "AAPL  260821C00200000", "action": "Buy to Open", "quantity": 1},
    {"symbol": "AAPL  260821P00180000", "action": "Buy to Open", "quantity": 1},
]


def trade(strategy="iron_fly", credit=5.00, expiration="2026-08-21", legs=None):
    return {
        "order_id": "T1",
        "strategy": strategy,
        "symbol": "AAPL",
        "expiration": expiration,
        "entry_credit": credit,
        "legs_json": json.dumps(LEGS if legs is None else legs),
        "opened_at": None,
    }


def snapshot(exit_debit, *, spot=190.0, spread=0.05, ok=True):
    """Quotes that price the structure to exactly `exit_debit`.

    Both shorts are bought back at ask and both longs sold at bid, so putting the whole debit on one
    short's ask and zeroing the rest gives an exact, readable target.
    """
    # The gate judges legs in percent AND in money now, so a "wide" stub must carry a leg that is
    # genuinely wide on both readings -- a synthesized aggregate over zero-width quotes tests
    # nothing. Kept off the priced leg so the exit debit the other tests read stays exact.
    wide = spread if spread > 0.25 else 0.0
    quotes = {
        LEGS[0]["symbol"]: {"bid": exit_debit, "ask": exit_debit, "mid": max(exit_debit, 0.01)},
        LEGS[1]["symbol"]: {"bid": 0.0, "ask": wide, "mid": max(wide / 2, 0.01)},
        LEGS[2]["symbol"]: {"bid": 0.0, "ask": 0.0, "mid": 0.01},
        LEGS[3]["symbol"]: {"bid": 0.0, "ask": 0.0, "mid": 0.01},
    }
    for q in quotes.values():
        q.setdefault("delta", None)
        q.setdefault("iv", None)
    return {"ok": ok, "quotes": quotes, "spot": spot, "max_spread_pct": spread, "source": "stream"}


CONFIG = {
    "strategies": {
        "iron_fly": {"profit_target_pct": 0.25, "stop_loss_credit_multiple": 1.5},
        "iron_condor": {"profit_target_pct": 0.50, "stop_loss_credit_multiple": 1.5},
    },
    "management": {},
}


def evaluate(t, snap, *, now=None, sessions_held=1, first=True, config=None):
    return management.evaluate(
        t,
        snap,
        config or CONFIG,
        now=now or at("10:00"),
        sessions_held=sessions_held,
        is_first_check_of_day=first,
    )


# --------------------------------------------------------------------------- the strategy still owns its thresholds
def test_the_configured_profit_target_closes_the_position():
    """25% of 5.00 credit means closing costs 3.75 or less. The threshold lives in the strategy's
    own config and is read by its own evaluate_position — this module never restates it."""
    decision = evaluate(trade(), snapshot(3.50))
    assert decision.action == "close_all" and decision.reason == "profit_target"


def test_short_of_the_target_and_profitable_the_position_carries():
    """The change from the old sweep: this used to close at 09:45 regardless."""
    decision = evaluate(trade(), snapshot(4.50))
    assert decision.action == "hold" and decision.reason == "working"


def test_the_configured_stop_closes_the_position():
    """1.5x of 5.00 credit: closing at 7.50 or worse."""
    decision = evaluate(trade(), snapshot(8.00))
    assert decision.action == "close_all" and decision.reason == "stop_loss"


def test_a_condor_keeps_its_own_looser_target():
    """A condor profits across a band rather than at a point, so it manages at 50% where the fly
    manages at 25% — the two must not be collapsed into one number."""
    at_fly_target = snapshot(3.50)  # 30% captured
    assert evaluate(trade("iron_condor"), at_fly_target).action == "hold"
    assert evaluate(trade("iron_condor"), snapshot(2.00)).action == "close_all"


# --------------------------------------------------------------------------- the PEAD gate
def test_a_loser_closes_on_the_first_morning():
    """The gap that put it there tends to continue rather than revert, so a losing position is not
    given more time to come back."""
    decision = evaluate(trade(), snapshot(6.00), first=True)
    assert decision.action == "close_all" and decision.reason == "pead_loser"


def test_a_winner_is_not_closed_by_the_pead_gate():
    assert evaluate(trade(), snapshot(4.50), first=True).action == "hold"


def test_a_loser_later_in_the_day_is_not_force_closed():
    """The gate is about the first reliable marks after the gap, not about every tick — a position
    dipping negative at 11:00 has already survived the morning's decision."""
    assert evaluate(trade(), snapshot(6.00), first=False).action == "hold"


def test_the_pead_gate_can_be_turned_off_per_strategy():
    config = {**CONFIG, "management": {"iron_fly": {"close_losers_first_morning": False}}}
    assert evaluate(trade(), snapshot(6.00), first=True, config=config).action == "hold"


def test_a_position_exactly_at_breakeven_is_treated_as_a_loser():
    """Nothing has been earned, and the drift risk of holding is the same — so it closes."""
    decision = evaluate(trade(), snapshot(5.00), first=True)
    assert decision.action == "close_all" and decision.reason == "pead_loser"


# --------------------------------------------------------------------------- the session cap
def test_a_winner_closes_at_the_session_cap():
    """Residual crush is spent by roughly the third session; what is left is direction, which this
    system has no edge on."""
    decision = evaluate(trade(), snapshot(4.50), sessions_held=3)
    assert decision.action == "close_all" and decision.reason == "max_hold"


def test_a_winner_inside_the_cap_still_carries():
    assert evaluate(trade(), snapshot(4.50), sessions_held=2).action == "hold"


def test_the_cap_does_not_apply_to_the_calendars():
    """They are held across expirations by construction and stop themselves on front DTE."""
    config = {
        "strategies": {"atm_calendar": {"profit_target_pct": 0.15, "exit_days_before_front_expiration": 5}},
        "management": {},
    }
    decision = evaluate(trade("atm_calendar", credit=-3.0), snapshot(-3.2), sessions_held=9, config=config)
    assert decision.action == "hold"


def test_an_unknown_hold_length_does_not_close_the_position():
    """session_span returns None on an unusable timestamp; treating that as "many sessions" would
    close a position on the strength of a missing measurement."""
    assert evaluate(trade(), snapshot(4.50), sessions_held=None).action == "hold"


# --------------------------------------------------------------------------- the same-session backstop
def test_the_four_hour_backstop_cannot_preempt_a_multi_day_hold():
    """18 hours pass between a 15:45 entry and the first morning mark. Left at its 240-minute
    default the backstop would fire on every position before any management rule was reached, and
    multi-day holds would be unreachable — so the policy injects a value past any hold it could
    preempt.

    Both times are taken from the tick being evaluated. Reading the machine clock here measured the
    gap between the fixture and whenever the suite happened to run, which is how the policy's
    ten-day ceiling was silently overshot and this rule looked unreachable."""
    now = at("10:00")
    t = trade()
    t["opened_at"] = (now - timedelta(hours=18)).timestamp()
    assert evaluate(t, snapshot(4.50), now=now).action == "hold"


def test_lowering_the_backstop_re_enables_a_same_session_close():
    """It is superseded, not deleted — a shorter hold is still expressible."""
    now = at("10:00")
    t = trade()
    t["opened_at"] = (now - timedelta(hours=5)).timestamp()
    config = {**CONFIG, "management": {"exit_after_announcement_minutes": 240}}
    decision = evaluate(t, snapshot(4.50), now=now, config=config)
    assert decision.action == "close_all" and decision.reason == "iv_crush_backstop"


def test_the_time_rules_follow_the_tick_and_not_the_machine_clock():
    """The same position, evaluated at two different moments, must decide differently.

    A rule that reads the machine clock answers identically whatever tick it is handed, so this is
    what separates the two. It is not hypothetical: the front-DTE stop and the four-hour backstop
    both did, which made one of them fire or not depending on what day the suite happened to run,
    and hid a real defect behind a test that passed most days.
    """
    # The calendars' front-expiration stop: five days out holds, two days out closes.
    cal_config = {
        "strategies": {"atm_calendar": {"profit_target_pct": 0.15, "exit_days_before_front_expiration": 5}},
        "management": {},
    }
    cal = trade("atm_calendar", credit=-3.0, expiration="2026-08-21")
    assert evaluate(cal, snapshot(-3.2), now=at("10:00", "2026-08-12"), config=cal_config).action == "hold"
    late = evaluate(cal, snapshot(-3.2), now=at("10:00", "2026-08-19"), config=cal_config)
    assert late.action == "close_all" and late.reason == "time_exit"

    # The credit strategies' post-announcement backstop, at a 240-minute setting.
    fly_config = {**CONFIG, "management": {"exit_after_announcement_minutes": 240}}
    entry = at("15:45", "2026-08-11")
    fly = trade()
    fly["opened_at"] = entry.timestamp()
    early = evaluate(fly, snapshot(4.50), now=entry + timedelta(hours=1), config=fly_config)
    assert early.action == "hold"
    later = evaluate(fly, snapshot(4.50), now=entry + timedelta(hours=5), config=fly_config)
    assert later.action == "close_all" and later.reason == "iv_crush_backstop"


# --------------------------------------------------------------------------- the pin guard
def test_a_short_strike_on_spot_closes_late_on_expiration_day():
    """Assignment is decided by the settlement print, so the guard fires on proximity rather than on
    being in the money — the outcome is still undetermined at 15:30."""
    t = trade(expiration="2026-08-12")
    decision = evaluate(t, snapshot(4.50, spot=190.4), now=at("15:30"))
    assert decision.action == "close_all" and decision.reason == "pin_risk"


def test_the_pin_guard_does_not_fire_earlier_in_the_day():
    t = trade(expiration="2026-08-12")
    assert evaluate(t, snapshot(4.50, spot=190.4), now=at("11:00")).action == "hold"


def test_the_pin_guard_does_not_fire_before_expiration_day():
    t = trade(expiration="2026-08-21")
    assert evaluate(t, snapshot(4.50, spot=190.4), now=at("15:30")).action == "hold"


def test_a_short_strike_far_from_spot_is_not_pin_risk():
    t = trade(expiration="2026-08-12")
    assert evaluate(t, snapshot(4.50, spot=205.0), now=at("15:30")).action == "hold"


def test_the_pin_guard_skips_when_spot_is_unknown():
    """An unsubscribed underlying must not close positions; the check simply does not run."""
    t = trade(expiration="2026-08-12")
    snap = snapshot(4.50)
    snap["spot"] = None
    assert evaluate(t, snap, now=at("15:30")).action == "hold"


def test_only_short_strikes_carry_pin_risk():
    """A long leg is exercised at our discretion, not assigned to us."""
    assert management.short_strikes(LEGS) == [190.0, 190.0]


def test_a_strike_is_read_from_the_occ_symbol():
    assert management.strike_from_occ("AAPL  260821C00190000") == 190.0
    assert management.strike_from_occ("garbage") is None


# --------------------------------------------------------------------------- execution gates
def test_a_verdict_before_the_execution_window_is_gated():
    """Recorded, not lost: the next tick reconsiders. This is what makes a 09:41 exit on a 09:33
    target explicable rather than looking like a late reaction."""
    assert (
        management.execution_gate(snapshot(3.50), CONFIG, "iron_fly", now=at("09:33")) == "before_exec_window"
    )


def test_the_gate_opens_at_the_configured_time():
    assert management.execution_gate(snapshot(3.50), CONFIG, "iron_fly", now=at("09:40")) is None


def test_quotes_wider_than_the_policy_are_not_acted_on():
    """An opening-auction width can exceed the edge being managed; a target computed off that mid is
    arithmetic, not a price."""
    wide = snapshot(3.50, spread=0.80)
    assert management.execution_gate(wide, CONFIG, "iron_fly", now=at("10:00")) == "spread_too_wide"


def test_a_penny_wide_leg_is_not_too_wide_to_close():
    """The win case: a short that has done its job quotes 0.00/0.01 -- a one-cent buyback and, as a
    ratio, a 200% spread. Before 2026-08-31 this refused 32 distinct positions their profit-target
    exits (5,695 gated ticks at exactly 2.000) and every one rode to expiry instead. Verified by
    restoring the aggregate percentage test and watching this admit-case refuse."""
    snap = snapshot(0.01)
    snap["quotes"][LEGS[1]["symbol"]] = {"bid": 0.0, "ask": 0.01, "mid": 0.005, "delta": None, "iv": None}
    snap["max_spread_pct"] = 2.0  # the aggregate the old gate read
    assert management.execution_gate(snap, CONFIG, "iron_fly", now=at("10:00")) is None


def test_the_two_readings_are_judged_per_leg():
    """The widest-by-percent and the widest-by-money can be different legs; two separate maxima
    would refuse a structure neither leg justifies."""
    snap = snapshot(2.0)
    snap["quotes"][LEGS[1]["symbol"]] = {"bid": 0.0, "ask": 0.01, "mid": 0.005, "delta": None, "iv": None}  # wide pct, 1c
    snap["quotes"][LEGS[2]["symbol"]] = {"bid": 5.0, "ask": 5.6, "mid": 5.3, "delta": None, "iv": None}  # 60c, 11% pct
    assert management.execution_gate(snap, CONFIG, "iron_fly", now=at("10:00")) is None


def test_a_snapshot_without_quotes_keeps_the_aggregate_percentage_test():
    """A mark that carries no per-leg quotes must not silently admit more than it used to."""
    bare = {"ok": True, "max_spread_pct": 2.0}
    assert management.execution_gate(bare, CONFIG, "iron_fly", now=at("10:00")) == "spread_too_wide"


def test_an_unusable_mark_gates_everything():
    refused = {"ok": False, "reason": "missing_leg_quotes"}
    assert management.execution_gate(refused, CONFIG, "iron_fly", now=at("10:00")) == "unusable_mark"


def test_a_normal_mark_in_the_window_passes():
    assert management.execution_gate(snapshot(3.50), CONFIG, "iron_fly", now=at("10:00")) is None


# --------------------------------------------------------------------------- policy resolution
def test_a_per_strategy_override_wins_over_the_common_key():
    config = {"management": {"hold_winners_max_days": 3, "iron_fly": {"hold_winners_max_days": 1}}}
    assert management.policy_for("iron_fly", config)["hold_winners_max_days"] == 1
    assert management.policy_for("iron_condor", config)["hold_winners_max_days"] == 3


def test_an_absent_management_block_falls_back_to_the_defaults():
    assert management.policy_for("iron_fly", {})["exec_window_start"] == "09:40"


def test_an_unpriceable_position_holds_rather_than_guessing():
    snap = snapshot(4.50)
    snap["quotes"].pop(LEGS[2]["symbol"])
    decision = evaluate(trade(), snap)
    assert decision.action == "hold" and decision.reason == "unpriceable"


def test_an_unknown_strategy_is_never_acted_on():
    assert evaluate(trade("some_new_strategy"), snapshot(3.50)).action == "hold"


def test_the_unrealized_mark_uses_the_same_arithmetic_as_the_realised_close():
    """A mark that disagreed with the P&L eventually recorded would make every excursion column a
    different measurement from the result it is supposed to explain."""
    assert management.unrealized_pnl({"entry_credit": 5.0}, 3.5) == pytest.approx(150.0)
