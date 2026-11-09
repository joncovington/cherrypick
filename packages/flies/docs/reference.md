# flies module reference

The layout, the read side, the data source, the arms and the pacing rules. The strategy, the honesty
rules and the guardrails are in [../CLAUDE.md](../CLAUDE.md); the live path is
[live.md](live.md).

## Layout

| file | role |
|---|---|
| `cherrypick/flies/fly.py` | payoffs, quote pricing, fees, position and book floor math. Pure. |
| `cherrypick/flies/engine.py` | centre selection, entry gates, the completion gate, settlement. Pure. |
| `cherrypick/flies/provider.py` | builds snapshots from the shared stream cache, read-only. No decisions. |
| `cherrypick/flies/paper_loop.py` | session driver: fetch, run every arm, settle at the bell. `run_once`/`run_settle` take `snapshot_fn` and `replay=True` for the quote-history replay: a replayed tick touches nothing but its ledger (no heartbeat, log file or advice decision, which writes). |
| `cherrypick/flies/replay.py` | the quote-history replay: rebuilds each recorded sample as paper's snapshot (quotes in paper's window and age, GEX from the recorded profile within 360 s, session bounds from the spot trail up to the instant) and runs `paper_loop.run_once` over it into a scratch ledger. Rule arms only; model-driven arms are refused. CLI `replay --date --out`. |
| `cherrypick/flies/replay_check.py` | the replay's decision-level validation: at each recorded sample the replay is handed paper's own book as of that instant (`as_of`) and runs one tick; each paper entry and completion is tested at its own moment, and replay decisions paper never made are counted as episodes. `summarise` applies the declared pass test (ten sessions). CLI `replay-check --dates`. |
| `cherrypick/flies/book.py` | wires engine decisions to the paper DB; one book per (date, arm, symbol). A replay hands `process_snapshot`/`settle_book` the tick's `now` (or `paper_loop.run_once` its `when`) and every stamp (entry_time, completed_at, latencies, the cadence clock, the journals) is that instant (`clock.pinned`); the loops pass none and stamp the wall clock. |
| `cherrypick/flies/db.py` | `fly_positions` (ledger) and `fly_books` (roll-up with the floor's price band). |
| `cherrypick/flies/analytics.py` | the one query layer every read surface goes through. Read-only. |
| `cherrypick/flies/analysis_rows.py` | The `scripts/flies_*` replays' settled-row loader and the ONE split of a row's `fees` into entry / completion / roll / settlement (`fees` is the total; the print's part is `settlement_fees`, else derived from a reconciled total, else the module's own fee at the print; the trades share the rest by shape). A replay never takes the entry's fee another way. |
| `cherrypick/flies/eod.py` | `logs_dir()`, the loops' path helper. `packages/review` reports the session. |
| `cherrypick/flies/cli.py` | `once` / `settle` / `status` / `regime` / `bands` / `replay-gates` / `hedge-overlay` (`--run-k`) / `reversal-book` / `fill-model` / `regime-cuts` / `debit-first-offsets` / `debit-ladder` / `selector-fit` / `selector-replay` / `close-or-hold` / `close-tags` / `close-tag-tracker` / `agent-eval` / `backfill-events` / `backfill-peak-risk`; the console bridges three read-only evidence verbs, `live-gap` (live vs paper paired per session), `gate-sessions` (gate arms vs control over each arm's own window) and `selector-sessions`. |
| `cherrypick/flies/close_or_hold.py` | `run.py close-or-hold` (live ledger; `--paper`, `--at HH:MM`): each of today's open positions closed now at natural (long legs at the bid, shorts at the ask, less `ic_close_fee`) beside its expected expiry P&L under the empirical move from this clock time (±5 min) to the close, from the gex recorder's `gex_spot_history` and `daily_closes`, settled through `fly.position_pnl` with its settlement fee. Any unusable leg quote means no close number, never a guess. **Unscaled** (a quiet day and a fast one weigh the same), ~50-80 sessions, no estimate under 20; values the structure as it stands, not a completion still resting. A read, not advice: nothing consults it and rule 5 stands. Pure functions plus a read-only reader. |
| `cherrypick/flies/selector.py` | The selector arm, pure: candidates against its own book, the merge, scoring, the choice, `fit`, model validation. See "The selector" below. |
| `cherrypick/flies/selector_replay.py` | The selector walked forward over recorded sessions, out of sample, beside every benchmark it must beat. Read-only. |
| `cherrypick/flies/ladder.py` | The debit-first shadow ladder, pure: rung geometry, the entry price, the per-tick fold through the arm's own completion function, settlement. |
| `cherrypick/flies/live_loop.py` | The LIVE loop: a 1-min `--once --live` tick fired by the supervisor while the arm record (`state/flies-live-arm.json`, written per day via `/live-flies-start`) is valid; self-disarms at `live.disarm_time` by deleting the record. The arm record, both disarm reasons, the supervisor-heartbeat read and the record-only arming rule are `cherrypick.core.live` (thin wrappers here; arming without a supervisor is refused); fill confirmation reads `cherrypick.core.execution.fill_state`. `--once` (dry-run default) is the rung-0 smoke; `--status`; `--settle --price` for the official print; burst fill-watchers `--watch-fills`. Every live tick marks each open position at mid into `fly_live_marks`: pure telemetry after every decision; an unquoted leg gets no row, never a zero. A mid is not a fill. Live only: paper's result is settled payoff by design. |
| `cherrypick/flies/broker_cli.py` | Thin broker seam on `cherrypick.core.broker` (preflight/governor). `live_gates` is `live_loop.readiness` plus today's arm record (read as the tick's dead-man's switch reads it), so `execute_trade --live` and every adapter submit refuse an unarmed day, a bad `live.arm` or a missing designated account exactly as the tick does. The loop's adapter is `cherrypick.core.execution.Broker` with this module's session, account, `live_gates`, serializer and deploy cap injected; only the REST re-quote remains here. `official_settlement_price` is `cherrypick.core.settlement`'s, kept as a module attribute so the adapter and tests patch one seam. |
| `cherrypick/flies/live_orders.py` | Pure engine-decision to order-spec builders (OCC symbols from the provider). Tick rounding is `cherrypick.core.structures`. |
| `cherrypick/flies/alert_daemon.py` | Optional order-alert daemon: one account-alert websocket for the trading day, a companion on the arm record that the supervisor keeps up while armed (restarted on death or silence) and stops on disarm. Decides nothing; appends to the inbox below so fills are *noticed* sooner. |
| `cherrypick/flies/alerts_db.py` | The WAL-mode alert inbox (`live_alerts.db`), separate from the ledger on purpose: 1 writer (daemon), N readers (tick, watcher). |
| `cherrypick/flies/credentials.py` | `fliesagent` keyring store + hidden-input CLI (orchestrator `connect` delegates here). `designated_account` is `CredentialStore.designated_account()`. |
| `cherrypick/flies/fill_model.py` | Fill realism, pure: the three at-fill distances, spread prices and gaps, first-touch records, rule scoring, the paper shadow's outcome. See "Fill realism" below. |
| `cherrypick/flies/fill_facts.py` | Fill realism, recorded: the live loop's order/path writers (`fly_live_orders`, `fly_order_path`), `rebuild`, and the dry-run-by-default `backfill` from broker transactions and the gex spot trail. |
| `cherrypick/flies/intraday_pack.py`, `intraday_advice.py`, `intraday_eval.py`, `intraday_replay.py` | The intraday agent ([intraday-agent.md](intraday-agent.md)): the fact pack with its look-ahead guard, validation of a decision and the live tick's read of it, the qualification (`agent-eval`), and the historical replay. Deterministic; the model call is `scripts/flies_intraday_agent.py`. |
| `cherrypick/flies/close_tags.py` | Closes of stranded verticals tagged at natural on the row, never executed (`close-tags`, `close-tag-tracker`). |
| `cherrypick/flies/completion_escalation.py` | A raised completion limit replayed on live's own quoted order path (`scripts/flies_completion_replay.py`). Pure. |
| `cherrypick/flies/replay_gates.py` | `replay-gates`: the miss-stop and trend-bucket gates re-run over recorded rows. Read-only. |
| `cherrypick/flies/stream_window.py` | Escalates the streamer's ATM window after repeated `missing_leg_quotes` refusals, and decays it back. |
| `cherrypick/flies/fee_reconcile.py` | Settled live rows against the broker's cash, run daily by the supervisor's `flies-fee-reconcile` job whether or not live was armed. |
| `cherrypick/flies/clock.py` | ET clock helpers; `clock.pinned` stamps a replayed tick with its own instant. |
| `tests/fixtures/books.json` | three real tastytrade order chains, transcribed. |

## The read side

**Everything reads through `analytics.py`**, and a test asserts the report's headline figure is exactly
what that layer returns: MEIC grew three call sites that disagree about what "net" means.

**Three journals, each kept separate on purpose.**
- `fly_decisions` records *why* every entry was made or refused, collapsing identical consecutive
  reasons into one counted run.
- `fly_iterations` records what each arm *wanted* on each iteration, before any gate could veto it,
  uncollapsed, because collapsing destroys what arm divergence needs.
- `fly_snapshots` records one row per (tick x symbol) of what the feed gave us: "ok" with quote counts,
  or the provider's refusal reason. A stretch of refused rows is a feed problem; a stretch with *no rows
  at all* is the loop not running, and without it those two silences were identical. Written in
  `paper_loop.run_once` on both the built and the refused path; pure telemetry, no decision reads it.

**A book's session peak risk is recorded at settlement** (`fly_books.peak_risk`/`peak_risk_at`,
`analytics.session_peak_risk`): the largest open worst case the book carried, `max(0, -floor)` over every
position, each rewound to its state at that moment (rule 3: an uncompleted vertical at full defined risk).
It is the capital `core.analysis` reads for flies, whose positions carry none per trade. A measurement
only; nothing in the loop reads it. `run.py backfill-peak-risk [--write]` fills books settled before the
column existed (dry run by default) and checks the replay against the live loop's own
`fly_live_marks.open_margin` at the same tick. The two differ for two known reasons, both named in its
output: an entry still at its limit when the broker filled it better, and an entry order working at that
tick that never filled. Any other gap is listed as unexplained.

**Five measurements this strategy needs and generic P&L reporting cannot give:**

- **Completion rate**: how often a leg-in became a fly. Near zero means short verticals wearing a
  costume. `analytics.completion_trend` gives it per session, because a blended rate can drift while
  looking stable.
- **The counterfactual** (`best_completing_debit`): for misses, *the market never offered it* versus
  *our gate refused it*, identical in the P&L and opposite in remedy. "Our gate" is two verdicts,
  `buffer_blocked` (`D < C - fee_buffer`) and `floor_blocked` (`floor >= min_floor_dollars`), read from the
  `fly_decisions` journal rather than recomputed, so they cannot drift from the gate as configured.
- **Completion latency**: a fly that took 40 minutes and 8 points of drift is far likelier to fill live
  than one that appeared for seconds. The paper-vs-live gap, measured.
- **The post-completion counterfactual** (`post_best_completing_debit`/`_credit`): for completions, how
  much better the price got AFTER the first qualifying tick was taken. The stream cache keeps no quote
  history, so it is recorded live (`book.py` step 1d, pure telemetry, no gate reads it) or lost; for
  sessions the gex recorder covers (from 2026-10-13, `core.quotehistory`, every ~15 s) a replay can
  re-derive it. `analytics.left_on_table` splits it by `completion_gex_bucket`. See "Completion timing".
- **Arm divergence**: how often arms picked different centres. **Only meaningful against an arm that
  centres differently** (`gex`): the ATM arms agree on centre by construction, which says nothing about
  redundancy. Read `time_window` vs `control` on timing and completion, `width-N` vs `control` on width.
  Never read a structural identity as a finding.

### Completion timing

Completion takes the first tick that clears the gate: `legged` buys the completing debit spread the moment
`D < C - fee_buffer` and the floor gate passes, and `debit_first` sells the completing credit spread at its
equivalent. Waiting for a richer price is not done, because a cheaper completing debit usually means spot
is running the wrong way, and a resting limit may never be offered it again. The ledger keeps pricing the
completing spread *after* a completion (running MIN of the completing debit for `legged`, running MAX of
the credit for `debit_first`; the completion tick seeds the tracker, so "never improved" reads as zero, not
missing; iron and bwb completions are excluded). A wait-for-better rule gets built only if the recorded data
shows, at the suite's usual promotion bar (at least 14 sessions and 20 tracked completions per mode), that
(1) the median post-completion improvement is materially larger than the ~$5-10 noise floor, (2) it
concentrates in an identifiable regime (e.g. `completion_gex_bucket = pinning`) rather than spread thin, and
(3) the implied wait would have survived the reversal paths actually recorded, evaluated against the
per-tick record, not the end-of-day best. If first-tick was already the best available, that is the finding
(rule 6). The measurement runs either way.

The theory agrees (du Toit and Peskir, "Selling a stock at the ultimate maximum", Annals of Applied
Probability 2009): the finite-deadline optimal-stopping rule is bang-bang in the drift. Against adverse
drift, stop immediately, which is first-qualifying-tick; under favourable drift the optimal rule is a
trailing retracement from the running maximum, tightening to zero at the deadline. That second branch is why
the door stays open for future gate work and why no fixed tightening was adopted.

### Fill realism, tag-don't-gate

Paper completes on the first tick its modelled debit clears the gate; live rests a limit at
`max_safe_completion_debit` and pays the limit. The gap between them is measured, not assumed:

- **Live** records every order (`fly_live_orders`, filled or not) and what it saw while it worked
  (`fly_order_path`, each tick and watcher cycle). At each fill it keeps three distances (spot past the
  centre, spot past the completing long strike, the mid/natural gap to the limit), signed as
  `fill_model.py` defines them, plus the broker's own fill time and leg-fill price. The price gaps come
  only from a look at most 15 s before the fill (its age is `fill_obs_age_s`), else NULL: a minute-old look
  on a fast tape reads gaps that were never there. **From 2026-10-12 the ledger's live `debit`/`credit` are
  the broker's fill price** (`fill_model.booked_price`) and the order's limit stays on
  `fly_live_orders.limit_price`; before that they were the limit, which hid price improvement (`fill_facts
  book-prices` rewrote those rows, each checked against the broker's transactions). With no leg fills, or a
  leg sum worse than the limit, the limit is booked. `fill_time_source = 'noticed'` says the broker's time
  is unknown. Every write goes through `live_loop._telemetry`: a failure is logged, never raised into a
  fill confirmation or a placement.
- **Paper** stamps each legged entry with the limit a live completion would rest at
  (`shadow_completion_limit`) and keeps first touches against it every tick until settlement
  (`shadow_touches`), whether or not paper's own rule completed the position.
  `analytics.shadow_completion` replays it at any grid value.
- **From 2026-10-05 every paper legged completion pays the live limit** (`engine.pays_limit`,
  `completion_price` / `completion_price_from`). The trigger is unchanged: the modelled debit at or under
  the limit. The price is `engine.completion_limit`, the one formula live's resting order is priced from.
  The plan's `market_debit` keeps the modelled market price, and the best-debit counterfactuals record
  that, never the price paid. This is a book-wide `completion_rule` break
  (`paper_loop._note_completion_rule`): **never pool completion P&L across 2026-10-05.** Switching paper to
  a live-like rule changes what control's numbers mean: fit first, shadow second, switch only at a declared
  boundary.
- **The entry shadow.** Live entry orders mostly fill, and the misses are not random: spot was usually
  moving toward completion, so live misses the entries that were going its way. Each paper legged entry
  stamps the limit live would ask (`shadow_entry_limit`) and its first 60 s (`shadow_entry_path`: spot's
  move toward completion, the entry spread's gaps). `run.py fill-model`'s `entry` block fits a miss rule on
  live's orders off the gex spot trail, never the order path (it stops at the fill), and splits paper's net
  by it. No rule yet beats assuming every entry fills, so the split is not read until one does. Paper still
  enters on the tick.

### Two shadow streams, both tag-don't-gate

- **`hedge_mid_at_completion`** is the overlay hedge's sale value on the legged completion tick.
  `hedge-overlay`'s `sell_at_completion` block reads it ("hold the hedge only while stranded"). Completed
  rows without it are untracked there, never a free sale.
- **The debit-first shadow ladder** (`ladder.py`, `fly_debit_ladder`). On an arm that sets `debit_ladder`,
  every fill also prices the same trade k strikes out each way. Each rung is carried by
  `engine.evaluate_debit_completion` itself and settled through `engine.settle`. A rung is never a
  position, and a refused rung carries its reason and no price. `run.py debit-ladder` reads it, calibrated
  against real delta-arm fills at the same centre; read the calibration before any cell. A rung at k = 0
  reproduces its anchor exactly, and a test pins it.

### The intraday agent

A model reads the session and decides the trend gate and which stranded verticals to close for the
`intraday-agent` arm, beside the fixed `trend-rule` arm. Closes are tagged at natural on the row, never
executed. Full description, configuration, the live daily yes and the qualification:
[intraday-agent.md](intraday-agent.md).

### Two overlays on the legged book, both tag-don't-gate

- **The hedge overlay** (`engine.hedge_candidate`, `book.py` step 1e, `analytics.hedge_overlay`,
  `run.py hedge-overlay`). At every legged entry the ~`hedge_delta` (0.05) option on the spread's losing
  side, beyond the long wing, is priced and stamped (`hedge_*` columns) and **never bought**; each tick
  keeps the running max of what selling it would fetch, and settlement records its intrinsic (0 when
  worthless, NULL only when no hedge was recorded; the read side keeps those apart). The sell-at-Nx replay
  over the running max **is an upper bound, not a fill**. Paper only; the spread's own decision and price
  are untouched.
- **The reversal book** (`analytics.reversal_book`, `run.py reversal-book`) pairs each settled `control`
  legged entry with the settled `debit_first` entry from the partner arm on the same losing side
  (`debit-first-down` for a put spread, `-up` for a call), same session, nearest in entry time within a
  window, each partner used once. A combined arm would break the one-variable rule twice. An unmatched base
  entry is reported, never paired with a distant partner.

### The regime-cuts artifact

`run.py regime-cuts --write` (nightly 16:40 ET via `paper.regime_cuts_at` / `regime_cuts_argv`). Every arm
with settled era rows, cut by every regime dimension and by the declared cross-tabs
(`analytics.CROSS_TABS`), written to `data/flies/regime_cuts-<session>.json` plus a `regime_cuts.json`
latest copy only a newer session replaces (`--session` re-cuts a past day, `--backfill --since` fills a
run). The contract is `cherrypick.core.regimecuts` (MEIC writes the same shape); the console and the
advisor's deep pack read it and recompute no cell. Rules that live in the writer, not in any reader:

- The era is scoped by this ledger's `measurement_breaks`: the latest book-wide break on or before the
  session starts it; an arm added later starts at its own `arm_added` break; a future-dated break is
  listed and ignored until it passes; a `partial_session` break never bounds anything.
- Every cell carries `sessions`, stamped `thin` below three: a pooled cut can look predictive when the
  whole effect sits in one few-session cell.
- With `--write` it also carries `gate_replay` (`replay_gates.sweep` over the advice base arm's era: miss
  stop 15..90 min, both trend-bucket refusals, every `entry_windows` choice), because the advisor reads
  only its pack. `entry_windows` has three fixed choices (control's own 10:00-14:30, a midday skip,
  10:30); a test pins that control's own window is one of them.
- The core's robustness stamps (`fragile`, `paired`, `history`, `multiplicity`) come from
  `by_regime(..., with_sessions=True)`. Pooled cells that look like findings are what a gate would be read
  against, not evidence the regime decides the entry.

**`drift_alignment`** is derived at read time, never stored: `with` / `flat` / `against` from
`completing_direction` against the stored `entry_trend_*` pair, with `flat` taken from the trend tag so the
band is the arm's own `regime_trend_points`: the cut and the `refuse_completion_against_trend` gate can
never disagree about "committed". Side alone is a coin flip; side x trend is the variable.
`by_drift_alignment` stays as the EOD version under `DRIFT_BAND_PCT`. Each book's summary also carries
`completion_latency_min` (p25/p50/p75/max) and `miss_gap` (`credit - best_completing_debit` over
uncompleted verticals), copied through `regimecuts.assemble` only when present so MEIC's books carry
neither. `by_regime`'s phase rename replaces every `entry_`, not the first; `test_regime_cuts.py` pins it.

**The second cross-tab, gex x drift_alignment**, is declared module-side in `analytics.CROSS_TABS`, not in
`core.regimecuts.DEFAULT_CROSS_TABS` (an iron condor has no drift analogue), appended so `cross_tabs[0]`
stays the shared one. A three-way gex x trend x drift_alignment cross is refused: 27 cells at this sample
is a fishing surface, and `trend` is already inside `drift_alignment`.

**The console's flies page has a time axis.** `analytics.session_timeline` assembles the day from rows
already written. `settle_now` replays the book at each tick as an expiry payoff at live spot: **not a
mark**, and the page says so. Replaying rewinds each legged row exactly (a short vertical until it
completes: pre-completion fee `vertical_open_fee`, net the recorded `credit`), never drawing a fly before
it existed. Both charts refuse to smooth: lines **break across a gap** rather than interpolate (a straight
segment over a 100-minute silence reads as a calm market), and the payoff curve draws one line per arm,
never a blended book.

## Data source

This module **runs no streamer**. `provider.py` reads the shared stream cache
(`~/.cherrypick/data/marketdata/stream_cache.db`) read-only, produced by `packages/streamer` from the
union of every module's `state/stream_requests/` file; this module rewrites its own every tick. Open
interest, and so GEX, exists only because the producer subscribes DXLink Summary for its ATM window.
**Each loop also declares its own open legs**, since spot leaving the ATM window is exactly when a short
vertical needs its marks: `fly_positions` carries four `*_leg_symbol` columns (the DXLink streamer symbol,
never OCC), stamped at entry and completion from the same quote the price came from; `stream_request.py`'s
`leg_sources` query points at each loop's OWN ledger (`flies.json` for paper, `flies-live.json` for live).
A live completing leg is stamped when the resting order is PLACED.

**The provider refuses rather than guesses.** Stale quotes (`max_quote_age_seconds`), crossed quotes,
missing spot, an empty chain: each returns `{"ok": False, "reason": ...}`, logged and stepped past.
Refusals are ordinary, not errors. `quote_stats` is recorded on every snapshot so a barren session reads as
"the data was thin", not "the strategy found nothing".

**GEX inputs are refused when stale or thin.** Gamma older than `max_gex_input_age_seconds` (1800) is
dropped, and below `min_gex_strikes` (20) the surface is refused and `select_center` degrades to ATM.
Without the bound a dead feed produced a surface indistinguishable from a live one.
`snapshot["gex_stats"]` carries fresh/stale/coverage and the `input_rule`.
- **From 2026-10-06 (`provider.GEX_SURFACE_RULE_FROM`, a `gex_surface` break) the inputs are the gex
  recorder's.** Open interest is kept at any age for a strike whose gamma is live, leftover gamma
  (`core.gex.LEFTOVER_ROW_SECONDS`) is dropped, and `gex_input_age` is the gamma's age alone.
- **Before that, OI was held to the gamma limit.** OI is a once-a-day number the streamer rarely re-sends,
  so most strikes lost theirs by the afternoon, and the stored net GEX often had the opposite sign to the
  recorder's. **Never pool `entry_/completion_gex_*` across 2026-10-06.**
- **The walls are still a different definition** from the recorder's: gross call/put peaks here, net-GEX
  walls there. Same inputs, different walls, by design.

`cherrypick.core.fees` supplies the fee schedule and `cherrypick.core.gex.compute_gex` the per-strike GEX
profile; neither is reimplemented here.

## The arms

Separate books, each differing from `control` in **exactly one** thing. Every gate is shared, so each
comparison measures one variable. Keep `max_positions` equal across any compared pair, or the comparison
measures opportunity count instead of the variable. `engine.ARMS` is the registry; an arm runs only where a
machine's config defines it (the shipped example holds only `control`, switched off like the module, with
each other arm's settings in a `_<arm>` note), and `tests/test_engine.py` checks that every configured arm
is a registered one. Arms that are no longer run stay in `ARMS` so their books stay readable, and
**ledger rows keep the arm string they were written under**; never rename one.

Arms are judged forward against `control` on per-session net over the same sessions. A gate chosen by
searching the rows that also measure it is a test, not a default, and its one-variable twin is pinned by a
test that checks this machine's definition equals control's apart from the gate's own settings.

**Baseline and centring**
- `control`: ATM, all day. The naive baseline without which a profitable arm proves nothing.
- `gex`: centre on the strongest positive per-strike net GEX near spot. Degrades to ATM when no OI is
  cached, recording `center_reason` so those samples can be excluded. Retired: it centres on where price
  *has been*, so it lags on trending days (see "Centre lag" below).
- `callwall`: centres the shorts at the GEX call wall; refuses the session when the wall is not above spot.
- `time_window`: ATM, only inside configured windows, which are **not** ranked: each trade is tagged with
  its window and the ranking comes from our own sessions. Its windows **straddle** control's rather than
  nest (nesting made the two identical but for opportunity count), and `max_positions_per_window` (2) stops
  one window spending the whole book.
- `window-1300`, `window-1230`: `control` with its entries confined to 13:00-13:59, or starting at 12:30 and
  ending with control's 14:30. One variable: the window.
- `width-2` ... `width-5`, `width-10`: control's twins pinning `wing_width_strikes` (`wing_width =
  strike_increment x wing_width_strikes`); `control` is the 1-strike rung, so there is no `width-1`. Defined
  in strikes so the sweep survives a symbol switch. **XSP-era rows under these names used raw point widths
  and are a different geometry, never pooled** (`CURRENT_ERA` keys on symbol; SPX from 2026-08-01). A
  hypothesis, not a fix: if no width yields a fee-positive floor, the drift is fundamental to the
  mechanism, which is itself a result (rule 6).
- `wide_wing`: **disabled**, superseded by the sweep.

**Construction**
- `debit-first`: isolates **legging order**: buy the debit vertical first, complete by *selling* the credit
  spread once spot drifts back toward the centre. Its uncompleted branch is bounded at the debit paid,
  never legged's `-W` tail. **GEX-centred** (`center_rule: "gex"`), so it differs from `control` in two
  things: read it against `gex` for legging order alone. `debit-first-atm` is its ATM twin.
- `debit-first-up`, `debit-first-down`: the OTM debit-first pair (`center_rule: "delta"`,
  `engine._delta_center`, deltas from `provider._attach_deltas`): buy a debit vertical centred at
  `debit_delta_target` (0.15, a magnitude) away from spot and complete by selling the same-centre credit
  spread once spot walks into it. Delta, not a strike offset (one trade all day). The chosen delta is
  stamped as `entry_center_delta` on **every** arm's rows. Up and down differ only in `center_direction`
  (a test pins it). **Refuse, never degrade**: no fresh delta, no strike within `debit_delta_tolerance`
  (0.05), or a spread not wholly beyond spot each return no centre with its own reason, since an ATM
  fallback would trade the ATM arm's trade under this arm's name. `min_debit_pct_of_width: 0.02` (the
  shared 0.20 would refuse a 15-delta spread by design). **Paper only**: `live_orders.py` builds legged
  specs alone.
- `iron`: **disabled; it never traded. Keep the negative result (rule 6).** Completing into an iron fly
  uses the identical strike pair as the debit completion, so put-call parity pins `D + credit2 =
  wing_width` for any skew (every implied-vol term cancels; the mirror case is the same algebra
  reflected). The two gates are one inequality, `iron net - W == fly net`, and the two completions are the
  same position in different clothing; what is left is fees and execution, and both go against the iron
  (an extra full fee stack and a worse fill). The code is kept and tested; `completion_modes` stays
  `["debit"]` everywhere. **`book.py`'s "take the higher floor" dispatch is wrong and must be fixed before
  any revival**: `fly` reserves 3 ITM strikes and `iron_fly` 2 (`fly.WORST_CASE_ITM_LEGS`) at different
  worst-case prices, so iron's floor reads $5.00 high everywhere. A regime selector must score its
  candidates at a common price for the same reason.
- `bwb`: isolates **entry construction**: a broken-wing butterfly entered whole for a credit, near wing
  `wing_width`, far wing `wing_width * bwb_far_width_ratio`. Until rolled it carries REAL negative tail
  risk (`wing_width - far_width`) that `position_floor`'s `bwb` branch never reports as bounded. The roll
  buys `centre -/+ wing_width` and sells the held far wing (a vertical of width `far_width - wing_width`),
  converting it to a symmetric fly once it clears its own price and floor gates. GEX-centred; `bwb-atm` is
  its ATM twin.
  - **The 25 bwb rows of 2026-08-04..08-06 are void** (the roll priced a spread 3x too wide). They carry
    `void_reason` and every read surface drops them (`db._VOID_BACKFILL`, `analytics.voided` states what
    was held back). A cutoff lives in data, never in prose.
  - **Side rule is `engine.choose_bwb_side` (`centre >= spot` means calls), never legged `choose_side`**:
    the legged rule put the roll spread in the money, where its intrinsic can never clear `roll_debit <
    credit - fee_buffer`.
  - **Safety and credit trade directly**: pushing the tail away from spot shrinks the credit
    (a bwb credit is `(C(K+w) - C(K+f)) - butterfly(K-w, K, K+w)`, capped by the gap between the two
    candidate far wings, and that gap collapses as the structure moves out of the money), so
    `min_bwb_credit_pct_of_tail` (0.15), not the price gate, is what binds.
  - **The roll trap**: the roll should cheapen under precisely the drift that makes the position
    profitable and grow dear when the tail is threatened (spot running toward the far wing). If that is how
    it behaves, the protection is unavailable whenever it matters.
- `bwb-up`, `bwb-down`, `bwb-up-w2`, `bwb-down-w2`: delta-placed bwb pairs on the same rule and target as
  the debit-first pair, so the two constructions sit on the same strikes and differ in one thing (credit
  now with a tail vs debit now with a conditional completion). **The credit floor is not loosened**;
  refusal rows are the result. Each bwb attempt row carries the structure in `proposed_legs` and, once
  priced, its `would_be_credit` (NULL on earlier rows), enough to settle a refused bwb against the print
  and replay any floor (`scripts/flies_bwb_floor_replay.py`). `entry_far_wing_delta` is stamped on every
  row so the flat floor can be re-derived against `P(tail) x tail`. Two widths (5/10 and 10/20) as separate
  arms, each with its own `max_bwb_tail_dollars`. **Paper only, never a live candidate.** Read the roll as
  the result (`best_roll_debit`, unrolled vs rolled P&L).
- **No `spot + N strikes` arm**: `center_offset` is stored as a signed continuous float and re-cut with
  `by_regime(bucket_edges=...)`; what matters is placement relative to the drift, not raw distance.

**Gates (each `control` plus one variable)**
- `vol-floor`: no entry while the ATM straddle is under `min_entry_straddle_pct` (0.0022) of spot
  (`engine.low_vol_refusal`, refusal `straddle_below_floor`). Completion is a race for spot to travel a
  wing width one way before the close, and the low-vol third of sessions completes least often. The
  threshold is a ratio so it survives spot moving.
- `range-high`: no entry while spot is at or above `range_high_pct` (0.9) of a session range narrower than
  `range_high_max_points` (30) (`engine.range_high_refusal`, refusal `near_session_high`; the range is
  `stream_summary`'s day high and low). Fails open with no session range.
- `range-centre`: no entry while spot is at or above `range_pos_max` (0.8) of the session range so far, on
  any width (refusal `range_position_high`), or while the ATM centre sits more than
  `center_above_spot_max` (1.25) points above spot, which on an ATM arm is the put spread sold furthest in
  the money (refusal `center_above_spot`). `engine.placement_refusal`; fails open without a session range.
- `wall-clear`: no legged entry while the GEX wall on the completing side (the call wall for a put spread,
  the put wall for a call spread) is under `wall_clear_ahead_points` (10) ahead of spot or crossed by under
  `wall_clear_past_points` (5) (`engine.wall_clearance_refusal`, refusals `call_wall_not_cleared` /
  `put_wall_not_cleared`). **It reads the recorder's walls,** `snapshot["recorded_gex"]` via
  `core.regime.gex_at` (at most 600 s old), never the snapshot's own `gex` walls (gross call/put peaks vs
  the recorder's net-GEX walls). Fails open when unmeasured.
- `restack`: the duplicate rule ignores a structure once it is completed risk-free (`restack_completed`,
  `engine.completed_risk_free`), so spot returning to a completed fly's centre may sell that vertical
  again. An open vertical or a negative-floor completion still blocks. The cost is concentration: every
  contract on a centre settles to the same payoff.
- `trend-rule`: `refuse_completion_against_trend` refuses an entry whose completion needs the day to turn
  against a drift beyond `regime_trend_points`, and `tag_closes: "rule"` tags (never executes) a close of a
  stranded vertical through its short but not past its wing on a day committed against it
  (`close_tags.rule_wants_close`). The arm the agent must beat.
- `intraday-agent`: `trend-rule`'s gate switched per tick by the agent's fresh decision
  (`trend_gate_source: "agent"`, `paper_loop.tick_config`; the rule's gate exactly when there is none), and
  the closes the agent names, tagged the same way. Judged against `trend-rule`, not just `control`. Needs
  `intraday_agent.enabled` and the supervisor's `flies-intraday-agent` job
  ([intraday-agent.md](intraday-agent.md)).

**Not variants of `control`**
- `live-shadow`: the live configuration in paper. An arm declaring `mirror_live` takes its params from the
  live block (`engine.live_mirror_params`: `live.arm`'s params, live's start, completion cutoff and
  buying-power cap, the entry credit floored to the tick) and ignores any key of its own. Read against the
  **live ledger** over the same sessions, never against `control`. `tests/test_live_shadow.py` pins it to
  `live_loop`'s params and lists, key by key, what paper cannot express (resting fills, fresh REST quotes,
  mid-first pricing, the one-minute tick, the quarter-end block).
- `selector`: see below.

### The selector

Every other arm is its own portfolio testing one variable. The selector is one portfolio that, each tick,
runs its declared sources' own entry functions against ITS book, merges identical plans (mode, side and
geometry; never pooling the proposers' rows), scores them on a model fitted the night before and pinned for
the session, and books at most one through the same booking code its source uses. Its sources are `control`
and `vol-floor`. Every tick writes `fly_selector_choices`. The variable under test is the choosing
procedure itself, read at 10 departing sessions on per-session net against control. Its contract, stated so
another module can keep it:

1. **Candidates are generated against the selector's own book**, each source's entry function run with the
   source's merged params, so cadence, the sign rule and the duplicate check describe the portfolio the
   selector actually holds.
2. **Identical proposals merge, never double-count.** The key is mode, side and geometry (`engine.structure_key`
   alone ignores kind and side, which is wrong here: a legged and a debit-first entry at one centre are
   different trades). A merged candidate is scored strictest proposer first.
3. **The model is frozen per session**: fitted the night before (`run.py selector-fit --write`), pinned at
   the first tick in `selector_active.json`. "No model" is pinned too, so a model landing at 11:00 cannot
   switch the arm on mid-session.
4. **With no model, the selector is the default's twin**: it books the default source's plan whenever that
   source offers one. A test runs control and the selector over the same ticks and requires identical books
   through settlement.
5. **The procedure is fixed; only its parameters move.** Refitting nightly is not a measurement break;
   changing the features, the shrinkage or the decision rule is, and bumps `PROCEDURE_VERSION`. It skips or
   departs only on a cell whose session interval (`core.regimecuts.robustness`) is wholly clear of zero,
   never on a source's all-regime average.
6. **Fitting reads only rows strictly before the session, each source scoped to its era** by the ledger's
   own `measurement_breaks` (`core.regimecuts.era_bounds`). A test adds future rows and requires an
   identical model.
7. **Completion follows the position**: a booked position completes under the params of the source that
   proposed it (`selected_from`), never the selector's own.

## Regime tagging

`engine.classify_regime` tags every entry and completion, on every arm, from the snapshot in hand:
`vol_bucket` (ATM straddle/spot), `gex_bucket` (gamma concentration, `"unknown"` without OI), `time_bucket`,
`skew_bucket` (OTM put vs call at the traded strikes), `center_offset_bucket` (signed `centre - spot`, one
strike per bucket), `trend_bucket` (`spot - day_open`, from the cache's `stream_summary` via
`provider._session_bounds`), `event_bucket` (the day's scheduled releases against the moment, from
`cherrypick.core.events` via `provider._day_events`). **Inert: nothing gates on it.** It exists so a
selector can pick the winning candidate for the current regime. A tag definition is expensive to change once
data accumulates, so think before changing one.

- **`event` is the one tag read from a calendar, not the market.** `after` (a major release has come out
  today: CPI, PPI, NFP, retail sales, PCE, GDP, JOLTS, FOMC), `before`, `none`, or `unknown` when any
  calendar source cannot speak for the day. **Unknown is never quiet:** a machine with no FRED key tags
  `unknown`, not `none`. `event_value` is minutes since the latest major release; `event_labels` lists every
  release, minor ones too. Backfillable exactly (`run.py backfill-events`, dry run unless `--write`),
  because a calendar is history. FRED mixes headline dates with revisions, so Census's own calendar is the
  source for its releases (retail sales, housing, durable goods). Michigan's preliminary sentiment reading
  comes from its own page (FRED lists only the final). ISM, Conference Board confidence, FOMC minutes and
  options expiry are computed by rule (`source: "rule"`). After a calendar correction, `backfill-events
  --restamp --write` re-tags.
- **Store the measure, not just the bucket.** `classify_regime` returns the continuous measure behind each
  bucket plus the GEX surface's provenance (`net_gex`, `gamma_flip`, `gex_strikes`, `gex_input_age`);
  `analytics.by_regime(..., bucket_edges=[...])` re-cuts it. Regime data has no backfill path, so a
  threshold can only be recalibrated from the stored number.
- **Trend band is 20 points** (not one 5-point strike, where the opposing bucket inverts). Chosen on the
  same rows that measure it, so a best estimate, not a calibrated constant. Trend-from-open lags.
- **`refuse_completion_against_trend` stays off.** An early opposing-drift split did not survive more
  sessions (`against` completed about as often as `with`, and `against` is fragile). Do not revive it from
  an early figure.
- **Trend can be backfilled only while `stream_summary` retains rows**; the cache offers no retention
  guarantee. Chop/trend stays absent: it needs the path, which is cross-tick state.
- **`center_offset` describes our own choice, not the market**: a market regime is something to condition
  on, this is something to change. Its sign fixes which way spot must go for a leg-in to complete.
  Signed and side-neutral, never a "lagging" boolean (a snapshot carries no trend). Kept alongside `trend`
  because they catch different entries and imply **opposite remedies** (skip the trade vs fix the
  centring). Content only on GEX-centred arms. Nothing gates on it.
- **`gex_bucket` is windowed near spot over the top 3 strikes** (whole-chain share read `thin`).
  **`time_bucket` boundaries are 11:00/13:00** (10:00/15:30 was constant by construction); completion falls
  as the session shortens, which is the mechanism (less session left to drift). It is **not** redundant with
  `entry_window`, whose dominant cell holds most rows.
- **`analytics.regime_coverage` guards the read.** A single-bucket dimension is `degenerate`: the EOD report
  warns and withholds that dimension's P&L table (a one-bucket table reads as a finding). It also reports
  `sessions`, `daily_scale`, `effective_n` and `underpowered`, because **rows are not draws**: positions on
  one day observe one market. `daily_scale` is measured (`DAILY_SCALE_RATIO`), not declared; `underpowered`
  is keyed on sessions, not `effective_n`. `degenerate` means re-cut the float, `underpowered` means
  collect more sessions. `by_regime` reports sessions per bucket.
- **A stale checkout silently loses regime data** (the loop imports from the working tree, so the
  checked-out branch decides what the ledger records). `db.stale_writer_columns` compares the running code
  against the **database file's** columns, since comparing the schema registry against `classify_regime`
  catches nothing when both are stale together. `paper_loop` logs it at session start and does not enforce:
  refusing to trade would turn a telemetry gap into an outage.

### Centre lag

`max_total_gamma` lags spot, and centre-versus-spot silently picks which side a leg-in is on. On a trending
day a GEX-centred arm ends up legging into the losing side while an ATM arm does not, so the difference
between the two is not the centring signal being wrong about where price settles. Lag costs only lag
*against the direction of travel*. Read it with these caveats:

- Completion is nearly the whole P&L, and the break-even completion rate is recomputed per window, never
  quoted from an earlier one; a miss is not automatically a loss.
- An ATM arm plus a duplicate-centre rule is not a neutral baseline on a trending day: control can only
  open a strike it does not already hold, and the first moment a new ATM strike frees up is when spot has
  just crossed into the lower half of that strike's zone. Its centres then sit above spot, which
  `engine.choose_side` turns into the put side needing an up move, so on an up day the baseline quietly
  becomes a trend-follower (mirrored on a down day, stalled on a chop day). Do not read a control win as
  evidence that ATM centring beats a signal: check the session's drift and the centre-versus-spot sign
  first.
- The flat band for the trend tag is `regime_trend_points` (20), not one 5-point strike, which was a
  familiar number rather than a measured one; a test pins the shipped value.
- `choose_side` is what generates the losing side, so a fix belongs there rather than in a bolt-on filter.
  The opposition rule and the band were both chosen on the rows that measure them and the down-day cell is
  small, so the surface is a reported prior, not a gate. A gate needs a second clearly down-trending
  session at least, and a paired comparison against `control` over the same sessions. Nothing gates on it.

## Two declared-but-off entry gates

`engine.trend_bucket_refusal` (`refuse_trend_bucket`: none | up_from_open | down_from_open) refuses every
entry while the session's trend-from-open bucket is the named one: the day, not the leg.
`engine.miss_stop_refusal` (`miss_stop_minutes`) stops an arm entering for the day once any of its spreads
has sat uncompleted that long (losing sessions were RUNS of misses into one tape). Both are off unless an
arm or advice sets them; `refuse_trend_bucket` is declared in `advice.bounds`, and `miss_stop_minutes` is
not (an arm may still set it). Both sit ahead of strike selection so a refusal is attributed to the
session; legged only. Because each reads only facts every row carries, `replay_gates.py` (`replay-gates`)
re-runs recorded sessions under either rule exactly, the cheap first answer before the advised twin's
forward A/B. The live tick stamps `entry_time_min` on its rows as paper does; without it the cadence clock
and this gate silently never fire live.

## Per-arm portfolios: cadence and the entry rules

Each arm is an independent portfolio with **unbounded capital and buying power**, so three rules are the
whole of what paces it, and **the refusals are the primary measurement**, not a diagnostic.

- **Cadence**: one entry per arm per `min_seconds_between_entries` (360), clocked from the last **fill**; an
  unfilled order did not spend the slot. `engine.cadence_state`.
- **The same-strike sign rule**: within one arm, every open leg at a given (expiry, right, strike) must
  share a sign; a long against a short is refused, because legs that net to zero make every downstream
  number describe a position nobody holds. `cherrypick.core.entry.sign_conflict`, fed by
  `fly.position_legs`. **Option type is part of the leg identity**: a short put and a long call at one
  strike do not net. The rule pushes adjacent structures a strike further apart by design. **Books before
  2026-08-11 had more entries; do not pool the two.**
- **No duplicate structure**: keyed on `(centre, wing_width, far_width)` across the whole day.

**Both sides of the sign comparison are stamped with one expiry token** (the day book is all 0DTE for its
`trade_date`; a snapshot's own date field is not guaranteed). If they ever disagreed the rule would silently
permit everything: **a gate that fails open and silently is worse than no gate.**

**`fly_entry_attempts` is the measurement record; `fly_decisions` stays the narrative.** One uncollapsed row
per evaluated opportunity (outcome, blocking strike, seconds still to wait). `no_fill` is its own outcome,
neither a spent slot nor a refusal. Writes are wrapped so a telemetry failure can never cost a trade.

**Changing the cadence is a measurement break**: entry pacing decides what a per-session net means. Journal
it and keep the eras apart.

## The advised arm (paper only, off by default)

When `advice.enabled` is true, the paper loop looks ONCE at session start for
`state/advice/flies-<session>.json`, re-validates it with `cherrypick.core.advice` against this module's own
`advice.bounds`, and runs each admitted experiment as a synthetic arm beside the base arm its entry names:
one arm per entry `advised_books` returns, tagged `advised:<experiment name>`. The core mechanism is in
[packages/core/CLAUDE.md](../../core/CLAUDE.md). Module specifics:

- Absent, stale, expired or invalid advice means baseline; one out-of-bounds value rejects that
  experiment's whole overlay. The day's decision is pinned in `data/flies/advice_active.json` so advice
  never starts, stops or changes mid-session.
- The base is read from the entry, never split out of the tag. An arm holding rows whose entry is gone
  resolves its base through `_advised_base` (the decision's entry, else legacy `advised:<arm>`, else
  `advice.base_arm`). Decision files from before 2026-09-17 still open `advised:control`;
  `experiment_id` (via `stamp_for`) separates the experiments that shared that tag. Rows from before the
  column read `NULL` and are never rewritten.
- **An advised arm is a new BOOK, not a measurement break** in `control`, which never changes meaning
  mid-experiment. Rows key on the arm string, so it needs no `engine.ARMS` entry.
- **No management twin, because this module has no exits.** Tick and settlement share ONE roster helper
  (`paper_loop.session_arms`) that includes any advised arm still holding rows for the day, whatever
  today's advice says: a narrower settlement roster would strand a book open.
- Keep the bounds narrow: an advised book that differs from control on five axes measures nothing.

## The live pilot

The live path's rules are in [live.md](live.md). Two points bear on reading the read side:

- **Legging is where live diverges hardest from paper.** Live, step 2 is a working limit that may sit or
  fill worse, so the paper completion rate is a **ceiling** on the live rate (although a resting limit has
  filled where paper's modelled debit said no; see fill realism). Once 30+ live legged entries exist,
  `analytics.live_vs_paper` flags `abort_rule.triggered` when the live rate is more than 15 points below
  paper over the same days. It is reported in `live_loop --status` and halts nothing.
- **`fund_from_open_credit` needs a real buying-power check** before any outright entry. Outright entries
  are off in live, so this must be solved before they could go live.

## Band placement (`python run.py bands`)

Where each book's band sat relative to the range the session actually printed. A fly's floor holding is the
joint event of band placement and realised range, so `floor_holds` alone credits a wide band on a quiet day
and blames a tight one on a fast day.

- **Both edges**: the metric is `min(low_margin, high_margin)` with the binding edge named (the upper edge
  binds as often as the lower), normalised by the session's realised range (ex-post) and by the VIX1D
  one-day implied move (knowable at entry, the only one that separates "places wider bands" from "got
  quieter days").
- **The range comes from the stream cache's `day_high`/`day_low`, not `fly_iterations`**: a 0.11-point
  breach is invisible to sampled ticks.
- **Ranges key on (session, symbol)**: this module has traded SPX and XSP, and keying on date alone scores
  XSP bands against SPX ranges.
- `band_placement_classifier` agrees with `floor_holds` far more often with two edges than with one.
  **Read it as agreement, not prediction**: `floor_holds` is a property of the structure settled before the
  open, so part of the agreement is mechanical; the rule-vs-rule comparison is the result. Residual
  disagreements (books that touched an edge and settled back inside) are labelled as such.
