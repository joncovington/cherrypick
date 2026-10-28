# cherrypick-flies

**Retired by the developer in the live config on 2026-10-19; may be reinstated at a later date.**
`modules.flies.enabled` is `false`: no paper or live session runs, and the supervisor schedules none
of flies' jobs. The code, ledgers and docs remain, backtest research still reads them, and
reinstating it is that one key (plus `flies_payoff_post`'s own `enabled`/`intraday.enabled`). Treat
flies data and open questions as historical unless the module is reinstated. The honesty rules and
guardrails below still govern any read of its numbers and any change to its live path.

The file layout, read side, data source, arms, gates, advised arm, live-pilot plan and band
placement are in [docs/reference.md](docs/reference.md).

0DTE net-credit butterflies on SPX (XSP 2026-07-29…07-31, SPX through 07-28; every era's books remain
in the ledger under their own symbol and widths) — the "profit forest". A **paper module** with a
narrow live pilot: it measures whether the strategy makes money net of costs, and it is built so that
a negative answer is a usable result rather than something to tune away.

**Where things live.** A new FINDING goes in [docs/experiment-log.md](docs/experiment-log.md), dated
and append-only. Work deliberately set down, with the condition that reopens each item, is
[docs/backlog.md](docs/backlog.md). The full narrative behind each rule below — incidents, sweeps,
how a number was found — is [docs/history.md](docs/history.md). This file keeps the rules, each
with the number that set it (the 20-point trend band, the stale-GEX limits, `min_floor_dollars`);
the evidence behind that number lives in the history, not beside the rule. Before changing a
parameter, read its history entry: the number is only as good as the measurement behind it.

**The 2026-08-01 SPX switch.** XSP fees were eating the result: 1-wide XSP collected a median $12.00
against $4.97 of fees (41.4% drag), 5-wide SPX $63.12 against $6.89 (10.9%) — credit scales with the
structure, the flat $5-per-ITM-strike fee does not. SPX 0DTE strikes are 5 points apart, so 5-wide is
the tightest structure SPX offers and per-contract risk rose $100 → $500, unavoidably. Per dollar of
risk the two are within noise (1.41% vs 1.49%, different weeks): the fee case is solid, the
risk-adjusted case is not established.

## What the strategy actually is

A long symmetric butterfly pays `max(0, W - |S - K|)` at expiry — bounded to `[0, W]`, never negative.
So a fly **held for a net credit** cannot lose at expiry; its worst case is the credit itself. Several
at different strikes give a risk graph green across a band, a peak at each centre: a forest of profit
zones on a positive floor.

You cannot simply buy such a fly — a negative debit for a non-negative payoff would be arbitrage. The
credit has to be manufactured, and there are exactly two ways (both seen in real order chains, both
implemented):

**`legged`** — sell a defined-risk credit spread for `C`, then buy the spread that completes it into a
symmetric fly for `D < C`. You hold a butterfly for `C - D` net credit: a genuine, unconditional,
**per-position** floor.

**`outright`** — buy a cheap fly for a debit, paid for out of premium the book already took in. This
manufactures nothing; it spends an existing floor. The result is a **book-level** floor that only holds
inside the funding spreads' wings.

Keeping those two straight is the module's main job. See "The honesty rules" below.

## The honesty rules

These are the constraints the module exists to enforce. Breaking one makes the numbers worthless.

1. **Every result is net of the modelled fee and slippage stack.** This suite has recorded a trade
   collecting $4.00 against $4.96 of fees. Gross credit is not a result.
2. **"Risk-free" is a measurement, never an assumption.** `position_floor` is computed after fees and
   `is_risk_free` can and does return `False` for a fly with a positive gross credit.
3. **A per-position floor and a book-level floor are different claims.** `book_floor` returns
   `unbounded_below` and a price `band` so a book leaning on open short verticals is never reported as
   unconditionally safe.
4. **The uncompleted branch is reported separately.** A legged entry that never completes is an
   ordinary credit spread with full defined risk; `completion_rate` is expected to decide whether this
   strategy is real. **This is also what rule 6 compares against** — refusing a completion leaves
   *this*, so the two rules must be read together.
5. **No adjustments after establishment.** No stops, no wing moves, no exceptions — hold to cash
   settlement. An adjustment rule tuned before a completion rate exists would be fitting noise.

   **A pre-close ITM exit (2026-07-30..08-01) was the one exception, and was removed after
   measurement — keep the negative result.** Early-closed ITM positions averaged −$105.64 against
   −$71.93 for ITM positions held to pay the fee (~$34/position worse, in paper); live refused it on
   cost 6 of 6 times. It cannot be fixed by tuning: the fee is flat while closing cost scales with
   notional; closing forfeits the settlement floor the module exists for; and it acts on intraday spot
   when the fee is set by the print (11.9% of settled positions changed ITM-leg count in between).
   Full table: [docs/history.md](docs/history.md#the-pre-close-itm-exit-2026-07-3008-01-removed-after-measurement).
   Still live in the code: `fly.position_floor` reserves the worst-case assignment fee
   (`fly.WORST_CASE_ITM_LEGS`), which tightens `live_orders.max_safe_completion_debit`. The 34 paper
   rows with `closed_before_expiry = 1` (`pinned = 0`) closed at an intraday quote — **exclude them
   when reading paper P&L**. They are deliberately *not* stamped `void_reason` (the numbers are real,
   they measure a behaviour that no longer exists), so a caller must exclude them knowingly.
6. **A floor is judged against the alternative, and "negative after fees" is still the finding.**

   **The comparison.** A completion's floor is judged against *what happens if we refuse it*, never
   against zero. On a legged entry the alternative is rule 4's open short vertical at full defined
   risk, so a small negative floor can be the better of two positions already held (why
   `min_floor_dollars` moved 50 → 10 on 2026-07-27).

   **The finding.** A book that needs negative floors to look viable is telling you the strategy does
   not work. Admitting them improves a losing book without making it a winning one — the break-even
   completion rate rises with the observed one. Take the change *and* keep the result. **This rule is
   satisfied by refusing to call that a fix, never by refusing to measure.**

   Two limits, because this is the rule most easily read as a licence:
   - It governs **completion of a position already open**. It never justifies an *entry*.
   - It is an argument from a **measured** alternative, not a standing permission. If the stranded
     branch stops being the dominant loss, the bar goes back up. Re-derive it per symbol and against
     the current floor definition; never inherit it.

   *"The answer is to stop, not to loosen `fee_buffer` until the numbers look better"* stands verbatim
   for `fee_buffer`, for entries, and for every gate whose alternative really is no position. **And
   `fee_buffer` is what bounds the downside**: the price gate caps the completing debit at
   `credit − fee_buffer`, so the worst floor that can pass it is `fee_buffer × 100 − fees − reserve`
   (about **−$11.89** on 5-wide SPX, independent of credit); `min_floor_dollars` has effect only above
   that. The dated 2026-08-06 measurement behind this (paper only; an upper bound, from best-ever
   telemetry) is in [docs/history.md](docs/history.md#rule-6-the-measurement-it-came-from-2026-08-06).

## Liveness is published, not inferred

The resident loop touches `state/flies.heartbeat` (`paper_loop._beat`, via
`cherrypick.core.home.heartbeat_path`) at the **top of every tick**, before any gate, and the
supervisor measures this job's silence against that file, never against the log. Luck is not a
supervision contract: a quieter log must never be able to trigger restarts. The log is free to be as
talkative as a human reader needs.

## Guardrails

- **Live is a narrow, per-day-armed pilot**: one arm (`control` since 2026-09-17), one symbol, sized
  by a local buying-power cap `live.max_open_margin_dollars` read from the ledger and the plan — never
  a balance call — with an uncompleted vertical counted at its worst case. No count limit on
  incomplete positions since 2026-09-25; `live.max_incomplete_spreads: 1` restores one-at-a-time. See
  `live_loop.py` and [docs/live-trading-plan.md](docs/live-trading-plan.md).
- **No live leg opposite another live loop's** (2026-10-15, `core.livepositions.cross_module_conflict`,
  refusal `cross_module_sign_conflict`): bwb and MEIC trade SPX in the same account, and a leg
  opposite one they hold or have working would net at the broker into a position no ledger describes.
- **No entry of any mode on an NYSE early-close session**, paper or live (`engine.early_close_gate`):
  every clock here assumes a 16:00 close, so a 13:00 day is refused outright.
- **No new entry after 12:30 ET on a triple-witching session** (`engine.triple_witching_gate`, refusal
  `triple_witching_no_new_entries` — MEIC's rule and reason string, verbatim). On 2026-09-18 every
  afternoon entry across paper and live settled through its short strike uncompleted while every
  morning entry completed. First binding session 2026-12-18; journaled as a break on both ledgers.
- **No new LIVE entry on a quarter-end session, all day** (`core.calendar.is_quarterly_expiry`,
  refusal `core.live.QUARTER_END_REASON`). In `live_loop.run_once`'s entry step, never the engine,
  so paper trades the day; fills, completions and settlement still run. Arming is allowed and says
  so (`warning` in the `--install-task` JSON; the watchdog posts it to Discord). First binding
  session 2026-12-31; journaled on the live ledger.
- **SPX/XSP only** — European cash-settled, so early exercise is structurally impossible. Cash
  exercise/assignment at expiry is not free: tastytrade charges **$5 per ITM STRIKE** — per distinct
  settling option symbol, not per contract — so a completed fly pays at most 3 charges. Modeled
  (`fly.expire_fee`, `fly.itm_legs_at_settlement`), reserved in every floor
  (`fly.WORST_CASE_ITM_LEGS`), and paid, never dodged (rule 5). `fee_reconcile` compares modelled vs
  real fees **per settlement symbol**, since an aggregate hid a per-contract mis-model as ~$12 of
  apparent slippage.
- **The decision path is deterministic.** `fly.py` and `engine.py` are pure functions over a
  pre-fetched snapshot — no model, no MCP, no network. Learning happens offline (orchestrator
  `report`/`calibrate`, `packages/review`), never inside the loop.
- **The streamer comes before API calls.** All pricing reads the shared stream cache, and cached quotes
  GATE broker calls (a resting entry is cancelled/replaced only when the cached evaluation moved;
  fill-status polls fire only when cached quotes touch the working limit, plus a slow heartbeat). The
  broker API is only for acting and for confirming a fill. Applies to all future live work here. Two
  narrow exceptions:
  - **Fresh re-price before a live entry** — immediately before submitting, never on the decision
    path, never in paper, `live_orders.entry_fresh_reprice` re-fetches both legs once over REST
    (`broker_cli.fresh_option_quotes`) and submits at that price, or skips the tick if unavailable or
    moved against us beyond `live.fresh_quote_tolerance_dollars`. Whether/what to enter stays 100%
    cache-driven. (Origin: the broker's execution-quality check rejected a cached price its own
    preflight passed.)
  - **`live.entry_pricing`** (2026-10-12, default `concession`, off in the example): `mid_first`
    asks the fresh mid (floored to the tick, never past it) on a structure's first order of the day
    and concedes to the concession price on the next tick through `_manage_pending_entry`'s
    cancel/replace; the ledger's unfilled rows, not process state, remember the first try. Every
    gate and the fresh-quote check apply to both orders. The completion limit follows the credit
    filled, so it rises with a mid fill. Switching it is a **live measurement break**.
  - **The order-alert stream** — `live.use_order_alert_stream` (per-burst websocket) or
    `live.use_order_alert_daemon` (supersedes it when both are on: `alert_daemon.py` holds one
    connection for the day and appends to `data/flies/live_alerts.db`). Both off by default, both only
    change how fast a fill is *noticed*, and both fail closed to the cache-gated poll. The inbox is a
    **separate database** from `live_trades.db`, whose concurrency is tuned for exactly two
    short-burst writers. The daemon starts on arm, stops on disarm, self-exits at `disarm_time`,
    decides nothing, places nothing, never writes the ledger; if it dies the heartbeat poll and
    `run_once`'s once-a-minute re-poll still confirm every fill. The daemon subscribes with an EMPTY
    order-id set, which means every order on the account (`packages/core/tests/test_broker.py` pins
    it — it once meant "match nothing" and the daemon recorded zero alerts from 2026-07-31 to
    2026-09-18). Fill latencies on or before 2026-09-18 are poll latencies.
- Package-specific: scratch work in `.tmp/`.

## Status

**Complete and tested:** decision engine, floor accounting, paper DB, snapshot provider, session
driver, CLI, and the orchestrator `fly_book` wiring across all four schema registries. The tests
include a provider suite built against the real `cherrypick.core.streamcache` DDL so an upstream
schema change fails here rather than silently producing empty snapshots. Runs in CI. What each session
measured: [docs/experiment-log.md](docs/experiment-log.md).

**Never pool completion rates across 2026-08-09.** The tick cadence went 60s to 15s, and a faster poll
catches completing-debit dips a slower one missed. The break is a `mode='cadence'` row in the decision
journal (`_note_cadence_change`).

**Settlement is marked in the database, not on disk.** `session_already_settled` asks whether every
`fly_books` row for the day is `settled`; a marker for "settlement happened" must be writable only by
settlement (a file marker once let a test run leave eleven positions unsettled). Tests are isolated by
an autouse fixture in `tests/conftest.py`, not one each test opts into, for the same reason.

**Settlement is approximate.** `--settle` defaults to the last streamed trade, which differs
systematically from the official print; a position centred within a point of spot can settle on the
wrong side. Pass `--price` with the official print for any book whose result matters.

**A live book never settles on an unasked fill status** (2026-10-13). Every settle path (the tick's,
`--settle`, `--settle-overdue`) first confirms each open position's pending entry or completion with
the broker; any answer still unknown refuses the whole book (`pending_fill_unconfirmed`) and the next
call retries. A completion that filled during an outage had been settled as a bare vertical: +$225
booked against +$480. A confirmed completion's `completed_at` and latency are the broker's fill
time, the moment it was noticed only when the broker gives none.
