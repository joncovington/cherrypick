"""Jade lizard earnings strategy, adapted for earnings: short put + short
call spread (short call, long call further OTM), structured so total
credit exceeds the call-spread width -- the standard "zero risk on the
call side" jade lizard condition. Published guidance generally warns
against this strategy near earnings (it assumes elevated-but-orderly IV,
not a binary earnings-scale move); this project's adaptation ties the
call spread's strikes to the same `expected_move` convention every other
strategy here uses (not a fixed delta, the standard-strategy default), and
adds an explicit stop-loss check on the naked put side via
evaluate_position() -- the original design has no answer for
earnings-scale downside risk otherwise.

The put side is undefined risk (that's inherent to what makes this a jade
lizard rather than an iron condor -- the whole point is trading away
upside risk for extra put-side exposure), so it shares short_strangle.py's
scanner.naked_strategies_allowed() gate -- paper mode is always allowed
regardless of `allow_naked_strategies` (no real capital or margin at risk
in paper mode), live mode still requires the flag deliberately enabled.

Uses src/scanner.py's shared engine for everything not specific to this
strategy's structure or thresholds. Strategy-specific config lives under
config.json's "strategies.jade_lizard" key.

Wired into the live/paper trading loop's Step 4b for paper-mode entries;
live-mode entries are hard-blocked there regardless of ranking outcome,
same reasoning as short_strangle.py. evaluate_position() is built and
unit-tested but not called from a loop step yet, same state
double_calendar.py's exit logic was in before its own Step 3b wiring.

Commands (see CLAUDE.md's Tool Reference):
  get_candidates --date MM/DD/YYYY
  get_order --symbol X --earnings_date YYYY-MM-DD --earnings_timing "..."
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scanner


def _strategy_config(config: dict) -> dict:
    return config.get("strategies", {}).get("jade_lizard", {})


def _wing_width_multiple(iv_rv_ratio: float | None, config: dict) -> float:
    """Same IV/RV-banded wing-width convention as iron_fly.py/iron_condor.py
    (own copy, not shared, matching the established one-order-builder-per-
    file convention) -- used here to size the call spread's width.
    """
    if iv_rv_ratio is None:
        return config.get("wing_width_credit_multiple", 3.0)
    low_max = config.get("wing_width_band_low_max", 1.25)
    mid_max = config.get("wing_width_band_mid_max", 1.75)
    if iv_rv_ratio < low_max:
        return config.get("wing_width_multiple_low", 2.5)
    if iv_rv_ratio < mid_max:
        return config.get("wing_width_multiple_mid", 3.0)
    return config.get("wing_width_multiple_high", 3.5)


def _compute_jade_lizard_legs(symbol: str, front_exp, price: float, expected_move: float, full_config: dict) -> dict:
    """Shared leg-selection/credit-economics logic used by both the
    scan-side fetch (to verify call_side_riskless at screening time, not
    just at order-build time) and the order-builder -- avoids duplicating
    the wide-chain fetch and strike-selection math twice while keeping
    each caller's own re-fetch discipline (both call this fresh, they just
    don't re-implement the strike math independently).

    Returns {"ok": True, "short_put": ..., "short_call": ..., "long_call": ...,
    "call_spread_width": ..., "total_credit": ..., "call_side_riskless": ...,
    "iv_rv_ratio": ...} or {"ok": False, "error": ...}.
    """
    config = _strategy_config(full_config)
    front_chain = scanner.call_tt([
        "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
        "--include_greeks", "--include_quotes", "--strike_count", "40", "--around_price", str(price),
    ])
    if not front_chain.get("ok"):
        return {"ok": False, "error": front_chain.get("error", "get_option_chain failed")}
    entries = front_chain["chain"][str(front_exp)]

    short_put = scanner.nearest_strike_entry(entries, "put", price - expected_move, -1.0)
    short_call = scanner.nearest_strike_entry(entries, "call", price + expected_move, -1.0)
    if short_put is None or short_call is None:
        return {"ok": False, "error": "no strikes found near expected-move boundaries"}
    if short_put.get("mid") is None or short_call.get("mid") is None:
        return {"ok": False, "error": "no quote data for short strikes"}

    ivrv = scanner.fetch_iv_rv_ratio(symbol, full_config)
    iv_rv_ratio = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None
    wing_multiple = _wing_width_multiple(iv_rv_ratio, config)
    # Call-spread width is sized off the short call's own premium (mirrors
    # iron_fly's wing sizing off straddle_credit), not the expected_move
    # value itself -- the call spread's width is a protection/credit
    # tradeoff, not a strike-placement decision.
    wing_width = wing_multiple * short_call["mid"]

    short_call_strike = float(short_call["strike_price"])
    long_call = scanner.nearest_strike_entry(entries, "call", short_call_strike + wing_width, short_call_strike)
    if long_call is None or long_call.get("mid") is None:
        return {"ok": False, "error": "no quote data for call-spread protection strike"}

    long_call_strike = float(long_call["strike_price"])
    call_spread_width = long_call_strike - short_call_strike
    call_spread_credit = short_call["mid"] - long_call["mid"]
    put_credit = short_put["mid"]
    total_credit = put_credit + call_spread_credit

    return {
        "ok": True,
        "short_put": short_put,
        "short_call": short_call,
        "long_call": long_call,
        "call_spread_width": call_spread_width,
        "total_credit": total_credit,
        "call_side_riskless": total_credit >= call_spread_width,
        "iv_rv_ratio": iv_rv_ratio,
    }


def fetch_price_and_term_structure(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Live price + term structure/expected move via scanner.py's shared
    helpers -- same criteria as iron_condor.py/short_strangle.py's scan-side
    fetch, plus `naked_strategies_allowed` (this strategy's put side is
    undefined risk, same gate as short_strangle.py) and `call_side_riskless`
    (verified here at screening time via _compute_jade_lizard_legs, not just
    when an order is actually built).

    Returns {"ok": False, "error": ...} on any missing data rather than
    raising -- same discipline as every other strategy.
    """
    try:
        qe = scanner.fetch_quote_and_expirations(symbol)
        if not qe.get("ok"):
            return qe
        price, expirations = qe["price"], qe["expirations"]

        front_exp, err = scanner.select_front_expiration(expirations, earnings_date, earnings_timing)
        if front_exp is None:
            return {"ok": False, "error": err}
        back_exp = scanner.select_back_expiration(expirations, front_exp, 21)
        if back_exp is None:
            return {"ok": False, "error": "no back-month expiration available for term structure"}

        atm = scanner.fetch_front_back_atm_entries(symbol, front_exp, back_exp, price)
        if not atm.get("ok"):
            return atm
        front_call, front_put, back_call = atm["front_call"], atm["front_put"], atm["back_call"]

        ts = scanner.compute_expected_move_and_term_structure(
            front_call["mid"], front_put["mid"], front_call["iv"], back_call["iv"], price,
        )
        liquidity = scanner.fetch_liquidity_criteria(symbol, front_exp, expirations, front_call, front_put)

        legs = _compute_jade_lizard_legs(symbol, front_exp, price, ts["expected_move_dollars"], full_config)
        call_side_riskless = legs.get("call_side_riskless") if legs.get("ok") else None

        return {
            "ok": True,
            "criteria": {
                "price": price,
                "term_structure": ts["term_structure"],
                "expected_move_dollars": ts["expected_move_dollars"],
                "expected_move_pct": ts["expected_move_pct"],
                "front_expiration_days": (front_exp - date.today()).days,
                "chain_complete": True,
                "naked_strategies_allowed": scanner.naked_strategies_allowed(full_config),
                "call_side_riskless": call_side_riskless,
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Same shape as iron_condor.py's/
    short_strangle.py's apply_tiering, plus two jade-lizard-specific hard
    filters: naked_strategy_disabled (put side is undefined risk) and
    call_side_not_riskless (the defining "zero risk on the call side"
    condition isn't met with real, live-fetched prices -- reject rather
    than let the order build with silent residual call-side risk).
    `config` is this strategy's own sub-config.
    """
    hard_fail: list[str] = []

    if criteria.get("price") is None:
        hard_fail.append("price_unverified")
    elif criteria["price"] < config["min_price"]:
        hard_fail.append("price_below_minimum")

    if criteria.get("term_structure") is None:
        hard_fail.append("term_structure_unverified")
    elif criteria["term_structure"] > config["min_term_structure"]:
        hard_fail.append("term_structure_insufficient")

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

    if not criteria.get("naked_strategies_allowed"):
        hard_fail.append("naked_strategy_disabled")

    if criteria.get("call_side_riskless") is None:
        hard_fail.append("call_side_riskless_unverified")
    elif criteria["call_side_riskless"] is False:
        hard_fail.append("call_side_not_riskless")

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


def fetch_jade_lizard_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable jade lizard order spec: short put at
    price - expected_move, short call at price + expected_move, long call
    further OTM at short_call_strike + wing_width (IV/RV-banded, same
    convention as iron_fly/iron_condor). Re-checks both the naked-strategy
    gate and the riskless-call-side condition before returning an order --
    the strategy's own defining constraint (credit must exceed call-spread
    width) is enforced here, not just flagged in apply_tiering.

    Deliberately re-fetches live data at call time -- same discipline as
    every other strategy's order-builder.
    """
    if not scanner.naked_strategies_allowed(full_config):
        return {"ok": False, "error": "naked strategies disabled (allow_naked_strategies=false)"}
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

        legs = _compute_jade_lizard_legs(symbol, front_exp, price, expected_move, full_config)
        if not legs.get("ok"):
            return legs
        if not legs["call_side_riskless"]:
            return {
                "ok": False,
                "error": "call_side_not_riskless",
                "total_credit": round(legs["total_credit"], 2),
                "call_spread_width": round(legs["call_spread_width"], 2),
            }

        short_put, short_call, long_call = legs["short_put"], legs["short_call"], legs["long_call"]
        short_put_strike = float(short_put["strike_price"])
        short_call_strike = float(short_call["strike_price"])
        long_call_strike = float(long_call["strike_price"])

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(legs["total_credit"], 2),
            "price_effect": "Credit",
            "legs": [
                {"symbol": short_put["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": short_call["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": long_call["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
            ],
        }
        return {
            "ok": True,
            "strategy": "jade_lizard",
            "order": order,
            "symbol": symbol,
            "expiration": str(front_exp),
            "underlying_price": price,
            "short_put_strike": short_put_strike,
            "short_call_strike": short_call_strike,
            "long_call_strike": long_call_strike,
            "expected_move": expected_move,
            "call_spread_width": round(legs["call_spread_width"], 2),
            "total_credit": round(legs["total_credit"], 2),
            "call_side_riskless": legs["call_side_riskless"],
            "iv_rv_ratio": legs["iv_rv_ratio"],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def evaluate_position(position: dict, open_legs: list[dict], quotes: dict, config: dict) -> dict:
    """Decide what (if anything) to do with an open jade lizard position
    this tick. Pure calculation, no I/O -- mirrors double_calendar.py's
    evaluate_position() pattern. The call spread is riskless by
    construction (credit >= width at entry), so this only ever needs to
    watch the naked put side for earnings-scale downside risk -- the
    original jade lizard design has no answer for that otherwise.

    `quotes` is `{symbol: {"bid": F, "ask": F, "delta": F}}` for the short
    put's symbol (and, if still open, the call spread's legs, though those
    are not evaluated here since they carry no directional stop -- the
    credit already collected exceeds their max width).

    Returns:
      {"action": "hold"}
      {"action": "close_put", "reason": "put_stop"}
    """
    cfg = config
    put_stop_delta_abs = cfg.get("put_stop_delta_abs", 0.45)
    short_put_leg = next((leg for leg in open_legs if leg["leg_role"] == "short_put"), None)
    if short_put_leg is None:
        return {"action": "hold"}
    q = quotes.get(short_put_leg["symbol"])
    if q is None or q.get("delta") is None:
        return {"action": "hold"}
    if abs(q["delta"]) >= put_stop_delta_abs:
        return {"action": "close_put", "reason": "put_stop"}
    return {"action": "hold"}


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date. Thin wrapper around
    scanner.run_candidate_scan -- shared with every other strategy's
    cmd_get_candidates.
    """
    config = scanner._load_config()
    strategy_config = _strategy_config(config)
    return scanner.run_candidate_scan(args.date, config, fetch_price_and_term_structure, apply_tiering, strategy_config)


def main() -> None:
    scanner.run_strategy_main(cmd_get_candidates, fetch_jade_lizard_order)


if __name__ == "__main__":
    main()
