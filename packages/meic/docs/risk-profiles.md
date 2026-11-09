# Risk presets and arm design rules

**What this covers:** the example four-tier preset ladder that `/set-risk-profile` can apply to the live
config, and the design rules every MEIC arm obeys (the file keeps its old name because the slash command,
`GATES.md` and `reference.md` cite it). Part of the [MEIC module](../README.md) in the cherrypick suite.

In this module's old vocabulary an arm was a "risk profile". The ledger column is `arm`; the arm
registry keeps the key `profiles`; the command keeps its name.

> **The ladder is an example, not shipped.** The package carries only `config.risk.example.json`, holding
> `control`. A machine's arm registry is its own `~/.cherrypick/config/meic.risk.json` (or the file
> `$MEIC_RISK_CONFIG` names). A machine that wants the ladder defines the tiers there, `enabled: false`
> for paper; `/set-risk-profile` refuses to run while only the shipped example exists.
>
> **The way to choose what live trades is `live.arm`** in the `live` block, which names a registry arm.
> `/set-risk-profile` instead writes a preset into the config's top level, which every paper arm that
> leaves a key unset inherits, `control` included. Read the warning in
> [`.claude/commands/set-risk-profile.md`](../.claude/commands/set-risk-profile.md).
>
> **`/set-risk-profile` mutates live config. Never pass it a paper arm** (`control`, `live-shadow`,
> `bp-*`, ...): `control` runs `overlap_scope: "none"` with no per-side stop, which only makes sense as
> independent paper samples, never as a live risk posture. The command does not filter on `enabled`; only
> the four ladder tiers are meant to be passed to it.

## The ladder

A preset is a named set of entry-gate thresholds. Each tier relaxes gates and pays for it with a tighter
stop, so risk is reallocated rather than only added. Every arm runs as an uncapped independent-sampling
stream (`max_concurrent_ics: 99`, `daily_ic_trade_target: 200`), so there is no position count to offset
against and **the tighter stop is the ladder's only offset**; the tiers differ solely in entry quality.

| Gate | conservative | moderate | aggressive | very-aggressive |
|---|---|---|---|---|
| `min_iv_rank` | 0.30 | 0.22 | 0.20 | 0.15 |
| `min_credit_pct_of_width` | 0.15 | 0.12 | 0.10 | 0.08 |
| `max_call_delta_entry` | 0.20 | 0.20 | 0.22 | 0.24 |
| `min_call_otm_pct` / `min_put_otm_pct` | 0.35% / 0.30% | same | 0.30% / 0.25% | 0.25% / 0.20% |
| `late_entry_bias_start_time` | 12:00 | 11:00 | 11:00 | 10:00 |
| `regime_vix_pause_threshold` | 25 | 25 | 25 | 30 |
| `regime_atr_pause_threshold_pct` | 0.015 | 0.015 | 0.015 | 0.020 |
| `stop_trigger_ratio` | 0.95 | 0.93 | 0.90 | 0.85 |

For aggressive and above, the tighter open/late call-delta ceilings (`_open_volatile`, `_late`) relax by
the same margin as the base ceiling so they stay proportionally below it and do not become the binding
constraint.

- **conservative**: fewest entries (about 1 to 2 a day), widest per-trade margin. For learning, uncertain
  markets or after a losing streak.
- **moderate**: a little more IV and credit slack and an earlier start. Move here when conservative
  refuses 40% or more of candidates and its win rate holds at 60% or better.
- **aggressive**: closer-to-money strikes, higher gamma; needs active stop management. Use after two or
  more weeks at moderate with 60%+ wins and no loss above 2% of account equity.
- **very-aggressive**: trades through VIX 25 to 30 and 5-day ATR 1.5% to 2.0%, where the mean-reversion
  edge is weakest. A deliberate one-week experiment with explicit drawdown limits, not a normal mode.

Do not skip tiers: relaxing gates in isolation creates risk, and the offsets only work if they compound.

**How `/set-risk-profile <name>` works.** It reads the preset from the machine's registry
(`paths.risk_profiles_path()`), backs up `meic.json` to `meic.json.bak`, overwrites the matching top-level
keys, records the registry's `active_profile`, and prints a before/after table. The next loop iteration
picks it up with no restart; in-flight positions are unaffected. Keys the preset does not name keep any
manual edit. `/set-risk-profile conservative` restores the ladder's baseline rung.

## Design rationale

The rules below are what a change to an arm must preserve.

### 1. The ladder's axis is riskier trades, not more trades

Climbing a rung loosens entry quality (IV-rank floor, credit floor, short-call delta, OTM distance).
"Aggressive" means each trade is a worse bet taken deliberately, not more bets, so **trade count is not a
ladder dimension**: `daily_ic_trade_target` is flat across every rung. A rising target alongside a falling
concurrency cap moved three signals in three directions and was arithmetically unreachable at the top.

### 2. Two axes: risk appetite x instrument

Risk appetite (the arm) and the instrument (the symbol) are orthogonal, and the unit of measurement is
their product: **one portfolio per (arm x symbol) pair**, each with its own `max_concurrent_ics` and
daily-entry budget. Budgets shared across symbols starve whichever symbol is processed last (one symbol
once went 1,313 iterations without a fill, skipped for `max_concurrent_ics_reached`). Pinning a symbol
into an arm's identity fuses the axes and makes "the same arm on another instrument" inexpressible, so
study arms leave `symbols` unset and let the grain supply the instrument axis.

### 3. Thresholds are arm-relative, never shared absolutes

Any gate expressed as an absolute IV-rank number is a latent bug, because each arm has its own
`min_iv_rank`. A shared absolute sits in a different place relative to each arm's floor and gives the
loosest arm the most relief, which is backwards. (A flat 0.35 low-IV relief ceiling with a flat 0.10
relaxed floor once put all four tiers on the same credit floor whenever IV rank sat under 0.35.) Both
halves are derived from the arm itself:

| Derived value | Formula |
|---|---|
| Low-IV relief ceiling | `min_iv_rank + low_iv_credit_floor_iv_rank_offset` (0.05) |
| Low-IV relief floor | `min_credit_pct_of_width × low_iv_credit_relief_multiple` (0.85) |
| Late-entry-bias ceiling | `min_iv_rank + late_entry_bias_iv_rank_offset` (0.15) |

The absolute keys (`low_iv_credit_floor_iv_rank_max`, `low_iv_min_credit_pct_of_width`) are still
honoured if an arm sets them. **The multiple is 0.85 because the relief must stay shallow enough to
preserve ordering:** a deeper cut (0.67) let a tier inside its borderline band undercut the next tier's
plain floor.

### 4. The daily target is guidance, not a cap

Hitting `daily_ic_trade_target` does not block entries. Past it, the credit floor is multiplied by
`over_target_credit_multiple` (1.5), so an extra trade is available only when premium is rich, and a
marginal setup is declined with `over_target_credit_below_floor`. An arm that opts into `stagger_entries`
keeps a hard cap, since spreading a fixed number of entries across the session is the point of staggering.

### 5. Open questions

- **IV rank vs IV percentile as the entry gate.** Rank is `(current − 52wk low) / (52wk high − low)`, so
  one spike permanently compresses it; percentile is outlier-robust. They can disagree sharply (NDX reads
  rank 0.19 against percentile 0.95), while SPX and SPY agree. Both are captured per symbol per day. If the
  gate moves to percentile, every floor must be re-baselined; the scales differ.
- **Whether rank should shape construction rather than gate entry.** A low rank beside a high percentile
  says premium is rich but this product has gone much higher, a tail-risk signal arguably better expressed
  by pushing strikes out and demanding more credit than by refusing the trade.

### 6. Invariants any change must preserve

1. The effective credit floor is **monotonic** across the ladder at every IV level: a stricter tier must
   never accept thinner credit than a looser one (enforced by test).
2. Every relaxed gate is paired with a compensating constraint (here, a tighter stop).
3. No gate threshold is an absolute IV-rank number; derive it from the arm's own `min_iv_rank`.
4. Trade count is not a ladder dimension.
5. Risk appetite and instrument stay separate axes; the portfolio is their product.
6. Paper study arms are never applied to live config.
