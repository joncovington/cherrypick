# bwb — history and incident record

Moved out of `packages/bwb/CLAUDE.md`, which keeps the rule each incident produced. This file keeps
the narrative behind them.

## Lineage

bwb absorbs and supersedes the "1-3-2 put condor" briefly floated as a fourth book for
`packages/ratios` (2026-08-22). The condor variant is retired; `ratios` stays jade lizard /
backratio / LT112. What survived: the 1-3-2 shape, the add-on as a plain matched vertical (which
keeps the combined position defined-risk by construction), and the reversal-not-falling-knife entry
philosophy.

## The expiration rule, as described versus as run (corrected 2026-09-12)

The ledger has always recorded "the next PM-settled weekly Friday strictly after today". Until
2026-09-12 the code described something else: "nearest `dte_target` (7), ties to the longer date".
A week-walk defect only ever offered the very next Friday, so "nearest" never had a second
candidate, and the third-Friday DATE was excluded outright, which left Friday 2026-09-11 with no
plan at all (`no_expiration_plan` on every book). Both were corrected and the rule restated as it
ran, so nothing either side of the fix needs separating. The plan now flags `am_monthly_date` so
monthly-date weeks, which begin here, can be told apart.

`dte_target` was removed from the advisable bounds, the example and the deployed config the same
day: an advised value would have done nothing.

## The trigger-tick substrate recorded nothing usable, 2026-08-24..27

2,337 rows, every one `measured = 0`, journaled as `trigger_ticks_unmeasured`. Two independent
defects, both fixed 2026-08-27:

- **`core.streamcache.greeks_for` never selected `gamma`.** `core.gex.compute_gex` skips any strike
  whose gamma is None, so the gamma-flip read failed every tick with `insufficient_gex_data` while
  the cache held gamma and open interest for every symbol involved. The `flip` book could not fire
  by construction for four sessions. Three other modules call the same reader and read only
  delta/iv/vega, which is why it surfaced here and nowhere else.
- **`_record_trigger_ticks` did not set `position_symbol` on its legs.** `bwb_legs` has no symbol
  column, so `build_mark_snapshot` resolved no underlying and wrote a NULL `spot` on every row. Both
  paths now go through `paper_loop._legs_with_symbol`.

How it hid: `trigger_coverage()` reported `refusal_share = 1.00` for four sessions and it read as a
number rather than an alarm (the same shape as the GEX regime-history `LIMIT 60`). `near_abs_delta`
was recorded correctly throughout and stayed in 0.05–0.39 against a 0.50 trigger, so `delta` and
`bounce` were legitimately quiet — a book that cannot fire and a book with no reason to fire looked
identical in the ledger.

## The add-on could never price (found 2026-08-27)

`_addon_snapshot` resolved `root = symbol`, asking the cache for `SPX`-rooted contracts while SPX's
weeklies list as `SPXW` — `not_root_listed` on every tick for every armed position. Every other
snapshot already resolved `config.get("occ_root") or symbol`. Once the flip became measurable the
`flip` book armed all four positions (10:39 ET) and sat unable to price one. With the root fixed,
three priced a real credit (0.25 / 0.45 / 1.05) and the fourth refused `addon_not_credit` at
−0.075. It hid because an armed position that cannot price produces a `hold`, and holds were not
recorded.

## Add-on bracket quotes were never recorded, 2026-08-24..09-25

The four `addon_*_bid/ask` columns were NULL on every row (53,410 ticks) while the docs and
`replay.py`'s own honesty rail said they rode every tick, so no replayed fire was ever priceable.
Fixed 2026-09-26 (see CLAUDE.md).

## Two recorded numbers corrected on 2026-09-18

Both found by the live work, both landed immediately per the suite rule: `entry_max_loss` had
overstated the worst case by the narrow width on every row (`wide - narrow - credit` is the payoff;
the 2026-09-04 settlement was the proof), and settlement had charged the $5 event fee per ITM leg
ROW, so the doubled body paid twice. Rows before 2026-09-18 carry the old values; both are
derivable and neither is rewritten.

## Milestones

Built 2026-08-23; paper since 2026-08-24. Runs as the supervisor's `bwb-paper` job on a 60s
interval; the console's `/bwb` page landed by 2026-09-02. Live path landed 2026-09-18.
