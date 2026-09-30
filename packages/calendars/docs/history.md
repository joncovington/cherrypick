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

## Advice ordering (2026-09-15)

Since this date the session's advice decision is derived and recorded BEFORE the ex-dividend gate,
so the advisor scores a refused week as advice that governed nothing rather than as an artifact that
never reached the loop.

## Console page (2026-08-17)

`/calendars` landed 2026-08-17; it reads the ledger directly for state and calls `cli.py` for
policies and the week plan (see CLAUDE.md for why).
