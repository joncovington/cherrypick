# Strategy Testing: the forced-sampling paper program

> _Part of the **cherrypick-earnings** package — [suite](../../../README.md) · [package README](../README.md) · [docs index](./README.md)._

A forward test of the defined-risk strategies (`iron_fly`, `iron_condor`, `directional_credit_spread`,
`expected_move_butterfly`, and the retired `double_calendar`, `atm_calendar` and
`broken_wing_butterfly`, which open nothing now), until each has enough cost-adjusted evidence to
decide: keep or retire.

This is a **separate program** from the ranked path (`rank_strategies.py`).
It never touches `db.py`, places no order, and never respects
`max_concurrent_earnings_positions` or the correlation block list — its whole point is to
open more than the loop ever would, on purpose, so every strategy accumulates a usable
sample.

In normal suite operation this program runs **unattended under the [orchestrator](../../orchestrator)**.
Its supervisor runs `paper_loop once` every 60 seconds as the `earnings-paper` job. The loop runs
the harness's entry scan at 15:35 into the strat_test books and manages exits itself. The
orchestrator then reads the resulting `paper_trades.db` (in the shared data home, `~/.cherrypick/data/earnings`) for cross-module
reporting. This module has no scheduler of its own. Run `/paper-start` here for a single manual day; see
the [package README](../README.md#how-this-fits-the-suite) for how the standalone and orchestrator-driven
roles fit together.

## Why a separate harness

`rank_strategies.py get_ranked_symbols` opens only the single best strategy per symbol per
night. Candidates are scarce (a typical night: 5-6 symbols, often 0-1 selected). Under
natural single-best-per-symbol selection, most strategies would starve and never reach a
statistically meaningful sample in weeks. `strat_test_harness.py` instead force-samples:
it opens a paper trade for **every** strategy that clears the screen on **every** viable symbol
each night, tagged into a per-strategy book in the shared `paper_trades.db` (in the cherrypick
data home, `~/.cherrypick/data/earnings`; see `docs/strat-test-portfolios.md`'s "book = strategy"
design).

## Fixed testing basis

Every night runs on one sizing basis — `available_capital_paper_mode` (the simulated NLV in
config) — so per-strategy comparison isn't confounded by differing capital or risk settings.
With `strat_test_portfolio: "per_strategy"` (the default), each strategy accumulates in its own
book (`arm='strat_test:<strategy>'`), so the comparison isolates the strategy itself.

## Cost model

Paper fills are cost-adjusted using tastytrade's real fee schedule (`cherrypick/earnings/costs.py`,
config's `tastytrade_costs` block) — not mid-price/zero-cost, the classic optimism bias,
worst for options:
- **Commission (open-only):** $1.00/contract to open, $0 to close, capped at $10/leg.
- **Pass-through fees:** clearing (~$0.10) + regulatory/ORF/FINRA-TAF/SEC (~$0.04) per
  contract, both sides.
- **Slippage:** a configurable fraction (default 12.5%) of each leg's bid-ask width, capped at
  15% of the leg's mid, applied at entry and exit.

`trades.entry_cost`/`exit_cost` store this separately from `trades.pnl` (which stays
gross) — cost-adjusted expectancy is computed downstream in `strategy_metrics.py`, never
baked into `pnl` itself.

## Nightly commands

**Entry window** (before close):
```
python -m cherrypick.earnings.strat_test_harness run_entries --date MM/DD/YYYY
```
Runs the shared scan once (`rank_strategies.evaluate_symbol` per calendar entry),
opens every (strategy, symbol) pair that clears the screen and builds/sizes successfully, logs
every candidate (selected or not) to `scan_log` tagged with its per-strategy book.

**Close window** (next morning):
```
python -m cherrypick.earnings.strat_test_harness run_closes
```
Manual/backfill only — the paper loop manages exits. `run_closes` closes the overnight strategies
unconditionally and lets the calendars and `expected_move_butterfly` hold on their own verdict,
pricing every close through the same generic exit-debit mechanism the real loop uses
(`scanner.compute_generic_exit_debit`), cost-adjusted.

## Weekly review

```
python -m cherrypick.earnings.strategy_report --since YYYY-MM-DD
# The console's Earnings page renders the same read models: http://127.0.0.1:5070/earnings
```
`strategy_report.py` prints a per-strategy table (sample count vs targets, win rate, profit
factor, expectancy, Sharpe, max drawdown, average IV crush, regime coverage) to stdout.
The console's Earnings page carries the charts.

`--json` prints the **verdict model** (`verdict.py`) instead, and the console's Earnings →
strategies page draws it rather than computing its own figures. It answers "is there an edge after
costs, and how sure are we" for each control arm: net ÷ capital at risk with a 90% interval from a
fixed-seed bootstrap that resamples **earnings events**, not trades. Every strategy opens on the same
name the same night, so ten trades on one report are one draw. The 30/100 sample targets above are
milestones for collecting data, not significance: an arm with 66 trades can still sit at t = 0.2. Rows
that record a defect rather than a decision are excluded by a dated rule list (`FLAG_RULES`: the
entry-tick closes 2026-09-16..10-15 and the stranded-expiry cohort opened 2026-08-25..27, see
[measurement boundaries](./measurement-boundaries.md)); `--include-flagged` keeps them.

**`--mode`**: the report defaults to `--mode paper` (this whole program is a paper test). `--mode live`
points it at `earnings_trades.db` instead, the history of the orders placed before v0.12.0, which
nothing writes now; its header reads `MODE: LIVE` and `--profile` defaults to `default`. In paper mode
`--profile` defaults to the whole strat_test family: the combined `strat_test` book plus every
`strat_test:<strategy>` sub-book. `--db PATH` overrides the DB path directly.

**IV crush**: `entry_iv`/`exit_iv` are the average live IV (from tastytrade's option-chain
greeks) across each trade's Sell-to-Open leg(s) — the side that's actually sold and later
crushes — captured at entry and again at exit via the same quote calls already made for
fill pricing (no extra network round trip). `strategy_metrics.iv_crush()` computes
`entry_iv - exit_iv` per trade (positive = IV fell, the expected post-earnings crush;
negative = IV actually rose); `avg_iv_crush()` averages it across a strategy's sample,
reported alongside the sample count that actually had both sides captured (greeks can be
momentarily unavailable, same discipline as every other None-able metric here).

## Sample targets

- **30 trades/strategy** — directional read (proceed with caution, not a conclusion).
- **100 trades/strategy** — statistical significance.

Don't conclude anything from a strategy with fewer than 30 trades. A strategy sitting at,
say, 12 trades after several weeks isn't a bug — check `regime_buckets` in the report before
assuming a gate needs loosening; some strategies (e.g. `broken_wing_butterfly`) are inherently
rare-regime and may only ever reach a directional read within a reasonable horizon.

## How the program runs

Nightly `run_entries` (the loop's 15:35 scan) accumulates trades, and a weekly `strategy_report.py`
review tracks progress toward 30 and then 100 per strategy, provisional expectancy and regime coverage.
Flag starved strategies, but don't loosen gates just to hit a count; check whether the regime is
genuinely rare first.

## Per-strategy regime expectations

- **Common-regime** (`iron_fly`, `iron_condor`, `directional_credit_spread`): expect to reach 30-100
  within the horizon on a typical earnings calendar.
- **Rare-regime**: `broken_wing_butterfly` (retired from the harness) needed a steep enough IV skew, so it fires less often
  and may only reach a directional read (30) rather than full significance (100) in the tested
  window.

## Evaluation

Per strategy, decide **keep / retire** based on:
- Cost-adjusted expectancy > 2x average cost per trade
- Profit factor > 1.5
- Max drawdown < 20% of capital basis
- Paper win rate roughly agreeing with the historical `scanner.compute_winrate` backtest
  (see `strategy_metrics.winrate_backtest_agreement`)

## Caveats (always read alongside the numbers)

- **Forward-only** — order construction needs live tastytrade chains, so there's no
  historical backfill; the horizon is real calendar weeks, not a fast backtest.
- **Correlated samples** — multiple strategies opened on the same symbol/night share one
  underlying earnings event. Per-strategy outcomes aren't fully independent draws.
- **Paper is still optimistic even with costs** — no real queue position, no emotional
  pressure. A real account would see larger drawdowns than the paper figure.
- **<100 trades isn't statistically significant; <30 isn't even directional.**
- Generate/read tearsheets for **end-of-window evaluation**, not to fine-tune gates
  mid-test — doing so overfits the strategy to this specific test window.

## See also

- `docs/strat-test-portfolios.md` — the per-strategy paper-book model this test writes into
- `cherrypick/earnings/sizing.py` — code-enforced risk-cap sizing
- `cherrypick/earnings/costs.py` — the tastytrade fee model
- `cherrypick/earnings/strategy_metrics.py` — the single source of truth for every number in the report/dashboard
