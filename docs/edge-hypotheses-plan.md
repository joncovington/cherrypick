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
and 7 depend on an intraday fill model that is no longer being built for Pulver's sake, so each now
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

### 8. Chris Pulver's daily routine

*Source: video 14. Reconstructed from five daily sessions (10-12 to 10-16) in
[pulver-daily-profit-play.md](pulver-daily-profit-play.md). The finding is that the condor is a
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
- **Clarified 2026-10-28, while building the replay, before any fetch or run**
  (`scripts/skate_expiry_item25.py`). Where the declaration was silent:
  - **The roll arm keeps the $0.03 buy-back.** The educator places it at the fill and rolls only
    when tested, so `roll` is `hold` plus the roll. A roll's new leg must pass the same spread bar.
  - **Unknown report dates.** A stock is clear only when no report falls in the span and Dolt shows
    the quarter around it: the reports either side no more than 120 days apart, or, when the next
    is not listed yet, the last one under 80 days before the expiry. Anything else is unknown, and
    not entered.
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
