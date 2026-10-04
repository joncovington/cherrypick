# Entry and exit setups on the technicals chart

The arrows on the console's technicals chart (`/charts/technicals`) are four textbook setups and
their short mirrors. Each one opens a simulated position when its entry rule fires and closes it when its own
exit rule fires. The engine is `src/cherrypick/technicals/setups.py`, the indicators it reads are in
`indicators.py`, and `chart.py` writes the results into each name's chart file (`chart_version` 3).
The console only draws what the file says.

This page records what each indicator is for, why the four setups are paired the way they are, and
the decisions taken while building them (2026-10-03).

## Why the arrows changed

The chart used to put an arrow on every session where one of the vendor's six scan rules matched
(`signals.py`). Those rules describe a pattern. They have no exit and no position behind them, so an
up arrow never had a matching "this is where it ended". The scan matches are still computed, still
feed the morning report and are still listed under the chart. They are no longer drawn on it.

The setups replace them because an arrow is only useful if it comes with its other half. Each entry
is paired with the exit its own rule gives, and a position that hasn't exited is reported as open.

## The indicators, and what each one is for

Use one indicator of each kind and don't stack several that measure the same thing. RSI, CCI and
Stochastic are all momentum readings, and three of them agreeing is one opinion counted three
times. The setups take one reading each of trend, momentum and volatility (and volume for the
breakout).

| Indicator | Kind | Parameters | What it contributes |
|---|---|---|---|
| EMA 9 / 21 | trend, fast | 9 and 21 sessions | The short-term trend. The 9 reacts within days; the 21 is roughly a trading month and is the usual first pullback level in a healthy trend. A 9-over-21 cross is the classic swing-trading momentum turn. |
| EMA 50 | trend, slow | 50 sessions | The trend filter. Long entries are taken only on the right side of it, which removes most crosses that happen inside a downtrend. |
| ADX | trend strength | Wilder, 14 | Whether a trend exists at all, regardless of direction. Above 20 is the conventional threshold for "trending". Crossovers in a trendless market are mostly whipsaw, and ADX is the standard way to skip them. |
| RSI | momentum | Wilder, 14 | How stretched the move is. Under 30 is oversold. In an uptrend, a dip into 40–50 is a pause, not a reversal. |
| Bollinger bands | volatility | SMA 20 ± 2 population sd | Where price is relative to its recent range. A touch of the lower band is a statistical stretch; the middle band is the mean it reverts to. This is the same band `trend.py`'s −4 is measured against, so the chart has one definition of it. |
| Band width (squeeze) | volatility regime | (upper − lower) ÷ middle, lowest of 120 sessions | Quiet markets precede active ones. The narrowest band of the last six months marks a coiled market. |
| ATR | volatility, in price | Wilder, 14 (22 for the Chandelier) | How far price normally moves in a day, so stops are sized to the name's own noise rather than a fixed percentage. |
| Supertrend | trailing stop | ATR 10, × 3 | A volatility-sized line under price that only ratchets up while the trend holds and flips when a close breaks it. A breakout is meant to run, and this lets it. |
| Volume | participation | today vs 1.5 × mean of the 50 sessions before | A breakout on ordinary volume is often a false start. A real one usually draws in more trading. |

Every indicator returns `None` until its window is full, so nothing is read off a partial window.
The EMA is seeded with the SMA of its first `n` values. RSI, ATR and ADX use Wilder's smoothing,
which is an EMA with α = 1/n. These are the textbook definitions: they are not fitted to the vendor
or to anything else.

## The pairing table

The table below is the one the setups were chosen from. The fifth row was not built, because the
chart holds daily bars and VWAP is an intraday reference.

| Style | Entry | Exit | Built |
|---|---|---|---|
| Trend following | 9/21 EMA cross, price above the 50, ADX > 20 | ATR trailing stop or a close under the 21 | yes: close under the 21 |
| Pullback in a trend | RSI dips to 40–50, the 21 EMA holds | prior high as a target, or Chandelier Exit | yes |
| Mean reversion | lower Bollinger band touch with RSI < 30 | the middle band | yes, plus a stop |
| Breakout | Bollinger squeeze, then a close outside the band on high volume | Supertrend flip or a Keltner midline close | yes: Supertrend |
| Intraday | VWAP reclaim with the 9 over the 21 on the 5-minute chart | loss of VWAP or the 9/21 crossing back | no: daily bars only |

The rules work as a set because each setup suits a different kind of market. Trend tools whipsaw
in ranges, and oscillators get run over in trends. Showing one setup at a time makes that
visible: a trend-following arrow in a choppy stretch is exactly the failure the ADX filter exists
to reduce.

### 1. Trend following

- **Entry:** the 9 EMA crosses above the 21 (it was at or under the 21 yesterday), the close is
  above the 50 EMA, and ADX(14) is above 20.
- **Exit:** the first close under the 21 EMA.
- **Why this exit:** the table offered an ATR trailing stop as an alternative. On daily bars a 3 ×
  ATR trail sits well under the 21 EMA in almost every trend, so it would almost never close a
  position first. A rule that never fires reads as protection without being any, so it was left
  out.
- **What to expect:** the cross often comes after the move has started, and in a V-shaped reversal
  it usually happens while price is still under the 50, so no entry is taken. That's the filter
  working, not a bug.

### 2. Pullback in a trend

- **Entry:** the EMAs are stacked 9 > 21 > 50, the bar's low reaches the 21 EMA but the close
  finishes above it (the 21 held), and RSI(14) dipped to between 40 and 50 at some point in the
  last 5 sessions.
- **Exit:** whichever comes first:
  - **Target:** the high reaches the highest high of the 20 sessions before entry, the swing high
    the trend is expected to retest.
  - **Stop:** the Chandelier Exit, a close under the highest high since entry less 3 × ATR(22).
- **Ties:** a bar that reaches the target but closes under the stop counts as a stop. A daily bar
  doesn't say which happened first, so the conservative reading is taken.
- **No target:** if the prior high is not above the entry close, the position has no target and
  only the stop can close it.
- **A rule change, measured:** the RSI reading was first written as "RSI(14) between 40 and 50 on
  the entry bar". On the real store that combination could not fire. Across eleven liquid names
  and three years (SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, JPM, XOM, TSLA, META), there were 553
  bars where the EMAs were stacked and the 21 held, and RSI(14) on them was never under 50.5
  (median 54.0). A close back above a rising 21 EMA lifts RSI above 50 by construction.
  - The dip and the hold are a sequence, so the RSI condition now looks back over the last 5
    sessions for the dip, and the hold is read on the entry bar.
  - That reading gave 228 qualifying bars on the same eleven names. The alternatives measured were
    dropping the 9 > 21 condition (4 bars) and both changes together (307 bars).

### 3. Mean reversion

- **Entry:** the bar's low touches or crosses the lower Bollinger band and RSI(14) is under 30.
- **Exit:** a close at or above the middle band (target), or a close more than 2 × ATR(14) under
  the entry close (stop). The ATR is the one on the entry bar, so the stop is fixed at entry.
- **Why the stop was added:** the table's pairing exits only at the middle band. In a real
  downtrend price can stay under the band for weeks, and without a stop the position would stay
  open indefinitely. The 2 × ATR stop was proposed before building and accepted. On the stops-
  versus-targets counts below it is the setup's most frequent loss, which is the honest cost of
  buying stretches.

### 4. Breakout

- **Entry:** a close above the upper Bollinger band, within 5 sessions of a squeeze (band width at
  its lowest of 120 sessions), on volume above 1.5 × the mean of the 50 sessions before, **with
  Supertrend(10, 3) already up**. Today's volume is excluded from the average it is compared against.
- **Exit:** the first close with Supertrend(10, 3) down.
- **Why Supertrend must already be up (added 2026-10-04, before the rules were frozen):** without
  it, a breakout entered while Supertrend was still down exited on the very next close. That
  happened on 33 of 45 such entries; the breakdown mirror, 47 of 57. Those were one-day round trips,
  two fills for nothing, not breakouts failing. With the condition, next-bar exits fell from 13.3%
  to 1.6% (breakdowns 17.8% to 0.5%), the median hold is 25 sessions, and about 12% of breakouts
  and 18% of breakdowns are no longer taken.
- **Missing volume:** a missing volume never confirms. An unmeasured day is not a quiet day.
- **What to expect:** this is the rarest setup by far. It needs three things in one week:
  compression, expansion and participation.

## SPX has no volume, so its breakout reads SPY's

SPX is a cash index. It is calculated from the prices of its member stocks and nothing trades as
SPX, so there is no volume to have. Dolt carries no cash indexes. SPX's bars come from the broker's
daily candles (`scripts/fetch_index_bars.py`), which land with volume null.

Three stand-ins were considered:

- **SPY's volume (chosen).** The ETF tracks SPX almost exactly. The store already holds it with full
  volume, and the rest of this package already uses SPY in SPX's place.
- **ES futures volume.** Arguably the truest measure of activity in the index, but the console's
  futures feed is intraday only and isn't in the store.
- **The summed volume of the members.** Accurate, but the store doesn't hold every member.

`chart.VOLUME_PROXY` maps SPX to SPY. The volumes are matched by date, and SPY's own 50-session
average is the comparison. The chart file names the stand-in in `volume_source`. The console
states it on the SPX chart whatever setup is shown, because the user asked that the chart say so
(a render test pins it).

Two things this means in practice (measured 2026-10-03):

- SPX's history starts on 2023-08-30 and SPY's on 2023-09-25. The 17 SPX sessions before SPY's
  first bar have no volume, so the volume test cannot confirm on them. It couldn't anyway: it needs
  50 sessions of history.
- In three years SPX had one squeeze breakout, on 2025-09-11, and SPY's volume that day was under
  1.5 × its average, so the test declined it. SPY's own chart declined the same days for the same
  reason. IWM's squeeze breakout in July 2024 did confirm on volume. On the index funds the volume
  test does real work.

## Conventions shared by all four

- **Daily bars, judged on the close.** An arrow sits on the bar whose close fired it, at that close.
  No fill, slippage or next-day open is modelled. These are signals, not trades.
- **Long and short.** Each side is drawn in its own colour and labelled, and a long and a short
  are separate positions (see "The short mirrors").
- **One position per setup.** An entry signal while a position is open is not a second position,
  and a bar that exits does not also enter.
- **Walked over the whole stored history** (about three years), then cut to the 250 sessions drawn.
  A position opened before the window still exits where its rule says. Its exit arrow is drawn
  without an entry arrow.
- **Each setup draws the lines its rule reads:** the 9/21/50 EMAs for trend following and pullback,
  the bands for mean reversion, and the bands plus Supertrend for breakout. A reader can then see
  why each arrow is where it is.
- **The parameters are constants at the top of `setups.py`.** Changing one changes what every arrow
  means, so say so in the commit and here.

## The short mirrors (2026-10-04)

Each long setup has a short mirror: every comparison reversed, highs for lows, the upper band for
the lower, an overbought RSI for an oversold one. A long and a short of the same family are separate
positions, and they never net against each other.

| Short | Entry | Cover |
|---|---|---|
| Trend following | the 9 EMA crosses below the 21, close under the 50 EMA, ADX(14) > 20 | first close over the 21 EMA |
| Pullback | EMAs stacked 9 < 21 < 50, the high reaches the 21 but the close is under it, RSI(14) rallied to 50–60 in the last 5 sessions | the low reaches the lowest low of the 20 sessions before entry (target), or a close over the lowest low since entry plus 3 × ATR(22) (stop); both on one bar is a stop |
| Mean reversion | the high touches the upper band with RSI(14) over 70 | a close at or under the middle band (target), or more than 2 × ATR(14) over the entry close (stop) |
| Breakdown | a close under the lower band within 5 sessions of a squeeze, volume > 1.5 × its 50-session average, Supertrend already down | the first close with Supertrend(10, 3) up |

**The short pullback has the same problem as the long one, measured the same way.** On bars stacked
down where the 21 EMA rejected the close, RSI(14) never reached 50: 232 bars on eleven names, peaking
at 49.8, because a close back under a falling 21 pulls RSI under 50. The rally is read over the last
5 sessions, as the long side's dip is, and fires on 87 of those bars.

**On the chart**, a short is drawn in pale grey, never amber, so a short's entry (a down arrow) can't
be read as a long's exit. The entry is a down arrow over the bar labelled "short", and the exit an
up arrow under the bar labelled "cover · <reason>". A Long / Short / Both control picks which
positions of the chosen setup are drawn (`?side=`). The watchlist has a Side column and filter.

## Vendor data: none in the setups

The setups, their indicators, the levels the chart and watchlist show, and the trend labels are all
computed from our own bars. The vendor is used only to check our calculations:

- **Our trend scores** match the vendor's day by day on 99.7% of days (`trend.py`).
- **Our RS rank** matches exactly on 52 of 62 captures (`levels.py`).
- **Our support and resistance** (`swings.py`) are compared with the levels the vendor's chart
  draws, and the result is recorded below. The comparison is never a target.

The vendor's levels remain on the chart as "Vendor's view" (and "All"), for comparison only.

## Our own support and resistance (`swings.py`)

A level is a confirmed swing point in our bars over the last 250 sessions:

- **A swing high** is above the 10 highs before it and not exceeded by the 10 after. **A swing low**
  is the mirror.
- **Resistance** is a swing high above the last close. **Support** is a swing low below it. A broken
  level is not turned into its opposite.
- The two nearest on each side are kept, skipping any within 1% of one already kept.
- Each level is dated on its swing bar and drawn from that date.

**Why 10 bars either side:** a high or low the market respected for two trading weeks on each side,
the textbook "major swing". As a check, against the levels the vendor's chart draws on 129 captures,
one of ours lies within 1% of one of theirs on 20% of their levels with 5 bars, 26% with 10, and 25%
with 20 (within 2%: 30%, 35%, 34%). The overlap is modest, as it should be: how the vendor picks its
levels is unsolved, and ours are a different, stated rule, not an attempt to copy theirs.

## How often they fire (527 charts, 2026-10-03)

These are frequencies, not results. Nothing here scores whether a setup makes money.

| Setup | Positions in 3 years | Names with at least one | Entries in the 250 drawn | Open now | Exits |
|---|---|---|---|---|---|
| Trend following | 1,919 | 508 | 674 | 15 | all at the 21 EMA |
| Pullback | 4,123 | 524 | 1,504 | 42 | 2,595 target, 1,486 stop |
| Mean reversion | 2,515 | 518 | 857 | 114 | 1,772 target, 629 stop |
| Breakout | 256 | 212 | 71 | 12 | all Supertrend |
| Trend following (short) | 1,857 | 505 | 643 | 59 | all at the 21 EMA |
| Pullback (short) | 2,490 | 512 | 980 | 34 | 1,295 target, 1,161 stop |
| Mean reversion (short) | 5,899 | 523 | 1,821 | 26 | 2,983 target, 2,890 stop |
| Breakdown (short) | 234 | 196 | 83 | 16 | all Supertrend |

The short rows, and both breakout rows after their Supertrend condition, were counted on
2026-10-04.

## How it is checked

- **`tests/test_setups.py`** restates each rule in the test's own words. Every entry must meet it,
  and every exit must be the FIRST bar its exit rule allows. In the other direction, every bar with
  no position open that meets the entry rule must be an entry, so a rule made stricter fails as
  surely as one made looser.
  - The fixture is a seeded random walk (3,000 bars) chosen because it trades every setup at least
    three times.
  - Hand-built cases cover the pullback's stop-and-target tie, the ADX outside bar, the Supertrend
    ratchet and SPX reading SPY's volume.
- **Each guard was shown to fail.** Twelve deliberate breaks were each caught by at least one test:
  - dropping or tightening ADX
  - letting an exit bar also enter
  - the mean-reversion stop sized on today's ATR
  - dropping volume
  - a 3-session squeeze window
  - reading RSI only on yesterday
  - a pullback stop that never fires
  - no dip to the 21
  - no SPY stand-in
  - ADX counting both moves of an outside bar
  - a Supertrend that doesn't ratchet
  - a shifted ATR seed

  The first version of the tests missed three of these. That is why the completeness check, the
  hand-built tie and the ADX case exist.
- **The shorts** are tested the same way on a walk that drifts down, and each was shown to fail
  when broken (eight deliberate breaks, every one caught).
- **The real store.** On 2026-10-03 an audit restated the four rules independently (all eight on
  2026-10-04) and ran them over all 527 chart names and their full histories: every entry, every exit, every exit being the
  first allowed, no missed entries, the exit reasons, and each chart file against the engine. It
  found no discrepancies. The page was then checked in Chrome (`pnpm ui-check`) on SPX, QQQ, NVDA,
  TSLA, MSFT, AAPL and GME, covering every setup.

## Limits

- Daily bars only. Intrabar order is unknown, which is why a pullback bar that touches both target
  and stop counts as a stop.
- Not scored against outcomes, and no costs. A setup firing often says nothing about whether it
  should be traded.
- The rules are textbook, not tuned. If they are ever tuned, do it on a declared sample and say
  which, as `docs/fitting-record.md` does for the vendor fits.
