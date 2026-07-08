"""Directional single credit spread earnings strategy: a single-sided
vertical credit spread (put spread if bullish, call spread if bearish) --
sell one OTM option, buy a further-OTM option of the same type/expiration
as protection. Single expiration, defined risk, same overnight-gap hold
model as iron_fly.py/iron_condor.py/expected_move_butterfly.py (opened
before close, closed after the next open).

Distinct from iron_condor.py in the ways that make this genuinely
directional rather than neutral:
- One side only, not a strangle -- iron_condor sells both the call and put
  expected-move boundaries; this strategy picks a single side via
  expected_move_butterfly.scanner.select_side()'s 25-delta risk reversal (sell
  whichever side, call or put, carries the richer IV) and reuses that
  function directly rather than re-deriving the same skew calculation a
  third time. Research confirms this mapping holds for a credit spread too:
  sell put spreads when puts are rich with fear premium, sell call spreads
  when calls are rich -- the same "sell what's overpriced" premium-
  collection logic already underlying iron_fly's/double_calendar's IV/RV
  screening, not a new interpretation.
- Strike selection is breakeven-anchored, not boundary-anchored -- every
  other strategy that uses the expected-move convention (iron_condor,
  double_calendar, expected_move_butterfly) places a strike AT price +/-
  expected_move. This strategy instead searches candidate short strikes so
  that BREAKEVEN (short strike minus credit received, for a put spread; plus
  credit, for a call spread) lands at that boundary -- the strike itself
  sits further OTM than the raw expected move, with the credit collected
  providing the additional cushion out to the boundary. The credit is part
  of the strike-selection target, not just a byproduct of a strike chosen
  another way, so this needs to evaluate multiple candidate strikes (see
  _select_short_strike) rather than a single formula lookup.

Uses src/scanner.py's shared engine (calendar, IV/RV, winrate, volume,
liquidity gates, generic exit-debit calc, credit-spread exit evaluation,
ranking, CLI scaffolding) for everything not specific to this strategy's
structure. Strategy-specific config lives under config.json's
"strategies.directional_credit_spread" key.

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
    return config.get("strategies", {}).get("directional_credit_spread", {})


def fetch_price_and_term_structure(symbol: str, earnings_date: date, earnings_timing: str, config: dict) -> dict:
    """Live price + term structure/expected move + side selection, via
    scanner.py's shared helpers and expected_move_butterfly.scanner.select_side().
    Mirrors iron_condor.py's fetch_price_and_term_structure with skew/side
    folded in, same shape expected_move_butterfly.py's own function returns.

    Returns {"ok": False, "error": ...} on any missing data rather than
    raising -- same discipline as every other strategy module.

    `config` here is the full project config (whatever run_candidate_scan
    passes), not this strategy's own sub-config -- scanner.select_side's
    skew_delta_target is read via `_strategy_config(config)` explicitly,
    same discipline documented in double_calendar.py/expected_move_butterfly.py
    for the same reason.
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
                "term_structure": ts["term_structure"],
                "expected_move_dollars": ts["expected_move_dollars"],
                "expected_move_pct": ts["expected_move_pct"],
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
    """Pure function -- no I/O. iron_condor.py's hard-fail/near-miss shape
    plus expected_move_butterfly.py's skew_abs hard-filter: a small
    call/put IV difference isn't a real directional signal to anchor a
    strike-selection target off of, same reasoning as that module's
    insufficient_skew_signal check. `config` is this strategy's own
    sub-config.
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

    # Entry condition: Realized move dispersion
    if criteria.get("realized_move_dispersion_pct") is not None:
        max_dispersion = config.get("max_realized_move_dispersion_pct", 0.25)
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
    """Identical convention to iron_fly.py's/iron_condor.py's own copies
    (own copy, not shared, matching the established one-order-builder-
    per-file convention) -- wing width sizing scaled by this candidate's
    own IV/RV ratio.
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


def _select_short_strike(entries: list[dict], side: str, price: float, expected_move: float, wing_multiple: float) -> dict | None:
    """The one genuinely new piece of strike-selection logic in this
    project: search candidate short strikes on `side` and pick the one
    whose resulting BREAKEVEN (short strike minus credit, for a put spread;
    plus credit, for a call spread) lands closest to the expected-move
    boundary (price - expected_move for puts, price + expected_move for
    calls) -- not the strike itself sitting at that boundary, the way
    iron_condor.py/double_calendar.py/expected_move_butterfly.py place
    theirs. Wing width for each candidate is sized off that candidate's own
    premium (wing_multiple * short_mid) since there's no second short leg
    to combine into a strangle credit the way iron_condor.py's sizing does.

    Pure function, no I/O -- `entries` is an already-fetched option chain
    for one expiration. Returns
    {"short": entry, "long": entry, "credit": float, "breakeven": float}
    or None if no valid candidate (positive credit, valid further-OTM long
    strike) exists.
    """
    want = side[0].lower()
    candidates = sorted(
        (e for e in entries if str(e.get("option_type", "")).strip().lower().startswith(want) and e.get("mid") is not None),
        key=lambda e: float(e["strike_price"]),
    )
    target = price - expected_move if side == "put" else price + expected_move

    best = None
    best_score = None
    for short_entry in candidates:
        short_strike = float(short_entry["strike_price"])
        short_mid = short_entry["mid"]
        wing_width = wing_multiple * short_mid
        long_target = short_strike - wing_width if side == "put" else short_strike + wing_width
        long_entry = scanner.nearest_strike_entry(entries, side, long_target, short_strike)
        if long_entry is None or long_entry.get("mid") is None:
            continue
        credit = short_mid - long_entry["mid"]
        if credit <= 0:
            continue
        breakeven = short_strike - credit if side == "put" else short_strike + credit
        score = abs(breakeven - target)
        if best_score is None or score < best_score:
            best = {"short": short_entry, "long": long_entry, "credit": credit, "breakeven": breakeven}
            best_score = score
    return best


def fetch_directional_credit_spread_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable directional credit spread order: sell one
    OTM option, buy a further-OTM option of the same type/expiration, with
    the short strike chosen via _select_short_strike so breakeven lands at
    the expected-move boundary.

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

        side_result = scanner.select_side(symbol, front_exp, price, config)
        if not side_result.get("ok"):
            return side_result
        side = side_result["side"]

        atm_probe = scanner.fetch_front_back_atm_entries(symbol, front_exp, front_exp, price)
        if not atm_probe.get("ok"):
            return atm_probe
        expected_move = 0.85 * (atm_probe["front_call"]["mid"] + atm_probe["front_put"]["mid"])

        # Wide strike window: the breakeven-anchored short strike can sit
        # meaningfully further OTM than the raw expected-move boundary,
        # same wide-net reasoning as double_calendar's/iron_condor's chain
        # fetch.
        chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_quotes", "--strike_count", "40", "--around_price", str(price),
        ])
        if not chain.get("ok"):
            return {"ok": False, "error": chain.get("error", "get_option_chain failed")}
        entries = chain["chain"][str(front_exp)]

        ivrv = scanner.fetch_iv_rv_ratio(symbol, full_config)
        iv_rv_ratio = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None
        wing_multiple = _wing_width_multiple(iv_rv_ratio, config)

        picked = _select_short_strike(entries, side, price, expected_move, wing_multiple)
        if picked is None:
            return {"ok": False, "error": "no valid short/long strike pair found"}

        short_entry, long_entry, credit, breakeven = picked["short"], picked["long"], picked["credit"], picked["breakeven"]
        short_strike = float(short_entry["strike_price"])
        long_strike = float(long_entry["strike_price"])

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(credit, 2),
            "price_effect": "Credit",
            "legs": [
                {"symbol": short_entry["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": long_entry["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
            ],
        }
        return {
            "ok": True,
            "strategy": "directional_credit_spread",
            "order": order,
            "symbol": symbol,
            "side": side,
            "expiration": str(front_exp),
            "underlying_price": price,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "expected_move": expected_move,
            "breakeven": round(breakeven, 2),
            "wing_width_multiple_used": wing_multiple,
            "iv_rv_ratio": iv_rv_ratio,
            "skew": side_result["skew"],
            "credit": round(credit, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def evaluate_position(position: dict, quotes: dict, config: dict) -> dict:
    """Decide whether to close an open directional credit spread position
    early (CLAUDE.md Step 3c) ahead of Step 3's unconditional close-window
    sweep. Identical pattern to iron_fly.py's/iron_condor.py's
    evaluate_position() -- same credit-spread shape, same shared
    scanner.evaluate_credit_spread_exit thresholds (config: profit_target_pct,
    stop_loss_credit_multiple), plus a 4-hour post-announcement backstop
    (iv_crush_backstop) to prevent sitting through directional drift after
    IV crush window closes. No `open_legs` argument -- this strategy
    never populates `trade_legs`, always closes as a single unit via
    `legs_json`.
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
    scanner.run_strategy_main(cmd_get_candidates, fetch_directional_credit_spread_order)


if __name__ == "__main__":
    main()
