"""Expected-move butterfly earnings strategy: pick call or put side (via
options skew), buy 1 ATM, sell 2 at the expected-move strike, buy 1 further
OTM equidistant from the short strike as the short is from ATM. A standard
symmetric 1-2-1 butterfly, NOT an iron butterfly (iron_fly.py's straddle +
wings, always direction-agnostic and a net credit) and NOT a broken-wing/
skip-strike butterfly (ATM-to-short and short-to-far distances are equal,
measured off real listed strikes, matching double_calendar.py's
nearest-available-strike discipline).

Side selection uses skew: compare front-month IV at the call short strike
(ATM + expected move) vs. the put short strike (ATM - expected move); build
the butterfly on whichever side is richer (higher IV), since selling the
richer side's short strikes captures more of the edge this strategy is
designed around -- same "sell what's overpriced" logic already underlying
iron_fly's and double_calendar's IV/RV-based screening, applied side-by-side
instead of across time or across name.

Single front expiration only (no back-month) -- built entirely on top of
scanner.py's shared helpers (fetch_quote_and_expirations,
select_front_expiration, fetch_liquidity_criteria/apply_liquidity_gates,
_band, run_candidate_scan, run_strategy_main), added alongside iron_fly.py
and double_calendar.py rather than duplicating their preamble/liquidity
logic a third time.

NOT yet wired into the live/paper trading loop (CLAUDE.md's Loop Steps) --
exit/stop-management for a debit butterfly (profit target near the short
strike, stop if price breaches either long wing, time exit before
expiration) is a distinct follow-up, analogous to double_calendar's own
exit-logic work.

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
    return config.get("strategies", {}).get("expected_move_butterfly", {})


def select_side(symbol: str, front_expiration, price: float, config: dict) -> dict:
    """25-delta risk reversal (call IV - put IV at matched |delta|, the
    industry-standard skew measure) to pick which side to sell -- NOT
    measured at the expected-move strikes themselves. Deliberately separate
    from strike placement: comparing IV at strikes chosen by raw dollar
    distance from price (price +/- expected_move) is NOT delta-symmetric
    whenever skew is already present, which distorts the very quantity being
    measured, and single-stock options carry a persistent *structural* put
    skew (investors chronically buying puts for downside protection) even
    with zero earnings catalyst -- comparing raw IV at dollar-distance
    strikes would pick "sell puts" on a large fraction of candidates
    regardless of any real earnings-specific signal. Matching on delta
    doesn't eliminate that structural baseline (still not backtested
    per-name against its own pre-earnings skew history, which would need
    data this project doesn't have), but it removes the additional,
    avoidable distortion of comparing non-delta-symmetric strikes.

    This function only decides direction; fetch_price_and_expected_move/
    fetch_expected_move_butterfly_order still build the actual order at the
    expected-move-boundary strikes on whichever side wins here.
    """
    chain = scanner.call_tt([
        "get_option_chain", "--symbol", symbol, "--expiration", str(front_expiration),
        "--include_greeks", "--strike_count", "40", "--around_price", str(price),
    ])
    if not chain.get("ok"):
        return {"ok": False, "error": chain.get("error", "get_option_chain failed")}
    entries = chain["chain"].get(str(front_expiration), [])

    target_delta = config.get("skew_delta_target", 0.25)
    calls = [e for e in entries if str(e.get("option_type", "")).strip().lower().startswith("c") and e.get("delta") is not None]
    puts = [e for e in entries if str(e.get("option_type", "")).strip().lower().startswith("p") and e.get("delta") is not None]
    if not calls or not puts:
        return {"ok": False, "error": "no greeks/delta data for skew measurement"}

    call_ref = min(calls, key=lambda e: abs(abs(e["delta"]) - target_delta))
    put_ref = min(puts, key=lambda e: abs(abs(e["delta"]) - target_delta))
    if call_ref.get("iv") is None or put_ref.get("iv") is None:
        return {"ok": False, "error": "no iv data for risk-reversal candidates"}

    call_iv, put_iv = call_ref["iv"], put_ref["iv"]
    side = "call" if call_iv >= put_iv else "put"
    return {"ok": True, "side": side, "call_iv": call_iv, "put_iv": put_iv, "skew": call_iv - put_iv}


def fetch_price_and_expected_move(symbol: str, earnings_date: date, earnings_timing: str, config: dict) -> dict:
    """Live price + expected move + side selection for expected-move-butterfly
    screening, via scanner.py's shared helpers. Single front expiration only
    -- no back-month is needed (this strategy has no term-structure/calendar
    comparison, just a same-expiration butterfly).

    Returns {"ok": False, "error": ...} on any missing data rather than
    raising -- same discipline as iron_fly.py/double_calendar.py.

    `config` here is whatever run_candidate_scan (via cmd_get_candidates)
    passes -- the full project config, not this strategy's own sub-config
    -- so select_side's skew_delta_target is read via
    `_strategy_config(config)` explicitly, matching the fix applied to
    double_calendar.py's analogous back_month_min_days_after bug (reading a
    strategy-specific key straight off this function's `config` param
    would silently fall back to the default every time).
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

        # No back-month needed, but fetch_front_back_atm_entries' front-side
        # ATM straddle is still the source of the expected-move calc -- reuse
        # the front expiration as its own "back" just to get the ATM
        # call/put mid without duplicating the chain-fetch/ATM-lookup logic
        # a third time. The back_call/back_iv it returns is discarded; only
        # the front-month straddle mid matters here.
        atm = scanner.fetch_front_back_atm_entries(symbol, front_exp, front_exp, price)
        if not atm.get("ok"):
            return atm
        front_call, front_put = atm["front_call"], atm["front_put"]

        expected_move = 0.85 * (front_call["mid"] + front_put["mid"])
        expected_move_pct = expected_move / price

        side_result = select_side(symbol, front_exp, price, strategy_config)
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
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Applies this strategy's own hard filters and
    near-miss bands. `config` is this strategy's own sub-config
    (config["strategies"]["expected_move_butterfly"]), not the full project
    config. Same discipline as iron_fly.py/double_calendar.py's apply_tiering:
    a missing value is an unverified hard-fail, never a silent pass.
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

    # A small call/put IV difference isn't a real directional signal, just
    # noise -- picking a side on it would be arbitrary, not "selling the
    # richer side."
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


def fetch_expected_move_butterfly_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable expected-move butterfly order: 1 long ATM,
    2 short at the expected-move strike, 1 long further OTM equidistant from
    the short strike as the short is from ATM (real listed-strike distances,
    not the raw expected-move value -- same nearest-available-strike
    discipline double_calendar.py established). Same option type (call or
    put, per skew) and expiration throughout.

    Deliberately re-fetches live data at call time -- same discipline as
    both existing order-builders.
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

        side_result = select_side(symbol, front_exp, price, config)
        if not side_result.get("ok"):
            return side_result
        side = side_result["side"]

        # Wide window: need the ATM strike and both short/far strikes,
        # potentially well away from it, same as iron_fly's wing-selection
        # window.
        chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_quotes", "--strike_count", "40", "--around_price", str(price),
        ])
        if not chain.get("ok"):
            return {"ok": False, "error": chain.get("error", "get_option_chain failed")}
        entries = chain["chain"][str(front_exp)]

        atm_entry = scanner.atm_entry(entries, side, price)
        if atm_entry is None or atm_entry.get("mid") is None:
            return {"ok": False, "error": "no quote data for ATM strike"}
        atm_strike = float(atm_entry["strike_price"])

        short_target = price + expected_move if side == "call" else price - expected_move
        short_entry = scanner.nearest_strike_entry(entries, side, short_target, atm_strike)
        if short_entry is None or short_entry.get("mid") is None:
            return {"ok": False, "error": "no quote data for short strike"}
        short_strike = float(short_entry["strike_price"])

        width = abs(short_strike - atm_strike)
        far_target = short_strike + width if side == "call" else short_strike - width
        far_entry = scanner.nearest_strike_entry(entries, side, far_target, short_strike)
        if far_entry is None or far_entry.get("mid") is None:
            return {"ok": False, "error": "no quote data for far OTM strike"}
        far_strike = float(far_entry["strike_price"])

        net_debit = atm_entry["mid"] - 2 * short_entry["mid"] + far_entry["mid"]

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(abs(net_debit), 2),
            "price_effect": "Debit" if net_debit >= 0 else "Credit",
            "legs": [
                {"symbol": atm_entry["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
                {"symbol": short_entry["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 2},
                {"symbol": far_entry["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
            ],
        }
        return {
            "ok": True,
            "strategy": "expected_move_butterfly",
            "order": order,
            "symbol": symbol,
            "side": side,
            "expiration": str(front_exp),
            "underlying_price": price,
            "atm_strike": atm_strike,
            "short_strike": short_strike,
            "far_strike": far_strike,
            "width": width,
            "expected_move": expected_move,
            "skew": side_result["skew"],
            "net_debit": round(net_debit, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date. Thin wrapper around
    scanner.run_candidate_scan -- shared with every other strategy's
    cmd_get_candidates.
    """
    config = scanner._load_config()
    strategy_config = _strategy_config(config)
    return scanner.run_candidate_scan(args.date, config, fetch_price_and_expected_move, apply_tiering, strategy_config)


def main() -> None:
    scanner.run_strategy_main(cmd_get_candidates, fetch_expected_move_butterfly_order)


if __name__ == "__main__":
    main()
