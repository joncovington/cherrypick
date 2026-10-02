# cherrypick-flies

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

## Layout

| file | role |
|---|---|
| `cherrypick/flies/fly.py` | payoffs, quote pricing, fees, position and book floor math. Pure. |
| `cherrypick/flies/engine.py` | centre selection, entry gates, the completion gate, settlement. Pure. |
| `cherrypick/flies/provider.py` | builds snapshots from the shared stream cache, read-only. No decisions. |
| `cherrypick/flies/paper_loop.py` | session driver: fetch, run every arm, settle at the bell. |
| `cherrypick/flies/book.py` | wires engine decisions to the paper DB; one book per (date, arm, symbol). |
| `cherrypick/flies/db.py` | `fly_positions` (ledger) and `fly_books` (roll-up with the floor's price band). |
| `cherrypick/flies/analytics.py` | the one query layer every read surface goes through. Read-only. |
| `cherrypick/flies/eod.py` | Report builders, retired 2026-08-13 (`packages/review` reports the session now). `logs_dir()` is still the loops' path helper. |
| `cherrypick/flies/cli.py` | `once` / `settle` / `status` / `regime` / `bands` / `replay-gates` / `hedge-overlay` / `reversal-book` / `fill-model` / `regime-cuts`. |
| `cherrypick/flies/live_loop.py` | The LIVE loop: a 1-min `--once --live` tick fired by the supervisor while the arm record (`state/flies-live-arm.json`, written per day via `/live-flies-start`) is valid; self-disarms at `live.disarm_time` by deleting the record. The arm record, both disarm reasons, the supervisor-heartbeat read and the record-only arming rule are `cherrypick.core.live` (thin wrappers here; the legacy schtasks fallback and pre-cutover record location stay flies-only); fill confirmation reads `cherrypick.core.execution.fill_state`. `--once` (dry-run default) is the rung-0 smoke; `--status`; `--settle --price` for the official print; burst fill-watchers `--watch-fills`. Every live tick marks each open position at mid into `fly_live_marks` — pure telemetry after every decision; an unquoted leg gets no row, never a zero. A mid is not a fill. Live only: paper's result is settled payoff by design. |
| `cherrypick/flies/broker_cli.py` | Thin broker seam on `cherrypick.core.broker` (preflight/governor); `--live` double-gated. The loop's adapter is `cherrypick.core.execution.Broker` with this module's session, account, `live_gates`, serializer and deploy cap injected; only the REST re-quote remains here. `official_settlement_price` is `cherrypick.core.settlement`'s, kept as a module attribute so the adapter and tests patch one seam. |
| `cherrypick/flies/live_orders.py` | Pure engine-decision → order-spec builders (OCC symbols from the provider). Tick rounding is `cherrypick.core.structures`. |
| `cherrypick/flies/alert_daemon.py` | Optional order-alert daemon: one account-alert websocket for the trading day, started on arm / stopped on disarm. Decides nothing — appends to the inbox below so fills are *noticed* sooner. |
| `cherrypick/flies/alerts_db.py` | The WAL-mode alert inbox (`live_alerts.db`), separate from the ledger on purpose — 1 writer (daemon), N readers (tick, watcher). |
| `cherrypick/flies/credentials.py` | `fliesagent` keyring store + hidden-input CLI (orchestrator `connect` delegates here). `designated_account` is `CredentialStore.designated_account()`. |
| `cherrypick/flies/fill_model.py` | Fill realism, pure: the three at-fill distances, spread prices and gaps, first-touch records, rule scoring, the paper shadow's outcome. [docs/fill-model.md](docs/fill-model.md). |
| `cherrypick/flies/fill_facts.py` | Fill realism, recorded: the live loop's order/path writers (`fly_live_orders`, `fly_order_path`), `rebuild`, and the dry-run-by-default `backfill` from broker transactions and the gex spot trail. |
| `tests/fixtures/books.json` | three real tastytrade order chains, transcribed. |

## The read side

**Everything reads through `analytics.py`**, and a test asserts the report's headline figure is exactly
what that layer returns — MEIC grew three call sites that disagree about what "net" means.

**Three journals, each kept separate on purpose.**
- `fly_decisions` records *why* every entry was made or refused, collapsing identical consecutive
  reasons into one counted run.
- `fly_iterations` records what each arm *wanted* on each iteration, before any gate could veto it —
  uncollapsed, because collapsing destroys what arm divergence needs.
- `fly_snapshots` records one row per (tick × symbol) of what the feed gave us: "ok" with quote counts,
  or the provider's refusal reason. A stretch of refused rows is a feed problem; a stretch with *no
  rows at all* is the loop not running — without it those two silences were identical. Written in
  `paper_loop.run_once` on both the built and the refused path; pure telemetry, no decision reads it.

**Five measurements this strategy needs and generic P&L reporting cannot give:**

- **Completion rate** — how often a leg-in became a fly. Near zero means short verticals wearing a
  costume. `analytics.completion_trend` gives it per session, because a blended rate can drift while
  looking stable.
- **The counterfactual** (`best_completing_debit`) — for misses, *the market never offered it* versus
  *our gate refused it*: identical in the P&L, opposite remedies. "Our gate" is two verdicts,
  `buffer_blocked` (`D < C - fee_buffer`) and `floor_blocked` (`floor >= min_floor_dollars`), read from
  the `fly_decisions` journal rather than recomputed, so they cannot drift from the gate as configured.
- **Completion latency** — a fly that took 40 minutes and 8 points of drift is far likelier to fill
  live than one that appeared for seconds. The paper-vs-live gap, measured.
- **The post-completion counterfactual** (`post_best_completing_debit`/`_credit`) — for completions,
  how much better the price got AFTER the first qualifying tick was taken. The stream cache keeps no
  quote history, so it is recorded live (`book.py` step 1d, pure telemetry, no gate reads it) or lost.
  `analytics.left_on_table` splits it by `completion_gex_bucket`. The decision record and the bar a
  wait-for-better rule must clear: [docs/completion-timing.md](docs/completion-timing.md).
- **Arm divergence** — how often arms picked different centres. **Only meaningful against an arm
  that centres differently** (`gex`): the ATM arms agree on centre by construction (100% over 184
  iterations), which says nothing about redundancy. Read `time_window` vs `control` on timing and
  completion, `width-N` vs `control` on width. Never read a structural identity as a finding.

**Fill realism, tag-don't-gate** (2026-10-02, [docs/fill-model.md](docs/fill-model.md)). Paper
completes on the first tick its modelled debit clears the gate and pays that debit; live rests a
limit at `max_safe_completion_debit` and pays the limit. 43 of the first 48 live completions filled
below the best modelled debit the live loop ever saw, so the gap between the two is measured, not
assumed:
- **Live** records every order (`fly_live_orders`, filled or not) and what it saw while it worked
  (`fly_order_path`, each tick and watcher cycle). At each fill it keeps all three distances (spot
  past the centre, spot past the completing long strike, the mid/natural gap to the limit), signed
  as `fill_model.py` defines them, plus the broker's own fill time and leg-fill price. **The
  ledger's live `debit`/`credit` are the order's LIMIT** (the status `price` field), so price
  improvement only shows in `broker_fill_price`. A row says `fill_time_source = 'noticed'` when
  the broker's time is unknown. Every write goes through `live_loop._telemetry`: a failure is
  logged, never raised into a fill confirmation or a placement.
- **Paper** stamps each legged entry with the limit a live completion would rest at
  (`shadow_completion_limit`) and keeps first touches against it every tick until settlement
  (`shadow_touches`), whether or not paper's own rule completed the position.
  `analytics.shadow_completion` replays it at any grid value. **Switching paper to a live-like
  rule changes what control's numbers mean: fit first, shadow second, switch only at a declared
  boundary.**
- **From 2026-10-05 every paper legged completion PAYS THE LIVE LIMIT** (`engine.pays_limit`,
  `completion_price` / `completion_price_from`). The trigger is unchanged: the modelled debit at
  or under the limit. The price is `engine.completion_limit`, the one formula live's resting order
  is priced from; live filled at it 42 times out of 42. The plan's `market_debit` keeps the
  modelled market price, and the best-debit counterfactuals record that, never the price paid. A
  book-wide `completion_rule` break (`paper_loop._note_completion_rule`): **never pool completion
  P&L across 2026-10-05.** The trigger's timing is the next step, fitted from quoted live paths
  against the shadow, as a second break.

**Two overlays on the legged book, both tag-don't-gate** (2026-09-19):
- **The hedge overlay** (`engine.hedge_candidate`, `book.py` step 1e, `analytics.hedge_overlay`,
  `run.py hedge-overlay`). At every legged entry the ~`hedge_delta` (0.05) option on the spread's
  losing side, beyond the long wing, is priced and stamped (`hedge_*` columns) and **never bought**;
  each tick keeps the running max of what selling it would fetch, and settlement records its
  intrinsic (0 when worthless, NULL only when no hedge was recorded — the read side keeps those
  apart). The sell-at-Nx replay over the running max **is an upper bound, not a fill**. Paper only;
  the spread's own decision and price are untouched.
- **The reversal book** (`analytics.reversal_book`, `run.py reversal-book`) pairs each settled
  `control` legged entry with the settled `debit_first` entry from the partner arm on the same losing
  side (`debit-first-down` for a put spread, `-up` for a call), same session, nearest in entry time
  within a window, each partner used once. A combined arm would break the one-variable rule twice. An
  unmatched base entry is reported, never paired with a distant partner.

**The regime-cuts artifact** (`run.py regime-cuts --write`, nightly 16:40 ET via `paper.regime_cuts_at`
/ `regime_cuts_argv`). Every arm with settled era rows, cut by every regime dimension and by the
declared cross-tabs (`analytics.CROSS_TABS`), written to `data/flies/regime_cuts-<session>.json` plus
a `regime_cuts.json` latest copy only a newer session replaces (`--session` re-cuts a past day,
`--backfill --since` fills a run). The contract is `cherrypick.core.regimecuts` (MEIC writes the same
shape); the console and the advisor's deep pack read it and recompute no cell. Rules that live in the
writer, not in any reader:
- The era is scoped by this ledger's `measurement_breaks`: the latest book-wide break on or before
  the session starts it; an arm added later starts at its own `arm_added` break; a future-dated break
  is listed and ignored until it passes; a `partial_session` break never bounds anything.
- Every cell carries `sessions`, stamped `thin` below three — a by-hand cut once made net-GEX sign look
  predictive (84% vs 75%) when the whole effect sat in one seven-session cell.
- With `--write` it also carries `gate_replay` (`replay_gates.sweep` over the advice base arm's era:
  miss stop 15..90 min, both trend-bucket refusals, every `entry_windows` choice), because the advisor
  reads only its pack — `miss-stop-90` was once proposed from latency quantiles when the replay said it
  would have cost $1,097. `entry_windows` has three fixed choices (control's own 10:00-14:30, a
  midday skip, 10:30); a test pins that control's own window is one of them.
- The core's robustness stamps (`fragile`, `paired`, `history`, `multiplicity`) come from
  `by_regime(..., with_sessions=True)`. First read: 1 of 46 same-day paired contrasts under p 0.10
  against ~4.6 by chance — pooled cells that look like findings are what a gate would be read
  against, not evidence the regime decides the entry.

**`drift_alignment`** is derived at read time, never stored: `with` / `flat` / `against` from
`completing_direction` against the stored `entry_trend_*` pair, with `flat` taken from the trend tag so
the band is the arm's own `regime_trend_points` — the cut and the `refuse_completion_against_trend`
gate can never disagree about "committed". Side alone is a coin flip; side × trend is the variable.
`by_drift_alignment` stays as the EOD version under `DRIFT_BAND_PCT`. Each book's summary also
carries `completion_latency_min` (p25/p50/p75/max) and `miss_gap` (`credit - best_completing_debit`
over uncompleted verticals), copied through `regimecuts.assemble` only when present so MEIC's books
carry neither. `by_regime`'s phase rename replaces every `entry_`, not the first; `test_regime_cuts.py`
pins it.

**The second cross-tab, gex × drift_alignment**, is declared module-side in `analytics.CROSS_TABS`, not
in `core.regimecuts.DEFAULT_CROSS_TABS` (an iron condor has no drift analogue), appended so
`cross_tabs[0]` stays the shared one. A three-way gex × trend × drift_alignment cross was refused: 27
cells at this sample is a fishing surface, and `trend` is already inside `drift_alignment`.

**The console's flies page has a time axis.** `analytics.session_timeline` assembles the day from rows
already written. `settle_now` replays the book at each tick as an expiry payoff at live spot — **not a
mark**, and the page says so. Replaying rewinds each legged row exactly (a short vertical until it
completes: pre-completion fee `vertical_open_fee`, net the recorded `credit`), never drawing a fly
before it existed. Both charts refuse to smooth: lines **break across a gap** rather than interpolate
(a straight segment over a 100-minute silence reads as a calm market), and the payoff curve draws one
line per arm, never a blended book.

## Data source

This module **runs no streamer**. `provider.py` reads the shared stream cache
(`~/.cherrypick/data/marketdata/stream_cache.db`) read-only, produced by `packages/streamer` from the
union of every module's `state/stream_requests/` file — this module rewrites its own every tick. Open
interest, and so GEX, exists only because the producer subscribes DXLink Summary for its ATM window.
**Each loop also declares its own open legs**, since spot leaving the ATM window is exactly when a
short vertical needs its marks: `fly_positions` carries four `*_leg_symbol` columns (the DXLink
streamer symbol, never OCC), stamped at entry and completion from the same quote the price came from;
`stream_request.py`'s `leg_sources` query points at each loop's OWN ledger (`flies.json` → paper,
`flies-live.json` → live). A live completing leg is stamped when the resting order is PLACED.

**The provider refuses rather than guesses.** Stale quotes (`max_quote_age_seconds`), crossed quotes,
missing spot, an empty chain — each returns `{"ok": False, "reason": ...}`, logged and stepped past.
Refusals are ordinary, not errors. `quote_stats` is recorded on every snapshot so a barren session
reads as "the data was thin", not "the strategy found nothing".

**GEX inputs are refused when stale or thin.** `max_gex_input_age_seconds` (1800 — OI is a
once-a-day snapshot) and `min_gex_strikes` (20); below that the surface is refused and
`select_center` degrades to ATM. `snapshot["gex_stats"]` carries fresh/stale/coverage. Without the
bound a dead feed produced a surface indistinguishable from a live one.

`cherrypick.core.fees` supplies the fee schedule and `cherrypick.core.gex.compute_gex` the per-strike
GEX profile — neither is reimplemented here.

## The arms

Separate books, each differing from `control` in **exactly one** thing. Every gate is shared, so each
comparison measures one variable. Keep `max_positions` equal across any compared pair, or the
comparison measures opportunity count instead of the variable. `engine.ARMS` is pinned by test to the
example config's arm set. Full history per arm: [docs/history.md](docs/history.md#the-arms-in-full).

- `control` — ATM, all day. The naive baseline without which a profitable arm proves nothing.
- `gex` — centre on the strongest positive per-strike net GEX near spot. Degrades to ATM when no OI
  is cached, recording `center_reason` so those samples can be excluded.
- `time_window` — ATM, only inside configured windows, which are **not** ranked: each trade is
  tagged with its window and the ranking comes from our own sessions. Its windows **straddle**
  control's rather than nest (nesting made the two identical but for opportunity count), and
  `max_positions_per_window` (2) stops one window spending the whole book — over 07-20…07-24 a global
  cap put 15 of 16 entries in the first window and the timing hypothesis was never exercised.
- `width-2` … `width-5`, `width-10` — control's twins pinning `wing_width_strikes`
  (`wing_width = strike_increment × wing_width_strikes`); `control` is the 1-strike rung, so there is
  no `width-1`. Defined in STRIKES since 2026-08-15 so the sweep survives a symbol switch; XSP-era
  rows under these names used raw point widths and are a different geometry — never pooled
  (`CURRENT_ERA` keys on symbol). A hypothesis, not a fix: if no width yields a fee-positive floor,
  the drift is fundamental to the mechanism, which is itself a result (rule 6).
- `wide_wing` — **disabled**, superseded by the sweep; kept in `ARMS` so its books stay readable.
- `debit-first` — isolates **legging order**: buy the debit vertical first, complete by *selling* the
  credit spread once spot drifts back toward the centre. Its uncompleted branch is bounded at the
  debit paid, never legged's `-W` tail. **GEX-centred** (`center_rule: "gex"`), so it differs from
  `control` in two things: read it against `gex` for legging order alone.
- `iron` — **RETIRED before it ever traded; keep the negative result (rule 6),
  [docs/iron-completion.md](docs/iron-completion.md).** Completing into an iron fly uses the identical
  strike pair, so put-call parity pins `D + credit2 = wing_width` for any skew: the two gates are one
  inequality, `iron net − W ≡ fly net`, and what is left is adverse cost (+$3.46/position, $495 over
  143 completions). Code kept and tested; disabled; `completion_modes` stays `["debit"]` everywhere.
  **`book.py`'s "take the higher floor" dispatch is wrong and must be fixed before any revival** —
  `fly` reserves 3 ITM strikes and `iron_fly` 2 (`fly.WORST_CASE_ITM_LEGS`) at different worst-case
  prices, so iron's floor reads $5.00 high everywhere.
- `bwb` — isolates **entry construction**: a broken-wing butterfly entered whole for a credit, near
  wing `wing_width`, far wing `wing_width * bwb_far_width_ratio` (a ratio so it scales with width).
  Until rolled it carries REAL negative tail risk (`wing_width - far_width`) that `position_floor`'s
  `bwb` branch never reports as bounded. The roll buys `centre −/+ wing_width` and sells the held far
  wing (a vertical of width `far_width - wing_width`), converting it to a symmetric fly once it
  clears its own price and floor gates. GEX-centred.
  - **The 25 bwb rows of 2026-08-04..08-06 are void** — the roll priced a spread 3× too wide and did
    not produce a butterfly. They carry `void_reason` and every read surface drops them
    (`db._VOID_BACKFILL`, `analytics.voided` states what was held back). A cutoff lives in data,
    never in prose.
  - **Side rule is `engine.choose_bwb_side` (`centre ≥ spot → calls`), never legged `choose_side`**:
    the legged rule put the roll spread in the money, where its intrinsic (5.00 on a 5-wide) can
    never clear `roll_debit < credit − fee_buffer`.
  - **Safety and credit trade directly**: pushing the tail away from spot shrinks the credit, so
    `min_bwb_credit_pct_of_tail` (0.15), not the price gate, is what binds. The roll trap it tests is
    in [docs/faq.md](docs/faq.md).
- `bwb-atm`, `debit-first-atm` — ATM twins of the two GEX-centred construction arms, restoring the
  one-variable rule: **X-atm vs `control`** isolates construction, **X-atm vs X** isolates centring. ATM
  means the structure straddles spot, deliberately. **No `spot + N strikes` arm**: `center_offset` is
  stored as a signed continuous float and re-cut with `by_regime(bucket_edges=...)`; what matters is
  placement relative to the drift, not raw distance. Build a placement arm only if the GEX arms'
  offset curve shows something, and make it drift-aware.
- `debit-first-up`, `debit-first-down` — the OTM debit-first pair (`center_rule: "delta"`,
  `engine._delta_center`, deltas from `provider._attach_deltas`): buy a debit vertical centred at
  `debit_delta_target` (0.15, a magnitude) away from spot and complete by selling the same-centre
  credit spread once spot walks into it.
  - **Delta, not a strike offset** — one trade all day. The chosen delta is stamped as
    `entry_center_delta` on **every** arm's rows, and `center_offset_value` is still recorded, so 0.15
    can be re-cut rather than cost a second pair.
  - **Two arms, one variable**: up and down differ only in `center_direction` (a test pins it). Should
    direction follow the day is a re-cut on `trend_bucket`, not a third arm.
  - **Refuse, never degrade**: no fresh delta, no strike within `debit_delta_tolerance` (0.05), or a
    spread not wholly beyond spot each return no centre with its own reason — an ATM fallback would
    trade the ATM arm's trade under this arm's name. Delta is filtered at the **quote** age limit.
  - `min_debit_pct_of_width: 0.02` (the shared 0.20 would refuse a 15-delta spread by design);
    `debit_cannot_be_out_earned` still applies. **Paper only** — `live_orders.py` builds legged specs
    alone; read against `control` over 15–20 sessions before live is a question. The post-completion
    counterfactual bites hardest here.
- `bwb-up`, `bwb-down`, `bwb-up-w2`, `bwb-down-w2` — delta-placed bwb pairs on the same rule and
  target as the debit-first pair, so the two constructions sit on the same strikes and differ in one
  thing (credit now with a tail vs debit now with a conditional completion). **The credit floor is not
  loosened**; refusal rows are the result. From 2026-09-30 each bwb attempt row, refused or filled,
  carries the structure in `proposed_legs` (strikes, quotes, deltas) and, once priced, its
  `would_be_credit` — enough to settle a refused bwb against the print and replay any floor; both are
  NULL before that. `entry_far_wing_delta` is stamped on every row so the flat
  floor can later be re-derived against `P(tail) × tail` — store first, retune second. Two widths
  (5/10 and 10/20) as separate arms, each with its own `max_bwb_tail_dollars`. **Paper only, never a
  live candidate.** Read the roll as the result (`best_roll_debit`, unrolled vs rolled P&L). No hedge
  overlay — insuring the far wing is the roll's job.

### Regime tagging

`engine.classify_regime` tags every entry and completion, on every arm, from the snapshot in hand:
`vol_bucket` (ATM straddle/spot), `gex_bucket` (gamma concentration, `"unknown"` without OI),
`time_bucket`, `skew_bucket` (OTM put vs call at the traded strikes), `center_offset_bucket` (signed
`centre − spot`, one strike per bucket), `trend_bucket` (`spot − day_open`, from the cache's
`stream_summary` via `provider._session_bounds`). **Inert — nothing gates on it.** It exists to build
a future selector that picks the winning entry/completion candidate for the current regime, and
**a regime selector must score its candidates at a common price** (the iron dispatch above is the
cautionary case). A tag definition is expensive to change once data accumulates, so think before
changing one. The narrative behind every dimension:
[docs/history.md](docs/history.md#regime-tagging-how-each-dimension-got-its-present-form).

- **Store the measure, not just the bucket.** `classify_regime` returns the continuous measure behind
  each bucket plus the GEX surface's provenance (`net_gex`, `gamma_flip`, `gex_strikes`,
  `gex_input_age`); `analytics.by_regime(..., bucket_edges=[...])` re-cuts it. Regime data has no
  backfill path, so a threshold can only be recalibrated from the stored number.
- **Trend band is 20 points** (not one 5-point strike, where the opposing bucket inverts). Chosen on
  the same 76 rows that measure it — a best estimate, not a calibrated constant. Trend-from-open lags.
- **`refuse_completion_against_trend` stays retired.** The early 89% vs 7% opposing-drift split (15
  trades, 3 sessions) did not survive the advisor era: over 25 sessions `against` completed 73% and
  `with` 67%, `with` beat `against` 7 of 19 days (p 0.36), and `against` is fragile. Do not revive it
  from the early figure.
- **Trend can be backfilled only while `stream_summary` retains rows** (back to 2026-07-29 today); the
  cache offers no retention guarantee. Chop/trend stays absent: it needs the path, which is
  cross-tick state. [docs/centre-lag.md](docs/centre-lag.md).
- **`center_offset` describes our own choice, not the market** — a market regime is something to
  condition on, this is something to change. Its sign fixes which way spot must go for a leg-in to
  complete; `max_total_gamma` centres where price *has been* and so lags on trending days. Signed and
  side-neutral, never a "lagging" boolean (a snapshot carries no trend). Kept alongside `trend`
  because they catch different entries and imply **opposite remedies** (skip the trade vs fix the
  centring). Content only on GEX-centred arms. The float was backfilled exactly on 292 paper / 9 live
  rows; the bucket was left NULL there — re-cut the float instead. What lag costs is lag *against the
  direction of travel*, not lag alone. Nothing gates on it yet; [docs/centre-lag.md](docs/centre-lag.md)
  says what evidence would justify a gate.
- **`gex_bucket` is windowed near spot over the top 3 strikes** (whole-chain share read `thin` 60/60).
  **`time_bucket` boundaries are 11:00/13:00** (10:00/15:30 was constant by construction); re-cut it
  splits 43/35/19 with completion falling 72% → 63% → 58%, which is the mechanism (less session left
  to drift). It is **not** redundant with `entry_window`, whose dominant cell holds 74 of 97 rows.
  Chosen on the same 97 rows that measure it.
- **`analytics.regime_coverage` guards the read.** A single-bucket dimension is `degenerate`: the EOD
  report warns and withholds that dimension's P&L table (a one-bucket table reads as a finding). It
  also reports `sessions`, `daily_scale`, `effective_n` and `underpowered`, because **rows are not
  draws** — positions on one day observe one market. `daily_scale` is measured (`DAILY_SCALE_RATIO`),
  not declared; `underpowered` is keyed on sessions, not `effective_n`. `degenerate` means re-cut the
  float, `underpowered` means collect more sessions. `by_regime` reports sessions per bucket.
- **A stale checkout silently loses regime data** (the loop imports from the working tree, so the
  checked-out branch decides what the ledger records). `db.stale_writer_columns` compares the running
  code against the **database file's** columns — comparing the schema registry against
  `classify_regime` catches nothing, since both are stale together. `paper_loop` logs it at session
  start and does not enforce: refusing to trade would turn a telemetry gap into an outage.

## Two declared-but-off entry gates

`engine.trend_bucket_refusal` (`refuse_trend_bucket`: none | up_from_open | down_from_open) refuses
every entry while the session's trend-from-open bucket is the named one — the day, not the leg.
`engine.miss_stop_refusal` (`miss_stop_minutes`) stops an arm entering for the day once any of its
spreads has sat uncompleted that long (losing sessions were RUNS of misses into one tape). Both are
off unless an arm or advice sets them; both are declared in `advice.bounds`; both sit ahead of strike
selection so a refusal is attributed to the session; legged only. Because each reads only facts every
row carries, `replay_gates.py` (`replay-gates`) re-runs recorded sessions under either rule exactly —
the cheap first answer before the advised twin's forward A/B. The live tick stamps `entry_time_min`
on its rows as paper does; without it the cadence clock and this gate silently never fired live.

## Per-arm portfolios: cadence and the entry rules

Each arm is an independent portfolio with **unbounded capital and buying power**, so three rules are
the whole of what paces it — and **the refusals are the primary measurement**, not a diagnostic.

- **Cadence** — one entry per arm per `min_seconds_between_entries` (360), clocked from the last
  **fill**; an unfilled order did not spend the slot. `engine.cadence_state`.
- **The same-strike sign rule** — within one arm, every open leg at a given (expiry, right, strike)
  must share a sign; a long against a short is refused, because legs that net to zero make every
  downstream number describe a position nobody holds. `cherrypick.core.entry.sign_conflict`, fed by
  `fly.position_legs`. **Option type is part of the leg identity** — a short put and a long call at
  one strike do not net. The rule pushes adjacent structures a strike further apart by design (the
  `+1 -2 +2 -2 +1` shape still stacks); **expect fewer entries than pre-2026-08-11 books, and do not
  pool the two.**
- **No duplicate structure** — keyed on `(centre, wing_width, far_width)` across the whole day.
  Replaces `center_already_occupied`.

**Both sides of the sign comparison are stamped with one expiry token** (the day book is all 0DTE for
its `trade_date`; a snapshot's own date field is not guaranteed). If they ever disagreed the rule would
silently permit everything — **a gate that fails open and silently is worse than no gate.**

**`fly_entry_attempts` is the measurement record; `fly_decisions` stays the narrative.** One
uncollapsed row per evaluated opportunity (outcome, blocking strike, seconds still to wait). `no_fill`
is its own outcome, neither a spent slot nor a refusal. Writes are wrapped so a telemetry failure can
never cost a trade.

**Changing the cadence is a measurement break** — entry pacing decides what a per-session net means.
Journal it and keep the eras apart.

## The advised arm (paper only, off by default)

When `advice.enabled` is true, the paper loop looks ONCE at session start for
`state/advice/flies-<session>.json`, re-validates it with `cherrypick.core.advice` against this
module's own `advice.bounds`, and runs each admitted experiment as a synthetic arm beside the base arm
its entry names — one arm per entry `advised_books` returns, tagged `advised:<experiment name>`. The
core mechanism is in [packages/core/CLAUDE.md](../core/CLAUDE.md). Module specifics:

- Absent, stale, expired or invalid advice means baseline; one out-of-bounds value rejects that
  experiment's whole overlay (its baseline day, nobody else's). The day's decision is pinned in
  `data/flies/advice_active.json` so advice never starts, stops or changes mid-session.
- The base is read from the entry, never split out of the tag. An arm holding rows whose entry is gone
  resolves its base through `_advised_base` (the decision's entry, else legacy `advised:<arm>`, else
  `advice.base_arm`). Pre-2026-09-17 decision files still open `advised:control`, so history reads
  unchanged; `experiment_id` (via `stamp_for`) separates the experiments that shared that tag. Rows
  from before the column read `NULL` and are never rewritten.
- **An advised arm is a new BOOK, not a measurement break** in `control`, which never changes meaning
  mid-experiment. Rows key on the arm string, so it needs no `engine.ARMS` entry.
- **No management twin, because this module has no exits.** Tick and settlement share ONE roster
  helper (`paper_loop.session_arms`) that includes any advised arm still holding rows for the day,
  whatever today's advice says — a narrower settlement roster would strand a book open.
- Keep the bounds narrow: an advised book that differs from control on five axes measures nothing.

## The honesty rules

These are the constraints the module exists to enforce. Breaking one makes the numbers worthless.

1. **Every result is net of the modeled fee and slippage stack.** This suite has recorded a trade
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
  (`fly.WORST_CASE_ITM_LEGS`), and paid, never dodged (rule 5). `fee_reconcile` compares modeled vs
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
driver, CLI, and the orchestrator `fly_book` wiring across all four schema registries. 300 tests,
including a provider suite built against the real `cherrypick.core.streamcache` DDL so an upstream
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

## The live pilot

Live trading is running, not hypothetical. The two questions moving to live raised, and how the pilot
resolves them:

- **Legging is where live diverges hardest from paper.** Live, step 2 is a working limit that may sit
  or fill worse, so the paper completion rate was taken as a **ceiling** on the live rate. The first
  48 live completions question that: a resting limit filled where paper's modelled debit said no
  (fill realism, above). Built-in abort: once
  30+ live legged entries exist, a live rate more than 15 points below paper over the same days halts
  the pilot automatically.
- **`fund_from_open_credit` needs a real buying-power check** before any outright entry. Moot for the
  pilot: outright entries are off in live, so this must be solved before they could go live.

The full plan — Gate 0, live-loop architecture, kill switches, the fee-math symbol decision, the
rung-by-rung rollout — is [docs/live-trading-plan.md](docs/live-trading-plan.md).

## Band placement (`python run.py bands`)

Where each book's band sat relative to the range the session actually printed. A fly's floor holding
is the joint event of band placement and realized range, so `floor_holds` alone credits a wide band on
a quiet day and blames a tight one on a fast day.

- **Both edges**: the metric is `min(low_margin, high_margin)` with the binding edge named (72 of 152
  books were bound by the UPPER edge), normalised by the session's realized range (ex-post) and by the
  VIX1D one-day implied move (knowable at entry — the only one that separates "places wider bands"
  from "got quieter days").
- **The range comes from the stream cache's `day_high`/`day_low`, not `fly_iterations`** — a 0.11-point
  breach is invisible to sampled ticks.
- **Ranges key on (session, symbol)** — this module has traded SPX and XSP, and keying on date alone
  scores XSP bands against SPX ranges.
- `band_placement_classifier` agrees with `floor_holds` 97.4% (two-edge) vs 72.4% (one-edge) on 152
  books. **Read it as agreement, not prediction**: `floor_holds` is a property of the structure settled
  before the open, so part of the agreement is mechanical; the rule-vs-rule comparison is the result.
  Residual disagreements (books that touched an edge and settled back inside) are labelled as such.

How the metric was arrived at: [docs/history.md](docs/history.md#band-placement-how-the-metric-was-arrived-at).
