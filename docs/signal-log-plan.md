# The chart setups' evidence: a historical study now, and a live signal log after it

***Phase 1 built 2026-10-04** on branch `technicals-history-study`: `history.py` (the store),
`universe.py`, `study.py` and `tuning_names.py`, with the `history land|check` and `study run`
commands. Its six guards were each shown to fail.*

***Phase 2 (the live signal log) is dropped (2026-10-04, the user's decision).*** Its main job was to
confirm live that a setup behaves as it did historically, and no setup earned that: none is
profitable after costs and better than random entry. Its secondary uses (the as-issued record, data
health) don't justify it alone. Revisit only if a setup passes the study, or a filter or exit tested
on history does. The design below stays as the record of what it would be.

***Results (2026-10-04; `packages/technicals/docs/setups.md`, "Historical evidence" and "Round 2"):***
- *Round 1 (after the merge fix): as written, no setup is worth trading. Mean reversion (long)
  beats random entry but nets only +0.015 R. The short pullback beats random shorts but loses 0.36 R.
  Trend following's entry, the long pullback and fading strength are worse than random.*
- ***Round 2 confirmed one improvement: mean reversion (long) on names trading at least $300M a
  day.*** *It netted +0.235 R (+1.09%) a trade with a +0.182 R edge (t 3.05) on the untouched half,
  matching discovery (+0.200 R, t 3.1). Every other hypothesis failed.*
- *That meets the revisit condition above. Phase 2 stays dropped until the user decides otherwise:
  a live log would confirm one filter on one setup, and that is a narrower job than the one it was
  designed for.*
- *Round 3 (two infographic setups, Supertrend + Vortex and squeeze + RSI divergence, long and
  short): nothing passed. Every side loses money after costs. The Supertrend + Vortex long is worse
  than random entry, and much worse than buying a random day the two already agree.*

*Drafted 2026-10-04; **revised the same day**, before anything was built or observed. The first
draft took its evidence only from signals recorded live from now on, which put the verdict on the
commoner setups one to four years away and on the breakouts decades away. The user's verdict:
"multiple years is not realistically useful to me". The local Dolt clone turned out to hold 15
years of daily bars, delisted names included, so the verdict now comes from history, within a week.
The live log follows, as confirmation and as a record. **Nothing is built yet.** Phase 1 is built
on its own branch once PR #31 (the setups, shorts and our own levels) has merged.*

**The short version:**

- **Phase 1** runs the eight chart setups over 2011–2026 on a liquid universe chosen as of each day,
  and scores them against a matched random baseline under a pre-declared analysis plan. That gives
  a verdict on every setup within a week.
- **Phase 2** adds an append-only log of each live signal as it is issued. Its job is to confirm
  that the setups behave live as they did historically (weeks to a few months for the frequent
  ones), to keep the record recomputation cannot, and to feed the watchlist, alerts and
  data-health checks.

## Why do any of this

### What we want to know

1. **Do the setups have any edge?** They are textbook rules, chosen with the user and never scored.
   The literature's prior is sobering:
   - moving-average and range-break rules lost their edge out of sample (Sullivan, Timmermann &
     White 1999);
   - in-sample edges vanish after modest costs (Bajgrowicz & Scaillet 2012);
   - Bollinger-band signals lost predictive power once they were popularised (Fang, Jacobsen &
     Qin 2017).

   The aim is a precise measurement that settles it either way, not a hoped-for result.
2. **Which context matters?** Whether "trend agrees", RS ≥ 7, distance to our support and
   resistance, or market breadth change the outcome.
3. **Which exit is best?** Alternative exits replayed against the same entries.
4. **What regime is the market in?** Daily counts of long versus short entries are a breadth
   reading the suite lacks.
5. **Do the options modules do better in some setup regimes?** For example, flies or bwb on days
   when SPX holds an open breakdown short.

### Why history can answer these now

The local Dolt `stocks` clone (read by the technicals landing) holds:

- **Daily bars from 2011-01-03 to the last session**: about 4,200 names in 2012, 5,300 in 2016 and
  12,500 today.
- **Delisted names.** Of the 5,349 names trading on 2016-06-01, 1,505 (28%) no longer trade,
  acquisitions among them (ABMD, AAWW). A test over this history is not limited to today's
  survivors (Brown, Goetzmann, Ibbotson & Ross 1992).
- **Its own `split` and `dividend` tables.**

At today's rates the setups make about 12 positions per name-year, across all eight. The liquid
universe grows from about 1,050 names in 2011 to about 1,800 in 2019 and about 2,900 in 2026 (a
one-day dollar-volume stand-in for the 50-session median, 2026-10-04). Over 15¾ years that is an
estimated **330,000 positions, about 8,500 of them breakouts and breakdowns**. Every setup-side
clears its sample threshold at once, even after same-day clustering.

### Why a live record still matters

Recomputation cannot reproduce what was known on the day. This is the real-time vintage problem
(Croushore & Stark 2001):

1. **Corporate actions restate history.** A dividend or split re-adjusts every earlier bar, and a
   signal sitting on a threshold can appear or vanish after the fact. CSCO's dividend on 2026-10-02
   moved its whole history by 0.39%. A capture of the vendor's data from the week before then
   disagreed with ours on all 3,012 prices until the comparison was fixed.
2. **Rule changes rewrite the past silently.** The pullback's RSI rule (2026-10-03) and the
   breakouts' Supertrend condition (2026-10-04) each changed every past signal of those setups.
3. **Data corrections.** The landing upserts Dolt wholesale, so a corrected bar replaces the signal
   it produced.

Today the nightly `technicals-report` (06:30 ET) recomputes every name's full history and
overwrites `charts/<SYMBOL>.json` and `charts/setups-index.json`. Nothing is kept.

## Principles

- **The verdict is pre-declared.** The analysis plan (v2 below) is written before any result is
  looked at. Nothing is tuned on the study's own output (Harvey 2017; Bailey et al. 2017).
- **No hindsight anywhere.**
  - The universe is chosen each day from data up to the day before.
  - Fills are at the next open.
  - A rule tuned on part of the history is judged only on the rest.
- **Record the measure, not a verdict** (as the regime recorder does,
  `docs/regime-recorder-plan.md`).
- **Append-only, enforced.** The live log's tables refuse `UPDATE` and `DELETE` through SQLite
  triggers, the first store in the suite to enforce this. Each guard is shown to fail by trying.
- **Rule versions are declared, and changing one is a boundary.** A rule change is
  measurement-affecting in the suite's sense. It lands at a declared boundary, under a new
  version, and is never pooled silently with the old one.
- **No vendor data.** The setups, their context and the study run on our own bars. The vendor only
  checks our trend scores and rank.
- **Record-only.** No loop, gate or sizing reads any of this until a later phase declares it, at a
  boundary.
- **Separate stores for separate jobs.**
  - `eod.db` is the nightly store, rebuildable from Dolt.
  - `history.db` is the study's research store, rebuildable from Dolt.
  - `signals.db` is the live log, which can't be rebuilt from anything.

## Phase 1: the historical study (the verdict, within a week)

### The data: `history.db`

A research store at `~/.cherrypick/data/technicals/history.db`, landed from the local Dolt clone
(`ohlcv`, `split`, `dividend`) for 2011-01-03 to the present.

- **How it's read:** in month-wide windows with no symbol filter, as the nightly landing already
  does. Dolt's keys lead with the date.
- **How it's adjusted:** with the package's own `adjust` code, including its two defect corrections
  (duplicate splits, and a ticker that changed hands, detected as a 3× one-day jump).
- **It's kept apart from `eod.db`** so the nightly job is not slowed, and the operational store
  does not grow by tens of millions of rows.

**Checks before anything is scored:**
- On the overlap (2023–26, our 527 names), adjusted bars from `history.db` must match `eod.db` to
  the cent wherever `eod.db` uses Dolt's own dividends.
- Before 2023:
  - overnight jumps of more than 40% with no split recorded are listed and inspected;
  - coverage gaps are counted per name.
- **Dividends** before the tastytrade set rely on Dolt alone. Dolt's dividends matched the vendor's
  adjusted prices on only 86.8% of prices when checked. That barely moves a 25-day return, but the
  study reports it.

**What the checks found (2026-10-04):**

- **The landing:** 29,229,043 bars (2011-01-03 to 2026-10-02), 4,105 splits, 499,338 dividends and
  24,278 listed names, in 15½ minutes.
- **The overlap matches exactly:** all 1,562,716 prices on the 527 names shared with `eod.db` agree
  to the cent.
- **Dolt's split table is badly incomplete before 2014.**
  - 1,426 close-to-close moves beyond 40% with no recorded split fall on days a name was in the
    universe. Most are real: earnings, biotech results, March 2020, the 2023 banks.
  - But 254 open at a clean split ratio. They are unrecorded splits (KO, NKE, GILD, TJX, ROST,
    LULU, FAST, CL 2:1; BEN, IBB, ACGL 3:1; DUK 1-for-3), and spin-offs priced like one (ABT and
    AbbVie, MDLZ and Kraft).
  - Left in, a missing 3:1 split reads as a 67% crash: a fake loss on a long held through it, a
    fake gain on a short, and weeks of false signals after it. The nightly landing's guard only
    restarts a series at a 3× jump, so a 3:1 split falls just inside it.
- **How they're handled:** `history.suspected_actions` marks an unexplained jump whose OPENING gap
  sits at a clean ratio.
  - The tolerance is 3% for 2, 3, 4, 5, 10 and 20 and their reciprocals, and 1% for 3:2, 4:3 and
    5:4.
  - At 3% for all of them it flagged 4 of 8 known genuine moves; at these tolerances it catches 11
    of 12 known unrecorded splits and flags 1 of 8 genuine moves.
  - **The study leaves out any position, real or baseline draw, held through one, and any entry in
    the 120 sessions after one.** The same rule applies to both, so the comparison stays fair.
  - It excludes rather than repairs. The cost is about 0.5% of name-days, and the counts are
    reported per setup.
  - A 4:3 or 5:4 split on an otherwise quiet day moves the price less than 40%, so it is not caught.
    Such splits are rare, but it is a known limit.

### The universe, chosen each day

A name qualifies on a session when, using bars **up to the previous session**:

- its close is at least $5, and
- its 50-session median dollar volume (close × volume) is at least $20M.

That is about 1,050 names in 2011, rising to about 2,900 today: names a trader could actually fill at
the open.

- A setup's entry counts only if the name qualified that day.
- A position, once opened, is held to its own exit, whether or not the name still qualifies.
- SPX is not in Dolt and is not in the study. The index funds are in it, if they qualify.

### The holdout for the rules that were tuned

Two families had their rules changed after looking at 2023–26 on our 527 names:

- the pullbacks (the RSI window);
- the breakouts and breakdowns (the Supertrend condition).

**These four setup-sides are judged only on data the change never saw:**
- 2011-01-03 to 2022-12-30, all names;
- 2023 onwards, names outside the 527.

Trend following and mean reversion were never tuned, and are judged on everything.

Each setup-side is also reported separately for 2011–2018 and 2019–2026. That is a stability check,
not an extra hypothesis.

Two more views, again not extra hypotheses (decided 2026-10-04). Options tradability can't be
reconstructed point-in-time (see "The options-tradable filter" in Phase 2), so the headline question
stays "does the signal predict the stock's move", on liquid stocks. The option rides on that move.
The views are:
- **names with a 50-session median dollar volume of at least $300M**, the best stock-only stand-in
  for options liquidity (76% of them have weeklies);
- **names on today's options-tradable list**, flagged as hindsight: it is today's list applied to
  past dates.

### Scoring

As analysis plan v2 below: next-open fills, a matched random baseline, costs, a family of eight
with a Holm correction, and results in calendar time. `core.metrics` already provides excursions,
expectancy and the probabilistic Sharpe. A missing mark is skipped, never read as zero.

### What it produces

- A versioned results fact set, `history-results-<run>.json`, with every setup-side and its sample.
- A dated summary appended to `packages/technicals/docs/setups.md`, including any setup-side that
  fails.

### Guards, each shown to fail

| Guard | Broken how, to check it fires |
|---|---|
| The universe uses no future data | qualify names on the same day's volume |
| Fills are at the next open | fill at the signal close |
| The tuned setup-sides cannot reach the verdict through their tuned window | let 2023–26 pullbacks into the verdict |
| The baseline replays identically | change the seed and see the result differ |
| The scorer reproduces the engine's own exits for every position | score a position off-by-one session |
| `history.db` matches `eod.db` on the overlap | apply a dividend twice |

### Storage

All of Dolt from 2011 is about 29 million bars, about 2.6 GB at the store's 90 bytes per row.
Storing only names that ever qualify is about 1 GB. Results are tens of MB: baseline draws are
regenerated from the fixed seed, not stored one by one (that would be about 1 GB more).

### Effort

About a day to land and check the history, and about a day for the scorer and baseline. Then an
hour or so of computing. **A verdict on all eight setup-sides within the week.**

## Phase 2: the live signal log (confirmation, record, day-to-day uses)

### What it is for, now that history gives the verdict

1. **Confirmation.** Does each setup-side behave live as it did historically?
   - This needs far fewer trades than proving an edge from scratch: about 150 live trades per
     setup-side, compared with the historical 95% interval.
   - The setups are executed exactly as specified, so a live drift points to the market or the data,
     not the rules.
2. **The as-issued record** that recomputation cannot reproduce: restatements, rule versions, and
   when each signal was first seen.
3. **Data health.** A logged signal that later recomputes differently is an alarm in itself.
4. **Day-to-day uses:** watchlist history, alerts, and the later phases.

**Decided 2026-10-04: the nightly setups widen to the study's liquid universe** (price ≥ $5,
50-session median dollar volume ≥ $20M, about 2,900 names today, not 527). Time to about 150 live
trades per setup-side, scaling today's 527-name rates by name count (about 5½×, a rough guide until
the wider universe's own rates are measured):

| Setup-side | Live positions a year (527 names) | Time to ~150 on 527 | On ~2,900 names |
|---|---|---|---|
| Mean reversion (short) | ~1,970 | ~4 weeks | ~1 week |
| Pullback (long) | ~1,370 | ~6 weeks | ~1 week |
| Mean reversion (long) | ~840 | ~9 weeks | ~2 weeks |
| Pullback (short) | ~830 | ~9 weeks | ~2 weeks |
| Trend following (long and short) | ~620–640 | ~12–13 weeks | ~2–3 weeks |
| Breakout / breakdown | ~78–85 | ~2 years | ~4 months |

Restricting to options-tradable names (below) slows these by however many names it removes; the
log records the label, so confirmation can be read either way. The wider pool also lets the
options-tradable filter find tradable names among all of them, not just today's curated list.

- **Storage:** `eod.db` grows from 57 MB to about 150–200 MB. The chart files grow from 27 MB to
  about 100–150 MB, rewritten nightly. The chart job takes about 3–4 minutes, up from 42 seconds.
- **Broker calls:** the IV-rank job asks tastytrade for 50 symbols per call with a 1-second pause, so
  about 2,900 names is about a minute a night. (An earlier note here said one symbol a second; that
  is the dividend script, not this one.) The dividend script does go one symbol a second, and
  refreshes each symbol weekly. Widened names use Dolt's dividends, and the tastytrade fetch stays
  on the curated names. The setups don't read IV or dividends directly, only through the adjustment.
- **The morning report is unaffected.** It keeps its own universe and its illiquid names. A name in
  the report is not a claim that it is tradable.

### The options-tradable filter (decided 2026-10-04)

***Revised the same day: tastytrade's rating is out of the label.*** *The label is now weekly
options AND a 50-session median stock dollar volume of $100M or more (`tradable.py`). The rating
marks a name down for its share price: among names with weeklies, the median close was $42 at
rating 4 and $189 at rating 2, and HD, LOW, APP, GS, CAT, LLY and COST were all rated 2. The
user's call: "I don't like that HD LOW and APP are marked not options-tradeable". The study's
hindsight view keeps the rating, as declared (`tradable.rated_today`). Measured at-the-money
bid/ask, on the standard monthly expiry, is to be added; the user's direction is that weeklies quote
wider. The universe script measures the monthly from 2026-10-05, and the bar is to be chosen from
the first three sessions of those readings. The text below is the original decision, kept as the
record.*

Many liquid stocks are still not names anyone would trade options on: wide spreads, thin books, or
monthly expiries only. The vendor's morning report includes such names too, which is fine for a
report and wrong for a trading watchlist.

**Definition.** A name is options-tradable on a session when both hold:
- **Weekly expiries:** at least 4 listed expiries in the next 35 days;
- **tastytrade's liquidity rating of 3 or 4**, on at least 7 of the last 10 sessions. The rating
  moves day to day (ABBV was 2 on 2026-10-02 and 3 on 2026-10-04), so the rule has hysteresis and
  names don't flicker in and out.

**Source.** The nightly IV-rank job already fetches both: the rating is recorded today, and the
expiry list (`option_expiration_implied_volatilities`) arrives in the same response but is
discarded. Keeping it costs no extra calls. The label is recorded every night, so it builds up as
point-in-time history from now on.

**Size today.** Of the 526 charted names, all have listed options, 242 (46%) have no weeklies, and
147 have weeklies with a rating of 3 or 4. Some examples:
- CSCO, NKE, HPQ and CHWY qualify.
- ZTS is rated 4 but monthly only.
- ANF has weeklies but is rated 2.
- NHC and MTD are rated 1, monthly only.

**Where it applies:**
- the setups watchlist, as its default filter, with "show all" one click away;
- the signal log, as context (`options_tradable`, `liquidity_rating`, `weekly_expiries`), so
  outcomes can be split by it;
- alerts.

**Where it doesn't:** the charts and the morning report keep every name.

**Why the history can't use it.** tastytrade gives only today. Dolt's option chains (from 2019)
carry no weeklies, only the standard monthlies, so SPY shows two expiries in 35 days. Their quotes
look like after-hours ones: ABBV's ~30-day at-the-money spread reads 17.9% of mid. Stock dollar
volume predicts the label poorly among names this liquid: at a median of $500M a day or more, only
53% are tradable, and 37% of tradable names fall below that line.

### Where it lives

`~/.cherrypick/data/technicals/signals.db`, via a new `paths.signals_db()`. **Decided:** a separate
file, because the log can't be rebuilt from anything and must not share a file whose rows are
replaced as a matter of course. This widens the package's write contract, and its `CLAUDE.md` will
say so.

### Tables

| Table | One row per | Key columns |
|---|---|---|
| `runs` | logging run | `run_id`, `started_at` (UTC), `as_of_session`, `code_sha` (git), `rule_version`, `context_version`, `symbols`, `events_new`, `restatements_new`, `outcome` |
| `rule_versions` | rule version ever used | `rule_version`, `fingerprint`, `parameters_json` (every constant in `setups.py`), `rules_json` (each setup's rule text), `first_run_id` |
| `signals` | **entry or exit, as first seen** | `signal_id`, `symbol`, `setup_id`, `family`, `side`, `event` (`entry`/`exit`), `event_session`, `position_key` (symbol, setup, entry session), `price_adj`, `close_raw`, `reason` (exits), `target` (pullbacks), `rule_version`, `first_run_id`, `first_seen_at`, `lag_sessions`, `seen_before_open`, `source` (`observed`/`reconstructed`), `tuned_on_data` |
| `signal_context` | signal | `context_version`; as of `event_session`: `trend_1m`, `trend_6m`, `rs`, `vs_spy_1m`, `atr14`, `volume_ratio`, our nearest `support`/`resistance` and their distances, `sentiment`, `spy_trend_1m`/`_6m`, `universe_member`, `options_tradable`, `liquidity_rating`, `weekly_expiries` |
| `restatements` | later disagreement | `signal_id` (or the new event), `run_id`, `kind` (`vanished`, `late`, `price_moved`, `exit_changed`), `old_json`, `new_json`, `probable_cause` (`corporate_action`, `rule_version`, `data_correction`, `unknown`) |

A few of these columns need explaining:

- **`close_raw`** never changes under later corporate actions, so it anchors any re-pricing.
- **`lag_sessions`** is 0 in the normal case. A missed night logs its events the next day with
  lag 1, and the log says so.
- **`seen_before_open`** is whether the signal was logged before the next 09:30 ET. The report runs
  at 06:30, so normally it was.
- **`tuned_on_data`** marks reconstructed rows of both pullbacks and both breakouts. Their rules
  were changed after looking at that history.
- **`context_version`** versions the context definitions (the swing-level width, "trend agrees",
  the volume ratio) separately from the rules. A context change is recorded, but does not restart a
  setup's evidence the way a rule change does.
- **`universe_member`** starts the point-in-time membership record for the live universe.

### How a run works

1. `report` writes the chart files first, then `signal_log.record(...)` takes the trades
   `setups.RUN` just computed. There is no second computation, so the log and the charts cannot
   disagree.
2. Each entry and exit is keyed by (`position_key`, `event`, `rule_version`):
   - **a new key** is inserted with its context (and flagged `late` if its session is before the
     run's);
   - **a known key, unchanged,** is ignored, so re-runs are idempotent;
   - **a known key, changed,** keeps the original row and appends the disagreement to
     `restatements`.
3. A cause is attributed:
   - unchanged raw closes with a corporate action in between is `corporate_action`;
   - a version change is `rule_version`;
   - changed raw bars are `data_correction`;
   - anything else is `unknown`, and is surfaced.
4. A re-run of a past session compares events only up to that session, so it can't "un-see" later
   ones.
5. **Failure handling.** The log is written in one transaction after the charts. A failure exits
   non-zero for the watchdog, and never costs the charts.

### Rule versions

- `setups.RULE_VERSION = "2026-10-04"` and `setups.fingerprint()`, a hash of every constant and rule
  text.
- A test pins the fingerprint, so changing a threshold without bumping the version fails the build.
  It is shown to fail by changing `ADX_MIN`.
- `CONTEXT_VERSION` works the same way for the context definitions.

### The backfill (decided)

- `signals reconstruct` is dry-run by default; `--write` inserts today's computation of the stored
  history.
- Its rows are flagged `source = 'reconstructed'`, and the tuned families are flagged
  `tuned_on_data`.
- Its use is continuity and the console's history view. The evidence comes from Phase 1, not from
  these rows.

### Commands, guards and size

- **Commands:** `signals status` (last run, counts, restatements, lags) and `signals export` (CSV
  or JSON).
- **Guards, each shown to fail:**
  - the triggers refuse an update;
  - a re-run inserts nothing;
  - the CSCO dividend case yields one `price_moved` with cause `corporate_action` and an unchanged
    `close_raw`;
  - a changed constant fails the fingerprint;
  - a vanished event is recorded;
  - `reconstruct` writes nothing without `--write`;
  - the log and the chart files agree on every event.
- **Size:** on about 2,900 names, about 275 events a night and 70,000 rows a year, about 20–40 MB.
  That is small.

## Phase 3: a read surface

- **On the console's Setups page:**
  - a log tab: first-seen time, whether a signal was known before the open, and late and restated
    marks;
  - the restatements, with their causes;
  - the Phase 1 results: each setup-side's verdict, sample and confidence interval, and, as live
    trades accrue, the live figures against the historical interval.
- **On the System page:** a chip for the last log run.
- Read-only, through `readOnlyDb`.

## Analysis plan v2 (2026-10-04)

**Supersedes v1, which was frozen earlier the same day and under which nothing was ever observed.**
What changed, and why: v1 took its evidence from observed live rows only, with a 12-month minimum.
That put verdicts years away, and 15 years of usable history turned out to exist. So v2:

- takes the verdict from the historical study;
- adds the holdout for the tuned rules;
- demotes live rows to confirmation.

A change to anything here is v3, dated, with its reason, and is never applied to results already
looked at under v2.

**What counts, and how it is filled**
- **Unit:** each historical entry made while its name was in the point-in-time universe, scored to
  its own rule exit. The four tuned setup-sides count only from their holdout.
- **Fills:** the next session's open after the signal close, for entries and exits alike. Prices
  are raw bars with corporate actions applied explicitly. A short pays the dividends it is held
  through. Close-to-close measurement is not used: it flatters short-term reversal through bid-ask
  bounce (Conrad, Gultekin & Kaul 1997), and overnight and intraday returns differ by strategy
  (Lou, Polk & Skouras 2019).

**What is measured**
- **Measures, per setup-side, never pooled across sides** (Brock, Lakonishok & LeBaron 1992):
  - expectancy in R (ATR(14) at entry) and in percent;
  - hit rate, payoff ratio, median hold;
  - MAE and MFE (Sweeney 1997).
- **Intrabar ties** take the stop.

**What it is compared against**
- **Baseline:** for each entry,
  - 20 same-date entries in random names from that day's universe, and
  - 20 same-name entries on random dates when the name was in the universe,

  each with the same side, exit rule and fills. The seed is fixed and the draws are stored (Brock,
  Lakonishok & LeBaron 1992; Lyon, Barber & Tsai 1999).

**The test**
- **The test:** the setup-side's mean R minus its baseline's, in calendar time (one return per
  session), with errors clustered by date (Petersen 2009). One-sided: only an edge matters.
- **Family and correction:** the eight setup-sides, with a Holm correction at a family-wise α of
  0.05. A later filter or exit joins the family (White 2000; Hansen 2005; Harvey, Liu & Zhu 2016).
- **Minimum sample:** 780 × the measured design effect in counted entries per setup-side. The
  historical sample is expected to clear it. If a tuned setup-side's holdout does not, it reads
  "not yet judged".

**Costs**
- **Spread:** the Corwin & Schultz (2012) estimator on our own bars.
- **Shorts:** a borrow rate declared before the first scoring run (general collateral is cheap,
  specials are not; D'Avolio 2002), and a Rule 201 flag after a 10% drop.
- **Declared 2026-10-04, before the first scoring run** (`study.py`):
  - the spread is the mean daily Corwin–Schultz estimate over the 21 sessions up to the day before
    each fill, and half of it is paid on every fill;
  - borrow is 1.0% a year on shorts, conservative for names this liquid (D'Avolio's general
    collateral was about 0.17%);
  - a Rule 201 flag marks a short whose signal day's low was 10% or more under the prior close, and
    each short setup-side is also reported without them.
- Costs appear as separate columns under the suite's money layout. High-turnover signals rarely
  survive costs (Novy-Marx & Velikov 2016).

**Live confirmation**
- After about 150 live trades per setup-side, live mean R is compared with the historical 95%
  interval.
- Outside it, the live figure is a finding to investigate, never a silent re-estimate.

**Reporting**
- Every setup-side is reported, passing or failing, by sub-period (2011–18 and 2019–26), with its
  sample.

## Round 2: improvements, declared before they were run (2026-10-04)

The first results found no setup worth trading. Round 2 tests whether a filter or a different exit
makes one worth trading. **These ideas were suggested by looking at round 1**, so testing them on
the same data would partly re-find what was seen. The design is built around that.

**Two stages, on disjoint names.**
- Every name is assigned for good to half A or half B by the CRC-32 of its ticker (even is A).
- **Stage A (discovery)** tests all nine hypotheses on half A's names only. Baseline draws also come
  only from half A.
- **Stage B (confirmation)** tests only the stage-A survivors on half B, which nothing in round 2
  looks at before then.

**The family: nine hypotheses**, with textbook settings fixed here and not tuned. A filter is
applied at entry, and the setup's positions are re-walked with it, so a filtered-out signal never
blocks a later one. An exit replaces the setup's own exit, and its positions are re-walked too.

| Id | Starts from | Change |
|---|---|---|
| `mr-trend-agrees` | mean reversion (long) | enter only when both trend scores (1M and 6M) are above zero |
| `mr-above-200` | mean reversion (long) | enter only when the close is above the name's 200-session SMA |
| `mr-300m` | mean reversion (long) | enter only when the 50-session median dollar volume (the session before) is ≥ $300M |
| `trend-market-up` | trend following (long) | enter only when SPY closed above its 200-session SMA |
| `breakout-market-up` | breakout (long) | enter only when SPY closed above its 200-session SMA |
| `trend-short-market-down` | trend following (short) | enter only when SPY closed below its 200-session SMA |
| `pullback-short-market-down` | pullback (short) | enter only when SPY closed below its 200-session SMA |
| `pullback-hold-10` | pullback (long) | exit after 10 sessions: no target, no stop |
| `pullback-no-target` | pullback (long) | no target: exit only on the Chandelier stop |

**Everything else is as in analysis plan v2:**
- the universe chosen as of each day;
- the holdout for the tuned families (pullbacks, breakouts);
- the corporate-action exclusions;
- next-open fills;
- R net of the same declared costs;
- calendar-time, one-sided tests.

**The baseline matches the conditions.** For each counted entry, 40 same-date candidates (other
half-A names in that day's universe) and 40 same-name candidates (other days the name qualified) are
drawn with a fixed seed. Only candidates that pass the hypothesis's own filter that day are kept,
and they are held to the hypothesis's exit. A filter is credited only for what the setup adds under
it, not for the filter itself (buying random stocks in an uptrend also does well).

**The decision rule.** A hypothesis advances from stage A only if both hold:
- its edge over the matched baseline is significant under Holm across all nine at a family-wise
  α of 0.05; and
- its net R is above zero, meaning it makes money after costs on its own.

In stage B, survivors are re-tested on half B with Holm across the survivors only. **Confirmed
means both conditions hold again on half B.** Anything else is reported as found: a stage-A failure,
or a stage-B failure.

**Two corrections found in round 2's first run (2026-10-04):**

- **A merge bug, affecting round 1 too.** Each entry's baseline draws land in many names, so in
  many workers' results. They were merged with `dict.update`, which kept one worker's share and
  dropped the rest: a median of one draw per entry instead of about forty.
  - The kept draws were still random, so the baselines were unbiased, but far noisier, and the tests
    weaker than declared.
  - Fixed in `study.merge_sums`. The end-to-end tests now require nearly every draw and fail with the
    old merge.
  - **Round 1 and stage A were re-run after the fix.** That fixes a bug, not the hypotheses or the
    rule.
  - The first stage-A result was read before the bug was noticed: with its noisy baselines, only
    `mr-300m` passed. It is superseded, and nothing in the family, the parameters or the rule changed
    after it was seen.
- **Outcome:** stage A passed `mr-300m` and `mr-above-200`. Stage B confirmed `mr-300m` (+0.235 R
  net, edge +0.182 R, t 3.05) and not `mr-above-200` (t 1.3).
- **`mr-trend-agrees` is empty by construction.** A lower-band touch with RSI(14) under 30 never has
  a 1-month trend score above zero: 0 entries in half A. It stays in the family as declared, and is
  reported as not judged. The same fact means the watchlist's "Trend agrees" filter hides every long
  mean-reversion signal.

## Round 3: two infographic setups, declared before they were run (2026-10-04)

The user brought two setups from Fingrad's indicator-pair infographics (a trading-education
publisher; no backtest stands behind either graphic). Neither was suggested by this history, so
round 3 tests them as round 1 tested the chart's eight: on every name, in one stage. The rules are
study-only (`setups.STUDIED`). The chart does not draw them, and round 1's eight are unchanged.

**The setups, as declared** (`setups.py`; `round3.py`):

| Id | Entry | Exit |
|---|---|---|
| `st-vortex` | the first close on which Supertrend(10, 3) is up AND VI+(14) is above VI-, after a close on which they were not both so | the first close with Supertrend down OR VI+ under VI- |
| `st-vortex-short` | the mirror | the mirror |
| `squeeze-div-short` (as drawn) | a close under the lower band (20, 2) within 5 sessions of a squeeze, while the latest two RSI(14) pivot highs confirmed by then are a bearish divergence | the first close over the middle band |
| `squeeze-div` (the mirror) | a close over the upper band, squeeze as above, and a bullish divergence of the latest two RSI(14) pivot lows | the first close under the middle band |

**The choices the graphics leave open, settled before running:**

- **Supertrend + Vortex enters on the first bar the two agree**, whichever turned second. Both
  turning on one bar would almost never happen. This is the only written-down version of the
  combination found (a "both states" ruleset). The graphic lists two exit conditions, and **either
  one exits**.
- **The Vortex** is Botes & Siepman's (TASC, January 2010): VM+ = |high - prior low|,
  VM- = |low - prior high|, each summed over 14 bars and divided by the summed true range (plain
  sums, not Wilder's averages). 14 is the period their examples use. **Supertrend** is the chart's
  own (10, 3 on Wilder's ATR), the TradingView default.
- **The divergence** is TradingView's Divergence Indicator at its defaults:
  - RSI(14) pivots with 5 bars on each side;
  - the two pivots 5 to 60 bars apart;
  - price read at the RSI pivot bars (the bar's high for a bearish divergence, its low for a
    bullish one);
  - known only 5 bars after the later pivot.

  The pivot is `swings.py`'s (above the bars before, not passed by the bars after). TradingView
  breaks a tie the other way and counts the span one bar differently. On a continuous RSI neither
  matters.
- **The divergence is current** while it is the latest confirmed pair. A newer RSI pivot replaces it.
  No further age limit is added.
- **The squeeze and the band break** are the chart's breakout definitions (band width at its
  narrowest of 120 sessions, within 5 sessions; judged on the close). Unlike the chart's breakout,
  there is no volume or Supertrend condition.
- **The long mirror of the squeeze setup** is tested because the suite tests every setup both ways.
  The graphic shows only the short.

**What was looked at before declaring:**

- **Frequencies only**, on the nightly store's 525 names over three years:
  - `st-vortex` about 34 entries per 1,000 bars (13,328), its short 32;
  - `squeeze-div` 0.30 (116), its short 0.46 (179).
- **Round 1's breakdown result** (−0.867 R, no edge). Its entry is Bollinger's own squeeze
  breakdown, so it is the nearest relative of `squeeze-div-short`. It was published before this
  round and is disclosed here.

**Two baselines, from the same draws.** For each counted entry, 20 same-date and 20 same-name
draws (seeded from the entry's identity), held to the setup's own exit and scored like the entry.
- **`random`** keeps every draw. This is plan v2's baseline, so the result sits beside round 1's
  table.
- **`matched`** keeps only the draws made on a day the setup's state already held:
  - for `st-vortex`, Supertrend and the Vortex already agreeing on its side;
  - for `squeeze-div`, a close already outside the band.

  It asks whether the trigger adds anything over simply being in that state. Without it, a draw
  outside the state leaves at the next close, a round trip that measures the spread, not an
  entry. A test pins that every real entry fires inside its matched state.

**The decision rule.**
- **Eight tests:** four setup-sides, each against two baselines. Holm across all eight at a
  family-wise α of 0.05.
- **A test passes** with a Holm-significant positive edge AND a positive net R.
- **A setup-side is confirmed** only when both of its tests pass.
- **Too small a sample:** a setup-side short of 780 effective entries reads "not yet judged".

**Views, never tests:**
- 2011–18 and 2019–26;
- the $300M names;
- halves A and B;
- the index funds (SPY, QQQ, IWM, DIA);
- each setup with one of its two conditions removed, described without a baseline: Supertrend
  alone, the Vortex alone, the squeeze without the divergence, the divergence without the squeeze.

**Everything else is plan v2's:**
- the universe as of each day;
- the corporate-action exclusions;
- next-open fills;
- the declared costs;
- the calendar-time one-sided test.

**Known departures from the sources:**
- **The Vortex's authors entered on a stop at the cross bar's high** (low for a short), not at
  the next open. This tests the infographic's version.
- **Bollinger's own squeeze method** (Method I) exits a breakdown short at a tag of the opposite
  band or a parabolic stop, and picks direction with a volume indicator, not RSI. The middle-band
  exit and the divergence are the graphic's.

**Outcome (run once, after this declaration; `round3-20261004-173057.json`):**
- **Nothing passed.** Every setup-side loses money after costs:
  - Supertrend + Vortex −0.027 R long and −0.437 R short;
  - the squeeze −0.073 R long and −0.613 R short.
- Only the two short "matched" edges were Holm-significant (+0.045 R, t 3.9; +0.203 R, t 2.5).
  Both sides lose money, so neither passes.
- The results and what they say are in `packages/technicals/docs/setups.md`, "Round 3".

## Round 4: the vendor's relative-strength breakout, declared before it was run (2026-10-09)

The vendor's webinars define relative strength as the stock's price divided by the S&P 500. Their
signal is that ratio at a new high together with a price breakout. Fitting the lookback to 25 dated
calls from their 2026 sessions gave about one month: 21 sessions reproduce 24 of the 25. The
calls only tell us whether our list matches theirs. Round 4 asks whether the signal makes money.

**What was looked at before declaring (2026-10-09, an exploratory script outside the repo).** It
is disclosed because it shaped the declaration:
- **Scope:** every name on days its 50-session median dollar volume was at least $300M, 2011 to
  2026-10, with SPY as the benchmark.
- **Signals:** price and ratio breakouts at lookbacks of 10 to 126 sessions, together and apart.
- **Measure:** forward excess return over the same day's liquid names, at 5, 21 and 63 sessions.
- **Result:** no lookback had an edge. The one lead was the vendor's own tie-break: breakouts on
  volume at least 1.5x the 30-session average led the rest in both 2011–2022 and 2023–2026 at 21
  sessions, at t near 1.
- **So the 21-session hold below was chosen after that look.** The lookback and the volume rule
  are the vendor's words, settled before any of this history was read.

**The test uses only what that look never saw:** entries on days the name's median dollar volume
was under $300M. The study universe still applies ($5, $20M), so this is the $20M–$300M slice.
Baseline draws come from the same slice on the same day.

**The setups, as declared** (`round4.py`, long only):

| Id | Entry | Exit |
|---|---|---|
| `rs-break` | a close above the highest close of the prior 21 sessions, on which close ÷ SPY's close is also above its highest of the prior 21, with no such close in the prior 10 sessions | the close 21 sessions after entry; filled at the next open |
| `rs-break-vol` | `rs-break`, and that session's volume at least 1.5x the mean of the prior 30 sessions' volume | the same |

**The choices the vendor leaves open, settled before running:**
- **The benchmark is SPY's adjusted close** (a total return), as their slide names SPY. The study
  holds no S&P 500 index history from 2011.
- **A session SPY did not trade** cannot fire.
- **"Fresh" is 10 sessions,** the one value the exploratory look used. It was not tuned.
- **A missing volume never confirms** (as `setups.volume_confirms`).
- **Ties do not count:** "above the highest" is strictly greater, as the exploratory look used.

**Three tests, Holm across the three at a family-wise α of 0.05.**
- `rs-break:random`: the vendor's signal against random entry.
- `rs-break-vol:random`: with the volume rule, against random entry.
- `rs-break-vol:matched`: with the volume rule, against draws made on days the name's close and
  its ratio were both at a 21-session high, at any volume. It asks whether the volume rule adds
  anything over the breakout itself.

Draws are 40 same-date and 40 same-name for each entry, seeded from the entry's identity, held 21
sessions and scored like the entry. A test passes with a Holm-significant positive edge AND a
positive net R. A setup short of 780 effective entries reads "not yet judged".

**Views, never tests:**
- the $300M slice (already seen);
- 2011–18 and 2019–26;
- halves A and B.

**Everything else is plan v2's:**
- the universe as of each day;
- the corporate-action exclusions;
- next-open fills;
- R net of the declared costs;
- the calendar-time one-sided test.

**Outcome (run once, after this declaration; `round4-20261009-152840.json`):**
- **Nothing passed, and the breakout is worse than random entry.**
  - `rs-break`: +0.090 R net against a +0.138 R baseline, edge −0.085 R (t −3.4).
  - `rs-break-vol`: +0.033 R, edge −0.127 R (t −3.8).
  - Against the breakout at any volume: −0.039 R (t −0.9).
- **The exploratory volume lead did not survive** on the slice it never saw.
- The results and what they say are in `packages/technicals/docs/setups.md`, "Round 4".

## Round 5: the 1-10 score inside the vendor's fundamentals, declared before it was run (2026-10-09)

Round 4 tested the relative-strength breakout as an entry on its own. The user's correction is that
the vendor never uses it that way. The 1-10 relative-strength score is read only where the
fundamentals are very good (bullish) or very bad (bearish), together with the directional
indicators. A 10 means "entering too late to capture significant value", and a 1 means "the price
action is ignoring the fundamentals". The vendor's 2026 sessions say the same: "a relative strength
of two while having compelling fundamentals is what we are looking for," and a 10 is "late to the
game". Round 5 asks whether a low score adds anything once the fundamentals and the direction
already agree.

**What was looked at before declaring (2026-10-09):**
- **The vendor's own words**: 192 transcripts and the 2026 slides, read for how they define
  fundamentals and use the score. The findings and the year-by-year changes are recorded outside
  the repo.
- **Frequencies only**, with no returns computed (the build ran with scoring off, and asserted it):
  - entries per test in the sector version: 826 (bullish, stage), 1,194 (bullish, trend), 1,327
    (bearish, stage) and 1,426 (bearish, trend);
  - the median comparison pool per entry: 10 to 49;
  - about 122 sector-map names labelled each week.
- **Round 4** (the breakout without fundamentals, worse than random) and its exploratory look at
  breakouts. No return by score decile or by fundamentals label has been seen.

**The fundamentals label** (`fundamentals.py`) uses Dolt's weekly consensus snapshots, as each stood
that week, from 2017-10:
- **Valuation:** forward P/E, the raw close over the next fiscal year's consensus EPS. A name with
  that EPS at zero or below ranks behind every profitable name, ordered among themselves by
  price/sales.
- **Estimated EPS growth and estimated revenue growth:** next fiscal year's consensus against the
  current year's, measured as (next − prior) / |prior|.
- **Net margin:** the last four reported quarters. A quarter counts only 45 days after it ends,
  because Dolt keeps period ends, not filing dates.
- **The score:** each measure's percentile within the name's sector that week (cheaper is better
  for valuation), with net margin counted twice. The vendor weights margin most and never states
  the weights. A name missing a measure, or in a sector of fewer than 5 scored names, has no score.
- **The label:** the top 15% of the week's scores are compelling and the bottom 15% are weak. The
  vendor labels only the clear tails; 15% is estimated from their list sizes.
- **The label in force on a session** is the latest snapshot strictly before it. A Sunday's snapshot
  first applies on Monday.

**The 1-10 score** is our reproduction (`levels.rank_score`, 52 of 62 exact against the vendor's).
It is computed on history exactly as `land.land_rank_cutoffs` builds it live: the whole listed
market, raw closes, at least $100k a day, and names with a split inside the window left out.

**Triggers, each both ways:**
- **Stage:** the name becomes an early leader under the live stage rule, with no leader reading of
  any stage in the 10 sessions before. The bearish trigger is an early laggard, likewise. The
  vendor's 2026 trigger is this early breakout.
- **Trend:** the short-term trend score reaches Bullish (3 or more) from below. The bearish trigger
  reaches Bearish (−3 or less) from above.

**Four tests, Holm across the four at a family-wise α of 0.05:**

| Id | Entry | Against |
|---|---|---|
| `bull:stage` | compelling + early-leader trigger + score 1-3, long | the same label and trigger at scores 4-10 |
| `bull:trend` | compelling + Bullish-trend trigger + score 1-3, long | the same at scores 4-10 |
| `bear:stage` | weak + early-laggard trigger + score 8-10, short | the same at scores 1-7 |
| `bear:trend` | weak + Bearish-trend trigger + score 8-10, short | the same at scores 1-7 |

- **The comparison pool** is the mean net R of the comparison entries made within 10 sessions
  either side. It is the other scores, not every score, so the test asks what the low (or high)
  score adds over the rest.
- **Every trigger is an entry.** Positions may overlap, and the calendar-time test weighs a day once.
- **A test passes** with a Holm-significant positive edge AND a positive net R.
- **Too small a sample:** fewer than 780 effective entries reads "not yet judged". The bullish
  stage test may land there.

**Views, never tests:**
- the whole-universe version (percentiles across every name with estimates, not within sectors);
- the "late" side: compelling + bullish trigger at scores 8-10, and weak + bearish trigger at 1-3.

**Known biases, stated now:**
- **The sector map is today's 489 names.** A name that scored low and then collapsed and delisted
  is missing, which flatters the bullish tests. The whole-universe view does not have this bias.
- **The data starts in October 2017.** That is about nine years, against fifteen for rounds 1-4.
- **The fundamentals are ours, not the vendor's.** Their scale, weights, threshold, estimate horizon
  and per-industry multiple are unstated. The sectors are broader than their industries.

**Everything else is plan v2's:**
- the universe as of each day;
- the corporate-action exclusions;
- next-open fills and the 21-session hold;
- R net of the declared costs (borrow on the shorts);
- the calendar-time one-sided test.

## Later phases: uses the study and log are shaped for, none committed

History makes most of these **runnable now, not someday**.

- **Alternative exits.** Replay a declared grid against the historical entries: a trailing ATR
  stop, a fixed R target, a time stop. Follow calendars' `exit_policies.py`:
  - the grid is declared;
  - the replay runs over recorded prices;
  - it refuses where data is missing;
  - the control policy must reproduce every rule exit exactly before any alternative is trusted.

  Each exit joins the family.
- **Filters and context.** "Trend agrees", RS ≥ 7, distance to our levels, volume at entry. Each is
  a declared hypothesis, testable on history at once.
- **A breadth and regime reading.** Daily long and short entry counts over the point-in-time
  universe, with 15 years of history to test it on, first as a way to stratify the results (breadth
  has cross-market evidence as a predictor; Zaremba et al. 2021). It goes into the morning overview
  as a recorded measurement that feeds no gate: the `overview/score.py` deployment precedent, a key
  in `facts.build()`.
- **Context for the options modules.**
  - An "SPX setup state" regime-cut dimension for flies and MEIC (`core.regimecuts`). It needs a
    `REGIME_DIMENSIONS` entry and a recorded measurement break in each module. Room must be made
    first, since the advisor's regime-cuts section sits at 71.9 KB of its 72 KB ceiling.
  - Setup state recorded at entry by bwb, pmcc and earnings, record-only.
  - Any action on it would be an advisor A/B experiment, never a rule change.
- **Alerts.** A morning card, and a post to Discord when a starred name fires. It would use the
  existing `notify/secrets.get_webhook` path as its own supervisor job, off by default, posting once
  a session.
- **An advisor fact source.** A setup-state summary in the deep-slot fact pack, after a size cut.
- **Checking our setups against the vendor's.** Compare with the vendor's saved daily lists.
  Validation only, never an input.
- **A paper module, gated on evidence.** Only for a setup-side that beats its baseline after costs
  under v2, in its holdout and in both sub-periods. It would be a new paper-only, credential-free
  package with real next-open fills, fees and the money layout. That gate could now be met, or
  plainly failed, within weeks.

## What could go wrong

| Risk | Handling |
|---|---|
| Hindsight in the universe | membership from data up to the day before only; a guard shown to fail |
| Survivorship | delisted names are in Dolt (28% of 2016's names); delisting returns are missing, so bankruptcies lose their final drop (Shumway 1997), and the report says so |
| Older data not checked to the cent | overlap check against `eod.db`; jump and gap inspection before 2023; Dolt-only dividends reported |
| Renamed tickers | two separate series; harmless to a per-position study |
| Tuned rules judged on their tuning data | the holdout; a guard |
| One regime dominating | results by sub-period |
| Many tests, one lucky result | a declared family, the Holm correction, every variant counted |
| Same-day clustering | calendar time, date-clustered errors |
| Costs ignored | spread estimated, borrow and Rule 201 for shorts, dividends owed on shorts |
| Live results drifting from history | Phase 2's confirmation, and a finding to investigate |
| History restated after the fact | the live log's raw closes and restatements |
| Rules changed unnoticed | the version and fingerprint guard |

## Decisions (2026-10-04)

1. **The live log lives in a separate `signals.db`.**
2. **The reconstructed backfill is written**, flagged, with `tuned_on_data` on both pullbacks and
   both breakouts.
3. **The rules are frozen as `RULE_VERSION = "2026-10-04"`** with the fingerprint guard. That
   includes the shorts, the amended pullback and the breakouts' Supertrend condition. A breakout or
   breakdown entered against Supertrend had exited the next close 73–82% of the time
   (`packages/technicals/docs/setups.md`). The context is versioned separately.
4. **The analysis plan is v2 above.** Costs are declared before the first scoring run.
   Exploratory looks are labelled, and a rule change they prompt is a new version.
5. **The vendor's scan-rule matches stay out of the log.** Ours are kept daily in the dated
   `report-<session>.json` files, and the vendor's lists are saved daily by the collector.
6. **The verdict comes from a historical study first** (revised 2026-10-04), over a universe
   chosen as of each day: price ≥ $5 and 50-session median dollar volume ≥ $20M. The live log
   follows as confirmation.
7. **The nightly setups widen to the liquid universe** (about 2,900 names), for roughly 5½ times
   faster confirmation and a wider pool for the tradable filter. Widened names use Dolt's dividends.
8. **An options-tradable filter:** weekly expiries, and a tastytrade liquidity rating of 3 or 4 on at
   least 7 of the last 10 sessions. It is the watchlist's default, and it is context in the log and
   alerts. The charts and the morning report keep every name. It is recorded nightly from now on.
   The historical study adds a $300M-a-day view and a flagged today's-list view.
9. **Phase 2, the live signal log, is dropped** (2026-10-04, after the first results). Decisions 1,
   2, 3 and 7 described it and are not acted on. The options-tradable label (8) stands on its own as
   a watchlist filter: the IV-rank script records its inputs nightly from 2026-10-04.

## References

Citations were checked against publisher, RePEc or SSRN pages except where marked.

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
