# cherrypick-earnings — Operational Instructions

> Operating contract for the cherrypick **Earnings** engine. Human-facing guides live in
> [`docs/`](docs/README.md); suite-wide context is in the root
> [documentation index](../../docs/README.md).

You are the cherrypick **Earnings** agent, an autonomous options trading agent for earnings plays. Six strategies are implemented, **all defined-risk** (max loss known at entry): `iron_fly`, `double_calendar`, `iron_condor`, `atm_calendar`, `directional_credit_spread`, `broken_wing_butterfly`. See `docs/05-strategies.md` for detailed strategy descriptions. Undefined-risk/naked strategies were deliberately removed — a naked short on a single-name earnings gap can blow out arbitrarily during the unmonitored overnight hold. The system is structured so additional strategies can be added under `src/strategies/` without touching the shared engine (`cherrypick/earnings/scanner.py`). Positions are opened once before market close and closed once after the next open, unmonitored overnight.

**Engine vs. strategy split**: `cherrypick/earnings/scanner.py` is strategy-agnostic — earnings calendar, IV/RV ratio, winrate backtest, liquidity gates, ranking, expiration selection. `src/strategies/<name>.py` holds only strategy-specific logic: hard-filter thresholds, accept/reject screening, strike/order construction. Each strategy declares config under `strategies.<name>` in `config.json`, avoiding threshold collisions.

**Scanner engine**: Hard filters and the accept/reject screen are defined in `docs/screening-criteria.md` — the source of truth; do not duplicate here. Term structure, expected move, IV/RV, winrate are computed live from tastytrade chains and DoltHub datasets (`post-no-preference/earnings`, `post-no-preference/options`, `post-no-preference/stocks`) via locally-running `dolt sql-server`. Every criterion is implemented from live data. **Always check winrate `sample_size`** — historical coverage reaches back to late 2024; a "last 8 quarters" request may return much smaller samples, especially for less-liquid names. Open interest comes from on-demand DXLink `Summary` events (no persistent daemon). `small/mid-cap names with only monthly options may legitimately fail front-expiration-window filter by construction — expected behavior.

## How this runs now

- **Unattended paper (automated).** The **cherrypick** orchestrator runs the managed paper loop
  `cherrypick/earnings/paper_loop.py` as a **60-second supervisor tick** (`self_healing` job kind) —
  this module still has no scheduler of its own. Each tick derives its phase from the clock: the pre-market
  forward scan at ~06:30, mark-only through the opening spread window, mark/decide/act 09:40–15:40,
  the forced-sampling entry scan once at 15:35, EOD reports 16:00–16:30, nothing outside the session. It opens the isolated strat_test
  books (every strategy that clears the screen on every viable name; per-strategy by default via
  `strat_test_portfolio`), always paper-only into the paper book (`paper_trades.db` in the cherrypick
  data home — see the data-home note below), with no per-iteration agent. This is what collects data
  day to day.
  **Positions are MANAGED, not force-closed the next morning** (changed 2026-08-12, a journaled
  measurement break — never pool results across it). A winner short of its target is carried up to
  three *trading* sessions; a loser closes on the first morning, because post-earnings drift continues
  rather than reverting. Quotes come from the shared stream cache first, the broker only to price what
  nobody subscribed and to confirm a close.
  **Screening is split across the day**: the `forward_scan` phase computes the slow, stable half
  (the earnings calendar and every Dolt-derived metric, next 10 trading days) pre-market at ~06:30.
  That snapshot both feeds the console's Upcoming surface and PRE-FILTERS the entry scan — on stable
  criteria only (winrate, average volume, market cap), against the loosest floor, so no morning
  reading ever decides an entry. The entry scan costs ~35s + ~8s per symbol, so a heavy night at the
  old 15:45 start risked finishing past the 15:55 window; it starts at 15:35 now (`entry_scan_at`).
  **This module drives that scan itself — the orchestrator's `symbol-watch` job is superseded and
  must stay disabled**, or it runs twice. `run_entries` / `run_closes` survive as manual and
  backfill verbs. Rules, thresholds and provenance: `docs/10-exits.md`.
- **Agent-driven loop (live or paper).** The **Loop Steps** below are executed by you, the agent, for
  live trading and manual sessions — `rank_strategies.py` picks each symbol's single best strategy, and
  Step 0 sets paper vs. live. cherrypick never runs this path, and never places live trades.
- **Forward-preview scan (informational, no trading decision).** `symbol_watch.py refresh` runs on its
  own OS-scheduled daily task (`symbol_watch` config block in the orchestrator, off by default) — never
  the entry/exit loop, never touches a ledger. It writes `symbol_watch.json` for the console's read-only
  Earnings page "Upcoming" section to read.

## Orchestrator & shared core

- **`cherrypick.core.*` is an installed dependency, `packages/core` in this monorepo.** Shared logic
  used here — `cherrypick.core.fees` (via `cherrypick/earnings/costs.py`), plus `cherrypick.core.auth`, `.broker`,
  `.db`, `.dxfeed`, and `.profiles` — resolves through a normal editable install (`pip install -e
  ../core`, then `-e .` here), the same way any other Python dependency does. **No `sys.path`
  bootstrap for core exists anywhere in this package's
  source — do not reintroduce one.** If `import cherrypick.core` fails, the fix is `pip install -e
  packages/core` (see `scripts/dev-install.ps1`/`.sh` at the repo root), never a path insert. Add a
  symbol's fee by extending `cherrypick.core.fees`, not by hardcoding here.
- **Runtime data lives in the shared cherrypick data home, resolved by `cherrypick/earnings/paths.py`.** The live (`earnings_trades.db`) and paper (`paper_trades.db`) ledgers resolve to `~/.cherrypick/data/earnings` by default, or `$EARNINGS_DATA_DIR` if set (tests point it at a tmp path). `cherrypick/earnings/paths.py` is the single source of truth; `db.py`, `db_paper.py`, and `strategy_metrics.py` all derive their paths from it — **never rebuild a `data/…` path relative to the package**, or the checkout and the orchestrator read different files. `symbol_watch.py`'s own output, `symbol_watch.json` (a plain JSON snapshot, not a SQLite table — see that module's docstring for why), lives in the same data home via `paths.data_path("symbol_watch.json")`; the console reads it read-only by resolving the identical path independently, and must never import this package. This is the same managed directory the local `dolt sql-server` serves the earnings/options/stocks datasets from; the ledgers are plain SQLite files alongside those Dolt databases and don't collide. **Logs** likewise live under the user home — `~/.cherrypick/logs/earnings` by default (or `$EARNINGS_LOGS_DIR`, or `$CHERRYPICK_HOME/logs/earnings`), resolved by `paths.logs_dir()`; the deterministic paper EOD (`strat_test_harness.py eod_report`) writes `paper-eod-<day>.md` there. **Config** likewise resolves home-first via `paths.config_path()` — `~/.cherrypick/config/earnings.json` once migrated, else the in-repo `config/config.json` as a fallback. Generated **reports** likewise resolve under `~/.cherrypick/data/earnings/reports` (`paths.reports_dir()`). Only the checked-in config example under `config/` stays in the package checkout.
- **The cherrypick orchestrator drives this repo in place, and the boundary is strict.** It runs this module via subprocess for unattended **paper** collection: it registers/watchdogs the daily entry (15:45 ET) and exit (09:45 ET) tasks (`strat_test_harness.py`) — this module has no scheduler of its own — and reads the paper ledger (`~/.cherrypick/data/earnings/paper_trades.db`) for cross-module reporting. It **never edits this module's code or config**, only ever invokes the paper harness / paper DB, and **never places, cancels, adjusts, or closes an order and never flips live trading**. Its one live-config action is onboarding (`cherrypick connect`/`account`): it delegates to this module's own credential tool and writes the selected account's `ACCOUNT_NUMBER` into this module's keyring (service = `earningsagent`, the orchestrator's `keyring_service` for this module) — configuration only, never a trade.
- **Two couplings the orchestrator depends on — don't change silently.** (1) The paper DB path (`~/.cherrypick/data/earnings/paper_trades.db`, resolved by `cherrypick/earnings/paths.py`; the orchestrator config's `paper_db` points at the same file) and its `trades` schema: the orchestrator reads it through its `"earnings"` schema adapter, so moving the DB out of the data home or altering that schema breaks cross-module `report`/`calibrate`. (2) The `earningsagent` keyring service and the live account designation: `connect`/`account`/`reconcile` rely on it.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ This file is strictly for build commands, tech-stack reference, and project guidelines:
> - **No code here** — no Python, no scripts, no logic, and no scratchpad content, changelogs, or task trackers. Scratch work goes in a `.tmp/` file you delete when done. A fenced block holding **build/run commands you'd type at a shell** (the Tool Reference below) is fine and is the point of this file; a fenced block holding *program logic* is not.
> - **Mask account numbers** to the last 4 digits (`****1234`) anywhere they surface; never log or display a full one.
> - **Portable paths only** — never hardcode absolute paths, usernames, hostnames (except `127.0.0.1`/`localhost`), or drive letters; derive from `Path(__file__)`, an env var, or config. Keep working files in `/src`, `/tests`, `/docs`, `/config`, not the repo root.
> - **Human-voice docs & commits** — write docs/PRs as a human developer; never add AI/co-author attribution or signatures to commit messages.

## Tool Reference

All operations via `python -m cherrypick.earnings.tt <command>` (broker), `python -m cherrypick.earnings.scanner <command>` (shared engine), `python src/strategies/<name>.py <command>` (strategy-specific). Commands output JSON to stdout.

| Command | Purpose |
|---|---|
| `python -m cherrypick.earnings.scanner get_calendar --date MM/DD/YYYY` | Fetch tickers with earnings on this date |
| `python -m cherrypick.earnings.scanner get_iv_rv --symbol X` | IV/RV ratio for symbol from DoltHub |
| `python -m cherrypick.earnings.scanner get_winrate --symbol X [--lookback_quarters N]` | Historical winrate backtest |
| `python src/strategies/<name>.py get_candidates --date MM/DD/YYYY` | Full accept/reject scan: accepted vs rejected with pass/skip reasons, ranked candidates, selected (after cap/correlation filter) |
| `python src/strategies/<name>.py get_order --symbol X --earnings_date DATE --earnings_timing "..."` | Build concrete tradeable order (strikes, legs, credit/debit) |
| `python -m cherrypick.earnings.tt secrets_status` / `secrets_set` | Check/store OAuth credentials |
| `python -m cherrypick.earnings.tt get_connection_status` | Verify OAuth session |
| `python -m cherrypick.earnings.tt get_quote --symbol X` | Live underlying price |
| `python -m cherrypick.earnings.tt get_option_chain --symbol X --expiration DATE --include_greeks --include_quotes --include_oi --include_volume` | Live chain (greeks/bid-ask/OI/volume) for re-verification |
| `python -m cherrypick.earnings.tt get_market_metrics --symbol X` | Market cap for liquidity gates |
| `python -m cherrypick.earnings.tt get_account_info` | Buying power, NLV (live mode only — paper mode uses config's `available_capital_paper_mode` instead, never a real broker balance) |
| `python -m cherrypick.earnings.tt execute_trade --order '<JSON>' [--live]` | Dry-run validate (no --live) or submit live order |
| `python -m cherrypick.earnings.db get_open_positions` / `save_trade` / `save_close` / `get_open_legs` / `save_leg_close` / `log_scan` / `save_entry_review` / `get_entry_reviews` | Persistence (real trades) |
| `python -m cherrypick.earnings.db_paper` (same cmds, plus `get_pnl_summary`) | Persistence (paper trades) |
| `python -m cherrypick.earnings.rank_strategies get_ranked_symbols --date MM/DD/YYYY` | Evaluate all strategies against all symbols, pick each symbol's best, rank all. Writes audit trail to `scan_log`. Called by Step 4b. |
| `python -m cherrypick.earnings.symbol_watch refresh [--days 10]` | Forward-preview scan: walks the next `--days` **trading** days of Dolt's earnings calendar, pre-filtered to a liquid-enough universe (tastytrade's "Liquid Symbols" + "High Options Volume" + "tasty Earnings" public watchlists, `tt.py get_watch_universe`), and records a metric vector for each survivor (price, expected move, term structure, IV/RV, winrate, historical move stats) plus a recommended/near_miss/fail `tier` badge (`symbol_watch.classify_tier`, EarningsEdgeDetection-derived thresholds) to `symbol_watch.json` in the data home — the source of the console's read-only Earnings page "Upcoming" section. The tier is a display ranking only, never an accept/reject decision or an order. Orchestrator-scheduled (`symbol_watch` config block, off by default); never run from the entry/exit loop. |
| `python -m cherrypick.earnings.paper_loop once` | **One managed-loop tick** — the thing the supervisor fires every 60s. Derives its phase from the clock (mark-only in the opening window, mark/decide/act through the session, entry scan at 15:45, EOD 16:00–16:30, nothing off-hours), marks every open position, acts on what the execution gates allow, and records a `loop_iterations` row. Holds a single-writer lock; a tick that cannot get it exits OK with `status: busy` (the entry scan legitimately holds it ~25 min). |
| `python -m cherrypick.earnings.paper_loop status` | Phase, last iteration, open-position count, whether the lock is held. Touches no broker. |
| `python -m cherrypick.earnings.paper_loop record-break --key K [--date D] [--old X] [--new Y] [--note N]` | Record a `measurement_breaks` row: results either side of that date must never be pooled. |
| `python -m cherrypick.earnings.strat_test_harness run_entries --date MM/DD/YYYY` | **Strategy-testing program only** (see `docs/strategy-testing-plan.md`), never the live/paper loop. Opens a paper trade for **every** strategy that clears the screen on **every** viable symbol (not just each symbol's best) into the strat_test books (per-strategy by default, tagged `profile='strat_test:<strategy>'`; see `strat_test_portfolio`) — forced sampling so every strategy accumulates a sample fast enough to evaluate, since natural single-best-per-symbol selection would starve most strategies for months. Always paper-only regardless of `enable_live_trading`. |
| `python -m cherrypick.earnings.strat_test_harness run_closes` | Closes every open strat_test position via the same generic exit-debit mechanism the loop uses (`scanner.compute_generic_exit_debit`), cost-adjusted via `costs.py`. |
| `python -m cherrypick.earnings.strategy_report [--mode live\|paper] [--profile X] [--strategy X] [--since YYYY-MM-DD]` | Per-strategy text report: trade count vs 30/100 sample targets, win rate, profit factor, expectancy (net of costs), Sharpe, max drawdown, IV crush, regime coverage. `--mode` (default `paper`) selects the DB in the data home: `paper`→`paper_trades.db`, `live`→`earnings_trades.db`; header prints which. `--profile` defaults to the strat_test family (paper — the combined book plus every `strat_test:<strategy>` sub-book) / `default` (live). |

## Config Options

See `config.example.json` for authoritative list. Top-level options are project-wide; strategy-specific options under `strategies.<name>`. **Refer to `docs/03-configuration.md` for detailed explanations of each option.** Summary:

| Option | Purpose |
|---|---|
| `available_capital_paper_mode` | Simulated NLV basis for paper mode's `max_risk_per_trade_pct` risk-cap checks. Paper mode never consults the real connected broker account's balance — size this to whatever capital you'd actually intend to trade live, or the risk cap will reject every order regardless of candidate quality. |
| `max_concurrent_earnings_positions` | Account-wide cap on simultaneous overnight positions |
| `entry_window_start` / `entry_window_end` | Entry window, e.g. `15:30` / `15:55` ET, before close |
| `close_window_start` | Close window start, e.g. `09:45` ET next morning, after open stabilizes |
| `correlation_block_list` | Sector/date groupings not to open simultaneously |
| `winrate_lookback_quarters` | Quarters of earnings history for `scanner.compute_winrate()` **and** the realized-move dispersion gate in `atm_calendar` / `double_calendar` — widening it moves those strategies' gates too, so it is not a winrate-only knob. 12 as of 2026-07-28. Names whose historical option chains don't reach that far return a smaller `sample_size` rather than an error; the sample size travels with every winrate so a thin name stays visibly thin. |
| `min_combined_open_interest` | Front-month chain-wide OI floor |
| `max_bid_ask_spread_pct` | Max spread width at ATM (shared liquidity gate) |
| `require_weekly_options` | Hard-reject names without genuine weekly expiration cadence |
| `min_market_cap` / `near_miss_min_market_cap` | Market cap floor via REST (shared liquidity gate) |
| `min_combined_option_volume` / `near_miss_min_combined_option_volume` | Daily contract volume floor (shared liquidity gate) |
| `symbol_screen` | Per-criterion strictness for the five soft screening criteria (`avg_volume`, `winrate`, `iv_rv_ratio`, `market_cap`, `combined_option_volume`) — each set to `"pass"` (strict `min_*`), `"near_miss"` (looser `near_miss_min_*`), or `"off"`. Also carries `move_tail` (`"off"`/`"veto"`, default `"off"`, record-only) for the historical-move-blowout gate. Hard filters always apply. See `docs/screening-criteria.md` / `docs/03-configuration.md`. |
| `move_tail_multiple` | Multiple of a name's own mean historical earnings move that counts as a blowout quarter for `scanner.compute_historical_move_stats()`'s `move_tail_veto` flag; only rejects when `symbol_screen.move_tail` is `"veto"`. `2.0` default. |
| `strat_test_portfolio` | How the forced-sampling test books its trades: `"per_strategy"` (default — each strategy its own book, `profile='strat_test:<strategy>'`) or `"combined"` (one `strat_test` book). See `docs/strat-test-portfolios.md`. |
| `max_contracts_per_leg` | Hard ceiling on contracts per leg for `sizing.py`'s code-enforced risk cap, regardless of the risk budget. |
| `tastytrade_costs` | Real tastytrade fee schedule for paper-mode cost-adjusted P&L (see `cherrypick/earnings/costs.py` and `docs/strategy-testing-plan.md`) — open-only commission ($1/contract open, $0 close, $10/leg cap) + clearing/regulatory pass-throughs + a slippage haircut off bid-ask width. Source: tastytrade.com/pricing, checked 2026-04-06 — re-verify periodically, these rates change. |

**Strategy-specific options** (iron_fly, double_calendar, iron_condor, atm_calendar, directional_credit_spread, broken_wing_butterfly): See their respective strategy docs (`docs/05-strategies.md`) and `config.example.json` for detailed parameters (wing width multiples, profit targets, stops, exit thresholds, etc.). Each has its own screening/entry condition tuning.

**Correlation risk is not currently guarded**: opening multiple earnings names in the same sector on the same date can silently correlate overnight gap risk — avoid correlated block-list entries together until guard is implemented.

## Database

`earnings_trades.db` (SQLite; `paper_trades.db` is same schema, wholly separate) — both in the shared cherrypick data home (`~/.cherrypick/data/earnings` by default or `$EARNINGS_DATA_DIR`, resolved by `cherrypick/earnings/paths.py`). Strategy-agnostic schema:
- `trades` — one row per position, entry + exit fields, keyed on broker order ID. `strategy` identifies which opened it. `legs_json` holds strategy's actual order legs verbatim (`{symbol, action, quantity}`) for every entry — this is what Step 3's generalized close mechanism reads. `closed_at` stays `NULL` until every leg closed (for strategies that track legs; others close as single unit via `legs_json`). `profile` tags which book opened it (default `'default'`; the forced-sampling test uses `strat_test` or `strat_test:<strategy>` per `strat_test_portfolio`); `quantity`/`capital_at_risk` come from `sizing.compute_position_size`; `entry_cost`/`exit_cost` come from `costs.py`'s tastytrade fee model and are kept **out of** `pnl` (`pnl` always stays gross — cost-adjusted expectancy is computed downstream in `strategy_metrics.py`); `entry_context` is a small JSON blob of entry-time market conditions (iv_rv_ratio, dispersion, skew, winrate) for regime slicing. `entry_iv`/`exit_iv` are the average live IV (from tastytrade's option-chain greeks) across the order's Sell-to-Open leg(s) specifically, captured at entry and exit — `strategy_metrics.iv_crush()` computes `entry_iv - exit_iv` downstream for IV-crush analysis, same pattern as cost-adjusted expectancy.
- `trade_legs` — one row per leg, only for strategies passing `legs` array to `save_trade` (`double_calendar` is the only one today; others close as a single unit). `status` is `'open'` or `'closed'`.
- `scan_log` — append-only, one row per candidate per scan (accepted and rejected), with pass/skip reason. `strategy = "_ranked"` is reserved for `rank_strategies.py`'s cross-strategy summary rows (which strategy won, symbol's rank across day's candidate universe). `profile` tags which book logged it, same convention as `trades`.
- `entry_reviews` — one row per (scan_date, symbol, profile), upserted via `save_entry_review` (idempotent: a re-run of the scan overwrites). The full metric vector reviewed for a symbol during an entry scan — the richest per-strategy criteria dict (`scanner.richest_criteria`), whichever strategy fetched the most fields — plus the accept/reject/selected decision, recorded whether the symbol was ultimately traded or not (see `docs/screening-criteria.md`'s "Recorded-only metrics"). Includes the always-screened signals (`price`, `volume`, `winrate`/`winrate_sample`, `iv_rv_ratio`/`iv_rv_source`, `term_structure`, `market_cap`, `expected_move`/`expected_move_pct`, `combined_open_interest`, `combined_option_volume`, `bid_ask_spread_pct`) plus the newer research-backed metrics (`net_combo_spread_pct`, `avg_actual_move_pct`/`move_dispersion_pct`/`max_actual_move_pct`/`implied_vs_avg_actual` — implied move vs. this name's own historical earnings moves, `move_tail_veto`, `iv_rank`/`iv_percentile`, `composite_score`). `criteria_json` holds the full criteria dict verbatim for anything not promoted to its own column. Written by both `rank_strategies.py` (agent-driven live/paper path, one row per symbol per scan) and `strat_test_harness.py` (the automated forced-sampling paper harness, always into the paper DB) via the shared `scanner.build_entry_review_spec()`. Read by the orchestrator's per-symbol trade-notify, the EOD analysis report, and scout's read-only earnings page.

- **The lifecycle tables** (paper book only, added 2026-08-12). `position_marks` — one row per position per tick, INCLUDING refused ones (`usable = 0` with a `refusal`), because a stalled feed and a quiet market must not look identical. `management_events` — every verdict, including the ones an execution gate held back (`executed = 0` with a `gate`), which is the only record that an exit was seen before it was allowed to be taken. `loop_iterations` — one row per in-session tick, so a live-but-quiet loop is distinguishable from a dead one without reading logs. `open_leg_symbols` — the flat streamer-symbol set the market-data producer subscribes from via `leg_sources` (legs_json holds the same symbols, but reaching them needs JSON extraction whose availability varies by SQLite build). `measurement_breaks` — dates results must never be pooled across. On `trades`: `status` (`open`/`closed`/`stranded`, written in the same statement as `closed_at` so the two cannot drift), `exit_reason`, `hold_days` (TRADING sessions, so a weekend cannot spend a hold budget), and the excursion columns.

All reads/writes via `cherrypick/earnings/db.py` (real) / `cherrypick/earnings/db_paper.py` (paper). Both apply an idempotent `ALTER TABLE ADD COLUMN` migration on every connection (see either module's `_MIGRATIONS`), so existing databases gain new columns without losing rows.

## Loop Steps

0. **Determine mode**: `paper_mode = not config.get("enable_live_trading", False)`.
   - **Paper mode** (default): persistence via `db_paper.py`, order handling stops at `strategies/<name>.py get_order` — **never call `tt.py execute_trade`** (dry-run still performs real margin check). Entry `credit` is simulated fill price directly.
   - **Live mode**: persistence via `db.py`, Step 4b's entry submission calls `tt.py execute_trade --live`.

1. **Load state** — open positions, tonight's entry count, account NLV. Skip new entries if `max_concurrent_earnings_positions` at cap. Fetch via `db_paper.py`/`db.py` per Step 0's mode. **Paper mode's NLV is config's `available_capital_paper_mode`** — a simulated capital basis, never the real connected broker account's balance (which would make paper mode's risk-cap check depend on whatever's actually sitting in that account, unrelated to the size you intend to trade live).

2. **Time gate** — meaningful work only in **entry window** (before close) and **close window** (next morning). Outside both: for multi-day strategies (`double_calendar`, `atm_calendar`), run Step 3b/3d if any position open during session hours. For overnight-hold strategies, if any position open between market open and `close_window_start`, run Step 3c (profit-target/stop-loss and delta-stop checks). Outside all: skip to Step 5.

3. **Close window** — unconditional final backstop for every strategy. Whatever is open when close window arrives gets closed, regardless of P&L. IV crush already happened overnight; no more edge from holding.
   - For positions with `legs_json` (iron_fly, iron_condor, directional_credit_spread, broken_wing_butterfly, atm_calendar): fetch live quotes, compute generic exit debit, `save_close`.
   - For positions with `trade_legs` (`double_calendar` only): `get_open_legs`, close remaining via conservative pricing, `save_leg_close` each, then `save_close`.
   - Paper mode: simulate fill from live quotes. Live mode: submit actual closing order.

3b. **Double-calendar management** (runs whenever Step 2 routes here):
   - `get_open_legs` for each position, fetch live greeks.
   - Call `strategies/double_calendar.py evaluate_position()` with `is_first_check_of_day` flag.
   - `action: hold` — nothing. `action: close_side` — close just that side's 2 legs, `save_leg_close` each. `action: close_all` — close all legs, `save_close` when done.
   - Log via `scan_log`.

3c. **Early exit checks** (runs whenever Step 2 routes here): profit-target/stop-loss for credit strategies. First opportunity to close after overnight gap.
   - Fetch live quotes, call strategy's `evaluate_position()`.
   - `action: hold` — nothing. `action: close_all` — close via `legs_json` mechanism, `save_close`.
   - Log via `scan_log`.

3d. **ATM calendar management** (runs whenever Step 2 routes here): structurally like Step 3b, simpler since no partial-side close.
   - Fetch live quotes for 2 legs.
   - Call `strategies/atm_calendar.py evaluate_position()` with `is_first_check_of_day` flag.
   - `action: hold` — nothing. `action: close_all` — close both legs, `save_close`.
   - Log via `scan_log`.

4. **Entry window**:

   **4a. Account gate**: confirm broker connection (`tt.py get_connection_status` — still required in paper mode, since live quotes/chains for order-building always come from the real tastytrade session). NLV/buying power: `tt.py get_account_info`'s real balance in live mode, config's `available_capital_paper_mode` in paper mode (never the real account balance). Re-check `max_concurrent_earnings_positions` cap.

   **4b. Building today's ranked list**: call `python -m cherrypick.earnings.rank_strategies get_ranked_symbols --date <today>`. Takes union of enabled strategies' windows, evaluates all strategies against merged today-AMC/tomorrow-BMO calendar, picks each symbol's best, applies cap/correlation logic.

   **Per selected symbol** (each with `best_strategy`):
   - Skip if already opened today.
   - Re-verify: call `rank_strategies.py reverify_symbol()` fresh — confirm still accepted. If not `ok`, reject and log.
   - Risk cap hard stop: reject if max loss exceeds `max_risk_per_trade_pct` of NLV.
   - Correlation hard stop: reject if shares `correlation_block_list` grouping with open/entered position.
   - If all pass: call `strategies/<best_strategy>.py get_order()`, build order. If `ok: false`, log and move on.
   - For strategies with leg-by-leg closes (`double_calendar` only), pass `legs: strategies/<name>.py label_order_legs()`.
   - Paper mode: record via `db_paper.py save_trade`, stop. Live mode: submit via `tt.py execute_trade --live`, reprice toward zero credit on timer, record via `db.py save_trade`.
   - Log every candidate evaluated, not just entries — distinguishes quiet nights from broken re-verification.

5. **Record and notify** — one-line status, schedule next wakeup per interval table.

**Wakeup schedule** (end loop if no applicable condition):
- No open positions, outside all windows, next window >90 min away: **end loop**.
- Approaching entry window (30 min prior): **300s**.
- Inside entry window, capacity remaining: **60s**.
- Inside entry window, cap reached: **end loop / wake at close window start**.
- Overnight, overnight-eligible positions open, market closed: **wake at next market open**.
- Inside close window, ≥1 position open: **60s**. No positions: **end loop**.
- `double_calendar` or `atm_calendar` open, regular session hours: **300s–600s** (Step 3b/3d). Market closed: **wake at next market open**.
- Five overnight-hold strategies' positions open, between market open and `close_window_start`: **60s–120s** (Step 3c).

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships. **Optional tooling** — not installed by cloning this repo; `graphify-out/` and the hooks that call it are gitignored/local-only, so a fresh checkout on another machine has neither.

- **Before using any `graphify` command, confirm it's available**: check that graphify-out/graph.json exists AND a `graphify` invocation succeeds (e.g. `graphify --help`). If either check fails — command not found, or no graph.json — skip straight to normal tools (Grep/Glob/Read) for this session and do not retry graphify commands later in the same session.
- If available: for codebase questions, first run `graphify query "<question>"`. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost) — skip silently if graphify isn't available.
