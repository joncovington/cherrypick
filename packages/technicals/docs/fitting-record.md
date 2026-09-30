# technicals — fitting record

The dated record of how each vendor rule was fitted, moved out of `packages/technicals/CLAUDE.md`
(which keeps each rule, its defining numbers, and what is still unsolved). The plan is
[`docs/market-report-plan.md`](../../../docs/market-report-plan.md).

## Adjustment (2026-09-25)

Against the vendor's chart data: MSFT's adjusted closes on all 753 sessions exactly; ANET's to
within a cent on the 21 of 3,012 prices where a pre-split division lands on a half cent, because the
vendor's raw prices carry more precision than Dolt's.

## Dividends (2026-09-27)

Dolt's `dividend` table alone agreed on 86.8% of prices across 40 names: gaps (CSCO's and CHT's
July 2024), zeros (BAP's September 2024 is 0.0) and wrong amounts (BAP's May 2024 is 0.939 for a
9.2875 payout). tastytrade has errors too (BAP's May 2026 as 50.00). Reconciled, 36 of 40 match at
99% or better (93.3% of all prices).

## Dolt query cost (first landing, 2026-09-27)

One month for 200 symbols took 27.5 s, the same month for all ~13,000 symbols 4.4 s; the first
landing written the obvious way ran past ten minutes. SPX, NDX and VIX came in through the universe
candidates, never landed, and each morning re-read three years for nothing (3 min 24 s); reported as
`not_in_dolt`, a steady-state landing takes ~13 s.

## Stages (editions of Sept 21-25)

BKNG trailed by 16-26% on every window and was a confirmed laggard four days running, then absent
the one day it beat the index — which is how the one-day condition was found. That condition alone
cut names wrongly listed from 859 to about 295.

## Trend scores (2026-09-29)

The old baseline, a sum of four ±1 signs, was right on about half and could never have been more:
a sum of four signs is always even, and the vendor's score is odd on 37% of short-term days and 32%
of long-term. What cracked it, in order: the capture's own score history starts on the 50th and
200th bars, which bounds every lookback; a free fit put integer weights on the price-vs-average
terms; the mixed rows split by exactly 2 on a WEIGHTED average (an SMA 36 is centred where a WMA 50
is, which is why the first fits kept finding 36); and the -4s are a band break. `trend.py` records
each step.

Sentiment: XLE's apparent miss was a 09-29 capture scored against 09-28 bars, which is why
`score-trends` now counts such captures as not yet landed. Read newest-first, the label looked
unrelated to the trend scores (+4/+4 names labelled Bearish); it was the read that was wrong.

## Level grid

The window is 250 sessions, not 252 (three names' lows sat on the 252nd), and ADI's range/100 of
2.24 takes 2.50, nearer by ratio. With dividends reconciled the grid places all 192 levels on the 36
names whose bars agree with the vendor's.

## Rank (2026-09-29)

The vendor's help pages call it "a summary of short, medium, and long term indicators". A single
~6-month return (the old reading) leaves 20 of ~1,500 pairs of captured names out of order; the
blend leaves 4; adding trend scores helps nothing. Ranked within our ~500 candidates it matched half
the captures and ran one decile LOW on the rest, never high — a curated liquid list is stronger than
the vendor's. Across the whole market, misses fall both ways.

## IV rank (2026-09-29)

Dolt's min-max IV rank (the definition the vendor's help pages state) against the vendor's
`impliedVolatilityRank` on the 49 captures where our IV is dated the capture's own day: correlation
0.66, median gap 4.6 points, ours about 6 higher on average, within 3 points on a third, DTE 79
apart. A min-max over our own 252-day IV history gives the same answer. tastytrade's rank
(`scripts/fetch_iv_rank.py`, 32 captures): correlation 0.64, median gap 6 points, and no variant
(headline, `tw`, `tos`, percentile) does better.

## Scan rules

First fitted on the old trend baseline (84%) and refitted when the trend was solved: its -4 had
meant "below every average" and now means a band break, so rules written as "trend = -4" caught 55%
until they moved to whole labels. Bullish trend-following turned out to be an RSI(14) band, not a
CCI cut. The CCI-5 period is stated on the list itself.

## Out of sample (2026-09-28)

A second capture of 39 scan-list names never used in fitting, 79 in all: bars match at 99%+ on 73
(ENB and ILMN join the foreign misses); the level grid places all 375 levels on those 73; the trend
baseline of the day agreed on 79.8% (short) and 76.0% (long) of ~97,000 daily labels, since
superseded by the solved trend.

## Gap levels (2026-09-29)

221 of 221 on our bars; SGOV, a T-bill fund with 72 gap levels, is the only name with misses (2),
and its bars do not agree with ours anyway. Of 2,444 pre-gap edges, 2 survive a fill. Features tried
for which gaps are drawn: size on any scale, fill by close or by range, depth, time since fill, a
regular level nearby. TradingView's convention fails because most drawn gaps have been entered.

## Level selection (2026-09-29, 108 names, 309 interior levels; later 441)

- Crossing profile: the vendor's levels average the 35th percentile (chance 50) and are local minima
  two to three times as often as a random grid point (61% vs 36% at ±1 step, 35% vs 14% at ±5).
  Volume at price agrees; touch and close counts point the wrong way.
- Given the date: 81% of dated bars are swing highs. Of the ~14 grid points within 0.75 ATR, the
  least-crossed one is the level 28% of the time against 8% by chance, the bar's high rounded up to
  the grid 26%; within one step about 55%.
- Which swing highs get dated: rise into the high, prominence and volume rank the picked highs at
  about the 57th-60th percentile; older highs slightly favoured.
- **Identity is the date; price is re-derived nightly.** TLT's 250-day low moved overnight and the
  levels dated 09-21 and 07-28 came back re-snapped (81.47 -> 81.44) while three dates rotated out
  and two in. A day's churn is mostly the grid moving.
- **Not every name is recomputed nightly.** On 2026-09-29 AMD's levels fit the grid one day earlier
  and MU's two days earlier; TLT's were that day's. The 250 window explains 737 of 743 levels over
  every capture on the vendor's own bars (251, 252 or a calendar year explain fewer); the six misses
  are AMD and MU (staleness) and one ZS level no lag up to 40 sessions explains.
- **Price depends on the grid beyond snapping.** TLT's bars were identical on both nights yet its
  07-28 level went 83.17 -> 83.04 while the anchor moved 0.43; re-snapping moves a fixed price at
  most a step (0.10). Histograms tried, as local-minimum rate against chance: bars overlapping the
  cell [p, p+step) 2.7x within ±2 cells, bars covering the point 2.3x, volume at price 2.0x,
  closes/opens/typical prices barely above 1x, a 63- or 125-session window about 1x. Levels sit on a
  cell holding a swing high's snapped high about twice as often as chance.
- **Price and date do not determine each other simply** (441 levels). Given the price, the nearest
  swing high within two steps is right 37%; last or first bar to trade at the price 1-13%. Given the
  date, the least-crossed cell within the bar's range and a step either side (ties to nearest its
  high) is right 35%; high rounded up 26%, rounded 24%. Half sit on the grid point just above or
  below the dated bar's high or low; the rest up to ten steps away, and no surrounding-bar price
  (five-bar pivot extremes, closes, opens, bodies, the bar's week) does better. Dated bars are swing
  highs 82%, swing lows 0.5%; one bar can carry two levels (AME's 2025-10-31: one at its high, one at
  its low).
- **Polarity is not the selector.** Tested as a confirmed role reversal (close above, retest, hold),
  picked swing highs rank at the 52nd-53rd percentile — chance. Published S/R methods (touch counts,
  pivot clustering, density peaks) place levels at heavily traded prices, which the crossing profile
  rules out.
- A nearest-snapped-swing-points-plus-extremes rule matches ~40% of levels, nearly all the extremes.
- The captures so far give one to three nights per name, which is why a fixed nightly panel is the
  next step.

## Data defects found by the report (Phase 7)

The first leaders list put BNY at +1,490% over six months. Some splits are recorded twice a week or
two apart (APH, CNQ); some tickers carry another security's history (BNY x13.6 in a day, SPCX x8.8,
HUT x4.6). Every scorer was re-run after the fix: no regressions, stage agreement up slightly.
