# cherrypick-flies

0DTE net-credit butterflies on SPX (XSP 2026-07-29…07-31, SPX through 07-28; every era's books remain
in the ledger under their own symbol and widths) — the "profit forest". A **paper module** with a
narrow live pilot: it measures whether the strategy makes money net of costs, and it is built so that
a negative answer is a usable result rather than something to tune away.

**The 2026-08-01 SPX switch, and what it cost.** XSP fees were eating the result: on the 1-wide XSP
book the median completed fly collected **$12.00 against $4.97 of fees — 41.4% drag** — while the
5-wide SPX book collected **$63.12 against $6.89, or 10.9%**. Credit scales with the structure; the
flat $5-per-ITM-strike assignment fee does not. But SPX 0DTE strikes are **5 points apart** (measured:
302 of 479 gaps), so 5-wide is the *tightest structure SPX offers* and per-contract risk rose
**$100 → $500, a 5× increase that is unavoidable rather than chosen**. Credit scaled with it (12.6% of
width vs XSP's 12.0%), so this is close to the same trade five times larger. Two caveats kept
deliberately visible: measured *per dollar of risk* the two are within noise (1.41% vs 1.49% per
trade), and the two samples come from different weeks — so the fee argument is solid while the
risk-adjusted case is not yet established. The width-sweep arms are disabled: 2/3/4 are not multiples
of 5 and cannot be built on SPX at all.

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
| `cherrypick/flies/fly.py` | payoffs, quote pricing, fees, position and book floor math. Pure. |
| `cherrypick/flies/engine.py` | centre selection, entry gates, the completion gate, settlement. Pure. |
| `cherrypick/flies/provider.py` | builds snapshots from MEIC's stream cache, read-only. No decisions. |
| `cherrypick/flies/paper_loop.py` | session driver: fetch, run every arm, settle at the bell. |
| `cherrypick/flies/book.py` | wires engine decisions to the paper DB; one book per (date, arm, symbol). |
| `cherrypick/flies/db.py` | `fly_positions` (ledger) and `fly_books` (roll-up with the floor's price band). |
| `cherrypick/flies/analytics.py` | the one query layer every read surface goes through. Read-only. |
| `cherrypick/flies/dashboard.py` | loopback HTTP dashboard: Today / History / Performance. |
| `cherrypick/flies/section.py` | the compact `cherrypick.core.viz` card for the suite dashboard. |
| `cherrypick/flies/eod.py` | `paper-eod-<day>.md` and `eod-analysis-<day>.md`. |
| `cherrypick/flies/cli.py` | `once` / `settle` / `status` / `dashboard` / `section`. |
| `cherrypick/flies/live_loop.py` | The LIVE loop: 1-min self-healing tick (`--once --live`, per-day arming via `/live-flies-start`, self-disarms at `live.disarm_time`) + burst fill-watchers (`--watch-fills`). `--once` (dry-run default) is the rung-0 smoke; `--status`, `--settle --price` for the official print. |
| `cherrypick/flies/broker_cli.py` | Thin broker seam on `cherrypick.core.broker` (preflight/governor); `--live` double-gated. |
| `cherrypick/flies/live_orders.py` | Pure engine-decision → order-spec builders (OCC symbols from the provider). |
| `cherrypick/flies/alert_daemon.py` | Optional order-alert daemon: one tastytrade account-alert websocket for the trading day, started on arm / stopped on disarm. Decides nothing — appends to the inbox below so fills are *noticed* sooner. |
| `cherrypick/flies/alerts_db.py` | The WAL-mode alert inbox (`live_alerts.db`), separate from the ledger on purpose — 1 writer (daemon), N readers (tick, watcher). |
| `cherrypick/flies/credentials.py` | `fliesagent` keyring store + hidden-input CLI (orchestrator `connect` delegates here). |
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

**Five measurements this strategy needs and generic P&L reporting cannot give:**

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
- **The post-completion counterfactual** (`post_best_completing_debit`/`post_best_completing_credit`,
  added 2026-08-03) — for *completions*, how much better the completing price got AFTER the first
  qualifying tick was taken. The `best_completing_*` trackers stop at the completion tick by
  construction, so before this the module could diagnose a miss but never say whether waiting past
  a completion would have paid — and the stream cache keeps no quote history, so the number is
  recorded live (`book.py` step 1d, pure telemetry, no gate reads it) or lost.
  `analytics.left_on_table` reports it split by `completion_gex_bucket`, because dealer-gamma
  pinning is the favorable-drift regime where waiting *should* have paid for `debit_first` — the
  measured answer to "lock in the win vs let the credit richen." Decision record, the ledger
  evidence for first-qualifying-tick, and the bar a wait-for-better rule must clear:
  [docs/completion-timing.md](docs/completion-timing.md).
- **Arm divergence** — how often the arms picked different centres. High agreement means the experiment
  cannot separate them, which is a finding to surface in week one, not month three. **Centre divergence
  is only meaningful against an arm that centres differently** — i.e. `gex`. `control`, `time_window`,
  `wide_wing` and the `width-N` arms are all ATM, so they agree on centre *by construction* (measured:
  100% across 184 iterations on 2026-07-27) and that number says nothing about whether those arms are
  redundant. Read `time_window` vs `control` on entry **timing** and completion, and the `width-N`
  arms vs `control` on wing width. Reading a structural identity as a finding is how the redundancy
  went unnoticed.

**Everything past the completion rate lives on a time axis, so the dashboard has one.** `analytics.session_timeline`
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

`cherrypick.core` is an installed dependency (`packages/core` in this monorepo, `pip install -e
packages/core`, same for every package). `cherrypick.core.fees` supplies the fee schedule and
`cherrypick.core.gex.compute_gex` the per-strike GEX profile — neither is reimplemented here.

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
- `debit-first` — added 2026-07-31 (`entry_modes: ["debit_first"]`,
  `fly.debit_vertical_payoff`/`engine.evaluate_debit_vertical_entry`/`evaluate_debit_completion`),
  isolating the **legging order**: `legged` sells the credit spread first and buys the completing
  debit spread cheaper once spot drifts *away* from the short strike; this arm buys the debit
  vertical first and completes by *selling* the credit spread once spot drifts back *toward* the
  centre — literally `legged`'s two trades in the opposite order, monetizing the opposite drift
  regime at the same centre. Its uncompleted branch is structurally different too: a long
  vertical's worst case at expiry is the debit already paid (bounded, floor never below `-debit`),
  never the `-W` full-defined-risk tail an uncompleted credit spread carries.
  **Re-centred onto GEX 2026-08-03** (`center_rule: "gex"`, `engine.select_center`): paying a real
  debit up front to bet on convergence only makes sense with some evidence spot is likely to move
  toward the strikes bought, not on pure chance, so this arm now reuses the `gex` arm's own
  centring logic instead of ATM — a `center_rule` override lets an arm opt into GEX centring
  without being named `gex` itself. That gives up a clean ATM-vs-ATM control pairing (control
  already gets that against `gex`'s own legged entries) in exchange for isolating BOTH centring
  and legging order at once — read it against `control` (both differ) and against `gex` (legging
  order only) rather than as a single-variable arm on its own.
- `iron` — **RETIRED 2026-08-03, before it ever traded. Keep the negative result (rule 6):
  [docs/iron-completion.md](docs/iron-completion.md).** It was control's twin isolating the
  **completion choice** — complete a legged credit spread by buying the same-type debit spread, or
  by *selling* the opposite-type credit spread into an **iron butterfly**. It cannot isolate
  anything. Both completions use the **identical strike pair** (`center` and `center ± wing_width`),
  so put-call parity pins `D + credit2 = wing_width` exactly — every IV term cancels, **for any
  skew**, since skew is IV across strikes while parity is an arbitrage at a strike. So the two
  gates are the same inequality, they fire on the same tick, and `iron net − W ≡ fly net` at every
  settlement price: the iron's larger credit is not extra money, it buys exactly `W` of extra
  liability. Verified on a real SPX 1DTE chain (18 strikes, implied forward 7617.69 ± 0.25;
  `D + C2 = 5.00` on a 5-wide). What is left is cost, all adverse: an iron always has one side ITM
  where a same-type fly settles clean in exactly the drift regime this book gets — **+$3.46 per
  position, $495 over the 143 completions in the ledger** — plus a wider crossing cost that a flat
  `slippage_frac` structurally cannot see, which is the deeper problem (an arm whose only real
  variable is invisible to the experiment measuring it cannot produce a finding). It was never in
  the deployed config, so it produced **zero** ledger rows. Code kept and still tested; disabled in
  config and `completion_modes` stays `["debit"]` everywhere, so the path is unreachable.
  **`book.py`'s "take the higher floor" dispatch is wrong and must be fixed before any revival** —
  `fly` reserves 3 ITM strikes and `iron_fly` 2 (`fly.WORST_CASE_ITM_LEGS`) at *different*
  worst-case prices, so iron's floor reads exactly $5.00 high at every spot and would have won
  ~100% of the time on that artifact.
- `bwb` — added 2026-07-31 (`entry_modes: ["bwb_roll"]`, kind `bwb`,
  `fly.bwb_payoff`/`fly.bwb_strikes`/`engine.evaluate_bwb_entry`/`evaluate_roll`), isolating the
  **entry construction**: instead of legging in over two ticks, enters a broken-wing butterfly
  WHOLE for a net credit: a near/protected wing at the usual `wing_width` and a far/wide wing at
  `wing_width * bwb_far_width_ratio` (a ratio, not an absolute point value, so it scales
  automatically with whatever `wing_width` an arm or symbol is already using — the common
  real-world near:far rule of thumb is roughly 1:2). Until rolled, this carries REAL, negative
  tail risk of `wing_width - far_width` that `fly.position_floor`'s `bwb` branch never reports as
  bounded — the entry credit is priced as rent for that tail, not against `wing_width` the way
  `legged`'s credit gates are. The roll buys **the symmetric fly's own wing on the risk side**
  (`centre −/+ wing_width`) and sells the held far wing — a 2-leg debit vertical of width
  `far_width - wing_width`, converting the position to an ordinary symmetric fly once it clears its
  own price and floor gates, bringing the far wing back to exactly `1.0x wing_width`.

  **That leg was wrong from the arm's first session until 2026-08-07, and it invalidated every bwb
  row in the ledger. Keep the negative result.** `evaluate_roll` priced
  `vertical_debit(near_wing, far_wing)` — but `bwb_strikes`' `near_wing` is on the *protected* side
  and the position **already holds it**. So the roll priced a spread of width `far + wing` instead
  of `far - wing` (**3x too wide** at the default 2.0 ratio), and `centre −/+ wing_width` — the leg
  the fly actually needs — was never quoted, never checked by `_have`, never referenced. Worse, the
  trade as specified does not produce a butterfly at all: buying a strike already held leaves
  `+2 @ near / -2 @ centre`, two debit spreads, while the ledger recorded `kind='fly'` and computed
  floor and payoff as a symmetric fly. The tests pinned the bug rather than caught it — the roll
  fixture quoted only the two wrong strikes, so `near_wing` read as correct and the needed strike's
  absence was invisible.
  This is what produced the "roll is unreachable exactly when needed" reading: failing rolls priced
  at 1.88–4.00x the credit (median **3.58x**) against a defect worth exactly 3x. **The 25 paper bwb
  positions of 2026-08-04..08-06 are not recoverable** — the decisions were made on wrong prices and
  the stream cache keeps no quote history, so 14 "rolls" and 11 refusals both rest on a spread that
  was never the trade. **They carry `void_reason` and every read surface drops them automatically**
  — this was a prose cutoff for one day, which `analytics.py` could not see and a reader who skipped
  this file could not apply; `db._VOID_BACKFILL` stamps them once when the column appears, and
  `analytics.voided` accounts for what was held back so the exclusion is stated rather than inferred
  from a gap in a total.
  Researched trap (see `docs/faq.md`), still untested for the same reason: the roll cheapens under exactly
  the drift that makes the position profitable, and balloons past the credit precisely when the
  tail is threatened — this arm measures whether that trade-off is actually survivable, not just
  theoretically credit-positive.
  **Enabled and GEX-centred 2026-08-03** (`center_rule: "gex"`), not turned on ATM first — the far
  wing is where this structure's real, uncapped-until-rolled tail sits, so a GEX-selected centre
  argues for a richer entry credit and for reduced odds of spot running past the far wing into the
  tail before a roll is reachable — same rationale as `debit-first`'s centring change the same day.

  **The side rule was the legged one, and it made the roll unreachable by construction (fixed
  2026-08-07, `engine.choose_bwb_side`).** `evaluate_bwb_entry` reused `choose_side`, whose
  docstring answers a *legged* question — sell the side spot is on the far end of, so the
  **completing** spread cheapens as the drift continues. A bwb's roll has the opposite geometry: it
  buys `centre −/+ wing_width` and sells the far wing, and **both sit on the risk side**. So the
  legged rule placed the roll spread *in the money*, and an ITM vertical cannot be bought below its
  intrinsic:

  > spot 7000, centre 7010, wing 5, far 10 —
  > legged rule → puts, holding +1 7015P / −2 7010P / +1 7000P, far wing **at spot**; roll = buy
  > 7005P sell 7000P, **intrinsic floor 5.00**.
  > Corrected → calls, holding +1 7005C / −2 7010C / +1 7020C, tail **20 points away**; roll = buy
  > 7015C sell 7020C, both OTM, **intrinsic 0.00**.

  A bwb credit runs ~1–3 points, so a roll with a 5.00 intrinsic floor can never satisfy
  `roll_debit < credit − fee_buffer` — unreachable before a quote is read. The rule is now the
  inverse (`centre ≥ spot → calls`), which also states the structure's intent: the butterfly sits
  OUT of the money with the near wing closest to spot, so spot drifting *further away* carries the
  roll further OTM and cheapens it. That is the drift this arm is built to monetize.

  **The cost of the correction, stated up front:** a bwb's credit decomposes as
  `(C(K+w) − C(K+f)) − butterfly(K−w, K, K+w)`, and that first gap collapses as the structure is
  pushed further out of the money. **Safety and credit trade against each other directly here** —
  moving the tail away from spot is exactly what shrinks the credit — so `min_bwb_credit_pct_of_tail`
  (0.15 of tail), not the price gate, is now what binds. Whether the corrected orientation clears it
  often enough to trade is an open empirical question: it cannot be answered from the ledger (every
  bwb row predates both fixes) nor from the stream cache (no quote history, and the cached 0DTE
  chain is a post-close snapshot where every OTM strike has decayed to zero). It needs live paper
  sessions.

- `bwb-atm`, `debit-first-atm` — the ATM twins of the two GEX-centred construction arms, added
  2026-08-07. **Both parents violated this section's own one-variable rule and nobody had noticed for
  `bwb`**: each overrides `entry_modes` *and* `center_rule`, so each differs from `control` in
  construction **and** centring at once and can attribute a result to neither. (`debit-first`'s notes
  already acknowledged carrying the confound — *"gives up a clean ATM-vs-ATM control pairing"* — which
  made it a known cost there and an unnoticed one on `bwb`.) Pinning the centring to ATM makes each a
  three-way read: **X-atm vs `control`** isolates the construction (`bwb_roll`/`debit_first` vs
  `legged`), **X-atm vs X** isolates the centring. Keep `max_positions` equal across the pair or the
  comparison measures opportunity count instead of the variable — the failure this file already
  records twice.
  Note ATM means the structure **straddles** spot (the near wing lands ~1 strike the other side of
  it), which is *not* the fully-OTM placement. That is deliberate and probably favourable: the roll
  span stays OTM under `choose_bwb_side` either way, and a straddling structure clears
  `min_bwb_credit_pct_of_tail` more easily, since a bwb credit is capped by `C(K+w) − C(K+f)` and that
  gap collapses as the structure is pushed out.
  **No `spot + N strikes` arm to go with them, deliberately.** That would pin one value of
  `center_offset` — a dimension the GEX arms already sweep (measured **−22..+23** points, against the
  ATM arms' **−2.5..+2.5**) and which is stored as a continuous float precisely so it can be re-cut
  with `by_regime(bucket_edges=...)` rather than cost an arm. A fixed offset would also bake in a
  direction, and this module's sharpest finding is that what matters is placement *relative to the
  drift* (89% vs 7% completion on the opposing-drift cut), not raw distance — which is exactly why
  `center_offset` is kept signed and side-neutral rather than collapsed to a "lagging" boolean. Read
  the offset curve off the GEX arms first; build a placement arm only if it shows something, and make
  it drift-aware.

**Regime tagging (`engine.classify_regime`, added 2026-07-31).** Every entry and completion, across
every arm, is tagged along six dimensions read purely from the snapshot in hand — `vol_bucket`
(ATM straddle/spot), `gex_bucket` (per-strike gamma concentration, `"unknown"` when no OI cache
exists yet, same honest degrade as the `gex` arm's own centring), `time_bucket` (open/midday/close),
`skew_bucket` (OTM put vs. OTM call price at the exact strikes this module trades — a direct read of
whether the chain itself is pricing in a direction), and `center_offset_bucket` (signed `centre −
spot` in points, bucketed at one strike), and `trend_bucket` (`spot − day_open`, see below). This is
deliberately inert: nothing here gates a
decision. It exists because the eventual goal is a live/paper mode that evaluates every eligible
entry candidate (`legged`/`debit_first`/`bwb_roll`) and completion candidate (`debit`/`iron`) each
tick and executes whichever wins *for the current regime* — `book.py`'s iron-vs-debit "take the
higher floor" dispatch was meant to be a working prototype of that pattern, and is instead a
cautionary one: comparing two kinds by each one's own worst-case floor is not a valid comparison
when those worst cases sit at different settlement prices (see the `iron` arm above). **A regime
selector must score its candidates at a common price.** That
selector needs regime-labelled real outcomes to be built from, not guessed at, and the tag definition
is expensive to change retroactively once data is accumulating — so it ships now, before `bwb_roll`
adds a third entry mode to tag. Deliberately excludes trend/chop: that needs a reference point in
time no single snapshot carries, and guessing at that plumbing before there's a reason to would be
the same mistake rule 6 warns against.

**That last sentence was wrong for three weeks, and the correction is the point (2026-08-04).** The
claim was that a trend read needs spot-now vs. spot-N-minutes-ago, which is cross-tick state this
module refuses to keep. The premise was false: the shared cache has always carried `stream_summary`
(`day_open`/`day_high`/`day_low`/`prev_day_close`) and `orb_ranges`, and `provider.py` read neither
— so `spot − day_open` is a single-row lookup with no history and no state, and the snapshot now
carries it as `session` (`provider._session_bounds`). What blocked this was never the discipline,
only an assumption about what a snapshot could contain, and the cost was real: on 2026-08-04 both
losing `gex` entries legged into the side a 106-point up-from-open day was against, and no recorded
tag could distinguish them. Across the SPX sessions with coverage, refusing an entry whose completing
direction opposes a committed drift from the open splits completion **89% vs 7%** — the sharpest
separation any dimension here has produced, and notably it is *completion* that moves (every other
candidate gate shifted P&L while leaving completion flat, which means it was not touching the
mechanism). Tagged, not gated: 15 opposing trades over 3 sessions.

**The band is 20 points, and it was 5 for exactly one day (corrected 2026-08-05).** 5 was one SPX
strike — the resolution the *centre* moves in, which says nothing about how far a session must
travel before its direction carries information. Split by how committed the day was, the 5-point tag
is not merely weak in the 10–25 range, it is **inverted**: entries opposing a 10–25 point drift
completed 100% of the time (n=5), while past 25 points the read is nearly absolute (0% and 14%).
Sweeping the band, the opposing bucket completes 33% at 5, 7% at 20, and degrades again by 30; 20
and 25 are identical, so it is a plateau rather than one lucky cut. Chosen on the same 76 rows that
measure it — a current best estimate, not a calibrated constant. This also names the dimension's own
failure mode: 2026-08-05 10:01 sat at +13.6 from the open, inside the old dead zone, so a 5-point
band approved an up-completion and the day then reversed to settle 48 points *below* its open.
Trend-from-open lags too — slower than a trailing window, not immune.

**Backfillable for now, contrary to what this said (corrected 2026-08-05).** No position row records
its session's open, but `stream_summary` currently retains a row per (symbol, trade_date) back to
2026-07-29, so those sessions can be reconstructed by joining on trade_date. The cache offers no
retention guarantee, so treat that as a window that will close rather than a property to rely on.
A chop/trend distinction is still deliberately absent: that needs the *path* between open and now,
which really is cross-tick state. [docs/centre-lag.md](docs/centre-lag.md).

**Store the measure, not just the bucket (2026-08-01).** Every threshold above is a placeholder, and
a bucket alone cannot be recalibrated — re-deriving "would this have been `pinning` at a different
cut?" needs the number, and re-running the session to get it is impossible (regime data has no
backfill path; `paper_replay` has no historical gamma source). `classify_regime` therefore returns
the continuous measure behind each bucket plus the GEX surface's provenance (`net_gex`,
`gamma_flip`, `gex_strikes`, `gex_input_age`), and both ledgers store them. `analytics.by_regime(...,
bucket_edges=[...])` re-cuts the float at analysis time. MEIC learned this first — see the rationale
on its `gex_net_at_entry` columns.

**`center_offset` is the fifth dimension and the odd one out (2026-08-04, `docs/centre-lag.md`).**
The other four describe the *market* we entered into; this one describes *our own* choice of centre
relative to spot — a market regime is something to condition on, this is something to change. It is
here because it turned out to decide the thing rule 4 says decides the strategy: `choose_side` sells
PUTS when spot is at or below the centre and CALLS when above, and `completing_side_direction` then
makes the put side complete on an UP move and the call side on a DOWN one — so this one signed
number fixes which way spot must go for a leg-in to complete at all. On 2026-08-04 the `gex` book
lost $386 at 60% completion against control's $613 at 95%, and both `gex` misses were centres behind
spot: `max_total_gamma` centres on where open interest is, which is where price *has been*, so on a
trending day it lags (measured that session: below spot 78% of 389 iterations, median −9.1 points,
while the index ran +115). Deliberately **signed and side-neutral rather than a "lagging" boolean** —
"lagging" is the trend-relative reading, and a single snapshot carries no trend (the same reason
there is no trend dimension at all), so collapsing it here would bake in an up-day assumption and
mislabel every down day. **Nothing gates on it**; 34 gex entries is a hypothesis, and the doc records
what evidence would justify a gate. Note it is also the one dimension that *could* be backfilled,
against the general rule below — `center` and `underlying_at_entry` were always stored, so the float
is an exact recomputation rather than a guess, and the 292 paper / 9 live historical rows were filled
in. The **bucket** was left NULL on those rows, because `strike_increment` is not stored per row and
the XSP era used a different one; re-cut the float with `bucket_edges` instead.
**It overlapped `trend`, was put on a retirement condition on 2026-08-04, and cleared it on
2026-08-05.** The condition was: retire it if it never fires outside `trend`. On 08-04's cross-tab it
never had — but that rested on **2 qualifying rows** and settled nothing. One session later the cell
is populated, and the two rules caught *different* entries on the same day: `center_offset` flagged
the 10:01 gex miss (centre +14.7 above spot) that `trend` read as `flat`, while `trend` flagged the
11:50 and 12:54 misses whose centres sat inside one strike and which `center_offset` structurally
cannot see. Kept; condition answered. They are kept apart because they imply **opposite remedies**:
`trend` is a property of the market and argues for skipping the trade, `center_offset` is a property
of our own centring rule and argues for fixing it, and only the second leaves an arm worth running.
Note `center_offset` only ever has content on the GEX-centred arms (`gex`, `debit-first`, `bwb`),
since the ATM arms sit at offset ≈ 0 by construction.

**The 2026-08-05 falling session is what makes the centring finding more than one day's artefact.**
It opened 7771.62 and settled 7723.55, and produced the exact mirror: all three `gex` misses were
up-completions with the centre *above* spot, both completions were down-completions. gex −$744 at 40%
against control's +$862 at 100%, same shape as 08-04 in the opposite direction. The sign flipped with
the market rather than persisting. Note also the entry centred 22 points *below* spot that completed
without trouble — lag alone is not the problem, lag **against the direction of travel** is.

**A stale checkout silently costs a session's regime data, and there is now a guard for it
(`db.stale_writer_columns`, 2026-08-05).** The loop imports from the working tree, so *whichever
branch happens to be checked out decides what the ledger records*. On 2026-08-05 the repo sat on an
unrelated branch and the whole session wrote NULL to both new dimensions' columns — no error, and
the four older dimensions populated normally, which is exactly what made it look fine at a glance.
Regime data generally has no backfill path, so a day lost this way is usually lost for good. The
check compares the running code against the **database file** — migration is additive and permanent,
so a ledger opened once by a newer checkout keeps columns an older checkout cannot fill, and that gap
is the signal. Comparing the schema registry against `classify_regime` would catch nothing, since on
a stale checkout both are stale together and agree. `paper_loop` logs it at session start and does
not enforce: a stale checkout cannot fix itself, and refusing to trade would turn a telemetry gap
into an outage.

**Two dimensions were measured degenerate, and are documented rather than re-guessed.**
`entry_gex_bucket` came back `thin` **60/60** because concentration was measured as one strike's
share of the *entire* chain (109–121 strikes on a real 0DTE surface); it is now windowed to near
spot and measured over the top 3 strikes, since pinning is a property of a cluster. `time_bucket`
came back `midday` **60/60** because entries only ever occur 09:45–15:00 while "midday" spans
10:00–15:30 — constant by construction. Its boundaries were deliberately *not* re-guessed at the
time: the raw minute is recorded now, so `bucket_edges` can cut it against what actually happened.
`analytics.regime_coverage` flags any single-bucket dimension, and the EOD report warns on it and
withholds that dimension's P&L table — a one-bucket table reads as a finding and is not one.

**`time_bucket` was re-cut on 2026-08-06, and the redundancy hypothesis it carried is answered — it
is kept.** Boundaries **10:00/15:30 → 11:00/13:00**, derived from the recorded minute with no session
re-run (entries span 10:00–14:42, median 11:19). The dimension had gone degenerate a second time by
then, 97/97 rows. Re-cut it splits **43/35/19**, and completion falls monotonically **72% → 63% →
58%** through the day. The monotonicity is why this is kept rather than merely reported as
non-degenerate: a legged entry completes only once spot drifts off the centre, so a later entry has
less session left to drift in, and the decline is the mechanism rather than a boundary flattering
itself. Net P&L splits the same direction under every cut tried; the sharpest is terciles
(10:29/12:27), where the middle third is the only profitable bucket (+$12.46 avg against −$69.96 and
−$35.80) — but that is 32/33/32 rows and the clock cut is the more honest one to ship. Chosen on the
same 97 rows that measure it, the same standing as the trend band's 20 points.

**It is not redundant with `entry_window`**, which was the standing alternative. That window's
dominant `10:00-14:30` cell holds **74 of the 97** rows and splits **35/27/12** across the new
buckets, so it structurally cannot see this variation; its own completion rates (62/70/38/57%) are
non-monotone and rest on cells of 7–8. The narrow windows map 1:1 onto single buckets, so the two
agree exactly where `entry_window` is already precise and diverge where it is not. **Read the sample
before the conclusion**: 97 rows, SPX only — regime tagging began 2026-07-31, so the XSP era carries
no minutes at all and the 65 pre-tagging rows sit in `unknown`.

**GEX inputs are refused when stale or thin (2026-08-01).** `provider._greeks_and_oi` previously read
gamma and OI with no age filter at all, so a dead feed produced a surface indistinguishable from a
live one — on the path that picks the live butterfly's centre (`DEFAULT_ARM` is `gex`). Now bounded
by `max_gex_input_age_seconds` (1800, much longer than the quote limit because OI is a once-a-day
snapshot) and `min_gex_strikes` (20); below that the surface is refused and `select_center` degrades
to ATM. `snapshot["gex_stats"]` carries fresh/stale/coverage the way `quote_stats` always has.

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
   number that decides whether this strategy is real. **This branch is also what rule 6 compares
   against** — refusing a completion does not free the slot, it leaves *this*, so the two rules
   describe one moment from opposite sides and must be read together.
5. **No adjustments after establishment.** No stops, no wing moves, no exceptions — hold to cash
   settlement. v1 is measuring a base rate, and an adjustment rule tuned before a single completion
   rate exists would be fitting noise.

   **A pre-close ITM exit existed from 2026-07-30 to 2026-08-01 as the one deliberate exception to
   this rule, and was removed after measurement. Keep the negative result.** It closed any ITM
   position in the final ten minutes whenever modeled closing slippage came in under the
   $5-per-ITM-strike assignment fee — framed as a cost comparison rather than an adjustment, which
   is why it was allowed through rule 5 at all. Comparing like with like (early-closed positions
   against positions that were *also* ITM and *did* pay the fee):

   | | n | mean P&L | median | negative |
   |---|---|---|---|---|
   | Closed before expiry | 34 | **−$105.64** | −$80.94 | 68% |
   | Held, paid the fee | 115 | **−$71.93** | +$0.61 | 50% |

   Closing cost ~**$34/position** in the mean and flipped the median from breakeven to −$81 — in
   *paper*, where slippage is modeled optimistically at 12.5% of spread. Live fired it **0 times in
   6**, refusing on cost every time (slippage $54–$104 against a $15–$20 fee, median 2.9× adverse).

   Three reasons it could not be fixed by tuning, all worth remembering before anything like it is
   proposed again. **It is structurally upside-down**: the fee is flat in dollars while closing cost
   scales with the option's dollar spread, so the trade gets *worse* with notional, not better
   (0DTE ATM spreads on 2026-07-31: XSP ~$1.50/contract, SPX ~$37.50/contract, against the identical
   flat $5/strike). **It forfeits the thing the module is for**: a net-credit fly's guarantee is a
   non-negative floor *at settlement*, and closing early trades that guarantee away for $5–15 of fee
   avoidance. **It acts on a number that does not exist yet**: it evaluates intraday spot at
   15:50–15:59 but the fee is decided by the settlement print, and across 9 paper sessions 23 of 194
   settled positions (11.9%) had their ITM-leg count change in between (net +$80 of unpredicted fees).

   Consequences still live in the code: `fly.position_floor` reserves the worst-case assignment fee
   again (`fly.WORST_CASE_ITM_LEGS`), since nothing bounds that cost any more, which tightens
   `live_orders.max_safe_completion_debit` with it. And the 34 paper rows carrying
   `closed_before_expiry = 1` closed at an intraday quote rather than a settlement price (`pinned =
   0`) — **exclude them when reading paper P&L**, they are not comparable to ordinary settled rows
   and are not representative of current behavior. That flag is the narrow, one-episode ancestor of
   `void_reason` (2026-08-07), which is the general form: these rows are *not* void — the mechanism
   ran and the numbers are real, they simply measure a behaviour that no longer exists — so they are
   deliberately left unstamped and still require a caller to exclude them knowingly.
6. **A floor is judged against the alternative, and "negative after fees" is still the finding.**
   Two claims, one sentence until 2026-08-06. Collapsing them made a gate argue against itself.

   **The comparison.** A completion's floor is judged against *what happens if we refuse it*, never
   against zero. On a legged entry the alternative is not "no position" — it is rule 4's open short
   vertical at full defined risk, because refusing does not free the slot. So a completion with a
   small negative floor can be the better of two positions we already hold, and refusing on the sign
   alone is not conservatism. Same reasoning that correctly moved `min_floor_dollars` 50 → 10 on
   2026-07-27.

   **The finding, unchanged and load-bearing.** A book that needs negative floors to look viable is
   telling you the strategy does not work. Admitting them improves a losing book without making it a
   winning one — the completion rate rises, and the break-even it is measured against rises with it,
   because thin completions dilute the completed average while removing the least-bad strandings
   worsens what remains. Take the change *and* keep the result. **This rule is satisfied by refusing
   to call that a fix, never by refusing to measure.**

   Two limits, because this is the rule most easily read as a licence:
   - It governs **completion of a position already open**. It never justifies an *entry*. Entering a
     structure whose floor is negative manufactures the loss rather than choosing between two you
     are already holding, and no alternative-branch argument applies.
   - It is an argument from a **measured** alternative, not a standing permission. If the stranded
     branch stops being the dominant loss, the comparison changes and the bar goes back up.
     Re-derive it per symbol and against the current floor definition; never inherit it.

   The original wording — *"the answer is to stop, not to loosen `fee_buffer` until the numbers look
   better"* — stands verbatim for `fee_buffer`, for entries, and for every gate whose alternative
   really is no position at all. **And `fee_buffer` is load-bearing in a way that was not obvious**:
   the price gate caps the completing debit at `credit − fee_buffer`, so the worst floor a completion
   can carry *while still passing it* is `fee_buffer × 100 − fees − reserve` — about **−$11.89** on
   5-wide SPX, independent of the credit. `min_floor_dollars` therefore only has effect inside
   `(−11.89, +∞)`; anything at or below that is inert because the price gate refuses first. So
   `fee_buffer`, not the floor bar, is what actually bounds the downside here, and loosening *it*
   moves a limit the floor bar cannot reach past.

   **The measurement this came from (2026-08-06, PAPER, SPX era, legged only — dated because it will
   go stale, and the second limit above says re-derive rather than inherit).** Completed +$54.12
   (n=64) against stranded −$195.05 (n=33): break-even 78.3% against 66.0% observed. Seven
   completions cleared the fee buffer and were refused on the floor; the sharpest, 2026-08-06, had a
   −$2.50 worst case and settled at −$288.44. Granting all seven is worth roughly +$1,286 and moves
   the era −$2,973 → −$1,687 at 73.2% against a break-even risen to 80.2% — still 7 points short,
   which is the finding half doing its job. **Treat that recovery as an upper bound**: the
   counterfactual is computed from `best_completing_debit`, best-*ever* telemetry, while the gate
   evaluated per tick, so not every one of the seven was necessarily transactable. **No live money
   was involved** — the live pilot's ledger records no floor-gate refusal.

## Guardrails (suite-wide)

- Paper by default; live is a deliberately narrow, per-day-armed pilot (one arm, one symbol, one
  incomplete position at a time — see `live_loop.py` and docs/live-trading-plan.md). SPX/XSP only —
  both European cash-settled, so EARLY exercise is structurally impossible and there is no
  early-exercise machinery to get wrong. Cash exercise/assignment at expiry is NOT impossible,
  though, and is not free: tastytrade charges **$5 per ITM STRIKE** — one charge per distinct
  option symbol that settles, *not* per contract and *not* scaled by quantity — the next business
  day. Modeled throughout (`fly.expire_fee`, `fly.itm_legs_at_settlement`), reserved in every
  position's floor (`fly.WORST_CASE_ITM_LEGS`), and paid rather than dodged — see rule 5 on why the
  mechanism that used to dodge it was removed.
  **Corrected 2026-07-31.** This was modeled as $5/contract until real transactions disproved it:
  a 2-contract XSP put leg was charged **$5.00, not $10.00** (`XSP 260730P00744000`, qty 2,
  `clearing_fees -5.00`), alongside a 1-contract leg also at $5.00. So a butterfly's doubled centre
  is ONE settlement event, and a completed fly pays at most 3 charges (its distinct strikes), never
  4 (its contracts). The old model over-charged every settled fly with an ITM centre; both ledgers
  were re-settled through the corrected math. `fee_reconcile` now compares modeled vs real fee
  **per settlement symbol**, not just as an aggregate P&L delta — the aggregate is what let this
  hide as ~$12 of apparent slippage noise for a day.
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
  **A second, independent exception** (added 2026-07-31, `live.use_order_alert_stream`, off by
  default): the burst fill-watcher may additionally block on tastytrade's own account-alert
  websocket (`AlertStreamer` — order/balance/position pushes, a completely separate stream from
  the shared market-data cache above) for a PUSH notification of a fill, instead of only sleeping
  on a fixed poll interval. This is still squarely "confirming what only the broker can know" —
  it never informs a decision, only how quickly a fill is *noticed* — and it fails closed to the
  exact same cache-gated poll behavior on any websocket/auth error. See `run_watch`'s docstring.
  **The daemon form of the same thing** (added 2026-07-31, `live.use_order_alert_daemon`, off by
  default, supersedes the per-burst flag when both are on): rather than the watcher opening a
  websocket per cycle, `cherrypick/flies/alert_daemon.py` holds ONE account-alert connection for the trading
  day and appends alerts to a WAL-mode inbox (`cherrypick/flies/alerts_db.py`,
  `data/flies/live_alerts.db`), which the watcher reads as a local query. Deliberately a
  **separate database** from `live_trades.db`: that ledger's concurrency was tuned for exactly two
  short-burst, file-locked writers (the tick and the watcher), and a third persistent writer would
  stack onto it — the inbox is the 1-writer/N-reader shape WAL exists for, so the ledger's writers
  are untouched. The daemon is **started on arm and stopped on disarm** (not an always-on
  watchdog-supervised service like `packages/streamer`), self-exits at `disarm_time`, decides
  nothing, places nothing, and never writes the ledger. This module's no-resident-daemon rule still
  holds where it matters: the daemon is an accelerator only — if it dies, stalls, or was never
  started, the heartbeat poll and `run_once`'s once-a-minute re-poll still confirm every fill.
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
