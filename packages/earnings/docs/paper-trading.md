# Paper Trading

> _Part of the **cherrypick-earnings** package — [suite](../../../README.md) · [package README](../README.md) · [docs index](./README.md)._

Simulates every strategy's entries, exits, and P&L across a real earnings calendar without ever
touching the tastytrade account's orders, positions, or buying power. Earnings is paper-only. The scanner, ranking and selection logic are the
production ones, so paper results are a genuine test of the system's current calibration, not a
separate toy implementation. The unattended paper loop
(`paper_loop.py`, ticked every 60 s by the supervisor) runs the forced-sampling harness into the
`strat_test` books and manages its positions (see [Exit Strategy Guide](./10-exits.md)).

Two distinct paper-testing *programs* build on this same mechanism, and can run
concurrently since they write to isolated books — see [Strategy Testing Plan](./strategy-testing-plan.md)
for `/paper-start`'s forced-sampling program (`cherrypick/earnings/strat_test_harness.py`, per-strategy `strat_test` books)
versus `/paper-trading-start`'s one-shot production-ranking analysis (`rank_strategies.py`; it
places nothing, but logs its evaluation to `scan_log` and `entry_reviews`). What follows describes the underlying mechanism both rely on.

## Data separation (hard requirement)

Paper trades are stored in `paper_trades.db`, a **separate SQLite file** from
`earnings_trades.db` (the history of the orders placed before v0.12.0; nothing writes it) — both in the shared cherrypick data home (`~/.cherrypick/data/earnings` by
default, or `$EARNINGS_DATA_DIR`; resolved by `cherrypick/earnings/paths.py`) — written and read exclusively through `cherrypick/earnings/db_paper.py` — a separate
CLI module from `cherrypick/earnings/db.py`, not a `--paper` flag on the same one. There is no code path, table,
or flag that can query both databases through one connection. This was a deliberate design
choice (a shared table with an `is_paper` column was considered and rejected) specifically so a
bug can never blend simulated and real P&L by a forgotten filter.

Both databases' `trades` table is strategy-agnostic — a `strategy` column identifies which of
the seven opened it — so every strategy's paper results land in the same database,
distinguishable via `get_pnl_summary --strategy <name>` or its `by_strategy` breakdown, without a
schema change. An `arm` column (the flag is still `--profile`) does
the same for the forced-sampling test books (default `'default'` for the production loop; `'strat_test:<strategy>'` per-strategy, or `'strat_test'`
combined, for the forced-sampling program — see [Strat-Test Portfolios](./strat-test-portfolios.md)).

## What Gets Simulated, and How

The ranked path, summarized here (the supervisor's paper loop runs the forced-sampling harness instead):

**Entry (during `entry_window_start`–`entry_window_end` ET, e.g. `15:30`–`15:55`):**
1. `rank_strategies.py get_ranked_symbols` evaluates all seven strategies against the merged
   today-AMC/tomorrow-BMO calendar and picks each symbol's single best-ranked strategy.
2. Re-verify each selected symbol fresh (`rank_strategies.reverify_symbol()`) — confirm it's
   still accepted right before building an order, since the scan and the entry window aren't the
   same moment.
3. For each symbol not already opened today: call that strategy's own `get_order` to build the
   concrete order (strikes, legs, credit/debit), then `db_paper.py save_trade` using the order's
   `credit`/`debit` as `entry_credit` and its `legs` (JSON-encoded) as `legs_json`. For
   `double_calendar` specifically, also pass `legs` from `label_order_legs()` so each side can
   close independently later.

**No order is placed or previewed at any point** — a real order preflight would check the live
account's actual buying power/margin, which would incorrectly couple a simulated fill to whatever
happens to be sitting in the connected account.

**Close (a manual `/paper-start` cycle, during and after `close_window_start`, e.g. `09:45` ET the
next morning).** The supervisor's paper loop instead holds on the entry session, then applies the
managed rules in [Exit Strategy Guide](./10-exits.md).
1. For positions closing as a single unit (`legs_json`): fetch live quotes for every leg's exact
   option symbol, compute the exit debit via `scanner.compute_generic_exit_debit()` (buy back
   short legs at ask, sell long legs at bid — the conservative, cross-the-spread convention that
   reflects what an actual close costs), and `save_close`.
2. For `double_calendar` positions (`trade_legs`): `get_open_legs`, close remaining legs via the
   same conservative pricing, `save_leg_close` each, then `save_close` once every leg is shut.
3. `pnl = (entry_credit - exit_debit) * 100` per contract, summed across the position's actual
   sized quantity (not fixed at 1 contract — see Position Sizing below).

## Position Sizing

Sized the same way a real order would be — via `cherrypick/earnings/sizing.py`'s `compute_position_size`,
scaling contract quantity to keep max loss within `max_risk_per_trade_pct` of the paper-mode
capital basis (`available_capital_paper_mode`), capped by `max_contracts_per_leg`. This is not a
fixed 1-contract simulation, so a paper position's `capital_at_risk` field reflects what a real
trade at that size would actually risk.

## Cost-Adjusted P&L

Every paper trade also records `entry_cost`/`exit_cost` from `cherrypick/earnings/costs.py`'s tastytrade fee
model (commission, clearing, regulatory pass-throughs, and a slippage haircut off the quoted
bid-ask width) — kept separate from the raw `pnl` column, which always stays gross. A position entered
on or after 2026-10-26 is marked and closed at mid, with the haircut charged on both sides
(`costs.exit_costs`). An older position exits on the natural side (shorts at the ask, longs at the
bid), which already concedes the spread inside `pnl`, so `costs.natural_exit_costs` records only its
fees with `exit_slippage` 0 (see [measurement boundaries](./measurement-boundaries.md)). Cost-adjusted
expectancy is computed downstream by `strategy_metrics.py`, not baked into `pnl` itself. See
`CLAUDE.md`'s Database section and `docs/strategy-testing-plan.md` for why this separation
matters.

## How Many Candidates Get Paper-Traded

For the ranked path: only each symbol's single best-ranked strategy, after
`scanner.select_positions()`'s `max_concurrent_earnings_positions`/`correlation_block_list`
filtering, not a simplified single-trade-a-day version. The separate forced-sampling program
(`strat_test_harness.py`/`/paper-start`) intentionally trades *every* candidate that clears the
screen under *every* qualifying strategy instead, specifically to avoid starving strategies that
rarely win the single-best-per-symbol comparison — see
[Strategy Testing Plan](./strategy-testing-plan.md).

## Running It

The supervisor's `earnings-paper` job runs the paper book on its own. By hand, use whichever slash
command matches what you're trying to do:

```
/paper-start              # forced-sampling strategy test, one full day's open-to-close cycle
/paper-trading-start      # one-shot production-ranking analysis, places nothing
```

## Reporting

```bash
python -m cherrypick.earnings.db_paper get_pnl_summary [--strategy iron_fly] [--profile strat_test]
```

Returns `total_trades`, `total_pnl`, `avg_pnl`, `win_count`/`loss_count`/`win_rate`,
`avg_win`/`avg_loss`, a `by_strategy`/`by_profile` breakdown, and the full closed-trade list —
run it any time to check cumulative raw P&L. For cost-adjusted expectancy, win rate, profit
factor, Sharpe, drawdown, and IV-crush metrics, use
`python -m cherrypick.earnings.strategy_report` (or the console's Earnings page) instead, which reads the
richer `strategy_metrics.py` computations on top of the same database.
