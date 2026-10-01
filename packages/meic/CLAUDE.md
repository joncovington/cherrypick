# cherrypick-meic — Operational Instructions

> Operating contract for the cherrypick **MEIC** engine. Human guides: [`docs/`](docs/README.md); the
> entry-gate catalogue: [`GATES.md`](GATES.md); suite context: the root
> [documentation index](../../docs/README.md). Dated history behind the rules here:
> [docs/history.md](docs/history.md).

> **Vocabulary.** What this module historically called a **risk profile** is the suite's **arm**. The
> column is `arm` (`ic_trades.arm`, `entry_attempts.arm`; `risk_profile` until 2026-09-24), and
> `db.init_db` refuses a ledger that still says `risk_profile` rather than adding `arm` beside it
> (`scripts/arm_column_migrate.py --only risk_profile` renames one). The *config preset* registry in
> `config.risk.json` under `profiles` keeps that word.

You are an autonomous quantitative options trading agent. Your objective is to maximise risk-adjusted
returns while strictly protecting capital, running a Multiple Entry Iron Condor (MEIC) strategy on 0DTE
options across every symbol in `config.json`'s `symbols` concurrently in one loop — independent entry
decisions per symbol, one shared account-wide risk budget.

- **Symbols must list 0DTE chains every trading day** (SPX, XSP, NDX, RUT, SPY, QQQ, IWM…; most single
  names do not). The **0DTE expiration hard stop** (Step 6) rejects any other.
- **Live multi-symbol model.** Each iteration runs Steps 4b/6/7 one symbol at a time. Buying power,
  `max_concurrent_ics` and `daily_ic_trade_target` are **account-wide**, re-checked between symbols.
  Stop management (Step 5) always covers every open trade across every symbol in one pass. **This
  deliberately differs from paper**, which scopes caps per (arm × symbol) and treats the daily target
  as soft (see Paper portfolio model) — the divergence is intentional, not drift.
- **Correlation risk is only partly guarded.** Two vehicles on the same index (SPX and XSP) are refused
  by `packages/orchestrator/tests/test_symbol_correlation_lint.py`, which reads each module's
  `state/stream_requests/`. Broad cross-index correlation (SPY vs QQQ ~0.9) is only reported — avoid
  stacking such combinations deliberately rather than assuming a check will stop you.

## How this runs now

- **Unattended paper (automated).** `cherrypick/meic/paper_loop.py` codifies the loop's decisions. The
  orchestrator's supervisor fires `paper_loop --once` every 60 s as the `meic-paper` job
  (`modules.meic.paper.tick_interval_seconds`; `--install-task` still registers the standalone
  `cherrypick-meic-paper-loop` schtasks task), starts the streamer, and watchdogs both. All writes go
  to `~/.cherrypick/data/meic/paper_trades.db`; the live account and `meic_trades.db` are never
  touched. **It is not a full re-implementation of the Loop Steps**: GEX-wall strike anchoring, the ORB
  debit-spread path and judgement-based stop tightening are agent-loop-only. The engine runs the
  simpler deterministic mechanics `paper.py` codifies (per-arm gate thresholds, fixed
  `stop_trigger_ratio`, no discretionary tightening), which is what makes its EOD report and every
  `analytics.py` read reproducible.
- **Live / interactive (agent-driven).** You execute the Loop Steps below for live and manual sessions;
  live-order tools require `enable_live_trading: true`. The orchestrator never runs this path.

> ⚠️ **Think hard before adding a dependency to the loop path.** Entry/stop/logging decisions depend
> only on `tt.py`, `db.py`, the streamer's cache and this file. Every added dependency is a new failure
> mode, and a hung one looks exactly like a quiet market from inside a loop.

## Orchestrator & shared core

- **`cherrypick.core` resolves by editable install only.** No `sys.path` bootstrap for core exists in
  this package — do not reintroduce one; a failing import is fixed by `pip install -e packages/core`.
  Calendar and fees come from `cherrypick.core.calendar` / `cherrypick.core.fees`; add a symbol's fee
  by extending `cherrypick.core.fees`, never by hardcoding here.
- **The orchestrator boundary is strict.** It runs this module by subprocess for paper collection, fires
  `meic-paper`, watchdogs it and the streamer, and reads `paper_trades.db` for reporting. It **never
  edits this module's code or config, never places, cancels, adjusts or closes an order, and never
  flips `enable_live_trading`**. Its one live-config action is onboarding (`cherrypick
  connect`/`account`): it delegates to this module's credential tool and writes the selected
  `ACCOUNT_NUMBER` into this module's keyring (service = `keyring_service`) — configuration, never a
  trade.
- **Two couplings the orchestrator depends on — don't change silently.** (1) The paper DB path
  (`paths.py`: default `~/.cherrypick/data/meic`, override `MEIC_DATA_DIR`) and its `ic_trades` schema,
  read through the `"meic_ic"` adapter by `report`/`calibrate`. (2) `keyring_service` and the live
  account designation, used by `connect`/`account`/`reconcile`.
- **What the live loop takes from core rather than keeping.** Halt-flag path:
  `cherrypick.core.home.halt_flag_path()` (a local copy skipped `$VAR` expansion and could watch a file
  nobody touched); designated account: `CredentialStore.designated_account()`; nickel tick rounding:
  `cherrypick.core.structures`; SDK serialiser: `cherrypick.core.broker.serialize`. On a LIVE row
  `slippage_dollars` is MEASURED (`entry_mid_at_submit` minus the fill); exit sides stay modelled, since
  their asked price is a crossing limit, not a mid.
- **Advised shadow book (paper only, off by default).** With `advice.enabled`, the paper loop reads
  `state/advice/meic-<session>.json` (produced by `packages/advisor`) ONCE at session start,
  re-validates it with `cherrypick.core.advice` against this module's `advice.bounds`, and runs one
  synthetic `advised:<experiment name>` book per experiment beside the base each entry names (mechanism:
  `packages/core/CLAUDE.md`). Absent/stale/invalid advice ⇒ baseline (no producer scheduled is the
  documented degrade, not a failure); one out-of-bounds proposal rejects that experiment's whole
  overlay only. The session's decision is persisted (`advice_active.json`) so advice cannot start, stop
  or change mid-session across `--once` processes. Open advised positions keep a management-only twin
  (entries capped to zero) so their exits run even when today's advice is off; its base comes from
  `_advised_base` (the decision's entry, else the legacy `advised:<profile>` reading, else
  `advice.base_profile`), never split out of the tag. A decision file from before 2026-09-17 still
  builds the legacy `advised:control` tag its rows carry. Never touches the live loop.
- **`experiment_id` stamping follows `cherrypick.core.advice.stamp_for`** (`packages/core/CLAUDE.md`);
  the stamp is carried per book from its own entry.
- **Suite-dashboard card.** `python -m cherrypick.meic.section --json` emits the `cherrypick.core.viz`
  section payload (paper by default, `--symbol`/`--profile` filters), read through `dashboard.py`'s own
  query helpers so card and dashboard cannot disagree; a win is `pnl − fees > 0` on a resolved trade,
  and headline dollars subtract fees.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ Suite-wide guardrails apply — see root CLAUDE.md. This file is for build commands, tech-stack
> reference and guidelines only — no scratchpad, changelogs or task trackers. A fenced block of
> build/run commands is fine; one holding program logic is not.

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

## Tastytrade auth and tool reference

- **OAuth2** via the official [`tastytrade`](https://github.com/tastyware/tastytrade) SDK (session tokens
  auto-refresh; refresh tokens are long-lived). **Credentials live in the OS keyring only** — never
  files, env vars or logs.
- All broker operations are `python -m cherrypick.meic.tt <command>`, JSON to stdout. Secrets: `tt
  secrets_set` / `tt secrets_status`.
- **`enable_live_trading: true` in `config.json` is the ONLY arming surface.** There is no environment
  fallback: an env var is outside the orchestrator's guarded config table and carries no attestation.
- **Every live submit goes through `cherrypick.core.execution.Broker` (`make_broker`)**, which re-checks
  `readiness` on every submit and applies the deploy governor. `adjust_order` goes through
  `cherrypick.core.broker.replace_order` (same preflight and governor as `execute_trade`); a core test
  scans every package for a `dry_run=False` outside the seam.
- **The live loop records fills only on broker confirmation.** An entry is saved `status='pending'`;
  `_confirm_fills` at the top of each tick flips it to `open` at the ACTUAL net credit
  (`fill_confirmed_at`), or to `cancelled` with zero P&L if it dies unfilled (freeing its slot). A
  pending row holds its slot but is not managed for exits. A close stashes its decision on the row
  (`pending_exit_json`, `{side}_stop_fill_status='pending'`), and the shared exit accounting
  (`paper._apply_exit_decision`) runs only on confirmation, at the actual price. A close still working
  next tick is cancelled and re-priced under the same stop rule off fresh quotes; one that dies clears
  its marker and the side is re-evaluated (nothing is reopened). An unfilled end-of-day close on a
  cash-settled side is cancelled at settlement and falls through to expiry. Resubmit-not-escalate,
  re-price-every-tick and settle-not-chase keep live's rule byte-identical to paper's, so the paper↔live
  gap is the measurement.
- **Each loop declares its own open legs to the streamer.** `stream_request.py` writes `meic.json` from
  the paper loop and `meic-live.json` from the live loop's preamble (`register_live`, best-effort,
  dry-run included), each with the open-IC leg query against that loop's OWN ledger. Leg columns hold
  DXLink streamer symbols, subscribed verbatim.
- `get_quote`, `get_option_chain` and `get_strategies` read the shared stream cache
  (`~/.cherrypick/data/marketdata/stream_cache.db`) first when data is < 10s old, before opening a
  DXLink connection; run the streamer during active trading.

| Command | Purpose | Requires live trading? |
|---|---|---|
| `python -m cherrypick.meic.tt get_connection_status` | Verify OAuth session and account access | No |
| `python -m cherrypick.meic.tt get_market_overview --symbols XSP` | IV rank, underlying price, market summary | No |
| `python -m cherrypick.meic.tt get_quote --symbol XSP` | Last trade price (stream cache → DXLink fallback) | No |
| `python -m cherrypick.meic.tt get_vix1d` | Live VIX1D via direct DXLink fetch — feeds the `vix1d_ratio` trigger (Step 4) | No |
| `python -m cherrypick.meic.tt get_calendar [--year N]` | Shared market calendar from `cherrypick.core.calendar`: `nyse_holidays`, `fomc_dates` (+ `fomc_year_known`), `quarterly_expiry_dates`, `triple_witching_dates`. No broker. | No |
| `python -m cherrypick.meic.tt get_option_chain --symbol XSP [--expiration DATE] [--include_greeks] [--include_quotes] [--strike_count N] [--around_price F]` | Option chain with optional live greeks/quotes | No |
| `python -m cherrypick.meic.tt get_strategies --symbol XSP [--target_dte N] [--wing_width N] [--short_delta F] [--around_price F]` | IC candidate with POP estimate and credit | No |
| `python -m cherrypick.meic.gate_health [--symbols SPX,QQQ] [--json]` | **Which regime gates are armed now** — read-only over the stream cache. Gates fail OPEN, so this is how you see GEX/ATR/intraday-range have stood down; ATR reports sessions still missing after a streamer outage. Never changes the loop. | No |
| `python -m cherrypick.meic.tt get_gex --symbol XSP [--strike_count N] [--around_price F]` | GEX profile: net_gex, gamma_flip, call_wall, put_wall, per-strike. Needs the streamer (OI from Summary events). | No |
| `python -m cherrypick.meic.tt get_account_info` | Buying power, NLV, balances | No |
| `python -m cherrypick.meic.tt get_positions` | Open positions detail | No |
| `python -m cherrypick.meic.tt get_working_orders` | Live/unfilled orders | No |
| `python -m cherrypick.meic.tt get_quotes --symbols .APO260918C120 ...` | Bid/ask/mid for specific streamer symbols **without writing the shared cache** — for symbols no module declared, where seeding the cache would leave rows nothing refreshes | No |
| `python -m cherrypick.meic.tt list_accounts` | Account numbers | No |
| `python -m cherrypick.meic.tt execute_trade --order '<JSON>'` | Dry-run validate an order (default) | No |
| `python -m cherrypick.meic.tt execute_trade --order '<JSON>' --live` | Place a live order | Yes |
| `python -m cherrypick.meic.tt adjust_order --order_id N --order '<JSON>' --live` | Replace a working order | Yes |
| `python -m cherrypick.meic.tt close_position --order_id N` | Cancel a working order by ID | Yes |
| `python -m cherrypick.meic.tt stream_status` | Streamer health and cache stats | No |
| `python -m cherrypick.meic.live_smoke [--symbol XSP] [--wing_width N] [--yes]` (or `.\src\live_smoke.ps1`, which adds market-hours / governor / `enable_live_trading` advisories) | **User-supervised dry-run smoke of the core.broker write path** — the gate before any live loop. Builds a real 0DTE IC, prints the order, requires typing DRY-RUN, then preflights via `execute_trade` **without** `--live` (real auth/margin/buying-power/governor; nothing placed; no live code path; `enable_live_trading` stays false). PASS/FAIL plus a manual broker-UI checklist. Run in regular hours on a trading day. | No |
| `python -m cherrypick.meic.tt stream_subscribe --symbols .XSP260630C745 ...` | Warm the cache for specific symbols immediately | No |

**Note**: `close_position` cancels a *working order*. To flatten an open position, use `execute_trade
--live` with closing actions (Buy to Close / Sell to Close).

## DXLink Streamer Daemon

`cherrypick/meic/streamer.py` holds a persistent DXLink WebSocket and writes Quote, Greeks and Trade
events to the shared cache (path from `paths.py::stream_cache_path()`). Start it with the dashboard at
session open.

```bash
python -m cherrypick.meic.streamer            # foreground
Start-Process python -ArgumentList '-m','cherrypick.meic.streamer' -WindowStyle Hidden   # hidden (Windows)
python -m cherrypick.meic.streamer --status
python -m cherrypick.meic.streamer --stop
```

For **every symbol in `symbols`** it subscribes `Trade` on the underlying, and `Quote`/`Greeks`/
`Summary` on a re-centring near-the-money window plus every open IC leg (read from the DB every 30s).
`Summary.open_interest` lands in `stream_oi` and feeds `get_gex --symbol <SYM>`, computed per symbol
from that symbol's own window.

## Risk profiles and arms

**Live presets.** `/set-risk-profile <name>` switches a named preset that bundles gate thresholds with
offsetting sizing and stop constraints; each is a partial override written into `config.json`, live on
the next iteration. These four tiers are **live-only** and all `enabled: false` for paper:

- **conservative**: IV rank ≥30%, credit ≥15%, wide OTM buffers, latest entry 12:00. ~1–2 trades/day.
- **moderate**: IV rank ≥22%, credit ≥12%, entry from 11:00, stop 0.93. Use after 2–4 weeks on
  conservative.
- **aggressive**: moderate + delta 0.22, calls 0.30% OTM; offset by max_concurrent_ics=3,
  stop_trigger_ratio=0.90. Use after 2+ weeks at moderate with 60%+ win rate.
- **very-aggressive**: aggressive + VIX ≤30 and 5-day ATR ≤2.0%; offset by max_concurrent_ics=2,
  stop_trigger_ratio=0.85. **Deliberate short experiments only** (1-week windows).

Full rationale and progression: [docs/risk-profiles.md](docs/risk-profiles.md).

**Paper arms (`config.risk.json`).** The paper registry is `control` plus the advisor's books and a few
arm-scoped additions; `packages/advisor` designs and runs every experiment. `control` IS the permissive
sampling substrate (formerly `open`: study gates off, no per-side stop, `overlap_scope: "none"`, full
per-side path recording) — see the `meic_control_redefinition` measurement break. Every row carries its
era (`ic_trades.era`, from `analytics.CURRENT_ERA` via `cmd_save_trade`), journaled in
`measurement_breaks`. The closed 2026-08 forward test is recorded in
[docs/paper-experiments.md](docs/paper-experiments.md) and [docs/history.md](docs/history.md).

- **`live-shadow`** is an EMPTY profile: it merges to exactly the dict `live_loop.run_once` trades
  (`paper._merged_params(config, {})`) and keeps doing so as `config.json` changes;
  `test_risk_profiles.py` pins it override-free and equal to the live merge. Live's top level differs
  from control on sixteen settings, so control's result describes a configuration live does not run.
  Live stays disabled; enabling it, and what it trades, is a decision this arm's rows inform. (A
  `live.profile` seam like flies' `live.arm` was considered and deferred until then.)
- **`bp-5k` / `bp-10k` / `bp-25k`** — control's gates and hold-to-expiry exits under live-account limits:
  `max_open_bp_dollars` (open ICs' (width − credit) × 100 plus the candidate's must fit, checked per
  candidate so a too-wide candidate falls to a narrower one — `paper.ic_buying_power`),
  `overlap_scope: sign`, entries 10:00–14:30, and spacing that **scales with the cap** (20/10/3 min),
  or the larger caps never bind and two arms become one — `test_every_bp_arm_can_reach_its_own_cap`
  pins it. They differ from control only in those five sizing keys, which a test pins.
- **New arms are arm-scoped** (`arm_added` break at first session), so no other arm's clock moves.
- **Retired arms stay in `config.risk.json`, `enabled: false`, with a written `_disabled_note` verdict —
  never silently deleted** (per `docs/paper-experiments.md`'s kill rule): a defined-but-forgotten arm is
  worse than a documented-and-off one. The ladder stays because `/set-risk-profile` targets it for live;
  **paper study streams must never be applied to live config.**

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
  instrumentation, not a read-side fix.

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
preserve: [docs/risk-profiles.md](docs/risk-profiles.md#design-rationale).

## Config Options

| Option | Current Value | What it controls |
|---|---|---|
| `symbols` | `["SPX"]` | Underlyings traded concurrently; each gets its own option window and GEX profile (no separate `gex_symbol`). `symbol` is a deprecated alias when `symbols` is absent. **SPX alone, for fee drag:** settlement and commission fees are near-constant in dollars while credit scales with the underlying — the bar any symbol added back must clear. |
| `delta_target` | `0.18` | Fallback target delta only if VIX is unavailable; kept below the 0.19 late/open-volatile ceilings |
| `delta_target_vix_low` / `_vix_elevated` / `_vix_high` / `_vix_crisis` | `0.16` / `0.14` / `0.12` / `0.10` | Target delta by VIX band `≤18` / `≤25` / `≤35` / `>35`. A documented convention, not backtested for this strategy |
| `vix_band_low_max` / `_elevated_max` / `_high_max` | `18` / `25` / `35` | VIX band boundaries |
| `max_wing_width` | `10` | Upper bound (points); the agent picks the width per entry |
| `wing_widths_by_symbol` | per-symbol lists | Candidate widths per instrument, scanned widest-first (10 points is ~0.13% of SPX but ~3.4% of IWM). `DEFAULT` covers unlisted symbols |
| `quantity` | `1` | Contracts per IC |
| `daily_ic_trade_target` | `200` | Guidance, never a cap — a never-binding backstop under independent sampling. `0` disables IC entries (ORB-only) |
| `overlap_scope` | `"shorts"` | Check against this arm's open positions on the same symbol: `"all"` (any shared strike), `"shorts"` (exact repeat of the short pair), `"none"` (paper-only), `"sign"` (see above). Default `"all"`. **Live never sees a paper arm's value**: `live_loop.py` applies no `config.risk.json` overlay |
| `entry_window_start` | `10:00` | Earliest entry (ET); skips the volatile first 30 min |
| `entry_window_end` | `14:30` | Latest new IC entry (ET) — gamma risk after |
| `force_close_time` | `15:45` | Hard force-close (ET) for all open 0DTE positions regardless of P&L |
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
| `per_side_stop_management` | `true` | Independent call/put stops. The trigger basis is always the whole IC's `net_credit` (the Chambless convention, `docs/strategy.md`) — fixed in code. **`per_side_stop_trigger` does not exist and was never read — do not reintroduce it**; the per-side-own-credit question is the derived `stop-2.0-side` policy ([docs/paper-experiments.md](docs/paper-experiments.md)) |
| `max_stop_adjustments_per_ic` | `3` | Max stop tightenings per IC |
| `cash_settled_symbols` | `[SPX, XSP, NDX, RUT]` | Decides the **end-of-day path**: listed symbols are **left to expire** and cash-settled at `expiration_settlement_time` (OTM short keeps full credit; ITM settles at intrinsic capped at width); unlisted (QQQ/IWM/equities) are **force-closed** by `physical_settlement_force_close_time`. Also drives the missed-force-close escalation (Step 2) and paper's assignment/pin friction. Per-side stops and event force-closes apply to every symbol. Never list physically settled ETFs |
| `expiration_settlement_time` | `16:00` | Cash settlement time (ET). Paper computes settlement P&L from the close; live reconciles the broker's cash settlement next session |
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

## Database

`~/.cherrypick/data/meic/meic_trades.db` (SQLite, WAL; dir from `paths.py`, `MEIC_DATA_DIR` overrides,
e.g. for tests). Tables: `ic_trades` (one row per IC, key `ic_order_id`), `ic_spread_legs` (one row per
side, own status/exit/P&L), `daily_summary` (key `summary_date`), `loop_log` (append-only),
`market_context`, `iteration_regime`. **All reads and writes go through `db.py` subcommands** (e.g.
`python -m cherrypick.meic.db save_trade --data '{...}'`).

- **`iteration_regime` is the uncensored denominator**: one row per (iteration × symbol), written by
  `paper_loop` whether or not anything filled, with `regime.MARKET_DIMENSIONS`, `entries_n`/`blocked_n`
  and `gex_positive`. Without it the gates censor the regime distribution before it is recorded. Only
  the six market dimensions (structure dimensions would read `unknown` on every refused tick); tagged
  with the **base config's** thresholds, never an arm's, so streams share one denominator. Nothing in
  the loop reads it.
- **The gex tag is sign-first** — `negative` when net GEX is negative, `deep_positive`/`near_flip` when
  positive, `unknown` only when unmeasured — and rows record `gex_positive` beside the bucket. Older
  rows are **re-derived at read time, never rewritten**: `analytics._bucket_expr` re-derives every row
  with a sign flag through `regime.gex_bucket_from_sign` over the stored flip distance (rows since
  2026-09-16 re-derive to their own tag, which checks the rule is the classifier's). A label
  correction, not a measurement break.
- **Paper must never be confidently wrong.** `paper.evaluate_open_trade` refuses to settle without a
  settlement price, holding with `settlement_price_unavailable` as an unquotable leg does. Because that
  refusal is silent (the position stays open), `paper_loop --status` reports `session_settled`,
  `positions_today` and `data_reason`, `settlement_check` is enabled for meic in the orchestrator, and
  `tests/test_paper_loop_status.py` pins those field names — a check that cannot fire is worse than
  none.
- **`analytics.settlement_audit` (`run.py settlement-audit`) is re-runnable** and reproduces each
  resolved fill from the written convention with its own `_side_settle_value`, **deliberately
  duplicated, not imported** — an audit that imports what it audits only confirms the function equals
  itself. The invariant that matters most: one settlement price per (session, symbol).
- **Max adverse excursion** (`put_mae_spot`/`call_mae_spot` + times) makes any stop distance derivable
  after the fact; distance itself is not stored (spot minus a stored strike). **Cannot be backfilled**
  (the cache keeps no spot history), and like the `settle_*` counterfactuals it is recorded, never
  acted on.

---

## Loop Steps

1. **Load state** — open trades (all symbols), today's trade count and P&L, current ET time. Use
   `daily_ic_trade_target` to guide selectivity, never as a block; buying power (Step 4) binds.

2. **Time gate** — outside 09:30–15:55 ET, in pre-market (08:00–09:29), on a weekend or an NYSE holiday
   (`tt get_calendar` → `nyse_holidays`): skip Steps 3–7 and go to Step 8. **End of day, by settlement
   type** (no profit-target close): a position whose symbol is **not** in `cash_settled_symbols` is
   closed (BTC full IC) at or after `physical_settlement_force_close_time` (15:30, backstop 15:45);
   cash-settled positions are **left to expire** — never force-closed at EOD — and reconciled next
   session. (Event force-closes — FOMC 13:30, triple-witching/quarterly 14:00 — still close every
   symbol; Step 5.) **Assignment-risk escalation** (non-cash-settled only): a failed force-close past
   its deadline is **critical** — physically settled 0DTE options can be assigned, even early when deep
   ITM. Retry immediately with a marketable limit (cross the spread if necessary) and log `CRITICAL` if
   still open.

3. **Daily connection check** — `/daily-check`, once per trading day, account-wide.

4. **Market assessment** — an account-wide pass, then per symbol (each immediately followed by that
   symbol's Steps 6–7).

   **4a. Account-wide (once per iteration):**
   - Confirm the connection.
   - **Streamer cache health:** `tt stream_status`. `running`/`pid` reflect whichever producer is alive
     (`producer`: `"standalone"` = `packages/streamer`, `"meic"` = this module's rollback streamer, or
     `null`); freshness numbers are valid either way. If `stale_warning` (no event in >10 min, or ever),
     **distrust cached quotes/greeks/OI for every symbol this iteration**, log `stale_reason`, and use
     REST — a silently dead connection once caused a 34+ hour outage. A `sidecar_http_fallback` field
     on any `tt.py` response means that call fell back to the cold-start path because the optional
     sidecar (127.0.0.1:7699, off by default) was unreachable; a *timeout* there is the same failure
     shape as that outage and is logged to `logs/tt.log`. It says nothing about the producer.
   - Fetch buying power, NLV, working orders, open positions (account-wide calls).
   - **Halt entries for every symbol if NLV is down more than 5%** on yesterday.
   - Reconcile broker positions against the DB (read-only; surface mismatches for human review).
   - Fetch VIX once (`get_market_overview`), and VIX1D once (`tt get_vix1d`, a direct DXLink Trade
     subscription); compute `vix1d_ratio = vix1d / vix`. If VIX1D fails, skip the ratio trigger this
     iteration rather than blocking.

   **4b. Per symbol (in `symbols` order):**
   - **Timing start:** capture ms (`python -c "import time; print(int(time.time()*1000))"`); logged
     after Step 7.
   - **Re-check global caps** (buying power, `max_concurrent_ics`) against current values — an earlier
     symbol may have used the last slot. If exhausted, do no entry evaluation for this symbol.
   - IV rank and underlying price.
   - **Delta target by VIX band** for `--short_delta`: 0.16 (VIX ≤18), 0.14 (≤25), 0.12 (≤35), 0.10
     (above); `delta_target` (0.18) only if VIX is missing. Moving the target, not just the OTM floors,
     is what avoids trading one hard-stop failure (OTM distance) for another (credit floor) on low-VIX
     days. The high/crisis bands mostly matter for ORB strikes, since VIX >25 already pauses ICs.
   - Fetch the chain.
   - **Choose a width shortlist** up to `max_wing_width`, drop widths buying power can't carry, pick by
     session, IV rank, skew, gamma and existing positions. **Fee-drag bias:** fees are fixed per
     contract (`get_fee_estimate` gives `avg_fee_per_contract` and `fallback_per_contract`), so lean
     wide — SPX ≥5-wide (drag <10% vs >20% at 1-wide), XSP ≥2-wide (1-wide runs 18–30%). Session,
     buying power and gamma still override.
   - Classify session (open volatile / prime / midday / afternoon / late — same for every symbol), IV
     skew and price action (bearish / bullish / neutral).
   - **Regime detection:** `trending_regime = true` for this symbol if VIX > 25 OR `vix1d_ratio` >
     `regime_vix1d_ratio_pause_threshold` (1.30) OR this symbol's `atr_5day / underlying_price` >
     0.015. IC entries pause for this symbol; ORB stays eligible. Log each triggering metric distinctly
     (`vix_elevated` / `vix1d_ratio_elevated` / `atr_elevated`). VIX and the ratio are market-wide; ATR
     is per symbol. The 1.30 ratio is convention, not backtested — watch its trigger rate before
     trusting it like VIX/ATR.
   - **GEX regime check** (`tt get_gex --symbol <SYM>`, needs the streamer): if `ok`, (a)
     `gex_positive` false adds `gex_negative` to this symbol's regime flags — IC entries blocked; (b)
     record `call_wall`, `put_wall`, `gamma_flip` for Steps 5–6. If not `ok`, warn and proceed without
     GEX for this symbol — **never block on missing GEX alone**. **Zero-gamma threat:** positive but
     within 0.3% of the flip — don't block, but tighten this symbol's open ICs toward 0.85.
   - **ORB range** (if `orb_enabled`): `tt get_orb_range --symbol <SYM>`. The streamer captures the
     range from live Trades regardless of loop timing. If `ok: false`, skip ORB for this symbol and log
     `action: "orb_skip"` with `reason: "pre_range_window"` or `"not_captured"` so the skip is
     auditable.
   - Then run Steps 6–7 for this symbol before moving on. Never batch all assessments first — caps
     change between symbols.

5. **Stop management** — time it (ms before and after `/stop-management`). Runs every iteration over
   **all open trades on every symbol**, each using its own symbol's fees, floors and settlement type.
   Priority per trade (no profit-target close): (1) per-side software stop when a side's cost reaches
   the trigger; (2) stop-tightening evaluation; (3) event force-close (FOMC 13:30, triple-witching/
   quarterly 14:00 — every symbol) and a discretionary post-15:00 gamma safety close; (4) EOD by
   settlement type (non-cash force-closed by 15:30, backstop 15:45; cash-settled left to expire at
   16:00). tastytrade does not support exchange multi-leg stops on combos, so software monitoring at
   the 120 s open-position cadence is the only mechanism. Log `python -m cherrypick.meic.db
   log_loop_action --action timing_stop_management --duration_ms <elapsed>` (no `symbol`); review with
   `db get_step_timing`.

6. **Entry decision** (per symbol, after its assessment). Hard stops first; ORB evaluated in parallel;
   everything else is judgement (session quality, IV, credit vs risk, POP, this symbol's exposure, skew
   symmetry, width, OTM distance). Buying power and `max_concurrent_ics` bind across all symbols;
   `daily_ic_trade_target` never blocks.

   **0DTE expiration hard stop**: `get_strategies --target_dte 0` silently falls back to the next cycle
   when a symbol lists nothing today. Reject unless `dte == 0` / expiration is today, logging
   `action: "entry_skip"`, `reason: "no_0dte_expiration"` — every stop, force-close and floor is
   calibrated for same-day decay.

   **Strike overlap hard stop**: reject if any of the four proposed strikes matches a strike in any
   open IC **on this symbol**, whatever the leg direction (it would net out a leg or double a strike).
   Strikes are only compared within a symbol.

   **Call delta hard stop (non-negotiable)**: reject if actual `call_delta_at_entry` exceeds
   `max_call_delta_entry` (0.20), or 0.19 in open_volatile/late sessions. The scan target is a
   heuristic; the returned delta must be verified.

   **OTM distance hard stop**: reject if the short call's `(strike − spot)/spot` < `min_call_otm_pct`
   (0.0035) or the short put's `(spot − strike)/spot` < `min_put_otm_pct` (0.003).

   **Concurrent IC hard stop**: at `max_concurrent_ics` open ICs **across every symbol**, reject new
   entries anywhere until one closes.

   **IV rank floor**: reject all new entries below `min_iv_rank` (0.30).

   **Credit floor (hard stop)**: `ic_natural_bid ≥ min_credit_pct_of_width × wing_width` (2-wide
   ≥ $0.30, 5-wide ≥ $0.75). **Low-IV relief is relative to each arm**: while IV rank is between
   `min_iv_rank` and `min_iv_rank + low_iv_credit_floor_iv_rank_offset` (0.05), the floor is
   `min_credit_pct_of_width × low_iv_credit_relief_multiple` (0.85) — always strictly below the arm's
   own floor, so the ladder is never flattened. The legacy absolute keys apply only if an arm sets
   them. Prefer a wider width clearing the relaxed floor to a narrow one barely clearing it.

   **Fee-adjusted credit floor (hard stop, in addition)**: `db get_fee_estimate --symbol <SYM>
   --lookback fee_estimate_lookback_trades`; use `avg_fee_per_contract` when `sample_size ≥
   fee_estimate_min_sample_size`, else `fallback_per_contract`. Reject if `(ic_natural_bid ×
   multiplier) − est_fee_per_contract < applicable_credit_pct_of_width × wing_width × multiplier`, with
   the same pct the Credit floor applied. The estimate is open-only (most 0DTE ICs expire with no
   closing commission) — a floor, not a round trip; note higher drag when an active close is likely.
   This exists because a narrow, low-credit setup can pass the pct check while fees eat the credit
   (XSP once: $4.00 gross, $4.96 fees). Narrow low-IV SPX credits of ~$0.45–$0.75 net qualifying is
   intended.

   **FOMC blackout hard stop** (`get_calendar` → `fomc_dates`; if `fomc_year_known` is false, no
   scheduled gating is available): at/after 13:30 reject entries and close open positions before the
   announcement; entries allowed only before 13:30 or after 14:30, and post-announcement only with IV
   rank ≥ 0.40 and intraday range ≤ 0.5% of spot (`tt get_intraday_range`, off the streamer's
   `stream_summary`). At 13:00 on FOMC days tighten `stop_trigger_current` on all open ICs by 10%.

   **Quarterly expiry hard stops** (`quarterly_expiry_dates` / `triple_witching_dates`): (a) no entries
   in open_volatile; (b) call OTM ≥ 0.0067; (c) halt entries for the session once intraday range exceeds
   0.5% of spot; (d) on triple witching, no entries after 12:30 and force-close all by 14:00.
   (e) **Live only: no new entry all day on a quarterly expiry** (the last trading day of a quarter,
   2026-09-30). `tt execute_trade --live` refuses any opening order that day with
   `quarter_end_no_new_entries`; closes still go through. Paper keeps (a)-(c) and trades the day.

   **Regime gate (ICs only)**: `trending_regime` rejects IC entries for this symbol this iteration. A
   VIX pause hits every symbol; ATR and GEX pauses are symbol-specific. Log reason and metric. ORB is
   NOT blocked — it profits from the directional environment.

   **GEX strike placement** (GEX available and positive; guidance only): anchor the short call at/above
   `call_wall`, the short put at/below `put_wall`; if call GEX dominates, the call may sit closer and
   the put gets more room, and vice versa. Delta, OTM and credit hard stops always win.

   **GEX stop tightening** (in Step 5, per trade's own symbol): (a) zero-gamma threat → toward 0.85;
   (b) flip breached (`gex_negative`) → toward 0.80 and consider closing the threatened side now; (c)
   approaching but not through the call wall → hold (dealer resistance); if it breaks on volume, close
   the threatened side. One symbol's GEX never affects another's positions.

   **Late-entry credit bias**: if enabled, IV rank ≤ 0.45 and before 12:00, skip IC entries until noon
   (morning entries carry 3+ hours of directional exposure for the credit theta pays by afternoon).
   IV rank above 0.45 bypasses it.

   **ORB debit spread** (if `orb_enabled`, range set, and before 12:00):
   - Bullish if spot > `orb_high × (1 + orb_breakout_threshold_pct)`; bearish if spot <
     `orb_low × (1 − orb_breakout_threshold_pct)`.
   - On a first-of-session break with no ORB open: bull call debit spread (buy ATM call, sell
     `orb_wing_width_by_symbol[symbol]` higher) or bear put debit spread (buy ATM put, sell that width
     lower), same-day expiry. Dry-run, then submit live if it passes.
   - Record in `loop_log` (`orb_entry`, full legs), never `ic_trades`. Close at 100% profit, 50% loss or
     15:30, checked every iteration.
   - One ORB per day per direction **per symbol**; no re-entry after a stop-out.
   - **Log every evaluation**: an `orb_evaluated` row each run with `underlying_price`, `orb_high`,
     `orb_low` and outcome (`no_breakout`, `entered`, `already_open`, `direction_exhausted`) — otherwise
     a quiet day and a broken check are indistinguishable.

7. **Execute entry** (per symbol, if Step 6 said yes): `/execute-entry --symbol <SYM>`, then continue
   to the next symbol. **Timing end** (whether or not an entry ran): log `python -m cherrypick.meic.db
   log_loop_action --symbol <SYM> --action timing_entry_evaluation --duration_ms <elapsed>`; review with
   `db get_step_timing --action timing_entry_evaluation`.

8. **Record and notify** (once per iteration, after every symbol): a `loop_log` row per symbol plus one
   account-wide row (`symbol` NULL), a one-line status covering all symbols, then schedule the next
   wakeup. After 15:55 on a trading day, run EOD once: persist closing NLV, spawn `/eod-report live`,
   log completion. (The paper loop writes its own deterministic EOD report; `/eod-report` with no
   argument reproduces both.)

---

After Step 8, schedule the next wakeup:

| Condition | Interval |
|---|---|
| No market action expected within 90 min (weekend, holiday, or before 08:00 ET) | **end loop** |
| After 15:55 ET on a trading day (EOD complete) | **Step 8 then end loop** |
| Pre-market 08:00–09:00 ET | **600s** |
| Pre-market 09:00–09:29 ET (approaching open) | **120s** |
| Market hours, off-hours outside pre-market window | **1800s** |
| Market hours with no open positions | **300s** |
| Market hours with one or more open positions | **120s** |

Use the longest applicable interval.
