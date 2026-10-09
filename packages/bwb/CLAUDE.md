# cherrypick-bwb

> **Vocabulary.** This module's **book** is the suite's **arm** (root `CLAUDE.md`). The column
> (`bwb_positions.book`) moves with the rest of the schema; prose and the console already say `arm`.

A daily-laddered SPX put broken-wing butterfly; the root file gives the posture. Every book enters
the IDENTICAL BWB from the same plan on the same tick and differs only in whether/when the add-on
fires. Ledger schema: **`bwb_132`**. The narrow live path (2026-09-18) is one arm, armed per day,
writing its own ledger. History and incident narratives: [docs/history.md](docs/history.md).
Suite-wide context: the root [documentation index](../../docs/README.md).

## The base structure (every book, identical)

Entered every session at one fixed tick, `entry_time` (default 10:00 ET — after the open settles,
doubly so here because a zero credit floor cannot screen out an illusory opening-quote credit).

- **Expected move**: `cherrypick.core.structures.expected_move()` on the target expiration's ATM
  straddle mids from the stream cache. A missing straddle quote refuses `no_expected_move`.
- **Body (short x2)**: nearest listed strike to `spot - expected_move`. **Near wing (long x1)**:
  one increment above ($5). **Far wing (long x1)**: two increments below ($10). Fixed shape.
- **Entry gate**: must price as a net credit at mid; any positive credit qualifies. `credit_floor`
  is a declared zero, a deliberate departure from `min_credit_pct_of_width`. Else `no_credit`.
- **Expiration**: the NEXT PM-settled weekly Friday strictly after today (root `occ_root`, SPXW),
  so Mon–Thu enter that week's Friday, Friday enters the next: a 1-7 DTE ladder. The third Friday
  trades on its PM weekly; the AM-settled monthly is kept out by the root filter, never by the
  calendar. Computed from the calendar, asserted against the cache, never nearest-matched from the
  chain. Plans flag `am_monthly_date`. **Deferred (backlog, not a break yet):** nearest-to-
  `dte_target` with a minimum-DTE floor; `dte_target` stays in `defaults`, reserved and unused, and
  must NOT be advisable (an advised value would do nothing).
- **Cadence**: a new BWB every session per enabled book, ~5-7 concurrent per book. Position
  identity is `(symbol, book, entry_session)`.

## The experiment design — four books, one variable each

| book | add-on trigger |
|---|---|
| `control` | never — the BWB rides alone to expiry |
| `delta` | the near wing's \|delta\| reaches `delta_trigger` (50Δ) — raw proximity |
| `bounce` | peak \|delta\| since entry >= `delta_trigger` AND current <= `delta_trigger - bounce_pullback` (45Δ). No `bounce_peak` key: delta and bounce differ by exactly one condition |
| `flip` | spot traded below `gamma_flip` since entry AND reclaimed to >= `flip * flip_buffer` (1.001) |

Advised twins follow `packages/core/CLAUDE.md`. bwb specifics: **deliberately UNBOUNDED** (no
`advice.bounds` at all); the pre-2026-09-17 tags `advised:control` / `advised:delta` still resolve
by name in `engine.base_book` and `management.effective_params`; the `wall` book is never an advised
base.

**The add-on** (identical for all three arms): a put credit spread bracketing the far wing — SELL
one increment above, BUY one below — which must itself price as a credit (`addon_credit_floor`, a
declared zero). Once triggered the position is `armed`; every tick re-prices, a non-credit tick is
`addon_not_credit` and the arm stays live until the first credit tick fires it. An armed add-on that
cannot price at all is recorded as a collapsed `addon_blocked:<reason>` decision (one counted row per
session), so "waiting for a credit" and "cannot read the chain" never share a silence. **One add-on
per position**, then the trigger disarms for good. Armed until expiry, no cutoff. After firing,
**hold everything to expiry** on every book — early exit is reserved for a future experiment.

**A missed add-on is skipped for good** (`bwb addon-missed --position-id ... --reason ... --apply`,
dry run without `--apply`). When the loop was down while a trigger may have been met (the
2026-10-08 power outage, 10:35-16:00 ET), the trigger is not reconstructed (rules 4 and 7): the
position is stamped `addon_missed_at`/`addon_missed_reason`, management holds it as
`addon_missed` and never arms or fires it, its latches keep updating, and `fire_counts` reports it
as `missed`, outside the fire rate. A later measured fire would be a different, later trade than the
rule would have made.

**Trigger cadence**: the in-session 60s resident loop, not the entry tick. Triggers are defined on
the 60s SAMPLED series, so the loop cadence is part of the instrument — changing it is a journaled
measurement break (flies' 60s→15s precedent).

**Latch state persists on the position row** (`peak_abs_delta`, `below_flip_seen`, `armed_at`),
updated every measured tick, so a supervisor restart cannot amnesia a morning touch.
`triggers.derive_latches_from_ticks` re-derives them from `bwb_trigger_ticks` as the integrity
cross-check.

**gamma_flip basis**: recomputed each tick from the stream cache via `cherrypick.core.gex` — MEIC's
basis, NOT the GEX recorder's ~5-min history, so a stalled recorder cannot freeze the trigger. The
basis is stamped on every trigger row. A chain with no OI cached yet refuses `insufficient_gex_data`,
so `flip` cannot arm until OI accumulates.

### The `wall` book (opt-in, 2026-08-31) — outside the paired design

`books.wall` trades a CALL-side BWB with the body at the GEX call wall (read off the same
`gamma_flip_reading` as the flip trigger): +1 near (one increment below) / −2 body / +1 far (two
above), net credit required. It tests whether the wall BOUNDS price over a week (the gex pin study:
close at or below the morning wall 19–21 of 23 sessions; a pin bet captured 2/23), so its result
**never pools** with the four base books.

It is NOT in `engine.BOOKS`, so "every book enters the identical BWB" stays true. It never arms
(`triggers.evaluate` returns fired=False for an unknown book) and has no add-on yet; its trigger-tick
cohort records the call-side candidates so a future add-on is a replay away (and its own break).
Its spread gate is percent AND absolute money (`max_leg_spread_abs`, since OTM calls zero-bid); the
put books' percent-only gate is deliberately untouched, since changing what they admit is a break.
Settlement is option-type aware (`engine.settle_intrinsic`); a test pins the call mirror, because a
transposed intrinsic would book the wall book's max loss as a win.

## Pairing, samples and the trigger tick path

Until an arm fires, its positions are byte-identical to control's — an expected
`find_identical_readings` collision. Each arm-vs-control comparison's effective sample is its
**fire count** (`analytics.fire_counts`), not its trade count. `delta` fires most, `flip` least; a
quiet `flip` book is the honest state (pmcc keltner precedent). **Ladder rows are correlated**: one
selloff can fire a trigger across several overlapping positions, so read fires by distinct
*episode*, not by position, and surface that beside the counts.

`bwb_trigger_ticks` is the module's second product: every tick, per open COHORT
(`entry_session, structure_signature`), it records near-wing delta, peak delta, spot, gamma_flip and
the below-flip latch — identical across the four base books. That keeps "when would each trigger
have fired on control" answerable, and makes a forward-recorded read-side replay possible (the
calendars `exit_policies` pattern — never vendor-imagined). `trigger_coverage()` splits `measured`
into `spot_measured` / `flip_measured` and carries a `total_failure` flag: ticks recorded with none
measured is a defect, not thin data (four such sessions went unnoticed; see history).

**The add-on bracket's own quotes are recorded only from 2026-09-26** (`engine.addon_bracket`, the
one rule `plan_addon` also uses). Anything priced off tick quotes starts at 2026-09-28.

### The add-on as its own trade (`addon_replay.py`, `bwb addon-replay`)

Read-side, no new loop: over 08-24..09-25 delta's add-on made 73% of its gross on 19% of its
buying power over time. Three views, paired to delta's cohorts:

- **`addon-only`** — delta's add-on alone, from the legs actually filled (entry mids, settled
  values, its own fee and slippage, the $5 settlement fee on ITM legs). Trigger timing is replayed
  from recorded deltas and must land on the session the real arm armed (`validation`; 23/23 at build).
- **`same_spread_other_timing`** — bounce's and flip's add-ons: what timing alone is worth.
- **`spread-daily`** — the same bracket sold at the cohort's entry tick, no trigger; priced from
  tick quotes, so history only from 2026-09-28 (`unpriced_cohorts` lists the rest).

Count results by settlement Friday, never by fire. A paper `addon-only` arm (and `spread-daily`
twin) follows only if the replay holds up, at a declared boundary — adding arms is measurement-
affecting.

## Honesty rules

1. **Net of the full fee and slippage stack.** Entry 4 legs/2 sells, add-on 2 legs/1 sell, and the
   $5 cash-settlement event fee per DISTINCT ITM symbol (the doubled body is one).
2. **Settlement fidelity is a stated caveat**: paper settles at intrinsic against the last cached
   tick, not the official print — uniform across arms. The live ledger settles on the official
   print only (hand-supplied `--price` or the broker chain's posted close) and waits rather than guess.
3. **A hole in the mark path is refused, never zero** (`usable = 0` with the refusal).
4. **A trigger fires only on a measured tick.** Missing/stale near-wing greeks or GEX inputs mean no
   evaluation that tick — never guessed, never carried forward; peak delta advances only on
   measured ticks.
5. **Correlated ladder rows** are surfaced, not buried.
6. **Zero credit floors are declared** in config `_note`s.
7. **No fallback paths in v1** — triggers refuse on missing data rather than degrade.
8. **Measurement breaks are journaled rows** (`measurement_breaks`).
9. **`bounce_pullback` must stay above zero** — config-lint guards it; at zero bounce equals delta.
10. **Never pool across a journaled break.** `trigger_ticks_unmeasured` (2026-08-24..27, every row
    `measured = 0`) must not pool with later rows; rows before 2026-09-18 carry an overstated
    `entry_max_loss` and a doubled body settlement fee, derivable and not rewritten (history doc).

SPX is cash-settled and European: an expiring leg books intrinsic against the settlement print; no
shares, no assignment, no dividend calendar. The event fee lands the next business day.

## Live (2026-09-18) — one arm, per-day armed, every fill the broker's word

Built to test whether the paper result survives a fill without disturbing the paper books.

- **Gating** (guardrail): `live.enabled`, `live.gate0_confirmed`, a per-day arm record
  (`/live-bwb-start`, a literal YES each day), a designated account, no suite halt flag, and
  `live.arm` naming a base book — every one re-checked on every tick and every submission, all
  guarded from the settings surface. Never run `--install-task` outside `/live-bwb-start`.
- **The structure is the paper structure.** `engine.plan_entry` plans it; `live_orders.entry_spec`
  only collapses the body into one sell leg at double quantity with a limit (mid minus
  `entry_concession`, floored to the nickel). Live-only rules REFUSE, never reshape: the
  cost-derived floor (fees plus `min_net_credit_dollars` per contract, the broker's dry-run fee
  estimate replacing the schedule when given; modelled slippage deliberately excluded — a limit fill
  IS the credit); `max_structures_per_day` (1; a cancelled entry spends nothing); total and
  per-expiration worst-case margin caps from expiry payoffs, RESERVING an unfired position's future
  add-on whenever the arm can fire; the settled-net breaker; and the mark-drawdown breaker, which
  blocks the NEXT entry only, never an exit.
- **No new live risk on a quarter-end session** (`core.calendar.is_quarterly_expiry`, refusal
  `core.live.QUARTER_END_REASON`): the entry refuses, and an armed add-on is deferred (not lost: it
  fires on the next session's first credit tick). Paper is untouched; arming warns. First binding
  session 2026-12-31, journaled on the live ledger.
- **Every fill is the broker's word.** An entry row is born `pending` (not marked or managed); the
  actual credit overwrites the modelled one on confirmation, realised slippage is measured against
  the mid at submission, and a terminal order leaves a `cancelled` row. The add-on records only a
  pending marker; its legs are written (through the paper writer, actual credit) only on broker
  confirmation. A dead add-on clears the marker and may re-fire with a new attempt suffix. One
  add-on order per tick across the ladder.
- **A resting order walks down, bounded**: one tick every `reprice_after_minutes` from the fresh
  mid's limit, never under the row's live floor, lifting with a rising mid (only the step count is
  monotonic); cancelled at `entry_cutoff`, `entry_cancel_after_minutes`, or when strikes move. A
  refused cancel waits for the next poll — never a second order over one that could not be cancelled.
- **No closing orders.** Settlement on an official print or not at all. Costs are estimates until
  `fee_reconcile.py` replaces them with real transactions (exact matching, modelled values
  snapshotted once, unmatched rows left alone).
- **Identity travels with the order**: the ledger key is the order's external identifier, so an
  uncertain submission is recovered by it (`cherrypick.core.execution`); an unreadable outcome HOLDS
  the adapter (`broker_held` in `--status`).
- **The paper books are untouched.** `paper_loop._manage_positions` has a `fire` seam defaulting to
  the old behaviour (a test pins it). The live ledger is a separate file paper surfaces never read,
  its own evidence with the same correlated-ladder caveat. Switching `live.arm` is a live
  measurement break — journal it.

## Layout

| file | role |
|---|---|
| `clock.py` | ET clock; PM-settled expiration selection with the AM-monthly shift. Pure. |
| `engine.py` | expected move, BWB and add-on construction and credit checks, worksheet, intrinsics, fee stack. Pure. |
| `triggers.py` | the three trigger conditions — pure, the module's core IP. |
| `provider.py` | entry/mark/trigger snapshots from the cache; the gamma_flip read; refuses rather than guesses. |
| `management.py` | per-book verdicts (arm/fire/hold) + advised-params choke point. Pure. |
| `book.py` | decisions -> ledger rows; add-on legs; cash settlement. |
| `paper_loop.py` | entry tick, 60s trigger/mark loop, expiry settle. |
| `analytics.py` | the one query layer: nets, fire counts, trigger-tick coverage. |
| `replay.py` | read-side threshold replay over `bwb_trigger_ticks` — a stubbed fast-follow. |
| `addon_replay.py` | the add-on scored as its own trade (`bwb addon-replay`). |
| `db.py`, `stream_request.py`, `cli.py` | the standard trio (`status`/`worksheet`/`fires`/`triggers`/`headline`/`replay`/`addon-replay`). `db.live_db_path()` is the live ledger; `stream_request.register(live=True)` writes `bwb-live`'s request file. |
| `live_loop.py` | the LIVE tick: dead-man's switch, orphan sweep, fill confirmation, the walk-down, official-print settlement, ONE gated entry attempt, then the paper pass over the live ledger with the fire seam swapped for order placement. |
| `live_orders.py` | pure: order specs, the live floor, the walk-down's next limit, worst-case payoffs, margin caps with the add-on reserve. |
| `broker_cli.py`, `credentials.py` | keyring service `bwbagent` (falling back to the shared login) and `live_gates`. Serializer, tick rounding, settlement price and arm record come from `cherrypick.core`, not copies. |
| `fee_reconcile.py` | replaces a settled live row's estimated costs with the broker's cash flow; run by the live tick and by hand. |

## Commands

```bash
python -m cherrypick.bwb.paper_loop --once        # one gated tick
python -m cherrypick.bwb.paper_loop --interval 60 # the in-session resident loop
python -m cherrypick.bwb.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.bwb.paper_loop --settle --date 2026-09-18 --price 6400.10  # official print
python run.py status | worksheet | fires          # positions + expiration / worksheet / fire counts
python -m pytest                                  # temp CHERRYPICK_HOME; no broker, no streamer
ruff check . && ruff format .                     # line-length 110

python -m cherrypick.bwb.live_loop --once         # LIVE dry-run smoke: preflights, places nothing
python -m cherrypick.bwb.live_loop --status       # armed_for / pending / orphans / breaker / broker_held
python -m cherrypick.bwb.live_loop --settle --price 6400.10 --date 2026-09-18   # official print
python -m cherrypick.bwb.fee_reconcile            # settled live rows vs broker transactions
```

`--once --live` is the real tick; `--install-task`/`--uninstall-task` are what `/live-bwb-start`
calls. Config: `config.example.json` -> `config.json` (git-ignored) or `~/.cherrypick/config/bwb.json`;
the example is the design document — read its `_note` keys first.

## Data source and guardrails

- **The paper loop holds no credentials**; only the live loop reads `bwbagent`, and nothing else
  imports `credentials.py`. Held expirations come from the `expirations` request field.
  **`window_hints` is load-bearing**: the body sits a full expected move below spot and the window
  must also cover the add-on bracket two increments below the far wing — escalated on recorded
  `no_strikes_in_window` refusals.
- **The decision path is deterministic**: `clock.py`, `engine.py`, `triggers.py`, `management.py`
  are pure over pre-fetched data.
- Settlement is always `cash`; the module trades exactly one underlying.
- Scratch work in `.tmp/`. Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`),
  never opt-in.
