# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Cherrypick is the **umbrella orchestrator** for a trading-tool suite. It drives the sibling module
repos (`../MEICAgent`, `../EarningsAgent`) **in place** — via subprocess, using paths from config — for
unattended **paper**-trading data collection, with a watchdog + notifications so a walk-away user is
told (or at least has it logged) whenever something stalls. It never edits a module's internals and
**never touches live trading**. `ROADMAP.md` tracks what has actually shipped; the full design lives in
`~/.claude/plans/cherrypick-plan.md`.

## Commands

```bash
# Fresh clone: pull the cherrypick-core submodule (src/_core) first — imports fail without it.
git submodule update --init

# Run the CLI from a source checkout (do NOT create a root cherrypick.py — see Gotchas):
python run.py <cmd>          # or, if pip-installed: `cherrypick <cmd>` / `python -m cherrypick`
python run.py doctor         # green/red readiness (read-only)
python run.py install        # register OS scheduled tasks + start the streamer (Windows-only)
python run.py status         # task registration + last heartbeats
python run.py watchdog       # one watchdog pass (what the scheduled task runs)
python run.py report         # unified cross-module paper P&L (read-only)
python run.py dashboard      # regenerate the static status dashboard -> dashboard.html
python run.py calibrate      # per-profile calibration readings + promotion recommendations
python run.py uninstall      # remove Cherrypick-managed tasks

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
  data fresh in-session, streamer alive, earnings SLA met), logs findings, and pushes alerts through
  `notify/notifier.py`. It has a **dedup / re-notify / recovery state machine** (`_process_notifications`
  in watchdog.py, state in `state/watchdog_state.json`).
- **Read side (look whenever you want):** `report.py` (cross-module paper P&L), `calibrate.py`
  (per-profile promotion advisor), and `dashboard.py` (a single static HTML page composing all of it +
  a log tail). These are **read-only and file-only**.

**Per-schema dispatch.** Each module's paper DB has a different schema, selected by
`paper.trade_schema` in config (`"meic_ic"` → MEICAgent `ic_trades`; `"earnings"` → EarningsAgent
`trades`). `report.py`, `calibrate.py`, and `trade_notifier.py` each carry a small reader/adapter
registry keyed by that value; add a schema by extending those registries, not the callers.

**cherrypick-core is a submodule.** Shared logic (`cherrypick.core.profiles`, `.fees`, etc.) lives in
`src/_core` and is put on `sys.path` by a bootstrap in `orchestrator/__init__.py` — so `import
cherrypick.core...` resolves under `run.py`, pytest, and the editable console script alike. `src/_core`
is excluded from ruff and from the packaged wheel.

## Invariants (do not violate — the reasons are load-bearing)

- **No network / service / AI dependency on the reliability path.** The watchdog → notify path uses only
  the stdlib + the OS shell (no MCP, no HTTP client, no AI tooling), so it has no new failure mode. A
  34-hour silent stall is the reason this rule exists.
- **Read surfaces read files, never the broker.** `report`/`calibrate`/`dashboard` read paper DBs (SQLite
  read-only), watchdog state, and logs. In particular `dashboard.py` reads the **watchdog heartbeat**
  (`state/watchdog.last.json`) for health rather than re-running `doctor` (which shells out to the
  broker/streamer) — keep it that way so viewing status stays fast and offline.
- **Paper ↔ live isolation.** Cherrypick only invokes paper engines / paper DBs. Anything advisory
  (e.g. `calibrate`'s promotion recommendations, the drawdown alert) is advisory only — it never mutates
  a module's config or switches live risk.
- **The watchdog's only trading-adjacent action is benign, non-trading remediation** (restart a dead
  streamer). It never places, cancels, or closes an order.
- **Account numbers are masked** to the last 4 digits (`****1234`) anywhere they surface in logs or
  output — never emit a full account number (suite-wide rule from `ROADMAP.md`).
- **Best-effort side calls never break the reliability path.** The watchdog tick fires
  `trade_notifier.run` and `dashboard.render` inside `try/except`; a push/render hiccup must not fail the
  health check. Preserve this pattern when adding tick-time work.
- **Opt-in AI/dev tooling is local-only and off every runtime path.** `graphify` / `agentmemory` are
  authoring aids; their artifacts (`graphify-out/`, `.claude/`) are gitignored and they are never a
  runtime dependency.

## Suite-wide guardrails (inherited from MEICAgent & EarningsAgent)

Cherrypick drives the module repos in place, so it operates under the same guardrails both modules
declare in their own `CLAUDE.md` (MEIC also keeps a full entry-gate catalog in `../MEICAgent/GATES.md`).
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
  `config/`. (Cherrypick resolves module paths relative to `config.json`'s directory.)
- **Credentials in the OS keyring only.** Every secret — broker OAuth tokens in the modules, Slack/
  Discord webhooks here — lives in the OS keyring (Windows Credential Manager/DPAPI, macOS Keychain,
  Linux Secret Service), never in files, env vars, or logs.
- **Account numbers masked to `****1234`** everywhere they surface (see Invariants).
- **Paper ↔ live isolation.** Live-order tools in the modules are gated behind `enable_live_trading:
  true`, and paper mode never calls `execute_trade` (even a dry-run performs a real margin check).
  Cherrypick only ever invokes paper engines / paper DBs; anything advisory stays advisory (see
  Invariants). EarningsAgent is additionally **defined-risk only** — naked strategies were removed
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
- **`config.json` and `state/`, `logs/`, `dashboard.html` are gitignored** (machine-local). Edit
  `config.example.json` when a config key should be documented for other machines.
- **Scheduler dispatches by platform.** `orchestrator/tasks.py` uses `schtasks` on Windows and a crontab
  backend on POSIX (Cherrypick lines tagged `# cherrypick:<name>`). The cron logic is pure + unit-tested;
  cron *execution* on a real POSIX host is still unvalidated. launchd/systemd are future backends.
- **Commit messages: no AI / co-author attribution or AI signatures** (a suite-wide rule from
  `ROADMAP.md`). Write docs and PRs from a human developer's perspective.
