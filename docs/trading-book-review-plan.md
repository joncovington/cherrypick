# A trading book, reviewed: what it asks of the suite

*Started 2026-10-24, all fifteen chapters and the conclusion reviewed the same day. A chapter-by-chapter review of a book on scientific method in trading, read against how this suite already tests and runs strategies. No module change is
built from this file directly: each action item below becomes its own PR, and any that changes what
a module measures lands at a declared boundary. The closing review at the end gives a suggested build order.*

## How a chapter is recorded

Each chapter gets the same four parts, in our own words. No passages are copied from the book.

1. **What it argues**: the claims and procedures, briefly.
2. **Already in the suite**: where our process meets or exceeds it, with the place it lives.
3. **Where it is weaker than our process**: errors and gaps, so a later item is not built on them.
4. **Action items**: numbered across the whole book (`B1`, `B2`, …), each with a status. A later
   chapter may add to, merge with or supersede an earlier item, and says so.

Statuses: `proposed` (named, not yet agreed), `agreed` (the user has said build it), `built` (with
its PR), `dropped` (with why).

## Running summary

| Item | From | What | Status | Measurement-affecting? |
|---|---|---|---|---|
| B1 | Ch. 1, 3, 7 | Written kill criteria for every live arm, enforced as entries-blocked; thresholds from a bootstrap of the arm's evidence, frozen when armed; a graduated response (normal, reduced size, blocked) from 50-trade windows measured in standard errors of the window mean, against a live-cost baseline | built for meic + bwb (window trigger reported); earnings open | No: blocks or reduces live entries only, paper unchanged |
| B2 | Ch. 1, 6, 14 | Confidence intervals and t on the console's expectancy and win-rate figures, with the median, spread and worst trade beside the mean and a skew flag in both directions; a win rate never shown without expectancy beside it | built (summary cards, overlap flag) | No: a read surface |
| B3 | Ch. 1 | Results in R per trade (1R = max loss for a defined-risk structure) in every module's history | built: `core.ledgers` `r_multiple`, console mean R | No: a presentation and recording change |
| B4 | Ch. 1, 2 | Entry context recorded on every trade row (SPX against its 50-day **and 200-day**, ATR %, relative volume, sector, entry time; holding period at exit) | built: `technicals.entrycontext`, derived on read | No: recording only, new columns |
| B5 | Ch. 2 | A slice report per arm (by setup, market condition, entry time, holding period) with n and an interval on every cell; a cell that looks good becomes a declared hypothesis, never a filter | built: `core.slices`, `technicals slices`, Backtest → slices | No: a read surface |
| B6 | Ch. 2, 7 | Facts and interpretations kept apart wherever a person decides a trade (desk tickets, manual notes): free text in its own field, never read by a calculation; plus an invalidation condition and a short pre-mortem written at entry | built: `cherrypick-desk propose --thesis` | No: recording only |
| B7 | Ch. 3, 7, 8 | A hypothesis register: one row per hypothesis with its four parts, mechanism, **rival explanations and the check that rules each out**, rejection rule, status and result pointer, checked for near-duplicates of rejected ideas before a new one is declared; every rule change logged with the evidence behind it and re-read after its first 100 trades | built: `docs/hypotheses.json`, `cherrypick hypotheses` | No: research tooling |
| B8 | Ch. 4 | One declared market-environment classifier (uptrend, sideways, downtrend) in `cherrypick.core`, used by every study report and by B4/B5, so "which environment" means the same thing everywhere | built: `core.environment`, on B4's rows | No: a shared definition; studies report by it, nothing trades on it |
| B9 | Ch. 4 | Replication across names as a standing second check in declared tests: the universe split by a fixed hash of the symbol, and a pass required in both name-halves as well as both time-halves | built: declaration template + `core.robustness` | No: tightens future declarations only |
| B10 | Ch. 5, 13 | An execution record per live arm: each live fill against the price its own model expected, and every human override (manual close, re-arm, sizing change, desk order beside an arm) flagged, read weekly beside P&L; plus a gap breakdown of paper against live (missed entries, fill slippage, costs, overrides) naming the biggest part | built: `cherrypick execution`, Live → execution; desk orders unrecorded | No: recording and a read surface |
| B11 | Ch. 6 | A survivorship audit of the research data's residual paths: how a trade across a delisting or acquisition settles, how often a delisted name's option chain comes back empty, and which inputs still hold today's names only | audited: declared limits written; counts fixed | No: an audit; findings become fixes or declared limits |
| B12 | Ch. 6 | A minimum detectable effect in every declaration: the smallest edge the planned sample could detect, computed from the spread of R, so "no verdict" says what it rules out; rule changes compared paired on the same trades | built: declaration template + `core.robustness` | No: tightens future declarations only |
| B13 | Ch. 9, 11 | A robustness report beside every passing result: the parameter neighbourhood (a plateau, not a peak), costs at 1.5× and 2×, and a bootstrap of the drawdown, with a declared cost stress the result must survive; plus the largest size at which the bootstrapped 99th-percentile drawdown stays inside a declared share of the account | built: declaration template + `core.robustness` | No: tightens future declarations only |
| B14 | Ch. 10 | A yearly re-test: every declared result, passed or failed, re-run unchanged on the newest year of data and recorded beside the original, so decay (or a late pass) is measured rather than assumed | built: `cherrypick retests --year`; recipes for items 1 and 5 | No: research tooling; results stay local |

---

## Chapter 1: The scientific method for trading

### What it argues

- A trading idea is only worth testing if it can be proven wrong. "Moving averages make money" is an
  opinion; "this crossover, these exits, this window, this win rate and payoff" is a hypothesis.
- **A seven-step method:**
  1. observe something specific;
  2. propose an explanation that could be wrong;
  3. build a test able to disprove it;
  4. gather enough data that luck is not the answer;
  5. compare against what would have happened otherwise;
  6. re-run the idea on unseen data;
  7. never rely on predicting.
- **A structured journal** with identical fields on every row: setup, entry, stop, exit, size, the
  result in R, and the market context at entry.
- **R as the unit:** the distance from entry to the initial stop, so results compare across prices
  and position sizes.
- **Expectancy over win rate:** (win rate × average win) − (loss rate × average loss). A 70% winner
  can lose money if its losses are large.
- **Sample size:** under about 30 trades says little, about 100 gives a first estimate, 200 more
  confidence, 300 or more a reliable picture.
- **Position size:** a fixed 1–2% of equity, with the share count set by the stop distance.
- **Change one rule per test** against an unchanged trade list.
- **Write the failure condition before going live**, and treat it as automatic. The examples are
  expectancy turning negative over a rolling 100 trades, or drawdown passing 1.5 times the worst
  seen in testing.
- **Intuition proposes, data decides:** keep a dated file of hunches with numeric conditions, and
  promote a few a month to formal testing.
- **The closing checklist adds statistics:** state the win rate with its standard error,
  √(p(1−p)/n), compute t = mean R ÷ standard deviation of R × √n, and hold risk at or below 1% until
  t passes 2.

### Already in the suite

| The book | Here |
|---|---|
| A hypothesis that can fail | Declared tests in [edge-hypotheses-plan.md](edge-hypotheses-plan.md), written before any data is read |
| A baseline to beat | Matched random entries per trade (items 14-17) |
| Enough data; t > 2 | t > 2 in **both** halves, at least 100 trades a half, Holm across the family |
| Unseen data | The design window and holdout (`cherrypick.backtest.settings`); item 17 picks in one half and scores in the other |
| One change at a time | Control and treatment arms; measurement-affecting changes batched to declared boundaries (root CLAUDE.md) |
| Costs | Working-price fills, fees and settlement in every study; decisions made net |
| A file of untested ideas | The hypothesis list and the research queue (`run.py queue-add`) |

### Where it is weaker than our process

- **Its own example breaks its rules.** The opening crossover (74 trades, 60% at 2:1) is under its
  own 100-trade threshold, implies +0.8R a trade (far above what tested setups produce), and carries
  no costs.
- **Costs are an afterthought.** For options, where costs often decide the answer, that is not
  enough.
- **R assumes a perfect stop.** Gaps and slippage make losses larger than −1R. Short premium has no
  natural 1R, unless R is defined as a spread's maximum loss.
- **Its statistics stop short:**
  - A win rate's interval matters less than expectancy's.
  - t assumes independent trades with thin tails. Overlapping trades and fat-tailed short-premium
    returns overstate it.
  - It runs many tests with no correction for testing many ideas.
  - "0.20R → 0.30R, so the new exit is better" needs a paired test, not a comparison of two means.
  - Holdouts, look-ahead and survivorship bias get one line.
- **Interactions:** one change at a time can miss a rule that helps one entry and hurts another.
- **The rolling-100 kill trigger is noisy.** A sound strategy dips below zero over some windows by
  chance. The 1.5× tested-drawdown trigger is the sturdier of the two.

### Action items

**B1. Written kill criteria for every live arm.** *(built 2026-10-24 for meic and bwb: `core.killcriteria`; the 50-trade-window trigger is reported in `cherrypick execution` and Live → execution, never enforced: z of the last 50 live trades' mean against the paper mirror's, `watch` at -2, `alarm` at -3. Earnings is still open)*
- **As built:** a stationary bootstrap (a week of sessions per block) of the arm's tested trades over a 100-trade horizon; the 95th percentile of drawdown or losing streak halves the entry, the 99th refuses and latches until a person clears it; under 100 trades a hand-written dollar limit at size one; missing criteria refuse. `python -m cherrypick.core.killcriteria derive` prints the block to write by hand, and the watchdog reports a latch as CRITICAL.
- **What:** each live loop (meic, flies, bwb, earnings) states in its config, dated and before it
  trades, the condition that declares its arm invalid. The default is drawdown beyond 1.5 times the
  worst in the arm's evidence; a rolling-expectancy trigger is reported, not enforced, given its
  noise.
- **When it fires:** the arm stops opening positions, as bwb's mark-drawdown breaker blocks entries
  today. It never closes anything and never flips a gate. One notification is sent, and the arm
  stays blocked until a person re-arms it.
- **Open questions:** what "the worst in the evidence" is for an arm with no replay (meic live has
  paper history; earnings has little); and whether the threshold is written by hand or derived and
  then frozen.

**B2. Intervals on the console.** *(built on the summary cards, 2026-10-24, with the overlap flag: trades closed the same session, and, for bwb and curve, trades held alongside another in their arm)*
- Every per-arm expectancy and win rate on the console shows its 95% interval and n. Expectancy also
  shows its t, so a 20-trade arm cannot read as proof.
- Overlapping trades are flagged where an arm holds several positions at once, so t is not taken at
  face value.
- A read-surface change only; the figures come from the existing ledgers.

**B3. Results in R.** *(built 2026-10-24: derived on read in `core.ledgers` as `r_multiple`, so every row already written has it with no backfill; the console's summary states mean R. bwb's capital now comes from its widths, correcting the overstated pre-2026-09-18 `entry_max_loss`.)*
- Every module's trade history gains `r_multiple`: net P&L divided by the trade's maximum loss at
  entry (the defined-risk structures' natural 1R). An undefined-risk structure says so and has no R.
- Recorded where the max loss is known at entry, derived for older rows where it can be recovered
  (a dry-run-by-default backfill, per the root CLAUDE.md's rule for recording missing components).
- It makes modules comparable on one scale, and gives B2 its unit.

**B4. Entry context on every trade row.** *(built 2026-10-24 as `cherrypick.technicals.entrycontext`: derived on read from the local end-of-day store, as of the close before entry, so every row already written has it and no module's schema or decisions change; `python -m cherrypick.technicals entry-context --db <ledger> --schema <schema>`. An index has no relative volume or sector.)*
- At entry, record SPX against its 50-day average, the name's ATR as a percentage of price,
  relative volume against 20 days, the sector, and the entry time.
- Lets "where do the losses cluster?" be answered from the ledgers rather than re-derived per study.
- Recording only; the module's decisions are unchanged.
- *Extended by Chapter 2:* SPX against its 200-day as well, and the holding period at exit.

---

## Chapter 2: The market as a laboratory

### What it argues

- Start from the assumption that you could be wrong. A researcher who wants a result finds it,
  because people collect agreeing evidence and file away the rest. Scrolling charts for setups that
  fit a theory is that bias at work: **when the hypothesis picks the sample, the result proves
  nothing.**
- **Start with a question.** An observation that keeps recurring becomes useful once it is turned
  into a question precise enough to check: which breakout, above which average, over what window,
  measured how, across how many trades.
- **Fix the boundaries before the first test:** the entry, exit and stop rules, the universe and the
  historical window. Re-tuning rules until the backtest looks good is overfitting, and it yields a
  system that backtests well and fails live. A weak result is recorded as a finding.
- **Rewrite every adjective as a number,** so two people applying a rule to the same chart agree.
  The examples are volume against its 20-day average, and distance from the 52-week high plus a
  close above a short EMA. A subjective filter applied with hindsight (picking the names that had
  already moved) inflated a backtest the rule itself would never have produced.
- **A journal replaces memory,** which keeps the best and worst trades and blurs the hundred in
  between, where the signal is. The minimum fields are as in Chapter 1, with results in R.
- **Compare in R, not dollars.** A smaller dollar gain on far less risk can be the better result.
- **Tag the market condition at entry** (SPX against its 50- and 200-day averages) and compare
  average R by tag after a few hundred trades. A strategy that wins in trends and loses below the
  200-day is not broken; it has conditions.
- **Facts and interpretations go in separate columns.** Prices, size and R are facts. Views on
  institutions, sectors or how a chart looked are interpretations: they feed a list of hypotheses,
  and never a calculation or a decision to break a rule.
- **Look for patterns** by setup, condition, time of day, sector and holding period. A frequent
  setup may be mediocre while an occasional one carries the expectancy.
- **Change one variable per backtest.** The example is a 3R target against 2R on identical trades,
  0.4R against 0.3R, worth $6,000 a year at 200 trades and $300 risk.
- **Sample-size benchmarks:** under 30 says almost nothing, 50 gives a first estimate, 100 is solid,
  200 across varied conditions is hard to call coincidence. Evidence is what lets a trader sit
  through a losing streak.
- **The trader's own history is the best research,** better than borrowed systems.

### Already in the suite

| The book | Here |
|---|---|
| Fix the rules before testing; a later change is a new experiment | Declarations written before any data is read; amendments are dated and recorded as such ([edge-hypotheses-plan.md](edge-hypotheses-plan.md) items 14-17) |
| Adjectives as numbers; two readers agree | Setup rules are code constants (`technicals/setups.py`), and changing one is called out because it changes every signal |
| No hindsight in the sample | Signals walked bar by bar from each session's own data; look-ahead guards that were shown to fail (`board_history.py`) |
| A weak result is a finding | Negative results recorded in the plans and module histories, never dropped |
| Facts never fed by interpretation | AI narratives live outside the packages (`scripts/`), and no ledger, report or loop reads them (root CLAUDE.md) |
| Compare in R | Studies report net as a share of max loss, which is R for a defined-risk structure (B3 brings it to the ledgers) |

### Where it is weaker than our process

- **Its sample-size benchmarks contradict Chapter 1's.** Chapter 1 calls 100 trades a first estimate
  and 200 much greater confidence; Chapter 2 calls 50 a first estimate and 100 solid. Neither is
  tied to the spread of R, which decides how many trades a given edge needs.
- **The slicing it recommends is the overfitting it warns against.** Sorting by setup, condition,
  time, sector and holding period, then keeping the cells that look good, is a search over many
  splits; some will look strong by chance. Its own example (0.9R above the 200-day, −0.3R below)
  is a finding chosen after seeing the data. It needs testing on data it was not found in before it
  becomes a rule.
- **A filter found this way rarely survives here.** Our own tests of condition gates mostly failed
  out of sample: the negative-GEX gate hurt flies and bwb, skipping VIX expiry days showed no edge
  for MEIC, and flies' range gate hurt.
- **"Two hundred trades across varied conditions is hard to explain as coincidence"** is only true
  for one system tested once. Across many systems or variants tried, the best one at 200 trades can
  still be luck.
- **The 3R-against-2R example** treats a 0.1R difference as established and prices it at $6,000 a
  year, with no test of whether the difference is real. With R's usual spread, 0.1R over 200 paired
  trades is often within noise.
- **R depends on the stop.** The chapter lists R among the facts, but a loose or moved stop changes
  it. It is a fact only if the initial stop is recorded at entry and never edited.

### Action items

**B4** is extended (above): SPX against its 200-day as well, and the holding period at exit.

**B5. A slice report per arm, with guardrails.** *(built 2026-10-24: `core.slices` with fixed bins, and `python -m cherrypick.technicals slices --db <ledger> --schema <s> --arm <a> [--month YYYY-MM]`, written under `data/technicals/slices/`. In the technicals package rather than `review`, which depends on core only while B4's context reads the end-of-day store. Intervals treat trades in one session as independent, and the report says so. The console reads them on Backtest → slices.)*
- **What:** a monthly table for each arm of average R (or net share of max loss) broken down by
  setup, market condition (B4's tags), entry time and holding period. Every cell shows its n and a
  95% interval, and cells under 30 trades are greyed rather than ranked.
- **Guardrails:** the report is labelled in-sample. A cell that looks good is written up as a
  declared hypothesis and tested on held-out data with a correction for the number of slices; it is
  never turned into a gate from the report. The number of cells looked at is shown, so a lone strong
  cell reads in context.
- **Where:** most naturally in `packages/review`'s fact set, read by the console. It depends on B4
  for the condition tags and on B3 for a common unit.

**B6. Facts and interpretations kept apart where a person decides.** *(built 2026-10-24: `cherrypick-desk propose --thesis "..."` journals the text as its own `thesis` line beside the ticket, outside the sealed order. The open question is answered: desk stored no free text before; its only text was machine refusal reasons. A test pins that only the CLI's write path names it.)*
- The automated loops have no interpretations to separate. The discretionary paths do: `desk`
  tickets and any manual notes on live trades.
- **What:** a free-text `thesis` field on a desk ticket (and any manual trade note), stored beside
  the order record. Nothing that computes P&L, expectancy or a decision ever reads it; a test pins
  that.
- **Routing:** a thesis that recurs becomes a candidate row for the hypothesis list, the book's
  "interpretations feed hypotheses, not trades".
- **Open question:** whether desk already stores any free text that a calculation reads. That
  needs checking before this is scoped.

---

## Chapter 3: Questions and hypotheses

### What it argues

- **Arrive with questions, not conclusions.** "This stock is going higher" ends an investigation;
  "what happens in the five days after this setup, compared with a random entry?" starts one. During
  a losing streak, the trader with conclusions defends the idea; the one with questions checks
  whether the losses fall inside the range the system expects.
- **A hypothesis is not a prediction.** A prediction is settled by one trade. A hypothesis claims a
  positive expectancy over a large sample under stated conditions, so a losing trade is a data point
  rather than a verdict.
- **Every hypothesis has four parts:**
  1. what is measured, precisely enough that two traders agree;
  2. the expected outcome as a number;
  3. the window, the history and the number of occurrences needed;
  4. the pass-or-fail rule.

  The fourth is the one usually skipped, and without it any result can be called encouraging.
- **Start from the null:** assume the idea has no edge, and make the data reject that. A random entry
  wins about half the time, so a 52% setup has barely beaten chance and costs will take the
  difference. A **breakeven win-rate table** gives the win rate each risk-to-reward ratio needs, from
  98% at 50:1 risk to 2% at 1:50.
- **Fund an idea only after it passes:** paper-trade it, or trade minimum size, until the sample says
  something. Rejecting an idea after 100 trades costs time; funding a broken one costs capital.
- **Write the walk-away number before testing.** The examples are expectancy under 0.1R at 100
  trades, or a win rate under 40% with average losses above 1.2R. No second chances, and no filters
  added after the fact. The opposite habit, calling a losing strategy "early", blaming the market or
  adding a filter after each loss, keeps the target moving and fits the rules to noise.
- **Correlation is a starting point; a mechanism raises the bar.** A pattern with a plausible reason
  to repeat needs at least 100 occurrences. One without a reason needs more, plus out-of-sample
  testing, before capital.
- **Stop trading stories.** A market narrative is tested by converting it into the four parts. Most
  can't be, and then it yields no rule and can't be traded responsibly.
- **Keep a hypothesis backlog** in three sections: raw observations, well-formed hypotheses awaiting
  a test, and completed tests, failures included. Failures are kept so the same broken idea is not
  rediscovered six months later. One or two useful findings in ten ideas is a productive rate.
- **The process never finishes.** When results leave the expected range, ask whether the conditions
  behind the hypothesis have changed enough to need a new one, and start the cycle again.

### Already in the suite

| The book | Here |
|---|---|
| Compare with a random entry | Matched random baselines (same name, same year) in items 14-17, stricter than an assumed 50% |
| The four parts, the rejection rule written first | Every declared item states its rule, window, sample bar and pass test before any data is read ([edge-hypotheses-plan.md](edge-hypotheses-plan.md)) |
| Start from the null | One-sided t > 2 in both halves against the baseline, and the arm's own net must be positive too |
| A mechanism first | "Where does the edge live?" is asked of every idea before its mechanics (premium, signal, execution, flow) |
| No filters added after the fact | Amendments are dated, and a post-hoc look is labelled a sensitivity that cannot pass (item 14's A'/C') |
| Paper before capital | Every module is paper by default; live pilots are one arm, armed per day, with a worst-case cap |
| Keep the failures | Results kept in each item and in "Not worth revisiting"; the research queue keeps finished rows with their results |

### Where it is weaker than our process

- **"A random entry wins about half the time" is the wrong baseline.** A random entry's win rate
  depends on its stop and target (with an asymmetric pair it sits near the breakeven rate, not 50%),
  and on drift: random long entries in a rising market win more than half. That is why the suite
  draws matched random entries rather than assuming one.
- **The breakeven table is arithmetically right** (risk ÷ (risk + reward)), **but it is costless and
  binary.** Costs move every row up, most at the high-win-rate end where premium sellers live. A
  credit spread sold at 35-40% of its width risks about 1.5-1.9 to make 1, so it needs roughly
  60-65% wins before costs. And options rarely finish at exactly the stop or the target.
- **Its own sample bars disagree.** The text asks for 50 trades before rejecting in one place and 100
  in another; for a pattern with no mechanism it says "100 or more" in the text and 150 in the
  checklist.
- **A walk-away line at a point estimate is noisy.** "Under 0.1R at 100 trades" ignores the spread of
  R; with a standard deviation near 1.5R, 100 trades leave an interval of about ±0.3R either way.
- **A mechanism can be a story too.** Requiring one is right, but a reason found after the pattern
  is easy to invent. The mechanism belongs in the declaration, written before the test, and it only
  lowers the bar if it predicts something the pattern alone doesn't.
- **"One or two in ten" can be all luck.** At a 5% false-positive rate per test, ten ideas tested
  without a correction produce a false finding about 40% of the time. The suite corrects across a
  family (Holm), which the book never does.
- **"Outside the expected range" needs a defined range.** The chapter never says how to compute one.

### Action items

**B1** gets an answer to its open question: *how is "the worst in the evidence" set?*
- The book's idea of an expected range, made concrete: when an arm is armed, bootstrap its tested
  trades (the replay or paper history it went live on) into thousands of resampled sequences of the
  live sample's length, and read off the 95th or 99th percentile drawdown and losing streak.
- Those percentiles become the arm's kill thresholds, written into its config and dated, then never
  re-derived while it trades. An arm with too little evidence to bootstrap (fewer than about 100
  trades) cannot be armed until it has some, or runs at its minimum size under a hand-written limit.
- The same bands give the console its "outside the expected range" marker (B2).

**B7. A hypothesis register.** *(built 2026-10-24: `docs/hypotheses.json`, seeded with items 1-17 as legacy rows that point to their prose; `cherrypick hypotheses` checks it, lists rule changes due a re-read, and finds rejected look-alikes. The rule-change log starts empty.)*
- **Why:** [edge-hypotheses-plan.md](edge-hypotheses-plan.md) holds the declarations and results as
  prose. That suits a reader, but makes "has this been tried before, under what conditions?" a
  search, and the book's "review the failed ones before writing a new one" a matter of memory.
- **What:** one machine-readable row per hypothesis, kept in the repo beside the plan. Each row has:
  - its id (the item number, and the research queue's `study` slug);
  - the four parts;
  - the mechanism, written before the test;
  - the rejection rule;
  - its status (observation, declared, running, passed, failed, no verdict);
  - a pointer to its result (the plan section and the local results directory).
- **A check:** declaring a new hypothesis lists any rejected one with the same setup, side,
  structure and window, so a retest is a deliberate choice, said so in the declaration.
- **Results stay local.** A row holds a status and a pointer, never licensed figures, so it can live
  in the public repo.

---

## Chapter 4: Experimental design

### What it argues

- **A good experiment removes excuses,** because every rule was written before the first data point.
  The common failures: changing several rules at once, testing only the charts and markets you
  remember, running a strategy through one bull market and calling it general, and adjusting rules
  until the backtest looks good.
- **The goal is not to prove a strategy works** but to find out whether it deserves capital.
- **Change one variable at a time, against a control.** The example keeps entries, stops and sizing
  fixed and changes only the exit, from 0.20R to 0.30R, worth $7,500 a year at 250 trades and $300
  risk.
- **Write the rules before looking at results,** in enough detail that a stranger could rerun the
  test: entry with numeric qualifiers, stop, exit, sizing, the minimum number of trades, and the
  result that rejects the idea. Then change nothing until the test is done.
- **Include every qualifying trade.** Excluding trades one by one ("earnings that morning", "a Fed
  week") hollows out the sample. Dropping 20 of 150 leaves a sample of a market that doesn't exist.
  A condition that genuinely warrants exclusion is written into the entry rules **before** the test,
  such as closing before earnings.
- **Keep testing and improving separate.** A losing streak mid-test is not a reason to change rules.
  A **table of the chance of X consecutive losses within 100 trades** at each win rate shows that a
  five-loss streak is normal at 60% wins, while seven deserves investigation. Finish the planned
  test, then look at the whole dataset for the next experiment.
- **Avoid fitting the past.** Adjusting rules after every poor result fits random events. Each added
  rule needs more data to justify it; eight rules tested on 50 trades proves nothing. Keep systems
  simple.
- **Test across at least three environments,** defined by the index:
  - an uptrend, above a rising 20-day average;
  - a sideways market, under 10% either way over six months;
  - a downtrend of at least 20% from a peak.

  The example (0.25R, 0.05R, −0.15R) leads to trading only above a rising 20-day.
- **Repeat the test on different data:** other names in the same period, or the same names in a
  non-overlapping period. The example is 0.22R in 2015-2020 against 0.18R in 2010-2015 (consistent),
  where −0.10R would have meant the original was period-specific.
- **Keep a research record** of every experiment, separate from the live journal: the hypothesis,
  full rules, date range and universe, trade count, expectancy and win rate, the result against the
  rejection rule, and the conclusion. Failures are the most valuable part.

### Already in the suite

| The book | Here |
|---|---|
| Rules written before the data; nothing changed mid-test | Declarations before any data is read; amendments dated and labelled |
| Control and one change | Control and treatment arms on the same sessions; replays that change one rule |
| Every qualifying trade counted; exclusions written into the rules beforehand | The planners apply only declared exclusions (earnings inside the trade, ex-dividend for calls, suspected splits) and **report every drop by reason** (`credit_spread_plan.py`) |
| Different data | Time halves plus a held-back holdout |
| Simple over fitted | Studies test the declared rule as written; sensitivities are labelled and cannot pass |
| A research record, failures included | Each item's declaration and result in [edge-hypotheses-plan.md](edge-hypotheses-plan.md), and the local results directories |

### Where it is weaker than our process

- **The losing-streak table understates streaks at the win rates that matter.** Checked exactly
  (independent trades, 100 per sample), it is close at low win rates and too low at higher ones:

  | Win rate | Streak | Exact | Book |
  |---|---|---|---|
  | 60% | 4 in a row | 80% | 70% |
  | 60% | 5 | 46% | 38% |
  | 60% | 7 | 9% | 7% |
  | 70% | 4 | 43% | 32% |
  | 70% | 5 | 15% | 11% |

  It is also per 100 trades. Over the chapter's own 300-trade test, seven losses in a row at 60%
  happens about one time in four. That isn't a reason to investigate.
- **Streaks are not independent in real trading.** Trades opened in the same regime lose together,
  so real streaks run longer than any independent-trade table. That is why B1 bootstraps a strategy's
  own trades, and only does so in blocks where trades overlap.
- **Its environment example breaks its own rule.** Choosing "trade only above a rising 20-day" after
  seeing 0.25R / 0.05R / −0.15R is a filter picked from the results. It needs a fresh test on data it
  wasn't found in. Our own gates of this kind mostly failed that test.
- **Three environments thin the sample.** A 20% index decline is rare: a few spells a decade. The
  downtrend cell will hold few trades, too few to support "don't trade here" on its own.
- **Replication is judged by eye.** 0.22R against 0.18R is called consistent, and −0.10R a failure,
  with no allowance for noise. Either could be chance at typical sample sizes.
- **Excluding earnings is right, but it is a design choice with a cost.** The trades left out are a
  different strategy's; the result describes the rule as written, not the market.

### Action items

**B1** is reinforced: the chapter's "expected range" of streaks is exactly what B1's bootstrap
produces, from the strategy's own trades rather than a table that assumes independence.

**B8. One declared market-environment classifier.** *(built 2026-10-24 as `cherrypick.core.environment`: the three definitions made exclusive by an order (downtrend 20% off the 252-session peak, then uptrend above a rising 20-day average, then sideways within 10% over 126 sessions, else `mixed`), from the closes before the session; B4's entry context carries it. On the store's SPX history (2023-08 on) no session is a downtrend: the 2025 sell-off closed just short of 20%, so that cell is empty.)*
- **What:** a pure function in `cherrypick.core` that labels each session uptrend, sideways or
  downtrend from the index's own bars, with its rules declared once (the book's three definitions as
  a starting point) and its parameters fixed.
- **Used by:** B4's context columns, B5's slice report, and every study report, which shows results
  by environment as description. A filter on it is a new hypothesis, tested under the usual rules.
- **Why:** today each study defines its own regime (SPY against its 200-day in one, VIX/VIX3M in
  curve and contango), so "it fails in downtrends" can mean different things in different reports.
- **Report beside it:** how many sessions and trades each environment holds, so a thin downtrend
  cell reads as thin.

**B9. Replication across names.** *(built 2026-10-24: the template in edge-hypotheses-plan.md, `core.robustness`)*
- **What:** future declarations add a second, independent split. The universe is divided by a fixed
  hash of the symbol into two name-halves, and a pass requires the result to hold in both, as well as
  in both time-halves.
- **Why:** a time split guards against a lucky period; a name split guards against a few names
  carrying the result, which the per-symbol tables (item 17) can show but not test.
- **Cost:** it halves the trades in each cell again, so it suits tests with large samples. A
  declaration says when it applies.
- **Scope:** declarations from now on. Results already declared and run are not re-judged.

---

## Chapter 5: Uncertainty and expectancy

### What it argues

- **Nobody knows what the market will do next.** What pays over thousands of trades is putting the
  odds in your favour and executing consistently. The better question is what has happened across a
  large sample of similar conditions, and how to size for it.
- **Every trade is uncertain, and losing streaks are normal.** At a 60% win rate, a five-loss streak
  within 100 trades is common (the chapter repeats Chapter 4's 38%). A trader who reads every normal
  streak as a broken system abandons good strategies before their edge plays out.
- **Judge a trade by whether it followed the rules,** not by its P&L. A loss taken exactly to plan
  is a well-executed trade; a win taken outside the rules is luck attached to a broken process.
- **Think like a casino.** American roulette's house edge is 5.26%. The casino doesn't know which
  spin wins, but it knows what tens of thousands of spins produce. A trader with a tested positive
  expectancy runs the same kind of business; one trading on predictions is gambling.
- **Expectancy is the operating number.** The worked examples:
  - 50% wins at 2R against 1R losses is +0.50R a trade, about $25,000 over 250 trades at $200 risk;
  - System A, 40% wins at 3R / 1R, is +0.60R, about +$30,000 a year;
  - System B, 80% wins at 1R / 5R, is −0.20R, about −$10,000 a year.

  The market pays expectancy, not accuracy, and the chapter calls reward-to-risk the biggest
  driver of it.
- **Each trade is one data point.** A hundred trades show the shape of the distribution; three
  hundred get close enough to decide. At a 40% win rate, eight losses in a row happens from time to
  time; fifteen deserves investigation.
- **Compute the review threshold before the streak arrives.** Write down the streak length that
  triggers a formal review, so math decides rather than the emotional weight of the losses.
- **Confidence comes from documentation.** Four hundred documented trades describe the system's
  behaviour through a losing streak; confidence built on a good month dissolves when it ends.
- **Judge the decision, not the result.** The example: one trader follows every rule and loses 1R;
  another doubles size on conviction and makes 2R. The first ran the better operation. Sizing on
  conviction eventually puts maximum size on a failing setup.
- **The checklist:**
  - confirm and record that each trade meets the written rules;
  - update expectancy monthly;
  - compare systems by expectancy, requiring 200 trades before funding;
  - set the review streak from the table in advance;
  - size by fixed fractional risk, never conviction;
  - keep an execution score separate from P&L as the main weekly measure;
  - at a streak review, require evidence it is outside the expected range before changing a rule;
  - review expectancy across three market environments each quarter.

### Already in the suite

| The book | Here |
|---|---|
| Rules followed exactly, no in-the-moment changes | Every module trades from code and config; nothing is decided at the order screen. Measurement-affecting changes wait for a declared boundary |
| Fixed sizing, never conviction | Module arms size from config (`live` blocks, caps); there is no conviction input to any loop |
| Expectancy over win rate | Studies and the console report net per trade and net as a share of max loss, never win rate alone |
| A tested edge before capital | Paper by default; a live arm is armed per day, one arm at a time, under a worst-case cap |

### Where it is weaker than our process

- **The streak numbers are wrong again, by the chapter's own example.** Checked exactly, assuming
  independent trades:

  | Claim | Exact |
  |---|---|
  | 60% wins, five losses in a row within 100 trades: "about 38%" | **46%** |
  | 40% wins, eight in a row "from time to time" (250 trades a year) | **83%** of years |
  | 40% wins, fifteen in a row "deserves investigation" | **4%** of years, a reasonable trigger |

  So the advice is sound but the table it relies on understates streaks, and it is quoted per 100
  trades while the examples run 250 a year. A threshold has to be computed for the number of trades
  it covers.
- **The casino analogy hides the real difference.** A casino's edge is exact, structural and doesn't
  decay. A trader's expectancy is an estimate with an error bar, from a market that changes. "Keep
  executing through the streak" is right only beside a written kill rule. That is B1, and the
  chapter's own 15-loss review is a version of it.
- **"Reward-to-risk is the biggest driver of expectancy" is wrong for options.** Win rate and payoff
  are traded against each other by the price. A wider target lowers the hit rate, and for an option
  sold at a fair price the two cancel: expectancy is near zero whatever the ratio. Changing the ratio
  is payoff shaping, not edge. This is the suite's own rule ("where does the edge live?"), and our
  short-delta studies found exactly that.
- **System B is our premium-selling profile.** 80% winners with large losers describes most
  credit-selling structures. The chapter treats it as an obvious trap; for us it is the normal case,
  judged by costs and tails, not by its look.
- **All examples are before costs.** The chapter says so once and moves on.
- **Yet another sample threshold:** 200 trades before funding, against 100 in Chapter 3 and 30, 50
  and 100 elsewhere.
- **Fixed fractional sizing is right, but the chapter stops there.** Nothing on how a risk fraction
  relates to the strategy's own drawdown distribution (B1's bootstrap answers that too).

### Action items

**B1** is reinforced. The chapter's "compute the review streak in advance" is B1's frozen threshold,
and its 15-loss example shows the right order of magnitude: a level reached in a few percent of
years. B1 computes it from the arm's own trades for the number it will actually trade, rather than
from a per-100 table.

**B8** is reinforced by the quarterly three-environment review.

**B10. An execution record per live arm.** *(built 2026-10-24 as `cherrypick execution`, a read over the live ledgers: the mid at submit stands for the model's price; overrides are kill clears (`state/kill/history.jsonl`), config-backup commits during a session and manual closes; the gap counts only sessions live was working. Desk orders keep no ledger and cannot be read. The console shows it on Live → execution.)*
- **Why:** the chapter's execution score is about rules a person might break. Our loops follow their
  rules by construction, so in the suite the process breaks in two other places: the fill, and a
  person stepping in.
- **What, for each live order:**
  - the price the arm's own model expected (the working price paper would have used) against the
    actual fill, in dollars and as a share of the structure's width;
  - whether it filled, partly filled or missed, and how long it took. Flies' history showed misses
    are not random: they cluster on the entries that move toward completion.
- **Human overrides, flagged on the trade:** a manual close, a re-arm after a block, a sizing or
  config change mid-session, a desk order in the same name as a live arm.
- **Read weekly** beside P&L: slippage against model per arm, fill rate, and the overrides. A losing
  week with clean execution reads differently from one with overrides.
- **Builds on** the live ledgers and fill-alert data already recorded; mostly a join and a read
  surface.

---

## Chapter 6: Signal and noise

*Pages 70–71 (effect size; hot and cold streaks) were photographed out of focus. Their main points
and the two dollar examples are legible; finer detail there may be missing.*

### What it argues

- **Small samples mislead.** Seven heads in ten flips doesn't change a fair coin. A strategy can win
  ten in a row with no edge, or lose its first seven with one. Price moves mix signal (a tendency that
  repeats) with noise (earnings surprises, large orders, headline reactions, data releases), and in
  small samples the two look the same.
- **Report the mean and the median.** In the example (−1, −1, −1, 0, 1, 1, 1, 2, 2, 10 R), the mean is
  1.4R but the median is 1R: one 10R trade carries the average. If the mean is well above the median,
  a few trades are producing the profit.
- **Standard deviation measures how bumpy the equity curve is.** Two systems at 0.30R can feel very
  different; the wider one needs smaller size and more patience.
- **Sample size:** under 30 says little, about 100 is a first estimate, 200 much more confidence, 300
  firm evidence.
- **Statistical significance:** a change from 0.30R to 0.35R may be chance after 30 trades; after 300,
  the chapter says, you can be much more confident. Be wary of a large improvement on a small sample.
- **Effect size:** is the improvement worth it? 0.30R → 0.31R is about $500 a year at $200 risk and
  250 trades; 0.30R → 0.60R is about $15,000. Change a working system only for a meaningful
  improvement.
- **Hot and cold streaks mean less than they seem.** Results return toward the long-term average.
  Don't raise size after a hot streak or abandon a strategy after a cold one.
- **Survivorship bias:** testing today's index members backwards tests only the companies that
  survived. Use point-in-time membership instead.
- **Testing many ideas finds false edges.** Try 100 versions and one will look good by chance. Test
  the winner on data it wasn't built on (built on 2015-2020, tested on 2021-2025, nothing changed).
- **The checklist:**
  - compute the mean and median every month and flag a gap above 0.3R;
  - use standard deviation to set size;
  - treat under 100 trades as early, and aim for 200;
  - value any improvement in dollars before changing the system;
  - don't resize after streaks;
  - use point-in-time history;
  - test everything out of sample before real money.

### Already in the suite

| The book | Here |
|---|---|
| Testing many versions finds a false edge | Holm across each declared family; sensitivities labelled and unable to pass |
| Out-of-sample data | Both time halves required, plus a held-back holdout no study reads until it is opened deliberately |
| Point-in-time universe | The history store keeps names that stopped trading (checked 2026-10-24: 1,963 of the 7,846 names that ever traded $100M a day have no bars after 2025), and the planners admit a name only on its dollar volume as of the session before entry |
| Don't resize after streaks | Sizing comes from config and changes only at a declared boundary |

### Where it is weaker than our process

- **The mean-median flag only looks one way.** It warns when the mean is well above the median (a few
  big winners). Premium selling has the opposite shape: many small wins and rare large losses, so the
  **median is above the mean**, and that is the dangerous case. A credit spread can show a healthy
  median every month until one loss takes back a year. The check has to flag both directions, and for
  a short-premium arm the worst trade and the left tail matter more than either average.
- **A fixed 0.3R gap is arbitrary.** Whether a gap matters depends on the spread of results; it should
  be judged against the distribution, not a constant.
- **Standard deviation understates fat tails.** Sizing by it assumes losses look like the middle of the
  distribution. For short premium, size from the drawdown distribution (B1's bootstrap) or the worst
  trades, not the spread alone.
- **The significance example is wrong by a wide margin.** With a typical spread of about 1.5R per trade,
  a 0.05R improvement (0.30R → 0.35R) compared as two separate samples gives t ≈ 0.4 at 300 trades
  each. Reaching t = 2 would take about 7,200 trades per version. Measured **paired** (the same trades
  with one rule changed), it needs roughly 150-400. The chapter never gives a test, and never makes the
  paired-or-not distinction that decides the answer.
- **Effect size is quoted before costs and without its error.** The $15,000 is an estimate with an
  error bar several times the size of the smaller example.
- **Regression to the mean is right, but it sits against B1.** A cold streak is usually noise, and
  sometimes decay. Only a threshold computed in advance (B1) tells the two apart without bias.
- **Out of sample once is a start.** One later window, with no correction for how many versions were
  tried before it, can still pass by luck.

### Action items

**B2** is extended: beside each arm's mean, show the median, the spread and the worst trade, with a
skew flag in **both** directions. A short-premium arm whose median sits well above its mean is flagged
as clearly as a trend arm living on a few big winners.

**B11. A survivorship audit of the residual paths.** *(audited 2026-10-24; findings below)*
- **Checked already (2026-10-24):** the history store keeps delisted names, and universes are built
  point-in-time from each session's own dollar volume.
- **What is left to audit:**
  - **Delisting and acquisition:** how a trade open across a delisting or a cash takeover is settled
    in each replay (the last close, the deal price, or dropped). A dropped trade is a biased one.
  - **Empty chains:** how often a delisted name's end-of-day chain comes back empty from the vendor.
    Those trades are dropped as `empty`, which would quietly remove failed companies from the options
    studies.
  - **Today-only inputs:** the board's sector map already holds today's names only (a declared limit
    in item 14). Find any other input that does the same.
- **Output:** each finding becomes a fix, or a declared limit carried into future declarations.
- **Findings (2026-10-24, code read; no figures):**
  - *Delisting and acquisition.* The technicals study settles a position open at a name's last bar at
    that close (`study.py`, `score`); no deal or recovery price is modelled. For a cash takeover the
    close sits near the deal; after a bankruptcy it sits above the eventual zero, so longs read
    slightly better than they were. `pead_history.py` leaves forward returns empty past a name's last
    bar, so events of names that left (acquired or failed) drop out of the horizons they cannot reach,
    a lean toward survivors. `emfly_history.py` and `earnings_navigator_history.py` drop such trades
    with a counted reason (`expiry_after_bars`, `no_bar_on_date`), not split out as delisted.
  - *Empty chains.* Not silent anywhere. ThetaData's end-of-day fetch records every empty request
    and skips it on re-run; most empties are single sessions or expiries of names that otherwise
    returned chains. Intraday sessions with no samples are recorded refused with a reason. Dolt
    chains that are empty or thin are counted drops (`no_entry_chain`, `no_atm`, `no_fly`), which
    lean against names whose chains thinned before they left. Nothing reads the end-of-day chain
    store yet, so its first study must state how empties are handled.
  - *Today-only inputs.* `study.tradable_today()` applies today's weeklies and liquidity label to
    history; it is already labelled hindsight and kept out of the universe, and it reads optimistic.
    `emfly_history.option_symbols` takes "optionable" from two sampled days a year (mild). No study
    reads the sector map, `leveraged_funds.json`, `seed_universe.json` or the liquidity verdicts;
    each needs a declared limit if one ever does.
- **Declared limits** (carried into every declaration through the template in
  `edge-hypotheses-plan.md`):
  - Delisted or acquired positions settle at the last traded close; no deal or recovery price.
  - Empty vendor chains are recorded and excluded, and the declaration says how many.
  - A view built on today's tradable list is hindsight, and reported apart from the point-in-time one.
- **Fixes (landed 2026-10-24):** the technicals study's result carries `unscored`, per setup and
  reason (`no_fill`, `open_at_end`), so positions on a name whose bars stop short of the ended
  threshold are counted instead of vanishing; `pead_history.py`'s report carries
  `lost_to_data_end`, the events per horizon with no forward return because the name's bars end.

**B12. A minimum detectable effect in every declaration.** *(built 2026-10-24: the template in edge-hypotheses-plan.md, `core.robustness`)*
- **What:** a declaration states the smallest effect its planned sample can detect at the pass bar,
  computed from the spread of R in comparable past results. A test that ends with no verdict then says
  what it has ruled out ("no edge larger than 0.08R"), rather than nothing.
- **Paired by default:** a rule change is compared on the same trades, which cuts the sample needed by
  an order of magnitude or more. An unpaired comparison has to say why.
- **Why now:** item 17 has eight setup-sides with very different sample sizes (breakout has a few
  hundred trades, mean reversion several thousand). Its declaration was written before this item and
  is not re-judged; later ones gain the line.

---

## Chapter 7: Updating on evidence

### What it argues

- **Update beliefs as evidence arrives.** The opening story is Semmelweis (1846): handwashing cut
  deaths in a Vienna maternity ward from about 10% to under 2%, and the establishment rejected the
  evidence. Traders do the same in miniature: when evidence contradicts an opinion they change the
  story rather than the opinion.
- **Every belief is a working estimate.** "This strategy works" becomes "this strategy has produced
  0.35R across 300 trades, and I have high current confidence that it continues". When the next 50
  trades come in at 0.10R, that is weaker evidence than the history but not enough to overturn it:
  reduce size while more data arrives, rather than quitting or ignoring it.
- **A rolling-window framework turns that into rules:**
  - after each 50-trade window, compare its expectancy with the long-run figure;
  - within one standard deviation, do nothing;
  - two windows in a row more than one standard deviation below: cut size by 25% and keep watching;
  - three in a row: suspend trading and review whether conditions have changed enough to invalidate
    the hypothesis.

  This replaces the binary of full size or quit with responses scaled to the evidence.
- **Counter confirmation bias in advance.** Before taking a strong view, write the evidence that
  would change it as precise thresholds: rolling 50-trade expectancy below zero for two windows, a
  close below the level central to the thesis, a sector lagging the index by 10% over 30 days. A
  trader who can't write that down is protecting a conclusion, not evaluating evidence.
- **Evidence has weights,** in three tiers:
  - **anecdotal** (single trades, news, opinions without a track record): a note in the backlog at
    most;
  - **observational** (patterns across 50+ trades, documented fundamental changes): can justify a
    size reduction or a formal rule test;
  - **experimental** (a designed test with held-out data and a rejection rule): drives system
    decisions.

  An analyst upgrade weighs according to that analyst's verified record on similar calls.
- **Updating is not constant changing.** The two failures are ignoring decay until the account is
  down 30%, and abandoning a system after a losing stretch inside its expected range. Both come from
  having no framework that says what evidence earns what response.
- **Separate identity from the system;** a hypothesis is the best current explanation, held
  expecting revision.
- **Strategies expire.** Some edges last a decade while their structural cause holds; others are
  competed away in two or three years, or vanish once published. Recognising the end when the rolling
  evidence says so, not predicting it, is the job.
- **The checklist:**
  - state each system as a current estimate (expectancy, trade count, an explicit "will update");
  - run 50-trade windows against the long-run average;
  - write the size-cut and review triggers in advance;
  - write disconfirming evidence before forming a view;
  - sort evidence into the three tiers and scale responses to them (single-window departures move
    allocation 25% or less, sustained ones up to suspension);
  - update the written system description every window and review it quarterly;
  - log every system change with its evidence, the rule changed, and the expectancy over its first
    100 trades.

### Already in the suite

| The book | Here |
|---|---|
| Evidence tiers; only experiments drive decisions | Module rules change only through a declared test; observations go to the hypothesis list; AI narratives and anecdote never reach a loop |
| Write what would change the view | Every declared item states its rejection rule before data is read |
| Analyst upgrades weighted by record | Tested: an analyst revision added no weight to the bearing (technicals round 6) |
| Edges expire | Each module's history keeps its arms' results, and the suite retires arms that fail (flies retired, curve's `hook`, pmcc's `control`) |
| Updating at boundaries, not constantly | Measurement-affecting changes are batched to declared boundaries, so updating never becomes constant changing |

### Where it is weaker than our process

- **"One standard deviation" is undefined, and the reading decides whether the rule works.** Checked
  by simulation (trades with a 1.5R spread, a 0.35R long-run expectancy, 50-trade windows):
  - **Measured in standard errors of the window's mean (about 0.21R),** the rule is good. A healthy
    system has its size cut by chance in about 8.5% of 250-trade years and is suspended in about 1%.
    If the edge drops to zero, the size cut comes after about 155 trades on average, and suspension
    after about 240.
  - **Measured in the standard deviation of a single trade (about 1.5R),** which the wording invites,
    the rule never fires, whether the system is healthy or dead.
- **The baseline is a backtest, not live.** Live fills and costs sit below a backtest's, so live
  windows will trend under the historical average even when nothing has changed, and the rule will
  fire for the wrong reason. Compare live windows with the live-expected figure (the backtest less
  measured execution costs, which is B10's job).
- **It calls this Bayesian, but it is a control chart.** No prior or likelihood is set out; the rule is
  a run of out-of-band windows. That is fine, and better tools exist for the same job: a CUSUM
  accumulates small shortfalls and reacts faster to steady decay than whole-window counts.
- **A 25% size cut is a judgement, not a result.** It is reasonable for live risk, but it changes the
  arm mid-measurement. Here that belongs to the live sizing only, written in advance, and never to a
  paper arm, whose job is to keep measuring the same thing.
- **"Observational evidence can justify a size reduction"** is right for live risk and wrong for rules.
  A rule changed on observational evidence is the post-hoc filter the earlier chapters warn against.
- **Edges expiring is asserted, not measured.** Decay and noise look the same in short windows, which
  is the reason for B1's thresholds.

### Action items

**B1** is extended with the chapter's graduated response, fixed where it is loose:
- **Three levels per live arm:** normal, reduced size, and entries blocked pending review, never a
  close of open positions or a flipped gate.
- **Measured properly:** each completed 50-trade window as a z-score of its mean against the arm's
  **live-expected** expectancy (backtest or paper less B10's measured execution cost), in standard
  errors of the window mean. A CUSUM beside it catches steady decay sooner. The thresholds, the
  reduced size and the window length are written into the arm's config when it is armed, with the
  false-alarm rate they imply (about 8.5% a year for a cut and 1% for a block, at the chapter's
  levels).
- **Paper arms are never resized.** They keep measuring; only the live arm's risk responds.

**B6** is extended: a desk ticket or manual trade carries an **invalidation condition** written at
entry (a price, a date, a measurable event), recorded beside the thesis and shown when the position is
reviewed.

**B7** is extended: every rule change goes into the register as its own entry, with the evidence
behind it and the rule changed. It is re-read automatically after its first 100 trades against what
the change was expected to do, so a change that didn't deliver is noticed rather than forgotten.

---

## Chapter 8: Correlation, causation and overfitting

### What it argues

- **Large datasets always contain coincidences.** Leinweber's demonstration fitted the S&P 500's
  history closely with butter and cheese production. Test enough indicators, filters and combinations
  and something will fit one stretch of history and fail on the next.
- **Correlation is not causation.** A 10/50-day crossover before a rally didn't cause it: buying
  pressure caused both. Indicators summarise price and volume after the fact; they can be signals but
  rarely causes.
- **Every pattern needs a mechanism** before it reaches a live system. The example is the 52-week-high
  effect, explained by investors anchoring on the old high and under-reacting, and few holders waiting
  to sell at breakeven. A pattern without an explanation deserves more scepticism.
- **Ask what caused the move, not what came before it.** A MACD buy signal after strong earnings
  reflected buying that was already under way.
- **Perfect backtests deserve more scepticism, not less.** Test on independent data without changing
  rules or parameters; two independent passes are much stronger than one.
- **Stories are not evidence.** "Rising rates hurt growth stocks" is untestable; "when rates rise over
  three months, growth underperforms value over the next three" can be tested.
- **Extraordinary claims need extraordinary evidence.** For any claim, ask how many trades and years,
  whether it held in bull and bear markets, whether it was tested on new data, whether anyone else
  reproduced it, and whether it was live or only a backtest.
- **Explanation is not prediction.** A rationale built after seeing a backtest makes a pattern feel
  more durable than the data justifies; only new data tests it.
- **Think like an investigator:** list what else could explain the result (one bull market, one
  unusual period, luck, rules adjusted to fit) and rule each out.
- **The checklist:**
  - every rule needs a logical explanation;
  - ask what else could explain a pattern;
  - turn opinions into hypotheses;
  - test on data not used to build;
  - question extraordinary claims;
  - review whether the conditions a strategy relies on still exist;
  - keep a record of ideas against outcomes;
  - consider several explanations before changing a system.

### Already in the suite

| The book | Here |
|---|---|
| A mechanism before mechanics | "Where does the edge live?" (premium, signal, execution, flow) is answered before any mechanics ([edge-hypotheses-plan.md](edge-hypotheses-plan.md)) |
| Rule out luck and drift | Matched random entries (same name, same year) remove market drift; t in both halves and Holm address luck |
| Independent data, rules unchanged | Both halves plus a held-back holdout; rules fixed in the declaration |
| Indicators measure, they don't cause | The setup studies treat signals as entries to be scored against random entries, and most failed (setups rounds 3-7) |
| Live versus backtest | Paper arms run every day beside the replays, and the flies quote-history replay was validated against live fills before its results were used |

### Where it is weaker than our process

- **Its own mechanism example is post hoc.** The 52-week-high explanation was written after the effect
  was found, which is the chapter's own warning about explanations built backward. A mechanism only
  adds weight if it predicts something the pattern alone doesn't (for example, a stronger effect
  where fewer holders sit near breakeven).
- **"A mechanism makes a pattern credible" can be gamed.** A plausible story can be found for almost
  any pattern; the chapter gives no test for a mechanism beyond plausibility.
- **"Has anyone reproduced it?"** is good advice it doesn't follow itself: none of the book's worked
  figures cite a source or a sample.
- **Two independent tests are "much more convincing"** without saying how many variants were tried
  before the first one passed, which is what decides how convincing a pass is.
- **Its testable rates example** still needs a rule for how much underperformance counts, and over how
  many periods. It stops at "testable".

### Action items

No new item; the chapter mostly restates what the suite already does. **B7** is extended:
- Each hypothesis states its **rival explanations** before the test (drift, one regime, a few names,
  costs, a data artefact) and the check that rules each out (the matched baseline, B8's environments,
  B9's name split, B12's net-of-cost bar, B11's audit).
- A mechanism is recorded **with a prediction of its own** beyond the pattern, where one exists, and
  marked "post hoc" when it was written after the result.

---

## Chapter 9: Robustness testing

### What it argues

- **A profitable backtest is the start of research, not permission to trade.** Backtests answer one
  narrow question about one historical sequence, and they assume conditions that don't exist: filling
  at the signal bar's close, no spread, slippage or partial fills, fixed commissions. Each assumption
  inflates the edge, and together they make the "backtest-overfitting premium".
- **Four tools:**
  - **Walk-forward testing:** build the rules on years 1-3, test them unchanged on year 4, slide a year
    and repeat, then chain the out-of-sample years into one record. At least 50 trades per test period.
  - **Monte Carlo:** reshuffle the historical trades thousands of times to see the range of drawdowns
    and streaks. A backtest's 8R maximum drawdown might be 15R in a worse order. Size so the larger
    drawdown is survivable.
  - **Stress testing:** cut the win rate 5-10 points, cut the average winner 10-20%, raise costs, test
    bull, bear, sideways, high- and low-volatility periods, then combine the stresses.
  - **Sensitivity analysis:** a 20-day average should also work at 15, 18, 22 and 25 days. If one exact
    setting works and its neighbours fail, it is probably overfit.
- **Realistic fills:** enter at the next available price, include commissions, slippage and spreads,
  and allow for partial fills in thinner markets. A 0.40R backtest might be 0.30R after costs.
- **The checklist:** walk-forward, Monte Carlo, stress, sensitivity, realistic execution, a full record
  of every test, and no single impressive backtest trusted alone.

### Already in the suite

| The book | Here |
|---|---|
| Realistic fills | Entry on the session after the signal; working-price fills (halfway from mid to natural), `core.fees` per leg and settlement; the flies quote-history replay fills on recorded quotes and was checked against live fills (entries matched 94%) |
| Independent test data | Both halves plus a held-back holdout no study reads until opened |
| Costs as the decider | Every study is decided net; the income and credit-spread studies were decided by costs |
| A record of every test | Each declaration and result in the plan, with local results directories |
| Thin markets | The leg bars judge every option leg (and a spread as one order) before it counts as tradable |

### Where it is weaker than our process

- **Reshuffling trades understates risk.** A permutation keeps the same trades in a new order: the
  total is fixed and no trade is worse than history's worst. It also assumes trades are independent.
  Resampling **with replacement**, in blocks where trades overlap, is the better tool (B1 already uses
  it), and even that cannot invent a loss larger than the worst ever seen. For short premium, add a
  tail scenario explicitly.
- **Walk-forward as described leaks.** Re-developing rules each year on a window that overlaps the
  last means the researcher has seen most of the data before testing on it. It is honest only if the
  re-fitting is mechanical (a fixed procedure, declared once), not a person tuning each year.
- **Fifty trades per walk-forward period is too few to judge one period,** by the book's own earlier
  thresholds. The chained record is what gets judged.
- **Stress tests with no pass bar.** "Remains profitable" after cutting the win rate 5-10 points is a
  very high bar for any strategy with a modest edge, and an arbitrary one. A declared stress (costs at
  1.5×, say) is better than an open-ended menu.
- **Sensitivity without a correction.** Testing 15, 18, 20, 22 and 25 days is five more tests; picking
  the best of them is the overfitting the chapter warns against. Sensitivity should describe the
  neighbourhood of a setting fixed in advance, never choose the setting.

### Action items

**B1** is confirmed: its bootstrap is the chapter's Monte Carlo, done with replacement and in blocks,
plus an explicit tail scenario for short-premium arms.

**B13. A robustness report beside every passing result.** *(built 2026-10-24: the template in edge-hypotheses-plan.md, `core.robustness`)*
- **Parameter neighbourhood:** the declared setting and its neighbours (for example the setups'
  lengths at ±10-25%), reported as description. A pass sits on a plateau; a lone peak is flagged.
  The setting itself is never re-chosen from this table.
- **Cost stress:** results at 1.5× and 2× the modelled costs. A declaration names the stress the result
  must survive to pass (1.5× by default), so a pass that only exists at mid-quality fills is caught.
- **Drawdown:** the B1 bootstrap of the result's own trades, giving the drawdown and losing-streak
  range a live arm should expect, which then becomes its frozen threshold if it goes live.
- **Scope:** future declarations. It reuses each study's own trades, so it costs compute, not data.

---

## Chapter 10: Markets as adaptive systems

### What it argues

- **Markets are complex adaptive systems, not machines.** Participants respond to each other, so the
  same input never produces quite the same response. A strategy can stop working with no change in
  its rules, because the market changed.
- **Emergence:** a trend, its acceleration, peak and reversal arise from many independent decisions
  (momentum buyers, then value buyers stepping back and profit-taking, then short sellers and stopped-out
  late buyers), with no one planning it.
- **Successful strategies erode themselves:** as more traders use an edge, prices adjust faster and it
  shrinks or disappears.
- **Tipping points:** price responses aren't proportional to news. What matters is how anticipated the
  news was and how participants were already positioned, so markets can rally on bad news.
- **Conditions change** between trending, sideways, high- and low-volatility periods. A strategy that
  struggles in one may not be broken.
- **Feedback loops:** positive feedback extends trends; distance from perceived fair value eventually
  brings in the opposing force.
- **Edges have a life cycle.** The chapter claims edges built on behaviour (fear and greed, overshoot and
  reversion, institutional accumulation before breakouts, under-reaction to earnings surprises) last
  longer than structural or arbitrage edges, citing research across a century and many countries.
- **Build simple, robust, "anti-fragile" systems** around recurring human behaviour, ideally with
  asymmetric trades: uncapped upside, tightly managed downside.
- **The checklist:** review the conditions a strategy depends on, watch for an edge becoming crowded,
  compare results with market conditions, build on recurring behaviour, keep it simple, review on a
  schedule even when it's working, and keep researching.

### Already in the suite

| The book | Here |
|---|---|
| Name what the edge depends on | "Where does the edge live?" before mechanics; each module's CLAUDE.md states its posture |
| Results read against conditions | Regime records alongside results (curve's daily VIX/VIX3M classification, MEIC's GEX and VIX tags, the overview's phase gates); B8 makes the environment one definition |
| Keep researching | The hypothesis list, the research queue and the declared tests |
| Simplicity | Studies test the declared rule as written; added filters must pass their own declared test |

### Where it is weaker than our process

- **Its durable behavioural edges are asserted, and several are ones we have tested.** Post-earnings
  drift (under-reaction to surprises) did not survive our costs (edge hypotheses item 1). Most chart
  setups failed against random entries (setups rounds 3-7). "Institutional accumulation precedes
  breakouts" is stated as fact with nothing behind it. A cited century of evidence is mostly in stock
  returns before costs; it does not carry over to a specific options structure.
- **"Anti-fragile" asymmetric trades are the opposite of most of our books.** Premium selling has capped
  upside and a large left tail: it is fragile by construction, paid for carrying that risk. The chapter's
  advice doesn't make it wrong, but it should be named as what we are doing, sized for the tail (B1,
  B13), not dressed as robust.
- **"Has the market changed or has the strategy failed?"** is asked but not answered. Decay and noise
  look the same over short windows; the chapter gives no way to tell them apart. That is B1's job, with
  thresholds set in advance.
- **Crowding is unmeasurable as described.** "Watch for signs an edge is becoming widely used" has no
  observable. The only practical signal is the edge's own results drifting down over time.
- **The trend illustration is a story,** told after the fact for a pattern seen on a chart: the
  explanation-after-the-fact that Chapter 8 warns about.

### Action items

**B1** covers "decay versus noise" for live arms. For research, the chapter's "review even when it is
working" becomes:

**B14. A yearly re-test of every declared result.** *(built 2026-10-24: `cherrypick retests --year Y` plans and, with `--apply`, queues the rows. Recipes exist for items 1 (`pead_history.py`) and 5 (`ema_trend_signal_history.py`); item 6 needs the two-step bwb run, and items 4, 8, 9, 10 and 12 run on licensed data through scripts that take no date window, so each needs one written before its first re-test.)*
- **What:** once a year, re-run each declared test unchanged on the newest year of data (the holdout as
  it is opened, then each later year), and record the result beside the original, passed or failed.
- **Why:** it measures decay instead of assuming it. A passing edge that fades shows up in its own
  numbers; a failed idea that starts to pass becomes a candidate for a fresh declaration, never a
  silent revival.
- **Bound:** a re-test can only confirm, weaken or prompt a new declaration. It never changes a past
  verdict, and one strong year is not a pass.
- **Mechanics:** a row per test in the research queue (`study`/`retest-YYYY`), using B7's register for
  the list and B13's report for each result. Results stay local under the licence.

---

## Chapter 11: Decision science

### What it argues

- **The job is good decisions under uncertainty, not prediction.** Judge a decision by its process,
  not its outcome: a strategy winning half its trades can win 14 of 20 or 7 of 20 by chance.
- **Decision trees:** decide before entry what you will do if the trade moves for you, against you, or
  reaches a key level, then follow the branch.
- **Expected utility, not just expected value:** two traders with the same edge, one risking 1% a trade
  and one 20%. Same expected return, but the second can be ruined before the edge pays. Maximise the
  chance you are still trading hundreds of trades from now.
- **Satisficing** (Herbert Simon): define the standard in advance, take what meets it, and don't wait
  for the perfect trade.
- **Opportunity cost:** every position uses capital and attention that could go elsewhere; take the best
  available opportunities rather than every acceptable one.
- **Decision logging:** record the reasons, the information, the risks seen and your confidence before
  entry, then compare with the outcome to learn whether you're over- or under-confident.
- **Pre-mortem** (Gary Klein): before an important trade, imagine it failed and ask why.
- **Reversible and irreversible decisions:** spend thought on the ones with lasting consequences
  (changing rules, raising risk, abandoning a process), not routine ones.
- **Decision fatigue:** make as many decisions as possible before the open, and keep the session to
  following the plan.

### Already in the suite

| The book | Here |
|---|---|
| Process over outcome | Every module trades its declared rules; arms are judged on their samples, never on a session |
| Decisions made in advance | Entries, exits, sizes and caps live in config and code; nothing is decided during the session |
| Irreversible decisions get more care | Measurement-affecting changes batch to declared boundaries; live gates need a per-day arm and a typed YES; desk orders need a PIN and a ticket |
| Size to survive | Live arms carry worst-case caps (bwb's margin cap, flies' buying-power cap, MEIC's `live` block) |
| No ranking without edge | The income lists sit in symbol order, never by yield, because a yield order would read as an edge ranking |

### Where it is weaker than our process

- **Its 50% example checks out, and the chance is lopsided.** At a true 50%, winning 14 or more of 20
  happens about 6% of the time and 7 or fewer about 13%. The point stands.
- **Expected utility stops at an example.** It never says how to choose the risk fraction. The answer
  depends on the edge, its spread and the account's tolerance, which is a drawdown question (B1's
  bootstrap) or a Kelly-style one (the edge divided by the variance, usually taken at a fraction because
  the edge is uncertain). A fixed 1% is a reasonable default, not a derivation.
- **"Take the best opportunities" assumes you can rank them.** Ranking needs a tested edge per candidate.
  Without one, a ranking by risk-to-reward or credit is a ranking by nothing, which is why the suite's
  lists are in symbol order.
- **A decision log only teaches if it is scored.** "Compare your thinking with the outcome" needs a
  stated confidence as a number, scored over many trades (a calibration check), or it becomes
  storytelling about winners.

### Action items

**B6** is extended: a desk ticket carries a **short pre-mortem** (the most likely way it loses) and a
**stated confidence** as a probability, beside its thesis and invalidation condition. Over enough
tickets, confidence is scored against outcomes, which turns the chapter's decision log into a
calibration check.

**B13** is extended: beside a passing result's drawdown bootstrap, report the **largest size** at which
the 99th-percentile drawdown stays inside a declared share of the account. That number goes into the
live arm's config cap when it is armed, so "risk small enough to survive" is a computed figure, not a
habit.

---

## Chapter 12: Cognitive biases

### What it argues

- The brain evolved for survival, not probability; naming each bias lets you build a specific defence.
- **Anchoring:** the entry price ($50 bought, now $45) is history. The only question is whether you'd buy
  it today under your system.
- **Loss aversion** (Kahneman and Tversky): losses hurt more than equal gains please, so traders hold
  losers and cut winners. Letting a $100 risk grow to $200 against a $200 target turns 2:1 into 1:1.
- **Recency bias:** the last 5 of 200 trades (2.5% of the evidence) feel far more important than they are.
- **Availability bias:** vivid events (crashes, manias) feel more likely than they are.
- **Overconfidence after streaks:** lifting risk from 1% to 3% after wins makes the next losses three
  times larger; the strategy hasn't changed.
- **Hindsight bias:** judge a decision on what was known then.
- **Sunk cost:** money already lost is gone whether you hold, sell or add.
- **Framing:** "wins 70%" and "loses 30%" are the same strategy.
- **Cognitive overload:** more indicators and opinions often make decisions worse; keep the inputs few.
- **The checklist** turns each bias into a rule: decide on current evidence, keep to the plan through
  fear and excitement, judge over large samples, don't raise risk after streaks, ignore sunk costs, look
  past presentation, keep it simple.

### Already in the suite

Almost every defence in the chapter is the suite's design rather than a habit to practise:

| Bias | Why it can't act here |
|---|---|
| Anchoring, loss aversion, sunk cost | Exits are rules in code (stops, targets, time exits, settlement); no loop reads its entry price to decide whether to "get back to even" |
| Recency, overconfidence after streaks | Sizing comes from config and changes only at a declared boundary; no loop resizes on recent results |
| Availability | Regime and risk rules come from declared tests over full histories, not from memorable episodes |
| Hindsight | Declarations are written before data is read, and replays enter on information available at the time |
| Framing | One money layout across every surface (net, signed, whole-position), so the same fact reads the same way |
| Overload | Each module decides from a fixed, small input set (`engine.py`, `management.py`, `fly.py` as pure functions over a snapshot) |

### Where it is weaker than our process

- **"Cut losses fast" is not universally right.** For a defined-risk options structure, stops tested
  poorly here: MEIC's 0.95× stop ate about three-quarters of P&L, and stops on strangles failed in both
  halves. Loss aversion is a bias, but a tight stop isn't automatically its cure; the exit has to be
  tested like anything else.
- **Kahneman and Tversky's finding is about choices, not markets.** The chapter turns it into trading
  advice without evidence that the advice pays after costs.
- **"Crashes and manias are not normal"** cuts both ways. The long-run average the chapter points to
  includes the crashes; treating them as abnormal understates tail risk, which matters most for
  short-premium arms whose losses arrive exactly then (B1's tail scenario, B13's drawdown sizing).
- **Where the biases really act in this suite is the people, not the loops:** desk orders, re-arming a
  live arm after a block, raising a live cap, or changing a rule mid-test. Those are the human override
  points B6 and B10 already record.

### Action items

No new item. The chapter is a catalogue of biases whose defences the suite enforces in code. Where a
person can still act on one, B6 (thesis, invalidation, pre-mortem, stated confidence) and B10 (every
override flagged) already cover it. **B1** is reinforced by the overconfidence example: a live arm's
size is fixed in its config and only changes at a declared boundary, never after a run of wins.

---

## Chapter 13: Systems thinking

### What it argues

- **Results come from the whole process:** market selection, trade selection, sizing, execution, risk
  management, record keeping and review. A weak step undoes strength elsewhere. Following entry,
  sizing and exit rules 90% of the time each means the full process runs as designed only about 73% of
  the time.
- **The whole behaves differently from the parts;** ordinary parts that support each other beat one
  outstanding part.
- **Improve the bottleneck first.** A factory runs at its slowest machine's rate. Often the trader is the
  bottleneck, through hesitation and inconsistency; better entries won't fix that.
- **Every improvement has side effects.** Tightening stops at a 50% win rate with 2R winners and 1R
  losers (+0.5R) halves the loss to 0.5R but drops winners from 50 to 30: 0.3 × 2 − 0.7 × 0.5 = +0.25R.
  More analysis per trade can cut opportunities from 20 to 10 a week.
- **Simplicity and repeatable processes:** fewer rules execute more consistently, and a stable routine
  makes a single change measurable.
- **The checklist:** see the whole system, fix the weakest part first, judge every change by its effect on
  the whole, mind trade-offs, keep it simple, change one thing at a time.

### Already in the suite

| The book | Here |
|---|---|
| The whole process, automated | Each module runs selection, sizing, entry, management and settlement as one loop from code and config, so the "90% compliance" chain is 100% by construction |
| Judge a change by the whole | Arms are compared on net results over the full process, never on one component's figure |
| Side effects of a tighter stop | Measured, not argued: MEIC's 0.95× stop and the strangle stops both lost overall even though each loss was smaller |
| One change at a time | Control and treatment arms on the same sessions |

### Where it is weaker than our process

- **Its arithmetic checks out** (0.9³ ≈ 0.73; +0.5R falling to +0.25R), but the stop example assumes the
  winners stay at 2R. A tighter stop usually changes which trades win, and their size too.
- **"90% compliance" multiplies steps as if each deviation were equally costly and independent.** A
  missed entry, an oversized trade and a skipped exit cost very different amounts.
- **"Find the bottleneck" has no measurement.** The chapter's score out of 100 per component is
  invented. For an automated suite, the bottleneck shows up as the gap between what an arm models and
  what it books live, and that gap can be split by cause.

### Action items

No new item. **B10** is extended: beside the execution record, a **gap breakdown** per live arm splits
paper against live P&L into its causes (entries missed, fill slippage against the model, costs above
the model, human overrides) and names the largest. That is the chapter's bottleneck, measured rather
than scored. Flies' live history is the precedent: its loss traced to stranded verticals, not costs,
which a breakdown like this shows directly.

---

## Chapter 14: Measurement

### What it argues

- **Choosing what to measure is its own discipline.** A good measurement is tied to a decision, moves
  when the behaviour moves, and is collected the same way every time.
- **Leading and lagging:** profit lags; rule-following leads. Going from 70 to 90 plan-compliant trades
  in 100 shows up at once, while profit may take months.
- **Precision is not accuracy:** a correct 60% win rate that leaves out every rule-breaking trade, or a
  precise backtest without costs, measures the wrong thing.
- **Measurement error:** relabelling the same trades ("breakout", then "momentum entry", then
  "early/late breakout"), or changing how risk or slippage is recorded, breaks every comparison.
- **Goodhart's law:** a measure that becomes a target stops being a good measure. Chasing an 80% win
  rate leads to quick profits and held losers, which can turn a profitable system unprofitable.
- **Vanity metrics** (trades placed, hours of research, charts reviewed) feel productive and decide
  nothing.
- **Actionable metrics:** ask "if this changed tomorrow, would I know what to do?" A falling compliance
  rate points at execution; a falling win rate alone points nowhere.
- **A hierarchy:** review process, then risk management, then execution, then results.

### Already in the suite

| The book | Here |
|---|---|
| Accuracy over precision | Every study and surface is net of costs; a figure without its costs is labelled gross |
| Consistent definitions | One money layout across every surface; `core.ledgers` is the single home for each schema's net and risk rules; changing what a number means is measurement-affecting and waits for a declared boundary |
| Relabelling breaks comparisons | The vocabulary rule: existing spellings are never withdrawn (`books`/`profiles` resolve for good), and a renamed arm keeps its history |
| Every trade counted | Planners report every dropped trade by reason; cancelled and voided rows are kept out of both tables, never silently mixed in |
| Execution measured separately from results | B10's execution record and gap breakdown |

### Where it is weaker than our process

- **Its leading indicator doesn't exist in an automated loop.** Rule compliance is 100% by construction.
  The suite's leading indicators are different: fill quality against the model, missed entries, and the
  data health checks (recording coverage, stream-cache freshness), which fail before P&L does.
- **"Process first, then results" can excuse a bad strategy.** A perfectly executed system with no edge
  still loses. Process metrics say whether the test is clean, not whether the idea works.
- **Goodhart applies to our own console.** The win-rate rings (#526) are the kind of figure the chapter
  warns about: a premium-selling arm can show a high win rate while losing money. They are a
  description, never a target, and shown alone they mislead.

### Action items

No new item. **B2** is extended: a win rate is never shown without the arm's expectancy (net per
trade, with its interval) beside it, so the console cannot present the one figure the chapter singles
out as a Goodhart trap on its own.

---

## Chapter 15 and the conclusion: continuous improvement

### What it argues

- **Many small, tested improvements beat one breakthrough** (kaizen); small changes are easier to
  evaluate one at a time.
- **Pareto:** a few improvements produce most of the gain. Of eight things a trader worked on in a
  quarter, two made a noticeable difference; measure your improvement efforts and spend time where the
  evidence says.
- **Improvement never finishes,** and gains shrink as the easy ones are taken.
- **The conclusion** restates the book as one cycle: observe, hypothesise, test fairly, evaluate with
  statistics, update with evidence, check for bias, execute within a system, measure what matters,
  adjust when the evidence justifies it, and repeat. Its closing checklist names one practice per
  chapter.

### Already in the suite

The cycle the conclusion describes is the suite's working method: hypotheses declared before data
([edge-hypotheses-plan.md](edge-hypotheses-plan.md)), tests against matched baselines, results kept
whether they pass or fail, changes landed at declared boundaries, and a research queue that keeps the
work going.

### Where it is weaker than our process

- **Small improvements are the hardest to prove.** A change worth a few hundredths of an R needs
  thousands of trades to tell apart from noise (Chapter 6's check: about 7,200 a version unpaired), so
  "keep what helps" will keep noise. Paired tests (B12) make small changes measurable; without them,
  kaizen is drift.
- **Many small changes are many tests.** Ten tweaks a year, each kept if it looks better, is the
  multiple-testing problem the book never corrects for. Here every rule change is a declared test, and
  measurement-affecting changes are batched to boundaries so the evidence clock isn't reset ten times.
- **"Two of eight helped" is only knowable with a measurement per change,** which is what B7's change
  log (each change re-read after 100 trades) and B10's gap breakdown provide.

### Action items

No new item. The chapter is the book's summary, and every practice it lists is covered by the suite or
by an item above.

---

## Closing review: what the book adds, and an order to build it

**The book's value to the suite** is mostly confirmation: the scientific cycle it teaches is the one the
suite already runs, often more strictly (matched baselines, both-halves passes, Holm, holdouts, costs
as the decider). Its arithmetic is usually right; its statistics stop early. The streak tables
understate streaks, sample thresholds contradict each other across chapters, small improvements are
called significant when they aren't, and multiple testing is never corrected. Several of its
"durable" behavioural edges are ones the suite has already tested and found wanting after costs.

**What it adds** is operational, mostly around live trading and reading results, and that is where the
fourteen items sit. None changes what any module measures.

**A suggested order**, cheapest and highest-value first:

1. **B2** (intervals, median, worst trade, skew both ways, no win rate without expectancy). A read
   surface; it stops a short sample from reading as proof, and fixes the Goodhart risk in the
   current win-rate rings.
2. **B1** (kill criteria for live arms: bootstrap thresholds frozen at arming, a graduated
   reduced/blocked response against a live-cost baseline). The only item that protects capital.
3. **B12, B13, B9** as a declaration template: minimum detectable effect, a robustness report with a
   declared cost stress and a size cap, and a name split. Docs only, used by every future test.
4. **B3 and B4** (results in R; context columns at entry). Recording only, with a dry-run backfill;
   they feed B5 and B8.
5. **B10** (execution record and paper-to-live gap breakdown). Most of the data is already recorded.
6. **B7 and B14** (the hypothesis register, and a yearly unchanged re-test of every result).
7. **B8, B5, B11, B6** (the environment classifier, the slice report, the survivorship audit, and the
   desk-ticket fields).

Each becomes its own PR once agreed; this file records the decision and the PR against each item.
