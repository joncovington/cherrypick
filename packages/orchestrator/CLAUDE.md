# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

cherrypick is the **orchestrator** for a trading-tool suite. It drives the sibling module
packages (`../meic`, `../earnings`, `../gex`, `../flies`) and the standalone market-data streamer
(`../streamer`, the suite's single producer of the shared stream cache) **in place** — via subprocess,
using paths from config — for unattended **paper**-trading data collection, with a watchdog +
notifications so a walk-away user is told (or at least has it logged) whenever something stalls. It never edits a module's internals and
**never places live trades** — the sole live-adjacent action is *onboarding config* (`connect`/`account`
select a module's live-trading account; see the Invariants below), never order placement. What has
actually shipped is tracked by git log / commit history (`ROADMAP.md` is deprecated — a frozen Stage 0
record, see its own header); the design rationale behind it is [`docs/design.md`](docs/design.md)
(a 2026-07-11 research report, deliberately not updated as work ships); and the suite-wide human
documentation is the root [documentation index](../../docs/README.md) (architecture, CLI, reporting,
configuration, guardrails).

## Commands

```bash
# Fresh clone: install packages/core first, or import cherrypick.core fails everywhere.
# From the repo root: scripts\dev-install.ps1 (or scripts/dev-install.sh) installs it and every package.

# Run the CLI from a source checkout (do NOT create a root cherrypick.py — see Gotchas):
python run.py <cmd>          # or, if pip-installed: `cherrypick <cmd>` / `python -m cherrypick`
python run.py doctor         # green/red readiness (read-only)
python run.py install        # register the ONE anchor task (cherrypick-supervisor), start the supervisor daemon + streamer/services, delete legacy per-job tasks; refuses while flies is live-armed today (--force overrides)
python run.py status         # supervisor job registry + heartbeats (legacy schtasks snapshot on a pre-cutover box)
python run.py watchdog       # one watchdog pass (the supervisor's 10-minute job runs this)
python run.py supervise      # run the supervisor daemon loop foreground (--stop asks a running one to exit); the anchor task keeps it alive via ensure-supervisor
python run.py ensure-supervisor  # the anchor task's probe: restart a dead/stale supervisor; escalate one CRITICAL after 3 failed probes
python run.py streamer-health    # one streamer-liveness pass (the supervisor's 60s in-session job; whole-session successor to the retired cherrypick-preopen task)
python run.py preopen-check  # deprecated alias for streamer-health (honors the legacy preopen enable flag)
python run.py report         # unified cross-module paper P&L (read-only); --eod / --date YYYY-MM-DD scopes to one session; --live reads the live-tagged ledgers (modules' live_db) instead — a separate view that never feeds calibrate/promotion
python run.py archive        # end-of-month rotation: zip each finished month's reports + rotated logs to logs/archive/ (--dry-run / --month YYYY-MM); scheduled monthly as cherrypick-log-archive
python run.py settings       # local config editor + secrets manager, loopback:8804 -- the suite's one mutating surface; --organize [target] [--apply] reorders config(s) into their example's sections instead of serving
python run.py calibrate      # per-profile calibration readings + promotion recommendations
python run.py migrate-home   # dry-run: move config files into ~/.cherrypick + sweep leftovers (--apply to perform)
python run.py uninstall      # remove cherrypick-managed tasks

# Tests (pytest; markers: unit [default lane], live, windows)
python -m pytest                                   # default: `-m "not live" -q` (see pytest.ini)
python -m pytest tests/test_report.py              # one file
python -m pytest tests/test_report.py::test_report_unifies_pnl_net_of_costs_across_modules  # one test

# Lint / format (line-length 110)
ruff check .
ruff format .
```

Config: copy `config.example.json` → `config.json` (gitignored, machine-local). Module paths in it are
resolved **relative to the config file's directory** — never hardcode absolute paths.

## Architecture

**src-layout PEP 420 namespace.** `src/cherrypick/` has no root `__init__.py`, so it composes with the
`cherrypick.core` package (an installed dependency, `packages/core` in this monorepo) under one
`cherrypick.*` import namespace. `run.py` puts `src/` on `sys.path` and delegates to
`cherrypick.cli:main`.

**Two halves, one config.** Everything hangs off `config.json` (`orchestrator/config.py`):
- **Write side (the reliability guarantee):** the **supervisor daemon** (`orchestrator/supervisor.py`,
  kept alive by the one OS anchor task `cherrypick-supervisor` → `ensure-supervisor` probe) derives
  every job from config each pass (`orchestrator/jobspec.py` — pure ET/DST-correct schedule math,
  per-job windows/catchup, unit-tested with fake clocks) and spawns them as the same short-lived
  headless ticks the OS scheduler used to fire, recording per-job state in
  `state/supervisor-jobs.json` + its own heartbeat in `state/supervisor.last.json` (atomic writes).
  `orchestrator/watchdog.py` runs as its 10-minute job, checks each module's paper pipeline (job
  present, data fresh in-session, the standalone streamer producer alive, earnings SLA met) plus the
  supervisor/anchor themselves, logs findings, and pushes alerts through `notify/notifier.py`. It has
  a **dedup / re-notify / recovery state machine** (`_process_notifications` in watchdog.py, state in
  `state/watchdog_state.json`). The supervisor itself is stdlib + local files only — no broker, no
  network, no AI — and every registration check dual-reads (schtasks fallback) until the transition
  window closes.
- **Read side (look whenever you want):** `report.py` (cross-module paper P&L; `run(session=…)` scopes
  to one settlement day for the daily/EOD views), `calibrate.py` (per-profile promotion advisor),
  and the per-schema ledger readers that both of those use, which now live in
  **`cherrypick.core.ledgers`** (one home for the net/cost/capital/session rules across `meic_ic`,
  `fly_book` and `earnings`). The page that composes all of it is the **console**
  (`packages/console`), which this package no longer serves anything of its own alongside. These are
  **read-only and file-only**.

  **End-of-day reporting left this package on 2026-08-13.** `eod_digest.py`, `eod_insight.py` and
  `advise.py` are gone, along with the watchdog's completion-triggered launch of them. The suite's
  EOD answer is now **`packages/review`**, which builds one versioned fact set per session across
  every module and renders from that — a module writing its own prose was a second, unreconciled
  account of the same session, and the digest and its AI synthesis both read those prose files as
  their input. This package's role is reduced to *scheduling* it: two supervisor jobs
  (`review-provisional` 16:30 ET, `review-final` 10:15 the next morning, trading days only) invoke
  `python -m cherrypick.review`. See `cfgmod.review_settings`; on by default, opt out with
  `"review": {"enabled": false}`. `logrotate.py` (`cherrypick archive`) is the maintenance
  counterpart: a monthly `cherrypick-log-archive` task zips each finished month's reports + rotated logs
  into `logs/archive/<YYYY-MM>/<scope>.zip` and removes the originals (idempotent, never touches the
  current month or an active `.log`) — also files-only and off the reliability path.

  **No AI is invoked from this package at all any more.** `eod_insight.py` and `advise.py` were the
  two places it was, and both went with the EOD cutover. The narrative and the recommendations they
  produced are now `packages/review`'s job, generated **outside every suite package** by a scheduled
  agent reading review's fact set — so no package holds an API key or a network dependency, and the
  "no AI on the reliability path" invariant holds by construction rather than by fencing.

  **The advice CONSUMERS are still live and still correct.** `cherrypick.core.advice`, each module's
  `advice_bounds` manifest, and the paper loops' session-start re-validation are untouched. With no
  producer writing `state/advice/<module>-<session>.json`, every loop simply sees absent advice and
  runs baseline — which is exactly the documented degrade, not a new failure mode. Re-pointing a
  producer at those bounds later needs no change on the consumer side.

**Per-schema dispatch.** Each module's paper DB has a different schema, selected by
`paper.trade_schema` in config (`"meic_ic"` → MEIC's `ic_trades`; `"earnings"` → the Earnings module's
`trades`; `"fly_book"` → the Flies module's `fly_positions`, tagged by experiment *arm* rather than risk
profile). The canonical schema set lives in `schemas.py` (`SCHEMAS`), and the coverage invariant is
enforced by `tests/test_schema_registry.py`, not prose: every surface registry (`report.py` readers and
`_OPEN_READERS`, `reconcile.py`, `trade_notifier.py`, `eval_activity.py`) must account for every schema —
with a reader, or an explicit not-applicable declaration (`eval_activity.NOT_APPLICABLE`, e.g. earnings,
whose "did it run" is the entry-SLA check). `calibrate.py` reads through `report.py`'s registry and must
never grow its own. Add a schema by adding it to `schemas.SCHEMAS` and extending each surface; a schema
wired into some surfaces but not others now fails CI instead of vanishing silently. `report.py`'s
`_OPEN_READERS` covers positions carried past the close (overnight capital-at-risk, no realized
P&L) that feeds only the report/digest — it needs no matching
entries in calibrate/reconcile/notifier. Only the multi-day earnings module carries overnight; the
0DTE modules (MEIC, flies) settle within the session and return an empty overnight view by design.

**SLA heartbeat paths derive from the module name** (`config.sla_state_files`), not from a literal
filename. They were hardcoded to `earnings_*.last.json`, which was harmless while Earnings was the only
`cherrypick_scheduled` module and wrong as soon as a second one existed — the read surface showed one
module's SLA under another's name and the watchdog raised a CRITICAL titled for the wrong module. Use
`paper.sla_state_prefix` to override for a module whose heartbeat files are named differently.

**cherrypick-core is an installed dependency.** Shared logic (`cherrypick.core.profiles`, `.fees`,
etc.) lives in `packages/core` in this monorepo and is a normal editable-installed Python package —
`pip install -e packages/core` before this one, or `import cherrypick.core...` fails under `run.py`,
pytest, and the installed console script alike. There is no `sys.path` bootstrap for it anywhere in
this package's source, and none should be reintroduced — `doctor` fails loudly
(`cherrypick.core: not installed`) if the install step was skipped.

## Invariants (do not violate — the reasons are load-bearing)

- **No network / service / AI dependency on the reliability path.** The watchdog → notify path uses only
  the stdlib + the OS shell (no MCP, no HTTP client, no AI tooling), so it has no new failure mode. A
  34-hour silent stall is the reason this rule exists. `follow_notifier.py` (the tastylive Follow Feed
  push) is the one notifier that makes an HTTP call, and that is exactly why it is **its own scheduled
  task and is never called from the watchdog tick** — unlike `trade_notifier`, which is files-only and
  may ride the tick. Any future notifier that touches a network gets the same treatment: its own task,
  every request wrapped, an outage degrading to "no notifications" rather than a failed tick.
  `desk_notifier.py` (`cherrypick notify-desk`, the `desk-notify` job) is the second such notifier and
  gets the same treatment for two reasons rather than one — it pushes a Discord card *and* asks the
  broker for order status. It cards each manual-desk order on submit and again when that order reaches
  a terminal state (filled / cancelled / rejected / expired). Fill detection is **poll-first**: the
  broker's own status is authoritative and an unreachable broker means "ask again next pass", never
  "nothing happened". Critically it **reads the desk's audit journal as a file and never imports
  `cherrypick.desk`** — observing desk orders must not make the submit path reachable from scheduled
  code, which is the desk's own load-bearing invariant. Its first pass seeds from the existing journal
  rather than backfilling a card per historical order (today's orders still join the watch list, since
  an order placed minutes before the switch was flipped is exactly the one whose fill matters).
- **Read surfaces read files, never the broker.** `report`/`calibrate` read paper DBs (SQLite
  read-only), watchdog state, and logs. This package serves **no HTTP read surface at all** since
  2026-08-12 — `dashboard.py`, `serve.py`, `embeds.py` and `sections.py` were deleted and the console
  (`packages/console`) is the one read surface. The watchdog no longer renders anything on its tick,
  which takes that work off the reliability path outright rather than merely keeping it cheap.
  `liveops.py` survives and did NOT go with the card that displayed it: it is the phase-5 gate
  surface — each module's `enable_live_trading` kill switch (home config first, then in-repo), its
  designated live account (masked, via the module keyring), and the suite **halt flag**,
  `state/halt-live.flag` in the cherrypick home, whose *presence* is the signal
  (`liveops.halt_flag_path()` defines the path; live loops poll the same file). It is files + keyring
  only and never writes. `settings_serve.py` imports it, and the watchdog reads it.
  **The live-ops view is the one thing the deletion left with no replacement** — it was
  broker-touching, so it was deliberately never ported to the console, and folding it in means
  revisiting that package's read-only guardrail. `cherrypick reconcile` still answers the
  broker-truth half on demand and as its daily job.
- **Paper ↔ live isolation.** cherrypick only invokes paper engines / paper DBs. Anything advisory
  (e.g. `calibrate`'s promotion recommendations, the drawdown alert) is advisory only — it never mutates
  a module's config or switches live risk. Live P&L is visible only through the explicitly live-tagged
  reader (`report.live_run`, `cherrypick report --live`) over a module's separate `live_db` config key —
  a **separate function by design**, so `calibrate` (which reads `report.run`, paper only) can never see
  a live ledger even by accident; a test asserts calibrate references neither `live_run` nor `live_db`.
  The one place cherrypick reads the *real* broker account is
  `reconcile` (`orchestrator/reconcile.py`, `cherrypick reconcile` + the serve-only `/api/reconcile`
  card): a paper↔live isolation guard that enumerates **every** account on the login (`list_accounts` —
  tastytrade returns multiple per user) and flags any open positions/BP a paper-only suite shouldn't
  have. Like `doctor` it is a broker-touching, on-demand diagnostic — **off the watchdog reliability
  path**, read-only broker calls only (`list_accounts`/`get_positions`/`get_account_info`, never an
  order), account numbers masked, advisory. It never trades or mutates config. For live operation
  (phase 5), `reconcile.schedule.enabled` promotes it to a daily scheduled task (`reconcile
  --scheduled`, its own task off the watchdog tick) that notifies on any non-FLAT verdict.
- **The settings surface (`cherrypick settings`, port 8804) is the second narrow live-config
  exception — and the suite's only mutating HTTP surface.** Every dashboard server in the suite is
  GET-only; `settings_serve.py` adds config-write and secrets-write routes, so it earns its own
  invariant rather than borrowing the read surfaces' "never writes" contract. It is loopback-only like
  every server here, but additionally: every route (GET included) requires a matching `Host` header
  (defeats DNS rebinding), and every POST requires a per-session CSRF token baked into the page plus an
  `application/json` content type (the server sends no CORS headers, so a cross-origin fetch can't
  reach it at all). Config writes (`configedit.py`) go through byte-offset splicing so a field edit
  never disturbs the file's `_note`/`_header` documentation or key order, are backed up
  (`state/config-backups/`) before every write, and refuse any change — in either direction — to a
  guarded live-trading pointer (`configedit.GUARDED`: `enable_live_trading`, flies' `live.enabled` and
  `gate0_confirmed`, and the live loss/deploy limits); those fields render read-only with a pointer to
  their real CLI path, so this surface can never arm or de-risk live trading. This is also the one
  place a bearer secret transits an orchestrator process: a POST body reaches `secretsops.py`, is
  passed straight to `CredentialStore.set_secret` / `notify.secrets.set_webhook`, and is dropped —
  never logged, never written to any file, never echoed in any response. Every GET response contains
  only `secrets_status()` booleans, webhook set/not-set strings, and `mask_account()` output. Like the
  onboarding exception below, it never places an order and is never started by the watchdog or a
  scheduled task — it runs only when a human runs `cherrypick settings` in the foreground, and `--organize` (reorder a config into its example's sections) is
  the only other thing it does outside the server.
- **The onboarding surface (`connect`/`account`) is the one narrow live-config exception.**
  `cherrypick connect --module <m>` and `cherrypick account --module <m>` (`orchestrator/connect.py`,
  `orchestrator/accounts.py`) let a user set up a module for eventual **live** trading: they run the
  module's *own* hidden-input credential tool for the OAuth bearer secrets (delegated — the orchestrator
  never sees/stores `client_secret`/`refresh_token`) and **select which account** the module trades in
  when live, writing that account's `ACCOUNT_NUMBER` into the module's keyring via the shared
  `cherrypick.core.auth.CredentialStore` (service from the module's `keyring_service` config). This is
  the *only* live-trading *configuration* cherrypick performs, and the boundary is strict: it still
  **never** places/cancels/closes/adjusts an order, never flips `enable_live_trading`, never runs a
  module's live engine, and never edits a module's code/config files. Account writes are human-confirmed;
  account numbers are masked everywhere (only the write to keyring uses the full number). `reconcile`
  honors the designation — a designated live account is *expected* to hold positions (not drift).
- **Streamer supervision is its own job, never a faster watchdog.** `streamer-health`
  (`watchdog.run_streamer_health`, the supervisor's 60s job, 09:00–16:00 ET on trading days) exists
  because the streamer's failure window is unrecoverable — a producer dead through 09:30–09:35
  loses the opening range for good, and a restart still needs the 240s settling window — while the
  full tick's 10-minute cadence is right for everything else it does. The invariant survives the
  supervisor cutover with the mechanism changed: the answer to "the streamer needs tighter watching"
  is a tighter cadence on THIS job (a config value now, not a second OS task), never speeding up the
  full tick and multiplying module checks and EOD triggers all day. It **reuses**
  `_check_streamer_health` (never a copy — that function carries the silence-restart lesson), writes
  **no heartbeat** (the full tick owns that; a second writer makes "when did the watchdog last run"
  ambiguous), and stops at the door on a non-trading day. `run_preopen` remains as a deprecated
  delegating alias for the retired `cherrypick-preopen` windowed task until the transition closes.
- **The watchdog's only trading-adjacent action is benign, non-trading remediation** (restart a dead or
  silently-stalled **market-data streamer** — the standalone producer, a top-level `streamer` config
  block, session-gated and restarted on *silence* not just death, since the 34-hour stall was a live-but-
  quiet socket — or a dead managed **service**: top-level `services`, background daemons like the gex
  spot-trail recorder that `install` starts, the watchdog keeps alive via `status_argv`/`start_argv`,
  and `uninstall` stops; single-instance guarded, located by `path`/`repo` like modules but with no
  paper DB or schedule of their own). It never places, cancels, or closes an order. The same
  remediation also covers a service that is **up but running stale config** (`servicecfg.py`): a
  daemon reads config once at launch, so a later edit reaches the file and never the process, and
  liveness cannot see it. `install` stamps a hash of each service's effective config (its own config
  file plus its `services[]` entry) and the watchdog recycles on a moved hash — gated on
  `auto_restart`, stamping only a restart that succeeded, and *adopting* rather than restarting a
  service it has no prior stamp for. The streamer is covered too, but only from its **healthy**
  branch (the stall path always wins) and never inside the `settling` window, so a config recycle can
  never become the restart loop that guard exists to prevent; producers stamp under their finding
  label so two of them can coexist through a cutover. A producer has a **second** staleness axis on
  the same file-versus-process gap: its underlyings come from the union of every module's stream
  request and bind once, when it builds its streamer, so a module that starts needing a new symbol
  writes its request file and the running producer never sees it. The launch stamp records that
  subscription snapshot and later ticks compare — **growth only**, never a hash: a module that stops
  needing a symbol leaves the producer over-subscribed, which is harmless, while treating that shrink
  as staleness would recycle the feed every time a module whose request tracks its open positions
  closed one. The union itself is read through `cherrypick.core.streamrequests`, the same code the
  streamer subscribes from, so the two can never disagree about what was asked for.
- **Every spawned process is headless.** The scheduled tasks run under `pythonw.exe` (no console), so
  any console-subsystem child launched without `CREATE_NO_WINDOW` pops a visible terminal window on the
  user's screen — on every watchdog tick, daemon restart, and desktop toast. Daemons and `services`
  start via the detached no-window launcher (`watchdog._start_streamer`: `pythonw` +
  `DETACHED|NO_WINDOW|NEW_GROUP`), and every other `subprocess.run`/`Popen` site passes
  `creationflags=CREATE_NO_WINDOW` (`orchestrator/util.py`; 0 off-Windows, so call sites stay
  cross-platform). `-WindowStyle Hidden` alone is not enough — the console flashes before PowerShell
  hides it. Enforced by `tests/test_headless.py` (a source scan), whose one exemption is `connect.py`:
  its delegated credential entry is interactive by design and must share the user's console.
- **Account numbers are masked** to the last 4 digits (`****1234`) anywhere they surface in logs or
  output — never emit a full account number (suite-wide rule).
- **Best-effort side calls never break the reliability path.** The watchdog tick fires
  `trade_notifier.run` inside `try/except`; a hiccup must not fail the health check. The tick stays
  stdlib + OS-shell only — preserve that when adding tick-time work. The EOD trigger that used to
  live here was removed with the reports it fired: the review runs as two ordinary supervisor jobs,
  so nothing EOD-shaped touches the watchdog any more.
- **Opt-in AI/dev tooling is local-only and off every runtime path.** `graphify` / `agentmemory` are
  authoring aids; their artifacts (`graphify-out/`, `.claude/`) are gitignored and they are never a
  runtime dependency. The one tracked exception is `.claude/commands/` — checked-in slash commands are
  shared dev conveniences (e.g. `/console`); the rest of `.claude/` (settings.local.json,
  session state, plans) stays local-only. Slash commands are never a runtime dependency either.

## Suite-wide guardrails (inherited from the MEIC & Earnings modules)

cherrypick drives the module packages in place, so it operates under the same guardrails both modules
declare in their own `CLAUDE.md` (MEIC also keeps a full entry-gate catalog in `../meic/GATES.md`).
Honor these here too; several are already stated as Invariants above and are cross-referenced, not
repeated.

- **Instruction files hold no code and no logs.** This `CLAUDE.md` is for build commands, tech-stack
  reference, and project guidelines only — never Python, scripts, code blocks, or scratchpad logic, and
  never a changelog or task tracker (that is what git history is for). Both modules
  mark this `CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE`.
- **Scratch work lives in `.tmp/`.** Temporary scripts/tests go under a gitignored `.tmp/` (or the job
  temp dir) and are deleted when finished — never left in the tree, never written to the repo root.
- **Portable paths, disciplined layout.** Never hardcode absolute paths (`C:\Users\...`), usernames,
  hostnames (except `127.0.0.1`/`localhost`), or drive letters — derive paths from `Path(__file__)`, an
  env var, or config. Never drop working files/tests in the repo root; use `src/`, `tests/`, `docs/`,
  `config/`. (cherrypick resolves module paths relative to `config.json`'s directory.)
- **Credentials in the OS keyring only.** Every secret — broker OAuth tokens in the modules, Slack/
  Discord webhooks here — lives in the OS keyring (Windows Credential Manager/DPAPI, macOS Keychain,
  Linux Secret Service), never in files, env vars, or logs.
- **Account numbers masked to `****1234`** everywhere they surface (see Invariants).
- **Paper ↔ live isolation.** Live-order tools in the modules are gated behind `enable_live_trading:
  true`, and paper mode never calls `execute_trade` (even a dry-run performs a real margin check).
  cherrypick only ever invokes paper engines / paper DBs; anything advisory stays advisory (see
  Invariants). The Earnings module is additionally **defined-risk only** — naked strategies were removed
  because an unmonitored overnight naked short can blow out arbitrarily.
- **No MCP / network / AI on any loop-decision or reliability path** (see Invariants). The modules'
  loops depend only on their local tools + this guidance; a 34-hour silent stall from an external
  streamer dependency (2026-07-01) is why the rule is load-bearing suite-wide.
- **Correlation risk is not currently guarded** in either module — trading correlated underlyings (MEIC:
  e.g. SPX + XSP) or same-sector/same-date earnings names (Earnings) simultaneously can silently
  multiply exposure. Do not configure correlated combinations until a guard exists.
- **Human-voice docs, no AI commit attribution** (see Gotchas below).

## Gotchas

- **The launcher is `run.py`, not `cherrypick.py`.** A root module named `cherrypick.py` would *shadow*
  the `src/cherrypick` namespace package (a regular module outranks a PEP 420 namespace on `sys.path`).
  Scheduled tasks invoke `run.py`; renaming it breaks them until re-registered via `python run.py
  install`.
- **Everything runtime lives under the per-user home, not the repo.** All path resolution now goes
  through `cherrypick.core.home` (the shared resolver): `config.json`, `state/`, and
  `logs/` all resolve under `~/.cherrypick` (relocated wholesale by `$CHERRYPICK_HOME`), so nothing
  runtime lands in a source checkout. `ROOT` is no longer the runtime home — it is only the *source
  anchor* for resolving a relative module `path` in config (e.g. `../meic`), derived from `__file__`.
  `load_config` reads `~/.cherrypick/config.json`, falling back to a legacy in-repo `config.json` until
  an explicit migrate moves it. The notifier computes the same logs home independently (it stays free of
  a config import on the reliability path). Edit `config.example.json` when a config key should be
  documented for other machines.
- **One anchor task; everything else is the supervisor.** Since 2026-08-09 the OS scheduler holds
  exactly one cherrypick entry (`cherrypick-supervisor`, every 2 min → `ensure-supervisor`);
  `orchestrator/tasks.py` (`schtasks` on Windows, tagged crontab lines on POSIX) survives to manage
  that anchor, to delete legacy per-job tasks by name (`legacy_task_names`), and as the dual-read
  fallback on a pre-cutover box. Job cadence/schedule questions are config + `jobspec.py` questions
  now — never a new scheduled task. The cron backend's *execution* on a real POSIX host is still
  unvalidated.
- **Commit messages: no AI / co-author attribution or AI signatures** (a suite-wide rule). Write docs
  and PRs from a human developer's perspective.
