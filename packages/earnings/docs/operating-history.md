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

## `expected_move_butterfly` added to `strat_test` from 2026-10-14 (declared 2026-10-13)

Built paper-only after `scripts/emfly_history.py` said not to build it
([expected-move-fly-plan.md](expected-move-fly-plan.md)), to measure real fills against the
study's cost model. It is dated in code the same way as the retirements below:
`strat_test_harness.STRAT_TEST_STARTS` maps it to 2026-10-14. Before that session its screen row
is written and the opening is refused as an execution-stage `strategy_not_started` drop.

- **Off the boundary, by choice.** It was declared for the 2026-10-19 boundary, then started on
  2026-10-14 at the operator's request. A new book changes no other book's numbers, but the set
  of strategies is a measurement key, so it has its own `strat_test_strategies` break dated
  2026-10-14, and the boundary's break (moved to 2026-10-15) covers only the calendar retirements.

## The two calendars retired from `strat_test` from 2026-10-15 (declared 2026-10-12; moved from 10-19 on 10-14)

The config's own 20-session review said to retire `atm_calendar` and `double_calendar` from the
harness if they were losing. Through 10-12 `atm_calendar` ran 56 trades at 21% won for −$6,349 and
`double_calendar` 35 at 14% for −$3,660.

- **How:** the registry has no per-strategy switch, and a deleted `strategies.<name>` block would
  raise rather than disable, so the retirement is dated in code: `strat_test_harness.STRAT_TEST_RETIRED`
  maps each to 2026-10-15. From that session the harness still writes the screen row and refuses the
  opening as an execution-stage `strategy_retired` drop, so "retired" never reads as "nothing
  qualified". It merges ahead of the boundary and binds on it, with no config change.
- **Open rows run off.** Six `atm_calendar` rows and one `double_calendar` row opened 10-12 (front
  expiry 10-16) close under the ordinary close path, which reads the ledger, not the registry.
- **The advised twin.** `advised:dc-stop-at-75pct-debit` (1 of 15 sessions) opens only beside a
  `strat_test` double calendar, so it would starve rather than conclude. It is concluded with a
  verdict by `advisor kill` from `scripts/boundary_2026_10_19.py` (the script kept its file name when
  the boundary moved); its open UNH twin keeps its frozen params to the close.
- `strat_test_strategies` break 2026-10-15 in the paper ledger, written by the same script.

## Positions closed on their entry tick (2026-09-16..10-15, fixed 2026-10-17)

The 15:35 entry scan opens positions inside the 09:40–15:40 act window. When it finished early, the
next tick evaluated the new positions as if the report had passed:

- **The calendars:** the front-DTE stop fired at once (the weekly is under five days out at entry).
- **The overnight structures:** that tick was their first check of the day, and a position that has
  just paid the spread marks at or below zero, so the PEAD gate closed it as a loser.

**23 rows**, 1 second to 3 minutes after opening, about −$821 net, by strategy:

| Strategy | Rows | Exit reason |
|---|---|---|
| `iron_fly` | 9 | `pead_loser` |
| `atm_calendar` | 7 | `time_exit` |
| `double_calendar` | 3 | `time_exit` |
| `iron_condor` | 2 | `pead_loser` |

They saw no event and measured only the round-trip spread. Found while scoring the retired calendars
for the time-spread plan (docs/time-spread-screen-plan.md, step 0), which excludes them.

- **The fix:** `management.evaluate` holds every position on its entry session (`entry_session`),
  behind only the expiry close and the pin guard.
- **Why it lands at once:** it is a recording fix, not a measurement change, since the rows were
  wrong.
- **The rows stay in the ledger.** A reader should drop `closed_at − opened_at < 600` when reading
  these strategies across that window.
