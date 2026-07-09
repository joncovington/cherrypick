# Paper Trading

Simulates every strategy's entries, exits, and P&L across a real earnings calendar without ever
touching the live tastytrade account's orders, positions, or buying power. **There is no
separate paper-trading loop or command distinct from the real one** — `CLAUDE.md`'s Loop Steps
are the single definition, and Step 0 (`paper_mode = not config.get("enable_live_trading")`)
branches persistence and order handling accordingly. Since `enable_live_trading` defaults to
`false`, running the loop at all is paper mode by default; nothing changes about the scanner,
ranking, or selection logic between modes, so paper results are a genuine test of the system's
current calibration, not a separate toy implementation.

Two distinct paper-testing *programs* build on this same paper/live split, and can run
concurrently since they write to isolated books — see [Strategy Testing Plan](./strategy-testing-plan.md)
for `/paper-start`'s forced-sampling program (`src/strategy_test_runner.py`, `profile='strat_test'`)
versus `/paper-trading-start`'s one-shot production-ranking analysis (`rank_strategies.py`, no
persistence at all). What follows describes the underlying mechanism both rely on.

## Data separation (hard requirement)

Paper trades are stored in `data/paper_trades.db`, a **separate SQLite file** from
`data/earnings_trades.db`, written and read exclusively through `src/db_paper.py` — a separate
CLI module from `src/db.py`, not a `--paper` flag on the same one. There is no code path, table,
or flag that can query both databases through one connection. This was a deliberate design
choice (a shared table with an `is_paper` column was considered and rejected) specifically so a
future live-trading bug can never accidentally blend simulated and real P&L by a forgotten
filter.

Both databases' `trades` table is strategy-agnostic — a `strategy` column identifies which of
the seven opened it — so every strategy's paper results land in the same database,
distinguishable via `get_pnl_summary --strategy <name>` or its `by_strategy` breakdown, without a
schema change. A `profile` column does the same for named risk profiles / test books (default
`'default'`, `'strat_test'` for the forced-sampling program).

## What Gets Simulated, and How

See `CLAUDE.md`'s Loop Steps for the authoritative, single copy of this logic — summarized here:

**Entry (during `entry_window_start`–`entry_window_end` ET, e.g. `15:30`–`15:55`):**
1. `rank_strategies.py get_ranked_symbols` evaluates all seven strategies against the merged
   today-AMC/tomorrow-BMO calendar and picks each symbol's single best-ranked strategy.
2. Re-verify each selected symbol fresh (`rank_strategies.reverify_symbol()`) — confirm it's
   still Tier 1/2 right before building an order, since the scan and the entry window aren't the
   same moment.
3. For each symbol not already opened today: call that strategy's own `get_order` to build the
   concrete order (strikes, legs, credit/debit), then `db_paper.py save_trade` using the order's
   `credit`/`debit` as `entry_credit` and its `legs` (JSON-encoded) as `legs_json`. For
   `double_calendar` specifically, also pass `legs` from `label_order_legs()` so each side can
   close independently later.

**No call to `tt.py execute_trade` at any point in paper mode** — a real order preflight checks
the live account's actual buying power/margin, which would incorrectly couple a simulated fill
to whatever happens to be sitting in the connected account.

**Close (during and after `close_window_start`, e.g. `09:45` ET the next morning):**
1. For positions closing as a single unit (`legs_json`): fetch live quotes for every leg's exact
   option symbol, compute the exit debit via `scanner.compute_generic_exit_debit()` (buy back
   short legs at ask, sell long legs at bid — the conservative, cross-the-spread convention that
   reflects what an actual close costs), and `save_close`.
2. For `double_calendar` positions (`trade_legs`): `get_open_legs`, close remaining legs via the
   same conservative pricing, `save_leg_close` each, then `save_close` once every leg is shut.
3. `pnl = (entry_credit - exit_debit) * 100` per contract, summed across the position's actual
   sized quantity (not fixed at 1 contract — see Position Sizing below).

## Position Sizing

Sized the same way a real order would be — via `src/sizing.py`'s `compute_position_size`,
scaling contract quantity to keep max loss within `max_risk_per_trade_pct` of the paper-mode
capital basis (`available_capital_paper_mode`), capped by `max_contracts_per_leg`. This is not a
fixed 1-contract simulation; paper-mode sizing exercises the exact same risk-cap logic live
trading would use, so a paper position's `capital_at_risk` field reflects what a real trade at
that size would actually risk.

## Cost-Adjusted P&L

Every paper trade also records `entry_cost`/`exit_cost` from `src/costs.py`'s tastytrade fee
model (commission, clearing, regulatory pass-throughs, and a slippage haircut off the quoted
bid-ask width) — kept separate from the raw `pnl` column, which always stays gross. Cost-adjusted
expectancy is computed downstream by `strategy_metrics.py`, not baked into `pnl` itself. See
`CLAUDE.md`'s Database section and `docs/strategy-testing-plan.md` for why this separation
matters.

## How Many Candidates Get Paper-Traded

For the production paper/live loop: only each symbol's single best-ranked strategy, after
`scanner.select_positions()`'s `max_concurrent_earnings_positions`/`correlation_block_list`
filtering — the same portfolio-construction logic live trading would use, not a simplified
single-trade-a-day version. The separate forced-sampling program
(`strategy_test_runner.py`/`/paper-start`) intentionally trades *every* Tier 1/2 candidate under
*every* qualifying strategy instead, specifically to avoid starving strategies that rarely win
the single-best-per-symbol comparison — see [Strategy Testing Plan](./strategy-testing-plan.md).

## Running It

Confirm `enable_live_trading` is `false` (or absent) in `config/config.json` — this is the only
switch between paper and live mode, and it defaults to paper. Then use whichever slash command
matches what you're trying to do:

```
/paper-start              # forced-sampling strategy test, one full day's open-to-close cycle
/paper-trading-start      # one-shot production-ranking analysis, no persistence
/earnings-start           # the actual continuous loop, following CLAUDE.md's Loop Steps
```

**Switching to live trading later** requires no loop changes — set `enable_live_trading: true`
in `config/config.json` and every subsequent iteration automatically persists via `db.py` and
submits real orders via `tt.py execute_trade --live` instead.

## Reporting

```bash
python src/db_paper.py get_pnl_summary [--strategy iron_fly] [--profile strat_test]
```

Returns `total_trades`, `total_pnl`, `avg_pnl`, `win_count`/`loss_count`/`win_rate`,
`avg_win`/`avg_loss`, a `by_strategy`/`by_profile` breakdown, and the full closed-trade list —
run it any time to check cumulative raw P&L. For cost-adjusted expectancy, win rate, profit
factor, Sharpe, drawdown, and IV-crush metrics, use
`python src/strategy_report.py` or `python src/strategy_dashboard.py` instead, which read the
richer `strategy_metrics.py` computations on top of the same database.
