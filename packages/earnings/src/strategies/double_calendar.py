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

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

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
    screening. Mirrors iron_fly.py's fetch_price_and_term_structure but
    reuses scanner.nearest_expiration_at_least_days_after for back-month
    selection (a genuine monthly cycle >=21 days out, per documented
    double-calendar convention) rather than iron_fly's own "nearest to
    +30 days" comparison-only back-month.

    Returns {"ok": False, "error": ...} on any missing data rather than
    raising -- same discipline as iron_fly.py.
    """
    try:
        quote = scanner.call_tt(["get_quote", "--symbol", symbol])
        if not quote.get("ok"):
            return {"ok": False, "error": quote.get("error", "get_quote failed")}
        price = quote.get("price")
        if price is None:
            return {"ok": False, "error": "get_quote returned no price"}

        chain_all = scanner.call_tt(["get_option_chain", "--symbol", symbol])
        if not chain_all.get("ok"):
            return {"ok": False, "error": chain_all.get("error", "get_option_chain failed")}
        expirations = sorted(date.fromisoformat(e) for e in chain_all["chain"].keys())
        if not expirations:
            return {"ok": False, "error": "no expirations in chain"}

        reaction_date = earnings_date + timedelta(days=1) if earnings_timing == "After market close" else earnings_date
        eligible = [e for e in expirations if e >= reaction_date]
        if not eligible:
            return {"ok": False, "error": f"no expiration on/after reaction date {reaction_date}"}
        front_exp = min(eligible)

        min_days = config.get("back_month_min_days_after", 21)
        back_exp = scanner.nearest_expiration_at_least_days_after(expirations, front_exp, min_days, monthly_only=True)
        if back_exp is None:
            back_exp = scanner.nearest_expiration_at_least_days_after(expirations, front_exp, min_days, monthly_only=False)
        if back_exp is None:
            return {"ok": False, "error": f"no monthly (or any) back-month expiration >={min_days} days after front"}

        front_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_greeks", "--include_quotes", "--strike_count", "3",
            "--around_price", str(price),
        ])
        back_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(back_exp),
            "--include_greeks", "--strike_count", "3", "--around_price", str(price),
        ])
        if not front_chain.get("ok") or not back_chain.get("ok"):
            return {"ok": False, "error": "front/back chain fetch failed"}

        front_entries = front_chain["chain"][str(front_exp)]
        back_entries = back_chain["chain"][str(back_exp)]
        front_call = scanner.atm_entry(front_entries, "call", price)
        front_put = scanner.atm_entry(front_entries, "put", price)
        back_call = scanner.atm_entry(back_entries, "call", price)
        if front_call is None or front_put is None or back_call is None:
            return {"ok": False, "error": "incomplete ATM strikes in front/back chain"}
        if front_call.get("mid") is None or front_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for front-month ATM strikes"}
        if front_call.get("iv") is None or back_call.get("iv") is None:
            return {"ok": False, "error": "no greeks/iv data for front/back ATM strikes"}

        term_structure = (back_call["iv"] - front_call["iv"]) / back_call["iv"]
        # Standard 0.85x straddle-to-expected-move correction, same
        # documented convention used in iron_fly.py's compute_term_structure.
        expected_move = 0.85 * (front_call["mid"] + front_put["mid"])

        front_chain_oi = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_oi", "--strike_count", "999",
        ])
        combined_oi = None
        if front_chain_oi.get("ok"):
            oi_entries = front_chain_oi["chain"].get(str(front_exp), [])
            ois = [e["open_interest"] for e in oi_entries if e.get("open_interest") is not None]
            if ois:
                combined_oi = sum(ois)

        return {
            "ok": True,
            "criteria": {
                "price": price,
                "term_structure": term_structure,
                "expected_move_dollars": expected_move,
                "expected_move_pct": expected_move / price,
                "combined_open_interest": combined_oi,
                "chain_complete": True,
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

    if criteria.get("combined_open_interest") is None:
        hard_fail.append("combined_open_interest_unverified")
    elif criteria["combined_open_interest"] < config["min_combined_open_interest"]:
        hard_fail.append("combined_open_interest_below_minimum")

    if not criteria.get("chain_complete"):
        hard_fail.append("chain_complete_unverified")

    if criteria.get("realized_move_dispersion_pct") is not None and config.get("max_realized_move_dispersion_pct") is not None:
        if criteria["realized_move_dispersion_pct"] > config["max_realized_move_dispersion_pct"]:
            hard_fail.append("realized_move_too_inconsistent")

    near_miss: list[str] = []

    def _band(value, min_pass, min_near_miss, name):
        if value is None:
            near_miss.append(f"{name}_unknown")
            return
        if value >= min_pass:
            return
        if value >= min_near_miss:
            near_miss.append(name)
            return
        hard_fail.append(f"{name}_below_near_miss")

    _band(criteria.get("avg_volume"), config["min_avg_volume"], config["near_miss_min_avg_volume"], "avg_volume")
    _band(criteria.get("iv_rv_ratio"), config["min_iv_rv_ratio"], config["near_miss_min_iv_rv_ratio"], "iv_rv_ratio")
    _band(criteria.get("winrate"), config["min_winrate"], config["near_miss_min_winrate"], "winrate")

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
        quote = scanner.call_tt(["get_quote", "--symbol", symbol])
        if not quote.get("ok"):
            return {"ok": False, "error": quote.get("error", "get_quote failed")}
        price = quote.get("price")
        if price is None:
            return {"ok": False, "error": "get_quote returned no price"}

        chain_all = scanner.call_tt(["get_option_chain", "--symbol", symbol])
        if not chain_all.get("ok"):
            return {"ok": False, "error": chain_all.get("error", "get_option_chain failed")}
        expirations = sorted(date.fromisoformat(e) for e in chain_all["chain"].keys())
        if not expirations:
            return {"ok": False, "error": "no expirations in chain"}

        reaction_date = earnings_date + timedelta(days=1) if earnings_timing == "After market close" else earnings_date
        eligible = [e for e in expirations if e >= reaction_date]
        if not eligible:
            return {"ok": False, "error": f"no expiration on/after reaction date {reaction_date}"}
        front_exp = min(eligible)

        min_days = config.get("back_month_min_days_after", 21)
        back_exp = scanner.nearest_expiration_at_least_days_after(expirations, front_exp, min_days, monthly_only=True)
        if back_exp is None:
            back_exp = scanner.nearest_expiration_at_least_days_after(expirations, front_exp, min_days, monthly_only=False)
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


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date, mirroring iron_fly.py's cmd_get_candidates
    but with this strategy's own screening: stricter volume floor, a
    relative expected-move-percentage floor instead of a dollar one, and
    (when available) a realized-move-dispersion check, since a single
    tail-risk surprise hurts this debit strategy more than the iron fly's
    width-defined max loss.
    """
    config = scanner._load_config()
    strategy_config = _strategy_config(config)
    iso_date = datetime.strptime(args.date, "%m/%d/%Y").strftime("%Y-%m-%d")
    calendar = scanner.fetch_dolthub_calendar(iso_date, config)
    lookback = config.get("winrate_lookback_quarters", 8)

    candidates = []
    for entry in calendar:
        symbol = entry["symbol"]
        criteria: dict = {}

        broker = fetch_price_and_expected_move(symbol, entry["date"], entry["timing"], config)
        if broker.get("ok"):
            criteria.update(broker["criteria"])
        broker_error = None if broker.get("ok") else broker.get("error")

        criteria["avg_volume"] = scanner.fetch_avg_volume(symbol, config)

        ivrv = scanner.fetch_iv_rv_ratio(symbol, config)
        criteria["iv_rv_ratio"] = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None

        winrate = scanner.compute_winrate(symbol, config, lookback)
        criteria["winrate"] = winrate["winrate"]
        winrate_sample_size = winrate["sample_size"]

        dispersion = realized_move_dispersion(symbol, config, lookback)
        if dispersion.get("ok"):
            criteria["realized_move_dispersion_pct"] = dispersion["realized_move_dispersion_pct"]

        tiering = apply_tiering(criteria, strategy_config)

        candidates.append({
            "symbol": symbol,
            "earnings_timing": entry["timing"],
            "tier": tiering["tier"],
            "hard_fail_reasons": tiering["hard_fail_reasons"],
            "near_miss_reasons": tiering["near_miss_reasons"],
            "criteria": criteria,
            "winrate_sample_size": winrate_sample_size,
            "broker_data_error": broker_error,
        })

    candidates.sort(key=lambda c: {"Tier 1": 0, "Tier 2": 1, "Near Miss": 2, "Reject": 3}[c["tier"]])

    ranked = scanner.rank_candidates(candidates, config)
    selection = scanner.select_positions(ranked, config)

    return {
        "ok": True,
        "date": args.date,
        "candidates": candidates,
        "ranked": ranked,
        "selected": selection["selected"],
        "skipped_for_selection": selection["skipped"],
    }


def cmd_get_order(args) -> dict:
    config = scanner._load_config()
    return fetch_double_calendar_order(
        args.symbol.strip().upper(),
        date.fromisoformat(args.earnings_date),
        args.earnings_timing,
        config,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_cand = sub.add_parser("get_candidates")
    p_cand.add_argument("--date", required=True)

    p_order = sub.add_parser("get_order")
    p_order.add_argument("--symbol", required=True)
    p_order.add_argument("--earnings_date", required=True)
    p_order.add_argument("--earnings_timing", required=True)

    args = parser.parse_args()
    dispatch = {
        "get_candidates": cmd_get_candidates,
        "get_order": cmd_get_order,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
