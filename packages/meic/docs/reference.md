# MEIC module reference

Moved from the meic CLAUDE.md, which keeps how the module runs, its boundaries, the live rules and the
database rules. The live block (`live.arm` and live-only sizing) stays there too.

## Read-side CLI

`python run.py <verb>` (or `python -m cherrypick.meic.cli`). Every verb is a READ and emits one JSON
object.

```bash
python run.py headline                       # per-arm results + what is still open
python run.py arms --era ALL                 # the per-stream comparison, cross-era
python run.py regime gex                     # outcomes by the regime an entry was tagged with
python run.py coverage                       # how much of the book is regime-tagged at all
python run.py exits                          # resolved outcomes, expiries split OTM/ITM
python run.py stops [--sessions]             # the stop_trigger_ratio curve, or per-session
python run.py gate-blocks --date 2026-08-25  # per-stream block reasons for one session
python run.py settlement-audit               # does the ledger reproduce its own convention?
python run.py gex-gate                       # what the negative-GEX gate refused
```

- `run.py headline` is what the console's `server/test/meic-mirror.test.ts` checks `readers/meic.ts`
  against — keep its shape.
- **Deliberately NOT here: anything that runs or writes** (`tests/test_cli.py` pins it). The paper loop,
  streamer, ledger writer and broker client keep their own argv: `paper_loop` shells out to `python -m
  cherrypick.meic.db` and `...meic.tt` every tick, and the orchestrator's jobspec, onboarding and the
  suite's skills name those module paths.
- **Regime-cuts artifact:** `python -m cherrypick.meic.regime_cuts --write` (its own entry point because
  it writes a file) — every arm with resolved rows in the era, cut by every regime dimension and the
  declared gex × trend cross-tab, in the `cherrypick.core.regimecuts` shape, to
  `data/meic/regime_cuts-<session>.json` plus a latest copy. Ledger opened read-only via `cli._connect`;
  built by `analytics.regime_cuts`; era from BOTH `db.measurement_breaks` and the `era` column, so the
  two can be seen to agree. Completion fields are null (a condor resolves, it does not complete).
  Nightly at `paper.regime_cuts_at`; the console slide and the advisor's deep pack read it without
  recomputing. It carries the core robustness stamps (`fragile`, `paired`, `history`, `multiplicity`)
  via `by_regime(..., with_sessions=True)`; per-session totals are **net of fees**, and
  `test_regime_cuts.py` pins that they add up to each cell's net.
- **Quote-history replay:** `python -m cherrypick.meic.replay run --date D --out LEDGER --arms a,b
  [--history-db H] [--settle-price P]` (its own entry point because it writes a scratch ledger) runs
  `paper.process_symbol` tick by tick over a recorded SPX session, the recorder's or any file in its
  schema (backtest's vendor stores), under `paper.pinned_clock`, at paper's 60 s cadence whatever the
  history's (`--tick-seconds`): on the recorder's 15 s file control would otherwise enter four times
  as often as paper can. Strikes come from `paper_loop.candidates_from_chain`, the builder the loop
  runs after its fetch. What the recorder schema lacks is rebuilt by a declared rule, never guessed
  (the module docstring): VIX at or before each tick from the history file's own
  `market_regime_history` (the recorder's readings; the vendor store's from VIX options, `backtest
  fetch-vix`), with the session's Cboe open as the declared, counted fallback, because paper moves its
  delta band the moment VIX crosses 18/25/35 and one session in five straddles one; VIX1D from Cboe's
  daily file at the open; IV rank as the tick's VIX in the prior 252 Cboe closes (`iv_rank_source =
  "vix_proxy"`); ATR from prior sessions' trails; GEX from the recorded profile. It cannot see XSP (nothing recorded), strikes
  beyond the recorder's ~20 each side, or `advised:*` arms, and refuses each. A known limit: volume
  GEX (`net_gex_vol`) is not rebuilt, so an arm with `regime_gex_block_mode: "both"` replays as
  `"oi"`. A scratch ledger is
  written under `db.scratch_writes()` (no fsync, no WAL checkpoint), which cut a tick from ~0.34 s
  to ~0.04 s; the real ledgers never take that path.
- **`... replay check --dates D1,D2 --arms ...`** validates it against paper on recorded sessions:
  entries on paper's own book as of each entry (flies' method), replay-only entry episodes, credit
  differences, and each side's worst cost on a full-day replay. No SPX arm ran a per-side stop in the
  recorder era, so the worst-cost path stands in for the plan's stops-matched check. The pass test
  was declared in `docs/quote-history-replay-plan.md` (Phase 2) before any number was read;
  `packages/backtest`'s validation reads its summary. Each session replays on the delta it ran with
  (`delta_source_since`); `--delta-source` replays every session on one source instead, which is how
  backtest's validation compares a vendor's history with the recording.

## What the advisor may move (`advice.bounds`)

Bounds are closed ranges nothing the advisor proposes can leave; the loop re-validates with the same
`cherrypick.core.advice` code the producer used. `tests/test_advice_bounds.py` lints the block, driven
off the shipped `config.example.json`, so a new bound is covered the moment it is declared.

- **A bound over a parameter the paper engine does not read is not harmless**: it validates, is
  admitted, and produces an advised book byte-identical to control — a spent slot that could measure
  nothing. (`entry_price_strategy` was removed from the bounds for this reason: only the agent-driven
  live path reads it, and the advisor only influences paper.)
- **`min_call_otm_pct` (0.0001–0.006) is one-directional by construction, which is why it is safe to
  grant**: control runs 0.0001, so the floor IS the baseline and every admissible value can only make
  the advised book refuse MORE than control. A test pins that; lowering the floor below the base must
  be a deliberate act. The ceiling sits above the deployed default (0.0035) and below the
  quarterly-expiry override (0.0067).
- **`max_open_bp_dollars` (2,500–50,000).** An advised twin is control plus that one key — the cap
  without the sign rule, window or spacing of the `bp-*` arms — so it measures the unspaced cap; the
  bound's note says so for the model.

## Risk profiles and arms

**Live presets (not shipped).** `/set-risk-profile <name>` applies a named preset from the machine's own
arm registry (`paths.risk_profiles_path`) to `~/.cherrypick/config/meic.json`, live on the next
iteration; it refuses while only the shipped control-only example exists. The four-tier ladder below no
longer ships with the package; it exists only in a registry a machine has built, as **live-only**
presets, all `enabled: false` for paper:

- **conservative**: IV rank ≥30%, credit ≥15%, wide OTM buffers, latest entry 12:00. ~1–2 trades/day.
- **moderate**: IV rank ≥22%, credit ≥12%, entry from 11:00, stop 0.93. Use after 2–4 weeks on
  conservative.
- **aggressive**: moderate + delta 0.22, calls 0.30% OTM; offset by max_concurrent_ics=3,
  stop_trigger_ratio=0.90. Use after 2+ weeks at moderate with 60%+ win rate.
- **very-aggressive**: aggressive + VIX ≤30 and 5-day ATR ≤2.0%; offset by max_concurrent_ics=2,
  stop_trigger_ratio=0.85. **Deliberate short experiments only** (1-week windows).

Full rationale and progression: [docs/risk-profiles.md](risk-profiles.md).

**Paper arms (the arm registry).** The registry is `$MEIC_RISK_CONFIG`, else the machine's own
`~/.cherrypick/config/meic.risk.json`, else the shipped control-only `config.risk.example.json`
(`paths.risk_profiles_path`); before 2026-10-01 it was `config.risk.json` in this package. A working
registry is `control` plus the advisor's books and a few arm-scoped additions; `packages/advisor`
designs and runs every experiment. `control` IS the permissive sampling substrate (formerly `open`: study gates off, no per-side stop, `overlap_scope: "none"`, full
per-side path recording) — see the `meic_control_redefinition` measurement break. Every row carries its
era (`ic_trades.era`, from `analytics.CURRENT_ERA` via `cmd_save_trade`), journaled in
`measurement_breaks`. The closed 2026-08 forward test is recorded in
[docs/paper-experiments.md](paper-experiments.md) and [docs/history.md](history.md).

- **`live-shadow`** carries no overrides, only `follows_live: true`: it resolves to exactly the dict
  `live_loop.run_once` trades (`paper.live_params`) and keeps doing so as the config changes;
  `test_risk_profiles.py` pins it override-free and equal to the live params. Control's result
  describes a configuration live does not run, so this arm is live's paper measurement. Other arms
  with `follows_live: true` resolve to `paper.live_params` plus their one override, so each is live
  with one thing changed. Which followers exist is the machine registry's business; their dated
  history is in [docs/history.md](history.md).
- **`bp-5k` / `bp-10k` / `bp-25k`** — control's gates and hold-to-expiry exits under live-account limits:
  `max_open_bp_dollars` (open ICs' (width − credit) × 100 plus the candidate's must fit, checked per
  candidate so a too-wide candidate falls to a narrower one — `paper.ic_buying_power`),
  `overlap_scope: sign`, entries 10:00–14:30, and spacing that **scales with the cap** (20/10/3 min),
  or the larger caps never bind and two arms become one — `test_every_bp_arm_can_reach_its_own_cap`
  pins it. They differ from control only in those five sizing keys, which a test pins.
- **New arms are arm-scoped** (`arm_added` break at first session), so no other arm's clock moves.
- **Retired arms stay in the machine's registry, `enabled: false`, with a written `_disabled_note` verdict —
  never silently deleted** (per `docs/paper-experiments.md`'s kill rule): a defined-but-forgotten arm is
  worse than a documented-and-off one. The ladder no longer ships; `/set-risk-profile` reads the
  machine's registry through `paths.risk_profiles_path`; **paper study streams must never be applied to
  live config.**

**Deriving stop policies read-side (`analytics.stop_grid`, `stop_policies.score_grid`).** The whole
`stop_trigger_ratio` curve comes from recorded rows: `*_max_cost` says whether a threshold would have
fired, `*_settle_value` what the side was worth unstopped — **recorded for stopped sides too**.
`analytics.stop_session_rollup` gives realised-vs-shadow per session (a session is one market event).

- **Censoring:** `*_max_cost` stops being observed when a side really stops, so a looser ratio scored
  against it would read "never fired" when the truth is "recording cut off".
  `stop_policies.censored_above` returns the ratio past which a stopped row says nothing; censored
  points are reported `censored` and excluded from totals. Score over an arm with
  `per_side_stop_management: false` (control), whose paths run to settlement.
- **Pricing:** a side whose real stop crossed first fills at its real stop cost; any other fired side is
  priced **at its trigger** (optimistic by the gap-through real stops pay — reported as
  `fill_over_trigger`), never at its running maximum. Every derived trade is charged the four fees the
  real book pays (open, each close, $5 per ITM strike at settlement — `stop_policies.Fees` via
  `paper.stop_fees()`). A force-closed trade is valued at its real force-close fill (`ic_spread_legs`)
  on every side the policy did not stop. Costs compare at their recorded 4-decimal precision.
- **Validate before trusting a range:** `analytics.validate_stop_derivation` re-derives every arm that
  REALLY stopped (never a hard-coded `control`, which does not stop) at each row's ratio and must
  reproduce its recorded P&L to the cent. The advisor era has no stopping arm, so use `era="ALL"`.
- **Max favourable excursion reads `None`, deliberately** — only the adverse maximum is stored and the
  cache keeps no quote history; a `0.0` would be misleadingly precise. It needs write-path
  instrumentation, not a read-side fix. **That instrumentation exists from 2026-10-13:** the gex
  recorder keeps SPX's chain quotes every ~15 s (`core.quotehistory`), covering every leg MEIC has
  traded since 09-01. The MEIC replay on it (`meic.replay`, above) prices a fired stop at the first
  recorded cost past its trigger, the gap-through `stop_policies` cannot see; Phase 2 of
  [quote-history-replay-plan.md](../../../docs/quote-history-replay-plan.md).
  Nothing before 10-13 is recoverable.

**A width (or any arm) comparison must be bucketed on whether the reference arm fired that session**
(`analytics.control_fired`). A stricter-gated arm can go dark on a session its looser siblings trade,
leaving no same-session baseline. Bucket on the tag; **never drop the dark sessions** — choosing the
sample to get an answer is the same error as reading a structural identity as a finding.

**Per-arm portfolio rules.**

- **`min_seconds_between_entries`** — minimum seconds between an arm's entry FILLS, applied to every
  arm; absent or 0 is off. Separate from `min_minutes_between_entries`, which binds only for arms opting
  into `stagger_entries` — which also turns `daily_ic_trade_target` into a hard cap — so an arm can take
  pacing without the throttle. Clocked on the last fill (`_profile_day_stats` reads the ledger), so an
  entry that evaluated green and did not fill never spent the slot.
- **`overlap_scope: "sign"`** — refuses a candidate leg only when it would sit OPPOSITE an open leg on
  the same contract (a pair that nets out, so the recorded risk is not the risk on); same-sign stacking
  is fine. Strictly between `all` (any strike touch) and `none`. It reads the LONG strikes, which are
  stored only inside `long_put_symbol`/`long_call_symbol`: `paper.ic_legs` derives them from
  `wing_width` in ONE place. Option type is part of leg identity (a short put and long call at one
  strike never net).
- **Adopting either on an existing arm is an experiment-design decision, not a config tidy-up.**
  `control` is the permissive sampling arm, not a copy of the live defaults (since 2026-08-21);
  changing its overlap rule changes what it measures.

**`entry_attempts` records what the gates refused, and why**: one uncollapsed row per evaluated
opportunity per (arm × symbol) per tick — outcome, binding gate, regime state. `no_fill` is its own
outcome. Written best-effort with every exception swallowed, after the decision and fill are persisted,
so a failure costs a telemetry row, never a tick.

**Paper portfolio model**: one portfolio per **(arm × symbol)**, each with its own
`max_concurrent_ics` and daily budget (a shared budget starved whichever symbol ran last — IWM once
went 1,313 iterations without a fill). `daily_ic_trade_target` is soft: past it the credit floor scales
by `over_target_credit_multiple`. Ladder thresholds are derived per arm from its own `min_iv_rank` /
credit floor (`low_iv_credit_floor_iv_rank_offset`, `low_iv_credit_relief_multiple`,
`late_entry_bias_iv_rank_offset`) — a shared absolute flattens the ladder. Invariants any change must
preserve: [docs/risk-profiles.md](risk-profiles.md#design-rationale).

## Config Options

| Option | Current Value | What it controls |
|---|---|---|
| `symbols` | `["SPX"]` | Underlyings traded concurrently; each gets its own option window and GEX profile (no separate `gex_symbol`). `symbol` is a deprecated alias when `symbols` is absent. **SPX alone, for fee drag:** settlement and commission fees are near-constant in dollars while credit scales with the underlying — the bar any symbol added back must clear. |
| `delta_target` | `0.18` | Fallback target delta only if VIX is unavailable; kept below the 0.19 late/open-volatile ceilings |
| `delta_target_vix_low` / `_vix_elevated` / `_vix_high` / `_vix_crisis` | `0.16` / `0.14` / `0.12` / `0.10` | Target delta by VIX band `≤18` / `≤25` / `≤35` / `>35`. A documented convention, not backtested for this strategy |
| `vix_band_low_max` / `_elevated_max` / `_high_max` | `18` / `25` / `35` | VIX band boundaries |
| `delta_source` / `delta_source_since` | `"stream"` / unset | Which delta picks the short strikes (and the call-delta ceiling): `"stream"`, the data feed's (it lags its quotes by about a minute, measured 2026-10-26), or `"computed"`, `core.chaindelta` from the chain's own mids at that instant. Loop-level, read from the top level by the paper loop and through the merged params by live, so every profile's menu shares one source. `delta_source_since` is the declared boundary: a validation replay uses `"stream"` for sessions before it, so it reproduces what they did, while a history run evaluates the rule as configured now. |
| `max_wing_width` | `10` | Upper bound (points); the agent picks the width per entry |
| `wing_widths_by_symbol` | per-symbol lists | Candidate widths per instrument, scanned widest-first (10 points is ~0.13% of SPX but ~3.4% of IWM). `DEFAULT` covers unlisted symbols |
| `equalize_credit` | `false` | Opt-in (per profile): also build put-wing/call-wing width-pair candidates and enter the most credit-balanced one that clears every gate, instead of widest-first. Hidden from every profile without the key, so existing books are untouched. `wing_width` records the wider wing; `put_wing`/`call_wing` on the row hold each side's own |
| `quantity` | `1` | Contracts per IC |
| `daily_ic_trade_target` | `200` | Guidance, never a cap — a never-binding backstop under independent sampling. `0` disables IC entries (ORB-only) |
| `overlap_scope` | `"shorts"` | Check against this arm's open positions on the same symbol: `"all"` (any shared strike), `"shorts"` (exact repeat of the short pair), `"none"`, `"sign"` (see above). Default `"all"`. **Live sees only the arm `live.arm` names** (none: the top level); no other arm's value reaches it |
| `entry_window_start` | `10:00` | Earliest entry (ET); skips the volatile first 30 min |
| `entry_window_end` | `14:30` | Latest new IC entry (ET) — gamma risk after |
| `force_close_time` | `15:45` | Backstop force-close (ET) for non-cash-settled symbols, after `physical_settlement_force_close_time`; cash-settled positions are left to expire |
| `max_credit` | `null` | Credit ceiling; `null` = agent decides |
| `separate_spread_entry` | `false` | `false` = 4-leg combo; `true` = two 2-leg spreads; `"auto"` = agent chooses |
| `entry_price_strategy` | `auto` | `mid` / `natural_bid` / `ioc_step` / `day_improve` / `auto` (picks by session/spread width/IV rank). Read only by the agent-driven live path |
| `mid_improve_wait_seconds` | `45` | Wait for a mid Day limit before falling back to natural bid |
| `mid_spread_gate` | `0.10` | Skip mid if average per-leg spread exceeds this |
| `ioc_step_increments` | `[0.02, 0.01]` | IOC price-improvement steps above natural bid |
| `ioc_step_wait_seconds` | `10` | Wait per IOC attempt |
| `day_improve_amount` | `0.03` | Amount above natural bid for a Day limit |
| `day_improve_wait_seconds` | `60` | Wait before cancelling the Day improve order |
| `stop_type` | `spread` | Software stop only (tastytrade auto-cancels exchange multi-leg stops) |
| `stop_trigger_ratio` | `0.95` | Per-side stop fires when that side's cost reaches this fraction of `net_credit` |
| `stop_limit_ratio` | `1.02` | Cushion on the marketable closing debit, `(short_ask − long_bid) × ratio`, so the Day limit stays marketable if the quote ticks against you |
| `per_side_stop_management` | `true` | Independent call/put stops. The trigger basis is always the whole IC's `net_credit` (the Chambless convention, `docs/strategy.md`) — fixed in code. **`per_side_stop_trigger` does not exist and was never read — do not reintroduce it**; the per-side-own-credit question is the derived `stop-2.0-side` policy ([docs/paper-experiments.md](paper-experiments.md)) |
| `max_stop_adjustments_per_ic` | `3` | Max stop tightenings per IC |
| `cash_settled_symbols` | `[SPX, XSP, NDX, RUT]` | Decides the **end-of-day path**: listed symbols are **left to expire** and cash-settled at `expiration_settlement_time` (OTM short keeps full credit; ITM settles at intrinsic capped at width); unlisted (QQQ/IWM/equities) are **force-closed** by `physical_settlement_force_close_time`. Also drives the missed-force-close escalation (Step 2) and paper's assignment/pin friction. Per-side stops and event force-closes apply to every symbol. Never list physically settled ETFs |
| `expiration_settlement_time` | `16:00` | Cash settlement time (ET). Paper settles at the close; live settles only on the official print (`core.settlement.official_index_close`), leaving the IC open until one is posted, and a missed session is settled by `live_loop --settle-overdue` (the hourly `settle-overdue-live` job) |
| `physical_settlement_force_close_time` | `15:30` | Force-close (ET) for non-cash-settled symbols (backstop `force_close_time`) |
| `physical_settlement_exit_friction` | `0.05` | **Paper only.** Per-spread friction added when force-closing a physically settled position |
| `pin_risk_threshold_pct` | `0.002` | **Paper only.** A short within 0.2% of spot at force-close is "pinned" |
| `pin_risk_penalty_pct_of_width` | `0.25` | **Paper only.** Extra cost (fraction of width) on a pinned physically settled short |
| `loop_interval_minutes` | `5` | Default cadence (overridden by self-pacing) |
| _(no profit target)_ | — | **MEIC has no profit-target close.** An IC exits only by per-side stop, time/event force-close, or cash settlement. Do not add a `profit_target_pct` (ORB keeps its own `orb_profit_target_pct`) |
| `min_credit_pct_of_width` | `0.15` | Credit floor as a fraction of width (2-wide ≥ $0.30) |
| `low_iv_credit_floor_iv_rank_max` | `0.35` | Legacy absolute low-IV relief threshold, honoured only if an arm sets it (see Credit floor) |
| `low_iv_min_credit_pct_of_width` | `0.1` | Legacy absolute relaxed floor, honoured only if set explicitly; default relief is relative |
| `fee_estimate_lookback_trades` | `20` | Recent closed trades per symbol for `db get_fee_estimate` |
| `fee_estimate_min_sample_size` | `5` | Minimum sample before trusting the DB average; else the computed fallback |
| _(fee fallback — computed)_ | via `get_fee_estimate` | `fallback_per_contract` (SPX 6.89, XSP/DEFAULT 4.49, NDX 5.49, RUT 5.21) from `cherrypick.core.fees.ic_open_fee`: open-only commission ($1.00/contract, $10/leg cap) + clearing $0.10 + ORF $0.02 + FINRA TAF on sells + the per-symbol index exchange fee (SPX $0.60, XSP $0, NDX $0.25, RUT $0.18); non-index symbols use the equity/ETF schedule. Extend `INDEX_EXCHANGE_FEE_PER_CONTRACT` to add one |
| `max_concurrent_ics` | `99` | Max simultaneously open ICs; structurally never binds under independent sampling (kept for a deliberate lower-cap experiment) |
| `min_iv_rank` | `0.30` | IV rank floor |
| `max_call_delta_entry` | `0.20` | Hard ceiling on actual short call delta at entry |
| `max_call_delta_entry_open_volatile` | `0.19` | Ceiling in open_volatile sessions |
| `max_call_delta_entry_late` | `0.19` | Ceiling in late sessions |
| `min_call_otm_pct` | `0.0035` | Short call minimum OTM distance, fraction of spot |
| `min_put_otm_pct` | `0.003` | Short put minimum OTM distance, fraction of spot |
| `pre_submit_requote_threshold` | `0.03` | Abort live submit if `ic_natural_bid` dropped more than this since the dry run |
| _(market-calendar dates)_ | via `get_calendar` | Holidays, FOMC and quarterly/triple-witching dates are computed by `cherrypick.core.calendar`, never hand-kept lists; the thresholds below stay config |
| `quarterly_expiry_skip_open_volatile` | `true` | No entries in open_volatile on quarterly expiry |
| `quarterly_expiry_min_call_otm_pct` | `0.0067` | Call OTM floor on quarterly expiry (overrides `min_call_otm_pct`) |
| `quarterly_expiry_max_intraday_range_pct` | `0.005` | Halt entries on quarterly expiry once intraday range exceeds 0.5% of spot |
| `fomc_blackout_start` | `13:30` | No new entries at/after this on FOMC days; close open positions before it |
| `fomc_blackout_end` | `14:30` | Entries may resume after this if IV rank ≥ `fomc_post_blackout_min_iv_rank` (0.40) and range ≤ `fomc_post_blackout_max_intraday_range_pct` (0.005) |
| `regime_vix_pause_threshold` | `25` | Pause IC entries above this VIX |
| `regime_atr_lookback_days` | `5` | Days in each symbol's own ATR |
| `regime_atr_pause_threshold_pct` | `0.015` | Pause IC entries when 5-day ATR exceeds 1.5% of spot (ORB stays eligible). Percentage thresholds scale across price levels; fixed-point ones mis-fired |
| `regime_gex_block_mode` | _(unset: `"oi"`)_ | With `regime_gex_block_negative`: `"oi"` blocks on open-interest GEX alone; `"both"` blocks only when volume GEX (`net_gex_vol`) is negative too, falling back to the OI rule when no volume reading exists (2026-10-15) |
| `orb_enabled` | `true` | ORB debit spread alongside IC entries |
| `orb_range_minutes` | `5` | Opening range 9:30–9:35 |
| `orb_breakout_threshold_pct` | `0.005` | Break beyond the range needed to enter (0.5% of spot) |
| `orb_wing_width_by_symbol` | per-symbol values | ORB width per instrument; `DEFAULT` for unlisted |
| `orb_entry_window_end` | `12:00` | No new ORB entries after this |
| `orb_profit_target_pct` | `1.00` | Close ORB at 100% profit on debit |
| `orb_stop_loss_pct` | `0.50` | Close ORB at 50% loss on debit |
| `orb_close_time` | `15:30` | Force-close ORB positions |
| `late_entry_bias_enabled` | `true` | Prefer post-noon IC entries when IV rank is borderline |
| `late_entry_bias_iv_rank_max` | `0.45` | Bias applies at IV rank ≤ this |
| `late_entry_bias_start_time` | `12:00` | No IC entries before this when the bias applies |
| `slippage_frac_of_spread` | `0.125` | Paper fills are priced at mid less this fraction of the leg spreads, on entry and every priced exit (a live row's entry slippage is measured instead; see above) |
| `live` | see the example | Live-only: the pinned `symbol`, the `gate0_confirmed` attestation, `arm`, the narrowing overrides, `max_structures_per_day`, `daily_loss_halt_dollars`, `disarm_time`, `use_order_alert_daemon` — see the live block above |
