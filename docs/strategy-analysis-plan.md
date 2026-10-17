# Strategy analysis — a per-arm overview for every module

*Drafted 2026-10-16. Nothing below is built yet. The plan follows a review of eleven videos from a
vendor's weekly series on running a portfolio of automated options strategies (weeks 1–10 and a
scaling episode, August to October 2026). It records what those screens did, what we take, what we leave, and the order we
build in.*

## Why

The console shows each module's P&L, and `core.metrics` already computes the usual figures (win rate,
profit factor, Sharpe, Sortino, max drawdown, expectancy). What no surface answers today, for any
arm, is the set of questions a person actually asks before trusting a strategy with money:

- Does the result survive without its few biggest winners?
- How much of it would worse fills take away?
- In which market conditions does it make and lose money, and how many trades say so?
- How long are its drawdowns, and how long does recovery take?
- When it did not trade, what stopped it?
- Is live doing what paper and the replay said it would, and where it isn't, why?

The vendor's platform answers most of these on one long page per backtest, and adds a live-side
"Analyze" view and a portfolio diversification report. Almost all of it is a pure function of a
trade ledger, a log of rejected entries and daily bars, so it fits the suite's deterministic
preference. Despite its "AI Analysis" label, very little of it needs a model.

## What the vendor showed

Three families of screens, which grew over the ten weeks:

1. **Per-strategy backtest analysis.** The headline stats, with **max risk** (most capital at risk at
   once) beside **max drawdown** (peak to trough from any start). Drawdown episodes, each with its
   peak, low and new high, and the days falling split from the days recovering. Insight flags (fees,
   assignment exposure, drawdown durations, wide bid/ask spreads). The analyzer sections:
   - entry selectivity: dates eligible against entered, and *why entries were blocked*, counted by
     the first filter that stopped each date;
   - a drawdown stress test that reshuffles the daily P&L and reads the 95th percentile;
   - profit durability: P&L without the top 1, 5 and 10% of winners;
   - execution sensitivity: a slippage ladder;
   - directional risk: P&L by market direction before entry and by the underlying's move during the
     trade;
   - market adaptability: P&L by trend and by ADX;
   - an "overfitting and reliability" panel of six fragility factors.

   A family of **bucket tables shares one column set** (P&L, trades, win rate, average return on
   risk, average P&L, average risk, best, worst), each drilling down to its trades: day of week,
   VIX at entry, market event, reward/risk at entry, any entry-time value, indicators, moving
   averages. A filter-search optimizer proposes filter sets, with a per-year walk-forward table and
   the share of trades each keeps.
2. **Live operation.** A bots list (P&L, % of allocation, open risk, streak); a bot dashboard with a
   capital panel (allocation, net liquidity, at risk, available); positions grouped by day and tagged
   by strategy, each with its **best and worst excursion beside each exit rule**; an Analyze page
   with filters, a **daily P&L calendar with weekly totals**, bars by weekday, hour, strategy and
   symbol, a held-to-expiry counterfactual and export; and a decision log showing the value every
   entry condition was checked against.
3. **Portfolio.** The combined curve, per-arm insight tabs, monthly P&L, a variant catalogue sorted
   by return over max drawdown with a compare overlay, re-runs over saved date windows, a 2x scaling
   preview, and a diversification report: correlation of daily P&L with no-trade days counted as
   zero, a separate "similarity" measured only on days both traded, an effective number of
   strategies, the drawdown benefit of combining, the worst portfolio days, and each strategy's
   contribution.

Their judging rules stayed constant: judge the portfolio over at least three months; never cut an
arm on a bad stretch; hold capital of about 2.5–5 times max drawdown; scale only once live tracks
the backtest. Their backtest-against-live reconciliation was a hand-built spreadsheet. It found a
filter-precision mismatch that had cost $1,428, and their platform has no view for it.

The video frames are not kept in the repo.

## What we take, and how it maps

Their *strategy* is our module; their *variant* is our **arm**. The arm comparison is the centre of
our version, where for them it is a side feature. Everything is a read surface: nothing here changes
what a module does, so none of it waits for a measurement boundary.

### Phase 0 — data foundation

Plain code changes that land immediately.

- **Widen the ledger record.** `core.ledgers` records carry the session date but no times, no spot
  and no exit reason. Each schema's reader adds entry and exit timestamps, entry and exit spot (from
  the module's marks for an early close, settlement spot otherwise), exit reason, and an entry-context
  bag of the values the module already records at entry (expected move, regime label, GEX, credit and
  risk). Absent fields stay absent, never zero.
- **Declare capital per arm.** Return on capital, risk per trade and drawdown multiples all need it,
  and `capital` is empty for flies and for older rows. Each module's config declares it per arm. Where
  it is missing, a stated fallback is used and every figure computed from it says so.
- **Best and worst excursion per position**, with the time each happened, from the marks tables.
- **One blocked-entry vocabulary.** Each module's reject reasons map to shared categories (calendar
  gate, regime gate, credit or price floor, quote quality, capacity, data missing), keeping the
  module's own reason as the detail. bwb joins the console's attempts reader. Calendars' attempts gain
  an arm column. Earnings maps its per-symbol `scan_log`. Contango's daily hold/switch decision is
  reported as what it is, not forced into the entry shape.

### Phase 1 — flies, in full

Flies goes first because it has paper, live, shadow tags, the quote-history replay and the fullest
attempt taxonomy, and because its open questions (stranded verticals, the range-high arm, the closer
on live, entry-fill adverse selection) are the ones this answers. It becomes the reference
implementation, as it already is for the money layout.

One deterministic, versioned **analysis fact set** in core, built from closed records, attempts and
bars, run the same way over paper, live or replay output:

- **Stats:** `core.metrics` plus win and loss streaks, best and worst trade, and max risk beside max
  drawdown.
- **Drawdown episodes:** peak, low, new high, days falling, days recovering, depth.
- **Profit durability:** net with the top 1, 5 and 10% of trades removed, extending
  `ledgers.concentration`. Removing rows is labelled as removal, never as a re-run.
- **Slippage ladder:** two paths, because flies and MEIC hold slippage inside gross and the
  cost-basis modules charge it separately (see the money layout in the root CLAUDE.md). Neither path
  subtracts slippage twice.
- **Drawdown stress test:** the 95th-percentile drawdown over reshuffled and block-reshuffled daily
  P&L, on `core.stats`' stationary bootstrap.
- **Entry selectivity:** sessions eligible against entered, and the blocked-entry categories.

The console gets a flies **Analysis tab**: equity with an optional underlying overlay and **markers at
every measurement boundary** (the vendor's live curve turned at a 2x sizing change, and nothing on
the chart said when), then the sections above, with arms side by side.

### Phase 2 — paper, live and replay reconciliation (flies)

A per-session paired table for flies: what paper, live and the replay each did, who traded and who
didn't with the reason, the P&L gap, and a reason category (fill, gate disagreement, timing, data).
This is the vendor's hand-built spreadsheet as a standing view. It is the question flies live keeps
asking, and every gate threshold carries the same precision risk that cost them.

### Phase 3 — the bucket engine

One engine, one column set (P&L, n, win rate, average return on risk, average P&L, average risk,
best, worst), over any value known at entry:

- weekday and entry hour;
- market events from **`core.events`**, which already reads FOMC, CPI, jobs, PCE, PPI and the rest
  from local files, adds rule events (monthly expiry, triple witching), and reports a day it cannot
  vouch for as `unknown`, never `none`; unknown days get their own bucket;
- VIX level and change;
- trend regime from `core.marketregime`, and ADX from `technicals.indicators.adx` (not yet wired to
  any regime artifact);
- reward/risk and expected move at entry;
- direction before entry and the underlying's move during the trade, the latter sized against the
  entry's expected move so a bucket means the same across symbols.

**Every cell shows n, and cells below a declared minimum are greyed out.** A few weeks of paper per
arm makes every bucket noise, so the engine runs over replay and backtest output as well (the flies
replay, the bwb two-year backtest), which is where the hundreds of trades are. Event buckets over
history need `core.events`' coverage for those years, and say `unknown` where it lacks it.

### Phase 4 — the broad views

Cheap once Phases 0–3 exist.

- **Arms overview, the console landing page.** One row per module × arm, paper or live: net, % of
  capital, open risk, profit factor, max drawdown, return over max drawdown, n, streak, and days
  since the arm's last measurement boundary. Sortable, with a compare overlay of chosen arms.
- **P&L calendar**, suite-wide and filterable by module, arm, symbol and paper/live: daily net with a
  weekly-total column. A day drills into its trades; a trade opens a drawer with its excursion against
  its exit rules and its attempt and decision trace.
- **Breakdown bars** by module, arm, symbol, weekday and hour.
- **Held-to-expiry counterfactual** where settlement is known. For a strategy whose entries depend on
  earlier ones (one position per arm in flies), this re-runs the replay rather than editing rows.

Then the remaining modules go through the same fact set, most data first: MEIC, bwb, curve, pmcc,
calendars, contango, earnings.

### Phase 5 — portfolio

Both correlation definitions (all sessions with no-trade days at zero; only sessions both arms
traded), the effective number of arms, the drawdown benefit of combining, the worst portfolio days
with how many arms lost, each arm's contribution, and re-runs over a chosen date window. It needs
months of overlapping history, and our arms have run together only since early October, so it comes
last, or runs first on replays for an early read.

## What we leave out

- **Composite 0–100 scores and their labels.** The vendor scored one strategy 97, "strong", beside its
  own maxed-out profit-concentration flag and a loss once the top 10% of winners were removed. We show
  the individual flags, each with its threshold.
- **The filter-search optimizer.** Searching filter sets over a few hundred trades manufactures
  overfitting, and the vendor's own caveat is that filtered results do not reproduce when entries
  depend on earlier ones. We keep its honest outputs, the share of trades a filter retains and a
  per-year walk-forward table, as the standard report of our replay gate studies.
- **Leaderboards, a public results page, and the conflicting-legs list.** Our arms are separate
  books, so leg conflicts between them do not arise.
- **AI anywhere in the packages.** Any narrative over the fact set belongs in `scripts/`, as with the
  end-of-day review.

## Decisions taken (2026-10-16)

- Flies first, in full, before the broad views.
- Reconciliation moves up to Phase 2.
- The event calendar is `core.events`, not a new file.
- Capital is declared per arm in each module's config, with a labelled fallback. *Revised the same
  day:* capital is two figures, built in this order. **Observed peak risk** (the most an arm had at
  risk at once, from each trade's recorded max loss) needs no declaration and covers all history; it
  is in `core.analysis.capital`. A **declared allocation** is dated, kept in one suite-level file
  (`<home>/config/arm_capital.json`) rather than the module configs no loop would read it from, and
  a session before the first declaration is counted as uncovered, never filled. Flies records no
  per-trade capital, so its peak waits for flies to record each session's peak on its books (with a
  backfill), which core then reads.
- No composite score and no optimizer.

## Open before Phase 0

- The minimum n below which a bucket greys out, and whether it counts trades or independent sessions
  (review already counts independent events).
- Where the fact set lives in core. It must not be called "overview" (the pre-open package) or use
  `book` or `profile` (see the vocabulary table in the root CLAUDE.md).
- How far back `core.events` covers, which bounds the event buckets over the replay and backtest
  history.
