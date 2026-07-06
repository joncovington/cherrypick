Run one iteration of the paper-trading loop (see `docs/paper-trading.md` for the full design). This command is meant to be invoked repeatedly by `/loop /paper-trade-tick` — it determines what to do from the current ET time and reschedules its own next wakeup at the end.

**CRITICAL: this command must never call `python src/tt.py execute_trade` (with or without `--live`), and must never write to `data/earnings_trades.db` / call `src/db.py`'s write commands (`save_trade`, `save_close`, `log_scan`).** All persistence goes through `src/db_paper.py` exclusively. Reading live prices/chains via `tt.py`'s read-only commands (`get_quote`, `get_option_chain`) is fine and required.

## Steps

1. **Determine current ET time and today's date.**

2. **Entry window** (`entry_window_start`–`entry_window_end` from `config.json`, e.g. 15:30–15:55 ET):
   - Check `python src/db_paper.py get_open_positions` — if any position's symbol was already opened today (compare `opened_at`'s date to today), skip it in step below to avoid double-entry across repeated ticks within the same window.
   - Call `python src/scanner.py get_candidates --date <today>`; keep only candidates with `earnings_timing == "After market close"`.
   - Call `python src/scanner.py get_candidates --date <tomorrow>`; keep only candidates with `earnings_timing == "Before market open"`.
   - Merge both filtered lists. Re-run `scanner.rank_candidates()` and `scanner.select_positions()` (via a small inline Python snippet, or by combining the two `selected` lists and re-sorting by `composite_score` if calling the ranking functions directly isn't convenient from the CLI) across the **combined** set — do not just concatenate each date's own `selected`, since each was independently capped at `max_concurrent_earnings_positions` and the combined set must respect one shared cap.
   - For each symbol in the combined selection not already opened today: call `python src/scanner.py get_order --symbol <SYM> --earnings_date <date> --earnings_timing "<timing>"`. If `ok: false`, log the reason and skip (do not retry within the same tick). If `ok: true`, call `python src/db_paper.py save_trade` with `order_id` set to something unique and reproducible (e.g. `f"{symbol}-{expiration}-paper"`), `symbol`, `expiration`, `short_strike`, `long_call_strike`, `long_put_strike`, and `entry_credit` set to the order's `credit`.
   - Log every candidate evaluated this tick (entered, skipped-already-open, skipped-order-failed) via `python src/db_paper.py log_scan` — same "log every evaluation, not just entries" discipline as the live design.

3. **Close window** (`close_window_start` onward, e.g. 09:45 ET the next morning):
   - Call `python src/db_paper.py get_open_positions`.
   - For each open position: call `python src/tt.py get_option_chain --symbol <sym> --expiration <expiration> --include_quotes --strike_count 40 --around_price <short_strike>`. Match the returned entries to the position's `short_strike` (both call and put), `long_call_strike`, and `long_put_strike` by `strike_price`.
   - Compute `exit_debit = (short_call.ask + short_put.ask) - (long_call.bid + long_put.bid)`. If any leg's bid/ask is missing, log the gap and retry next tick rather than closing on incomplete data.
   - Compute `pnl = (entry_credit - exit_debit) * 100`.
   - Call `python src/db_paper.py save_close` with `order_id`, `exit_debit`, `pnl`.

4. **Outside both windows**: no action.

5. **Schedule the next wakeup**, reusing `CLAUDE.md`'s own interval table (this project has only one such table — do not duplicate it with different numbers here):

| Condition | Interval |
|---|---|
| Outside both windows, no open positions, next window >90 min away | **end loop** |
| Approaching entry window (30 min prior) | **300s** |
| Inside entry window | **60s** |
| Overnight, positions open, outside close window | **end loop / wake at close window start** |
| Inside close window with open positions | **60s** |
| Inside close window, no positions remain | **end loop** |

Use the longest applicable interval.
