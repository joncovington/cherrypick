# Large options orders as a signal-board method — plan

*Drafted 2026-10-17. A build plan and a declared test, nothing built yet.*

## The question

The signal board (`packages/technicals` `board.py`) counts, for each stock, how many methods agree on
one side. The vendor's board carries three flow tags the suite's does not: institutional call
buying, put selling and put buying. Can the suite build its own version of those, and does it
earn a place in the count? (Its own version, named for what it measures: see "What a tag claims".)

They earn it only by predicting. A tag that does not separate what a stock does next from what a
same-stage stock without it does is noise in the count, however plausible it reads.

## Where the data comes from

ThetaData Options Standard, which the backtest package already holds (`packages/backtest`). Its
historical **trade with quote** endpoint returns every option print with the bid and ask standing
when it printed, its size, its exchange and its trade conditions, at tick level, per name and day.
The whole-market daily file needs the Professional tier and is not used.

Rejected, and why:

- **QuikOptions flow** (`scripts/quikoptions_flow.py`) lists the day's top trades only, and its names
  rarely coincide with the vendor's flow tags. It stays what it is: a daily read with its own test.
- **Cboe Open-Close and Nasdaq Trade Outline** state origin (customer, firm, market maker) and
  opening or closing outright, which no inference here can. They cover their own exchanges only and
  are priced per exchange and per month of history. The upgrade, if the test below finds something.
- **OCC's volume by account type** (already landed) has no side and no open or close.
- **Retail flow services** screen by rules they do not publish, so a tag built on them could not be
  reproduced or audited.

**The licence outranks the build.** Everything fetched, and everything computed from it, lives in the
backtest package's licensed store and results, and never enters this repo, a PR, an issue or a
published page. This document holds the method only. A flow tag on the board is a derived work
too, so the board reads it locally and the console shows it locally, and it is never published.

## The method, declared

**An order, not a print.** Prints are grouped before anything is measured:

- an intermarket sweep (condition `INTERMARKET_SWEEP`, and the single-leg auction and cross ISO
  variants) is one order across every exchange it printed on at that instant;
- prints in the same contract, on the same side, within `ORDER_GAP` seconds of each other are one
  order: an algorithm slicing a block is still one decision.

**Excluded outright:** every leg of a multi-leg order (the `MULTI_LEG_*`, `ML_*` and stock-options
conditions). A spread's legs are not two opinions. Cancelled prints are dropped.

**Its side** comes from where it filled against the quote standing then: edge = (price − mid) / half
the spread. At or past +0.5 it is **bought**; at or past −0.5 **sold**; between, **unread** and
never tagged. That is the QuikOptions scorer's `READ_EDGE`, kept so the two reads agree on what
"bought" means.

**Its size** is premium (contracts × 100 × price) and, beside it, delta dollars (contracts × 100 ×
|delta| × the stock's close). Premium decides whether an order is large; delta dollars is kept
because premium overstates deep in-the-money orders (mostly stock replacement) and understates far
out-of-the-money ones.

**Large** means both:

1. premium at or above the cut, one of **$50,000, $100,000, $250,000** (declared now, never
   extended); and
2. in the top 1% of that name's orders by premium over its previous 20 sessions, so an order that
   is routine for NVDA is not large because it would be large for a mid-cap.

A contract count is not a test: on one heavily traded name's day, most orders of 100 or more
contracts were small money, many of them options priced at cents.

**Opening** is confirmed the next session: the contract's open interest rose by at least half the
order's size. An order that does not open a position is not a bet and is never tagged.

**The tags**, for a stock on a session, each set by at least one large, opened order:

| Tag | Order | Side | The vendor's nearest tag |
|---|---|---|---|
| Large call buying, opened | a call bought | bullish | Institutional Call Buying |
| Large put selling, opened | a put sold | bullish | Institutional Put Selling |
| Large put buying, opened | a put bought | bearish | Institutional Put Buying |

A call sold is recorded and never tagged; the vendor's board carries no such tag, and an overwrite
says little about direction.

## What a tag claims, and what it does not

**Nothing here knows who traded.** OPRA's prints carry no account type; only an exchange's own
origin data (Cboe Open-Close, Nasdaq Trade Outline) says customer, firm or market maker. So the
word "institutional" is the vendor's, and never ours: a tag is named for what was measured, and
each part of the name is one measurement:

- **large**: the order's premium, against a fixed cut and against the name's own orders. Measured.
- **buying / selling**: where the fill sat against the quote standing then. **Inferred**: a fill at
  the ask is usually a buyer, but a market maker can lift an offer too, and a fill at the ask can
  close a short as easily as open a long. The row says "read from the fill", with the edge.
- **opened**: the next session's open interest rose by at least half the order. Measured the next
  morning, so a tag does not exist until then; a session's tags are the previous session's orders.

On the board a flow tag reads as its own method ("Large options orders"), listed beside the others
with its detail: the contract, the premium, the edge it was read from and the open-interest change
that confirmed it. It is marked derived from licensed data and is local only. Where it is scored
against the vendor's board, ours and theirs are paired by the table above, in the scoring only;
the board never prints the vendor's words for our tags.

If Cboe or Nasdaq origin data is ever bought, a tag confirmed by it may say "customer" or "firm",
because then it would be measured.

## The declared test

Over the backtest package's **design** window (2022-05-16 to 2025-06-30), on every stock and session
in the universe below:

- **Outcome:** the stock's return over the next 5 sessions minus SPY's, signed by the tag's side
  (a fall is a gain for a bearish tag). Close to close, from the session after the tag is known
  (the open interest that confirms it is the next morning's), so no outcome leaks into its own
  signal.
- **Against:** stocks in the same relative-strength stage on the same session without the tag (the
  stage rule the board already uses). A tag that only marks leaders is the stage method again.
- **The comparison:** the tagged stocks' mean signed excess against the matched baseline's, with a
  stationary-bootstrap interval over sessions (`packages/backtest` `stats.py`).
- **Trials:** three tags × three cuts = nine, every one counted. The deflation is by nine whatever
  is looked at first.

**Pass:** a tag passes at a cut when its interval excludes zero after deflation AND the larger cuts
do at least as well as the smaller. Bigger orders must not predict worse, or size is not what is
carrying it.

**Then, once:** the passing tags at their passing cut are run over the **holdout** (2025-07-01 to
2026-10-12). Spent once. A tag that fails there does not enter the board.

**The universe, chosen with no hindsight:** on each session, the stocks whose raw close was at
least $5 and whose 50-session median dollar volume, up to the session before, was at least $100M
(the options-tradable test's stock bar; the technicals study's `universe.membership` rule at that
bar). Not today's tradable list, which would test 2022 on 2026's survivors.

## The build, in pull requests

1. **The fetch** (`packages/backtest`): per stock and session, trade-with-quote for every expiry,
   grouped into orders and classified as above; every large-candidate order at the smallest cut
   kept, with each name's daily order-premium distribution so the top-1% rule can be applied for
   any session; and the next session's open interest for each contract kept. Resumable, refusals
   recorded, one ThetaData session at a time, scheduled clear of the nightly backtest fetch.
   It starts with a timed sample. If the full design window would take more than a week of nights,
   the sessions are thinned on a fixed rule (every other session) declared before any outcome is
   read, never by which ones looked interesting.
2. **The test** (`packages/backtest`): the declared test above, results under the package's
   results store only.
3. **The board method** (`packages/technicals`), only for the tags that passed: the nightly fetch
   for the latest session, and the method reading it, marked as derived from licensed data.

Until 3, the board counts five methods and says flow is not counted, as it does now.

## Open questions

- `ORDER_GAP`, the grouping window, is set before the test (a few seconds) and not tuned by it.
- Whether delta dollars should replace premium as the size test is a question for after the test,
  answered on the holdout's leftovers, never by re-running design.
