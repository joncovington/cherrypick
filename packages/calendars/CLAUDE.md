# cherrypick-calendars

> **Vocabulary.** This module's **book** is the suite's **arm** (root `CLAUDE.md`). The column
> (`dc_positions.book`) moves with the rest of the schema; prose and the console already say `arm`.

Weekly SPY double calendars, a paper-only forward exit-parameter experiment (posture: root file).
Every Monday (Tuesday after a Monday holiday) at 10:00 ET: a put calendar at the expected-move-down
strike and a call calendar at the expected-move-up strike, shorts expiring that Friday, longs the
following Monday. Entry is deliberately unconditional; the one question is **which exit rule makes
this structure worth anything, net of costs?** The underlying was SPX until 2026-08-15 (why SPY, not
XSP: [docs/history.md](docs/history.md)). Suite-wide context: the root
[documentation index](../../docs/README.md).

## The experiment design

One plan, N books: every book's positions for a week come from the SAME entry plan — identical
strikes, mids and modelled costs — so any divergence is exit policy and nothing else.

- **`control`**: sell every leg in the Friday exit window. No stops, targets or weekend hold.
- **`path`** (permissive superset, MEIC's `open` precedent): never closes; shorts run to Friday
  settlement, longs ride the weekend and sell on their Monday expiration morning. Its job is the
  recorded per-tick mark path (`dc_marks`) everything else derives from.
- **`advised:<experiment name>`** (paper, off by default): mechanism in `packages/core/CLAUDE.md`;
  required here because this module has exits. Pre-2026-09-17 tags `advised:control` /
  `advised:friday:control` still resolve by name in `engine.base_book` and
  `management.effective_params`. Open books keep being marked, managed and settled under the params
  frozen on their own rows, whatever today's decision says.

**`control` and `path` differ in short AND long handling at once — a deliberate exemption** from
the one-variable rule. Single-variable questions are answered by the read-side grid. Do not "fix" it
with intermediate books.

**The exit grid is derived read-side, not run as books.** `exit_policies.py` replays profit targets
(10/20/30% of debit), stops (25/50/100%), the short-strike-touch side close, exit timings (Thursday
close, Friday noon, Friday close) and both long dispositions over `path`'s recorded marks, tick by
tick at recorded prices through the live cost stack.

**Validated against reality every run.** `validate_against_control` re-derives `control` from its
own marks and must match its real recorded net **to the cent**; `expiry-longs-mon` likewise against
`path`. A derivation that cannot reproduce the books beside it has no business ranking policies.

**A Friday-entry regime is designed, not built** — [docs/friday-entry-arm.md](docs/friday-entry-arm.md)
(`dc_7_10` vs this `dc_4_7`). It is NOT a fourth book: it prices its own snapshot and strikes, so it
is a parallel regime with its own books and population, and an ordering problem on a Friday that
already carries the exit, settlement and share delivery.

## Two settlement styles, one set of numbers

`settlement_style` per underlying: **`cash`** (SPX, XSP) is European intrinsic at the bell;
**`physical`** (SPY) is American delivery — an ITM short hands over 100 shares per contract, held
until the next session's disposal, so a Friday short carries stock across the **weekend**. A symbol
declared as neither is refused at entry (`unknown_settlement`). Adding a style is a code change, not
a config edit; `cash_settled_symbols` is the pre-SPY spelling and still reads.

**Delivered shares are booked at the settlement spot, not the strike.** For a short put at K,
credit E, settlement spot S_f, disposal S_m:

| | |
|---|---|
| option leg | `E − (K − S_f)` — the existing intrinsic accounting, untouched |
| share leg | `S_m − S_f` — long shares, basis S_f |
| total | `E − K + S_m` — the true cash flow: take E, buy at K, sell at S_m |

Basing shares at K would double-count. Physical is cash **plus** a share leg; cash is the case where
the share term is zero — so one settlement path, derivation and validation serve both.
`tests/test_engine.py` asserts it against raw cash flow for both sides. A week does **not** close
while shares are outstanding (`finalize_if_done` treats them as an open leg), and an undisposed
share position makes an expiry policy `derivable: False`, never zero.

**Ex-dividend weeks are excluded, not modelled.** Entry refuses the whole week when a declared
ex-date falls in `[entry_session, back_expiration]` — the full span, because Friday-delivered shares
ride the weekend. Dates live in config's `dividends` block, declared from the issuer's schedule and
**refreshed annually by hand**: they cannot be computed and cannot be fetched (no network on a loop
path). A week past `declared_through` refuses too (`dividend_calendar_lapsed`) — a missing table and
"no dividend" must never look alike. The session's advice decision is recorded BEFORE this gate.
**The skips bias the sample deliberately**: ~four weeks a year, exactly the quarterly-expiration
weeks, so the policy table covers ordinary weeks only.

**Weighed and left unmodelled: other assignment drivers** (interest-carry exercise of deep-ITM
puts, random assignment). The P&L transfer is the abandoned extrinsic (pennies, in our favour); a
same-strike calendar's deep-ITM short is hedged by an equally-ITM long; and the timing error runs
conservative (a Thursday assignment would dispose Friday with no weekend hold, while the model books
one).

**`capital` is not the whole risk story for `path`.** `cherrypick.core.ledgers` reports `dc_week`
capital as `entry_debit × 100 × quantity` — right for `control` and every policy exiting before the
bell, but delivered shares' weekend move is not bounded by the debit. Read a `path` or `expiry-*`
drawdown as including it.

## The honesty rules

1. **Net of the modelled fee and slippage stack** — per-symbol index exchange fee (SPX
   $0.60/contract; SPY none), the $5-per-ITM-symbol settlement/assignment event, SEC and FINRA
   pass-throughs on share disposal, and the suite's slippage model.
2. **Exit rules are declared up front, never tuned mid-experiment**; a removed rule keeps its
   negative result on the record.
3. **A hole in the mark path is `derivable: False`, never zero.**
4. **Structure tags never pool.** A Tuesday-entry `dc_3_6` or holiday `dc_4_8` is a different trade
   from `dc_4_7`; every read surface groups by tag.
5. **Changing the tick cadence is a journaled measurement break** — it bounds replay precision.
6. **A refused mark is still a row** (`dc_marks.usable = 0` with the refusal; `dc_snapshots` is the
   feed ledger).
7. **The policy table travels with its validation.** No surface shows the ranking without it.
8. **A spread is judged in money as well as percent, per leg.** The exit gate refuses a leg only when
   wide on BOTH `max_leg_spread_pct` and `max_leg_spread_abs`: a near-worthless short at
   `0.00/0.01` is a one-cent buyback and a 200% ratio. curve exempts the short leg from this at
   ENTRY (its premium is the whole credit); on exit every leg is closing and a penny is a penny.
   Journaled `exit_gate_absolute_spread_floor`; earlier weeks report under `pre_break` in `validate`,
   not as mismatches.

## Data source and liveness

4DTE/7DTE chains come from the `expirations` request field, computed from the calendar (the next
entry's Friday and following Monday, plus any expiration still held), so the request changes only at
an ET date boundary — never mid-session. The provider refuses rather than guesses. On a third-Friday
week the OCC-root filter admits only the PM-settled weekly; if none is listed the week is skipped and
journaled (`not_weekly_listed`), never traded on the AM monthly.

**Liveness is published, not inferred.** The loop touches `state/calendars.heartbeat` at the **top
of every tick, before any gate**, and the supervisor measures silence against it. **Never make this
loop's log carry reliability meaning again**: every log line is event-driven, and supervising on it
once had the loop restarted every two minutes, losing up to 61% of a session's ticks (history doc).

## Live-trading prerequisites (user directive 2026-08-16)

Skipping ex-div weeks is a paper simplification only. Any live path MUST first have (a)
**post-assignment management** — detecting surprise/early assignment and a defined disposal/repair
procedure, since live assignment cannot be excluded by skipping weeks — and (b) a **calculated
ex-div decision** priced as expected assignment cost against the week's edge, never a default.
Prerequisites, not enhancements. **`live.enabled` is an inert placeholder**: it lets config/console
surfaces show "paper only" (`readModuleGate`/`liveops._live_enabled`), nothing checks it, and it is
`configedit.GUARDED`; flipping it by hand does nothing.

## Layout

| file | role |
|---|---|
| `src/cherrypick/calendars/clock.py` | ET clock + week anchors, holiday shifts, structure tags. Pure. |
| `src/cherrypick/calendars/engine.py` | EM targeting, strike **intersection**, structure math, settlement decomposition, fee stack. Pure. |
| `src/cherrypick/calendars/provider.py` | snapshots from the stream cache, read-only, refuses rather than guesses. |
| `src/cherrypick/calendars/management.py` | per-book verdicts + execution gate + advised-params choke point. Pure. |
| `src/cherrypick/calendars/book.py` | decisions → ledger rows: entries, closes, settlement, share disposal. |
| `src/cherrypick/calendars/paper_loop.py` | mark, manage, enter on the entry day, settle at the bell. |
| `src/cherrypick/calendars/exit_policies.py` | the read-side derivation and its validation — the module's point. |
| `src/cherrypick/calendars/analytics.py` | the one read-only query layer. |
| `src/cherrypick/calendars/db.py` | schema, additive migrations, stale-writer guard, writers. |
| `src/cherrypick/calendars/stream_request.py` | declares symbols, open legs and the two expirations. |
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
python run.py record-break --key K --date D [--old X] [--new Y] [--note N]  # journal a break
python -m pytest                                        # temp CHERRYPICK_HOME; no broker, no streamer needed
ruff check . && ruff format .                           # line-length 110
```

Config: `config.example.json` → `config.json` (git-ignored) or `~/.cherrypick/config/calendars.json`;
the example is the design document — read its `_note` keys first.

## Guardrails and couplings

- **Paper only; no live path**, no order code (see prerequisites above).
- **Deterministic decision path**: `engine.py` and `management.py` are pure over pre-fetched
  snapshots — a policy table is only worth its reproducibility.
- **Orchestrator couplings — don't change silently:** the paper DB path
  (`~/.cherrypick/data/calendars/paper_trades.db`, also load-bearing for review and the advisor fact
  pack) and its `dc_week` schema, read through `cherrypick.core.ledgers` (coverage enforced by the
  orchestrator's schema-coverage test).
- **The console's `/calendars` page must not re-implement `exit_policies` or `clock.week_plan`**; it
  calls `cli.py`'s `policies` and `status` as a subprocess. A second derivation could drift exactly
  where the validation cannot see; a second holiday calendar could disagree with the one traded.
  **Keep `cli.py`'s JSON shape stable**, or the page degrades to an error banner.
- Scratch work in `.tmp/`. Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`),
  never opt-in.

## Status

Complete and tested end to end. The policy table is empty until completed weeks exist and
underpowered until many do. The first scheduled Monday (2026-08-17) took no position
(`no_fresh_quotes`, journaled `week_skipped_entry_window_exhausted`) — correct behaviour on a stale
cache, and worth watching on entry Mondays.
