"""Double calendar earnings strategy: sell a front-month ATM-ish call and
put at the expected-move boundaries, buy the same two strikes in a later
monthly expiration. Net debit. Profits from the front-month IV crushing
harder than the back-month after the earnings reaction -- same underlying
term-structure edge as iron_fly.py, expressed as a calendar spread instead
of a straddle sale.

Distinct from iron_fly.py in a few structural ways worth keeping in mind:
- Debit trade, not credit -- P&L and "how much room to be wrong" scale
  with the debit paid, not a collected credit.
- Strikes sit AT the expected-move boundaries, not ATM -- the profit zone
  is centered on "price lands near where the options market predicted,"
  not "price stays anywhere within a symmetric range."
- Two expirations, not one -- back-month must be a genuine monthly cycle
  (scanner.is_monthly_expiration) at least 21 days after front-month, a
  documented convention distinct from iron_fly's back-month (which only
  needs *a* later expiration for the term-structure comparison, not a
  tradeable leg itself).
- Screening favors consistency (low dispersion in historical realized
  moves) over iron_fly's plain average winrate, and a stricter liquidity
  floor, since execution cost matters more across two expirations and a
  single tail-risk surprise can wipe out the position more completely
  than the iron fly's width-defined max loss.

Uses src/scanner.py's shared engine (calendar, IV/RV, winrate, volume,
ATM/monthly-expiration helpers, ranking) for everything not specific to
this strategy's structure or thresholds. Strategy-specific config lives
under config.json's "strategies.double_calendar" key.

Commands (see CLAUDE.md's Tool Reference):
  get_candidates --date MM/DD/YYYY
  get_order --symbol X --earnings_date YYYY-MM-DD --earnings_timing "..."
"""

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scanner


def _strategy_config(config: dict) -> dict:
    return config.get("strategies", {}).get("double_calendar", {})


def realized_move_dispersion(symbol: str, config: dict, lookback_quarters: int = 8) -> dict:
    """Standard deviation of historical realized earnings moves (as a % of
    pre-earnings price), computed from the same historical earnings dates
    scanner.compute_winrate() already pulls. A low-dispersion name is a
    better double-calendar candidate than a name with the same average
    winrate but occasional huge surprise moves -- a single blowout move
    can wipe out this strategy's debit more completely than the iron
    fly's width-defined max loss, so consistency matters here in a way
    plain average winrate doesn't capture.
    """
    winrate = scanner.compute_winrate(symbol, config, lookback_quarters)
    pct_moves = [
        q["realized_move"] / q["pre_close"]
        for q in winrate["quarters"]
        if q.get("pre_close")
    ]
    if len(pct_moves) < 2:
        return {"ok": False, "symbol": symbol, "sample_size": len(pct_moves), "error": "insufficient sample for dispersion"}
    mean = sum(pct_moves) / len(pct_moves)
    variance = sum((m - mean) ** 2 for m in pct_moves) / (len(pct_moves) - 1)
    std_dev = variance ** 0.5
    return {
        "ok": True,
        "symbol": symbol,
        "sample_size": len(pct_moves),
        "mean_realized_move_pct": mean,
        "realized_move_dispersion_pct": std_dev,
    }


def fetch_price_and_expected_move(symbol: str, earnings_date: date, earnings_timing: str, config: dict) -> dict:
    """Live price, term structure, and expected move for double-calendar
    screening, via scanner.py's shared helpers. Mirrors iron_fly.py's
    fetch_price_and_term_structure but reads this strategy's own
    back_month_min_days_after for back-month selection (a genuine monthly
    cycle, per documented double-calendar convention) rather than
    iron_fly's fixed 21-day comparison-only back-month.

    Returns {"ok": False, "error": ...} on any missing data rather than
    raising -- same discipline as iron_fly.py.

    `config` here is whatever run_candidate_scan (via cmd_get_candidates)
    passes -- the full project config, not this strategy's own sub-config
    -- so back_month_min_days_after is read from `_strategy_config(config)`
    explicitly rather than off `config` directly. A real bug caught while
    wiring up expected_move_butterfly.py's analogous skew_delta_target:
    reading a strategy-specific key straight off this function's `config`
    param silently falls back to the default every time, since that key
    only exists under config["strategies"]["double_calendar"], not at the
    top level -- harmless only because the default happened to match, but a
    real inconsistency with fetch_double_calendar_order (the order-builder),
    which correctly extracts its own sub-config via _strategy_config().
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

        min_days = strategy_config.get("back_month_min_days_after", 21)
        back_exp = scanner.select_back_expiration(expirations, front_exp, min_days)
        if back_exp is None:
            return {"ok": False, "error": f"no monthly (or any) back-month expiration >={min_days} days after front"}

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
                "chain_complete": True,
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Applies this strategy's own hard filters
    and near-miss bands. `config` is this strategy's own sub-config
    (config["strategies"]["double_calendar"]), not the full project
    config. Same discipline as iron_fly.py's apply_tiering: a missing
    value is an unverified hard-fail, never a silent pass; a
    present-but-out-of-range value gets its own distinct rejection
    reason.
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

    if not criteria.get("chain_complete"):
        hard_fail.append("chain_complete_unverified")

    if criteria.get("realized_move_dispersion_pct") is not None and config.get("max_realized_move_dispersion_pct") is not None:
        if criteria["realized_move_dispersion_pct"] > config["max_realized_move_dispersion_pct"]:
            hard_fail.append("realized_move_too_inconsistent")

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


def fetch_double_calendar_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable double calendar order spec: sell front-month
    call/put at the expected-move boundaries, buy the same two strikes in
    the back month. Net debit. `full_config` is the whole project config
    (not just this strategy's sub-config), matching iron_fly.py's
    fetch_iron_fly_order signature, since IV/RV/expected-move computation
    needs the top-level DoltHub connection settings.

    Deliberately re-fetches live data at call time rather than reusing an
    earlier scan snapshot -- same reasoning as fetch_iron_fly_order.
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

        min_days = config.get("back_month_min_days_after", 21)
        back_exp = scanner.select_back_expiration(expirations, front_exp, min_days)
        if back_exp is None:
            return {"ok": False, "error": f"no monthly (or any) back-month expiration >={min_days} days after front"}

        # Wide window on both expirations: strikes sit at the expected-move
        # boundaries, not ATM, so need a wide enough net to find them --
        # unlike iron_fly's ATM-only front-chain lookup. Both chains are
        # fetched up front (not front-first-then-hope) because a calendar
        # spread requires the SAME strike in both expirations, and
        # different expirations can list different strike increments --
        # caught live during testing: picking the front strike independently
        # first produced a strike with no exact match at all in the
        # back-month chain. Strikes are now selected from the intersection
        # of what's actually listed in both chains, which guarantees an
        # exact match always exists once a candidate strike is chosen.
        front_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_greeks", "--include_quotes", "--strike_count", "40",
            "--around_price", str(price),
        ])
        if not front_chain.get("ok"):
            return {"ok": False, "error": front_chain.get("error", "get_option_chain failed")}
        front_entries = front_chain["chain"][str(front_exp)]

        back_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(back_exp),
            "--include_quotes", "--strike_count", "40", "--around_price", str(price),
        ])
        if not back_chain.get("ok"):
            return {"ok": False, "error": back_chain.get("error", "get_option_chain failed")}
        back_entries = back_chain["chain"][str(back_exp)]

        front_atm_call = scanner.atm_entry(front_entries, "call", price)
        front_atm_put = scanner.atm_entry(front_entries, "put", price)
        if front_atm_call is None or front_atm_put is None:
            return {"ok": False, "error": "incomplete ATM strikes for expected-move calc"}
        if front_atm_call.get("mid") is None or front_atm_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for front-month ATM strikes"}

        expected_move = 0.85 * (front_atm_call["mid"] + front_atm_put["mid"])
        call_target = price + expected_move
        put_target = price - expected_move

        back_call_strikes = {float(e["strike_price"]) for e in back_entries if str(e.get("option_type", "")).strip().lower().startswith("c")}
        back_put_strikes = {float(e["strike_price"]) for e in back_entries if str(e.get("option_type", "")).strip().lower().startswith("p")}
        front_calls_with_back_match = [e for e in front_entries if str(e.get("option_type", "")).strip().lower().startswith("c") and float(e["strike_price"]) in back_call_strikes]
        front_puts_with_back_match = [e for e in front_entries if str(e.get("option_type", "")).strip().lower().startswith("p") and float(e["strike_price"]) in back_put_strikes]

        front_call = scanner.nearest_strike_entry(front_calls_with_back_match, "call", call_target, -1.0)
        front_put = scanner.nearest_strike_entry(front_puts_with_back_match, "put", put_target, -1.0)
        if front_call is None or front_put is None:
            return {"ok": False, "error": "no front-month strikes near expected-move boundaries with a matching back-month strike"}
        if front_call.get("mid") is None or front_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for front-month calendar strikes"}

        call_strike = float(front_call["strike_price"])
        put_strike = float(front_put["strike_price"])

        back_call = scanner.nearest_strike_entry(back_entries, "call", call_strike, -1.0)
        back_put = scanner.nearest_strike_entry(back_entries, "put", put_strike, -1.0)
        if back_call is None or back_put is None or back_call.get("mid") is None or back_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for back-month calendar strikes"}

        # Net debit: pay more for the back-month legs than collected selling
        # the front-month legs (back-month options retain more time value).
        net_debit = (back_call["mid"] - front_call["mid"]) + (back_put["mid"] - front_put["mid"])

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(net_debit, 2),
            "price_effect": "Debit",
            "legs": [
                {"symbol": front_call["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": front_put["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": back_call["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
                {"symbol": back_put["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
            ],
        }
        return {
            "ok": True,
            "strategy": "double_calendar",
            "order": order,
            "symbol": symbol,
            "front_expiration": str(front_exp),
            "back_expiration": str(back_exp),
            "underlying_price": price,
            "call_strike": call_strike,
            "put_strike": put_strike,
            "expected_move": expected_move,
            "debit": round(net_debit, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def label_order_legs(order_result: dict) -> list[dict]:
    """Tag `fetch_double_calendar_order`'s 4 order legs with a `leg_role`
    ('front_call'/'front_put'/'back_call'/'back_put') for `save_trade`'s
    `legs` argument. Relies on `fetch_double_calendar_order`'s fixed leg
    order (front_call, front_put, back_call, back_put) rather than
    re-parsing OCC symbols, since that order is a stable contract of this
    module, not the broker's.
    """
    legs = order_result["order"]["legs"]
    roles = ["front_call", "front_put", "back_call", "back_put"]
    return [
        {"leg_role": role, "symbol": leg["symbol"], "action": leg["action"], "quantity": leg["quantity"]}
        for role, leg in zip(roles, legs)
    ]


def evaluate_position(
    position: dict, open_legs: list[dict], quotes: dict, config: dict,
    is_first_check_of_day: bool = False,
) -> dict:
    """Decide what (if anything) to do with an open double calendar position
    this tick. Pure calculation, no I/O -- callers fetch `open_legs` (via
    `db.py`/`db_paper.py get_open_legs`) and `quotes` (live bid/ask/delta per
    open leg's symbol, via `tt.py get_option_chain`) themselves.

    `quotes` is `{symbol: {"bid": F, "ask": F, "delta": F}}` for every symbol
    in `open_legs`. `position` has at least `entry_credit` (stored as a
    negative number -- see fetch_double_calendar_order's `debit` field) and
    `expiration` (the front-month expiration, stored at entry).

    `is_first_check_of_day` should be True when this is the loop's first
    Step 3b tick since the prior session close (the "wake at next market
    open" case in CLAUDE.md's wakeup table, not a mid-session poll). It does
    NOT change any threshold or action -- an earnings reaction gap happens
    overnight, so no polling frequency could have caught it sooner regardless
    of this flag. It only relabels a loss-driven stop's `reason` with an
    `_overnight_gap` suffix so `scan_log` can distinguish "this stop fired on
    the first possible tick after a gap" from "this stop fired mid-session,"
    which matters for judging whether the stop thresholds themselves are
    miscalibrated -- a gap-driven stop firing immediately is not evidence the
    polling cadence is too slow.

    Returns one of:
      {"action": "hold"}
      {"action": "close_all", "reason": "profit_target"|"stop_loss"|"stop_loss_overnight_gap"|"time_exit"}
      {"action": "close_side", "side": "call"|"put", "reason": "leg_stop"|"leg_stop_overnight_gap"}
    """
    gap_suffix = "_overnight_gap" if is_first_check_of_day else ""
    cfg = _strategy_config(config)
    debit = abs(position["entry_credit"])
    by_role = {leg["leg_role"]: leg for leg in open_legs}

    def _missing_quote(role: str) -> bool:
        leg = by_role.get(role)
        return leg is not None and leg["symbol"] not in quotes

    # Conservative same-side-of-spread pricing to close everything still open,
    # mirroring iron_fly's exit-pricing convention: buy back shorts at ask,
    # sell longs at bid.
    if not any(_missing_quote(r) for r in ("front_call", "front_put", "back_call", "back_put")):
        cost_to_close = 0.0
        for role, leg in by_role.items():
            q = quotes[leg["symbol"]]
            if role.startswith("front"):
                cost_to_close += q["ask"]
            else:
                cost_to_close -= q["bid"]
        net_credit_on_close = -cost_to_close

        if net_credit_on_close >= debit * (1 + cfg.get("profit_target_pct", 0.25)):
            return {"action": "close_all", "reason": "profit_target"}
        if -net_credit_on_close >= debit * cfg.get("stop_loss_pct_of_debit", 1.0):
            return {"action": "close_all", "reason": f"stop_loss{gap_suffix}"}

    front_expiration = datetime.strptime(position["expiration"], "%Y-%m-%d").date()
    days_to_front_expiration = (front_expiration - date.today()).days
    if days_to_front_expiration <= cfg.get("exit_days_before_front_expiration", 5):
        return {"action": "close_all", "reason": "time_exit"}

    leg_stop_delta_abs = cfg.get("leg_stop_delta_abs", 0.45)
    for side, role in (("call", "front_call"), ("put", "front_put")):
        leg = by_role.get(role)
        if leg is None:
            continue
        q = quotes.get(leg["symbol"])
        if q is None or q.get("delta") is None:
            continue
        if abs(q["delta"]) >= leg_stop_delta_abs:
            return {"action": "close_side", "side": side, "reason": f"leg_stop{gap_suffix}"}

    return {"action": "hold"}


def _add_dispersion(symbol: str, config: dict, lookback: int, criteria: dict) -> None:
    """extra_criteria_fn hook for scanner.run_candidate_scan -- the one
    genuine per-strategy addition inside the otherwise shared candidate loop.
    """
    dispersion = realized_move_dispersion(symbol, config, lookback)
    if dispersion.get("ok"):
        criteria["realized_move_dispersion_pct"] = dispersion["realized_move_dispersion_pct"]


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date, mirroring iron_fly.py's cmd_get_candidates
    but with this strategy's own screening: stricter volume floor, a
    relative expected-move-percentage floor instead of a dollar one, and
    (when available) a realized-move-dispersion check, since a single
    tail-risk surprise hurts this debit strategy more than the iron fly's
    width-defined max loss. Thin wrapper around scanner.run_candidate_scan --
    shared with every other strategy's cmd_get_candidates.
    """
    config = scanner._load_config()
    strategy_config = _strategy_config(config)
    return scanner.run_candidate_scan(args.date, config, fetch_price_and_expected_move, apply_tiering, strategy_config, extra_criteria_fn=_add_dispersion)


def main() -> None:
    scanner.run_strategy_main(cmd_get_candidates, fetch_double_calendar_order)


if __name__ == "__main__":
    main()
