# Streamer history

Dated incidents behind the rules in [`../CLAUDE.md`](../CLAUDE.md). The rules live there; this file
keeps the story of how each was found.

## Directional window hints (2026-08-24)

pmcc's deep-ITM long sits far below spot while its short sits at it. A symmetric strike count bought an
identical block of strikes above spot that no module could read, and on 2026-08-24 that block was the
largest single waste in the suite's subscription budget (`docs/streamer-subscription-budget.md` at the
repo root). Window hints became `(below, above)` spans, unioned per side.

## Summary closes erased by a later event (fixed 2026-08-27)

The `stream_summary` upsert did not COALESCE OHLC fields until 2026-08-27, and the cost was 22
consecutive sessions of SPX and XSP closes: both kept receiving Summary events until ~20:07 ET with
`day_close_price` cleared, and the bare overwrite copied that null over the settled value.
`daily_closes` — the suite's only multi-year series — froze at 2026-07-28 for SPX while every other
symbol stayed current, because symbols whose last event of the day landed earlier (VIX 10:08, SPY
16:15) never met the clearing event.

## Quote/Greeks publish filter (2026-08-24 probe)

The 2026-08-24 entitlement probe showed SKEW, VIX9D, VIX and VIX1D all printing Trade and none
printing Quote. The quoteless set was made a declared list rather than a ticker pattern because a leg
with no price at all is the expensive failure here (2026-08-14 Summary, 2026-08-17 Trade).

## The dead 0DTE chain (2026-09-10)

The nightly DXLink drop reconnected in-process at 23:58 ET on the 9th; the chain fetch picked the 9th
itself (nearest by distance, expired eight hours earlier); the process survived the night, and the base
SPX window served that dead chain all session while every aggregate stayed fresh — the SPX index
ticked, a 1DTE extra window ticked, no chain fetch error, no dead underlying. The flies module refused
all 4,662 entries as `no_0dte_expiration`; MEIC traded normally because it fetches its own chain by
REST. The fix is the session-date selection, date-roll refetch and `stale_chains` status described in
the invariants.
