# cherrypick-manage

The daily position-management evaluation. At 10:00 ET on trading days the orchestrator reads the live
broker account (read-only) and this package judges every trade in it against a configurable ruleset.
For each trade it reports an action, the rule behind it, a confidence, the closing order at mid, and
for a trade it would hold, the levels at which it would stop holding. The result is on the console's
**Live → positions** and **Live → portfolio** tabs.

It is advice. Nothing here places an order.

## Turning it on

1. In the orchestrator config (`~/.cherrypick/config.json`) set `manage.enabled` to `true`, or flip
   *Daily position evaluation* under **Config → Position rules**. The supervisor re-derives its jobs from
   config on every pass, so the `manage-eval` job appears without a reinstall.
2. Optionally copy `config.example.json` to `~/.cherrypick/config/manage.json` and change thresholds.
   The file is merged key by key over the default, so it may hold only what you change. The console's
   Config page can only edit keys the file already has, so a full copy is the easier start.

Run it by hand any time with `python run.py manage`, and try a rule change on a past day with
`python -m cherrypick.manage reevaluate --session 2026-10-13 --rules trial.json`.

## How a trade is judged

Each position is judged as the broker's **order chain**: the order that opened it and every roll or
adjustment since, rebuilt from the account's trade history. The chain's net cash is the trade's basis,
so a roll's credit counts. GLD on 2026-10-13 was opened for 8.33 db and rolled twice for 6.40 cr:
1.93 db in all, where its open legs alone said 11.04 db. Two orders on one name that never touched
each other are two trades (SOFI's short put and its call spread). The chain's recorded fees give a net
P&L beside the gross one.

A chain is used only when its own orders, replayed, leave exactly the quantities held today. When the
history is missing, starts after the position did, or the broker moved a leg without an order, the
trade falls back to grouping by expiry (a diagonal stays whole) and is priced from its open legs, and
the page says so. A net credit is a **credit trade**, a net debit a **debit trade**.

Every rule that fires names an action. The trade's recommendation is the most decisive one (close,
then take profit, then roll-or-close, then close legs, then review) at that rule's confidence, less 10
points for each fired rule pointing the other way (rolling against closing). A trade where nothing
fired is a **hold** at 70, less 10 when an earnings report falls inside its short legs' life.

## Shapes

Every trade is named (`shapes.py`) and the page shows it. Two shape families change how a trade is
judged:

- **Super bear / super bull / risk reversal.** A super bear is a long put spread below a short call
  spread (OIC's double bear spread); a super bull is the mirror. A risk reversal is long one side and short
  the other. These earn from direction, and are usually opened near even money. When the net premium is
  under a quarter of the modelled max loss, the trade is `directional`: it takes profit at half its max
  gain (capped at 1x its risk), stops at half its max loss, and exits at 21 DTE. Percentages of a $23 credit
  measure nothing; XLU on 2026-10-13 was being closed on a $30 loss against a $277 risk.
- **Jade lizard** (and the variant with a put wing). Taken in for at least the call spread's width, it has
  no upside risk, so an in-the-money call is not a broken thesis: the breached-strike rule looks only at
  its puts.

A **broken-wing butterfly** bought for under a quarter of its max loss is judged the same way (`risk-judged`):
its premium says little about the tail beyond its wider wing. GLD on 2026-10-13 was $193 in with $1,693
at risk, and the percentage of the $193 said nothing useful. So is a **ratio butterfly**: long-short-long at
three strikes, the wings together matching the body but not 1-2-1, named by its quantities from the top
strike (`1-3-2`; SPY on 2026-10-18 was +1 745P / -3 740P / +2 730P, $200 in against $1,300 at risk), or a
**ratio time spread** when its legs span expiries. Ordinary butterflies, condors, verticals and
diagonals are named but judged by their premium as before.

## Checking a roll before sending it

A credit roll is not automatically a good roll. `check-roll` judges a proposed order against the trade
as the latest evaluation sees it:

    python -m cherrypick.manage check-roll --trade GLD \
        --close "-2 2026-10-23 425C @0.14" --close "+1 2026-10-23 445C @0.05" \
        --open "-2 2026-11-20 415C @2.57" --open "+1 2026-11-20 435C @1.01"

It checks:
- that the roll takes in a net credit;
- the shape before and after;
- whether the shorts move into the long's own expiry;
- strikes where the trade ends up net short (unlimited for calls);
- that every short stays beyond the long's breakeven, counting the roll's credit;
- max loss before and after, and against the per-trade budget;
- earnings inside the new shorts' life.

The verdict is `reject` for a debit roll, an unlimited loss, or a naked put rolled through earnings;
`risky` with its reasons; or `ok`. GLD's own 10/09 roll comes back risky on three counts: the shape
changes from diagonal to broken-wing butterfly, the shorts move into the long's expiry, and what it can
lose grows.

## The default ruleset (`manage-2026-10`)

Built on 2026-10-13 from an options-education vendor's webinar library (2022–2026), following the
presenter who cuts losers rather than the one who repairs them, and checked against outside research.
"Vendor" below is that library.

| Rule | Default | Action | Conf. | Source | Outside evidence |
|---|---|---|---|---|---|
| `expiry` | physically-settled legs at ≤ 3 DTE | close | 90 | vendor, every year; the $600K post-close assignment case | Confirmed: FINRA's exercise cutoff is 5:30 pm ET, and auto-exercise is at $0.01 in the money |
| `credit.take_profit` | 50% of the credit kept | take profit | 80 | vendor, unchanged 2022–2026 | Improves win rate, P&L per day and drawdown, but lowers P&L per trade; best risk-adjusted is 50–75%; 25% loses after commissions |
| `credit.stop` | loss = 1× the credit (else half the modelled max loss) | close | 70 | vendor, unchanged | Cuts tails and usually costs total return; wider stops (2×) did worst |
| `credit.time_exit` | nearest short at 21 DTE: roll a winner, close a loser | roll or close | 75 | vendor; tastylive's original rule | Reduces short-premium risk; costs return on wheel-type books |
| `credit.breached` | a short in the money and reward/risk left < 0.5 | close | 70 | the vendor's "would I open this today?" and ⅓-of-width entry rule | Untested anywhere; it is the entry standard applied again |
| `credit.close_before_earnings` | off | close | 55 | the repair-style presenter (not the default) | No data |
| `debit.stop` | −50% of the debit | close | 65 | vendor, "hard and fast" | No outside test; stop-loss theory says it only helps when trends persist |
| `debit.take_profit` | +75% on the debit | take profit, roll or add | 65 | vendor (75–100%) | No outside test |
| `debit.time_exit` | nearest short at 21 DTE, or long-only at 14 DTE | roll or close | 70 | vendor | Long premium decays fastest in its last weeks |
| `roll.naked_put` | a naked short put a rule says to close: same strike, next monthly 4+ weeks out, modelled credit ≥ 0.10 | roll for credit (in the close's place) | 60 | your rule, 2026-10-14: rolling out in time for a credit can be useful, especially for naked short puts | Mixed: rolling tested short puts beat holding them (tastylive); repairing defined-risk spreads did not |
| `roll.diagonal_short` | a diagonal's short structure at 21 DTE or 80% captured: same strikes, next monthly 4+ weeks out | roll for credit | 60 | your rule, 2026-10-14; the vendor's PMCC teaching (close or roll the short at 14-21 DTE) | Our own PMCC study: the short calls' edge rests on the IV assumption and the held long does most of the work, so the roll is routine, not a rescue |
| `legs.short_take_profit` | a short leg 80% captured, bought back with its wings | close legs | 85 | vendor: buy back nickels; close credit legs near max before earnings | Mechanically sound; nothing left to earn |
| `ex_dividend` | ITM short call over a declared ex-date with time value < the dividend | review | 80 | vendor | Confirmed by option theory; in practice often not exercised |
| `risk.oversize` | modelled downside > 3× (2% of net liq) | review | 60 | vendor's 2%-per-trade rule | Direction supported (fractional Kelly); the numbers are convention |
| `directional.take_profit` | a `directional` trade (net premium under ¼ of its modelled max loss) at half its modelled max gain, capped at 1× its risk | take profit | 65 | our rule, 2026-10-13: a percentage of a small premium measures nothing | No outside test |
| `directional.stop` | a `directional` trade at half its modelled max loss | close | 65 | our rule, 2026-10-13 | No outside test |
| `earnings_trade` | an **earnings trade**: opened 1–7 days before a report, its nearest short expiring no more than 14 days after. Before the call it holds, and its close, take-profit and roll rules wait; after the call (the day after the report, or the report's day when before the open) it closes, and that day, not expiry less 21 days, is its time exit: the report's day when before the open (the broker's `BTO`, a calendar's `BMO`), else the next trading day. An untimed report waits the extra day, so the snapshot fills a time the broker leaves blank from Nasdaq's calendar for the same date (`earnings_time_source: "nasdaq"`) | hold, then close | 75 | your practice, 2026-10-18: earnings trades go on 1–7 days before the call and come off after it | None tested here. Safety rules (expiry and assignment, ex-dividend), sizing and unpriced legs never wait |

**An earnings trade is remembered past its report.** The day after a report the broker may already
show next quarter's date. So the `evaluate` command carries each earnings trade the previous evaluation
found (by underlying and short expiry) into the snapshot as `earnings_trades_seen`, where `reevaluate`
reads it too. A name excused with `positions.<SYMBOL>.disable: ["earnings_trade"]` is not one at all:
its evaluation carries no `earnings_trade`, its ordinary exits stand, and nothing is carried forward.

**A roll is only ever for a net credit.** A winner at its time exit may roll out, but only if the roll
takes in money; one that would cost a debit is a close. A losing spread at its time exit closes.

**A naked short put may roll out instead of closing** (`roll.naked_put`, confidence 60). When a rule
says to close a trade that is nothing but short puts at one strike, the evaluation prices the same
strike at the first monthly at least four weeks further out. If that roll is modelled to take in at
least 0.10 a share, it is recommended in the close's place, with the close kept as the fallback. The
credit is an estimate (today's IV for the new expiry, no term structure), so it says to send the roll
only for a net credit and to close the put if it will not fill for one. **A put is never rolled through
earnings** (`skip_through_earnings`): when the report falls on or before the new expiry, the close stands,
and the explanation suggests re-opening after the report.

**A diagonal rolls its short side for a credit** (`roll.diagonal_short`, confidence 60). In a trade whose
shorts expire before a long they are sold against, the short structure (the shorts and any wings in
their expiry) rolls out to the next monthly at least four weeks later, at the same strikes, when it
reaches the 21-day exit or has given up 80% of its premium. It applies only when all of these hold:
- the trade is not stopped out (selling new shorts against a long that has failed is repairing a loser);
- the long outlives the new short by 45 days;
- the roll is modelled to take in at least 0.10 per unit;
- every new short stays beyond the long's breakeven (the long strike plus the chain's net debit per share,
  or minus it for puts), so an assignment cannot lock in a loss.

Earnings inside the new short's life are flagged, not blocked, because the long covers the short through
a report. When the roll is not taken, the explanation says which condition stopped it.

## The account as one book

Beside the trades, each evaluation reads the whole account as one book (`portfolio.py`; the plan is in
[docs/portfolio-guidance-plan.md](../../docs/portfolio-guidance-plan.md)). It records:

- **beta-weighted delta (BWD)** in SPY shares, and what a 1% SPY move does to it. The beta is the
  broker's own, so the figure can be checked against the broker's screen;
- **theta** in dollars per calendar day and as a share of net liq;
- **the break-even move**: the SPY move that wipes out a day's theta, beside the move SPY's implied
  volatility expects that day;
- **vega**, raw and weighted to a 30-day equivalent;
- **buying power used** and the account's kind (margin or cash);
- **a stress line**: the book repriced at SPY −10% and +5%, each name moved by its beta;
- **each underlying's share** of the book's theta and BWD;
- **beta doubts**: names whose broker beta disagrees in sign with its own three-month correlation,
  which happens (RTX, FXI, DELL on 2026-10-18), so their BWD may be misstated;
- **the broker check** (`broker_check`, from snapshot v9): BWD and theta summed again on the
  broker's own per-leg greeks, over the same legs with the same betas, with the gap overall and by
  underlying. A leg only one model prices is named and left out of both. It decides nothing; it is
  the daily record of how far our model sits from the broker's before the bands are trusted.

Everything is modelled (Black-Scholes at each leg's own IV). A leg the model cannot price, or an
underlying with no beta, is named and left out, never counted as zero; the reading says it is
partial. A leg expiring today is taken at its moneyness, with its remaining time value as that day's
theta.

The bands are retail heuristics. None has published out-of-sample evidence, so each runs at
confidence 40. Each fires REVIEW and names what drives it:

| Rule | Default | Source | Outside evidence |
|---|---|---|---|
| `portfolio.theta` | theta 0.1–0.3% of net liq a day; above the ceiling is oversizing | retail options education | None. Theta is paid for by gamma, so more theta is not more edge |
| `portfolio.delta` | BWD from −0.5× to +0.75× theta (SPY shares), leaning long; ±0.5% of net liq per 1% move when theta is not positive | retail options education ("BWD within about half the theta") | None |
| `portfolio.breakeven` | short gamma, with a break-even move under SPY's implied one-day move | option theory: theta = −½ Γ S² σ² | The identity is exact; whether acting on it pays is untested |
| `portfolio.concentration` | one name over 25% of gross theta or gross BWD | convention | None |
| `portfolio.buying_power` | buying power used over a cap set by VIX: 25% under 15, 30% under 20, 35% under 30, 40% under 40, 50% above | retail options education (more room when premium is rich) | None. On 2026-10-18 the account used 53% against a 30% cap at VIX 20 |

Each band is written into the reading with the figure's place in it (`bands`), so the console draws
the band manage used and never works one out itself.

**When `portfolio.delta` fires, a hedge is sized** (`hedge.py`) in XSP: European, cash-settled, a
tenth of SPX, and allowed in an IRA as a defined-risk spread. The snapshot carries one quoted XSP
expiry: the monthly 30–60 days out, preferring 45–60. Two verticals are offered, each `width` (5)
points wide, sized toward a point a quarter of the band's width inside the breached edge:

- a **credit spread** on the side the book leans away from, short near 30 delta. It cuts delta and
  adds theta, and the short gamma that comes with it;
- a **debit spread** on the side it leans toward, long near 40 delta. It cuts delta and usually costs
  a little theta.

Each shows its legs, the mid and the natural price (sell at the bid, buy at the ask), the cash, the
max loss, and the book after it: BWD (and whether that is inside the band the book would then have),
theta, the break-even move and buying power used. A credit spread never takes buying power used past
50% of net liq, and one that cannot fit says so. A debit spread is exempt: its cost is its whole
worst case and it only lowers the book's, so it is offered past the cap and marked `past_cap`. The
mid is not a fill. Nothing is placed.

**Scenarios and the expiry ladder.** The reading also carries:

- a **risk matrix**: the book's P&L across SPY moves (−10% to +10%) and IV shifts (−5, 0, +5 points),
  each name moved by its beta, time held still. The shift is stated for a 30-day option and scaled
  to each leg by √(30 / DTE), so front months move most. A flat shift once showed the book *gaining*
  $425 on +5 points, carried by long-dated longs, while the front months a vol spike hits were short;
- a **risk graph**: P&L across SPY −15% to +15%, now and at the nearest expiry (legs expiring by then
  at intrinsic);
- an **expiry ladder**: per expiry, the trades whose nearest short expires then, what they can still
  lose and the theta of the legs expiring then; and the share of theta and downside in trades with
  earnings before their nearest short expires (72% of theta on 2026-10-18).

Every figure is modelled repricing, not a forecast.

**Income trades, sized to the account** (`candidates.py`). The snapshot carries the previous
afternoon's income reading (`fetch_screener_greeks.py income`). It covers every name the liquidity
verdict calls tradable, on the cycle monthly, quoted in regular hours, with earnings dates, beta and
the name's stress move. From it manage builds the screener rules' structures:

- a short put near 0.40 delta;
- put and call credit spreads, short the first strike out of the money and long near 0.25 delta, at
  a credit of at least a third of the width;
- a covered call near 0.165 delta, on shares the account holds.

It drops any structure with an illiquid or unquoted leg, or an earnings report inside the trade. An
unknown earnings date is flagged, never called clear. Each structure left gets the largest quantity
that clears every cap, all scaled by net liq:

| Cap | Default |
|---|---|
| per-trade risk: a spread's max loss, a short put's stress loss (the name's 1st-percentile move over the holding period, at expiry, less the credit) | 3% of net liq |
| a short put's notional (strike × 100) per name | 25% of net liq in a cash account or IRA, 50% on margin |
| buying power used after the trade | the VIX-scaled cap (`bands.buying_power`), else 50% |
| beta-weighted delta | stays inside its band, or moves toward it |
| theta | stays under the band's ceiling |
| one name's theta | under 25% of the theta the account may carry |

Buying power follows the account kind: cash or an IRA secures a put at its strike, Reg T margin takes
its formula, and portfolio margin is not modelled. The list is ranked by theta per dollar of buying
power. A trade that does not fit is shown with the cap that stopped it. A short put or spread the rule
built too wide is offered narrowed: the same short, and the widest long that keeps one unit under the
per-trade cap and still takes a third of the width. Priced at mid from a day-old reading: re-quote
before sending.

## Known limits

- **Marks are mids** and the payoff model is Black-Scholes at today's IV per leg, with no dividends.
  It bounds what a trade can still do; it is not a price.
- **Fees are known only for a reconciled chain.** A trade priced from its open legs shows gross P&L only, and its basis misses any earlier roll.
- **Ex-dividend dates are the last declared ones.** The rule fires once the next ex-date is announced.
- **Earnings dates are the broker's expected dates**, which can move.
- **SPX 0DTE is left out** (`exclude.zero_dte_underlyings`): it is the flies live loop's own book,
  managed by that loop and cash-settled at the close.
- **Thesis is not knowable.** `credit.breached` is a mechanical stand-in for "the reason for the trade
  is gone". Use `positions.<SYMBOL>.intent` and `disable` to mark a hedge or a position you mean to be
  assigned on.
