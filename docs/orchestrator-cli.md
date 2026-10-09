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
| `secrets-set` | Store a Slack/Discord webhook URL in the OS keyring (prompted without echo if `--url` omitted); for `telegram`, prompts for the bot token and then the chat ID (both without echo). | `--channel slack\|discord\|telegram`, `--url` |
| `secrets-status` | Show which push-channel secrets are configured (secret-free). | — |
| `secrets-delete` | Remove a stored secret. | `--channel` |
| `capabilities` | What this machine can carry for the two optional dependencies: `claude` (the Claude Code CLI — the advisor and the EOD/morning narratives need it) and `dolt` (the Dolt binary **and** the earnings/options/stocks clones — earnings and technicals need it). With no flag, prints the resolved view: each capability, each module's switch and effective state (with what is missing), and the gated features. `--detect` probes; `--detect --write` records the answers in `config.json`'s `capabilities` block (what the installer runs); `--cap name=true\|false` records one by hand. An absent capability is `false`, and detection never switches a feature on — each still has its own switch. `doctor` compares the record with the machine. | `--detect`, `--write`, `--cap name=true\|false` |
| `config-backup` | A git history of this machine's configuration (opt-in). With no flag, one pass: commit whatever the home repository's allow-list tracks if it changed, then push when `config_backup.push` is on and a remote exists (never prompts; a failed push is reported and retried next pass). `--init` makes `~/.cherrypick` a git repository whose `.gitignore` admits only `config.json` and `config/*.json`, gives it a local git identity if the machine has none, and makes the first commit; `--remote URL` adds the push remote. `--enable`/`--disable` flip `config_backup.enabled` (also on the console's Config page); the supervisor's `config-backup` job then runs a pass every `interval_minutes`. | `--init`, `--remote URL`, `--enable`, `--disable` |
| `settings` | Local web editor for every config file + a keyring secrets manager (loopback `:8804`) — the suite's one mutating HTTP surface, run on demand, never watchdog-started. Live-trading gate fields render read-only. With `--organize` it instead reorders a live config into its example's sections and exits (no server). | `--host`, `--port` (def `8804`), `--no-browser`, `--organize [target]`, `--apply` |

## Turning the suite on/off

| Command | What it does |
|---|---|
| `install` | Register the ONE `cherrypick-supervisor` anchor task, start the supervisor daemon (which derives every job — module paper loops, earnings entry/exit, Dolt keep-alive, watchdog, streamer-health, trade-notify, log-archive, opt-ins — from config each pass), delete every legacy per-job task, and start the streamer / services if down. Refuses while flies is live-armed today (`--force` overrides). The suite review runs as two daily jobs (`review-provisional`, `review-final`). Full verified inventory: [operations.md](operations.md). |
| `uninstall` | Delete the anchor task first, stop the supervisor, remove any legacy per-job tasks, and stop the orchestrator's own background services. Recorded data and config are untouched. |
| `status` | Supervisor liveness + per-job registry (last start/exit, next run) + heartbeats. File reads plus one anchor-task query; falls back to the OS-scheduler snapshot on a pre-cutover box. |
| `supervise` | Run the supervisor daemon loop in the foreground (diagnostic; the anchor task keeps it alive normally). `--stop` asks a running daemon to exit via its stop file. `--restart` asks it to exit with code 3 so it is started again with fresh code: by the Windows service after its restart delay (the service restarts only after a failure, so a clean stop would leave it stopped), otherwise by the anchor's next probe. In service mode the anchor waits 5 minutes for a stopped service to restart itself before starting a supervisor outside it. |
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
| `notify-desk` | Card each manual-desk order on submit and again when it reaches a terminal state (filled / cancelled / rejected / expired). **Off by default**; a network-calling notifier, so it runs on its own task and *never* on the watchdog tick — it pushes a Discord card **and** asks the broker for order status. Reads the desk's audit journal as a file and never imports `cherrypick.desk`, so observing desk orders cannot make the submit path reachable from scheduled code. | — |
| `notify-status` | Post the hourly suite-status digest: one Discord card, live first. A **Live** field (halt flag, each armed module's structures, mark, settle band and worst case — flies from its own book roll-up and live marks — closed live P&L, any live-enabled module left unarmed, and the last broker reconcile's verdict); a **Paper** field of one line per module (entries, closes and net with the move since the last post, open risk), with quiet modules folded into one line; and an **Attention** field only when the watchdog has findings. Loop internals (iterations, mark counts, blocked-attempt tallies) stay on the console. Composes existing artifacts only — it refreshes the provisional review fact set (the same idempotent build the 16:30 job runs) and reads it, the live ledgers through `report --live`'s reader, `live_positions.last.json`, the arm records, `watchdog.last.json` and the morning pack; never the broker. Unmeasured figures render as an em dash, and no suite-wide net, live or paper, is ever printed. **Off by default** (`status_digest.enabled`); a webhook push, so it runs as its own `status-digest` supervisor job (hourly, 10:00–16:10 ET, trading days) and never on the watchdog tick. A daily companion job (`status-digest-close`, 16:35 ET) posts the day's CLOSE card once the 0DTE books have settled. | `--close` (the CLOSE card), `--force` (post on a non-trading day) |
| `live-positions` | Compare each designated live account's broker positions with what the live ledgers say is filled and open (flies, bwb, meic report theirs through `live_loop --expected-legs`, files and DB only). Only the underlyings a live module trades are compared, netted per account. Verdict IDLE (nothing armed or open; the broker is not asked), MATCH, SETTLING (a difference seen once), MISMATCH (the same difference on two checks running; the watchdog raises CRITICAL `live_positions`) or UNKNOWN (WARN). Writes `state/live_positions.last.json`. Read-only at the broker. Runs as the `live-positions` supervisor job every 300 s, 09:31-16:10 ET on trading days (`live_positions.*`). Added 2026-10-08 (safeguard 2(c)). | — |
| `settle-overdue-live` | Settle any past session a live ledger (flies, bwb) still holds open, at **that session's official close** (`core.settlement.dated_index_close`, Yahoo's daily bar, source `yahoo_daily`); never a provisional price. A session with a pending entry, or no official close found, is left open and the watchdog's `<module>.live_overdue` keeps alerting. Each settlement is announced on every channel. Runs each module's `live_loop --settle-overdue`; scheduled hourly as the `settle-overdue-live` job (`live_settle_overdue.*`). Added 2026-10-08 (safeguard item 4). | — |
| `service` | **Optional, Windows only, off by default:** run the supervisor as a Windows service (WinSW v2) so the suite runs with nobody logged on. `service prepare` writes the service definition (`~/.cherrypick/service/`, from config `service.*`; the user's PATH carried, no password in the file) and prints the elevated commands a person runs: WinSW `install /p` (asks for the account password) and `start`, plus re-registering the anchor to run while logged off. `service status` reads `sc query`; `service uninstall` prints the undo. In service mode the anchor starts the service (`sc start`), never a rival supervisor; `install` keeps the anchor as configured; desktop toasts report `skipped` (session 0 has no desktop); `doctor` shows the service. Collector sign-ins still need a person at the machine. | `prepare` \| `status` \| `uninstall` |
| `power-watch` | Notify **every** channel while this machine runs on battery (Windows `GetSystemPowerStatus`, Linux `/sys/class/power_supply`, macOS `pmset -g batt`; `cherrypick.core.power`; a Linux machine with no battery entry is not a laptop and never alerts): at once when it switches, then every `power_watch.repeat_minutes` (15), WARNING turning CRITICAL at `critical_percent` (20) or `critical_minutes` (30) of time left, naming an armed live session; one message when AC returns. A reading it cannot take is never read as plugged in or unplugged. Offline and local; runs as the `power-watch` supervisor job every 60 s, every day (`power_watch.enabled`, on by default). Added after the 2026-10-08 power outage. | — |
| `notify-send` | Post a hand-written message — a correction, a note — to a webhook through the same send and outbound record as every scheduled post. The text is read from a file, so nothing a shell would expand (the `$1` in `$17.55M`) can be lost on the way. `--refers-to` ties it to the message it corrects, which `sent` then shows on both. | `--channel`, `--file`, `--kind correction\|note`, `--refers-to <message id>`, `--image` (repeatable), `--date <session>`, `--dry-run` |
| `sent` | What the suite sent to webhooks, from the outbound record (`data/outbound/YYYY-MM.jsonl`): every QuikOptions post, flies payoff chart, status digest, notification and hand-sent message, with its text, images, message id and session. `--stale` names each post's input files that changed after it went out; `--verify` asks Discord whether each message still exists. Read-only. | `--date <session>`, `--days N` (default 7), `--kind`, `--stale`, `--verify`, `--json` |
| `ps` | Every supervisor job and managed daemon (streamer, services) by name: running PID, whether it is held, its last exit and whether that exit was asked for. Read-only. | — |
| `restart NAME` | Restart a job or daemon by name, never by PID. For a job the command writes a request with its own id and the **supervisor** carries it out: stop the process tree, confirm the PID is gone (by PID *and* creation time, so a reused PID is never mistaken for it), and only then start one. A child that will not die is left running and reported, never doubled. The command waits for the supervisor's answer to *its* request and refuses outright if the supervisor is down. A daemon is held for the duration, stopped and started again. A **daily or monthly job** that is not running is **run once now** (`queued to run now`): an extra run, so its scheduled run still comes, under the usual holds (network, one browser job at a time, start spacing). Neither raises an alert or counts as a failure. | — |
| `stop NAME` / `stop --all` | Hold a job or daemon (`state/holds.json`) and stop it: a held name is never started by the supervisor or restarted by the watchdog, and alerts about it go quiet. A hold older than 12 hours is a watchdog WARN, so a forgotten one is noticed. With the supervisor down, a job's recorded PID is killed only if its creation time still matches. `--all` is the full stop that follows `uninstall`, refused while the supervisor or its anchor task could start things again. `install` clears every hold. | `--all` |
| `start NAME` | Release a hold. A job starts on the supervisor's next pass (the command waits for a resident's PID); a daemon is started directly. | — |
| `restart-console` | Alias for `restart console`, kept for muscle memory. Reports, never kills, a stray listener on the console's port. Never scheduled, never called from the watchdog. | — |

## Reporting & review (the read side)

| Command | What it does | Key flags |
|---|---|---|
| `report` | Unified cross-module paper P&L: totals + per-profile breakdown, **gross and net** of costs. | `--eod` (today ET), `--date YYYY-MM-DD` (one session; default all-time) |
| `calibrate` | Per-profile calibration readings + advisory promotion recommendations (never changes risk settings). | — |
| `review` | Suite end-of-day review (`packages/review`): build one session's cross-module fact set and render it. `--final` marks the session final and re-runs reconciliation; without it the pass is provisional. Read-only over every module's ledger. What the two daily supervisor jobs run. | `--final` |
| `morning` | Build the pre-open morning fact pack (`packages/overview`): index/vol/sector readings from the stream cache, gamma flip and walls from the suite's own GEX history, and the mechanical GREEN/YELLOW/RED phase. What the daily morning-factpack job runs; a pure stream-cache + GEX consumer writing only into its own home, so a bad pass costs a report, never a trade. The session it describes is overview's own contract (today's ET trading day, resolved internally). | — |
| `archive` | End-of-month rotation: zip each finished month's dated reports + rotated log backups into `logs/archive/<YYYY-MM>/<scope>.zip` and remove the originals (idempotent; never touches the current month or an active `.log`). | `--month YYYY-MM`, `--dry-run` |
| `backup` | Nightly backup of the suite's own data -- configs, `state/`, every ledger (copied through SQLite's online backup API and quick_checked) and every artifact under `data/` -- into ONE zip at `backup.dest/cherrypick-backup.zip` (default `~/.cherrypick/backups`), replaced each night. A night with a problem lands as `cherrypick-backup.failed.zip`, never replaces the good backup, and notifies. Skips the Dolt stores, stream caches, logs, the vendor browser profile and backup/archive copies. What the daily `suite-backup` job (01:30 ET) runs; `doctor` warns past 36h. | `--dry-run`, `--list`, `--verify`, `--restore-to DIR` (never into the live home) |

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
| `ensure-dolt` | Start a module's declared Dolt server if down (the earnings keep-alive job); leaves it down while `dolt-server` is held. |
| `dolt-server-status` | The `dolt-server` daemon's status contract (JSON `running`/`pid`): what `status dolt-server` reads. |
| `dolt-server-start` | Start the Dolt server if it is down: what `start`/`restart dolt-server` call (under the Windows service, through the supervisor). |
| `dolt-server-stop` | Stop the Dolt server: ends only a dolt process listening on the Dolt port, never a guess. |
| `dolt-sql` | `dolt-sql <database> --query "..."`: a query through the running Dolt server by address. Use it instead of a bare `dolt sql` in the data folder, which on Windows deletes a service-started server's `sql-server.info`. |

The module paper loops are supervisor jobs too: MEIC as a 60 s `--once` spawn
(`modules.meic.paper.tick_interval_seconds`), flies as the one **resident** child (its own
`--interval 15` mode in-session, supervised for death and silence) plus a 60 s off-session `--once`
job that owns settlement. The paper modules' own scheduled-task helpers were removed on
2026-10-08; flies' and bwb's live `--install-task` only writes the day's arm record, and refuses
without a running supervisor.

## Global flags

`--date YYYY-MM-DD` (report) · `--eod` (report — scope to one
settlement session) · `--live` (report — read the modules' separate live ledgers instead of paper; a
deliberately separate path calibrate can never reach) · `--fast` (doctor) ·
`--module` / `--set` / `--clear` / `--yes` (connect/account) ·
`--host` / `--port` / `--no-browser` (settings) · `--apply` (migrate-home,
settings --organize) · `--organize [target]` (settings) · `--stop` (supervise — ask a running
supervisor to exit) · `--detail` / `--account <last4>` / `--json` (positions) · `--scheduled` (reconcile — the daily job's mode; notifies on any non-FLAT
verdict) · `--month` / `--dry-run` (archive) · `--dry-run` / `--list` / `--verify` / `--restore-to DIR` (backup) · `--channel` / `--url` (secrets) · `--detect` / `--write` / `--cap name=bool` (capabilities) · `--force` (init).

## Slash-command equivalents (Claude Code)

Some workflows are also exposed as checked-in slash commands for interactive sessions:
`/install`, `/uninstall`, `/console`, `/meic-start`, `/earnings-start`. These are dev
conveniences, never a runtime dependency.
