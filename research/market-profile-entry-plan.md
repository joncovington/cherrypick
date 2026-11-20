# Market Profile entry signals: plan

> **Status (2026-11-18): closed. Stage A ran as research item 93, and all four candidates failed**, so
> no stage B is declared. The profile stays a chart, and item 81's first-hour width remains the one
> profile reading that has passed. The verdicts are in the register and in section 93 of
> [edge-hypotheses-plan.md](edge-hypotheses-plan.md).

## The question

Does a Market Profile reading, taken at a fixed time in the session from data up to that time, pick
SPX 0DTE entries that earn after costs, and earn more than the same trade entered at the same time on
every day?

The second half is the real test. A rule that only says "trade today" is worth something only if the
days it picks beat the days it skips. The unconditional trade at the same clock time is the baseline
for every candidate.

The source is a common teaching workflow: mark the prior day's value and structure before the open,
read the open against value, track the first hour, then wait for acceptance or rejection at a level.
The teaching ends at "order-flow confirmation and a clear stop". That step is not testable here (see
Data), so every entry below is a rule on bar closes.

## What the register already says

| Item | Reading | Verdict |
|---|---|---|
| 80 | Open outside prior value, held through the first hour → wider rest of day | failed |
| 81 | First-hour width against its 20-session median → rest-of-day range | passed (persistence) |
| 82 | MEIC live-1400 skipping wide-first-hour days | failed (skipped days still earn) |
| 83 | EMA 9 crossing session VWAP | failed |
| range-features, signal-log | Chart features for range and direction | found nothing |
| 90 | Opening-range breakout put spread; ADX condor | declared, not run |

What follows from this: direction read from chart features has a poor record here, and the one
profile reading that has worked is a **range** forecast. So the first candidate is a range trade, and
the directional ones are ranked below it.

## Data

- **Price:** the licensed SPX one-minute store, 2022-05-16 to 2026-11-13, regular session. Time only:
  an index has no volume.
- **Option prices:** SPXW 0DTE one-minute quotes from the same vendor (Value tier: quotes, no trades
  or greeks; delta and IV are computed per instant). Priced through the automation driver, the same
  engine, fills, fees and settlement as item 90.
- **Profile:** `cherrypick.core.marketprofile` as it stands: 2.5-point rows, 30-minute periods,
  CBOT two-row 70% value area, IB = periods A and B. No new derivation is written for these studies.
- **Not available, so out of scope for any verdict:**
  - **Volume.** No volume history is being bought. The broker's candle feed is the only source, and
    it is shallow. The profile-bars recorder holds minute bars only from 2026-11-05. A probe on
    2026-11-17 asked for SPY, QQQ and /ES from 2020 at coarser intervals:

    | Interval | SPY, QQQ | /ES (active contract) |
    |---|---|---|
    | 1 minute | 8,000 bars (about 3 weeks of SPY) | 8,000 bars (about 6 days) |
    | 5 minutes | from 2026-08-20 | 8,000 bars, from 2026-10-08 |
    | 30 minutes | from 2026-08-20 | from 2026-08-19 |
    | 1 hour | from 2026-02-06 | from 2026-02-06 |

    Every bar carried volume. An expired contract (/ESZ25) returned nothing, so older futures history
    can't be assembled contract by contract.

    So about 60 sessions of 5-minute volume are available now, and about 190 of hourly bars, which
    are too coarse to place a value area. That is short of the 40 signal days a verdict needs for any
    rule that fires on a minority of days. Volume value areas, HVN/LVN and "volume confirms
    acceptance" therefore stay out of every verdict here. They are kept as observations, gathered
    forward from saved bars, until the saved history is long enough to declare on.
  - **Order flow.** No futures depth or prints on the API feed.

## Candidates

Each is stated as a reading at a decision time, an entry, an exit, the mechanism it needs, and what
kills it. Parameters are fixed here and are not searched.

### E1. Balance-day condor (first)

- **Reading at 10:30 ET:** the open was inside prior value, the first hour is narrow (item 81's w at
  most ln 0.75), and price at 10:30 is inside the IB.
- **Entry at 10:30:** a 0DTE iron condor with shorts at the first strikes outside the IB high and low,
  $10 wings. Held to settlement.
- **Baseline:** the same condor (shorts at the IB edges) at 10:30 on every day.
- **Mechanism:** item 81 showed a narrow first hour forecasts a narrow rest of day. The edge exists
  only if 0DTE premium at 10:30 does not already price that.
- **Killed by:** option IV at 10:30 on these days already lower in proportion, so the condor collects
  less and earns no more than the baseline per unit of buying power.

### E2. Acceptance beyond prior value

- **Reading:** from period C, the first time two consecutive 30-minute periods each close with their
  whole range above prior VAH (below prior VAL), no later than 13:00.
- **Entry at that period's close:** a 0DTE put credit spread (call credit spread below value), short
  nearest 0.30 delta, $10 wide, held to settlement.
- **Baseline:** the same spread on the same side at the same clock time on days without the reading.
- **Mechanism:** price that holds outside value for an hour has found new acceptance and keeps going.
- **Killed by:** item 80's result extending to later in the day; held-outside days revert as often as
  they continue.

### E3. Rejection back into value (the "80% rule")

- **Reading:** the open is outside prior value, then two consecutive periods close with their whole
  range inside it, no later than 12:30.
- **Entry at that period's close:** a 0DTE debit spread toward the far value edge, long at the money,
  short at the far edge. Exit at the far-edge touch, back outside value, or the close.
- **Baseline:** the same spread at the same clock time toward a random side.
- **Mechanism:** a failed auction outside value rotates through value to the other side.
- **Killed by:** the far-edge touch rate no higher than for the same distance on random days.

### E4. Unfinished business (target only)

Poor highs and lows, single prints and untested POCs as levels price returns to. This is not an entry.
It is measured only as a touch rate against random levels at the same distance and age, and it is used
as a target for E2 or E3 only if it passes.

## How each is tested

**Stage A: forecast, on price alone.** Quick, local, no option data.
- E1: does the rest of the day stay inside the condor's short strikes more often than on baseline days?
- E2: the share of days that close beyond the level, and the adverse excursion before the close.
- E3 and E4: the touch rate against a matched random baseline.

Each test has its statistic, sign and bar fixed in the declaration, with Holm across the batch. It must
hold in both halves of time. A candidate that fails stage A gets no stage B.

**Stage B: the trade, priced.** Only for stage-A survivors.
- Fills at the suite's slippage model (an eighth of each leg's bid-ask from mid); halfway to natural
  is the stress; fees from core.
- Judged on the three bars (capital, tail, delta), and against the baseline arm on the same days.
- Overlap with MEIC: on signal days, does the entry add to live-1400 or duplicate it?

**For every declaration:**
- One run over the whole window, cut at 2025-07-01. The holdout counts only after a design pass.
- Fewer than 40 signal days in the design part means no verdict.
- Rule stored as data. Scripts report overall and per-symbol progress through `core.progress`.
- Queued behind the full-set fetch rows.

## If one passes

A paper arm only, off by default, through the Builder, run alongside the baseline for a declared
number of weeks before any decision about live trading. The console shows nothing new until then.

## Order of work

1. Declare E1, plus stage A for E2 to E4, as one batch.
2. Run stage A (minutes).
3. Declare and run stage B for survivors only.
4. Record verdicts here and in the register, with no licensed figures.

## Decisions (2026-11-17)

- **Decision times:** as stated. 10:30 for E1; no later than 13:00 for E2 and 12:30 for E3. No other
  times are tried.
- **Structures:** as stated. MEIC-style condors are not used: item 82 already tested a profile
  reading as a filter on MEIC, and it failed.
- **Volume:** no purchase. The broker's feed is shallow (see Data), so the volume half stays an
  observation. Saving the feed's 5-minute SPY, QQQ and /ES bars once now, back to 2026-08-20, would add
  about ten weeks before the recorder's start. That is a small script change, separate from this plan.
