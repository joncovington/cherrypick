"""Iron condor earnings strategy: sell a strangle at the expected-move
boundaries (short call at price + expected_move, short put at
price - expected_move), buy wings sized off the credit received -- the
same credit-spread-plus-wings shape as iron_fly.py, but the short strikes
sit at the expected-move boundary instead of ATM. This is a genuinely
different risk/reward profile, not a config tweak of iron fly: a wider
profit zone and lower credit than iron fly's ATM straddle, with a
materially different historical win rate.

Uses src/scanner.py's shared engine (calendar, IV/RV, winrate, volume,
ATM/strike helpers, liquidity gates, ranking, CLI scaffolding) for
everything not specific to this strategy's structure or thresholds.
Strategy-specific config lives under config.json's "strategies.iron_condor"
key.

Commands (see CLAUDE.md's Tool Reference):
  get_candidates --date MM/DD/YYYY
  get_order --symbol X --earnings_date YYYY-MM-DD --earnings_timing "..."
"""

import json
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scanner


def _strategy_config(config: dict) -> dict:
    return config.get("strategies", {}).get("iron_condor", {})


def fetch_price_and_term_structure(symbol: str, earnings_date: date, earnings_timing: str, config: dict) -> dict:
    """Live price + term structure/expected move via scanner.py's shared
    helpers. Mirrors iron_fly.py's fetch_price_and_term_structure exactly
    except there is no atm_delta_abs sanity check (that existed to confirm
    "is this strike actually ATM," which doesn't apply here -- the short
    strikes are deliberately OTM) -- expected_move_pct is checked instead,
    same convention as double_calendar/expected_move_butterfly, since the
    short strikes are placed at the expected-move boundary.

    Returns {"ok": False, "error": ...} on any missing data rather than
    raising -- same discipline as iron_fly.py.
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

        return {
            "ok": True,
            "criteria": {
                "price": price,
                "term_structure": ts["term_structure"],
                "expected_move_dollars": ts["expected_move_dollars"],
                "expected_move_pct": ts["expected_move_pct"],
                "front_expiration_days": (front_exp - date.today()).days,
                "chain_complete": True,
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Same structure as iron_fly.py's apply_tiering,
    minus atm_delta_abs (not meaningful for deliberately-OTM short strikes)
    and plus expected_move_pct (double_calendar/expected_move_butterfly's
    convention, since strikes are placed at the expected-move boundary
    here too). `config` is this strategy's own sub-config.
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
    """Identical convention to iron_fly.py's own copy (own copy, not shared,
    matching the established one-order-builder-per-file convention) --
    wing width sizing scaled by this candidate's own IV/RV ratio.
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


def fetch_iron_condor_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable iron condor order spec: sell a strangle
    at the expected-move boundaries, buy wings sized by _wing_width_multiple()
    from each short strike outward. Mirrors fetch_iron_fly_order's structure
    exactly except short_call/short_put are selected via
    scanner.nearest_strike_entry targeting price +/- expected_move (like
    double_calendar's/expected_move_butterfly's strike selection) instead of
    scanner.atm_entry.

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

        # ATM straddle mid (narrow window, front expiration used as its own
        # "back" -- same reuse trick expected_move_butterfly.py uses) is the
        # source of the expected-move calc; the back_call/back_iv it
        # returns is discarded.
        atm_probe = scanner.fetch_front_back_atm_entries(symbol, front_exp, front_exp, price)
        if not atm_probe.get("ok"):
            return atm_probe
        expected_move = 0.85 * (atm_probe["front_call"]["mid"] + atm_probe["front_put"]["mid"])

        # Wide strike window: need both short strikes (out at the
        # expected-move boundary) and wings potentially far beyond them.
        front_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_quotes", "--strike_count", "40", "--around_price", str(price),
        ])
        if not front_chain.get("ok"):
            return {"ok": False, "error": front_chain.get("error", "get_option_chain failed")}
        entries = front_chain["chain"][str(front_exp)]

        short_call = scanner.nearest_strike_entry(entries, "call", price + expected_move, -1.0)
        short_put = scanner.nearest_strike_entry(entries, "put", price - expected_move, -1.0)
        if short_call is None or short_put is None:
            return {"ok": False, "error": "no strikes found near expected-move boundaries"}
        if short_call.get("mid") is None or short_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for short strikes"}

        short_call_strike = float(short_call["strike_price"])
        short_put_strike = float(short_put["strike_price"])
        strangle_credit = short_call["mid"] + short_put["mid"]
        ivrv = scanner.fetch_iv_rv_ratio(symbol, full_config)
        iv_rv_ratio = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None
        wing_multiple = _wing_width_multiple(iv_rv_ratio, config)
        wing_width = wing_multiple * strangle_credit

        long_call = scanner.nearest_strike_entry(entries, "call", short_call_strike + wing_width, short_call_strike)
        long_put = scanner.nearest_strike_entry(entries, "put", short_put_strike - wing_width, short_put_strike)
        if long_call is None or long_put is None:
            return {"ok": False, "error": "no valid wing strikes found"}
        if long_call.get("mid") is None or long_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for wing strikes"}

        net_credit = strangle_credit - long_call["mid"] - long_put["mid"]

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(net_credit, 2),
            "price_effect": "Credit",
            "legs": [
                {"symbol": short_call["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": short_put["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": long_call["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
                {"symbol": long_put["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
            ],
        }
        return {
            "ok": True,
            "strategy": "iron_condor",
            "order": order,
            "symbol": symbol,
            "expiration": str(front_exp),
            "underlying_price": price,
            "short_call_strike": short_call_strike,
            "short_put_strike": short_put_strike,
            "long_call_strike": float(long_call["strike_price"]),
            "long_put_strike": float(long_put["strike_price"]),
            "expected_move": expected_move,
            "wing_width_target": wing_width,
            "wing_width_multiple_used": wing_multiple,
            "iv_rv_ratio": iv_rv_ratio,
            "credit": round(net_credit, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def evaluate_position(position: dict, quotes: dict, config: dict) -> dict:
    """Decide whether to close an open iron condor position early
    (CLAUDE.md Step 3c) ahead of Step 3's unconditional close-window
    sweep. Identical pattern to iron_fly.py's evaluate_position() -- same
    credit-spread shape, same shared scanner.evaluate_credit_spread_exit
    thresholds (config: profit_target_pct, stop_loss_credit_multiple), plus
    a 4-hour post-announcement backstop (iv_crush_backstop) to prevent
    sitting through directional drift after IV crush window closes.
    No `open_legs` argument since iron_condor never populates `trade_legs`
    either -- always closes as a single unit via `legs_json`.
    """
    # IV-CRUSH BACKSTOP: Close after 4 hours have elapsed since entry
    exit_after_announcement_minutes = config.get("exit_after_announcement_minutes", 240)
    if position.get("opened_at") is not None:
        elapsed_minutes = (time.time() - position["opened_at"]) / 60.0
        if elapsed_minutes >= exit_after_announcement_minutes:
            return {"action": "close_all", "reason": "iv_crush_backstop"}

    # PROFIT TARGET / STOP LOSS: Primary exit mechanisms (post-IV-crush capture)
    legs = json.loads(position["legs_json"])
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
    return scanner.run_candidate_scan(args.date, config, fetch_price_and_term_structure, apply_tiering, strategy_config)


def main() -> None:
    scanner.run_strategy_main(cmd_get_candidates, fetch_iron_condor_order)


if __name__ == "__main__":
    main()
