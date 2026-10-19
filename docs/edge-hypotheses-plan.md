# Edge hypotheses: what the 2026-10 video review left worth testing

*Drafted 2026-10-17. Nothing below is built. It follows a review of twenty Theta Profits interviews
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
([history-studies-2026-10-c.md](history-studies-2026-10-c.md)), and all five are closed:
- 1, post-earnings drift;
- 4 and 8, the box edge and the intraday direction reads (licensed-data results kept locally);
- 5, the EMA signal;
- 6, the BWB down-day filter.

That leaves 2 (MEIC afternoon entries), 3 (the mid-morning iron fly) and 7 (MEIC stop mechanics). 3
and 7 depend on an intraday fill model that is no longer being built for Pulver's sake, so each now
has to justify that build on its own.

**Added 2026-10-18:**
- 9–11, Option Alpha's three automated bots (a separate source from the Theta Profits series).
- 12, GEX levels. They were in "not worth revisiting" until the backtest store made years of rebuilt
  dealer profiles available.
- 13, Option Alpha's credit-structure screener.

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
