# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

cherrypick is the **orchestrator** for a trading-tool suite. It drives the sibling module
packages (`../meic`, `../earnings`, `../gex`, `../flies`) and the standalone market-data streamer
(`../streamer`, the suite's single producer of the shared stream cache) **in place** — via subprocess,
using paths from config — for unattended **paper**-trading data collection, with a watchdog +
notifications so a walk-away user is told (or at least has it logged) whenever something stalls. It never edits a module's internals and
**never places live trades** — the sole live-adjacent action is *onboarding config* (`connect`/`account`
select a module's live-trading account; see the Invariants below), never order placement. `ROADMAP.md`
tracks what has actually shipped; the full design lives in `~/.claude/plans/cherrypick-plan.md`; and the
suite-wide human documentation is the root [documentation index](../../docs/README.md) (architecture, CLI,
reporting, configuration, guardrails).

## Commands

```bash
# Fresh clone: pull the cherrypick-core submodule (src/_core) first — imports fail without it.
git submodule update --init

# Run the CLI from a source checkout (do NOT create a root cherrypick.py — see Gotchas):
python run.py <cmd>          # or, if pip-installed: `cherrypick <cmd>` / `python -m cherrypick`
python run.py doctor         # green/red readiness (read-only)
python run.py install        # register OS scheduled tasks + start the standalone streamer producer (Windows-only)
python run.py status         # task registration + last heartbeats
python run.py watchdog       # one watchdog pass (what the scheduled task runs)
python run.py report         # unified cross-module paper P&L (read-only); --eod / --date YYYY-MM-DD scopes to one session
python run.py eod-digest     # write logs/eod-digest-<day>.md: one session's cross-module P&L + module paper-eod links
python run.py notify-eod     # write the digest + push a one-line summary (the watchdog fires this, detached, once every module has settled)
python run.py archive        # end-of-month rotation: zip each finished month's reports + rotated logs to logs/archive/ (--dry-run / --month YYYY-MM); scheduled monthly as cherrypick-log-archive
python run.py eod-insight    # opt-in AI synthesis over the day's deterministic reports -> logs/eod-insight-<day>.md (needs Claude Code on PATH + eod_insight.enabled); watchdog-fired (detached) on the same completion event as the digest
python run.py dashboard      # regenerate the static status dashboard -> dashboard.html
python run.py calibrate      # per-profile calibration readings + promotion recommendations
python run.py migrate-home   # dry-run: move config files into ~/.cherrypick + sweep leftovers (--apply to perform)
python run.py uninstall      # remove cherrypick-managed tasks

# Tests (pytest; markers: unit [default lane], live, windows)
python -m pytest                                   # default: `-m "not live" -q` (see pytest.ini)
python -m pytest tests/test_dashboard.py           # one file
python -m pytest tests/test_report.py::test_report_unifies_pnl_net_of_costs_across_modules  # one test

# Lint / format (line-length 110; src/_core is excluded)
ruff check .
ruff format .
```

Config: copy `config.example.json` → `config.json` (gitignored, machine-local). Module paths in it are
resolved **relative to the config file's directory** — never hardcode absolute paths.

## Architecture

**src-layout PEP 420 namespace.** `src/cherrypick/` has no root `__init__.py`, so it composes with the
`cherrypick.core` package (from the `src/_core` submodule) under one `cherrypick.*` import namespace.
`run.py` puts `src/` on `sys.path` and delegates to `cherrypick.cli:main`.

**Two halves, one config.** Everything hangs off `config.json` (`orchestrator/config.py`):
- **Write side (the reliability guarantee):** `orchestrator/watchdog.py` runs on a schedule
  (`orchestrator/tasks.py` → Windows `schtasks`), checks each module's paper pipeline (task registered,
  data fresh in-session, the standalone streamer producer alive, earnings SLA met), logs findings, and pushes alerts through
  `notify/notifier.py`. It has a **dedup / re-notify / recovery state machine** (`_process_notifications`
  in watchdog.py, state in `state/watchdog_state.json`).
- **Read side (look whenever you want):** `report.py` (cross-module paper P&L; `run(session=…)` scopes
  to one settlement day for the daily/EOD views), `calibrate.py` (per-profile promotion advisor),
  `eod_digest.py` (one session's cross-module roll-up → `logs/eod-digest-<day>.md`, citing `report`'s
  numbers so it can't drift, + links to each module's own `paper-eod-<day>.md` and conversational
  `eod-analysis-<day>.md`), and `dashboard.py` (a single static HTML page composing all of it + a log
  tail). These are **read-only and file-only**. The EOD digest is also surfaced through the notifier.
  It is no longer a fixed-time task — the **watchdog fires it once every installed module has written its
  `paper-eod-<day>.md`** (with an `eod_digest.deadline` backstop, ET, so a late or flat module can't skip
  the day), launched **detached** so its notify push never runs on the reliability path. On by default;
  opt out with `"eod_digest": {"enabled": false}`. `logrotate.py` (`cherrypick archive`) is the maintenance
  counterpart: a monthly `cherrypick-log-archive` task zips each finished month's reports + rotated logs
  into `logs/archive/<YYYY-MM>/<scope>.zip` and removes the originals (idempotent, never touches the
  current month or an active `.log`) — also files-only and off the reliability path. `eod_insight.py`
  (`cherrypick eod-insight`) is the one place AI is invoked, and it is deliberately fenced: **opt-in and
  feature-detected** (`eod_insight.enabled` + Claude Code on PATH — off by default), it pipes the day's
  deterministic reports to `claude -p` in headless mode with **no execution/edit/network tools** and
  writes `eod-insight-<day>.md` (surfaced on the dashboard EOD card). The watchdog **launches it detached**
  on the same completion event as the digest — never in the watchdog process, so the `claude` call stays
  off the reliability path. It is an enrichment surface **off the watchdog reliability path**, best-effort,
  paper-reports-only — the deterministic `eod-analysis` stays the source of record, so the "no AI on the
  reliability path" invariant holds.

**Per-schema dispatch.** Each module's paper DB has a different schema, selected by
`paper.trade_schema` in config (`"meic_ic"` → MEIC's `ic_trades`; `"earnings"` → the Earnings module's
`trades`; `"fly_book"` → the Flies module's `fly_positions`, tagged by experiment *arm* rather than risk
profile). `report.py`, `calibrate.py`, `reconcile.py`, and `trade_notifier.py` each carry a small
reader/adapter registry keyed by that value; add a schema by extending those registries, not the
callers. All four must be extended together — a schema registered in three of them vanishes silently
from the fourth surface, with no error to notice. `report.py` additionally carries a **separate**
`_OPEN_READERS` registry for positions carried past the close (overnight capital-at-risk, no realized
P&L) that feeds only the report/digest — it is *not* one of the four, so it does not need matching
entries in calibrate/reconcile/notifier. Only the multi-day earnings module carries overnight; the
0DTE modules (MEIC, flies) settle within the session and return an empty overnight view by design.

**SLA heartbeat paths derive from the module name** (`config.sla_state_files`), not from a literal
filename. They were hardcoded to `earnings_*.last.json`, which was harmless while Earnings was the only
`cherrypick_scheduled` module and wrong as soon as a second one existed — the dashboard showed one
module's SLA under another's name and the watchdog raised a CRITICAL titled for the wrong module. Use
`paper.sla_state_prefix` to override for a module whose heartbeat files are named differently.

**cherrypick-core is a submodule.** Shared logic (`cherrypick.core.profiles`, `.fees`, etc.) lives in
`src/_core` and is put on `sys.path` by a bootstrap in `orchestrator/__init__.py` — so `import
cherrypick.core...` resolves under `run.py`, pytest, and the editable console script alike. `src/_core`
is excluded from ruff and from the packaged wheel.

## Invariants (do not violate — the reasons are load-bearing)

- **No network / service / AI dependency on the reliability path.** The watchdog → notify path uses only
  the stdlib + the OS shell (no MCP, no HTTP client, no AI tooling), so it has no new failure mode. A
  34-hour silent stall is the reason this rule exists.
- **Read surfaces read files, never the broker.** `report`/`calibrate`/`dashboard` read paper DBs (SQLite
  read-only), watchdog state, and logs. In particular the **static** `dashboard.py` render reads the
  **watchdog heartbeat** (`state/watchdog.last.json`) for health rather than re-running `doctor` (which
  shells out to the broker/streamer) — keep it that way so the auto-regenerated file stays fast and
  offline. The one exception is deliberate and gated: `dashboard --serve` exposes a `/api/system` route
  that runs `doctor.run()` for a live-checks card, polled client-side. That broker-touching call lives
  only on the served path (never the static regen), mirroring how the live section cards work — so the
  file written on every watchdog tick still never touches the broker. `dashboard --serve` also embeds
  each module's own dashboard in an iframe (`orchestrator/embeds.py`, `/embed/<id>` route): it launches
  a module's dashboard server or regenerates its static HTML on demand, driven by config-declared argv
  with **PAPER mode forced** in that argv. This too is serve-only (the static file omits the iframes)
  and never invokes a live/broker view.
- **Paper ↔ live isolation.** cherrypick only invokes paper engines / paper DBs. Anything advisory
  (e.g. `calibrate`'s promotion recommendations, the drawdown alert) is advisory only — it never mutates
  a module's config or switches live risk. The one place cherrypick reads the *real* broker account is
  `reconcile` (`orchestrator/reconcile.py`, `cherrypick reconcile` + the serve-only `/api/reconcile`
  card): a paper↔live isolation guard that enumerates **every** account on the login (`list_accounts` —
  tastytrade returns multiple per user) and flags any open positions/BP a paper-only suite shouldn't
  have. Like `doctor` it is a broker-touching, on-demand diagnostic — **off the watchdog reliability
  path**, read-only broker calls only (`list_accounts`/`get_positions`/`get_account_info`, never an
  order), account numbers masked, advisory. It never trades or mutates config.
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
- **The watchdog's only trading-adjacent action is benign, non-trading remediation** (restart a dead or
  silently-stalled **market-data streamer** — the standalone producer, a top-level `streamer` config
  block, session-gated and restarted on *silence* not just death, since the 34-hour stall was a live-but-
  quiet socket — or a dead managed **service**: top-level `services`, background daemons like the gex
  spot-trail recorder that `install` starts, the watchdog keeps alive via `status_argv`/`start_argv`,
  and `uninstall` stops; single-instance guarded, located by `path`/`repo` like modules but with no
  paper DB or schedule of their own). It never places, cancels, or closes an order.
- **Account numbers are masked** to the last 4 digits (`****1234`) anywhere they surface in logs or
  output — never emit a full account number (suite-wide rule from `ROADMAP.md`).
- **Best-effort side calls never break the reliability path.** The watchdog tick fires
  `trade_notifier.run`, `dashboard.render`, and the EOD digest/insight trigger inside `try/except`; a
  hiccup must not fail the health check. The EOD trigger only *launches* `notify-eod`/`eod-insight` as
  detached subprocesses — the digest's webhook push and the insight's `claude` call run in those children,
  never here — so the tick itself stays stdlib + OS-shell only. Preserve this pattern when adding
  tick-time work.
- **Opt-in AI/dev tooling is local-only and off every runtime path.** `graphify` / `agentmemory` are
  authoring aids; their artifacts (`graphify-out/`, `.claude/`) are gitignored and they are never a
  runtime dependency. The one tracked exception is `.claude/commands/` — checked-in slash commands are
  shared dev conveniences (e.g. `/serve-dashboard`); the rest of `.claude/` (settings.local.json,
  session state, plans) stays local-only. Slash commands are never a runtime dependency either.

## Suite-wide guardrails (inherited from the MEIC & Earnings modules)

cherrypick drives the module packages in place, so it operates under the same guardrails both modules
declare in their own `CLAUDE.md` (MEIC also keeps a full entry-gate catalog in `../meic/GATES.md`).
Honor these here too; several are already stated as Invariants above and are cross-referenced, not
repeated.

- **Instruction files hold no code and no logs.** This `CLAUDE.md` is for build commands, tech-stack
  reference, and project guidelines only — never Python, scripts, code blocks, or scratchpad logic, and
  never a changelog or task tracker (that is what `ROADMAP.md` and git history are for). Both modules
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
  through `cherrypick.core.home` (the shared resolver): `config.json`, `state/`, `dashboard.html`, and
  `logs/` all resolve under `~/.cherrypick` (relocated wholesale by `$CHERRYPICK_HOME`), so nothing
  runtime lands in a source checkout. `ROOT` is no longer the runtime home — it is only the *source
  anchor* for resolving a relative module `path` in config (e.g. `../meic`), derived from `__file__`.
  `load_config` reads `~/.cherrypick/config.json`, falling back to a legacy in-repo `config.json` until
  an explicit migrate moves it. The notifier computes the same logs home independently (it stays free of
  a config import on the reliability path). Edit `config.example.json` when a config key should be
  documented for other machines.
- **Scheduler dispatches by platform.** `orchestrator/tasks.py` uses `schtasks` on Windows and a crontab
  backend on POSIX (cherrypick lines tagged `# cherrypick:<name>`). The cron logic is pure + unit-tested;
  cron *execution* on a real POSIX host is still unvalidated. launchd/systemd are future backends.
- **Commit messages: no AI / co-author attribution or AI signatures** (a suite-wide rule from
  `ROADMAP.md`). Write docs and PRs from a human developer's perspective.
