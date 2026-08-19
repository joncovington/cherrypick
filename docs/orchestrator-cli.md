# Orchestrator CLI reference

Every command the orchestrator exposes. Run them from `packages/orchestrator` as `python run.py <cmd>`;
a pip install (`pip install -e ".[dev]"`) also exposes them as `cherrypick <cmd>` / `python -m cherrypick`.

All commands are **read-only or paper-only** except the narrow onboarding pair (`connect`/`account`) and
`settings`, which write *configuration* (never an order). See
[guardrails-and-modes.md](guardrails-and-modes.md).
(The flies module's own live-trading loop is a separate program, started with `/live-flies-start` rather
than any command on this page — see [strategy-engines.md](strategy-engines.md#flies--0dte-net-credit-butterflies).)

## Onboarding & setup

| Command | What it does | Key flags |
|---|---|---|
| `init` | Scaffold + validate `~/.cherrypick/config.json` from the template (first-run). | `--force` (overwrite an existing config) |
| `connect` | **The onboarding path for most setups.** The suite wizard: the shared login entered once (hidden input), an offer to migrate any per-module credential copies into it so there is one rotation point, one suite-wide account designation, and optional webhooks (Enter skips). Never trades. | — |
| `connect --module <m>` | The per-module **override** layer: runs that module's own hidden-input credential tool for the OAuth secrets (the orchestrator never sees `client_secret`/`refresh_token`) and selects that module's account. Only needed when one module must differ from the suite default. | `--module meic\|earnings` |
| `account` | List, set, or clear the **suite-wide** designated live-trading account (masked) — the default every module inherits through the store fallback chain. Configuration only. | `--set <last4\|index>`, `--clear`, `--yes` |
| `account --module <m>` | The same, for one module's own designation (its override). Configuration only. | `--module`, `--set <last4\|index>`, `--clear`, `--yes` |
| `migrate-home` | Move in-repo config into `~/.cherrypick` and sweep leftovers. Dry-run by default. | `--apply` (perform the move) |
| `secrets-set` | Store a Slack/Discord webhook URL — or the Lossdog Clerk `__client` cookie — in the OS keyring (prompted without echo if `--url` omitted). `discord_follow` is a **second** Discord webhook so the Follow Feed can post to its own channel; `lossdog` is not a webhook but the cookie the Lossdog notifier mints its feed tokens from. | `--channel slack\|discord\|discord_follow\|lossdog`, `--url` |
| `secrets-status` | Show which push-channel secrets are configured (secret-free). | — |
| `secrets-delete` | Remove a stored secret. | `--channel` |
| `settings` | Local web editor for every config file + a keyring secrets manager (loopback `:8804`) — the suite's one mutating HTTP surface, run on demand, never watchdog-started. Live-trading gate fields render read-only. With `--organize` it instead reorders a live config into its example's sections and exits (no server). | `--host`, `--port` (def `8804`), `--no-browser`, `--organize [target]`, `--apply` |

## Turning the suite on/off

| Command | What it does |
|---|---|
| `install` | Register the ONE `cherrypick-supervisor` anchor task, start the supervisor daemon (which derives every job — module paper loops, earnings entry/exit, Dolt keep-alive, watchdog, streamer-health, trade-notify, log-archive, opt-ins — from config each pass), delete every legacy per-job task, and start the streamer / services if down. Refuses while flies is live-armed today (`--force` overrides). The suite review runs as two daily jobs (`review-provisional`, `review-final`). Full verified inventory: [operations.md](operations.md). |
| `uninstall` | Delete the anchor task first, stop the supervisor, remove any legacy per-job tasks, and stop the orchestrator's own background services. Recorded data and config are untouched. |
| `status` | Supervisor liveness + per-job registry (last start/exit, next run) + heartbeats. File reads plus one anchor-task query; falls back to the OS-scheduler snapshot on a pre-cutover box. |
| `supervise` | Run the supervisor daemon loop in the foreground (diagnostic; the anchor task keeps it alive normally). `--stop` asks a running daemon to exit via its stop file. |
| `ensure-supervisor` | The anchor task's 2-minute probe: fresh heartbeat + live PID → no-op; otherwise start the daemon detached; after 3 consecutive failed probes, raise one CRITICAL (`supervisor.down`). Stdlib + local files only. |

## Health & reliability

| Command | What it does | Key flags |
|---|---|---|
| `doctor` | One green/red readiness check — Python, config, broker session, data feed, DBs, (earnings) Dolt. | `--fast` (skip the authenticated broker round-trip) |
| `watchdog` | Run one watchdog pass — the reliability check the scheduled task invokes (data-fresh, streamer alive, earnings SLA, dedup/re-notify/recovery). stdlib + OS shell only. | — |
| `streamer-health` | Streamer liveness only — the supervisor's 60 s in-session job (09:00–16:00 ET, trading days), the whole-session successor to the retired `cherrypick-preopen` windowed task. Still exists so the full 10-minute tick never has to speed up to protect the streamer, whose 09:30–09:35 opening-range window is unrecoverable once missed. Reuses `_check_streamer_health` and the normal notify path; writes no heartbeat; no-ops on a non-trading day. | — |
| `preopen-check` | Deprecated alias for `streamer-health`, kept so the legacy preopen flag and any external caller still resolve. Same pass, same notify path; prefer `streamer-health` in anything new. | — |
| `reconcile` | Paper↔live isolation guard: enumerate **every** account on the login (read-only `list_accounts`/`get_positions`/`get_account_info`) and flag any open positions/BP a paper-only suite shouldn't hold. On-demand; never trades; accounts masked. `reconcile.schedule.enabled` promotes it to a daily `cherrypick-reconcile` task (`--scheduled` notifies on any non-FLAT verdict) — the phase-5 posture once anything trades live. | `--scheduled` |
| `positions` | **Live P/L by underlying for the real broker account** — the question the paper dashboards cannot answer. Prices every open position from the shared stream cache first and the feed only for what the cache lacks (the suite-wide streamer-before-API rule), writing nothing anywhere. Marks are midpoints: legs whose bid/ask is wide relative to their price are flagged with the dollar doubt they carry, and a leg neither source can price is reported and excluded from the totals rather than counted as zero. Read-only broker calls; accounts masked; never scheduled. Exit 2 = broker unreachable, 3 = something went unpriced. | `--detail`, `--account <last4>`, `--json` |
| `notify-test` | Fire a test notification through every configured channel. | — |
| `notify-trades` | Push new paper entries/exits to the trade channels (also runs best-effort on each watchdog tick). | — |
| `notify-follow` | Push new [tastylive Follow Feed](https://follow.tastylive.com) orders — other traders' fills, as shown on the platform's Follow page — to their own channel. **Off by default**; a network-calling notifier, so it runs on its own task and *never* on the watchdog tick. Read-only, no auth, no broker. Cards render through the shared feed-card grammar (`cherrypick.notify.feedcard`, same layout as `notify-lossdog`) and post under the service's own name/avatar. | — |
| `notify-lossdog` | Push new Lossdog VIP trade-feed trades to the follow channel (`discord_follow`). **Off by default**; a PRIVATE, undocumented API read with a personal token (see [the token section](configuration-and-storage.md#lossdog-vip-feed-token)) — its own task, never on the watchdog tick, and an outage or dead credential degrades to "no notifications". Cards render through the shared feed-card grammar (`cherrypick.notify.feedcard`, same layout as `notify-follow`) and post under the service's own name/avatar. | `--replay-last N` (re-post the newest N regardless of seen state), `--dry-run` (print embeds as JSON instead of posting) |
| `notify-desk` | Card each manual-desk order on submit and again when it reaches a terminal state (filled / cancelled / rejected / expired). **Off by default**; like `notify-follow` it runs on its own task and *never* on the watchdog tick — it pushes a Discord card **and** asks the broker for order status. Reads the desk's audit journal as a file and never imports `cherrypick.desk`, so observing desk orders cannot make the submit path reachable from scheduled code. | — |
| `restart-console` | Dev convenience: kill the console's process tree so the supervisor replaces it on its next tick, picking up whatever the checkout currently builds to. Never scheduled, never called from the watchdog. | — |

## Reporting & review (the read side)

| Command | What it does | Key flags |
|---|---|---|
| `report` | Unified cross-module paper P&L: totals + per-profile breakdown, **gross and net** of costs. | `--eod` (today ET), `--date YYYY-MM-DD` (one session; default all-time) |
| `calibrate` | Per-profile calibration readings + advisory promotion recommendations (never changes risk settings). | — |
| `review` | Suite end-of-day review (`packages/review`): build one session's cross-module fact set and render it. `--final` marks the session final and re-runs reconciliation; without it the pass is provisional. Read-only over every module's ledger. What the two daily supervisor jobs run. | `--final` |
| `archive` | End-of-month rotation: zip each finished month's dated reports + rotated log backups into `logs/archive/<YYYY-MM>/<scope>.zip` and remove the originals (idempotent; never touches the current month or an active `.log`). | `--month YYYY-MM`, `--dry-run` |

There is no `dashboard` command. The suite's read surface is the **console** (`packages/console`, on
127.0.0.1:5070), which the supervisor keeps running as an always-on resident job; the orchestrator's
static/served dashboard was deleted on 2026-08-12. See
[reporting-and-dashboard.md](reporting-and-dashboard.md) for how the read commands compose and the
report files they produce.

## Module drivers (invoked by supervisor jobs — rarely run by hand)

| Command | What it does |
|---|---|
| `run-earnings-entry` | Run the Earnings paper **entry** pass now (the daily ~15:45 ET job). |
| `run-earnings-exit` | Run the legacy Earnings paper **exit** sweep now. Manual/backfill only since the 2026-08-12 lifecycle cutover — the managed loop (`earnings-paper`, every 60s) owns exits. |
| `run-earnings-symbol-watch` | Run the Earnings forward-preview scan now (`symbol_watch.py refresh`) — the source of the console's read-only Earnings page "Upcoming" section. Purely informational; off by default (`symbol_watch.enabled`). |
| `ensure-dolt` | Start a module's declared Dolt server if down (the earnings keep-alive job). |

The module paper loops are supervisor jobs too: MEIC as a 60 s `--once` spawn
(`modules.meic.paper.tick_interval_seconds`), flies as the one **resident** child (its own
`--interval 15` mode in-session, supervised for death and silence) plus a 60 s off-session `--once`
job that owns settlement. The modules' `--install-task` helpers remain only for standalone
(orchestrator-less) use.

## Global flags

`--date YYYY-MM-DD` (report) · `--eod` (report — scope to one
settlement session) · `--live` (report — read the modules' separate live ledgers instead of paper; a
deliberately separate path calibrate can never reach) · `--fast` (doctor) ·
`--module` / `--set` / `--clear` / `--yes` (connect/account) ·
`--host` / `--port` / `--no-browser` (settings) · `--apply` (migrate-home,
settings --organize) · `--organize [target]` (settings) · `--stop` (supervise — ask a running
supervisor to exit) · `--detail` / `--account <last4>` / `--json` (positions) · `--scheduled` (reconcile — the daily job's mode; notifies on any non-FLAT
verdict) · `--month` / `--dry-run` (archive) · `--channel` / `--url` (secrets) · `--force` (init).

## Slash-command equivalents (Claude Code)

Some workflows are also exposed as checked-in slash commands for interactive sessions:
`/install`, `/uninstall`, `/console`, `/meic-start`, `/earnings-start`. These are dev
conveniences, never a runtime dependency.
