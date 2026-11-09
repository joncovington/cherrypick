# cherrypick-meic — Operational Instructions

> Operating contract for the cherrypick **MEIC** engine. Human guides: [`docs/`](docs/README.md); the
> entry-gate catalogue: [`GATES.md`](GATES.md); suite context: the root
> [documentation index](../../docs/README.md). The declared boundaries (dates from which rows were
> produced under a different rule; never pool across one) are in
> [docs/reference.md](docs/reference.md#declared-boundaries).

This module's old word `risk profile` is the root's `arm`; the column is `arm`, and `db.init_db` refuses a
ledger that still says `risk_profile` (`scripts/arm_column_migrate.py --only risk_profile` renames one).
The arm registry keeps the key `profiles`.

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
  (`modules.meic.paper.tick_interval_seconds`; the module has no launcher of its own), starts the streamer, and watchdogs both. All writes go
  to `~/.cherrypick/data/meic/paper_trades.db`; the live account and `meic_trades.db` are never
  touched. **It is not a full re-implementation of the Loop Steps**: GEX-wall strike anchoring, the ORB
  debit-spread path and judgement-based stop tightening are agent-loop-only. The engine runs the
  simpler deterministic mechanics `paper.py` codifies (per-arm gate thresholds, fixed
  `stop_trigger_ratio`, no discretionary tightening), which is what makes its daily roll-up and every
  `analytics.py` read reproducible.
- **Live (deterministic, armed per day).** `cherrypick/meic/live_loop.py` trades one pinned
  `live.symbol` with the same `paper.py` decision functions, against `meic_trades.db`. The supervisor
  runs it as the `meic-live` job (one `--once --live` tick a minute), and it is inert unless
  `readiness()` comes back empty. New ICs need a human's `/live-meic-start` arm record for today;
  management does not (below). No ORB, no discretionary tightening, no working-order repricing
  beyond the stop rule.
- **Interactive (agent-driven).** You execute the Loop Steps in [docs/agent-loop.md](docs/agent-loop.md) for manual `/meic-start` sessions,
  through `tt execute_trade --live`. That path needs `enable_live_trading: true` and the designated
  account, but not `live.symbol` or `live.gate0_confirmed`. An **opening** order on
  it also obeys the loop's entry rules (`tt._manual_open_refusal`): no halt flag, today's arm record
  (`/live-meic-start`), and the written kill criteria (BLOCKED refuses; otherwise each leg is capped
  at `kill.sized`). A closing order is never held back. The orchestrator never runs it.

> ⚠️ **Think hard before adding a dependency to the loop path.** Entry/stop/logging decisions depend
> only on `tt.py`, `db.py`, the streamer's cache and this file. Every added dependency is a new failure
> mode, and a hung one looks exactly like a quiet market from inside a loop.

## Orchestrator & shared core

- **`cherrypick.core` resolves by editable install only.** No `sys.path` bootstrap for core exists in
  this package — do not reintroduce one; a failing import is fixed by `pip install -e packages/core`.
  Calendar and fees come from `cherrypick.core.calendar` / `cherrypick.core.fees`; add a symbol's fee
  by extending `cherrypick.core.fees`, never by hardcoding here.
- **The orchestrator boundary is strict.** It runs this module by subprocess for paper collection, fires
  `meic-paper`, watchdogs it and the streamer, and runs the `meic-live` tick (entries from a
  human's arm record only); it also runs `meic-fee-reconcile`, `meic-regime-cuts` and the suite's
  `settle-overdue-live`, and reads `paper_trades.db` for reporting. It **never
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
  `_advised_base` (the decision's entry, else the legacy `advised:<arm>` reading, else
  `advice.base_profile`), never split out of the tag. Never touches the live loop.
- **`experiment_id` stamping follows `cherrypick.core.advice.stamp_for`** (`packages/core/CLAUDE.md`);
  the stamp is carried per book from its own entry.
- **No section payload.** The console reads both ledgers directly, so there is no second payload to
  keep in step. Shared read-side queries live in `cherrypick.meic.analytics`.

## Tastytrade auth and tool reference

- **OAuth2** via the official [`tastytrade`](https://github.com/tastyware/tastytrade) SDK (session tokens
  auto-refresh; refresh tokens are long-lived). **Credentials live in the OS keyring only** — never
  files, env vars or logs.
- All broker operations are `python -m cherrypick.meic.tt <command>`, JSON to stdout. Secrets: `tt
  secrets_set` / `tt secrets_status`.
- **`enable_live_trading: true` in `config.json` is the master switch.** There is no environment
  fallback: an env var is outside the orchestrator's guarded config table and carries no attestation.
- **The live loop is armed one day at a time (the flies/bwb posture).** `/live-meic-start`
  writes the arm record (`--install-task`, the agent path that prompts for nothing because the
  slash command already asked; a person arms with `--arm --confirm YES`, which prints `DISCLAIMER.md`
  and refuses on any other word; `--disarm` is the old `--uninstall-task`; all in
  `cherrypick.core.live`), and each `--live` tick deletes
  it once stale or past `live.disarm_time`, before any broker read. **The arm gates new ICs only.** Unarmed, a tick whose book holds nothing today ends there without a broker call;
  one that holds an IC or a pending entry runs management only (fill confirmation, resting stops,
  stops, force-close, settlement), with entries blocked as `not_armed`. The supervisor therefore
  runs `meic-live` every trading day from the open to the disarm cutoff, armed or not. Unarmed
  with a live gate unmet (the master switch off), the tick touches nothing and says `unmanaged`;
  the watchdog's `live_unmanaged` is the CRITICAL. `--status` is the arming
  command's and the watchdog's read (no broker). Its `arm_ready` (`arm_verdict`) is the
  one answer to "may live arm": `blockers` are the arming command's stop-here states (an unmet
  gate, orphans, a held submission, an overdue session, an unreadable ledger, no supervisor, kill
  criteria blocked, read with `killcriteria.peek` so a readout never latches) and `warnings` what
  a YES may pass (the halt flag, a tripped or unset breaker, positions held). `--install-task` and
  `--arm` refuse while a blocker stands. `live.max_structures_per_day` (default 1) caps new
  ICs a session (`null` lifts the count cap, so buying power alone sizes); a one-tick lock keeps a
  slow tick from overlapping the next. `live.daily_loss_halt_dollars` and the halt flag block NEW ICs
  only: fills are still confirmed and open ICs still stopped and force-closed. The breaker sums
  today's `ic_trades.pnl`, which is gross (before fees), so it trips on gross, not net.
  `live.kill` is the arm's frozen kill criteria, read over the live ledger's net
  through `core.killcriteria.gate`: reduced halves the order's quantity (at least one), blocked
  refuses new ICs and latches (`state/kill/meic.json`, a watchdog CRITICAL) until a person clears it;
  missing or malformed criteria refuse. A closed live IC since the criteria's `written` date with
  **no net** (`pnl` or `fees` NULL) also refuses new ICs: the verdict skips a row with no net, so its loss would pass the drawdown and streak
  check. The refusal names the ICs, does not latch (recording their `pnl` and `fees` is the fix) and,
  like every kill verdict, never holds back a fill confirmation, stop or force-close; earnings
  refuses on the same rows. Derived from the arm's replay with
  `python -m cherrypick.core.killcriteria derive --db <run dir> --schema meic_ic --arm <arm>`.
  `live.use_order_alert_daemon` makes the supervisor keep `cherrypick.meic.alert_daemon` up until
  `disarm_time`; it records order alerts in `live_alerts.db` and starts a tick when an order the
  ledger knows stops working, so a fill is confirmed in seconds rather than at the next minute.
- **Live settles on the official print only.** A held side settles at
  `core.settlement.official_index_close` (the posted close, else Yahoo/Barchart); until one is posted
  the IC stays open and the next tick asks again. A past session left open is settled at that day's
  close by `live_loop --settle-overdue` (the hourly `settle-overdue-live` job), or by hand with
  `--settle --date --price`. **Either asks the broker first** (a close that filled unseen would
  otherwise be read as cancelled and its side settled at expiry) when the session has an
  order in doubt (a pending entry or close, a resting stop last seen working): the tick's own
  `_poll_resting_stops` and `_confirm_fills`, through `_SettleBroker`, which reads statuses and
  refuses to place, replace or cancel. A fill is booked at the broker's legs and a dead order
  cleared; anything unresolved, or no broker to ask, leaves the session open (`left`).
- **The orphan sweep is suite-wide.** flies, bwb and MEIC trade SPX in one account, so each loop
  publishes its ledger's order ids (`core.execution.publish_known_orders`) and counts the others' as
  known; an order none of them records blocks new entries, and a sweep that cannot look does too.
  An entry with a leg opposite one another live loop holds is refused (`core.livepositions`).
- **Every live submit goes through `cherrypick.core.execution.Broker` (`make_broker`)**, which re-checks
  `readiness` on every submit and applies the deploy governor. `adjust_order` goes through
  `cherrypick.core.broker.replace_order` (same preflight and governor as `execute_trade`); a core test
  scans every package for a `dry_run=False` outside the seam.
- **`tt`'s order commands act on the designated account or refuse.** `execute_trade` (dry run
  included), `adjust_order` and `close_position` resolve through `_trading_account`: no designation
  (unset or unreadable) is `AccountNotDesignated`, never the login's first account, and an explicit
  `--account_number` must be the designated one. Read-only commands keep `resolve_account`'s fallback.
- **An entry is written ahead of its order.** The row it will need goes into
  `entry_intents.json` beside the live ledger under a minted `meic-<uuid>`, sent as the order's
  `external_identifier`; an intent that cannot be written sends no order. The saved row clears it.
  A leftover intent (the process died between submit and save, or the submit was uncertain) is
  resolved first thing in the next live tick against one read of today's orders: found, its row
  is saved pending; absent, it is dropped; unreadable, or from a past session, it blocks entries.
  It also keeps an unarmed tick running (`holds_today`) and blocks arming (`entry_intents`).
- **The live loop records fills only on broker confirmation.** An entry is saved `status='pending'`;
  `_confirm_fills` at the top of each tick flips it to `open` at the ACTUAL net credit
  (`fill_confirmed_at`), or to `cancelled` with zero P&L if it dies unfilled (freeing its slot).
  `put_credit`/`call_credit` follow the fill (`entry_fill_credits`): each side from the
  broker's per-leg fills when all four legs are reported and sum to no less than the limit, else the
  booked fill split in the quote-time modelled ratio; `side_credit_source` says which. Stop and
  expiry P&L are charged per side, so the sides add up to the row's net. A
  pending row holds its slot but is not managed for exits. A close stashes its decision on the row
  (`pending_exit_json`, `{side}_stop_fill_status='pending'`), and the shared exit accounting
  (`paper._apply_exit_decision`) runs only on confirmation, at the actual price: the side's two leg
  fills when both are reported on the expected action and the debit is within the limit, else the
  booked price (`exit_fill_price`; `{side}_exit_fill_source` says which). A close still working
  next tick is cancelled and re-priced under the same stop rule off fresh quotes; one that dies clears
  its marker and the side is re-evaluated (nothing is reopened). An unfilled end-of-day close on a
  cash-settled side is cancelled at settlement and falls through to expiry. Resubmit-not-escalate,
  re-price-every-tick and settle-not-chase keep live's rule byte-identical to paper's, so the paper↔live
  gap is the measurement.
- **A failed entry snapshot never skips management.** A feed outage must not leave a stop unmanaged:
  a side left alone through an outage can be stopped late and far past its trigger. The full snapshot is
  the entry menu. When it fails (no spot, no chain) and the live ledger holds an IC, `_tick` builds a
  manage-only snapshot from the held legs alone (`_manage_snapshot`): the stream cache within 10 s,
  else one broker REST read (`core.broker.rest_quotes`, no DXLink). Entries are blocked
  (`manage_only (...)`) and fill confirmation, stops and force-close run as usual. A held leg neither
  source prices fails the tick (exit 1, `held legs unpriced`) and is never priced off an older quote.
  Every failed tick writes its reason to `meic_live.log` and stderr (`_fail`), and a placed entry
  whose row will not save raises the suite halt flag.
- **What the loop tells a person.** `--status` names each of today's held ICs in words
  (`open_ics`, `describe_ic`: id, strikes, sides still held, credit, stop), and the watchdog's live
  findings quote them. Every `CRITICAL` line a tick logs (and `_quiet`'s, and an unpriced-legs exit)
  is appended to `live_criticals.jsonl` beside the ledger; `--status` carries today's
  (`criticals_today`) and the watchdog's `meic.live_critical` announces each once. The module
  still never talks to a notification channel.
- **Resting stops (`live.resting_stop`, off by default).** Once an entry's fill is
  confirmed, each held side gets a Day stop-limit at the broker (`live_orders.resting_stop_spec`):
  trigger = the row's stop ratio x the whole net credit, limit `limit_offset_frac` of it above,
  capped at the wing. Each tick polls them first (`_poll_resting_stops`): a fill becomes the side's
  close and `_confirm_fills` books it at the broker's legs; a dead one is re-placed after management
  (`_place_resting_stops`). The loop's own stop stays the backup and chaser. Before it closes,
  force-closes or settles a side it cancels that side's resting stop and needs the cancel confirmed
  (`_clear_resting_stop`); a refused cancel means nothing is sent and settlement waits a tick, so a
  side never has two closes. Their ids are in `_ORDER_ID_COLUMNS`, so the orphan sweep knows them.
  The broker triggers off the spread's NBBO ask, above the loop's mid-plus-haircut, so switching it
  on is a measurement boundary against live-shadow. The replay models it per arm with `stop_resting`
  (`paper._resting_stop_fill`: the ask trigger, filled at the tick's ask or, with
  `stop_resting_fill: "trigger"`, at the trigger); an arm without it is priced as before.
- **Order legs are OCC; ledger legs are streamer symbols.** `live_orders.order_symbol` converts at the
  order boundary (`core.streamcache.streamer_to_occ_symbol`) and refuses a symbol with no OCC form;
  the test `FakeBroker` rejects any non-OCC leg. The agent path's `tt execute_trade` and
  `adjust_order` run every leg through the same function before anything reaches the broker
  (`reason: leg_symbol_not_orderable` on a refusal). A live row records the quantity its order carried
  (`synthetic_entry_fill(quantity=)`), and the exit accounting scales P&L, close fees and slippage
  by the row's quantity -- a no-op on paper, where every row is quantity 1. **Live entries are one
  contract (`MAX_LIVE_QUANTITY`)** until partial fills are modelled: no order path reads
  a filled quantity, so a close cancelled after a partial fill would be re-sent at full size. A
  larger configured quantity refuses entries (`quantity_over_1`) and blocks arming; what is held
  is still managed.
- **Each loop declares its own open legs to the streamer.** `stream_request.py` writes `meic.json` from
  the paper loop and `meic-live.json` from the live loop's preamble (`register_live`, best-effort,
  dry-run included), each with the open-IC leg query against that loop's OWN ledger. Leg columns hold
  DXLink streamer symbols, subscribed verbatim.
- `get_quote`, `get_option_chain` and `get_strategies` read the shared stream cache
  (`~/.cherrypick/data/marketdata/stream_cache.db`) first when data is < 10s old, before opening a
  DXLink connection; run the streamer during active trading.

## Market data

MEIC has no streamer of its own. The standalone producer (`packages/streamer`) writes the shared cache
(path from `paths.py::stream_cache_path()`), and MEIC declares what it needs through
`stream_request.py`: **every symbol in `symbols`**, with a near-the-money window, plus every open IC
leg of the paper and live ledgers (re-read by the producer every poll). `Summary.open_interest` lands
in `stream_oi` and feeds `get_gex --symbol <SYM>`, computed per symbol from that symbol's own window;
the opening range comes from the producer's `orb.py`. `tt stream_status` reports the producer's health.

## The live block (arm registry)

- **The live block, flies' `live.arm` seam.** `live.arm` names the registry arm live
  trades (unset: the top level); `live.max_open_bp_dollars`, `wing_widths_by_symbol`,
  `entry_window_start` and `entry_window_end` apply to live alone and only narrow the arm (later start,
  earlier end, smaller cap; widths replace). **Live-only sizing belongs there, never at the top level**:
  every paper arm that leaves a top-level key unset inherits it, `control` included, so a live cap at
  the top level would cap the sampling substrate. Both loops warn while a live-only key sits at
  the top level off its shipped default, or while the block shapes live and no enabled arm follows it;
  `python -m cherrypick.meic.live_migrate` moves such keys (dry run by default, `--apply` backs up).
  `readiness` refuses a `live.arm` that is missing or itself follows live.

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
  correction, not a measurement break. Without the sign flag a window net negative end to end has no
  flip crossing and would tag `unknown`.
- **Paper must never be confidently wrong.** `paper.evaluate_open_trade` refuses to settle without a
  settlement price, holding with `settlement_price_unavailable` as an unquotable leg does. Because that
  refusal is silent (the position stays open), `paper_loop --status` reports `session_settled`,
  `positions_today` and `data_reason`, `settlement_check` is enabled for meic in the orchestrator, and
  `tests/test_paper_loop_status.py` pins those field names — a check that cannot fire is worse than
  none.
- **A session left open past the close is settled at the official close, and only that.**
  `paper_loop.settle_overdue` runs from the tick's outside-window branch (and `--settle-overdue` by
  hand): each cash-settled (session, symbol) still open — a past day at once, today's from 20 minutes
  after settlement — settles through `paper.process_symbol` at one print from
  `core.settlement.OFFICIAL_SOURCES` (a past day by date, today's via `tt.py get_official_close`),
  clock pinned to the settlement instant, no entries evaluated, then the daily_summary roll-up.
  No official print, a physically settled symbol, an event force-close day, or a session already
  partly settled at another print: left open and logged, never a guess. An open row is honest; a
  settlement at a guessed price is a wrong number that looks final (an outage across the close once left
  hundreds of paper positions open, and this is the rule that handles them).
- **`analytics.settlement_audit` (`run.py settlement-audit`) is re-runnable** and reproduces each
  resolved fill from the written convention with its own `_side_settle_value`, **deliberately
  duplicated, not imported** — an audit that imports what it audits only confirms the function equals
  itself. The invariant that matters most: one settlement price per (session, symbol).
- **Max adverse excursion** (`put_mae_spot`/`call_mae_spot` + times) makes any stop distance derivable
  after the fact; distance itself is not stored (spot minus a stored strike). **Cannot be backfilled**
  (the cache keeps no spot history), and like the `settle_*` counterfactuals it is recorded, never
  acted on.

## Elsewhere

- **Interactive sessions:** the Loop Steps (hard stops, stop management, ORB, wakeup cadence) and the
  `tt` command table are in [docs/agent-loop.md](docs/agent-loop.md). Read it before any interactive
  iteration.
- **Read-side CLI, replay, advisor bounds, arms and stop-policy derivation, and every config option**
  are in [docs/reference.md](docs/reference.md). Read the arm section before changing an arm, and the
  config row before changing a key.
