# Examples & Case Studies

Worked examples showing how a real night's candidates get ranked, sized, entered, and exited —
grounded in the actual composite scoring formula and CLI commands, not a hypothetical scenario.

---

## Example 1: A Two-Symbol AMC/BMO Night

### Setup

3:00 PM ET, two names reporting around tonight's/tomorrow's session: AMC-timed `NVEX` (after
today's close) and BMO-timed `BNKX` (before tomorrow's open). `available_capital_paper_mode` is
$100,000, `max_concurrent_earnings_positions` is 3.

```bash
python src/rank_strategies.py get_ranked_symbols --date 07/08/2026
```

### What Each Symbol's Evaluation Looked Like

**NVEX** — high IV/RV ratio, but term structure and skew both point toward a wide, less
directional structure:

```
iron_fly:    Tier 2 (near_miss: iv_rv_ratio_near_miss), score 0.0183
iron_condor: Tier 1, score 0.0201
reverse_fly: Reject (min_skew_abs not met)
... other strategies: Reject or lower score
→ best_strategy: iron_condor, best_score: 0.0201
```

**BNKX** — tight term structure, clean winrate sample, textbook iron_fly setup:

```
iron_fly:      Tier 1, score 0.0254
directional_credit_spread: Tier 2, score 0.0198
... others: Reject
→ best_strategy: iron_fly, best_score: 0.0254
```

### Cross-Symbol Ranking

```json
{
  "ranked": [
    {"symbol": "BNKX", "composite_score": 0.0254},
    {"symbol": "NVEX", "composite_score": 0.0201}
  ],
  "selected": ["BNKX", "NVEX"]
}
```

Both clear `max_concurrent_earnings_positions` (only 2 of 3 slots used) and neither shares a
`correlation_block_list` grouping, so both get selected — no waitlist logic needed tonight.

### Building the Orders

```bash
python src/strategies/iron_fly.py get_order --symbol BNKX --earnings_date 2026-07-09 --earnings_timing "Before market open"
python src/strategies/iron_condor.py get_order --symbol NVEX --earnings_date 2026-07-08 --earnings_timing "After market close"
```

```
BNKX iron_fly: sell 97 straddle, wings at 90/104 (3.0x credit multiple),
  entry credit $3.18/spread, quantity 8, capital_at_risk $1,940 (below max_risk_per_trade_pct)

NVEX iron_condor: sell 11/16 call spread + 5/0 put spread,
  entry credit $0.80/spread, quantity 12, capital_at_risk $3,840
```

Paper mode: both recorded via `db_paper.py save_trade`, nothing submitted to the broker. Live
mode would submit both via `tt.py execute_trade --live`.

### Overnight and Next-Morning Outcome

```
NVEX announces after close, gaps +10%.
Next morning 09:15 ET (Step 3c): iron_condor's exit_debit computed from live quotes —
  profit came in under the 50% target, exit_debit stayed under the 1.5x stop → hold.
09:45 ET (Step 3, unconditional close-window): still open → close regardless of P&L.
  Realized: -$310 net (defined risk — the wings capped it well short of the raw
  spread-width max loss).

BNKX announces before tomorrow's open, moves +1.2%, mild and well within the straddle's
  expected move.
09:15 ET (Step 3c): exit_debit computed at $1.53/spread.
  profit = $3.18 - $1.53 = $1.65, which is ≥ $3.18 * 0.50 ($1.59) → close_all, profit_target.
  Realized: +$1,320 across 8 spreads (net of costs via costs.py, slightly lower than gross).
```

Net for the night: a loss on NVEX largely offset by a win on BNKX — a fairly ordinary two-name
night, not a "everything worked" story. Read the actual `scan_log` rows for both symbols
(`strategy = "_ranked"` summary plus each strategy's own evaluation row) to see this full trail
persisted, not just the console output.

---

## Example 2: More Qualifying Candidates Than Capacity

### Setup

Five symbols tier Tier 1/2 across various strategies on the same night, but
`max_concurrent_earnings_positions` is 3.

```
Rank 1: SYMA  best_strategy: iron_fly              score 0.0231
Rank 2: SYMB  best_strategy: iron_condor            score 0.0198
Rank 3: SYMC  best_strategy: directional_credit_spread  score 0.0177
Rank 4: SYMD  best_strategy: broken_wing_butterfly  score 0.0160
Rank 5: SYME  best_strategy: atm_calendar           score 0.0142
```

`scanner.select_positions()` walks this list top-down. The first 3 (SYMA, SYMB, SYMC) get
selected. SYMD and SYME are logged with `outcome: "concurrency_cap_reached"` — not silently
dropped, just recorded as skipped with the specific reason, visible in both the console output
and `scan_log`.

If SYMB had instead shared a `correlation_block_list` grouping with SYMA (say, both regional
banks reporting the same night), `select_positions()` would skip SYMB with
`outcome: "correlation_blocked"` and backfill SYMD into that third slot instead of leaving it
empty — diversifying across names rather than concentrating in the two highest scorers alone.

---

## Example 3: Reading a Rejection Trail

A quiet night where nothing entered — this is the more common outcome on any given weeknight,
and the point of this example is showing it's diagnosable, not mysterious:

```bash
python src/rank_strategies.py get_ranked_symbols --date 07/10/2026
```

```json
{
  "symbols": [
    {
      "symbol": "SMLCAP",
      "outcome": "rejected_no_viable_strategy",
      "reason": "iron_fly: front_expiration_days_too_far_out; iron_condor: front_expiration_days_too_far_out; atm_calendar: no_weekly_options"
    },
    {
      "symbol": "MEGACAP",
      "outcome": "rejected_no_viable_strategy",
      "reason": "iron_fly: iv_rv_ratio_below_threshold; iron_condor: iv_rv_ratio_below_threshold"
    }
  ]
}
```

`SMLCAP` failed because it only has monthly options — a small/mid-cap name legitimately falling
outside a 9-day front-expiration window built for weekly-optioned liquid names (see
[Screening Criteria](./screening-criteria.md)'s note on this exact pattern). `MEGACAP` failed
because its options simply weren't pricing in enough premium relative to its own historical
realized moves that night — IV/RV below the 1.25 floor every strategy shares. Neither is a bug;
both are the hard filters doing their job.

---

## Example 4: Forced-Sampling vs. Production Selection, Side by Side

On the same night, `/paper-start`'s forced-sampling program and the production
`rank_strategies.py` selection can produce different outcomes for the same symbol, and that
difference is intentional:

```bash
python src/strategy_test_runner.py run_entries --date 07/08/2026 --profile balanced
```

If `NVEX` tiers Tier 1/2 under both `iron_condor` and `directional_credit_spread`,
`strategy_test_runner.py` opens **both** into the `profile='strat_test'` book — every strategy
that qualifies gets its own sample, not just the single best. Meanwhile that same night's
`rank_strategies.py get_ranked_symbols` run would have picked only `iron_condor` (the
higher-scoring one) for the actual production paper/live book. Both write to entirely separate
`profile` values in the same database, so running both the same night doesn't conflict — it's
how the forced-sampling program accumulates a usable sample for every strategy instead of
starving whichever one rarely wins the head-to-head comparison.

---

## Example 5: Checking Accumulated Results

Once a few weeks of `/paper-start` cycles have run:

```bash
python src/strategy_report.py --mode paper --profile strat_test --strategy iron_condor
```

```
iron_condor (paper, profile=strat_test)
  Trades: 34 / 30 target (directional sample reached)
  Win rate: 58.8%
  Profit factor: 1.42
  Expectancy (cost-adjusted): $87.30/trade
  Sharpe: 0.61
  Max drawdown: -$1,240
  IV crush (entry_iv - exit_iv, avg): 0.087
```

`python src/strategy_dashboard.py --mode paper --profile strat_test` writes the same numbers as
an HTML dashboard with equity curves and a regime heatmap. See
`docs/strategy-testing-plan.md` for what sample sizes actually mean here and
`docs/strategy-optimization.md` for the specific hypotheses these numbers get checked against
before any config value changes.

---

## Navigation

**← Previous:** [Exit Strategy Guide](./10-exits.md)
**← Return to:** [Documentation Index](./README.md)
