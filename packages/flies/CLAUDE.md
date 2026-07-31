# cherrypick-flies

0DTE net-credit butterflies on XSP (SPX through 2026-07-28; both books remain in the ledger) — the
"profit forest". A **paper module**: it measures whether the strategy makes money net of costs, and it
is built so that a negative answer is a usable result rather than something to tune away.

## What the strategy actually is

A long symmetric butterfly pays `max(0, W - |S - K|)` at expiry. That is bounded to `[0, W]` and is
never negative. So a fly **held for a net credit** cannot lose at expiry — its worst case is the credit
itself. Several of them at different strikes give a risk graph that is green across a band with a peak
at each centre: a forest of profit zones sitting on a positive floor.

You cannot simply buy such a fly. Paying a negative debit for a non-negative payoff would be arbitrage.
The credit has to be manufactured, and there are exactly two ways, both of which were observed in real
order chains and both of which this module implements:

**`legged`** — sell a defined-risk credit spread for credit `C`, then buy the spread that completes it
into a symmetric fly for debit `D < C`. You end up holding a butterfly for `C - D` of net credit. This
produces a genuine, unconditional, **per-position** floor.

**`outright`** — buy a cheap fly for a debit, paid for out of premium the book already took in. This
manufactures nothing; it spends an existing floor. The result is a **book-level** floor that only holds
inside the funding spreads' wings.

Keeping those two straight is the module's main job. See "The honesty rules" below.

## Layout

| file | role |
|---|---|
| `src/fly.py` | payoffs, quote pricing, fees, position and book floor math. Pure. |
| `src/engine.py` | centre selection, entry gates, the completion gate, settlement. Pure. |
| `src/provider.py` | builds snapshots from MEIC's stream cache, read-only. No decisions. |
| `src/paper_loop.py` | session driver: fetch, run every arm, settle at the bell. |
| `src/book.py` | wires engine decisions to the paper DB; one book per (date, arm, symbol). |
| `src/db.py` | `fly_positions` (ledger) and `fly_books` (roll-up with the floor's price band). |
| `src/analytics.py` | the one query layer every read surface goes through. Read-only. |
| `src/dashboard.py` | loopback HTTP dashboard: Today / History / Performance. |
| `src/section.py` | the compact `cherrypick.core.viz` card for the suite dashboard. |
| `src/eod.py` | `paper-eod-<day>.md` and `eod-analysis-<day>.md`. |
| `src/cli.py` | `once` / `settle` / `status` / `dashboard` / `section`. |
| `src/live_loop.py` | The LIVE loop: 1-min self-healing tick (`--once --live`, per-day arming via `/live-flies-start`, self-disarms at `live.disarm_time`) + burst fill-watchers (`--watch-fills`). `--once` (dry-run default) is the rung-0 smoke; `--status`, `--settle --price` for the official print. |
| `src/broker_cli.py` | Thin broker seam on `cherrypick.core.broker` (preflight/governor); `--live` double-gated. |
| `src/live_orders.py` | Pure engine-decision → order-spec builders (OCC symbols from the provider). |
| `src/credentials.py` | `fliesagent` keyring store + hidden-input CLI (orchestrator `connect` delegates here). |
| `tests/fixtures/books.json` | three real tastytrade order chains, transcribed. |

## The read side

**Everything reads through `analytics.py`.** MEIC grew three call sites that disagree about what "net"
means — its Today grid uses raw `pnl`, its profile comparison uses `pnl - fees` — so here the dashboard,
the section card, and the EOD writer all share one layer and a test asserts the headline figure agrees
across surfaces.

**Two journal tables, deliberately.** `fly_decisions` records *why* every entry was made or refused,
collapsing consecutive identical reasons into one counted run (a gate that blocked all morning is one
row with `occurrences: 18`). `fly_iterations` records what each arm *wanted* on each iteration, before
any gate could veto it — collapsing would destroy exactly what arm divergence needs.

**And a feed ledger, `fly_snapshots`.** One row per (tick × symbol) recording what the feed gave us —
`status` "ok" with the quote counts, or the provider's refusal reason. It is separate from
`fly_iterations` on purpose: that table is per-arm and only written when a snapshot *succeeds*, so a
refused tick never reaches the arm loop and, without this, leaves no trace at all. That is the gap this
closes — a stretch of the day with refused rows is a feed problem (`no_fresh_quotes`, `no_spot_price`);
a stretch with *no rows at all* is the loop not running. Before `fly_snapshots` those two silences were
identical on every read surface, and `quote_stats` reached only the module log. The timeline now labels
each of its gaps accordingly ("no data · 100m · loop silent" vs "· no_fresh_quotes ×20"), which is how
2026-07-20's outage is legible as an ops failure rather than a quiet market. Recording lives in
`paper_loop.run_once` on both the built and the refused path; it is pure telemetry and touches no
decision.

**Four measurements this strategy needs and generic P&L reporting cannot give:**

- **Completion rate** — how often a leg-in actually became a fly. If this is near zero the strategy is
  short verticals wearing a costume, and no P&L on the completed ones changes that. Besides the
  blended rate, `analytics.completion_trend` gives it per session, and the suite-dashboard card
  (`section.py`) draws that trend as a timeseries — a blended rate can drift slowly while looking
  stable; the trend is what makes a deterioration (or a config change's effect) visible.
- **The counterfactual** (`best_completing_debit`) — for misses, whether *the market never offered it*
  or *one of our own gates refused it*. Identical in the P&L, opposite remedies. Completion is gated
  by `D < C - fee_buffer` **and** `floor >= min_floor_dollars`, so "our gate" is reported as two
  separate verdicts — `buffer_blocked` and `floor_blocked`. They were once lumped together as
  `buffer_too_tight`, which pointed at the wrong knob: the first five sessions split 1 buffer vs 5
  floor, and the single buffer case had a post-fee floor of **−$1.89**, i.e. the buffer correctly
  refused a money-losing fly. Which gate bound is read from the `fly_decisions` journal, not
  recomputed, so it cannot drift from the gate as configured.
- **Completion latency** — a fly that took 40 minutes and 8 points of drift is far likelier to fill live
  than one that appeared for seconds. This is the paper-vs-live gap, measured.
- **Arm divergence** — how often the arms picked different centres. High agreement means the experiment
  cannot separate them, which is a finding to surface in week one, not month three. **Centre divergence
  is only meaningful against an arm that centres differently** — i.e. `gex`. `control`, `time_window`,
  `wide_wing` and the `width-N` arms are all ATM, so they agree on centre *by construction* (measured:
  100% across 184 iterations on 2026-07-27) and that number says nothing about whether those arms are
  redundant. Read `time_window` vs `control` on entry **timing** and completion, and the `width-N`
  arms vs `control` on wing width. Reading a structural identity as a finding is how the redundancy
  went unnoticed.

**The last three of those live on a time axis, so the dashboard has one.** `analytics.session_timeline`
assembles the day from rows already written — spot and every arm's wanted centre on each iteration,
entries and completions, and each leg-in as a span running to its completion, so latency is a length
beside the drift that bought it. `settle_now` replays the book at each tick: what it would have been
worth had the session ended at that moment and that price. That is an expiry payoff evaluated at a
live spot and **not a mark** — nothing here is quoted intraday, and the label says so on the page.

Replaying requires rewinding. A legged position is a short vertical until it completes and a fly
afterwards, but the stored row only ever holds its latest state; drawing straight from it would show
the morning as though every fly existed from the moment its credit spread was sold, which asserts the
per-position floor rule 3 exists to withhold. The rewind is exact, not approximate — the completing
purchase is a 2-leg vertical, so the pre-completion fee is `vertical_open_fee` and the pre-completion
net is the recorded `credit`.

The dashboard binds to **127.0.0.1 only** and draws its charts with plain canvas — no CDN, so it works
offline and adds no third-party dependency to a surface whose only job is reading a local SQLite file.
Both charts refuse to smooth over what they do not know: the timeline **breaks its lines across a gap**
in the record rather than interpolating a shape through it (the 2026-07-20 session has a 100-minute
silence, and a straight segment across it would read as a calm market), and the payoff curve draws one
line per arm rather than a blended book.

## Data source

This module **runs no streamer**. `provider.py` reads the suite's canonical shared stream cache
(`~/.cherrypick/data/marketdata/stream_cache.db`) read-only — the same piggyback path `cherrypick-gex`
uses — so the suite runs one streamer rather than three, and flies can never disturb the loop that is
actually trading. The producer is the standalone `packages/streamer` daemon (the suite's single writer
since the 2026-07-21 cutover; MEIC's in-module streamer is the disabled rollback path), subscribed to
the union of every module's `state/stream_requests/` file — this module rewrites its own on every tick;
open interest, and therefore GEX, exists only because the producer subscribes DXLink Summary for its
ATM window.

The provider refuses rather than guesses. Stale quotes (older than `max_quote_age_seconds`), crossed
quotes, a missing spot, an empty chain — each returns `{"ok": False, "reason": ...}`, which the loop
logs and steps past. Refusals are ordinary and frequent; they are not errors. `quote_stats` is recorded
on every snapshot so a barren session can be read afterwards as "the data was thin" rather than
mistaken for "the strategy found nothing".

`src/_core` is the `cherrypick-core` submodule (same URL and pinned SHA as every other package).
`cherrypick.core.fees` supplies the fee schedule and `cherrypick.core.gex.compute_gex` the per-strike
GEX profile — neither is reimplemented here.

## The arms

Separate books, each differing from `control` in **exactly one** thing. Every gate is shared, so each
comparison measures one variable rather than a bundle of confounded changes.

- `gex` — centre on the strongest positive per-strike net GEX near spot. Degrades to ATM when the
  streamer has no OI cached yet, and records `center_reason` so those samples can be excluded later.
- `time_window` — ATM, entering only inside configured windows. The windows are **not** ranked; we
  have no intraday history to rank them with. Each trade is tagged with its window and the ranking
  comes out of our own sessions. Its `max_positions_per_window` is what makes that ranking possible
  at all — see below. Its windows **straddle** control's rather than nesting inside them (one before
  control opens, one overlapping, one after control closes); nested windows made the two arms
  identical in everything but opportunity count.
- `control` — ATM, all day. The shared baseline: `gex` vs `control` isolates the **centring**,
  `time_window` vs `control` the **timing**, `wide_wing` vs `control` the **width**. Without a naive
  baseline a profitable arm would prove nothing.
- `width-2` … `width-5` — control's twins (ATM, same window and cap) pinning `wing_width` to 2–5
  strike increments; `control` at the default width is the sweep's 1-increment rung, so there is no
  `width-1` arm (it would duplicate control's book under a second name). Added 2026-07-29 with the
  XSP move, generalizing `wide_wing`'s single-point hypothesis into a curve. The signal behind it
  (2026-07-27, first five SPX sessions): completions arrive only after spot has walked away from the
  centre (median drift 15.3–17.3 SPX points against a 5-point wing), so 19 of 23 completed flies
  settled outside their wings and the book collected its floor and nothing more. It is a hypothesis,
  not a fix — wider wings cost more to build and risk more per structure, and if no width produces a
  fee-positive floor then the drift is fundamental to the mechanism, which is itself a result (rule 6).
- `wide_wing` — the SPX-era single-point version of the width question (a 20-point wing bracketing
  the observed drift). **Disabled** since the sweep; kept in `ARMS` so its books' attribution stays
  readable. On XSP its scaled equivalent (~2 points) is exactly `width-2`.
- `debit-first` — control's twin isolating the **legging order**, added 2026-07-31 (`entry_modes:
  ["debit_first"]`, `fly.debit_vertical_payoff`/`engine.evaluate_debit_vertical_entry`/
  `evaluate_debit_completion`). `legged` sells the credit spread first and buys the completing
  debit spread cheaper once spot drifts *away* from the short strike; this arm buys the debit
  vertical first and completes by *selling* the credit spread once spot drifts back *toward* the
  centre — literally `legged`'s two trades in the opposite order, monetizing the opposite drift
  regime at the same centre. Its uncompleted branch is structurally different too: a long
  vertical's worst case at expiry is the debit already paid (bounded, floor never below `-debit`),
  never the `-W` full-defined-risk tail an uncompleted credit spread carries.
- `iron` — control's twin isolating the **completion choice**, added 2026-07-31
  (`completion_modes: ["debit", "iron"]`, `fly.iron_fly_payoff`/`engine.evaluate_iron_completion`).
  `legged`'s completion always buys the same-type debit spread; this arm may instead complete by
  *selling* the opposite-type credit spread (put held -> sell call, or vice versa), producing an
  **iron butterfly** — the same geometry regardless of which side was legged first. Payoff-
  equivalent to a same-type fly shifted down by `wing_width`, so it is **not** automatically
  risk-free the way a completed fly is: the floor is genuinely `(credit1 + credit2 - wing_width) *
  100 * qty - fees`, which can land negative even after both gates pass their price check, and
  `position_floor`'s `iron_fly` branch never assumes otherwise. When both completion paths clear
  their gates on the same iteration, the position takes whichever leaves the higher post-fee
  floor. `completion_modes` defaults to `["debit"]` everywhere else, so no other arm's behavior
  changes.

**A global position cap does not make a multi-window arm test its windows.** `max_positions` alone let
the book fill in the first window: over 07-20…07-24 `time_window` put 15 of its 16 legged entries in
`10:30-11:00`, 1 in `12:30-13:00` and 0 in `14:00-14:30`, so the timing hypothesis was never exercised
and the per-window ranking had nothing to rank. `max_positions_per_window` (off unless set; live on
`time_window` at 2) caps what any one window may spend. This is the same failure the arm's config
`_history_note` already records once — a shared cap being exhausted before the contrast can happen.

## The honesty rules

These are the constraints the module exists to enforce. Breaking one makes the numbers worthless.

1. **Every result is net of the modeled fee and slippage stack.** This suite has already recorded a
   trade collecting $4.00 against $4.96 of fees. Gross credit is not a result.
2. **"Risk-free" is a measurement, never an assumption.** `position_floor` is computed after fees and
   `is_risk_free` can and does return `False` for a fly with a positive gross credit.
3. **A per-position floor and a book-level floor are different claims.** `book_floor` returns
   `unbounded_below` and a price `band` precisely so a book leaning on open short verticals is never
   reported as unconditionally safe.
4. **The uncompleted branch is reported separately.** When a legged entry never completes, you are
   holding an ordinary credit spread with full defined risk. `completion_rate` is expected to be the
   number that decides whether this strategy is real.
5. **No adjustments after establishment.** No stops, no wing moves — hold to cash settlement. v1 is
   measuring a base rate, and an adjustment rule tuned before a single completion rate exists would be
   fitting noise. **One narrow, mechanical exception** (added 2026-07-30, applies to both paper and
   live, and to both a completed fly and a still-open short vertical): `engine.evaluate_pre_close_exit`
   closes any ITM leg in the closing minutes (`pre_close_exit_time`, default 15:50) whenever doing so
   costs less than the $5/contract exercise-assignment fee it would otherwise incur overnight — a cost
   comparison, not a P&L-driven stop or a strategy adjustment tuned on the session's own data. For a
   fly this is pure fee avoidance (the payoff is already bounded); for a vertical it stops the fee from
   stacking on top of a loss the position is already realizing. A vertical is only ever considered once
   its own entry has confirmed and any resting completion order is gone, so it never races a working
   order.
6. **If the floor comes out negative after fees, that is the finding.** The answer is to stop, not to
   loosen `fee_buffer` until the numbers look better.

## Guardrails (suite-wide)

- Paper by default; live is a deliberately narrow, per-day-armed pilot (one arm, one symbol, one
  incomplete position at a time — see `live_loop.py` and docs/live-trading-plan.md). SPX/XSP only —
  both European cash-settled, so EARLY exercise is structurally impossible and there is no
  early-exercise machinery to get wrong. Cash exercise/assignment at expiry is NOT impossible,
  though, and is not free: tastytrade charges $5/contract on every ITM leg the next business day
  (confirmed against a real overnight charge, 2026-07-30) — modeled throughout (`fly.expire_fee`,
  `itm_contracts_at_settlement`) and the reason `engine.evaluate_pre_close_exit` exists at all.
- **No AI, no MCP, and no network on any decision path.** `fly.py` and `engine.py` are pure functions
  over a pre-fetched snapshot. Learning happens offline in the orchestrator's read side (`report`,
  `calibrate`, `eod-insight`) over closed rows — never inside the loop.
- **The streamer comes before API calls** whenever practical, for efficiency or latency: all pricing
  reads the shared stream cache, and cached quotes GATE broker calls (a resting entry order is only
  cancelled/replaced when the cached evaluation moved; fill-status polls fire only when cached quotes
  touch the working limit, plus a slow heartbeat). The broker API is only for acting (place/cancel)
  and for confirming what only it can know — a fill. Applies to all future live work in this module.
  **One narrow, deliberate exception** (added 2026-07-30 after a live entry was rejected by the
  broker's real-time execution-quality check on a cached price its own preflight dry-run never
  flagged): immediately before submitting a live entry — never on the per-tick decision path, never
  in paper — `live_orders.entry_fresh_reprice` re-fetches both legs once via a plain REST market-data
  call (`broker_cli.fresh_option_quotes`, no streaming session) and submits at that fresh price,
  or skips the entry this tick if it's unavailable or has moved against us beyond
  `live.fresh_quote_tolerance_dollars`. The decision of *whether/what* to enter is still 100%
  cache-driven; only the final submitted price gets a last-second freshness correction, at the exact
  moment the broker is already about to be touched anyway.
- Credentials in the OS keyring only. Account numbers masked to `****1234`.
- Portable paths only; scratch work in `.tmp/`. Human-voice docs and commits, no AI attribution.
- Instruction files hold no code.

## Status

**Complete and tested:** decision engine, floor accounting, paper DB, snapshot provider, session
driver, CLI, and the orchestrator `fly_book` wiring across all four schema registries. 300 tests,
including a provider suite built against the real `cherrypick.core.streamcache` DDL so an upstream
schema change fails here rather than silently producing empty snapshots. The package runs in CI (its
own cell in the `.github/workflows/ci.yml` matrix, `ruff` + `pytest` on every push and PR).

**First live paper session: 2026-07-20.** Eleven structures, 80% completion rate, +$14.89 net —
which is the floor and nothing more, since no fly finished inside its wings. Fees were 82% of gross.
Two things to keep watching, both visible in that one session: completions arrived only after 10–21
points of drift away from the centre (the mechanism that makes completion cheap is the one that
walks spot out of the wings), and `control` vs `time_window` wanted the identical centre on 141 of
141 shared iterations, so only the disjoint windows separate them. `gex` vs `control` disagreed 84%
of the time and is the comparison with real power.

**Five sessions in (07-20…07-24), the uncompleted branch is the whole result.** Rule 4 said completion
rate would be the number that decides this, and it now has a threshold to clear. Settled: 40 legged
entries, 23 completed. Every completed fly made money (avg **+$110.47**, min +$51.86 — the floor doing
what it promises). The book still lost **−$1,175**, because the 17 misses averaged **−$208.51** each,
and 4 outright flies lost on all four. A miss costs ~1.9× what a completion earns, so break-even
completion rate is **≈65%** against **57.5%** observed. 07-24 settled on the official print (7411.98,
confirmed), so its four inside-wings flies stand — but they carry ~half the positive P&L, and one
session driving the result is a concentration caveat, not a validation.

Three changes came out of that, all 2026-07-27: `min_floor_dollars` 50 → **10** (the old value assumed
refusing a completion frees the position slot; it does not — it leaves the losing short vertical, so
turning down a guaranteed +$9.36 to keep a lottery averaging −$208 was backwards; 5 completions were
blocked by that gate alone at floors of $9.36–$39.36), `entry_modes` → **legged only** (outright lost
4 of 4 and only `gex` was taking them, quietly confounding gex vs control), and the `wide_wing` arm.
None of this separates the arms — 40 entries over 5 sessions, and the 50%/62%/62% spread is 2 trades
wide. These are mechanism and accounting changes, not signal findings.

**Settlement is marked in the database, not on disk.** `session_already_settled` asks whether every
`fly_books` row for the day is `settled`. It used to ask whether `paper-eod-<day>.md` existed, which
made the marker settable by anything that could write a file — on 2026-07-20 a test run against the
real managed home created that file mid-session, the loop read its own day as finished, and eleven
positions went unsettled under a report describing a fixture. A marker for "settlement happened"
must be writable only by settlement. Tests are isolated by an autouse fixture in `tests/conftest.py`
rather than one each test opts into, for the same reason.

**Settlement is approximate.** `--settle` defaults to the last streamed trade, which is close to but
not the official settlement print. The difference is systematic rather than random, and a position
centred within a point of spot can settle on the wrong side of its centre because of it. Pass
`--price` with the official print for any book whose result matters.

## If this ever goes live

The engine already returns decisions rather than performing fills, which is the same split MEIC uses,
and `cherrypick.core.broker` carries the write path and governor. Two things must be resolved first,
and neither is a detail:

- **Legging is where live diverges hardest from paper.** In paper the completion gate is a clean
  inequality. Live, step 1 fills and step 2 is a working limit that may sit unfilled or fill worse — so
  the completion rate measured here is an **upper bound** on the live rate, not an estimate of it.
- **`fund_from_open_credit` needs a real buying-power check.** Funding an outright fly from a still-open
  credit spread spends premium that has not been earned.

The full plan — the quantitative Gate 0 the paper experiment must pass first, how both blockers
resolve (a 1-lot measurement pilot with an abort rule for the first; legged-only live v1 mooting the
second), the live-loop architecture, kill switches, the fee-math symbol decision, and the rung-by-rung
rollout — is [docs/live-trading-plan.md](docs/live-trading-plan.md). Until Gate 0 passes, the only
work it calls for is running the paper experiment honestly.
