**What this covers:** the frozen contract for the daily range-features study: what is measured,
against what baseline, which way each effect should point, and what result would close the study.
Declared **2026-10-03**. This file is the declaration and does not change except to record results
and retirement; changing a definition below is a new declaration, not a fix.

# Daily range features on SPX

## Why there is a question

Chart-pattern folklore claims that the shape of recent bars predicts what comes next. The careful
literature says that for large US stocks and indexes it does not predict **direction**, once data
snooping, costs and a real holdout are applied (Sullivan, Timmermann & White 1999; Hsu & Kuan 2005;
Bajgrowicz & Scaillet 2012). The best-known positive result, Lo, Mamaysky & Wang (2000), found that
patterns change the **shape** of the following return distribution (its spread and tails), not its
mean, and says outright that "informativeness does not guarantee a profitable trading strategy".
Dawson & Steeley (2003) replicated the mean returns as "not significantly different" and located
the difference in the higher moments.

Shape is what a short-premium book is exposed to. Every module in the suite sells premium against
how far SPX travels before expiry: flies and MEIC over the session, calendars over the week, bwb
over about seven sessions. No study the literature review found tests whether a chart feature adds
anything to a standard range-based volatility forecast. That is the question here, and only that.

## This is pre-registered on historical data

None of the features below has been computed against an outcome on this data. The session counts
in "Data" were read to size the segments; nothing else was. That makes the historical evaluation
segment a genuine pre-registration. It rests on nobody running the analysis before this file was
committed, so the forward holdout exists as the part that cannot have been seen.

**Already examined, therefore excluded:** inside day and outside day. Both failed the
daily-candle holdout recorded in [packages/flies/docs/openingrange.md](../packages/flies/docs/openingrange.md)
(inside day flipped sign, −0.011 in-sample, +0.225 out). They are not re-tested here.

## Data

- **Primary: SPX daily OHLC** from `streamcache.daily_bars` (both close routes), the series
  `core.marketregime` already reads. At declaration: 1,412 sessions, 2021-02-16 to 2026-10-01, none
  missing a price.
- **Calibration segment:** feature sessions *t* through **2022-12-30** (474 sessions). Used only to
  fit the baseline and fix tercile cut points. A calibration row whose outcome window reaches past
  2022-12-30 is dropped, so no outcome crosses into evaluation.
- **Evaluation segment:** *t* from **2023-01-03** through **2026-10-02** (938 sessions). The
  decision is made here.
- **Forward holdout:** *t* from **2026-10-05** onward.
- **Cross-check only:** the technicals end-of-day panel (655 names, from 2023-08-30). Those names are
  stocks and the suite trades SPX, so the panel is reported descriptively and never enters the
  decision.

## Frozen definitions

Notation: Hₜ, Lₜ, Cₜ are session *t*'s high, low and close. Every feature for session *t* uses bars
**through t's close and nothing later**. Each one is a reading available before session *t+1*
opens.

**Range as a fraction of price:** rₜ = (Hₜ − Lₜ) ÷ Cₜ.

**Outcome, horizon h:** yₕ(t) = (max Hₛ − min Lₛ over s = t+1 … t+h) ÷ Cₜ, for **h = 1, 5, 7**
(flies and MEIC, calendars, bwb).

**Baseline: HAR on ranges** (after Corsi 2009, in range form per Alizadeh, Brandt & Diebold 2002):

ln yₕ(t) = aₕ + b₁ ln rₜ + b₅ ln r̄₅(t) + b₂₂ ln r̄₂₂(t)

where r̄ₖ(t) is the mean of r over sessions t−k+1 … t. It is fitted by ordinary least squares on
the calibration segment, separately for each h. The coefficients are then frozen.

**Excess range:** eₕ(t) = ln yₕ(t) − the frozen baseline's fitted value. Every test is on eₕ, so a
feature counts only for what it adds beyond recent range at three speeds.

| feature | definition | declared sign on eₕ |
|---|---|---|
| `nr7` | 1 when Hₜ − Lₜ is strictly smaller than each of the previous six sessions' high − low, else 0 | **positive**: compression precedes expansion (Crabel; practitioner, untested in the refereed literature) |
| `close_extremity` | \|2(Cₜ − Lₜ) ÷ (Hₜ − Lₜ) − 1\|; undefined when Hₜ = Lₜ | **positive**: a close at the edge of its range precedes travel. Jiang, Kelly & Xiu (2023) found close position the main driver of their image model; mapping that onto range is this study's inference. |
| `channel_extremity` | \|2(Cₜ − Lmin) ÷ (Hmax − Lmin) − 1\|, with Hmax and Lmin over t−19 … t | **positive**: a close near a 20-session extreme precedes breakout travel |
| `round_distance` | distance from Cₜ to the nearest multiple of 100 SPX points, ÷ ATR20, where ATR20 is the mean of `marketregime.true_ranges` over t−19 … t | **positive**: take-profit orders cluster at round numbers and contain price (Osler 2003), so a close far from one travels further |

That makes four features at three horizons: **twelve tests, one family.**

## Statistics

- **Statistic.** For `nr7`, the mean of eₕ on NR7 sessions minus the mean on the rest. For each
  continuous feature, the mean of eₕ in its top tercile minus its bottom tercile. Tercile cut points
  come from the calibration segment's feature values, so nothing about the cut is chosen on
  evaluation data.
- **Inference.** A stationary block bootstrap (Politis & Romano) on the evaluation segment, with
  mean block length max(10, 2h) sessions, 10,000 resamples and seed 20261003. P-values are
  two-sided. Overlapping 5- and 7-session windows are handled by the block length, never by
  pretending the rows are independent.
- **Multiplicity.** Bonferroni across the twelve, so p < 0.05 ÷ 12 ≈ 0.0042. This is conservative
  on purpose: a null result is cheap here, a false finding is not.
- **Unit.** One session is one observation. The panel cross-check clusters by date, because 655
  names on one day share one market.

**What the sample can see.** NR7 fires about one session in seven by construction, so about 134
times in the evaluation segment. Assume a residual standard deviation near 0.35 log points, which is
typical of daily range models but not measured here. Then the smallest effect detectable at this
threshold is roughly 0.10 log points, about 10% of range. **A null result means "no effect of about
that size", not "no effect".**

## Decision rule

A feature × horizon is a **finding** only if all four hold:

1. p < 0.0042 on the evaluation segment;
2. the sign matches the declared sign;
3. the effect is at least 0.05 log points (about 5% of range): smaller is real-but-useless for
   strike placement;
4. at **60 forward sessions**, the forward estimate has the same sign. This is evaluated once, at
   that checkpoint, and is not re-tested until it passes.

A significant result with the **opposite** sign is evidence against that feature's mechanism. It is
recorded as such, not as a finding.

**The conditioner is reported, not tested.** Each statistic is also shown within the
`marketregime` volatility buckets (expanding or compressed, from bars strictly before *t*).
A cell under 14 sessions (`MIN_EFFECTIVE_N`) reads `not_yet_readable`. Those cells add no tests to
the family, and nothing in the decision reads them.

## Explicitly excluded, and why

- **Inside day and outside day.** Already examined and failed (see above).
- **Classic geometric patterns** (head-and-shoulders, double tops, triangles). They form about once
  per stock per year (Lo, Mamaysky & Wang's counts), so a single index cannot produce the roughly 200
  events per pattern a volatility difference needs.
- **Candlestick catalogues** (for example TA-Lib's 61 functions). The evidence is mixed and turns on
  how the exit is defined: Lu, Chen & Hsu (2015) find profits under one exit rule and none under
  another. Most are also direction calls.
- **Image or neural-network models.** Jiang, Kelly & Xiu's model is credible but ranks stocks
  against one another, never an index, and its training is random rather than deterministic.
- **Direction as an outcome.** This study asks how far, never which way.
- **Touching the expected move** (a triple-barrier outcome: does spot reach ±EM before expiry).
  The implied-vol history covers only about a year, and `core.impliedvar` is not recording yet. It
  gets its own declaration once a year of implied variance exists.
- **Floor-trader pivots.** They are a statement about the path *within* a session, so they belong to
  the intraday question below.

## The intraday (5-minute) question: not declared here

A 0DTE entry rule built from 5-minute SPX bars is the same question at a finer scale: does the shape
of the morning say how far spot travels before the close? It is not declared here because the data
cannot answer it yet.

- The recorded intraday SPX trail (the gex recorder's spot history) holds **74 sessions**, from
  2026-07-16.
- **The session is the unit.** Ten entry times in one session share one tape, so they are one
  observation, not ten.
- 74 sessions cannot resolve a range difference of the size this design needs. The 09:30–10:00
  window is already covered by the opening-range declaration, with its own 30-session checkpoint.
- **What would open it:** measuring how far back the broker serves SPX 5-minute candles.
  `scripts/probe_candles.py` measures history depth for futures and would need a small change to
  read the index. If years of 5-minute history exist, an intraday declaration in this same form
  becomes possible. If not, the trail accrues at about 250 sessions a year.

## Implementation

Planned home: `cherrypick.core.rangefeatures`. It does not exist yet. It will be pure functions over
daily bars, derived and never recorded, following `core.marketregime` and `core.openingrange`.

**Before any statistic is run,** a truncation-invariance test must pass and must be shown to fail.
Every feature computed on the full history must equal the same feature computed on bars cut off at
session *t*, for every *t*. Breaking it on purpose, by reading one bar ahead, must make the test
fail. That single test catches each look-ahead form in the code reviewed for this study:

- a condition reading the next bar (`shift(-1)`);
- a rolling window that includes the bar being decided on;
- a turning point that is redrawn later.

## What would retire it

- **No feature × horizon is a finding** on the evaluation segment: the study closes, and the result
  is recorded here.
- **Every finding fails the forward check:** the study closes as an in-sample artefact.
- **No re-cutting.** A different window, threshold or round-number step is a new declaration. Any
  later declaration on daily SPX range features must count these twelve tests in its own
  multiplicity.

## Status

**Reports only. Gates nothing.** No entry path reads it. If a finding survives, the route to a gate
is the suite's standing one, each step gating the next:

1. the feature becomes a `regimecuts` dimension for the module whose horizon it predicts;
2. then a declared paper arm beside control, differing in exactly one thing;
3. then an `advice.bounds` key, only after that arm has paid over at least 15 sessions.

## Results

*None yet.*
