# overview — incident record

Moved out of `packages/overview/CLAUDE.md`, which keeps the rule each incident produced.

## `calm_tape` never measured (first session to 2026-08-27)

The gate reported "not measured" on every stored pack, so the phase was a five-gate verdict only four
gates could join, sitting permanently at `MIN_MEASURED_FOR_GREEN` with no margin. The package runs
pre-open, where today's `stream_summary` row does not exist, while the prior close's base was looked
up on the CALENDAR date of the last trade — for an overnight print, today. Fixed both halves: the
base falls back to the newest completed session's row, and the print is dated to the session it
belongs to.

## Breadth declared as `symbols` (2026-08-17)

The eleven sector ETFs plus VIX/VVIX/GLD/USO/HYG/TLT in `symbols` had the producer maintaining 0DTE
chains for sixteen symbols nothing reads, ~20,000 subscriptions; it crash-looped on a locked cache
and every trading module's quotes went stale, during market hours.

## `history_days` at 1000

Across sixteen symbols it never finished: each reconnect restarted the backfill from the top, so the
producer spent its life re-fetching four years of candles instead of serving quotes.

## Events a leg is served

Cash legs were Quote-only, so the index readings (which publish Trade, never Quote) had no price and
the panel froze on 2026-08-17; Trade was added. Summary was underlyings-only until 2026-08-25, so
`day_close` never landed for any leg and `daily_closes` stopped accumulating on 2026-08-14.

## SKEW from the stream

The stream's SKEW backfill returned five scattered rows in 270 days.

## First pre-market tape run (2026-09-27)

All nine new futures/index legs printed within seconds and none had a settle until the request
declared 30 days of history for them. The prior-close reader, with the prior session's row missing,
took the newest row it could find: IWM's Friday (+0.11%) printed as -1.83% against a close two
sessions back.
