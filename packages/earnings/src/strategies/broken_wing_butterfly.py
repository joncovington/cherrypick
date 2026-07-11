"""Broken wing (skip strike) butterfly earnings strategy: a body-anchored
short strangle-of-one (2 short contracts at the expected-move strike),
protected by two long wings sized directly off the body -- NOT the 1-2-1
"long ATM, short at expected-move, long far OTM" shape expected_move_butterfly.py
uses. A real broken wing butterfly has no ATM leg at all: both wings anchor
to the body strike itself, one narrow (toward current price -- the side
price travels THROUGH to reach the body, the expected move itself) and one
wide (away from current price -- an overshoot beyond what's expected, the
side research says should sit at a low-probability-of-touching strike).
Side (call or put) picked via expected_move_butterfly.scanner.select_side()'s
25-delta risk reversal, reused directly.

Widening the far wing relative to the near wing makes it cheaper
(further-OTM options cost less), financing the position toward a smaller
net debit or a net credit, at the cost of a larger (but still defined) max
loss if price overshoots the body by more than the wide wing's width. The
near wing's width is scaled per-candidate via the same IV/RV-banded
wing-multiple convention iron_fly.py/iron_condor.py/directional_credit_spread.py
use (own _wing_width_multiple() copy, own config bands, applied to the
body leg's own premium -- there's no strangle/straddle credit to combine,
just the one short leg) rather than a fixed dollar width. The far wing is a
configurable multiple of the near wing (config: wide_wing_multiple), not
solved for a credit/zero-debit target -- a deliberately simpler design than
directional_credit_spread.py's breakeven-anchored iterative search.
fetch_broken_wing_butterfly_order hard-rejects (net_debit_positive_credit_required)
any candidate that still prices as a net debit at these widths.

Uses src/scanner.py's shared engine (calendar, IV/RV, winrate, volume,
liquidity gates, generic exit-debit calc, debit-spread exit evaluation,
ranking, CLI scaffolding) for everything else. Strategy-specific config
lives under config.json's "strategies.broken_wing_butterfly" key.

Commands (see CLAUDE.md's Tool Reference):
  get_candidates --date MM/DD/YYYY
  get_order --symbol X --earnings_date YYYY-MM-DD --earnings_timing "..."
"""

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scanner


def _strategy_config(config: dict) -> dict:
    return config.get("strategies", {}).get("broken_wing_butterfly", {})


def fetch_price_and_expected_move(symbol: str, earnings_date: date, earnings_timing: str, config: dict) -> dict:
    """Live price + expected move + side selection, identical shape to
    expected_move_butterfly.py's fetch_price_and_expected_move -- the wing
    asymmetry is purely an order-construction detail (see
    fetch_broken_wing_butterfly_order), not a screening signal, so
    screening needs no new criteria beyond what that module already
    computes.

    Returns {"ok": False, "error": ...} on any missing data rather than
    raising -- same discipline as every other strategy module.

    `config` here is the full project config, not this strategy's own
    sub-config -- scanner.select_side's skew_delta_target is read via
    `_strategy_config(config)` explicitly, same discipline documented in
    double_calendar.py/expected_move_butterfly.py for the same reason.
    """
    strategy_config = _strategy_config(config)
    try:
        qe = scanner.fetch_quote_and_expirations(symbol)
        if not qe.get("ok"):
            return qe
        price, expirations = qe["price"], qe["expirations"]

        front_exp, err = scanner.select_front_expiration(expirations, earnings_date, earnings_timing)
        if front_exp is None:
            return {"ok": False, "error": err}

        atm = scanner.fetch_front_back_atm_entries(symbol, front_exp, front_exp, price)
        if not atm.get("ok"):
            return atm
        front_call, front_put = atm["front_call"], atm["front_put"]

        expected_move = 0.85 * (front_call["mid"] + front_put["mid"])
        expected_move_pct = expected_move / price

        winrate_result = scanner.compute_winrate(symbol, config, config.get("winrate_lookback_quarters", 8))
        realized_move_dispersion = None
        if winrate_result.get("ok"):
            realized_moves = [q["realized_move"] / q["pre_close"] for q in winrate_result["quarters"]]
            if realized_moves:
                mean_realized = sum(realized_moves) / len(realized_moves)
                variance = sum((m - mean_realized) ** 2 for m in realized_moves) / len(realized_moves)
                realized_move_dispersion = variance ** 0.5

        side_result = scanner.select_side(symbol, front_exp, price, strategy_config)
        if not side_result.get("ok"):
            return side_result

        liquidity = scanner.fetch_liquidity_criteria(symbol, front_exp, expirations, front_call, front_put)

        return {
            "ok": True,
            "criteria": {
                "price": price,
                "expected_move_dollars": expected_move,
                "expected_move_pct": expected_move_pct,
                "skew_abs": abs(side_result["skew"]),
                "side": side_result["side"],
                "front_expiration_days": (front_exp - date.today()).days,
                "chain_complete": True,
                "realized_move_dispersion_pct": realized_move_dispersion,
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Identical to expected_move_butterfly.py's
    apply_tiering: a missing value is an unverified hard-fail, never a
    silent pass; a small call/put IV difference isn't a real directional
    signal to build the butterfly on. `config` is this strategy's own
    sub-config.
    """
    hard_fail: list[str] = []

    if criteria.get("price") is None:
        hard_fail.append("price_unverified")
    elif criteria["price"] < config["min_price"]:
        hard_fail.append("price_below_minimum")

    if criteria.get("expected_move_pct") is None:
        hard_fail.append("expected_move_pct_unverified")
    elif criteria["expected_move_pct"] < config["min_expected_move_pct"]:
        hard_fail.append("expected_move_pct_below_minimum")

    if criteria.get("front_expiration_days") is None:
        hard_fail.append("front_expiration_days_unverified")
    elif criteria["front_expiration_days"] > config["max_front_expiration_days"]:
        hard_fail.append("front_expiration_days_too_far_out")

    if not criteria.get("chain_complete"):
        hard_fail.append("chain_complete_unverified")

    # Entry condition: Realized move dispersion
    if criteria.get("realized_move_dispersion_pct") is not None:
        max_dispersion = config.get("max_realized_move_dispersion_pct", 0.20)
        if criteria["realized_move_dispersion_pct"] > max_dispersion:
            hard_fail.append("realized_move_too_inconsistent")

    if criteria.get("skew_abs") is None:
        hard_fail.append("skew_abs_unverified")
    elif criteria["skew_abs"] < config["min_skew_abs"]:
        hard_fail.append("insufficient_skew_signal")

    near_miss: list[str] = []

    scanner.apply_liquidity_gates(criteria, config, hard_fail, near_miss)
    scanner._band(criteria.get("avg_volume"), config["min_avg_volume"], config["near_miss_min_avg_volume"], "avg_volume", near_miss, hard_fail)
    scanner._band(criteria.get("iv_rv_ratio"), config["min_iv_rv_ratio"], config["near_miss_min_iv_rv_ratio"], "iv_rv_ratio", near_miss, hard_fail)
    scanner._band(criteria.get("winrate"), config["min_winrate"], config["near_miss_min_winrate"], "winrate", near_miss, hard_fail)

    if hard_fail:
        tier = "Reject"
    elif not near_miss:
        tier = "Tier 1"
    elif len(near_miss) == 1:
        tier = "Tier 2"
    else:
        tier = "Near Miss"

    return {"tier": tier, "hard_fail_reasons": hard_fail, "near_miss_reasons": near_miss}


def _wing_width_multiple(iv_rv_ratio: float | None, config: dict) -> float:
    """Same IV/RV-banded convention as iron_fly.py's/iron_condor.py's/
    directional_credit_spread.py's own copies (own copy, not shared,
    matching the established one-order-builder-per-file convention), but
    tuned for a much smaller near-body wing here: this sizes a tight wing
    off the body leg's own single-leg premium, not a protective wing off a
    full straddle/strangle credit, so the multiples/defaults are
    deliberately smaller than iron_fly's 2.5/3.0/3.5.
    """
    if iv_rv_ratio is None:
        return config.get("wing_width_multiple_mid", 1.25)
    low_max = config.get("wing_width_band_low_max", 1.25)
    mid_max = config.get("wing_width_band_mid_max", 1.75)
    if iv_rv_ratio < low_max:
        return config.get("wing_width_multiple_low", 1.0)
    if iv_rv_ratio < mid_max:
        return config.get("wing_width_multiple_mid", 1.25)
    return config.get("wing_width_multiple_high", 1.5)


def fetch_broken_wing_butterfly_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable broken wing butterfly order: 2 short
    contracts at the expected-move strike (the body), protected by a narrow
    long wing toward current price and a wide long wing away from it --
    both anchored directly off the body, NOT off a separate ATM leg the way
    expected_move_butterfly.py's 1-2-1 shape is. See module docstring for
    the full rationale.

    Deliberately re-fetches live data at call time -- same discipline as
    every other strategy's order-builder.
    """
    config = _strategy_config(full_config)
    try:
        qe = scanner.fetch_quote_and_expirations(symbol)
        if not qe.get("ok"):
            return qe
        price, expirations = qe["price"], qe["expirations"]

        front_exp, err = scanner.select_front_expiration(expirations, earnings_date, earnings_timing)
        if front_exp is None:
            return {"ok": False, "error": err}

        atm_probe = scanner.fetch_front_back_atm_entries(symbol, front_exp, front_exp, price)
        if not atm_probe.get("ok"):
            return atm_probe
        expected_move = 0.85 * (atm_probe["front_call"]["mid"] + atm_probe["front_put"]["mid"])

        side_result = scanner.select_side(symbol, front_exp, price, config)
        if not side_result.get("ok"):
            return side_result
        side = side_result["side"]

        # Wide window: the wide wing can sit well beyond the body given
        # wide_wing_multiple -- same reasoning as iron_fly's/
        # expected_move_butterfly's wing-selection window.
        chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_quotes", "--strike_count", "40", "--around_price", str(price),
        ])
        if not chain.get("ok"):
            return {"ok": False, "error": chain.get("error", "get_option_chain failed")}
        entries = chain["chain"][str(front_exp)]

        body_target = price + expected_move if side == "call" else price - expected_move
        body_entry = scanner.nearest_strike_entry(entries, side, body_target, -1.0)
        if body_entry is None or body_entry.get("mid") is None:
            return {"ok": False, "error": "no quote data for body strike"}
        body_strike = float(body_entry["strike_price"])

        ivrv = scanner.fetch_iv_rv_ratio(symbol, full_config)
        iv_rv_ratio = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None
        narrow_multiple = _wing_width_multiple(iv_rv_ratio, config)
        narrow_width = narrow_multiple * body_entry["mid"]
        wide_wing_multiple = config.get("wide_wing_multiple", 2.5)
        wide_width = narrow_width * wide_wing_multiple

        # put: near wing sits ABOVE the body (toward current price, which
        # is above a put body); wide wing sits BELOW (away from price).
        # call: mirrored -- near wing BELOW the body (toward price, which
        # is below a call body), wide wing ABOVE (away from price).
        near_target = body_strike - narrow_width if side == "call" else body_strike + narrow_width
        far_target = body_strike + wide_width if side == "call" else body_strike - wide_width

        near_entry = scanner.nearest_strike_entry(entries, side, near_target, body_strike)
        far_entry = scanner.nearest_strike_entry(entries, side, far_target, body_strike)
        if near_entry is None or near_entry.get("mid") is None:
            return {"ok": False, "error": "no quote data for near wing strike"}
        if far_entry is None or far_entry.get("mid") is None:
            return {"ok": False, "error": "no quote data for far wing strike"}
        if float(near_entry["strike_price"]) == float(far_entry["strike_price"]):
            return {"ok": False, "error": "near and far wing strikes collapsed to the same strike"}

        near_strike = float(near_entry["strike_price"])
        far_strike = float(far_entry["strike_price"])
        near_width = abs(near_strike - body_strike)
        far_width = abs(far_strike - body_strike)

        net_debit = near_entry["mid"] - 2 * body_entry["mid"] + far_entry["mid"]

        # Only enter for a net credit (or breakeven) -- a positive net_debit
        # means these wing widths didn't buy back enough premium to finance
        # the position, so the defined-risk cushion this strategy is built
        # around (see module docstring) isn't actually there yet. Checked
        # here, not in apply_tiering, since net_debit only resolves once
        # real premiums are fetched at order-build time -- screening has no
        # visibility into it.
        if net_debit > 0:
            return {"ok": False, "error": "net_debit_positive_credit_required", "net_debit": round(net_debit, 2)}

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(abs(net_debit), 2),
            "price_effect": "Debit" if net_debit >= 0 else "Credit",
            "legs": [
                {"symbol": near_entry["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
                {"symbol": body_entry["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 2},
                {"symbol": far_entry["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
            ],
        }
        return {
            "ok": True,
            "strategy": "broken_wing_butterfly",
            "order": order,
            "symbol": symbol,
            "side": side,
            "expiration": str(front_exp),
            "underlying_price": price,
            "body_strike": body_strike,
            "near_strike": near_strike,
            "far_strike": far_strike,
            "near_width": near_width,
            "far_width": far_width,
            "narrow_wing_multiple_used": narrow_multiple,
            "wide_wing_multiple_used": wide_wing_multiple,
            "iv_rv_ratio": iv_rv_ratio,
            "expected_move": expected_move,
            "skew": side_result["skew"],
            "net_debit": round(net_debit, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def evaluate_position(position: dict, quotes: dict, config: dict, is_first_check_of_day: bool = False) -> dict:
    """Decide whether to close an open broken wing butterfly position early
    (CLAUDE.md Step 3c) ahead of Step 3's unconditional close-window sweep.

    Broken wing butterfly enters for a net credit, holds through earnings
    announcement (the binary event), and closes post-announcement when IV crush
    happens. Uses credit-spread exit logic with two key safety mechanisms:

    1. Per-leg delta stop: close if either short leg goes deep ITM (delta > 0.60),
       preventing gamma blowout during announcement volatility
    2. Profit target/stop loss: based on credit received, not debit paid;
       closes post-announcement when position reaches 10-50% profit

    `is_first_check_of_day`: set to True on first post-gap tick to label
    gap-driven stops separately from mid-session stops.
    """
    gap_suffix = "_overnight_gap" if is_first_check_of_day else ""
    cfg = _strategy_config(config)

    legs = json.loads(position["legs_json"])

    # PER-LEG DELTA STOP: Close if either short body leg goes deep ITM
    # (this prevents gamma blowout risk during announcement volatility)
    leg_stop_delta_abs = cfg.get("leg_stop_delta_abs", 0.60)
    for leg in legs:
        q = quotes.get(leg["symbol"])
        if q is not None and leg["action"] == "Sell to Open":
            delta = q.get("delta")
            if delta is not None and abs(delta) >= leg_stop_delta_abs:
                return {"action": "close_all", "reason": f"leg_stop_delta{gap_suffix}"}

    # PROFIT TARGET / STOP LOSS: Use credit-spread exit logic
    # Close when position reaches 10% profit (conservative post-IV-crush target)
    # or stops at 2.0x credit loss (trader-tested threshold)
    exit_debit = scanner.compute_generic_exit_debit(legs, quotes)
    if exit_debit is None:
        return {"action": "hold"}
    return scanner.evaluate_credit_spread_exit(position["entry_credit"], exit_debit, config)


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date. Thin wrapper around
    scanner.run_candidate_scan -- shared with every other strategy's
    cmd_get_candidates.
    """
    config = scanner._load_config()
    strategy_config = _strategy_config(config)
    return scanner.run_candidate_scan(args.date, config, fetch_price_and_expected_move, apply_tiering, strategy_config)


def main() -> None:
    scanner.run_strategy_main(cmd_get_candidates, fetch_broken_wing_butterfly_order)


if __name__ == "__main__":
    main()
