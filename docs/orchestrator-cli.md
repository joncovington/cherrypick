# Orchestrator CLI reference

Every command the orchestrator exposes. Run them from `packages/orchestrator` as `python run.py <cmd>`;
a pip install (`pip install -e ".[dev]"`) also exposes them as `cherrypick <cmd>` / `python -m cherrypick`.

All commands are **read-only or paper-only** except the narrow onboarding pair (`connect`/`account`),
which writes *configuration* (never an order). See [guardrails-and-modes.md](guardrails-and-modes.md).

## Onboarding & setup

| Command | What it does | Key flags |
|---|---|---|
| `init` | Scaffold + validate `~/.cherrypick/config.json` from the template (first-run). | `--force` (overwrite an existing config) |
| `connect --module <m>` | Guided per-module onboarding: runs the module's **own** hidden-input credential tool for the OAuth secrets (the orchestrator never sees `client_secret`/`refresh_token`) and selects the live-trading account. Never trades. | `--module meic\|earnings` |
| `account --module <m>` | List, set, or clear a module's **designated** live-trading account (masked). Configuration only. | `--module`, `--set <last4\|index>`, `--clear`, `--yes` |
| `migrate-home` | Move in-repo config into `~/.cherrypick` and sweep leftovers. Dry-run by default. | `--apply` (perform the move) |
| `secrets-set` | Store a Slack/Discord webhook URL in the OS keyring (prompted without echo if `--url` omitted). | `--channel slack\|discord`, `--url` |
| `secrets-status` | Show which push-channel webhooks are configured (secret-free). | — |
| `secrets-delete` | Remove a stored webhook. | `--channel` |

## Turning the suite on/off

| Command | What it does |
|---|---|
| `install` | Register all scheduled tasks (MEIC paper loop, earnings entry/exit, watchdog, fast trade-notify, EOD digest, monthly log-archive, and — if enabled — the AI EOD insight) and start the streamer / services if down. See [scheduling](reporting-and-dashboard.md) and the task table in [configuration-and-storage.md](configuration-and-storage.md). |
| `uninstall` | Remove all cherrypick-managed scheduled tasks and stop the orchestrator's own background services. Recorded data and config are untouched. |
| `status` | Task registration + last heartbeats + last earnings run. Local OS-scheduler queries only. |

## Health & reliability

| Command | What it does | Key flags |
|---|---|---|
| `doctor` | One green/red readiness check — Python, config, broker session, data feed, DBs, (earnings) Dolt. | `--fast` (skip the authenticated broker round-trip) |
| `watchdog` | Run one watchdog pass — the reliability check the scheduled task invokes (data-fresh, streamer alive, earnings SLA, dedup/re-notify/recovery). stdlib + OS shell only. | — |
| `reconcile` | Paper↔live isolation guard: enumerate **every** account on the login (read-only `list_accounts`/`get_positions`/`get_account_info`) and flag any open positions/BP a paper-only suite shouldn't hold. On-demand; never trades; accounts masked. | — |
| `notify-test` | Fire a test notification through every configured channel. | — |
| `notify-trades` | Push new paper entries/exits to the trade channels (also runs best-effort on each watchdog tick). | — |

## Reporting & review (the read side)

| Command | What it does | Key flags |
|---|---|---|
| `report` | Unified cross-module paper P&L: totals + per-profile breakdown, **gross and net** of costs. | `--eod` (today ET), `--date YYYY-MM-DD` (one session; default all-time) |
| `calibrate` | Per-profile calibration readings + advisory promotion recommendations (never changes risk settings). | — |
| `eod-digest` | Write `logs/eod-digest-<day>.md`: one session's cross-module roll-up + a conversational snapshot + links to each module's paper-eod / eod-analysis files. | `--date` |
| `notify-eod` | Write the digest **and** push a one-line summary through the notify channels (what the scheduled `cherrypick-eod-digest` task runs). | `--date` |
| `eod-insight` | **Opt-in AI synthesis** over the day's deterministic reports → `logs/eod-insight-<day>.md`. Needs Claude Code on PATH + `eod_insight.enabled`; read-only, no dangerous tools, off the reliability path. Best-effort (prints `skipped`/`error` when absent/disabled). | `--date` |
| `archive` | End-of-month rotation: zip each finished month's dated reports + rotated log backups into `logs/archive/<YYYY-MM>/<scope>.zip` and remove the originals (idempotent; never touches the current month or an active `.log`). | `--month YYYY-MM`, `--dry-run` |
| `dashboard` | Regenerate the static status dashboard → `~/.cherrypick/dashboard.html`, **or** run a localhost live server with `--serve`. | `--serve`, `--host` (def `127.0.0.1`), `--port` (def `8787`), `--no-browser` |

See [reporting-and-dashboard.md](reporting-and-dashboard.md) for how these compose and the report files they produce.

## Module drivers (invoked by scheduled tasks — rarely run by hand)

| Command | What it does |
|---|---|
| `run-earnings-entry` | Run the Earnings paper **entry** pass now (the daily ~15:45 ET task). |
| `run-earnings-exit` | Run the Earnings paper **exit** pass now (the daily ~09:45 ET task). |
| `ensure-dolt` | Start a module's declared Dolt server if down (the earnings keep-alive task). |

MEIC's paper loop is **self-healing** and registers its own task (`cherrypick-meic-paper-loop`); the
orchestrator invokes MEIC's installer during `install` rather than driving each iteration.

## Global flags

`--date YYYY-MM-DD` (report/eod-digest/notify-eod/eod-insight) · `--fast` (doctor) ·
`--module` / `--set` / `--clear` / `--yes` (connect/account) ·
`--serve` / `--host` / `--port` / `--no-browser` (dashboard) · `--apply` (migrate-home) ·
`--month` / `--dry-run` (archive) · `--channel` / `--url` (secrets) · `--force` (init).

## Slash-command equivalents (Claude Code)

Some workflows are also exposed as checked-in slash commands for interactive sessions:
`/install`, `/uninstall`, `/serve-dashboard`, `/meic-start`, `/earnings-start`. These are dev
conveniences, never a runtime dependency.
