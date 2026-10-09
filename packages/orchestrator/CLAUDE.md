# CLAUDE.md

Guidance for Claude Code when working in the orchestrator package.

## What this is

The **orchestrator** drives the module packages and the standalone streamer (`../streamer`) **in
place**, by subprocess with paths from config, for unattended **paper** data collection, with a
watchdog and notifications so a walk-away user is told (or at least has it logged) whenever something
stalls. It never edits a module's internals and **never places live trades** — its sole live-adjacent
action is onboarding config (`connect`/`account`, below).

What shipped is in git history (`ROADMAP.md` is a frozen Stage 0 record). The design rationale is
[`docs/design.md`](docs/design.md) (a 2026-07-11 research report, not updated as work ships); the
incidents behind the rules below are in [`docs/history.md`](docs/history.md); suite-wide human docs
start at the root [documentation index](../../docs/README.md).

## Commands

```bash
# Fresh clone: scripts\dev-install.ps1 (or .sh) from the repo root, or pip install -e packages/core first.
# From a source checkout use run.py (never create a root cherrypick.py — see Gotchas); if pip-installed, `cherrypick <cmd>`.
python run.py doctor             # green/red readiness (read-only)
python run.py install            # register the ONE anchor task, start supervisor + streamer/services, delete legacy per-job tasks; refuses while flies is live-armed today (--force overrides)
python run.py uninstall          # remove cherrypick-managed tasks
python run.py status             # supervisor job registry + heartbeats (legacy schtasks snapshot on a pre-cutover box)
python run.py watchdog           # one watchdog pass (the supervisor's 10-minute job)
python run.py supervise          # supervisor daemon loop, foreground (--stop asks a running one to exit)
python run.py ensure-supervisor  # the anchor's probe: restart a dead/stale supervisor; one CRITICAL after 3 failed probes
python run.py streamer-health    # one streamer-liveness pass (the 60s in-session job)
python run.py preopen-check      # deprecated alias for streamer-health (honours the legacy preopen flag)
python run.py report             # cross-module paper P&L; --eod / --date YYYY-MM-DD for one session; --live reads the live-tagged ledgers (never feeds calibrate/promotion)
python run.py calibrate          # per-arm calibration readings + promotion recommendations
python run.py positions          # live P/L of the REAL broker account (read-only, on demand); --detail / --account <last4> / --json
python run.py archive            # monthly rotation of finished months' reports + logs to logs/archive/ (--dry-run / --month YYYY-MM)
python run.py backup             # nightly suite backup (the suite-backup job, 01:30 ET): ONE verified zip, rewritten; a bad night never replaces it (--dry-run / --list / --verify / --restore-to DIR)
python run.py settings           # local config editor + secrets manager on loopback:8804; --organize [target] [--apply] reorders configs instead
python run.py migrate-home       # dry-run move of config into ~/.cherrypick (--apply to perform)
python -m pytest                 # default lane `-m "not live" -q`; markers unit, live, windows
python -m pytest tests/test_report.py::test_report_unifies_pnl_net_of_costs_across_modules  # one test
ruff check . ; ruff format .     # line-length 110
```

Config: copy `config.example.json` → `config.json` (machine-local). Module paths resolve **relative to
the config file's directory**. Document a new key in `config.example.json`.

## Architecture

**src-layout PEP 420 namespace.** `src/cherrypick/` has no root `__init__.py`, so it composes with
`cherrypick.core` under one namespace. `run.py` puts `src/` on `sys.path` and calls `cherrypick.cli:main`.
`cherrypick.core` is an installed editable dependency; there is no `sys.path` bootstrap for it and none
may be reintroduced — `doctor` fails loudly (`cherrypick.core: not installed`).

**Write side — the supervisor.** The daemon (`orchestrator/supervisor.py`), kept alive by the one OS
anchor task `cherrypick-supervisor` → `ensure-supervisor`, derives every job from config each pass
(`orchestrator/jobspec.py`: pure ET/DST-correct schedule math, per-job windows/catchup, tested with
fake clocks) and spawns short-lived headless ticks, recording per-job state in
`state/supervisor-jobs.json` and its heartbeat in `state/supervisor.last.json` (atomic writes). It is
stdlib + local files only — no broker, network or AI — and every registration check dual-reads
(schtasks fallback) until the transition window closes.

**A child's stderr goes to `logs/jobs/<job-id>.stderr.log`** (a file, never a pipe: a child that
outlives a supervisor restart keeps writing). On a failed exit the supervisor masks the run's last
lines through `core.redact` and puts them in `supervisor.log` and the registry as `last_error`, which
`status` shows and the churn and live-loop findings quote.

**Stops and restarts go by name, and the supervisor does the killing** (`orchestrator/proc.py`,
`ps`/`restart`/`stop`/`start`). Nobody looks up a PID:

- **Identity is PID plus creation time** (`pid_started_at` in the registry, `supervisor.same_process`).
  A recorded PID that is alive but younger than the record is a different process — never adopted,
  never killed.
- **Restart**: the command writes `state/restart-requested.<job>.json` with its own id; the supervisor
  stops the tree, waits until the child is confirmed gone, and only then launches. A child that will
  not exit is left running and reported (`last_restart.result`) — never a second copy beside it. The
  exit is logged as requested: no failure, no backoff, no churn count, and `last_exit_requested` rides
  `supersnap.job_run_info` so the live-loop check does not read its code as a failed tick. A request
  older than 10 minutes is ignored, so it cannot excuse a later crash.
- **Holds** (`orchestrator/holds.py`, `state/holds.json`): a held name is stopped and never started,
  by the supervisor or the watchdog's auto-restart, and its findings go OK. Two exceptions, on
  purpose: a held LIVE loop is still a WARN in session, and any hold over 12 hours is a WARN.
  `install` clears every hold.
- **Duplicates**: the watchdog reads the OS process list and reports two instances of one job's
  command line (WARN; CRITICAL for a `--live` argv). A launcher and its child are one instance.

Do not add a path that kills a supervised child by PID from outside the supervisor; it is the one
process that knows whether the old one is gone.

The registry is a picture of what the supervisor is **currently** driving, which gives three states to
watch:

- **Retired jobs are pruned** (`_prune_retired`), because a frozen `enabled: true` row is
  indistinguishable from a job that silently stopped firing. Two rows are **kept**: one whose child is
  still alive (or the overlap guard loses it), and one whose derivation *failed* this pass (dropping it
  would erase the evidence).
- **Jobs added to `jobspec` do not exist until the daemon restarts** (it imports `jobspec` once) — no
  row at all, so everything reads healthy. `supersnap.jobs_missing_from_registry` detects it (returns
  None for "cannot tell", never an empty list); watchdog and doctor render it as WARN, remedy a
  human restart.
- **A job whose derivation failed** is invisible to both checks above. `supersnap.jobs_failing_derivation`
  reads `derive_errors` FROM THE REGISTRY rather than re-deriving, so it reports what the running
  daemon actually hit.

**The watchdog** (`orchestrator/watchdog.py`, a 10-minute job) checks each module's paper pipeline (job
present, data fresh in-session, streamer alive, earnings SLA met), the supervisor/anchor, and the
console's resident-job state (its only signal, since the console writes no trade data), logs findings
and pushes alerts through `notify/notifier.py`, with a dedup / re-notify / recovery state machine
(`_process_notifications`, state in `state/watchdog_state.json`). A finding that lists incidents
(`members`: `jobs.failed`, `jobs.missed`, one id per job and stamp) is re-posted only when a new
incident joins, never on the `renotify_minutes` clock, and posts one all-clear when the list empties.

**Read side.** `report.py` (`run(session=…)` scopes to one settlement day) and `calibrate.py`, over the
ledger readers in **`cherrypick.core.ledgers`**. Read-only and file-only. The console is the one page
that composes it; this package serves nothing of its own. `logrotate.py` (`archive`, the monthly
`cherrypick-log-archive` task) zips finished months into `logs/archive/<YYYY-MM>/<scope>.zip`,
idempotent, never touching the current month or an active `.log`, off the reliability path.
`backup.py` (the `suite-backup` job) keeps ONE zip of the suite's own data, rewritten nightly: live
ledgers copy through SQLite's online backup API and are quick_checked, a night with a problem lands as
`.failed.zip` and never replaces the good one, `restore` refuses the live home, and `doctor` warns past
36h. Only `.dolt/` is skipped, never its parent: `data/earnings` holds ledgers beside one.

**This package holds no AI and no review or advisor logic; it only schedules them.**
- The end-of-day review is `packages/review`: jobs `review-provisional` (16:30 ET) and `review-final`
  (10:15 next morning), trading days only, invoke `python -m cherrypick.review`
  (`cfgmod.review_settings`; on by default, opt out with `"review": {"enabled": false}`).
- The advisor: trading-day jobs tagged `ai` invoke `scripts/advisor_checkpoint.py`
  (`cfgmod.advisor_settings`; OFF by default; module names travel on argv so no model id appears in
  code). The schedule is **`advisor-deep` at 17:00 only**; `checkpoints` accepts an empty list or
  dict, deriving no light jobs.
- Both scripts are jobs like any other — spawned, never imported, never on the watchdog tick.
- `_check_advice_enactment` runs `python -m cherrypick.advisor enactment` **as a subprocess** between
  10:30 and 16:30 on trading days and reports only `not_enacted`; `no_artifact` is the ordinary state
  and stays silent.
- The advice consumers (`cherrypick.core.advice`, each module's bounds, the loops' session-start
  re-validation) are untouched by any of this; absent advice means baseline.

**Per-schema dispatch.** Each paper DB's schema is selected by `paper.trade_schema` (`meic_ic`,
`earnings`, `fly_book`, …). The canonical set is `schemas.SCHEMAS`, and `tests/test_schema_registry.py`
enforces that every surface registry (`report.py` readers and `_OPEN_READERS`, `reconcile.py`,
`trade_notifier.py`, `eval_activity.py`) accounts for every schema — a reader or an explicit
not-applicable declaration (`eval_activity.NOT_APPLICABLE`). Add a schema to `SCHEMAS` and extend each
surface. `calibrate.py` reads through `report.py`'s registry and must never grow its own.
`_OPEN_READERS` covers positions carried past the close (only the report uses them; 0DTE modules
return an empty overnight view by design).

**An eval-activity liveness signal must stop when the LOOP stops, not when ENTERING stops.** Feed
ledgers go quiet once a module finishes entering, so a reader keyed to them reports "stopped" daily and
cannot tell a dead loop apart. Fold in `<module>_loop_iterations` through `_loop_ticks` /
`_with_loop_liveness` (never a fourth ad-hoc solution); it degrades to the feed ledger on a checkout
that predates the table.

**SLA heartbeat paths derive from the module name** (`config.sla_state_files`), never a literal
filename; override with `paper.sla_state_prefix`.

## Invariants (do not violate — the reasons are load-bearing)

- **The reliability path is deterministic and local.** The watchdog → notify path uses only the stdlib
  and the OS shell — no MCP, HTTP client or AI — so the thing that watches for a stall cannot stall the
  same way. Any network-touching notifier gets its own scheduled job, never a call from the watchdog
  tick, every request wrapped, an outage degrading to "no notifications". Best-effort side calls on
  the tick (e.g. `trade_notifier.run`) sit inside `try/except`. Nothing EOD-shaped touches the tick.
- **`desk_notifier.py`** (`notify-desk`, the `desk-notify` job) cards each manual-desk order on submit
  and at a terminal state. Fill detection is **poll-first**: the broker's status is authoritative and
  an unreachable broker means "ask again", never "nothing happened". It **reads the desk's audit
  journal as a file and never imports `cherrypick.desk`**, so the submit path is never reachable from
  scheduled code. Its first pass seeds from the journal rather than backfilling cards (today's orders
  still join the watch list).
- **Read surfaces read files, never the broker**, and this package serves **no HTTP read surface**
  (the console is the one). `liveops.py` is the live gate surface: each module's `enable_live_trading`
  (home config first, then in-repo), its designated live account (masked, via keyring), and the suite
  **halt flag**, `state/halt-live.flag`, whose *presence* is the signal (path defined by
  `cherrypick.core.home.halt_flag_path()`, which the live loops poll; `liveops.halt_flag_path()`
  mirrors it). It writes **exactly one thing: the halt flag**, via `set_halt` (create to halt, delete
  to clear), so a stop is reachable from a surface a human is looking at; the console's halt toggle
  routes through it. It never writes `enable_live_trading`, a module's config or code, or an order.
  **The halt stops new live risk only** (2026-10-08): every live loop refuses new entries (and bwb
  add-ons) while it is up, and still confirms fills, manages working orders, stops and closes, and
  settles. Before that date it stopped the whole tick, so a halt left open positions unmanaged.
  The broker-touching live-ops view was deliberately never ported to the console; `reconcile` answers
  that half.
- **Paper ↔ live isolation.** Only paper engines and paper DBs are invoked; anything advisory
  (`calibrate`, the drawdown alert) never mutates a config or switches live risk. Live P&L is only
  through the separate `report.live_run` (`report --live`) over a module's `live_db`; a test asserts
  `calibrate` references neither `live_run` nor `live_db`.
- **The real broker account is read in exactly two places, both on demand and off the watchdog path,
  with read-only calls and masked accounts.** `reconcile` enumerates **every** account on the login
  (`list_accounts`) and flags any positions/BP a paper-only suite shouldn't have (a designated live
  account is *expected* to hold positions); `reconcile.schedule.enabled` makes it a daily job
  (`reconcile --scheduled`) that notifies on any non-FLAT verdict. `positions.py` is the second, and
  the last to be added without an equally explicit reason: **never scheduled**, reuses `reconcile`'s
  account enumeration, writes nothing (the stream cache included), marks from the stream cache first
  and the feed only for undeclared symbols. It must never regress on two things: a **stale** cached
  quote is withheld, not priced, and a leg **neither source can price** is reported and excluded,
  never counted as zero.
- **The settings surface (`settings`, port 8804) is the suite's only mutating HTTP surface.**
  Loopback-only; every route checks the `Host` header (DNS rebinding); every POST needs a per-session
  CSRF token and `application/json` (no CORS headers are sent). Config writes (`configedit.py`) splice
  by byte offset so `_note`/`_header` and key order survive, are backed up to `state/config-backups/`
  first, and **refuse any change, in either direction, to a guarded live-trading pointer**.
  **`configedit.GUARDED` is the list** — do not re-enumerate it here or in a module file.
  `tests/test_guarded_live_pointers.py` DISCOVERS every module's `config.example.json` and fails if a
  declared live gate is not refused. Guarded fields render read-only with a pointer to their CLI path,
  so this surface can never arm or de-risk live trading. A secret in a POST body goes straight through
  `secretsops.py` to `CredentialStore.set_secret` / `notify.secrets.set_webhook` and is dropped —
  never logged, written or echoed; GET responses carry only status booleans and masked accounts. It
  places no order and runs only when a human starts it in the foreground (`--organize` is its only
  other job). `packages/desk` is outside all of this.
- **`configcli.py`** is the same two modules for non-Python callers (the console): one JSON request on
  stdin, one response on stdout. Dispatch only — every guarantee above is inherited, `secretsops` is
  deliberately NOT wired in, and a refusal is data (`ok: false` plus a `code`) on exit 0 so a caller
  can tell "the config said no" from "the bridge is broken". Keep new ops thin.
- **Onboarding (`connect`/`account`) is the one live-config action.** `orchestrator/connect.py` and
  `accounts.py` run the module's *own* hidden-input credential tool (the orchestrator never sees
  `client_secret`/`refresh_token`) and **select the live account**, writing `ACCOUNT_NUMBER` to the
  module's keyring via `cherrypick.core.auth.CredentialStore` (service from `keyring_service`). It
  never places/cancels/closes/adjusts an order, flips `enable_live_trading`, runs a live engine, or
  edits a module's code or config. Account writes are human-confirmed; only the keyring write sees the
  full number.
- **A resident job's liveness is PUBLISHED by the job, never inferred from its log.** `silence_file`
  is a heartbeat the job writes each tick (`cfgmod.resident_heartbeat_path` → `state/<name>.heartbeat`,
  convention owned by `cherrypick.core.home.heartbeat_path`). **Never point a `silence_file` at a log**
  (a quiet calendars loop was restarted 107 times in one session). A job with no heartbeat degrades
  **safely**: `_resident_silent` returns False, it is not silence-supervised, and the watchdog reports
  the gap — restarting on "I can't tell" is the failure being fixed.
- **A WINDOWED resident that exits 0 is believed, not restarted.** `module_stopped` marks it idle until
  its window reopens (cleared in the `not want` branch). Scoped three ways: **windowed only** (the
  console has no window, so its clean exit still takes the backoff ladder); **settled only** (an exit
  0 immediately after start is `_EXIT_TOO_SOON` and takes the ladder); **a dead adopted orphan is a
  failure** (`_EXIT_UNKNOWN`). A module that exits 0 wrongly now stays down for its window, so
  `watchdog._check_resident_health` reporting it is not optional.
- **Ambiguity is reported, never remediated — restart is the most expensive remedy.** An unjudgeable
  child is left running. `watchdog._check_resident_health` (mirrored in `doctor`) reports restart
  **churn** (`starts_in_window`, because a clean-exit storm resets `consecutive_failures`), a module
  that **stopped itself** mid-window, and a resident **publishing no heartbeat** — states no
  paper-freshness check can see, since a restart loop keeps the DB fresh.
- **Port reclaim.** A resident job declaring a `port` (today only `console`, from
  `cfgmod.console_serve_port()`) may, after `_PORT_RECLAIM_AFTER_FAILURES` (8) consecutive failed
  spawns, ask the OS who holds the port and, if that PID is alive and not one the supervisor knows
  (`_known_pids`: live handles plus every `running_pid`), kill its tree and reset the ladder. Gated on
  the failure count so it never fires on a normal restart race or a brief dev server. Opt out with
  `console.reclaim_stuck_port: false`.
- **Streamer supervision is its own job, never a faster watchdog.** `streamer-health`
  (`watchdog.run_streamer_health`, 60s, 09:00–16:00 ET trading days) exists because a producer dead
  at the open loses the opening range for good (and a restart still needs a 240s settle). Tighter watching means a tighter cadence on THIS job,
  never a faster full tick. It **reuses** `_check_streamer_health` (never a copy), writes **no
  heartbeat** (the full tick owns that), and stops at the door on a non-trading day. `run_preopen`
  stays a deprecated alias until the transition closes.
- **The watchdog's only trading-adjacent action is benign remediation**, gated on `auto_restart`; it
  never places, cancels or closes an order:
  - restart the **streamer** (top-level `streamer` block, session-gated) on death, on *silence*, and
    on a base window **serving a chain dated before the session** (`stale_chains`) — fresh ages can
    hide an expired chain;
  - restart a dead managed **service** (top-level `services`: daemons `install` starts and `uninstall`
    stops, via `status_argv`/`start_argv`, single-instance guarded, located by `path`/`repo`);
  - recycle (stop, then start — a plain start loses to the wedged pid's lock) a service that says
    `stalled: true` in its status payload. A service publishing no stall signal is left alone:
    running means healthy;
  - recycle on **stale config** (`servicecfg.py`): `install` stamps a hash of each service's effective
    config (its config file plus its `services[]` entry); a moved hash recycles, stamping only a
    successful restart, *adopting* a service with no prior stamp. For the streamer, only from its
    healthy branch and never inside `settling`; producers stamp under their finding label;
  - recycle a producer whose **subscription grew** (the union of stream requests, read through
    `cherrypick.core.streamrequests` like the streamer itself): **growth only**, never a hash, since a
    shrink is harmless. A new symbol recycles on sight; a **hint-only widening waits out
    `servicecfg.HINT_RECYCLE_COOLDOWN_S`** from the last launch (a decaying hint once restarted the
    producer every five minutes for two hours), and the reason says it is holding off.
- **Every spawned process is headless.** Daemons and services start through the detached no-window
  launcher (`watchdog._start_streamer`: `pythonw` + `DETACHED|NO_WINDOW|NEW_GROUP`); every other
  `subprocess.run`/`Popen` passes `creationflags=CREATE_NO_WINDOW` (`orchestrator/util.py`, 0
  off-Windows). `-WindowStyle Hidden` alone is not enough. Enforced by `tests/test_headless.py`; its one
  exemption is `connect.py`, whose credential entry is interactive by design.
- **Opt-in AI/dev tooling is local-only and off every runtime path.** `graphify`/`agentmemory`
  artifacts (`graphify-out/`, `.claude/`) are gitignored; the one tracked exception is
  `.claude/commands/`. Slash commands are never a runtime dependency.

## Guardrails beyond the root file

- **Scratch work lives in a gitignored `.tmp/`** (or the job temp dir) and is deleted when finished.
  Layout: `src/`, `tests/`, `docs/`, `config/` — nothing in the repo root.
- **Credentials in the OS keyring only** — broker tokens in the modules, Slack/Discord webhooks here;
  never files, env vars or logs.
- **Paper mode never calls `execute_trade`** (even a dry-run performs a real margin check); module
  live-order tools are gated behind `enable_live_trading: true`. Earnings is **defined-risk only**.
- **Correlation risk is only PARTLY guarded.** `tests/test_symbol_correlation_lint.py` refuses two
  vehicles on the SAME index (SPX + XSP), reading `state/stream_requests/`. Cross-index correlation
  (~0.9 SPY/QQQ; pmcc's leveraged ETFs) and earnings' same-sector names are reported, not failed —
  the latter stay a hand-kept `correlation_block_list`. Do not read the lint as covering more.
- MEIC's full entry-gate catalogue is `../meic/GATES.md`.

## Gotchas

- **The launcher is `run.py`, not `cherrypick.py`** — a root `cherrypick.py` would shadow the
  namespace package. Scheduled tasks invoke `run.py`; renaming it breaks them until `install` re-runs.
- **Everything runtime lives under `~/.cherrypick`** (relocatable with `$CHERRYPICK_HOME`), resolved
  by `cherrypick.core.home`: `config.json`, `state/`, `logs/`. `ROOT` is only the source anchor for
  relative module paths. `load_config` falls back to a legacy in-repo `config.json` until migrated. The
  notifier computes the logs home independently, staying free of a config import.
- **One anchor task; everything else is the supervisor.** The OS scheduler holds exactly one entry
  (`cherrypick-supervisor`, every 2 min → `ensure-supervisor`). `orchestrator/tasks.py` (`schtasks`,
  tagged crontab on POSIX) manages that anchor, deletes legacy tasks (`legacy_task_names`), and is the
  dual-read fallback. Cadence questions are config + `jobspec.py` questions — never a new scheduled
  task. The cron backend's execution on a real POSIX host is still unvalidated.
