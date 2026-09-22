**What this covers:** the frozen contract for the opening-range study — what is measured, against
what, which way the effect should point, and what result would retire it. Declared **2026-09-22**.
Part of the [flies module](../README.md) in the cherrypick suite. The findings, when there are any,
go to [experiment-log.md](experiment-log.md); this file is the declaration and does not change
except to record that it was retired.

# The opening range (09:30–10:00 ET)

## Why there is a question

Both flies and MEIC are forbidden to enter before **10:00 ET** (`no_entry_before`,
`entry_window_start`). That makes 09:30–10:00 a half-hour of market information available to every
entry decision and used by none of them.

A butterfly only completes if spot walks away from its centre — the module's own measurement
(median drift 15–17 SPX points against a 5-point wing) says completion IS travel. So if the opening
range forecasts the session's travel, that forecast is available exactly when the decision is made.

## This is a declared holdout, not a pre-registration

The features below were chosen **after** examining 38 sessions of this same data, which found flies
control completion splitting 68.5% (narrow opening range) vs 82.2% (wide) — and then found that
conditioning on the volatility regime collapsed it (low-vol 78.2% vs 79.7%, gone; high-vol 65.2%
vs 76.9%, surviving on 8–9 sessions a cell, under the floor).

So: **every session on or before 2026-09-22 is in-sample.** It chose these features and cannot also
test them. Every session after is out-of-sample. Calling this "pre-registered" would be false; what
is genuinely frozen here is the *definitions*, on the date above.

## Frozen definitions

Window is 09:30:00 ≤ t < 10:00:00 ET, six 5-minute buckets on wall-clock boundaries, ET via
`zoneinfo` so a DST boundary cannot shift it. **A session missing any bucket is `unmeasured`** and
is never interpolated — a range over four of six buckets is a different measure wearing the name.

| feature | definition |
|---|---|
| `or_points` | max − min of spot over the window |
| `or_atr` | `or_points` ÷ ATR20, ATR from `marketregime.true_ranges` over the 20 completed daily bars **before** the session, bars via `streamcache.daily_bars` (both close routes) |
| `position` | (last close − min) ÷ (max − min); 1.0 means the window ended on its high |
| `efficiency` | \|last close − first open\| ÷ Σ\|closeᵢ − closeᵢ₋₁\| over the six bucket closes; 1.0 a straight line, 0.0 a round trip |
| `gap_atr` | (first tick − prior session close) ÷ ATR20 |
| `or_vs_prior` | `or_points` ÷ prior session's (high − low) |
| `regime` | `marketregime` swing quadrant, from bars strictly before the session — **the declared conditioner** |

Implementation: `cherrypick.core.openingrange`. Changing any definition above is a **new
declaration**, not a fix.

## Outcomes, in causal order

The mechanism is opening range → session travel; a strategy result is downstream of travel. So the
primary outcome is the market's, which also has the most sessions because it does not require that
anything traded.

1. **Primary — `rest_of_day_range_atr`**: (max − min of spot, 10:00 ≤ t ≤ 16:00) ÷ ATR20.
   Deliberately excludes the opening window, so the opening range cannot mechanically inflate its
   own outcome.
2. **Secondary — flies completion**, locked to arm `control`, `entry_mode='legged'`, symbol `SPX`,
   via `flies.analytics.completion_trend` (which has no arm filter and must be post-filtered —
   never blended across arms).
3. **Tertiary — MEIC**, via `meic.analytics.daily_rollup`.

## Sign expected, per module

Declared so that opposite results read as confirmatory rather than "mixed":

- **flies: wide opening range should HELP completion.** Travel completes a fly.
- **MEIC: wide opening range should HURT.** An iron condor wants spot to stay put.

One hypothesis, two signs. A result where both move the same way is evidence *against* the
mechanism, not for it.

## Decision rule

- **Unit is the session, never the entry.** Entries inside one session share a tape. Every count
  is sessions; a table of hundreds of entries across dozens of sessions reads as powerful and is
  not.
- **Floor:** `MIN_SESSIONS = 14` per cell — the same number as `MIN_EFFECTIVE_N` and
  `experiment.MIN_SESSIONS_FOR_INTERVAL`, so the question does not resolve differently depending
  on which file a reader opens. A cell under it reports `not_yet_readable`, never a difference.
- **Headline** (median split on `or_atr`): evaluated at the first monthly checkpoint holding
  **≥ 30 out-of-sample sessions**. Estimator is `meic.analytics.session_bootstrap`, the suite's
  session-axis bootstrap, run over the paired per-session values `study()` returns. Null if the
  95% CI on the difference includes zero.
- **Regime-conditioned:** evaluated only when **both** volatility regimes hold ≥ 14 out-of-sample
  sessions. Until then the answer is "not yet readable", which is not the same as "no effect".
- **A month is a checkpoint, not an endpoint.** A null at any checkpoint is recorded and
  collection continues; it is not re-cut until it passes.

## Explicitly excluded

- **Inside day and outside day.** Both failed the daily-candle holdout — inside day flipped sign
  (−0.011 in-sample, +0.225 out). Recording the refusal matters as much as recording the keep.
- **Any breakout or directional rule.** The refereed evidence is weak (one positive, Holmberg et
  al. 2013); the widely-cited Zarattini/Aziz papers are unrefereed with conflicted authors, and an
  independent replication has their P&L crossing zero at ~2.2¢/share against a ~1¢ spread. This is
  a conditioner, not a signal.
- **Deseasonalising the window.** The intraday U-shape (Wood/McInish/Ord 1985;
  Andersen–Bollerslev 1997) is an objection to comparing *different times of day*. Every session
  here is measured over the same 09:30–10:00 window, holding the seasonal constant.

## What would retire it

- The headline split's 95% CI includes zero at two consecutive checkpoints with ≥ 30 out-of-sample
  sessions each → the opening range does not forecast session travel for this symbol, and the
  study closes.
- Or the effect survives only in the vol regime it was found in and vanishes in the other once
  both clear 14 sessions → it was the regime, which is the answer the in-sample pass already
  suspected.
- Or flies and MEIC move the same direction → the mechanism is wrong, whatever the significance.

Retirement is recorded here and the finding written to `experiment-log.md`.

## Status

**Reports only. Gates nothing.** No entry path reads it. The route to a gate, each step gating the
next: report → a declared paper arm beside control differing in exactly one thing, with its own
retirement condition → a `advice.bounds` key only after ≥ 15 sessions of that arm paying. The gate,
if it ever exists, would be a day-level refusal evaluated once at 10:00 beside
`engine.trend_bucket_refusal` and `engine.containment_refusal`, opt-in per arm and off when unset.
