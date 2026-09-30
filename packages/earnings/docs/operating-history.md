# Earnings — operating history

Incident narratives moved out of `packages/earnings/CLAUDE.md`, which keeps the rule each produced.
Exit rules and the settlement backfill are in [10-exits.md](10-exits.md); screening rules in
[screening-criteria.md](screening-criteria.md).

## The forward scan logged nothing (to 2026-08-25)

When entry and exit moved into the managed loop at the 2026-08-12 cutover, nothing took over the
retired scheduled verbs' run log; it simply stopped, and "did earnings run today, and what did it
decide" stopped being answerable from the logs while the loop ran fine. The forward scan — the top
of the funnel — was the one phase that logged nothing, so eleven starved sessions stayed invisible:
the aged-out Dolt calendar left it finding nothing every morning, while the only trace was the entry
phase's `opened: []`, which reads exactly like "screened, none cleared".

## The forward scan ran unbounded (to 2026-08-25)

Only survivable because a stale Dolt clone left it an empty calendar. Its first real pass took 13
minutes over 22 symbols, holding the loop's single-writer lock throughout. The entry scan costs
~35s + ~8s per symbol, so a heavy night at the old 15:45 start risked finishing past the 15:55
window; it moved to 15:35.

## Expired positions stranded (2026-08-31)

Retiring the next-morning sweep removed the only thing resolving expiries. An expired contract keeps
quoting a zero bid against a stale ask, which marked as usable with a 200% spread, so the engine
decided to close and `spread_too_wide` refused — every tick, forever. The first expiration under the
managed lifecycle stranded 59 positions across all six strategies.

## The entry-calendar admissibility gate (removed 2026-09-02)

Requiring an exact timing string with no fallback had collapsed the nightly universe to one or two
names and dropped liquid ones on their own earnings day. The replacement gated blank-timing rows on
membership in the morning snapshot, and produced its own 2026-08-17..24 five-session outage when the
snapshot came back empty and the gate read that as "admit nothing" (break
`entry_calendar_admissibility_gate_removed`).

## Advised twins unmanaged (2026-08-26..31)

`paper_loop.managed_book` asked the bare `_is_strat_test_book`, which answers no for
`advised:strat_test:iron_condor`. Every twin — 13 of them, against 4,953 marks on the controls
beside them — went unmarked, unevaluated and unclosed while the advice choke point worked as designed
and was never called.

## `symbol-watch` described two ways (to 2026-08-20)

This file described the orchestrator's `symbol-watch` job both as a feature waiting to be switched
on and as a constraint. It is a constraint.

## `scan_log` acceptance "gap"

An earlier note claimed a 2,349-vs-64 acceptance gap; that was a miscount — 2,238 of those are
legacy capital-R `Reject` rows, i.e. rejections. Real acceptances reconcile with trades opened, so
the execution stage records a gap that is currently small, not a hole.
