# Fill realism: measuring live fills, then making paper fill like live

Started 2026-10-02. Paper and live complete a legged spread by two different rules, and until now
nothing measured the difference.

- **Paper** completes on the first 15-second tick where the modelled completing debit is below
  `credit − fee_buffer` and the floor gate passes. The modelled debit is mid plus `slippage_frac`
  (0.125) of each leg's bid-ask spread. Paper pays that modelled debit.
- **Live** rests a Day limit at `live_orders.max_safe_completion_debit` the moment the entry fills.
  It pays the limit whenever the market reaches it, or is cancelled at 15:30.

## What prompted it

Over 2026-07-30..10-02, **43 of 48 live completions filled below the best modelled debit the live
loop ever saw** while the spread was open, by a median 0.14 points (about $14 a position). Paper's
gate would have refused those completions on the same quotes.

The live loop samples once a minute, so part of that gap may be dips it never saw. Even so, it
points the opposite way from the live-trading plan's working assumption that paper's completion
rate is a ceiling on live's. Which way the gap really runs is the first thing this work answers.

## What is recorded

All of this is telemetry. No gate reads any of it, and a failure to write it never costs a trade
(`live_loop._telemetry`). It does not change what a session's numbers mean, so it is not a
measurement break.

**`fly_live_orders`** (live ledger) has one row per live order, entry and completion alike, filled
or not. A fill rule has to be fitted on the orders that never filled as much as on the ones that
did. Each row carries:

- **At placement:** the limit, the spread's mid, spot.
- **At resolution:** the outcome (`filled`, `cutoff_cancelled`, `replaced`, `cancelled` or a broker
  terminal state); when we noticed; the broker's own fill time and the net price from its leg fills.
- **At the fill**, the three distances. Each is the basis of a different paper rule, so all three
  are kept:
  - `fill_dist_center`: spot against the centre, the short strike the open spread was sold at.
  - `fill_dist_long`: spot against the completing long strike.
  - `fill_mid_gap` / `fill_natural_gap`: the order's spread at mid and at the natural, against the
    limit. They come from the last path look at or before the fill, and only from one no more than
    15 s before it (`fill_facts.MAX_QUOTE_AGE_SECONDS`); `fill_obs_at` is that look and
    `fill_obs_age_s` how long before the fill it was. Past 15 s both gaps are NULL. Until
    2026-10-12 they took a look up to 60 s old, and the burst watcher's minute-long holes in the
    path meant seven completions on 2026-10-05..07 measured a market that had since moved: five
    recorded it 0.20-0.55 above a limit it filled at, from looks 34-53 s old. A `rebuild` re-derives
    those rows under the new bound.

  Both spot distances are signed so `+` is the completing direction. Both price gaps are signed so
  `+` means the market had not reached the limit at that price. `fill_dist_widths` and
  `fill_dist_moves` normalise the centre distance by the wing width and by the entry's ATM
  straddle.

**`fly_order_path`** (live ledger) records, for each working order, the four leg quotes and spot
every time the loop looked: each main tick, and each burst-watcher cycle (about every 10 seconds).
The stream cache keeps no quote history, so this is the only record of what an order saw before it
filled, and it stays the finest one (about every 10 s, and tied to the order). From 2026-10-13 the
gex recorder also keeps the chain's quotes every ~15 s for 20 strikes each side of spot
(`core.quotehistory`), which covers every strike, traded or not.

**`shadow_completion_limit` / `shadow_touches`** (paper ledger, `fly_positions`) are the live-like
completion shadow:

- **The limit.** Every paper legged entry stamps the limit a live completion order would have
  rested at, computed exactly as `resting_completion_spec` submits it.
- **The touches.** Every tick folds the completing spread's mid gap, natural gap and spot distance
  into a first-touch record over fixed grids (`fill_model.GAP_GRID`, `WIDTH_GRID`).
- **How long.** It runs until settlement, whether or not paper's own rule has completed the
  position: the shadow is a second, independent completion of the same entry.

## Two caveats the data already carries

- **The ledger's live `debit` and `credit` were limits, not fills, until 2026-10-12.** They came
  from the order status's `price` field, which is the order's own limit. A check against the
  broker's transactions that day found 23 of 163 filled orders better than their limit, by up to
  0.10 (three of them), and `broker_fill_price` matched the transactions on all 163. Since then a
  position books `broker_fill_price` (`fill_model.booked_price`), and `python -m
  cherrypick.flies.fill_facts book-prices` (dry run unless `--write`; it backs up the ledger and
  refuses any change the transactions do not confirm) rewrote the earlier rows. On rows fee
  reconciliation had confirmed, `net` and `pnl` were already the broker's cash, so only
  `credit`/`debit` and `slippage_dollars` moved.
- **Before the order status carried leg fills, the only fill time known was when the loop noticed.**
  Such rows say `fill_time_source = 'noticed'`, and their measures describe that moment.

## Backfill

`python -m cherrypick.flies.fill_facts backfill` (dry run unless `--write`) rebuilds rows for the
orders placed before recording began. Its sources:

- the live ledger;
- the decision journal (when each completion was placed and cut off);
- the broker's transactions (GET-only), for fill times and prices;
- the gex recorder's spot trail, for spot at the fill and the spot path while each completion worked.

It **cannot** recover a price gap, because there is no quote history. Those stay NULL and are never
estimated. A row written live is never overwritten by the backfill.

## Reading it

`python run.py fill-model [--grid] [--basis mid|natural|dist]`. It is read-only on both ledgers,
via `analytics.fill_realism` and `analytics.shadow_completion`.

- **`live`:** per order leg, the outcomes, the distribution of every at-fill measure, price
  improvement against the limit, minutes to fill and notice lag.
- **`live.rule_fit`** (with `--grid`): for each basis and grid value, how well "fill at the first
  touch" reproduces what live did. It counts hits, misses, false fills and true non-fills, and
  gives the timing error against the real fill. Price bases are scored only over orders with a
  quoted path (2026-10-02 onward). The distance basis also covers backfilled orders, whose path is
  the spot trail. The counts say which population each figure comes from.
- **`shadow`:** per paper arm, paper's own completion rate and net beside the shadow's at each grid
  value, on the same modelled cost stack at each row's own settlement price.
  A negative price value is a deeper resting limit (`limit + value`), so the same record replays
  a bigger net target, down to credit − 1.00, with the 15:30 cutoff applied exactly. The ledger's
  own lowest-debit columns cannot do that: they keep only the day's low, usually after the cutoff.

## What comes next, and what would change paper

0. **Price: done, from 2026-10-05.** Live filled at its limit 42 times out of 42, so the price was
   never in doubt. Every paper legged completion now pays the live limit (`engine.pays_limit`)
   instead of its modelled debit, on the unchanged trigger: about $4 less a completion, and a
   book-wide `completion_rule` break. What follows is the trigger, a separate and later break.
1. **Collect.** Let live orders accumulate quoted paths. A price rule needs a few dozen completion
   orders with quotes, filled and unfilled, before its grid value means anything.
2. **Fit.** Pick the basis and value whose rule fit agrees best with live, with the smallest timing
   error.
   - A distance rule is simpler, but the same distance is worth more debit late in the day and less
     on a fast one. Read it across `time_bucket` and `vol_bucket` before preferring it.
   - The backfill (2026-10-02; broker fill times, spot from the trail; 42 filled and 13 cut-off
     completion orders, 2026-08-01..10-02) is in the experiment log. In short: completions filled
     with spot a median 1.03 widths past the centre, at the completing long strike; no fill was
     better than its limit by more than 0.05; and a distance rule at 0.25–0.75 widths agreed with
     live on 84% of orders, firing a median 2.5–6.5 minutes before the real fill.
3. **Shadow first.** Read the shadow at the fitted value against paper's own rule, and against live
   on the days both traded.
4. **Switch at a declared boundary.** Replacing paper's completion rule changes what control's
   completion rate and net mean. That is a measurement-affecting change: it lands at a declared
   boundary, journaled as a `measurement_breaks` row, and never mid-era.

5. **The live limit itself is a lever, and the same data prices it.** The completion is a buy, so a
   HIGHER limit fills sooner, with less spot travel, and nets less; a lower one locks in more and
   needs spot to go further. Today the `min_floor_dollars` bound caps the limit at credit − 0.25.
   - **The odds are lopsided:** a miss cost about $265 against $25 for a completion, so 0.10 more
     on the limit ($10 a completion) pays for itself if it converts more than about one miss in 27
     completions.
   - **Measuring it:** first touch of `mid_gap <= δ` (and the natural's) on the shadow and on live
     quoted paths is "would a limit δ higher have filled, and when".
   - **Changing it** is a live, measurement-affecting change. Rule 6 permits a lower floor only
     against that measured alternative.

Entries are recorded the same way.

## The entry shadow (2026-10-14)

**What live entries do.** Live asks the plan's credit floored to the tick (mid less 0.05 on SPX).
Of the first 50 entry orders recorded at placement (10-05..10-14), 43 filled, most within 0–4 s and 9
of them better than the limit. 6 of the 7 misses were cancelled and re-placed on the next tick, which
usually filled a minute later at a new centre or side. One (10-14 11:12) was lost.

**The misses are not a random 14%.** On the gex spot trail, spot moved toward the completing side in
6 of the 7 (+0.76 points at 20 s, +1.00 at 60 s), against about half the fills (−0.07 / −0.02). A
spread that starts working cheapens under the limit before the order fills, so live misses the
entries that were going its way. A random haircut on paper would take out average trades. This one
takes out good ones.

**Do not measure it from the order path.** `fly_order_path` stops at the fill, so a filled order's
later moves were never observed there. The first read of this effect compared spread moves from the
path and found −0.24 against +0.01; that was survivorship. The trail covers every order alike.

**What paper records.** Every legged entry stamps `shadow_entry_limit` (what `live_orders.entry_spec`
would ask) and `shadow_entry_path`: one look per tick for the first `ENTRY_SHADOW_WINDOW_SECONDS`
(60) after it, at spot's move toward completion and the entry spread's gaps to that limit
(`fill_model.entry_observation`). Tag-don't-gate: paper still enters at its modelled credit on the
tick.

**Reading it** (`run.py fill-model`, the `entry` block). `fit` scores the rule "live misses an
entry whose market moved `value` its way within `window`" (`fill_model.entry_fills`) against live's
entry orders on the spot trail (`fill_facts.entry_spot_paths`), beside `always_fill`, the agreement of
assuming every entry fills. `shadow` applies the rule to paper's legged rows per arm and splits their
net into would-fill and would-miss.

**No rule clears the bar yet.** Over the first 50 orders, the best window and threshold (15 s, 1.5
points) agreed 88% against 86% for always-fill, catching 1 of the 7 misses. Spot moves the size the
misses saw are as common among fills, so the group difference does not pick out the order. Read the
shadow only once a rule beats `always_fill` by a margin that survives more misses (about 20–30).
Paper's 15 s look cannot see the seconds that decide a live entry, so the price basis (`mid_gap` on
paper; the quote history for live orders from 2026-10-13) is the next thing to fit.

**What would change paper.** As for completions: fit, shadow, then switch only at a declared
boundary, journaled as a break. A missed entry is re-placed on the next tick, so a paper rule must
re-evaluate rather than drop the trade.
