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
    limit.

  Both spot distances are signed so `+` is the completing direction. Both price gaps are signed so
  `+` means the market had not reached the limit at that price. `fill_dist_widths` and
  `fill_dist_moves` normalise the centre distance by the wing width and by the entry's ATM
  straddle.

**`fly_order_path`** (live ledger) records, for each working order, the four leg quotes and spot
every time the loop looked: each main tick, and each burst-watcher cycle (about every 10 seconds).
The stream cache keeps no quote history, so this is the only record of what an order saw before it
filled.

**`shadow_completion_limit` / `shadow_touches`** (paper ledger, `fly_positions`) are the live-like
completion shadow:

- **The limit.** Every paper legged entry stamps the limit a live completion order would have
  rested at, computed exactly as `resting_completion_spec` submits it.
- **The touches.** Every tick folds the completing spread's mid gap, natural gap and spot distance
  into a first-touch record over fixed grids (`fill_model.GAP_GRID`, `WIDTH_GRID`).
- **How long.** It runs until settlement, whether or not paper's own rule has completed the
  position: the shadow is a second, independent completion of the same entry.

## Two caveats the data already carries

- **The ledger's live `debit` and `credit` are limits, not fills.** They come from the order
  status's `price` field, which is the order's own limit. Any price improvement was invisible
  until the broker's leg fills were recorded. `fee_reconcile` corrects `net` from the broker's cash
  a day later; the at-fill price comes from `broker_fill_price`.
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

## What comes next, and what would change paper

1. **Collect.** Let live orders accumulate quoted paths. A price rule needs a few dozen completion
   orders with quotes, filled and unfilled, before its grid value means anything.
2. **Fit.** Pick the basis and value whose rule fit agrees best with live, with the smallest timing
   error.
   - A distance rule is simpler, but the same distance is worth more debit late in the day and less
     on a fast one. Read it across `time_bucket` and `vol_bucket` before preferring it.
   - A first look at the backfilled orders (fill times as noticed, not the broker's): at the fill,
     spot was a median 6.4 points (1.28 widths, 0.30 straddles) past the centre. A distance rule at
     0.5–0.75 widths agreed with what live did on 90% of 52 orders, but fired 2–4 minutes before the
     real fill.
3. **Shadow first.** Read the shadow at the fitted value against paper's own rule, and against live
   on the days both traded.
4. **Switch at a declared boundary.** Replacing paper's completion rule changes what control's
   completion rate and net mean. That is a measurement-affecting change: it lands at a declared
   boundary, journaled as a `measurement_breaks` row, and never mid-era.

Entries are recorded the same way. There is no paper entry shadow yet: paper enters at its modelled
credit on the tick, and what a live entry needs (fill rate per submission, time to fill, the mid gap
at the fill) has to be measured before a rule for it can be written down.
