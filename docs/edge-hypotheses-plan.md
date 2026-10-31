# Edge hypotheses: what the 2026-10 video review left worth testing

*Drafted 2026-10-17. No module change is built from it; five of the eight tests have run (see
Status). It follows a review of twenty Theta Profits interviews
(YouTube, 2024-09 to 2026-06) and asks one question of each idea before anything else: **where does
the edge live?** Every item names its hypothesised edge, the claim that would falsify it, the data we
already hold, the smallest test that answers it, and the result that would justify building
anything.*

## The rule this plan follows

A strategy makes money only if something in it survives fair pricing at mid, costs and slippage. That
leaves four places an edge can live:

1. **A risk premium**: variance, skew, term structure. Implied volatility is priced above realised
   on average, and the seller is paid for carrying that risk.
2. **A selection or timing signal**: which names, which days, which minutes. A signal earns only if
   it separates outcomes beyond what the option prices already say.
3. **An execution advantage**: fills, fees, settlement.
4. **A structural or flow effect**: dealer hedging, post-earnings drift, index rebalancing.

Almost everything else in the material reviewed was one of three things that are not edges:

- **Payoff shaping.** Stops, rolls, scaling out, adding wings, "transforming" a position into a
  risk-free one. These move risk between the middle and the tails of the distribution. They change
  the win rate, not the expectancy, unless they exploit something in the list above.
- **Accounting.** Spending profit already booked as a floor. Flies' honesty rules call this an
  `outright` structure: it spends a floor rather than manufacturing one.
- **A sample that paid.** Three months, or one year, in a rising market.

The suite's own negative results all trace to an edge nobody could name. The bwb control
(−$9.1k over two modelled years, no arm profitable from 2011), the navigator spread (−4.3% return on
risk) and the expected-move fly (fair at mid, ~18% costs) all fit that pattern. So every test below is
built to test the **edge claim on its own**, before any management rule is layered on. Management is
treated as variance and tail shaping unless a test shows otherwise.

## Declaration template (from item 18 on, 2026-10-24)

Every declaration from here on carries these lines, written before any look. Declarations already
written are not re-judged. The arithmetic is `cherrypick.core.robustness`
(`docs/trading-book-review-plan.md` B12, B13, B9).

- **A register row** in [hypotheses.json](hypotheses.json) (B7), added with the declaration: the four
  parts, the mechanism, the rival explanations each with the check that rules it out, and a pointer
  here. Run `cherrypick hypotheses --similar '{"setup": ..., "structure": ...}'` first; a rejected
  look-alike is named in the declaration, so a retest is said out loud. `cherrypick hypotheses`
  fails on a malformed row. A `retest` recipe (the research queue's kind and args) lets B14 re-run it.
  When the result lands, the row's status changes, `results` names the files as `{home}/...` paths
  and `outcome` says the decision in words (2026-10-29, for the Backtest page's studies board).
- **A result file the board can read:** a top-level `verdict` (one word, or one per arm, or per arm
  as `{"verdict": ...}`), its `window`, and the `run_id` when the study ran through the runner.
- **Declared limits** (B11): how a position open at a name's last bar is settled (the last close; no
  deal or recovery price), how many trades empty chains removed, and whether any input reflects
  only today's state (the tradable list is hindsight; report it apart).
- **Pass test**, as before: the statistic, the bar, and both time-halves.
- **Minimum detectable effect** (B12): the smallest edge the planned sample detects at the pass bar,
  from the spread of comparable past results (`planned_mde`). A no-verdict result then reads "no
  edge larger than X". **Paired by default**: a rule change is compared on the same trades, and an
  unpaired comparison says why.
- **Name split** (B9), where the sample is large enough: the universe is split by `name_half` (a
  fixed hash of the symbol), and a pass must hold in both name-halves as well as both time-halves.
  The declaration says whether the split applies; a small universe says why it does not.
- **Robustness report** (B13), reported beside a pass and never used to re-choose the setting:
  - the declared parameter's neighbours (for example ±10-25%), flagged a plateau or a lone peak
    (`neighbourhood`);
  - the result at 1.5× and 2× costs, with the declared stress the pass must survive, 1.5× by default
    (`cost_stress`);
  - the bootstrap of its own trades' drawdown and losing streak (`drawdown_range`), the frozen B1
    thresholds if it goes live;
  - the largest size whose 99th-percentile drawdown stays inside a declared share of the account
    (`max_size`).

## Data we already hold

| Source | Coverage | Fit |
|---|---|---|
| SPX 0DTE quote history | ~15 s cadence from 2026-10-13 | Prices intraday structures exactly; ~5 sessions now, 10 by ~10-26. |
| GEX recorder and spot trail | SPX from 2026-07-29; per-strike profile from 10-05 | Intraday spot path, expected move, walls, flip. |
| ThetaData (`packages/backtest`) | Options Standard from 2026-10-17; 1-minute history before 2026 | Years of intraday option prices. **Licensed: results stay under `~/.cherrypick` and never enter this repo, a PR or an artifact.** |
| Dolt clones (`packages/technicals`) | Daily bars 2011→; option chains (sparse pre-2024); earnings events 2020→ | Swing and earnings studies; the navigator study's event set (3,587 priced events). |
| Module ledgers | MEIC `open` arm (no stop, full path); flies, bwb, pmcc, calendars paper books | Paired comparisons against what the modules actually did. |
| Existing replays | `scripts/bwb_history_backtest.py`, `scripts/pmcc_shield_replay.py`, `scripts/earnings_navigator_history.py`, `cherrypick.meic.stop_policies` | Each new rule here is a variant of one of these, not a new harness. |

## Status (2026-10-17)

Five of the eight have been tested against their declared pass tests
([history-studies-2026-10-c.md](history-studies-2026-10-c.md)):
- 1, post-earnings drift: closed;
- 4 and 8, the box edge and the intraday direction reads: not being built (they ran on licensed
  data, so the results are kept locally under `~/.cherrypick`, per the licence). Their yearly
  re-test (B14) runs `scripts/box_touch_history.py` on one calendar year, and on one year the box
  test's "positive in two-thirds of the years holding 20 or more touches" reduces to that single
  year, so the re-test is not identical to the original test;
- 5, the EMA signal: closed;
- 6, the BWB down-day filter: closed.

That leaves 2 (MEIC afternoon entries), 3 (the mid-morning iron fly) and 7 (MEIC stop mechanics). 3
and 7 depend on an intraday fill model that is no longer being built for that routine's sake, so each now
has to justify that build on its own.

**Added 2026-10-18:**
- 9–11, Option Alpha's three automated bots (a separate source from the Theta Profits series).
- 12, GEX levels. They were in "not worth revisiting" until the backtest store made years of rebuilt
  dealer profiles available.
- 13, Option Alpha's credit-structure screener.

**Added 2026-10-19:**
- 14, selecting credit spreads for the income lists (which names, which side), declared before
  any fetch; its first step is a probe of how far back ThetaData's end-of-day chains reach.

### Provisional now, re-tested after the unlock (decided 2026-10-18)

9–12 needn't wait for validation to unlock vendor history (`validate.UNLOCK_SESSIONS`, ~2026-10-26).
Each gets a provisional look now, and a re-test once validation unlocks.

- **Now: a provisional look, on the design window only** (2022-05-16 to 2025-06-30). It's the
  runner's `--provisional`: no trial recorded, marked provisional in every report and reader, never
  shown to the advisor. 9–12 aren't flies or MEIC rules, so their looks are scratch studies that read
  the store directly, as the 10-17 IC-start study did. They follow the same rules by hand:
  - clip to the design window;
  - write under `~/.cherrypick/data/backtest/results/`, labelled provisional;
  - record no trials.

  **None of them reads the holdout.** A look that touched it would spoil the re-test below.
- **After the unlock: the re-test, on the most recent window.**
  - Re-run validation (`python -m cherrypick.backtest validate --cadence 60`).
  - Re-run each item's spec on the design window as a trial. Every variant tried in the provisional
    look counts toward the trial count, so DSR deflates for the search.
  - Then spend the holdout (2025-07-01 to 2026-10-12) **once** per spec that still passes. Only that
    result is evidence; a provisional look can only send an item to its re-test or close it.
- **Declare each pass test before the look**, and use the same one for the re-test. A threshold
  moved between the look and the re-test is a new spec.
- **What can't run yet:** 11 needs the next-day expiry fetched first. 13 reads Dolt rather than the
  vendor store, so the lock doesn't apply. It still keeps its newest year aside as its own holdout,
  with the same once-only rule.
- **One deliberate exception (2026-10-18).** The MEIC stop and entry-window arms `pm-hold`,
  `pm-095`, `pm-200`, `day-095` and `day-200` (items 2 and 7) were run provisionally over the
  holdout dates by the operator's choice, as well as over the design window. For those arms the
  holdout is **already seen**: their after-unlock re-test can't count it as out of sample. Any
  evidence for them needs fresh sessions, those after 2026-10-12. Every other spec keeps its holdout
  clean.
- **Added 2026-10-19: `pm-200-1400`**, with the same consequence. It's `pm-200` entering once a
  day, 14:00–14:30, and it was suggested by `pm-200`'s own ledgers. Its provisional replay covers
  the full vendor history by choice, holdout dates included, so its evidence also has to come from
  sessions after 2026-10-12. Its first four recorded sessions (10-13 to 10-16) are such sessions:
  provisional and far too few to judge.
- **Added 2026-10-19: `pm-200-1400-w5`**, with the same consequence. It's `pm-200-1400` with SPX
  wings one strike (5 points) wide instead of 10, run over the same full history to answer whether
  live could trade the rule at half the buying power. It did worse than the 10-wide in the paired
  sessions and in nearly every quarter, and isn't adopted: live trades `pm-200-1400`'s rule at 10
  wide from 2026-10-19. The figures stay under `~/.cherrypick`.

### Declared pass tests for the provisional looks (2026-10-18, before any look ran)

These apply to the looks and, unchanged, to their re-tests. They're stricter than each item's
"build only if", and a result that misses them closes the item.

**Common to all:**
- **Halves:** H1 is 2022-05-16 to 2023-12-31; H2 is 2024-01-01 to 2025-06-30.
- **Intervals:** 95% intervals come from a day-level bootstrap (2,000 resamples, fixed seed).
- **Fills:** credit at mid; at mid − $0.10 (his order, assumed to fill: optimistic); and a quarter
  and a half of the way from mid to natural (q25, half). **q25 is the basis for every pass.**
- **Costs:** `core.fees`' open fee, plus $5 per ITM leg at settlement.
- **Entry minute:** the first fully quoted one-minute sample in the entry window.

**The tests:**
- **9, sandwich.** Shorts at the strikes nearest spot ± 5 and 5-wide wings, entered 13:45–14:00.
  Four filter settings: none; VIX < 24; mid credit ≥ $2.50 (reward to risk ≥ 1); both.
  - *Passes if* at q25 the setting's mean net is positive in at least three of 2022, 2023, 2024 and
    2025 H1, **and** its overall interval's lower bound is above zero.
  - A filter earns its place only if it raises the q25 mean over the unfiltered rule in both halves.
- **10, flatfly.** Entered 10:00–10:05. The prior-close-centred fly is paired with the spot-centred
  fly from the same minute. Wings 10 and 20, both declared.
  - *Passes if* at q25 the paired difference (prior close − spot) has its interval's lower bound
    above zero in both halves, **and** the prior-close fly's mean net is positive in at least three
    of the four years.
  - Days the prior-close centre isn't quoted are counted, not filled.
- **12, GEX reads.** Signal only, from the store's profile through `core.gex`, by gamma and by open
  interest:
  - *(a) Pin*, at 10:00, 12:00 and 14:00. Measure whether the close moves from spot toward the
    largest-exposure strike. *Passes if* the interval's lower bound on the hit rate is above 50% in
    both halves, for the strike above spot and for the strike below spot taken separately. That
    rules out drift.
    - *Found 2026-10-25, after the look:* as written, this test cannot pass. The strike above spot
      is always above it, so "the close moves toward it" means "the close is above spot". The
      strike below is always below, so moving toward it means "the close is below spot". The two
      hit rates sum to at most 100%, so both can't be above 50%, and its fail says nothing about
      pinning. The declaration stays as written. A pin test that could fire would be a new
      hypothesis with its own declaration, and none is declared.
  - *(b) Barrier*, at 10:00. Count touches of the largest call wall above spot and the largest put
    wall below it, against the touches expected at the same distance in straddles, taken from the
    same half's days. *Passes if* observed ÷ expected is ≤ 0.85 with the interval's upper bound
    below 1, for both the call and the put wall of one level type, in both halves.
  - *(c) Regime*, at 10:00. Compare the range to the close, in straddles, on days of negative
    against positive net GEX. *Passes if* the interval of the difference (negative − positive) is
    above zero in both halves.
- **13, screener.** Dolt end of day. Universe: 40 fixed liquid names. Expiry: the shortest listed at
  10–17 DTE; Dolt lists nothing shorter, so the video's 7-DTE version needs single-stock vendor
  chains. Shorts ≥ 2% OTM; spreads one or two listed strikes wide; iron condors paired at equal
  width. Earnings spans are excluded, and so is any expiry past the design end. Held to expiry; the
  40% take-profit isn't modelled.
  - *(a) Passes if* the screened set's q25 mean net has its interval above zero in both halves,
    **and** so does its excess over same-type, same-delta-bin unscreened trades. The bins are 0.05
    of delta, weighted by screened counts.
  - *(b) Passes if*, for one ~0.20-delta spread of each type per name and day, sell-off days (SPY
    down ≥ 3% over 5 sessions) have their q25 interval above zero in both halves.

### Declared pass test for item 7 (2026-10-19, before any look)

Item 7 runs on the recorder's own quote history (SPX 0DTE, 20 strikes each side, from 2026-10-13),
not the vendor store. Every session in it is after 2026-10-12, so for the `pm-200` arms its result
counts as evidence, not a provisional look. Its figures can be published.

- **Condors:** every one `pm-200` would enter on a recorded session (16 delta, 10 wide, every 30
  minutes 12:00–14:30), rebuilt from the recorded chain. `pm-200-1400`'s entries, the live rule, are
  reported as a subset. A session whose recording has a gap while a condor is open is left out and
  counted.
- **Baseline:** `pm-200`'s stop. A side closes when its cost reaches 2× the whole credit; the other
  side is held to settlement.
- **Variant A, a tighter surviving side.** Once one side stops, the other side's trigger drops by
  10% and, separately, by 25% (1.8× and 1.5× the whole credit). Those are the ends of Talon's stated
  range.
- **Variant B, a stop on the short leg only.** The short is bought back when its own ask reaches the
  side's trigger plus the long's entry price. The long is then sold at the first bid after the fill;
  separately, it's held to settlement. Both are declared.
- **Fills:** every rule is priced on the same recorded quotes. A stop fills at the natural (ask to
  buy, bid to sell) on the first sample at or past its trigger.
- **Costs:** `core.fees`' close fee for each leg closed, plus $5 per ITM leg at settlement.
- **Sample:** no verdict before 30 condors have had a first stop, across at least 10 sessions.
  Until then, only counts are reported.
- **Passes if** the paired difference in net per condor (variant − baseline) has a positive mean
  and its 95% interval's lower bound above zero. The interval comes from a day-level bootstrap
  (2,000 resamples, fixed seed). Drawdown (Talon's claim: similar profit, half the drawdown) is
  reported beside it, but it can't pass the test alone. A drawdown-only gain is a sizing argument
  and is made as one.

### Results of the provisional looks (2026-10-19)

Every look ran against the tests above, unchanged. 9, 10 and 12 used licensed vendor data, so as for
4 and 8 only the verdicts are recorded here; the figures stay under `~/.cherrypick`.

- **9, sandwich: closed.** No filter setting passes. The VIX filter raises the mean in both halves;
  the credit filter doesn't.
- **10, flatfly: closed.** It fails both parts. Centring on the prior close does worse than centring
  on spot, most of all after large gaps, as the daily check above predicted.
- **12, GEX levels: closed again**, this time over years rather than weeks.
  - *(a) Pin:* fails for both gamma and open interest at every time. The above/below split shows
    the apparent pull toward a strike above spot is market drift.
  - *(b) Barrier:* fails for both level types.
  - *(c) Regime:* fails.

  Reopen only with a new mechanism and a new declared test, not a new threshold.

  **Reviewed 2026-10-25.** The committed script (`scripts/gex_levels_history.py`) reproduced this
  verdict that morning. A review of it then found three faults, which are now fixed, and one test
  that could never fire. What stands and what doesn't:
  - *(a) Pin: invalidated, for both level types.* As declared it cannot pass on any data (see the
    note under the declared tests), so its fail says nothing about pinning. It isn't redefined; a pin
    test that could fire is a new hypothesis, and none is declared.
  - *(b) Barrier, open interest: stands.* None of the fixes changes it. The put-wall fault was in
    the gamma read only. Under the corrected snapshot rule (below), the open-interest walls are the
    same strikes on every design-window day, and the same days are measured.
  - *(b) Barrier, gamma: was invalid, re-run, still fails.* Its put wall was the strike below spot
    with the *least* put exposure, not the most: `core.gex` stores put exposure as positive, and
    its put wall is the largest. The read was re-run on the design halves only (never the holdout),
    with core's rule and the corrected snapshot. Both walls are touched less often than expected in
    both halves, but in neither half does either wall reach the declared bar (≤ 0.85 with the
    interval's upper bound below 1). It fails in both halves.
  - *(c) Regime: stands, not re-run.* It was read from the first profile at or *after* 10:00 (up to
    15 minutes later), so its net-GEX sign could use prices after the decision. The script now
    reads the latest profile at or before the decision minute, at most 15 minutes stale. The fail
    is kept; the next re-test reads it with the fixed rule.
  - *The holdout guard* let a window with `--start` and no `--end` read the holdout. An open end now
    counts as reaching the store's last session. The 2026-10-25 run read the declared halves with
    no window, so it wasn't affected.

  So item 12 stays closed on (b) and (c). (a) was never a test of pinning.
- **13, screener: closed** (figures under item 13).
- **11** is still blocked on the next-day expiry fetch.

## The hypotheses, in priority order

Priority weighs two things: how plausible the edge is, and how cheaply our existing data can answer
it.

### 1. Post-earnings drift through a put credit spread

*Source: video 20 (2025-07-20).*

- **Rule as stated:** after the report, on a liquid name in an uptrend that did not drop (a 10–15%
  drop means pass), sell a ~20-delta put spread below the pre-report price, 30–45 DTE, $0.80–1.20
  on 5 wide. Take profit at 50%, stop at 2× credit. Average hold 8 days.
- **Where the edge would live:** a **flow effect**. Post-earnings announcement drift (PEAD) is one of
  the best-documented anomalies in the literature. A name that gaps up tends to keep drifting the
  same way for weeks. The seller also collects a post-report IV that is still elevated.
- **What would falsify it:** across past events, gap-up names show no better 8–30 day returns than
  the post-report IV prices in. In other words, the put spread's win rate at entry is no better than
  its delta implies.
- **Data:** Dolt earnings events and chains 2020→, the same set the navigator study priced. Daily
  bars for the drift itself.
- **Smallest test:** in two steps.
  1. *Signal only:* for each event, the reaction-day gap and the return from the reaction close to
     +8, +14 and +30 sessions. Bucket by gap sign and size, and by whether the stock held past gaps
     (his 8-report screen). Does the drift exist in our sample, and does the screen sharpen it?
  2. *Structure:* only if step 1 shows drift. Price his exact spread at the reaction close from the
     chain, with real costs and his exits.
- **Build only if:** step 1 shows drift beyond noise in out-of-sample years (not one regime), **and**
  step 2 returns positive after costs over both halves of the sample.
- **Note:** the earnings module enters only before the report today. Its PEAD logic is an exit gate
  (`management.py`: a loser closes the first morning, a winner may carry). A post-report entry would
  be a new strategy, so it's measurement-affecting and waits for a boundary.

### 2. Afternoon entries for MEIC

*Sources: videos 1, 6, 12; our own 10-14 entry-time study.*

- **Rule as stated:** four sources independently prefer entries from about 12:00 ET. Talon uses
  12:00–14:30, the host's best hours are the last three, and MEtF uses 12:30–14:45. Our study found
  12:30 the best single slot.
- **Where the edge would live:** **timing**. The edge is the 0DTE variance premium either way, but
  mornings carry the session's tail risk (open blowups on 09-03, 09-21, 10-05 in our sample) while
  afternoons carry less time for it.
- **What would falsify it:** with a much larger sample, afternoon entries earn no more per unit of
  risk than morning ones after stops.
- **Data:** our study used 36 sessions, with no afternoon crash among them. ThetaData 1-minute
  history gives years.
- **Smallest test:** re-run the entry-time grid (09:45 to 14:45, half-hourly) over ThetaData history.
  Use MEIC's own strike rule and its 0.95× stop priced on the 1-minute path, plus the no-stop case.
  Report net by slot, by year, and the worst session per slot.
- **Build only if:** the afternoon advantage holds in most years, not just on average, and survives
  the worst-session comparison. A window change is measurement-affecting, so it waits for a boundary.
- **Held-out test declared 2026-10-28 as item 24**, after the B5 slice report on MEIC paper found
  entry hour the one dimension that separates. Live already trades 14:00; item 24 asks which
  afternoon slot, with two morning slots reported beside it for this hypothesis's own read.
- **Closed 2026-10-29 by item 24's fail:** no afternoon slot beats 14:00, and mornings trail it.

### 3. The mid-morning ATM iron fly

*Sources: video 16 (10:00, opening-range filter), video 18 (10:30, 25% target, 14:30 time exit).*

- **Where the edge would live:** the **intraday variance premium**, harvested early and briefly.
  Video 16 adds a **selection filter**: trade only if price is still inside the first 30 minutes'
  range.
- **What would falsify it:** an ATM fly's decay in the first hours does not beat realised movement
  once fills and four legs of fees are charged. Or the opening-range filter does not separate winning
  days from losing ones.
- **Data:** the quote history prices both exactly, but only from 10-13. ThetaData 1-minute history
  for years.
- **Smallest test:** two fixed rule sets, no discretion.
  - *(a)* Enter at 10:00 if spot sits inside the 09:30–10:00 range. Short at the first strike above
    spot, wings at ± the expected move. Take profit at $0.50 / $1.00, stop when spot reaches the
    wings' break-evens.
  - *(b)* Enter at 10:30, 20-wide wings. Take profit at 25% of credit, else close at 14:30.

  For (a), report the filter's split: days it allowed against days it refused.
- **Build only if:** positive after fills at a realistic fraction of the spread and real fees, in
  most years. For (a), the filter has to beat the unfiltered version. Fills decide this one: the
  targets are a few percent of the credit, so a 25%-to-natural fill assumption alone could flip the
  sign. Run the fill sensitivity first.

### 4. An intraday direction signal: trend (MEtF) against reversion (the expected-move fade)

*Sources: video 12 (MEtF: 1-minute 20/40 EMA picks the side), video 2 (fade a touch of the expected
move).*

- **Where the edge would live:** a **direction signal**. The two videos claim opposite things.
  Momentum says the trend continues for the rest of the day; reversion says the move snaps back once
  it reaches the expected move.
- **What would falsify it:** neither signal predicts the SPX move from entry to the close better than
  a coin flip.
- **Data:** for the signal alone, only SPX intraday prices: the spot trail from 07-29, plus ThetaData
  history if the backtest store carries the index. The spread pricing comes later and only matters if
  a signal exists.
- **Smallest test:** at each MEtF entry time (12:30 to 14:45), the EMA state against the sign of the
  move to the close. At each first touch of the expected move before 13:00, the touch direction
  against the move over the next 30, 60 and 120 minutes. Hit rates, with day-clustered confidence
  intervals.
- **Build only if:** a hit rate clears the break-even of the structure it would drive. That's about
  67% for the fade (risk 1× to make 0.5×); for MEtF's 1:1 stop, whatever its credit and fees imply.
  Our earnings-direction reads came out at 48–49%; this test should be held to the same standard.

### 5. The dynamic PMCC short delta

*Source: video 15, Tom King, the author of the Income Shield pmcc's `shield` arms already run.*

- **Rule as stated:** the weekly short's delta follows the daily 8/21 EMA. 20–30 delta in an
  uptrend, at the money when flat, 60–70 delta in the money in a downtrend.
- **Where the edge would live:** a **trend signal timing net delta**. Selling in the money in a
  downtrend is mostly being less long when the trend turns. The "extrinsic income" framing is
  accounting.
- **What would falsify it:** the 8/21 EMA state does not predict the next week's return on the pmcc
  symbols. If it doesn't, the dynamic short is just a noisier fixed short.
- **Data:** Dolt daily bars 2011→; `scripts/pmcc_shield_replay.py`, which already reproduces the
  shield against Cboe WPUT.
- **Smallest test:** first the signal: EMA state against the next-week return, by symbol, 2011→. Then,
  only if it predicts, his delta schedule as a variant in the shield replay against the fixed 0.70
  short.
- **Build only if:** the signal predicts out of sample on most symbols, and the variant beats the
  fixed short in most windows. That's the bar `shield_hold` had to clear: 11 of 15 windows. Remember
  our study's warning that the short's edge flips sign within ±10% IV. Run the variant across the
  same IV band.

### 6. A down-day entry filter for the put BWB

*Source: video 11 (21 DTE put BWB, 25×50, take profit at 2% of width, enters on down days, avoids
all-time highs).*

- **Where the edge would live:** the **put skew premium**, bought cheaper after a down day, when IV is
  richer and strikes sit further away. His 21 DTE structure and take-profit are payoff shaping on top.
- **What would falsify it:** in our history, BWBs entered after down days do no better than the rest.
  The base structure already has no edge from 2011, so the filter has to create one, not polish one.
- **Data:** `scripts/bwb_history_backtest.py` (VIX9D-scaled smile, daily bars, 2011→).
- **Smallest test:** split the existing backtest's entries by the prior day's SPX return (and by
  distance from the all-time high). Then his 21 DTE take-profit shape as one variant, with the same
  split.
- **Build only if:** the down-day subset is positive after costs in most years. Otherwise the
  structure stays where the 2011→ study left it.

### 7. MEIC stop mechanics: a tighter stop on the surviving side; stops on the short leg only

*Sources: video 1 (Talon: cut the surviving side's stop 10–25% once one side fires), videos 1 and 6
(stop on the short; close the long after).*

- **Where the edge would live:** **nowhere new**. Both reshape the payoff and change how stops fill.
  The underlying edge is the variance premium, and our 10-14 study found the 0.95× stop costs most of
  it at every slot. The only possible gain is in fills (an execution edge): a short-only stop rests at
  the exchange, and the long sometimes keeps gaining for a while after the stop fires.
- **What would falsify it:** the tighter surviving-side stop just trades a slightly higher
  double-stop rate for smaller losses, with no change in net. Short-only stops fill no better than
  our recorded spread stops (median 1.064× trigger, p90 1.23×).
- **Data:** `stop_policies` cannot price either: it keeps each side's running maximum, not the path
  after the first fire. The quote history can (from 10-13), and ThetaData 1-minute history can at
  coarser grain.
- **Smallest test:** re-score the `open` arm's recorded paths under both rules on quote-history
  sessions. Report the drawdown claim (Talon: similar profit, half the drawdown) separately from net.
- **Build only if:** net improves, not just drawdown. A drawdown-only improvement is a sizing choice,
  and should be argued as one.

### 8. A trader's daily SPX 0DTE routine

*Source: video 14. Reconstructed from five daily sessions (10-12 to 10-16) in
[daily-0dte-routine.md](daily-0dte-routine.md). The finding is that the condor is a
resting order far above the market, which fills only when price runs toward a box edge. So the edge
claim is reversion at the expected-move box edge, not the open-to-close variance premium assumed
below. The test plan there supersedes this section.*

- **Where the edge would live:** probably the **0DTE variance premium**, through a no-stop 10-delta
  condor. Our MEIC data agrees that loosening the stop helps, up to the tail. Plus **gap-adjusted
  strike placement**: an expected-move box from open + EM down to prior close − EM. His defense flies
  are payoff shaping. His flies at gamma levels depend on vendor levels, and our GEX tests have been
  negative.
- **What to log per session:**
  - the broker's expected move, the open and the prior close;
  - iron condor time, strikes, width, credit and delta;
  - defense strikes, price and time;
  - fly levels and debits, and what he calls the level;
  - every exit with time, price and reason;
  - the VIX, and any skip or size-down.
- **Then:** after 10–20 sessions, separate the rules from the judgment calls. Test the rule-like
  parts against our recorded quotes and GEX levels on the same days.

### 9. The 1:45 PM sandwich

*Sources: Option Alpha, "triple threat" (https://youtu.be/1BOZ0M3fSCs), a bot running live since
2026-04-01; and the video where it was chosen and set up (https://youtu.be/4ngxNC6AWQI, about
2026-03-31).*

- **Rule as stated:** at 13:45 ET, a 0DTE SPX iron condor with its shorts at spot ±5 and 5-wide
  wings, 10 wide overall. Held to settlement, with no target and no stop. Two filters:
  - VIX below 24.
  - Reward to risk of 100% or more. On a 5-wide side that means a credit of at least $2.50, so at
    most $250 at risk.

  Orders work from 13:45 to 14:00, giving up to $0.10 from mid. Run at a broker that charges no
  exercise fee, because it settles in the money more often than not.
- **How it was found:** he picked it from a pool of 671,000 profitable community backtests, filtered
  to 3 years, 0DTE, at least 300 trades and an average loss of $200 or less. Then he added the
  reward-to-risk filter, which cut the drawdown from $2,085 to $1,638 for $300 less profit (≈$20k
  over 3 years, ~45% wins). A rule chosen from a pool that size carries a trial count nobody can
  know, so its backtest counts for nothing here. The live record from 04-01 is its only
  out-of-sample evidence, and our own test is scored as a new trial with DSR.
- **Where the edge would live:** the **0DTE variance premium** over the last two and a quarter hours,
  in nearly the narrowest structure there is: an ATM iron fly with a 10-point flat top. It's flies'
  edge in a different shape.
- **His live figures:** 41% wins, average win $281, average loss $153. That's about +$26 a trade
  before fees, and four SPX legs filled below mid cost about that much.
- **What would falsify it:** net after a realistic fill, four legs of fees and settlement is zero or
  less.
- **Data:** the quote history from 10-13; ThetaData's 0DTE minute history for years.
- **Smallest test:** the one fixed rule, with and without each filter. Run the fill sensitivity first
  (mid, $0.10 off mid as he trades it, 25% and 50% of the way to natural), then report net by year
  and the worst session. Historical VIX for the filter comes from `fetch-vix`.
- **Build only if:** positive at 25% to natural in most years, and the filters earn their place
  against the unfiltered rule out of sample.
- **Harness:** the runner replays flies and MEIC only, and this structure is neither module's rule.
  Before building a third harness, check whether MEIC's replay can express it (one entry, fixed strike
  offsets instead of a delta band).

### 10. The flatfly: an iron fly pinned to the prior close

*Source: as 9.*

- **Rule as stated:** at 10:00 ET, a 0DTE SPX iron fly with its shorts at the strike nearest the
  prior close and 10-wide wings, held to settlement.
- **Where the edge would live:** a claimed **structural pin**: SPX closes near the prior close more
  often than the option prices allow. His backtest put that at about 25% of days.
- **Already checked, on public daily data** (Dolt SPY 2023-09 to 2026-10, scaled to SPX points, with
  the open standing in for 10:00 spot): the prior close is a worse centre than the open at every
  width.
  - Within 10 points at the close: 20.1% of days from the prior close, against 25.3% from the open.
  - A 10-wide fly is worth 1.07 points at expiry on average centred on the prior close, against 1.30
    on the open.
  - On days that gap 10 points or more: 14.8% within 10 points of the prior close, against 23.8% of
    the open.

  His 25% matches the open, not the prior close. An off-centre fly is also cheaper to buy, so this
  doesn't settle its expectancy; only quotes can.
- **His live figures:** 12 wins and 62 losses, average win ~$2,063, average loss $391, so about +$7 a
  trade. Drawdown $10.4k over two and a half months. Scaled back to his backtest's size, that drawdown
  is already more than the whole three-bot backtest's in 3.5 years.
- **What would falsify it:** centred on the prior close, the fly nets no more than the same fly
  centred on 10:00 spot that day.
- **Smallest test:** paired, the same session and minute, prior-close centre against spot centre,
  with the fill sensitivity. Split by gap size.
- **Build only if:** it beats the spot-centred pair in most years **and** nets positive after costs.
  The daily check predicts a negative.

### 11. The overnight gamma scalper (1DTE)

*Source: as 9.*

- **Rule as stated:** at 13:30 ET, a 1DTE SPX iron fly with its shorts at the next day's
  highest-gamma strike and 30-wide wings. Take profit at 15% of credit or $300; otherwise hold to the
  next day's settlement.
- **Where the edge would live:** **dealer pinning** carried overnight, a structural effect. It is one
  case of 12, held overnight.
- **His live figures:** 53% wins, average win larger than average loss; no amounts given.
- **What would falsify it:** the next day's close lands no nearer the highest-gamma strike than the
  13:30 spot predicts.
- **Data gap:** the store fetches the 0DTE expiry only (`fetch.py` refuses anything else). This needs
  the next day's expiry from 13:30, with its quotes overnight and through the next session. That is a
  fetch extension (a second expiry on each session), and the signal test needs it too, because the
  highest-gamma strike is read from that expiry's own profile.
- **Smallest test:** the signal first. Measure the distance from the next close to the highest-gamma
  strike, against its distance from the 13:30 spot, as in 12(a). Price the fly only if the signal
  exists.
- **Build only if:** as 12, then positive after costs with his take-profit.
- **Retired, not run (2026-10-30, the user's call).** Despite its name it is no gamma scalp: nothing
  is long gamma and nothing is hedged. It is a short 1DTE iron fly, so its only possible edge is
  pinning carried overnight, which is 12(a) held for a night. That read was invalidated rather than
  answered, and no corrected pin test has been declared. The nearest evidence that exists is against
  it: the prior-close pin fly (10) failed, and the overnight 1DTE SPX premium is about fair at mid,
  with the spread and fees taking the rest (19 and 22, design and holdout). Running it would need
  tomorrow's expiry and its open interest from 13:30, which no store holds, and a full pass rule,
  to test a mechanism with no new reason to expect an edge. Reopen only with a new mechanism and a
  newly declared test, as 12 says.

### 12. GEX levels, revisited with history

*Sources: Theta Profits videos 5, 7 and 14; Option Alpha's gamma scalper (11). Moved out of "not
worth revisiting" on 2026-10-18.*

- **Why reopen it:** the evidence that closed it was short. It came from the 10-15 sign-gate replay
  over recorded sessions and bwb's `wall` arm, and the recorder has held per-strike profiles only
  since 10-05. The backtest store writes the recorder's own `gex_profile_history` and
  `gex_regime_history` tables for every vendor session: per-strike open interest, volume, IV and
  gamma, plus walls and the flip. So the same readers can be run over years instead of weeks.
- **Where the edge would live:** a **flow effect**, dealer hedging. Long gamma at a strike fades
  price toward it; short gamma chases price away.
- **The reads to test, signal first, each against a same-distance baseline** (walls tend to sit on
  round strikes near spot, so the baseline is a strike at the same distance, not a random one):
  - *(a) Pin:* the close lands nearer the highest-gamma strike (or a long-gamma cluster) than a
    same-distance strike (video 5, 11).
  - *(b) Barrier:* price crosses the largest long-gamma strike above or below by the close less often
    than a same-distance strike (videos 7 and 14: sell beyond the node).
  - *(c) Regime:* net-positive gamma sessions realise a smaller range relative to the expected move
    than net-negative ones (video 7's fade versus chase). The sign gate already lost on this in our
    short sample; history decides whether that was the sample.

  Read each two ways: by gamma, and by **open interest alone**. Video 7 reads the vendor's chart as
  contract counts, so far out-of-the-money clusters still count.
- **What would falsify it:** no read separates outcomes from its baseline out of sample.
- **Caveats to declare before running:**
  - The store's gamma is computed from the vendor's IV (`greeks.bs_gamma`), not taken from a live
    feed.
  - The store's profile covers the 0DTE expiry only. An all-expiries level isn't reproduced, so the
    test states which one it uses.
  - Profile strike counts differ between the vendor and the recorder (the validation report carries
    both).
  - Until validation unlocks, a read is a provisional look on the design window only (see
    "Provisional now" above). Results are licensed and stay under `~/.cherrypick`; this doc records
    only that a read passed or closed, as for 4 and 8.
- **Build only if:** a read beats its baseline in most years. Then a structure priced at that level
  (a credit spread beyond a barrier, or a fly at a pin) nets positive after costs. A passing read
  becomes a proposal for flies' `call_wall` rule or a new arm, landing at a boundary.
- **Closed (2026-10-19), reviewed 2026-10-25:** the barrier and regime reads fail. The pin read, as
  declared, could never pass, so it tested nothing. The gamma barrier read was re-run after a
  put-wall fix and still fails. See "Results of the provisional looks" for what stands.

### 13. A screener for credit structures the model calls positive-expectancy

*Source: Option Alpha (https://youtu.be/4ngxNC6AWQI), two discretionary trades placed live after a
sell-off.*

- **Rule as stated:** in the vendor's trade-ideas screener, look for iron condors and credit spreads
  on single stocks (index ETFs excluded) that meet all of these:
  - reward to risk ≥ 100%;
  - probability of max profit ≥ 50%;
  - 7 DTE or less;
  - at least 2% out of the money.

  Take profit at 40%; otherwise hold to expiry. His examples: an ORCL 2 DTE iron condor at $0.47
  (mid $0.49; 88% reward to risk once filled), and a 17 DTE VZ call spread at $1.26 (101%). Entered
  after a sell-off, to catch the contraction of a volatility spike.
- **Where the edge would live:** a **risk premium**, the variance premium sold when IV is high after
  a sell-off. The screen itself is a different matter. A credit structure with reward to risk of
  100% or more *and* at least a 50% chance of max profit has positive expectancy by the screener's
  own model. Under fair pricing that can't happen. So the screen selects the trades where the model's
  probabilities disagree most with the market: skew the model flattens, stale or wide mids (ORCL's
  quote was wide), or event risk the model leaves out. That makes it a **model-error selector**
  unless shown otherwise.
- **What would falsify it:**
  - *(a)* Trades passing both thresholds win max profit no more often than their prices imply, and
    net no better than same-delta structures that fail the screen.
  - *(b)* Selling after a sell-off earns no more of the variance premium than selling on other days.
- **Data:** Dolt option chains (end of day, dense from 2024; sparse before) and daily bars for single
  stocks. The ThetaData store is SPXW 0DTE only, so this doesn't use it.
- **Smallest test:**
  - *(a)* From the chains, build the candidates each day (ICs and verticals, ≤7 DTE, ≥2% OTM). Score
    probability of max profit from each leg's own IV, as a screener would. Then compare the
    both-threshold set with a same-delta control on realised max-profit rate and net at natural less
    25%. Exclude earnings spans, which the screen doesn't.
  - *(b)* Split the same candidates by a prior-week drawdown in the underlying, or IV rank.
- **Build only if:** (a) the screened set nets positive after costs and beats the control in both
  halves of the sample, or (b) the after-sell-off split is positive where the rest isn't. Expect (a)
  to fail. A pass would more likely point at a premium already measured: the vendor income
  screeners' short puts and credit spreads, and `curve`'s VXX regime gate.
- **Provisional look, 2026-10-18: fails both tests; closed.** Dolt end of day, design window, 10–17
  DTE, 40 names. All figures are q25 net per trade.
  - **(a)** 132 of 561,324 candidates passed the screen: 7 in H1, 125 in H2. Model P(max profit)
    0.61, realised 0.58. Mean +$52, but the interval spans zero in both halves (H1 −$10 to +$152;
    H2 −$75 to +$187). Their excess over same-type, same-delta trades spans zero too (H1 −$0 to
    +$191; H2 −$18 to +$80).
  - **(b)** The ~0.20-delta spreads on sell-off days: H1 −$4 (−$17 to +$8); H2 +$0.5 (−$24 to
    +$24).
  - *Seen, not tested:* on all other days the same spreads lost: −$14 in H1, −$21 in H2, both
    intervals below zero. A sell-off-versus-other difference wasn't a declared test, so acting on it
    would need its own.
  - Not tested: the 7-DTE version (no Dolt expiry that short) and the 40% take-profit.

### 14. Selecting credit spreads: which names, which side

*Source: the user's own trade (2026-10-19). Credit spreads on the monthly 30-45 days out, sold for
premium, at a credit of 35-40% of the width, on both sides. The Screeners page's credit-spread tab
still shows the vendor's lists, which stop on 2026-10-24, because which names make them was never
reproduced (the best rule found picked 10-15% of its slots right).*

- **Rule as traded:**
  - the standard monthly 30-45 days out;
  - a put spread (bullish) or a call spread (bearish);
  - the working credit 35-40% of the width;
  - no earnings report inside the trade.
- **Where the edge would live:** a **selection signal**.
  - At 35-40% of the width the short strike sits near 35-40 delta, so the payoff is mostly
    direction.
  - The premium alone has measured fair to slightly negative after costs: the short-put and
    covered-call delta studies (2026-10-19) and the delta-hedged put tests.
  - A spread earns here only if something picks the name and the side better than chance.
- **Selectors, each a side:**
  - **A: `mr-300m` → put spread.** The one setup that passed the historical study's confirmation:
    mean reversion, long, on names trading $300M a day ([setups.md](../packages/technicals/docs/setups.md),
    round 2). It was tested on stock fills only; this asks whether it survives as an option trade.
  - **B: the signal board → either side.** A put spread where 3 or more of the board's methods agree
    bullish, a call spread where 3 or more agree bearish. The methods are stage, sector rotation,
    fundamentals against industry peers, fundamentals against price, the trend scan and analyst
    revisions. This is the "fundamentals against the industry, mixed with technical signals"
    hypothesis for how the vendor picked its names. Its fit to the vendor's lists is not the test;
    whether it makes money is.
  - **C: the short setups → call spread.** The short pullback and fading strength (mean reversion,
    short). As stock shorts they lost money through 2011-2026, and fading strength did worse than
    random. A call spread is not a stock short, though: it wins if the stock merely fails to rally
    past the short strike, and gives up nothing below it. This asks whether that difference is
    enough.
  - **D: SPY, QQQ, IWM with no selector → both sides.** The reference: the place premium selling
    has looked least bad. A stock selector has to clear it to be worth a list.
- **Structure, the same for every selector:**
  - the latest standard monthly 30-45 days out at entry (no trade when there is none);
  - the short is the out-of-the-money strike nearest 0.40 delta;
  - the long is the widest listed strike beyond it whose working credit is 35-40% of the width;
  - no trade when no width lands in that range.
- **Entry and fills:**
  - Entry is at the end of the session after the signal, on that session's quotes.
  - Fills are at the working price (the mid less a quarter of the width) both ways.
  - Costs are `core.fees`' schedule plus $5 for each leg settled in the money.
- **Excluded:**
  - an earnings report between entry and expiry;
  - a call spread across an ex-dividend date (early assignment is not modelled, so a spread that
    risks it is left out and counted);
  - trades across a suspected unadjusted split (`history.suspected_actions`).
- **Exits:**
  - **(i) hold to expiry**, the primary exit;
  - (ii) close at half the credit, on end-of-day marks;
  - (iii) close on the selector's own exit, for A and C;
  - (ii) and (iii) are reported but cannot pass on their own.
- **Universe:** every name that, on the session before entry, has listed options and a 50-session
  median dollar volume of at least $300M on raw bars, the study's own universe rule. Weekly-option
  listings have no history here, so the options-tradable label is not used.
- **Baseline, per selector and side:** five matched random entries for each selector trade. Each is
  the same name, a random session in the same calendar year on which the name was in the universe,
  and the same structure, exits, costs and exclusions. The test is what the selector adds, not
  whether spreads paid in a rising market.
- **Data:**
  - End-of-day option quotes from the ThetaData subscription, fetched for the universe into
    `packages/backtest`'s store. A new fetch: the store holds SPX intraday only.
  - Bars, the board's inputs and the study's setups from `history.db`, rebuilt as of each session.
  - **Window:** from the later of 2017-11 (Dolt's first estimate snapshot, 2017-10-26, which B's
    fundamentals need) and ThetaData's first end-of-day chain. It ends at the holdout boundary
    (`cherrypick.backtest.settings`). *Amended 2026-10-20 by the probe result below: the deciding
    window starts 2020-01.*
  - A, C and D may start earlier if ThetaData reaches back further; B never does.
  - The halves are split by date at the window's midpoint.
  - Results stay under `~/.cherrypick`, per the licence.
- **Probe first, before any fetch:**
  - which years the subscription serves end-of-day chains for single stocks;
  - whether a whole chain comes in one request;
  - the fetch's size and time for the universe;
  - the result decides the window, and is recorded here.
- **Probe result (2026-10-20), and the window it sets.** Declared before any option data was fetched.
  - **Depth:** the Standard subscription serves end-of-day single-stock chains from 2016. 2012 and
    2014 need the Professional tier and were refused. Expirations are listed from 2012, but listings
    are not quotes.
  - **No whole-market file:** the per-day file (`option_flat_file_eod`) is Professional-only.
  - **Speed:** one name's chain for one session takes about a second. A 45-session range of one
    expiry took about 30 seconds.
  - **So the fetch is event-driven:** with hold-to-expiry the primary exit, a trade needs only its
    entry session's chain, because the expiry value comes from the stock's close. The half-credit
    exit's daily marks are fetched only for a selector that passes on the primary exit.
  - **Earnings (amendment):** Dolt's earnings calendar starts 2020-01-22, and `eps_history` holds
    quarter-ends, not report dates. So the **deciding window is 2020-01 to the holdout boundary**,
    where the earnings exclusion is exact, and its halves are split at its midpoint.
  - **2016-2019** are reported beside it, with reports approximated as 14-60 days after each quarter
    end. Those years can't pass or fail a selector.
  - B's own start (2017-11) is unchanged and falls inside the reported years.
  - The report (counts and timings only) is kept at
    `~/.cherrypick/data/backtest/results/research/item14-probe-20261020.txt`.
- **Trade counts (2026-10-20, local bars only; no option data yet).** Over 2016-01 to 2026-09, on the
  4,970 names that ever traded $300M a day. A signal is a trade only if a standard monthly falls 30-45
  days after entry, which about 55% do.
  - **A** (`reversion`): 2,828 trades, 2,207 of them from 2020.
  - **C** (`pullback-short`): 1,873 trades, 1,309 from 2020.
  - **C** (`reversion-short`): 5,167 trades, 4,131 from 2020.
  - **D:** 297 entry sessions across 3 funds and both sides, 1,782 trades.
  - **B** is counted once its historical board is built.
  - **Fetch size:** with five matched baselines a trade, about 60,000 one-session chain requests,
    about 46,000 of them in the deciding window. That is before the earnings exclusion, and
    overnight work for a few workers at about a second a request.
- **The plan itself** (2026-10-20, local data only): `scripts/credit_spread_plan.py`.
  - **What it does:** applies every exclusion above and draws the five baselines per trade with a fixed
    seed. It writes `trades.json` and the fetch's `requests.json` under
    `~/.cherrypick/data/backtest/results/research/item14/`.
  - **Window:** trades end on the design window's last day (2025-06-30). A trade whose expiry falls in
    the holdout is dropped.
  - **Deciding-era trades:**
    - A: 1,150;
    - C `pullback-short`: 614;
    - C `reversion-short`: 1,338;
    - D: 683.
  - **Reported years are thin:** 2016-19 hold 300, 101, 238 and 543. The 14-60-day report
    approximation covers most 30-45-day trades.
  - **Fetch:** 21,707 distinct chain requests, since trades and baselines share chains.
- **Passes if,** for a selector and side, with the hold-to-expiry exit:
  - the mean of (selector trade's net − the mean of its five matched baselines' net) is positive;
  - its one-sided t is above 2 in **both** halves;
  - the selector's own mean net per trade is positive in both halves;
  - it survives Holm across the five selector-sides tested (A, B-put, B-call, C, and D's two sides
    reported as reference, not tested).
  - Fewer than 100 trades in a half is no verdict, only counts.
- **What would falsify it:** no selector beats its matched baseline; or one does but loses money
  outright (a better loser is not a list).
- **Known limits, declared:**
  - B's sector map holds today's names only, which biases the older years towards survivors.
  - The board's three flow tags are not counted, as on the live board.
  - Revisions cover the names their fetch has read.
  - Early assignment is not modelled, so call-spread results are an upper bound beside the ex-date
    exclusion.
  - Fills at the working price assume a liquid chain, which the $300M universe makes likelier but
    does not ensure; trades whose legs fail the leg bars on the entry quote are reported separately.
- **Build only if:** a selector passes. A pass becomes a proposal for the credit-spread tab, and for
  manage's spread candidates, which today build the vendor's structure with no side rule. It is
  never an arm by itself.
- **Result (2026-10-21), run offline over the fetched chains; figures kept locally per the licence:**
  - **A and C: no verdict.** Fewer than 100 paired trades in each half: a 0.40-delta short rarely
    admits a 35-40% working credit at any width, so most trades and baselines had no structure.
  - **B: not run.** There is no historical board yet.
  - **D:** the call side lost in both halves; the put side priced too few trades to read.
  - A sensitivity added after the declared run (the short moved to the strike nearest 0.40 delta that
    does admit the credit) cannot pass by construction, and failed the bar anyway.
  - Beside it, the income review's question (`docs/income-review-plan.md`, item 2): at 0.20, 0.25 and
    0.30 delta and the first out-of-the-money strike, unconditioned, **no short delta had edge after
    costs**, on either side, in either half. The credit-share constraint is what starved this test;
    item 15 drops it.
- **Selector B, as declared above (2026-10-23).** Its historical board is `scripts/board_history.py`:
  the live board's six methods (`technicals/board.py`) replayed on every session from 2017-10-26, each
  input cut at the session, with a look-ahead guard that re-reads a sample through the live
  `board.tags_on` and refuses the build on any difference. B's trades are built exactly as written
  above (the short nearest 0.40 delta, the widest long at 35-40%, else no trade), but its test is
  not: the amendment below pools its put and call sides into one member of the Holm family (see
  **Holm** there). Two details the text left open, fixed here before any B trade was priced:
  - **A signal** is a session on which a name's count on one side reaches 3 or more having been
    below 3 on that side the session before (a fresh reading, as a setup's entry is), on a name in
    item 14's universe that session. A name with a B trade open on that side (entry to expiry) takes
    no second one.
  - **The universe** is single-name stocks only, as on the live board (it reads no fund).
- **Amendment (2026-10-23): A' and C', a new hypothesis, fixed before any B trade was priced.**
  *Source: the user, 2026-10-23, after the declared run starved A and C of trades.* The same
  selectors (A: `mr-300m` put spreads; C: the short pullback and fading strength as call spreads),
  the same trades and five matched baselines from `trades.json`, the same expiry, fills, costs,
  exclusions, window, halves and pass rule. **Only the structure changes:**
  - **Short:** among the two-sided out-of-the-money strikes whose delta is between **0.30 and 0.50**
    in absolute value, taken in order of their distance from 0.40 (the nearer first; a tie goes to the
    strike further out of the money), the first for which some listed long beyond it gives a working
    credit of 35-40% of the width.
  - **Long:** for that short, the widest such strike.
  - **No trade** when no short in the band admits one; counted, as before.
  - **Baselines** are priced under the same rule, so a trade and its baselines share one structure.
  - **Holm:** A', B and C' are corrected together as one family of three (B's put and call sides
    pooled into one selector, each side also reported). D stays the reference, not tested. This
    replaces the family in the pass rule above (A, B-put, B-call and C), and it changes B's test:
    its two sides were two members of that family and are now one, which changes both its
    multiple-testing correction and its power (one test on the pooled trades, in which an edge on
    one side can be diluted by the other). They are pooled to keep the family at three members; no
    other reason was recorded when this was written.
  - **The original A and C** stay on the record as the 2026-10-21 run's result (no verdict: too few
    paired trades in each half). They are not members of this family, and A' and C' do not replace
    that result.
  - **Not a clean test:** a post-hoc version of this structure (the same search with no delta band)
    was already looked at in the 2026-10-21 sensitivity run, and it failed. Whatever A' and C' show
    is read with that in mind, and the result says so.
- **Result (2026-10-24), figures kept locally per the licence:**
  - **B fails.** It meets the pass rule's floor (100 paired trades in each half), so it has a
    verdict: it trails its random-date baselines and loses money outright in both halves. Nearly all its trades are put spreads; the call side alone is
    too thin to read.
  - **A' and C' fail.** Each beats its baselines in one half only, and loses money outright in the
    second half: a better loser, not a list. They were never a clean test anyway (see above).
  - No selector survives, so neither the credit-spread tab nor manage's spreads get a side rule from
    item 14.

### 15. Which strike and exit for a bullish put spread on `mr-300m`

*Source: the income review (2026-10-21). After item 14 gave no verdict, the user asked which strikes a
bullish put spread should use when a directional signal stands behind it, and whether closing at 50%
of the credit helps. Declared here before any of it is run.*

- **Question:** does `mr-300m`, the one directional signal that passed its historical confirmation,
  make a put credit spread worth its risk after costs, and if so with which short strike and which
  exit? The answer decides manage's put-spread candidates (`docs/income-review-plan.md`, item 2).
- **Where the edge would live:** the **selection signal**. Item 14's delta comparison found no edge in
  the premium alone; a spread earns here only if the signal moves the odds of finishing above the
  short strike by more than the costs.
- **Entries:**
  - **Signal:** every `mr-300m` long entry in item 14's plan (`trades.json`, selector A), entering at
    the end of the session after the signal on that session's quotes.
  - **Baseline:** two matched random entries per trade, drawn from item 14's five (same name, a random
    session in the same calendar year on which the name was in the universe), first two by its fixed
    seed. Two, not five, to bound the daily-mark fetch below.
  - **Excluded,** as item 14: an earnings report between entry and expiry, a suspected unadjusted
    split. No ex-dividend exclusion for puts.
  - **Universe (amended 2026-10-21, before any of it was run):** **single-name stocks only** -- every
    signal on a name `history.db` does not list as a fund (`listings.is_etf`). That is 285 names and
    736 signal trades in the deciding window before the structure's own drops, against 1,101 with the
    funds. Funds are left out because the user trades credit spreads on single names and on a few
    funds of their own (item 16), and because a mean-reversion long on an inverse fund is a short.
    GLD, SLV, GDX and GDXJ, which do clear the signal's liquidity rule, are reported as one row of
    their own (12 signals in the window: never a verdict).
- **Structure (one rule for every cell):**
  - **Expiry:** the latest standard monthly 45 to 60 days after entry; no trade when there is none.
  - **Short:** the out-of-the-money put nearest **0.20, 0.30 or 0.40** delta (three cells).
  - **Long:** the listed put nearest **half the short's delta** (0.10, 0.15, 0.20), so each cell keeps
    its shape on every name, a high-IV name getting a wider spread in dollars. No trade when that is
    the short's own strike.
  - **No credit-share floor.** The credit is an output.
  - Delta: Black-76 on a forward from put-call parity at the three strikes nearest the close, IV from
    each strike's mid, r = 0, calendar days, as item 14's run.
- **Exits (two cells each):**
  - **(i) hold to expiry**, settled at intrinsic from the expiry close, capped at the width;
  - **(ii) close at 50% of the credit** on an end-of-day mark, **else at 21 days to expiry**, else
    hold.
  - No stop-loss arm: the strangle management test (2026-10-20) found stops lose in both halves.
- **Costs:** fills at the working price (`legbars.structure_prices`: the mid credit less a quarter of
  the legs' bid-ask widths) on entry and on any early close; `core.fees` per leg; $5 for each leg
  settled in the money. Early assignment of a deep in-the-money put is not modelled: an upper bound.
- **Marks for exit (ii):**
  - **Modelled, for every trade:** each leg repriced each session from the stock's close and the
    name's daily IV in `history.db`, scaled by the leg's entry IV over the name's entry-day IV (the
    entry skew held fixed), at the leg's own remaining time.
  - **Real, for a sample:** 200 trades drawn at random (fixed seed) across signal and baseline, their
    expiry's end-of-day chain fetched for every session from entry to exit.
  - **The model is used only if** on the sample its 50%-hit rate is within 5 points of the real one,
    and its mean net per trade as a share of max loss is within 2 points. If it is not, exit (ii) is
    reported as not decidable from the model, and decided only on a fuller real-mark fetch.
- **Reported per cell (short delta × exit), signal and baseline:**
  - trades; mean net per trade as a share of max loss (the comparable figure across accounts) and in
    dollars per unit; credit as a share of max loss; win rate;
  - for exit (ii): the share reaching 50%, the median sessions to it, and the share closed at 50% that
    would have finished worthless anyway (what the early close gave up);
  - worst trade, cost drag (round-trip costs ÷ credit);
  - **fit by budget:** the share of signals whose one unit's max loss fits a per-trade budget of
    $250, $361, $500, $1,000 and $2,500, and the signals a month that leaves at each.
- **Window:** item 14's deciding window, 2020-01-22 to 2025-06-30, halves split at its midpoint;
  2016-19 reported only.
- **Passes if,** for a cell:
  - the mean of (signal trade's net − the mean of its two baselines' net), as a share of max loss, is
    positive with a one-sided t above 2 in **both** halves, t clustered by expiry date;
  - the signal's own mean net per trade is positive in both halves;
  - it survives Holm across the six cells.
  - Fewer than 100 trades in a half is no verdict.
- **Decides:** manage's put-spread candidates use the best passing cell (its short and long rule and
  its exit), on names with a live `mr-300m` signal. If none passes, put spreads stay offered with "no
  tested edge" at the least-bad unconditioned short from item 14's comparison.
- **Data:** item 14's store holds 30-45-day expiries only. The 45-60-day entry chains for the signal
  and its two baselines, and the 200 sampled trades' daily chains, are a new fetch into the same store
  (`fetch-chains`), run only while no other session holds the ThetaData login. Results under
  `~/.cherrypick`, never in this repo.
- **Result (2026-10-21), figures kept locally per the licence:**
  - **Hold to expiry fails at every delta.** The signal's own net was negative in the first half and
    it did not beat its random-date baselines in either. The least-bad cell was the 0.20-delta short.
  - **Close at 50% else at 21 days:** the 200-trade real-mark check ran (2026-10-21). The model
    passed the declared tolerances at 0.40 delta only; it understated how often 50% is reached by
    a few points more than allowed at 0.20 and 0.30, while getting the net close. So **0.40 fails**
    on the model, and **0.20 and 0.30 are not decidable from it**, as declared. The 200 trades priced
    on real marks lose at all three deltas, so a full real-mark fetch is not worth running for them.
  - The metals row and the user's own traded names (a row added after declaration, reported only)
    show nothing that changes this.
  - **Departures from the text, written down before any option data was fetched:** the entries are
    item 14's planner re-run with the 45-60-day window and its first two seeded baselines (read
    literally, item 14's 30-45-day trades left too few with a 45-60-day monthly to reach a verdict);
    the model's daily IV comes from the local Dolt volatility history, since `history.db`'s IV table
    is empty, which leaves 2016-19 and funds unmodelled.
  - **Decided:** manage's put spreads use the least-bad unconditioned cell, as declared
    (`docs/income-review-plan.md`, item 2).

### 16. Put credit spreads on the funds the user trades: precious metals and bitcoin

*Source: the income review (2026-10-21). The user's own credit spreads are, beyond single names,
GLD, GDX, SLV, GDXJ, SIL and SILJ, and IBIT now and then. `mr-300m` fires on them 12 times in five
years and never on SIL or SILJ (about $15M a day, against its $300M rule), and precious metals trend
rather than revert. So this asks the premium question directly, with no signal. Declared before any
of it is run.*

- **Question:** on each of these funds, is a put credit spread worth its risk after costs, and with
  which short strike and which exit? The expectation, from the short-put delta studies, is "least bad,
  not edge"; this measures it on the funds actually traded.
- **Funds and what decides each:**
  - **Precious metals:** GLD, SLV, GDX, GDXJ, SIL, SILJ, each decided on its own.
  - **Bitcoin:** **BITO** (options from 2021-10) decides; its futures-roll drag makes it a
    conservative stand-in for a short put. **IBIT** (options from 2024-11, about seven monthly cycles
    before the window ends) is reported, never decided. The holdout after 2025-06-30 may be opened
    for IBIT alone later, under these same rules, once it has enough cycles.
- **Entries:** every Monday (or the next session when Monday is a holiday) on which a standard monthly
  expires 45 to 60 days later, entering at that session's close on its quotes. No signal and no
  baseline: overlapping trades on one expiry are handled by clustering t by expiry date.
- **Structure, exits and costs:** exactly item 15's -- shorts at 0.20, 0.30 and 0.40 delta, the long
  at half the short's delta, no credit-share floor; hold to expiry against close at 50% of the credit
  else at 21 days; working-price fills, `core.fees`, $5 per leg settled in the money; early
  assignment not modelled.
- **Marks:** **real** end-of-day chains for every session of every trade. The universe is small
  enough to fetch them all, so no model is needed here.
- **Window:** 2020-01-22 to 2025-06-30 (BITO from its first listed monthly), halves split at its
  midpoint.
- **Reported per fund and cell:** item 15's figures (net and credit as a share of max loss, win rate,
  50%-hit rate and sessions to it, what the early close gave up, worst trade, cost drag, fit by
  budget), plus the leg bars' pass rate on entry quotes, which says whether SIL's and SILJ's chains can
  be filled near the mid at all.
- **Passes if,** for a fund and cell: the mean net per trade as a share of max loss is positive with a
  one-sided t above 2 (clustered by expiry) in **both** halves, surviving Holm across that fund's six
  cells. Fewer than 40 trades in a half is no verdict.
- **Decides:** a passing cell becomes the rule manage and the Screeners lists use for that fund's put
  spreads. A fund with no passing cell keeps "no tested edge" at its least-bad cell, which the report
  names.
- **Data:** a new fetch into item 14's store (`fetch-chains`): every entry session's chain and every
  later session's chain for each trade's expiry, about 70 monthly expiries a fund. Run only while no
  other session holds the ThetaData login. Results under `~/.cherrypick`, never in this repo.
- **Result (2026-10-21), figures kept locally per the licence:**
  - **GLD, SLV, GDX, GDXJ, SIL and SILJ fail in every cell.** GLD came closest, its return sitting
    in one half (the 2024-25 gold rally), which is drift, not a repeatable edge.
  - **BITO: no verdict** (too few trades in its second half, after entries whose parity forward
    strayed from the close were dropped). IBIT reported only.
  - **Closing at half the credit did worse than holding on every fund,** by far the most on SIL and
    SILJ, where buying back across wide quotes is dear.
  - **SILJ's entry quotes almost never clear the leg bars;** SIL never makes the tradable universe.
  - **Decided:** each fund keeps "no tested edge" at its least-bad cell; manage's single rule (0.20
    short, 0.10 long, held) covers them, and the live leg bars already mark SILJ thin.

### 17. Our own credit-spread list: setup, side and width, per symbol

*Source: the user (2026-10-24), when the vendor subscription ended and its credit-spread tab froze at
the 2026-10-23 lists. The list is to come from the short-put screener's universe and the chart
setups, on both sides, with the width analysed per symbol. Item 14 fixed the structure (a 0.40-delta
short, the widest strike giving 35-40% of the width) and never crossed setup, side and width; this
does. Declared here before any of it is run.*

- **Question:** for which setup and side, short strike and width, does a credit spread pay after
  costs, and does a width picked per symbol beat one width for every name? The answer is the
  credit-spread tab's rule.
- **Where the edge would live:** the **selection signal**, with width as payoff shaping. Items 14-16
  found no edge in the premium alone, so a cell earns only if its setup moves the odds past the short
  strike by more than the costs. A per-symbol width can only help if a name's own strike spacing,
  skew or move size makes one width repeatably better there.
- **Setups, each mapped to a side** (the rules exactly as [setups.md](../packages/technicals/docs/setups.md)
  states them, parameters unchanged):
  - **Trend following:** long → put spread; the short mirror → call spread.
  - **Pullback in a trend:** long → put spread; the short mirror → call spread.
  - **Mean reversion:** long → put spread; the short mirror → call spread.
  - **Breakout:** long → put spread; the breakdown → call spread.
  - Eight setup-sides. Entry is at the end of the session after the signal, on that session's quotes.
- **Universe:** the short-put screener's rule, as of the session before entry: listed options and a
  50-session median dollar volume of at least $100M on raw bars. Weekly listings have no history
  here, so the weeklies half of the live rule is not applied. Funds and single names together, with
  inverse and leveraged funds left out.
- **Baseline:** five matched random entries per trade, as item 14 (the same name, a random session in
  the same calendar year on which it was in the universe, the same structure, costs and exclusions).
- **Structure:**
  - **Expiry:** the latest standard monthly 30-45 days after entry; no trade when there is none.
    45-60 days reported only.
  - **Short:** the out-of-the-money strike nearest **0.20, 0.30 or 0.40** delta.
  - **Width, in two units, each its own family:**
    - **strike steps:** the long 1, 2, 3 or 5 listed strikes beyond the short;
    - **expected move:** the long at the listed strike nearest the short plus (or minus) 0.25, 0.5 or
      0.75 of the 1-SD move to expiry (the at-the-money IV × √(days/365) × the close).
    - No trade when the long would be the short's own strike.
  - **No credit-share floor.** Credit received is an output: credit ÷ width and credit ÷ max loss are
    reported on every trade.
  - Delta and IV as item 15 (Black-76 on a parity forward, r = 0, calendar days).
- **Scoring exit:** **hold to expiry**, settled at intrinsic from the expiry close, capped at the
  width. This is the scoring convention only: the live position decides the real exit, so the list
  states no exit rule. Close-early variants are not run.
- **Costs and exclusions:** item 14's. Fills at the working price (`legbars.structure_prices`),
  `core.fees` per leg, $5 for each leg settled in the money. Excluded: an earnings report between
  entry and expiry, a call spread across an ex-dividend date, a suspected unadjusted split. Early
  assignment is not modelled, so call spreads are an upper bound.
- **Window:** item 14's deciding window, 2020-01-22 to 2025-06-30, split at its midpoint into H1 and
  H2. 2016-19 reported only.
- **Two rules under test, per setup-side and width family:**
  - **Pooled pick:** the (short, width) cell with the best mean net per trade as a share of max loss
    in one half, scored in the other.
  - **Per-symbol pick:** for each symbol, the cell that was best in one half on that symbol's own
    trades (needs at least 8 of them; fewer falls back to the pooled pick), scored in the other.
  - Each rule runs both ways (pick in H1, score in H2; pick in H2, score in H1), so every score is
    out of sample.
- **Passes if,** for a setup-side, width family and rule, **in both directions:**
  - the mean of (trade's net − the mean of its five baselines' net), as a share of max loss, is
    positive with a one-sided t above 2, clustered by expiry date;
  - the rule's own mean net per trade is positive;
  - it survives Holm across the 32 tests (8 setup-sides × 2 width families × 2 rules).
  - Fewer than 100 scored trades in a direction is no verdict.
  - **The per-symbol pick earns its place** only if it also beats the pooled pick on the same trades
    (paired t above 2 in both directions). Otherwise the list uses the pooled width for every name.
- **Reported, per setup-side and cell, pooled and by symbol:** trades; mean net as a share of max loss
  and in dollars per unit; credit ÷ width and credit ÷ max loss; win rate; worst trade; cost drag
  (round-trip costs ÷ credit); the share of entries whose legs clear the leg bars on the entry quote.
  The per-symbol tables are descriptive and never a verdict on their own.
- **Decides:** the credit-spread tab on the Screeners page (replacing the vendor's frozen one) and
  manage's spread candidates.
  - **A pass:** the tab lists names with a live passing setup, on its side, at the passing short and
    width (per symbol where that rule earned it), with the credit received.
  - **No pass:** the tab lists the least-bad setup-side and cell the report names, under a standing
    "no tested edge" note, like the short-put list.
- **Data:** the setups and bars from `history.db`, as of each session. Entry chains already in item
  14's store (`eod_chains.db`) are reused; the rest are a new `fetch-chains` run, only while no other
  session holds the ThetaData login and stopping before 18:45 ET on trading days (`backtest-fetch`).
  Results under `~/.cherrypick`, never in this repo.
- **Known limits, declared:** the setups lost as stock trades in their historical rounds (only
  `mr-300m` passed, on $300M names, and failed as a put spread in item 15), so the prior is low; the
  $100M universe takes in thinner chains than item 14's, which the leg-bar share reports;
  breakouts are rare, so their setup-sides may end as no verdict.
- **The plan** (2026-10-24, local data only, before any option data was read):
  `scripts/credit_spread_plan.py --item 17`, item 14's planner with item 17's spec. Its exclusions,
  baselines and request file are item 14's; re-run for item 14, it reproduces item 14's plan byte for
  byte. Seed 20261024. The universe is every name that ever traded $100M a day, less the declared
  leveraged, inverse and volatility funds.
  - **Deciding-era trades** (2020-01-22 to the design window's end), before the five baselines each:

    | Setup | Put spread (long) | Call spread (short mirror) |
    |---|---|---|
    | Trend following | 2,088 | 1,175 |
    | Pullback | 2,934 | 1,604 |
    | Mean reversion | 3,192 | 3,452 |
    | Breakout | 233 | 172 |

  - **Fetch:** 97,402 distinct chain requests, 12,500 of them already in the store from items 14-16
    and B. That leaves 84,902 to fetch, 67,345 of them deciding-era; the request file puts those
    first. At item 14's pace (about 3 a second with 3 workers) that is 7-9 hours.
  - 50 trades are short of five baselines (thin years).
- **A deviation, recorded after the run (2026-10-25):** the pass test above declares the excess as
  (the trade's net − the mean of its baselines' net) as a share of max loss, but the one run scored
  each trade's net over its own max loss less the mean of each baseline's net over that baseline's
  own max loss. The two differ only where a baseline's max loss differs from its trade's. The run and
  its verdict stand as run, and the script keeps that measure for item 17 so the run reproduces.
- **Result (2026-10-25, recorded here 2026-10-30): fail.** Of the 32 tests, none passes; 24 fail
  and 8 have no verdict (every breakout cell, too few trades).
  - **The closest cell,** mean-reversion put spreads, clears the t bar in both halves but fails
    Holm. Its excess over the random entries comes mostly from the baselines losing, not from
    the cell earning: on its own it sits near breakeven, its slippage is about its mean net,
    and only a minority of its entries have tradable legs.
  - **Per-symbol widths:** no per-symbol pick earns its place over the default.
  - **Call side:** mean-reversion call spreads clear the t bar but lose money in the second
    half.
  - The closest cell was tested once more on the holdout as item 18 (no verdict, too few liquid
    trades). Every figure is in the local result
    (`~/.cherrypick/data/backtest/results/research/item17/study.json`).

### 18. Mean-reversion put spreads, tested once on the holdout

*Source: item 17's one run (2026-10-25). It ran on licensed vendor history, so, as for items 4, 8, 9,
10 and 12, only the verdict is recorded here; every figure is in the local result
(`~/.cherrypick/data/backtest/results/research/item17/study.json`). Item 17 found no pass. One cell
came closest: mean-reversion put spreads, short nearest 0.20 delta, long 0.75 of the expected move
beyond. It met item 17's bars in both directions except Holm across its 32 tests, and it was chosen
by looking at those 32. So this tests that ONE cell, declared before anything is fetched, on data no
look has touched. The user approved the test on 2026-10-25. Declared here before any of it is run.*

- **Question:** does that cell earn after costs over random entries, on sessions item 17 never saw,
  **when only liquid strikes are traded**?
- **Where the edge would live:** the **selection signal**, with the structure fixed. A mean-reversion
  entry comes after a short-term drop in an uptrend. Put skew is then richer, and the name has
  bounced more often than chance, so a far out-of-the-money short with a wide wing may collect more
  than the move it is exposed to.
- **What item 17's readout says about the rule (read before declaring; figures local only):** most
  of the cell's excess over random came from the random entries losing, while the cell itself was
  near breakeven per unit of risk. Its credit was a small share of the width. Its slippage, averaged
  over every entry (most of them illiquid), was large next to its mean net. And only a minority of
  its entries had legs that clear the bars. So the third bar below, the trade paying on its own
  risk, is the one that matters, and the test is held to liquid strikes.
- **Rival explanations, each with its check:**
  - *Drift:* a rising market makes every short put look good. **Check:** the score is excess over
    five matched random entries (same name, same calendar year, same structure and costs), which
    take the same drift.
  - *Winner's curse:* the best of 32 cells is lucky by construction. **Check:** one declared cell,
    on the untouched holdout, with no re-choosing of short or width.
  - *A few names carry it:* **Check:** the result in each name-half (B9) and the share of the excess
    from the five largest names, both reported.
  - *A calm year:* **Check:** results by B8 environment and by B4 ATR band, reported. A pass that
    lives in one cell is noted as such.
- **The four parts:**
  - **The trades:** **liquid entries only**, those whose two legs clear the spread/two-sided bars
    (`legbars`) on the entry quote. That is how the spread would actually be placed (the user,
    2026-10-25: "the practical application would only use liquid strikes"). Each trade's baselines
    are held to the same bar. The store holds no open interest, so the bar is spread and
    two-sidedness only.
  - **Measured:** each trade's net minus the mean net of its matched baselines priced in the same
    cell, as a share of max loss. Also the cell's own mean net, in dollars and as a share of max loss.
    The script computes this as declared, (trade net − mean baseline net) ÷ the trade's max loss,
    and not with item 17's per-trade returns (the deviation noted under item 17), from 2026-10-25,
    before the run.
  - **Expected:** a positive excess of the size item 17 measured for this cell (in its local result).
  - **Window and sample:** the holdout, **2025-07-01 to 2026-10-12** ("the re-test, on the most
    recent window", above), read **once**, about 15 monthly expiries. Liquid entries are a minority
    of the cell's entries, so the scored sample is expected to sit near the 100-trade floor, and a no
    verdict is a likely outcome, accepted as one.
  - **Pass-or-fail rule:** all three must hold:
    - the excess has a one-sided t above 2, clustered by expiry date, p from Student's t with
      clusters − 1 degrees of freedom;
    - the cell's own mean net is positive in dollars;
    - **and** its own mean net as a share of max loss is positive. This is stricter than item 17,
      where a positive dollar mean could hide a negative mean per unit of risk.
    - Fewer than 100 scored trades is no verdict. There is no Holm: it is one declared test.
- **Minimum detectable effect (B12):** computed from item 18's own scored liquid trades, clustered
  by expiry, and recorded in the local result beside the verdict. (Amended 2026-10-25, before the
  run: item 17's result kept no per-trade rows, so its spread could not be rebuilt; the user accepted
  the change. Descriptive only.) With about 15 clusters and a liquid-only sample, it is expected to
  sit well above the effect item 17 measured. So this test passes only an edge much stronger than
  item 17's. A fail rules out an edge that large, and says nothing about one of item 17's size. The
  low power is the accepted price of testing the trade as it would be placed.
- **Account for B13's size line:** the live account's net liquidation value, chosen by the user
  before the run and passed at run time (`--account`, in the local queue row), so the account's
  size stays out of this repo.
- **Structure, unchanged from item 17:**
  - setup `reversion` long → put spread;
  - the latest standard monthly 30-45 days after entry;
  - the short nearest 0.20 delta;
  - the long at the listed strike nearest the short minus 0.75 × the 1-SD move to expiry;
  - hold to expiry at intrinsic capped at the width;
  - item 14's costs and exclusions.
  - Entries whose only monthly is 45-60 days out (the list's fallback) are **not** in the test. They
    are reported apart.
- **Fills:** the pass is judged at the working price (halfway between the mid and the natural fill),
  item 17's convention and the conservative one. Beside it, as description, the same trades and their
  baselines priced at **mid** and at **mid − $0.05** a spread, with slippage and cost drag for each.
  These show how much of any result rests on filling near mid. The only fill evidence the suite holds
  is its own SPX live fills (flies' entries filled at mid − $0.05 most of the time, and the misses
  were the trades moving their way). So mid is optimistic, and no fill assumption here is measured
  for these names.
- **Every entry, liquid or not:** reported beside the test as description, never as the test. This
  was decided on 2026-10-25, before anything in the holdout was fetched or read.
- **Name split (B9):** reported, not a bar. The holdout is too short to require both name-halves.
- **Robustness report beside a pass (B13), never used to re-choose:**
  - the neighbours (shorts 0.20/0.30, longs 0.5/0.75 of the move);
  - costs at 1.5× and 2×, with **1.5× declared as the stress the pass must survive**;
  - the bootstrapped drawdown;
  - the largest size inside a declared 10% of the account.
- **Declared limits (B11):**
  - delisted names settle at the last close;
  - the empty chains are counted;
  - no input reflects only today's state.
- **Data:**
  - the planner gains item 18 (item 17's rules, reversion only, the holdout window);
  - a `fetch-chains` run for its requests;
  - the item 17 study script restricted to this one cell and to liquid entries (a `--cells` option
    and a liquid filter, so the pricing and statistics are the same code);
  - all three queued, each `--after` the one before;
  - the result written under the backtest data dir only.
  - Its figures stay there; this plan records the verdict.
- **Decides:**
  - **A pass:** a proposal for the credit-spread tab to promote this selection from "least bad" to
    "tested", with the robustness report.
  - **A fail or no verdict:** closes the cell.
  - Either way the holdout is spent for this cell.
- **Result (2026-10-26): no verdict; the cell is closed.** Too few holdout entries cleared the
  liquidity bars to reach the 100-trade floor: the cell mostly selects strikes that cannot be
  traded. The holdout is spent for this cell, and the selection stays on the credit-spread tab
  under "no tested edge". Every figure is in the local result
  (`~/.cherrypick/data/backtest/results/research/item18/study.json`).
- **Not tested here:** the call-side twin (mean reversion short → call spread). Its item 17 picks did
  not agree on a short, so there is no one cell to declare.

### 19. The Time Warp: two out-of-the-money SPX flies bought at the close, sold the next morning

*Source: a public channel's live final-hour shows (reviewed 2026-10-26; transcripts kept in session
scratch only). The host trades it every session and reports a winning run, with no ledger and no
backtest. He expects a long-run win rate near half and says winners average a few times the debit.
The user approved the test on 2026-10-26. Declared here before any of it is fetched or run; the
two-session fetch smoke test read row counts only.*

- **Question:** does buying the host's fly pair in the last minutes of a session, and selling it the
  next session, earn after costs?
- **Where the edge would live:** the **premium**, in one specific place. The claim is that in a calm
  market the next day's wings are priced for a quiet session, while much of the move happens
  overnight, so a pair of cheap out-of-the-money flies bought at the close is under-priced for the
  gap. That is a claim that the overnight move is under-priced in far wings, not a signal.
- **Look-alikes:** `cherrypick hypotheses --similar` finds no rejected row with this setup and
  structure. The nearest evidence is flies' 0DTE record (retired 2026-10-19: lost on intraday
  strands) and the 0DTE long-fly payoff studies, all intraday. None held overnight.
- **Rival explanations, each with its check:**
  - *Drift:* a rising market pays a call fly more often. **Check:** the pair is two-sided; the call
    and put sides are reported apart, by B8 environment.
  - *A calm span:* **Check:** both time-halves of the design window must pass, and results are
    reported by VIX band.
  - *The exit carries it:* a trail can look good on a lucky path. **Check:** a second arm sells at a
    fixed 10:30 with no judgement, and both arms are Holm-adjusted.
  - *Fills:* far wings are wide. **Check:** the verdict is at the working price; mid and
    mid ± $0.05 a fly are reported beside it.
- **The structure (all of it from the host's stated rules, nothing chosen by us):**
  - SPX weekly options (SPXW), expiring the next trading day E; a Friday's E is the Monday.
  - Two long butterflies, one of calls above spot and one of puts below, each long 1 / short 2 /
    long 1 with wings `w` from the body. `w` by the prior session's VIX close: **20** below 17,
    **30** from 17 to 32, **50** above 32 (his table; 30 is inside his 25-30 band).
  - Entry at **15:50 ET** on session D, from D's 15:50 one-minute quote.
  - Each fly's body is the strike **nearest spot whose fly costs at most 5% of `w`** at the working
    price, walking out from spot one listed strike at a time. All four of its strikes must be quoted
    two-sided. A side with no such body is not entered; a session with neither is skipped and counted.
  - No stop, no adjustment. A fly not sold settles at E's official close (intrinsic).
- **Two exit arms** (the second added by us as the rival check, declared now):
  - **A, the trail (his).** On E's five-minute marks from 09:35, per fly: once its value is above its
    debit, record the high-water mark `h`. Sell when value falls below
    `debit + (1 - g) × (h - debit)`, with `g` = 0.75 before 12:00, 0.50 from 12:00 to 14:00, and
    0.20 after 14:00. Otherwise it settles.
  - **B, a fixed time.** Sell each fly at E's 10:30 mark, whatever it is worth.
- **Costs:** the suite's tastytrade schedule (`cherrypick.core.fees`): $1.00 a contract to open,
  clearing, ORF, the SPX exchange fee and TAF on every leg, no commission to close, and $5 per
  in-the-money settlement symbol. About $10 a fly round trip.
- **Measured:** the pair's net per session, in dollars, at one contract each side. Also as a share of
  the pair's debit.
- **Window and sample:**
  - **Design:** 2022-05-16 to 2024-12-31, about 660 sessions. Read for the verdict.
  - **Holdout:** 2025-01-02 to 2026-10-23, about 450 sessions, read **once**, and only if the design
    passes, with the same rules unchanged.
  - Fewer than 100 entered sessions in a window is no verdict.
- **Pass-or-fail rule**, per arm, all must hold:
  - mean pair net per session above zero with a one-sided t above 2, one session one observation;
  - Holm across the two arms;
  - each time-half of the design window positive;
  - still positive at **1.5× costs**;
  - then the holdout, with mean net above zero and a one-sided t above 2.
- **Minimum detectable effect (B12):** from the design window's own spread of session nets,
  recorded in the local result before the holdout is read. No comparable result exists to plan it
  from: no suite study held a fly overnight.
- **Declared limits (B11):**
  - a fly still open at E's close settles at the SPX close in `SPX-60s.db`'s `daily_closes`, with
    no recovery price. That close is the vendor's official index close from 2024-05-13 and its last
    underlying print before then; the two are reported apart;
  - sessions skipped for missing quotes or no affordable body are counted, by reason;
  - the VIX band uses D−1's close from Cboe's published VIX history (the local market-files copy),
    known at entry; nothing reflects only today's state.
- **Name split (B9):** does not apply; one symbol.
- **Robustness report beside a pass (B13), never used to re-choose:**
  - neighbours: 4% and 6% of `w`; entry at 15:45 and 15:55; widths one band narrower and wider;
  - costs at 1.5× and 2×;
  - the bootstrapped drawdown and losing streak;
  - the largest size inside a declared 10% of the account, with the account passed at run time.
- **Data:** `scripts/fetch_spx_time_warp.py` writes `SPX-time-warp.db`, declared in
  `stores.STORES`. It holds each session's 15:45-15:55 chain for the next expiry at one minute,
  and that expiry's next session at five minutes. The study script reads only it and `SPX-60s.db`. Results go under the
  backtest data directory only. Its figures stay there; this plan records the verdict.
- **Decides:**
  - **A pass:** a proposal for a paper arm of the trade beside the suite's loops, with the
    robustness report.
  - **A fail or no verdict:** closes this structure as specified.
- **Result (2026-10-27): fail, both arms; the structure is closed.** In the design window neither
  the trail nor the 10:30 sale has a positive mean net after costs, and neither has a positive
  time-half. The mid-price fills, reported only as context, do not change this: none passes the rule
  either. Every figure is in the local result
  (`~/.cherrypick/data/backtest/results/research/item19/study-2022-05-16-2024-12-31.json`).
- **Holdout read anyway (2026-10-27), at the user's request; exploratory, not a verdict.** The
  declared rule reads the holdout only after a design pass, so this run cannot overturn the fail.
  Run with the rules unchanged: both arms fail every check there too, the trail no better than the
  10:30 sale. The holdout is now spent for this specification; a revised Time Warp needs a new one.
  Local result: `.../item19/study-2025-01-01-2026-10-23.json`.
- **Not tested here:**
  - his "Convex Stack" (one with-trend 4-5DTE fly, twice a week), declared as item 20;
  - his late-day 0DTE broken-wing scalp, which depends on his chart levels. Its volume-profile rule
    is not yet known well enough to code.

### 20. The Convex Stack: one with-trend SPX fly bought twice a week at about 5 DTE

*Source: the same public channel as item 19, its live final-hour shows of 2026-10-15 to 10-22 and
earlier teaching videos (transcripts kept in session scratch only). The host shows about five live
trades and a "100% hit rate", with no ledger and no backtest. The user approved the test, and a read
of the holdout whatever the design shows, on 2026-10-27. Declared here before any of it is fetched or
run.*

- **Question:** does buying the host's one out-of-the-money fly on the side his moving average
  points to, twice a week at about 5 DTE, earn after costs?
- **Where the edge would live:** a **signal** carried by **premium**. The claim is that a fly a
  little out of the money on the trend side, a few days out, is cheap for how often a trending
  market reaches it. If the trend rule carries nothing, the same fly on the other side does as well.
- **Look-alikes:** `cherrypick hypotheses --similar` finds no rejected row with this setup, side,
  structure and window. The nearest closed evidence: item 5 (an EMA trend gate on PMCC, failed),
  item 4 (trend against reversion on 0DTE spreads, failed), item 19 (this host's overnight fly
  pair, failed in design and holdout) and flies' 0DTE record (retired 2026-10-19). None held one
  fly for several days.
- **Rival explanations, each with its check:**
  - *Drift:* in a rising market a call fly pays more often, trend rule or not. **Check:** the same
    rule's fly on the other side is priced and scored at every entry and reported beside it, by
    side. A trend rule that only matches its opposite has no signal.
  - *A calm span:* **Check:** both time-halves of the design window must pass; results by width
    band.
  - *The exit carries it:* **Check:** three exit arms, Holm-adjusted together, one of them simply
    held to expiry.
  - *Overlap:* a Monday trade and the Thursday trade overlap by a day and share a move. **Check:**
    entries are reported by weekday, and each week (Monday plus the Thursday after it) is also
    tested as one observation beside the per-entry test.
  - *Fills:* **Check:** the verdict is at the working price; mid and mid + $0.05 a fly beside it.
- **The structure (his stated rules, with each gap filled by a declared mechanical stand-in):**
  - SPX weekly options (SPXW). Entries on **Mondays**, expiring that **Friday**, and on
    **Thursdays**, expiring the next **Tuesday**. A holiday expiry moves to the listed trading day
    before it; an entry day that is a holiday is skipped. His replacement entry after an early close
    is not in the test: it adds trades only after a winner, which mixes the exit into the count.
  - Entry at **15:45 ET** on D, from D's 15:45 one-minute quote (his entries are in his 3 PM show;
    he states no minute).
  - **Direction:** a sine-weighted moving average, length 14, of SPX on four-hour bars of the
    regular session (09:30-13:30 and 13:30-16:00, each closing at its last spot print in
    `SPX-60s.db`'s spot history). At entry the second bar is still open and closes at the 15:45
    spot, as a live chart shows it. The average above its value one bar earlier means a **call
    fly**; otherwise a **put fly**. The weight of the i-th of the 14 bars is `sin(i·π/15)`.
  - **Width** `w` by D−1's VIX close, his table at our declared point in each band: **25** below
    17, **30** from 17 to 24.5, **40** from 24.5 to 32, **50** above 32.
  - One symmetric fly, long 1 / short 2 / long 1, wings `w` from the body. The body is the strike
    **nearest spot, on the trend side, whose fly costs at most 10% of `w`** at the working price,
    walking out one listed strike at a time (his cap is 10%; his heat-map choice inside 5-10% cannot
    be replayed). All four strikes two-sided. No such body: the entry is skipped and counted.
  - No stop, no roll, no adjustment. A fly not sold settles at X's official close (intrinsic).
- **Three exit arms**, Holm-adjusted together:
  - **A, hold:** settle at X's close.
  - **B, take profit at 3× the debit:** on the five-minute marks from 09:35 on each session after D,
    sell at the first mark worth at least three times the fly's debit. Otherwise it settles. (His
    "300-400%" is loose; 3× the debit is the declared point.)
  - **C, two sessions in:** sell at the 15:45 mark of the second trading session after D (Wednesday
    for a Monday entry, Monday for a Thursday's), his "usually two or three days in". A missing 15:45
    mark uses that session's last mark before it; none at all settles, counted.
- **Costs:** the suite's tastytrade schedule (`cherrypick.core.fees`), as item 19: $1.00 a contract
  to open, exchange and regulatory fees on every leg, no commission to close, $5 per in-the-money
  settlement symbol.
- **Measured:** each entry's net in dollars at one contract, and as a share of its debit.
- **Window and sample:**
  - **Design:** 2022-05-16 to 2024-12-31, about 270 entries (the first once 14 four-hour bars
    exist). Read for the verdict.
  - **Holdout:** 2025-01-01 to 2026-10-23, about 180 entries, with the same rules unchanged. At the
    user's request it is **run straight after the design whatever the design shows**. It counts
    toward a pass only after a design pass; after a design fail it is reported as exploratory and
    cannot overturn the fail.
  - Fewer than 100 entries in a window is no verdict.
- **Pass-or-fail rule**, per arm, all must hold:
  - mean net per entry above zero with a one-sided t above 2, one entry one observation;
  - Holm across the three arms;
  - each time-half of the design window positive;
  - still positive at **1.5× costs**;
  - the week-as-one-observation t above 2 as well;
  - then the holdout, with mean net above zero and a one-sided t above 2.
- **Minimum detectable effect (B12):** from the design window's own spread of entry nets, recorded in
  the local result. No comparable result exists to plan it from.
- **Declared limits (B11):**
  - a fly open at X's close settles at the SPX close in `SPX-60s.db`'s `daily_closes`, the vendor's
    official close from 2024-05-13 and its last underlying print before; the two are reported apart;
  - the path is fetched about 400 points either side of each session's spot; a leg outside it has no
    mark that session, so arm B cannot sell then, and arm C falls back as above;
  - entries skipped for missing quotes, bars or an unaffordable body are counted, by reason;
  - the VIX band uses D−1's close, and the moving average only spot printed by 15:45 on D.
- **Name split (B9):** does not apply; one symbol.
- **Robustness report beside a pass (B13), never used to re-choose:**
  - neighbours: the body at 5% and 7.5% of `w`; entry at 15:40 and 15:50; widths one band narrower
    and wider; arm B at 2× and 4×;
  - costs at 1.5× and 2×; the bootstrapped drawdown and losing streak; the largest size inside a
    declared 10% of the account, passed at run time.
- **Data:** `scripts/fetch_spx_convex_stack.py` writes `SPX-convex-stack.db`, declared in
  `stores.STORES`: each entry's 15:40-15:50 chain at one minute, and that expiry on every session to
  expiry at five minutes. The study reads only it, `SPX-60s.db`'s spot history and daily closes, and
  Cboe's VIX history. Results go under the backtest data directory only; this plan records the
  verdict.
- **Decides:**
  - **A pass:** a proposal for a paper arm beside the suite's loops, with the robustness report.
  - **A fail or no verdict:** closes this structure as specified.
- **Result (2026-10-27): fail, all three arms; the structure is closed.** In the design window no
  arm (hold, 3x the debit, two sessions in) has a positive mean net after costs or a positive
  time-half. The trend rule carries no signal: the opposite side's fly, scored at every entry, lost
  less than the trend side's in every arm but the 3x sale, where the gap is far from significant.
  The mid-price fills, reported only as context, pass the rule in no arm either. Every figure is in
  the local result
  (`~/.cherrypick/data/backtest/results/research/item20/study-2022-05-16-2024-12-31.json`).
- **Holdout, run straight after as declared; exploratory, not a verdict.** After a design fail it
  cannot overturn the fail. With the rules unchanged, all three arms fail every check there too,
  and the trend side again does no better than the opposite side. The holdout is now spent for this
  specification. Local result: `.../item20/study-2025-01-01-2026-10-23.json`.
- **Not tested here:** his 2025 "convexity stack" (both sides at 25-50 cents, 2-5 DTE, around
  events) and his "Sigma Drift" (5-10 DTE), which have no mechanical rules in the transcripts.

### 21. Legged SPX flies with a resting entry limit, with and without the range-high refusal

*Source: the user, 2026-10-27, after a screenshot of a trader legging XSP 0DTE flies: "Fills and
staying away from the edges of the range." Flies were retired on 2026-10-19 on stranded verticals;
the four-year design replay found completion rate a symptom and the price paid the lever, and tried
entry-credit filters but never a better entry price with fill realism. Declared here before any of
it is run.*

- **Question:** do flies' legged 0DTE SPX flies earn after costs if each entry rests above mid and
  fills only when the market comes to it, alone or with the `range-high` refusal?
- **Where the edge would live:** **execution**. A credit vertical sold richer than control's
  modelled fill starts each position with more credit to fund completion and to cushion a strand.
  Against it, a resting credit order fills when spot moves against completion: live's entries at mid
  − 0.05 filled 43 of 50, and the misses were the ones already working. The rule carries that
  adverse selection instead of assuming it away.
- **Look-alikes:** `cherrypick hypotheses --similar` finds no rejected row with this setup, side,
  structure and window. The nearest closed evidence is flies' own: the 2026-10-17 provisional design
  run (control lost every year, no entry feature or range position separated strands), the
  entry-credit filters and completion-limit sweeps the same day (none positive in either half), and
  the 10-09 and 10-14 range gate reviews (both-ends gates hurt; only the narrow-range high was a
  lead, the `range-high` arm, never read).
- **Rival explanations, each with its check:**
  - *Fewer trades, not better ones:* a limit that rarely fills only trades less. **Check:** the
    verdict is the arm's own net, not its gap to control; trades per session are reported beside it.
  - *Optimistic touch:* a mid that reaches the limit is not a fill. **Check:** the offsets run up to
    0.10 above mid, and the arm's paired difference against its base (control, or `range-high` for a
    twin) is reported.
  - *A calm span:* **Check:** both time-halves of each window must pass.
  - *The range gate alone:* **Check:** `range-high` runs as a reference beside the `-rh` twins.
  - *Fees:* **Check:** still positive at 1.5× fees.
- **The structure:** flies' `control` arm exactly (ATM centre, 5-wide, legged, 10:00-14:30 entries,
  completion at its own rule, held to settlement), changed only in how the entry fills
  (`engine.entry_limit_settings`):
  - the entry rests at the planned spread's **mid credit plus the offset**, floored to the tick;
  - it fills at that limit on the first **later** tick whose mid credit reaches it, within **120 s**
    (two vendor ticks), never on the tick it is placed;
  - unfilled, it lapses, the entry is re-planned, and no cadence slot was spent; while it rests the
    arm plans no other legged entry.
- **Six arms**, Holm-adjusted together: offsets **0.00, 0.05 and 0.10** on `control` (`limit-000`,
  `limit-005`, `limit-010`), and the same three on `range-high` (`limit-000-rh` and so on).
  `control` and `range-high` run beside them as references, never judged.
- **Costs:** flies' own model. Fees are the suite's tastytrade schedule and the settlement fee $5 per
  in-the-money strike. A limit fill concedes no slippage; completion keeps control's modelled fill.
- **Measured:** each session's summed net per arm, in dollars at one contract.
- **Window and sample:** the backtest's own windows over the 60 s vendor store (`SPX-60s.db`).
  - **Design:** 2022-05-16 to 2025-06-30, about 780 sessions.
  - **Holdout:** 2025-07-01 to 2026-10-12, about 320 sessions. At the user's request it is **run
    straight after the design whatever the design shows**. It counts toward a pass only after a
    design pass; after a design fail it is exploratory and cannot overturn the fail.
  - **Both runs are provisional.** Flies validation needs 10 sessions of paper's book beside the
    recorder, and paper stopped at five, so flies history cannot unlock. The user chose to run it
    anyway (2026-10-27). The holdout spends once per arm through the runner's
    `unvalidated_holdout`, added for this. Results from these runs carry the vendor data's own
    unchecked error.
  - Fewer than 100 sessions in a window is no verdict.
- **Pass-or-fail rule**, per arm, all must hold:
  - mean net per session above zero with a one-sided t above 2, one session one observation;
  - Holm across the six limit arms;
  - each time-half of the window positive;
  - still positive at **1.5× fees**;
  - then the holdout, with mean net above zero and a one-sided t above 2.
- **Declared limits (B11):** 60 s ticks see one sample where paper saw four (the cadence cost is
  measured in `validate`, not here); a fill is judged on mid, never on a print; a resting order
  left at the session's end opens nothing.
- **Name split (B9):** does not apply; one symbol.
- **Data:** no new store. `scripts/flies_entry_limit_item21.py` builds a scratch flies config from the
  machine's (the six arms added) and runs `cherrypick.backtest.runner` over each window. Results go
  under the backtest data directory only; this plan records the verdict.
- **Decides:**
  - **A pass:** a proposal to revive flies in paper with the passing arm, with the robustness report.
  - **A fail or no verdict:** legged 0DTE flies stay retired; entry price and the range gate are
    closed as levers.
- **Result (2026-10-27): fail, all six arms; legged flies stay retired.** In the design window no
  arm passes any check: every mean net is negative, in both time-halves and at 1.5× fees, and the
  `control` and `range-high` references lose as heavily. Resting at mid or mid + 0.05 does worse
  than the arm's base, significantly at mid: the orders that fill are the ones spot is moving away
  from completion, the adverse selection the rule was built to carry. Only the 0.10 offsets edge
  ahead of their bases, by far less than significance. Entry price and the range gate are closed as
  levers. Every figure is in the local result
  (`~/.cherrypick/data/backtest/results/research/item21/study-design-2022-05-16-2025-06-30.json`).
- **Holdout, run straight after as declared; exploratory, not a verdict.** After a design fail it
  cannot overturn the fail. With the rules unchanged, all six arms fail every check there too, in
  the same order: mid worst, 0.10 level with its base. The holdout is now spent for this
  specification. Local result: `.../item21/study-holdout-2025-07-01-2026-10-12.json`.

### 22. The overnight condor: a 1DTE SPX iron condor sold at the close, bought back the next morning

*Source: the suite's own evidence and a published anomaly. Item 19 bought far 1DTE wings across the
night from the same store and lost in both windows and both arms: the long side of the overnight
premium failed after costs. Jones and Shemesh ("Option Mispricing around Nontrading Periods",
Journal of Finance, 2018) report that options are priced on calendar time while variance over a
market closure is lower than over the same hours of trading, so writing options across nights, and
weekends above all, earns excess returns. The user approved the test, and a read of the holdout
whatever the design shows, on 2026-10-27. Declared here before any of it is run. No new fetch.*

- **Question:** does selling a 1DTE SPX iron condor in the last minutes of a session and buying it
  back at the next open earn after costs?
- **Where the edge would live:** the **premium**, in one specific place: the overnight share of a
  1DTE option's time value, priced as if the closure traded. Not a signal.
- **Look-alikes:** `cherrypick hypotheses --similar` finds no rejected row with this setup, side,
  structure and window. The nearest closed evidence is item 19 (the same night, the same store, the
  other side of the trade), item 9 (a 0DTE afternoon condor held to settlement, failed), item 2 (0DTE
  entry times) and MEIC's condors, all intraday. None sold across the closure.
- **Rival explanations, each with its check:**
  - *Drift:* the put side wins because the market rose overnight. **Check:** the call and put
    verticals are priced and reported apart; a result the call side cannot share is drift.
  - *A calm span:* **Check:** both time-halves of the design window must pass; results by VIX band
    (D−1's close) are reported.
  - *The tail:* a few gaps decide it. **Check:** the five worst sessions are reported with the
    verdict, and the bootstrapped drawdown beside a pass; the holdout holds 2025's April gaps.
  - *Fills:* 1DTE out-of-the-money SPX quotes are several ticks wide at the close. **Check:** the
    verdict is at the working price; mid and mid − $0.05 a structure are reported beside it.
  - *Fees:* four legs opened for an eighteen-hour hold. **Check:** still positive at 1.5× costs.
  - *The mechanism's own prediction:* if the closure is the premium's source, the weekend should pay
    at least the weeknight. **Check:** nets by closure kind (one night; a weekend or holiday) are
    reported. This is a description beside the verdict, never a filter.
- **The structure (declared by us, nothing tuned):**
  - SPX weekly options (SPXW) expiring the next trading day E; a Friday's E is the Monday.
  - Entry at **15:50 ET** on session D, from D's 15:50 one-minute quote.
  - Short call: the call above spot whose delta is nearest **+0.10**; short put: the put below spot
    whose delta is nearest **−0.10**, each by the vendor's delta at that quote. Each wing **25**
    points further out. One contract a side. All four legs must be quoted two-sided; a session
    missing a side is skipped and counted.
  - No stop, no adjustment.
- **Two exit arms**, Holm-adjusted together:
  - **A, buy back at the open:** the whole condor at E's **09:35** five-minute mark, the first mark
    to 09:50 when 09:35 has no quote for a leg; none by 09:50 settles. The mechanism's own arm.
  - **B, settle:** hold to E's official close. Whether the day adds to the night or takes from it.
- **Costs:** the suite's tastytrade schedule (`cherrypick.core.fees`), as item 19: $1.00 a contract
  to open, exchange and regulatory fees on every leg, no commission to close, $5 per in-the-money
  settlement symbol.
- **Measured:** the condor's net per session in dollars at one contract a side, and as a share of
  its credit.
- **Window and sample:**
  - **Design:** 2022-05-16 to 2024-12-31, about 660 sessions. Read for the verdict.
  - **Holdout:** 2025-01-01 to 2026-10-23, about 450 sessions, the same rules unchanged. At the
    user's request it is **run straight after the design whatever the design shows**. It counts
    toward a pass only after a design pass; after a design fail it is exploratory and cannot
    overturn the fail.
  - Fewer than 100 entered sessions in a window is no verdict.
- **Pass-or-fail rule**, per arm, all must hold:
  - mean net per session above zero with a one-sided t above 2, one session one observation;
  - Holm across the two arms;
  - each time-half of the design window positive;
  - still positive at **1.5× costs**;
  - then the holdout, with mean net above zero and a one-sided t above 2.
- **Minimum detectable effect (B12):** from the design window's own spread of session nets, recorded
  in the local result before the holdout is read. Item 19's session nets are the nearest comparable
  spread but are a long structure's; they are not used to plan this one.
- **Declared limits (B11):**
  - a condor settling at E's close uses the SPX close in `SPX-60s.db`'s `daily_closes`, the vendor's
    official close from 2024-05-13 and its last underlying print before; the two are reported apart;
  - the store's deltas are the vendor's; a strike without one is not a short candidate;
  - sessions skipped for missing quotes or a missing side are counted, by reason;
  - the VIX band uses D−1's close, known at entry; nothing reflects only today's state.
- **Name split (B9):** does not apply; one symbol.
- **Robustness report beside a pass (B13), never used to re-choose:**
  - neighbours: shorts at 0.05 and 0.15 delta; wings 15 and 50; entry at 15:45 and 15:55; the
    buy-back at 09:45 and 10:30;
  - costs at 1.5× and 2×; the bootstrapped drawdown and losing streak; the largest size inside a
    declared 10% of the account, passed at run time.
- **Data:** no new store. `scripts/overnight_condor_item22.py` reads `SPX-time-warp.db` (item 19's
  store, which holds every session's 15:45-15:55 next-expiry chain and that expiry's next session at
  five minutes), `SPX-60s.db`'s daily closes and Cboe's VIX history. Results go under the backtest
  data directory only; this plan records the verdict.
- **Decides:**
  - **A pass:** a proposal for a paper arm beside the suite's loops, with the robustness report. The
    account is an IRA, so the defined-risk condor is the only version worth proposing.
  - **A fail or no verdict:** closes the overnight 1DTE short as specified; with item 19 that closes
    both sides of the 1DTE overnight premium.
- **Result (2026-10-27): fail, both arms; the structure is closed.** In the design window neither
  the 09:35 buy-back nor the settled condor has a positive mean net after costs at the working
  price, and neither has a positive time-half. The mid-price fill, reported as context, puts the
  buy-back arm near zero: the overnight premium is about fair at mid, and the spread and fees take
  the rest. The closure split does not behave as the mechanism predicts: the weekend's sign is not
  the same in the two windows. Every figure is in the local result
  (`~/.cherrypick/data/backtest/results/research/item22/study-2022-05-16-2024-12-31.json`).
- **Holdout, run straight after as declared; exploratory, not a verdict.** After a design fail it
  cannot overturn the fail. With the rules unchanged the buy-back arm fails every check there too.
  The settled arm's mean is positive in the holdout but far from the t bar, and its closure split is
  the design window's reversed. The holdout is now spent for this specification. Local result:
  `.../item22/study-2025-01-01-2026-10-23.json`.
- **What this closes:** with item 19, both sides of the 1DTE overnight premium on SPX as specified.
  Idea B of the 2026-10-27 review (the same condor on RUT and NDX) is not fetched.

### 23. The short delta of MEIC's live rule: 12, 20, 25 and 30 against 16

*Source: the user, 2026-10-28: "what does allowing pm-200 to enter at 20 delta do to the expected
value?", then "does it make sense to test a range of deltas?" and "test with no floor, ceilings?".
No run has varied the delta of the
rule live trades: every MEIC backtest keeps `pm-200` at 16, and the 2026-10-18 condor study tried
only 12 and 16, at every half hour. Item 22 is taken by the overnight condor (PR #625). Declared
here before any of it is run.*

- **Question:** does a different short delta raise the expected value per session of the rule live
  trades, `pm-200` entering once a day 14:00–14:30?
- **Where the edge would live:** **premium**. A nearer short collects more credit, so fees take a
  smaller share and the 2× stop sits further away in dollars. Against it, a nearer short is tested
  more often in the last two hours, and stopped losses already run larger than the average win.
  Which wins is the question; nothing here assumes it.
- **Look-alikes:** the 10-18 condor study (16 delta held at net-2.0 in both halves, 12 did not, at
  every half hour 12:00–14:30, outside MEIC's own code); the income studies, where selling nearer the
  money found no edge after costs. Neither tested MEIC's own engine at 14:00 above 16.
- **Rival explanations, each with its check:**
  - *A gate, not the delta:* the credit floor (15% of width) would refuse far shorts and the call
    ceiling near ones. **Check:** the judged arms carry neither; a session with no entry still counts
    at zero in the pairing, and trades per arm are reported.
  - *A calm span:* **Check:** the paired difference must be positive in both time-halves.
  - *Fees:* **Check:** the arm's own net is still positive at 1.5× fees.
  - *Searching a grid:* **Check:** Holm across the four arms; the grid is fixed here and not
    extended after a look.
- **The structure:**
  - `live-1400` is the registry's `pm-200` (SPX, 10 wide, per-side stop at 2× the whole credit,
    30-minute spacing) with entries 14:00–14:30, so one condor a session: the rule live trades
    through its live block, its credit floor and call-delta ceiling included.
  - `d16` is `live-1400` with **no credit floor** (`min_credit_pct_of_width` and the low-IV relief
    floor both 0) and **no call-delta entry ceiling** (all three set to 1). A condor whose credit is
    not positive, or does not cover its opening fee, is still refused: that is structure, not a gate.
  - `d12`, `d20`, `d25` and `d30` are `d16` with `short_delta_target` changed. Nothing else differs.
  - `live-1400` and `control` are references, never judged. `d16` against `live-1400` is reported:
    it is what the two gates do at 16.
- **Costs:** MEIC paper's own model: fees from `core.fees`, stops filled under the replay's fill
  model, cash settlement at the store's close.
- **Measured:** each session's summed net per arm, in dollars at one condor.
- **Window and sample:** the backtest's own windows over the 60 s vendor store (`SPX-60s.db`).
  - **Design:** 2022-05-16 to 2025-06-30. **Holdout:** 2025-07-01 to 2026-10-12, run straight after
    the design whatever it shows (the user's choice, 2026-10-28). It counts toward a pass only after
    a design pass; after a design fail it is exploratory and cannot overturn the fail.
  - `pm-200-1400` was chosen with the holdout dates seen, so `live-1400`'s result there is not out
    of sample. The ungated arms have never run, so their paired differences there are.
  - **Both runs are provisional.** MEIC validation has not unlocked (row #16 is its verdict), and
    the user chose to run anyway. The holdout spends once per arm through the runner's
    `unvalidated_holdout`. Results carry the vendor data's own unchecked error.
  - Fewer than 100 sessions in a window is no verdict.
- **Pass-or-fail rule**, per grid arm, all must hold:
  - the paired difference against `d16` (one session one observation) has a mean above zero and a
    one-sided t above 2;
  - Holm across the four arms;
  - the paired difference positive in each time-half of the window;
  - the arm's own mean net above zero, and still positive at **1.5× fees**;
  - then the holdout: the paired difference with a mean above zero and a one-sided t above 2.
- **Declared limits (B11):** 60 s ticks; strikes by the replay's delta source; a stop fills under
  the fill model, not a recorded fill. VIX bands do not move the delta: every arm sets its own.
- **Name split (B9):** does not apply; one symbol.
- **Data:** no new store. `scripts/meic_delta_grid_item23.py` builds a scratch MEIC arm registry from
  the machine's (the six arms added) and runs `cherrypick.backtest.runner` over each window. Results
  go under the backtest data directory only; this plan records the verdict.
- **Decides:**
  - **A pass:** a proposal to move `live.arm` to the passing delta at a declared boundary, after MEIC
    validation unlocks, with the kill criteria re-derived from that arm's replay. The passing arm is
    ungated, so whether the floor and ceiling come with it is part of that proposal, read from
    `d16` against `live-1400`; it is not decided by this test.
  - **A fail or no verdict:** live stays at 16 delta, and the short delta is closed as a lever for
    the 14:00 rule.
- **Result (2026-10-28): fail, all four arms; live stays at 16 delta and the short delta is closed
  as a lever for the 14:00 rule.** Provisional, as declared. In the design window no arm's paired
  difference against `d16` clears the t bar or Holm. 12 delta trails 16 in both windows. 20, 25
  and 30 lean ahead in both time-halves of the design but far from significance, and the
  holdout, exploratory after a design fail, does not change that. Every arm's own net is positive
  and stays so at 1.5× fees, so the rule earns at each delta; it does not earn more away from 16.
  Item 24 therefore runs at 16. Every figure is in the local results
  (`~/.cherrypick/data/backtest/results/research/item23/study-design-2022-05-16-2025-06-30.json`
  and `study-holdout-2025-07-01-2026-10-12.json`).

### 24. The entry slot of MEIC's live rule: 12:00 to 13:30 against 14:00

*Source: the user, 2026-10-28, after the B5 slice report on MEIC paper `control` found entry hour the
one dimension that separates (12:00 and 13:00 entries ahead of 14:00, mornings well behind; paper
figures, in-sample, a single market regime). Hypothesis 2's held-out test. Declared here before any
of it is run, and run after item 23's verdict.*

- **Question:** does one condor a session entered earlier in the afternoon earn more than the same
  condor entered at 14:00, the slot live trades?
- **Where the edge would live:** **timing**. An earlier entry collects more premium and has more
  hours to decay, and also more hours for a move to reach a short and fire a stop. Mornings carry
  the open's tail; the slot report suggests the trade-off turns around midday.
- **Look-alikes:** the 10-14 entry-time study (36 sessions: 12:30 the best single slot); the
  pm-200 provisional replay's per-slot read, which chose 14:00 (13:30 the one slot positive in every
  span); the B5 slice report. They disagree on the best afternoon slot, which is this question.
- **Rival explanations, each with its check:**
  - *A gate, not the slot:* the credit floor binds harder as credit decays through the afternoon,
    and the call ceiling differs by session period. **Check:** every arm is ungated, as item 23's.
  - *A calm span:* **Check:** the paired difference must be positive in both time-halves; each
    arm's net by year and its worst session are reported.
  - *Fees:* **Check:** the slot's own net still positive at 1.5× fees.
  - *Searching a grid:* **Check:** Holm across the four slots; the grid is fixed here.
- **The structure:**
  - Every arm is the registry's `pm-200` (SPX, 10 wide, per-side stop at 2× the whole credit,
    30-minute spacing) with no credit floor (`min_credit_pct_of_width` and the low-IV relief floor
    both 0) and no call-delta entry ceiling (all three 1), entering in one 30-minute slot, so one
    condor a session.
  - `s1400` (14:00–14:30) is the base: item 23's `d16`, or its passing arm.
  - **Judged:** `s1200`, `s1230`, `s1300`, `s1330`, each slot 30 minutes from its name.
  - **References, never judged:** `s1000` and `s1100`, for hypothesis 2's afternoon-against-morning
    read, and `control`, which the runner requires.
  - **The delta is item 23's verdict:** 16 if item 23 fails or has no verdict; the passing arm's
    delta if one passes (the strongest by paired t if several do). The run is queued only once that
    verdict is recorded, and the delta it ran at is in its result file's name.
- **Costs:** MEIC paper's own model, as item 23.
- **Measured:** each session's summed net per arm, in dollars at one condor.
- **Window and sample:** the backtest's own windows over the 60 s vendor store (`SPX-60s.db`).
  - **Design:** 2022-05-16 to 2025-06-30. It decides.
  - **Holdout:** 2025-07-01 to 2026-10-12, **already seen for these slots.** `pm-200` entered at
    every one of them over the holdout dates, and its per-slot read there chose live's 14:00 (see
    "Provisional now" above). So the holdout run is a consistency check only: it is reported,
    and a passing slot whose paired mean there is not positive is reported as such, but it cannot
    make or overturn a verdict. Out-of-sample evidence for any slot comes from sessions after
    2026-10-12.
  - **Provisional**, as item 23: MEIC history has not unlocked.
  - Fewer than 100 sessions in a window is no verdict.
- **Pass-or-fail rule**, per judged slot, all must hold in the design window:
  - the paired difference against `s1400` (one session one observation) has a mean above zero and a
    one-sided t above 2;
  - Holm across the four slots;
  - the paired difference positive in each time-half;
  - the slot's own mean net above zero, and still positive at **1.5× fees**.
- **Declared limits (B11):** 60 s ticks; one entry per slot, at the first tick that clears; strikes
  by the replay's delta source; stops under the fill model.
- **Name split (B9):** does not apply; one symbol.
- **Data:** no new store. `scripts/meic_entry_slot_item24.py --delta D` builds a scratch MEIC arm
  registry from the machine's (seven arms added) and runs `cherrypick.backtest.runner` over each
  window. Results go under the backtest data directory only; this plan records the verdict.
- **Decides:**
  - **A pass:** a proposal to move live's entry window to the passing slot at a declared boundary,
    after MEIC validation unlocks and with the kill criteria re-derived, with fresh sessions after
    2026-10-12 read before the change.
  - **A fail or no verdict:** live stays at 14:00; the afternoon slot is closed as a lever.
    Hypothesis 2's morning read is recorded either way.
- **Result (2026-10-29): fail, all four slots; live stays at 14:00 and the afternoon slot is closed
  as a lever.** Provisional, as declared, at 16 delta (item 23's verdict). In the design window
  12:00, 12:30 and 13:00 all trail 14:00, none significantly; 12:00 and 13:00 lose on their own and
  12:30 does not survive 1.5× fees. 13:30 is the one slot ahead of 14:00, far short of the t bar
  and behind it in the second half, which matches the pm-200 replay's read of 13:30. The B5 paper
  read (12:00 and 13:00 ahead) does not hold over history. **Hypothesis 2's morning read:** both
  morning references trail 14:00, so mornings are worse than the afternoon. Every figure is in the
  local result
  (`~/.cherrypick/data/backtest/results/research/item24/study-design-2022-05-16-2025-06-30-d16.json`).
- **Holdout refused on its first run (2026-10-29), re-queued.** The runner refused the window
  because `control`, which runs beside every arm and is never judged, was spent there by item 23.
  The runner now exempts `control` from the spent check; the consistency check is reported here
  when it runs, and cannot change the verdict.
- **Holdout, reported (run 2026-10-29, recorded 2026-10-30):** a consistency check only. Two
  slots lean ahead of 14:00 there and two behind, and none is far from it: no slot's paired
  difference has a t near 1. Nothing in it argues against the design's fail. The figures are in
  `~/.cherrypick/data/backtest/results/research/item24/study-holdout-2025-07-01-2026-10-12-d16.json`.

### 25. The skate put's expiry and management: 14 days against the monthly, held, halved or rolled

*Source: the user, 2026-10-28: "for skate trades, would shorter dated CSP be better to use,
assuming no earnings cross the expiry?", then "shorter dated, letting them run to expiry or
assignment vs rolling?". The skate study (docs/skate-plan.md) measured how often a band strike
skates, not what a breach costs; its "Not in this plan" left that to the licensed data. This is
that test. Declared here before any of it is fetched or run.*

- **Question:** does a cash-secured put sold at the skate band earn more on a 14-day weekly than on
  the 45-60 day monthly, and does holding it, closing it at half the credit, or rolling it when
  tested change the answer?
- **Where the edge would live:** **premium**, harvested faster. The band holds the per-cycle skate
  rate near 89% at every expiry (the 2026-10-18 study), and a shorter option earns more model theta
  a day. Against it: more cycles pay more spread and fees and meet more gaps, and a weekly's strikes
  are thinner. Earnings favour the short expiry: a 14-day cycle crosses a report far less often than
  a 45-60 day one, so it spends less time idle. Rolling would live in **management**: payoff
  shaping, not a source of edge on its own.
- **Look-alikes:** the 10-19 short-put sweep and the 10-23 confirmation (16 delta 45-60 DTE closed
  at half credit: no tested edge; 28-35 DTE worse at every delta); the 10-20 strangle management
  test (rolling the untested side failed both halves; this rolls the tested side); the skate
  study's expiry read (7-14 days best on the model price, the monthly on a tradable bid).
- **7 days was screened out before declaring.** The skate quote runs of 2026-10-20 to 10-27 (broker
  quotes in regular hours, the fixed band's put pick, entry cost alone at a quarter of the bid-ask
  plus the opening fee, a lower bound) put the median annual cost at 3.2% of capital at 7 days
  with open interest of 100 or more (two thirds of picks above 2%), 1.0% at 14 days and 0.4% on the
  monthly. No put rule measured so far has shown more than about 2% a year of edge, so a 7-day arm
  starts behind; it is not run. The 14-day arm clears that bar.
- **Rival explanations, each with its check:**
  - *Drift, not premium:* a short put is long the stock. **Check:** every arm is measured as edge,
    net minus the delta-equivalent stock position over the same days, as the 10-19 sweep.
  - *A bull year:* the 10-23 confirmation's put edge was a 2025-26 effect. **Check:** 2022 must
    agree before a pass.
  - *Costs on more cycles:* **Check:** the arm's own edge still positive with a 3/8 spread
    concession.
  - *A few names:* **Check:** the paired difference is per name, and a sign count across names is
    reported beside the mean.
  - *Searching a grid:* **Check:** Holm across the five judged arms; the grid is fixed here.
- **The structure:** six arms, one put at a time per name.
  - **Strike:** the first listed put at or below the fixed band (close − 0.75 × the 10-session
    average of the high-low range over N sessions, N the trading days to the expiry; `technicals
    skate`'s band), on the entry day's close. The by-expiry band is not run.
  - **Expiry:** `w14`, the weekly 12-16 calendar days out; `m45`, the standard monthly 45-60 days
    out. An arm with no expiry in its range that day waits.
  - **Earnings:** a stock whose report falls on or before the expiry is not entered; a report date
    that is unknown is not "clear". Funds are exempt. Dates from Dolt's `earnings_calendar`.
  - **Management**, crossed with both expiries:
    - `hold`: the educator's buy-to-close at $0.03 resting from the fill, filled on the first close
      whose ask is at or under it; otherwise held to expiry or assignment.
    - `tp50`: bought back at half the credit, as the 10-19 sweep's `tp50`.
    - `roll`: when a close is at or below the strike, the educator's net-zero roll: buy it back and
      sell the same arm's next expiry, at the highest strike at or below the old one whose bid
      covers the buyback. No such strike, a report inside the new expiry, or two rolls already made:
      no roll, and the put runs to expiry. A rolled position is one trade, its cash summed.
  - **Assignment:** settled at the expiry close's intrinsic value with the $5 settlement fee; the
    shares are not kept. The wheel's calls are not part of this test.
  - **Base:** `m45-tp50`, the short-put list's rule at the band strike. **Judged:** `w14-hold`,
    `w14-tp50`, `w14-roll`, `m45-hold`, `m45-roll`.
- **Costs:** fills at mid conceding a quarter of the bid-ask, the broker's fees from `core.fees`,
  $5 per in-the-money settlement. Early assignment is not modelled (an upper bound).
- **Measured:** per name and arm, edge as a percentage of the strike's capital a year, over the
  whole window, idle days included, so an arm that sits out earnings pays for it.
- **Names:** the 56 names of the 10-23 confirmation, all from the universe. A name is in a
  window's run only if its weeklies are listed on at least 90% of that window's weeks; a name that
  fails is dropped from every arm, so the pairing holds. **Judged:** the stocks. **References, never
  judged:** the funds (SPY, QQQ, IWM, GLD, XLE, SLV), reported per name, SPY first: if the skate
  stocks cannot beat the index put, the band adds nothing.
- **Window and sample:** the vendor's end-of-day daily paths (`eod_chains.db`), the confirmation's
  two windows.
  - **Design:** entries 2025-10-01 to 2026-10-16. It decides.
  - **Holdout:** entries in 2022, run straight after the design. It counts toward a pass only after a
    design pass, and cannot overturn a design fail.
  - The monthly arms are near the confirmation's 16-delta rule (band strikes sit near 0.15 delta),
    so `m45` is partly seen in both windows. The weekly arms are not.
  - Fewer than 30 judged names or 300 trades in an arm in a window is no verdict.
- **Pass-or-fail rule**, per judged arm, all must hold in the design window:
  - the per-name paired difference against `m45-tp50` has a mean above zero and a one-sided t above
    2, with Holm across the five arms;
  - the paired difference positive on more than half the names;
  - the arm's own median edge above zero, and still positive at a 3/8 spread concession;
  - then the holdout: the paired mean above zero and the arm's own median edge above zero.
- **Declared limits (B11):** daily closes only, so the $0.03 buy-back, half credit and the tested
  test are read once a day, and a gap through the strike inside a day is seen at the close; the
  store has no volume, so open interest of 100 cannot be checked, and a leg whose quote fails
  `core.legbars`' spread bar on the entry day is skipped; early assignment not modelled; the band is
  our reconstruction of the educator's unpublished one.
- **Name split (B9):** design and holdout cover the same names; the funds are reported apart.
- **Data:** a weekly fetch on top of the confirmation's monthly paths. A request file (each name,
  each weekly expiry in both windows, its path from 18 days before) goes through `python -m
  cherrypick.backtest fetch-chain-paths` as a research-queue row (`item25/fetch-chain-paths`), and
  the replay as a committed script after it (`item25/study`). Results go under the backtest data
  directory only; this plan records the verdict.
- **Decides:**
  - **A pass:** the Skate tab's best-expiry pick and the wheel view's `ROLL_OR_CLOSE` suggestion
    follow the passing arm, a read-surface change, and the short-put list's rule is re-read against
    it.
  - **A fail or no verdict:** the monthly stays the skate default, the tab's per-day ranking gets a
    note that it is not a tested edge, and rolling a tested put is recorded as payoff shaping that
    did not pay.
- **Result (2026-10-29): no verdict, all five arms; the monthly stays the skate default.** The base,
  `m45-tp50`, falls under the 300-trade floor in the design window: a 45-60 day put crosses a report
  on most entry days, so the earnings rule leaves it few trades. Without the floor the answer would
  not change: no arm's paired difference comes near the t bar in the design. In the holdout, which
  counts only after a design pass, the weekly arms trail the monthly and the two other monthly arms
  lean ahead on too few trades. The Skate tab's best-expiry ranking now says it is not a tested
  edge, the wheel view's `ROLL_OR_CLOSE` is unchanged, and rolling a tested put is recorded as
  payoff shaping that did not pay. Every figure is in the local result
  (`~/.cherrypick/data/backtest/results/research/item25/results.json`).
- **Clarified 2026-10-28, while building the replay, before any fetch or run**
  (`scripts/skate_expiry_item25.py`). Where the declaration was silent:
  - **The roll arm keeps the $0.03 buy-back.** The educator places it at the fill and rolls only
    when tested, so `roll` is `hold` plus the roll. A roll's new leg must pass the same spread bar.
  - **Unknown report dates.** A stock is clear only when no report falls in the span and Dolt shows
    the quarter around it: the reports either side no more than 120 days apart, or, when the next
    is not listed yet, the last one under 80 days before the expiry. Anything else is unknown, and
    not entered.
  - **Where report dates come from.** Dolt's `earnings_calendar`, under a name's old ticker too
    (META's reports before 2022-07 are under FB). Dolt lists nothing at all on 37 trading days since
    2021-06, among them 2022-01-24 to 01-28, the week big tech reported, so those days alone come
    from Nasdaq's public calendar (`fetch_earnings_times.fetch_day`). The dates are cached once by
    the script's `reports` step, and the study reads only that file. What stays unknown: COST's
    March reports, which neither lists by date, and one gap each for PEP, GS and MS.
  - **Splits.** An entry or roll whose span holds a split is not taken. The band is figured on
    split-adjusted bars as a ratio to the close and applied to the day's own close, so the strike is
    on the chain's share basis.
  - **Run ends.** Entries stop at each window's end, and positions run on to the last session the
    monthly paths reach: 2026-10-22 for the design, 2023-02-17 for the holdout. A position still
    open then is marked at its working buy-back.
  - **What is fetched.** Only the weeklies the store does not hold, each from 18 days before its
    expiry, plus the March 2023 monthly a late 2022 roll can reach. Fridays only, so `w14` enters
    Wednesday to Friday: a Monday or Tuesday is 11 or 18 days from a Friday.
  - **Weekly coverage** is the share of a window's non-monthly Fridays whose path came back
    complete.

### 26. A dip-buying share ladder on leveraged funds: buy 7% under the previous close, sell each lot 4% up

*Source: the user, 2026-10-29: members of a trading group buy SOXL shares after a 7% drop and sell
after a 4% gain, sometimes holding several tranches; the user trades it by hand. Then: "I think it
is from the previous day's close. I think they hold until the 4%", "let's not limit this to just
one symbol", "could probably add TECL". **The user builds the module whatever this shows**
(2026-10-29); the verdict sets how it is sized and armed, not whether it exists. Declared here
before any of it is fetched or run.*

- **Question:** does buying a leveraged fund's shares 7% under the latest 4:00 pm close, and
  selling each lot on its own at 4% over its cost, earn more than the same exposure taken at random
  times?
- **Where the edge would live:** **signal**: short-term mean reversion after a large down move in a
  daily-reset fund. Against it: a 3x fund's own path decay, and a hold-until-up rule with no stop
  that keeps every loss open. A win rate near 100% on closed lots says nothing; the open lots carry
  the result.
- **Look-alikes:** none in this plan trades shares. The contango module (SVXY/SHV) is the nearest:
  a fund held on a rule, where the replay found the gate bought drawdown, not return.
- **Rival explanations, each with its check:**
  - *Long a bull market, not a signal:* any long rule in SOXL wins since 2023. **Check:** every arm
    is judged as edge over a random-entry control: the same number of entries a year on sessions
    drawn at random, filled at that session's open, with the same exit, cap and budget, averaged over
    200 draws.
  - *Leverage, not the trigger:* **Check:** the same rules on each fund's 1x counterpart (SOXL →
    SOXX, TECL → XLK, TQQQ → QQQ, and so on where one exists), reported beside the fund, never
    judged.
  - *A few names:* **Check:** a sign count across names beside the mean.
  - *One era:* **Check:** both time-halves of the design must agree, and the holdout holds 2020 and
    2022.
  - *Costs:* **Check:** still positive at three times the declared cost.
  - *Searching a grid:* **Check:** Holm across the ten judged arms; the grid is fixed here.
- **The structure:** one budget per name, `cap × tranche`; whole shares; no stop, no time limit.
  - **Entry trigger:** a price at or under 93% of the latest 4:00 pm close: the previous session's
    before 4:00 pm, the same session's after it (the user's 10/29 buy at 16:06 was 12% under that
    day's own close). The second-tranche rule is not settled (the user's 10/22 buys were 11%
    apart), so it is an arm:
    - `once`: at most one new lot a session;
    - `chain`: a further lot at each 7% under the last lot bought, in the same session or later;
    - `steps`: lots at 7%, 14%, 21% … under the reference close, one per step reached.
  - **Exit:** each lot sells at 104% of its own cost.
  - **Cap:** at most 1, 3 or 5 lots open per name. An entry with the cap full is skipped, not
    queued.
  - **Arms:** the nine `once/chain/steps × 1/3/5`, plus `scaled`: `once`, cap 3, with the drop at
    1.5 and the gain at 0.9 times the fund's 20-session standard deviation of daily returns (about
    7% and 4% for SOXL at its long-run volatility), so a calmer fund is asked for a move of its own
    size. All ten are judged. No limit on lots is reported, not judged: its budget is not defined.
- **Fills on daily bars:** a resting buy at the trigger fills at the open if the session opens
  under it, otherwise at the trigger if the low reaches it. A resting sell fills at the open if the
  session opens over it, otherwise at the target if the high reaches it. A lot bought in a session
  is never sold in that session (the worse order where daily bars cannot tell). Lots open at a
  window's end are marked at the last close.
- **Costs:** 0.05% of the price a side, for spread, slippage and the regulatory fees on sales; the
  broker charges no commission on these shares. Checked at 0.15%.
- **Measured:** per name and arm, net as a percentage of the name's budget a year, idle days
  included; edge is that less the random-entry control's mean. Reported beside it, never judged:
  the drawdown with open lots marked to market, the longest a lot stayed open, the most capital
  tied up, buy-and-hold, and the 1x counterpart.
- **Names:** every `leveraged` (long, not inverse or volatility) fund in `technicals`'
  `leveraged_funds.json` with daily bars from 2011-01-03 and a median daily dollar volume of $20M or
  more in the broker-sourced dollar-volume file of 2026-10-29 (`scripts/fetch_dollar_volume.py`,
  the 50-session median the liquidity verdict reads). **Judged:** all of them. SOXL, TECL and TQQQ, the
  names the user trades or asked for, are reported first. Single-stock funds start after 2022, so
  they fall out by rule. A fund closed before 2026 is not in the list, so the sample is survivors
  only, an upper bound.
- **Window and sample:** daily bars, the broker's first (the user's rule), Yahoo's where the broker
  has none.
  - **Which bar a session takes:** the broker's regular-hours daily candle, unless it is missing or
    its close is more than 1% from Yahoo's, then Yahoo's. Both are split-adjusted, but the broker's
    close on the session before a split is not (SOXX 2024-03-06, TQQQ 2025-11-19, XLK 2025-12-04),
    and its feed lacked 2026-10-28 when first fetched. The broker's candles start 2022-12-29, so the
    design window is Yahoo's alone and the holdout mostly the broker's.
  - **Design:** sessions 2011-01-03 to 2019-12-31. It decides.
  - **Holdout:** sessions 2020-01-02 to 2026-10-28, run straight after the design. It counts toward
    a pass only after a design pass, and cannot overturn a design fail.
  - Fewer than 8 judged names, or 300 closed lots in an arm in a window, is no verdict.
- **Pass-or-fail rule**, per judged arm, all must hold in the design window:
  - the per-name edge has a mean above zero and a one-sided t above 2, with Holm across the ten
    arms;
  - edge positive on more than half the names;
  - mean edge positive in both time-halves (2011-2015, 2016-2019);
  - mean edge still positive at the 0.15% cost;
  - then the holdout: mean edge above zero and positive on more than half the names.
- **Declared limits (B11):** daily bars are the regular session, so a drop or a rise seen only
  before 9:30 or after 4:00 is missed, and the open stands in for the pre-market. **Checked
  separately, reported, never judged:** hourly bars with extended hours for SOXL, TECL and TQQQ,
  the broker's from 2026-01 and Yahoo's from 2023-12 before that, cut to 8:00-20:00 ET
  (tastytrade's equity day is 8:15-20:00; SOXL may not trade overnight there,
  `overnight-trading-permitted` false on 2026-10-29), replayed with the same rules against the
  daily replay over the same span. Yahoo's extended-hours bars carry bad prints (SOXL's 10-29 16:00
  bar: a high of 123 and a low of 68.5 inside a 102-to-89 move), so outside 9:30-16:00 a Yahoo
  bar's high and low are clipped to 3% beyond its open and close. Also: the user's five SOXL fills
  of 10/22-10/29 must come out of a replay on the broker's 5-minute bars as trades, at or inside
  each bar's range. Survivors only, as above.
- **Name split (B9):** design and holdout cover the same names.
- **Data:** fetched by a committed script (`scripts/fetch_ladder_bars.py`) into
  `~/.cherrypick/data/research/item26/`, never the repo: the broker's DXLink candles (daily,
  regular hours, for every candidate and 1x counterpart; hourly and 5-minute with extended hours
  for the three names) and Yahoo's public bars for what the broker does not reach (daily from each
  fund's start with split events; hourly from 2023-12). The replay is
  `scripts/share_ladder_item26.py`, a pure function of those files, run as research-queue rows
  (`item26/fetch-bars`, `item26/study`). Results stay under that directory; this plan records the
  verdict.
- **Decides** (the module is built either way, by the user's choice):
  - **A pass:** the paper module's default arm follows the passing arm, and the live pilot's sizing
    is proposed from its drawdown and longest open lot.
  - **A fail or no verdict:** the module is still built, paper first. Its console tab says the rule
    is not a tested edge. The live pilot's sizing is proposed from the measured drawdown alone, and
    the user decides whether to arm it.
- **Clarified 2026-10-29, while building the replay, before the full run**
  (`scripts/share_ladder_item26.py`; one trial on SOXL alone, from a scratch store, to time it).
  Where the declaration was silent:
  - **A lot is $10,000** in whole shares, so a name's budget is $10,000 times its cap.
  - **`chain` with lots open** steps only from the last lot bought; a new session's 7% under the
    previous close does not open a lot while one is held. With none held, it starts from the
    previous close again.
  - **A slot sold in a session frees the next session**, and a cap that is full skips the entry.
    Daily bars cannot tell which came first, so the replay takes the worse order.
  - **The halves are their own runs**, each starting with no lots; so is the cost check, on the
    design window.
  - **The control's draws** are sessions with a previous close in the window (for `scaled`, with
    20 returns behind them), one lot at most a session, as many as the arm opened. A draw whose cap
    is full is skipped, as the arm's would be.
  - **The 1x counterpart** runs every arm's rules and is reported as its own % a year, without a
    control.
  - **Run** as a research-queue row (`item26/study`) after the fetch; about 5 minutes a name.
- **Clarified 2026-10-29, before the full run: one fund per bet.** Two funds on the same index at
  the same leverage move as one (UPRO and SPXL, 3x the S&P 500; TNA and URTY, 3x the Russell
  2000; MIDU and UMDD, 3x mid-caps), so counting both would inflate the name count behind the t
  and the sign count. The judged names keep one fund per 1x counterpart and leverage
  (`fetch_ladder_bars.LEVERAGE`), the one with the larger median dollar volume; the dropped funds
  are listed in the result. A different leverage on the same index (SSO beside SPXL, QLD beside
  TQQQ) stays a separate name. The user asked about UPRO and SPXL, which raised it; nothing had
  run. This applies to item 27 too, which reads item 26's names.
- **Result (2026-10-30): fail, every arm, in the design window; the holdout is reported, not
  judged.** On 17 names (UPRO and URTY dropped as the same bets as SPXL and TNA), each arm's mean
  edge over the random-entry control is small and positive (except `chain-5` and `scaled-3`), and
  positive on most names. But the t is under 1 for every arm, far from the bar, and Holm passes
  none. For most arms that is the only check that fails; `once-1`, `chain-1` and `steps-1` also have
  a negative first half, and `scaled-3` fails throughout.
  - **By name:** the equity-index funds (semiconductors, technology, Nasdaq, S&P, Dow) carry the
    positive edge. The commodity, gold-miner, oil and China funds carry the negative.
  - **Rivals:** holding the fund beats the arm on most names in the design window. The same rules
    on the 1x counterparts make little, so what the ladder earns comes mostly from the leverage,
    not the trigger.
  - **The holdout, reported:** the mean edge stays small and positive on about three names in five.
  - **Intraday, reported:** on hourly bars the `once-3` replay makes a few points a year less than
    the daily replay over the same span on SOXL, TECL and TQQQ, so the daily fills read somewhat
    high. The user's own six SOXL fills each sit inside the broker's 5-minute bar, every buy under
    the trigger, every sell matching a lot at its target.
  - Every figure is in the local results (`~/.cherrypick/data/research/item26/results.json`,
    `intraday.json`).
- **What this decides:** the fail branch. The module is still built, paper first; its console tab
  says the rule is not a tested edge. The live pilot is sized from the measured drawdown, and the
  user decides whether to arm it. The defaults lean to the equity-index funds, where the edge sign
  was positive, though that is a reading of a failed test, not a pass.

### 27. Getting the share ladder out of multi-year holds: averaging down, a decaying target, a time stop, a trend check, a confirmation

*Source: the user, 2026-10-29: "I would like you to speculate on ways to avoid having to hold for
such a long period", then "yes" to declaring the likeliest. Declared before item 26's verdict.
**Seen before declaring:** item 26's timing trial on SOXL alone (scratch store) and its per-lot
slices. Every lot held over a year was bought early in a long semis fall (2011, 2022, 2024), and
lots bought on a 15%+ drop or a gap-down open made the least, with intervals spanning zero. Those
cells motivated `confirm`; they are in-sample and one name.*

- **Question:** does a change to how the ladder adds or exits lots shorten its longest holds
  without costing more than 2 points a year of return?
- **Where the edge would live:** **management**, payoff shaping, not a new source of edge. Long
  holds come from lots bought early in a long fall, and a 3x fund needs a larger rebound than its
  index to get back to cost. Each arm attacks one of those: buying less early in a fall, or needing
  less of a rebound to exit.
- **Look-alikes:** the 10-20 strangle management test (stops failed both halves); the pmcc roll
  policy (lost to holding); the 10-19 covered-call study.
- **Base, fixed now:** item 26's `once-3`, the arm nearest the user's own trading. It is the base
  whatever item 26 finds, so its verdict cannot steer the comparison.
- **The arms**, each `once-3` with one change, unless stated:
  - `avg-5`: averaging down. The first lot as `once`; while a position is open, a further lot at
    each 7% under the last lot bought, up to 5 lots; all of the position sells together at 104% of
    its average cost. Its budget is 5 lots.
  - `decay`: each lot's target is 104% of cost for its first 30 calendar days, its cost plus the
    round-trip cost (breakeven) from day 31, and 95% of cost from day 121.
  - `avg-5-decay`: `avg-5`, with the blended target decaying on the same schedule from the
    position's first lot.
  - `time-120`: a lot still open 120 calendar days after its purchase sells at the next session's
    open. A bound on `decay`, not a proposal.
  - `trend`: no new lot while the 1x counterpart's previous close is under its 200-session
    average. Run only on names with a counterpart.
  - `confirm`: a session reaching the 7% trigger arms a signal; the lot is bought at the open of
    the session after the first one that closes up, within 5 sessions, or the signal lapses.
- **Measured**, per name and arm, in each window: the longest hold in days and the 90th
  percentile; capital-days tied up per budget; the share of sessions with the cap full; the
  drawdown with open lots marked to market; % of budget a year; lots opened and closed. Each
  against `once-3` on the same name, as paired differences.
- **Rival explanations, each with its check:**
  - *Shorter holds by selling losers:* that is how `decay` and `time-120` work. **Check:** the
    return condition below; realised losses are reported.
  - *Fewer lots, not better ones* (`trend`, `confirm`): **Check:** lots opened reported beside
    every figure.
  - *More capital at risk* (`avg-5`): **Check:** drawdown as % of its own larger budget, and peak
    capital committed, reported.
  - *One era:* **Check:** both design halves must agree.
  - *Searching a grid:* **Check:** Holm across the six arms; the schedule (30/120 days, 95%,
    5 lots) is fixed here.
- **Pass-or-fail rule**, per arm, all must hold in the design window:
  - the per-name log ratio of the longest hold to `once-3`'s has a mean below zero with a one-sided
    t under −2, Holm across the six arms, and is below zero on more than half the names;
  - it is below zero in both halves (2011-2015, 2016-2019);
  - the per-name paired difference in % a year has a mean no lower than −2 points, at 0.05% and at
    0.15% a side;
  - the mean paired difference in drawdown is no worse than zero;
  - then the holdout: the mean log ratio of the longest hold below zero and the return condition
    holding.
- **Names, windows, costs, fills:** item 26's judged names, windows, costs and daily-bar fill
  rules. An exit at the open (`time-120`, a `decay` step reached by a gap) fills at the open.
  Fewer than 8 names, or 300 closed lots in the arm or the base, in a window is no verdict.
- **Declared limits (B11):** daily bars only; `avg-5` takes on more exposure at the bottom of a
  fall, and a fund that never recovers takes all of it; the decay schedule is one guess, not a
  search.
- **Data:** item 26's `bars.db`; nothing new is fetched. A script importing item 26's replay
  (`scripts/share_ladder_item27.py`), run as `item27/study` after `item26/study`.
- **Not in this test, with why** (kept for a later item if this one does not solve it):
  - *Exit when the 1x fund is up X% since the lot's entry,* sidestepping the 3x fund's decay. Not
    yet: it changes what the ladder sells for. It is a different rule, not a management of this
    one, and needs its own declaration.
  - *Spread across funds that do not fall together* (semis, gold miners, energy, bonds). Not yet:
    it does not shorten a hold, only how much of the budget one stuck group freezes, and the
    planned correlated-group cap already covers part of it. Best tested as a portfolio item once
    the module's caps are set.
  - *Selling calls against stuck lots* to lower their break-even. Not yet: a contract needs 100
    shares, beyond the user's lot size (about $1,000, 2026-10-29), and the 10-19 covered-call study
    found the overlay lost against holding on most names.
- **Decides:**
  - **A pass:** the passing arm with the shortest longest hold becomes the `ladder` module's
    default, paper first, and the live pilot's caps are proposed from its drawdown and longest
    hold.
  - **A fail or no verdict:** the module keeps item 26's rule. Its console tab shows each open
    lot's age and the capital it holds. A management rule chosen by hand is labelled untested.
- **Clarified 2026-10-29, while building the replay, before the full run**
  (`scripts/share_ladder_item27.py`; one trial on SOXL, TECL and TQQQ from a scratch store).
  Where the declaration was silent:
  - **The base is item 26's own `once-3`.** A test holds this script's base to item 26's replay,
    lot for lot.
  - **`confirm`'s five sessions start with the trigger session.** A signal is not re-armed while
    one is open or a buy is scheduled.
  - **`trend` takes no lot until the counterpart has 200 sessions** of closes behind the session.
  - **`avg-5`'s exit** is read on the position held at the session's start, and a session that
    sells the position opens no new one. Its "losses realised" count lots sold under their own
    cost inside a position sold whole at a gain, so they are reported, not read as losing trades.
  - **No random-entry control:** every arm is paired against the base, as declared.
- **Result (2026-10-30): `decay` passes; the other five fail.** On item 26's 17 names:
  - **`decay`** meets every check in the design window and both in the holdout. The longest hold is
    shorter on most names and in both halves, return gives up less than a point a year at both
    costs, and drawdown is no worse. The gain is modest: the median name's longest hold falls by
    about a sixth, and lots still wait over a year on the hard names.
  - **`confirm`** fails only on the t of the shorter hold. Its return and drawdown are both better
    than the base in the design window and the holdout. Promising, but not shown.
  - **`avg-5`, `avg-5-decay`, `time-120`** shorten holds clearly, but pay for it: return falls
    beyond the 2-point margin or drawdown worsens (`time-120` sharply).
  - **`trend`** shortens holds on too few names, and its return falls beyond the margin in the
    design window.
  - Every figure is in the local result (`~/.cherrypick/data/research/item27/results.json`).
- **What this decides:** the pass branch for `decay`. It becomes the `ladder` module's default exit,
  paper first, and the live pilot's caps are proposed from its drawdown and longest hold. Item 26
  failed, so this is the better way to run an untested rule, not an edge.

### 28. The share ladder before 2011 and on single stocks: daily-reset funds rebuilt from their 1x history

*Source: the user, 2026-10-29, asking which other symbols fit the ladder, then "yes" to rebuilding
the funds' history. Item 26 starts in 2011, so it never sees 2000-02 or 2008, the falls that test
"hold until +4%" hardest, and its history rule leaves out every single-stock fund (all listed
2022 or later). Declared before item 26's or 27's verdict; only their trials (SOXL, TECL, TQQQ,
2011 on) have been seen.*

- **Question:** do item 26's arms keep their edge over random entries, and what do their holds and
  drawdowns look like, on leveraged funds rebuilt from their 1x history back to 1999, through
  2000-02 and 2008? And on single-stock 2x funds rebuilt from their stocks?
- **Where the edge would live:** item 26's: **signal**, mean reversion after a large fall. This
  item adds no new arm. It is a further holdout, with the two worst falls in the data, and a risk
  read for setting caps.
- **The rebuilt fund.** Each session, the fund's return is the leverage times the 1x fund's
  close-to-close total return (Yahoo's adjusted close, so dividends are in), less the fund's
  expense ratio and the leverage less one times the 3-month Treasury bill rate, each a year over
  252 sessions. The open, high and low are the previous rebuilt close moved by the leverage times
  the 1x fund's own move from its previous close, which is exact for a fund that resets daily and
  trades with its index. The 3-month rate is Treasury's daily par yield curve (the source
  `scripts/fetch_market_files.py` already reads), back to 1999. Expense ratio: 0.95% a year,
  every rebuilt fund.
- **Validation first, per name.** Over the years the real fund and the rebuilt one overlap, daily
  returns must correlate at 0.99 or more and the annual returns must differ by 3 points or less
  on average. A name that fails is reported and not used.
- **Names.**
  - **Funds:** item 26's judged names with a 1x counterpart in `fetch_ladder_bars.COUNTERPART`,
    each rebuilt from its counterpart's first bar at the fund's leverage today
    (`fetch_ladder_bars.LEVERAGE`). Item 26 already keeps one fund per counterpart and leverage, so
    no two rebuilt series are the same (UPRO and SPXL would both be 3x SPY). A fund whose leverage
    changed (ERX and NUGT went from 3x to 2x in 2020) is validated only on sessions since the
    change.
  - **Single stocks, reported apart and never pooled with the funds:** every single-stock long 2x
    fund in `leveraged_funds.json` at $20M or more of median dollar volume on the broker file of
    2026-10-29, rebuilt from its stock. One stock counts once, however many funds track it (the
    most traded fund is the one validated).
    Validated over the fund's own short life.
- **Windows:**
  - **Pre-2011 holdout:** sessions from the rebuilt series' first (1999 at the earliest, once 20
    sessions of history stand behind it) to 2010-12-31.
  - **Stress reads, reported:** 2000-03-01 to 2002-12-31 and 2007-10-01 to 2009-12-31.
  - **Single stocks:** from 2000-01-03, or the stock's listing, to 2026-10-28.
- **Measured:** item 26's measures per name and arm (edge over the random-entry control, % a year,
  longest hold, drawdown with open lots marked, lots never closed by the window's end), plus item
  27's longest hold and capital share for its arms, on the same rebuilt bars.
- **Rival explanations, each with its check:**
  - *The rebuild flatters the fund:* the validation above, and a rebuilt series that closes a
    window richer than the real fund is reported as such.
  - *Survivors only:* rebuilding from the 1x fund removes the closure problem for the index funds;
    the single stocks are today's survivors, said so in the report.
  - *One era:* the two stress windows are read apart.
- **Pass-or-fail rule**, only for an item 26 arm that passed item 26: in the pre-2011 holdout, the
  mean edge across the funds is above zero and positive on more than half of them. An arm that
  failed item 26 is only reported here. Fewer than 8 funds or 300 closed lots is no verdict.
- **Declared limits (B11):** the rebuild has no tracking error beyond fees and financing, no swap
  spread and no intraday rebalancing effects; daily bars only; 0.95% is one fee for every fund.
- **Data:** daily bars and adjusted closes for the 1x funds and stocks (the broker's where it
  reaches, Yahoo's before), and Treasury's yearly yield-curve files, fetched by script into
  `~/.cherrypick/data/research/item28/`. A replay importing item 26's and 27's
  (`scripts/share_ladder_item28.py`), run as `item28/fetch` and `item28/study`.
- **Decides:**
  - **A pass:** the arm stays the module's default, and the module's caps and default names are
    proposed from the worst pre-2011 drawdown and longest hold.
  - **A fail:** item 26's pass is recorded as a post-2011 effect. The module's console tab says so,
    and the live pilot is sized from the pre-2011 drawdown.
  - **Either way:** a name whose rebuilt history holds a lot open over 5 years, or a drawdown over
    90% of its budget, is left off the module's default list, kept only by the user's choice.
    Single stocks join the default list only if they meet that and their edge is above zero across
    the whole span.
- **Result (2026-10-30): no verdict; no arm passed item 26, so every arm is reported.** The fetch and
  replay are `scripts/share_ladder_item28.py` (`item28/fetch`, `item28/study`).
  - **Validation:** 11 of the 17 funds pass. AGQ, FAS, TECL, UCO, USD and YINN fail, on correlation
    or the annual difference. TECL's miss sits mostly in 2009, before it moved to the index the 1x
    fund tracks. It stays out as declared. ERX's fit from 2020-04-01 confirms the change date. Most
    rebuilt series end the overlap richer than the real fund (financing and swap costs above the
    bill rate), so the rebuild flatters, if anything.
  - **The pre-2011 holdout, reported:** most arms' mean edge is negative and positive on a minority of
    funds. The `chain` arms at caps 3 and 5 are the exception, positive on most. The 2007-09 read is
    negative for nearly every arm, sharply so for most; the 2000-02 read is mixed.
  - **Holds:** a lot bought in the 2000-02 fall on rebuilt SOXL, TQQQ and others is still open in
    2026. Over the whole rebuilt span, `once-3` holds a lot over five years or draws down over 90% of
    its budget on 10 of the 11 funds. SSO is the one fund left on the default list.
  - **Single stocks:** 32 of 40 fail validation, almost all on the annual difference. A 3-point
    annual bar is tight for stocks with 100%-plus years, where a small daily shortfall in the fund
    compounds into many points. The eight left are reported apart. On `once-3`, CBRS and NOW meet
    the default-list rule. All of them are today's survivors.
  - Every figure is in the local result (`~/.cherrypick/data/research/item28/results.json`).
- **What this decides:** the "either way" branch. Under the declared rule, the module's default list
  holds SSO alone among the funds. Every other fund, SOXL and TQQQ included, joins only by the
  user's choice, with the console tab showing its rebuilt worst hold and drawdown. Caps are proposed
  from the pre-2011 drawdowns, not the post-2011 ones.

### 29. The share ladder in options: a call debit spread, a put credit spread or a ZEBRA in place of the share lot

*Source: the user, 2026-10-29 and 30. They asked "Is there a way to create a trade like this with
options?", then added the ZEBRA and asked for its expiry to be tested as a grid. Declared after items
26 to 28 reported (item 26 failed). The only options data seen is the broker quote probe of
2026-10-30, one snapshot on a day SOXL fell 22%. Strikes were $5 apart near the money, and a round
trip at the natural cost a sixth to a half of a spread's best case.*

- **Question:** on the ladder's trigger days, does an option structure in place of the share lot
  make more per trade than a share lot of the same delta, after the options' wider costs?
- **Where the edge would live:** not in a signal; item 26 found none. In **execution and
  structure**: whether the options' defined loss and convexity, or the put spread's premium, pay
  for their costs over the lot's life. The quote probe says costs are the hurdle.
- **Names:** SOXL and TQQQ, the ladder's names with weekly options that trade. TECL's options are
  thin.
- **The entry:** a session whose low reaches 93% of the previous close (item 26's `once` trigger).
  Daily chains cannot see the intraday trigger price, so the structure enters at that session's
  close, from the vendor's end-of-day quotes, and the share lot it is paired with enters at the same
  close. One open position per name and arm; a trigger while one is open is skipped and counted.
- **The target:** the first later close at or above 104% of the entry close, as the share lot's.
- **Tenors** (the expiry chosen at entry):
  - **7-day:** the first expiry 5 or more calendar days out.
  - **21-day:** the first expiry 18 or more days out.
  - **60-90-day:** the expiry in 60 to 90 days nearest 75.
- **Structures,** one set each, strikes from the entry close S:
  - **Call debit spread:** long the call nearest S; short the first strike at or above 1.04 x S.
  - **Put credit spread:** short the put nearest S; long the first strike at or below 0.96 x S.
  - **ZEBRA:** long 2 calls at the strike whose delta is nearest 0.70; short 1 call nearest S.
    Delta is Black-Scholes from the leg's mid, the 3-month bill rate and no dividend.
- **When the target is not reached by expiry** (for the 60-90-day tenor, by 21 days left):
  - **close:** the structure is closed at that session's quotes;
  - **roll:** closed and opened again at the same tenor from that session's close, the target
    staying 104% of the first entry close, until it is reached or the window ends;
  - **exercise** (ZEBRA only, reported): the long calls become 200 shares at their strike, the
    short is closed, and the shares are held to the target as a share lot.
- **Judged arms:** 3 structures x 3 tenors x {close, roll}, 18 in all, Holm across them.
- **Measured,** per trade: the structure's net in dollars less the net of a share lot of the same
  entry delta (whole shares), entered and exited at the same closes, per share of that delta.
  Reported beside it: the structure's net as a share of its capital at risk (the debit, or the width
  less the credit), the share lot's on its cost, the hold, and the worst trade.
- **Costs:** fills at the natural, buying at the ask and selling at the bid, as the working price;
  mid reported. The suite's tastytrade schedule (`cherrypick.core.fees`): $1.00 a contract to
  open, exchange and regulatory fees on every leg, no commission to close. An exercise is charged
  as the broker's. The share lot pays item 26's 0.05% a side.
- **Windows:**
  - **Design:** the 7- and 21-day tenors 2022-01-03 to 2024-12-31 (SOXL lists an expiry every week
    from 2022; TQQQ's weeklies reach 2015 but are not used, so both names share one window); the
    60-90-day tenor 2016-05-02 to 2024-12-31 (the subscription serves both names' chains from 2016;
    2013 needs a higher tier).
  - **Holdout:** 2025-01-02 to 2026-10-28, every tenor, counting only after a design pass.
  - Fewer than 30 closed trades in an arm in a window is no verdict.
- **Pass-or-fail rule**, per arm, all must hold:
  - in the design window, the mean paired difference above zero with a one-sided t above 2, one
    trade one observation, and Holm across the 18;
  - positive in each time-half of the design window, and on each name;
  - still positive with fees at 1.5x and fills at the natural plus $0.05 a leg;
  - then the holdout's mean paired difference above zero.
- **Rival explanations, each with its check:**
  - *Leverage, not the structure:* every trade is paired against shares of the same entry delta.
  - *A bull market:* the structure and its share lot buy on the same days, so the market's drift is
    in both.
  - *Costs read too kindly:* the verdict is at the natural; mid is reported only.
  - *One volatility regime:* the design holds 2022's fall, and both time-halves must agree.
  - *One name:* positive on each of the two.
- **Minimum detectable effect (B12):** from the design window's own spread of paired differences,
  recorded in the local result before the holdout is read.
- **Declared limits (B11):** end-of-day quotes only, so the entry is at the close, not at the
  trigger level, and an intraday target is read on closes. Black-Scholes deltas, not the vendor's.
  Early assignment of a short leg is not modelled. The 7- and 21-day tenors have no history before
  2022.
- **Settled with the replay, before the history was fetched (2026-10-30):**
  - Strikes are read against the day's raw close (the technicals history's bars). The trigger and
    the target read split-adjusted ratios.
  - A split ex-date inside a trade ends it at the session before, at that session's quotes, because
    an adjusted contract's strikes and deliverable change. TQQQ split five times in the windows.
  - A leg with no two-sided quote on an exit session is marked at its last earlier quote in the
    trade. On its expiry session, it is marked at intrinsic value with the settlement fee. Both are
    counted.
  - A roll that cannot be opened ends the trade where the last set closed.
  - The reported ZEBRA exercise applies only when the long calls are in the money.
  - The stress takes the options' fees and fills only. The share lot keeps its 0.05% a side.
- **Data:** ThetaData Options Standard end-of-day chains for SOXL and TQQQ, fetched by a research-queue
  `fetch-chains` row into the backtest store, beside item 26's `bars.db` and item 28's Treasury files.
  The replay is `scripts/share_ladder_item29.py`, run as `item29/plan`, `item29/fetch-chains` and
  `item29/study`. The results are derived from licensed data, so they stay under
  `~/.cherrypick/data/backtest/results/research/item29/`, and this plan records the verdict without
  figures.
- **Decides:**
  - **A pass:** the passing structure becomes an option in the `ladder` module beside the share lot,
    paper first, its legs placed in regular hours only.
  - **A fail or no verdict:** the module trades shares only, and the option versions are closed as
    specified.
- **Result (2026-10-30): fail; twelve arms fail and six have no verdict.** None passes.
  - **The twelve:** every `close` arm, and the 60-90-day `roll` arms. Each fails every check: the
    mean paired difference is negative, its t is strongly negative, Holm passes none, and both
    design halves, both names and the stress are negative too. So each structure did measurably
    worse than the share lot, not merely no better.
  - **The six:** the 7- and 21-day `roll` arms closed too few trades in the design window. A roll
    held from early 2022 stays open for most of the window, and each arm holds one position at a
    time. Their holdout, reported, is negative.
  - **Why, reported:** marked at the mid, the paired difference sits near zero for every arm. What
    the structures lose to the share lot is about the cost of crossing their spreads, which over a
    4% move outweighs what the defined loss, convexity or premium returns. The same holds in the
    holdout.
  - **The ZEBRA exercise, reported:** taking the shares through the long calls instead of closing
    them changes little; it is negative in both windows.
  - Every figure is in the local result
    (`~/.cherrypick/data/backtest/results/research/item29/results.json`).
- **What this decides:** the fail branch. The `ladder` module trades shares only, and the option
  versions are closed as specified.

### 30. GEX walls on the monthly expiry: does price stop short of them over the next two weeks?

*Source: the user, 2026-10-30. The console review dropped a view of the user's short strikes against
the GEX walls (#703), because the module's walls are the 0DTE SPX/XSP expiry's and the user's shorts
are 20-60 day single names and ETFs. The user asked how the walls could be tested for those trades,
and for a non-index name without daily expiries beside the index. **A retest, said out loud:** item
12 closed the 0DTE barrier read (failed, 2026-10-19, re-run 10-25). This is a different expiry and
horizon: walls from the monthly a 20-60 day trade sells, read over 5 to 20 sessions.*

- **Question:** does price reach a wall built from the monthly expiry less often over the next 10
  sessions than it reaches any level the same distance away?
- **Where the edge would live:** a **flow effect**, dealer hedging of the monthly's open interest. If
  it exists, a short strike beyond a wall is breached less often than its delta says, which is premium
  the delta does not price. Test A asks only whether the barrier exists. Test B, whether a short
  strike behind a wall earns more after costs than one at the same delta, is declared after Test A
  passes and before any of its outcomes are read. A Test A fail closes both.
- **Names:** SPY, the index, whose daily chains are already on disk (SPX's are not); and AAPL, the
  single stock with some of the deepest single-name open interest, listed weekly and monthly. The study
  reports, from the fetched open interest, every week in which a name lists more than one expiry
  (expected for SPY's dailies, and zero for AAPL). Two names, so no name split (B9).
- **The walls,** per name and session D, at D's close:
  - **Target expiry:** the standard monthly (third Friday, or the trading day before when it is a
    holiday) 20 to 60 calendar days out, nearest 45.
  - **Each strike's vol:** Black-Scholes from its own side's mid, the 3-month bill rate before D, no
    dividend. Its gamma is from that vol.
  - **Open interest:** the vendor's report on D's morning, the previous close's, so nothing published
    after D's close is used.
  - **The walls:** `core.gex.compute_gex_profile` (one gamma per strike) and `core.gex.net_walls`, the
    GEX module's own: the call wall is the most positive net strike, the put wall the most negative.
    The single-side peaks (largest call gamma, largest put gamma) are reported, not judged.
  - **No wall that side that day:** a wall on the wrong side of the close, or nearer than 0.25 or
    further than 2 sigma_h.
- **Measured:**
  - sigma_h = the at-the-money strike's vol x the close x sqrt(h / 252), h = 10 sessions.
  - The excursion M: the highest high (call side) or lowest low (put side) over the h sessions after
    D, from D's close, in sigma_h.
  - F(x): the share of all of a name's and side's sessions in the window whose M reaches x, the rate at
    which any level that far away is reached. (On one day every level on a side is reached or not by
    the same path, so the baseline is across days at the same distance.)
  - Each wall-day's **d** = 1 if M reaches the wall, else 0, less F(the wall's distance). With no
    effect, d averages zero; a wall that holds makes it negative.
  - Pooled: each session's mean d over its name-side cells (four).
- **Windows:**
  - **Design:** 2022-01-03 to 2024-12-31 (2022's fall and 2023-24's rise).
  - **Holdout:** 2025-01-02 to 2026-09-30. It is computed and reported whatever the design result
    (changed 2026-10-30, before any data was fetched), but a design fail is still a fail.
  - Fewer than 200 wall-days for a name in a window is no verdict.
- **Pass-or-fail rule**, all must hold in the design window:
  - the pooled mean d at or below -0.05 (5 points fewer crossings than distance alone predicts);
  - the 95% one-sided upper bound of the pooled mean, by stationary bootstrap (mean block h, 2,000
    resamples, seeded), below zero;
  - d negative on each of the four name-side cells, and in each half of the window;
  - the pooled mean d below the placebo's;
  - then the holdout's pooled mean d below zero.
- **Rival explanations, each with its check:**
  - *Walls sit where the regime makes crossing unlikely* (a wall's distance follows volatility): the
    **placebo**, the same name's and side's wall distance from 21 sessions earlier, read against this
    session's path. The walls must beat it.
  - *Implied vol overstates the move,* so every level looks hard to reach: F is measured from the
    realised excursions at the same sigma distance, so that bias is in the baseline too.
  - *One name or one side:* each of the four cells must agree.
  - *One era:* both halves of the design window must agree, then the holdout.
  - *Round strikes:* walls and baseline are both read at their own distances, and a round strike is
    no nearer to being reached than another at the same distance.
- **Reported, not judged:** h = 5 and 20, and the single-side peak walls, beside the judged reading
  (B13's neighbours). There are no costs in Test A, so there is no cost stress.
- **Minimum detectable effect (B12):** d is a crossing (0 or 1) less a rate, so a cell's spread is
  near 0.5, and overlapping 10-session paths leave about 75 independent readings a cell in the design
  window. Pooled over four correlated cells, the bootstrap bound clears zero only for an effect near
  -0.07 or larger. A no-verdict or fail reads "no barrier stronger than about 7 points".
- **Declared limits (B11):**
  - End-of-day chains, so the walls are read at the close, and the excursion uses daily highs and
    lows (the technicals bar store, raw, the chain's own strike basis; neither name split in the
    windows).
  - The wall comes from one expiry, the monthly a trade would sell, not all expiries summed.
  - Open interest does not say who holds it. `net_walls` assumes dealers are short puts and long
    calls; the peak walls, reported beside, assume nothing about the side.
  - Black-Scholes vols with no dividend; the out-of-the-money side keeps the dividend's effect small.
  - A session whose target chain or open interest is missing is skipped and counted (`missing` in the
    result).
- **Data:** the stored ThetaData end-of-day chains for the monthly expiries, with the 2023-25 monthlies
  fetched by a `fetch-chain-paths` row; open interest by the new `fetch-oi` (`oifetch.py`, its own
  store `open_interest.db`). The study is `scripts/gex_walls_item30.py`, run as `item30/fetch-chain-paths`,
  `item30/fetch-oi` and `item30/study`. The results are derived from licensed data, so they stay under
  `~/.cherrypick/data/backtest/results/research/item30/`, and this plan records the verdict without
  figures.
- **Decides:**
  - **A pass:** Test B is declared (short strikes behind a wall against the same delta without one,
    after costs), and the console's dropped view is reconsidered only if Test B passes.
  - **A fail or no verdict:** the walls stay a 0DTE read, the view stays dropped, and the barrier read
    is closed for monthly expiries too.
- **Result (2026-10-30): fail.** Both names cleared the 200 wall-day floor in both windows.
  - **The judged reading:** walls are reached barely less often than any level at the same
    distance, far short of the declared effect, and the bootstrap bound sits above zero. Only the
    placebo check holds.
  - **By side:** the call walls on both names are reached somewhat less often than distance alone
    predicts, and the put walls more often, so the sides cancel. A barrier on one side only is not
    the barrier declared. The halves disagree too.
  - **Reported:** at 5 and 20 sessions, and on the single-side peak walls, no reading meets every
    check. The nearest, the holdout's 5-session peak walls, fails only on size. The holdout's judged
    reading is below zero but small; it does not count after a design fail.
  - **Expiries:** SPY lists more than one expiry in most weeks of both windows, as expected; AAPL in
    few.
  - Every figure is in the local result
    (`~/.cherrypick/data/backtest/results/research/item30/results.json`), with its reading in
    `results.md` beside it.
- **What this decides:** the fail branch. The walls stay a 0DTE read, the view of short strikes
  against the walls stays dropped, and Test B is not declared.

### 31. The earnings module's paper rule: does any strategy it opens earn after costs?

*Source: the user, 2026-10-30. They are not going live on earnings and asked whether the module's paper
rule has an edge at all, before live is ever considered. They approved this design on 2026-10-30, with the
pilot run first and reported before the full fetch. **Look-alikes, said out loud.** Item 1 (the put spread on post-earnings drift)
failed; it traded the drift after the report, not the premium into it. The 2026-10-13 expected-move fly
study found a debit fly fair at mid and its costs too large; that is a different structure and the
module's `expected_move_butterfly` stays outside this item for that reason. The 2026-10-17 navigator
history priced a credit or debit spread off a direction read and found nothing after costs; here the
side of the one-sided arm comes from skew, and the two other arms take no side. The time-spread plan's
step 0 scored the retired calendars and found nothing made money. None of these ran the module's
whole rule: its screens, its strike and wing choice from the credit received, and its management (a
loser closed the first morning, a winner carried up to three sessions). That whole rule is what the
paper ledger has been running since 2026-07-15, and it is the thing asked about.*

- **Question:** does the iron fly, the iron condor or the directional credit spread, entered at 15:40 ET
  the session before a report and managed as the module manages it, earn more than zero after the
  suite's costs, and more than the same structure opened on the same name on an ordinary session?
- **Where the edge would live:** a **risk premium**: the options' implied move into a report exceeds
  the move that follows, and the seller of the event is paid for carrying it. The module's screens
  (an inverted term structure, an expected move above a floor) are a **selection signal** aimed at the
  same premium. Its management (the first-morning loser close, the three-session cap, the targets and
  stops) is **payoff shaping**, expected to move the win rate and not the expectancy, so it is
  measured last and reported beside the unmanaged readings. **What would falsify it:** the mean net on
  capital at risk is zero or negative after costs, or it is positive and the control earns as much
  (the premium would then be ordinary overnight theta, not the report).
- **What is judged.** The three strategies that still open and can be ranked in `strat_test`:
  `iron_fly`, `iron_condor` and `directional_credit_spread`, one arm each. Not judged, with the
  reason: `expected_move_butterfly` (paper only, never ranked, already studied above); `atm_calendar`
  and `double_calendar` (retired from the harness 2026-10-15; the time-spread plan covers them);
  `broken_wing_butterfly` (retired 2026-10-26, costs about half its premium). Their paper rows run
  off as they are.
- **The structures, built by the module's own code.** Thresholds are the deployed file's at the
  declaration date, copied here so a later edit cannot move the test: profit target 50% of the credit
  and stop at 1.5x the credit for all three (the example config and the strategy guide say 25% for the
  fly and a 2.0x stop for the spread; the deployed file is what the ledger ran, so it is what is
  tested); wing width the credit times 2.5, 3.0 or 3.5 by the name's IV/RV band (1.25 and 1.75), the
  ratio read from Dolt's `volatility_history` as of the entry date, as the module's own pre-gate does.
  - **iron_fly:** short the at-the-money call and put; longs at the strikes nearest the short strike
    plus and minus the wing width.
  - **iron_condor:** shorts at the strikes nearest the spot plus and minus the expected move (0.85 x
    the front straddle's mid); longs one wing width beyond each.
  - **directional_credit_spread:** the side with the richer 25-delta implied volatility (calls or
    puts), skew at least 0.02; the short strike is the candidate whose break-even lands nearest the
    expected-move boundary; the long a wing width (the short's mid times the multiple) beyond it.
  - **Expiry:** the nearest on or after the reaction date (the report date for a pre-market report,
    the next session for an after-close one). **Size:** one contract a leg; a structure whose one
    contract risks more than the deployed paper budget (5% of the paper capital) is skipped and
    counted.
  - The replay calls the module's pure functions (`_select_short_strike`, `select_side`'s arithmetic,
    `compute_expected_move_and_term_structure`, `management.evaluate`, `evaluate_credit_spread_exit`,
    `compute_generic_exit_debit`, `exit_debit_bounds`, `sizing.per_contract_max_loss`) over chain rows
    read from the store in the broker-chain shape, so a changed gate changes the replay too. The chain
    readers, which do I/O, are the only things replaced.
- **The entry.** The 15:40 ET bar of the entry session (the scan starts at 15:35 and orders land
  through 15:55; 15:35 and 15:50 are read from the same fetch and reported). One entry per name and
  event per arm, an after-close report entered that day and a pre-market report entered the session
  before it, as the module's calendar admits both. **Events:** Dolt `earnings_calendar` rows whose
  timing is stated ('After market close' or 'Before market open'); a blank timing is not read as
  after-close here, as the module does live, because history can be exact (it is 94 rows of the
  universe, counted below, and all are dropped).
- **The admission,** applied point in time to each event:
  - *Universe.* The names the suite's liquidity verdict calls `tradable` today (158 on 2026-10-30), the
    list the module has been restricted to since 2026-10-28. That list is **hindsight**: it says
    which names are liquid now. It is reported apart. The point-in-time versions of its tests are also
    applied: the stock's 50-session median dollar volume at or above the verdict's bar (Dolt bars),
    a listed weekly (a chain for a weekly expiry within 9 days exists in the store), and the at-the-money
    spread at the entry bar at most 7.5% of mid.
  - *Hard filters,* as `screening-criteria.md` Layer 1, read from the entry-bar quotes: price at least
    $10; front expiry at most 9 days out; front-month open interest at least 2,000 (the options
    history's open interest of the entry morning); term structure at most -0.004; at-the-money delta
    at most 0.57; expected move at least $0.90 for the fly and 4% of spot for the other two; the
    front at-the-money spread at most 15% of mid.
  - *Soft criteria.* Of the five, the deployed file has winrate, IV/RV and market cap **off**, so they
    are not applied; the 30-day average volume (at least 1.5 million shares, Dolt) is applied; the
    combined option volume floor (500 contracts) is **not reproduced** (the entry bar's volume is not in
    the store; the open interest floor stands in) and is a declared limit.
- **The management,** on a 5-minute grid from 09:45 to 15:55 of each session after entry, through
  `management.evaluate` exactly (the live loop marks each minute and acts from 09:40; the grid is the
  declared simplification):
  - nothing acts on the entry session except the expiry close and the pin guard;
  - the strategy's target and stop on the mid mark; the PEAD gate on the first bar of each morning (a
    position at or below breakeven closes); the three-trading-session cap; the pin guard (a short
    strike within $1.00 of spot in the last 60 minutes of its expiry day); the expiry close at 15:30 of
    any leg's expiry day;
  - the spread gate: a close decided on a leg wider than 35% of mid and $0.05 waits for the next bar,
    except the expiry close;
  - a position still open after a leg's expiry settles at intrinsic against the stock's close.
- **The fills and costs.** Entry and exit at each leg's mid on the bar, the exit clamped to the
  structure's payoff range, then `cherrypick.core.fees`: the commission to open ($1.00 a contract, none to
  close, $10 a leg cap), the clearing and regulatory pass-throughs, the slippage haircut of 12.5% of each
  leg's spread (capped at 15% of its mid) on **both** sides, and the settlement fee on legs that
  expire. This is the paper ledger's own basis for positions entered from 2026-10-26, so the replay and
  the paper check read the same fills. **Verdict basis: the module's working price** (above).
  **Reported beside it:** the natural side both ways (shorts bought at the ask, longs sold at the bid),
  the mid with fees only, and the stress below. (The user's choice, 2026-10-30, over the house habit
  of judging at the natural: the replay then reads the same fills as the paper ledger.)
- **The control.** The same structure on the same name at an ordinary session, paired with the event
  trade:
  - **Which session:** the session on the same weekday four weeks earlier (so the same days to
    expiry at the same weekly cadence), kept only if Dolt lists no report for the name from that
    session to the control's expiry and none within three sessions before it; else the same weekday
    four weeks later under the same test; else five weeks earlier; else the event is dropped from the
    paired readings (the unpaired reading keeps it) and counted.
  - **What is built:** the same code, from the control bar's quotes, with the screens not applied (an
    ordinary session fails the term-structure and expected-move floors by design). Same expiry rule
    (the nearest at the event's days-to-expiry), same management, same fills and costs.
  - **What the pair isolates:** the report, together with the screen's pick of a name whose front
    month is rich. A control's own IV is read, so the wing sizing and strikes follow its quotes.
- **Measured,** per trade: net dollars for the whole position, in columns that add up (gross, entry
  fees and slippage, exit fees and slippage, settlement), and net as a share of capital at risk (the
  widest wing less the credit, times 100). Per arm: the mean and its date-clustered bootstrap, the
  win rate, the hold, the exit reasons, and the paired difference (event net share minus its
  control's). Reported, not judged, from the same data: the exit read three ways (closed at the first
  morning's first bar regardless; held to the expiry close with no target, stop or gate; the managed
  rule without the PEAD gate), which separates the overnight premium from the management.
- **Windows** (the paper ledger began 2026-07-15, so nothing here reads its era):
  - **Design:** 2020-01-22 (the calendar's first row) to 2023-12-31. Halves: 2020-01-22 to 2021-12-31
    and 2022-01-03 to 2023-12-31.
  - **Holdout:** 2024-01-02 to 2026-07-14, run after the design whatever it shows, counting only after a
    design pass.
  - **The later check, untouched:** the paper ledger's entries from 2026-10-28 (after both the exit
    basis of 10-26 and the liquidity verdict of 10-28 bind). Nothing here reads a paper trade's
    outcome. The replay is validated on paper entries from 2026-07-15 to 2026-10-23 for the
    *entries* only: the same structures, strikes and credits to within a tick on at least 20 events
    (the share agreeing is reported, and below 90% the study stops and the replay is fixed first). The
    outcomes of those rows are not read.
  - **Minimum trades:** fewer than 150 closed, paired trades in an arm in the design window, or 100 in the
    holdout, is no verdict for that arm.
- **Gates, added 2026-10-30, before anything was fetched or read** (the user asked whether trades
  could be narrowed to perform better; only gates with a stated mechanism are taken, and each is
  counted as its own arm). Each gate is applied to each of the three strategies, so there are nine
  judged arms: three ungated, three under G1, three under G2.
  - **G1, a rich premium.** The entry bar's expected move (the module's own, 0.85 x the front
    straddle's mid) is at least 1.2 times the name's mean absolute reaction move over its previous
    eight stated-timing reports (the reaction session's close against the last close before the
    report, from Dolt's `stocks.ohlcv`). A name with fewer than four earlier reports in the calendar
    carries no G1 trade, so G1's design sample starts later than the others'. **Mechanism:** the seller
    is paid only when the implied move overstates the realised one, and the name's own history is the
    plainest estimate of whether it does.
  - **G2, a credit that can pay its costs.** The entry's round-trip crossing cost (each leg's full
    quoted spread, summed, times 100) is at most 25% of the credit received (times 100). **Mechanism:**
    in the paper ledger, costs were about the whole loss; a credit that small against its own spreads
    cannot pay them whatever the report does.
  - **Reported, not judged:** each arm split by whether the report falls in a week with a scheduled
    FOMC decision or CPI release, from the public calendars.
  - Each gated arm has its own minimum trades (as below) and its own paired control (the control is
    gated on its own bar's quotes for G2; G1 is a property of the name and report, so the control keeps
    the event's G1 status). A gated arm passes on the full rule below; one that passes while its
    ungated arm fails is recorded as such, never as the strategy passing.
- **Pass-or-fail rule,** per arm, all must hold in the design window:
  - the mean net share of capital at risk above zero, with a one-sided date-clustered bootstrap t above
    2 (resampling entry sessions, 2,000 resamples, seeded), Holm across the nine arms;
  - the mean paired difference above zero on the same test;
  - the mean net positive in each time-half and in each name-half (`name_half`, B9), and the paired
    difference positive in each time-half;
  - still positive with fees at 1.5x and the slippage haircut at 25% of spread (double);
  - still positive with the best 5 trades removed (a win rate this structure earns on small credits
    and an occasional full wing makes the tail the whole story);
  - then, in the holdout, both the mean net and the mean paired difference above zero.
  - **Reading a partial pass.** Net positive and the pair not: the premium is ordinary overnight
    theta, not the report; recorded as "pays, not for the report" and not a pass. Net negative and the pair
    positive: the report is worth something but the costs eat it; a fail.
- **Rival explanations, each with its check:**
  - *One era:* both time-halves, then the holdout (which holds 2024's and 2025's calm and 2026's moves
    that 2020-23 did not). The 2020 spring is in the first half, flagged, and the result is reported
    with 2020-02 to 2020-06 removed.
  - *One name or a few:* both name-halves; no name above 8% of an arm's trades, the top five names'
    share and the result without them reported.
  - *Costs read too kindly:* the stress above; the natural side reported; the entry-bar quote ages and
    spreads reported by year.
  - *The report itself against ordinary overnight theta:* the paired control; the three exit readings.
  - *A selection effect of the screen:* the result by term-structure tercile and by the at-the-money
    spread, reported (the module's own 2026-10 test of term structure as a covariate stays its own
    declaration and is not scored here).
  - *Survivorship:* the universe is today's liquid list. The point-in-time tests are applied and the
    result is also split by the measured spread tercile, so a result that lives only in names that
    were illiquid then and liquid since shows up there. It cannot be removed, and a pass says so.
  - *Missing quotes drop the big losers:* a leg with no two-sided quote at entry drops the event; the
    drops are counted by year and arm and a drop rate that differs by more than 5 points between the
    paired halves, or that is largest in the years with the worst results, is reported with the
    result. At an exit bar a leg with no two-sided quote is marked at its last earlier quote in the
    trade, counted.
  - *Many events on one night:* the bootstrap resamples entry sessions, and the event counts per
    session are reported. The module's correlation block list is not applied (the harness does not
    apply it either).
- **Minimum detectable effect (B12):** (the figures below are for an ungated arm; a gated arm keeps
  fewer trades and detects only a larger effect, recorded with the design result.) a credit structure's net on capital at risk spreads widely, a
  small win most nights and a loss near the full wing on a few. The per-trade standard deviation is
  taken as about 0.8 of capital at risk, **an assumption** until the design window's own spread
  replaces it in the local result before the holdout is read. At the Holm-adjusted bar (t about 2.4)
  and an effective sample of 0.7 of the trades (sessions cluster), 600 trades detect about 9 points of
  capital at risk per trade, 1,000 about 7, and 300 about 13; the paired difference, with a wider
  spread, needs about a third more. A no-verdict or fail then reads "no edge larger than about X
  points of capital at risk".
- **Name split (B9):** applies; 158 names split by `name_half`.
- **Declared limits (B11):**
  - The universe is today's liquid list, hindsight, reported apart.
  - The deployed screens are applied as the file stands now; the thresholds before it were not journalled.
  - The combined option volume floor is not reproduced.
  - The management is on a 5-minute grid, not each minute, so a target or stop touched between bars is
    seen at the next bar, which understates stops and targets alike.
  - The wing multiple's IV/RV band uses Dolt's `volatility_history` (the module's fallback source), not
    the broker's 30-day figures.
  - Early assignment is not modelled; the module closes before an expiry anyway.
  - A position open at a name's last bar settles at that session's mid mark; a position past a leg's
    expiry settles at intrinsic on the stock's close. A split between entry and exit ends the trade
    at the session before and is counted.
  - A trade with no usable chain (an empty request, a refusal, no weekly) is dropped and counted by
    reason, year and arm.
  - Dolt's calendar is the only source of dates and timings. A report that was moved, or listed on the
    wrong day, is an error the study cannot see. The paired control would show it as an event with no
    event premium, so the share of events whose front straddle is not richer than its control's is
    reported.
- **Data:**
  - *Events (counted, nothing priced):* of Dolt's explicit-timing reports for the 158 names, 923 after
    the close and 919 before the open in the design window, 685 and 664 in the holdout, 3,191 in all,
    and 94 with no stated timing (dropped). Dolt's calendar starts 2020-01-22.
  - *What is fetched from the options history:* for every event, the entry bar's front chain (a
    15:35-15:50 window, quotes, delta and implied volatility, the underlying price in the rows), the
    back month's (term structure), and the open interest of the front expiry; then, for the events
    that pass the admission, the front chain on each later session through the expiry or the third
    session (09:40 to 15:55 at 5 minutes), and the same for the control session. A request is a name,
    a session and an expiry, resumable in the store beside `eod_chains.db` (a new intraday store,
    registered in `stores.STORES`), by a `fetch-chains`-style queue row with `--stop-by`.
  - *Count:* about 3 requests for each of the 3,191 events before admission (about 9,600), and about
    5.4 for each that passes (2.2 exit sessions, one control entry bar and 2.2 control exit sessions).
    If 40% pass (to be counted from the entry bars, not assumed), another 6,900, in all about 16,500
    requests.
  - *Pace:* not measured for a single name's intraday history. The only basis is the store's
    end-of-day chain fetch, which `packages/backtest/CLAUDE.md` puts at about a second a request;
    an intraday window returns more rows, so 1 to 4 seconds is the range assumed: **about 4.5 to 18
    hours**, in the supervisor's research queue, holding the account's one vendor session throughout
    (nothing else may use the options history meanwhile). A first step of 20 requests measures the real
    pace and replaces this estimate.
  - *The pilot, a coverage test, run before anything else:* 30 events a year for 2020-2025 drawn by a
    seeded hash of name and date (never by outcome), the entry bar and one later session each. It
    reads row counts and whether both bars and the delta and implied volatility came back, never a
    price into a result. **The basis is chosen by a rule declared now:** if at least 90% of the
    pilot's requests are complete in every year, the intraday basis above is used for the whole study;
    if not, the **end-of-day fallback** below is used for the whole study (one basis for all years,
    never mixed), and the intraday reading is run on the years it covers as a reported sensitivity.
  - *The end-of-day fallback:* entry at the entry session's end-of-day quotes (about 16:00, against the
    15:40 bar), marks at each later session's end-of-day quotes only, so the PEAD gate is the first
    mark after the report (the reaction session's close, not its first minutes), a target or stop is
    seen only at a close, and an expiry day closes at its end-of-day mark (not 15:30). The direction of
    the bias is not known in advance. The requests are the store's existing `fetch-chains`, about a
    second each, 16,500 requests.
  - *Other inputs:* Dolt `earnings_calendar`, `stocks.ohlcv` (price, 30-day average volume, dollar
    volume, expiry settlement) and `options.volatility_history`, all local. `cherrypick.core.fees`
    for costs. The suite's liquidity verdict file for the pool.
  - *Licence.* Everything fetched and everything derived from it stays under
    `~/.cherrypick/data/backtest/results/research/item31/`, and this plan records the verdict without
    figures. The replay is `scripts/earnings_item31.py`, run as `item31/plan`, `item31/pilot`,
    `item31/fetch-chains` and `item31/study`.
- **Decides** (the user is not going live; paper continues whatever this shows):
  - **An arm passes:** it is marked *eligible to be considered for live later*: that is all. The paper
    ledger's entries from 2026-10-28 are then its untouched later check, with a pass requiring the
    mean net above zero once it has 100 closed trades since that date. Live stays off; the module's
    gate, per-day arm and the rest are untouched.
  - **An arm fails:** it stops opening in `strat_test` from the next declared boundary (a dated entry in
    `STRAT_TEST_RETIRED` with its own `strat_test_strategies` break, batched, never mid-measurement);
    its open rows run off; its paper rows stay in the ledger and the reports. If the module is left with
    no tested edge in any arm, that is reported as the finding, and what to do with the module is the
    user's call, not this item's.
  - **A no-verdict arm:** it keeps opening unchanged, with "no edge larger than X" written beside it,
    and the question is asked again when the paper trades since 2026-10-28 add enough to change the
    answer.
  - **"Pays, not for the report"** is reported as it is, not as a pass, and the premium is then looked for
    in the overnight alone (item 22's condor and item 19's flies are the nearest done).

### 32. Large options orders as a signal: the flow plan's declared test, registered

*Source: [flow-signal-plan.md](flow-signal-plan.md), declared 2026-10-24 with its fetch, and never run.
Registered here on 2026-10-30, when the user asked for it to run. The method, the three tags, the
three cuts, the matched baseline, the trials and the pass rule are that plan's, unchanged. This entry
records only what had to be settled before the first read, and nothing here has read an outcome.*

- **Question:** does a large, opened options order (a call bought, a put sold, a put bought) predict
  the stock's next five sessions against stocks in the same relative-strength stage without one?
- **Where the edge would live:** **flow**: a buyer who pays the spread to open a large position may
  carry information, or hedging demand, the share price absorbs over the following sessions.
- **The test:** as declared in [the flow plan](flow-signal-plan.md#the-declared-test): nine trials
  (three tags by three cuts), deflated by nine, the larger cuts at least as good as the smaller; then
  once on the holdout. The second family (40 trials) and the earnings test (two) are counted apart,
  as the plan declares.
- **Settled before the first read (2026-10-30):**
  - **The top-1% rule's look-back.** The plan reads a name's previous 20 sessions, but the fetch holds
    only the grid's every fifth session and the sessions before a report. The rule is read over the
    name's last 20 *fetched* sessions before the tag session, at least 4 of them; a name with fewer
    carries no tag that session.
  - **Thin sessions.** About 21 grid sessions from 2025-01-16 hold far fewer names than the roughly
    600 elsewhere. The plan's fetch is complete, so this is the universe's membership in the local
    bar store, diagnosed before the run. The verdict is read with and without those sessions, and
    both are reported.
  - **Reported, not judged:** a stale-tag placebo (the previous grid session's tags against this
    session's outcome window); the reading with name-sessions whose outcome window holds a report
    dropped; and the reading with the five most frequent names removed.
  - **The holdout fetch:** the grid rule carried on over 2025-07-01 to 2026-10-12 (every fifth session,
    counted on from the design grid), with the sessions before each report for the earnings test, by
    `scripts/flow_fetch_plan.py` extended to the holdout. Fetching reads no outcome; the holdout is
    still read once, after the design results.
- **A gate, added 2026-10-30 before anything was read:** the second family gains **F7, no report
  ahead**: a tag is not counted when the name's next report has its reaction day inside the outcome
  window. **Mechanism:** an order placed ahead of a report may be a bet on the report, which the
  plan's earnings test asks apart; without them the tag asks about information outside reports.
  F7 on three tags at three cuts adds nine trials, so the second family is 49, deflated together.
  It replaces the reported reading with report windows dropped, which is now judged.
- **Settled with the build, before the first read (2026-10-30, `scripts/flow_signal_item32.py`,
  `cherrypick.backtest.flowstudy`):**
  - **The thin 2025 sessions were a calendar change, not missing data.** 2025-01-09 (a national day of
    mourning) became a closed day in the suite's calendar on 2026-10-25, after the grid was fetched,
    so a grid recounted today drifts by one session from 2025-01-16. The study reads the fetched grid
    from the plan file and never recounts it. The with-and-without reading is kept (a session under
    80% of the median fetched name count is flagged); it should flag none.
  - **The outcome** runs from the close of the session the tag is known on (D+1) to the close five
    sessions later (D+6), name minus SPY, signed. "A report in the window" for F7 is a reaction day
    from D+2 to D+6.
  - **The top-1% rule** pools the histograms of the name's previous 20 fetched sessions, the
    pre-report ones included, at least 4.
  - **The baseline** is the same-stage names fetched whole that session, with the look-back and an
    outcome, without the tag; a name with no stage verdict is its own stage; a tagged name whose
    stage has no baseline name is dropped. The stage is the board's channel rule on adjusted closes;
    the adjustment uses dividends known later, a small look-ahead, declared here as a limit.
  - **Each session's value** is the tagged mean less its matched baseline's, sessions weighted
    equally. **The interval** is the stationary bootstrap over grid sessions (mean block 5, 20,000
    resamples) at a level of 1 − 0.05 divided by the family's trials. **A pass** needs the lower
    bound above zero. **No verdict:** fewer than 30 sessions holding the tag (a floor the plan did not
    state, set here).
  - **Larger cuts:** each larger cut's mean at least this cut's; a larger cut with no reading fails it.
  - **Second family:** F2 judged on D's raw close; F3 replaces the premium cut only, against the
    $100,000 put sale; F4's partner is sought among stored orders ($50,000 and up), so straddles are
    undercounted; F5 at the same cut on the same session; F6 over the previous 10 sessions, among
    those fetched. A filtered tag shares its unfiltered tag's baseline and must beat its mean.
  - **The earnings test:** a report with at least one first-family tag at $100,000 or more in the 10
    sessions before its reaction day; the reaction-day close against the previous close, less SPY,
    signed; a bootstrap over reports, deflated by two; a pass needs the hit-rate bound above 50% and the
    mean above the assumed 1%; at least 30 reports.
  - **The holdout** reads each design pass at its own cut, deflated by the number carried in its
    family, and is refused without a design pass list or once a spent marker exists. Its grid is the
    holdout plan's (about 65 sessions from 2025-07-03), fetched by a `fetch-flow` row.
- **Data:** the flow store (`flow.db`, 2022-05-16 to 2025-06-26 fetched) and the local bars. Results
  stay under `~/.cherrypick/data/backtest/results/research/item32/`, and this plan records the verdict
  without figures.
- **Result (2026-10-30): fail; no cell passes in the design window, so the holdout is not read.**
  - **First family:** none of the nine tag-and-cut cells has a deflated interval above zero. Call
    buying and put selling lean positive and put buying negative, as the tags' sides would have it,
    but each is small against a session's noise.
  - **The placebo reads as high as the tags:** last grid session's tags, set against this session's
    outcomes, do about as well as this session's. What the tags pick up is mostly a persistent
    difference between the names that draw large orders and their same-stage peers, not the orders.
  - **Second family (49 trials, F7 included):** none passes; several filters fail to beat their
    unfiltered tag.
  - **The earnings test:** neither side's hit rate clears 50% by its interval, and neither covers an
    assumed 1% round trip.
  - **Thin sessions:** none; the calendar change was handled by reading the fetched grid.
  - Every figure is in the local result (`~/.cherrypick/data/backtest/results/research/item32/`).
- **Decides:** as the flow plan's build: a tag that passes on the holdout becomes a board method, read
  from the broker's nightly orders (its steps 3 and 4); a tag that fails never enters the board, and
  the board keeps saying flow is not counted.

### 33. MEIC on Fed decision days: does entering at the 14:00 statement cost more than other days?

*Source: the user, 2026-10-30, asking whether gates could make the studies perform better, then for
outside sources. The published pre-FOMC drift (Lucca and Moench, 2015) largely disappeared after 2015,
so it is not used as a direction signal. This item takes a different mechanism: the Fed's statement
is released at 14:00 ET, the minute MEIC's live rule starts entering, and the press conference
follows at 14:30. **Look-alikes, said out loud.** Item 24 tested the entry slot and closed it; the
MEIC VIX-expiry gate (2026-10-21) found no edge; items 23 and 24 found no lever larger than the
sample could see. **Seen before declaring:** `pm-200`'s replay over both windows, in aggregate only.
Its sessions have never been split by Fed day.*

- **Question:** on scheduled FOMC decision days, does `pm-200` (the live rule: SPX, 10 wide, 16 delta,
  entering from 14:00) net less than on other days, enough that skipping those days raises its net?
- **Where the edge would live:** **avoiding a scheduled volatility release** at the moment of entry. A
  short condor opened as the statement lands takes the jump at once and the press conference's moves
  after. The premium may already price it (implied volatility rises into the release), which is what
  the test asks. **What would falsify it:** Fed-day nets no lower than other days', after costs.
- **The days:** the Fed's published calendar of scheduled FOMC decisions (unscheduled meetings are
  not counted), fetched by a script into `~/.cherrypick` as public data. **Reported, not judged:**
  CPI release days (08:30, before entry) and the days with an unscheduled Fed action.
- **Measured,** per session: `pm-200`'s net (its replay, the live rule's own engine and fills). The
  gate's effect is the mean net of Fed days against the mean of the other sessions, and the gated
  rule's whole-window net (Fed days removed) against the ungated rule's.
- **Windows:** the backtest's own over the 60-second store: **design** 2022-05-16 to 2025-06-30 (about
  25 decision days), **holdout** 2025-07-01 to 2026-10-12 (about 10). **The untouched check:** every
  scheduled decision day from 2026-11-01 on, in the paper and live ledgers.
- **Pass-or-fail rule,** in the design window:
  - the mean Fed-day net below zero, and below the other sessions' mean, with a one-sided
    permutation p under 0.05 (the Fed-day label redrawn at random over the window's sessions,
    10,000 draws, seeded);
  - below the other sessions' mean in each half of the window;
  - then, in the holdout, the Fed-day mean below the other sessions' mean.
  - Fewer than 20 Fed days in the design window is no verdict.
- **Minimum detectable effect (B12):** with about 25 Fed days against a session spread of several
  hundred dollars, only a Fed-day loss of roughly the size of a typical losing session is
  detectable. A fail reads "no Fed-day cost that large", not "Fed days are safe".
- **Rival explanations, each with its check:**
  - *Fed days fall in volatile months anyway:* the permutation draws from the same window; the result
    is also reported against the five sessions either side of each Fed day.
  - *One meeting or one year:* both halves; the result without the worst Fed day reported.
  - *Replay not yet trusted:* MEIC's replay is provisional until validation unlocks (row 46, not
    before 2026-11-09). This item runs after that unlock, or is recorded as provisional.
- **Declared limits (B11):** 60-second ticks; entries and stops by the replay's fill model; about 35 Fed
  days in all, so the holdout alone cannot confirm a small effect.
- **Data:** the 60-second store, MEIC's replay, the Fed's calendar (public). Results stay under
  `~/.cherrypick/data/backtest/results/research/item33/`; this plan records the verdict without
  figures. The study is `scripts/meic_fomc_item33.py`, run as `item33/study`.
- **Decides:**
  - **A pass:** a Fed-day skip is proposed for MEIC paper at a declared boundary; live follows only on
    the user's word.
  - **A fail or no verdict:** no gate. A hand-set Fed-day skip remains the user's risk choice, labelled
    untested.

### 34. The option-to-stock premium ratio: does heavy option trading foretell weaker returns?

*Source: Johnson and So (2012), "The option to stock volume ratio and future returns", reported a
weekly spread between the lowest and highest deciles of option volume over stock volume, largest
where short sales are costly. Proposed 2026-10-30 from the user's request for outside sources.
**Look-alike:** item 32 reads large opened orders from the same store; this reads total option
trading, a different signal, and shares item 32's grid, windows and holdout fetch. Nothing in the
flow store's outcomes has been read.*

- **Question:** do stocks whose options trade heavily relative to their shares underperform, over the
  next five sessions, those whose options trade lightly?
- **Where the edge would live:** **information in where trading goes**: a trader with bad news, facing
  short-sale costs, uses options, so heavy option trading carries negative information the share
  price absorbs later.
- **The signal,** per name and grid session D (item 32's fetched grid, names fetched whole that
  session): the premium of the day's single-leg option orders, summed from the store's premium
  histogram at each bucket's geometric midpoint, over the stock's dollar volume that session (raw
  close times volume). Contract volume is not in the store, so this is a premium ratio, not the
  paper's contract ratio, declared as such. Known at D's close.
- **The outcome:** item 32's: the close of D+1 to the close of D+6, name minus SPY.
- **Measured,** per session: the mean outcome of the lowest decile of the ratio less that of the highest
  (long the lowest, short the highest), less 0.10% a side on each leg. Sessions weighted equally.
- **Pass-or-fail rule,** in the design window (2022-05-16 to 2025-06-30, the fetched grid):
  - the mean session spread above zero, with the stationary-bootstrap one-sided 95% lower bound above
    zero (grid sessions, mean block 5, 20,000 resamples, seeded);
  - positive in each time-half and in each name-half (`name_half`, B9);
  - positive at 1.5x the costs;
  - positive with name-sessions whose outcome window holds a report's reaction day removed;
  - then the holdout (item 32's fetch, 2025-07-01 to 2026-10-12), read once: the mean spread above zero.
  - Fewer than 100 grid sessions with both deciles filled is no verdict.
- **Rival explanations, each with its check:**
  - *Size or liquidity:* the ratio falls with share volume; the spread is also read within terciles of
    dollar volume, reported.
  - *Momentum or reversal:* the spread read within terciles of the prior five sessions' return,
    reported.
  - *Reports:* judged with report windows removed (above).
  - *The short leg is untradable:* the long-only half (lowest decile against the session's mean) is
    reported; no borrow cost is charged, a declared limit.
- **Declared limits (B11):** premium, not contracts; single-leg orders only; the histogram's buckets
  are coarse; grid sessions only; no borrow cost on the short leg.
- **Data:** the flow store (design fetched; holdout by item 32's row) and the local bars. Results stay
  under `~/.cherrypick/data/backtest/results/research/item34/`, verdict here without figures. The
  study is `scripts/option_stock_ratio_item34.py`, run as `item34/study`.
- **Decides:** **a pass** makes the ratio a candidate board method beside item 32's tags (read from
  the broker's nightly data, as item 32's would be); **a fail** closes it for this store and grid.

### 35. The bwb module's whole rule at real quotes: does the put broken-wing butterfly or any add-on arm earn after costs?

*Source: the user, 2026-10-30. Their standing rule is that no module is retired without a backtest of
its own rule; this is bwb's. They approved it with bwb's paper loop kept running and live off (bwb has
never traded live). The retired `delta` arm was added to the judged set the same day, with two variants.
**Look-alikes, said out loud.** (1) The modelled two-year backtest (`scripts/bwb_history_backtest.py`,
2024-10 to 2026-10) priced the same rule off a fitted smile and daily bars: control and `delta` lost,
`bounce` was about flat, and the paper window's `delta` lead was a rebound. (2) Its 2011-onward extension
found the VIX9D/VIX gate roughly halves the losses and that no arm was profitable out of sample, a
modelled result. (3) The add-on standalone study (2026-10-23) replayed the add-on put spread alone, at
real quotes, on this same minute store: no trigger arm passed in design or holdout. That study
traded the add-on without the fly; this one trades the whole position, so the fly, its credit and
its tail are priced for the first time at real quotes. (4) Item 6 (a down-day entry filter) failed.
(5) The GEX sign gate (2026-10-15) was not adopted for bwb. (6) The advisor's paper experiment
`delta-trigger-early` (an earlier trigger) never armed (its base was `control`, which has no trigger)
and measured nothing; the variants below are not that experiment and no other trigger level
(no 60 or 70 delta) is added, since that would be a search. **Seen before declaring, honestly:** the
modelled results above; the add-on study's verdict (qualitatively, not its figures); and the paper
ledger's outcomes by arm to 2026-10-14, which have been read. The `delta` arm's paper record
(2026-08-24 to 2026-10-14) sits in a rebound and is not a clean check for it, and none of the paper
ledger is a clean holdout. No real-quote result for the base fly or for any whole arm has been
computed.*

- **Question:** does the module's rule, the SPX put broken-wing butterfly entered once a session at
  10:00 ET for the next weekly expiry and held to expiry, earn more than zero per trade after the
  suite's costs, alone (`control`) or with an add-on put spread (`bounce`, `flip`, `delta` and two
  `delta` variants), and does it do so if entries are kept to days when VIX9D is below VIX?
- **Where the edge would live:** a **risk premium**: the implied move the body is sold one expected
  move below overstates the realised move, and the butterfly's short body earns the difference. The
  add-on is **payoff shaping** and a **timing signal** at once: it sells more premium after the market
  has fallen toward the far wing, on the reading that a reversal follows. The VIX9D/VIX gate is a
  **selection signal**: an inverted short-dated term structure marks stress, when the premium is
  least likely to be harvested. **What would falsify it:** mean net per trade zero or negative after
  costs in a half, or a positive mean that lives in a few tail weeks, or one that needs the
  gate to be chosen after seeing the result.
- **The rule, from the module's own code.** Expiry `clock.target_expiration` (the next PM-settled weekly
  strictly after the entry day, a holiday Friday shifted back to Thursday: 1 to 7 days, 8 after a
  holiday-shifted Thursday). Expected move `core.structures.expected_move` from the ATM call and put
  mids. Strikes `engine.select_strikes` ($5 increment: body at spot less the move, near wing one
  increment above, far wing two below, body sold twice). Gates as `engine.plan_entry`: a leg spread
  over 25% of mid refuses `spread_too_wide`, a credit at mid of zero or less refuses `no_credit`. Fees
  and slippage from `engine` and `core.fees`, the $5 settlement fee per distinct in-the-money
  symbol. The replay calls these functions over chain rows read from the store; the chain readers are
  the only things replaced. One lot.
- **The add-on** (`engine.plan_addon`): a put credit spread, sell one increment above the far wing, buy
  one below, which must price as a credit at mid (floor zero); once armed it re-prices each minute
  and fires on the first credit minute; one per position; held to expiry.
- **The arms judged (six, each ungated and gated, twelve judged tests).**
  - `control`: no add-on.
  - `bounce`: arms when the near wing's absolute delta has reached 0.50 and fires when it is back at
    0.45 or below.
  - `flip`: arms when spot has traded below the gamma flip, fires when it reclaims 1.001 times the flip.
  - `delta` (retired 2026-10-15): fires once the near wing's absolute delta reaches 0.50, as bwb ran it.
  - `delta-dte3`: `delta`, but the add-on opens only while at least 3 calendar days remain to expiry (the
    expiry date less the day of the opening minute); a trigger met later opens no add-on and the
    position is the bare fly. **Mechanism:** an add-on sold near expiry carries the most gamma and the
    least premium to absorb a further fall, and the standalone study's add-on losses were tail-driven.
  - `delta-shortstop`: `delta`, but the add-on alone (never the fly) is bought back at the first minute
    after it opens that spot is at or below the add-on's short strike, at that minute's real quotes.
    **Mechanism:** it caps the tail that decided the add-on's losses. It is expected to change the shape
    more than the mean, so it must still pass the full rule below.
  - **Reported, not judged:** `wall` (retired; a call-side structure, and the store holds no call chain
    beyond a 10:00 ATM strip, so it cannot be replayed at all, said here so its absence is not read as
    a pass); the live pilot's shape (`bounce`, gated, entries on Tuesdays only, one lot, with the live
    quarter-end skip) as a slice of the `bounce` gated arm; the advised twins (none can arm).
- **The VIX9D/VIX gate, declared now, both states judged.** Gated: an entry day counts only when the
  day's VIX9D opening print divided by VIX's opening print is below 1.0 (the live bar, `vix9d_vix_max`,
  which refuses at or above it); a day missing either print is refused, as live refuses
  `regime_unmeasured`. The prints are the exchange's public daily files (open values are known before
  10:00; the module reads the intraday prints at the tick, which the store cannot reproduce). Reported
  beside it: the prior close's ratio, and, on the paper ledger's recorded ratios (from 2026-10-09), how
  often the open-based gate agrees with the one the tick saw. Paper never gated; live-only.
- **The entry.** The 10:00 ET minute stamp's quotes and index price. A paper entry is also dropped when
  the minute lacks a two-sided quote on any needed leg, the ATM call mid, or the index price; counted
  by reason, year and arm. Refusals are not trades, as in paper.
- **The triggers**, on the minute series, as the module defines them: latches advance only on a
  measured minute (a missing quote, a zero implied volatility or a boundary delta of exactly minus one
  is unmeasured and evaluates nothing), never carried on a guess. Delta is the store's near-wing put
  delta at the minute. The module ticks every 60 s on its own cache, so the minute store is the same
  cadence but a different feed (the feed's delta has run about a minute behind the cache's); the
  rival below stresses it.
- **The gamma flip (`flip` only).** A historical flip series exists in the 60-second store
  (`gex_regime_history.zero_gamma`, about every five minutes, 2022-05-16 on), but it is the 0DTE
  chain's flip, and the module computes `flip` from the position's **own weekly expiry** chain and
  open interest. They are different numbers, so that series is not used for the judged arm. The
  judged `flip` is **recomputed** each minute from the weekly chain in the minute store (the put
  implied volatility gives each strike's gamma; call gamma at a strike equals the put's) and the
  weekly open interest of that morning, fetched by the same vendor path as the open-interest store
  (to be queued; availability for the SPXW weeklies is **not yet verified**). **Validation before
  any outcome is read:** the recomputed flip against the flip the paper loop recorded in
  `bwb_trigger_ticks` since 2026-08-24; within 5 index points on at least 80% of measured ticks, or the
  `flip` arms are recorded **no verdict (input)** and nothing about them is read. They stay in the
  twelve for Holm either way.
- **Settlement.** Index close for the expiry day (the PM weekly settles on it), from the exchange's
  public daily file, cross-checked against the store's 16:00 index price (differences reported); the
  module's paper settles on the last cached tick and its live ledger on the official print, so this is
  nearer the live basis. Intrinsic per leg, the $5 settlement fee per distinct in-the-money symbol.
- **Fills and costs.** The paper ledger's basis, so the replay and the paper check read the same fills:
  each leg filled at mid, plus slippage of 12.5% of its quoted spread (capped at 15% of mid) on
  every opening leg, which is 25% of the way from mid to the natural side, plus `core.fees` opening
  fees (`ic_open_fee`: 4 legs and 2 sells for the fly, 2 legs and 1 sell for the add-on) and the
  settlement fee. **Verdict basis: this one.** Stressed (the pass must survive each): halfway to the
  natural side (25% of the spread, uncapped) and 1.5 times the fees. Reported: the natural side, and
  the live basis (mid less 0.05 on the fly's net credit and on the add-on's).
  - **The `delta-shortstop` exit.** bwb has no closing order in paper or live (positions settle), so
    the exit basis is declared here: the suite's close schedule (`ic_close_fee`, 2 legs) and slippage
    of 12.5% of each leg's spread, at the quotes of the first minute the condition holds; a minute
    without two-sided quotes on both legs waits for the next. Stressed with the fill one minute later.
- **Measured,** per trade (one entry of one arm, the whole position): net dollars in columns that add
  up (fly gross, add-on gross, entry fees and slippage, exit fees and slippage, settlement), and the
  number of fires. Per arm: the mean, its week-clustered bootstrap, the win rate, the worst trade, the
  largest five losses, the fire count by distinct expiry week (never by position), and net by year.
- **Windows** (the store covers expiries 2022-05-20 to 2026-10-16, entries from 2022-05-13):
  - **Design:** 2022-05-13 to 2024-12-31 (about 650 entries). Halves: 2022-05-13 to 2023-09-29 and
    2023-10-02 to 2024-12-31.
  - **Holdout:** 2025-01-02 to 2026-08-21 (about 415 entries), run after the design whatever it shows,
    counting only after a design pass.
  - **The paper ledger** (entries from 2026-08-24; the store runs to the 2026-10-16 expiry) is used
    **for validation of the replay's entries only**: same expiry, same strikes and entry credit within
    a tick (0.05) on at least 90% of the paper entries on overlapping dates, below which the study
    stops and the replay is fixed first; add-on fire-or-not and fire day agreement is reported, not
    gated. Paper outcomes have been read (by the coordinator on 2026-10-30), so paper is not a
    holdout, and nothing in the replay reads a paper outcome.
  - **Minimum trades:** 300 entries per arm in the design window (250 gated), 150 (125 gated) in the
    holdout; an add-on arm also needs at least 40 fires in distinct expiry weeks in the design window.
    Fewer is no verdict for that arm and state.
- **Pass-or-fail rule,** per arm and gate state (twelve tests), all in the design window:
  - the mean net per trade above zero, with a one-sided week-clustered bootstrap p (resampling
    settlement weeks, 2,000 resamples, seeded), **Holm across the twelve** at 0.05;
  - for the five add-on arms, the mean net per trade also above `control`'s on the **same entries in
    the same gate state** (paired, all entries, week-clustered, one-sided p under 0.05). This is the
    unconditional pairing: the add-on study's pairing over fired cohorts only was selected by
    construction (triggers fire on down weeks, where a daily trade loses) and is not reused. A variant
    (`delta-dte3`, `delta-shortstop`) is also reported against `delta`, not judged on it;
  - the mean net positive in each time-half;
  - still positive at halfway-to-natural fills and at 1.5 times fees (each);
  - still positive with the five worst settlement weeks removed;
  - then, in the holdout, the mean net above zero and (for an add-on arm) above `control`'s.
  - **Reading a partial pass.** A gated arm passing while its ungated arm fails is recorded as that, and
    never as the structure passing. An add-on arm that is positive but not above `control` is recorded
    "pays, not for the add-on".
- **Rival explanations, each with its check:**
  - *One regime (the 2024-25 rebound):* both time-halves, then the holdout; net by year and by
    calendar quarter reported; the result with the 2022 drawdown removed and with 2025 removed, reported.
  - *Tail weeks decide the mean:* the five-worst-weeks removal in the rule; the share of net lost in
    the worst 5% of weeks reported.
  - *Model against real fills:* the earlier modelled credit missed the recorded entry credits by about
    14 cents; here quotes are real and the same-date paper entries validate them. The stresses above
    and the natural side bound what fill quality alone can do; the quote age of a minute stamp is nil,
    so the replay is kinder than a cache that is up to 300 s old, reported as a limit.
  - *Trigger timing at the minute against the module's tick:* the same series run with the trigger read
    one minute late, and the validation's fire-day agreement against paper.
  - *The gate chosen by looking:* declared now, one threshold (the live bar), both states judged, the
    open-based ratio with the prior-close reading reported beside it. No other threshold is run.
  - *A fire on one ladder row counted many times:* fires counted by distinct expiry week; the
    bootstrap resamples weeks.
  - *The `flip` input is not the module's:* the recomputed-flip validation above gates its verdict.
  - *A thin start or refusals dropping the big losers:* refusals and drops counted by reason, year and
    arm; a drop rate that differs by more than 5 points between the halves, or is highest in the
    worst years, is reported with the result.
- **Minimum detectable effect (B12), with the twelve tests.** Holm over twelve puts the first test's bar
  at one-sided p of about 0.004 (z about 2.6), against 2.4 for six tests: about 8% more effect
  needed than the six-test family the arms would otherwise carry, and the gated tests keep fewer
  trades (a further few per cent). The spread is an **assumption** until the design window's own
  replaces it before the holdout is read: the per-trade standard deviation about $200 (the structure's
  worst case is a few hundred dollars a lot and most trades win a little), and an effective sample
  about 0.4 of the trades because five ladder rows share each expiry week. On those, 650 design
  trades (about 260 effective) detect a mean of about $45 a trade at the Holm bar (about $40 at
  six tests, about $31 unadjusted), and a gated arm with 85% of the trades about $48. An add-on arm's
  paired difference concerns only the fired rows and is detected only if it is larger; a fail reads "no
  edge larger than about $45 a trade", which is about as large as the costs. The `flip` and `delta`
  variants with few fires will often be no verdict.
- **Name split (B9):** does not apply (one underlying).
- **Declared limits (B11):**
  - The 7 holiday-Thursday expiries (2023-04-06, 2024-03-28, 2025-04-17, 2025-07-03, 2026-04-02, 2026-06-18,
    2026-07-02) and the Thursday before each holiday (8-day entries for the next expiry) are
    not in the store (it was fetched for Friday expiries only); they need a fetch by the same
    committed code (`scripts/fetch_addon_weeklies.py`, registered in `stores.STORES`, extended to take
    those dates). Until fetched, those entries drop and are counted. Two Fridays (2024-06-21, 2024-06-28)
    were refused by the vendor and cannot be fetched; their entries (about ten) drop.
  - Minute quotes, not the module's cache ticks; the vendor's delta feed lags the cache's.
  - The store holds puts at all strikes and calls only as a 10:00 ATM strip (enough for the expected
    move); no call-side structure can be replayed.
  - About 0.4% of put rows carry no index price and 0.8% a zero implied volatility; they are
    unmeasured, not repaired.
  - The VIX9D/VIX gate is read from daily opening prints, not the tick.
  - The cost and exit bases are declared stand-ins where bwb has no rule (the close of the add-on).
  - Early exercise is not an issue (cash-settled, European). The SET-print AM monthly is not traded.
  - The `flip` input is recomputed from end-of-previous-day open interest; the module's cache refuses
    `flip` until open interest arrives.
  - One lot of a $5/$10 structure at index levels from about 3,700 to 7,000; the wings are fixed in
    points, so the same rule is a different trade in 2022 and 2026. Reported by year, not scaled.
- **Data:** the minute store `addon_weeklies.db` (registered in `stores.STORES`, built by
  `scripts/fetch_addon_weeklies.py`; 222 Friday expiries with 5 entries each, minute stamps from 09:30
  to 16:15, puts at all strikes in a band around spot), the 60-second store for the paper-validation
  gamma series, the exchange's public daily volatility and index files under `~/.cherrypick`, the
  paper ledger (validation only), `cherrypick.core.fees`. Everything fetched and derived stays under
  `~/.cherrypick/data/backtest/results/research/item35/`; this plan records the verdict without figures.
  The study is a new script (`scripts/bwb_item35.py`), run as `item35/plan`, `item35/fetch`,
  `item35/validate` and `item35/study`, queued as research-queue rows.
- **Decides** (the user decides everything about retirement and live; this item only supplies evidence):
  - **An arm passes:** bwb continues in paper with that arm, a paper check begins from the declared
    boundary, and the live pilot may be reconsidered by the user. It is *eligible to be considered*:
    nothing about live gates changes here.
  - **Every arm fails or has no verdict:** the evidence the user's rule needs before any retirement
    decision is in hand (the real-quote record of the whole rule, arm by arm, with the minimum
    detectable effect), and the retirement decision stays the user's. Paper keeps running meanwhile.
  - **A variant passes where `delta` fails:** recorded as the variant only, a new arm at a declared
    boundary, never `delta` itself brought back.

### 36. pmcc's own rule on real quotes: the shield, three variants and two baselines

*Source: the user, 2026-10-30. The rule: no module is retired without a backtest of its own rule. The
user approved keeping pmcc paper running unchanged and declaring this backtest, with these variants.
**Look-alikes, said out loud.** Item 5 (the EMA-driven short delta) failed, and is not retried. The
2026-10-12 roll-policy replay found the early roll losing on five of six symbols, so the early-roll
policy is not varied here (`shield` carries it as pmcc runs it). The 2026-10-19 covered-call delta study
(shares, 16 names, daily paths) found the call overlay trailing the shares on most names with the index
the exception; it is the closest prior for `hold-30d` and `hold-30d-monthly`, and it did not use a
year-long long. The 2026-10-04 shield study priced everything off a model and said so: its short-premium
edge flipped sign within a tenth of the implied-volatility level, and the skew was uncalibrated. This
item replaces the model with quoted chains. **Seen before declaring:** the model replay's aggregate
tables for the shield arms (2011 to 2026, modelled prices), the three paper arms' first weeks (the
ledger is not read for this item), and the stored chain coverage (counts of what is fetched; no price
or outcome). No real-quote outcome of any arm below has been computed.*

- **Question:** does pmcc's own rule, a year-long deep in-the-money call with a weekly short call sold
  against it, earn more than the long held alone, and more than a one-trade-a-year short put, once costs
  are paid on quoted prices? And does any of three declared changes to the short earn more than the
  rule as run?
- **Where the edge would live:** **the variance premium in the short call, net of the exposure it
  takes on.** A short call earns the premium option buyers pay for variance. It also sells equity
  reversal exposure the market does not reward (Israelov and Nielsen, "Covered Calls Uncovered", Financial
  Analysts Journal, 2015), and that exposure grows with how far in the money the strike sits. pmcc's
  0.70-delta short sits deep in the money. **What would falsify it:** the arm earns no more than the long
  alone after costs. The long alone carries equity beta, the year-long call's own variance cost and
  the 45-day close; any arm that merely adds net long delta in a rising market will look good against
  the put, so the long-alone baseline is the one that isolates the short.

## The arms (six judged, Holm across them)

Rules are read from `packages/pmcc/src/cherrypick/pmcc/engine.py` (`ARM_RULES`, `select_long`,
`select_short_by_delta`) and `management.py` (`evaluate`, `assignment_exposed`), not restated from
memory; the replay imports their parameters. What they share: the long is the third-Friday monthly nearest
360 days in [240, 540], chosen by delta in 0.88 to 0.92, no extrinsic fallback; held to 45 DTE, then closed
and re-bought the next session; closed early if P&L to date falls to -30% of the long's cost; the
account policy is `ira` (an expiring short is bought back at the close at any moneyness).

| arm | short | management of the short |
|---|---|---|
| `shield` | 0.70 delta (band 0.65 to 0.78), soonest Friday 5 to 11 DTE, above the long's strike | rolled on its expiry day; also early at 85% of its sale extrinsic decayed, or when spot reaches its strike; at most once a session |
| `shield_hold` | as `shield` | rolled on its expiry day only |
| `shield_guard` | as `shield` | `shield_hold` plus: an in-the-money short whose extrinsic is under 0.05 a share is rolled, at most once a session, never on a cash-settled leg |
| `hold-30d` | 0.30 delta (band 0.25 to 0.35), soonest Friday 5 to 11 DTE | `shield_hold`'s long and management |
| `hold-30d-monthly` | 0.30 delta (band 0.25 to 0.35), the listed expiry nearest 37 DTE within [30, 45] | held to its expiry, bought back at the close that day, the next sold |
| `hold-vrp` | `shield_hold`'s short | sold only on a Friday when the at-the-money implied volatility at about 30 days exceeds the underlying's realised volatility over the last 20 sessions; otherwise no short that week and the long runs alone |

- **Mechanisms, one each.** *`hold-30d`:* a far out-of-the-money short keeps the volatility premium with
  less of the equity-reversal exposure (the paper above). *`hold-30d-monthly`:* costs. Weekly rolling paid
  about $3k in fees over two months of paper; a monthly short collects its premium with about a quarter of
  the trades. *`hold-vrp`:* pmcc's own shield study found the short's edge flipping sign within a tenth
  of the implied-volatility level, so the short is sold only when implied volatility is above
  realised. The gate follows from that finding; it is one declared rule, not a search over thresholds.
- **Not included, said out loud.** Item 5's EMA-driven short delta (it failed); the early-roll policy
  (it lost five of six symbols in the 2026-10-12 replay); other delta levels such as 0.20 or 0.40 (a
  search the sample cannot afford under a Holm of six).
- **Measured arms' definitions that need a rule.** *Strict greater-than* in `hold-vrp`'s gate, no margin.
  *Implied volatility* for the gate: the Friday chain's expiry nearest 30 DTE within [21, 38], the mean of
  the call and put implied volatilities at the strike nearest the forward. *Realised:* annualised
  close-to-close over 20 sessions ending that day. The monthly arm takes the nearest-37 expiry that
  clears every declared ex-dividend date and, for single stocks, report date (pmcc's refusals); if none in
  the band clears, no short that cycle, counted.

## The baselines every arm must beat

- **`long-alone`:** the same long, no short, under pmcc's own long management (45-DTE close and the -30%
  stop on the long's own P&L). It isolates what the short adds. Also reported without the stop (a rival).
- **`put-benchmark`:** pmcc's own put benchmark: a short put nearest 20 delta at the same expiry the
  long uses, sold on the day the long is bought, held to 45 DTE, re-sold the next session. No stop. It
  shares the long's chain path, so it costs no extra fetch.

## Symbols and the XSP choice

- **Symbols:** the eleven in the deployed config. Funds (judged): XSP, QQQ, GLD, IWM, SLV, SMH. Stocks
  (reported, not judged): AMZN, TSLA, AMD, NVDA, PLTR.
- **XSP is replayed on SPY's chains**, labelled `XSP(SPY)` everywhere. Reasons: (1) XSP is absent from the
  fetched store, and the account's tier refuses index endpoints, so SPX has no underlying price to
  select strikes against; (2) SPY's chains are listed weekly for the whole window and are the deepest
  there are, where XSP's early years are thin and a thin chain's closing quotes would measure the
  spread, not the strategy; (3) the shield study already replays XSP on SPY. **What is lost and how it is
  handled:** SPY is American with dividends. The `XSP(SPY)` row is treated as cash-settled as pmcc
  treats XSP (no ex-dividend refusal, no assignment flags, `shield_guard` never rolls), so on that row
  `shield_guard` is identical to `shield_hold` by construction and is dropped from that row's
  comparison; it is not independent evidence. SPY's would-be flags and ex-dates are still counted and
  reported, as what an SPY holder would have met. One request at build time may check whether
  the options store serves a clean XSP chain; if it does, a 2023-2026 side check on XSP's own chain is
  open, but the declaration does not depend on it.
- **Minimum data (per symbol, per half):** a symbol is judged only with at least 4 completed long cycles
  and at least 120 priced weeks in the design half and 90 in the holdout (monthly arm: 25 and 18 priced
  cycles), and with no more than 20% of the half's weeks missing for unlisted expiries or unusable
  quotes. A symbol below the floor is reported, not judged; PLTR (listed 2020-09) cannot meet the design
  floor and is reported only. **Fewer than 4 judged funds is no verdict for any arm.** SMH's weekly
  expiries before 2023 are unconfirmed; the first fetch pass finds out, and a SMH short-less stretch
  counts against it under the floor, not as a zero.

## Measured

Everything per symbol, per arm and per baseline, on a weekly grid (Friday close to Friday close), one
contract, marked from the stored closing bid and ask each session.

- **% a year on capital.** Capital is the arm's cash tied up: the long's debit at each long entry, held
  fixed through the cycle (the put's is its cash-secured strike). The weekly return is the week's marked
  change in the whole position less that week's costs, over capital; a year is 52 weeks. Interest on the
  idle cash is left out of the primary measure; it earns the same rate per dollar posted in every
  arm and baseline and cancels in a paired difference. Reported beside it: the same difference per dollar
  of underlying notional (the put posts about three times the shield's capital, so the notional basis is
  the like-for-like one against it, the long-alone baseline the like-for-like one in capital).
- **Paired difference.** Per symbol and week: the arm's weekly return minus the baseline's. The pooled
  measure is the equal-weight mean across judged funds of each symbol's mean paired difference. Both
  baselines are subtracted separately.
- **Windows.** Reported per symbol and pooled, per half, and per five-year window starting each January
  2016 to 2021 (six overlapping windows; the shield study's 11-of-15 framing). The windows read the
  holdout, so they are tabulated only at the holdout read.
- **Drawdown.** Maximum peak-to-trough of the cumulative weekly return on capital, per symbol and arm,
  against the baselines'. Reported and read, not a gate (see the rule).
- **The short's contribution** (arm less `long-alone`) split into premium collected, the short's
  mark-to-market, fees, settlement and slippage, in their own columns (the money layout).
- **Beta-adjusted alpha.** The regression intercept of the paired difference against `long-alone` on the
  underlying's weekly return, per symbol. Reported. It tells a reader whether a failure of the raw
  rule is lost equity beta or lost premium; it does not change the verdict.

## Fills, fees, assignment and dividends (pmcc's basis)

- **Fills and fees:** pmcc's paper basis, imported not rewritten: mid of the stored closing bid and ask
  with slippage as the suite's cost model charges it (`engine.leg_costs` over `cherrypick.core.fees`,
  the module's `tastytrade_costs` overrides), commissions per its stack, $5 settlement event on every
  in-the-money expiry. An action is refused, not filled, on a missing side, a crossed quote, or a leg
  spread over `max_leg_spread_pct` (0.25), as pmcc refuses it; refusals are counted per arm. Everything
  happens at the close's quotes: pmcc rolls at the close minus 60 minutes with a buyback deadline 20
  minutes from the bell, so the replay is a close-quote approximation (a declared limit).
- **1.5x costs:** slippage, commissions and settlement all scaled by 1.5.
- **Early assignment: measured, never modelled.** No exercise is simulated. On every held in-the-money
  short on a physical-settlement symbol the daily mark is tested with `management.assignment_exposed`
  (extrinsic under 0.05 a share) and the sessions and weeks flagged are counted per arm and symbol. Only
  `shield_guard` acts on the flag. Pin risk is not modelled.
- **Dividends:** a short whose life [sale day, expiry] spans a declared ex-date is refused, on physical
  symbols only, via `core.settlement.ex_date_in_span`; in the replay the ex-dates are the dividend history
  already on disk (GLD and SLV pay none), and the weeks without a short are counted. For single stocks,
  report dates from the local earnings calendar likewise refuse a short across an announcement (the
  `earnings_span` refusal).
- **Splits:** a cycle that spans a split of its underlying (NVDA, AMZN, TSLA) is dropped from every arm
  and baseline and counted, as pmcc treats a split mid-position as a measurement break. Spot is the
  unadjusted close; where only an adjusted series is at hand, spot comes from put-call parity at the
  strike nearest the forward on the shortest expiry, and is checked against the bar on unsplit days.
- **Greeks.** The store holds bid, ask, close and volume, no implied volatility or delta. Delta is
  computed from the mid's implied volatility by Black-Scholes with the 3-month T-bill rate and the
  trailing dividend yield, no early-exercise premium (a declared limit: pmcc selects on the broker's
  delta, which on American deep-in-the-money calls runs below parity). A strike with no solvable
  implied volatility is skipped as pmcc skips a strike with no delta.

## Windows and the pass rule

- **Design:** 2016-01-01 to 2021-12-31 (six years, the bull market, the 2018 fourth-quarter fall and the
  2020 crash). **Holdout:** 2022-01-01 to 2026-10-23 (the 2022 bear, then the recovery), read once.
  The split is the only calendar one that gives each half a drawdown and the long cycles their floor.
  There is no untouched forward check for the three variants (they have no paper twin); a passing
  variant would get one by joining paper at a declared boundary.
- **Pass rule, in the design window, per arm, against each of the two baselines (an arm passes only if
  every clause holds against both):**
  - the pooled mean paired difference above zero, with the one-sided 95% lower bound above zero from a
    stationary bootstrap over calendar weeks (mean block 8 weeks, weeks resampled jointly across
    symbols, 20,000 resamples, seeded);
  - the arm's p-value is the larger of its two baseline p-values; **Holm at 0.05 across the six arms**;
  - above zero on at least two thirds of the judged funds (rounded up);
  - above zero in each time-half of the design window (2016-18 and 2019-21), pooled;
  - above zero, pooled and on at least two thirds of the judged funds, at 1.5x costs;
  - above zero, pooled, with the best calendar year of the design window removed.
- **Then the holdout, read once, for the arms that pass the design:** the pooled mean paired difference
  above zero against both baselines, above zero on at least two thirds of the judged funds, and at 1.5x
  costs; and the six five-year windows' symbol-by-window cells (cells with under 40 weeks dropped)
  showing the arm ahead in at least two thirds of them against each baseline.
- **Drawdown reading, not a gate.** A pass whose maximum drawdown on capital is deeper than
  `long-alone`'s on a majority of symbols is reported to the user as a pass with that caveat.
- **Minimum detectable effect (B12).** Estimated from the volatility arithmetic of a 0.70-delta weekly short
  on a capital base of about a third of notional, not from any outcome: one symbol's mean paired
  difference over six years has a standard error of several points a year on capital, and pooling six
  correlated funds does not remove most of it. Only a difference of roughly the size of the whole
  premium collected can be told from zero in the design window; the 0.30-delta arms, whose short moves
  half as much, are measured somewhat more tightly. **A fail reads "no edge of detectable size", not "no
  edge".**

## Rival explanations, each with its check

- **One regime (a 2016-2021 bull).** Two halves with different regimes, per-year pooled tables, the
  best-year-removed clause, and the 2020 crash weeks reported with and without.
- **The long carries everything.** The `long-alone` baseline is in the pass rule itself; the short's
  contribution is shown on its own; the beta-adjusted alpha is reported.
- **Costs.** Fees, settlement and slippage in their own columns; the 1.5x clause; the weekly arms'
  trade counts beside the monthly arm's.
- **Skew.** The 2026-10-04 study's uncalibrated skew is replaced by the real chains' own: the short and
  the long are priced where the market quotes them. What replaces it is a different limit, the close's
  quote as a fill, handled by costs and the 1.5x clause. The replay's own WPUT check (the weekly
  at-the-money put on SPY, costs removed) is re-run on quotes, tolerance 2 points a year as a
  data-and-engine sanity check, before any arm is read.
- **Stocks against funds.** The stocks are tabulated on their own with the same statistics and no
  verdict: the 2026-10-05 study found every tested stock negative on a model, and a stock cycle that
  spans a split or a report is thrown out, so a stock result rests on less. A pass that holds on the funds
  alone is the pass; the stocks are the measurement they were added as.
- **The gate and the cadence are not independent of the delta.** `hold-vrp` is `shield_hold`'s delta, and
  the 0.30-delta arms are compared with each other as well as the baselines in the report (reported, not
  judged).

## Guards, each shown to fail

Before an arm is read, and each broken on purpose to see the check fail:
- **Reproduction:** the replay's chosen long and short strikes match the paper ledger's recorded legs on
  the sessions both cover (since 2026-10-06), within one strike on at least 80% of entries. Broken by
  moving the long's delta band to 0.80 to 0.84.
- **Accounting:** each arm's P&L equals `long-alone`'s plus the short's, less costs, to the cent; rows add
  up. Broken by dropping a cost.
- **Coverage:** a week with a missing leg quote is a counted refusal, never a zero return. Broken by
  deleting one quote.
- **The WPUT check** above, on SPY.

## Data and the fetch

All options history is the licensed store's end-of-day chains (`eod_chain_quotes`), fetched by
`fetch-chain-paths` and `fetch-chains` into the marked vendor directory; the store is already declared in
`stores.STORES`. Nothing licensed enters the repo, a PR or an issue. Underlying closes and rates come
from the local bar store and the T-bill series on disk. Results stay under
`~/.cherrypick/data/backtest/results/research/item36/`; this plan records the verdict without figures. The
study is `scripts/pmcc_item36.py`, run as `item36/plan` (writes the request files and counts),
`item36/pilot` (SPY first: the fetch, the guards, no arm read), and `item36/study`.

**What a full test needs, per symbol** (about 564 Fridays, 2016-01 to 2026-10):

| request | unit | per full-history symbol |
|---|---|---|
| weekly short: each Friday expiry's path from 11 days out to expiry (covers entry, roll and early-roll sessions) | 2 seven-day calls per expiry | 1,128 |
| monthly short: the same expiries' earlier days, chosen by nearest-37 within [30, 45] (about 113 expiries) | 4 calls each | 452 |
| `hold-vrp` gate: the Friday chain of the expiry about 28 days out, each Friday | 1 call each | 564 |
| year-long long, its put and the long-alone baseline: one path from entry to 45 DTE, about 315 days, per long (about 13 cycles, 17 with stop re-entries) | 45 seven-day calls each | 765 |
| **gross** | | **about 2,900** |

- **Already stored** (counted, not assumed): about 108 Friday expiries per symbol of 2022 onward on the
  nine names the store holds for other items (a 14-to-18-day path each, which covers the 11-day need), about 23
  to 70 of the 28-day chains, about 23 to 73 of the monthly stretch, and no chain beyond 66 days to
  expiry, so **no year-long long is stored for any symbol**. SMH and PLTR have none of the weekly paths.
- **Net requests:** about 2,650 per full-history symbol (SPY, QQQ, IWM, GLD, SLV, AMZN, TSLA, AMD, NVDA:
  about 23,800), SMH about 2,900, PLTR about 1,560 (listed 2020-09), **about 28,200 in all**. Allow 10% more
  for expiries a symbol did not yet list (recorded `empty`, still one call each).
- **Time:** at about one second a request, the basis `chainfetch.py` states for the account's tier on
  single-name chains, 28,200 requests is **about 7.8 hours serial**; the fetcher takes up to three
  workers on one client, which if the tier lets them overlap would shorten it to a few hours, unverified,
  so budget the serial figure. Run it as research-queue rows, one per symbol, SPY first and the
  stocks last, resumable (the store skips complete requests).
- **Disk:** the year-long paths dominate (an ETF's long-dated chain is several hundred rows a session
  for about 315 sessions a cycle); the order of 2 to 3 GB in all, estimated, not measured. The SPY rows
  now stored are 1.5 million.

## Declared limits (B11)

Close-of-day quotes, not the intraday tape pmcc trades on; one contract, so lot-size effects on spreads
are invisible; delta computed, without early-exercise premium; early assignment unmodelled, counted;
an expiry not listed is a week without a short; `XSP(SPY)` is a stand-in; stocks lose cycles to splits
and reports; the three variants and `shield_guard` have no paper twin before a boundary and so no
live-fill check; an unlisted arm (other deltas, other expiries) is outside the sample by design.

## Decides

- **A pass:** the passing arm or arms continue in paper as pmcc's base, and a variant joins as a paper arm
  at a declared boundary (a measurement-affecting change, batched). The shield arms pass or fail by this
  same rule; one passing makes it the base, none passing leaves paper running unchanged.
- **A fail or no verdict:** the evidence the retirement rule needs, with the minimum-detectable-effect
  note beside it. Any retirement of the module or removal of a symbol stays the user's decision; paper
  keeps running unchanged until then.

### 37. A real-quote test of curve's own rule: the contango-gated VXX call spread, with two variants

*Source: the user, 2026-10-30. Their rule: no module is retired without a backtest of its own rule. They
approved keeping curve's paper unchanged and declaring this test. **Look-alikes, said out loud.** The
2026-10 history studies replayed curve on VXX's closes with a modelled mark path; the modelled `control`
and `noflip` lost after costs and the modelled `hook` could only lose its costs (`docs/history-studies-2026-10-b.md`).
A separate replay at recorded credits, held 20 sessions, put the $2 width slightly ahead. Both used
modelled option prices; this is the first test on real end-of-day quotes. **Seen before declaring:** those
aggregate verdicts and the module's own paper record (a handful of sessions, three arms). No real-quote
outcome of any arm has been read. `hook` is retired (it never fired) and is not tested.*

- **Question:** run exactly as curve runs it, on every session the gate allows, does the VXX call credit
  spread make money per trade after fees and slippage? And do two changes to its exits and its width
  make it do so where the base does not?
- **Where the edge would live:** **premium (the term-structure roll yield) and execution.** VXX decays in
  contango, so the underlying drifts down and a short call spread sells that drift with a defined loss.
  The gate (VIX/VIX3M under 0.97) is the signal; the spread is the structure. The earlier replays say the
  signal is real and the costs eat it. **What would falsify it:** a mean net per trade at or below zero
  after costs, or one that only holds in a window without a volatility spike.
- **The arms (judged, Holm across five):**
  - **`control`, `noflip`, `near`:** as curve runs them, read from `engine.py`, `management.py`,
    `regime.py` and the deployed config (below). `noflip` is `control` without the regime-flip exit;
    `near` is `control` on a 0.40-delta short.
  - **`hold-expiry`:** `control` without the profit take and without the 7-DTE close; held to expiry,
    or out on the flip exit. **Mechanism:** costs. Each early close pays the exit spread, and VXX's wing
    spreads run near half of mid. Most spreads expire worthless, so holding avoids that cost on most
    trades, at the price of exposure to a late spike, which the worst trade, the drawdown and the
    assignment count measure.
  - **`wide5`:** `control` at $5 wide. **Mechanism:** VXX's option costs are close to fixed dollars a
    leg, so a wider spread's costs are a smaller share of its credit and of its max loss. The $2 width was
    chosen from one day's quotes (2026-10-05) and a modelled mark path.
  - **Not tested, said out loud:** a 0.90 gate (worse in the 10-05 replay); UVXY options (the same trade
    at a fair price, no cost edge); entry time of day (end-of-day quotes cannot test it); other widths
    ($1, $3, $10: a search); `hook`.
- **The rule as curve runs it** (`packages/curve`, the deployed config; every number below is the
  config's, none is chosen here):
  - **Gate:** the day's VIX/VIX3M ratio under `contango_max` 0.97. A missing ratio is a refusal
    (`regime_unmeasured`), never an entry.
  - **Expiry:** the monthly-cycle Friday (holiday-shifted back) inside 25 to 60 days, the one nearest
    35 (`clock.target_expiration`). No candidate is a refusal.
  - **Short:** the call above spot whose delta is nearest `short_delta_target` (0.30; 0.40 in `near`).
  - **Wing:** the lowest listed strike at or above the short plus the width ($2; $5 in `wide5`).
  - **Quotes and refusals,** applied in the module's order:
    - a leg quote is usable when its ask is above zero and its bid is not above its ask
      (`core.streamcache.usable_quote`);
    - the short leg is refused when its spread exceeds 30% of mid;
    - the wing is refused only when its spread exceeds 30% of mid and also $0.25;
    - the entry is refused when its net credit after entry costs is under 10% of max loss
      (`min_net_credit_to_max_loss`, with quantity 2). The percentage-of-width floors are not applied
      because the deployed config declares the net floor;
    - no strike with a delta, no wing strike, or no chain, are refusals with their own reasons.
  - **Size and holding:** quantity 2, one open position an arm. Within a session curve enters first and
    manages second, so a close frees the slot for the next session's entry.
  - **Exits** (`management.evaluate`, in this order, then the exit-spread gate):
    - `close_dte`: 7 or fewer calendar days to expiry;
    - the **flip:** the session's ratio at or above `contango_max`. **The code closes on 0.97, not the
      1.0 the module's CLAUDE.md and config text say;** the test follows the code and the docs are
      corrected separately;
    - the **profit take:** the cost to close at mid at or under half the entry credit;
    - an exit is refused when any leg is wider than 30% of mid and also $0.05 (`exit_spread_blocks`), and
      is tried at the next session's quotes.
- **How the test runs it,** each session `D` in the window, in date order, one arm at a time:
  1. Spot is the raw VXX close from the technicals history; the ratio is that session's VIX and VIX3M
     closes (the exchange's public files already under `~/.cherrypick`).
  2. Open positions are marked at `D`'s end-of-day chain quotes and managed by the rule above. A close is
     at `D`'s quotes.
  3. If no position is open and the gate is open, the entry is planned on `D`'s chain by `engine.plan_entry`
     itself (the module's own function, fed the stored chain), so the selection and every refusal are the
     module's code, not a reimplementation.
  4. The entry check precedes management, as in the loop, so a position entered on `D` is first managed
     on `D+1`.
- **The delta:** the stored chain has bid, ask and last, not the feed's delta or IV. Each call's IV is
  inverted from its mid by Black-Scholes at rate 0 and no dividend, and its delta is
  `engine.bs_call_delta`, the module's own fallback (`short_selected_by = delta_computed`). A strike whose
  mid is at or under intrinsic has no IV and is skipped, as a strike with no delta is in the module.
- **Costs and fills (curve's paper basis, unchanged):**
  - Legs are priced at mid. The suite's cost model is charged on top, as the paper ledger charges it:
    `cherrypick.core.fees` defaults (`tastytrade_costs` is empty in the deployed config), meaning $1.00 a
    contract to open, clearing and regulatory fees on every contract, nothing to close, and slippage of
    0.125 of each leg's spread (capped at 0.15 of its mid) on each fill. VXX carries no index exchange fee.
  - **Settlement:** a spread that reaches expiry is settled at the raw expiry-day close. The paper
    ledger's physical decomposition applies: intrinsic value, $5 for each in-the-money symbol, and the
    delivered shares disposed at the next session's open with their share-side fees
    (`core.fees.assignment_fee`, `core.settlement`).
  - **Early assignment is measured, not modelled,** as the module does. Each session a short leg's
    extrinsic value is under `assignment_exposure_tv` ($0.05) it is counted as exposed. Reported: exposed
    sessions and trades per arm, and a sensitivity that closes each exposed trade at that session's
    natural fill. The verdict is the unadjusted paper basis, an upper bound (honesty rule 2).
  - **Stress:** every fee and slippage figure at 1.5x. Reported beside it, not judged: fills at the
    natural (buying at the ask, selling at the bid) and fills at mid with no cost.
- **Splits:** VXX's raw closes jump on three ex-dates in the local technicals history, each a one-for-four
  reverse split: **2021-04-23, 2023-03-07 and 2024-07-24.** No other day in 2019-05 to 2026-10 moves by
  more than a factor of 1.6 either way. A split ex-date inside a trade ends it at the session before, at
  that session's quotes, because an adjusted contract's deliverable and strikes change (as item 29).
  Those trades are counted and are kept in the verdict; the result without them is reported. An entry
  needs a short strike above the raw close and a chain that lists the strikes the plan reads, which turns
  away a session whose chain is the adjusted series. Share-side data (the reference below) is adjusted by
  the three ratios.
- **Measured,** per arm, per trade: net dollars for the whole position (credit, less exit cost or plus
  settlement, less every fee and slippage, in separate columns: fees, slippage, settlement) and net as a
  share of the position's max loss at entry. Per arm and window: the trade count, the mean and the median,
  net per calendar year, the worst trade, and the maximum drawdown of cumulative net dollars over closed
  trades, in dollars and in units of the arm's mean max loss. Quantity is held at 2 in every arm, so
  dollars are not comparable between widths; shares of max loss are.
- **Windows** (entries; a trade is followed to its close wherever that falls):
  - **Design:** 2019-05-13 to 2022-12-30. It holds 2020-02 to 2020-04 and 2022.
  - **Holdout:** 2023-01-03 to 2026-10-28, run flat from its first session, counting only after a design
    pass. It holds 2024-08 and 2025-04. A position still open on 2026-10-28 is left out and counted.
  - The window starts at 2019-05-13 because the local VXX history has no bars from 2019-01-30 to
    2019-05-10; filling the hole is a declared limit (below), not part of this test.
- **Pass-or-fail rule,** per arm, all must hold:
  - in the design window, the mean net share of max loss above zero, with the stationary-bootstrap
    one-sided p (trades in entry order, mean block 3, 20,000 resamples, seeded) passing **Holm across the
    five arms at 0.05**. One trade is one observation;
  - positive in each design half (2019-05-13 to 2021-02-26, and 2021-03-01 to 2022-12-30);
  - still positive at 1.5x costs;
  - then the holdout's mean net share of max loss above zero. The holdout's interval is reported.
  - **Minimum trades:** an arm with fewer than 30 closed trades in a window has **no verdict** for that
    window, and a half with fewer than 10 is not read. A no-verdict from refusals is stated as such, is
    not a fail, and means that arm's holdout is not read. `noflip` and `control` share every trade until a
    flip fires, so `flip_divergence_count` is reported beside the pair as their real difference.
- **Reported, not judged:** the refusal rate and the refusals by reason and year (below); the worst
  trade; the arm with each pre-declared episode removed; natural-fill and no-cost mean; the exposure
  counts; the sensitivity without the split-ended trades.
- **Rival explanations, each with its check:**
  - *One spike decides (2020-03, 2024-08):* both episodes are inside a window, so a pass cannot be
    "no spike in the sample". Each arm's trades open during 2020-02-19 to 2020-04-30, 2024-07-31 to
    2024-08-30 and 2025-04-01 to 2025-04-30 are listed with their nets and the mean is reported with
    them removed. A verdict that turns on one of them is called that.
  - *Refusals differ at the close from 10:00 to 10:30:* curve enters in the morning and the stored quotes
    are the day's last. The refusal rate by reason is reported against paper's (the share of sessions with
    the gate open and no fill, from `curve_entry_attempts`, read when the study runs). **A sample check:**
    one session in every 25 of the sessions the gate allows has the target expiry's chain fetched at
    10:00 to 10:30 from the intraday chain store, and `plan_entry` is run on it. Reported: how often the
    refusal and the strikes differ from the close's, and the vendor's own delta against ours on the
    short. Not judged. If the morning refuses much more or less often than the close, the verdict says
    how far it can be carried.
  - *Splits:* above, with the split-ended trades kept and removed.
  - *The signal without the spread:* gated short VXX shares are the reference. Short at the close of the
    session after a close with the ratio under 0.97, flat otherwise, close to close on the split-adjusted
    series, 0.05% a side, no borrow, no dividends. CAGR, drawdown and worst day per window, reported next
    to the arms. If the shares make money and the spreads do not, the cost is the structure's.
  - *Costs read too kindly:* the 1.5x stress is in the rule; the natural fill is reported.
  - *The Holm family is too narrow:* four of the five arms share the gate and most of their sessions, so
    the correction is conservative. It is kept.
- **Minimum detectable effect (B12):** recorded in the local result before the holdout is read: each arm's
  design standard error of the mean and the mean it would need for the bound to clear zero. With a few
  dozen trades whose outcomes run from small wins to a full loss, the standard error is several points of
  max loss, so only a mean well above the modelled replay's reading is detectable. **A fail reads "no edge
  that large", not "no edge".**
- **Declared limits (B11):**
  - end-of-day quotes: curve enters from 10:00 to 10:30 and acts on 60-second marks, so entry, profit take
    and flip are read once a day, and a profit take is found a session late;
  - delta and IV are ours, not the feed's;
  - the gate reads the day's closing ratio; the loop reads it at the entry tick (the 27 recorded paper
    sessions matched the close within a few hundredths);
  - early assignment is measured, not modelled, so the result is an upper bound;
  - ETN risks (the 2022 creation halt, issuer risk) are not modelled;
  - the local VXX history has no bars from 2019-01-30 to 2019-05-10 (about 72 sessions), so they are out;
  - no more than about 90 monthly expiries, so the design holds few trades.
- **Settled with the replay, before the history was fetched (2026-10-30):** none yet. Anything found
  while building the replay is written here before the first result is read.
- **Data:** the licensed end-of-day chain store (`eod_chains.db`), daily paths for the monthly VXX expiries
  from 2019-06-21 to 2026-12-18, each from the first session it can be the target to its expiry (or the
  last stored session). Fetched by research-queue `fetch-chain-paths` rows, not by hand. The sample check
  uses `fetch-intraday-chains`. Spot from the technicals bars, ratio from the public index files. The
  replay is `scripts/curve_item37.py`, run as `item37/plan`, `item37/fetch-chain-paths`,
  `item37/fetch-intraday` and `item37/study`. Results are derived from licensed data and stay under
  `~/.cherrypick/data/backtest/results/research/item37/`; this plan records the verdict without figures.
- **Decides:**
  - **A pass:** that arm continues in paper as curve's base, proposed for a declared boundary (a width,
    exit or delta change is measurement-affecting). If more than one passes they are all reported and the
    user picks; `control` stays unless a variant's design mean is higher.
  - **A fail or no verdict:** curve stays as it is in paper, unchanged, and this section is the evidence
    the retirement rule needs. Retirement stays the user's decision.
  - Curve's paper config is not changed while this item is open.

### 38. The calendars module's own rule at real quotes: does the weekly SPY double calendar earn after costs, entered Monday or the Friday before?

*Source: the user, 2026-10-30. Their standing rule is that no module is retired without a backtest of
its own rule; this is calendars'. They approved it with calendars' paper loop kept running unchanged.
The module is a forward exit-parameter experiment, so its paper ledger answers "which exit is least
bad"; it has never been asked whether the entry it holds every week, with the best of those exits,
earns anything after costs. **Published mechanism the Friday arm tests:** Jones and Shemesh (2018,
Journal of Finance, "Option Mispricing around Nontrading Periods") find option returns lower over
weekends, with little effect in S&P 500 index options. A calendar is short the front week and long
the back, so a weekend of differential decay is the thing the Friday-close entrance would own and the
Monday entrance forgoes; the paper's index-option null is the reason to expect SPY to show little,
and the test is whether it does. **Look-alikes, said out loud.** (1) The module's own read-side
exit-policy table (`exit_policies.py`), which replays a Friday-noon exit, profit targets, stops and
the strike-touch close over `path`'s recorded marks in paper; it has ranked exits, never the entry's
sign. (2) The advisor's paper experiments on a noon exit (`time_exit: fri_noon`, concluded by advisor
kill and removed from the bounds on 2026-10-15): the `noon-friday` arm below is that exit again,
at real quotes over years rather than a few paper weeks, not a new idea. (3) Step 0 of the time-spread
plan scored the retired calendars and found nothing that made money; it was a modelled read, not this
module's rule at real quotes. (4) Item 31 (the earnings module) and item 35 (bwb) are the same kind
of study for other modules and share this item's fetcher and fee code. **Seen before declaring,
honestly:** the paper ledger since 2026-08-24, whose outcomes the coordinator read on 2026-10-30; the
hold-to-settlement `path` arm loses in nearly every paper week, which is why `noon-friday` was
chosen, and no paper figure is used here. None of it is a holdout. No real-quote result for any arm
has been computed.*

- **Question:** does the module's double calendar earn more than zero per trade after the suite's
  costs, entered on Monday morning (`control`) or at the previous Friday's close (`friday:control`),
  and does it do so if entered only when SPY's implied volatility is low (`low-ivr`) or if it is closed
  at Friday noon instead of the last fifteen minutes (`noon-friday`)?
- **Where the edge would live:** a **risk premium and a decay differential at once**. A calendar sells
  the front week's time value and buys the back week's, so it earns when the front decays faster than
  the back and spot stays near the strikes. The strikes sit about 0.85 expected moves either side, so
  the structure is a bet that the week is quieter than the straddle priced, and that the term structure
  is not already pricing the difference. The Friday arm adds a **calendar effect** (the weekend's
  decay, the published mechanism above). `low-ivr` is a **selection signal**: a calendar is long the
  back week's vega, implied volatility mean-reverts, so an entry when it is low gives that vega a
  tailwind and one when it is high fights it. `noon-friday` is **payoff shaping and timing**: the
  short legs' gamma is steepest in Friday's last hours near the short strikes, so an earlier close
  avoids the steepest part of the path (its price is the extra spread paid at noon on the long legs and
  the theta given up). **What would falsify it:** a mean net per trade of zero or less after costs in
  a half; or a positive mean that lives in a few big-move weeks; or a gate or an exit that helps only
  because it was chosen after the result.
- **The rule, from the module's own code** (`clock.py`, `engine.py`, `management.py`, the deployed
  config):
  - **Week and expiries.** The entry session is the week's Monday (the next trading day of that week if
    Monday is closed). Front (short) legs expire on the week's last trading day; back (long) legs on
    the first trading day of the following week. The ordinary week is tagged `dc_4_7`
    (`dc_7_10` for the Friday arm). The module never pools tags; **judged here: ordinary-tag weeks
    only** (`dc_4_7` for the Monday arms, `dc_7_10` for the Friday arm). Holiday-shifted weeks
    (`dc_3_6`, `dc_4_8`, Thursday fronts) are counted and reported, not judged and not fetched.
  - **Expected move and strikes.** `core.structures.expected_move` from the front expiry's ATM call and
    put mids with the 0.85 factor; put strike at spot less the move, call strike at spot plus it, each
    the strike quoted on **both** expiries nearest the target (`engine.plan_entry`). A side whose debit
    (back mid less front mid) is zero or less refuses the week (`non_positive_debit`); no common
    strike refuses (`no_intersection_strike`); no ATM quote refuses (`no_em_quotes`). One lot of each
    side (the double is two positions).
  - **Entry time.** Monday arms: the first bar from 10:00 ET whose chain meets the entry rules, through
    12:15 (the module retries each tick to the window's end; at real quotes the 10:00 bar will almost
    always pass). Friday arm: the first passing bar from 15:50 to 16:00 on the previous trading day,
    **only after** that session's Monday-regime exits are done (the module's state gate; the replay
    enforces it by order: the exit marks are fixed before the Friday entry bar is read).
  - **Exits.** `control`: every leg closed in the window 15:45 to 15:55 on the front expiry, each bar
    gated by `core.spreadbook.exit_spread_blocks` (a leg is too wide only when wide in percent,
    over 25% of mid, **and** in money, over $0.05, per leg); a window with no passing bar falls through
    as in the module: shorts settle, longs are sold at the Monday disposition (09:45, bars to 09:55).
    `path`: nothing closes; shorts settle on the Friday close, longs are sold at the Monday
    disposition. `friday:control` and `friday:path` close the same way on the same expiries.
  - **Ex-dividend weeks.** A week with a declared SPY ex-date in `[entry session, back expiry]`
    (Friday arm: from the entry Friday) is skipped, as the module skips it. The historical dates are
    declared from the issuer's distribution schedule and written to the run card before any fetch
    (see the open choices in the plan step); they are never computed.
- **The arms judged (four; Holm across four).**
  - `control`: the Monday entry above, all legs closed in the Friday window.
  - `friday:control`: the Friday-close entry above, all legs closed in the following Friday's window.
    Its strikes are its own (the module's design: a seven-day expected move at the Friday close), so it
    is judged on its own mean. Reported beside it, not judged: the difference to `control` on the
    weeks both traded (the two skip different weeks, so no surface assumes a match), and the module's
    paired-debit read, the Monday-moment price of the strikes the Friday arm bought, which needs no
    extra request (the Monday bar is already fetched).
  - `low-ivr`: `control`, entered only when SPY's 30-day at-the-money implied volatility is in the
    lowest third of its trailing range. **The reading, declared:** each Friday at the 15:55 bar (known
    before Monday), the two standard monthly expiries bracketing 30 calendar days give the vendor's
    at-the-money implied volatility (call and put mean at the strike nearest spot), interpolated in
    total variance to exactly 30 days; the **rank** is (reading less the trailing minimum) divided by
    (maximum less minimum) over the trailing 52 weekly readings (the same 252-session window at weekly
    sampling, a declared saving: daily readings would add about 5,000 requests); entered when the rank
    is **at or below one third**, one threshold, no other run. A week with a missing reading is
    refused as the live module would refuse an unmeasured regime. Reported beside it: the same rank
    from the public 30-day volatility index (zero requests), and the agreement of the two.
  - `noon-friday`: `control`'s entry; every leg closed at the first passing bar from 12:00 ET on the
    front expiry's day, the same gate, falling through to the `control` window if no bar to 12:10 passes.
  - **Reported, not judged (recording arms):** `path` and `friday:path`, with settlement and the
    delivered shares' disposal as below.
  - **Not included, said out loud.** Other strike distances (a search over a parameter the module
    does not vary); event skipping around Fed days, CPI or earnings (there is no clear sign for a
    calendar: a long back week gains from a vol rise, a short front loses from a move, and the sign
    depends on which dominates); the read-side grid's profit targets, stops, strike-touch and
    Thursday-close exits (they need the whole week's path, which this fetch does not hold, and they stay
    a forward paper question); the advised twins (they cannot arm in a replay).
- **Data.** Everything comes through the intraday chain fetcher built for item 31
  (`packages/backtest`: `intradayfetch.py`, `fetch-intraday-chains`, store `intraday_chains.db`,
  client `ThetaDataClient.chain_window`, already registered in `stores.STORES`). A request is a symbol,
  a session, an expiry and a window; SPY, one request returns every strike and both sides at each bar
  with the bid, ask, delta, implied volatility and the underlying's price. Per fetched week:

  | request | session and expiry | window |
  |---|---|---|
  | Monday entry, front | entry session, front expiry | 10:00 to 12:15, 15-minute bars (10 bars) |
  | Monday entry, back | entry session, back expiry | the same |
  | Friday noon, front and back | front expiry's day, each expiry | 12:00 to 12:10, 5-minute bars |
  | Friday close, front and back | front expiry's day, each expiry | 15:45 to 15:55, 5-minute bars |
  | Monday disposition (`path`, and any fall-through) | back expiry's day, back expiry | 09:45 to 09:55, 5-minute bars |
  | Friday entry, front and back | previous trading day, each expiry | 15:50 to 16:00, 5-minute bars |
  | 30-day reading, the two bracketing monthlies | previous Friday, each | the 15:55 bar |

  That is 11 requests a week. **The bar choice, stated:** the judged Monday entry is the 10:00 bar (the
  first bar the module's window opens on); the 10:30, 11:30 and 12:00 bars of the same request are
  reported as sensitivities at no extra request. The 16:00 bar of the Friday window may not exist;
  the 15:50 and 15:55 bars are the judged ones and a request with no 15:50 or 15:55 bar is counted by
  reason. Settlement spot is the official SPY close (broker daily bars first, the history's end-of-day
  file as the cross-check, differences reported); the 15:55 bar's underlying is the sensitivity.
  Other inputs: the public 30-day volatility index file, the declared ex-dividend dates, the paper
  ledger (validation only), `cherrypick.core.fees` and `core.structures`.
  - **Window start, found not assumed.** The structure needs a Friday and a following-Monday SPY
    expiry in the same week. Monday and Wednesday SPY expiries are believed to date from somewhere
    between 2016 and 2022; this is **not verified**. A listing step (one expirations call a year, about
    11 calls, counting nothing else) finds the first Monday for which both exist in the vendor's
    history; the start **S** is that Monday, written to the run card before any chain is fetched. A
    **coverage pilot** then draws 40 weeks by a seeded hash of the date (never by outcome), across the
    years from S, and fetches their 11 requests; the declared rule is that S moves to the first year
    from which **every** later year has at least 90% of the pilot's requests complete (rows at the
    needed bars, with delta, implied volatility and the underlying present), and the study runs on
    that one basis, never mixed. The pilot reads row counts and bar presence, never a price into a
    result. It also tests the 15-minute interval and the 16:00 bar.
  - **Request count and time.** About 42 judged-candidate weeks a year (52 less holiday-shifted weeks,
    ex-dividend weeks and Good Friday), 11 requests each, plus the 52 weeks of warm-up readings (104
    requests), plus about 10% for retries and refusals.
    - **If S is September 2016** (the optimistic reading): about 424 weeks to the week of 2026-10-19,
      4,660 requests, 4,800 with warm-up, about **5,300** with retries. At the pace item 31's smoke test
      saw (about 0.4 to 1.0 seconds a request) that is **35 minutes to 1.5 hours**, in the research
      queue, holding the one vendor session (nothing else may use the options history meanwhile).
    - **If S is May 2022** (the pessimistic reading, with item 35's store): about 186 weeks, 2,050
      requests, 2,150 with warm-up, about **2,400** with retries, **16 to 40 minutes**. This window
      cannot meet the minimum below, so the study would read no verdict; it is stated now so that is
      not a surprise.
    - Rows: strikes are bounded either side of spot as the minute store does (50), about 200 rows a
      bar-chain; about 43 bar-chains a week, about 8,600 rows a week, about 3.8 million rows over ten
      years. Unbounded, about five times that.
    - The exact count and the pace replace these figures after the listing step and the pilot's first
      20 requests.
  - Everything fetched and derived stays under `~/.cherrypick/data/backtest/results/research/item38/`;
    this plan records the verdict without figures. The study is a new script
    (`scripts/calendars_item38.py`), run as `item38/plan`, `item38/pilot`, `item38/fetch`,
    `item38/validate` and `item38/study`, queued as research-queue rows.
- **Fills and costs: the paper ledger's basis,** so the replay and the paper check read the same fills.
  Each opening and closing leg at mid plus slippage of 12.5% of its quoted spread, capped at 15% of
  mid (`core.fees` default, calendars' config sets no override): that is 25% of the way from mid to the
  natural side. Fees from `engine.entry_cost` and `engine.close_cost` (`core.fees.spread_entry_cost`
  and `spread_close_cost`: SPY carries no index exchange fee), the $5 settlement event per in-the-money
  symbol (`ic_expire_fee`), and for `path` the physical-settlement terms below. **Verdict basis:
  this one.** Stressed (the pass must survive): **1.5 times all modelled costs** (fees and slippage
  together). Reported, not required: halfway to the natural side (25% of the spread, uncapped, twice
  the paper's slippage), the natural side, and mid with no slippage.
  - **The spread gate and the strike window,** applied to the stored quotes as the module applies
    them. Entry: `strike_window_pct` 0.04 admits only strikes within 4% of spot into the entry
    snapshot; the module has **no** spread gate at entry (its gate acts on exits), so an entry leg
    wider than 25% of mid is counted and reported, not refused. Exit: a leg wide in percent and in
    money blocks that bar; the next bar in the window is tried; none passing means the fall-through
    above. Stored quotes have no age; the module's quote-age test (300 seconds) cannot bind and its
    absence is a stated limit, kinder than a cache.
  - **Settlement (`path`, `friday:path`, and a fall-through).** SPY is physical: an in-the-money short
    hands over 100 shares per contract at the Friday close; the shares are booked at the settlement
    spot and disposed at the Monday 09:45 bar's underlying, with the module's share-disposal costs and
    the $5 event (`engine.assignment_from`, `core.fees.assignment_fee`); the long legs are sold at
    the Monday bar's quotes through the close cost. Calendars' dividend skip stands in for early
    assignment, as in paper.
  - **Refusals.** Refused weeks are not trades, as in paper. Counted by reason (`no_em_quotes`,
    `no_intersection_strike`, `non_positive_debit`, no usable bar, ex-dividend, calendar shift), by
    year and by arm; exits that fell through counted by arm and year. **Compared with paper's:** the
    paper ledger's skipped-week reasons since 2026-08-24 (its first Monday took no position on a
    stale cache) are tallied by the same names. The replay has real minute-stamped quotes, so
    feed refusals (`no_fresh_quotes`) have no counterpart and the comparison is on the reasons that do:
    the debit and strike refusals and the exit-gate fall-throughs. A rate that differs by more than
    5 points between the halves, or is highest in the worst years, is reported with the result.
- **Measured,** per trade (one week of one arm: the double, both sides, one lot each): net dollars in
  columns that add up: gross at the entry and exit marks, entry fees, entry slippage, exit fees, exit
  slippage, settlement and share-disposal costs, and the net. Per arm: the mean, its week bootstrap,
  the win rate, the worst trade, the largest five losses, net by year and by quarter, and the entry
  debit (so net is also reported as a share of the debit; SPY roughly tripled over the window, so the
  dollar size of a lot differs by year and the share is the scale-free check, **not scaled**).
- **Windows** (S per the listing step and pilot; end: the week of 2026-10-19, whose Monday
  disposition falls on 2026-10-26; the week of 2026-10-26 is not included):
  - **Holdout:** entries 2024-01-02 to 2026-08-21 (about 105 judged weeks), run after the design
    whatever it shows, counting only after a design pass.
  - **Design:** S to 2023-12-29. Halves: at the median judged week, the date written to the run card.
  - **If S is after 2019-12-31,** the split moves before any quote is read to design S to 2024-12-31
    and holdout 2025-01-02 to 2026-08-21 (about 75 weeks), so the design is as long as the history
    allows; the minimums below then decide whether there is a verdict.
  - **The paper ledger** (entries from 2026-08-24, to the week of 2026-10-19) is used **for
    validation of the replay's entries only**, since it is not a clean holdout and its outcomes have
    been read. Rule: the same expiries, and on at least **75% of the overlapping Monday weeks and of
    the overlapping Friday weeks** the same strikes on both sides and each side's debit within $0.10
    (the paper's 30-second tick against a 15-minute or 5-minute bar moves spot; the strike is the
    check, the debit the tolerance), else the study stops and the replay is fixed first. With about
    ten Monday weeks and seven Friday weeks this is a coarse check and is said to be one; the entry
    bar nearest the paper's recorded entry time is also run and reported. Exit and
    settlement agreement on those weeks is **reported, not gated**, and nothing in the replay reads a
    paper outcome.
  - **Minimum weeks:** 150 judged weeks per arm in the design window (50 for `low-ivr`) and 60 in the
    holdout (20 for `low-ivr`). Fewer is no verdict for that arm. If S is September 2016 the design
    holds about 290 weeks; `low-ivr` about a third of that.
- **Pass-or-fail rule,** per arm (four tests), all in the design window:
  - the mean net per trade above zero, with a one-sided bootstrap p resampling **weeks** (one trade per
    arm per week; 2,000 resamples, seeded) and the lower bound of the 95% interval above zero,
    **Holm across the four** at 0.05;
  - the mean net positive in each time-half;
  - still positive at **1.5 times the costs**;
  - still positive with the five worst weeks removed;
  - `noon-friday`: the mean net also above `control`'s on the same weeks (paired, one-sided p under
    0.05);
  - `low-ivr`: the mean net also above the mean of `control` on the weeks it did **not** enter
    (two-sample, week bootstrap, one-sided p under 0.05); a `low-ivr` that is positive but not above
    those weeks is recorded "pays, not for the gate" and means `control` itself is the thing;
  - `friday:control`: no pairing is required; the difference to `control` is reported;
  - then, in the holdout, read once: the mean net above zero and, for `noon-friday` and `low-ivr`,
    above their comparators.
  - **Reading a partial pass.** An arm passing while `control` fails is recorded as that arm only;
    `control` failing and `friday:control` passing is recorded as the Friday entry only.
- **Rival explanations, each with its check:**
  - *One regime (a single volatility regime, a trending decade):* both time-halves, then the holdout;
    net by year and by calendar quarter; the result with each calendar year removed in turn (the
    worst two reported); the low-rank weeks counted by year for `low-ivr`.
  - *A few big-move weeks decide the mean:* the five-worst-weeks removal in the rule; the share of net
    lost in the worst 5% of weeks reported; the win rate beside the mean (a calendar wins small and
    loses on a move).
  - *The intraday bar choice:* the 10:30, 11:30 and 12:00 entry bars, the Friday entry at 15:50 and at
    15:55, the exit at each bar of its window, all reported as sensitivities of the same weeks; the
    sign must not depend on a bar the module did not name. The first-passing-bar entry is the judged
    one.
  - *The module's own exit-policy replay (`exit_policies.py`):* it already derives `control`, the
    Friday-noon exit and `path` (`expiry-longs-mon`) through the live cost stack and validates itself
    against the recorded arms to the cent. **Reused if it fits:** the replay builds week records
    in the shape `week_data` returns, from the stored bars (ticks at the fetched bars only), and calls
    `derive` for the three policies that need no other tick (`control`, `time-fri-noon`,
    `expiry-longs-mon`) and `validate_against_control` against a paper week to confirm the code reads
    it the same way. If `derive` cannot be fed without the ledger, its pure helpers
    (`_close_roles`, `_settle_shorts`) are reused and the validation is the cent check on the paper
    overlap weeks. A second derivation of the same rule would drift where nothing could see it.
  - *Model against real fills:* quotes are real, and the same-date paper entries validate them; the
    stresses above and the natural side bound what fill quality alone can do. Early SPY Monday
    expiries were thin, so the back leg's spread by year is reported and a mid-fill result in
    those years is not read as a fill.
  - *The entry quote is stale or one-sided:* a bar with a zero bid or ask on a needed leg is a usable
    bar only if the module would price it (`mid` from a two-sided quote); counted by year.
  - *Selecting the Friday arm's strikes by a seven-day move:* the paired-debit read at the Monday
    bar isolates what each entrance paid for the same strikes; reported beside the judged mean.
  - *The signal chosen by looking:* one threshold (one third), one reading declared; the public-index
    rank beside it; no other threshold is run.
  - *Dividend skips bias the sample:* the skipped weeks (about four a year) are the quarterly
    expirations; the judged population is ordinary weeks and says so, the same as the module's.
  - *Multiple comparison:* four tests under Holm; the sensitivities are reported and none is judged.
- **Minimum detectable effect (B12).** Holm over four puts the first test's bar at a one-sided p of
  about 0.0125 (z about 2.24). The trade-to-trade spread is an **assumption** until the design
  window's own replaces it before the holdout is read: per-week net standard deviation of one double
  about $90, weeks independent (one trade per arm a week). On those, 290 design weeks detect a mean of
  about **$12 a trade** at the Holm bar (about $9 unadjusted, and about **$16** at 80% power at the
  Holm bar); `low-ivr` with about 95 weeks about **$22** (about $29 at 80% power); `noon-friday`'s
  paired gap, with a smaller spread on the difference, is detected if larger than about $8 (an
  assumption: the paired spread is taken as half the unpaired). A fail reads "no edge larger than about
  $15 a trade", which is smaller than the calendar's costs, a figure that depends on the debit (the
  costs are the second number to read it against). If S is later and the design shorter, the same
  formula gives the larger figure the run card records.
- **Name split (B9):** does not apply (one underlying).
- **Declared limits (B11):**
  - Bars, not the module's 30-second cache ticks; the Monday entry is read at 15-minute bars and the
    Friday windows at 5 minutes.
  - The vendor's delta and implied volatility are used only for the `low-ivr` reading and the
    delta in reporting; the module's entry rule needs neither.
  - Calendars before the weekly structure existed on both expiries cannot be built; the start is the
    listing step's, not chosen.
  - Early assignment is not modelled (the module does not model it either; a skipped week stands in
    for ex-dividend risk, a real-money module would need the live prerequisites in the module file).
  - Settlement is the official close, the paper's is the last cached trade, so replay is nearer the
    live basis.
  - SPY's level changed about threefold, so a lot's dollar size differs by year; reported by year and
    as a share of debit, not scaled.
  - Weekly sampling stands in for 252 daily readings in the IV rank.
  - The historical ex-dividend dates are declared from the issuer's schedule and are only as good as
    that declaration.
- **Decides** (the user decides everything about retirement and live; this item only supplies evidence):
  - **An arm passes:** that arm continues in paper as calendars' base, a paper check begins from a
    declared boundary (`dc_4_7` and `dc_7_10` still never pooled), and calendars' paper config is
    changed only at that boundary. No live prerequisite in the module file is touched; a pass makes
    the arm *eligible to be considered*.
  - **Every arm fails or has no verdict:** the evidence the user's rule needs before any retirement
    decision is in hand (the real-quote record of the module's rule, arm by arm, with the minimum
    detectable effect and the weeks that decided it), and retirement stays the user's. Paper keeps
    running meanwhile, unchanged.
  - **Only `noon-friday` passes:** recorded as the exit only, the finding the module's grid already
    carries (a Friday-noon row) confirmed or refuted at real quotes, over years.
  - **Only `friday:control` passes:** recorded as the Friday entrance only, and Jones and Shemesh's
    weekend effect found in SPY where the paper found little in the index.

### 39. The contango module's own rule on real daily prices: the switch, a 9-day exit and a realised-volatility gate

*Source: the user, 2026-10-30. The rule: no module is retired without a backtest of its own rule. The
user approved keeping contango's paper running unchanged and declaring this backtest, with these two
variants. **Seen before declaring, said out loud.** `scripts/contango_replay.py` ran over SVXY and SHV
from 2018-03 to 2026-10 before the module was built (2026-10-05), and its aggregate table (CAGR,
maximum drawdown, switches, at 2 and at 10 bps a side, for `control`, `flipexit` and holding SVXY) is
in the module's CLAUDE.md. It was exploratory: the 0.97 and 1.0 thresholds were chosen having seen it,
it was never a declared test, it had no holdout (its span covers both windows below) and no stress
beyond the 10 bps line. So for `control`, `flipexit` and the SVXY baseline the holdout below is not
clean; the clean checks are paper (from 2026-10-06, untouched) and, for the two new arms, which have
never been run, the holdout. The same aggregate table is why the return floor below is a live
question for `control` at 10 bps rather than a formality. Also seen: the stored coverage (counts of
sessions and dates, no return or outcome). **No outcome of any arm below has been computed under this
declaration.***

- **Question:** does contango's own rule, holding SVXY while VIX/VIX3M is below 0.97 and SHV
  otherwise, earn a better risk-adjusted return than holding SVXY, once switching costs are paid? And
  does either of two declared changes to the exit do better than the rule as run?
- **Where the edge would live:** **premium, timed by a regime filter.** SVXY (-0.5x short VIX futures)
  earns the roll yield while the curve is in contango and gives it back, with interest, when the curve
  inverts. The gate claims to sidestep the inversions at the price of the quiet days it sits out. The
  edge is the *avoided loss*, not extra return, which is why the judgement is risk-adjusted. **What
  would falsify it:** the gate's Sharpe ratio is not above holding SVXY's, or it is only above in one
  crash, or the return it gives up exceeds 3 points a year. The 2026-10-05 notes are the prior: the gate
  bought drawdown, not return.
- **Look-alikes:** curve (the same signal on VXX call spreads; costs take about half the credit);
  the `/VXM` plan (`docs/vxm-plan.md`, CFE settlements, awaiting the user's decisions, not built);
  item 26-27 (a leveraged-fund rule with a long-hold problem, the same family of funds).

#### The arms (four judged, Holm across them)

Rules are the module's own (`regime.target_state`, `replay.run`), not restated from memory; the script
imports them. A decision is a state, SVXY or SHV; `control` and the gates are stateless, `flipexit`
holds through the band.

| arm | holds SVXY when | otherwise |
|---|---|---|
| `control` | VIX/VIX3M < 0.97 | SHV |
| `flipexit` | VIX/VIX3M < 0.97 to enter; stays in until it reaches 1.0 | SHV |
| `vix9d-exit` | `control`'s test holds **and** VIX9D/VIX < 1.0 | SHV (both conditions must clear to re-enter) |
| `rv-gate` | `control`'s test holds **and** VIX > SPX's 10-session realised volatility | SHV |

- **Mechanisms, one each.** *`vix9d-exit`:* the 9-day index reacts to a shock before the 30-day one
  does; the front of the curve inverts first, so the exit can come a session or two earlier than the
  30-day ratio's. *`rv-gate`:* SVXY earns the variance premium (implied over realised). When
  realised volatility runs above implied, the premium is negative whatever the curve's shape.
- **Definitions that need a rule.** *Realised volatility:* the sample standard deviation of 10
  daily SPX log returns ending on the decision day's close, times the square root of 252, times 100,
  in volatility points beside VIX. Strict greater-than in the gate, no margin. *VIX9D/VIX:* both at
  the same close, strict less-than 1.0. The gates add no state: a day either clears them or not.
- **Not included, said out loud.** Other ratio thresholds (a search the sample cannot afford under a
  Holm of four); the `/VXM` plan (another instrument); leveraged short-volatility funds (borrow, and
  worse risk-adjusted in the 2026-10-05 notes); SVIX (-1x, replayed worse).

#### The baselines

- **`hold-svxy`:** SVXY bought on the first session and held, one side of cost. The comparison every
  arm is judged against.
- **`hold-shv`:** SHV held; reported, not judged. Excess return over the T-bill is measured against
  SHV's own total return, so an arm sitting in SHV earns an excess of zero by construction.

#### Measured

Daily, for each arm and baseline, in each window and half: the Sharpe ratio (mean daily return in
excess of SHV's, over its standard deviation, times the square root of 252), the CAGR, the maximum
drawdown, the Calmar ratio (CAGR over the absolute drawdown), the worst day and worst five-session
week, the share of sessions in SVXY, the number of switches, the **whipsaw count** (a switch followed
by the opposite switch within 5 sessions), and the costs paid as points a year. Each arm's difference
from `hold-svxy` is a paired daily series.

#### Fills and costs

- **The decision is approximated by the close.** The module reads the ratio at 15:50 and fills inside
  an 8-minute window; the replay reads the close's ratio and fills at the close, as
  `cherrypick.contango.replay` does. Reasons: the daily index files carry no 15:50 print, the
  stream cache has no history before 2026-10-05, and the module's own replay is the one the paper loop
  is validated against (`contango_replay.py --validate`). **What is lost:** the close can sit on
  the other side of a threshold from 15:50's reading, and it uses ten minutes of information the module
  did not have. Two handlings: the **lag clause** below (the prior close's ratio, same-day close
  fill, which uses less information than the module had), and the validation's per-session decision
  agreement on the paper days, reported.
- **Costs:** 10 bps of NAV a side, two sides per switch, and one side to open. Judged at 10 bps;
  reported at 2 bps (the module's slippage floor). The two funds trade at a few basis points in the
  sizes this suite runs, so 10 bps is a stress, not an estimate.
- **Returns:** SVXY from the store's raw bars, split-adjusted; SHV total return with its
  distributions credited on the ex-date (the technicals store's `dividends`, as the module credits
  them). A session missing any input for an arm is skipped and counted, never filled with a zero.

#### Data

All on disk, nothing licensed, nothing fetched. Results and any derived series stay under
`~/.cherrypick/data/research/item39/`; this plan records the verdict without figures.

- **Prices:** SVXY and SHV daily bars from the technicals store (`history.db`), through
  `pmcc_shield_replay.closes(total_return=True)`, the reader `contango_replay.py` uses.
- **Indexes:** VIX, VIX3M, VIX9D and SPX daily closes from the exchange's public files in
  `~/.cherrypick/data/market-files/`.
- **Coverage, counted 2026-10-30 (no returns):**
  - SVXY has bars from 2011-10-04 to 2026-10-29 and **one split, 2024-04-11, 2 for 1** (a forward
    split, which the adjustment carries). No dividends. The 2018-02-27 change from -1x to -0.5x is a
    change of objective, not a split, so the store has no marker for it: **the start is 2018-03-01**.
  - SHV has bars from 2011-01-03 and 184 recorded distributions to 2026-10-01.
  - VIX has 2018-03 onward complete. VIX3M starts 2009-09 and has no gap in either window. VIX9D
    starts 2011-01-04. SPX is complete through 2026-10-16.
  - **VIX9D and SPX stop at 2026-10-16**, nine sessions short of VIX and VIX3M. They are refreshed
    before the study runs; if they are not, the two arms that need them end at 2026-10-16 and the
    report says so. `control` and `flipexit` need only VIX and VIX3M.
  - 2020-02-17 (Presidents' Day) is a row in the SVXY store but in none of the index files. It is
    skipped and counted as a non-session; seven session dates differ between the SVXY and SHV
    stores in all, listed by the plan step and skipped the same way.
  - Session counts, 2018-03-01 on: design 1,216; first half 586, second half 630; holdout 960 to
    2026-10-29, the last stored session (today's bar is not yet in the store).
  - **More than 1% of a window's sessions missing an input for an arm is no verdict for that arm.**

#### Windows and the pass rule

- **Design:** 2018-03-01 to 2022-12-30 (1,216 sessions), split at 2020-07-01 into 2018-03 to
  2020-06 (the 2018 fourth-quarter fall and the 2020 crash) and 2020-07 to 2022 (the recovery and
  the 2022 bear). **Holdout:** 2023-01-03 to the last stored session (2026-10-29 at drafting), read
  once, run after the design. **Paper** from 2026-10-06 is the untouched later check, read with
  `contango_replay.py --validate` and the console's `tracking`; it has no say in the verdict.
  The 2020-07-01 split is the only date that puts a different volatility event in each half; the
  crash sits in the first, the 2022 rise in rates in the second.
- **Pass rule, per arm, all at 10 bps a side, in the design window against `hold-svxy`:**
  1. **Sharpe:** the arm's Sharpe ratio above `hold-svxy`'s, with the one-sided 95% lower bound of
     the difference above zero from a stationary bootstrap of paired daily returns (mean block 20
     sessions, 20,000 resamples, seed 39, the difference in Sharpe ratios recomputed on each
     resample). The arm's p-value is the share of resamples with a difference at or below zero;
     **Holm at 0.05 across the four arms.**
  2. **Drawdown:** a smaller maximum drawdown than `hold-svxy`'s.
  3. **Return floor:** CAGR no more than 3 points a year below `hold-svxy`'s. A non-inferiority
     floor: item 27's margin was 2 points for a rule that changed an exit; this rule gives up
     exposure by design and the seen aggregates had it giving up several, so 3 is the declared
     price of the drawdown, and it is set here, before any run.
  4. **Both halves:** in each half separately, the Sharpe difference above zero and a drawdown no
     deeper than `hold-svxy`'s.
  5. **Not one crash:** with 2020-02-19 to 2020-04-30 removed from every series (sessions dropped,
     the rest joined), the Sharpe difference still above zero.
  6. **Not the close:** with the ratio (and the 9-day and realised-volatility inputs) lagged one
     session and the fill still at the same-day close, the Sharpe difference still above zero.
- **Then the holdout, read once, for the arms that pass the design, at 10 bps:** the arm's Sharpe
  above `hold-svxy`'s and the return floor met (CAGR no more than 3 points below). A holdout cannot
  overturn a design fail.
- **If more than one arm passes,** the one with the higher design Sharpe difference becomes the base
  candidate; the others are reported.
- **Minimum detectable effect (B12), an assumption, not an outcome.** Taking SVXY's volatility as
  roughly 45 to 50% a year, an arm that is in SVXY about two thirds of the time (correlation with
  holding about 0.8), and 1,216 daily sessions, the standard error of the Sharpe difference is of
  the order of 0.3, and clearing a Holm-of-four one-sided bound needs a difference of roughly
  0.6 to 0.7. Volatility shocks cluster, and the design window holds about three independent ones
  (2018 fourth quarter, 2020, 2022), so the effective sample is smaller than the session count and
  the bootstrap's block of 20 only partly allows for it. **A Sharpe difference under about 0.5 cannot
  be told from zero here, so a fail reads "no edge of detectable size", not "no edge".** Drawdown
  and the return floor are descriptive: a gate can pass them and fail the Sharpe clause for want of
  power, and the report says which.

#### Rival explanations, each with its check

- **One crash decides (2020-03).** The not-one-crash clause above, the per-year table, and the
  worst-day and worst-week columns with and without the window. A gate that works only in 2020 is
  not a rule.
- **Costs.** Judged at 10 bps, reported at 2; switches, whipsaw count and costs as points a year
  beside every figure. `flipexit` switches about half as often as `control`; `vix9d-exit` and
  `rv-gate` add switches, so they are expected to pay more, and the count shows it.
- **The ratio read at the close against at 15:50.** The lag clause, and the paper-days decision
  agreement from `--validate`, reported. A rule that flips on a one-session lag was reading the
  close's noise.
- **One regime.** Two design halves with different events, the holdout's own run, and per-year
  tables. The holdout is a low-volatility stretch for most of its length; a gate that sits in SVXY
  almost always has little to show there, so a holdout in which the arm is out of SVXY for under
  5% of sessions is reported as uninformative about the gate rather than as a pass.
- **The fund, not the rule.** SVXY is a fund, not the futures: the roll, rebalancing and fees are
  inside its price; the fund's own history is what an account holds, so it is the right test, and
  it is a single fund (a declared limit).
- **Tuning.** The 0.97 and 1.0 thresholds were seen (above). The two new arms add one test each at
  fixed values (1.0, 10 sessions) and no grid; the thresholds of `control` and `flipexit` were chosen
  with the data in sight, which the report states beside their results.

#### Guards, each shown to fail

Before an arm is read, each broken on purpose to see the check fail:
- **Reproduction:** `control` and `flipexit` from the script equal `contango_replay.py` and
  `replay.run` NAV for NAV on the same inputs. Broken by moving a threshold to 0.98.
- **Accounting:** each NAV is the product of the held fund's daily returns less costs, to 1e-9; the
  cost column equals switches times two sides. Broken by dropping a side of cost.
- **No look-ahead:** the ratio, the VIX9D/VIX ratio and the realised volatility on day t use closes
  through day t only. Broken by shifting the inputs back one day: the result must change.
- **Split:** SVXY's return across 2024-04-11 is an ordinary day's, not a halving. Broken by using
  the raw closes.
- **Coverage:** a session missing an input is counted and skipped, never a zero return. Broken by
  deleting a VIX3M row.

#### Declared limits (B11)

Daily closes, not the 15:50 print; the decision and fill both at the close, which uses ten minutes of
information the module did not have (the lag clause bounds it); one fund (SVXY) and one cash fund
(SHV); costs a flat fraction of NAV, no spread or size effects; whole shares ignored; taxes ignored;
the rules judged here are the module's, at thresholds seen before; four arms and about three
independent volatility shocks in the design window, so low power (see the MDE); `hold-svxy` is the
only judged baseline, and SHV is reported.

#### Data and the run

A script, `scripts/contango_item39.py`, importing `contango_replay.py`'s readers and the module's
`regime` and `replay` for `control` and `flipexit`, and run as `item39/plan` (the coverage counts and
the listed skipped dates, no returns) and `item39/study` (queue rows, supervisor-run). It writes
`~/.cherrypick/data/research/item39/results.json`. The plan step runs first and must report the
VIX9D and SPX refresh done.

- **Settled with the build, before the first read (2026-10-30, `scripts/contango_item39.py`):**
  - The design window holds 1,215 sessions, not 1,216: the count above included a store row on a
    day that was not a session (2020-02-17), which is skipped.
  - The VIX files carry rows on some exchange holidays, so the realised-volatility window and the lag
    run read SPX's own dates as the calendar; an SVXY session absent from SPX counts as missing for
    those two runs.
  - The crash-removed reading drops that window's daily returns from each full run and joins the
    rest; it does not re-run the switching path. The lag run lags the final daily signal one session
    (the same as lagging each input), with the fill at the same close.
  - Each arm is paired with holding SVXY over the arm's own usable sessions. The first day's return is
    the opening side of the cost. The halves are slices of the one design run. Holding SHV has no
    Sharpe, its excess being zero by construction.
  - If any declared guard fails, every arm reads no verdict and none is read. VIX9D and SPX were
    refreshed from the exchange's public files to 2026-10-30 before the run.

#### Decides

- **A pass:** the passing arm (the higher design Sharpe difference if two) becomes contango's base
  in paper at a declared boundary: a measurement-affecting change (which arms exist), batched, with a
  `measurement_breaks` row. `control` and `flipexit` passing keeps the module as it is, with that arm
  named the base.
- **A fail or no verdict:** the evidence the retirement rule needs, with the minimum-detectable-effect
  note beside it. Paper keeps running unchanged. Retiring the module, or either arm, stays the
  user's decision.

#### Not worth revisiting

| Idea | Why not | Evidence |
|---|---|---|
| Other ratio thresholds (0.90, 0.95, 1.05) | A search the sample cannot afford under a Holm of four | This item |
| A `/VXM` arm | Another instrument; the plan awaits the user's decisions | `docs/vxm-plan.md` |
| Leveraged short-volatility funds | Hard to borrow, worse risk-adjusted | 2026-10-05 notes |

## Not worth revisiting

Each of these came up again in the review and is already answered:

| Idea | Why not | Evidence |
|---|---|---|
| Debit-first fly building (videos 4, 8) | Payoff shaping that pays fees on extra legs | Flies debit-first arms: −$35 gross against $1,979 of fees |
| A cheap single long as a hedge | Paid twice, recovered little | Flies hedge overlay: +$803 → −$4,813 |
| Roll-for-a-credit, never close (video 17) | Wins most weeks, keeps the tail | pmcc roll policy lost to holding on 5 of 6 symbols; a 25-delta SPY spread never collects ⅓ of the width |
| "Risk-free" transforms (videos 4, 13) | Accounting: spends booked profit as the floor | Flies' `outright` honesty rule |
| Pin flies with 1,000%+ average wins (video 5) | Implausible expectancy; fair at mid otherwise | Expected-move fly study |

## Process

- **No module changes come out of this plan directly.** Each test is read-only. It reuses an existing
  replay where one fits, and writes its results under `~/.cherrypick`. That's mandatory for anything
  derived from ThetaData.
- **Declare the pass test before running.** Each "build only if" above is the starting point; tighten
  it before looking at results, as the quote-history replay's decision check did.
- **A positive result becomes a proposal, not an arm.** It names the edge, the evidence and the
  boundary it would land on. Window, stop or entry changes in a running module are
  measurement-affecting and batch to a declared boundary.
- **Record every result in the module's history or experiment log, negative or not.** A clean
  negative closes the question for the next review.
