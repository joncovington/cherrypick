# Strategy Testing Plan: Thoroughly Testing Each Strategy in Paper Mode

Multi-week forward-test of all 7 implemented (defined-risk) strategies (`iron_fly`,
`double_calendar`, `iron_condor`, `atm_calendar`, `directional_credit_spread`,
`broken_wing_butterfly`, `reverse_fly`), until each has enough cost-adjusted evidence to
decide: enable live, keep paper, or retire.

This is a **separate program** from the live/paper trading loop (CLAUDE.md's Loop Steps).
It never touches `db.py`, never calls `tt.py execute_trade`, and never respects
`max_concurrent_earnings_positions` or the correlation block list — its whole point is to
open more than the loop ever would, on purpose, so every strategy accumulates a usable
sample.

## Why a separate harness

`rank_strategies.py get_ranked_symbols` opens only the single best strategy per symbol per
night. Candidates are scarce (a typical night: 5-6 symbols, often 0-1 selected). Under
natural single-best-per-symbol selection, most strategies would starve and never reach a
statistically meaningful sample in weeks. `strategy_test_runner.py` instead force-samples:
it opens a paper trade for **every** strategy that tiers Tier 1/2 on **every** viable symbol
each night, into its own isolated book (`profile='strat_test'` in the shared
`data/paper_trades.db` — see `docs/paper-trading-profiles.md`'s "profile = book" design).

## Fixed testing basis

Every night runs on one sizing profile (`balanced`: 100k capital, `risk_pct_multiplier`
1.0) so per-strategy comparison isn't confounded by which profile's gates/capital were
active. Risk-profile comparison (conservative/balanced/aggressive) is a separate, later
program that reuses this same instrumentation once individual strategies are validated.

## Cost model

Paper fills are cost-adjusted using tastytrade's real fee schedule (`src/costs.py`,
config's `tastytrade_costs` block) — not mid-price/zero-cost, the classic optimism bias,
worst for options:
- **Commission (open-only):** $1.00/contract to open, $0 to close, capped at $10/leg.
- **Pass-through fees:** clearing (~$0.10) + regulatory/ORF/FINRA-TAF/SEC (~$0.04) per
  contract, both sides.
- **Slippage:** a configurable fraction (default 25%) of each leg's bid-ask width,
  applied at entry and exit.

`trades.entry_cost`/`exit_cost` store this separately from `trades.pnl` (which stays
gross) — cost-adjusted expectancy is computed downstream in `strategy_metrics.py`, never
baked into `pnl` itself.

## Nightly commands

**Entry window** (before close):
```
python src/strategy_test_runner.py run_entries --date MM/DD/YYYY --profile balanced
```
Runs the shared live scan once (`rank_strategies.evaluate_symbol` per calendar entry),
opens every Tier 1/2 (strategy, symbol) pair that builds and sizes successfully, logs every
candidate (selected or not) to `scan_log` tagged `profile='strat_test'`.

**Close window** (next morning):
```
python src/strategy_test_runner.py run_closes --profile balanced
```
Closes every open `strat_test` position via the same generic exit-debit mechanism the real
loop uses (`scanner.compute_generic_exit_debit`), cost-adjusted.

## Weekly review

```
python src/strategy_report.py --since YYYY-MM-DD
python src/strategy_dashboard.py --since YYYY-MM-DD
```
`strategy_report.py` prints a per-strategy table (sample count vs targets, win rate, profit
factor, expectancy, Sharpe, max drawdown, average IV crush, regime coverage) to stdout.
`strategy_dashboard.py` writes a self-contained, offline `reports/strategy_dashboard.html`
(matplotlib charts as base64 PNGs — no server, no network) with equity curves,
drawdown/underwater plots, a regime-coverage heatmap, a rejection-reason histogram, and a
cross-strategy comparison grid with a Cumulative / Rolling-4-week / Rolling-1-week /
Per-week timeframe toggle. Both read `strategy_metrics.py`, so they can never disagree.

**Live vs paper (`--mode`)**: both tools default to `--mode paper` (this whole program is a
paper test, and `enable_live_trading` is off). `--mode live` points them at
`data/earnings_trades.db` instead of `data/paper_trades.db` — useful once live trading is
enabled and you want the same views over real fills. In live mode the report header reads
`MODE: LIVE`, the dashboard carries a red "LIVE — Real Money" badge (vs amber "PAPER —
Simulated") and writes a separate `reports/strategy_dashboard_live.html` so it never clobbers
the paper view, and `--profile` defaults to `default` (live trades aren't tagged
`strat_test`). `--db PATH` overrides the DB path directly if needed.

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
assuming a gate needs loosening; some strategies (e.g. `reverse_fly`) are inherently
rare-regime and may only ever reach a directional read within a reasonable horizon.

## Schedule

- **Week 0 — setup & dry run.** One night, confirm all 7 strategies construct, size, cost,
  persist, and close with no error.
- **Weeks 1-2 — calibration.** Nightly `run_entries`/`run_closes`. Goal is plumbing
  shakeout, not statistics: confirm every strategy fires at least a few times and every exit
  path (profit target, stop, close-window backstop) gets exercised at least once.
- **Weeks 3-8+ — accumulation.** Continue nightly. Weekly `strategy_report.py`/
  `strategy_dashboard.py` review: progress toward 30 then 100 per strategy, provisional
  expectancy, regime coverage. Flag starved strategies but don't loosen gates just to hit a
  count — check whether the regime is genuinely rare first.

## Per-strategy regime expectations

- **Common-regime** (`iron_fly`, `iron_condor`, `directional_credit_spread`, the two
  calendars): expect to reach 30-100 within the horizon on a typical earnings calendar.
- **Rare-regime**: `reverse_fly` needs a realized/expected move gap ratio > 1.10, and
  `broken_wing_butterfly` needs a steep enough IV skew — both fire less often, so they may
  only reach a directional read (30) rather than full significance (100) in the tested window.

## Evaluation & promotion

Per strategy, decide **enable live / keep paper / retire** based on:
- Cost-adjusted expectancy > 2x average cost per trade
- Profit factor > 1.5
- Max drawdown < 20% of capital basis
- Paper win rate roughly agreeing with the historical `scanner.compute_winrate` backtest
  (see `strategy_metrics.winrate_backtest_agreement`)

Promotion copies the validated strategy's exact parameters into the live config path;
`enable_live_trading` flips only after separate review. Then start the risk-profile testing
program (`docs/paper-trading-profiles.md`) on the survivors.

## Caveats (always read alongside the numbers)

- **Forward-only** — order construction needs live tastytrade chains, so there's no
  historical backfill; the horizon is real calendar weeks, not a fast backtest.
- **Correlated samples** — multiple strategies opened on the same symbol/night share one
  underlying earnings event. Per-strategy outcomes aren't fully independent draws.
- **Paper is still optimistic even with costs** — no real queue position, no emotional
  pressure. Expect live drawdown to run 1.5-2x the paper figure.
- **<100 trades isn't statistically significant; <30 isn't even directional.**
- Generate/read tearsheets for **end-of-window evaluation**, not to fine-tune gates
  mid-test — doing so overfits the strategy to this specific test window.

## See also

- `docs/paper-trading-profiles.md` — the risk-profile testing program this reuses
- `src/sizing.py` — code-enforced risk-cap sizing
- `src/costs.py` — the tastytrade fee model
- `src/strategy_metrics.py` — the single source of truth for every number in the report/dashboard
