# calendars — history and incident record

Moved out of `packages/calendars/CLAUDE.md`, which keeps the rule each episode produced.

## SPX to SPY (2026-08-15)

Changed for buying power: a calendar's requirement is its debit, and SPY is a tenth of SPX's
notional for the same structure. XSP is the same tenth and would have needed no code, but was
rejected on measured liquidity — median option spread 26% of mid against a `max_leg_spread_pct` of
0.25, so the median leg would fail the execution gate; SPY's median measured 3%, tighter than SPX's
own 4%. What SPY cost is the settlement model (physical delivery; see CLAUDE.md).

## Ex-dividend weeks (user decision 2026-08-15)

Every 2026 SPY ex-date (09-18, 12-18, potential excise 12-31) lands exactly on the short-expiry
Friday, and an ITM short call is really assigned at the close *before* the ex-date, a session before
this module books anything. Rather than approximate that — this is a paper experiment testing exit
rules, and an ex-div week is a different trade — entry refuses the week. The third-Friday rule fails
on SSGA's own Jun 2026 date and aggregators disagree by a day, which is why dates are hand-declared.

## Supervising on the log (2026-08-14..17)

Every line the loop writes is event-driven, so a week holding no position wrote nothing and a
healthy quiet loop looked wedged. The supervisor killed and restarted it every two minutes from
launch (49 times on 08-14, 107 on 08-17); the only thing refreshing the log was the restart's own
startup line. Against the declared 30s cadence that cost 61% and 28% of those sessions' ticks, in
gaps of up to ten minutes. The ledger survived only because no position had opened (`dc_marks` was
empty); otherwise the mark path would have been shot through with ten-minute holes at a ragged,
undeclared cadence. Replaced by the top-of-tick heartbeat.

## First scheduled Monday (2026-08-17)

No position: every entry attempt in the 10:00–10:15 window refused `no_fresh_quotes` — 248 near-spot
option quotes in the cache, every one older than `max_quote_age_seconds` — and the week was journaled
`week_skipped_entry_window_exhausted`. The module behaved correctly; the cache had no fresh SPY
option quotes.

## The absolute spread floor on exit (2026-08-28)

A percentage-only gate refused the control put's scheduled Friday close on all thirty ticks of its
window (`bid 0.00 / ask 0.01` is a one-cent buyback and, as a ratio, a 200% spread) while the call
side closed normally at 0.222. The position missed its exit, its front expired, the longs went
Monday under `long_disposition`, and the result differed from the replay by $1.30. Journaled as
`exit_gate_absolute_spread_floor`.

## The replay's validation tightened to a cent (2026-10-12)

`validate_against_control` defaulted to a $0.50 tolerance while every description of it said "to
the cent". Read against the paper ledger, `control` matched within a cent on every graded week but
`path` came out $0.44–0.49 rich on all six: the recorder marks open legs only, so the Monday
disposal tick is the last one written and lands before the disposition minute, the derivation falls
back to the recorded close price, and the fallback dropped the disposal's fee and slippage. The
fallback now carries the recorded disposal cost (the row's exit charges net of its settlement
charges) and the default is $0.01. All twelve graded week-arms now agree within a cent (path to
$0.00, control to −$0.01 from rounding), and `expiry-longs-mon` in the policy table is $0.44–0.49 a
week lower than before. A correction of a wrong number, not a measurement change.

## Advice ordering (2026-09-15)

Since this date the session's advice decision is derived and recorded BEFORE the ex-dividend gate,
so the advisor scores a refused week as advice that governed nothing rather than as an artifact that
never reached the loop.

## Console page (2026-08-17)

`/calendars` landed 2026-08-17; it reads the ledger directly for state and calls `cli.py` for
policies and the week plan (see CLAUDE.md for why).

## `dc_paired_debits` never recorded a row (2026-08-24..10-12, fixed 10-12)

The Friday regime's primary measurement -- the Monday-moment price of the strikes the Friday arm
bought -- was never written. `_try_entry` rebinds `arms` to the session roster before it tests
`arms is None` to mean "the default regime's pass", so the test was always false and
`_record_paired_debits` never ran, from the day it was added. Four Friday weeks traded (09-07,
09-28, 10-05, 10-12) and should have left eight rows. The direct tests of `_record_paired_debits`
could not see it; `tests/test_paired_debits_wiring.py` now drives the real Monday path and was
shown to fail on the old line. A recording fix, not a measurement change: it lands immediately.
**The lost weeks cannot be backfilled** (the cache keeps no Monday-morning history), so the paired
sample starts with the next Monday, 10-19, and the retirement bar in friday-entry-arm.md that reads
it (15 paired weeks) starts counting there.

Two smaller fixes beside it: a failed paired-debit write now prints to stderr instead of passing
silently, and `run.py record-break` opens the ledger for writing (the 09-24 switch of every CLI verb
to read-only caught it, and it raised on every call since). Still open: `friday_week_skipped` can
never be journaled (it waits for `now_min > 16:00`, and the loop stops at 16:00), and the read-side
exit grid covers `path` but not `friday:path`.
