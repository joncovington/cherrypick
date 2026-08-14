# cherrypick-calendars

Weekly SPX double calendars — a **paper-only forward exit-parameter experiment**. Every Monday
(Tuesday after a Monday holiday) at 10:00 ET: one put calendar at the expected-move-down strike, one
call calendar at the expected-move-up strike; short legs expiring that week's Friday, long legs the
following Monday. The entry is deliberately unconditional and mechanical — the module exists to
answer one question honestly: **which exit rule makes this structure worth anything, net of costs?**

Suite-wide context is the root [documentation index](../../docs/README.md).

## The experiment design

One plan, N books. Every book's positions for a week are written from the SAME entry plan —
identical strikes, identical mids, identical modeled costs — so the comparison is exactly paired by
construction and any divergence between books is exit policy and nothing else.

- **`control`** (user-defined baseline): sell every leg in the Friday exit window. No stops, no
  targets, no weekend hold.
- **`path`** (permissive superset — MEIC's `open` arm precedent): never closes. Shorts run to
  Friday cash settlement, longs ride the weekend and are sold on their own Monday expiration
  morning. Its job is the **recorded per-tick mark path** (`dc_marks`), the substrate everything
  else derives from.
- **`advised:control`** (paper, off by default): the AI advisor's admitted exit params, frozen on
  each row at entry and restated through one choke point (`management.effective_params`) at every
  later tick — the earnings advised-twin pattern, required here because this module has exits.

**The exit grid is derived read-side, not run as books.** `exit_policies.py` replays profit targets
(10/20/30% of debit), stops (25/50/100%), the short-strike-touch side close, exit-timing variants
(Thursday close, Friday noon, Friday close) and both long dispositions over the path book's recorded
marks — an exact tick-by-tick replay at recorded prices through the same cost stack the live books
use. One permissive arm answers the whole grid, which is why the grid is not fifteen books.

**The derivation is validated against reality every time it runs.** `validate_against_control`
re-derives the `control` policy from the control book's own marks and compares to that book's real
recorded net (they must agree to the cent — same ticks, same mids, same cost model), and
`expiry-longs-mon` against the path book's real net. A derivation that cannot reproduce the books it
sits beside has no business ranking the policies between them.

**One deliberate exemption from the one-variable arm rule:** `control` and `path` differ in short
AND long handling at once. That is the permissive-superset design, not an oversight — the
single-variable questions are answered by the read-side grid, not by pairing these two books. Do not
"fix" it by adding intermediate books.

## Layout

| file | role |
|---|---|
| `src/cherrypick/calendars/clock.py` | ET clock + the week anchors: entry session, front/back expirations, holiday shifts, structure tags. Pure. |
| `src/cherrypick/calendars/engine.py` | EM targeting, strike **intersection**, structure math, the SPX fee stack. Pure. |
| `src/cherrypick/calendars/provider.py` | snapshots from the shared stream cache, read-only, refuses rather than guesses. |
| `src/cherrypick/calendars/management.py` | per-book verdicts + the execution gate + the advised-params choke point. Pure. |
| `src/cherrypick/calendars/book.py` | engine decisions → ledger rows: entries, traded closes, cash settlement. |
| `src/cherrypick/calendars/paper_loop.py` | the session driver: mark, manage, enter on the entry day, settle at the bell. |
| `src/cherrypick/calendars/exit_policies.py` | the read-side derivation and its validation — the module's point. |
| `src/cherrypick/calendars/analytics.py` | the one query layer every read surface goes through. Read-only. |
| `src/cherrypick/calendars/db.py` | schema, additive migrations, the stale-writer guard, every writer. |
| `src/cherrypick/calendars/stream_request.py` | declares symbols, open legs, and the two expirations to the streamer. |
| `src/cherrypick/calendars/cli.py` | `status` / `headline` / `policies` / `validate`. |

## Commands

```bash
python -m cherrypick.calendars.paper_loop --once        # one gated tick (what the supervisor spawns off-session)
python -m cherrypick.calendars.paper_loop --interval 30 # the in-session resident loop
python -m cherrypick.calendars.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.calendars.paper_loop --settle --date 2026-08-21 --price 6543.21  # official print
python run.py status                                    # open positions + the current week plan
python run.py policies                                  # the derived exit-policy table + its validation
python run.py validate                                  # the validation alone
python -m pytest                                        # temp CHERRYPICK_HOME; no broker, no streamer needed
ruff check . && ruff format .                           # line-length 110
```

Config: copy `config.example.json` → `config.json` (git-ignored), or place
`~/.cherrypick/config/calendars.json`. The example file is the design document — read its `_note`
keys before changing a value.

## The honesty rules

1. **Every result is net of the modeled fee and slippage stack** — the SPX index exchange fee, the
   $5-per-ITM-symbol cash-settlement fee, and the suite's slippage model included. Gross is not a
   result.
2. **Exit rules are declared up front and measured, never tuned mid-experiment.** A removed rule
   keeps its negative result on the record (the flies pre-close-ITM-exit discipline).
3. **A hole in the mark path is `derivable: False`, never zero.** "Not recorded" and "was zero" are
   different facts, here and in every ledger column.
4. **Structure tags never pool.** A Tuesday-entry `dc_3_6` and a holiday-Monday `dc_4_8` are
   different trades from `dc_4_7`; every read surface groups by the tag.
5. **Changing the tick cadence is a journaled measurement break** — the mark path's resolution
   bounds how precisely a derived trigger replays, so pre/post derivations are not comparable.
6. **A refused mark is still a row.** A stalled feed and a quiet market must never look identical
   in the record (`dc_marks.usable = 0` with the refusal; `dc_snapshots` for the feed ledger).
7. **The policy table travels with its validation.** No surface shows the ranking without the
   reason to believe it.

## Data source and the two expirations

This module runs no streamer and holds **no broker credentials at all** — the paper path is a pure
read-only consumer of the suite's shared stream cache. Its 4DTE/7DTE chains exist in that cache
because `stream_request.py` declares them through the registry's `expirations` field every tick
(computed from the calendar: the next entry's Friday and following Monday, plus any expiration still
held open). The producer serves each date its own chain rows and ATM quote window; see
`cherrypick.core.streamer`. The request is derived from dates only, so it changes exactly at an ET
date boundary — never a mid-session subscription change.

The provider refuses rather than guesses: stale or crossed quotes, a missing chain, a spot that
won't print — each is a recorded refusal the loop steps past, not an error. On a third-Friday week
the OCC-root filter admits only the PM-settled weekly (`SPXW`); if none is listed the week is
skipped and journaled (`not_weekly_listed`), never traded on the AM-settled monthly.

## Guardrails (suite-wide)

- **Paper only. There is no live path** — no `enable_live_trading`, no live loop, no order code.
- **No AI, no MCP, no network on any decision path.** `engine.py` and `management.py` are pure
  functions over pre-fetched snapshots.
- **Cash-settled symbols only** (`cash_settled_symbols`): the settlement model is European
  intrinsic-at-the-bell. An American-style ETF (QQQ/IWM) is refused at entry until early-exercise
  handling exists — that expansion is a code change, not a config edit.
- **Two couplings the orchestrator depends on — don't change silently:** the paper DB path
  (`~/.cherrypick/data/calendars/paper_trades.db`, also load-bearing for review and the advisor
  fact pack) and its `dc_week` schema, read through `cherrypick.core.ledgers`.
- Credentials in the OS keyring only (this module holds none). Account numbers masked to
  `****1234`. Portable paths only; scratch work in `.tmp/`. Human-voice docs and commits, no AI
  attribution. Instruction files hold no code.
- Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`), never opt-in — the flies
  2026-07-20 lesson.

## Status

Complete and tested: clock/week anchors, entry engine, both books, marking, management, settlement,
disposition, the exit-policy derivation with its validation, analytics, and the suite wiring
(`dc_week` across every registry, enforced by the orchestrator's schema-coverage test). Paper data
collection starts with its first scheduled Monday; the policy table is empty until completed weeks
exist, and underpowered until many do. The console renders this module through the generic Review
page; a dedicated page is deliberately deferred until real weeks accumulate.
