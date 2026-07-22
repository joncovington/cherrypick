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
import threading
import time
from datetime import date as _date
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import costs
import db_paper
import paths
import rank_strategies
import scanner
import sizing
import strategy_metrics as metrics
from strategies import (
    atm_calendar,
    broken_wing_butterfly,
    directional_credit_spread,
    double_calendar,
    iron_condor,
    iron_fly,
)

TEST_PROFILE = "strat_test"

_ORDER_FNS = {
    "iron_fly": iron_fly.fetch_iron_fly_order,
    "double_calendar": double_calendar.fetch_double_calendar_order,
    "iron_condor": iron_condor.fetch_iron_condor_order,
    "atm_calendar": atm_calendar.fetch_atm_calendar_order,
    "directional_credit_spread": directional_credit_spread.fetch_directional_credit_spread_order,
    "broken_wing_butterfly": broken_wing_butterfly.fetch_broken_wing_butterfly_order,
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
    broken_wing_butterfly uses "net_debit". "total_credit" is kept
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


# --- Per-symbol entry review (the data reviewed for a symbol + the chosen/rejected decision) ---------
_TIER_RANK = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3, "Near-Miss": 4, "Reject": 5}


def _best_tier(results: list[dict]) -> str | None:
    tiers = [r.get("tier") for r in results if r.get("tier")]
    return min(tiers, key=lambda t: _TIER_RANK.get(t, 9)) if tiers else None


def _richest_criteria(results: list[dict]) -> dict:
    """The fullest criteria dict across a symbol's per-strategy results (they share the symbol-level
    fields; some strategies add extras, so take the largest)."""
    best: dict = {}
    for r in results:
        c = r.get("criteria") or {}
        if len(c) > len(best):
            best = c
    return best


def _summarize_skips(reasons: list[str]) -> str:
    """A compact rejection reason from the per-strategy skip reasons for one symbol."""
    heads = [rr.split(":")[0].strip() for rr in reasons if rr]
    if not heads:
        return "no qualifying strategy"
    counts: dict[str, int] = {}
    for h in heads:
        counts[h] = counts.get(h, 0) + 1
    top = max(counts.items(), key=lambda kv: kv[1])[0]
    n = len(reasons)
    return f"{top} ({n} strateg{'y' if n == 1 else 'ies'} evaluated)"


def _save_entry_review(scan_date, symbol, timing, results, opened_strategies, skip_reasons) -> None:
    """Persist one per-symbol review — the reviewed data + the chosen/rejected decision — for the
    orchestrator's per-symbol notification and the EOD analysis. Best-effort; never breaks the scan."""
    crit = _richest_criteria(results)
    selected = bool(opened_strategies)
    reason = ("opened " + ", ".join(sorted(set(opened_strategies)))) if selected else _summarize_skips(skip_reasons)
    try:
        db_paper.cmd_save_entry_review(argparse.Namespace(data=json.dumps({
            "scan_date": scan_date, "symbol": symbol, "timing": timing,
            "price": crit.get("price"), "volume": crit.get("avg_volume"),
            "winrate": crit.get("winrate"), "winrate_sample": crit.get("winrate_sample_size"),
            "iv_rv_ratio": crit.get("iv_rv_ratio"), "term_structure": crit.get("term_structure"),
            "market_cap": crit.get("market_cap"),
            "expected_move": crit.get("expected_move_dollars") or crit.get("expected_move"),
            "best_tier": _best_tier(results), "selected": selected, "reason": reason,
            "criteria": crit, "profile": TEST_PROFILE,
        })))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Deterministic end-of-day paper report
# ---------------------------------------------------------------------------

def _logs_dir() -> Path:
    """The earnings logs home (~/.cherrypick/logs/earnings by default; see paths.logs_dir). Created on
    demand since paths.logs_dir returns a pure path."""
    d = paths.logs_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _eod_report_path(day: str) -> Path:
    return _logs_dir() / f"paper-eod-{day}.md"


def _money(x) -> str:
    if x is None:
        return "-"
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _close_session(trade: dict) -> str:
    """Trading-session date (ISO) an earnings trade belongs to = its close date. Earnings
    positions open one afternoon and close the next morning, so closed_at (not opened_at) is
    the settlement session -- the same rule the orchestrator's report.py applies, so this
    module's daily file and the suite roll-up never disagree about a trade's day."""
    try:
        return _date.fromtimestamp(float(trade["closed_at"])).isoformat()
    except (TypeError, ValueError, OSError, OverflowError, KeyError):
        return ""


def _open_session(trade: dict) -> str:
    """Trading-session date (ISO) an earnings trade was *entered* on = its opened_at date. The
    counterpart to _close_session: a position opened this afternoon is carried overnight and does
    not settle until the next morning, so its open session and close session are different days.
    The EOD report shows both — what settled this morning (closed) and what was put on this
    afternoon (opened) — because the close pass and the entry pass run six hours apart and each
    only ever sees one of the two."""
    try:
        return _date.fromtimestamp(float(trade["opened_at"])).isoformat()
    except (TypeError, ValueError, OSError, OverflowError, KeyError):
        return ""


def _group_stats(trades: list[dict]) -> dict:
    """Win/loss/net/expectancy/profit-factor over a trade list, all net of costs
    (metrics.net_pnl subtracts entry+exit cost) -- the same numbers strategy_report.py reports."""
    n = len(trades)
    net = sum(metrics.net_pnl(t) for t in trades)
    wins = sum(1 for t in trades if metrics.net_pnl(t) > 0)
    return {
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": metrics.win_rate(trades),
        "net_pnl": net,
        "expectancy": metrics.expectancy(trades),
        "profit_factor": metrics.profit_factor(trades),
    }


def _group_by(trades: list[dict], key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for t in trades:
        out.setdefault(t.get(key) or "?", []).append(t)
    return out


def _write_eod_report(day: str) -> Path:
    """Write a deterministic end-of-day paper report for `day` to logs/paper-eod-<day>.md and
    return the path. Code-generated (no agent) so the scheduled close pass can write it
    unattended, mirroring the MEIC paper loop's settlement-time report. Scoped to trades whose
    close session (see _close_session) is `day`. Reads the shared paper_trades.db through
    strategy_metrics, so it can never disagree with strategy_report.py on the same data."""
    trades = [t for t in metrics.load_closed_trades() if _close_session(t) == day]
    opened = [t for t in metrics.load_open_trades() if _open_session(t) == day]

    overall = _group_stats(trades)
    by_symbol: dict[str, float] = {}
    for t in trades:
        by_symbol[t["symbol"]] = by_symbol.get(t["symbol"], 0.0) + metrics.net_pnl(t)

    wr = f"{overall['win_rate'] * 100:.0f}%" if overall["win_rate"] is not None else "-"

    L = [f"# Earnings Paper Trading - EOD Report {day}", ""]
    L.append("_Deterministic forced-sampling paper book (strat_test). Defined-risk strategies only; "
             "each position opens one afternoon and closes the next morning. Two sections, because the "
             "two happen on different days: **Closed this session** is what settled this morning (realized "
             "P&L, net of entry+exit costs); **Opened this session** is what was entered this afternoon and "
             "is carried overnight (no P&L yet — capital at risk is the known max loss)._")
    L.append("")
    L.append("## Closed this session (realized P&L)")
    L.append("")
    L.append("## Account-wide (all profiles)")
    L.append(f"- Trades closed: **{overall['trades']}**")
    L.append(f"- Net P&L (net of costs): **{_money(round(overall['net_pnl'], 2))}**")
    L.append(f"- Wins / Losses: {overall['wins']} / {overall['losses']} (win rate {wr})")
    if by_symbol:
        L.append("- By symbol: " + ", ".join(
            f"{s} {_money(round(v, 2))}" for s, v in sorted(by_symbol.items())))
    L.append("")

    def _table(heading: str, col_label: str, groups: dict[str, list[dict]]) -> None:
        L.append(f"## {heading}")
        L.append(f"| {col_label} | Trades | Wins | Losses | Win % | Net P&L | Expectancy | Profit Factor |")
        L.append("|---|---|---|---|---|---|---|---|")
        if not groups:
            L.append("| _(none)_ | 0 | - | - | - | $0.00 | - | - |")
        for name, grp in sorted(groups.items()):
            s = _group_stats(grp)
            gwr = f"{s['win_rate'] * 100:.0f}%" if s["win_rate"] is not None else "-"
            pf = "inf" if s["profit_factor"] == float("inf") else (
                f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "-")
            exp = _money(round(s["expectancy"], 2)) if s["expectancy"] is not None else "-"
            L.append(f"| {name} | {s['trades']} | {s['wins']} | {s['losses']} | {gwr} | "
                     f"{_money(round(s['net_pnl'], 2))} | {exp} | {pf} |")
        L.append("")

    _table("Per profile", "Profile", _group_by(trades, "profile"))
    _table("Per strategy", "Strategy", _group_by(trades, "strategy"))

    if not trades:
        L.append("_No trades closed this session - flat day._")
        L.append("")

    # Opened this session ------------------------------------------------------
    # The entry pass runs ~6 hours after the close pass that first wrote this file, so this section
    # is empty in the morning and fills in when the afternoon entry pass regenerates the report.
    # These are the positions actually carrying overnight risk tonight -- distinct from the closed
    # block above, which is this morning's settlements.
    L.append("## Opened this session (carried overnight)")
    if opened:
        open_risk = sum(t.get("capital_at_risk") or 0.0 for t in opened)
        open_cost = sum(t.get("entry_cost") or 0.0 for t in opened)
        by_sym_open: dict[str, int] = {}
        for t in opened:
            by_sym_open[t["symbol"]] = by_sym_open.get(t["symbol"], 0) + 1
        L.append(f"- Positions opened: **{len(opened)}** across {len(by_sym_open)} name(s) "
                 f"({', '.join(f'{s} x{n}' for s, n in sorted(by_sym_open.items()))}).")
        L.append(f"- Capital at risk overnight (defined max loss, summed): **{_money(round(open_risk, 2))}**.")
        L.append(f"- Entry costs paid: {_money(round(open_cost, 2))}.")
        L.append("")
        L.append("| Symbol | Strategy | Qty | Expiry | Entry credit | Capital at risk | Entry cost |")
        L.append("|---|---|---|---|---|---|---|")
        for t in sorted(opened, key=lambda x: (x["symbol"], x.get("strategy") or "")):
            L.append(f"| {t['symbol']} | {t.get('strategy', '-')} | {t.get('quantity', '-')} | "
                     f"{t.get('expiration', '-')} | {_money(t.get('entry_credit'))} | "
                     f"{_money(t.get('capital_at_risk'))} | {_money(t.get('entry_cost'))} |")
    else:
        L.append("_Nothing opened this session - no overnight risk carried (or the afternoon entry "
                 "pass has not run yet; this section fills in after it does)._")
    L.append("")

    L.append(f"_Generated {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')} "
             "· paper DB only; live account untouched._")

    path = _eod_report_path(day)
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def cmd_eod_report(args) -> dict:
    day = args.date or _date.today().isoformat()
    path = _write_eod_report(day)
    analysis = _write_eod_analysis(day)
    return {"ok": True, "date": day, "report": str(path), "analysis": str(analysis)}


# ---------------------------------------------------------------------------
# EOD analysis report -- conversational, 7-section, still fully deterministic
# ---------------------------------------------------------------------------

def _analysis_path(day: str) -> Path:
    return _logs_dir() / f"eod-analysis-{day}.md"


def _signed(x) -> str:
    return f"+{x:.2f}" if x is not None and x >= 0 else (f"{x:.2f}" if x is not None else "?")


def _write_eod_analysis(day: str) -> Path:
    """Write a conversational 7-section end-of-day analysis for `day` to logs/eod-analysis-<day>.md.
    Deterministic templated prose (no agent/LLM/network) so the scheduled close pass can write it
    unattended, sitting alongside the terse paper-eod-<day>.md. Reads the same paper book through
    strategy_metrics, so its numbers reconcile with strategy_report.py and the suite digest. Scoped
    to trades whose close session (see _close_session) is `day`."""
    trades = [t for t in metrics.load_closed_trades() if _close_session(t) == day]
    opened = [t for t in metrics.load_open_trades() if _open_session(t) == day]
    try:
        config = scanner._load_config(TEST_PROFILE)
    except Exception:
        config = {}
    block_list = config.get("correlation_block_list", []) or []

    nets = [metrics.net_pnl(t) for t in trades]
    gross = sum(t.get("pnl") or 0.0 for t in trades)
    costs_total = sum((t.get("entry_cost") or 0.0) + (t.get("exit_cost") or 0.0) for t in trades)
    net_total = sum(nets)
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    by_symbol = {}
    for t in trades:
        by_symbol[t["symbol"]] = by_symbol.get(t["symbol"], 0.0) + metrics.net_pnl(t)
    crush = metrics.avg_iv_crush(trades)

    def _entry_ctx(t):
        # load_closed_trades already parses entry_context into a dict; tolerate a raw string too.
        ec = t.get("entry_context")
        if isinstance(ec, dict):
            return ec
        try:
            return json.loads(ec or "{}")
        except (TypeError, ValueError):
            return {}

    L = [f"# Earnings Paper - EOD Analysis {day}", ""]
    L.append("_Plain-English read on the forced-sampling paper book (strat_test). Auto-generated from the "
             "paper DB (no agent) - conversational, but rule-based, not a hand-written synthesis. Defined-risk "
             "strategies only; each position opens one afternoon and closes the next morning. Two sides, on "
             "different days: what **settled** this morning (closed, realized P&L net of costs) and what was "
             "**opened** this afternoon and is carried overnight (capital at risk, no P&L yet)._")
    L.append("")

    # Opened-this-session summary reused in the snapshot and in section 4.
    open_risk = sum(t.get("capital_at_risk") or 0.0 for t in opened)
    by_sym_open: dict[str, int] = {}
    for t in opened:
        by_sym_open[t["symbol"]] = by_sym_open.get(t["symbol"], 0) + 1

    def _opened_line() -> str:
        if not opened:
            return ("No new positions were opened this afternoon, so nothing is carried into tonight — "
                    "or the entry pass has not run yet (this analysis is regenerated after it does).")
        names = ", ".join(f"{s} x{n}" for s, n in sorted(by_sym_open.items()))
        return (f"**{len(opened)}** position{'s' if len(opened) != 1 else ''} opened this afternoon "
                f"({names}), carrying **{_money(round(open_risk, 2))}** of defined max loss overnight. "
                "These have no P&L yet — they settle at the next open and land in that day's closed section.")

    # 1. Executive snapshot ----------------------------------------------------
    L.append("## 1. Executive snapshot")
    if not trades:
        L.append("Flat session - nothing settled this morning. Either no names qualified into the book last "
                 "afternoon, or none were held into this close. A quiet book is a decision, not a gap - the "
                 "scan_log shows which names were evaluated and why they were passed.")
        L.append(_opened_line())
    else:
        best = max(by_symbol.items(), key=lambda kv: kv[1])
        worst = min(by_symbol.items(), key=lambda kv: kv[1])
        wr = f"{len(wins) / len(trades) * 100:.0f}%" if trades else "-"
        drag = f", after {_money(round(costs_total, 2))} in costs ({(costs_total / gross * 100):.0f}% of the {_money(round(gross, 2))} gross)" if gross > 0 else f", with {_money(round(costs_total, 2))} of costs on top of a losing gross"
        L.append(
            f"**{len(trades)}** position{'s' if len(trades) != 1 else ''} closed out this session for "
            f"**{_money(round(net_total, 2))}** net ({len(wins)} up, {len(losses)} down, win rate {wr}){drag}.")
        line = "Average winner " + (_money(round(avg_win, 2)) if avg_win is not None else "-")
        line += ", average loser " + (_money(round(avg_loss, 2)) if avg_loss is not None else "-") + "."
        if best[0] != worst[0]:
            line += f" {best[0]} was the standout ({_money(round(best[1], 2))}); {worst[0]} the drag ({_money(round(worst[1], 2))})."
        L.append(line)
        L.append(_opened_line())
    L.append("")

    # 2. Position-level detail -------------------------------------------------
    L.append("## 2. Position-level detail")
    L.append("_Defined-risk earnings structures. Capital at risk is the known max loss set at entry; the IV "
             "crush column is the entry-to-exit drop in the sold legs' implied vol - the edge these plays harvest._")
    if trades:
        L.append("")
        L.append("| Symbol | Strategy | Legs | Qty | Max loss (cap@risk) | Entry IV | Exit IV | IV crush | Net P&L |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for t in trades:
            try:
                nlegs = len(json.loads(t.get("legs_json") or "[]"))
            except (TypeError, ValueError):
                nlegs = "-"
            ivc = metrics.iv_crush(t)
            ei = f"{t['entry_iv']:.1f}" if t.get("entry_iv") is not None else "-"
            xi = f"{t['exit_iv']:.1f}" if t.get("exit_iv") is not None else "-"
            ivc_txt = _signed(ivc) if ivc is not None else "-"
            L.append(f"| {t['symbol']} | {t.get('strategy', '-')} | {nlegs} | {t.get('quantity', '-')} | "
                     f"{_money(t.get('capital_at_risk'))} | {ei} | {xi} | {ivc_txt} | "
                     f"{_money(round(metrics.net_pnl(t), 2))} |")
    else:
        L.append("")
        L.append("_No positions settled - nothing to detail._")
    L.append("")

    # 3. Trade activity log ----------------------------------------------------
    L.append("## 3. Trade activity log")
    if trades:
        L.append("| Opened | Closed | Symbol | Strategy | Entry credit | Exit debit | Entry cost | Exit cost |")
        L.append("|---|---|---|---|---|---|---|---|")
        for t in sorted(trades, key=lambda x: x.get("opened_at") or 0):
            def _ts(v):
                try:
                    return datetime.fromtimestamp(float(v)).strftime("%m-%d %H:%M")
                except (TypeError, ValueError, OSError, OverflowError):
                    return "-"
            L.append(f"| {_ts(t.get('opened_at'))} | {_ts(t.get('closed_at'))} | {t['symbol']} | "
                     f"{t.get('strategy', '-')} | {_money(t.get('entry_credit'))} | "
                     f"{_money(t.get('exit_debit'))} | {_money(t.get('entry_cost'))} | "
                     f"{_money(t.get('exit_cost'))} |")
    else:
        L.append("_No settlements - nothing to log._")
    L.append("")

    # 4. Risk metrics ----------------------------------------------------------
    # Overnight risk is what is carried into TONIGHT -- the positions opened this afternoon, not the
    # ones that closed this morning (their risk is already resolved). The old version keyed this off
    # the closed trades and so reported "no overnight risk was carried" on a day that had just opened
    # five positions, because the report was written by the morning close pass before the afternoon
    # entry pass ran. Keying it off `opened` fixes that once the entry pass regenerates the file.
    L.append("## 4. Risk metrics")
    if opened:
        L.append(f"- Capital at risk overnight tonight (defined max loss, summed): "
                 f"**{_money(round(open_risk, 2))}** across {len(opened)} position(s) just opened.")
        conc = ", ".join(f"{s} {n} pos" for s, n in sorted(by_sym_open.items()))
        L.append(f"- Concentration by name: {conc}.")
        # Correlation groups from the block list (names that share overnight-gap risk) -- computed
        # over tonight's opens, since that is the exposure actually being carried.
        groups: dict[int, set] = {}
        for t in opened:
            for i, grp in enumerate(block_list):
                if t["symbol"] in grp:
                    groups.setdefault(i, set()).add(t["symbol"])
        collisions = {i: names for i, names in groups.items() if len(names) > 1}
        if collisions:
            for names in collisions.values():
                L.append(f"  - Correlation flag: {', '.join(sorted(names))} sit in the same block-list group - "
                         "the forced-sampling book intentionally ignores the correlation cap, so their overnight "
                         "gap risk is effectively correlated (the live loop would not hold these together).")
        else:
            L.append("  - No two names share a correlation block-list group - tonight's overnight risk is idiosyncratic per name.")
    else:
        L.append("- No positions were opened this session - no new overnight risk is carried into tonight.")
    if trades:
        settled_risk = sum(t.get("capital_at_risk") or 0.0 for t in trades)
        L.append(f"- For reference, the {len(trades)} position(s) that settled this morning had carried "
                 f"{_money(round(settled_risk, 2))} of defined max loss overnight; that risk is now resolved.")
    L.append("")

    # 5. Market context --------------------------------------------------------
    L.append("## 5. Market context")
    mctx = db_paper.cmd_get_market_context(argparse.Namespace(date=day))
    today_ctx, prior_ctx = mctx.get("today"), mctx.get("prior")
    if today_ctx and today_ctx.get("vix") is not None:
        dv = f" ({_signed(today_ctx['vix'] - prior_ctx['vix'])} vs the prior capture, roughly entry-evening)" if (prior_ctx and prior_ctx.get("vix") is not None) else ""
        L.append(f"VIX at this close sat around **{today_ctx['vix']:.1f}**{dv}.")
    else:
        L.append("No VIX snapshot was captured around this session (best-effort capture; the per-name IV crush "
                 "below is the volatility signal that actually matters for these plays).")
    if crush["sample_count"]:
        direction = "fell as expected (the post-earnings crush paid)" if crush["avg_crush"] and crush["avg_crush"] > 0 else "actually rose (no crush - the move outran the vol drop)"
        L.append(f"- Average IV crush across the {crush['sample_count']} measured position(s): "
                 f"**{_signed(crush['avg_crush'])}** vol points - implied vol {direction}.")
    ivrvs = [c.get("iv_rv_ratio") for c in (_entry_ctx(t) for t in trades) if c.get("iv_rv_ratio") is not None]
    if ivrvs:
        L.append(f"- Entry edge: average IV/RV ratio at entry was {sum(ivrvs) / len(ivrvs):.2f} "
                 "(>1 means options were pricing more move than the stock had realized - the setup these plays want).")
    L.append("- Catalyst: each position's own earnings release overnight is the event - there is no shared "
             "market catalyst across names the way an index book has.")
    L.append("")

    # 6. Tax / accounting notes ------------------------------------------------
    L.append("## 6. Tax / accounting notes")
    L.append("_Informational only - not tax advice. Paper book, so nothing here is a real taxable event._")
    if trades:
        L.append("- **Equity-option treatment** (not Section 1256): these are single-name equity options, so "
                 "ordinary short-term/long-term capital-gains rules apply - not the 60/40 mark-to-market that "
                 "broad-based index options get.")
        L.append("- Holding period: opened one afternoon, closed the next morning - **short-term** across the board.")
        loss_names = {}
        for t in trades:
            if metrics.net_pnl(t) <= 0:
                loss_names[t["symbol"]] = loss_names.get(t["symbol"], 0) + 1
        repeats = [s for s, n in loss_names.items() if n > 1]
        if repeats:
            L.append(f"- **Wash-sale watch**: {', '.join(sorted(repeats))} closed at a loss more than once this "
                     "session - repeated same-name losses within 30 days are where the wash-sale rule can defer a "
                     "loss (equity options, unlike 1256, are subject to it).")
    else:
        L.append("- No positions - no lots to classify.")
    L.append("")

    # 7. Notes / journal -------------------------------------------------------
    L.append("## 7. Notes / journal")
    if not trades:
        if opened:
            L.append(f"- Nothing settled this morning (nothing was held in from the prior afternoon), but the "
                     f"book is not idle: {len(opened)} position(s) went on this afternoon and are carried into "
                     "tonight. They settle at the next open and show up in that day's closed section.")
        else:
            L.append("- Nothing settled and nothing opened. Worth confirming the entry pass actually ran this "
                     "afternoon (a scan that found no candidates and a scan that silently failed look identical "
                     "here) - the scan_log and the entry-review table below show which names were evaluated.")
    else:
        by_strategy = {}
        for t in trades:
            by_strategy.setdefault(t.get("strategy", "?"), []).append(metrics.net_pnl(t))
        strat_net = {s: sum(v) for s, v in by_strategy.items()}
        best_s = max(strat_net.items(), key=lambda kv: kv[1])
        worst_s = min(strat_net.items(), key=lambda kv: kv[1])
        L.append(f"- Best strategy today: **{best_s[0]}** ({_money(round(best_s[1], 2))}); weakest: "
                 f"**{worst_s[0]}** ({_money(round(worst_s[1], 2))}).")
        if crush["sample_count"] and crush["avg_crush"] is not None:
            if crush["avg_crush"] > 0 and net_total > 0:
                L.append("- The thesis held: IV crushed and the book kept the premium. Textbook earnings-vol session.")
            elif crush["avg_crush"] <= 0:
                L.append("- **Recommendation:** IV rose rather than crushed - the stocks moved more than the vol "
                         "gave back. If this recurs, the entry IV/RV bar may be too low for the current regime.")
        if gross > 0 and costs_total / gross > 0.30:
            L.append(f"- **Recommendation:** costs ate {(costs_total / gross * 100):.0f}% of gross - these are "
                     "small defined-risk plays where the fixed per-contract fee bites; favor higher-conviction, "
                     "better-liquidity names to keep the cost share down.")
        if avg_loss is not None and avg_win is not None and abs(avg_loss) > 2 * (avg_win or 0):
            L.append("- **Recommendation:** the average loser is more than 2x the average winner - defined risk "
                     "capped the damage, but the win/loss asymmetry says the losers are running to their max. "
                     "Consider earlier profit-taking or tighter names.")
    L.append("")

    # --- Symbols reviewed for entry (the scan behind these positions) ---------
    L.append("## Symbols reviewed for entry")
    scan_date, reviews = _entry_reviews_for(day)
    if reviews:
        chosen = sum(1 for rv in reviews if rv.get("selected"))
        L.append(f"_The {scan_date} entry scan reviewed **{len(reviews)}** symbol(s) — {chosen} chosen, "
                 f"{len(reviews) - chosen} rejected. The data reviewed per symbol and why each was taken "
                 "or passed:_")
        L.append("")
        L.append("| Symbol | Decision | Price | Volume | Winrate | IV/RV | Term struct | Market cap | Tier | Reason |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for rv in reviews:
            price = f"${rv['price']:,.2f}" if rv.get("price") is not None else "-"
            vol = f"{int(rv['volume']):,}" if rv.get("volume") is not None else "-"
            if rv.get("winrate") is not None:
                wr = f"{rv['winrate'] * 100:.0f}%"
                if rv.get("winrate_sample") is not None:
                    wr += f" ({int(rv['winrate_sample'])})"
            else:
                wr = "-"
            ivrv = f"{rv['iv_rv_ratio']:.2f}" if rv.get("iv_rv_ratio") is not None else "-"
            term = f"{rv['term_structure']:.3f}" if rv.get("term_structure") is not None else "-"
            mcap = f"{int(rv['market_cap']):,}" if rv.get("market_cap") is not None else "-"
            decision = "✅ chosen" if rv.get("selected") else "⚪ rejected"
            L.append(f"| {rv['symbol']} | {decision} | {price} | {vol} | {wr} | {ivrv} | {term} | "
                     f"{mcap} | {rv.get('best_tier') or '-'} | {rv.get('reason') or '-'} |")
    else:
        L.append("_No entry-review records for the scan behind this session (the scan predates this "
                 "feature, or ran on a different book)._")
    L.append("")

    L.append(f"_Generated {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')} · paper DB only; "
             "live account untouched. Companion to paper-eod-" + day + ".md._")

    path = _analysis_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def _entry_reviews_for(day: str) -> tuple:
    """(scan_date, reviews) for the most recent entry scan on or before `day` — the entries whose
    positions are settling by this session. Best-effort; ([], None) if unavailable."""
    try:
        res = db_paper.cmd_get_entry_reviews(argparse.Namespace(date=day, scan_date=None))
        return res.get("scan_date"), res.get("reviews", [])
    except Exception:
        return None, []


def cmd_eod_analysis(args) -> dict:
    day = args.date or _date.today().isoformat()
    path = _write_eod_analysis(day)
    return {"ok": True, "date": day, "analysis": str(path)}


def _capture_market_context(day: str) -> None:
    """Best-effort VIX snapshot for the EOD analysis report, keyed on the action date. Never a
    trading input and never fails the pass -- earnings' real volatility signal is per-name IV crush;
    this only colors the market-context section with the overnight index move. The report reads the
    close-session row plus the prior day's row (roughly entry-evening VIX) for the overnight delta."""
    try:
        q = scanner.call_tt(["get_quote", "--symbol", "VIX"])
        vix = q.get("price") if isinstance(q, dict) and q.get("ok") else None
        if vix is None:
            return
        db_paper.cmd_save_market_context(argparse.Namespace(data=json.dumps({
            "context_date": day, "vix": vix, "updated_at": time.time(),
        })))
    except Exception:
        pass


class _OpTimeout(Exception):
    """A bounded scan step (a Dolt-heavy operation) exceeded its wall-clock budget."""


def _run_bounded(fn, timeout_s, *args, **kwargs):
    """Run ``fn(*args, **kwargs)`` with a wall-clock ceiling; return its result or raise _OpTimeout.

    The entry scan's Dolt queries have no client-side read timeout (mysql-connector offers none) and
    Dolt does not honor the server-side ``max_execution_time`` SELECT cap (verified against the live
    server), so a Dolt that is cold-starting or compacting makes ``cur.execute()`` block forever --
    which got the scheduled entry run killed at its 30-minute external timeout (2026-07-14 and
    2026-07-22). Running the step in a daemon thread lets the scan abandon a hung symbol and move on;
    the orphaned thread cannot be killed but dies with this short-lived process. Same bounded-and-
    returns-failure intent as ``scanner.call_tt``, without a subprocess (the Dolt calls are in-process).
    """
    box: dict = {}

    def _target():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the caller's except below
            box["error"] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise _OpTimeout(f"exceeded {timeout_s}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def cmd_run_entries(args) -> dict:
    if not rank_strategies._ensure_dolt_running():
        return {"ok": False, "error": "dolt sql-server not available"}
    if not rank_strategies._verify_tastytrade_connection():
        return {"ok": False, "error": "tastytrade connection failed"}

    profile = args.profile
    config = scanner._load_config(profile)
    tier_floor = config.get("tier_floor", "Tier 2")
    allowed_tiers = ("Tier 1",) if tier_floor == "Tier 1" else ("Tier 1", "Tier 2")

    calendar_timeout = config.get("dolt_calendar_timeout_seconds", 30)
    try:
        calendar = _run_bounded(scanner.fetch_entry_window_calendar, calendar_timeout, config)
    except _OpTimeout:
        # Dolt could not return the entry calendar in time -- fail fast with a clear cause rather than
        # letting the scheduled task hang to its 30-minute kill (which logs only an opaque timeout).
        return {"ok": False, "error": f"dolt calendar fetch exceeded {calendar_timeout}s"}
    scan_date = str(_date.today())
    _capture_market_context(scan_date)  # entry-evening VIX for the next close session's analysis

    opened: list[dict] = []
    skipped: list[dict] = []

    scan_deadline = time.monotonic() + config.get("entry_scan_budget_seconds", 1500)
    symbol_timeout = config.get("dolt_symbol_timeout_seconds", 90)

    for entry in calendar:
        if time.monotonic() > scan_deadline:
            # Overall wall-clock backstop: even bounded-but-slow Dolt across many names must not push
            # the run into its external kill. Stop scanning, keep what opened, write partial results.
            skipped.append({"symbol": None, "strategy": None, "reason": "entry_scan_budget_exceeded"})
            break
        symbol, earnings_date, timing = entry["symbol"], entry["date"], entry["timing"]
        try:
            results = _run_bounded(
                rank_strategies.evaluate_symbol, symbol_timeout, symbol, earnings_date, timing, config
            )
        except _OpTimeout:
            # This symbol's Dolt evaluation stalled -- skip it (the run continues) instead of letting
            # one hung query stall the whole scan to the external kill.
            skipped.append({"symbol": symbol, "strategy": None, "reason": f"evaluate_symbol_timeout_{symbol_timeout}s"})
            _save_entry_review(scan_date, symbol, timing, [], [], [f"evaluate_symbol_timeout_{symbol_timeout}s"])
            continue
        except Exception as exc:
            skipped.append({"symbol": symbol, "strategy": None, "reason": f"evaluate_symbol_error: {exc}"})
            _save_entry_review(scan_date, symbol, timing, [], [], [f"evaluate_symbol_error: {exc}"])
            continue

        op0, sk0 = len(opened), len(skipped)  # this symbol's slice of the run-wide result lists
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

        # After all of this symbol's strategies: record the per-symbol review (data reviewed + the
        # chosen/rejected decision) for the notifier and the EOD analysis.
        _save_entry_review(
            scan_date, symbol, timing, results,
            [o["strategy"] for o in opened[op0:]],
            [s.get("reason", "") for s in skipped[sk0:]],
        )

    # Regenerate today's EOD report to fold in the positions just opened. The morning close pass
    # wrote this file ~6 hours ago with the "Opened this session" section empty (entries had not
    # happened yet); this rewrite fills it in and corrects the overnight-risk metric, which would
    # otherwise read "no risk carried" on a day that just opened positions. Unconditional overwrite,
    # not the close pass's file-exists guard -- the whole point is to update the existing file. The
    # closed-trades sections are recomputed from the same DB rows, so they come out identical.
    # Best-effort: a report failure must never fail the entry result the scheduled task depends on.
    today = _date.today().isoformat()
    try:
        _write_eod_report(today)
        _write_eod_analysis(today)
    except Exception:
        pass

    return {"ok": True, "date": scan_date, "profile": profile, "opened": opened, "skipped": skipped}


def cmd_run_closes(args) -> dict:
    if not rank_strategies._verify_tastytrade_connection():
        return {"ok": False, "error": "tastytrade connection failed"}

    config = scanner._load_config(args.profile)
    _capture_market_context(_date.today().isoformat())  # close-session morning VIX for the analysis
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

    # Once-per-day EOD report, written on the settlement (close) pass -- mirrors the MEIC paper
    # loop. Best-effort with a file-exists guard: a report failure must never fail the close
    # result the scheduled exit task depends on, and a manual re-run of run_closes won't clobber
    # an existing file (regenerate on demand with the eod_report subcommand instead).
    today = _date.today().isoformat()
    if not _eod_report_path(today).exists():
        try:
            _write_eod_report(today)
        except Exception:
            pass
        # Companion conversational analysis, written the same once-per-day pass.
        try:
            _write_eod_analysis(today)
        except Exception:
            pass

    return {"ok": True, "closed": closed, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_entries = sub.add_parser("run_entries")
    p_entries.add_argument("--date", required=True)
    p_entries.add_argument("--profile", default="balanced")

    p_closes = sub.add_parser("run_closes")
    p_closes.add_argument("--profile", default="balanced")

    p_eod = sub.add_parser("eod_report")
    p_eod.add_argument("--date", default=None, help="Close-session day (YYYY-MM-DD); default today")

    p_eoda = sub.add_parser("eod_analysis")
    p_eoda.add_argument("--date", default=None, help="Close-session day (YYYY-MM-DD); default today")

    args = parser.parse_args()
    dispatch = {
        "run_entries": cmd_run_entries,
        "run_closes": cmd_run_closes,
        "eod_report": cmd_eod_report,
        "eod_analysis": cmd_eod_analysis,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
