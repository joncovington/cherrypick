# cherrypick-flies

**Retired 2026-10-19; off by default.** The code, ledgers and docs are kept so the module can be reinstated. To
reinstate: set `modules.flies.enabled` to true in the suite config and `enabled` to true on the `control`
arm in `flies.json`; the supervisor derives the flies jobs from the module switch. The extras
`flies_payoff_post.enabled`, `flies_payoff_post.intraday.enabled` and `intraday_agent.enabled` are separate
and stay off until chosen, and live trading stays behind its own gate and per-day arm (`/live-flies-start`
and `--arm` refuse while the module is off).

With the module off no paper or live session runs and the supervisor schedules none of flies' jobs.
Backtest research still reads the ledgers. The honesty rules and guardrails below govern any read of its
numbers and any change to its live path.

The file layout, read side, data source, arms, the advised arm and band placement are in
[docs/reference.md](docs/reference.md); the live path is [docs/live.md](docs/live.md); the intraday agent is
[docs/intraday-agent.md](docs/intraday-agent.md). Each rule below keeps the number that set it (the 20-point
trend band, the stale-GEX limits, `min_floor_dollars`). Before changing a parameter, remember the number is
only as good as the measurement behind it.

0DTE net-credit butterflies on SPX, the "profit forest" (earlier XSP and SPX books remain in the ledger
under their own symbol and widths; never pool them). A **paper module** with a narrow live pilot: it
measures whether the strategy makes money net of costs, and it is built so that a negative answer is a
usable result rather than something to tune away.

**Why SPX.** Credit scales with the structure and the flat $5-per-ITM-strike settlement fee does not, so
5-wide SPX (the tightest structure SPX offers, since 0DTE strikes are 5 points apart) carries a far smaller
fee drag than 1-wide XSP (about 10.9% against 41.4% of the median credit). Per-contract risk is 5x larger
($100 to $500), unavoidably. Per dollar of risk the two were within noise, so the fee case is solid and the
risk-adjusted case is not established.

## What the strategy actually is

A long symmetric butterfly pays `max(0, W - |S - K|)` at expiry: bounded to `[0, W]`, never negative. So a
fly **held for a net credit** cannot lose at expiry; its worst case is the credit itself. Several at
different strikes give a risk graph green across a band, a peak at each centre: a forest of profit zones on
a positive floor.

You cannot simply buy such a fly (a negative debit for a non-negative payoff would be arbitrage). The
credit has to be manufactured, and there are exactly two ways, both implemented:

**`legged`**: sell a defined-risk credit spread for `C`, then buy the spread that completes it into a
symmetric fly for `D < C`. You hold a butterfly for `C - D` net credit: a genuine, unconditional,
**per-position** floor.

**`outright`**: buy a cheap fly for a debit, paid for out of premium the book already took in. This
manufactures nothing; it spends an existing floor. The result is a **book-level** floor that only holds
inside the funding spreads' wings. Off (`entry_modes` is `["legged"]`).

Keeping those two straight is the module's main job. See "The honesty rules" below.

## The honesty rules

These are the constraints the module exists to enforce. Breaking one makes the numbers worthless.

1. **Every result is net of the modelled fee and slippage stack.** A trade can collect $4.00 against $4.96
   of fees. Gross credit is not a result.
2. **"Risk-free" is a measurement, never an assumption.** `position_floor` is computed after fees and
   `is_risk_free` can and does return `False` for a fly with a positive gross credit.
3. **A per-position floor and a book-level floor are different claims.** `book_floor` returns
   `unbounded_below` and a price `band` so a book leaning on open short verticals is never reported as
   unconditionally safe.
4. **The uncompleted branch is reported separately.** A legged entry that never completes is an ordinary
   credit spread with full defined risk; `completion_rate` is expected to decide whether this strategy is
   real. **This is also what rule 6 compares against**: refusing a completion leaves *this*, so the two
   rules must be read together.
5. **No adjustments after establishment.** No stops, no wing moves, no exceptions: hold to cash
   settlement. An adjustment rule tuned before a completion rate exists would be fitting noise.

   **A pre-close ITM exit was tried and removed after measurement; keep the negative result.** Early-closed
   ITM positions did worse than ITM positions held to pay the settlement fee, and live refused the close on
   cost every time. It cannot be fixed by tuning: the fee is flat while closing cost scales with notional;
   closing forfeits the settlement floor the module exists for; and it acts on intraday spot when the fee is
   set by the print. Still in the code: `fly.position_floor` reserves the worst-case assignment fee
   (`fly.WORST_CASE_ITM_LEGS`), which tightens `live_orders.max_safe_completion_debit`. Paper rows with
   `closed_before_expiry = 1` (`pinned = 0`) closed at an intraday quote: **exclude them when reading paper
   P&L**. They are deliberately *not* stamped `void_reason` (the numbers are real, they measure a behaviour
   that no longer exists), so a caller must exclude them knowingly.
6. **A floor is judged against the alternative, and "negative after fees" is still the finding.**

   **The comparison.** A completion's floor is judged against *what happens if we refuse it*, never against
   zero. On a legged entry the alternative is rule 4's open short vertical at full defined risk, so a small
   negative floor can be the better of two positions already held (which is why `min_floor_dollars` is 10,
   not 50).

   **The finding.** A book that needs negative floors to look viable is telling you the strategy does not
   work. Admitting them improves a losing book without making it a winning one: the break-even completion
   rate rises with the observed one. Take the change *and* keep the result. **This rule is satisfied by
   refusing to call that a fix, never by refusing to measure.**

   Two limits, because this is the rule most easily read as a licence:
   - It governs **completion of a position already open**. It never justifies an *entry*.
   - It is an argument from a **measured** alternative, not a standing permission. If the stranded branch
     stops being the dominant loss, the bar goes back up. Re-derive it per symbol and against the current
     floor definition; never inherit it.

   *"The answer is to stop, not to loosen `fee_buffer` until the numbers look better"* stands for
   `fee_buffer`, for entries, and for every gate whose alternative really is no position. **And
   `fee_buffer` is what bounds the downside**: the price gate caps the completing debit at
   `credit - fee_buffer`, so the worst floor that can pass it is `fee_buffer x 100 - fees - reserve` (about
   **-$11.89** on 5-wide SPX, independent of credit); `min_floor_dollars` has effect only above that.

## Liveness is published, not inferred

The resident loop touches `state/flies.heartbeat` (`paper_loop._beat`, via
`cherrypick.core.home.heartbeat_path`) at the **top of every tick**, before any gate, and the supervisor
measures this job's silence against that file, never against the log. A quieter log must never be able to
trigger restarts. The log is free to be as talkative as a human reader needs.

## Guardrails

- **Live is a narrow, per-day-armed pilot**: one arm (`control`), one symbol, sized by a local buying-power
  cap `live.max_open_margin_dollars` read from the ledger and the plan, never a balance call, with an
  uncompleted vertical counted at its worst case. `live.max_incomplete_spreads: 1` restores one at a time.
  See `live_loop.py` and [docs/live.md](docs/live.md).
- **No live leg opposite another live loop's** (`core.livepositions.cross_module_conflict`, refusal
  `cross_module_sign_conflict`): bwb and MEIC trade SPX in the same account, and a leg opposite one they
  hold or have working would net at the broker into a position no ledger describes.
- **No entry of any mode on an NYSE early-close session**, paper or live (`engine.early_close_gate`): every
  clock here assumes a 16:00 close, so a 13:00 day is refused outright.
- **No new entry after 12:30 ET on a triple-witching session** (`engine.triple_witching_gate`, refusal
  `triple_witching_no_new_entries`; MEIC's rule and reason string, verbatim). Afternoon entries on such a
  session settle through the short strike uncompleted far more often than morning ones. Binding from
  2026-12-18, journaled as a break on both ledgers.
- **No new LIVE entry on a quarter-end session, all day** (`core.calendar.is_quarterly_expiry`, refusal
  `core.live.QUARTER_END_REASON`). In `live_loop.run_once`'s entry step, never the engine, so paper trades
  the day; fills, completions and settlement still run. Arming is allowed and says so (`warning` in the
  `--install-task` JSON; the watchdog posts it to Discord). Binding from 2026-12-31, journaled on the live
  ledger.
- **SPX/XSP only**: European cash-settled, so early exercise is structurally impossible. Cash settlement at
  expiry is not free: tastytrade charges **$5 per ITM STRIKE**, per distinct settling option symbol, not per
  contract, so a completed fly pays at most 3 charges. Modelled (`fly.expire_fee`,
  `fly.itm_legs_at_settlement`), reserved in every floor (`fly.WORST_CASE_ITM_LEGS`), and paid, never
  dodged (rule 5). `fee_reconcile` compares modelled vs real fees **per settlement symbol**, since an
  aggregate hides a per-contract mis-model as apparent slippage. SPY and /ES-/MES options are American-style
  and physically or futures-settled, so they are out ([docs/faq.md](docs/faq.md)).
- **The decision path is deterministic.** `fly.py` and `engine.py` are pure functions over a pre-fetched
  snapshot: no model, no MCP, no network. Learning happens offline (orchestrator `report`/`calibrate`,
  `packages/review`), never inside the loop.
- **The streamer comes before API calls.** All pricing reads the shared stream cache, and cached quotes
  GATE broker calls (a resting entry is cancelled/replaced only when the cached evaluation moved;
  fill-status polls fire only when cached quotes touch the working limit, plus a slow heartbeat). The broker
  API is only for acting and for confirming a fill. Applies to all future live work here. Narrow
  exceptions:
  - **Fresh re-price before a live entry**: immediately before submitting, never on the decision path,
    never in paper, `live_orders.entry_fresh_reprice` re-fetches both legs once over REST
    (`broker_cli.fresh_option_quotes`) and submits at that price, or skips the tick if unavailable or moved
    against us beyond `live.fresh_quote_tolerance_dollars`. Whether/what to enter stays 100% cache-driven.
    (The broker's execution-quality check can reject a cached price its own preflight passed.)
  - **`live.entry_pricing`** (default `concession`, off in the example): `mid_first` asks the fresh mid
    (floored to the tick, never past it) on a structure's first order of the day and concedes to the
    concession price on the next tick through `_manage_pending_entry`'s cancel/replace; the ledger's
    unfilled rows, not process state, remember the first try. Every gate and the fresh-quote check apply to
    both orders. The completion limit follows the credit filled, so it rises with a mid fill. Switching it
    is a **live measurement break**.
  - **The order-alert stream**: `live.use_order_alert_stream` (per-burst websocket) or
    `live.use_order_alert_daemon` (supersedes it when both are on: `alert_daemon.py` holds one connection
    for the day and appends to `data/flies/live_alerts.db`). Both off by default, both only change how fast
    a fill is *noticed*, and both fail closed to the cache-gated poll. The inbox is a **separate database**
    from `live_trades.db`, whose concurrency is tuned for exactly two short-burst writers. The daemon
    starts on arm, stops on disarm, self-exits at `disarm_time`, decides nothing, places nothing, never
    writes the ledger; if it dies the heartbeat poll and `run_once`'s once-a-minute re-poll still confirm
    every fill. The daemon subscribes with an EMPTY order-id set, which means every order on the account
    (`packages/core/tests/test_broker.py` pins it: an empty set once meant "match nothing" and the daemon
    recorded zero alerts). Fill latencies recorded on or before 2026-09-18 are poll latencies.
- Package-specific: scratch work in `.tmp/`.

## Measurement boundaries

- **Never pool completion rates across 2026-08-09.** The tick cadence went 60s to 15s, and a faster poll
  catches completing-debit dips a slower one missed. The break is a `mode='cadence'` row in the decision
  journal (`_note_cadence_change`).
- Never pool paper completion P&L across 2026-10-05 (paper began paying the live limit), `entry_/completion_gex_*`
  across 2026-10-06 (the GEX inputs became the recorder's), entry counts across 2026-08-11 (the same-strike
  sign rule), or XSP-era and SPX-era books. Details in [docs/reference.md](docs/reference.md).

## Status

**Complete and tested:** decision engine, floor accounting, paper DB, snapshot provider, session driver,
CLI, and the orchestrator `fly_book` wiring across all four schema registries. The tests include a provider
suite built against the real `cherrypick.core.streamcache` DDL so an upstream schema change fails here
rather than silently producing empty snapshots. Runs in CI.

**Settlement is marked in the database, not on disk.** `session_already_settled` asks whether every
`fly_books` row for the day is `settled`; a marker for "settlement happened" must be writable only by
settlement (a file marker once let a test run leave positions unsettled). Tests are isolated by an autouse
fixture in `tests/conftest.py`, not one each test opts into, for the same reason.

**Settlement is approximate in paper.** `--settle` defaults to the last streamed trade, which differs
systematically from the official print; a position centred within a point of spot can settle on the wrong
side. Pass `--price` with the official print for any book whose result matters.

**A live book never settles on an unasked fill status.** Every settle path (the tick's, `--settle`,
`--settle-overdue`) first confirms each open position's pending entry or completion with the broker; any
answer still unknown refuses the whole book (`pending_fill_unconfirmed`) and the next call retries. A
completion that fills during an outage must not be settled as a bare vertical. A confirmed completion's
`completed_at` and latency are the broker's fill time, the moment it was noticed only when the broker gives
none.
