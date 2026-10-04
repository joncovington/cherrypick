# cherrypick-pmcc

> **Vocabulary.** This module's **book** is the suite's **arm** (root `CLAUDE.md`). The column
> (`pmcc_positions.book`) moves with the rest of the schema; prose and the console already say `arm`.

PMCC — deep-ITM covered calls on TQQQ and XSP (posture: root file). Buy an 85-90-delta call at
~21 DTE — a stock substitute, deliberately NOT a LEAP — and sell the ATM call nearest spot at ~7 DTE,
whichever side of spot it lands. Hold to the short's own expiration, close BOTH legs together,
re-enter. Ledger schema: **`pmcc`** (`pmcc_99` until the 2026-10-04 rename; a config still
naming it resolves through `core.ledgers.SCHEMA_ALIASES`). History and incidents: [docs/history.md](docs/history.md);
window sizing: [docs/window-parameters.md](docs/window-parameters.md). Suite-wide context: the root
[documentation index](../../docs/README.md).

## Measurement breaks — never pool across these

- **2026-08-23 redesign**: from 3 symbols/3 books (TNA/TQQQ/UPRO; `control`/`keltner`/`roll`) to one
  `control` book with a delta-band long, nearest-spot short and hold-to-expiry exit. Pre-redesign
  rows (including TNA/UPRO symbols and `keltner`/`roll` books) are history, not comparable
  measurement.
- **2026-08-23 XSP added** to the same `control` book (additive: TQQQ stays comparable across it).
  **Always read `pmcc` grouped by `symbol`, never pooled** — XSP differs in settlement, risk and
  fees, so the two are comparable only as separate populations under one rule set.
- **2026-08-28 entry spread gate**: `max_leg_spread_pct` is enforced at entry (`plan_entry`, as in
  curve and bwb) as well as at exit (`management.execution_gate`). Earlier entries were not
  spread-gated.

## The experiment design

- **`control`** — mechanical entry whenever the (symbol, book) slot is free, the 85-90-delta long,
  an ATM short with no yield floor, hold to the short's expiration, close both legs. Never rolls.
- **`advised:<experiment name>`** (paper, off by default) — mechanism in `packages/core/CLAUDE.md`,
  params frozen at entry and restated through `management.effective_params`. The one thing worth
  advising now is `tv_managed_exit`/`tv_close_threshold`: the old early-tv-exhaustion close as a
  paper A/B against hold-to-expiry. `control` is the only base a twin can name (`advice.base_book`);
  the pre-2026-09-17 tag `advised:control` and the retired `advised:keltner`/`advised:roll` (via
  config's `books` keys) still resolve for history. **Every twin is on the stream-request roster** —
  otherwise it is starved of deep strikes (see Data source).

## Evidence era

`pmcc_positions.era` (MEIC's `CURRENT_ERA` convention): `analytics.headline(conn)` scopes to
`analytics.CURRENT_ERA` by default; `era="ALL"` (CLI `pmcc headline --era ALL`) pools explicitly. The
console mirrors both as `readers/meic.ts` does. One era so far, **`"redesign"`** (2026-08-23 ->),
stamped by `book.enter_position`. Pre-column rows read `era = NULL`, which never equals a literal, so
the pre-redesign cycles are excluded by construction, not by a backfilled guess; they stay in the
ledger and the History tab. Journaled in `measurement_breaks` (`key='era'`). `worksheet()` is not
era-scoped.

## The honesty rules

1. **Net of the modelled fee and slippage stack** — commissions/clearing/ORF/TAF, the $5-per-ITM-
   symbol settlement/assignment event, SEC and FINRA pass-throughs on the share side of an
   assignment, the suite's slippage model. ETFs carry no index exchange fee; XSP is looked up on
   `cherrypick.core.fees.INDEX_EXCHANGE_FEE_PER_CONTRACT`.
2. **Early assignment is unmodelled but MEASURED, so TQQQ's paper result is an UPPER BOUND.** This
   module sells ITM calls by design. Every mark where the short's extrinsic is under
   `assignment_exposure_tv` is flagged `assignment_exposed` (`pmcc_marks`, `analytics.exposure`). Do
   not read TQQQ's net as achievable live without that exposure beside it. **XSP is exempt**
   (European, cannot be exercised early; `management.assignment_exposed` is always False) and its
   result is not an upper bound in this sense.
3. **Ex-dividend spans are refused, not modelled — physical symbols only.** A short leg spanning a
   declared ex-date refuses `ex_dividend_span`; a span the calendar cannot answer for refuses
   `dividend_calendar_lapsed` (a lapsed table stops entries loudly). Dates are hand-declared from the
   issuer's schedule (quarterly for TQQQ) and hand-refreshed. A missing table and "no dividend" must
   never look alike. Skipped entirely for XSP, which needs no `dividends` entry.
4. **Rules are declared up front, never tuned mid-experiment.** A removed rule keeps its negative
   result on the record.
5. **A hole in the mark path is refused, never zero** (`pmcc_marks.usable = 0` with the refusal;
   `pmcc_snapshots` is the feed ledger).
6. **Changing tick cadence is a journaled measurement break.**
7. **A degraded long selection stays excludable.** Missing deep-strike greeks → selection on the
   extrinsic bound alone, `long_selected_by = 'extrinsic'`, logged as a warning naming
   symbol/book/strike. `allow_extrinsic_fallback` (default true) set false refuses instead, to test
   whether the fallback shapes results.
8. **A full book is recorded, not silent.** When every slot is held the entry phase records
   `slot_held`, collapsed per (day, book, symbol) — otherwise "all slots full" (the ordinary state
   for weeks at a time) and "never evaluated entry" look identical.

## Data source and the deep window

Its two expirations come from the `expirations` request field; its **deep strikes** come from
`window_hints`, because an 85-90-delta long sits well below spot, outside any default ATM window.
`stream_window.py` computes the width the chain needs and escalates on
`no_deep_itm_long`/`missing_leg_quotes` refusals.

- **The widened window is gated on whether ANY book can still enter, advised twins included**: the
  request asks `paper_loop.session_books`, the roster the entry phase uses. Asking only about
  `control` once starved a twin for two sessions (658 refusals) — an A/B whose arms cannot enter on
  the same days is not an A/B. Once EVERY book holds the symbol, the window is pure cost and is dropped.
- **The hint is declared DOWNWARD only** (`hints_for_symbols` → `{"down": width, "up": margin}`):
  the long is 15-19% ITM on TQQQ and the short is ATM. The producer floors both sides at its own
  `window_strike_count`, so the short is always covered. Open legs stay subscribed via `leg_sources`,
  so window risk is confined to entry pricing and shows as refusals, never bad fills.
- The provider refuses rather than guesses. The OCC-root filter admits only the configured root
  (a post-split root like `TQQQ1` shares dates). A split mid-position is unmodelled — manual
  `--settle` plus a measurement break. XSP does not split.
- Deep-ITM spreads may trip `max_leg_spread_pct`; recalibration is a config change plus a break.

## Physical settlement (TQQQ only)

The calendars decomposition verbatim: an ITM short at expiry books its intrinsic AND delivers
**short 100 shares per contract at the settlement spot** (`pmcc_assignments`); the ~14-DTE long stays
open; the next session's combined disposal covers the shares and sells the long at its mark. A
position does **not** close while shares are outstanding (`finalize_if_done`), and the
Friday-to-Monday gap stays visible — it IS the weekend exposure. `tests/test_engine.py` asserts the
cash-flow equivalence. For XSP (`settlement_style.XSP = "cash"`), `book.settle_expiring_legs` calls
`engine.assignment_from` only in its `if physical:` branch, so an ITM leg books intrinsic and
finalizes — no shares, no disposal, no weekend carry. Nothing special-cases XSP's multiplier or
strike increments; keep an eye that standard contract mechanics stay accurate.

## Live-trading prerequisites (none built)

Live-pilot-*shaped* but **no live path** — no order code, no keyring. Before any live rung:
(a) **post-assignment management** (detecting a surprise early assignment, a defined cover/repair
procedure — live assignment cannot be excluded by skipping ex-div spans); (b) a **calculated ex-div
decision** priced as expected assignment cost; (c) the exposure telemetry (rule 2) read as the
paper-to-live gap. Prerequisites, not enhancements. **`live.enabled` is an inert placeholder**: it
shows "paper only" on config/console surfaces (`readModuleGate`/`liveops._live_enabled`), nothing
checks it, it is `configedit.GUARDED`, and flipping it by hand does nothing.

## Layout

| file | role |
|---|---|
| `src/cherrypick/pmcc/clock.py` | ET clock + expiration plan (~7DTE short / ~21DTE long Fridays, holiday-shifted). Pure. |
| `src/cherrypick/pmcc/engine.py` | leg selection, worksheet math, settlement decomposition, fee stack. Pure. |
| `src/cherrypick/pmcc/provider.py` | read-only cache snapshots, one-sided DEEP quote window, refuses rather than guesses. |
| `src/cherrypick/pmcc/management.py` | control verdict (or advised tv exit) + execution gate + choke point + exposure flag. Pure. |
| `src/cherrypick/pmcc/book.py` | decisions → ledger rows: entries, closes, settlement, share disposal. |
| `src/cherrypick/pmcc/paper_loop.py` | mark, manage, enter, dispose, settle at the bell. |
| `src/cherrypick/pmcc/analytics.py` | the one read-only query layer. |
| `src/cherrypick/pmcc/db.py` | schema, additive migrations, stale-writer guard, writers. |
| `src/cherrypick/pmcc/stream_request.py` | declares symbols, open legs, expirations, window hints. |
| `src/cherrypick/pmcc/stream_window.py` | deep-window width: computed from the chain, escalated on misses. |
| `src/cherrypick/pmcc/cli.py` | `status` / `headline` / `worksheet` / `exposure`. |

## Commands

```bash
python -m cherrypick.pmcc.paper_loop --once        # one gated tick (what the supervisor spawns off-session)
python -m cherrypick.pmcc.paper_loop --interval 60 # the in-session resident loop
python -m cherrypick.pmcc.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.pmcc.paper_loop --settle --date 2026-08-28 --price 71.23  # official print
python run.py status                               # open positions + the expiration plan
python run.py worksheet                            # the live per-position worksheet
python run.py exposure                             # the early-assignment-exposure telemetry
python -m pytest                                   # temp CHERRYPICK_HOME; no broker, no streamer needed
ruff check . && ruff format .                      # line-length 110
```

Config: `config.example.json` → `config.json` (git-ignored) or `~/.cherrypick/config/pmcc.json`; the
example is the design document — read its `_note` keys first.

## Guardrails and couplings

- **Paper only; no live path** (see prerequisites).
- **Deterministic decision path**: `engine.py` and `management.py` are pure over pre-fetched data.
- Declared settlement only (`settlement_style`); a symbol declared as neither is refused.
- **Orchestrator coupling — don't change silently:** the paper DB path
  (`~/.cherrypick/data/pmcc/paper_trades.db`, also load-bearing for review and the advisor fact pack)
  and its `pmcc` schema, read through `cherrypick.core.ledgers`.
- **The console's `/pmcc` page mirrors `analytics.py` in TypeScript** rather than sharing it; the two
  are cross-checked by hand against `python run.py headline`. It shows state first, a
  measurement-integrity strip carrying rule 2's exposure bound above any P&L, the dividend calendar's
  refresh state, and full cycle history.
- Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`), never opt-in.
