# Flies live trading — the plan, the gates, and where things stand

**What this document is:** the rulebook for moving the flies butterfly strategy (see the
[module README](../README.md) for what the strategy actually is) from paper trading into real
money. It defines the statistical bar the paper results need to clear before scaling up, the
safety controls that apply regardless, and exactly what has been built so far. It's the
reference to check before changing anything about how live trading runs, and the record of why
each decision was made the way it was.

**Where things stand today (2026-07-30):** live trading is running as a **small, deliberately
under-sized pilot** — one strategy variant, one contract at a time, one open position at a
time — authorized specifically to shake out real trading-mechanics bugs (order fills, timing,
cancellations) before any larger commitment is considered. It is **not** a claim that the
statistical bar below has been cleared. The paper results from 2026-07-20 through 07-24 were,
on their own, a losing result (40 legged entries, 23 completions — 57.5%, against a roughly 65%
break-even rate; the day's book lost $1,175 net). Since a set of configuration changes made
2026-07-27, the `gex` variant specifically has done much better — 8 completions out of 9,
+$603 net — but that's only 3 sessions and 9 trades, nowhere near enough to trust statistically.
The user made an explicit, informed decision to authorize a small live pilot anyway, precisely
*because* it's small enough that being wrong about the strategy doesn't matter much, while still
being real enough to find bugs that only show up with real orders and a real broker. That
decision is recorded in the module's configuration as a logged, named exception — not a claim
that the bar was met.

Every guardrail that applies to the rest of the suite applies here too: paper and live money are
kept completely separate in every report, nothing on the trading-decision path uses AI or any
network call, credentials live only in the operating system's secure credential store, account
numbers are always shown masked (e.g. `****1234`), and only strategies with strictly defined,
bounded risk are traded live.

---

## The bar the strategy needs to clear (Gate 0) — nothing below is meant to start until this passes

This is the "is this actually a working strategy" question, answered from the paper-trading
data using the module's own analytics, over a window that starts *after* the 2026-07-27 setting
changes (data from before that point measured a meaningfully different strategy, so it doesn't
count).

1. **Completion rate at or above the real break-even rate, sustained over a meaningful sample.**
   The break-even rate isn't a fixed number — it's recalculated from that window's own ratio of
   what a miss costs versus what a completion earns (the "roughly 65%" above was specific to the
   first five sessions, not a permanent target). The minimum sample considered meaningful: at
   least 20 trading sessions and at least 100 legged entries.
2. **The day's book is net profitable after all fees, over that window** — and whichever
   variant is winning has to clear the same, already-hardened bar the suite uses before trusting
   any strategy result: at least 14 days, at least 20 sample trades, a statistical-significance
   check, and the result has to survive a stress-test that assumes worse-than-modeled slippage.
3. **The winning variant is genuinely separable from the plain baseline** — meaning it actually
   made different trading choices, not just the same choices measured twice. (Two of the four
   variants pick the same strike as the baseline by construction, so agreement between those
   two proves nothing either way; the comparison that can actually show a difference is the
   `gex` variant against the baseline.)
4. **The wide-wing question is answered one way or the other** — either using wider strike
   spacing captures the price movement that happens before a completion arrives (so completions
   land inside the position's range and the book earns more than just its floor), or it
   doesn't, in which case that price movement is a fundamental property of the strategy rather
   than something a setting can fix — a genuinely negative finding, and an equally valid outcome
   of this whole exercise.

This bar is judged by a person reading the reports — there's no automatic switch that flips.

## The two open technical questions, and how they're resolved for the pilot

Moving from paper to live raises two questions the paper simulation can't answer by itself.

**How much does reality differ from the simulation when a two-step trade doesn't complete
instantly?** In paper trading, "did the completing leg get cheap enough to buy" is a clean
yes/no check against the recorded price. In live trading, the first leg (the credit spread)
fills first, and the second leg (the completing purchase) is a resting order that might sit
unfilled for a while or fill at a worse price than modeled. That means the paper-trading
completion rate is best understood as a **ceiling** on what live trading can achieve, not a
prediction of it — so the first job of the pilot is simply to measure the real, live completion
rate directly, at the smallest possible size, before anything scales up. Mechanically: the
first leg is a live 2-leg credit-spread order; the second leg rests as a working limit order at
a price no worse than (credit collected − a fee buffer), subject to the same profit-floor check
paper trading uses, and it's automatically cancelled at a fixed cutoff time if it hasn't filled.
If it never fills, you're simply left holding the original credit spread to expiration — the
same "uncompleted branch" outcome the paper simulation already tracks and reports separately.
**A built-in abort rule protects against a bad surprise here:** once at least 30 live legged
entries have happened, if the live completion rate falls more than 15 percentage points below
the paper completion rate over the same days, the pilot halts automatically. The strategy's
entire edge *is* the completion rate — a persistent gap that size would mean the paper ceiling
simply isn't achievable in the real market.

**The "outright" entry mode needs a real check against actual buying power before it could ever
go live.** This is moot for the pilot: outright entries were already turned off in paper trading
as of 2026-07-27 (they lost money in all four instances tried, and were quietly skewing the
comparison between strategy variants), and the live pilot **does not trade this mode at all**.
If it's ever reconsidered, it goes back into paper trading first, and any live version would
need to pass the same real balance checks every other live order does.

## How live trading is built

Live trading reuses the exact same decision-making logic as paper trading — the code that
decides "is this a good trade" doesn't know or care whether the order that follows is simulated
or real. Live trading only adds the parts that talk to the actual brokerage account:

- A separate live-trading process, running side-by-side with (not instead of) the paper-trading
  process — paper keeps running throughout the pilot as the point of comparison.
- A minimal, purpose-built connection to the broker for placing orders, checking their status,
  and cancelling them. All pricing and trade decisions still come from the same shared,
  read-only market-data feed the rest of the suite uses — the broker connection is used *only*
  to act (place or cancel an order) or to confirm something only the broker can know (whether an
  order actually filled). This "streamer before API calls" rule is a suite-wide convention, not
  specific to live trading — see the [module's technical guide](../CLAUDE.md) for how it's
  applied elsewhere.
- **Only the winning strategy variant trades live.** The four-variant design exists to compare
  strategies on paper; live trading isn't an experiment, so its settings are locked to whichever
  variant is authorized (currently `gex`), and those settings don't change mid-pilot — changing
  them partway through would make the measurement meaningless.
- **A completely separate ledger for live trades**, kept out of every comparison, calibration,
  or "what's working" report the suite generates from paper data — live results only ever show
  up in their own dedicated view.
- **Several independent kill switches**, any one of which stops new trades: a master on/off
  setting (off by default, checked every minute); a suite-wide "halt" flag that any part of the
  system (including the automated monitor) can set, which blocks new entries within about a
  minute without touching anything already open; a daily loss limit (if the day's live trading
  is down past a set dollar amount, no new positions open — anything already open still runs its
  course to settlement, since these are always fully defined-risk trades); and a real,
  broker-checked buying-power/exposure limit on every single order.
- **Settlement discipline**: live-trading results are recorded against the official market
  closing print, not the same "last streamed trade" approximation paper trading uses by default.
  A daily automated reconciliation check compares what the system believes is open against what
  the brokerage account actually shows.
- **An automated monitor** watches that the live-trading process is actually running and
  healthy during market hours — it can raise an alert or set the halt flag, but it never places
  or cancels an order itself.

**Why XSP instead of SPX for the pilot.** Paper trading runs on SPX, where fees ate 82% of gross
profit in the very first session. XSP is the same underlying index at 1/10th the size, so the
dollar risk per trade is smaller — but because the trading fees are roughly fixed per contract
regardless of size, fees actually take a *larger* percentage bite at the smaller XSP scale. The
choice between the two comes down to which one the fee-adjusted math actually favors under the
winning variant's settings, not a preference — and for this pilot, XSP is the one that still
clears its costs. Both are settled in cash with no possibility of early assignment, so neither
carries assignment risk. The two are never traded live at the same time.

## The rollout ladder — each step only starts once the one before it has been checked by a person

- **Step 0 — broker smoke test.** Build a real order pair from live market data, run it through
  the broker's practice/dry-run check against the real account, confirm the buying-power effect
  and safety-limit result look right — and place nothing. Done under direct supervision.
- **Step 1 — the measurement pilot (where things are now).** At most one position open at a
  time, one strategy variant, at least 15 sessions. What gets collected: the real live
  completion rate, how much worse fills are than the modeled price, actual fees versus the
  modeled estimate, and how long completions actually take compared to paper. The abort rule
  above is active from the very first trade.
- **Step 2 — normal size at one contract.** Only reached if step 1's live completion rate clears
  the recalculated break-even bar. Settings stay locked, and the suite's standard "is this
  strategy working" evaluation runs on the live results directly, using the same hardened bar
  every other strategy has to clear.
- **Scaling beyond one contract** is a separate decision, made by a person, after step 2 passes —
  and isn't addressed by this plan at all.

## How live and paper results are kept honest and separate

- Live results are visible only through the live-specific report view. Every "what's working"
  or calibration report the suite generates is paper-only, by construction and checked by tests.
- Every live fill records the actual price it filled at versus what was quoted at the time — so
  over the pilot, the suite's estimated slippage cost (previously just a modeled guess) gets
  replaced with real, measured numbers.
- The paper-trading process keeps running the same days live does, specifically so live results
  can be compared against a paper "control" from the same sessions — differences in completion
  rate, fill speed, and pricing between the two are reported as first-class numbers, not an
  afterthought.

## What this plan explicitly does not cover

No adjusting a position after it's opened (the strategy is hold-to-settlement by design, in both
paper and live). No "outright" entries live. No more than one strategy variant live at once. No
trading SPX and XSP live at the same time. No automated or AI-driven decision anywhere near a
live order — anything that reads trading history to suggest changes is only ever allowed to look
at paper data, and none of the suite's more advanced automation features apply to flies until
this plan's steps are complete.

## Build status

**Built and working (as of 2026-07-28, off by default until deliberately armed):** the secure
credential connection to the broker, the broker command-line tool (account connection, order
practice-checks, all gated behind the master on/off setting plus a recorded human sign-off), the
order-building logic (which can never price an order past what the strategy's own profit-floor
check allows), the live trading loop itself (checks every safety gate every single tick: enabled,
signed off, one variant configured, the right account connected, no halt flag present; a daily
loss breaker; automatic cancellation of stale orders), a fully separate live ledger, and support
for the exact option contract identifiers the broker needs on every quote.

**Built and working (as of 2026-07-30, the full trading-cycle build):**

1. **Connecting the pieces together** — the live ledger and broker credentials are wired into
   the suite's central configuration and monitoring. Done.
2. **Watching for and recording fills** — every check-in polls any pending order; while an order
   is actually pending, a short-lived helper process checks in roughly every 10 seconds (itself
   gated by the shared market-data cache, so it isn't hammering the broker with pointless
   checks) until the order fills or the window runs out. Only a broker-confirmed fill — never a
   guess from cached prices — updates the ledger. Done.
3. **Managing working orders without constant repricing** — the entry order is re-checked each
   minute against current market data and only cancelled-and-replaced if the picked strike or
   the price has genuinely moved; the completing order is placed once, at a price that already
   builds in the profit-floor requirement, so there's nothing to chase — it just sits until it
   fills or hits its cutoff time. Done.
4. **Settling the live day's results** — an automatic provisional settlement happens at 16:20
   ET from the last streamed trade price, clearly labeled as provisional; the official closing
   print can be applied afterward to finalize the day's numbers, and that official number is
   protected from being accidentally overwritten. Done.
5. **Monitoring and alerts** — the automated monitor now watches the live loop specifically:
   whether its scheduled task is still registered, whether its log is still updating during
   market hours, whether settlement is overdue, and a backstop that raises the suite-wide halt
   flag if the loop somehow fails to turn itself off at the end of the day. Trade notifications
   (entries, completions, settlements) are pushed with a clear "LIVE" label plus a desktop popup,
   completely separate from the trading loop itself. Done.
6. **Comparing live to paper, day by day** — a dedicated comparison, restricted to the exact same
   trading days for both, tracks completion rate, fill speed, and pricing side by side, and
   evaluates the abort rule described above automatically. This shows up in the live status
   check, in the end-of-day live report, and in the suite-wide daily digest on any day live
   trades settled. A daily-structure cap is also available (off by default) to throttle how many
   new positions can open per day, independent of the one-open-position-at-a-time rule. Done.

**How arming works day to day:** live trading is armed **one day at a time**. Running the
`/live-flies-start` command with a fresh confirmation registers the day's trading task; it turns
itself off automatically at a set time each evening (17:00 ET by default), and the automated
monitor backstops that shutoff with the suite halt flag if it somehow doesn't happen on its own.
The rule that governs every piece of this: market data comes from the shared cache first, and
the broker is only ever contacted to actually place or cancel an order, or to confirm a fill that
only the broker can know about.
