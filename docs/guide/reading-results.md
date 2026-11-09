# Reading results honestly

The suite exists to find out whether a strategy makes money after costs, and to be hard to fool while
doing it. This page covers how to read the numbers it shows: what each money column means, why a paper
result flatters, when a result is too young to trust, and why one good week tells you very little.

## The money layout

Every table that shows a trade states money the same way, so modules can be compared and rows can be
checked. The same rules apply on the console, in the end-of-day review and in a Discord post.

- **"P&L" means net.** Net is gross less fees, less settlement, and less slippage where slippage is
  charged as a cost. A figure before costs is labelled **gross**. If you see a bare "P&L", it is net.
- **Signed cash flow.** A credit received is positive, a debit paid is negative, on every entry and exit.
- **Whole-position dollars.** Prices are multiplied out by the contract multiplier and the quantity, so a
  figure is what actually moved in your account. The one per-share figure is the net price of a trade, and
  it says so, as in `1.25 cr` (a credit) or `0.40 db` (a debit).
- **Rows add up.** Entry plus exit is gross. Gross less each cost column is net. If you ever find a row
  where the columns do not reconcile, that is a bug to report, not a rounding habit.
- **Costs are separate columns.**
  - **Fees** are the broker's commissions and exchange charges.
  - **Settlement** is the charge for exercise, assignment or delivery, which is a different thing from a
    trading fee. Where the split was never recorded, settlement reads `n/r` (not recorded) and the total
    stays in fees. It is never shown as a zero.
  - **Slippage** is the gap between the mid price and the fill. A module's table says which model it uses:
    some concede it inside the fill, so it is already in gross and is shown as a measure, not taken off
    again (MEIC works this way). Others fill at the mid and charge slippage as a cost column, taken off
    once. A figure taken off in one module and not another is never shown under one unqualified name.
- **Open and closed are separate tables.** Open trades show the entry side, and a mark where there is one.
  Closed trades show how they ended (closed, settled, expired or assigned), gross, costs and net.
- **Fee drag** is fees divided by the premium collected. It is the quickest way to see whether a strategy
  can survive its costs: a trade that keeps 20 cents of a 25 cent credit after fees is not much of a trade.

## Paper is an upper bound on live

Most of what you see is paper: live market data in, simulated fills out, no order reaches your broker.
A paper result is the best case. The same trades done live would have done the same or worse, for these
reasons.

- **Fills are at the mid or at the model.** Paper assumes a price. A live limit order might fill at a
  worse price or not at all.
- **No missed entries.** In paper, an entry always happens. Live, an order can be rejected, only
  part-filled, or sit unfilled while the market moves away.
- **Stops are priced by a rule.** In paper, a stop is priced by the model. Live, a fast market can fill it
  worse.
- **Early assignment is measured, not modelled.** Where a module says so, an in-the-money short can be
  assigned early in real life. Paper records that it happened, but does not charge you for it.
- **Hindsight.** Anything designed after looking at the data will look better on that data than it will
  going forward.

So treat a positive paper result as the most it could be, and a negative one as likely worse live. This is
the reason the suite shows fees and slippage in their own columns instead of hiding them. It is also why
a live loop should earn its way up from the smallest size; see [going-live.md](going-live.md).

## Provisional, and the minimum number of sessions

"Provisional" is used in three places. They are different, and the console labels which is meant.

- **A result with too little behind it.** A result built on fewer sessions than the module's minimum is
  **provisional**: it is shown, and it is a hint, not a finding. The suite's shared floor is **14
  sessions**. Below it, no confidence interval is quoted, no threshold is re-cut from the data and no arm
  can qualify for a comparison against its control. Some measures need more: the tail-risk figure (CVaR)
  is withheld under 20 sessions, a stress estimate under 20, and a row in a breakdown by weekday or entry
  hour is marked `thin` under 5. A thin row is dimmed and shows no colour, because a colour would read as
  a finding it cannot carry.
- **A trade that has not settled.** An open trade's net carries a `provisional` badge: it is a mark, not
  a settlement, and it can still move.
- **A day's review.** The end-of-day review is provisional at 16:30 ET, when the same-day modules are
  complete but earnings is still carrying overnight, and final the next morning.

Sessions, not trades, are what count. Twenty condors opened on one day saw one market between them; they
are close to one observation, not twenty. A summary card therefore shows how many trades there are, an
interval around the mean, and an early-estimate note under 30 trades. A win rate never stands alone: it
sits beside net per trade, and its colour follows the sign of net per trade. An 85% win rate that loses
money reads red.

## Measurement boundaries: never pool across one

A **measurement boundary** is a date on which something changed what a session's numbers mean: the pace
of entries, a gate's meaning, how a net is worked out, which arms exist. Rows before and after were
produced under different rules, so adding them together answers a question nobody asked.

- Each module declares its boundaries. MEIC's are listed under "Declared boundaries" in
  [its reference](../../packages/meic/docs/reference.md), with the date and what changed.
- The console and the analysis commands read the **current era** by default, from the latest boundary on.
  Compare's scope selector says "each module's era" by default, and "all rows (pooled across breaks)" is
  a stated widening, never the default.
- A change that moves what the numbers mean is batched to a boundary and announced there, so the clock
  restarts once rather than several times. A fix to a number that was recorded wrong lands at once,
  because it does not change what the module did.
- Turning on a new setting yourself is a boundary too. If you change an entry gate, a stop or a window,
  judge the change only from the day you made it.

When you compare two arms, make sure both were measured over the same era. Compare uses only the sessions
both arms closed, for exactly this reason.

## Per session or per trade

A module that opens several trades a day can be summarised two ways, and they do not agree.

- **Per day** (per session) adds up everything that happened in one session. It is the independent
  observation, and it is what drawdown, tail risk and the paired comparison use.
- **Per trade** divides the net by the number of entries. It is a useful size-of-edge figure, and it
  treats trades on one day as if they were independent when they are not.

The console words these two differently on purpose: a figure says "per day" or "per trade" in its label.
Two figures that look alike, such as a max drawdown measured per day and one measured per trade, can differ
a great deal, and the page labels which is which. When a number seems too good, check which one you are
reading before you compare it with another. Likewise, a ratio over capital or peak risk names its
denominator, such as "net / largest peak" or "per $100 of risk", so it is never mistaken for a return on
the account.

## What a research verdict means

Backtests and research studies are the suite's way to test an idea against history before paper trading
it. They are run under rules meant to stop you from fooling yourself.

- **The test is declared first.** Before the data is looked at, the study states what it will measure and
  what result counts as a pass. The pass or fail is recorded afterwards against that test. The test is not
  rewritten to fit what came out.
- **The verdict is after costs.** A pass means net of fees, settlement and slippage. An idea that wins
  before costs and loses after them is a fail.
- **History is split.** The design window is where an idea is tried and tuned. A holdout is kept unseen
  until the design is fixed, and is spent once. A result that works on the design window and not on the
  holdout is a fail.
- **A verdict has three values:** pass, fail or open (not yet measurable). "Underpowered" is not "failed":
  too few sessions simply means it has not been measured.
- **A verdict is a statement about that rule on that data.** A pass says the declared rule had an edge
  after costs in the past. It does not predict the future. It is a reason to run the idea on paper, and
  paper is a reason to look further, not to size up.

Research that uses licensed vendor history (the ThetaData-based backtests) has one more rule. That data,
and anything derived from it, stays on your machine under `~/.cherrypick` and is never posted, shared or
published, including in a public issue or a pull request. See [privacy.md](privacy.md).

## Why one good week means little

A week is five sessions. Even a strategy with no edge at all will have good weeks, and a strategy with a
real edge will have bad ones. Short samples make it almost impossible to tell the two apart.

- **Five sessions is under the 14-session floor.** A week is provisional by the suite's own rule.
- **Premium selling hides its risk.** A strategy that wins small and often can make money for months and
  give a large part of it back on one bad day. A good run is exactly what it looks like before that day.
- **One market, not many.** Trades on the same day share a tape. A calm week is one calm market.
- **Regime.** A week in a quiet, rising market says little about a volatile one.
- **Selection.** If you look at many arms, one of them will be at the top of the table by luck.

So judge an arm over dozens of sessions, against its own control, from the paired difference rather than
its own net. Leave an underpowered result running until it has enough sessions. And don't change a
setting, size up or switch an arm off because of a few trades. Settings are in Config; see
[system-and-config.md](system-and-config.md), and for comparing arms see [research.md](research.md).
