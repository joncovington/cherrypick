# Earnings time spreads: screen and direction plan

A screen for every earnings time spread (single calendar, straddle calendar, double calendar and
diagonal), built on one quantity, the **modelled endurable gap**, with a combined side/direction read
that picks the structure. Status: **the engine is built, nothing else (2026-10-17).**
`cherrypick/earnings/time_spread.py` holds the event split, the endurable gap, coverage, the scores
and the side ladder as pure functions. `tests/test_time_spread.py` pins them to this doc's worked
example. No strategy, scan or replay reads them yet. We revisit this once backtesting data is
available; every threshold below is a placeholder until a history study sets it.

## Why it needs a screen at all

Both calendar strategies lost in `strat_test`. By 2026-10-12, `atm_calendar` had run 56 trades, 21%
won, for −$6,349, and `double_calendar` 35 trades, 14% won, for −$3,660. Both were retired from the
harness ([operating-history.md](operating-history.md)). The unscreened trade is not an edge. The
question here is whether a subset that can be picked out before entry is one.

The premise is the usual calendar one. Both expiries price the same announcement, but the front
packs the event variance into far fewer days, so its IV crushes much harder than the back's when
the event passes. A same-strike call calendar and put calendar are worth almost the same under
put-call parity, so a straddle calendar is roughly a doubled ATM calendar. It is a double calendar
with both strikes equal, not a separate idea.

## How these trades make money

Every time spread here is short a front-month option and long a later one, for a net debit. Over the
announcement the position has three sources of P&L:

1. **The crush (the reason for the trade).** Both expiries carry the same event variance, but the
   front spreads it over a few days and the back over a month. When the event passes, the front loses
   most of its value and the back only a little. Being short the front and long the back, the spread
   widens.
2. **The gap (the risk).** The front is short gamma. A large move turns the front leg into mostly
   intrinsic value, which grows faster than the back leg gains, so the spread collapses toward zero.
   The loss is capped at the debit while the position is held as a spread.
3. **The back leg's IV after the event.** The back keeps its base vol. If base vol falls after the
   report as well, the long leg loses more than the crush model assumed.

Costs come off all three. At a fair price the average outcome is about zero before costs. **The edge,
if there is one, is choosing names whose gaps are reliably smaller than the options price.** That is
what the screen is for.

### A real trade: TXN call calendar, January 2026

A single ATM-ish call calendar, opened the afternoon before the report and closed the next morning:
exactly the hold this plan assumes.

**Entry, 2026-01-27 14:38, the day before earnings**

| Action | Qty | Expiry | Strike | Price |
|---|---|---|---|---|
| Buy to open | +5 | 20 Feb 26 (24 days) | 197.5 call | 8.68 |
| Sell to open | −5 | 30 Jan 26 weekly (3 days) | 197.5 call | 6.78 |
| **Net** | | | | **1.90 debit** (−$950) |

**Exit, 2026-01-28 08:30 (as stamped, probably Central time; options don't trade at 08:30 ET), the day after**

| Action | Qty | Expiry | Strike | Price |
|---|---|---|---|---|
| Sell to close | −5 | 20 Feb 26 | 197.5 call | 11.92 |
| Buy to close | +5 | 30 Jan 26 weekly | 197.5 call | 9.57 |
| **Net** | | | | **2.35 credit** (+$1,175) |

**Leg by leg** (whole position, 5 contracts × 100):

| Leg | Entry | Exit | P&L |
|---|---|---|---|
| Long Feb 197.5 call | −$4,340 | +$5,960 | +$1,620 |
| Short Jan 30 197.5 call | +$3,390 | −$4,785 | −$1,395 |
| **Gross** | −$950 | +$1,175 | **+$225** (+24% on the debit) |

Fees on the suite's schedule are about $12 ($1 a contract to open, nothing to close, plus
pass-throughs), so net is about **+$213**. Both fills were limits, so the slippage is already inside
the prices.

**What happened.** Both calls rose, so the stock gapped up through the strike. The front call at 9.57
with two days left is now mostly intrinsic value, which puts the stock somewhere around 205–207. That
is inferred from the price, not observed. The short front lost $1,395, but the long back gained
$1,620: it kept its time value and its delta, while the front's event premium vanished. The gap was
roughly 4–5% (also inferred, from a pre-report price near the strike), but it was **inside the trade's endurable gap**, so it still paid. A gap well
past that and the two calls converge toward the same intrinsic value, and the spread collapses
toward zero.

It is one trade, so it shows the mechanism, not an edge.

### Worked example: straddle calendar

Illustrative numbers from a simple normal model, not a forecast. Stock at 100, reporting after
tonight's close. Base vol is 30% a year. The market prices a one-day event move with a 6% standard
deviation.

| | Front (3 days) | Back (31 days) |
|---|---|---|
| Event share of total variance | 83% | 32% |
| IV at entry | ~73% | ~36% |
| 100 straddle at entry | 5.26 (sold) | 8.46 (bought) |
| IV the morning after (base only) | 30% | 30% |

The **debit is 3.20, or $320** for one straddle each way. The suite's expected move (0.85 × the front
straddle) is about ±4.5%. The next morning, at the stock's new price:

| Stock next morning | Front straddle | Back straddle | Spread | P&L (gross) |
|---|---|---|---|---|
| 100 (unchanged) | 1.77 | 6.86 | 5.09 | **+$189** |
| 104 (+4%) | 4.06 | 7.59 | 3.53 | +$33 |
| 106 (+6%) | 6.05 | 8.47 | 2.42 | −$78 |
| 120 (+20%) | ~20.0 | ~20.1 | ~0.1 | ≈ −$310, near the whole debit |

The front lost 3.49 on an unchanged stock and the back lost 1.60. That gap between the two crushes is
the profit. In this normal model the move is symmetric, so a fall works the same way.

- **The endurable gap** is where the P&L crosses zero: about ±4.6% gross, and about ±4.1% after an
  assumed $25 round trip in costs. The engine prices with Black-Scholes, which makes it a little
  lopsided: −4.4% / +4.8% gross, −3.9% / +4.4% after costs. **That is inside the ±4.5% expected move.** The trade can lose on
  a move the market considered ordinary, which is why coverage is measured against the endurable gap
  and not the implied move.
- **The back-leg risk:** if base vol falls from 30% to 27% after the report, the unchanged-stock
  result drops from +$189 to about +$138.
- **What it needs to work:** names where, say, 10 of the last 12 gaps sat inside ±4.1% (83% coverage),
  while the market still priced a 6% event.

**As trade tickets** (one of each leg; ATM call and put are equal in this model):

*Entry, the afternoon before the report*

| Action | Qty | Expiry | Strike | Price |
|---|---|---|---|---|
| Buy to open | +1 | 31 days | 100 call | 4.23 |
| Buy to open | +1 | 31 days | 100 put | 4.23 |
| Sell to open | −1 | 3 days | 100 call | 2.63 |
| Sell to open | −1 | 3 days | 100 put | 2.63 |
| **Net** | | | | **3.20 debit** (−$320) |

*Exit the next morning, stock unchanged at 100*

| Action | Qty | Expiry | Strike | Price |
|---|---|---|---|---|
| Sell to close | −1 | 30 days | 100 call | 3.43 |
| Sell to close | −1 | 30 days | 100 put | 3.43 |
| Buy to close | +1 | 2 days | 100 call | 0.89 |
| Buy to close | +1 | 2 days | 100 put | 0.88 |
| **Net** | | | | **5.09 credit** (+$509): **+$189 gross** |

*Exit the next morning, stock gapped to 106*

| Action | Qty | Expiry | Strike | Price |
|---|---|---|---|---|
| Sell to close | −1 | 30 days | 100 call | 7.23 |
| Sell to close | −1 | 30 days | 100 put | 1.24 |
| Buy to close | +1 | 2 days | 100 call | 6.03 |
| Buy to close | +1 | 2 days | 100 put | 0.02 |
| **Net** | | | | **2.42 credit** (+$242): **−$78 gross** |

In the gap case both sides lose about equally. The call calendar goes from 1.60 (4.23 − 2.63) to
1.20 (7.23 − 6.03), −$40. The put calendar goes from 1.60 to 1.22, −$38. Straddle calendars at the
same strike are two equal calendars, so they win and lose together. TXN's call calendar won on a gap
of about that size. The likely reasons are that its gap was a little smaller (inferred, not observed)
and that its back month kept more IV than this model's assumption that it falls to base vol. That
second point is exactly the back-leg variable the screen measures.

### Worked example: OTM call calendar

Same stock and vols. Sell the 3-day 105 call, buy the 31-day 105 call. The 105 call is 0.85 in the
front and 2.20 in the back, so the **debit is 1.35, or $135**. The next morning:

| Stock next morning | Front call | Back call | Spread | P&L (gross) |
|---|---|---|---|---|
| 95 (−5%) | ~0 | 0.52 | 0.52 | −$83 |
| 100 (unchanged) | 0.01 | 1.50 | 1.49 | +$14 |
| 105 (on the strike) | 0.89 | 3.43 | 2.54 | **+$119** |
| 110 (+10%) | 5.02 | 6.50 | 1.48 | +$13 |
| 112 (+12%) | ~7.0 | 8.00 | 1.00 | −$35 |

*Entry, the afternoon before the report*

| Action | Qty | Expiry | Strike | Price |
|---|---|---|---|---|
| Buy to open | +1 | 31 days | 105 call | 2.20 |
| Sell to open | −1 | 3 days | 105 call | 0.85 |
| **Net** | | | | **1.35 debit** (−$135) |

*Exit the next morning, stock landed on the strike at 105*

| Action | Qty | Expiry | Strike | Price |
|---|---|---|---|---|
| Sell to close | −1 | 30 days | 105 call | 3.43 |
| Buy to close | +1 | 2 days | 105 call | 0.89 |
| **Net** | | | | **2.54 credit** (+$254): **+$119 gross** |

*Exit the next morning, stock fell to 95*

| Action | Qty | Expiry | Strike | Price |
|---|---|---|---|---|
| Sell to close | −1 | 30 days | 105 call | 0.52 |
| Buy to close | +1 | 2 days | 105 call | 0.00 |
| **Net** | | | | **0.52 credit** (+$52): **−$83 gross** |

**The endurable range is lopsided:** about −0.4% to +10.5% before costs. It pays most if the stock
lands near the strike, roughly breaks even if it sits still, and loses if it falls. That is a
directional trade. It is worth taking only when the side read says up, and it is why each rung of the
ladder below has a higher break-even hit rate.

### The other two structures

- **Double calendar:** an OTM put calendar below and an OTM call calendar above (for example 95 and
  105). The tent is flatter and wider than the straddle calendar's, with peaks near each strike. It
  pays less on an unchanged stock and tolerates a larger move in either direction.
- **Diagonal:** the long back leg sits at a different strike, usually nearer the money (a long back
  100 call against the short front 105). It costs more and carries more delta, so it behaves like a
  calendar with a call debit spread added. It earns more if the direction call is right, loses more
  if it is wrong, and needs the most accurate side read of the four.

## What the literature says

- **The term structure prices the event.** Dubinsky & Johannes (2006), later with Kaeck & Seeger
  (RFS 2019), extract the announcement's expected move from the IV term structure. On average it is
  larger than a history-based estimate (10.4% vs 8.5%), which is the premium a calendar sells.
- **Front straddles into earnings are, on average, underpriced.** Gao, Xing & Zhang (JFQA 2018):
  ATM straddles held from three days before through the announcement day earned +3.34%. The effect
  is strongest for small, volatile, high-kurtosis, illiquid names and those with erratic past
  surprises. Selling the front works only on a subset, and these traits mark the names to exclude.
- **A concave smile signals a bimodal event.** Goyal et al. (Review of Finance 2025): concave
  short-dated IV curves before earnings predict larger moves, and calendar-straddle returns are
  significantly lower when they appear. The leg orientation needs confirming from the full paper.
- **Inverted term structures predict weak straddle returns** outside earnings (Vasquez, JFQA 2017).
  That is supportive of the short front, but not an earnings test.
- **A practitioner backtest of this exact structure** (short front straddle, long back straddle,
  ~50 delta, in the day before, out the day after) found it the best of four, at about +0.9% of the
  stock price per trade (205 trades 2020–21, 54 out of sample). Costs were not modelled. One of its
  triggers was a *high* ex-earnings IV over historical vol, which runs against a mean-reversion
  worry about the long back leg.

## The modelled endurable gap

Not a standard term. Here it means **the largest overnight gap, up or down, that the position takes
and still breaks even, after repricing it at the exit with modelled post-event IVs.** It is
computed from the entry chain alone:

1. Split each expiry's variance into a base part and the event part (the Dubinsky–Johannes
   decomposition). Both expiries carry the same event variance.
2. Remove the event variance to get each leg's post-event IV. Base vol is held unchanged, then
   shocked up and down as a sensitivity.
3. Reprice at S × (1 ± g): the front leg is mostly intrinsic plus a small residual, the back leg sits
   at its post-event IV. Subtract the debit and the round-trip costs.
4. Solve for g_up and g_down where P&L is zero, or where it reaches a tolerated loss if we choose
   one. They differ because of skew, the back leg's vega and, for directional structures, delta.

It applies unchanged to every structure in the family, and to the module's other earnings structures
(iron fly, condor, credit spread). That makes it a possible common selector across all earnings
trades, not just time spreads.

**Why not the implied move?** Practitioner checklists compare past moves with the implied move. A
time spread's break-even is not the implied move. It depends on the debit, the back month's crush
and costs, and often sits well inside it. Coverage has to be measured against the trade's own
break-even.

## The structure family

| Structure | Put strike | Call strike | Back leg |
|---|---|---|---|
| Single calendar | none, or K | K, or none | same strike, later expiry |
| Straddle calendar | K | K | same strikes |
| Double calendar | K − a | K + b | same strikes |
| Diagonal | any | any | a different strike from the short |

One parameterisation covers them all. Only the leg construction is structure-specific. Away from the
money, put-call parity no longer makes the sides equivalent, so the side becomes a real choice (see
below), not the fixed call side `atm_calendar` uses.

## The screen

**1. Eligibility gates** (pass/fail):

- Report date and timing confirmed.
- Weekly options and $100M a day in the stock.
- Both expiries quoted.
- One event inside the front, and nothing else between the two expiries: no second catalyst, no
  ex-dividend date in the front (assignment).
- An inverted term structure: front IV well above back IV.
- Package spread over debit under a cap. Costs are probably the binding gate: one worked example
  quoted a 0.17 × 0.31 market on a 0.25 debit.

**2. The core score:**

- **Coverage:** the share of the name's past N earnings gaps inside [−g_down, +g_up]. This is the
  central number.
- **Cushion:** g over the implied event move.
- **Richness:** the implied event move over the median past gap. `entry_reviews` already records
  `implied_vs_avg_actual` and the historical-move stats, which can be reused.

**3. Vetoes:**

- A concave front smile.
- A blowout in the name's history (the largest past gap well beyond g). `move_tail_veto` is the
  existing record-only version of this.
- The Gao traits: small, high-kurtosis or erratic-surprise names.

**Measured, not filtered:** the back leg's ex-event IV over non-event realised vol. The
mean-reversion argument and the practitioner trigger point in opposite directions, so the backtest
decides its sign before it becomes a gate.

**The checklist items** this covers, for reference: timing, single event, term-structure inversion,
coverage, cushion, smile shape, name traits, back-leg valuation, costs over debit, liquidity on both
expiries, assignment exposure (a deep in-the-money short bid below parity), and a fixed exit plan
(the morning after, worked through the first hour, never legged out, because legging out turns a
defined debit into a naked leg).

## Choosing the side and the structure

An OTM calendar or diagonal is a bet on **where the stock lands**: the tent peaks at the short
strike, so a call calendar at S + x does best near S + x and loses on an overshoot or a fall. A side
signal sets the side; the expected-move offset sets the distance.

**The bar is high.** The expected-move butterfly study, the nearest shape we have, needed the side
call right 68–83% of the time after costs ([expected-move-fly-plan.md](expected-move-fly-plan.md)).
Our best signal there was 53%. A calendar's bar should be lower (a wider tent, and the front crush
pays even when the stock sits still), but it has to be measured: **the break-even hit rate for each
structure comes first, before any signal is read.**

**Signals are combined by family, not by indicator,** since indicators built from the same price
history count one opinion several times:

- **Price direction:** Bearing, trend score, RS, momentum.
- **Options positioning:** rr25 skew and side richness (front wing against back). This is the
  closest to the strategy's own edge, and it is a value choice, not a forecast.
- **Fundamentals and revisions:** consensus revision, beat record. These showed nothing in the
  butterfly study, so they may end up as a veto at most.

Each family gives +1, 0 or −1, with its cuts **and its sign** fitted on 2020–2023 only (rr25 came out
contrarian there, against the literature). The combined score is the sum, with no fitted weights.
The score maps onto a ladder:

| Agreement | Structure |
|---|---|
| Families conflict, or all 0 | Neutral double or straddle calendar, or skip |
| One family, the others neutral | Neutral, or a slightly OTM calendar on that side |
| Two agree | OTM single calendar on that side |
| All three agree, and the endurable gap clears both ways | Diagonal |

Each rung's break-even hit rate is higher than the one below, so the backtest also tells us where the
ladder stops. When price direction and positioning disagree, the default is neutral: a trend read
never overrides the options market's own skew. **Direction picks the side and the structure;
coverage decides whether to trade at all.** No signal overrides the endurable-gap gate.

The prior is that price direction is weak: it failed in rounds 4 and 5 and in the butterfly study. So
the likely outcome is that positioning does most of the work. The drop-one test below is what would
show it.

## The backtest, when data is available

Declared before it runs, as the earlier history studies were.

- **Step 0, on rows we already have:** if coverage means anything, the 91 retired paper calendars
  should have lost mainly at low coverage. If the losers had high coverage too, the idea fails here.
- **Grid, kept small:** the structures above, strike offsets of about 0, 0.5 and 1 × EM, two
  back-expiry rules, one or two exits. Each extra cell is another comparison.
- **Every event priced both ways** (call side and put side), so a signal is scored on what it adds:
  the chosen side against the neutral structure and against a random side.
- **Fit on 2020–2023, score on 2024 onward.**
- **Pass criterion:** the top coverage tercile is net-positive after costs out of sample, in most
  years, and beats the unscreened set.
- **The combination is tested for what it adds:** drop one family at a time, report how often the
  families agree, compare the measured hit rate with each rung's break-even, and print cell counts.
  A cell with 30 events is anecdote.
- **Costs** use `cherrypick.core.fees`, as the earlier studies did. Early assignment is measured,
  not modelled.

**Data.** The exit has to match the gap measured. A morning-after exit needs intraday quotes; on
end-of-day data it can only be priced at the reaction-day close, which is a different trade. The
Dolt chains also list few expirations a day (and only Mon/Wed/Fri before 2024), so many events lack a
usable back month. Both point to running this on the intraday options history rather than Dolt.

## Decisions still open

- **Gap definition:** close to mid-morning (needs intraday data), or close to open as a coarser
  stand-in.
- **History depth:** how many past quarters (8? 12?), knowing that old quarters may not describe
  the company today.
- **Loss line:** solve the endurable gap for break-even, or for a tolerated loss such as −25% of the
  debit.
- **Scope of the selector:** time spreads only, or the endurable gap across every earnings strategy.

## If it passes

Re-opening time spreads is measurement-affecting: it changes which arms exist. So it lands at a
declared boundary and is journaled in the operating history. The screened versions go in as new
strategy keys, so `STRAT_TEST_RETIRED` stays truthful, and their rows are never pooled with the
retired, unscreened ones, which ran under an older exit rule. Paper only, as with every new earnings
strategy.

## Sources

- Dubinsky & Johannes, [Anticipated uncertainty, earnings announcements and equity options
  (2006)](https://business.columbia.edu/sites/default/files-efs/pubfiles/6051/DJ_2006.pdf); Dubinsky,
  Johannes, Kaeck & Seeger, [Option pricing of earnings announcement risks (RFS
  2019)](https://sussex.figshare.com/articles/journal_contribution/Option_pricing_of_earnings_announcement_risks/23454023)
- Gao, Xing & Zhang, [Anticipating uncertainty: straddles around earnings announcements (JFQA
  2018)](https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/anticipating-uncertainty-straddles-around-earnings-announcements/7B34877AD5E06304BA3C55FBA3219FDD)
- Goyal et al., [Pricing event risk: evidence from concave implied volatility curves (Review of
  Finance 2025)](https://www.sfi.ch/en/publications/pricing-event-risk-evidence-from-concave-implied-volatility-curves.)
- Vasquez, [Equity volatility term structures and the cross section of option returns (JFQA
  2017)](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/equity-volatility-term-structures-and-the-cross-section-of-option-returns/F0A40E99FD2458367DD9A56A89783D38)
- ORATS, [earnings options strategies backtest](https://orats.com/blog/earnings-options-strategies-backtest)
- Checklist material: [earnings-watcher](https://earnings-watcher.com/wiki/calendar-spreads-around-earnings),
  [OneOption](https://oneoption.com/options/calendar-spreads/)
