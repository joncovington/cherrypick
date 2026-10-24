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
  data, so the results are kept locally under `~/.cherrypick`, per the licence);
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
  `board.tags_on` and refuses the build on any difference. B is run exactly as written above (the
  short nearest 0.40 delta, the widest long at 35-40%, else no trade). Two details the text left
  open, fixed here before any B trade was priced:
  - **A signal** is a session on which a name's count on one side reaches 3 or more having been
    below 3 on that side the session before (a fresh reading, as a setup's entry is), on a name in
    item 14's universe that session. A name with a B trade open on that side (entry to expiry) takes
    no second one.
  - **The universe** is single-name stocks only, as on the live board (it reads no fund).
- **Amendment (2026-10-23): A' and C', a new hypothesis, declared before anything was computed.**
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
    pooled into one selector, each side also reported). D stays the reference, not tested.
  - **Not a clean test:** a post-hoc version of this structure (the same search with no delta band)
    was already looked at in the 2026-10-21 sensitivity run, and it failed. Whatever A' and C' show
    is read with that in mind, and the result says so.
- **Result (2026-10-24), figures kept locally per the licence:**
  - **B fails.** It has over 100 paired trades in each half, but trails its random-date baselines
    and loses money outright in both. Nearly all its trades are put spreads; the call side alone is
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
