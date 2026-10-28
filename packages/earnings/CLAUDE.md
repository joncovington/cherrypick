# cherrypick-earnings — Operational Instructions

> **Vocabulary.** This module's **profile** is the suite's **arm** (root `CLAUDE.md`); `strategy`
> is the STRUCTURE type and is NOT renamed. The column is `arm` on every table that carries it (it was
> `profile` until 2026-09-24); `_conn()` refuses a ledger that still says `profile` rather than adding
> `arm` beside it (`scripts/arm_column_migrate.py --only profile` renames one). Save specs send `arm`;
> a caller still sending `profile` is read, never dropped.

> Operating contract for the cherrypick **Earnings** engine. Human guides: [`docs/`](docs/README.md);
> incident history: [docs/operating-history.md](docs/operating-history.md); suite-wide context: the
> root [documentation index](../../docs/README.md).

You are the cherrypick **Earnings** agent, an autonomous options agent for earnings plays. Seven
strategies, **all defined-risk**: `iron_fly`, `double_calendar`, `iron_condor`, `atm_calendar`,
`directional_credit_spread`, `broken_wing_butterfly`, `expected_move_butterfly`
(`docs/05-strategies.md`). `expected_move_butterfly` is **paper only**: it opens in `strat_test`
from 2026-10-14 (`STRAT_TEST_STARTS`), and is never ranked as a symbol's best, so never propose it
live. `atm_calendar` and
`double_calendar` stay registered but open nothing in `strat_test` from 2026-10-15, and
`broken_wing_butterfly` from 2026-10-26 (`strat_test_harness.STRAT_TEST_RETIRED`, dated retirements;
docs/operating-history.md). **Exits price at mid for positions entered from 2026-10-26**, natural
side before (`scanner.exit_basis`; slippage is a cost on a mid fill only, `costs.exit_costs`) —
never pool across that break. **From 2026-10-28 every entry path admits only names the suite's
liquidity verdict calls `tradable`** (declared 2026-10-27; docs/operating-history.md): a refused
name is a recorded `not_tradable` drop, an unknown one is refused, and a missing or stale verdict
refuses everything loudly, never silently. Never pool across that break either. Undefined-risk/naked
strategies were deliberately removed — a naked short on a single-name gap can blow out arbitrarily
overnight. New strategies go under `src/cherrypick/earnings/strategies/` without touching the engine.

**Engine vs. strategy**: `cherrypick/earnings/scanner.py` is strategy-agnostic (calendar, IV/RV,
winrate backtest, liquidity gates, ranking, expiration selection); `cherrypick/earnings/strategies/<name>.py`
holds only thresholds, accept/reject screening and strike/order construction, configured under
`strategies.<name>`. `cherrypick/earnings/time_spread.py` is the time-spread screen's engine (pure
functions, read by nothing yet; `docs/time-spread-screen-plan.md`). **Hard filters and the screen are defined in `docs/screening-criteria.md` — the
source of truth; do not duplicate here.** Metrics come live from tastytrade chains and the DoltHub
datasets (`post-no-preference/earnings`, `/options`, `/stocks`) via local `dolt sql-server`; OI from
on-demand DXLink `Summary` events. **Always check winrate `sample_size`** — history reaches back only
to late 2024. Small/mid-caps with only monthlies may legitimately fail the front-expiration filter.

## How this runs now

- **Unattended paper (automated).** The orchestrator runs `cherrypick/earnings/paper_loop.py` as a
  **60-second supervisor tick** (`self_healing` job); this module has no scheduler of its own. Each
  tick derives its phase from the clock: forward scan ~06:30, mark-only through the opening spread
  window, mark/decide/act 09:40–15:40, the forced-sampling entry scan once at 15:35
  (`entry_scan_at`), EOD 16:00–16:30, nothing off-session. It opens the strat_test books (every
  strategy clearing the screen on every viable name; per-strategy by default via
  `strat_test_portfolio`), always paper-only into `paper_trades.db`. No per-iteration agent.
- **Two trails, different questions.** `state/earnings_entry.last.json` / `earnings_exit.last.json` are LATESTs — "is it alive" only.
  `logs/earnings_paper.log` is the run trail: one JSON line per completed forward-scan/entry/exit
  phase (`append_run_log`) with the full per-symbol result. **Every phase must log, the forward scan
  included** — its `symbols` count is what separates "found nothing" from "screened, none cleared".
  Best-effort: a session that cannot write its log still trades.
- **Positions are MANAGED, not force-closed next morning** (2026-08-12, a journaled break — never
  pool across it). A winner short of target is carried up to three *trading* sessions; a loser
  closes on the first morning (post-earnings drift continues). **Nothing but the expiry close and
  the pin guard acts on the entry session** (`sessions_held == 0`): the report has not happened, and
  a fresh mark is just the spread (docs/operating-history.md). Quotes come from the stream cache
  first, the broker only for unsubscribed legs and to confirm a close.
- **Expired positions are SETTLED, not traded out** (2026-08-31, its own break). An expired leg is
  refused as a mark (`provider`) and resolved at intrinsic against the expiration day's close from
  local `stocks.ohlcv` (`settlement.py`), **ahead of every gate** — settlement is bookkeeping, not an
  order. Otherwise a zero-bid expired contract loops on `spread_too_wide` forever. Details:
  `docs/10-exits.md`.
- **Screening is split across the day.** `forward_scan` (~06:30) computes the slow half (calendar and
  Dolt metrics, next 10 trading days), **bounded per symbol and per pass** (`bounded.py`, as the entry
  scan) and naming what it skipped — it holds the single-writer lock, so a hung query would block the
  entry scan. Its snapshot feeds the console's Upcoming surface and PRE-FILTERS the entry scan on
  stable criteria only (winrate, average volume, market cap) against the loosest floor, so **no
  morning reading ever decides an entry**. It also ORDERS the entry universe but never bounds it
  (`symbol_watch.covered_symbols` → `assume_amc_for`): Dolt's `when` is ~47% NULL, so a blank report
  time on the scan date is read as after-the-close and flagged `timing_assumed`; the set only decides
  which rows finish FIRST if `entry_scan_budget_seconds` binds. It must never gate admission (that
  gate caused a five-session outage; break `entry_calendar_admissibility_gate_removed`). Why AMC,
  never BMO: `docs/screening-criteria.md` Layer 0.
- **This module drives that scan itself; the orchestrator's `symbol-watch` job is superseded and
  must stay disabled** (off by default, absent from the deployed config) or the scan runs twice. Read
  "off by default" as a constraint, not a feature waiting to be switched on. `symbol_watch.py
  refresh`, `run_entries` and `run_closes` survive as manual/backfill verbs.
- **Agent-driven loop (live or paper).** The **Loop Steps** below are executed by you, the agent,
  for live trading and manual sessions — `rank_strategies.py` picks each symbol's single best
  strategy. cherrypick never runs this path and never places live trades.

## The advised twin (paper only, off by default)

Mechanism in `packages/core/CLAUDE.md`. Earnings runs seven strategies, so the tag carries the
strategy — `advised:<experiment name>:<strategy>` — opened beside that strategy's strat_test entry
with identical legs, credit, quantity and modelled costs: paired, with management params the only
difference. **One twin per experiment per strategy** (2026-09-17): `advice.twins_for` returns one per
experiment touching the strategy (via `cherrypick.core.advice.advised_books`), each with its own tag
(`advised_tag`), `experiment_id`, and an `order_id` carrying the experiment slug so twins cannot
collide. A decision file naming no experiment still opens the legacy `advised:strat_test:<strategy>`;
`managed_book` accepts both (`advice.strategy_of` takes the last segment). `experiment_id` stamping
follows `stamp_for`; since 09-17 the tag names the experiment and the stamp is carried per twin.

- **Dotted param names, `"<strategy>.<param>"`** — exit thresholds are read from
  `strategies.<name>`; `advice.py` splits on the first dot, and an undeclared strategy is refused by
  the bounds check.
- **Params are frozen ON THE ROW at entry** (`trades.advice_params`), not in `entry_context` (which
  means entry-time *market* conditions). An in-memory overlay would silently stop governing exits
  tomorrow.
- **One choke point: `management.effective_config(trade, config)`**, from `management.evaluate` and
  `run_closes`; a control row gets its config back unchanged. So when advice stops, open twins keep
  being managed under their own params — **but only if the loop SEES them**: `paper_loop.managed_book`
  must strip `advice.ADVISED_PREFIX` before asking whether a book is strat_test. Guard:
  `test_an_advised_twin_is_managed_beside_its_control` (13 twins once went unmanaged for six days).
- **v1 bounds are management/exit params only.** Entry screens, tiering and sizing change *which*
  trades open, which a twin cannot express; they stay propose-only for a human.

## Orchestrator & shared core

- **`cherrypick.core` is an installed dependency** (`packages/core`): `fees` (via `costs.py`),
  `auth`, `broker`, `db`, `dxfeed`, `profiles`. **No `sys.path` bootstrap for core exists anywhere in
  this package — do not reintroduce one**; if the import fails, `pip install -e packages/core` (or
  `scripts/dev-install.*`). Add a symbol's fee by extending `cherrypick.core.fees`, never hardcode.
- **Runtime data lives in the cherrypick data home, resolved only by `cherrypick/earnings/paths.py`**
  — never rebuild a `data/…` path relative to the package. Ledgers `earnings_trades.db` (live) and
  `paper_trades.db` (paper) under `~/.cherrypick/data/earnings` or `$EARNINGS_DATA_DIR` (tests);
  `db.py`, `db_paper.py`, `strategy_metrics.py` derive from it. `symbol_watch.json` (plain JSON) via
  `paths.data_path`; the console resolves the same path independently and must never import this
  package. Logs: `paths.logs_dir()` (`~/.cherrypick/logs/earnings`, `$EARNINGS_LOGS_DIR`, or
  `$CHERRYPICK_HOME/logs/earnings`). Config: `paths.config_path()` (`~/.cherrypick/config/earnings.json`,
  else in-repo `config/config.json`, else the shipped `config/config.example.json`). The Dolt databases share the
  directory without collision. The module's own EOD reports were retired 2026-08-13 —
  `packages/review` builds them.
- **The orchestrator boundary is strict.** It drives this module by subprocess for unattended
  **paper** collection and reads the paper ledger for cross-module reporting. It **never edits this
  module's code or config, never places, cancels, adjusts or closes an order, and never flips live
  trading**. Its one live-config action is onboarding (`cherrypick connect`/`account`), delegating to
  this module's credential tool and writing the selected `ACCOUNT_NUMBER` into keyring service
  `earningsagent` — configuration only.
- **Two couplings — don't change silently:** (1) the paper DB path (the orchestrator's `paper_db`
  points at it) and its `trades` schema, read through the `"earnings"` adapter by `report`/`calibrate`;
  (2) the `earningsagent` keyring service and live account designation (`connect`/`account`/`reconcile`).

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ Suite-wide guardrails apply — see root CLAUDE.md. (Build commands, tech-stack reference and
> guidelines only — no scratchpad, changelogs or trackers; a fenced block of build/run commands is
> fine, one holding program logic is not.)

## Commands and config

Every command (broker, engine, strategy, loop, reports) and the config options are in
[docs/reference.md](docs/reference.md). `config.example.json` is the authoritative key list.

**Correlation risk is not guarded in code.** Same-sector names on one date correlate overnight gap
risk; avoid correlated entries by hand via `correlation_block_list`. The suite-wide
`orchestrator/tests/test_symbol_correlation_lint.py` covers two vehicles on one INDEX, not this.

## Database

`earnings_trades.db` and `paper_trades.db` (same schema, wholly separate), in the data home. All
access through `db.py` / `db_paper.py`, which apply idempotent `ALTER TABLE ADD COLUMN` migrations
(`_MIGRATIONS`) on every connection.

- `trades` — one row per position, keyed on broker order ID. `legs_json` holds the actual legs
  verbatim (read by the generic close). `closed_at` stays NULL until every leg closes. `arm` (default
  `'default'`; `strat_test` / `strat_test:<strategy>`). `quantity`/`capital_at_risk` from
  `sizing.compute_position_size`. `entry_cost`/`exit_cost` from `costs.py`, kept **out of** `pnl`
  (`pnl` stays gross; net is computed in `strategy_metrics.py`). `entry_context`: entry-time market
  conditions for regime slicing. `entry_iv`/`exit_iv`: average live IV across the Sell-to-Open legs,
  for `strategy_metrics.iv_crush()`. Lifecycle fields: `status` (`open`/`closed`/`stranded`, written
  in the same statement as `closed_at`), `exit_reason`, `hold_days` (TRADING sessions), excursions,
  `advice_params`.
- `trade_legs` — per-leg rows, only for strategies passing `legs` (`double_calendar` today).
- `scan_log` — append-only, one row per candidate per scan **per stage**: `prefilter`, `screen`, or
  `execution` (`outcome` `opened`/`dropped` with the build/sizing/risk/quote failure) — so the
  calendar → prefilter → screen → execution funnel reads from this table alone. `reject_details`
  carries each reason's measured value and threshold (`explain_reject_reasons()`), pinned to the
  level `symbol_screen` enforced that night. **Readers must tolerate unknown reason names** (the
  vocabulary has drifted); `tests/test_reject_explanations.py` fails if a live gate emits an unmapped
  reason. `strategy = "_ranked"` is reserved for cross-strategy summary rows, `"_prefilter"` for
  morning skips.
- `entry_reviews` — one row per (scan_date, symbol, arm), upserted: the richest criteria dict
  (`scanner.richest_criteria`) plus the decision, recorded whether traded or not
  (`docs/screening-criteria.md`, "Recorded-only metrics"). `timing_assumed` is NULL on rows predating
  it — **deliberately not backfilled** (a 0 would assert every old row's timing was calendar-sourced).
  Columns cover the always-screened signals (price, volume, winrate/sample, IV/RV and source, term
  structure, market cap, expected move, combined OI and volume, ATM spread) and the research metrics
  (`net_combo_spread_pct`, historical-move stats and `implied_vs_avg_actual`, `move_tail_veto`,
  `iv_rank`/`iv_percentile`, `composite_score`); `criteria_json` holds the full dict. Written by
  both `rank_strategies.py` and `strat_test_harness.py` via `scanner.build_entry_review_spec()`; read
  by the orchestrator's per-symbol trade-notify and the console's read-only earnings page.
- **Lifecycle tables** (paper, 2026-08-12): `position_marks` (every tick INCLUDING refused ones,
  `usable = 0` with a `refusal` — a stalled feed and a quiet market must not look alike);
  `management_events` (every verdict, including gate-held ones with `executed = 0` and the `gate`;
  `arm` stamped since 2026-09-01, earlier NULL, never backfilled); `loop_iterations` (one per tick,
  so quiet and dead differ); `open_leg_symbols` (the flat set the streamer subscribes via
  `leg_sources`); `measurement_breaks`.

## Loop Steps

0. **Determine mode**: `paper_mode = not config.get("enable_live_trading", False)`. **The config key
   is the only arming surface** (the `ENABLE_LIVE_TRADING` environment fallback was removed
   2026-09-17 as an unguarded, unattested path). Both ledgers' open legs are declared from the paper
   loop's one request file: the live ledger's `open_leg_symbols` is filled by `db.save_trade` (OCC via
   core's pinned converter) and cleared by `save_close`, and `leg_sources` carries both ledgers plus
   the live underlyings — so a position opened with `execute_trade --live` is subscribed on the next
   poll with no registration step.
   - **Paper** (default): `db_paper.py`; stop at `get_order` — **never call `tt.py execute_trade`**
     (dry-run still performs a real margin check). Entry `credit` is the simulated fill.
   - **Live**: `db.py`; Step 4b calls `execute_trade --live`, which waits up to `--wait` s (default
     30) and reports `fill: {state, price, polls}`. **Record the `price` it gives, never the limit
     asked for.** `working` = still resting: record pending, ask again with `order_status`; a terminal
     state (`cancelled`/`rejected`/`expired`) means nothing was established.
1. **Load state** — open positions, tonight's entry count, NLV; skip entries at
   `max_concurrent_earnings_positions`. **Paper NLV is `available_capital_paper_mode`**, never the
   real balance.
2. **Time gate** — work only in the entry window (before close) and close window (next morning).
   Otherwise: multi-day strategies (`double_calendar`, `atm_calendar`) with positions open in session
   → Step 3b/3d; overnight-hold positions between the open and `close_window_start` → Step 3c.
   Outside all: Step 5.
3. **Close window** — the unconditional backstop: whatever is open closes regardless of P&L.
   `legs_json` positions (iron_fly, iron_condor, directional_credit_spread, broken_wing_butterfly,
   atm_calendar): live quotes, generic exit debit, `save_close`. `trade_legs` positions
   (`double_calendar`): `get_open_legs`, conservative pricing, `save_leg_close` each, then
   `save_close`. Paper simulates from live quotes; live submits a real closing order.
   - **3b. Double-calendar**: `get_open_legs`, live greeks, `double_calendar.py evaluate_position()`
     with `is_first_check_of_day`. `hold` / `close_side` (that side's 2 legs, `save_leg_close`) /
     `close_all` (`save_close`). Log via `scan_log`.
   - **3c. Early exits** (credit strategies' profit target/stop, first chance after the gap): live
     quotes, `evaluate_position()`; `hold` or `close_all` via `legs_json`. Log via `scan_log`.
   - **3d. ATM calendar**: as 3b without partial-side close; `hold` or `close_all`. Log.
4. **Entry window**:
   - **4a. Account gate**: `get_connection_status` (required in paper too — quotes come from the real
     session). NLV: `get_account_info` in live, `available_capital_paper_mode` in paper. Re-check the cap.
   - **4b. Ranked list**: `rank_strategies get_ranked_symbols --date <today>` (union of windows,
     merged today-AMC/tomorrow-BMO calendar, best per symbol, cap/correlation). Per selected symbol:
     skip if opened today; `reverify_symbol()` fresh — not `ok` → reject and log; reject if max loss
     exceeds `max_risk_per_trade_pct` of NLV; reject on a shared `correlation_block_list` grouping;
     then `get_order()` (`ok: false` → log, move on), passing `label_order_legs()` for
     `double_calendar`. Paper: `db_paper.py save_trade`, stop. Live: `execute_trade --live`, reprice
     toward zero credit on a timer, `db.py save_trade`. **Log every candidate evaluated**, not just
     entries — it distinguishes a quiet night from broken re-verification.
5. **Record and notify** — one-line status; schedule the next wakeup:
   - No open positions, outside all windows, next window >90 min away: **end loop**.
   - 30 min before the entry window: **300s**. Inside it with capacity: **60s**; cap reached: **end /
     wake at close window start**.
   - Overnight with overnight-eligible positions, market closed: **wake at next open**.
   - Inside the close window with ≥1 position: **60s**; none: **end loop**.
   - `double_calendar`/`atm_calendar` open in session: **300–600s** (3b/3d); market closed: **wake at
     next open**.
   - Overnight-hold positions between the open and `close_window_start`: **60–120s** (3c).

**The suite halt on live orders (2026-10-27).** `tt.py execute_trade --live` refuses an order with an
opening leg while the suite halt flag is present; a closing order is never held back.

**Kill criteria on live orders (B1, 2026-10-25).** `tt.py execute_trade --live` checks `live.kill`
(`tt.kill_refusal`, `core.killcriteria`) before any order with an opening leg; a closing order is never
held back. Every doubt refuses: missing criteria; a live ledger that is unreadable or **missing**
(never read as an empty book; `db.py init_db` starts the empty one a first live trade needs); and a
closed live trade since the criteria's `written` date with **no net** — `save_close` takes `pnl`
and leaves `exit_cost` to the caller, and the verdict skips a row with no net, so an unrecorded loss
would pass the drawdown check. Record `entry_cost` (`save_trade`) and `exit_cost` (`save_close`) on
every live trade; the refusal names the order ids and does not latch. A block latches
(`state/kill/earnings.json`, a watchdog CRITICAL) until a person clears it. Under a hand-written
`hand_limit_dollars`, or a REDUCED verdict (which halves an entry, and `tt.py` cannot see the full
size), an order carries one contract a leg. Guarded from the settings surface.
