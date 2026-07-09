"""Forced-sampling paper-trading harness for thoroughly testing every
strategy (see docs/strategy-testing-plan.md). `rank_strategies.py` opens
only the single best strategy per symbol per night -- fine for the live
loop, but candidates are scarce enough that most strategies would starve
under natural selection and never reach a statistically meaningful sample
in weeks. This module instead opens a **separate paper book**
(`profile='strat_test'` in the shared data/paper_trades.db) with a trade
for *every* strategy that tiers Tier 1/2 on *every* viable symbol each
night -- up to one per (symbol, strategy) pair.

This is entirely separate from the live/paper trading loop (CLAUDE.md's
Loop Steps, rank_strategies.py's own get_ranked_symbols) -- it never
selects a single "best" strategy, never respects max_concurrent_earnings_
positions or the correlation block list (the test book intentionally holds
many overlapping positions at once), and never calls tt.py execute_trade.
Always paper-only, regardless of config's enable_live_trading.

Sizing basis is fixed to one profile (see config.json's "profiles" and
docs/paper-trading-profiles.md) via --profile (default "balanced") so
per-strategy comparison isn't confounded by which profile's capital/gates
were active on a given night -- risk-profile comparison is a separate,
later program. Fills are cost-adjusted via costs.py's tastytrade fee
model, not mid-price.

Position sizing/P&L convention: `entry_credit`/`exit_debit`/`pnl` in
`trades` are stored **already multiplied by quantity** (not per-contract),
and each leg inside `legs_json` carries its real contract quantity (not
the get_order template's quantity=1) -- so `scanner.compute_generic_exit_
debit` and the existing `pnl = (entry_credit - exit_debit) * 100` formula
both work unchanged, without a second quantity multiplication anywhere.
`entry_cost`/`exit_cost` (from costs.py) are stored separately and kept
OUT of `pnl` itself -- `trades.pnl` stays gross, exactly like every other
caller of save_trade/save_close, so cost-adjusted expectancy is computed
downstream in strategy_metrics.py rather than baked into a column every
other reader of this table has always assumed is gross.

IV crush: `entry_iv`/`exit_iv` are the average live IV (from tastytrade's
option-chain greeks, already fetched alongside bid/ask for cost/exit-debit
purposes -- no extra network round trip) across this order's Sell-to-Open
legs specifically -- the side that's actually sold and later crushes, a
strategy-agnostic proxy that needs no per-strategy special-casing (see
_avg_sold_iv). `iv_crush = entry_iv - exit_iv` is computed downstream in
strategy_metrics.py, same pattern as cost-adjusted expectancy.

Commands:
  run_entries --date MM/DD/YYYY [--profile balanced]
  run_closes [--profile balanced]
"""

import argparse
import json
import os
import sys
import time
from datetime import date as _date

sys.path.insert(0, os.path.dirname(__file__))

import costs
import db_paper
import rank_strategies
import scanner
import sizing

from strategies import (
    atm_calendar,
    broken_wing_butterfly,
    directional_credit_spread,
    double_calendar,
    iron_condor,
    iron_fly,
    reverse_fly,
)

TEST_PROFILE = "strat_test"

_ORDER_FNS = {
    "iron_fly": iron_fly.fetch_iron_fly_order,
    "double_calendar": double_calendar.fetch_double_calendar_order,
    "iron_condor": iron_condor.fetch_iron_condor_order,
    "atm_calendar": atm_calendar.fetch_atm_calendar_order,
    "directional_credit_spread": directional_credit_spread.fetch_directional_credit_spread_order,
    "broken_wing_butterfly": broken_wing_butterfly.fetch_broken_wing_butterfly_order,
    "reverse_fly": reverse_fly.fetch_reverse_fly_order,
}


def _occ_expiration(symbol: str) -> str:
    """Parse YYYY-MM-DD out of a standard OCC option symbol. The date+C/P+
    strike suffix is a fixed 15 characters read from the right, so the
    root symbol's own length/padding (up to 6 chars, space-padded) doesn't
    matter -- avoids needing a second stored column for a calendar
    spread's back-month expiration; each leg's own symbol already encodes
    which expiration it belongs to.
    """
    suffix = symbol[-15:]
    yy, mm, dd = suffix[0:2], suffix[2:4], suffix[4:6]
    return f"20{yy}-{mm}-{dd}"


def _leg_quotes_for_symbols(underlying: str, leg_symbols: list[str], price: float) -> dict | None:
    """Live {symbol: {"bid","ask","iv"}} for every symbol in `leg_symbols`,
    fetched per distinct expiration (a calendar spread's legs span two) and
    merged. Returns None if any leg's quote is missing bid or ask (IV is
    optional -- greeks can be temporarily unavailable without blocking the
    trade itself, so a missing IV degrades only the IV-crush analysis, not
    the fill). `scanner.fetch_quotes_by_symbol` already requests
    --include_greeks, so IV is already in the response; this just surfaces
    it instead of discarding it."""
    expirations = {_occ_expiration(s) for s in leg_symbols}
    quotes: dict = {}
    for exp in expirations:
        quotes.update(scanner.fetch_quotes_by_symbol(underlying, exp, leg_symbols, price))

    result = {}
    for s in leg_symbols:
        q = quotes.get(s)
        if q is None or q.get("bid") is None or q.get("ask") is None:
            return None
        result[s] = {"bid": q["bid"], "ask": q["ask"], "iv": q.get("iv")}
    return result


def _avg_sold_iv(legs: list[dict], quotes: dict) -> float | None:
    """Average IV across an order's Sell-to-Open (short) legs -- the side
    that's actually sold and later crushes post-earnings. A strategy-
    agnostic proxy for "the IV that mattered": works unchanged for
    iron_fly's two short legs, a calendar's front-month short leg, a naked
    single short leg, etc., without per-strategy special-casing. Returns
    None if no short leg has an available IV (e.g. greeks momentarily
    missing), not zero -- a missing measurement, not a measured zero."""
    ivs = [
        quotes[leg["symbol"]]["iv"]
        for leg in legs
        if leg.get("action") == "Sell to Open" and quotes.get(leg["symbol"], {}).get("iv") is not None
    ]
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def _per_contract_credit(order: dict) -> float:
    """Per-contract entry credit (positive) or debit (returned negative, so
    the stored sign convention -- positive costs money to close, negative
    nets a credit -- stays consistent for every strategy). Field names vary
    per strategy's get_order result: iron_fly/iron_condor/directional use
    "credit", atm_calendar/double_calendar use "debit", and
    broken_wing_butterfly/reverse_fly use "net_debit". "total_credit" is kept
    in the lookup as a general fallback for any future credit strategy that
    aggregates multiple credit legs."""
    for key in ("credit", "total_credit"):
        if key in order:
            return order[key]
    for key in ("debit", "net_debit"):
        if key in order:
            return -order[key]
    raise KeyError(f"no credit/debit field found on order for strategy {order.get('strategy')!r}")


def _entry_context(criteria: dict, composite_score) -> dict:
    return {
        "iv_rv_ratio": criteria.get("iv_rv_ratio"),
        "dispersion": criteria.get("realized_move_dispersion_pct"),
        "skew_abs": criteria.get("skew_abs"),
        "winrate": criteria.get("winrate"),
        "composite_score": composite_score,
    }


def cmd_run_entries(args) -> dict:
    if not rank_strategies._ensure_dolt_running():
        return {"ok": False, "error": "dolt sql-server not available"}
    if not rank_strategies._verify_tastytrade_connection():
        return {"ok": False, "error": "tastytrade connection failed"}

    profile = args.profile
    config = scanner._load_config(profile)
    tier_floor = config.get("tier_floor", "Tier 2")
    allowed_tiers = ("Tier 1",) if tier_floor == "Tier 1" else ("Tier 1", "Tier 2")

    calendar = scanner.fetch_entry_window_calendar(config)
    scan_date = str(_date.today())

    opened: list[dict] = []
    skipped: list[dict] = []

    for entry in calendar:
        symbol, earnings_date, timing = entry["symbol"], entry["date"], entry["timing"]
        try:
            results = rank_strategies.evaluate_symbol(symbol, earnings_date, timing, config)
        except Exception as exc:
            skipped.append({"symbol": symbol, "strategy": None, "reason": f"evaluate_symbol_error: {exc}"})
            continue

        for r in results:
            strategy_name = r["name"]
            reasons = r["hard_fail_reasons"] or r["near_miss_reasons"]
            db_paper.cmd_log_scan(argparse.Namespace(data=json.dumps({
                "scan_date": scan_date,
                "strategy": strategy_name,
                "symbol": symbol,
                "tier": r["tier"],
                "outcome": r["tier"],
                "reason": "; ".join(reasons) if reasons else None,
                "logged_at": time.time(),
                "profile": TEST_PROFILE,
            })))

            if r["tier"] not in allowed_tiers:
                skipped.append({"symbol": symbol, "strategy": strategy_name, "reason": f"tier_excluded_{r['tier']}"})
                continue

            try:
                order = _ORDER_FNS[strategy_name](symbol, earnings_date, timing, config)
                if not order.get("ok"):
                    skipped.append({"symbol": symbol, "strategy": strategy_name, "reason": f"order_build_failed: {order.get('error')}"})
                    continue

                strategy_config = config["strategies"][strategy_name]
                size = sizing.compute_position_size(order, strategy_config, config)
                if not size["ok"]:
                    skipped.append({"symbol": symbol, "strategy": strategy_name, "reason": size["reason"]})
                    continue
                quantity = size["quantity"]

                template_legs = order["order"]["legs"]
                leg_symbols = [leg["symbol"] for leg in template_legs]
                price = order.get("underlying_price", 0.0)
                leg_quotes = _leg_quotes_for_symbols(symbol, leg_symbols, price)
                if leg_quotes is None:
                    skipped.append({"symbol": symbol, "strategy": strategy_name, "reason": "leg_quotes_unavailable"})
                    continue

                entry_costs = costs.apply_entry_costs(
                    order, [leg_quotes[s] for s in leg_symbols], quantity, config,
                )
                entry_iv = _avg_sold_iv(template_legs, leg_quotes)

                scaled_legs = [{**leg, "quantity": quantity} for leg in template_legs]
                per_contract = _per_contract_credit(order)
                entry_credit = per_contract * quantity

                order_id = f"{TEST_PROFILE}-{strategy_name}-{symbol}-{scan_date}-{int(time.time() * 1000)}"
                save_result = db_paper.cmd_save_trade(argparse.Namespace(data=json.dumps({
                    "order_id": order_id,
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "expiration": order.get("expiration") or order.get("front_expiration"),
                    "legs_json": json.dumps(scaled_legs),
                    "entry_credit": entry_credit,
                    "profile": TEST_PROFILE,
                    "quantity": quantity,
                    "capital_at_risk": size["capital_at_risk"],
                    "entry_cost": entry_costs["total_cost"],
                    "entry_iv": entry_iv,
                    "entry_context": _entry_context(r["criteria"], r["composite_score"]),
                })))
                if not save_result.get("ok"):
                    skipped.append({"symbol": symbol, "strategy": strategy_name, "reason": f"save_trade_failed: {save_result.get('error')}"})
                    continue

                opened.append({
                    "order_id": order_id, "symbol": symbol, "strategy": strategy_name,
                    "quantity": quantity, "capital_at_risk": size["capital_at_risk"],
                    "entry_cost": entry_costs["total_cost"],
                })
            except Exception as exc:
                # One candidate's unexpected failure (e.g. an order-building edge case)
                # must not lose every other candidate's already-accumulated results for
                # the night -- log and move on, same discipline as the evaluate_symbol
                # try/except above.
                skipped.append({"symbol": symbol, "strategy": strategy_name, "reason": f"unexpected_error: {exc}"})

    return {"ok": True, "date": scan_date, "profile": profile, "opened": opened, "skipped": skipped}


def cmd_run_closes(args) -> dict:
    if not rank_strategies._verify_tastytrade_connection():
        return {"ok": False, "error": "tastytrade connection failed"}

    config = scanner._load_config(args.profile)
    positions = db_paper.cmd_get_open_positions(argparse.Namespace())["positions"]
    positions = [p for p in positions if p.get("profile") == TEST_PROFILE]

    closed: list[dict] = []
    skipped: list[dict] = []

    for trade in positions:
        order_id = trade["order_id"]
        symbol = trade["symbol"]
        try:
            quantity = trade["quantity"] or 1
            legs = json.loads(trade["legs_json"])
            leg_symbols = [leg["symbol"] for leg in legs]

            quote = scanner.fetch_quote_and_expirations(symbol)
            price = quote.get("price", 0.0) if quote.get("ok") else 0.0

            leg_quotes = _leg_quotes_for_symbols(symbol, leg_symbols, price)
            if leg_quotes is None:
                skipped.append({"order_id": order_id, "reason": "leg_quotes_unavailable"})
                continue

            full_quotes = {s: leg_quotes[s] for s in leg_symbols}
            exit_debit = scanner.compute_generic_exit_debit(legs, full_quotes)
            if exit_debit is None:
                skipped.append({"order_id": order_id, "reason": "exit_debit_unavailable"})
                continue

            exit_costs = costs.apply_exit_costs(
                {"order": {"legs": legs}}, [leg_quotes[s] for s in leg_symbols], quantity, config,
            )
            # Same legs list (action labels preserved from entry) -> this is the
            # same specific short contract(s)' IV, now, for a clean entry-vs-exit
            # crush comparison -- not a different strike/expiration's IV.
            exit_iv = _avg_sold_iv(legs, full_quotes)

            pnl = (trade["entry_credit"] - exit_debit) * 100

            close_result = db_paper.cmd_save_close(argparse.Namespace(data=json.dumps({
                "order_id": order_id,
                "exit_debit": exit_debit,
                "pnl": pnl,
                "exit_cost": exit_costs["total_cost"],
                "exit_iv": exit_iv,
            })))
            if not close_result.get("ok"):
                skipped.append({"order_id": order_id, "reason": f"save_close_failed: {close_result.get('error')}"})
                continue

            closed.append({"order_id": order_id, "symbol": symbol, "pnl": round(pnl, 2), "exit_cost": exit_costs["total_cost"]})
        except Exception as exc:
            # Same discipline as cmd_run_entries: one position's unexpected failure
            # must not lose every other open position's already-accumulated closes.
            skipped.append({"order_id": order_id, "reason": f"unexpected_error: {exc}"})

    return {"ok": True, "closed": closed, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_entries = sub.add_parser("run_entries")
    p_entries.add_argument("--date", required=True)
    p_entries.add_argument("--profile", default="balanced")

    p_closes = sub.add_parser("run_closes")
    p_closes.add_argument("--profile", default="balanced")

    args = parser.parse_args()
    dispatch = {
        "run_entries": cmd_run_entries,
        "run_closes": cmd_run_closes,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
