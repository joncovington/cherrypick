# Entry Conditions Framework

How a candidate actually gets from "reports earnings tonight" to "here's an order" — no
decision tree lookup table, just each strategy's own tiering plus one cross-strategy ranking
step.

---

## The Two-Layer Model

There's no single global "entry conditions" gate that all seven strategies share and a router
that then picks a bucket. Instead:

1. **Each strategy tiers itself.** `src/strategies/<name>.py`'s `apply_tiering()` runs that
   strategy's own hard filters and near-miss bands against a symbol and returns `Tier 1`,
   `Tier 2`, `Near Miss`, or `Reject` with specific reasons. `iron_fly`'s version is documented
   in full in [Screening Criteria](./screening-criteria.md) — the source of truth for hard
   filter numbers, near-miss bands, and the composite scoring formula. The other six strategies
   share the same shared-engine plumbing (`scanner.py`'s liquidity gates, IV/RV computation,
   winrate backtest) but plug in their own thresholds and a few strategy-specific criteria
   (`double_calendar`'s realized-move dispersion, `broken_wing_butterfly`/`directional_credit_spread`/`reverse_fly`'s
   skew gate, the calendars' `back_month_min_days_after`). See each strategy's own config block
   in [Configuration Guide](./03-configuration.md) for its exact numbers.

2. **Cross-strategy ranking picks a winner per symbol.** A single symbol can tier Tier 1 or
   Tier 2 under more than one strategy at once — a name with rich IV/RV and negative term
   structure might legitimately qualify for `iron_fly`, `iron_condor`, and `directional_credit_spread`
   all on the same night. Opening more than one of those on the same underlying would just be
   the same overnight gap risk twice, not diversification. `src/rank_strategies.py` resolves
   this: it runs every registered strategy's `apply_tiering()` against every symbol on the
   merged today-AMC/tomorrow-BMO calendar, keeps only the Tier 1/Tier 2 results, and picks each
   symbol's single highest-scoring strategy.

```bash
python src/rank_strategies.py get_ranked_symbols --date MM/DD/YYYY
```

This is what the live/paper loop's Step 4b actually calls at entry time (see `CLAUDE.md`'s Loop
Steps) — there's no separate, hand-maintained routing table to keep in sync with it.

---

## How the Winning Strategy Is Chosen

Within a symbol, `rank_strategies.py` reuses `scanner.compute_composite_score()` — the same
scoring formula each strategy already uses to rank its own candidates against each other:

```
score = abs(term_structure) * iv_rv_ratio * shrunk_winrate
```

(`shrunk_winrate` pulls a thin-sample winrate toward a neutral 0.5 prior — see Screening
Criteria's ranking section for why.) Whichever strategy scores highest for that symbol wins;
`double_calendar` and `broken_wing_butterfly`/`directional_credit_spread`/`reverse_fly` substitute their own
signal (dispersion, skew) into the same formula shape rather than getting a separate scoring
system. This is a **relative** score used only to break ties between strategies that already
both cleared their own bar — it is not itself a pass/fail gate.

**Important caveat, directly from the code's own docstring**: this comparison is not
risk-adjusted for how differently these strategies pay off (defined-risk credit vs. debit,
different capital consumption per contract). That calibration is deferred until there's enough
real trade data (via `strategy_test_runner.py`'s forced-sampling paper program) to justify a
specific adjustment — see [Strategy Optimization Research](./strategy-optimization.md) for the
hypotheses queued to validate this properly.

---

## After a Winner Is Picked: Portfolio-Level Selection

Cross-strategy ranking produces one candidate per qualifying symbol, but a night can have more
qualifying symbols than `max_concurrent_earnings_positions` allows, or two symbols that
collide on `correlation_block_list`. `scanner.select_positions()` (the same function every
single-strategy `get_candidates` already uses) walks the ranked list, applies the concurrency
cap and correlation blocking, and backfills the next-best non-conflicting candidate into any
skipped slot rather than leaving it empty. Every skip is logged with its specific reason, not
silently dropped.

`rank_strategies.py` also writes a full audit trail to `scan_log` — one row per (symbol,
strategy) evaluated, plus a summary row per symbol (`strategy = "_ranked"`) explaining both why
the winning strategy beat its within-symbol runner-up and where that symbol ranked against the
rest of the night's universe. Query `scan_log` directly if you want to see the full evaluation
trail for a specific past night, not just tonight's console output.

---

## Entry-Time Re-Verification

The scan that produces tonight's ranked list typically runs earlier in the afternoon; by the
time the entry window actually opens, prices and IV may have moved. `rank_strategies.reverify_symbol()`
re-runs the winning strategy's own `apply_tiering()` fresh, immediately before order submission,
and rejects if it's fallen out of Tier 1/2 since the scan. This is Step 4b's re-verification
check in `CLAUDE.md`'s Loop Steps, and Layer 2 of [Screening Criteria](./screening-criteria.md#layer-2--entry-time-re-verification-immediately-before-order-submission-not-at-scan-time) —
same liquidity/term-structure/expected-move checks, just re-run live rather than trusted from
the earlier scan.

---

## Seeing It Work on a Real Night

There's no separate "test the entry framework" script — the framework *is* the ranking command,
so running it against a real date is the test:

```bash
python src/rank_strategies.py get_ranked_symbols --date MM/DD/YYYY
```

Read the `reason` field on each symbol in the output. A quiet night with mostly
`rejected_no_viable_strategy` or `tier_excluded` outcomes isn't a bug — it means nothing on that
night's calendar cleared any strategy's hard filters. See
[Screening Criteria](./screening-criteria.md) for what each specific rejection reason means.

---

## Adjusting Thresholds

There's no separate "entry condition" config block distinct from each strategy's own — every
threshold mentioned above lives under `strategies.<name>` in `config/config.json`. See
[Configuration Guide](./03-configuration.md) for the full parameter list per strategy, and
`docs/strategy-optimization.md`'s "do not blind-tune" protocol before changing anything based on
a single night's result: change one parameter, run a real sample through
`strategy_test_runner.py`'s paper program, and compare cost-adjusted expectancy before deciding
it actually helped.

---

## Navigation

**← Previous:** [Configuration Guide](./03-configuration.md)
**Next →** [Strategy Guide](./05-strategies.md)
