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
so a roll's credit counts. A trade on AAA (invented figures) opened for 7.20 db and rolled twice for 5.10 cr:
2.10 db in all, where its open legs alone said 9.80 db. Two orders on one name that never touched
each other are two trades (BBB's short put and its call spread). The chain's recorded fees give a net
P&L beside the gross one.

**Every trade has a stable ID** (`tradeids.py`), `<NAME>-<YYMMDD>-<n>`: its name, the day its chain
opened, and `n` for trades opened on the name that day (`XYZ-260401-1`). It is assigned once and kept
in `trade-ids.json` with every order the chain has held, so a roll keeps it and a new trade on the name
never renumbers it (the old `XYZ#2` labels did). A trade whose chain can no longer be rebuilt (its
opening has left the 400 days of history read a name) keeps its ID by its exact legs. A join that makes
one trade of two keeps the older ID and the card says the other was merged into it. A trade with
neither a chain nor a match gets a provisional `<NAME>-open-<hash>` until its orders are known. Each
snapshot carries the IDs, so a re-run and the minute's re-marks give the same ones. The ID is what the
cards show, what `check-roll --trade` takes (a bare name still works for a name's only trade), and a
`trades.<ID>` key in the overrides: `intent`, `note` and `disable` for that trade alone, read over the
name's `positions.<SYMBOL>`.

A chain is used only when its own orders, replayed, leave exactly the quantities held today. When the
history is missing, starts after the position did, or the broker moved a leg without an order, the
trade falls back to grouping by expiry (a diagonal stays whole) and is priced from its open legs, and
the page says so. A net credit is a **credit trade**, a net debit a **debit trade**.

Two exceptions join two chains. A short call sold in an order of its own, after the last one sold
against a long call expired, is a chain of its own, and alone it reads as a naked call. When the PMCC
view links that short to the long call it is sold against, and the link reconciles (the PMCC shape:
one long, one short of the same size at or above its strike expiring first, no shares; its orders
replaying to exactly those two legs), the two are one trade (`grouped_by: "pmcc link"`), whether or
not the trade is recognised as a PMCC: coverage is a fact about the legs. Its basis is every order the
link found, the earlier shorts' premiums included. Only a group holding the long or the short alone
is moved; an unreconciled link or a group with other legs keeps the chains' grouping.

The second covers what the link cannot reach (`grouped_by: "covered call"`): a long expiring with the
short, a long covering more contracts than the short, or a link that does not reconcile. When the name's groups hold exactly one long call alone and exactly one short call alone,
both reconciled order chains, the long expiring on or after the short at or below its strike in at
least its quantity, and the name holds no shares (shares covering a call are the wheel's), the two
are one trade. Its basis is the two chains' orders; nothing links the shorts that expired earlier in
orders of their own, so their premiums are not in it. Two lone longs or shorts that could pair, a long
that covers only part, or a chain that does not reconcile keep the chains' grouping. A long call alone
and a short call alone with nothing covering it stay as they are.

Every rule that fires names an action. The trade's recommendation is the most decisive one (close,
then take profit, then roll-or-close, then close legs, then review) at that rule's confidence, less 10
points for each fired rule pointing the other way (rolling against closing). A trade where nothing
fired is a **hold** at 70, less 10 when an earnings report falls inside its short legs' life.

## Shapes

Every trade is named (`shapes.py`) and the page shows it. Besides the verticals, condors, butterflies,
calendars and diagonals, manage names the structures of the broker's Options Strategy Guide (2023):
the short straddle and strangle, the big lizard, the reverse jade and reverse big lizards, the call and
put zebra (two longs against one short further out), the call and put front-ratio (one long against
two shorts further out), and the broken-heart butterfly (a long vertical financed by a wider short
vertical further out). A structure that matches none of them is `custom`, and is judged as a credit or
a debit trade like any other.

Three shape families change how a trade is judged:

- **Super bear / super bull / risk reversal.** A super bear is a long put spread below a short call
  spread (OIC's double bear spread); a super bull is the mirror. A risk reversal is long one side and short
  the other. These earn from direction, and are usually opened near even money. When the net premium is
  under a quarter of the modelled max loss, the trade is `directional`: it takes profit at half its max
  gain (capped at 1x its risk), stops at half its max loss, and exits at 21 DTE. Percentages of a $23 credit
  measure nothing; CCC on 2026-01-12 (invented) was being closed on a $24 loss against a $260 risk.
- **Lizards.** A jade lizard (and the variant with a put wing) or a big lizard, taken in for at least
  the call spread's width, has no upside risk, so an in-the-money call is not a broken thesis: the
  breached-strike rule looks only at its puts. A reverse jade or reverse big lizard is the mirror: taken
  in for at least the put spread's width, it is judged on its calls alone.
- **Shapes with their own profit target** (`by_shape` under `credit.take_profit` and
  `debit.take_profit`). A short straddle, an iron butterfly and both big lizards take profit at 25% of
  the credit: one of their short strikes is nearly always in the money, so they rarely keep half. A
  calendar, and a call or put zebra, take profit at +25% on the debit. The targets are the strategy
  guide's, at confidence 55. Any shape manage names can be given its own target in the override file, and
  a shape it never names is reported.

A **broken-wing butterfly** bought for under a quarter of its max loss is judged the same way (`risk-judged`):
its premium says little about the tail beyond its wider wing. AAA on 2026-01-12 (invented) was $180 in with $1,620
at risk, and the percentage of the $180 said nothing useful. So is a **ratio butterfly**: long-short-long at
three strikes, the wings together matching the body but not 1-2-1, named by its quantities from the top
strike (`1-3-2`; BBB on 2026-01-15 (invented) was +1 95P / -3 90P / +2 80P, $150 in against $1,350 at risk), or a
**ratio time spread** when its legs span expiries. So is a **broken-heart butterfly**, which gives up
nearly all of its credit to open the gap between its two spreads. Ordinary butterflies, condors,
verticals and diagonals are named but judged by their premium as before. A front-ratio is named but
judged by its premium too: the call's risk is unbounded, so it cannot be judged on its max loss, and the
put's runs to the strike.

## Checking a roll before sending it

A credit roll is not automatically a good roll. `check-roll` judges a proposed order against the trade
as the latest evaluation sees it:

    python -m cherrypick.manage check-roll --trade AAA \
        --close "-2 2026-01-30 150C @0.14" --close "+1 2026-01-30 170C @0.05" \
        --open "-2 2026-02-27 140C @2.57" --open "+1 2026-02-27 160C @1.01"

It checks:
- that the roll takes in a net credit;
- the shape before and after;
- whether the shorts move into the long's own expiry;
- strikes where the trade ends up net short (unlimited for calls);
- that every short stays beyond the long's breakeven, counting the roll's credit;
- max loss before and after, and against the per-trade budget;
- earnings inside the new shorts' life.

The verdict is `reject` for a debit roll, an unlimited loss, or a naked put rolled through earnings;
`risky` with its reasons; or `ok`. A roll like this one can come back risky on three counts: the shape
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
| `credit.stop` | loss = 1× the credit (else half the modelled max loss) | close | 50 | vendor, unchanged | Cuts tails and usually costs total return; wider stops (2×) did worst. Set at 50 after a declared local study of short-strangle management on licensed history, whose plan and results stay under `~/.cherrypick` by the data licence |
| `credit.time_exit` | nearest short at 21 DTE: roll a winner, close a loser; skipped when the order chain shows the short was opened 21 days or fewer before its expiry | roll or close | 75 | vendor; the broker's original rule; the skip is your rule | Reduces short-premium risk; costs return on wheel-type books. Our declared management study tested it on trades opened about 45 days out; on one opened inside it the rule only says close on day one, and no exit for such entries has been tested |
| `credit.breached` | a short in the money and reward/risk left < 0.5 | close | 70 | the vendor's "would I open this today?" and ⅓-of-width entry rule | Untested anywhere; it is the entry standard applied again |
| `credit.close_before_earnings` | off | close | 55 | the repair-style presenter (not the default) | No data |
| `debit.stop` | −50% of the debit | close | 65 | vendor, "hard and fast" | No outside test; stop-loss theory says it only helps when trends persist |
| `debit.take_profit` | +75% on the debit | take profit, roll or add | 65 | vendor (75–100%) | No outside test |
| `credit.take_profit` by shape | short straddle, iron butterfly, big and reverse big lizard at 25% of the credit | take profit | 55 | the broker's Options Strategy Guide (2023): one strike is always in the money, so "we temper our profit target to 25%" | The 25%-loses-after-commissions result above was on OTM credit trades; none tested these shapes |
| `debit.take_profit` by shape | calendar, call and put zebra at +25% on the debit | take profit, roll or add | 55 | the strategy guide: a calendar at 10–25% of the debit, a zebra at 25% | No outside test |
| `debit.diagonal_width` | a two-leg diagonal (a PMCC or its put mirror), on its net debit with rolls counted, modelled at the short's expiry with the long at its own IV and the name's dividend yield: its best case under a third over the net (the net over 75% of the best case), or the spread worth under the net one expected move (the short's IV) past the short strike. With no IV to model, the net over 75% of the distance between the strikes | review | 60 | the strategy guide's PMCC entry rule, applied while held; modelled is your rule | Option arithmetic: a long expiring soon after the short is worth about the width at best, so there the first test is the guide's 75%-of-width rule. A long with years left keeps time value at the short's expiry that the width ignored, and the width rule flagged such trades with plenty left to make. Modelled, like every payoff level here; no outside test |
| `debit.price_stop` | a calendar: the underlying outside the band where the trade's modelled value at the short's expiry (the long at its own IV) is 25% under the debit | review (close if the stock closes there) | 45 | your rule | No outside test. A calendar's quote is wide, so a stop on its price fills badly; the move is what loses it. Modelled, like every payoff level here; the −50% debit stop stays as the backstop |
| `debit.time_exit` | nearest short at 21 DTE, or long-only at 14 DTE; a diagonal or calendar whose short was sold 14 days or fewer before its expiry at the long's 21 DTE; skipped when the clock leg was opened inside its own exit | roll or close | 70 | vendor; the long's clock for a weekly short and the skip are your rules | Long premium decays fastest in its last weeks. A one-week short is inside 21 days the day it is sold, so the short's clock fired on day one, with a time-exit date before the trade opened |
| `roll.naked_put` | a naked short put a rule says to close: same strike, next monthly 4+ weeks out, modelled credit ≥ 0.10 | roll for credit (in the close's place) | 60 | your rule: rolling out in time for a credit can be useful, especially for naked short puts | Mixed: rolling tested short puts beat holding them (the broker's studies); repairing defined-risk spreads did not |
| `roll.diagonal_short` | a diagonal's short structure at 21 DTE or 80% captured: same strikes, next monthly 4+ weeks out; a weekly short at the expiry close or 80% captured: the first quoted expiry a week or more out, while the long outlives it by 21 days | roll for credit | 60 | your rule (weekly shorts); the vendor's PMCC teaching (close or roll the short at 14-21 DTE) | Our own PMCC study: the short calls' edge rests on the IV assumption and the held long does most of the work, so the roll is routine, not a rescue |
| `legs.short_take_profit` | a short leg 80% captured, bought back with its wings | close legs | 85 | vendor: buy back nickels; close credit legs near max before earnings | Mechanically sound; nothing left to earn |
| `legs.long_vertical` | a butterfly's (broken-wing, broken-heart) long spread worth 90% of its width: sell it, keeping the short spread; never inside the expiry window, where the whole trade closes | close legs | 50 | the broker's Options Strategy Guide (2023): "we sell out of it to retain that value and either hold the credit spread, or manipulate the trade into something else like an iron condor" | No outside test. What is left is a short spread, and the explanation says what it costs to buy back and how wide it is |
| `roll.fly_off` | a broken-wing butterfly opened for a net credit: buy the wing that makes it a plain butterfly, sell the wide one, when that costs no more than the credit; not inside the expiry window | review | 55 | the strategy guide: "our initial goal with BWBs is to remove risk by rolling into a symmetrical butterfly … for a debit less than the initial credit received" | No outside test. The new wing is modelled at an IV read between the body's and the old wing's, so the debit is an estimate: re-quote, and check the strike is listed |
| `roll.untested_side` | **off by default** (the same local study; `"roll": {"untested_side": {"enabled": true}}` turns it on). A short strangle or straddle tested (one short at 0.40 delta or more, the other under 0.30): roll the untested short toward the money, same expiry, to the listed strike nearest 0.30 delta, for at least 0.10 a share at the quoted mids. It never passes the tested strike (a strangle may become a straddle, no more): **an inverted strangle always ranks below closing or rolling out in time without the inversion** (your rule), so an inverting roll, which is every in-expiry roll of a straddle's untested side, is never proposed. It is recorded as `untested_roll.inversion`, ranked last | review; roll for credit in place of the short-leg buy-back when that names the untested leg | 50 | the strategy guide: "we can roll the other untested side closer to the tested side to pick up additional credit and reduce the delta" | No outside test. It is repair, which this ruleset otherwise avoids, so it never replaces a stop or a close. Strikes and mids are the snapshot's `roll_chains` (v11): one quoted chain read per expiry holding a short put and call. On a naked strangle the risk-budget review (60) heads the recommendation and the roll is named beside it |
| `wheel.take_profit` | a wheel cycle's short marked at or under its buy-to-close: the resting order's price, else $0.03 ($0.05 on a wide market). Buys back the short alone, never the shares | take profit | 80 | the skate method (an options educator's covered-call process): a GTC buy-to-close at $0.03, $0.05 if illiquid, placed right after the fill | None tested; mechanically there is nothing left to earn at a few cents, as with `legs.short_take_profit` |
| `wheel.tested` | a wheel cycle's short at or through its strike: roll out in time or close. Priced: the **net-zero** roll (buy back at the mid, sell on the quoted monthly the strike furthest out whose mid pays for it; a put's strike capped at old strike + original premium − buy-back) and the **premium gambit** (the buy-back rests at the original sale price, the same strike sold on the monthly). Each judged by `check-roll`; neither moves toward the money | roll or close | 50 | the skate method: losers are rolled "99% of the time"; the strike cap is thetagang's | Mixed: rolling tested short puts beat holding them in the broker's studies; repairing defined-risk spreads did not. Repair, which this ruleset otherwise avoids, so a close outranks it |
| `wheel.next_leg` | shares delivered by an assignment and not yet written against: the covered call at the skate call band (`band`: fixed or by_expiry) moved to today's price, on the quoted monthly, the first quoted strike at or above it; flagged when under the wheel basis. Without bands in the snapshot it names no strike and says so | review | 50 | the skate method: calls at or above the upper band | The study: the band matches delta on average and does not beat it |
| `pmcc.width` | a recognised PMCC whose short strike less its long strike does not exceed the long's net cost per share after every premium collected. Shows the P&L if assigned in dollars, and the short's roll candidates: one per quoted expiry after the short and before the long (the next five the snapshot quotes: snapshot v14's `roll_out_chains` reads them that session, below), the most premium at 0.40 delta or less, a roll costing no more than $0.10 a share, keeping the width; each judged by `check-roll` | roll or close | 65 | the PMCC method's own entry check, kept while held; candidates as an open-source PMCC advisor chooses them (docs/strategy-sources.md) | Option arithmetic: assigned with the long exercised, the trade is worth the width, so a net cost past it loses. Selling the long instead keeps its time value, which softens it; the check assumes none is left. Our pmcc study found the short calls' edge rests on the IV assumption, so rolling is routine, not a rescue |
| `pmcc.assignment` | a recognised PMCC's short in the money with less time value than the next dividend before an ex-date inside its life, or under $0.05 at any time (the pmcc package's near-zero flag); never on a cash-settled root. Rolls priced as above | roll or close | 65 | the pmcc package's assignment-exposed region; a published PMCC guide's dividend rule (docs/strategy-sources.md) | Confirmed by option theory (a call holder exercises early when the dividend exceeds the time value given up); in practice often not exercised. An assignment against a long call leaves the account short the shares until the long is exercised or sold |
| `pmcc.roll_long` | a recognised PMCC's long at 120 days left or fewer, or under 0.70 delta today (solved with the dividend yield) | review | 45 | two published PMCC guides (docs/strategy-sources.md): roll the long at 90-120 days, or when its delta falls toward 0.70 | None tested. Retail practitioner guides; the long's decay accelerates in its last months |
| `ex_dividend` | ITM short call over a declared ex-date with time value < the dividend | review | 80 | vendor | Confirmed by option theory; in practice often not exercised |
| `risk.oversize` | modelled downside > 3× (2% of net liq) | review | 60 | vendor's 2%-per-trade rule | Direction supported (fractional Kelly); the numbers are convention |
| `directional.take_profit` | a `directional` trade (net premium under ¼ of its modelled max loss) at half its modelled max gain, capped at 1× its risk | take profit | 65 | our rule: a percentage of a small premium measures nothing | No outside test |
| `directional.stop` | a `directional` trade at half its modelled max loss | close | 65 | our rule | No outside test |
| `earnings_trade` | an **earnings trade**: opened 0–7 days before a report (the report's own day only for one after the close), its nearest short expiring no more than 14 days after (on the report's day only for a report before the open). Before the call it holds, and its close, take-profit, stop and roll rules wait, and so does the expiry close when the trade comes off after the call before its legs' expiry day; after the call (the day after the report, or the report's day when before the open) it closes, unless it is losing with a leg in a later expiry (a diagonal, a calendar), which can recover and is left to the ordinary rules (`kept`). That day, not expiry less 21 days, is its time exit: the report's day when before the open (the broker's `BTO`, a calendar's `BMO`), else the next trading day. An untimed report waits the extra day, so the snapshot fills a time the broker leaves blank from Nasdaq's calendar for the same date (`earnings_time_source: "nasdaq"`). A rescheduled report, or a corrected time, moves the trade with it |  hold, then close | 75 | your practice: earnings trades go on 1–7 days before the call and come off after it; the report's own day counts before an after-the-close call, the expiry close waits when the post-call close comes first, and a losing multi-expiry trade is kept after the call | None tested here. Safety rules (expiry and assignment, ex-dividend), sizing and unpriced legs never wait, except that expiry close; a trade closing on its expiry day still gets it |

**An earnings trade is remembered past its report.** The day after a report the broker may already
show next quarter's date. So the `evaluate` command carries each earnings trade the previous evaluation
found (by underlying and short expiry) into the snapshot as `earnings_trades_seen`, where `reevaluate`
reads it too; an evaluation with no trades (a day the broker could not be read) is passed over. The
broker's date still wins while it falls between the trade's opening and its short's expiry, so a
rescheduled report is followed; only a date past that expiry (next quarter's) defers to the record. A name excused with `positions.<SYMBOL>.disable: ["earnings_trade"]` is not one at all:
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
  or minus it for puts), so an assignment cannot lock in a loss. A short at its own long's strike (a
  calendar) is exempt: an assignment there is met by the long at the same strike, so the trade can lose
  no more than its debit.

Earnings inside the new short's life are flagged, not blocked, because the long covers the short through
a report. When the roll is not taken, the explanation says which condition stopped it.

**A short written week by week runs on the long's clock.** When the order chain shows
a diagonal's or calendar's nearest short was sold 14 days or fewer before its expiry
(`debit.time_exit.short_term_short_days`; a roll counts from the order that sold the current short), the
time exit fires when the long it is sold against reaches 21 DTE (`time_spread_long_dte`), not the short.
The short is due for its roll at the expiry close (three days out) or when 80% captured, and rolls to the
first expiry at least a week later that the snapshot's roll-out chains quote, at their mid where the chain
has the strike (else the first monthly that far out, modelled), while the long outlives the new short by
21 days (`short_term_min_long_days`). Every other condition above still holds. Without an order chain the
sale date is unknown and the short's own 21-day clock stands.

**A trade opened inside its own time exit skips it** (`skip_if_entered_inside`). When
the order chain shows the legs on the exit's clock were opened that many days or fewer before their expiry
(a credit trade's short at 21 DTE or fewer, a debit spread at 21, a long option at 14; a roll counts from
the roll), the time exit does not apply and no time-exit date is shown: the trade is managed by its take
profit, stop and the expiry close, and the explanation says so. The 21-day exit was tested on trades opened
about 45 days out; no exit for a trade opened inside it has been tested. Without a chain the exit stands.

## The wheel view

Each evaluation also follows every wheel on the account (`wheel.py`): a cash-secured put, the shares it delivered, the calls written against them, until the shares
are called away or sold. It is written into the evaluation as the account's `wheel`, one entry per
cycle, and names the evaluated trades holding its legs. The `wheel.*` rules in the table above read it.

- **Linking.** Orders are linked through the contracts they open and close, as the order chains are.
  A chain is a wheel leg when every option in it is one right sold to open or bought to close, so a
  spread, a strangle or anything with a long option stays out, and a call joins only when shares are
  held as it is sold. An assignment is matched option row to share row: the same name, the side the
  option implies, shares = contracts x 100, at the strike, within four days, each share row claimed
  once. A delivery nothing explains is flagged and never filled in.
- **Reconciled or not.** A running cycle must replay to today's shares and contracts. When it does not
  (history that starts too late, a close the history never saw opened, an unmatched delivery) it is
  `unreconciled`, with each reason listed. Shares held before the history began have
  `basis_known: false`: no basis is guessed for them, not even the positions' open price.
- **Money.** Every figure is the broker's cash from the history rows, never price x quantity, with
  fees apart. Two bases: the **tax basis** (the price each lot was acquired at, an assignment at its
  strike, first in, first out) and the **wheel basis** (what the shares held have cost once every
  premium, roll, fee and dividend in the cycle is counted, so a net-debit roll raises it). Breakeven
  is the wheel basis, or with only a put open its strike less the net cash per share. Option and stock
  P&L are apart, realised and unrealised apart; `pnl_net` adds fees and is null when any part is
  unknown. A cycle is **complete** when it is flat and its credits exceed its debits; one flat at a
  loss is not complete: until the next put on the name continues it, nothing is held and its
  status is `waiting`. A cash-settled index
  (`expiry.cash_settled_roots`) is never a wheel: no shares are delivered there, so the name is left
  out and listed in `left_out`, not read as cycles and "assignment without shares" flags.
- **Skate yield** is a comparison figure: net P&L over time-weighted capital (strike x 100 per open put,
  the shares' cost while held; a covered call adds none), x 365 / days. None under seven days, and
  flagged past 500%.
- **Today's check**, for each short option a running cycle holds: tested or not (the price at or
  through the strike), extrinsic value left at the mid, any buy-to-close resting on the day's working
  orders (snapshot v10), and the strike against the skate screen's band for its expiry, moved to
  today's price (snapshot v12's `skate_bands`; null, saying why, when the snapshot has none).

**The wheel rules and the trade's other rules.** A wheel rule fires into the list of the trade that
holds the cycle's short (or, for the next leg, its shares) and is ranked like every other rule, by
action: close, then take profit, then roll-or-close, then review. So a stop, the breached-strike close
or the expiry close still outranks the wheel's roll, and a naked put's credit roll is still offered in a
close's place; the wheel's rolls stay on the trade (`wheel[].rolls`) for a person to read. A wheel roll
never moves a strike toward the money, so it can never invert against the other side of the wheel.
`wheel.take_profit` agrees with the credit take-profit when both fire. The wheel rules wait for an
earnings trade's call like the credit rules. **An unreconciled cycle gets no wheel rule at all**, only a
review (`wheel_data`, no confidence) saying it does not reconcile and why.

**Rolls are priced on the snapshot's quoted chains.** Since snapshot v14 the orchestrator reads, for
each short a roll out in time starts from (a covered call, a PMCC's short, and a short put on a name
holding no long put, each within 60 days of expiry), the next five listed expiries after it: before
the long's expiry for a PMCC, within 60 days of the short's otherwise. Each is quoted that session
with the broker's greeks, 40 strikes either side of the short's strike, and kept to the short's right
(`roll_out_chains`). One read per expiry per name, plus one listing per name the stream cache does not
list; the step is capped at 30 reads and five minutes (`manage.roll_out` in the orchestrator config),
and what the cap cuts, or a read that fails, is recorded in the snapshot, never fatal. The wheel rolls
to the **nearest** of those expiries that prices the net-zero roll, else the nearest that quotes
anything at or beyond the strike, and lists the expiries it looked at (`considered`). Where the
snapshot has no such chain for the name (any snapshot before v14, a failed read, a short outside the
bounds), the roll is priced as before on the income reading (an afternoon's quotes, stamped `taken_at`), which lists every
tradable name's strikes and two-sided quotes on the cycle monthly; a short expiring on or after that
monthly, or a name the reading does not cover, then gets no roll, and the rule says so. Both
rolls go through `check-roll`, which now counts shares held behind a covered call (the command counts
the name's shares itself): coverage, and a covered call's risk as the shares falling, with the per-trade
budget judged only on option-only trades.

## The PMCC view

Each evaluation also reads every poor man's covered call on the account (`pmcc.py`): a covered call written against a long call in
place of the shares. It is written into the evaluation as the account's `pmcc`, one entry per name
whose calls have a PMCC's shape, recognised or not, naming the trades holding its legs. The `pmcc.*`
rules in the table above read it. The view reads the real account, not the paper `pmcc` module (retired),
and works the same whether or not that module runs.

- **Shape.** One long call and one short call of the same size, the short expiring first at or above
  the long's strike, no shares. Neither leg's days left decides it (the standard definition: the short's expiry and delta may vary). A long running short of time is
  the roll flag's (`roll_long`, 120 days).
- **Recognition.** Shape alone does not make a PMCC. The long must have been entered at 0.70 delta or
  more (the method's own floor of 0.80 is not used), solved from its opening fill and that day's close (snapshot v13's `underlying_history`, from
  the technicals history store) with the dividend yield carried: the dividends gone ex in the year
  before, over the close. Without the yield a long-dated call on a high-yield fund reads as all
  intrinsic, a delta of 1. A delta the snapshot cannot solve (no close for the day yet, a split since,
  a fill no volatility prices) is **unknown**, and an unknown delta recognises nothing.
  An intent decides whatever the delta: `"pmcc"` recognises a PMCC, `"diagonal"` keeps one out. Set it
  on one PMCC by its long's contract, `trades.<LONG CONTRACT>.intent` (spaces ignored, e.g.
  `"XYZ 280121C00040000"`; the Live → pmcc card shows the key), or on every PMCC on a name,
  `positions.<SYMBOL>.intent`; the contract wins. A rolled long is a new PMCC: the old contract's
  intent does not carry over, so it is decided by its own entry or an intent on its own contract.
  Anything not recognised keeps its diagonal rules and nothing else.
- **Linking.** A short sold after the last one expired is an order chain of its own, so the view links
  the long and its shorts as the wheel view links a wheel: the long's chain, and every chain on the
  name made only of calls sold to open and bought back, expiring before the long, opened since the
  chain's first long was bought. A roll of the long in one order links the old long (the order touches
  both contracts); a roll in two orders (the old long sold, the new one bought separately) links only
  when `trades.<NEW LONG>.continues` names the old contract. A long call sold within five days of the
  long's purchase that nothing links is flagged as a possible two-order roll, never stitched on a
  guess: until it is confirmed, the cost leaves the old long out and the width check may pass a short
  whose assignment would lock in a loss. Recognition stays the newest long's own. They must replay to
  today's long and short. History that starts after the long was bought,
  an assignment or exercise, or a contract the history never opened makes the PMCC **unreconciled**:
  its money is shown as far as the rows go (the width check on the open legs' prices, labelled so),
  and it gets only a review saying why (`pmcc_data`, no confidence).
- **Money.** The broker's cash from the history rows, fees apart. The long's net cost is every linked
  order's cash, so each premium lowers it; the width check and the P&L if assigned (short strike -
  long strike - net cost per share, x 100 x contracts) are read from it, gross and net of fees. Every
  short sold against the long is listed with its cash and how it ended. The skate yield is a
  comparison figure: the shorts' net P&L to date (the open one at its mark, their fees subtracted) over the long's own cost,
  x 365 / days since it was bought; none under a week, flagged past 500%.
- **Today.** The long: delta at entry and today, extrinsic as a share of its price, days left, and the
  roll flag. The short: tested or not, against today's skate call band moved to today's price ("band
  not in the snapshot" without one), early-assignment exposure, its chance of finishing in the money
  (N(d2)), and an earnings report inside its life ("no report date" for a name without one, never
  "clear"; a date already out, which the broker lists until it has the next one, is `past`, never
  read as after the expiry).

**The PMCC rules and the diagonal rules.** A recognised PMCC is still a diagonal, and every diagonal
rule still judges it. The PMCC rules fire into the trade holding the short and rank by action like
every other rule: a stop, the breached-strike close or the expiry close outranks their roll-or-close
(and counts it as a conflict); `roll.diagonal_short`, when its own conditions hold (a credit, the long's
breakeven cleared, the long outliving the new short), is offered in their place as it is for any close,
and lists them under `replaces`; `ex_dividend` and `debit.diagonal_width` stay reviews beside them. They
do not wait for an earnings trade's call: a width failure and an assignment are what the waiting is
meant to protect against. A roll candidate costing a debit (allowed to $0.10) comes back `reject` from
`check-roll`, which never rolls for a debit; the candidate says so and a person decides.

## The account as one book

Beside the trades, each evaluation reads the whole account as one book (`portfolio.py`; the plan is in
[docs/history/portfolio-guidance-plan.md (tag archive/docs-2026-11)](https://github.com/joncovington/cherrypick/blob/archive/docs-2026-11/docs/history/portfolio-guidance-plan.md)). It records:

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
  which happens on a few names, so their BWD may be misstated;
- **the broker check** (`broker_check`, from snapshot v9): BWD and theta summed again on the
  broker's own per-leg greeks, over the same legs with the same betas, with the gap overall and by
  underlying. A leg only one model prices is named and left out of both. Legs expiring today are
  their own line (`expiring`), out of the totals: ours takes them at moneyness and the broker's
  prices a whole day at the quote's IV, which before the open can be most of the theta
  gap. `legs_on_broker_iv` counts the legs whose IV is the broker's (below). It decides nothing; it
  is the daily record of how far our model sits from the broker's before the bands are trusted.

Everything is modelled (Black-Scholes at each leg's own IV). That IV is solved from the leg's mid,
except where the snapshot flags its quote `wide` and the broker has an IV for it: then it is the
broker's, and the leg's `iv_source` says so. Two wide mids in one spread gave IVs about twice as far
apart as the broker's surface did (median 0.030 against 0.014 over the wide pairs of two sample days;
before the open a far-out 70C on one name, bid 0.01 ask 1.50, solved 55 points under its partner), while on tight
quotes our own agreed better (0.005 against 0.010). The broker's IV is taken only when, priced at the
snapshot's spot, it lands inside the leg's own bid/ask: the broker figures its greeks on its own
underlying price, which after the close on a report night is the after-hours print (a 705C on a
report night at 16:35 ET took an IV worth $10.40 against a 21.80/24.40 quote, and its bull call spread
read a negative delta). Every leg is solved and priced at the name's dividend yield (`div_yield`, the
year's dividends gone ex over today's price from the snapshot's `underlying_history`; `div_source`
`none` when the name has no record, priced at 0), its greeks with it, and a call's modelled value is
floored at its intrinsic. The model carries the dividend yield; without it a long-dated call on a
high-yield fund has a mid under the no-dividend floor (spot less the discounted strike) that no vol
prices. A mid under the model's floor still solves to
no vol at all; it is `unpriceable`, not IV zero and delta 1. A leg the model cannot price, or an
underlying with no beta, is named and left out, never counted as zero; the reading says it is
partial. A leg expiring today is taken at its moneyness, with its remaining time value as that day's
theta.

The bands are retail heuristics. None has published out-of-sample evidence, so each runs at
confidence 40, and the vol-expansion cap, which is ours alone, at 30. Each fires REVIEW and names
what drives it:

| Rule | Default | Source | Outside evidence |
|---|---|---|---|
| `portfolio.theta` | theta 0.1–0.3% of net liq a day; above the ceiling is oversizing | retail options education | None. Theta is paid for by gamma, so more theta is not more edge |
| `portfolio.delta` | BWD from −0.5× to +0.75× theta (SPY shares), leaning long; ±0.5% of net liq per 1% move when theta is not positive | retail options education ("BWD within about half the theta") | None |
| `portfolio.breakeven` | short gamma, with a break-even move under SPY's implied one-day move | option theory: theta = −½ Γ S² σ² | The identity is exact; whether acting on it pays is untested |
| `portfolio.concentration` | one name over 25% of gross theta or gross BWD | convention | None |
| `portfolio.buying_power` | buying power used over a cap set by VIX: 25% under 15, 30% under 20, 35% under 30, 40% under 40, 50% above | retail options education (more room when premium is rich) | None. A book using 53% against a 30% cap at VIX 20 is the case it flags |
| `portfolio.vol_expansion` | SPY −5% with IVs up 10 points (term-weighted, time held still) losing over 10% of net liq; the drivers are the names shortest 30-day vega | ours: a spike moves price and vol together, and the stress line holds IV still | None. Vol is easy to forecast and hard to profit from, but the expansion half cannot be timed, so this bounds it rather than predicting it |

Each band is written into the reading with the figure's place in it (`bands`), so the console draws
the band manage used and never works one out itself.

**When `portfolio.delta` fires, a hedge is sized** (`hedge.py`) in XSP: European, cash-settled, a
tenth of SPX, and allowed in an IRA as a defined-risk spread. The snapshot carries one quoted XSP
expiry: the monthly 30–60 days out, preferring 45–60. Two verticals and a collar are offered, each
vertical `width` (5) points wide, sized toward a point a quarter of the band's width inside the
breached edge:

- a **credit spread** on the side the book leans away from, short near 30 delta. It cuts delta and
  adds theta, and the short gamma that comes with it;
- a **debit spread** on the side it leans toward, long near 40 delta. It cuts delta and usually costs
  a little theta;
- a **collar** (`portfolio.hedge.collar`, **no tested edge, research item 55**): the debit spread
  and a credit spread on the other side together, so a book too long buys the put spread and sells a
  call spread, and a book too short mirrors it. The credit short is the out-of-the-money strike that
  brings the net at mid nearest zero, and the collar is offered only if the nearest is within
  `max_net` (0.25 a share); otherwise the card says why. Both halves are verticals, so no call is
  ever naked and a cash account or an IRA can hold it. It gives up the upside past the short call
  strike (the downside gain past the short put, for a book too short), and with put skew a zero-cost
  collar's call sits nearer the money than its put. Its worst case at expiry (the credit half's max
  loss and the debit paid, together) counts against the buying-power cap like a credit spread's. It
  is offered because it was asked for, not because it works: item 55 is declared to test it.

Each shows its legs, the mid and the natural price (sell at the bid, buy at the ask), the cash, the
max loss, and the book after it: BWD (and whether that is inside the band the book would then have),
theta, the break-even move and buying power used. A credit spread or a collar never takes buying
power used past 50% of net liq, and one that cannot fit says so. A debit spread is exempt: its cost is its whole
worst case and it only lowers the book's, so it is offered past the cap and marked `past_cap`. The
mid is not a fill. Nothing is placed.

**Scenarios and the expiry ladder.** The reading also carries:

- a **risk matrix**: the book's P&L across SPY moves (−10% to +10%) and IV shifts (−5, 0, +5 points),
  each name moved by its beta, time held still. The shift is stated for a 30-day option and scaled
  to each leg by √(30 / DTE), so front months move most. A flat shift once showed the book *gaining*
  $425 on +5 points, carried by long-dated longs, while the front months a vol spike hits were short.
  It is stated at each of `scenarios.horizons` (now, +7 and +21 days, nothing done
  meanwhile), since with time held still it shows what a short-premium book risks and never the decay
  it is paid. Each horizon adds a **history row** (`spotvol.py`): IV moved by today's VIX times the
  median end/start VIX ratio over past windows of that length (Cboe daily closes since 2006, carried in
  snapshot v18) whose SPX move was near the column's. Over a week a 10% fall has lifted VIX about 1.4×
  and a 10% rise cut it to about 0.85×: the answer bends and is lopsided, so it is read as medians, not
  a slope;
- **ideas** under the matrix (`ideas.py`, `portfolio.ideas`): a header, a subheader with the figures and
  a plain-English paragraph on how a change would help, with the matrix's row now before and after
  (the history row where there is one) and theta. Five kinds: broad indexes held together over a
  budget (closing the biggest), one trade's income from a short leg about to expire (closing it),
  trades over the per-trade cap (each resized to the cap), the cheap call side sold beside rich puts
  (the calls dropped), and expiries over a planning target (no model: the change is where the next
  trades go). An effect can make a cell worse, since dropping a side moves risk rather than removing
  it, and the figures say so. Thresholds are ours, with no published evidence; ideas size, place and
  close nothing;
- a **risk graph**: P&L across SPY −15% to +15%, now and at the nearest expiry (legs expiring by then
  at intrinsic);
- an **expiry ladder**: per expiry, the trades whose nearest short expires then, what they can still
  lose and the theta of the legs expiring then; and the share of theta and downside in trades with
  earnings before their nearest short expires (72% of theta, say), with that downside by
  report date.

Every figure is modelled repricing, not a forecast.

Two rules read the ladder: `portfolio.earnings_exposure` is a REVIEW when the trades
spanning a report can still lose more than `max_pct_nlv` of net liq between them (10%), naming each
report date; `portfolio.expiry_concentration` is one when a single expiry's trades can (20%), and a
new income candidate on that expiry waits.

**Income trades, sized to the account** (`candidates.py`). The snapshot carries the previous
afternoon's income reading (`fetch_screener_greeks.py income`). It covers every name the liquidity
verdict calls tradable, on the cycle monthly, quoted in regular hours, with earnings dates, beta and
the name's stress move. From it manage builds the screener rules' structures:

- a short put near 0.16 delta (not the vendor's 0.40, which the short-put delta
  study found to be market exposure, not premium);
- a put credit spread, short near 0.20 delta and long near 0.10, held to expiry, with no floor on the
  credit . The credit-spread studies (edge-hypotheses items 14-16) found no short
  strike, direction signal or exit with an edge after costs; this was the least bad, and the first
  strike out of the money it replaced was the worst. Each carries its odds -- the chance of any loss
  and of the max loss from the legs' deltas, the break-even, and what getting in costs over the mid
  -- and says it has no tested edge. Call spreads are off (`call_spreads`): they sell against equity
  drift, and short delta comes from the hedge (`hedge.py`) when the book needs it;
- a covered call near 0.16 delta, on its own expiry -- the monthly 30-45 days out (the covered-call
  study's least-bad window), else the cycle monthly -- on shares the account holds and has not
  already written against.
  Each short call open on the name, at any expiry and inside any structure (a spread's or diagonal's
  short included, its long not taken as cover), ties up 100 shares; a name with fewer than 100 left
  gets none. A call being sold to open on a working order counts too (snapshot v10's
  `working_orders`; only the part not yet filled, since the filled part is already a position).

It drops any structure with an illiquid or unquoted leg, or an earnings report inside the trade. An
unknown earnings date is flagged, never called clear. A fund (the reading's `fund`, from the
technicals store's listings) reports no earnings, so its missing date is none, not
unknown; a reading without the flag leaves it unknown. Each structure left gets the largest quantity
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
its formula, and portfolio margin is not modelled. The list is by symbol: with no
tested edge, a ranking would read as one, and the theta-per-buying-power order before it favoured the
near-the-money spreads most likely to lose. A trade that does not fit is shown with the cap that
stopped it. A short put or spread the rule built too wide is offered narrowed: the same short, and the
widest long that keeps one unit under the per-trade cap (and over `spread_min_credit`, 0 by default),
and for a spread strictly narrower than its own: one stopped by how full the book is gets no wider version.
Priced at mid from a day-old reading: re-quote before sending.

**The Screeners lists against the account.** The lists themselves are account-blind, so manage judges
each list trade (`candidates.screen_fit`, written into the evaluation as `screen_fit`) and the console
looks the row up. A short put's buying power is worked out at every strike within the lists' delta
tolerance, cash-secured or Reg T, against the buying power available now; a covered call fits the
shares the account holds and has not already written against. An unjudged row stays shown. The same
reading marks a candidate `leveraged`, `inverse` or `volatility` when the name is on the declared fund
list the skate screen leaves out (`technicals/leveraged_funds.json`): a badge, not an exclusion, since
a daily-reset fund decays and gaps in ways its delta does not price. A candidate on a name that already
has an option leg on the same expiry stands alone and carries `held_same_expiry`, with its sizing
unchanged. Earnings inside a list trade are not judged here: the own-list tab hides them by default,
and an unknown date stays shown, marked.

**Waiting for room.** A trade stopped only by how full the book already is -- buying
power, the theta ceiling, or an expiry whose trades already hold more downside than
`portfolio.expiry_concentration` allows (`ROOM_CAPS`), not a cap on the trade itself -- is also listed as
`waiting`, sized as if the book had room (`if_room`). Each carries where taking it today would leave
the account: buying power used after it, graded against the VIX-scaled cap as `ok`, `over` (within
`bp_warn_over`, 10 points, of it) or `far_over`, and the book's theta after it against the ceiling.
It is the queue that opens as positions close, never a recommendation to run past a cap. A trade
that waits is listed once; its narrowed version waits only when it does not.

**Ex earnings.** When the account holds earnings trades (`earnings_trade`: put on a few
days before a report and closed after the call), the list is sized a second time as if their buying
power were already free, as `ex_earnings` beside the trades set aside and the dollars freed. The
broker states buying power only for the whole account, so each trade's is its defined-risk max loss
from open (a credit spread's width less its credit, a debit trade's debit; modelled at the nearest
expiry for a diagonal). One earnings trade with no bounded loss refuses the whole list, rather than
freeing part of it. Only buying power is freed: their theta and delta still count. The console's
income card switches between the two lists with a checkbox.

### Time spreads (`book.time_spreads`)

The time-spread scanner's calendars and diagonals, sized to the account; the page that shows them is in
[the screeners guide](../../docs/guide/screeners.md). The idea is a term-structure premium: a front week
priced well above the back month is a short leg that may be overpriced for what the stock then does.

`scripts/fetch_time_spreads.py` (the `manage-time-spreads` job at about 10:30 ET on trading days, or
`run.py time-spreads` on demand; read-only broker reads, so the streamer's budget is untouched) writes
`time-spreads/<day>.json` in two steps. The term read takes the broker's market metrics for the
liquidity verdict's tradable names: the front IV (the expiry 5 to 16 days out nearest the middle of that
range), the back IV (a standard monthly 30 to 75 days out), IV rank and the next earnings date. The
prefilter (`timespreads.RULE`) keeps a name with a front/back ratio of at least 1.15, an IV rank of at
least 30%, a $20B market cap (a fund has none and is kept) and no earnings before the back expiry; a
spread across a report is a different trade, screened by the earnings module. Every name's row is kept
with the reason it failed. The chains step quotes both expiries' strikes near the spot for the 20 best
ratios only. Manage stays credential-free and judges only what the snapshot carries.

The snapshot carries the day's reading (`time_spreads`, snapshot v17), and
`timespreads.py` builds from its quoted chains: same-strike calendars at the money and one strike either
side, and put and call diagonals (long 0.55-0.75 delta, short 0.15-0.35 beyond it, the debit at most 75%
of the gap), each gated as one order by the leg bars and a natural fill within 15% of the debit. Every
number on a row is modelled at each leg's own IV: profit at the short's expiry flat, up 5% and down 10%,
the breakeven and 25%-loss price-stop bands, the take profit, value stop and roll date.

Sizing reuses the candidates' caps (`candidates.size`: per-trade risk, the delta band), with three
differences. A debit spread's max loss is its debit, so the debit is its risk, its buying power and its
notional. The book's own buying-power band and theta caps are set aside for this block's ceiling
(`portfolio.time_spreads.max_bp_share`, 60%), since the theta caps are a short-premium book's gamma
guard and refuse every calendar on a small account; the theta a row adds is shown. Above
`delta_limit` (+100) beta-weighted delta, bullish structures are held back and counted. A name or a
sector already held is flagged on the row. `show` (30) cuts the list that fits; the ones too big are
all listed, each with the cap that stops it, so the screener tab can mark every scanned row. **No
structure here has a tested edge**: research item 54 (does the ratio, or the "loses on an unchanged
stock" verdict, predict profit after costs) failed, so rows stay ordered by the front/back IV ratio,
labelled an untested hypothesis, and the scanner is a convenience, never a source of trades. Above
all it is advice: nothing here touches an order path.

## The opening half hour

An evaluation of marks taken between 09:30 and `early.verdicts_from` (10:00) New York time carries
`early`: the opening quotes are wide and jumpy, so the verdicts are worked out but not
to be acted on. The console shows prices, P&L and needs-a-look and holds the verdicts back; the
orchestrator does not post the list (needs-a-look still posts). Before 09:30 is `pre_open`'s. Each
evaluated leg also carries the broker's previous close (`prev_close`), from which the console marks
the day's P&L live.

## The payoff

`payoff.py` draws each held name's P&L across its price: the legs' model value less
their cost, whole position, on today, 7 and 14 days on (each before the nearest expiry) and at the
nearest expiry, where the legs expiring then are worth intrinsic and later ones the model's. Each
option leg's vol is solved from its mid with the name's dividend yield (the last year's dividends
over today's price, from the snapshot's history-store read; none on record is said, and 0 used):
Bjerksund-Stensland 2002 (`core.american`, early exercise) for an equity or ETF option, Black-Scholes
for a cash-settled index root. So today's curve passes through today's P&L at today's price. A leg
no vol prices nulls the dated curves rather than drawing them on a guess; the expiry curve needs no
vol unless a later leg does. Breakevens are where the nearest-expiry curve crosses zero. A picture,
not a rule.

## Model a trade

`model.py` (`python -m cherrypick.manage model`) draws proposed legs on a held name's
payoff with what it holds, against the stored snapshot. A leg given a price is taken at it and its vol
solved from it; one with none is priced at the name's IV index, and says so. For the proposed legs
alone: the net credit or debit, the model's delta and theta per day, and the worst case at their
nearest expiry from zero to three times today's price (the buying power a defined-risk trade holds
in a cash account; a worst case still falling at three times is unbounded, stated, never a number).
Against the candidate caps (`portfolio.candidates`, by the account's kind): the worst case over net
liq, the name's worst case with it, and buying power in use with it. The name's implied vol and its
30-day realised are shown beside each other, never ranked on. Advice only: nothing is sent.

## Needs a look

`attention.py` measures every short option held against its strike: its distance out of
the money as a share of the strike (negative is in the money) and its time value. A short is listed
`early exercise` (a call in the money with less time value than a dividend going ex before expiry, or
any short in the money with time value under `attention.near_zero_extrinsic`; never a cash-settled
index), `in the money`, or `near` (within `attention.near_pct`, 3%), worst first; every leg with
`attention.expiring_days` (7) or fewer left is listed apart. 0DTE legs on `exclude.zero_dte_underlyings` are left out (a loop or the hand manages those).
A reading, not a rule: it fires nothing. The orchestrator posts each listed name once a session
(`managealert.py`), from the full runs and the minute re-marks; the console draws it on Live -> wheel.

## Your limits

`limits.py` sets each of the book's measures beside a limit from the ruleset's `limits`
block: beta-weighted delta (benchmark shares, +/-), dollar delta (+/-), theta per day (a ceiling, on the
theta band's figure without the front legs), vega (+/-), buying power used over net liq, and one
name's worst case from open over net liq. A blank (null) limit falls back to the guidance band where
there is one (delta, theta, buying power) or the candidate cap (one name, by the account's kind), and
each row says whose limit it is; dollar delta and vega have none. A row is `over` past its limit
(`under` below a floor above zero, as theta's band has), `near` past `near_share` (0.8) of the way,
`within`, or `unknown` with no figure. The day's P&L (each
leg's mark less its previous close) is held against `day_loss_alert` dollars, else
`day_loss_alert_pct` (2%) of net liq; a leg with no mark makes it unknown, never zero. Advice only: a
limit stops nothing. The orchestrator posts the day's loss once a session when it crosses
(`managealert.run_day_loss`, `manage.day_loss_notify`); the console draws the rows on Live ->
portfolio, and Config -> Position rules sets them.

## The ledger

`ledger.py` reads the same account-wide history as the pace, taken from the account's
opening (snapshot `account_transactions` since `ACCOUNT_HISTORY_FROM`), and answers what each name has
earned. **Puts** and **calls** are each option leg's flat-to-flat net after fees on its closing day
(`premium.closed_lots`); **shares** are each stock span's, so an assigned put's premium is the put's and
the stock's move is the shares', each once; **dividends** are on their day; **fees** are what the
closed spans paid. `net` is puts + calls + shares + dividends, per name and for the account, over 1M,
3M, 6M, 1Y, YTD and ALL by the closing day, and by calendar month. Every closed span is listed (`lots`,
with how it ended), and what is still held is `open`, its cash so far, never counted as kept. The
0DTE legs on `exclude.zero_dte_underlyings` and spans the history entered mid-position are counted aside with their legs
and net, as the pace counts them. The console's Live -> wheel tab draws it above the cycles.

The evaluation's `ledger` counts the wheel's names alone: the names with a cycle, which
the wheel view states as `names`, are passed as `scope`, and every total, month, lot and open leg is
over them alone; `scope` is in the reading. The scope is by name, so a name's other trades (a
diagonal on a name the wheel also runs) are in it. With no cycle the ledger is empty, never the
account's.

## The premium pace

`premium.py` reads three figures side by side for the broker tool's designated account (the
account-wide history is that account's), as the evaluation's `premium`:

- **Theta against its target:** `portfolio.theta.target_pct_nlv` of net liq a day (default 0.2%, the
  middle of the theta band, agreed with the operator), on the band's own figure (without
  the legs `front_dte` days or less from expiry). It is a pace to hold, not a quantity to maximise:
  theta is paid for by gamma, and the short-put studies found extra premium from higher delta was
  market drift, not edge.
- **Collected:** each order that opened something, at its whole net before fees, when that is a
  credit, in the month it filled. A roll is one order, so it counts at its net credit, never at the
  new short's price.
- **Kept:** each option leg's net cash after fees from flat to flat, in the month it went flat:
  bought back, expired, exercised, assigned (the premium; the shares delivered are outside it) or
  cash settled (the cash row joins the leg its quantity row closed).

Six calendar months (ET) and the current week. Two things are left out of both and stated with their
count and net: 0DTE legs on `exclude.zero_dte_underlyings` (positions a trading loop or the hand manages the same day) and legs the
history entered by a close (opened before the 400 days it reaches). Only option legs count. The
history is the snapshot's `account_transactions` (v15); a snapshot without it, or a failed read,
gives the target alone and the reason.

Collected and kept are timed differently, so one month's pair is not a ratio; the months together
are the comparison. A book that keeps far less than it collects is paying for its premium in losses.

## Known limits

- **Marks are mids** and the payoff model is Black-Scholes-Merton at today's IV per leg and the
  name's dividend yield, a call floored at intrinsic. It is European with a continuous yield: it
  ignores early exercise beyond that floor and when a dividend falls, so it can differ by cents from
  the payoff chart's American model. It bounds what a trade can still do; it is not a price.
- **A run before the open reads yesterday's close.** Spot and marks are the last
  session's, and an option expiring today has no quote, so its leg is unpriced and a name's delta
  reads off the stale spot. The evaluation says so (`pre_open`: the New York time it was taken, and
  the trades with an unpriced leg expiring today); a run after 09:30 reprices.
- **A snapshot older than the code leaves later views empty.** The wheel, PMCC and time-spread views
  need fields only a newer snapshot carries. Manage snapshots every 30 minutes from 10:00 to 16:35 ET,
  so only a merge after the day's last run leaves a stale one, and only until 10:00. The watchdog's
  `manage.snapshot_version` WARNs while the newest snapshot is older than the installed
  `SNAPSHOT_VERSION`, and the console's manage tabs show that finding as it states it.
- **Fees are known only for a reconciled chain.** A trade priced from its open legs shows gross P&L only, and its basis misses any earlier roll.
- **Ex-dividend dates are the last declared ones.** The rule fires once the next ex-date is announced.
- **Earnings dates are the broker's expected dates**, which can move.
- **0DTE on the listed underlyings is left out** (`exclude.zero_dte_underlyings`, SPX by default): a
  same-day, cash-settled position is managed by whoever placed it (a trading loop, or the hand) and
  settles at the close. The exclusion is about the position's lifetime, not about any one module.
- **A covered call the PMCC link does not reach counts only its two chains.** Earlier shorts sold in orders of
  their own and expired are not in its basis, and an ambiguous pairing (two lone longs or shorts on
  one name) is left split.
- **A PMCC between shorts is not shown.** With no short open the long is a long call and the view
  drops it until the next short is sold.
- **Thesis is not knowable.** `credit.breached` is a mechanical stand-in for "the reason for the trade
  is gone". Use `positions.<SYMBOL>.intent` and `disable` to mark a hedge or a position you mean to be
  assigned on.
