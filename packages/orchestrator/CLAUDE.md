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
python run.py positions      # live P/L by underlying for the REAL broker account (read-only, on-demand); marks from the stream cache first, the feed only for the rest; --detail / --account <last4> / --json
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
  That registry is a picture of what the supervisor is **currently** driving, so `_prune_retired`
  drops rows for jobs config no longer derives: a retired job's row used to sit at `enabled: true`
  forever, frozen at its last fire and never marked missed (it is no longer evaluated), which is
  indistinguishable from a scheduled job that has silently stopped firing and cost a real diagnosis
  to tell apart after the 2026-08-12 earnings lifecycle cutover. Two rows are deliberately **kept**:
  one whose child is still alive (or the overlap guard loses a process it would otherwise reap), and
  one whose derivation *failed* this pass — that job is missing because something is broken, not
  because it was retired, and dropping its history would erase the evidence.
  **The inverse fault is invisible from that registry, and needs its own check.** The supervisor
  imports `jobspec` once, at startup, so a job ADDED to that module does not exist until the daemon
  restarts — and because the registry describes what it is currently driving, the new job is not a
  row that looks wrong, it is no row at all. `status`, `doctor` and the watchdog all read healthy,
  since they enumerate that registry. On 2026-08-25 `earnings-dolt-pull` and `futures-contracts` had
  both sat undelivered for a day; the first exists to stop the earnings calendar ageing out, which
  had already cost eleven sessions of paper trading, and it had never once run.
  `supersnap.jobs_missing_from_registry` is the one derivation (returning None for "cannot tell"
  rather than an empty list, so a caller cannot read that as "nothing missing"); the watchdog and
  doctor each render it. WARN, not CRITICAL — nothing is broken at that moment, and the remedy is a
  human restarting the daemon.
  **A job whose DERIVATION failed is a third state, invisible from both directions.** It is omitted
  from the derived table, so the drift check never sees it missing, and `_prune_retired`
  deliberately keeps its registry row because it is absent through breakage rather than retirement —
  so both checks read healthy. The supervisor has always written these (`derive_errors`) and nothing
  read them: on 2026-08-20 `advisor-open` failed with a ValueError and the only trace anywhere was
  one line in `supervisor.log`. `supersnap.jobs_failing_derivation` reads them FROM THE REGISTRY
  rather than re-deriving, so it reports what the running daemon actually hit — a fresh derivation
  would use current code and could succeed while the daemon carries on failing.
  `orchestrator/watchdog.py` runs as its 10-minute job, checks each module's paper pipeline (job
  present, data fresh in-session, the standalone streamer producer alive, earnings SLA met), the
  supervisor/anchor themselves, and the console's resident-job state (added 2026-08-14: unlike a
  module it writes no trade data whose staleness would out a stuck restart loop by proxy, so this is
  the only signal that job kind gets), logs findings, and pushes alerts through `notify/notifier.py`.
  It has a **dedup / re-notify / recovery state machine** (`_process_notifications` in watchdog.py, state in
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
  "no AI on the reliability path" invariant holds by construction rather than by fencing. The same
  is true of the advisor added 2026-08-14: this package schedules `scripts/advisor_checkpoint.py`
  and nothing more. Both scripts are jobs like any other — spawned, never imported, never on the
  watchdog's health tick, which is what the retired `eod-insight` trigger was.

  **The advice CONSUMERS are still live and still correct.** `cherrypick.core.advice`, each module's
  `advice_bounds` manifest, and the paper loops' session-start re-validation are untouched. With no
  producer writing `state/advice/<module>-<session>.json`, every loop simply sees absent advice and
  runs baseline — which is exactly the documented degrade, not a new failure mode.

  **A producer exists again since 2026-08-14, and it is not this package.** `packages/advisor`
  writes those artifacts, through the same `cherrypick.core.advice` contract — which is why the
  claim above about re-pointing a producer needing no consumer change was testable and turned out
  true: MEIC's consumer took zero edits. This package's role is *scheduling*, exactly as with the
  review: supervisor jobs (trading days only, tagged `ai`) invoke `scripts/advisor_checkpoint.py`.
  See `cfgmod.advisor_settings`; OFF by default, and the module names travel on argv so no model id
  appears in code.

  **The schedule is now `advisor-deep` at 17:00 and nothing else (2026-08-26).** It began as eight
  jobs — seven light intraday checkpoints plus the deep post-close run — was cut to one light slot
  on 08-21, and is now deep-only. The evidence is the advisor's own record: across 36 light
  checkpoints the light slots produced **4 proposals, all `creative`** (the kind no code path acts
  on) and **one** critical flag, which that same evening's deep slot re-derived more precisely from
  the settled numbers. The `midday` slot specifically produced **zero proposals in its entire
  history**. Light slots also cannot issue anything — the only loop-facing output is the artifact
  `enact` writes in the deep slot — and they were contributing ~43KB (about 10%) of the deep pack
  the model then had to read. `checkpoints` accepts an empty list or dict, which derives no light
  jobs and leaves `advisor-deep` untouched.

  **What the light slots were nominally for is now a watchdog check.** `advice_enacted` rides every
  pack partly so a dropped artifact shows at 10am rather than in the evening verdict — but that
  question is deterministic and no light checkpoint ever caught one. `_check_advice_enactment` runs
  it between 10:30 and 16:30 on trading days by invoking `python -m cherrypick.advisor enactment`
  **as a subprocess**, per this package's standing rule that it drives the advisor by subprocess and
  never by import. It reports only `not_enacted` (an artifact that existed, validated, and was
  ignored); `no_artifact` is the ordinary state of a module with no active experiment and stays
  silent. Verified against the real 2026-08-25 incident, where it names all three modules at 11:00. **This package holds no advisor logic** — not the fact packs, not the validation,
  not the experiment lifecycle. It starts a script and reads the exit code, the same relationship it
  has with the narrative.

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

- **The reliability path is deterministic and local.** The watchdog → notify path uses only the
  stdlib + the OS shell — no MCP, no HTTP client, no AI tooling — so it has no failure mode beyond
  its own. A 34-hour silent stall is why this is worth protecting: the thing that watches for a
  stall must not be able to stall the same way. Any notifier that touches a network gets the same
  treatment: its own scheduled job, never a call from the watchdog tick, every request wrapped, an
  outage degrading to "no notifications" rather than a failed tick. (The two third-party feed
  notifiers that established this rule — the tastylive Follow Feed and Lossdog pushes — moved
  wholesale to the standalone `follow-feed-notifier` repo on 2026-08-21, scheduled by the OS Task
  Scheduler and entirely outside this suite; the rule outlives them here.)
  `desk_notifier.py` (`cherrypick notify-desk`, the `desk-notify` job) is the network-calling
  notifier this package still owns, and it earns the treatment for two reasons rather than one —
  it pushes a Discord card *and* asks the broker for order status. It cards each manual-desk order on submit and again when that order reaches
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
  (`liveops.halt_flag_path()` defines the path; live loops poll the same file). It is files +
  keyring only, and it writes **exactly one thing: the halt flag**, via `set_halt` — create to
  halt, delete to clear. That is the one write by design, because a stop must be reachable from a
  surface a human is already looking at; the console's Config page routes its halt toggle through
  this function rather than touching the file itself. Everything else here is read-only: it never
  writes `enable_live_trading`, a module's config or code, or an order. (This paragraph read "never
  writes" until 2026-08-20, which was wrong from the moment the console's toggle landed — worth
  knowing that the sentence a reader most wants to trust here was the inaccurate one.)
  `settings_serve.py` imports it, and the watchdog reads it.
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
  **`positions.py` (`cherrypick positions`) is the second such read, and the last one that should be
  added without a reason this explicit.** It answers what no paper dashboard can — what the real account
  holds right now and what it is worth — and it takes the same posture as `reconcile` rather than a
  looser one: read-only broker calls (`get_positions`/`get_account_info`/`get_quotes`, never an order),
  account numbers masked, off the watchdog path, **never scheduled**, and it reuses `reconcile`'s own
  account enumeration instead of growing a parallel one. It writes nothing at all, the shared stream
  cache included: marks come from that cache first (the suite-wide streamer-before-API rule) and from
  the feed only for symbols no module declared, since seeding those undeclared symbols into the cache
  would leave rows no daemon refreshes. Two failure modes it must never regress on, because a wrong P/L
  is worse than no P/L: a **stale** cached quote is withheld rather than priced (four SPY legs sat in
  that cache 21 hours old on 2026-08-18 — an old row does not announce itself, it just reads as live),
  and a leg **neither source can price** is reported and excluded from the totals, never silently
  counted as zero.
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
  guarded live-trading pointer. **`configedit.GUARDED` is the list** — it covers all five modules
  that have a live gate (meic and earnings' `enable_live_trading`, flies/calendars/pmcc's
  `live.enabled`, plus flies' `gate0_confirmed` and the meic/flies loss and deploy limits), and
  `tests/test_guarded_live_pointers.py` fails if a module ever declares a live gate the table does
  not refuse. Do not re-enumerate it here or in a module's own file: this paragraph listed three of
  the five until 2026-08-20, which is how a prose promise decays into a partial one. Those fields
  render read-only with a pointer to their real CLI path, so this surface can never arm or de-risk
  live trading. (`packages/desk` is deliberately outside all of this — it is the discretionary live
  path, authorized on its own config and PIN, and never through this surface.) This is also the one
  place a bearer secret transits an orchestrator process: a POST body reaches `secretsops.py`, is
  passed straight to `CredentialStore.set_secret` / `notify.secrets.set_webhook`, and is dropped —
  never logged, never written to any file, never echoed in any response. Every GET response contains
  only `secrets_status()` booleans, webhook set/not-set strings, and `mask_account()` output. Like the
  onboarding exception below, it never places an order and is never started by the watchdog or a
  scheduled task — it runs only when a human runs `cherrypick settings` in the foreground, and `--organize` (reorder a config into its example's sections) is
  the only other thing it does outside the server.
  **`configcli.py` is a second front-end to the same two modules, for callers that aren't Python** —
  one JSON request on stdin, one JSON response on stdout, no HTTP and no server. It exists because
  the console (Node) needs a config surface and the alternative was a TypeScript reimplementation of
  `configedit`, i.e. a second copy of the guard table and the splicing rules, free to drift from this
  one. It is dispatch only: every guarantee above (GUARDED in both directions, backups, atomic
  writes, validation, mtime concurrency) is inherited rather than restated, `secretsops` is
  deliberately NOT wired in so no secret can transit it, and a refusal is returned as data
  (`ok: false` plus a machine-readable `code`) on exit 0, so a caller can tell "the config said no"
  from "the bridge is broken". Keep any new op thin for the same reason: logic added there is logic
  living outside the module that owns it.
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
- **A resident job's liveness is PUBLISHED by the job, never inferred from its log.** Every resident
  job's `silence_file` is a heartbeat the job itself writes at the top of each tick
  (`cfgmod.resident_heartbeat_path` → `state/<name>.heartbeat`, the filename convention owned by
  `cherrypick.core.home.heartbeat_path` so the writer and this watcher cannot drift). It used to be
  the module's LOG, on an assumption `jobspec` stated as fact and nothing enforced — *"every
  in-session iteration writes at least one log line per symbol"*. Flies and MEIC happened to satisfy
  it; calendars, whose lines are all event-driven, did not, and a week holding no position was killed
  and restarted every two minutes for four days (107 times on 2026-08-17), losing up to 61% of a
  session's ticks. A log is a side effect of having something to say, so supervising on it makes log
  verbosity a reliability dependency and makes a quiet healthy loop indistinguishable from a wedged
  one. **Never point a `silence_file` at a log again.** A module that publishes no heartbeat degrades
  **safely** rather than loudly — `_resident_silent` returns False for a missing file, so it is
  simply not silence-supervised — because restarting on "I can't tell" is the failure being fixed,
  and refusing to derive the job would take a trading loop down over telemetry. The watchdog reports
  the gap instead; a diagnosis belongs there, not in a kill.
- **A WINDOWED resident that exits 0 is believed, not restarted.** For a session-scoped loop, exit 0
  is a statement — "my own gate closed", or "another instance holds my lock" — and both times
  respawning is wrong. Reading it as "the run finished, go again" caused the 2026-08-17 16:00 storm:
  the module's gate closes on the dot while `in_window` still says 16:00 (whole minutes, inclusive),
  the child exited 0, `code == 0` erased the only throttle there is, and the ~1s loop respawned
  `calendars-paper` 53 times and `flies-paper` 53 times inside that minute with no backoff line
  between them. `module_stopped` now marks the job idle until its window reopens (cleared in the
  `not want` branch, the one place guaranteed to run between two windows). Three scoping rules, each
  load-bearing: **windowed only** — the console declares no window on purpose, so a clean exit there
  is never expected and still takes the ladder, or the suite's only read surface would go down and
  stay down; **settled only** — a child exiting 0 the instant it starts is a misconfiguration, not a
  session end, and takes the ladder (`_EXIT_TOO_SOON`) rather than stopping the job for its whole
  window on the first tick; **a dead adopted orphan counts as a failure** (`_EXIT_UNKNOWN`) rather
  than recording nothing, which was a second ungated respawn path by the same shape. The trade this
  makes deliberately: a module that exits 0 *wrongly* now stays down for its window, so
  `watchdog._check_resident_health` reporting that is not optional — a loud silence is the point,
  and without it this is just a quieter bug.
- **Ambiguity is reported, never remediated — and restart is the most expensive remedy there is.**
  A streamer recycle reloads every chain and costs a 240s settle; a module restart drops in-flight
  ticks. So the bar for restarting scales with what the restart costs, and everything below it pages
  a human instead. `e4f427e` applied this to the producer (a hint-only widening waits out a cooldown,
  a missing symbol does not); the resident jobs now follow it too — an unjudgeable child is left
  running rather than killed on suspicion. What makes that safe is `watchdog._check_resident_health`
  (mirrored in `doctor`), which reports the three states nothing else can see: restart **churn**
  (`starts_in_window` — a new counter, because `consecutive_failures` is reset by a clean exit and a
  clean exit is the storm's own signature: 161 spawns beside 0 failures), a module that **stopped
  itself** mid-window, and a resident **publishing no heartbeat**. None of these have a
  paper-freshness backstop — a restart loop's own writes keep the DB looking fresh, which is exactly
  how 107 restarts in a session went unreported under an unbroken `OK / 0 min old`.
- **A resident job that declares a `port` can reclaim it from a process the supervisor never spawned.**
  `adopt_prior_state` only ever adopts a PID already in ITS OWN persisted registry — a process left
  running by a manual launch (`pnpm dev:server`, the console skill, a supervisor that itself died
  uncleanly mid-restart) is invisible to it, was never spawned or adopted, and so was never reachable
  by anything. On 2026-08-23 an orphaned console child held `:5070` for 9 hours and ~1600 failed
  restarts — every attempt died on `EADDRINUSE`, climbed the backoff ladder, and tried again, forever,
  because nothing on the reliability path could tell "my own child, mid-restart" apart from "something
  else is squatting on my port". `_reclaim_stuck_port` (`supervisor.py`) closes that gap for jobs that
  declare a `port` in their `JobSpec` (today: only `console`, from `cfgmod.console_serve_port()`,
  itself read the same way the Node server resolves its own): past
  `_PORT_RECLAIM_AFTER_FAILURES` (8) consecutive failed spawns it asks the OS who actually holds the
  port, and if that PID is not one the supervisor recognizes as its own (`_known_pids` — every
  live handle plus every job's `running_pid`, adopted orphans included) and is genuinely still alive,
  it kills that PID's whole tree and resets the failure ladder for a clean next attempt. Gated on the
  failure count specifically so this can never fire on a normal restart race or a developer's own
  brief `pnpm dev:server` session — only on the sustained, minutes-long stuck case the ladder alone
  never recovers from. Opt out per job via config (`console.reclaim_stuck_port: false`) for a checkout
  where a manually-run console should be left alone indefinitely instead.
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
  paper DB or schedule of their own). It never places, cancels, or closes an order. A service that is
  **alive but wedged** is covered too (2026-08-23), but only when the service itself says so: a
  `status_argv` payload reporting `stalled: true` (the gex recorder publishes a per-tick heartbeat
  and derives that flag from its silence) gets the stop-then-start recycle — stop first because a
  plain start loses to the wedged pid's single-instance lock — under the same `auto_restart` gate.
  A service that publishes no stall signal keeps the original contract: running means healthy,
  nothing is touched, because restarting on "I can't tell" is the calendars failure. The same
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
  **A new symbol and a wider window are not the same urgency**, and treating them alike cost a
  session: on 2026-08-17 pmcc walked its per-symbol window hint up its escalation ladder as its
  deep-ITM misses accumulated, every step counted as growth, and the producer was stopped and
  restarted roughly every five minutes for two hours — each restart reloading every chain. That
  module's hint also *decays* after a quiet hour, so the ladder gets climbed more than once and the
  loop has no natural end. A new symbol still recycles on sight, because a module that cannot see an
  instrument at all is blind; a hint-only widening waits out `servicecfg.HINT_RECYCLE_COOLDOWN_S`
  from the last launch, and the staleness reason says it is holding off rather than going quiet.
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
- **Deterministic solutions preferred over AI/agentic ones** (root file), and most sharply on
  loop-decision and reliability paths. The modules' loops depend only on their local tools + this
  guidance; the 2026-07-01 stall — an external streamer dependency silent for 34 hours, invisible
  from the decision path because a hung dependency looks like a quiet market — is the case that
  makes the preference worth stating rather than assuming.
- **Correlation risk is PARTLY guarded (2026-08-20).** Two vehicles on the SAME index (MEIC's
  SPX + XSP case) are refused by `tests/test_symbol_correlation_lint.py`, which reads what each
  module declares in `state/stream_requests/` — per-symbol caps would otherwise count one
  directional bet as two independent risks. Still unguarded, and reported rather than failed by
  that same test: broad correlation across DIFFERENT indices (~0.9 for SPY against QQQ, and pmcc
  currently holds three leveraged equity ETFs), and Earnings' same-sector/same-date single names,
  which stay a hand-maintained `correlation_block_list`. Do not read the lint as covering more
  than the same-index case.
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
