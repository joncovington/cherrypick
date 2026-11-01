# Large options orders as a signal-board method — plan

*Drafted 2026-10-17. A build plan and a declared test. Step 1, the ThetaData flow fetch
(`backtest fetch-flow`, `flow.py`, `flowfetch.py`), was built the same evening; the declared test and
the broker's nightly feed are not yet run or built. 2026-10-24: the fetched sessions declared,
the plan file and parallel fetch added, and a second family of tests declared.*

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

**The broker carries the nightly feed, so the method outlives the ThetaData subscription.** The
broker's DXLink feed serves the same prints as TimeAndSale events (price, size, the bid and ask at
the print, an aggressor side, a spread-leg flag and the sale conditions), and it answers a
subscription with a start time with the trades since then. Checked 2026-10-17 on one contract pair:
the broker's prints for a session matched ThetaData's one for one, first and last timestamps
included. But its history reaches back about one week only. So:

- **ThetaData** is for history: the declared test below, run while the subscription lasts. Its data
  and the numbers computed from it fall under its licence and the backtest package's purge.
- **The broker** is for every session from the build on: the nightly fetch reads it, and open
  interest comes from the broker too. A missed night is caught up from its week of history. It is
  read over its own DXLink session, never through the streamer, whose cache has one writer.
- **The two run side by side** while both exist, and the broker's orders must match ThetaData's
  name by name and session by session before the broker is relied on alone. A difference in the
  grouping or the side read between two sources would otherwise move the tags without anyone
  noticing.

Rejected, and why:

- **QuikOptions flow** (`scripts/quikoptions_flow.py`) lists the day's top trades only, and its names
  rarely coincide with the vendor's flow tags. It stays what it is: a daily read with its own test.
- **Cboe Open-Close and Nasdaq Trade Outline** state origin (customer, firm, market maker) and
  opening or closing outright, which no inference here can. They cover their own exchanges only and
  are priced per exchange and per month of history. The upgrade, if the test below finds something.
- **OCC's volume by account type** (already landed) has no side and no open or close.
- **Retail flow services** screen by rules they do not publish, so a tag built on them could not be
  reproduced or audited.

**The licence outranks the build.** Everything fetched from ThetaData, and everything computed from
it, lives in the backtest package's licensed store and results, and never enters this repo, a PR, an
issue or a published page. This document holds the method only. The board's nightly tags come from
the broker's feed, which is market data for the account holder's own use: they stay local too, read
by the board and shown by the console, never published.

## The method, declared

**An order, not a print.** Prints are grouped before anything is measured:

- an intermarket sweep (condition `INTERMARKET_SWEEP`, and the single-leg auction and cross ISO
  variants) is one order across every exchange it printed on at that instant;
- prints in the same contract, on the same side, within `ORDER_GAP` seconds of each other are one
  order: an algorithm slicing a block is still one decision.

**Excluded outright:** every leg of a multi-leg order (the `MULTI_LEG_*`, `ML_*` and stock-options
conditions). A spread's legs are not two opinions. Cancelled prints are dropped.

**Known gap (2026-11-01, not a rule change):** "dropped" means the cancel prints (conditions 40-44)
only. The trade each one cancels stays in and counts as an order. ThetaData's history carries no
link from a cancel to its original (`sequence` is 0), so removing it means matching on contract,
exchange, price and size, which is ambiguous when several earlier prints match. Item 32 ran with
the gap. Any new test on this history declares a matching rule first, before results are read. The
broker's prints have no gap: a cancel there names the print it replaces.

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
that confirmed it, and the source it came from. It is local only. Where it is scored
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

## The sessions fetched, declared 2026-10-24

The timed sample (five names, one session) took a median 14 s a name-day and 60–80 s for the
largest. The universe above holds 531–788 names a session (1,056 over the window), so the whole
design window is about 70 days of fetching, past the week the build allows. Thinned on a fixed rule,
chosen before any outcome was read:

- **every 5th design session**, counted from the window's first (157 sessions, 96,445 name-days);
- **plus the 10 sessions before each report's reaction day**, for the earnings test below, on the
  sessions the name passes the universe bar (7,377 reports; 153,577 name-days in all).

`scripts/flow_fetch_plan.py` writes that list from the local stores; `backtest fetch-flow --plan`
reads it, several requests at a time on the one session, and `--stop-by` keeps it clear of the
nightly fetch's login. The tag test above reads the grid sessions only; with a 5-session outcome
they do not overlap.

## A second family, declared 2026-10-24

A vendor education session (2026-10-23) stated rules our tags do not have. They are tested here as
their own family, counted apart from the nine above, and declared before the fetch ran at scale.
None is tuned; each threshold is the one stated, or where none was stated, set here.

**Order filters**, each applied to a first-family tag at the same cut. A filtered tag is compared
with the same matched baseline, and with the unfiltered tag:

| Filter | Tags | Rule |
|---|---|---|
| F1 days to expiry | all three | the contract's expiry is at least 7 calendar days after the session |
| F2 not deep in the money | all three | the strike is no more than 10% in the money against the session's close |
| F3 committed capital | put selling | strike × contracts × 100 at least $10M, in place of the premium cut |
| F4 no straddle | all three | dropped when an order of the other right at the same strike and expiry, the same side and size within 10%, starts within 60 s of it: a volatility bet, no direction |
| F5 protection beside a sale | put buying | not tagged on a session the name also carries a large opened put sale |
| F6 repeat | all three | needs a second large opened order of the same tag in the name within the previous 10 sessions |
| F7 no report ahead (added 2026-10-30, before any read; item 32) | all three | not counted when the name's next report has its reaction day inside the 5-session outcome window |

The vendor's call-to-put premium ratio (2:1) is not tested: the stored histogram does not split by
right or side, and adding it would change the fetch after it started. Funds are outside the
universe already, so its "index puts are protection" rule needs no test.

**Trials:** F1, F2, F4, F6 and F7 on three tags at three cuts (45), F5 on one tag at three cuts (3),
F3 once (it replaces the cut): **49** (40 until F7 was added on 2026-10-30), deflated as one family, apart from the first. **Pass** as above,
and a filter must also beat its unfiltered tag, or it adds nothing.

**The earnings test.** The vendor's claim: over 63 reports, a large bet in the 10 sessions before a
report pointed the right way 53% of the time for bullish orders and 64% for bearish. Declared:

- **Events:** each design-window report whose name has at least one large opened order
  (first-family rules, $100,000 cut) in the 10 sessions before its reaction day. The sign is the
  net of bullish minus bearish premium over those orders; a tie is dropped.
- **Outcome:** the reaction day's close against the last pre-report close, minus SPY's, signed.
- **The comparison:** the events' hit rate against 50%, with a bootstrap interval over reports, and
  the mean signed excess against zero.
- **Two trials** (bullish, bearish). **Pass:** the hit-rate interval excludes 50% and the mean signed
  excess is above zero after an assumed 1% round-trip cost on the move. Then once on the holdout.

## Registered and settled before the first read, 2026-10-30

Registered as item 32 in [edge-hypotheses-plan.md](edge-hypotheses-plan.md), which records what had
to be settled before any outcome was read: the top-1% rule reads a name's last 20 fetched sessions
(at least 4), the thin 2025 grid sessions are read with and without, three readings are reported
beside the verdict (a stale-tag placebo, report windows dropped (judged as F7 from 2026-10-30), the five most frequent names
removed), and the holdout is fetched on the same grid rule. Nothing else here changes.

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
3. **The nightly broker fetch** (a script: it reaches the broker): each tradable name's contracts,
   that session's prints and the next morning's open interest, grouped and classified by the same
   code as the ThetaData fetch, so the two cannot drift. Compared with ThetaData's for every
   session both hold.
4. **The board method** (`packages/technicals`), only for the tags that passed, reading the broker's
   nightly orders.

Until 4, the board counts its other methods and says flow is not counted, as it does now.

## Open questions

- `ORDER_GAP`, the grouping window, is set before the test (a few seconds) and not tuned by it.
- Whether delta dollars should replace premium as the size test is a question for after the test,
  answered on the holdout's leftovers, never by re-running design.
