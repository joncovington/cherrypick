# cherrypick-pmcc

PMCC-99 — deep-ITM covered calls on TQQQ and XSP, **paper-only and credential-free**: a pure
stream-cache consumer in the calendars posture. Buy an 85-90-delta call at ~21 DTE — a stock
substitute, deliberately NOT a LEAP — and sell the ATM call nearest spot at ~7 DTE, whichever side
of spot it lands on. Hold to the short's own expiration, then close BOTH legs together and
re-enter. Ledger schema: **`pmcc_99`**.

> **2026-08-23 — measurement break: redesigned from 3-symbol/3-book to single-symbol/single-book.**
> The original design (TNA/TQQQ/UPRO, `control`/`keltner`/`roll` books, a ~99-delta long chosen by
> an extrinsic-and-delta floor, a yield-targeted ITM short, and an early tv-exhaustion exit) is
> retired. The replacement trades TQQQ only, in a single `control` book: the long is chosen inside a
> DELTA BAND (0.85-0.90) rather than past a floor, the short is simply the strike NEAREST spot with
> no yield search (it can land OTM), and the default exit holds the position to the short's own
> expiration rather than closing early on tv exhaustion. The old tv-exhaustion rule survives as
> `tv_managed_exit`, a live advisor-tunable override read only through the `advised:control` book's
> frozen params — the suite can now run hold-to-expiry vs. early-tv-exit as a paper A/B. Results
> from either side of this boundary must never be pooled: the symbol changed, the book roster
> changed, both leg-selection rules changed, and the default exit rule changed. `pmcc_99` rows from
> before this date carry `symbol` values (TNA, UPRO) and `book` values (`keltner`, `roll`) this
> checkout no longer writes; they remain in the ledger as history, not as comparable measurement.

> **2026-08-23 — measurement break: XSP added as a second symbol alongside TQQQ, same `control`
> book.** No new book — `symbols` is now `["TQQQ", "XSP"]`, both trading the identical `control`
> rule set (delta-band long, nearest-spot short, hold-to-expiry exit). XSP (Mini-SPX) is a
> **European-style, cash-settled** broad-based index option — structurally different from TQQQ's
> American physical-delivery ETF option — so it carries none of TQQQ's early-assignment machinery:
> `settlement_style.XSP = "cash"` means the ex-dividend refusal (`ex_dividend_span`,
> `dividend_calendar_lapsed`) never runs for it, `assignment_from` is never called for it (no
> delivered shares, no next-session disposal, no weekend share carry), and
> `management.assignment_exposed` is exempt for it (always False) — that telemetry measures a risk
> XSP structurally cannot have. XSP also differs in the fee stack: it is a broad-based index option
> and is looked up on `cherrypick.core.fees.INDEX_EXCHANGE_FEE_PER_CONTRACT` by symbol (TQQQ, an
> ETF, is off that schedule entirely). **TQQQ and XSP results are NOT interchangeable substitutes
> for each other** — different settlement mechanics, different risk profile, different fee
> treatment — but both run the byte-identical `control` rule set, so they are directly comparable
> AS SEPARATE POPULATIONS under the same book: `pmcc_99` rows should always be read grouped by
> `symbol`, never pooled across it. Unlike the redesign above, this is additive rather than a
> retirement — TQQQ's own results before and after this date remain comparable, since nothing about
> how TQQQ trades changed. Only "the module's rows" as a whole gains a new, non-poolable slice.

Suite-wide context is the root [documentation index](../../docs/README.md).

## The experiment design

One book, plus the advisor's synthetic twin — a deliberate simplification from the original
3-book design (see the measurement-break note above):

- **`control`** — the strategy as taught: mechanical entry whenever the (symbol, book) slot is
  free, an 85-90-delta long, an ATM short with no yield floor, hold to the short's own expiration,
  then close both legs together. Never rolls (there is no roll book any more).
- **`advised:control`** (paper, off by default) — the AI advisor's admitted params, frozen on each
  row at entry and restated through one choke point (`management.effective_params`) every tick. The
  one thing currently worth advising is `tv_managed_exit`/`tv_close_threshold` — flipping the exit
  rule back to the pre-redesign early-tv-exhaustion close, as a paper A/B against hold-to-expiry.

There is no more multi-book fill pairing to reason about: with one book plus its advised twin, every
`control` row is directly comparable to every other `control` row.

## The honesty rules

1. **Every result is net of the modeled fee and slippage stack** — commissions/clearing/ORF/TAF,
   the $5-per-ITM-symbol settlement/assignment event, the SEC and FINRA pass-throughs on the share
   side of an assignment, and the suite's slippage model. ETFs carry no index exchange fee. Gross
   is not a result.
2. **Early assignment is unmodelled but MEASURED, and the paper result is therefore an UPPER
   BOUND — for PHYSICAL-settlement symbols only.** A real short ITM call with near-zero extrinsic
   can be assigned any day; this module sells ITM calls by design. Every mark where the short's
   extrinsic sits under `assignment_exposure_tv` is flagged `assignment_exposed`
   (`pmcc_marks`, aggregated by `analytics.exposure`), which bounds what the unmodelled mechanism
   could have touched. Do not read TQQQ's net as achievable live until that exposure is read beside
   it. **XSP carries none of this caveat**: European-style options cannot be exercised early, so
   `assignment_exposed` is exempt for cash-settled positions (always False) — XSP's paper result is
   not an upper bound in this sense.
3. **Ex-dividend spans are refused, not modelled — for PHYSICAL-settlement symbols only.** An entry
   whose short leg spans a declared ex-date is refused (`ex_dividend_span`); a span the declared
   calendar cannot answer for is refused too (`dividend_calendar_lapsed`) — a lapsed table stops
   entries loudly, by design. The dates are hand-declared from the issuer's schedule (quarterly for
   TQQQ) and refreshed by hand. A missing table and "no dividend" must never look alike. This whole
   check is skipped outright for cash-settled symbols (XSP): a European option cannot be exercised
   before its own expiration, so there is no ex-date question to answer, and XSP needs no
   `dividends` config entry at all.
4. **Rules are declared up front and measured, never tuned mid-experiment.** The delta band, the
   ATM short rule, the tv threshold — all stated in config before data accumulates. A removed rule
   keeps its negative result on the record.
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
| `src/cherrypick/pmcc/clock.py` | ET clock + the expiration plan (~7DTE short / ~21DTE long Fridays, holiday-shifted). Pure. |
| `src/cherrypick/pmcc/engine.py` | leg selection (delta-band long, ATM short), the worksheet math, the settlement decomposition, the fee stack. Pure. |
| `src/cherrypick/pmcc/provider.py` | snapshots from the shared stream cache, read-only, one-sided DEEP quote window, refuses rather than guesses. |
| `src/cherrypick/pmcc/management.py` | the control verdict (hold-to-expiry, or the advised tv-managed-exit override) + the execution gate + the advised-params choke point + the exposure flag. Pure. |
| `src/cherrypick/pmcc/book.py` | engine decisions → ledger rows: entries, closes, settlement, share disposal. |
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
python run.py status                               # open positions + the expiration plan
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
same file sends `window_hints`: an 85-90-delta long lives noticeably below spot, outside any
default ATM window, so `stream_window.py` computes the width the chain actually needs and escalates
flies-style on `no_deep_itm_long`/`missing_leg_quotes` refusals. Once a leg is OPEN, `leg_sources`
keeps it subscribed regardless of the window, so the risk is confined to entry-time pricing and
surfaces as recorded refusals, never bad fills.

The provider refuses rather than guesses: stale or crossed quotes, a missing chain, a spot that
won't print — each is a recorded refusal the loop steps past. The OCC-root filter admits only the
configured root: TQQQ splits occasionally, and a post-split adjusted root (`TQQQ1`) shares
expiration dates with the standard root. A split mid-position is unmodelled — manual `--settle`
plus a measurement break.

## Physical settlement (TQQQ only)

TQQQ is American physical delivery, handled by the calendars decomposition verbatim: an ITM short
call at expiry books its intrinsic AND delivers **short 100 shares per contract at the settlement
spot** (`pmcc_assignments`); the surviving ~14-DTE long stays open; the next session's combined
disposal covers the shares and sells the long at its mark. A position does **not** close while its
shares are outstanding (`finalize_if_done` treats them exactly as an open leg), and the
Friday-to-Monday gap is left visible in the ledger — it IS the weekend exposure. Booking shares at
the settlement spot rather than the strike is what keeps the option accounting untouched;
`tests/test_engine.py` asserts the cash-flow equivalence.

None of this fires for XSP. `book.settle_expiring_legs` only calls `engine.assignment_from` inside
its `if physical:` branch, so a cash-settled ITM leg simply books its intrinsic at the settlement
print and finalizes — no `pmcc_assignments` row, no delivered shares, no next-session disposal, no
weekend carry. This is the "calendars decomposition with the share term zeroed" the module
docstrings describe: the same `settle_intrinsic`/`leg_pnl` math handles both styles, and physical
settlement is exactly cash settlement plus a share leg that XSP never grows.

## Live-trading prerequisites (none built; gates any future live plan here)

This module is live-pilot-*shaped* (per-position ledger, book strings, argv contract) but has **no
live path** — no order code, no keyring. Before any live rung: (a)
**post-assignment management** — detecting a surprise early assignment in the account and a defined
cover/repair procedure, because live assignment cannot be excluded by skipping ex-div spans; (b) a
**calculated ex-div decision** priced as expected assignment cost against the span's edge; (c) the
paper exposure telemetry (rule 2) read as the gap between the paper result and a live expectation.
Prerequisites, not enhancements.

**The config's `live.enabled` field (added 2026-08-16) is an inert placeholder, not a rung.** It
lets the suite's config/console surfaces show this module as "paper only" instead of "unknown" —
`readModuleGate`/`liveops._live_enabled` read it the same way they read flies' nested switch — but
no code anywhere checks it, and it is `configedit.GUARDED` so the settings surface can't touch it.
Flipping it to `true` by hand does nothing until the prerequisites above are built and a real live
loop reads the flag.

## Guardrails (suite-wide)

- **Paper only. There is no live path** — no live loop, no order code. `live.enabled` in config is
  a documented placeholder only (see Live-trading prerequisites above); it is not a working gate.
- **The decision path is deterministic.** `engine.py` and `management.py` are pure functions over
  pre-fetched data — no model, no MCP, no network in the decision itself.
- Declared settlement only (`settlement_style`); a symbol declared as neither style is refused.
- Credentials in the OS keyring only (this module holds none). Account numbers masked to
  `****1234`. Portable paths only; scratch work in `.tmp/`. Human-voice docs and commits, no AI
  attribution. Instruction files hold no code.
- Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`), never opt-in — the flies
  2026-07-20 lesson.

## Known risks, stated up front

0. **XSP has none of TQQQ's early-assignment or ex-dividend risk** — see rules 2-3 and the Physical
   settlement section above. The one thing to keep an eye on instead is standard XSP contract
   mechanics (multiplier 100, same as any equity/index option) staying accurate as the chain data
   arrives; nothing here special-cases XSP's multiplier or strike increments differently from any
   other symbol.
1. **Deep-ITM quote/greeks coverage** rests on `window_hints` doing its job; the degrade path is
   extrinsic-only long selection (recorded as `long_selected_by='extrinsic'`, excludable, and
   logged as a warning naming the symbol/book/strike at the moment it fires). Config-gated via
   `allow_extrinsic_fallback` (default true) — set false to refuse a no-delta candidate outright
   instead of degrading, to isolate whether the fallback itself is shaping results. First sessions
   measure it via `pmcc_snapshots` and `no_deep_itm_long` counts.
2. **Deep-ITM spreads** may trip `max_leg_spread_pct`, especially when the short lands ITM; the
   attempts table measures, and a recalibration is a config change plus a journaled break.
3. **Early assignment** — rule 2. The paper result is an upper bound.
4. **Dividend calendar upkeep** — TQQQ only, quarterly, hand-refreshed; lapse halts entries. XSP
   needs no dividend calendar (rule 3).
5. **Splits mid-position** — unmodelled for TQQQ; manual settle + break. XSP does not split.

## Status

Built 2026-08-16; first paper data collection starts with its first scheduled session. Redesigned
2026-08-23 to the single-symbol/single-book shape described above (see the measurement-break note
near the top). The console gained a dedicated **PMCC page** (`/pmcc`) on 2026-08-17, once the first
session's positions existed: current state first (open legs, the short's remaining time value
against `tv_close_threshold`, whichever exit rule is in force), a measurement-integrity strip above
any P&L carrying rule 2's exposure bound, the dividend calendar's refresh state, and a full cycle
history with the short chain and any delivered shares. Its reader mirrors `analytics.py`'s
semantics in TypeScript rather than sharing them — the two are cross-checked by hand against
`python run.py headline`. The module still also appears in the generic Review page.
