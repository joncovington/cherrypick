# The signal log: recording the chart setups as issued, and what it is for

*Drafted 2026-10-04. **Nothing is built yet.** Phase 1 (the log itself) is the proposal; its five
open decisions were all made on 2026-10-04 (see "Decisions"). Phase 3's analysis plan is frozen
below as v1. The later phases are ideas, recorded here so the log is shaped to serve them, and none
is committed to. Phase 1 is built on its own branch once PR #31 (the setups, shorts and our own
levels) has merged.*

**The short version:** the technicals chart setups fire about 50 entries and exits every session
across 527 names, and today none of it is kept. Every run recomputes the whole history and
overwrites the last answer. This plan adds an append-only record of each signal exactly as it was
issued: when it was first seen, at what price, under which version of the rules, and against what
context. Without that record, no later question about these setups (do they work, which filters
matter, what regime they fire in) can be answered honestly.

## Why record the signals at all

### What happens today

- `technicals-report` runs at 06:30 ET (`orchestrator/jobspec.py`). It calls `chart.write_all`,
  which walks every name's full history through the eight setups (`setups.py`) and **overwrites**:
  - `charts/<SYMBOL>.json`, holding the last 250 sessions;
  - `charts/setups-index.json`, holding open positions and the last 20 sessions' events.
- Nothing outside the console reads them. No Python package reads `data/technicals/`.
- The rules are deterministic, so past signals can *mostly* be regenerated. "Mostly" is the
  problem:
  1. **Corporate actions restate history.** A dividend or split re-adjusts every earlier bar. The
     recomputed entry price moves, and a signal sitting on a threshold (a crossover, a band touch)
     can appear or vanish after the fact. CSCO's dividend on 2026-10-02 moved its whole history by
     0.39%. A capture of the vendor's data from the week before then disagreed with ours on 3,012
     of 3,012 prices until the comparison was fixed.
  2. **Rule changes rewrite the past silently.** On 2026-10-03 the pullback's RSI rule was amended
     (it could not fire as first written). Every past pullback signal changed with it, and nothing
     records what the old rule said.
  3. **Data corrections.** The landing upserts Dolt wholesale, so an upstream correction replaces a
     bar, and with it any signal that bar produced.
- In forecasting research this is the *real-time vintage* problem: a model is judged on the data it
  had at the time, not on today's revised history (Croushore & Stark 2001). An as-issued record is
  the only defence, because no amount of recomputation reproduces what was known on the day.

### What we hope to gain

The record is the evidence base for questions the suite cannot answer today:

1. **Do the setups have any edge?** They are textbook rules, chosen with the user and never scored.
   The literature's prior is sobering:
   - Moving-average and range-break rules lost their edge out of sample (Sullivan, Timmermann &
     White 1999).
   - In-sample edges vanish after modest costs (Bajgrowicz & Scaillet 2012).
   - Bollinger-band signals lost predictive power after they were popularised (Fang, Jacobsen & Qin
     2017).

   The aim is not to prove the setups work. It is to measure them precisely enough that the suite
   neither trusts nor discards them on anecdote.
2. **Which context matters?** Whether "trend agrees", RS ≥ 7, distance to our support and
   resistance, or the market's breadth changes outcomes. Each is a question only a record of
   context-at-issue can answer.
3. **Which exit is best?** Alternative exits replayed against the same entries, the way calendars'
   `exit_policies.py` replays its policy grid.
4. **What regime is the market in?** Daily counts of long versus short entries are a breadth
   reading the suite does not have yet.
5. **Do the options modules do better in some setup regimes?** For example, flies or bwb on days
   when SPX holds an open breakdown short.
6. **Data health.** A logged signal that later recomputes differently is itself an alarm: a
   corporate action, a correction or a code change moved history. It would have caught the CSCO
   problem on its own.
7. **A track record.** If any setup ever earns a paper module, its evidence is a record that
   predates the decision, not a backtest written after it (Harvey 2017's case for pre-registration).

## Principles

These come from the suite's own rules (root `CLAUDE.md`) and from the methodology literature
(references at the end).

- **Record the measure, not a verdict.** As the regime recorder does
  (`docs/regime-recorder-plan.md`): raw values in, buckets and judgements derived on read.
- **Append-only, enforced, not just a convention.** No store in the suite enforces this today; the
  log would be the first, with SQLite triggers that refuse `UPDATE` and `DELETE` on its record
  tables. Like every guard in the suite, these are shown to fail by trying.
- **As issued.** Each row carries the run that first saw it, the wall-clock time, the rule version,
  and the price two ways: as adjusted on that day and **raw** (unadjusted). The raw close never
  changes under later corporate actions, so it anchors any later re-pricing.
- **Rule versions are declared, and changing one is a boundary.** A rule change is
  measurement-affecting in the suite's sense. It lands at a declared boundary, under a new version,
  and the log never pools two versions without saying so.
- **Known before the next open.** The report runs at 06:30 ET, so a session's signals are normally
  logged before the next 09:30 open. The log records whether each one was. That is what makes a
  next-open fill a fair assumption in the later scoring.
- **No vendor data.** The setups and their context are computed from our own bars (since
  2026-10-04). The vendor is used only to check our trend scores and rank, never as a log input.
- **Record-only.** No loop, gate, or sizing rule reads the log until a later phase declares it, at a
  boundary, the way the overview's deployment score "feeds no gate, no phase, no sizing".
- **A logging failure must be loud, never silent, and must not cost the charts.** The chart files
  are written first. The log is written in one transaction after them, and a failure exits non-zero
  so the watchdog sees it.

## Phase 1: the log

### Where it lives

A new SQLite file, `~/.cherrypick/data/technicals/signals.db`, via a new `paths.signals_db()`.

It is deliberately **not** a table in `eod.db`. That store holds raw data upserted wholesale from
Dolt, and could be rebuilt from Dolt at any time. The log can't be rebuilt from anything, so it must
not share a file whose rows are replaced as a matter of course, or be deleted along with it.
Separating the two also makes backup and the append-only rule simple to state. This widens the
technicals package's stated write contract ("writes only `eod.db` and its report/chart files"), and
its `CLAUDE.md` will say so.

### Tables

| Table | One row per | Key columns |
|---|---|---|
| `runs` | logging run | `run_id`, `started_at` (UTC), `as_of_session`, `code_sha` (git), `rule_version`, `context_version`, `symbols`, `events_new`, `restatements_new`, `outcome` |
| `rule_versions` | rule version ever used | `rule_version`, `fingerprint`, `parameters_json` (every constant in `setups.py`), `rules_json` (each setup's rule text), `first_run_id` |
| `signals` | **entry or exit, as first seen** | `signal_id`, `symbol`, `setup_id`, `family`, `side`, `event` (`entry`/`exit`), `event_session`, `position_key` (symbol, setup, entry session), `price_adj`, `close_raw`, `reason` (exits), `target` (pullbacks), `rule_version`, `first_run_id`, `first_seen_at`, `lag_sessions`, `seen_before_open`, `source` (`observed`/`reconstructed`), `tuned_on_data` |
| `signal_context` | signal | `context_version`; as of `event_session`: `trend_1m`, `trend_6m`, `rs`, `vs_spy_1m`, `atr14`, `volume_ratio` (today against the 50-session mean), our nearest `support`/`resistance` and their distances, `sentiment`, `spy_trend_1m`/`_6m`, `universe_member` |
| `restatements` | later disagreement | `signal_id` (or the new event), `run_id`, `kind` (`vanished`, `late`, `price_moved`, `exit_changed`), `old_json`, `new_json`, `probable_cause` (`corporate_action`, `rule_version`, `data_correction`, `unknown`) |

A few of these columns need explaining:

- **`lag_sessions`** is how many sessions after the event the log first saw it. It is 0 in the
  normal case. A missed night's run logs its events the next day with lag 1, and the log says so
  rather than pretending otherwise.
- **`seen_before_open`** is whether `first_seen_at` came before 09:30 ET on the session after the
  event.
- **`source='reconstructed'`** marks rows from the backfill (below). Analysis keeps them apart from
  observed rows unless it is told otherwise.
- **`tuned_on_data`** is set on reconstructed rows of any setup whose rule was changed after
  looking at that same history: both pullbacks (the RSI amendment) and both breakouts (the
  Supertrend condition, 2026-10-04). Those rows can never count as evidence, and the flag keeps
  that from being forgotten. Observed rows never carry it.
- **`context_version`** versions the context definitions (`CONTEXT_VERSION`: the swing-level
  width, "trend agrees", the volume ratio) separately from the rules. Changing context is recorded,
  but it does not restart a setup's evidence clock the way a rule change does.
- **`universe_member`** records whether the name was a universe member that day. It starts the
  point-in-time membership record that survivorship-free scoring needs (Brown, Goetzmann, Ibbotson
  & Ross 1992; Shumway 1997).

### How a run works

1. `report` writes the chart files as it does today.
2. `signal_log.record(...)` takes the trades `setups.RUN` just computed for every name. No second
   computation, so the log and the charts cannot disagree.
3. Each entry and exit is keyed by (`position_key`, `event`, `rule_version`).
   - **A key not yet in the log** is inserted with its context, as a new signal. If its session is
     before the run's, it is also flagged `late`.
   - **A key already logged, recomputed the same,** is ignored. Re-runs are idempotent.
   - **A key already logged, recomputed differently** keeps the original row. The disagreement is
     appended to `restatements`:
     - `price_moved`: the price is different.
     - `exit_changed`: the exit date or reason is different.
     - `vanished`: a logged event is no longer produced.
4. **Attributing a cause.** A restatement whose raw closes are unchanged, with a dividend or split
   gone ex in between, is `corporate_action`. A change of rule version is `rule_version`. Changed
   raw bars are `data_correction`. Anything else is `unknown`, and is surfaced.
5. **Re-running a past session** (`report --session D`) runs as of D. It only compares events up to
   D, so it cannot "un-see" anything logged later.

### Rule versions, and the guard on them

- `setups.RULE_VERSION` is a declared string, for example `"2026-10-04"`.
- `setups.fingerprint()` hashes every constant and every rule's text.
- A test pins the fingerprint for the declared version. **Changing a threshold without bumping the
  version fails the build.** The guard is shown to fail by doing exactly that.
- A bump is the suite's measurement-affecting change: batched to a declared boundary, recorded once
  in `rule_versions`, and noted in `packages/technicals/docs/setups.md`.

### The backfill

- `python -m cherrypick.technicals signals reconstruct` is **dry-run by default**, like every
  backfill in the suite. It reports what it would insert.
- `--write` inserts today's computation of the full history as `source='reconstructed'`, under the
  current rule version.
- These rows give an early first look, and they are not as-issued evidence. **The pullback and
  breakout rows are not even a clean test:** both rules were amended after looking at three years
  of these same names (the pullback's RSI on 2026-10-03, the breakouts' Supertrend condition on
  2026-10-04), so their reconstructed rows carry `tuned_on_data`. Observed rows start with the first
  logged run.

### Commands and surfaces

- `python -m cherrypick.technicals signals status` prints the last run, counts by setup and side,
  observed versus reconstructed, restatements by kind and cause, and any `lag_sessions > 0`.
- `signals export [--from --to --setup --side --source]` writes CSV or JSON for analysis.
- Console (a small Phase 2): see below.

### Guards, each shown to fail

| Guard | Broken how, to check it fires |
|---|---|
| Triggers refuse `UPDATE`/`DELETE` on `signals`, `signal_context`, `restatements` | issue an update in a test |
| A re-run of the same session inserts nothing | drop the key check |
| A dividend after a logged entry yields one `price_moved` restatement with cause `corporate_action`, and `close_raw` unchanged | the CSCO case, rebuilt as a fixture |
| A changed constant without a version bump fails | change `ADX_MIN` |
| A logged event that stops being produced is recorded `vanished` | remove a bar from the fixture |
| `reconstruct` writes nothing without `--write` | call it without the flag |
| The log and the chart files agree on every event a run produced | log from a second computation |

### Size

The three-year history holds about 19,300 positions across the eight setups, about 25 a session. That is
about 50 entry and exit events a night, or about 13,000 rows a year plus context. That is small for
SQLite, and needs no retention policy.

## Phase 2: a read surface

- A **"log" tab** on the console's Setups page:
  - each signal with its first-seen time;
  - whether it was known before the next open;
  - `late` and `restated` marks.
- A **restatements list** with probable causes.
- A **System page chip** for the last log run.
- Read-only, through the console's `readOnlyDb`, so "no log here" and "the read threw" are told
  apart.

## Phase 3: scoring the outcomes (the reason for the log)

A deterministic scorer over the log and our bars, written to a versioned fact set like the review's.
Its rules are **declared here, before the data accrues**, so the analysis cannot be fitted to the
result (Harvey 2017; Bailey et al. 2017 on overfitting).

### How outcomes are measured

- **Fills at the next open, never at the signal close.**
  - A signal known before the open fills at the next session's open; an exit, at the open after its
    exit close.
  - Close-to-close measurement flatters short-term reversal through bid-ask bounce (Conrad,
    Gultekin & Kaul 1997), and overnight and intraday returns differ systematically by strategy
    (Lou, Polk & Skouras 2019).
  - The gap between the signal close and the fill is recorded as its own number: Perold's (1988)
    "paper versus reality".
- **Prices** are raw bars, with corporate actions applied explicitly over the holding period. A
  short owes a payment in lieu of any dividend it is held through, and on raw bars that is a debit
  (adjusted bars net it silently).
- **Per signal:**
  - holding-period return in the trade's direction;
  - holding time;
  - MAE and MFE from the daily path (Sweeney 1997), normalised by ATR(14) at entry, and as
    R-multiples where the rule has a stop.
  - `core.metrics.excursions` already provides the excursion maths. It skips a missing mark rather
    than reading it as zero, which is the right behaviour here.
- **Per setup and side, never pooled across sides** (Brock, Lakonishok & LeBaron 1992 found buys
  and sells behave differently):
  - hit rate, payoff ratio and **expectancy** (`core.metrics.expectancy`);
  - per-trade Sharpe, and the probabilistic Sharpe (`core.metrics.probabilistic_sharpe`);
  - `sample_progress` against `MIN_EFFECTIVE_N`.
- **Intrabar order is unknown** on daily bars. When a stop and a target fall inside one bar, the
  stop is taken, as the setups already do.

### What it is compared against

**A matched random baseline, the core of the test** (Brock, Lakonishok & LeBaron 1992; Lyon, Barber &
Tsai 1999). For every real signal:

- (a) entries on the **same date** in randomly drawn universe names, which removes that day's market
  move;
- (b) entries in the **same name** on random dates, which removes the stock's own drift.

Each draw uses the same side, the same exit rule and the same fill rules. The seed is fixed and the
draws are stored, so the baseline replays exactly. A setup that does no better than its own
baseline has no edge, whatever its raw hit rate.

**Same-day signals are not independent.** Many signals land on the same market-wide move. Results
are reported in calendar time (one portfolio return per session), or with standard errors
clustered by date (Petersen 2009). A naive per-trade t-statistic overstates confidence.

### Costs

- **Spread** is estimated per name per day from our own high/low/close (Corwin & Schultz 2012; Abdi
  & Ranaldo 2017), so it stays deterministic.
- **Shorts** also carry:
  - a **borrow** assumption, a declared parameter: general collateral is cheap, specials are not
    (D'Avolio 2002), and loans can be recalled (Engelberg, Reed & Ringgenberg 2018);
  - a **Rule 201** flag: after a drop of 10% or more, a short entry at the next open cannot be
    assumed filled at the bid.
- **The money layout.** The suite's root rule applies: P&L means net, gross is labelled gross, and
  an unrecorded cost reads `n/r`, never zero. Swing setups turn over fast, and high-turnover
  signals rarely survive costs (Novy-Marx & Velikov 2016).

### The test, and when to run it

- **The family is declared: 4 setups × 2 sides = 8 hypotheses.** Corrected with Holm or
  Benjamini–Hochberg–Yekutieli: small, deterministic, and enough for a fixed family. A Reality
  Check or SPA bootstrap (White 2000; Hansen 2005) is only needed if parameter variants are ever
  searched. In that case every variant tried is logged (`rule_versions` does this), and the
  Deflated Sharpe applies (Bailey & López de Prado 2014). A filter or exit added in Phases 4 and 5
  is a new hypothesis and is added to the family, not tested on its own.
- **No verdict before the sample supports one.** The required sample size was worked with the
  normal approximation, at a two-sided α of 0.05 and power of 0.80. **A hit rate of 55% against 50%
  needs about 780 independent trades**, and 53% against 50% about 2,180. The same 5-point difference
  measured against a baseline needs about 1,565 trades in each group.
- **Clustering inflates those counts** by 1 + (m−1)ρ. Twenty same-day signals at ρ = 0.1 gives
  about 2.9×. At today's rates, without clustering:

| Setup and side | Positions a year (from three years) | Years to ~780 trades | With 2.9× clustering |
|---|---|---|---|
| Mean reversion (short) | ~1,970 | 0.4 | 1.2 |
| Pullback (long) | ~1,370 | 0.6 | 1.6 |
| Mean reversion (long) | ~840 | 0.9 | 2.7 |
| Pullback (short) | ~830 | 0.9 | 2.7 |
| Trend following (long) | ~640 | 1.2 | 3.5 |
| Trend following (short) | ~620 | 1.3 | 3.7 |
| Breakout (long) | ~85 | 9.1 | 27 |
| Breakdown (short) | ~78 | 10.0 | 29 |

- **The breakouts can't be judged from the log in any useful time** (the rows above are after
  their Supertrend condition, which removed about 12% and 18% of them). They need a different answer:
  pooling with a declared, longer reconstructed history, or accepting that they stay unjudged. That
  is a decision to make at the time, written down, not drifted into.
- **The live record is the out-of-sample test.** The scorer reports observed and reconstructed rows
  separately. A conclusion rests on observed rows only.

## Later phases: uses to shape the log for, none committed

### Phase 4: alternative exits

Replay a declared grid of exits against the same logged entries: a trailing ATR stop instead of the
21 EMA, a fixed R-multiple target, a time stop. This follows the calendars pattern in
`exit_policies.py`:

- the grid is declared up front;
- the replay runs over recorded prices;
- it refuses rather than guesses where data is missing;
- it checks itself: the control policy must reproduce every logged rule exit exactly before any
  alternative is trusted.

### Phase 5: filters and context

Whether "trend agrees", RS ≥ 7, distance to our nearest level, or volume at entry changes outcomes,
read from `signal_context`. Each filter is one more declared hypothesis in Phase 3's family.

### Phase 6: a breadth and regime reading

- **The reading:** daily counts of new long and short entries by setup, divided by the point-in-time
  universe.
- **Where it goes:** into the morning overview's fact pack as a **recorded measurement that feeds no
  gate**. The precedent is `overview/score.py`'s deployment block; it is a new key in `facts.build()`
  and a line in `render.py`.
- **How it is used:** first, to stratify Phase 3 (outcomes by breadth regime, with the baseline drawn
  within the same regime). Breadth has some cross-market evidence as a predictor (Zaremba et al.
  2021) and weaker evidence near term. Using it as a predictor here would be one more declared
  hypothesis, so it is not one by default.

### Phase 7: context for the options modules

- **Regime cuts.** An "SPX setup state" dimension for flies' and MEIC's regime cuts
  (`core.regimecuts`). Either a column stamped at entry (through each module's added-columns
  migration, which `stale_writer_columns` would then watch), or an at-or-before join from the
  module into `signals.db`, read-only, on the model of `core.regime`.
  - Either way it means a `REGIME_DIMENSIONS` entry and a recorded measurement break in each module.
  - The advisor's thinned regime-cuts section sits at 71.9 KB against a 72 KB ceiling, so something
    must be cut first.
- **bwb, pmcc and earnings** could record their underlying's setup state at entry, record-only.
  Recording a context column changes what a row says, not what the module did, so it isn't
  measurement-affecting.
- **Promotion is gated.** If a cut ever showed a difference, acting on it would be an advisor A/B
  experiment under the existing mechanism, never a rule change.

### Phase 8: alerts

- A "fired since the last session" card on Reports → Morning.
- A starred-names list (the console's prefs store), with a post to Discord through the existing
  `notify/secrets.get_webhook` path when a starred name fires. It would be its own supervisor job,
  off by default, never called from the watchdog tick, posting once per session with a state file
  (the `flies_payoff_post.py` model).

### Phase 9: an advisor fact source

A setup-state summary for the names each module trades, in the advisor's deep-slot fact pack. One
reader in `factpack.build()`, a `PACK_VERSION` bump, and room made under the pack's size ceiling.

### Phase 10: checking our setups against the vendor's

The vendor's daily trade-ideas lists are already saved with each chart capture. Comparing our logged
signals with them is a check on our work only, never an input, in line with "the vendor validates,
never feeds".

### Phase 11: a paper module, gated on evidence

- **The gate:** consider it only if a setup-side beats its matched baseline after costs, under the
  pre-declared Phase 3 test, at the declared sample size.
- **What it would be:**
  - a new paper-only, credential-free package, like calendars or pmcc;
  - one arm per setup-side;
  - real next-open fills, fees, and the suite's money layout.
- **It isn't promised.** The literature says most such tests fail, and a well-measured "no" is an
  outcome this plan is built to deliver.

## What could go wrong, and what the log does about it

| Risk | Handling |
|---|---|
| History restated after the fact | raw closes stored; restatements recorded with a cause, never silently replaced |
| Rules changed without anyone noticing | version plus fingerprint guard; versions never pooled silently |
| Survivorship (today's universe, delisted names gone) | `universe_member` recorded from day one; reconstructed rows flagged; delisting returns noted as a gap until the store keeps delisted names |
| Look-ahead (signal-close fills) | `seen_before_open`; next-open fills in Phase 3 |
| Many tests, one lucky result | a declared family, corrections, every variant logged |
| Same-day clustering | calendar-time or date-clustered errors |
| Too few trades | the sample-size table; no verdict before it |
| Costs ignored | spread estimated, borrow and Rule 201 for shorts, dividends owed on shorts |
| A missed nightly run | logged next run with `lag_sessions > 0`, visible in `status` |
| Logging breaks the report | charts written first; the log in its own transaction; non-zero exit for the watchdog |

## Decisions (all made 2026-10-04)

1. **Where the log lives: a separate `signals.db`**, for the reasons above.
2. **The reconstructed backfill: written**, flagged `reconstructed`, over the full stored history,
   with `tuned_on_data` on both pullbacks and both breakouts.
3. **The rules are frozen as `RULE_VERSION = "2026-10-04"`** with the fingerprint guard, including
   the shorts, the amended pullback and one last fix made first: **a breakout or breakdown requires
   Supertrend already on the trade's side at entry**. Without it, 73–82% of the entries made against
   Supertrend exited the next close, a one-day round trip (`packages/technicals/docs/setups.md`).
   The context definitions are versioned separately (`CONTEXT_VERSION`). The first observed record
   is the first nightly run after Phase 1 ships, which follows the merge of PR #31.
4. **Phase 3's analysis plan is frozen in its core now, as v1 below.** The cost parameters are
   declared before the first scoring run and dated before any observed outcome is looked at.
   Exploratory scoring of reconstructed rows is allowed, labelled exploratory, under one rule: any
   rule change it prompts is a new rule version and restarts that setup's observed clock.
5. **The vendor's scan-rule matches stay out of the log.** Our reproduction of them is already
   kept daily in the dated `report-<session>.json` files, and the vendor's own lists are saved
   daily by the collector (`trade-ideas.json`), so Phase 10's check has both sides without the log
   holding vendor-shaped data.

## Analysis plan v1 (frozen 2026-10-04)

Declared before any observed signal exists. A change to anything here is v2, dated, with its reason,
and is never applied to results already looked at under v1.

- **Unit:** each logged entry with `source = 'observed'`, scored against its own logged exit.
  Reconstructed rows are exploratory only, and rows with `tuned_on_data` are never evidence.
- **Fills:** the next session's open after the signal close, for entries and exits alike. A signal
  not seen before that open (`seen_before_open = 0`) fills at the open after it was seen. Prices are
  raw bars with corporate actions applied explicitly; a short pays the dividends it is held through.
- **Measures, per setup-side, never pooled across sides:** expectancy in R (ATR(14) at entry) and
  in percent, hit rate, payoff ratio, median hold, MAE and MFE. Intrabar ties take the stop.
- **Baseline:** for each signal, 20 same-date entries in random universe names and 20 same-name
  entries on random dates, same side, same exit rule, same fills. The seed is fixed and the draws
  are stored.
- **Test:** the setup-side's mean R minus its matched baseline's, in calendar time (one return per
  session) with errors clustered by date. One-sided, because only an edge matters.
- **Family and correction:** the 8 setup-sides. Holm correction at a family-wise α of 0.05. Every
  later filter or exit tested joins the family.
- **Minimum sample:** no verdict on a setup-side before 780 × its measured design effect in
  observed entries (the table above), and never before 12 months of observed record.
- **Costs:** spread from the Corwin–Schultz estimator on our own bars. Shorts also pay a borrow
  rate declared before the first scoring run, and carry a Rule 201 flag after a 10% drop. All costs
  appear as separate columns under the suite's money layout.
- **Reporting:** every setup-side is reported, passing or not, with its sample progress. A
  setup-side short of its minimum sample reads "not yet judged", never a number dressed as a
  verdict.

## References

Citations were checked against publisher, RePEc or SSRN pages where marked; the rest are standard
texts.

- **Baselines and evidence on technical rules**
  - Brock, Lakonishok & LeBaron (1992), "Simple Technical Trading Rules and the Stochastic Properties
    of Stock Returns", *Journal of Finance* 47(5).
  - Lo, Mamaysky & Wang (2000), "Foundations of Technical Analysis", *Journal of Finance* 55(4).
  - Sullivan, Timmermann & White (1999), "Data-Snooping, Technical Trading Rule Performance, and the
    Bootstrap", *Journal of Finance* 54(5).
  - Park & Irwin (2007), "What Do We Know About the Profitability of Technical Analysis?",
    *Journal of Economic Surveys* 21(4).
  - Bajgrowicz & Scaillet (2012), "Technical Trading Revisited: False Discoveries, Persistence Tests,
    and Transaction Costs", *Journal of Financial Economics* 106(3).
  - Fang, Jacobsen & Qin (2017), "Popularity versus Profitability: Evidence from Bollinger Bands",
    *Journal of Portfolio Management* 43(4). The variant tested was not confirmed.
  - McLean & Pontiff (2016), "Does Academic Research Destroy Stock Return Predictability?",
    *Journal of Finance* 71(1).
- **Multiple testing and overfitting**
  - White (2000), "A Reality Check for Data Snooping", *Econometrica* 68(5).
  - Hansen (2005), "A Test for Superior Predictive Ability", *Journal of Business & Economic
    Statistics* 23.
  - Harvey, Liu & Zhu (2016), "…and the Cross-Section of Expected Returns", *Review of Financial
    Studies* 29(1).
  - Harvey (2017), "Presidential Address: The Scientific Outlook in Financial Economics",
    *Journal of Finance* 72(4).
  - Bailey & López de Prado (2014), "The Deflated Sharpe Ratio", *Journal of Portfolio Management*
    40(5).
  - Bailey, Borwein, López de Prado & Zhu (2017), "The Probability of Backtest Overfitting",
    *Journal of Computational Finance* 20(4).
- **Long-horizon tests and standard errors**
  - Lyon, Barber & Tsai (1999), "Improved Methods for Tests of Long-Run Abnormal Stock Returns",
    *Journal of Finance* 54(1).
  - Petersen (2009), "Estimating Standard Errors in Finance Panel Data Sets", *Review of Financial
    Studies* 22(1).
- **Point-in-time data and survivorship**
  - Croushore & Stark (2001), "A Real-Time Data Set for Macroeconomists", *Journal of Econometrics*
    105.
  - Brown, Goetzmann, Ibbotson & Ross (1992), "Survivorship Bias in Performance Studies", *Review of
    Financial Studies* 5.
  - Shumway (1997), "The Delisting Bias in CRSP Data", *Journal of Finance* 52(1).
- **Fills and the cost of trading**
  - Conrad, Gultekin & Kaul (1997), "Profitability of Short-Term Contrarian Strategies:
    Implications for Market Efficiency", *Journal of Business & Economic Statistics* 15. Details
    from secondary summaries.
  - Lou, Polk & Skouras (2019), "A Tug of War: Overnight versus Intraday Expected Returns",
    *Journal of Financial Economics* 134(1).
  - Perold (1988), "The Implementation Shortfall: Paper versus Reality", *Journal of Portfolio
    Management* 14(3).
  - Corwin & Schultz (2012), "A Simple Way to Estimate Bid-Ask Spreads from Daily High and Low
    Prices", *Journal of Finance* 67(2).
  - Abdi & Ranaldo (2017), "A Simple Estimation of Bid-Ask Spreads from Daily Close, High, and Low
    Prices", *Review of Financial Studies* 30(12).
  - Novy-Marx & Velikov (2016), "A Taxonomy of Anomalies and Their Trading Costs", *Review of
    Financial Studies* 29(1).
  - D'Avolio (2002), "The Market for Borrowing Stock", *Journal of Financial Economics* 66. Its
    borrow-fee levels date from 2000–01.
  - Engelberg, Reed & Ringgenberg (2018), "Short-Selling Risk", *Journal of Finance* 73(2).
  - SEC Rule 201 (2010), the alternative uptick rule.
- **Breadth**
  - Zaremba, Szyszka, Karathanasopoulos & Mikutowski (2021), "Herding for Profits: Market Breadth
    and the Cross-Section of Global Equity Returns", *Economic Modelling* 97.
- **Practitioner texts**
  - Sweeney (1997), *Maximum Adverse Excursion: Analyzing Price Fluctuations for Trading
    Management* (Wiley).
  - Aronson (2006), *Evidence-Based Technical Analysis* (Wiley).
