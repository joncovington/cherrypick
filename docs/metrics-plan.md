# Trading-metrics expansion — a proposal

*Drafted 2026-08-23, from a survey of standard algorithm-evaluation metrics mapped against what the
suite already computes. A design proposal, not shipped work — the companion to
[regime-recorder-plan.md](regime-recorder-plan.md), and its read-side complement: that plan records
what the market was doing; this one scores what the strategies did, including cut by that record.
When it ships, the landing note belongs in `docs/history/`.*

## What exists, and what the gaps are

`cherrypick.core.metrics` is the shared calibration bundle — one vocabulary for promotion evidence
over the normalized closed-trade records the per-schema readers emit. It holds `return_on_capital`,
a **deliberately per-trade, un-annualized** `sharpe`, `max_drawdown` over the session-ordered
equity path, and `sample_progress`. The modules keep richer per-module analytics (completion rates,
counterfactuals, exposure telemetry), and the suite's standing honesty rules — net of fees always,
unknowns stay `None` with coverage counts, eras never pooled, sessions as the effective n — already
cover ground most metric suites miss.

The survey (sources in the git history of this file's landing) found the standard families split
cleanly into covered, cheap-to-add, and genuinely-missing:

| Family | Covered today | Gap |
|---|---|---|
| Edge | net-after-fees, RoC, per-module win/loss | expectancy, profit factor |
| Risk-adjusted | per-trade Sharpe | Sortino, SQN |
| Path | max drawdown | drawdown duration / time-to-recovery |
| Tail | floor/upper-bound honesty rules | **CVaR (expected shortfall) and worst-session — the highest-value gap** |
| Trade path | best-completing counterfactuals, completion latency | **MAE/MFE, derivable read-side from mark paths already recorded** |
| Execution | fee reconcile, slippage stack, no-fill outcomes | — (covered) |
| Robustness | sample gates, era separation, exact pairing, sessions-as-n | a trials-aware correction for selection across arms |

Why the tail family leads: this suite predominantly sells premium (MEIC, curve, earnings, flies'
uncompleted branch), and short-premium return series systematically show high win rates and
flattering Sharpe/profit factors *until the tail arrives*. Win rate and profit factor are
structurally misleading for these books; expected shortfall and worst-session are the honest reads,
and nothing computes them today.

## Design rules, inherited not invented

- **Per-trade, un-annualized, cross-module.** The existing `sharpe` convention extends to every new
  ratio: annualizing a 0DTE series and an overnight-earnings series differently would defeat the
  bundle's whole purpose. Sortino and SQN take the same per-trade net series Sharpe already takes.
- **Unknowns stay `None`, never 0, with coverage counts.** A tail metric below its minimum sample
  refuses; a record without capital contributes nothing to RoC. New metrics inherit the
  `*_coverage` convention verbatim.
- **Tail metrics key on SESSIONS, not trades.** Rows are not draws (the flies `regime_coverage`
  lesson): twenty trades on one day observe one market between them. CVaR and worst-session are
  computed over per-session nets, and refuse below a declared minimum session count.
- **Computed in Python, rendered by the console.** The console computes no verdicts (its own rule):
  every metric lands in `core.metrics` or a module's own analytics layer, reaches the console
  through the existing readers/bridges, and the page draws it. "Mirror a query, bridge a
  derivation" applies — MAE/MFE is a derivation and goes out through the module that owns the
  marks.
- **Nothing here is measurement-affecting.** All of it is read-side over stored rows; no decision
  path, cadence, or recorded meaning changes. It can land any time, no boundary batching.

## Phase 1 — extend `core.metrics` (pure functions, same record shape)

Six additions, each with the shown-to-fail test the house rule requires:

- **`expectancy`** — mean per-trade net: the edge in dollars, the number a sample-size gate
  multiplies.
- **`profit_factor`** — gross profits ÷ gross losses; `None` when either side is empty (a book
  with no losses yet has an undefined PF, not an infinite one).
- **`sortino`** — per-trade, downside deviation in the denominator; `None` below 2 losing samples.
- **`worst_session` and `cvar`** — over per-session nets: the single worst, and the mean of the
  worst tail (default the worst 10%, quantile declared in the result). Refuse below a minimum
  session count (proposed 20) — a CVaR over six sessions reads as a risk number and is not one.
- **`sqn`** — √n × mean ÷ stdev of per-trade nets. Deliberately reported BESIDE
  `sample_progress`, never replacing it: SQN folds sample size into the score, the progress gate
  keeps it visible.
- **`drawdown_span`** — alongside `max_drawdown`'s depth, the longest peak-to-recovery stretch in
  sessions (ongoing drawdowns reported as open, not clamped). Depth without duration hides the
  difference between a bad week and a bad quarter.

All six fold into `calibration_reading` with coverage counts, so `calibrate` and the advisor's
promotion evidence pick them up through the one existing seam.

## Phase 2 — MAE/MFE, read-side, over marks already recorded

Maximum adverse/favorable excursion — the deepest a position went against its entry and the best
it got before close — answers "were the exits any good" and "where would a stop actually have sat."
The raw material already exists wherever a module records a mark path:

- **calendars** (`dc_marks`), **curve** (`curve_marks`), **pmcc** (`pmcc_marks`) — full per-tick
  paths, refusal-aware (`usable = 0` rows are excluded from excursions, never read as zero).
- **bwb** — the cohort-keyed trigger-tick path.
- **flies/meic** — no general per-position mark path (0DTE, and flies' `best_completing_*`
  trackers are the MFE idea already applied to its own mechanism); they join this phase only where
  their stored rows support it, and that limit is stated on the surface rather than papered over.

Split of labor: `core.metrics` gets the one generic pure function (excursions over an ordered mark
series relative to an entry basis); each module's analytics layer — the one query layer every read
surface already goes through — pairs its own marks to its own positions and feeds it. The product
is two per-position numbers plus their distributions, exposed exactly like the module's existing
analytics.

## Phase 3 — trials-aware honesty (selection across arms)

The suite runs many concurrent variants — flies' ~10 arms, every `advised:` twin, curve's three
books. Picking the best performer of N trials and reading its Sharpe at face value is the exact
selection-bias setting the Deflated Sharpe Ratio (Bailey & López de Prado) was built for. Full DSR
needs the variance of Sharpe estimates across trials and non-normality corrections — research-grade,
not phase-1 material. The cheap, deterministic first rung ships instead:

- Every promotion reading carries **"selected from N concurrent arms over M sessions"** — the
  trials count made visible beside the metric it inflates, the same move `sample_progress` made for
  sample size.
- A **Probabilistic Sharpe** reading (the probability the true Sharpe exceeds zero given n, skew,
  kurtosis) as a `core.metrics` function — closed-form, testable, and honest about short samples.
- Full DSR stays an open question at the bottom of this file until someone needs it enough to
  justify its inputs.

## Phase 4 — console surfaces

Conventions first: every number arrives computed from Python (reader or bridge per the rules
above); the pages draw with the house SVG kit (`components/Charts.tsx` — `LineChart` with its
fill-to-zero equity/underwater mode, `BarChart`, `SignedBar`, `SeriesLegend`, plus the stat-tile
grid and `DataTable`); axis text uses the shared tokens; and the dataviz design pass applies when
the components are actually built.

A survey of reference surfaces (2026-08-23: quantstats/pyfolio tearsheets, the journal dashboards —
TradesViz, Edgewonk — and systematic-monitoring practice) plus an inventory of the console's own
pages sharpened this phase in three ways. First, the console is already STRONG on mechanism
visuals — the payoff forests, the flies session timeline, the GEX ladders — and thin on the
evaluation layer the reference tools treat as table stakes: rolling metrics, distributions,
consistency views. Second, coverage is uneven: MEIC and flies carry rich performance visuals while
calendars, pmcc, curve, bwb, the advisor, and both report tabs are tables-only. Third, the two
most valuable tearsheet panels ALREADY EXIST in the console, each welded into one module's page:
MEIC's daily-P&L calendar heatmap (`MeicDeepCards`) and flies' cumulative+underwater twin
(`Flies/PerformanceTab`). The highest-leverage work is extraction, not invention.

**Step 0 — extract, then spread.** Promote the calendar heatmap and the equity+underwater twin
into the shared kit beside `Charts.tsx`, then give every module's history/performance tab both.
This one move brings the four chartless module pages to the industry-baseline pair for the cost
of a refactor. New primitives, all in the spare hand-rolled house style, no dependency added:
`CalendarHeatmap` and `EquityUnderwater` (extractions), `Sparkline` (a tiny `LineChart` variant),
`Histogram` (a `BarChart` adaptation), `Scatter` (genuinely new), and `LevelStrip` (a number line).
The journal tools' design rule applies throughout: each page keeps an OPINIONATED 5–8 headline
tiles, with depth behind the performance tab — which is already the console's shape.

**Per-module page — a "Performance" card family** (module pages already have performance tabs):

1. **Metric tile row** — expectancy, profit factor, Sortino, SQN, RoC as stat tiles, each carrying
   its coverage count and the `underpowered` chip where the sample gate says so. A `None` renders
   as an em-dash, never `$0.00` — the pmcc rule, suite-wide.
2. **Equity + underwater chart** — the session-ordered equity path (`LineChart`), with the
   underwater (drawdown) series as the fill-to-zero companion beneath it; `drawdown_span` annotated
   on the deepest excursion, an open drawdown drawn to the edge and labeled open. Lines break
   across measurement-break boundaries rather than pooling eras — the flies timeline convention.
3. **Session-net histogram with the tail marked** — per-session nets binned (`BarChart`), the CVaR
   band and worst-session called out. This is the anti-"win rate looks great" picture for premium
   sellers: the tail is drawn, not averaged away.
4. **MAE scatter** — per-position MAE (x) against final net (y), the classic stop-placement read:
   winners that survived deep excursions and losers that never recovered separate visually. Its
   twin, MFE against realized, reads exit efficiency (how much peak profit exits captured). Only on
   modules with mark paths; absence is stated ("no mark path recorded for this module"), never an
   empty chart.
5. **Rolling-window metric line** — rolling expectancy and Sharpe over the trailing N sessions on
   each performance tab. The monitoring literature's core point: a full-history number hides
   deterioration, and this suite already believes that — the flies completion-TREND chart exists
   because a blended rate drifted while looking stable. This is the same move applied to the
   money metrics. Lines break at measurement boundaries like everything else.
6. **Regime-cut small multiples** — the phase-1 metrics per regime bucket (VIX/VIX3M state, GEX
   sign, trend), joined through `cherrypick.core.regime.regime_at` against the regime recorder's
   series. Bars per bucket with sessions-per-bucket printed on each; an underpowered bucket renders
   greyed with its count, never as a finding. This is where the two plans meet: the regime series
   makes the cut possible, and this surface is what it was recorded for. Once it exists, a **regime
   ribbon** (background bands by VIX/GEX state under the equity curve) turns "when did it make
   money" into "in what market did it make money" on the same chart.

**One telling visual for each tables-only module page** (beyond the shared pair from step 0):

7. **curve — a daily regime ribbon.** The contango/backwardation series is that module's declared
   second product and currently renders as a table; a colored day-strip (with unusable days marked,
   never skipped) is the read it was recorded for.
8. **bwb — a trigger-fire timeline.** Which book fired, when, at what gamma-flip reading — the
   latches and readings are already on the rows; the flies timeline idiom applies directly.
9. **advisor — advised-vs-control cumulative lines per experiment.** Exact pairing makes the
   comparison rigorous, and it is the page's entire question rendered as one picture. The
   experiment's `underpowered` state renders on the chart, not only in the table.

**Suite and report level:**

10. **Review page (EOD tab)** — the metric tile row per module (risk-adjusted context beside net),
    an inline compact `SignedBar` in the "What each module did" table, and a `Sparkline` per module
    in the Trend section — small multiples for cross-module consistency at a glance. The trend
    already stops at measurement breaks; the sparklines break the same way.
11. **Overview page — a suite calendar strip.** A GitHub-style year strip colored by suite net per
    session — the consistency-at-a-glance view every journal tool leads with, and the one thing the
    one-session-deep EOD card cannot show.
12. **Morning page — a gamma-levels `LevelStrip`.** Spot positioned on a number line between put
    wall, zero-gamma flip, and call wall: the pack's most spatial data, currently read out of a
    table. Prior-basis readings render with the same prior labeling the cards carry.
13. **Calibrate/promotion surface** — wherever a promotion reading renders, the trials count
    ("selected from N arms") and PSR sit beside the headline metric, and the tooltip carries the
    full coverage detail. The number that inflates under selection is never shown without the
    number of selections.

Verification per house rules: every new endpoint checked against a rebuilt, restarted server
(`withReadOnlyDb` hides broken readers behind healthy-looking empties), and every new card driven
in the real browser via `ui-check` before it counts as landed. Where a page mirrors a module
query, the mirror is named and checked against the module's own CLI answer (the pmcc-mirror
pattern).

## Sequencing

1. Phase 1 (`core.metrics` + tests) — small; one sitting.
2. Phase 4 step 0 (extract `CalendarHeatmap` + `EquityUnderwater`, spread to the chartless module
   pages) — a refactor with outsized reach; no new numbers needed, so it can land before or beside
   phase 1.
3. Phase 4 items 1–3 for one module (proposed: meic, richest ledger) — proves the reader→tile→chart
   path end to end before it multiplies.
4. Report-level quick wins (items 10–12: review sparklines + SignedBars, the suite calendar strip,
   the morning LevelStrip) — display-only over numbers that already exist.
5. Phase 2 (MAE/MFE) for calendars/curve/pmcc, then their scatters (item 4); the per-module visuals
   (items 7–9) alongside.
6. Items 5–6 and 13 (rolling metrics, regime cuts + ribbon, promotion trials count) — the regime
   items gated only on the recorder's series having accumulated a few weeks of rows.
7. Phase 3's PSR alongside, DSR deferred.

## Open questions

- **CVaR quantile and minimum sessions** — proposed worst-10% over ≥20 sessions; settle at
  implementation and stamp it in the function's contract.
- **Do tail metrics enter promotion GATES or stay report-only?** — **Decided 2026-08-23:
  report-only at first.** Gating on CVaR changes what promotion means — measurement-adjacent even
  if not measurement-affecting — so the gate question is deliberately DEFERRED, not dismissed:
  **revisit once the tail metrics have accumulated enough sessions to know what a reasonable bar
  would even be** (the numbers must exist before a threshold on them means anything). When
  revisited, a gate is its own journaled decision with its own boundary, never a quiet edit to
  `calibrate`'s comparison.
- **Scatter primitive** — the one new chart component; keep it as spare as the house kit
  (categorical-free axes, shared tokens, no dependency added).
- **Full Deflated Sharpe** — deferred until the arm count or the advisor's experiment volume makes
  the trials-count label insufficient.
