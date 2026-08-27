# cherrypick-bwb

bwb: a **daily-laddered SPX put broken-wing butterfly** entered at the expected move for a net
credit, ~7 DTE, held to expiry — **paper-only and credential-free**, a pure stream-cache consumer
in the calendars/pmcc/curve posture. Every book enters the IDENTICAL BWB from the same plan on the
same tick; the books differ only in whether/when a reversal-triggered put credit spread add-on
fires, turning the fly into a 1-3-2. Ledger schema: **`bwb_132`**.

Lineage note: this module absorbs and supersedes the "1-3-2 put condor" idea briefly floated as a
fourth book for `packages/ratios` (2026-08-22). The condor variant is retired; `ratios` stays jade
lizard / backratio / LT112. What survived: the 1-3-2 shape, the add-on-as-plain-matched-vertical
insight (keeps the combined position defined-risk by construction), and the reversal-not-falling-
knife entry philosophy.

Suite-wide context is the root [documentation index](../../docs/README.md).

## The base structure (every book, identical)

Put broken-wing butterfly on SPX, entered every session at a single fixed tick (`entry_time`,
default 10:00 ET — entries after the open settles, matching the suite's SPX timing research, and
doubly here because the credit gate has no floor to screen out an illusory opening-quote credit).

- **Expected move**: `cherrypick.core.structures.expected_move()` on the target expiration's ATM
  straddle mids, read from the stream cache at the entry tick.
- **Body (short x2)**: the nearest listed strike to `spot - expected_move`.
- **Near wing (long x1)**: one strike increment ABOVE the body (toward spot) — $5 on SPX.
- **Far wing (long x1)**: two strike increments BELOW the body — $10. Fixed shape; no wider search.
- **Entry gate**: the whole structure must price as a net credit at mid. Any positive credit
  qualifies — a deliberate departure from the suite's `min_credit_pct_of_width` convention;
  `credit_floor` is a declared zero. Not a credit -> recorded refusal `no_credit`.
- **Expiration**: ~7 DTE, PM-settled only. When the nearest date is the AM-settled third-Friday
  monthly, the plan shifts to the nearest PM weekly, ties broken toward the LONGER date. Computed
  from the calendar, asserted against the cache — never nearest-matched from the chain. A missing
  ATM straddle quote refuses `no_expected_move`.
- **Cadence**: daily ladder — a new BWB every session per enabled book, so ~5-7 positions ride
  concurrently per book at steady state. Position identity is `(symbol, book, entry_session)`.

## The experiment design — four books, one variable each

| book | add-on trigger |
|---|---|
| `control` | never — the BWB rides alone to expiry |
| `delta` | the near wing's \|delta\| reaches `delta_trigger` (50Δ default) — raw proximity |
| `bounce` | peak \|delta\| since entry >= `delta_trigger` AND current <= `delta_trigger - bounce_pullback` (45Δ at defaults). No separate `bounce_peak` key: the qualifying bar is `delta_trigger` itself, so delta/bounce differ by exactly one condition by construction |
| `flip` | spot has traded below `gamma_flip` at some point since entry AND reclaimed to >= `flip * flip_buffer` (1.001, the curve `contango_max` precedent) |

Plus `advised:<base>` (paper, off by default; this module is deliberately UNBOUNDED — see
config's `advice` block).

**The add-on** (identical construction for all three arms): a put credit spread bracketing the far
wing — SELL one increment above it, BUY one increment below. Must itself price as a credit
(`addon_credit_floor`, also a declared zero). **Trigger latching**: once met, the position is
`armed`; every tick re-prices the add-on — a non-credit tick is `addon_not_credit` and the arm
stays live — until the first credit tick fires it. **One add-on maximum per position**; after
firing, the trigger disarms permanently. **Armed until expiry, no cutoff.** **After firing: hold
everything to expiry** — no profit-take, no stop, on any book; early exit is reserved as a future
advisor experiment.

**Trigger cadence**: evaluated on the in-session 60s resident loop, NOT the daily entry tick — a
reversal at 1pm must not wait for tomorrow. Triggers are defined on the 60s SAMPLED series, not the
continuous path: a 50Δ touch between ticks does not exist by definition, which makes the loop
cadence part of the measurement instrument — changing it is a journaled measurement break, same as
flies' 60s→15s precedent.

**Latch state persists on the position row** (`peak_abs_delta`, `below_flip_seen`, `armed_at`),
updated on every measured tick — never held only in loop memory, so a supervisor restart mid-
session cannot amnesia a morning touch. `bwb_trigger_ticks` can re-derive the latches
independently (`triggers.derive_latches_from_ticks`), which doubles as the integrity cross-check.

**gamma_flip basis**: recomputed fresh each tick from the stream cache via `cherrypick.core.gex` —
the same basis MEIC's own gate reads, NOT the GEX recorder's ~5-min history, so a stalled recorder
can never silently freeze this module's trigger. The basis read is stamped on every trigger row.

## Pairing, collisions, and effective sample

Until an arm's add-on actually fires, that arm's positions are byte-identical to control's — an
expected `find_identical_readings` collision, not a defect. Each arm-vs-control comparison's
effective sample is that arm's **fire count** (`analytics.fire_counts`), not its trade count. The
three arms will NOT fire equally often: `delta` fires most, `bounce` needs the move plus a turn,
`flip` needs spot to have entered negative-gamma territory at all and come back. A quiet `flip`
book is the honest state, not a broken one (the pmcc keltner precedent).

**Daily-ladder correlation caveat:** concurrent positions share regime context — one sharp selloff
can fire the same trigger across several overlapping positions in one session. Rows are not
independent samples; the honest unit for "how often does this trigger help" is closer to distinct
fire *episodes* than fired positions. Recorded as an honesty rule, surfaced beside the counts.

## The trigger tick path is the module's second product

Every loop tick, for every open COHORT — `(entry_session, structure_signature)`, not per position —
`bwb_trigger_ticks` records the near-wing delta, peak delta, spot, gamma_flip (and the
below-flip-since-entry latch), byte-identical across the four base books that share one signature.
Two reasons: (1) counterfactual on control — control's cohort rows carry the same measures, so
"when would each trigger have fired on the untouched book" stays answerable read-side; (2) a
read-side threshold replay (`replay.py`, a fast-follow, not required for v1) becomes possible over
data this module itself recorded — the calendars `exit_policies` pattern, forward-recorded, then
replayed, never vendor-imagined.

## Honesty rules

1. **Net of the full modeled fee and slippage stack.** Entry is 4 legs/2 sells, the add-on 2
   legs/1 sell, and each distinct ITM leg at settlement pays the $5 cash-settlement event fee.
2. **Settlement fidelity is a stated caveat, not a bias**: paper settles each leg at intrinsic
   against the last cached tick, not the official closing/SET print — uniform across arms.
3. **A hole in the mark path is refused, never zero** (`usable = 0` with the refusal).
4. **A trigger can only fire on a measured tick.** Missing/stale greeks for the near wing, or
   unavailable GEX inputs, mean the trigger cannot evaluate that tick — never a guess, never
   carried forward. Peak-delta tracking also only advances on measured ticks.
5. **Correlated ladder rows** — the pairing section's rule; surfaced, not buried.
6. **Zero credit floors are a declared design choice**, stated in config `_note`s.
7. **No fallback paths in v1** — the delta triggers refuse-on-missing rather than degrade.
8. **Measurement breaks are journaled rows** (`measurement_breaks`, the shared table shape).

## Settlement

SPX is cash-settled, European-style — the cleanest settlement model in the suite. An expiring leg
books intrinsic (`max(0, strike - spot)` for a put) against the settlement print; no shares, no
assignment, no dividend calendar. Every distinct ITM settlement symbol pays the $5 event fee the
next business day.

## Layout (mirrors curve/pmcc)

| file | role |
|---|---|
| `clock.py` | ET clock; PM-settled target-expiration selection with the AM-monthly shift. Pure. |
| `engine.py` | expected-move read, BWB construction + credit gate, add-on construction + credit check, worksheet math, cash-settlement intrinsics, fee stack. Pure. |
| `triggers.py` | the three trigger conditions over (tick telemetry, position state) — pure, the module's core IP. |
| `provider.py` | entry/mark/trigger snapshots from the stream cache; the gamma_flip read; refuses rather than guesses. |
| `management.py` | per-book verdicts (arm/fire/hold) + advised-params choke point. Pure. |
| `book.py` | decisions -> ledger rows; add-on leg append; cash settlement. |
| `paper_loop.py` | session driver: entry tick, 60s trigger/mark loop, expiry settle. |
| `analytics.py` | the one query layer: per-book nets, fire counts, trigger-tick coverage. |
| `replay.py` | the read-side threshold replay over `bwb_trigger_ticks` — a FAST-FOLLOW, not built in v1. |
| `db.py`, `stream_request.py`, `cli.py` | the standard trio (`status` / `worksheet` / `fires` / `triggers` / `headline` / `replay`). |

## Commands

```bash
python -m cherrypick.bwb.paper_loop --once        # one gated tick
python -m cherrypick.bwb.paper_loop --interval 60 # the in-session resident loop
python -m cherrypick.bwb.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.bwb.paper_loop --settle --date 2026-09-18 --price 6400.10  # official print
python run.py status                              # open positions + target expiration
python run.py worksheet                           # the live per-position worksheet
python run.py fires                               # per-book add-on fire counts
python -m pytest                                  # temp CHERRYPICK_HOME; no broker, no streamer needed
ruff check . && ruff format .                     # line-length 110
```

Config: copy `config.example.json` -> `config.json` (git-ignored), or place
`~/.cherrypick/config/bwb.json`. The example file is the design document — read its `_note` keys
before changing a value.

## Data source

This module runs no streamer and holds **no broker credentials at all**. Its held expirations exist
in the shared cache because `stream_request.py` declares them via the registry's `expirations`
field every tick. `window_hints` is load-bearing: the body sits a full expected move below spot, at
or beyond a default ATM window's edge, and the window must also cover the add-on bracket two
increments below the far wing — escalated on recorded `no_strikes_in_window` refusals, the
flies/pmcc pattern.

## Known risks, stated up front

1. **The trigger tick path depends on a live `gamma_flip` read every 60s**, computed from the
   stream cache's own chain + greeks + OI. A chain with no OI cached yet (a symbol just requested)
   refuses `insufficient_gex_data` rather than guessing — the `flip` book simply cannot arm until
   OI accumulates.
2. **Settlement fidelity** — rule 2 above; a stated caveat, not modelled bias.
3. **Correlated ladder rows** — rule 5; read fire counts by episode, not by position, when the
   sample is small.
4. **The `bounce`/`delta` distinction depends on `bounce_pullback` staying above zero** —
   config-lint guards this; at exactly zero the two arms are mathematically identical.

## Guardrails (suite-wide)

- **Paper only. There is no live path.** `live.enabled` in config is a documented placeholder only.
- **The decision path is deterministic.** `clock.py`, `engine.py`, `triggers.py`, `management.py`
  are pure functions over pre-fetched data — no model, no MCP, no network in the decision itself.
- Declared settlement only (SPX is always `cash`); a symbol this module is not built for is out of
  scope by construction (it trades exactly one underlying).
- Credentials in the OS keyring only (this module holds none). Account numbers masked to
  `****1234`. Portable paths only; scratch work in `.tmp/`. Human-voice docs and commits, no AI
  attribution. Instruction files hold no code.
- Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`), never opt-in — the
  flies 2026-07-20 lesson.

## Status

Built 2026-08-23; trading paper since 2026-08-24, four open cohorts across the four base books.
`replay.py` is a stubbed fast-follow (the trigger-tick substrate is recorded from day one; the
reader over it is not).

**The trigger-tick substrate recorded nothing usable for its first four sessions
(2026-08-24..27) — 2,337 rows, every one `measured = 0`.** Journaled as a measurement break
(`trigger_ticks_unmeasured`); those rows must never be pooled with corrected ones. Two independent
defects, both fixed 2026-08-27:

- **`core.streamcache.greeks_for` never selected `gamma`.** `core.gex.compute_gex` skips any strike
  whose gamma is None, so the gamma-flip read failed on every tick and reported
  `insufficient_gex_data` — while the cache held gamma AND open interest for every symbol involved.
  **The `flip` book could not fire by construction for four sessions.** Three other modules call
  the same reader and were unaffected because they read only delta/iv/vega, which is why this
  surfaced here and nowhere else.
- **`_record_trigger_ticks` did not set `position_symbol` on its legs.** `bwb_legs` has no symbol
  column, so `build_mark_snapshot` resolved no underlying and wrote a NULL `spot` on every row. The
  marks path set it and the tick path did not; both now go through `paper_loop._legs_with_symbol`.

Worth keeping from how this hid. `trigger_coverage()` reported `refusal_share = 1.00` for four
consecutive sessions and it read as a number rather than an alarm — the same shape as the GEX
regime-history `LIMIT 60`, where a reader could not tell a quiet session from a truncated one. The
reading now separates the two halves of `measured` (`spot_measured` / `flip_measured`, with the
refusal naming both), and carries a `total_failure` flag, because *ticks recorded and none measured*
is a defect and not thin data. `near_abs_delta` was recorded correctly throughout and stayed in
0.05–0.39 against a 0.50 trigger, so `delta` and `bounce` were legitimately quiet — a book that
CANNOT fire and a book with no reason to fire looked identical in the ledger, which is the more
dangerous half of this.

**The add-on could never price, and that was a third defect found the same day.**
`_addon_snapshot` resolved `root = symbol`, so every lookup asked the cache for `SPX`-rooted
contracts while SPX's weeklies are listed as `SPXW` — `not_root_listed`, on every tick, for every
armed position. Every other snapshot in this module already resolved
`config.get("occ_root") or symbol`; this was the one that did not. The moment the gamma flip became
measurable the `flip` book armed all four of its positions (`arm` / `flip_trigger_met`, 10:39 ET)
and then sat unable to price a single one. With the root fixed, three of the four price a real
credit (0.25 / 0.45 / 1.05) and the fourth refuses `addon_not_credit` at −0.075 — the designed
refusal path, previously unreachable.

It hid for the same reason the trigger substrate did: **an armed position that cannot price
produces a `hold`, and holds are not recorded.** "Waiting for a credit" and "cannot read the chain
at all" were the same silence. An armed-but-unpriceable add-on is now written as a collapsed
`addon_blocked:<reason>` decision — one counted row per session, not one per tick — so the module
says what stopped it. `addon_not_credit` was always meant to be recorded; the path that reached it
was simply never taken.

Not yet wired into the orchestrator's supervisor job registry (a tracked fast-follow, not an
oversight); `packages/console` mirrors the integrity reading but has no dedicated page yet
(deferred until first real positions exist, the pmcc/curve sequencing).
