# pmcc — history and incident record

Moved out of `packages/pmcc/CLAUDE.md`, which keeps the rule each episode produced.

## The 2026-08-23 redesign (measurement break)

The original design traded TNA/TQQQ/UPRO in `control`/`keltner`/`roll` books, with a ~99-delta long
chosen by an extrinsic-and-delta floor, a yield-targeted ITM short, and an early tv-exhaustion exit.
It was retired for TQQQ only in a single `control` book: the long chosen inside a DELTA BAND
(0.85-0.90) rather than past a floor, the short simply the strike NEAREST spot with no yield search
(it can land OTM), and hold to the short's own expiration by default. The old tv rule survives as
`tv_managed_exit`, reachable only through an advised twin's frozen params. The symbol, the roster,
both leg-selection rules and the default exit all changed. Pre-redesign rows carry `symbol` values
(TNA, UPRO) and `book` values (`keltner`, `roll`) this checkout no longer writes; four closed cycles
(TQQQ, one apiece across `control`/`keltner`/`roll`/`advised:control`) sit before the era boundary.

## XSP added (2026-08-23, additive break)

`symbols` became `["TQQQ", "XSP"]` in the same `control` book. Additive rather than a retirement:
TQQQ's own results either side remain comparable, since nothing about how TQQQ trades changed.

## `slot_held` (2026-08-28)

The module showed no entry attempts for a whole session while entirely healthy — 241 entry
iterations, marks half a minute old — because every book already held every symbol and the entry
phase short-circuited before writing a row. "All slots full" and "never evaluated entry" produced
the same empty table, and the first is this module's ordinary state for one to two WEEKS at a time.

## The advised twin starved of deep strikes (2026-08-24..27)

`engine.BOOKS` holds base books only, so the window gate asked "can `control` still enter?" and
dropped the widened window once control filled a symbol, while `advised:control` was still trying.
Control took XSP on 08-24; the twin recorded 658 `no_deep_itm_long` refusals across 08-25 and 08-26
before a re-centre let it in on the 27th.

## Why the window hint has a direction (2026-08-24)

A symmetric count bought an identical block of strikes above spot that no book reads; that block was
the largest single waste in the suite's subscription budget that day.

## Entry spread gate (2026-08-28)

`max_leg_spread_pct` had been enforced only in `management.execution_gate` (whether a mark may be
acted on when closing), so nothing measured the spread paid at entry and the docs described a gate
the code did not have. Measured over all 8 entries before landing: seven at 0.018–0.072, deep-ITM
long legs included, and one at 0.293 — so the gate refuses the anomaly rather than the strategy.

## The shield boundary (2026-10-05, measurement break)

On 2026-10-04 Tom King's "Income Shield" (a ~1-year deep-ITM call held, a ~70-delta weekly call
rolled against it) was compared with control. The replay (`scripts/pmcc_shield_replay.py`,
docs/shield-study.md) found:
- holding the long beat re-buying it on all five candidate symbols over 2011–2026, though in only 11
  of 15 five-year windows;
- the weekly short's edge turns on the IV assumption;
- TQQQ is the worst vehicle for any version.

So the module took Tom's design as two new arms beside control, rather than redesigning control.
`shield` follows his rules including the early roll; `shield_hold` holds each short to Friday. The
symbols became XSP, QQQ, GLD, IWM and SLV, and TQQQ ran off.

Built beside it:
- the held-long lifecycle (`management.evaluate_held_long`);
- per-leg costs;
- per-symbol stream requests;
- the producer's persisted expiry listing (`stream_expirations`), with a request for an unlisted
  date no longer treated as a stall;
- `tracker.value_at`, with the console's tracker tab and weekly A/B;
- the notifier's per-leg settlement and notable-roll pings.

The arms' defining rules live in code (`engine.ARM_RULES`), and an undeclared arm stays off.

Found on the way:
- **Six pre-existing bugs**, fixed first in their own commits:
  - `excursions` filtered on a leg role the module never wrote;
  - the console mirror test could not fail;
  - the review's expected readers filtered on a column that had moved;
  - settlement iterated config symbols rather than the ledger's;
  - the watchdog choked on bwb's orphan list;
  - bwb's live log did not mask account numbers.
- **The year-long pick:** nearest-to-360 chose end-of-quarter expirations whose grids stop above the
  deep band, so the pick prefers standard monthlies (`scripts/pmcc_leap_probe.py`).
- **The replay's own IV level:** fitted to the ledger, it priced weeklies about 5% rich against WPUT,
  so it was refitted to that benchmark.

The era is `shield`. Pre-boundary rows stay under `redesign`.

## Milestones

Built 2026-08-16. `live.enabled` placeholder added 2026-08-16. The console's `/pmcc` page landed
2026-08-17.
