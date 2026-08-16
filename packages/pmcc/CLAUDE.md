# cherrypick-pmcc

PMCC-99 — deep-ITM covered calls on leveraged ETFs (TNA, TQQQ, UPRO), **paper-only and
credential-free**: a pure stream-cache consumer in the calendars posture. Buy the deepest ~99-delta
call at ~21 DTE — a stock substitute with near-zero extrinsic, deliberately NOT a LEAP — and sell an
ITM call at ~9 DTE. The short's intrinsic is the downside buffer; its time value is the entire
profit. When that time value is exhausted (~$0.10), close BOTH legs together and re-enter; never
roll (except the book built to measure rolling). Ledger schema: **`pmcc_99`**.

Suite-wide context is the root [documentation index](../../docs/README.md).

## The experiment design

Three books, one variable each, plus the advisor's synthetic twin:

- **`control`** — the strategy as taught: mechanical entry whenever the (symbol, book) slot is
  free, the deepest ITM short whose net time value clears `target_weekly_yield_min` per week on the
  net debit (max protection subject to yield), close both legs at `tv_close_threshold`, hold like a
  covered call on a breach, never roll.
- **`keltner`** — control's management exactly; only the **entry** differs: spot within
  0.5×ATR of the Keltner midline (20-EMA of daily closes) AND above yesterday's close AND bounced
  ≥ 0.25×ATR off today's low (the user's pullback-and-reversal spec, 2026-08-16). It refuses
  entries (`insufficient_bar_history`) until ~21 completed daily bars exist — **the refusal is the
  honest state, not a failure** — and in practice that history arrives in one pass: the stream
  request's `history_days` field has the producer backfill the daily series from DXLink candles
  (2026-08-16), so the cold start only persists if the backfill has nothing to serve. Every book's
  rows carry the keltner measures, so the filter's counterfactual stays readable from control.
- **`roll`** — control's entry exactly; only the **breach handling** differs: it rolls the short
  down/out (once per position per session, same yield search at the current spot, never past the
  long's expiration) instead of holding, and closes (`roll_exhausted`) once the long is under
  `min_long_dte_for_roll` days.
- **`advised:<base>`** (paper, off by default) — the AI advisor's admitted params, frozen on each
  row at entry and restated through one choke point (`management.effective_params`) every tick.

**Pairing is partial, and deliberately so.** `control` and `roll` enter from the SAME plan on the
same tick — identical strikes, mids, and modeled costs — so roll-vs-hold is exactly paired by
construction. `keltner` enters on its own tick when its gate passes, because its variable IS the
entry tick. Read surfaces must not treat the three as a fully paired grid.

## The honesty rules

1. **Every result is net of the modeled fee and slippage stack** — commissions/clearing/ORF/TAF,
   the $5-per-ITM-symbol settlement/assignment event, the SEC and FINRA pass-throughs on the share
   side of an assignment, and the suite's slippage model. ETFs carry no index exchange fee. Gross
   is not a result.
2. **Early assignment is unmodelled but MEASURED, and the paper result is therefore an UPPER
   BOUND.** A real short ITM call with near-zero extrinsic can be assigned any day; this module
   sells ITM calls by design. Every mark where the short's extrinsic sits under
   `assignment_exposure_tv` is flagged `assignment_exposed` (`pmcc_marks`, aggregated by
   `analytics.exposure`), which bounds what the unmodelled mechanism could have touched. Do not
   read the books' net as achievable live until that exposure is read beside it.
3. **Ex-dividend spans are refused, not modelled.** An entry (or roll) whose short leg spans a
   declared ex-date is refused (`ex_dividend_span`); a span the declared calendar cannot answer for
   is refused too (`dividend_calendar_lapsed`) — a lapsed table stops entries loudly, by design.
   The dates are hand-declared from the issuers' schedules (Direxion, ProShares; quarterly) and
   refreshed by hand. A missing table and "no dividend" must never look alike.
4. **Rules are declared up front and measured, never tuned mid-experiment.** The roll book's
   parameters, the yield floor, the tv threshold — all stated in config before data accumulates. A
   removed rule keeps its negative result on the record.
5. **A hole in the mark path is refused, never zero.** A refused mark is still a row
   (`pmcc_marks.usable = 0` with the refusal); `pmcc_snapshots` is the feed ledger; a stalled feed
   and a quiet market must never look identical in the record.
6. **Changing the tick cadence is a journaled measurement break** — the mark path's resolution
   decides how finely the tv-exhaustion trigger and the exposure telemetry sample.
7. **A degraded long selection stays excludable.** When deep-strike greeks are missing, the long is
   selected on the extrinsic bound alone and the row records `long_selected_by = 'extrinsic'`.

## Two couplings the orchestrator depends on — don't change silently

The paper DB path (`~/.cherrypick/data/pmcc/paper_trades.db`, also load-bearing for review and the
advisor fact pack) and its `pmcc_99` schema, read through `cherrypick.core.ledgers`.

## Layout

| file | role |
|---|---|
| `src/cherrypick/pmcc/clock.py` | ET clock + the expiration plan (~9DTE short / ~21DTE long Fridays, holiday-shifted). Pure. |
| `src/cherrypick/pmcc/engine.py` | leg selection (delta/extrinsic long, yield-targeted short), the worksheet math, rolls, the settlement decomposition, the fee stack. Pure. |
| `src/cherrypick/pmcc/keltner.py` | daily bars mirrored from `stream_summary`, the channel, the pullback-and-reversal gate. |
| `src/cherrypick/pmcc/provider.py` | snapshots from the shared stream cache, read-only, one-sided DEEP quote window, refuses rather than guesses. |
| `src/cherrypick/pmcc/management.py` | per-book verdicts + the execution gate + the advised-params choke point + the exposure flag. Pure. |
| `src/cherrypick/pmcc/book.py` | engine decisions → ledger rows: entries, closes, rolls, settlement, share disposal. |
| `src/cherrypick/pmcc/paper_loop.py` | the session driver: mark, manage, enter, dispose, settle at the bell. |
| `src/cherrypick/pmcc/analytics.py` | the one query layer every read surface goes through. Read-only. |
| `src/cherrypick/pmcc/db.py` | schema, additive migrations, the stale-writer guard, every writer. |
| `src/cherrypick/pmcc/stream_request.py` | declares symbols, open legs, expirations, and window hints to the streamer. |
| `src/cherrypick/pmcc/stream_window.py` | the deep-window width: computed from the chain, escalated on real misses. |
| `src/cherrypick/pmcc/cli.py` | `status` / `headline` / `worksheet` / `exposure`. |

## Commands

```bash
python -m cherrypick.pmcc.paper_loop --once        # one gated tick (what the supervisor spawns off-session)
python -m cherrypick.pmcc.paper_loop --interval 60 # the in-session resident loop
python -m cherrypick.pmcc.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.pmcc.paper_loop --settle --date 2026-08-28 --price 71.23  # official print
python run.py status                               # open positions + the expiration plan + keltner readiness
python run.py worksheet                            # the live per-position worksheet
python run.py exposure                             # the early-assignment-exposure telemetry
python -m pytest                                   # temp CHERRYPICK_HOME; no broker, no streamer needed
ruff check . && ruff format .                      # line-length 110
```

Config: copy `config.example.json` → `config.json` (git-ignored), or place
`~/.cherrypick/config/pmcc.json`. The example file is the design document — read its `_note` keys
before changing a value.

## Data source and the deep window

This module runs no streamer and holds **no broker credentials at all**. Its two expirations exist
in the shared cache because `stream_request.py` declares them via the registry's `expirations`
field every tick, and — the module's one unusual demand — its **deep strikes** exist because the
same file sends `window_hints`: a 99-delta long lives 30–45% below spot, outside any default ATM
window, so `stream_window.py` computes the width the chain actually needs and escalates
flies-style on `no_deep_itm_long`/`missing_leg_quotes` refusals. Once a leg is OPEN, `leg_sources`
keeps it subscribed regardless of the window, so the risk is confined to entry-time pricing and
surfaces as recorded refusals, never bad fills.

The provider refuses rather than guesses: stale or crossed quotes, a missing chain, a spot that
won't print — each is a recorded refusal the loop steps past. The OCC-root filter admits only the
configured root per underlying: leveraged ETFs split often, and post-split adjusted roots (`TNA1`)
share expiration dates with the standard root. A split mid-position is unmodelled — manual
`--settle` plus a measurement break.

## Physical settlement

All three symbols are American physical delivery, handled by the calendars decomposition verbatim:
an ITM short call at expiry books its intrinsic AND delivers **short 100 shares per contract at the
settlement spot** (`pmcc_assignments`); the surviving ~12-DTE long stays open; the next session's
combined disposal covers the shares and sells the long at its mark. A position does **not** close
while its shares are outstanding (`finalize_if_done` treats them exactly as an open leg), and the
Friday-to-Monday gap is left visible in the ledger — it IS the weekend exposure. Booking shares at
the settlement spot rather than the strike is what keeps the option accounting untouched;
`tests/test_engine.py` asserts the cash-flow equivalence.

## Live-trading prerequisites (none built; gates any future live plan here)

This module is live-pilot-*shaped* (per-position ledger, book strings, argv contract) but has **no
live path** — no `enable_live_trading`, no order code, no keyring. Before any live rung: (a)
**post-assignment management** — detecting a surprise early assignment in the account and a defined
cover/repair procedure, because live assignment cannot be excluded by skipping ex-div spans; (b) a
**calculated ex-div decision** priced as expected assignment cost against the span's edge; (c) the
paper exposure telemetry (rule 2) read as the gap between the paper result and a live expectation.
Prerequisites, not enhancements.

## Guardrails (suite-wide)

- **Paper only. There is no live path.**
- **No AI, no MCP, no network on any decision path.** `engine.py`, `management.py`, `keltner.py`
  are pure functions over pre-fetched data.
- Declared settlement only (`settlement_style`); a symbol declared as neither style is refused.
- Credentials in the OS keyring only (this module holds none). Account numbers masked to
  `****1234`. Portable paths only; scratch work in `.tmp/`. Human-voice docs and commits, no AI
  attribution. Instruction files hold no code.
- Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`), never opt-in — the flies
  2026-07-20 lesson.

## Known risks, stated up front

1. **Deep-ITM quote/greeks coverage** rests on `window_hints` doing its job; the degrade path is
   extrinsic-only long selection (recorded, excludable). First sessions measure it via
   `pmcc_snapshots` and `no_deep_itm_long` counts.
2. **Deep-ITM spreads** may trip `max_leg_spread_pct` often; the attempts table measures, and a
   recalibration is a config change plus a journaled break.
3. **Early assignment** — rule 2. The paper result is an upper bound.
4. **Dividend calendar upkeep** — three symbols, quarterly, hand-refreshed; lapse halts entries.
5. **Splits mid-position** — unmodelled; manual settle + break.
6. **The keltner book's history depends on the candle backfill** — its bars arrive via the
   producer's `history_days` backfill (DXLink daily candles; absent dates only, today never), and
   until that runs the book idles on `insufficient_bar_history`. Its comparison against control is
   entry-timing only either way. Note the backfilled bars and the live Summary rows come off
   different consolidation feeds; the insert-only rule keeps provenance clean, and a mixed series
   is expected.

## Status

Built 2026-08-16; first paper data collection starts with its first scheduled session. The console
renders this module through the generic Review page; a dedicated page is deliberately deferred
until real positions accumulate.
