# Cherrypick — Roadmap & Stage 0 status

Cherrypick is the umbrella orchestrator for a trading-tool suite. It drives sibling modules
(**MEICAgent**, **EarningsAgent**) in place for **unattended paper-trading data collection**, with a
watchdog and notifications so a walk-away user is told — or at least has it logged — whenever anything
stalls.

## Documentation & Commit Rule
- Write all documentation and pull request descriptions from a human developer's perspective.
- Never include co-author attribution or AI signatures in git commit messages.

> - **NEVER** log personal changelogs or task trackers here.
> - **NEVER** log or display account numbers. **Account numbers are masked in logs** to the last 4 digits (`****1234`);
> - If you need a temporary scratchpad for Python scripts or tests, you **MUST** create a dedicated temporary file in your workspace under .tmp/ and delete it when finished.

> ⚠️ **CRITICAL INSTRUCTION**: This repo runs correctly on any machine/OS, not just the dev machine.
> - **NEVER** hardcode absolute paths (e.g. `C:\Users\...`, `/Users/...`). Build paths relative to file location (`Path(__file__).resolve().parent...`) or from config/environment.
> - **NEVER** save working files/tests to root — use `/src`, `/tests`, `/docs`, `/config`
> - **NEVER** hardcode machine-specific details (username, hostname except `127.0.0.1`/`localhost`, drive letters)
> - Before committing new path-construction code, verify it uses `Path(__file__)`, env var, or config value — never a literal machine path.

> **Full design & phased roadmap:** `~/.claude/plans/cherrypick-plan.md` (Stages 0–8, the
> `cherrypick-core` shared-library extraction, new modules, onboarding, standards). This file tracks
> only what has actually been built.

## Prime directive
A user sets up paper plans, **walks away** for a day/night/week, and trusts the process won't be
silently interrupted: any failure is **notified**, or at an absolute floor **warned through logging**.

## Design invariants (inherited from both modules — do not violate)
- **Umbrella only.** Cherrypick never edits a module's internals and never touches **live** trading.
- **No decision-path dependency.** The watchdog/notify path uses only stdlib + the OS shell — no MCP,
  no network client, no AI tooling — so it has no new failure mode. Opt-in AI tooling (agentmemory,
  graphify) is for authoring only, never runtime.
- **Portable.** Paths come from `Path(__file__)` or config — never hardcoded absolute/machine paths.
- **Credentials in the OS keyring only.** Secrets (e.g. Slack webhook) come from env vars, never files.
- **Paper ↔ live isolation.** Cherrypick only invokes paper engines / paper DBs.

## Stage 0 — built (tonight)
- [x] Scaffold: `config.json`, `src/cherrypick/{orchestrator,notify}/`, `logs/`, `state/`, and the
      `run.py` launcher (packaged as the `cherrypick` distribution — `pipx install` gives a `cherrypick`
      console script; the src-layout `cherrypick` namespace composes with `cherrypick.core`).
- [x] **MEIC paper** wired via its own self-healing task (`paper_loop.py --install-task`) + streamer
      ensured up.
- [x] **EarningsAgent paper** scheduled by Cherrypick (module has no scheduler of its own): daily
      `run-earnings-entry` (~15:45 ET) and `run-earnings-exit` (~09:45 ET) via
      `strategy_test_runner.py run_entries/run_closes`.
- [x] **Watchdog** (`Cherrypick-Watchdog`, every 10 min): task registration, session-time freshness,
      streamer liveness (benign auto-restart), Dolt reachability, and an earnings entry-SLA check —
      logs to `logs/watchdog.log` **and notifies** with de-dup + re-notify throttling.
- [x] **Notifications**: logging floor (`logs/notify.log`) + desktop toast; Slack/Discord webhooks with
      URLs stored in the **OS keyring** (`cherrypick secrets-set --channel slack|discord`) — never in
      files or env vars.
- [x] **Trade notifier**: pushes each paper entry/exit to `notify.trade_channels` (log + Discord). Two
      schemas wired — MEIC `ic_trades` and Earnings `trades` (`orchestrator/trade_notifier.py`,
      dispatched by `paper.trade_schema`). A dedicated **`Cherrypick-TradeNotify`** task (`trade_notify`
      config, every ~2 min) drives the low-latency path; the watchdog tick and the end of each earnings
      run also fire it as fallbacks. State is written atomically so overlapping runs can't corrupt it.
- [x] **`cherrypick doctor`**: interpreter, config, module paths, broker/keyring, streamer, tasks,
      paper-DB writability, clock/timezone, Dolt.

## Reporting & alerting hub (Part 3.4 / Part 14) — in progress
- [x] **`cherrypick report`** (first slice): unified, read-only cross-module paper P&L
      (`orchestrator/report.py`). Reads each module's paper DB by `paper.trade_schema` (MEIC `ic_trades`,
      Earnings `trades`), computes net-of-cost P&L / win rate per module and **per risk profile**, plus a
      suite total. Profile grouping mirrors `cherrypick.core.profiles.compare_profiles` inline (Cherrypick is
      not yet a cherrypick-core consumer; the umbrella must not import a module's vendored `_core`). 6
      tests; verified against live paper data (13 MEIC closed trades across all four profiles).
- [x] **`cherrypick dashboard`** (Part-14 first slice): a read-only, file-only **status dashboard**
      (`orchestrator/dashboard.py`). One self-contained static HTML page — suite header (overall status,
      ET clock/session, watchdog heartbeat age, notify channels, suite P&L), an active WARN/CRITICAL
      rollup, per-module cards (PAPER badge, P&L + per-profile table + that module's findings + a
      **calibration** panel of advisory promotion recommendations), and a bounded, level-colored
      **log tail** with a client-side filter. Health comes from the watchdog
      heartbeat (not a live `doctor`/broker call), so it never touches the broker/network. Regenerated
      on each watchdog tick and by the command; written atomically to `dashboard.html`.
- [x] **Paper-drawdown alert** (drift): opt-in, report-driven watchdog check
      (`watchdog._check_drawdown`, config `watchdog.drawdown`). Suite/module net paper P&L at/below a
      floor → WARN, below `floor*critical_multiplier` → CRITICAL, flowing through the existing
      dedup/re-notify state machine. Off unless configured; paper-only, never trades.
- [x] **`cherrypick calibrate`** (Part-10 advisor surface): per-profile paper **calibration readings**
      (sample, win rate, distinct sessions, net-of-cost P&L) + an **advisory promotion recommendation**
      per risk-ladder rung (`orchestrator/calibrate.py`). Reuses `report`'s session-augmented readers;
      inline-mirrors `cherrypick.core.profiles.recommend_promotion`/`PROMOTION_RULE` (umbrella isn't a
      cherrypick-core consumer). Ladder/rule/deliberate-only come from each module's `calibration`
      config; advisory only — never mutates config or switches live risk. Verified against live paper
      data (per-profile net matches `report`'s `by_profile`).
- [x] **Umbrella now consumes cherrypick-core.** Added `cherrypick-core` as a git submodule at
      `src/_core` (bootstrapped onto `sys.path` in `orchestrator/__init__`, mirroring the trading
      modules), and dropped the inline profile mirrors: `report.py` and `calibrate.py` now import
      `compare_profiles`/`recommend_promotion`/`PROMOTION_RULE` from `cherrypick.core.profiles` directly.
      Behavior unchanged (the mirrors were faithful; the existing tests are the regression guard). CI
      checks out submodules; `src/_core` is excluded from ruff + packaging. **Fresh clones:** run
      `git submodule update --init` (as the modules require).
- [x] **`dashboard --serve` live view + declarative section contract (shipped 2026-07-11).**
      `dashboard --serve` runs a localhost `ThreadingHTTPServer` (`orchestrator/serve.py`) that
      re-renders the suite page fresh per request. The `cherrypick.core.viz` `DashboardSection` contract
      lets any module contribute a live card by emitting a small JSON payload (metrics tiles + a signed
      bar series) — the umbrella renders it generically (`orchestrator/sections.py` subprocesses the
      module by config-declared `fetch_argv`; never imports its internals), so a new module gets a card
      for free. First consumer: **cherrypick-gex**, a self-hosted GEX (gamma-exposure) dashboard —
      GEX-by-OI vs GEX-by-volume, gamma flip, call/put walls — with its own streaming (see below).
- [ ] **Next:** embedded module dashboards; broker-vs-DB **reconciliation** drift.
      *(The parallel-shadow paper **run** orchestration that feeds calibration stays module-side.)*

## GEX dashboard + shared streamer (shipped 2026-07-11)
- [x] **`cherrypick.core.gex.compute_gex_profile`** — the rich per-strike OI+volume GEX aggregation
      (net GEX by OI and by volume, call/put walls, gamma flip), shared so cherrypick-meic's dashboard
      and the GEX module compute identically (retired the drift the GEX math was extracted to prevent).
- [x] **`cherrypick-gex`** — a new standalone module: reads a stream cache read-only, computes GEX via
      the shared core, serves its own Chart.js view, and emits a `core.viz` section for the umbrella.
- [x] **`cherrypick.core.streamer` + `cherrypick.core.streamcache`** — the persistent DXLink streaming
      engine + cache schema extracted from MEIC into one shared implementation. cherrypick-gex streams
      standalone; MEIC's `streamer.py` migrated onto it (ORB / open-position policy / 7699 API injected
      as hooks), retiring ~500 lines of duplicate engine. Verified live end-to-end.

## Shipped since Stage 0 — cherrypick-core extraction + standards
> Full design & running reconciliation live in `~/.claude/plans/cherrypick-plan.md` (see its
> **Build Status** section). `cherrypick-core` is a public GitHub repo, submoduled into both suite
> modules at `src/_core`; each consumer keeps a thin shim and cut its call sites over. All CI-green.

- [x] **`cherrypick-core` shared library (8 packages), consumed by MEICAgent & EarningsAgent:**
      `auth` (credentials + session), `calendar`, `dxfeed`, `fees` (cost-adjusted fills +
      the IC open/`ic_close_fee`/`ic_expire_fee` schedule — MEIC's paper engine now draws from
      this one source too, the first brick of the Part 11 shared cost model), `gex`, `broker`
      (account primitives + option-chain strike helpers + `build_order` + `place_order`),
      `risk` (`evaluate_deploy_limit`), `db` (`connect` + additive-migration runner),
      `profiles` (Phase A: dual-source registry + generalized merge engine; Phase B:
      `attribution_tag` contract; Phase C: `compare_profiles` calibration comparison engine —
      metric-agnostic group-by-tag orchestration, metrics injected, both modules' P&L rollups cut
      over; Phase D: `recommend_promotion` advisor — codified risk-ladder progression, pure /
      advisory / human-gated, never auto-promotes live). Read-path
      cutovers verified with live read-only broker smokes; order construction/submission verified
      offline + via mocked command tests.
- [x] **Account deploy-limit governor** — wired into both modules' `execute_trade` through
      `broker.place_order`, **opt-in / off by default / fail-closed**, enforced only on a live submit
      (`account_deploy_limit_pct` in config, default 0). A live dry-run smoke is a pending
      user-supervised step.
- [x] **Engineering standards** — ruff + GitHub Actions CI + pre-commit (`ruff-check`) across all
      three repos (cherrypick-core, MEICAgent, EarningsAgent).
- [x] **Packaging & install** — `cherrypick-core` and the `cherrypick` umbrella are pip-installable
      (single `cherrypick.*` PEP 420 namespace, a `cherrypick` console script, the `run.py` launcher, and
      CI validating the editable install). All scheduled tasks were re-registered at the new launcher
      (`run.py install`; `doctor` ALL GREEN), which surfaced and fixed two `src/_core` bootstrap-order
      bugs (`paper_loop.py`, `session.py`).
- [–] **Not extracted (by design):** metrics (MEIC daily-series vs Earnings event-trade — same names,
      different math; parameterize-not-unify), stdlib logging (n=1: Earnings is print-based), MEIC's
      cache/futures `_fetch_chain`, Earnings' strategy `sizing.py`, and the module dashboards.
- [ ] **Remaining (design efforts):** the **paper-trading framework** (Part 11) — now the linchpin;
      it unblocks the two deferred profiles pieces that need it (Phase C's parallel-shadow *run*
      orchestration and Phase D's advisor *wiring*, since profile-tagged calibration data lives
      behind the paper-DB isolation boundary). `profiles/` Phases A–D (merge engine, attribution
      contract, comparison engine, promotion advisor) are all shipped as core primitives. Then: new
      modules (wheel, roll manager, reporting hub), the Part-14 dashboard, watchdog hardening,
      onboarding wizard, POSIX scheduler backend, and `git init` of Cherrypick itself.

## Suite modularization & install (shipped 2026-07-11)
> First slice of the module re-architecture the plan had filed under "deferred": the trading modules
> now live under `cherrypick-*` names, and the umbrella can materialize them on any machine.

- [x] **Module repos split under the `cherrypick-*` name.** The current (namespace-migrated) state of
      each module was forked into new private repos — `cherrypick-meic` ← MEICAgent, `cherrypick-earnings`
      ← EarningsAgent (full history + tags + `src/_core` submodule preserved). The **original**
      `MEICAgent`/`EarningsAgent` repos were rolled back to their pre-migration ~09:30-ET 2026-07-10
      state; the discarded work is recoverable from the copies and from a `pre-rollback-2026-07-11` tag
      on each original.
- [x] **Managed modules home.** A module is located by a `repo` URL **or** an explicit `path` override
      (in-place dev checkout). `cherrypick install` clones a module into `~/.cherrypick/modules/<name>`
      (`+ git submodule update --init`, which pulls that module's own pinned `cherrypick-core`) **only
      when the checkout is absent** — it never touches an existing one; `uninstall` leaves checkouts in
      place. `doctor` reports `not installed (run: cherrypick install)`. Home precedence:
      `CHERRYPICK_MODULES_HOME` → `CHERRYPICK_HOME/modules` → `~/.cherrypick/modules`; kept independent
      of the source-checkout root so module runtime data (e.g. Earnings' Dolt store) never nests in a repo.
- [x] **Runtime-home unification.** `ROOT` (config.json, logs/, state/, dashboard.html) defaults to
      `~/.cherrypick` for a pip-installed copy, keeps the repo root for a source checkout, and honors
      `CHERRYPICK_HOME` — fixing the installed console script's previously unwritable `site-packages`
      home. `tests/test_config.py` covers the resolution.
- [x] **`CLAUDE.md` added** — umbrella architecture guide + a **Suite-wide Guardrails** section
      consolidating the shared rules inherited from both modules (Part 13.5: guardrails documented in one
      place the umbrella honors).
- [x] **Installed umbrella imports `cherrypick.core`.** `cherrypick-core` is now a declared dependency
      (a direct git reference — it's Private :: Do Not Upload, so not on PyPI); a real install (wheel /
      `pip install git+…` / pipx) pulls it, while source/editable checkouts still resolve core from the
      `src/_core` submodule (which wins on `sys.path`). Also added `tzdata` and made
      `timeutil._tz`'s pytz fallback fire at **call time** — the old import-time guard was dead code, so a
      Windows install with no IANA tz database crashed on every timezone op. Validated in a clean venv
      (non-editable + editable/CI-parity, full suite green).
- [x] **Module cutover complete.** Both new checkouts seeded with machine-local `config.json` + durable
      `data/` DBs (Earnings' multi-GB Dolt store left as a localhost service, not copied); `cherrypick
      install` re-registered every task at the new paths and moved the streamer to `cherrypick-meic`.
- [x] **`cherrypick init` (onboarding, Part 12 Concept E).** Scaffolds `config.json` non-destructively
      (the repo's `config.example.json`, or a compact embedded template for an installed copy that ships
      none), then validates it structurally + checks each enabled module's checkout and prints the next
      steps (`secrets-set` → `doctor` → `install`). Runs before the config pre-load so a fresh user with
      no config can use it. Pure `validate_config`/`scaffold` are unit-tested (`tests/test_init.py`).

## Known Stage-0 limitations (hardened in later phases)
- **Scheduler: Windows (`schtasks`) + a POSIX cron backend.** `tasks.py` now dispatches by platform —
  Windows uses `schtasks`; POSIX manages the user crontab (each Cherrypick line tagged
  `# cherrypick:<name>` for idempotent upsert/remove). The cron-line + crontab-editing logic is pure and
  unit-tested cross-platform (`tests/test_tasks_cron.py`); end-to-end cron **execution** (env,
  notifications) still wants validation on a real POSIX host. launchd/systemd remain future options.
- **No watchdog-of-the-watchdog.** The Windows Task Scheduler durability + the logging floor are the
  Stage-0 backstop; `doctor` surfaces a missing watchdog task. Phase 6b promotes this into the shared
  reliability framework.
- **Throwaway-tolerant.** This watchdog/notify logic is the seed Phase 6b absorbs into `cherrypick-core`.

## Commands
```
python run.py doctor        # green/red readiness (read-only)
python run.py install       # register all tasks + start streamer
python run.py status        # task state + last heartbeats
python run.py watchdog      # one watchdog pass (what the task runs)
python run.py notify-test   # prove notifications reach you
python run.py uninstall     # remove Cherrypick-managed tasks
```
