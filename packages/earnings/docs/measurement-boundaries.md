# Earnings: measurement boundaries

> _Part of the **cherrypick-earnings** package: [suite](../../../README.md) · [package README](../README.md) · [docs index](./README.md)._

Each row below is a date from which the rules changed what a session's numbers mean. Rows either side
of a boundary were produced under different rules, so **never pool across one**: read a per-arm result
from the boundary date, or end it before. The same dates are journalled in the paper ledger
(`python -m cherrypick.earnings.db_paper get_measurement_breaks`; new ones are written with
`paper_loop record-break`).

## Boundaries

| Date | What began | What it means for a reader |
|---|---|---|
| 2026-08-12 | Positions are managed (marked every tick, carried up to three trading sessions), not force-closed the next morning. Lifecycle tables begin. Per-strategy exit thresholds changed with it. | Win rate, expectancy and hold time all moved together. The 64 earlier trades carry `exit_reason = 'legacy_next_morning'`. Sample counts for any question about exits restart here; entry screening is unaffected. |
| 2026-08-31 | Expired positions are settled at intrinsic against the expiration close, ahead of every gate (`settlement.py`). | Before this an expired contract with a zero bid looped on `spread_too_wide`. The cohort opened 2026-08-25..27 is the stranded one; the verdict model excludes it. |
| 2026-09-01 | `management_events.arm` is stamped. | Earlier rows are NULL and are not backfilled. |
| 2026-09-02 | The entry-calendar admissibility gate is gone: the morning snapshot orders the scan and never gates it. | Entry volume before and after are not comparable. |
| 2026-09-24 | The `profile` column is `arm`. | A rename only. `scripts/arm_column_migrate.py --only profile` renames an old ledger. |
| 2026-10-14 | `expected_move_butterfly` opens in `strat_test` (`strat_test_harness.STRAT_TEST_STARTS`). | Before that session its screen row is written and the opening is refused as a `strategy_not_started` drop. The set of strategies is a measurement key (`strat_test_strategies` break). |
| 2026-10-15 | `atm_calendar` and `double_calendar` open nothing in `strat_test` (`STRAT_TEST_RETIRED`). | The harness still writes the screen row and refuses the opening as a `strategy_retired` drop. Open rows run off under the ordinary close path. |
| 2026-10-26 | Exits price at mid for any position entered on or after this date (`scanner.MID_EXIT_FROM`), clamped to the structure's payoff range (`scanner.exit_debit_bounds`). `broken_wing_butterfly` is retired from `strat_test` (`STRAT_TEST_RETIRED`). | Older positions run off on the natural side, so no trade is measured under both. Expect targets reached sooner, stops later, and gross P&L nearer mid. |
| 2026-10-28 | Entry admits only names the suite's liquidity verdict calls `tradable` (`universe.py`). | About half the entries per session and a universe of larger names. A refused name is a recorded `not_tradable` drop, never a silence. |
| 2026-10-30 | A failed entry scan is retried inside the window; entries open as the scan goes; a close the spread gate has held for `spread_gate_patience_minutes` (30) in one session is taken anyway. | Exit timing changed. A held close is taken at the position's own basis and records `spread_gate_waited_minutes`. |
| 2026-11-05 | Early-close days: every exit clock is the same offset from the day's close and **no entry scan runs**. Leg quotes (`entry_bid`/`entry_ask`, `exit_legs_json`) and the term-structure inputs on `entry_reviews` are recorded. The winrate backtest scores the expected move, not the full straddle. | Early-close entries are absent by rule (`half_day_no_entries`), not by a quiet screen: never read them as a zero-entry sample. The new columns are NULL before this date and never backfilled. A winrate recorded before this date is on the old definition. |
| 2026-11-06 | The PEAD gate fires at the first executable check of the first morning; the scan screens tradable names first; the expected move is the report's own move (`expected_move_method`); `assignment_risk` closes a short at assignment risk; the expiry close is at 15:20. | Read results by `expected_move_method`. A paired twin, `advised:pead-gate-off:<strategy>`, opens beside each overnight control from this date with the first-morning loser gate off: compare a control with its own twin only. |
| v0.12.0 | Earnings is paper-only. | `earnings_trades.db` holds the orders placed before the change; nothing writes it. |

## Recording fixes that rewrote old rows

A number recorded wrong is fixed at once, not at a boundary. These are fixes, and rows from the affected
windows need a filter:

- **Positions closed on their entry tick (2026-09-16..10-15).** A tick right after the 15:35 scan
  evaluated a new position as if the report had passed, and closed it within minutes (the calendars on
  the front-DTE stop, the overnight structures on the PEAD gate). `management.evaluate` now holds every
  position on its entry session behind only the expiry close and the pin guard. The rows stay in the
  ledger: drop `closed_at - opened_at < 600` when reading those strategies across that window.
- **Exit slippage charged twice (to 2026-10-23).** A natural-side exit already concedes half of every
  leg's spread, and the old close paths also charged the slippage haircut into `exit_cost`.
  `costs.natural_exit_costs` now charges fees and records `exit_slippage` 0.
  `scripts/earnings_backfill_exit_slippage.py` (dry run by default) subtracted each row's own
  `exit_slippage`; 40 rows from before slippage was recorded keep an unknown haircut.
- **Exits priced past the payoff (to 2026-10-26).** On wide-quoted expiry-day books the far touch went
  beyond what the structure can be worth. `scripts/earnings_clamp_exit_debits.py` (dry run by default;
  it backs the ledger up and writes a changed-row record beside it) applied the clamp to the older
  rows: `exit_debit` and gross `pnl` only. Calendars have no payoff bound and are left as recorded.

## Rules these boundaries left behind

- **Declare the boundary, then land together.** Entry pacing, gate semantics, exit basis and which
  strategies open all change what the numbers mean. They land on one declared date, dated in code
  (a `STRAT_TEST_*` map or a module constant) so they merge ahead of it and bind on it with no config
  change, and one `paper_loop record-break` row is written on the day.
- **A refused entry is a drop with a reason.** `not_tradable`, `strategy_retired`, `strategy_not_started`
  and `entry_window_closed` are execution-stage rows, so "refused for liquidity" never reads as
  "nothing qualified". A missing or stale liquidity verdict (more than three trading sessions old)
  refuses every candidate as `liquidity_verdict_unavailable`, with why, rather than admitting
  unconfirmed names.
- **The forward scan logs and is bounded.** Every phase writes a run-log line, the forward scan
  included, so "found nothing" differs from "screened, none cleared". The scan holds the single-writer
  lock, so each symbol and each pass has a time bound.
- **Advised twins stay managed.** `paper_loop.managed_book` must strip the advised prefix before asking
  whether a book is a `strat_test` book, or a twin is never marked, evaluated or closed
  (`test_an_advised_twin_is_managed_beside_its_control`).
- **Per-event correlation is read-side.** Every strategy opens on every viable name, so one event can
  land in several arms. Capping it would decide which arm gets the event and starve the rest, so the
  read side clusters its confidence intervals by event instead.
- **`term_structure` as an entry covariate is a hypothesis.** It was the one of 11 recorded covariates
  that separated outcomes (more inverted did better, Spearman about -0.29 against net over capital at
  risk). Scored once on the first 70 events entered from 2026-10-26 by `iron_fly`, `iron_condor` and
  `directional_credit_spread`. Pass: rho at or below -0.20 and the most-inverted third beats the least.
  A pass proposes a `symbol_screen` gate, which is its own boundary.
