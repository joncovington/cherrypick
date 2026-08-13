"""Forced-sampling paper-trading harness for thoroughly testing every
strategy (see docs/strategy-testing-plan.md). `rank_strategies.py` opens
only the single best strategy per symbol per night -- fine for the live
loop, but candidates are scarce enough that most strategies would starve
under natural selection and never reach a statistically meaningful sample
in weeks. This module instead opens a **separate strat_test paper book**
(the `profile` column in the shared data/paper_trades.db) with a trade for
*every* strategy that clears the screen on *every* viable symbol each night
-- up to one per (symbol, strategy) pair.

The book is split by `strat_test_portfolio` config: "per_strategy" (the
default) tags each trade `strat_test:<strategy>` so every strategy is its
own portfolio with its own P&L/equity curve (and a newly added strategy
automatically gets its own stream); "combined" keeps a single `strat_test`
book (see docs/paper-trading.md and _book_tag).

This is entirely separate from the live/paper trading loop (CLAUDE.md's
Loop Steps, rank_strategies.py's own get_ranked_symbols) -- it never
selects a single "best" strategy, never respects max_concurrent_earnings_
positions or the correlation block list (the test book intentionally holds
many overlapping positions at once), and never calls tt.py execute_trade.
Always paper-only, regardless of config's enable_live_trading.

Sizing basis is config's available_capital_paper_mode (each per-strategy
book draws on the full basis, as independent paper accounts). Fills are
cost-adjusted via costs.py's tastytrade fee model, not mid-price.

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
  run_entries --date MM/DD/YYYY
  run_closes
"""

import argparse
import concurrent.futures as _cf
import json
import sys
import threading
import time
from datetime import date as _date
from pathlib import Path

from cherrypick.core import viz

from cherrypick.earnings import costs, db_paper, paths, rank_strategies, scanner, sizing, symbol_watch
from cherrypick.earnings import strategy_metrics as metrics
from cherrypick.earnings.strategies import (
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

# Multi-day strategies are MANAGED, not force-closed: the 09:45 sweep consults the
# strategy's own evaluate_position (CLAUDE.md Steps 3b/3d) and closes only on its
# verdict -- profit target, stop, leg stop, or its own time_exit backstop ahead of
# front expiration. Force-closing them the morning after entry (the old behavior)
# measured a one-night structure nobody intends to trade. The five overnight
# strategies keep the unconditional sweep: that IS their Step 3 close-window design
# ("IV crush already happened overnight; no more edge from holding").
_MULTI_DAY = {
    "atm_calendar": atm_calendar,
    "double_calendar": double_calendar,
}

# Overnight-hold strategies keep Step 3's unconditional close-window backstop --
# unlike _MULTI_DAY, a "hold" verdict here never skips the close. But before falling
# through to that backstop, consult the strategy's own Step 3c evaluate_position so a
# profit target, stop loss, or backstop that already fired gets its real reason (and
# the config's own thresholds, not just "close_window") recorded instead of masking it.
_OVERNIGHT_MANAGED = {
    "iron_fly": iron_fly,
    "iron_condor": iron_condor,
    "directional_credit_spread": directional_credit_spread,
    "broken_wing_butterfly": broken_wing_butterfly,
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
        # delta rides along for double_calendar's per-leg stop (evaluate_position treats
        # a missing delta as "skip that check", same optionality as iv).
        result[s] = {"bid": q["bid"], "ask": q["ask"], "iv": q.get("iv"), "delta": q.get("delta")}
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


def _scaled_legs(template_legs: list[dict], quantity: int) -> list[dict]:
    """Scale a get_order leg template to the sized position for legs_json. Each leg's
    stored quantity is its own structure ratio (e.g. broken_wing_butterfly's x2 body;
    1 when the field is absent) times the position quantity. Overwriting with the
    position quantity instead would flatten a 1-2-1 fly to 1-1-1, and the close --
    which prices legs_json via scanner.compute_generic_exit_debit -- would buy the
    body back once while entry_credit had sold it twice: a phantom profit of one
    body price per contract on every ratioed structure."""
    return [{**leg, "quantity": int(leg.get("quantity", 1) or 1) * quantity} for leg in template_legs]


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
    """The conditions this position was opened into, for slicing outcomes by regime later.

    Deliberately wider than it was. The original five keys omitted `bid_ask_spread_pct` entirely --
    0 of 64 trades carried it -- which is the single field the cost analysis most wants, since
    modelled slippage is 91% of cost and slippage is a function of spread. Regime data is the one
    thing that cannot be backfilled: a condition not captured at entry is gone, so the bar for
    including a field here is "might we ever want to slice on it", not "do we have a use today".
    With ~14 independent earnings events on file, that day is a long way off either way.

    Everything here is already in `criteria` at entry -- no extra fetch, no broker call. Costs are
    NOT duplicated in: `entry_cost`, `entry_slippage` and `capital_at_risk` are their own columns
    on `trades`, so cost-to-risk is a query over what is already stored rather than a stored field.
    Market-wide regime is likewise not copied here; `market_context` records VIX per scan_date and
    joins on it.
    """
    return {
        # Signal strength at entry
        "iv_rv_ratio": criteria.get("iv_rv_ratio"),
        "term_structure": criteria.get("term_structure"),
        "expected_move_pct": criteria.get("expected_move_pct"),
        "iv_rank": criteria.get("iv_rank"),
        "iv_percentile": criteria.get("iv_percentile"),
        # Implied vs this name's own realized history -- the edge these plays actually harvest
        "implied_vs_avg_actual": criteria.get("implied_vs_avg_actual"),
        "dispersion": criteria.get("realized_move_dispersion_pct"),
        "move_tail_veto": criteria.get("move_tail_veto"),
        # Execution quality: what the fill was up against
        "bid_ask_spread_pct": criteria.get("bid_ask_spread_pct"),
        "net_combo_spread_pct": criteria.get("net_combo_spread_pct"),
        "front_expiration_days": criteria.get("front_expiration_days"),
        # Liquidity band
        "market_cap": criteria.get("market_cap"),
        "avg_volume": criteria.get("avg_volume"),
        "combined_open_interest": criteria.get("combined_open_interest"),
        "combined_option_volume": criteria.get("combined_option_volume"),
        # Sample quality -- a winrate means little without the size behind it
        "skew_abs": criteria.get("skew_abs"),
        "winrate": criteria.get("winrate"),
        "winrate_sample_size": criteria.get("winrate_sample_size"),
        "composite_score": composite_score,
    }


# --- Per-symbol entry review (the data reviewed for a symbol + the chosen/rejected decision) ---------
def _book_tag(config: dict, strategy_name: str) -> str:
    """The paper-book (profile) tag a strat_test trade is written under. In
    "per_strategy" mode (the default) each strategy gets its own book,
    strat_test:<name>, so its P&L and equity curve stand alone and a newly
    added strategy automatically gets its own stream; "combined" keeps the
    single strat_test book."""
    mode = config.get("strat_test_portfolio", "per_strategy")
    if mode == "per_strategy":
        return f"{TEST_PROFILE}:{strategy_name}"
    return TEST_PROFILE


def _is_strat_test_book(profile: str | None) -> bool:
    """True for any strat_test book tag -- the single combined tag or a
    per-strategy strat_test:<name> tag -- so run_closes sweeps them all."""
    return bool(profile) and (profile == TEST_PROFILE or profile.startswith(TEST_PROFILE + ":"))


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


def _save_entry_review(
    scan_date, symbol, timing, results, opened_strategies, skip_reasons, timing_assumed=None
) -> None:
    """Persist one per-symbol review — the reviewed data + the chosen/rejected decision — for the
    orchestrator's per-symbol notification and the EOD analysis. Best-effort; never breaks the scan.
    Delegates the spec shape to scanner.build_entry_review_spec, shared with rank_strategies.py's own
    _save_entry_review so both callers' entry_reviews rows carry the same research-backed screening
    metrics (implied-vs-historical move, spread quality, IV rank, move-tail flag), whichever caller
    ran the scan."""
    crit, strategy, score = scanner.richest_criteria(results)
    selected = bool(opened_strategies)
    reason = (
        ("opened " + ", ".join(sorted(set(opened_strategies))))
        if selected
        else _summarize_skips(skip_reasons)
    )
    spec = scanner.build_entry_review_spec(
        scan_date,
        symbol,
        timing,
        crit,
        strategy,
        selected,
        reason,
        composite_score=score,
        timing_assumed=timing_assumed,
    )
    spec["profile"] = TEST_PROFILE
    try:
        db_paper.cmd_save_entry_review(argparse.Namespace(data=json.dumps(spec, default=str)))
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
    # The suite's one formatter (cherrypick.core.viz), keeping this report's "-" placeholder.
    return viz.fmt_money(x, none="-")


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


def _open_positions_with_marks() -> list[dict]:
    """Open positions, each carrying its latest USABLE mark and how long it has been held.

    Usable only: a refused mark records that we looked and could not price it, which is worth
    keeping but is not a valuation. Reporting one as the position's worth would put a number in the
    report that no quote ever supported.
    """
    out = []
    for trade in metrics.load_open_trades():
        marks = db_paper.cmd_get_marks(
            argparse.Namespace(order_id=trade["order_id"], session_date=None, limit=50)
        )["marks"]
        usable = next((m for m in marks if m.get("usable")), None)
        out.append(
            {
                **trade,
                "_mark": usable,
                "_sessions_held": db_paper.session_span(trade.get("opened_at"), time.time()),
            }
        )
    return out


def _feed_quality(day: str) -> dict:
    """What the data looked like on `day` — how many marks were taken, how many were refused and
    why, and which decisions an execution gate held back."""
    marks = db_paper.cmd_get_marks(argparse.Namespace(order_id=None, session_date=day, limit=5000))["marks"]
    events = db_paper.cmd_get_management_events(
        argparse.Namespace(order_id=None, session_date=day, limit=5000)
    )["events"]

    refusals: dict[str, int] = {}
    for m in marks:
        if not m.get("usable") and m.get("refusal"):
            refusals[m["refusal"]] = refusals.get(m["refusal"], 0) + 1
    gated: dict[str, int] = {}
    for e in events:
        if not e.get("executed") and e.get("gate"):
            gated[e["gate"]] = gated.get(e["gate"], 0) + 1

    return {
        "marks": len(marks),
        "usable": sum(1 for m in marks if m.get("usable")),
        "refused": sum(1 for m in marks if not m.get("usable")),
        "rest": sum(1 for m in marks if m.get("source") == "rest"),
        "refusals": refusals,
        "gated": gated,
    }


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


# ---------------------------------------------------------------------------
# EOD analysis report -- conversational, 7-section, still fully deterministic
# ---------------------------------------------------------------------------


def _analysis_path(day: str) -> Path:
    return _logs_dir() / f"eod-analysis-{day}.md"


def _signed(x) -> str:
    return f"+{x:.2f}" if x is not None and x >= 0 else (f"{x:.2f}" if x is not None else "?")


def _entry_reviews_for(day: str) -> tuple:
    """(scan_date, reviews) for the most recent entry scan on or before `day` — the entries whose
    positions are settling by this session. Best-effort; ([], None) if unavailable."""
    try:
        res = db_paper.cmd_get_entry_reviews(argparse.Namespace(date=day, scan_date=None))
        return res.get("scan_date"), res.get("reviews", [])
    except Exception:
        return None, []


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
        db_paper.cmd_save_market_context(
            argparse.Namespace(
                data=json.dumps(
                    {
                        "context_date": day,
                        "vix": vix,
                        "updated_at": time.time(),
                    }
                )
            )
        )
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


def _parallel_scan(calendar, config, workers, symbol_timeout, budget_seconds):
    """Evaluate every calendar symbol's strategies concurrently, bounded two ways: each symbol by
    ``symbol_timeout`` (via _run_bounded) and the whole phase by ``budget_seconds``. Returns a list
    aligned to ``calendar`` of ``(entry, results, reason)`` -- ``reason`` is None on success, else a
    skip reason (timeout / error / entry_scan_budget_exceeded).

    Broker reads only, no DB writes: evaluate_symbol calls are independent and broker-read heavy, and
    the tt cache is thread-local, so running symbols concurrently is safe and collapses the scan's
    wall clock by roughly the worker count. Order building and every SQLite write stay in the caller's
    sequential phase so the paper DB keeps a single writer.
    """
    out: list = [None] * len(calendar)
    # Check the budget BEFORE dispatching anything. Submitting first and relying on as_completed's
    # timeout + cancel_futures does not hold: a worker begins executing the moment it is submitted,
    # and cancel_futures only cancels futures that have not started -- so an already-exhausted budget
    # still fired up to `workers` Dolt-heavy evaluations, decided by a thread race. That is the exact
    # work this backstop exists to prevent.
    if budget_seconds is not None and budget_seconds <= 0:
        return [(e, None, "entry_scan_budget_exceeded") for e in calendar]
    pool = _cf.ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        fut_to_idx = {
            pool.submit(
                _run_bounded,
                rank_strategies.evaluate_symbol,
                symbol_timeout,
                e["symbol"],
                e["date"],
                e["timing"],
                config,
            ): i
            for i, e in enumerate(calendar)
        }
        try:
            for fut in _cf.as_completed(fut_to_idx, timeout=budget_seconds):
                i = fut_to_idx[fut]
                try:
                    out[i] = (calendar[i], fut.result(), None)
                except _OpTimeout:
                    out[i] = (calendar[i], None, f"evaluate_symbol_timeout_{symbol_timeout}s")
                except Exception as exc:
                    out[i] = (calendar[i], None, f"evaluate_symbol_error: {exc}")
        except _cf.TimeoutError:
            pass  # overall budget hit -- unfinished symbols fall through to budget_exceeded below
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for i, slot in enumerate(out):
        if slot is None:
            out[i] = (calendar[i], None, "entry_scan_budget_exceeded")
    return out


def _log_scan_row(
    scan_date: str,
    symbol: str,
    strategy: str,
    profile: str,
    *,
    stage: str,
    outcome: str,
    reason: str | None,
    reject_details: list | None = None,
) -> None:
    """Append one scan_log row. Best-effort: telemetry must never fail a scan.

    `stage` separates the halves of a candidate's life -- 'prefilter', 'screen' for the
    accept/reject verdict, 'execution' for what became of an accepted one, 'exit' for a close.
    All are recorded for every (symbol, strategy) that gets that far, so the funnel from calendar
    to open position can be read off this table alone rather than inferred from what is missing.

    Deliberately writes no `tier`. That column only ever restated `outcome`, except for the
    'close_sweep' literal that marked an exit row -- which `stage` now says directly. It stays in
    the schema because the historical rows need it: the retired graded ladder is only identifiable
    by its own tier/outcome vocabulary (see screen_metrics.classify).
    """
    try:
        db_paper.cmd_log_scan(
            argparse.Namespace(
                data=json.dumps(
                    {
                        "scan_date": scan_date,
                        "symbol": symbol,
                        "strategy": strategy,
                        "outcome": outcome,
                        "reason": reason,
                        "stage": stage,
                        "reject_details": reject_details or None,
                        "logged_at": time.time(),
                        "profile": profile,
                    },
                    default=str,
                )
            )
        )
    except Exception:
        pass


def _log_prefilter_skip(scan_date: str, symbol: str, reason: str) -> None:
    """Record a name the morning scan disqualified, so a symbol missing from the evening's candidate
    list is explained rather than simply absent. Best-effort: telemetry must never fail a scan."""
    try:
        _log_scan_row(
            scan_date,
            symbol,
            "_prefilter",
            TEST_PROFILE,
            stage="prefilter",
            outcome="skipped",
            reason=reason,
        )
    except Exception:
        pass


def cmd_run_entries(args) -> dict:
    if not rank_strategies._ensure_dolt_running():
        return {"ok": False, "error": "dolt sql-server not available"}
    if not rank_strategies._verify_tastytrade_connection():
        return {"ok": False, "error": "tastytrade connection failed"}

    config = scanner._load_config()

    scan_date = str(_date.today())
    calendar_timeout = config.get("dolt_calendar_timeout_seconds", 30)
    # This morning's forward scan also bounds which UNANNOTATED calendar rows are admissible as AMC:
    # the earnings calendar's `when` column is mostly NULL now, and requiring it dropped liquid names
    # on their own earnings day (see scanner.fetch_entry_window_calendar).
    assume_amc_for = symbol_watch.covered_symbols(session=scan_date)
    try:
        calendar = _run_bounded(
            scanner.fetch_entry_window_calendar,
            calendar_timeout,
            config,
            assume_amc_for=assume_amc_for,
        )
    except _OpTimeout:
        # Dolt could not return the entry calendar in time -- fail fast with a clear cause rather than
        # letting the scheduled task hang to its 30-minute kill (which logs only an opaque timeout).
        return {"ok": False, "error": f"dolt calendar fetch exceeded {calendar_timeout}s"}
    _capture_market_context(scan_date)  # entry-evening VIX for the next close session's analysis

    # Narrow the list using this morning's forward scan before paying for a live chain per name.
    # Stable criteria only (winrate, average volume, market cap) and against the loosest floor, so a
    # dropped name is one that could not have passed under any setting -- every survivor is still
    # screened entirely on live data below. Measured cost is ~8s per symbol, so on a heavy night this
    # is the difference between finishing inside the entry window and running past it.
    calendar, prefiltered = symbol_watch.prefilter_symbols(calendar, config, session=scan_date)
    if prefiltered:
        for symbol, reason in prefiltered.items():
            _log_prefilter_skip(scan_date, symbol, reason)

    opened: list[dict] = []
    skipped: list[dict] = []

    workers = config.get("entry_scan_workers", 4)
    symbol_timeout = config.get("dolt_symbol_timeout_seconds", 90)
    budget = config.get("entry_scan_budget_seconds", 1500)

    # Scan phase (parallel, broker reads only): evaluate every symbol's strategies concurrently.
    # Write phase (sequential, below): order building + every paper-DB write, so SQLite has one writer.
    scanned = _parallel_scan(calendar, config, workers, symbol_timeout, budget)

    for entry, results, scan_reason in scanned:
        symbol, earnings_date, timing = entry["symbol"], entry["date"], entry["timing"]
        timing_assumed = entry.get("timing_assumed")
        if scan_reason is not None:
            # Timed out, errored, or past the overall budget -- skip cleanly, keep everything else.
            skipped.append({"symbol": symbol, "strategy": None, "reason": scan_reason})
            _save_entry_review(scan_date, symbol, timing, [], [], [scan_reason], timing_assumed)
            continue

        op0, sk0 = len(opened), len(skipped)  # this symbol's slice of the run-wide result lists
        for r in results:
            strategy_name = r["name"]
            reasons = r["reject_reasons"]
            decision = "accepted" if r["accepted"] else "rejected"
            book = _book_tag(config, strategy_name)
            _log_scan_row(
                scan_date,
                symbol,
                strategy_name,
                book,
                stage="screen",
                outcome=decision,
                reason="; ".join(reasons) if reasons else None,
                reject_details=scanner.explain_reject_reasons(
                    reasons, r["criteria"], config["strategies"].get(strategy_name, {})
                ),
            )

            if not r["accepted"]:
                # The actual gates, not the word "screen_rejected" -- they are right here in scope,
                # and this is what the per-symbol review summarises for the EOD table.
                skipped.append(
                    {"symbol": symbol, "strategy": strategy_name, "reason": "; ".join(reasons) or "rejected"}
                )
                continue

            # From here the candidate has passed the screen, so every remaining exit is an EXECUTION
            # outcome. Those went unrecorded until now -- a candidate that cleared the screen and then
            # failed order building, sizing, the risk cap or quote availability left no trace at all.
            # Defaults bind this iteration's symbol/strategy/book -- `drop` is only ever called
            # within the iteration that defines it, but binding makes that independent of where a
            # future edit moves the call.
            def drop(reason: str, _sym=symbol, _s=strategy_name, _b=book) -> None:
                skipped.append({"symbol": _sym, "strategy": _s, "reason": reason})
                _log_scan_row(
                    scan_date, _sym, _s, _b, stage="execution", outcome="dropped", reason=reason
                )

            try:
                order = _ORDER_FNS[strategy_name](symbol, earnings_date, timing, config)
                if not order.get("ok"):
                    drop(f"order_build_failed: {order.get('error')}")
                    continue

                strategy_config = config["strategies"][strategy_name]
                size = sizing.compute_position_size(order, strategy_config, config)
                if not size["ok"]:
                    drop(size["reason"])
                    continue
                quantity = size["quantity"]

                template_legs = order["order"]["legs"]
                leg_symbols = [leg["symbol"] for leg in template_legs]
                price = order.get("underlying_price", 0.0)
                leg_quotes = _leg_quotes_for_symbols(symbol, leg_symbols, price)
                if leg_quotes is None:
                    drop("leg_quotes_unavailable")
                    continue

                entry_costs = costs.apply_entry_costs(
                    order,
                    [leg_quotes[s] for s in leg_symbols],
                    quantity,
                    config,
                )
                entry_iv = _avg_sold_iv(template_legs, leg_quotes)

                scaled_legs = _scaled_legs(template_legs, quantity)
                per_contract = _per_contract_credit(order)
                entry_credit = per_contract * quantity

                order_id = f"{TEST_PROFILE}-{strategy_name}-{symbol}-{scan_date}-{int(time.time() * 1000)}"
                save_spec = {
                    "order_id": order_id,
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "expiration": order.get("expiration") or order.get("front_expiration"),
                    "legs_json": json.dumps(scaled_legs),
                    "entry_credit": entry_credit,
                    "profile": book,
                    "quantity": quantity,
                    "capital_at_risk": size["capital_at_risk"],
                    "entry_cost": entry_costs["total_cost"],
                    "entry_slippage": entry_costs["slippage"],
                    "entry_iv": entry_iv,
                    "entry_context": _entry_context(r["criteria"], r["composite_score"]),
                }
                # Strategies with role-labeled legs (double_calendar today) get trade_legs
                # rows, so the close sweep's evaluate_position can run its per-leg checks.
                label_fn = getattr(_MULTI_DAY.get(strategy_name), "label_order_legs", None)
                if label_fn is not None:
                    save_spec["legs"] = [
                        {**leg, "quantity": int(leg.get("quantity", 1) or 1) * quantity}
                        for leg in label_fn(order)
                    ]
                save_result = db_paper.cmd_save_trade(argparse.Namespace(data=json.dumps(save_spec)))
                if not save_result.get("ok"):
                    drop(f"save_trade_failed: {save_result.get('error')}")
                    continue

                _log_scan_row(
                    scan_date,
                    symbol,
                    strategy_name,
                    book,
                    stage="execution",
                    outcome="opened",
                    reason=None,
                )
                opened.append(
                    {
                        "order_id": order_id,
                        "symbol": symbol,
                        "strategy": strategy_name,
                        "quantity": quantity,
                        "capital_at_risk": size["capital_at_risk"],
                        "entry_cost": entry_costs["total_cost"],
                    }
                )
            except Exception as exc:
                # One candidate's unexpected failure (e.g. an order-building edge case)
                # must not lose every other candidate's already-accumulated results for
                # the night -- log and move on, same discipline as the evaluate_symbol
                # try/except above.
                drop(f"unexpected_error: {exc}")

        # After all of this symbol's strategies: record the per-symbol review (data reviewed + the
        # chosen/rejected decision) for the notifier and the EOD analysis.
        _save_entry_review(
            scan_date,
            symbol,
            timing,
            results,
            [o["strategy"] for o in opened[op0:]],
            [s.get("reason", "") for s in skipped[sk0:]],
            timing_assumed,
        )

    # Regenerate today's EOD report to fold in the positions just opened. The morning close pass
    # wrote this file ~6 hours ago with the "Opened this session" section empty (entries had not
    # happened yet); this rewrite fills it in and corrects the overnight-risk metric, which would
    # otherwise read "no risk carried" on a day that just opened positions. Unconditional overwrite,
    # not the close pass's file-exists guard -- the whole point is to update the existing file. The
    # closed-trades sections are recomputed from the same DB rows, so they come out identical.
    # Best-effort: a report failure must never fail the entry result the scheduled task depends on.
    portfolio_mode = config.get("strat_test_portfolio", "per_strategy")
    return {
        "ok": True,
        "date": scan_date,
        "portfolio_mode": portfolio_mode,
        "opened": opened,
        "skipped": skipped,
    }


def _log_close_decision(trade: dict, outcome: str, reason: str | None) -> None:
    """Journal the close sweep's per-position verdict (hold / closed + reason) to
    scan_log -- the same audit trail the entry scan writes, so "why is this calendar
    still open" and "what closed this" are answerable from the DB. Best-effort: a
    journal failure must never affect the close itself."""
    try:
        db_paper.cmd_log_scan(
            argparse.Namespace(
                data=json.dumps(
                    {
                        "scan_date": _date.today().isoformat(),
                        "strategy": trade.get("strategy"),
                        "symbol": trade.get("symbol"),
                        # A close is not a screening decision. It used to be marked by
                        # tier='close_sweep', which is why `tier` survived at all; `stage` says it
                        # properly now, and screen_metrics still reads the old marker for history.
                        "stage": "exit",
                        "outcome": outcome,
                        "reason": reason,
                        "logged_at": time.time(),
                        "profile": trade.get("profile"),
                    }
                )
            )
        )
    except Exception:
        pass


def cmd_run_closes(args) -> dict:
    if not rank_strategies._verify_tastytrade_connection():
        return {"ok": False, "error": "tastytrade connection failed"}

    config = scanner._load_config()
    _capture_market_context(_date.today().isoformat())  # close-session morning VIX for the analysis
    positions = db_paper.cmd_get_open_positions(argparse.Namespace())["positions"]
    positions = [p for p in positions if _is_strat_test_book(p.get("profile"))]

    closed: list[dict] = []
    skipped: list[dict] = []
    stranded: list[dict] = []
    held: list[dict] = []
    # A skipped close must never be silent: bump the position's close_attempts, carry
    # the count on the skip record, and surface any position that has now missed more
    # than one daily sweep as "stranded" so the orchestrator's exit heartbeat can warn.
    retries = int(config.get("close_quote_retries", 1))
    retry_pause = float(config.get("close_quote_retry_seconds", 30))

    def _skip(trade: dict, reason: str) -> None:
        entry = {"order_id": trade["order_id"], "symbol": trade["symbol"], "reason": reason}
        try:
            rec = db_paper.cmd_record_close_failure(
                argparse.Namespace(
                    data=json.dumps(
                        {
                            "order_id": trade["order_id"],
                            "reason": reason,
                        }
                    )
                )
            )
            attempts = rec.get("close_attempts") if rec.get("ok") else None
        except Exception:
            attempts = None
        entry["close_attempts"] = attempts
        skipped.append(entry)
        if attempts is not None and attempts >= 2:
            stranded.append(entry)

    for trade in positions:
        order_id = trade["order_id"]
        symbol = trade["symbol"]
        try:
            quantity = trade["quantity"] or 1
            legs = json.loads(trade["legs_json"])
            leg_symbols = [leg["symbol"] for leg in legs]

            quote = scanner.fetch_quote_and_expirations(symbol)
            price = quote.get("price", 0.0) if quote.get("ok") else 0.0

            # Missing quotes get a short in-run retry window before the position is
            # skipped for the day -- a slow open is recoverable, a halt is not.
            leg_quotes = _leg_quotes_for_symbols(symbol, leg_symbols, price)
            for _ in range(retries):
                if leg_quotes is not None:
                    break
                time.sleep(retry_pause)
                leg_quotes = _leg_quotes_for_symbols(symbol, leg_symbols, price)
            if leg_quotes is None:
                _skip(trade, "leg_quotes_unavailable")
                continue

            full_quotes = {s: leg_quotes[s] for s in leg_symbols}

            # Multi-day strategies: the strategy's own management logic decides. hold is a
            # DECISION (the position keeps working), not a close failure. close_side (a
            # double-calendar leg stop) closes the whole position here: the paper book is
            # single-row/single-close accounting, and a front short past its delta stop
            # means the structure is broken -- the harness exits rather than trims. The
            # reason keeps the _close_all suffix so the two are distinguishable in the log.
            strategy_name = trade.get("strategy") or ""
            exit_reason = "close_window"
            manager = _MULTI_DAY.get(strategy_name)
            if manager is not None:
                if strategy_name == "double_calendar":
                    open_legs = db_paper.cmd_get_open_legs(argparse.Namespace(order_id=order_id)).get(
                        "legs", []
                    )
                    decision = manager.evaluate_position(
                        dict(trade), open_legs, full_quotes, config, is_first_check_of_day=True
                    )
                else:
                    decision = manager.evaluate_position(
                        dict(trade), full_quotes, config, is_first_check_of_day=True
                    )
                action = decision.get("action")
                if action == "hold":
                    held.append({"order_id": order_id, "symbol": symbol, "strategy": strategy_name})
                    _log_close_decision(trade, "hold", None)
                    continue
                exit_reason = decision.get("reason") or action
                if action == "close_side":
                    exit_reason = f"{exit_reason}_close_all"
            else:
                overnight_manager = _OVERNIGHT_MANAGED.get(strategy_name)
                if overnight_manager is not None:
                    if strategy_name == "broken_wing_butterfly":
                        decision = overnight_manager.evaluate_position(
                            dict(trade), full_quotes, config, is_first_check_of_day=True
                        )
                    else:
                        decision = overnight_manager.evaluate_position(dict(trade), full_quotes, config)
                    if decision.get("action") == "close_all":
                        exit_reason = decision.get("reason") or "close_all"

            exit_debit = scanner.compute_generic_exit_debit(legs, full_quotes)
            if exit_debit is None:
                _skip(trade, "exit_debit_unavailable")
                continue

            exit_costs = costs.apply_exit_costs(
                {"order": {"legs": legs}},
                [leg_quotes[s] for s in leg_symbols],
                quantity,
                config,
            )
            # Same legs list (action labels preserved from entry) -> this is the
            # same specific short contract(s)' IV, now, for a clean entry-vs-exit
            # crush comparison -- not a different strike/expiration's IV.
            exit_iv = _avg_sold_iv(legs, full_quotes)

            pnl = (trade["entry_credit"] - exit_debit) * 100

            close_result = db_paper.cmd_save_close(
                argparse.Namespace(
                    data=json.dumps(
                        {
                            "order_id": order_id,
                            "exit_debit": exit_debit,
                            "pnl": pnl,
                            "exit_cost": exit_costs["total_cost"],
                            "exit_slippage": exit_costs["slippage"],
                            "exit_iv": exit_iv,
                        }
                    )
                )
            )
            if not close_result.get("ok"):
                _skip(trade, f"save_close_failed: {close_result.get('error')}")
                continue

            _log_close_decision(trade, "closed", exit_reason)
            closed.append(
                {
                    "order_id": order_id,
                    "symbol": symbol,
                    "pnl": round(pnl, 2),
                    "exit_cost": exit_costs["total_cost"],
                    "reason": exit_reason,
                }
            )
        except Exception as exc:
            # Same discipline as cmd_run_entries: one position's unexpected failure
            # must not lose every other open position's already-accumulated closes.
            _skip(trade, f"unexpected_error: {exc}")

    # Once-per-day EOD report, written on the settlement (close) pass -- mirrors the MEIC paper
    # loop. Best-effort with a file-exists guard: a report failure must never fail the close
    # result the scheduled exit task depends on, and a manual re-run of run_closes won't clobber
    return {"ok": True, "closed": closed, "skipped": skipped, "stranded": stranded, "held": held}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_entries = sub.add_parser("run_entries")
    p_entries.add_argument("--date", required=True)

    sub.add_parser("run_closes")

    args = parser.parse_args()
    dispatch = {
        "run_entries": cmd_run_entries,
        "run_closes": cmd_run_closes,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
