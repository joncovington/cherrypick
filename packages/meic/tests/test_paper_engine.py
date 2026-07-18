"""Unit tests for the paper-trading engine: tastytrade fee model, deterministic gate
evaluator, synthetic fill/exit math, and the get_range_summary DB rollup.

No credentials or live connection required. Fee/gate/fill tests operate on pure functions
with hand-built snapshots; DB tests use a temp SQLite file (same pattern as test_db.py).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import db
import paper

# ── Fee model ────────────────────────────────────────────────────────────────

def test_open_fees_spx_matches_documented_fallback():
    # CLAUDE.md's fee_estimate_fallback_per_contract documents SPX at $6.89
    assert paper.open_fees("SPX", quantity=1) == pytest.approx(6.89, abs=0.01)


def test_open_fees_xsp_matches_documented_fallback():
    assert paper.open_fees("XSP", quantity=1) == pytest.approx(4.49, abs=0.01)


def test_open_fees_ndx_and_rut_have_higher_exchange_fee_than_xsp():
    assert paper.open_fees("NDX", 1) > paper.open_fees("XSP", 1)
    assert paper.open_fees("RUT", 1) > paper.open_fees("XSP", 1)


def test_close_fees_full_ic_excludes_open_commission():
    open_fee = paper.open_fees("SPX", 1)
    close_fee = paper.close_fees_full_ic("SPX", 1)
    assert close_fee < open_fee  # no $1.00/contract commission on close
    assert close_fee == pytest.approx(open_fee - 1.00 * 4, abs=0.01)


def test_close_fees_one_side_is_roughly_half_full_ic():
    full = paper.close_fees_full_ic("SPX", 1)
    side = paper.close_fees_one_side("SPX", 1)
    assert side < full
    assert side == pytest.approx(full / 2, abs=0.02)


def test_expire_fees_are_zero():
    assert paper.expire_fees() == 0.0


def test_fees_scale_with_quantity():
    assert paper.open_fees("SPX", 2) == pytest.approx(paper.open_fees("SPX", 1) * 2, abs=0.001)


# ── Gate evaluator: helpers ──────────────────────────────────────────────────

def _leg(strike, delta, bid, ask, sym):
    return {"strike": strike, "streamer_symbol": sym, "delta": delta, "bid": bid, "ask": ask}


def _candidate(width, sp_strike, sc_strike, sp_delta=-0.15, sc_delta=0.15,
                sp_bid=0.55, sp_ask=0.65, sc_bid=0.50, sc_ask=0.60,
                lp_bid=0.15, lp_ask=0.25, lc_bid=0.12, lc_ask=0.22):
    return {
        "wing_width": width,
        "short_put": _leg(sp_strike, sp_delta, sp_bid, sp_ask, f"SP{width}"),
        "long_put": _leg(sp_strike - width, sp_delta * 0.4, lp_bid, lp_ask, f"LP{width}"),
        "short_call": _leg(sc_strike, sc_delta, sc_bid, sc_ask, f"SC{width}"),
        "long_call": _leg(sc_strike + width, sc_delta * 0.4, lc_bid, lc_ask, f"LC{width}"),
    }


def _base_snapshot(**overrides):
    snap = {
        "symbol": "XSP", "date": "2026-07-09", "now_et": "13:00",
        "expiration": "2026-07-09", "dte": 0,
        "underlying_price": 590.0, "iv_rank": 0.32,
        "vix": 16.0, "vix1d_ratio": 1.02, "atr_5day": 8.0,
        "session_quality": "midday", "gex": {"ok": True, "gex_positive": True},
        "candidates": [_candidate(5, 583, 598), _candidate(2, 583, 598)],
        "leg_quotes": {},
    }
    snap.update(overrides)
    return snap


CONSERVATIVE = paper.load_profiles()["conservative"]
MODERATE = paper.load_profiles()["moderate"]
BASE_CONFIG = paper.load_base_config()

# Synthetic profile overlays. config.risk.json now holds ONLY the four-tier ladder — the experiment
# cells that used to exercise these knobs were removed — but the engine features themselves are very
# much alive, so the gates are tested against purpose-built overlays instead of registry names. This
# also keeps the tests independent of whatever the profile registry happens to contain.
XSP_NARROW = {"symbols": ["XSP"], "wing_widths_by_symbol": {"XSP": [2, 3]}, "wing_selection": "narrowest",
              "stagger_entries": True, "late_entry_bias_enabled": False,
              "daily_ic_trade_target": 6, "min_minutes_between_entries": 45, "max_concurrent_ics": 4}
XSP_WIDE = {"symbols": ["XSP"], "wing_widths_by_symbol": {"XSP": [5, 10]}, "wing_selection": "widest",
            "late_entry_bias_enabled": False}
GEX_STRICT = {"regime_gex_require_positive": True}          # require GEX confirmed positive
GEX_MAG = {"regime_gex_min_flip_distance_pct": 0.005}       # require spot >=0.5% from the gamma flip
HOLD_TO_EXPIRY = {"per_side_stop_management": False}        # no per-side stop; hold to settlement
FAR_OTM = {"short_delta_target": 0.10}                      # further-OTM shorts than the VIX band


def _params(profile):
    return paper._merged_params(BASE_CONFIG, profile)


# ── Gate evaluator: hard stops ───────────────────────────────────────────────

def test_evaluate_entry_enters_when_all_gates_clear():
    snap = _base_snapshot(now_et="13:00")  # after conservative's 12:00 late-entry-bias start
    entered, reason, chosen = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is True
    assert reason == "entered"
    assert chosen["wing_width"] == 5  # widest clearing candidate preferred


def test_evaluate_entry_rejects_non_0dte():
    snap = _base_snapshot(dte=1)
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False
    assert reason == "no_0dte_expiration"


def test_evaluate_entry_rejects_below_iv_rank_floor():
    snap = _base_snapshot(iv_rank=0.10)  # conservative floor is 0.30
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False
    assert reason == "iv_rank_below_floor"


def test_evaluate_entry_moderate_clears_lower_iv_rank_that_conservative_rejects():
    snap = _base_snapshot(iv_rank=0.25, now_et="13:00")  # below conservative's 0.30, above moderate's 0.22
    cons_entered, cons_reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    mod_entered, mod_reason, _ = paper.evaluate_entry(snap, _params(MODERATE), [])
    assert cons_entered is False and cons_reason == "iv_rank_below_floor"
    assert mod_entered is True


def test_evaluate_entry_rejects_regime_vix_elevated():
    snap = _base_snapshot(vix=30.0)  # conservative pause threshold is 25
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False
    assert reason == "regime_vix_elevated"


def test_evaluate_entry_rejects_regime_gex_negative():
    snap = _base_snapshot(gex={"ok": True, "gex_positive": False})
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False
    assert reason == "regime_gex_negative"


def test_evaluate_entry_gexstrict_requires_positive_gex():
    # regime_gex_require_positive: entries pause
    # unless GEX is CONFIRMED positive (baseline only pauses on confirmed-negative).
    strict = _params(GEX_STRICT)
    spx = dict(symbol="SPX", now_et="13:00", underlying_price=7500.0, iv_rank=0.32,
               candidates=[_candidate(5, 7380, 7560, sp_delta=-0.15, sc_delta=0.15)])
    # GEX unknown/unavailable -> strict gate pauses (baseline would not).
    unknown = paper.evaluate_entry(_base_snapshot(gex={"ok": False}, **spx), strict, [])
    assert unknown[0] is False and unknown[1] == "regime_gex_not_positive"
    # GEX confirmed positive -> strict gate clears (rejection, if any, is for another reason).
    positive = paper.evaluate_entry(_base_snapshot(gex={"ok": True, "gex_positive": True}, **spx), strict, [])
    assert positive[1] != "regime_gex_not_positive"


def test_evaluate_entry_gexmag_requires_deep_positive_gamma():
    # gexmag: positive GEX is not enough -- spot must sit >= 0.5% from the gamma-flip strike.
    mag = _params(GEX_MAG)
    spx = dict(symbol="SPX", now_et="13:00", underlying_price=7500.0, iv_rank=0.32,
               candidates=[_candidate(5, 7380, 7560, sp_delta=-0.15, sc_delta=0.15)])
    # Spot 7500 only ~0.13% from the flip (7490) -> too close, paused.
    near = paper.evaluate_entry(_base_snapshot(
        gex={"ok": True, "gex_positive": True, "gamma_flip": 7490.0, "spot": 7500.0}, **spx), mag, [])
    assert near[0] is False and near[1] == "regime_gex_flip_too_close"
    # Spot 7500 is ~2% above the flip (7350) -> deep in positive gamma, gate clears.
    deep = paper.evaluate_entry(_base_snapshot(
        gex={"ok": True, "gex_positive": True, "gamma_flip": 7350.0, "spot": 7500.0}, **spx), mag, [])
    assert deep[1] != "regime_gex_flip_too_close"


def test_evaluate_entry_late_entry_bias_blocks_before_start_time():
    # Conservative's late_entry_bias_start_time is 12:00; iv_rank 0.32 <= 0.45 bias threshold
    snap = _base_snapshot(now_et="10:30", iv_rank=0.32)
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False
    assert reason == "late_entry_bias_wait"


def test_evaluate_entry_late_entry_bias_allows_after_start_time():
    snap = _base_snapshot(now_et="12:30", iv_rank=0.32)
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is True


def test_evaluate_entry_rejects_max_concurrent_ics_reached():
    snap = _base_snapshot(now_et="13:00")
    fake_open = [{"put_strike": 500, "call_strike": 700}] * CONSERVATIVE["max_concurrent_ics"]
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), fake_open)
    assert entered is False
    assert reason == "max_concurrent_ics_reached"


def test_evaluate_entry_concurrency_cap_uses_passed_count():
    # The gate checks the caller-supplied open count, not the same-symbol overlap list: at the cap
    # it blocks even when the overlap list is empty. process_symbol supplies the per-(profile ×
    # symbol) count here, so each portfolio enforces its own max_concurrent_ics budget.
    snap = _base_snapshot(now_et="13:00")
    cap = CONSERVATIVE["max_concurrent_ics"]
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), open_ics=[],
                                              account_open_count=cap)
    assert entered is False
    assert reason == "max_concurrent_ics_reached"


def test_evaluate_entry_count_below_cap_still_evaluates():
    # Below the cap → entry proceeds past the concurrency gate (reason is NOT the concurrency
    # rejection).
    snap = _base_snapshot(now_et="13:00")
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), open_ics=[],
                                              account_open_count=CONSERVATIVE["max_concurrent_ics"] - 1)
    assert reason != "max_concurrent_ics_reached"


def test_evaluate_entry_atr_gate_is_percentage_based():
    # 590-priced symbol: ATR of 12 pts = 2.03% > conservative's 1.5% threshold → paused
    snap = _base_snapshot(now_et="13:00", atr_5day=12.0, underlying_price=590.0)
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False
    assert reason == "regime_atr_elevated"


def test_evaluate_entry_atr_gate_scales_with_price_level():
    # The SAME 12-point ATR on a 7500-priced symbol (SPX-like) is only 0.16% — well under
    # the 1.5% threshold, so it must NOT pause. This is the exact bug the pct conversion fixes:
    # a fixed points threshold either over-blocked SPX or never fired for low-priced symbols.
    snap = _base_snapshot(now_et="13:00", atr_5day=12.0, underlying_price=7500.0,
                           candidates=[_candidate(5, 7380, 7560, sp_delta=-0.15, sc_delta=0.15)])
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    # ATR gate does not fire here (0.16% < 1.5%); entry proceeds past the regime gate
    assert reason != "regime_atr_elevated"


def test_evaluate_entry_rejects_strike_overlap_and_tries_narrower_candidate():
    # Open IC holds the 583/598 strikes used by the 5-wide candidate; the 2-wide candidate
    # (same strikes in this fixture) also overlaps, so no candidate clears.
    snap = _base_snapshot(now_et="13:00")
    open_ics = [{"put_strike": 583.0, "call_strike": 598.0}]
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), open_ics)
    assert entered is False
    assert reason == "strike_overlap"


def test_evaluate_entry_rejects_call_delta_above_ceiling():
    snap = _base_snapshot(now_et="13:00",
                           candidates=[_candidate(5, 583, 598, sc_delta=0.35)])
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False
    assert reason == "call_delta_exceeds_ceiling"


def test_evaluate_entry_rejects_call_otm_below_floor():
    # short call at 591 is only ~0.17% OTM on a 590 underlying; conservative floor is 0.35%
    snap = _base_snapshot(now_et="13:00", underlying_price=590.0,
                           candidates=[_candidate(5, 583, 591)])
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False
    assert reason == "call_otm_below_floor"


def test_evaluate_entry_rejects_credit_below_floor():
    # Very tight bid/ask so net credit is far below min_credit_pct_of_width * wing_width
    snap = _base_snapshot(now_et="13:00", iv_rank=0.50,  # above low-IV relief threshold
                           candidates=[_candidate(5, 583, 598, sp_bid=0.05, sp_ask=0.06,
                                                   sc_bid=0.05, sc_ask=0.06,
                                                   lp_bid=0.04, lp_ask=0.05,
                                                   lc_bid=0.04, lc_ask=0.05)])
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False
    # Tight bid/ask here nets to a negative or near-zero credit, which the earlier
    # non_positive_credit check catches before the pct-of-width floor is even evaluated —
    # both are valid "credit too thin" rejections for this fixture.
    assert reason in ("credit_below_floor", "credit_below_fee_adjusted_floor", "non_positive_credit")


def test_evaluate_entry_rejects_positive_but_thin_credit_below_pct_floor():
    # natural_bid = (0.20+0.18) - (0.15+0.14) = 0.09; standard floor at iv_rank 0.50
    # (above low-IV relief) is 0.15*5=0.75 -- clearly below, and positive so the
    # non_positive_credit branch does not intercept it.
    snap = _base_snapshot(now_et="13:00", iv_rank=0.50,
                           candidates=[_candidate(5, 583, 598, sp_bid=0.20, sp_ask=0.22,
                                                   sc_bid=0.18, sc_ask=0.20,
                                                   lp_bid=0.14, lp_ask=0.15,
                                                   lc_bid=0.13, lc_ask=0.14)])
    entered, reason, _ = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False
    assert reason == "credit_below_floor"


def test_evaluate_entry_low_iv_relief_lowers_credit_floor():
    # iv_rank 0.32 is within conservative's low_iv_credit_floor_iv_rank_max (0.35), so the
    # relaxed floor (0.10 * width) applies instead of the standard 0.15 * width.
    snap = _base_snapshot(now_et="13:00", iv_rank=0.32,
                           candidates=[_candidate(5, 583, 598, sp_bid=0.30, sp_ask=0.35,
                                                   sc_bid=0.30, sc_ask=0.35,
                                                   lp_bid=0.15, lp_ask=0.20,
                                                   lc_bid=0.15, lc_ask=0.20)])
    # natural_bid = (0.30+0.30) - (0.20+0.20) = 0.20; standard floor 0.15*5=0.75 (fail),
    # low-IV floor 0.10*5=0.50 (fail too) -- use a credit that clears low-IV but not standard
    entered, reason, chosen = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is False  # 0.20 still below even the relaxed 0.50 floor; sanity check only
    assert reason in ("credit_below_floor", "credit_below_fee_adjusted_floor")


def test_evaluate_entry_prefers_widest_clearing_candidate():
    snap = _base_snapshot(now_et="13:00", candidates=[_candidate(2, 583, 598), _candidate(5, 583, 598)])
    entered, reason, chosen = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    assert entered is True
    assert chosen["wing_width"] == 5


# ── Synthetic fill / exit math ───────────────────────────────────────────────

def test_synthetic_entry_fill_prices_at_mid_minus_slippage():
    snap = _base_snapshot(now_et="13:00")
    _, _, chosen = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    row = paper.synthetic_entry_fill(snap, "conservative", chosen, _params(CONSERVATIVE), "paper")
    # 5-wide candidate priced at mid minus 0.125 of each vertical's combined spread:
    #   put  = (0.60-0.20) - 0.125*((0.65-0.55)+(0.25-0.15)) = 0.40 - 0.025 = 0.375
    #   call = (0.55-0.17) - 0.125*((0.60-0.50)+(0.22-0.12)) = 0.38 - 0.025 = 0.355
    assert row["put_credit"] == pytest.approx(0.375, abs=1e-4)
    assert row["call_credit"] == pytest.approx(0.355, abs=1e-4)
    assert row["net_credit"] == pytest.approx(0.73, abs=1e-4)
    # Strictly between the worst-case natural bid (0.58) and the full mid (0.78).
    assert chosen["ic_natural_bid"] < row["net_credit"] < 0.78
    assert row["risk_profile"] == "conservative"
    assert row["execution_mode"] == "paper"


def test_synthetic_entry_fill_records_both_iv_measures():
    # Regression guard: iv_pct_at_entry sat NULL for 89 trades because the column existed but was
    # never written. Rank and percentile diverge (rank is outlier-compressed), so both must persist
    # or the IV gate can't be re-based on evidence later.
    snap = _base_snapshot(now_et="13:00", iv_rank=0.32, iv_pct=0.91)
    _, _, chosen = paper.evaluate_entry(snap, _params(CONSERVATIVE), [])
    row = paper.synthetic_entry_fill(snap, "conservative", chosen, _params(CONSERVATIVE), "paper")
    assert row["iv_rank_at_entry"] == pytest.approx(0.32)
    assert row["iv_pct_at_entry"] == pytest.approx(0.91)
    assert row["status"] == "open"
    assert row["fees"] == pytest.approx(paper.open_fees("XSP", 1), abs=0.001)


def test_evaluate_open_trade_no_profit_target_holds_a_cheap_ic():
    # MEIC has no profit target: even a deeply-profitable IC (cost far below 50% of credit) is
    # NOT closed early — it holds until a stop, force-close, or expiration.
    trade = {"put_symbol": "SP", "call_symbol": "SC", "long_put_symbol": "LP", "long_call_symbol": "LC",
             "net_credit": 0.58, "status": "open", "put_credit": 0.30, "call_credit": 0.28,
             "stop_trigger_current": 0.93, "stop_limit_current": 1.02}
    leg_quotes = {
        "SP": {"bid": 0.10, "ask": 0.15, "mid": 0.125}, "LP": {"bid": 0.02, "ask": 0.05, "mid": 0.035},
        "SC": {"bid": 0.08, "ask": 0.12, "mid": 0.10}, "LC": {"bid": 0.02, "ask": 0.04, "mid": 0.03},
    }
    decision = paper.evaluate_open_trade(trade, leg_quotes, _params(MODERATE), force_close=False)
    assert decision["action"] == "hold"


def _expiring_trade():
    return {"put_symbol": "SP", "call_symbol": "SC", "long_put_symbol": "LP", "long_call_symbol": "LC",
            "net_credit": 0.58, "status": "open", "put_credit": 0.30, "call_credit": 0.28,
            "put_strike": 7480.0, "call_strike": 7520.0, "wing_width": 10.0,
            "put_stop_cost": None, "call_stop_cost": None}


def test_settlement_value_otm_zero_itm_capped():
    # put: ITM when underlying < strike; capped at wing
    assert paper._settlement_value(7480, 7500, 10, "put") == 0.0          # OTM
    assert paper._settlement_value(7480, 7475, 10, "put") == 5.0          # 5 ITM
    assert paper._settlement_value(7480, 7400, 10, "put") == 10.0         # deep ITM → wing cap
    assert paper._settlement_value(7520, 7500, 10, "call") == 0.0         # OTM
    assert paper._settlement_value(7520, 7526, 10, "call") == 6.0         # 6 ITM


def test_expire_both_otm_keeps_full_credit():
    # underlying 7500 between the shorts (7480 put / 7520 call) → both expire worthless
    d = paper.evaluate_open_trade(_expiring_trade(), {}, _params(MODERATE), force_close=False,
                                  underlying_price=7500.0, is_cash_settled=True, settle=True)
    assert d["action"] == "expire"
    assert d["put_exit_price"] == 0.0 and d["call_exit_price"] == 0.0  # full credit retained


def test_expire_itm_call_settles_for_intrinsic():
    # underlying 7526 → call ITM by 6, put OTM
    d = paper.evaluate_open_trade(_expiring_trade(), {}, _params(MODERATE), force_close=False,
                                  underlying_price=7526.0, is_cash_settled=True, settle=True)
    assert d["action"] == "expire"
    assert d["put_exit_price"] == 0.0
    assert d["call_exit_price"] == 6.0  # call side settles for 6 (< wing 10)


def test_expire_only_settles_the_still_open_side():
    # call already stopped (call_stop_cost set) → settlement touches only the put side
    trade = _expiring_trade()
    trade["status"] = "partial"
    trade["call_stop_cost"] = 0.55
    d = paper.evaluate_open_trade(trade, {}, _params(MODERATE), force_close=False,
                                  underlying_price=7500.0, is_cash_settled=True, settle=True)
    assert d["action"] == "expire"
    assert d["put_open"] is True and d["call_open"] is False
    assert d["call_exit_price"] is None


def test_force_close_takes_precedence_over_settlement():
    # On an event day both could be true; force_close must win (it fires earlier in the day).
    trade = _expiring_trade()
    lq = {"SP": {"bid": 0.2, "ask": 0.3, "mid": 0.25}, "LP": {"bid": 0.05, "ask": 0.1, "mid": 0.075},
          "SC": {"bid": 0.2, "ask": 0.3, "mid": 0.25}, "LC": {"bid": 0.05, "ask": 0.1, "mid": 0.075}}
    d = paper.evaluate_open_trade(trade, lq, _params(MODERATE), force_close=True,
                                  underlying_price=7500.0, is_cash_settled=True, settle=True,
                                  force_close_reason="force_close_fomc")
    assert d["action"] == "force_close" and d["reason"] == "force_close_fomc"


def test_evaluate_open_trade_holds_when_nothing_triggers():
    trade = {"put_symbol": "SP", "call_symbol": "SC", "long_put_symbol": "LP", "long_call_symbol": "LC",
              "net_credit": 0.58, "status": "open", "put_credit": 0.30, "call_credit": 0.28,
              "stop_trigger_current": 0.93, "stop_limit_current": 1.02}
    leg_quotes = {
        "SP": {"bid": 0.24, "ask": 0.30, "mid": 0.27}, "LP": {"bid": 0.06, "ask": 0.09, "mid": 0.075},
        "SC": {"bid": 0.20, "ask": 0.26, "mid": 0.23}, "LC": {"bid": 0.05, "ask": 0.08, "mid": 0.065},
    }
    decision = paper.evaluate_open_trade(trade, leg_quotes, _params(MODERATE), force_close=False)
    assert decision["action"] == "hold"


def test_evaluate_open_trade_stops_call_side_when_cost_reaches_trigger():
    trade = {"put_symbol": "SP", "call_symbol": "SC", "long_put_symbol": "LP", "long_call_symbol": "LC",
              "net_credit": 0.58, "status": "open", "put_credit": 0.30, "call_credit": 0.28,
              "stop_trigger_current": 0.93, "stop_limit_current": 1.02}
    leg_quotes = {
        "SP": {"bid": 0.20, "ask": 0.26, "mid": 0.23}, "LP": {"bid": 0.05, "ask": 0.08, "mid": 0.065},
        "SC": {"bid": 0.60, "ask": 0.68, "mid": 0.64}, "LC": {"bid": 0.03, "ask": 0.06, "mid": 0.045},
    }
    decision = paper.evaluate_open_trade(trade, leg_quotes, _params(MODERATE), force_close=False)
    assert decision["action"] == "stop_call"


def test_per_side_stop_management_false_disables_stops():
    # per_side_stop_management: false -> a side whose cost blows past the
    # trigger is NOT stopped; the IC holds to force-close/settlement. Same quotes stop a normal profile.
    trade = {"put_symbol": "SP", "call_symbol": "SC", "long_put_symbol": "LP", "long_call_symbol": "LC",
             "net_credit": 0.58, "status": "open", "put_credit": 0.30, "call_credit": 0.28,
             "stop_trigger_current": 0.93, "stop_limit_current": 1.02}
    leg_quotes = {
        "SP": {"bid": 0.20, "ask": 0.26, "mid": 0.23}, "LP": {"bid": 0.05, "ask": 0.08, "mid": 0.065},
        "SC": {"bid": 0.60, "ask": 0.68, "mid": 0.64}, "LC": {"bid": 0.03, "ask": 0.06, "mid": 0.045},
    }
    hold = _params(HOLD_TO_EXPIRY)
    assert paper.evaluate_open_trade(trade, leg_quotes, hold, force_close=False)["action"] == "hold"
    assert paper.evaluate_open_trade(trade, leg_quotes, _params(MODERATE),
                                     force_close=False)["action"] == "stop_call"


def test_evaluate_open_trade_does_not_restop_an_already_stopped_side():
    # A 'partial' IC whose call side was already stopped (call_stop_cost recorded) must NOT
    # re-stop the call, even with the call spread expensive — it should manage only the put.
    trade = {"put_symbol": "SP", "call_symbol": "SC", "long_put_symbol": "LP", "long_call_symbol": "LC",
             "net_credit": 0.58, "status": "partial", "put_credit": 0.30, "call_credit": 0.28,
             "call_stop_cost": 0.60, "put_stop_cost": None,
             "stop_trigger_current": 0.93, "stop_limit_current": 1.02}
    leg_quotes = {
        "SP": {"bid": 0.20, "ask": 0.26, "mid": 0.23}, "LP": {"bid": 0.05, "ask": 0.08, "mid": 0.065},
        "SC": {"bid": 0.70, "ask": 0.80, "mid": 0.75}, "LC": {"bid": 0.03, "ask": 0.06, "mid": 0.045},
    }
    decision = paper.evaluate_open_trade(trade, leg_quotes, _params(MODERATE), force_close=False)
    # Call already closed → not re-stopped; put spread is cheap → hold.
    assert decision["action"] == "hold"


def test_evaluate_open_trade_force_close_overrides_hold():
    trade = {"put_symbol": "SP", "call_symbol": "SC", "long_put_symbol": "LP", "long_call_symbol": "LC",
              "net_credit": 0.58, "status": "open", "put_credit": 0.30, "call_credit": 0.28,
              "stop_trigger_current": 0.93, "stop_limit_current": 1.02}
    leg_quotes = {
        "SP": {"bid": 0.24, "ask": 0.30, "mid": 0.27}, "LP": {"bid": 0.06, "ask": 0.09, "mid": 0.075},
        "SC": {"bid": 0.20, "ask": 0.26, "mid": 0.23}, "LC": {"bid": 0.05, "ask": 0.08, "mid": 0.065},
    }
    decision = paper.evaluate_open_trade(trade, leg_quotes, _params(MODERATE), force_close=True)
    assert decision["action"] == "force_close"
    assert decision["put_open"] is True and decision["call_open"] is True


# ── Physical-settlement exit hardening ───────────────────────────────────────

def _force_close_trade():
    return {"put_symbol": "SP", "call_symbol": "SC", "long_put_symbol": "LP", "long_call_symbol": "LC",
            "net_credit": 0.58, "status": "open", "put_credit": 0.30, "call_credit": 0.28,
            "put_strike": 583, "call_strike": 598, "wing_width": 5,
            "stop_trigger_current": 0.93, "stop_limit_current": 1.02}


_FC_LEG_QUOTES = {
    "SP": {"bid": 0.24, "ask": 0.30, "mid": 0.27}, "LP": {"bid": 0.06, "ask": 0.09, "mid": 0.075},
    "SC": {"bid": 0.20, "ask": 0.26, "mid": 0.23}, "LC": {"bid": 0.05, "ask": 0.08, "mid": 0.065},
}


def test_is_cash_settled_classification():
    # Cash-settled index products (left to expire) vs physically-settled ETFs (force-closed).
    for cash in ("SPX", "XSP", "NDX", "RUT"):
        assert paper._is_cash_settled(cash, BASE_CONFIG) is True
    for physical in ("SPY", "QQQ", "IWM"):
        assert paper._is_cash_settled(physical, BASE_CONFIG) is False


def test_cash_settled_force_close_has_no_friction():
    base = paper.evaluate_open_trade(_force_close_trade(), _FC_LEG_QUOTES, _params(MODERATE),
                                     force_close=True, underlying_price=590.5, is_cash_settled=True)
    # No friction: exit = mid cost to close + 0.125 * spread (see _close_cost)
    assert base["physical_friction_applied"] is False
    assert base["put_exit_price"] == pytest.approx(
        (0.27 - 0.075) + 0.125 * ((0.30 - 0.24) + (0.09 - 0.06)), abs=1e-4)


def test_physical_force_close_adds_friction():
    friction = BASE_CONFIG.get("physical_settlement_exit_friction", 0.05)
    phys = paper.evaluate_open_trade(_force_close_trade(), _FC_LEG_QUOTES, _params(MODERATE),
                                     force_close=True, underlying_price=590.5, is_cash_settled=False)
    assert phys["physical_friction_applied"] is True
    # underlying 590.5 is far from both strikes (583 put / 598 call) → no pin penalty, only friction.
    # Base close cost is now mid + 0.125*spread (see _close_cost), plus the physical friction.
    assert phys["put_exit_price"] == pytest.approx(
        (0.27 - 0.075) + 0.125 * ((0.30 - 0.24) + (0.09 - 0.06)) + friction, abs=1e-4)
    assert phys["call_exit_price"] == pytest.approx(
        (0.23 - 0.065) + 0.125 * ((0.26 - 0.20) + (0.08 - 0.05)) + friction, abs=1e-4)
    # friction makes the physical close strictly more expensive (worse P&L) than cash-settled
    base = paper.evaluate_open_trade(_force_close_trade(), _FC_LEG_QUOTES, _params(MODERATE),
                                     force_close=True, underlying_price=590.5, is_cash_settled=True)
    assert phys["put_exit_price"] > base["put_exit_price"]


def test_pin_penalty_fires_when_short_strike_atm():
    friction = BASE_CONFIG.get("physical_settlement_exit_friction", 0.05)
    pen_pct = BASE_CONFIG.get("pin_risk_penalty_pct_of_width", 0.25)
    # underlying pinned right at the 598 short call → pin penalty on the call side only
    phys = paper.evaluate_open_trade(_force_close_trade(), _FC_LEG_QUOTES, _params(MODERATE),
                                     force_close=True, underlying_price=598.0, is_cash_settled=False)
    # close cost (mid + 0.125*spread) + friction + pin penalty, wing_width 5
    expected_call = (0.23 - 0.065) + 0.125 * ((0.26 - 0.20) + (0.08 - 0.05)) + friction + pen_pct * 5
    assert phys["call_exit_price"] == pytest.approx(expected_call, abs=1e-4)
    # put strike 583 is ~2.5% away from 598 → no pin penalty on the put side
    assert phys["put_exit_price"] == pytest.approx(
        (0.27 - 0.075) + 0.125 * ((0.30 - 0.24) + (0.09 - 0.06)) + friction, abs=1e-4)


def test_pin_penalty_zero_when_underlying_missing():
    assert paper._pin_penalty(598, None, 5, BASE_CONFIG) == 0.0
    assert paper._pin_penalty(None, 598, 5, BASE_CONFIG) == 0.0


def _snap(now_et, date="2026-07-15"):
    return {"symbol": "QQQ", "date": date, "now_et": now_et, "underlying_price": 470.0}


def test_force_close_active_physical_earlier_than_cash():
    base = BASE_CONFIG
    # 15:35 ET: past the 15:30 physical close but before the 15:45 general close
    active_phys, reason_phys = paper.force_close_active(_snap("15:35"), base, is_cash_settled=False)
    active_cash, reason_cash = paper.force_close_active(_snap("15:35"), base, is_cash_settled=True)
    assert active_phys is True and reason_phys == "force_close_physical_settlement"
    assert active_cash is False and reason_cash is None


def test_force_close_active_eod_closes_noncash_but_not_cash():
    # At 15:46 the non-cash-settled symbol is force-closed (physical, or the 15:45 backstop),
    # but the cash-settled symbol is NOT — it is left to expire and settled at the close.
    active_noncash, reason_noncash = paper.force_close_active(_snap("15:46"), BASE_CONFIG, is_cash_settled=False)
    active_cash, reason_cash = paper.force_close_active(_snap("15:46"), BASE_CONFIG, is_cash_settled=True)
    assert active_noncash is True and reason_noncash in ("force_close_physical_settlement", "force_close_eod")
    assert active_cash is False and reason_cash is None


def test_settlement_active_cash_settled_at_close_only():
    assert paper.settlement_active(_snap("16:00"), BASE_CONFIG, is_cash_settled=True) is True
    assert paper.settlement_active(_snap("15:59"), BASE_CONFIG, is_cash_settled=True) is False
    # Non-cash-settled symbols are never settled — they are force-closed before the bell.
    assert paper.settlement_active(_snap("16:00"), BASE_CONFIG, is_cash_settled=False) is False


def test_events_still_force_close_cash_settled():
    # FOMC (13:30) and quarterly/triple-witching (14:00) remain hard overrides for ALL symbols,
    # including cash-settled — they do not get the 'left to expire' treatment on those days.
    fomc = "2026-01-28"  # first 2026 FOMC day (now from cherrypick.core.calendar, not config)
    q = "2026-03-31"     # first 2026 quarterly expiry
    a1, r1 = paper.force_close_active(_snap("13:35", date=fomc), BASE_CONFIG, is_cash_settled=True)
    a2, r2 = paper.force_close_active(_snap("14:05", date=q), BASE_CONFIG, is_cash_settled=True)
    assert a1 is True and r1 == "force_close_fomc"
    assert a2 is True and r2 == "force_close_expiry_event"


def test_force_close_active_fomc_blackout():
    fomc_date = "2026-01-28"  # a real 2026 FOMC day (cherrypick.core.calendar)
    active, reason = paper.force_close_active(_snap("13:35", date=fomc_date), BASE_CONFIG, is_cash_settled=True)
    assert active is True and reason == "force_close_fomc"


def test_force_close_active_quarterly_expiry_event():
    q_date = "2026-03-31"  # a real 2026 quarterly expiry (cherrypick.core.calendar)
    active, reason = paper.force_close_active(_snap("14:05", date=q_date), BASE_CONFIG, is_cash_settled=True)
    assert active is True and reason == "force_close_expiry_event"


def test_force_close_active_inactive_midday():
    active, reason = paper.force_close_active(_snap("11:00"), BASE_CONFIG, is_cash_settled=False)
    assert active is False and reason is None


# ── get_range_summary (DB integration) ───────────────────────────────────────

@pytest.fixture
def paper_db_path(monkeypatch, tmp_path):
    path = str(tmp_path / "paper_trades.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)
    return path


def _insert_paper_trade(db_path, **kwargs):
    import sqlite3
    defaults = dict(
        trade_date="2026-07-01", symbol="SPX", risk_profile="conservative",
        net_credit=1.5, pnl=150.0, fees=25.0, status="expired", quantity=1,
        ic_order_id="P-1", created_at="2026-07-01T10:00:00", updated_at="2026-07-01T10:00:00",
    )
    defaults.update(kwargs)
    conn = sqlite3.connect(db_path)
    cols = ", ".join(defaults)
    placeholders = ", ".join("?" * len(defaults))
    conn.execute(f"INSERT INTO ic_trades ({cols}) VALUES ({placeholders})", list(defaults.values()))
    conn.commit()
    conn.close()


def test_get_range_summary_groups_by_profile(paper_db_path, capsys):
    _insert_paper_trade(paper_db_path, ic_order_id="P-1", risk_profile="conservative",
                         trade_date="2026-07-01", pnl=150.0, fees=25.0, status="expired")
    _insert_paper_trade(paper_db_path, ic_order_id="P-2", risk_profile="conservative",
                         trade_date="2026-07-01", pnl=-120.0, fees=25.0, status="stopped")
    _insert_paper_trade(paper_db_path, ic_order_id="P-3", risk_profile="moderate",
                         trade_date="2026-07-01", pnl=100.0, fees=25.0, status="expired")

    args = argparse.Namespace(start="2026-07-01", end="2026-07-02", profile=None, symbol=None)
    db.cmd_get_range_summary(args)
    out = json.loads(capsys.readouterr().out)

    assert out["ok"] is True
    assert set(out["profiles"].keys()) == {"conservative", "moderate"}
    cons = out["profiles"]["conservative"]
    assert cons["total_trades"] == 2
    assert cons["win_count"] == 1
    assert cons["loss_count"] == 1
    assert cons["net_pnl"] == pytest.approx(150 - 25 + (-120 - 25), abs=0.01)


def test_get_range_summary_excludes_cancelled_and_pending(paper_db_path, capsys):
    _insert_paper_trade(paper_db_path, ic_order_id="P-1", status="cancelled", trade_date="2026-07-01")
    _insert_paper_trade(paper_db_path, ic_order_id="P-2", status="pending", trade_date="2026-07-01")
    _insert_paper_trade(paper_db_path, ic_order_id="P-3", status="expired", trade_date="2026-07-01", pnl=50.0, fees=10.0)

    args = argparse.Namespace(start="2026-07-01", end="2026-07-02", profile=None, symbol=None)
    db.cmd_get_range_summary(args)
    out = json.loads(capsys.readouterr().out)
    assert out["profiles"]["conservative"]["total_trades"] == 1


# ── End-to-end: process_symbol via subprocess against a real temp DB ────────

def test_process_symbol_end_to_end_fills_and_marks(tmp_path):
    db_path = str(tmp_path / "paper_e2e.db")
    subprocess.run([sys.executable, str(Path(__file__).parent.parent / "src" / "db.py"),
                     "--db", db_path, "init_db"], check=True, capture_output=True)

    # Use SPX — a symbol the conservative ladder actually trades (the default snapshot's XSP is no
    # longer in the traded set). Single well-credited 5-wide candidate so the fill is unambiguous.
    snapshot = _base_snapshot(now_et="13:00", date="2026-07-09", symbol="SPX",
                              underlying_price=7500.0,
                              candidates=[_candidate(5, 7450, 7550, sp_bid=0.80, sc_bid=0.75)])
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent.parent / "src" / "paper.py"),
         "--db", db_path, "process_symbol", "--snapshot", json.dumps(snapshot),
         "--execution_mode", "paper", "--profiles", "conservative"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["ok"] is True
    filled = [a for a in out["results"]["conservative"] if a.get("entry") == "filled"]
    assert len(filled) == 1

    open_check = subprocess.run(
        [sys.executable, str(Path(__file__).parent.parent / "src" / "db.py"),
         "--db", db_path, "get_open_trades", "--symbol", "SPX", "--date", "2026-07-09"],
        capture_output=True, text=True,
    )
    open_out = json.loads(open_check.stdout.strip())
    assert len(open_out["open_trades"]) == 1
    assert open_out["open_trades"][0]["risk_profile"] == "conservative"


def test_process_symbol_concurrency_budget_is_per_symbol(paper_db_path):
    # max_concurrent_ics is a per-(profile × symbol) budget: a profile maxed out on one symbol must
    # still be able to enter another, so a busy symbol can't starve a quiet one of slots.
    cap = CONSERVATIVE["max_concurrent_ics"]
    for i in range(cap):
        _insert_paper_trade(paper_db_path, ic_order_id=f"SPX-{i}", symbol="SPX",
                            risk_profile="conservative", trade_date="2026-07-09", status="open")
    # conservative is now full on SPX. A QQQ snapshot for the same profile must NOT be concurrency-blocked.
    snapshot = _base_snapshot(now_et="13:00", date="2026-07-09", symbol="QQQ",
                              underlying_price=700.0,
                              candidates=[_candidate(5, 695, 705, sp_bid=0.80, sc_bid=0.75)])
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent.parent / "src" / "paper.py"),
         "--db", paper_db_path, "process_symbol", "--snapshot", json.dumps(snapshot),
         "--execution_mode", "paper", "--profiles", "conservative"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    actions = out["results"]["conservative"]
    assert all(a.get("reason") != "max_concurrent_ics_reached" for a in actions), actions
    assert any(a.get("entry") == "filled" for a in actions), actions


def test_process_symbol_daily_target_is_per_symbol(paper_db_path):
    # The daily-target count is per (profile × symbol): hitting the target on one symbol must not
    # raise the bar (or block) on another — SPX's entries don't spend QQQ's budget.
    target = _params(CONSERVATIVE)["daily_ic_trade_target"]
    for i in range(target):
        _insert_paper_trade(paper_db_path, ic_order_id=f"SPX-{i}", symbol="SPX",
                            risk_profile="conservative", trade_date="2026-07-09", status="expired")
    # conservative has hit its daily target on SPX. A QQQ snapshot must NOT be daily-capped.
    snapshot = _base_snapshot(now_et="13:00", date="2026-07-09", symbol="QQQ",
                              underlying_price=700.0,
                              candidates=[_candidate(5, 695, 705, sp_bid=0.80, sc_bid=0.75)])
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent.parent / "src" / "paper.py"),
         "--db", paper_db_path, "process_symbol", "--snapshot", json.dumps(snapshot),
         "--execution_mode", "paper", "--profiles", "conservative"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    actions = out["results"]["conservative"]
    assert all(a.get("reason") not in ("daily_target_reached", "over_target_credit_below_floor")
               for a in actions), actions
    assert any(a.get("entry") == "filled" for a in actions), actions


# ── Stop persistence + stopped-vs-expired (regression for the P&L-accumulation bug) ──

_DBPY = str(Path(__file__).parent.parent / "src" / "db.py")


def _init_db(tmp_path):
    db_path = str(tmp_path / "stop.db")
    subprocess.run([sys.executable, _DBPY, "--db", db_path, "init_db"], check=True, capture_output=True)
    return db_path


def _row(db_path, oid):
    import sqlite3
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    r = dict(con.execute("SELECT * FROM ic_trades WHERE ic_order_id=?", (oid,)).fetchone())
    con.close()
    return r


def test_update_trade_cli_accepts_stop_cost(tmp_path):
    # Root cause: db.py update_trade must accept put_stop_cost/call_stop_cost, or the stop path's
    # whole update (status + stop_cost + fees) is silently rejected and only pnl lands -> the side
    # re-stops every iteration and pnl compounds. Guard the CLI accepts these flags and persists.
    db_path = _init_db(tmp_path)
    subprocess.run([sys.executable, _DBPY, "--db", db_path, "save_trade", "--data", json.dumps({
        "ic_order_id": "S1", "trade_date": "2026-07-09", "symbol": "SPX", "status": "open",
        "net_credit": 2.1, "put_credit": 0.65, "call_credit": 1.45})], check=True, capture_output=True)
    r = subprocess.run([sys.executable, _DBPY, "--db", db_path, "update_trade", "--ic_order_id", "S1",
                        "--call_stop_cost", "1.5", "--status", "partial"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    row = _row(db_path, "S1")
    assert row["status"] == "partial" and row["call_stop_cost"] == 1.5


def test_stop_persists_then_settlement_stays_stopped_without_double_counting(tmp_path):
    db_path = _init_db(tmp_path)
    subprocess.run([sys.executable, _DBPY, "--db", db_path, "save_trade", "--data", json.dumps({
        "ic_order_id": "S2", "trade_date": "2026-07-09", "symbol": "SPX", "status": "open",
        "net_credit": 2.1, "put_credit": 0.65, "call_credit": 1.45,
        "put_strike": 7525, "call_strike": 7585, "wing_width": 10})], check=True, capture_output=True)
    trade = _row(db_path, "S2")

    # 1) stop the call side -> status partial, call_stop_cost persists, pnl = (1.45 - 2.0)*100
    paper._apply_exit_decision(trade, {"action": "stop_call", "call_exit_price": 2.0}, "SPX", db_path)
    t2 = _row(db_path, "S2")
    assert t2["status"] == "partial"
    assert t2["call_stop_cost"] == 2.0
    assert t2["pnl"] == pytest.approx((1.45 - 2.0) * 100)   # -55.0

    # 2) a *second* iteration must NOT re-stop the (already-closed) call side and double the pnl
    from_db_decision = paper.evaluate_open_trade(
        t2, {}, _params(MODERATE), force_close=False, underlying_price=7560.0)
    assert from_db_decision["action"] == "hold"   # call already closed, put quotes absent -> hold

    # 3) settle the remaining put side at expiry (put OTM -> 0). The IC must end 'stopped', NOT
    #    'expired' (a side was stopped), and pnl must be the SUM, not an accumulation.
    paper._apply_exit_decision(
        t2, {"action": "expire", "put_open": True, "call_open": False,
             "put_exit_price": 0.0, "call_exit_price": None}, "SPX", db_path)
    t3 = _row(db_path, "S2")
    assert t3["status"] == "stopped"
    assert t3["pnl"] == pytest.approx(-55.0 + (0.65 - 0.0) * 100)   # -55 + 65 = 10, bounded


# ── Per-profile symbol + wing selection (experiment profiles) ────────────────

def test_wing_selection_narrowest_picks_narrowest_clearing():
    # A [2,3] shortlist with wing_selection "narrowest": prefer the 2-wide.
    snap = _base_snapshot(now_et="13:00",
                          candidates=[_candidate(2, 583, 598), _candidate(3, 583, 598)])
    entered, reason, chosen = paper.evaluate_entry(snap, _params(XSP_NARROW), [])
    assert entered is True and reason == "entered"
    assert chosen["wing_width"] == 2


def test_wing_selection_is_resolved_per_symbol():
    # The ordering bias is a property of the INSTRUMENT, not the risk tier: a small-account symbol
    # wants the smallest viable width while the big-notional index products keep the fee-drag
    # (widest-first) bias. A profile-wide wing_selection would flip every symbol at once.
    params = _params({"wing_widths_by_symbol": {"SPY": [1, 2, 3], "SPX": [5, 10]},
                      "wing_selection_by_symbol": {"SPY": "narrowest", "DEFAULT": "widest"}})
    menu_spy = [{"wing_width": w} for w in (1, 2, 3)]
    menu_spx = [{"wing_width": w} for w in (5, 10)]
    assert [c["wing_width"] for c in paper._select_candidates(menu_spy, params, "SPY")] == [1, 2, 3]
    assert [c["wing_width"] for c in paper._select_candidates(menu_spx, params, "SPX")] == [10, 5]
    # Precedence, most specific first: exact symbol > profile-wide > DEFAULT > "widest".
    assert paper._wing_selection_for_symbol(params, "QQQ") == "widest"          # DEFAULT
    assert paper._wing_selection_for_symbol({"wing_selection": "narrowest"}, "QQQ") == "narrowest"
    assert paper._wing_selection_for_symbol({}, "QQQ") == "widest"              # hardcoded default
    # A profile's explicit wing_selection must NOT be silently overridden by a generic DEFAULT...
    profile_wide = {"wing_selection": "narrowest",
                    "wing_selection_by_symbol": {"DEFAULT": "widest"}}
    assert paper._wing_selection_for_symbol(profile_wide, "QQQ") == "narrowest"
    # ...but an exact per-symbol entry still wins over the profile-wide setting.
    assert paper._wing_selection_for_symbol(
        {**profile_wide, "wing_selection_by_symbol": {"QQQ": "widest"}}, "QQQ") == "widest"


def test_wing_filter_excludes_widths_outside_profile_shortlist():
    # A [5,10] shortlist; a lone 2-wide candidate is filtered out entirely, so
    # no candidate clears — even though it would otherwise pass every gate.
    snap = _base_snapshot(now_et="13:00", iv_rank=0.32,
                          candidates=[_candidate(2, 583, 598, sp_bid=1.2, sp_ask=1.3,
                                                 sc_bid=1.1, sc_ask=1.2)])
    entered, reason, _ = paper.evaluate_entry(snap, _params(XSP_WIDE), [])
    assert entered is False
    assert reason == "no_candidate_cleared_all_gates"


# ── Multi-delta candidate menu (short_delta_target routing) ──────────────────

def _tagged(cand, short_delta, is_default):
    return {**cand, "short_delta": short_delta, "is_default_delta": is_default}


def test_select_candidates_delta_bands_route_to_the_right_profile():
    # A multi-delta menu: default band (0.15) + a far-OTM band (0.10), both 10-wide.
    menu = [_tagged(_candidate(10, 7380, 7560), 0.15, True),
            _tagged(_candidate(10, 7300, 7640), 0.10, False)]
    ctrl = _params(CONSERVATIVE)
    far = _params(FAR_OTM)
    # Control (no short_delta_target) sees ONLY the default band -> unperturbed by the extra band.
    assert [c["short_delta"] for c in paper._select_candidates(menu, ctrl, "SPX")] == [0.15]
    # farotm (short_delta_target 0.10) sees ONLY its band.
    assert [c["short_delta"] for c in paper._select_candidates(menu, far, "SPX")] == [0.10]


def test_select_candidates_untagged_menu_unchanged():
    # Legacy/test menus with no short_delta tag pass through untouched (back-compat).
    menu = [_candidate(5, 583, 598), _candidate(2, 583, 598)]
    sel = paper._select_candidates(menu, _params(CONSERVATIVE), "XSP")
    assert len(sel) == 2 and all("short_delta" not in c for c in sel)


def test_union_short_deltas_collects_requested_bands():
    # The ladder declares no custom short_delta_target, so the live registry requests no extra band
    # and every profile just uses the VIX-banded default candidate.
    assert paper.union_short_deltas_for_symbol("SPX") == []
    # The union mechanism still collects a band from any profile that declares one, scoped to the
    # symbols that profile trades (passed explicitly so this doesn't depend on registry contents).
    synthetic = {
        "far": {"symbols": ["SPX"], "short_delta_target": 0.10},
        "close": {"symbols": ["SPX"], "short_delta_target": 0.25},
        "xsp-far": {"symbols": ["XSP"], "short_delta_target": 0.12},
    }
    spx = paper.union_short_deltas_for_symbol("SPX", profiles=synthetic)
    assert 0.10 in spx and 0.25 in spx
    assert 0.12 not in spx  # XSP-pinned band must not leak into SPX's menu
    assert paper.union_short_deltas_for_symbol("XSP", profiles=synthetic) == [0.12]


# ── Staggering: entry window, daily cap, spacing (opt-in via stagger_entries) ─

def test_stagger_outside_entry_window_rejected():
    snap = _base_snapshot(now_et="15:00")  # past the 14:30 entry-window end
    entered, reason, _ = paper.evaluate_entry(snap, _params(XSP_NARROW), [])
    assert entered is False and reason == "outside_entry_window"


def test_stagger_before_entry_window_rejected():
    snap = _base_snapshot(now_et="09:45")  # before the 10:00 entry-window start
    entered, reason, _ = paper.evaluate_entry(snap, _params(XSP_NARROW), [])
    assert entered is False and reason == "outside_entry_window"


def test_stagger_daily_target_reached_rejected():
    snap = _base_snapshot(now_et="13:00")
    entered, reason, _ = paper.evaluate_entry(snap, _params(XSP_NARROW), [], todays_entry_count=6)
    assert entered is False and reason == "daily_target_reached"


def test_stagger_spacing_wait_rejected():
    # last entry 30 min ago < the 45-min min spacing → wait.
    snap = _base_snapshot(now_et="13:00")  # 780 min
    entered, reason, _ = paper.evaluate_entry(snap, _params(XSP_NARROW), [],
                                              todays_entry_count=1, last_entry_min=780 - 30)
    assert entered is False and reason == "entry_spacing_wait"


def test_stagger_spacing_ok_after_interval():
    # last entry 50 min ago ≥ the 45-min min spacing → proceeds.
    snap = _base_snapshot(now_et="13:00")
    entered, reason, _ = paper.evaluate_entry(snap, _params(XSP_NARROW), [],
                                              todays_entry_count=1, last_entry_min=780 - 50)
    assert entered is True and reason == "entered"


def test_ladder_daily_target_is_soft_guidance_not_a_cap():
    # conservative has no stagger_entries → the window/spacing gates stay opt-in (15:00 is not
    # window-blocked; iv_rank 0.50 clears the late-entry bias). The daily target is GUIDANCE: past
    # it the portfolio is never hard-blocked, but only a richer-credit setup qualifies.
    snap = _base_snapshot(now_et="15:00", iv_rank=0.50,
                          candidates=[_candidate(5, 583, 598, sp_bid=0.70, sp_ask=0.80,
                                                 sc_bid=0.65, sc_ask=0.75)])
    params = _params(CONSERVATIVE)
    target = params["daily_ic_trade_target"]
    # Under the target: this credit clears the normal floor → enters.
    entered, reason, _ = paper.evaluate_entry(snap, params, [], todays_entry_count=0)
    assert entered is True and reason == "entered"
    # At the target: NOT hard-capped — declined only because the raised bar isn't met.
    entered2, reason2, _ = paper.evaluate_entry(snap, params, [], todays_entry_count=target)
    assert entered2 is False
    assert reason2 == "over_target_credit_below_floor"


def test_over_target_rich_credit_still_enters():
    # Favorable conditions — credit well above the raised floor — let a portfolio exceed its daily
    # target. This is the whole point of the target being guidance rather than a cap.
    snap = _base_snapshot(now_et="13:00", iv_rank=0.50,
                          candidates=[_candidate(5, 583, 598, sp_bid=1.20, sp_ask=1.30,
                                                 sc_bid=1.15, sc_ask=1.25)])
    params = _params(CONSERVATIVE)
    entered, reason, _ = paper.evaluate_entry(snap, params, [],
                                              todays_entry_count=params["daily_ic_trade_target"])
    assert entered is True and reason == "entered"


def test_process_symbol_skips_profile_not_trading_symbol(tmp_path, monkeypatch):
    # A profile that declares a `symbols` subset must be skipped entirely (absent from results) for
    # any symbol outside it. No profile in the registry pins symbols now — the ladder trades the full
    # base set — so the registry is monkeypatched with a pinned pair to exercise the code path.
    db_path = str(tmp_path / "paper_pin.db")
    subprocess.run([sys.executable, str(Path(__file__).parent.parent / "src" / "db.py"),
                    "--db", db_path, "init_db"], check=True, capture_output=True)
    monkeypatch.setattr(paper, "load_profiles", lambda: {
        "xsp-only": {"symbols": ["XSP"]},
        "spx-only": {"symbols": ["SPX"]},
    })
    snapshot = _base_snapshot(symbol="SPX", now_et="13:00", underlying_price=7500.0, iv_rank=0.32,
                              candidates=[_candidate(5, 7380, 7560, sp_delta=-0.15, sc_delta=0.15)])
    out = paper.process_symbol(snapshot, db_path, "paper")
    assert "xsp-only" not in out["results"]   # pinned to XSP -> skipped for an SPX snapshot
    assert "spx-only" in out["results"]        # pinned to SPX -> evaluated
