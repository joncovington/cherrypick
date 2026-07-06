# Paper Trading

Simulates the iron fly strategy's entries, exits, and P&L throughout earnings season without ever touching the live tastytrade account's orders, positions, or buying power. Runs as a recurring `/loop`-driven process, using the exact same scanner (`src/scanner.py`) and screening criteria as live trading would, so paper results are a genuine test of the strategy's current calibration, not a separate toy implementation.

## Data separation (hard requirement)

Paper trades are stored in `data/paper_trades.db`, a **separate SQLite file** from `data/earnings_trades.db`, written and read exclusively through `src/db_paper.py` — a separate CLI module from `src/db.py`, not a `--paper` flag on the same one. There is no code path, table, or flag that can query both databases through one connection. This was a deliberate design choice (not the only option — a shared table with an `is_paper` column was considered and rejected) specifically so a future live-trading bug can never accidentally blend simulated and real P&L by a forgotten filter.

## What gets simulated, and how

**Entry (during `entry_window_start`–`entry_window_end` ET, e.g. 15:30–15:55)**:
1. Call `scanner.py get_candidates` for **today's date**, keep only rows with `earnings_timing == "After market close"` (their front-month/reaction-date logic is already correct for an afternoon-today entry).
2. Call `scanner.py get_candidates` for **tomorrow's date**, keep only rows with `earnings_timing == "Before market open"` (a report before tomorrow's open is still ahead of us this afternoon; a same-day BMO report already happened this morning and must not be re-entered).
3. Merge the two filtered candidate lists, then run `scanner.rank_candidates()` and `scanner.select_positions()` across the **combined** set — ranking/selecting on either date's raw output alone would miss half of today's real opportunity set. This merge-and-filter step happens at the orchestration layer (the loop command below), not inside `get_candidates` itself, to avoid destabilizing that function's already-tested single-date behavior.
4. For each symbol in the merged `selected` list not already open today (check `db_paper.py get_open_positions` first — a tick runs every 60s during the entry window and must not re-enter the same candidate twice): call `scanner.py get_order` to build the concrete iron fly (strikes, legs, credit), then `db_paper.py save_trade` using that order's `credit` as `entry_credit`. **No call to `tt.py execute_trade` at any point** — a real order preflight checks the live account's actual buying power/margin (confirmed live during testing: a correctly-built order was rejected purely on account funding, not order validity), which would incorrectly couple a simulated fill to the real account's financial state.

**Close (during `close_window_start` onward, e.g. 09:45 ET the next morning)**:
1. For each row in `db_paper.py get_open_positions`, call `tt.py get_option_chain --symbol <sym> --expiration <stored expiration> --include_quotes --strike_count 40 --around_price <stored short_strike>` to get fresh quotes for the position's four legs.
2. Match each leg by its stored strike (`short_strike`, `long_call_strike`, `long_put_strike`) against the returned chain entries.
3. Simulated exit debit (conservative, not mid-price): buy back both short legs at their live **ask**, sell both long legs at their live **bid** — `exit_debit = (short_call_ask + short_put_ask) - (long_call_bid + long_put_bid)`. Using ask-to-buy-back/bid-to-sell (not mid) reflects the real cost of crossing the spread to close promptly, which is what an actual close does.
4. `pnl = (entry_credit - exit_debit) * 100` (100 shares/contract, 1 contract per position per the fixed-size decision below).
5. `db_paper.py save_close`.

## Position sizing

**Fixed at 1 contract per iron fly**, regardless of price level or account size. This keeps every paper trade's P&L comparable across symbols and avoids introducing a simulated-account-balance concept that doesn't exist anywhere else in this project. `get_pnl_summary` reports both raw dollar P&L and can be manually normalized against `max_risk_per_trade_pct` if capital-efficiency comparisons are needed later.

## How many candidates get paper-traded

**All of `selected`** (already ranked and `max_concurrent_earnings_positions`/`correlation_block_list`-aware — see `docs/screening-criteria.md`), not just the single top-ranked name. This exercises the same portfolio-construction logic live trading would use, not a simplified single-trade-a-day version.

## Running it

Start a recurring loop against the tick command below:

```
/loop /paper-trade-tick
```

The command itself determines entry-vs-close-vs-idle behavior from the current ET time (see `.claude/commands/paper-trade-tick.md`) and reschedules its own next wakeup using the same interval table as `CLAUDE.md`'s live-trading design (short intervals during entry/close windows, long idle sleeps otherwise) — see that file's own wakeup table for the exact values, reused here rather than duplicated.

## Reporting

`python src/db_paper.py get_pnl_summary` returns `total_trades`, `total_pnl`, `avg_pnl`, `win_count`/`loss_count`/`win_rate`, `avg_win`/`avg_loss`, and the full closed-trade list — run it any time during earnings season to check cumulative performance without waiting for a formal end-of-season report.
