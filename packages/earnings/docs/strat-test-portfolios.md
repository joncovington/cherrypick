# Strat-Test Portfolios — Per-Strategy Paper Books

> _Part of the **cherrypick-earnings** package — [suite](../../../README.md) · [package README](../README.md) · [docs index](./README.md)._

Goal: give the forced-sampling strategy test (`docs/strategy-testing-plan.md`) a clean,
per-strategy view of results, so each strategy accumulates its own P&L and equity curve on the
same nightly earnings calendar and can be compared head-to-head after a few weeks — no
re-derivation, no guesswork.

---

## Design principle: book = strategy

The forced-sampling harness (`strategy_test_runner.py`) opens a paper trade for **every strategy
that clears the screen** on **every viable symbol** each night — deliberately more than the
production loop would, so no strategy starves for a sample. Each of those paper trades is tagged
with a `profile` value in the shared `paper_trades.db` that decides which **book** it lands in.
That tag is controlled by the top-level `strat_test_portfolio` config key:

| `strat_test_portfolio` | Behavior |
|---|---|
| `"per_strategy"` (default) | Each strategy's trades are tagged `strat_test:<strategy>` (e.g. `strat_test:iron_fly`), so every strategy has its **own** book — its own open positions, P&L ledger, and equity curve. A newly added strategy automatically gets its own stream the first night it fires; nothing else has to be wired up. |
| `"combined"` | All forced-sampling trades share a single `strat_test` book (the original behavior). One blended P&L across every strategy. |

The expensive live scan (`rank_strategies.evaluate_symbol` per calendar entry, which makes the
tastytrade/DoltHub calls) still runs **once** per night regardless of this setting; the only
thing `strat_test_portfolio` changes is how the resulting trades are bucketed for reporting.
Per-strategy is the default because the whole point of the test is to judge strategies
individually — a blended book hides which strategy is carrying (or dragging) the result.

---

## How the books are read back

Reporting and the orchestrator's read side group by that per-strategy book tag:

- `strategy_report.py` / `strategy_dashboard.py` — `--profile` defaults to the **strat_test
  family**: a `--profile strat_test` request matches the whole family (the combined `strat_test`
  book *plus* every `strat_test:<strategy>` sub-book), so you get a complete picture whether the
  harness ran in `per_strategy` or `combined` mode. Narrow to one strategy with `--strategy
  <name>`, or to a date range with `--since YYYY-MM-DD`.
- The orchestrator's `report` and `calibrate` read the same paper ledger and group by the same
  book tags. Because each strategy is now its own book rather than a rung on a risk ladder,
  `calibrate` gives **per-book readings only** — there's no "graduate to the next rung"
  promotion for the earnings module (the earnings calibration ladder in the orchestrator config
  was emptied deliberately; see the [orchestrator](../../orchestrator) docs).

`db_paper.py get_pnl_summary` exposes the same split directly via its `by_profile` breakdown
(and `--profile <book>` / `--strategy <name>` filters) for a quick raw-P&L check.

---

## Attribution: the `profile` column

Every forced-sampling trade and every `scan_log` row carries the `profile` column that holds its
book tag (`strat_test:<strategy>` in per-strategy mode, `strat_test` in combined mode; the
production paper/live loop uses `'default'`). `trades` also carries `quantity` and
`capital_at_risk` from `sizing.compute_position_size`. This is what lets a single shared
`paper_trades.db` hold every strategy's book side by side without a schema change per strategy —
the reporting layer just filters on `profile`.

Sizing basis for the whole test is simply `available_capital_paper_mode` (the simulated NLV in
config), the same basis the paper loop uses — there's no separate per-book capital or risk
multiplier. The forced-sampling harness isn't trying to compare risk appetites; it's isolating
each strategy's edge on one fixed, shared capital basis.

---

## Per-contract max-loss rules (sizing.py)

Sizing needs a defensible per-contract max loss for each strategy. Strikes are in points;
one contract controls 100 shares.

| Strategy | Per-contract max loss |
|---|---|
| iron_fly | (widest wing − credit) × 100 |
| iron_condor | (widest wing − credit) × 100 |
| directional_credit_spread | (\|long−short\| − credit) × 100 |
| atm_calendar / double_calendar | debit × 100 |
| broken_wing_butterfly | (far_width − near_width + net_debit) × 100 |

Every strategy is defined-risk, so max loss comes straight from the order's own
strikes/debit — there is no naked/undefined-risk margin proxy. The BWB gap approximation is
an **estimate** to be refined once real paper fills accumulate.

---

## Reading the books side by side

Compare strategies by running `python src/strategy_report.py` (or `strategy_dashboard.py`) and
reading the per-strategy numbers — the cross-strategy comparison grid in the dashboard already
puts every book's equity curve, drawdown, and expectancy next to each other. There's no separate
head-to-head report script; the per-strategy books plus the shared `strategy_metrics.py`
computations *are* the comparison.

Once a strategy has a decision-grade sample (see `docs/strategy-testing-plan.md`'s sample
targets), decide **enable live / keep paper / retire** on it, then copy that strategy's validated
parameter block into the live config root and flip `enable_live_trading` only after review.
