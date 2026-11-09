# cherrypick-earnings — Operational Instructions

This module's old word `profile` is the root's `arm` (`--profile` selects an arm); `strategy` is the
STRUCTURE type and is not renamed. The column is `arm` on every table that carries it, and `_conn()`
refuses a ledger that still says `profile` (`scripts/arm_column_migrate.py --only profile` renames one).

> Operating contract for the cherrypick **Earnings** engine. Human guides: [`docs/`](docs/README.md);
> incident history: [docs/operating-history.md](docs/operating-history.md); suite-wide context: the
> root [documentation index](../../docs/README.md).

The cherrypick **Earnings** module runs options structures around earnings reports. **It is
paper-only since v0.12.0**: it places no live order and has no live gate, arm or kill criteria, and a
leftover `enable_live_trading` or `live` block in its config is ignored (`run.py doctor` says it can
go). Seven strategies, **all defined-risk**: `iron_fly`, `double_calendar`, `iron_condor`, `atm_calendar`,
`directional_credit_spread`, `broken_wing_butterfly`, `expected_move_butterfly`
(`docs/05-strategies.md`, the one strategy table). `expected_move_butterfly` is **paper only** and is
never ranked as a symbol's best. `atm_calendar`, `double_calendar` and `broken_wing_butterfly` stay
registered but open nothing in `strat_test` (`strat_test_harness.STRAT_TEST_RETIRED`, dated
retirements). **Exits price at mid** (natural side before the 2026-10-26 boundary:
`scanner.exit_basis`; slippage is a cost on a mid fill only, `costs.exit_costs`). **Every entry path
admits only names the suite's liquidity verdict calls `tradable`**: a refused name is a recorded
`not_tradable` drop, an unknown one is refused, and a missing or stale verdict refuses everything
loudly, never silently. A failed entry scan is retried inside the window, entries open as the scan
goes, and a close the spread gate has held 30 minutes in a session is taken anyway. The PEAD gate
fires at the first executable check of the first morning, the scan screens tradable names first,
the expected move is the report's own move, a short at assignment risk closes, and the expiry close
is at 15:20.

**Several of those rules began on a date, and the rows either side are not comparable. Never pool
across a boundary; the list, with each date, is [Declared boundaries](docs/operating-history.md#declared-boundaries).**
Strategy table: [docs/05-strategies.md](docs/05-strategies.md); this file does not repeat it.

Undefined-risk/naked
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
  (`entry_scan_at`), EOD 16:00–16:30, nothing off-session. These are a normal 16:00 close; on a
  13:00 early-close day every exit clock is the same offset from that day's close and **no entry
  scan runs** (`session_clock.py`; docs/operating-history.md). It opens the strat_test books (every
  strategy clearing the screen on every viable name; per-strategy by default via
  `strat_test_portfolio`), always paper-only into `paper_trades.db`. No per-iteration agent.
- **Two trails, different questions.** `state/earnings_entry.last.json` / `earnings_exit.last.json` are LATESTs — "is it alive" only.
  `logs/earnings_paper.log` is the run trail: one JSON line per completed forward-scan/entry/exit
  phase (`append_run_log`) with the full per-symbol result. **Every phase must log, the forward scan
  included** — its `symbols` count is what separates "found nothing" from "screened, none cleared".
  Best-effort: a session that cannot write its log still trades.
- **Positions are MANAGED, not force-closed next morning** (a journaled break, see the boundaries).
  A winner short of target is carried up to three *trading* sessions; a loser closes on the first
  morning (post-earnings drift continues). **Nothing but the expiry close and the pin guard acts on
  the entry session** (`sessions_held == 0`): the report has not happened, and a fresh mark is just
  the spread. The expiry close is at 15:20, and a short at assignment risk (a call under a coming
  dividend, or 3%+ in the money with no time value) closes as `assignment_risk` (docs/10-exits.md).
  Quotes come from the stream cache first, the broker only for unsubscribed legs and to confirm a close.
- **Expired positions are SETTLED, not traded out.** An expired leg is refused as a mark (`provider`)
  and resolved at intrinsic against the expiration day's close from local `stocks.ohlcv`
  (`settlement.py`), **ahead of every gate** -- settlement is bookkeeping, not an order. Otherwise a
  zero-bid expired contract loops on `spread_too_wide` forever. Details: `docs/10-exits.md`.
- **Screening is split across the day.** `forward_scan` (~06:30) computes the slow half (calendar and
  Dolt metrics, next 10 trading days), **bounded per symbol and per pass** (`bounded.py`, as the entry
  scan) and naming what it skipped — it holds the single-writer lock, so a hung query would block the
  entry scan. Its snapshot feeds the console's Upcoming surface and PRE-FILTERS the entry scan on
  stable criteria only (winrate, average volume, market cap) against the loosest floor, so **no
  morning reading ever decides an entry**. It also ORDERS the entry universe but never bounds it
  (`symbol_watch.covered_symbols` → `assume_amc_for`): Dolt's `when` is ~47% NULL, so a blank report
  time on the scan date is read as after-the-close and flagged `timing_assumed`; the set only decides
  which rows finish FIRST if `entry_scan_budget_seconds` binds. It must never gate admission (a past
  gate caused a five-session outage; docs/operating-history.md). Why AMC,
  never BMO: `docs/screening-criteria.md` Layer 0.
- **The entry scan runs tradable names first.** `run_entries` splits the calendar
  with `universe.refusal` before screening: names the liquidity verdict calls tradable go first, in
  the calendar's own order, then refused names, which are screened only with the budget left and
  then recorded as before (screen rows, then the `not_tradable` execution drop; never opened). A
  refused name the budget never reached is the ordinary `entry_scan_budget_exceeded` outcome. A
  missing or stale verdict refuses everything, so the order is unchanged. Retired strategies run no
  screen at all and leave no row.
- **This module drives that scan itself.** The orchestrator's `symbol-watch` job, a second run of
  the same scan, was removed 2026-11-02; don't bring one back. `symbol_watch.py refresh`,
  `run_entries` and `run_closes` survive as manual/backfill verbs.
- **No agent session.** There is no per-iteration agent and no `/earnings-start`; the supervisor's
  `earnings-paper` job above is the whole loop. `rank_strategies.py` (best strategy per symbol, run
  by hand through `/paper-trading-start`) is a standalone analysis tool that records to the paper
  ledger and places nothing.

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
  — never rebuild a `data/…` path relative to the package. Ledgers `paper_trades.db` (paper) and the
  historic `earnings_trades.db` (live orders placed before v0.12.0; nothing writes it now) under `~/.cherrypick/data/earnings` or `$EARNINGS_DATA_DIR` (tests);
  `db.py`, `db_paper.py`, `strategy_metrics.py` derive from it. `symbol_watch.json` (plain JSON) via
  `paths.data_path`; the console resolves the same path independently and must never import this
  package. Logs: `paths.logs_dir()` (`~/.cherrypick/logs/earnings`, `$EARNINGS_LOGS_DIR`, or
  `$CHERRYPICK_HOME/logs/earnings`). Config: `paths.config_path()` (`~/.cherrypick/config/earnings.json`,
  else in-repo `config/config.json`, else the shipped `config/config.example.json`). The Dolt databases share the
  directory without collision. The module's own EOD reports were retired 2026-08-13 —
  `packages/review` builds them.
- **The orchestrator boundary is strict.** It drives this module by subprocess for unattended
  **paper** collection and reads the paper ledger for cross-module reporting. It **never edits this
  module's code or config and never places, cancels, adjusts or closes an order**. Its one config
  action is onboarding (`cherrypick connect`), delegating to this module's credential tool for
  keyring service `earningsagent` — configuration only. The broker session is read-only: quotes and
  chains, no orders.
- **Two couplings — don't change silently:** (1) the paper DB path (the orchestrator's `paper_db`
  points at it) and its `trades` schema, read through the `"earnings"` adapter by `report`/`calibrate`;
  (2) the `earningsagent` keyring service (`connect`).

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
  `advice_params`. Quotes for the slippage study (from 2026-11-05, NULL before, never backfilled):
  each `legs_json` leg carries `entry_bid`/`entry_ask` (+ source and age), and `exit_legs_json` holds
  the quote each traded-out leg was closed from.
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
  The term-structure inputs (`front_iv`, `back_iv`, `front_dte`, `back_dte`, both expiration dates)
  are NULL before 2026-11-05 likewise; the gate measures the ratio, not the per-day slope.
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
