# earnings reference

Moved from the earnings CLAUDE.md, which points here.

## Tool Reference

All operations via `python -m cherrypick.earnings.tt <command>` (broker), `python -m
cherrypick.earnings.scanner <command>` (engine), `python -m cherrypick.earnings.strategies.<name>
<command>` (strategy). JSON to stdout.

| Command | Purpose |
|---|---|
| `python -m cherrypick.earnings.scanner get_calendar --date MM/DD/YYYY` | Tickers with earnings on this date |
| `python -m cherrypick.earnings.scanner get_iv_rv --symbol X` | IV/RV ratio from DoltHub |
| `python -m cherrypick.earnings.scanner get_winrate --symbol X [--lookback_quarters N]` | Historical winrate backtest |
| `python -m cherrypick.earnings.strategies.<name> get_candidates --date MM/DD/YYYY` | Full accept/reject scan with reasons, ranked candidates, selected (after cap/correlation) |
| `python -m cherrypick.earnings.strategies.<name> get_order --symbol X --earnings_date DATE --earnings_timing "..."` | Build a concrete order (strikes, legs, credit/debit) |
| `python -m cherrypick.earnings.tt secrets_status` / `secrets_set` | Check/store OAuth credentials |
| `python -m cherrypick.earnings.tt get_connection_status` | Verify OAuth session |
| `python -m cherrypick.earnings.tt get_quote --symbol X` | Live underlying price |
| `python -m cherrypick.earnings.tt get_option_chain --symbol X --expiration DATE --include_greeks --include_quotes --include_oi --include_volume` | Live chain for re-verification |
| `python -m cherrypick.earnings.tt get_market_metrics --symbol X` | Market cap for liquidity gates |
| `python -m cherrypick.earnings.tt get_account_info` | Buying power, NLV — live mode only; paper uses `available_capital_paper_mode`, never a real balance |
| `python -m cherrypick.earnings.tt execute_trade --order '<JSON>' [--live]` | Dry-run validate (no `--live`) or submit live. Both act on the designated account or refuse (`AccountNotDesignated`) — never the login's first account; an explicit `--account_number` must be the designated one |
| `python -m cherrypick.earnings.db get_open_positions` / `save_trade` / `save_close` / `get_open_legs` / `save_leg_close` / `log_scan` / `save_entry_review` / `get_entry_reviews` | Persistence (real trades) |
| `python -m cherrypick.earnings.db_paper` (same, plus `get_pnl_summary`) | Persistence (paper) |
| `python -m cherrypick.earnings.rank_strategies get_ranked_symbols --date MM/DD/YYYY` | All strategies × all symbols, each symbol's best, ranked; audit trail to `scan_log`. Step 4b. |
| `python -m cherrypick.earnings.symbol_watch refresh [--days 10]` | Manual/backfill forward-preview scan (the loop's `forward_scan` normally does this). Next `--days` **trading** days, pre-filtered to tastytrade's "Liquid Symbols" + "High Options Volume" + "tasty Earnings" watchlists (`tt.py get_watch_universe`); writes a metric vector and a recommended/near_miss/fail `tier` (`classify_tier`) to `symbol_watch.json`. Tier thresholds are the **loosest bar any live strategy applies**, read from their config (`_tier_thresholds`) — never a parallel ladder; `_TIER_DEFAULTS` only fills undeclared criteria. Perishable criteria (`_PERISHABLE_TIER_CRITERIA`: IV/RV, term structure, expected move, OI) can reach only `near_miss`, never `fail`; only price, winrate and average volume disqualify this early. **Display ranking only** — never a decision or an order, never run from the entry/exit loop. |
| `python -m cherrypick.earnings.paper_loop once` | **One managed-loop tick** (what the supervisor fires every 60s): phase from the clock, marks every open position, acts on what the gates allow, records `loop_iterations`. Single-writer lock (`core.looplock`: a live holder is never stolen however long it runs, a dead one is reclaimed at once); a tick that cannot get it exits OK with `status: busy` (the entry scan legitimately holds it ~25 min). The manual `strat_test_harness` takes the same lock and refuses with `busy` rather than run beside the loop. |
| `python -m cherrypick.earnings.paper_loop status` | Phase, last iteration, open count, lock state. No broker. |
| `python -m cherrypick.earnings.paper_loop record-break --key K [--date D] [--old X] [--new Y] [--note N]` | Record a `measurement_breaks` row: never pool across that date. |
| `python -m cherrypick.earnings.paper_loop settle-expired [--apply]` | Settle open positions whose legs already expired, at intrinsic against the expiration day's close (`front_expiry` for a calendar whose back month is listed). **Dry by default**; deliberately human-run — every one lands in the measurement. For backlogs; the loop settles by itself. |
| `python -m cherrypick.earnings.strat_test_harness run_entries --date MM/DD/YYYY` | **Strategy-testing only** (`docs/strategy-testing-plan.md`): a paper trade for **every** strategy clearing the screen on **every** viable symbol, into the strat_test books (`arm='strat_test:<strategy>'` per `strat_test_portfolio`) — forced sampling, since single-best selection would starve most strategies. Always paper regardless of `enable_live_trading`. |
| `python -m cherrypick.earnings.strat_test_harness run_closes` | Close every open strat_test position via `scanner.compute_generic_exit_debit`, cost-adjusted by `costs.py`. |
| `python -m cherrypick.earnings.screen_report [--mode live\|paper] [--profile X] [--strategy X] [--since YYYY-MM-DD] [--limit N] [--what-if REASON=THRESHOLD] [--cost-gate F] [--json]` | Why the screen rejected what it did (CLI over `screen_metrics.py`; reads `scan_log` only — safe mid-session). Funnel; reasons with a **sole-blocker** column (only sole blocks are rescuable by a threshold change); distance to the bar; co-firing gates; `_unverified` rejections as coverage gaps. **Cost-to-risk** per strategy with `--cost-gate` (default 0.05/0.10/0.15) — record-only, derived from `entry_cost`/`exit_cost`/`capital_at_risk`, the one counterfactual that may report P&L (those trades were taken). `--what-if` reports **counts and symbols only, never P&L**. Rows are classified first (`screen_metrics.classify`: four incompatible `scan_log` vocabularies) and exclusions are printed, never silent. `--json` is what the console's rejection card reads — one derivation, two renderers; the console must not re-derive it. |
| `python -m cherrypick.earnings.entry_replay --screen "winrate=off,iv_rv_ratio=near_miss"` | Read-side replay of a proposed entry screen over `entry_reviews` joined to outcomes (`docs/control-book-plan.md`, phase 3). `measured` carries P&L; `counterfactual` never does. |
| `python -m cherrypick.earnings.strategy_report [--mode live\|paper] [--profile X] [--strategy X] [--since YYYY-MM-DD]` | Per-strategy report: count vs 30/100 targets, win rate, profit factor, net expectancy, Sharpe, max drawdown, IV crush, regime coverage. `--mode` (default `paper`) picks the DB and the header says which; `--profile` defaults to the strat_test family (paper) / `default` (live). `--json [--since D] [--include-flagged]` prints the **verdict model** (`verdict.py`) the console's strategies page renders and must not re-derive: per control arm net, net ÷ capital at risk and expectancy with a fixed-seed 90% bootstrap **clustered by event** (symbol + open session — a trade count is not significance), cost drag, net by exit reason, worst-three-event concentration, stops that were green first, covariate terciles; advised twins only as paired twin − control. Rows a dated `FLAG_RULES` entry marks as recording a defect are excluded unless asked for, and all are listed so the history can mark them. |

## Config Options

Authoritative list: `config.example.json`; explanations: `docs/03-configuration.md`.

| Option | Purpose |
|---|---|
| `available_capital_paper_mode` | Simulated NLV for paper's `max_risk_per_trade_pct` cap — never the real broker balance. Size it to intended live capital, or the cap rejects every order. |
| `max_concurrent_earnings_positions` | Account-wide cap on overnight positions |
| `entry_window_start` / `entry_window_end` | Entry window, e.g. `15:30` / `15:55` ET |
| `close_window_start` | Close window start, e.g. `09:45` ET next morning |
| `correlation_block_list` | Sector/date groupings not to open together |
| `winrate_lookback_quarters` | Quarters for `compute_winrate()` **and** the move-dispersion gate in `atm_calendar`/`double_calendar` — not a winrate-only knob. 12 as of 2026-07-28. Thin names return a smaller `sample_size`, never an error. |
| `min_combined_open_interest` | Front-month chain-wide OI floor |
| `max_bid_ask_spread_pct` | Max ATM spread width (liquidity gate) |
| `require_weekly_options` | Hard-reject names without genuine weekly cadence |
| `min_market_cap` / `near_miss_min_market_cap` | Market cap floor via REST |
| `min_combined_option_volume` / `near_miss_min_combined_option_volume` | Daily contract volume floor |
| `symbol_screen` | Per-criterion strictness for `avg_volume`, `winrate`, `iv_rv_ratio`, `market_cap`, `combined_option_volume`: `"pass"`, `"near_miss"` or `"off"`; plus `move_tail` (`"off"`/`"veto"`, default off, record-only). Hard filters always apply. |
| `move_tail_multiple` | Multiple of a name's mean historical move that counts as a blowout for `move_tail_veto`; rejects only when `symbol_screen.move_tail` is `"veto"`. Default `2.0`. |
| `strat_test_portfolio` | `"per_strategy"` (default, `arm='strat_test:<strategy>'`) or `"combined"` (`strat_test`). `docs/strat-test-portfolios.md`. |
| `max_contracts_per_leg` | Hard per-leg ceiling for `sizing.py`'s code-enforced cap. |
| `tastytrade_costs` | Fee schedule for paper cost-adjusted P&L (`costs.py`): $1/contract open, $0 close, $10/leg cap, pass-throughs, and a slippage haircut off bid-ask. Checked 2026-04-06 at tastytrade.com/pricing — re-verify periodically. |

Strategy-specific options: `docs/05-strategies.md` and `config.example.json`.
