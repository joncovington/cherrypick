# cherrypick — Project Reference (Internal Wiki)

> **What it is.** cherrypick is the **umbrella orchestrator** for a paper-trading data-collection suite.
> It drives sibling trading modules (MEIC, Earnings) **in place**, on an OS schedule, for hands-off
> **paper**-trading data collection — with a watchdog and notifications so a walk-away operator is told
> whenever something stalls. It **never places live trades**; its only live-adjacent action is
> onboarding config (selecting which broker account a module *would* trade in live).

The suite is a **monorepo**: `packages/umbrella` (this orchestrator) + `packages/meic` +
`packages/earnings` (the trading modules) + a shared library (`cherrypick-core`) vendored per package as
a git submodule at `packages/<pkg>/src/_core`.

---

## 1. Architecture Overview

### Layout
```
cherrypick/                     # monorepo
├── packages/
│   ├── umbrella/               # orchestrator (this doc's subject)
│   │   ├── run.py              # launcher (puts src/ on sys.path, delegates to cli:main)
│   │   ├── src/cherrypick/
│   │   │   ├── cli.py          # CLI dispatch (all subcommands)
│   │   │   ├── orchestrator/   # config, tasks, watchdog, doctor, report, calibrate,
│   │   │   │                   #   reconcile, dashboard, serve, sections, embeds,
│   │   │   │                   #   accounts, connect, timeutil, util
│   │   │   └── notify/         # Notifier + keyring-backed webhook secrets
│   │   ├── src/_core → submodule (cherrypick.core.*)
│   │   ├── config.example.json # config template (copy to config.json, gitignored)
│   │   └── tests/
│   ├── meic/                   # MEIC 0DTE multiple-entry iron-condor module
│   └── earnings/               # earnings-play module (defined-risk strategies)
└── .gitmodules                 # 3 × cherrypick-core (one per package)
```

### How the components interact
- **Drive-by-subprocess, never import.** The umbrella runs each module as a subprocess using
  **config-declared argv** (`orchestrator/config.py` resolves module locations; `install`, the paper
  runners, `sections`, `embeds`, `reconcile`, and `connect`/`account` all invoke module scripts in
  place). It **never imports a module's internals** — the boundary is a directory boundary, and the
  reliability/isolation guarantees rest on it.
- **Two halves, one config.** Everything hangs off `config.json`:
  - **Write side (the reliability guarantee).** `orchestrator/watchdog.py` runs on a schedule
    (`orchestrator/tasks.py` → Windows `schtasks` / POSIX cron). Each tick it checks every module's
    paper pipeline (task registered, data fresh in-session, streamer alive, earnings SLA met), logs
    findings, and pushes alerts via `notify/notifier.py`. It has a dedup / re-notify / recovery state
    machine (`state/watchdog_state.json`).
  - **Read side (look anytime).** `report.py` (cross-module paper P&L), `calibrate.py` (per-profile
    promotion advisor), `dashboard.py` (static HTML + a `--serve` live view), and `reconcile.py`
    (paper↔live isolation guard). Read-only and file-only, **except** `reconcile` and the `--serve`
    live cards, which make read-only broker queries on demand (off the reliability path).
- **Per-schema dispatch.** Each module's paper DB has a different SQLite schema, selected by
  `paper.trade_schema` (`"meic_ic"` → MEIC `ic_trades`; `"earnings"` → Earnings `trades`).
  `report`, `calibrate`, `reconcile`, and `trade_notifier` each carry a small reader/adapter registry
  keyed by that value — add a schema by extending the registries, not the callers.
- **Shared logic** lives in `cherrypick.core.*` (the `cherrypick-core` submodule): `auth`, `broker`,
  `calendar`, `db`, `dxfeed`, `fees`, `gex`, `profiles`, `risk`, `streamcache`, `streamer`, `viz`.

### Load-bearing invariants
- **No network / service / AI on the reliability path.** The watchdog → notify path uses only the
  stdlib + the OS shell (no MCP, no HTTP client, no AI tooling). A past 34-hour silent stall from an
  external dependency is why this rule exists.
- **Read surfaces read files, never the broker** — except the explicitly-gated, on-demand `reconcile`
  and `--serve` doctor/section cards.
- **Paper ↔ live isolation.** cherrypick only invokes paper engines / paper DBs. The single
  live-*config* exception is onboarding (`connect` / `account`): it selects which account a module
  trades in when live, but never places/cancels/closes an order and never flips `enable_live_trading`.
- **Credentials in the OS keyring only** — never in files, env vars, or logs. Account numbers are
  masked to `****1234` everywhere they surface.

---

## 2. Setup & Installation

**Prerequisites:** Python **3.11+** (tested on 3.13), `git`, and — for the Earnings module — a local
[Dolt](https://github.com/dolthub/dolt) install on `PATH`. Windows is the primary target (Task
Scheduler); a POSIX cron backend exists but end-to-end cron execution is not yet validated.

```bash
# 1. Clone with submodules (each package needs its src/_core — imports fail without it)
git clone --recurse-submodules https://github.com/joncovington/cherrypick.git
cd cherrypick
git submodule update --init --recursive          # if you forgot --recurse-submodules

# 2. Install the orchestrator (editable, with dev extras)
cd packages/umbrella
pip install -e ".[dev]"                           # exposes the `cherrypick` console script
#   The two trading modules install the same way (packages/meic: pip install -e ".[dev]";
#   packages/earnings: pip install -r requirements.txt -r requirements-dev.txt).

# 3. Configure (config.json is gitignored / machine-local)
cp config.example.json config.json                # then edit: module paths, notify channels, etc.

# 4. Store broker credentials in the OS keyring (per module — see §3). Guided:
python run.py connect --module meic               # OAuth creds + live-account selection

# 5. Verify readiness (read-only, never installs/trades)
python run.py doctor                              # green/red across interpreter, config, broker,
                                                  #   streamer, tasks, paper DBs, Dolt, notify

# 6. Register the unattended schedule (Windows Task Scheduler / POSIX cron)
python run.py install
```

> Run the CLI from a source checkout with **`python run.py <cmd>`** (not a root `cherrypick.py` — a
> root module of that name would shadow the `cherrypick` namespace package). A pip-installed copy also
> exposes `cherrypick <cmd>` and `python -m cherrypick`.

---

## 3. Environment Variables

**There is no `.env` file, by design.** Every secret — broker OAuth tokens and Slack/Discord webhook
URLs — lives in the **OS keyring** (Windows Credential Manager/DPAPI, macOS Keychain, Linux Secret
Service), never in files or environment variables. This is a load-bearing security guardrail.

| Secret | Where it lives | How to set it |
|---|---|---|
| Broker OAuth (`client_secret`, `refresh_token`) | Each module's keyring service (`meicagent` / `earningsagent`) | `cherrypick connect --module <m>` (delegates to the module's own hidden-input tool) |
| Live-trading account (`account_number`) | Same module keyring | `cherrypick account --module <m> --set <last4|index>` |
| Slack/Discord webhook URLs | Umbrella keyring service `cherrypick-notify` | `cherrypick secrets-set --channel slack|discord` |

The only actual **environment variables** are optional path overrides (never required):

| Var | Purpose | Default |
|---|---|---|
| `CHERRYPICK_HOME` | Runtime home for `config.json`, `logs/`, `state/`, `dashboard.html` | repo root (source checkout) or `~/.cherrypick` (installed copy) |
| `CHERRYPICK_MODULES_HOME` | Where `install` materializes module checkouts (when a module has no explicit `path`) | `~/.cherrypick/modules` |
| `MEIC_DATA_DIR` *(module)* | MEIC's data dir (paper DB, stream cache) | `~/.cherrypick/data/meic` |

### `config.json` (the real "environment")
Machine-local, gitignored; see `config.example.json` for the authoritative, commented template. Key
blocks: `modules.<name>` (`path`/`repo`, `keyring_service`, `paper.{kind,trade_schema,paper_db,…}`,
`streamer`, `calibration`), `watchdog`, `dashboard` (`serve`, `sections`, `embeds`), `reconcile`,
`trade_notify`, `notify` (`channels`, `trade_channels`). Note `paper.paper_db` accepts a `~`/env-prefixed
or absolute path so it can point at a module's managed data home portably.

---

## 4. Core Features

- **Unattended paper data collection.** Drives each module's paper pipeline on an OS schedule. Two
  pipeline *kinds*: `self_healing` (MEIC manages its own loop task; the umbrella registers/verifies +
  starts the streamer) and `cherrypick_scheduled` (the umbrella owns daily entry/exit tasks for a
  module that has no scheduler of its own).
- **Watchdog reliability guarantee.** Periodic health checks (task registered, in-session data
  freshness, streamer liveness, earnings SLA), benign auto-remediation (restart a dead streamer — never
  a trading action), and alerting with dedup/re-notify/recovery.
- **Notifications.** Pluggable channels — `log` (always-on floor), `desktop`, `slack`, `discord` —
  with a dedicated low-latency **trade-notify** path that pushes new paper fills.
- **Read surfaces:** cross-module **`report`** (paper P&L, net of costs, per profile), **`calibrate`**
  (advisory promotion recommendations per risk-ladder rung), a **`dashboard`** (self-contained static
  HTML, plus a `--serve` live view with a System panel, live GEX-style section cards, embedded
  per-module dashboards, and a paper↔live reconcile card).
- **`doctor`** one-shot readiness check (`--fast` skips the authenticated broker round-trip).
- **`reconcile`** paper↔live isolation guard — queries every account on the real login and flags any
  that isn't flat.
- **`connect` / `account`** onboarding — set a module's OS-keyring credentials and select its
  live-trading account (config only; never trades).
- **Optional Dolt keep-alive** for the Earnings module (a cherrypick-managed minute task).

---

## 5. CLI / Function Reference

The CLI **is** the public API. Invoke as `python run.py <cmd>` (or `cherrypick <cmd>` when installed).
All commands emit JSON to stdout unless noted; scheduled tasks run under `pythonw` where stdout is
suppressed.

| Command | Inputs | Output / effect |
|---|---|---|
| `init [--force]` | — | Scaffold + validate `config.json`. |
| `install` | reads config | Register all scheduled tasks; start the streamer. **Windows/POSIX only.** |
| `uninstall` | reads config | Remove cherrypick-managed tasks (leaves module checkouts). |
| `status` | reads config | JSON: task registration state + last heartbeats. |
| `doctor [--fast]` | reads config, broker | Human-readable green/red report; exit 0 if OK/WARN, 1 on FAIL. `--fast` skips the broker check. |
| `watchdog` | reads config, files | Run one watchdog pass (what the scheduled task runs); writes heartbeat + logs; alerts on WARN/CRITICAL. |
| `report` | paper DBs | JSON: suite + per-module + per-profile P&L (net of costs). Read-only, file-only. |
| `calibrate` | paper DBs | JSON: per-profile readings + advisory promotion recs. Advisory only. |
| `reconcile` | broker (read-only) | Human report: verdict `FLAT`/`DRIFT`/`UNKNOWN` across every real account (masked). Exit 0/1/2. |
| `connect --module <m>` | interactive | Guided onboarding: OAuth creds (delegated to the module) + account selection. |
| `account --module <m> [--set <last4|index>] [--clear] [--yes]` | broker (read) + keyring (write) | List / set / clear a module's live-trading account (masked). |
| `dashboard [--serve] [--host H] [--port P] [--no-browser]` | files (+ broker on `--serve` cards) | Write `dashboard.html`, or serve a live localhost view. Loopback-only. |
| `notify-test` | notify channels | Fire a test notification through every configured channel. |
| `notify-trades` | paper DBs, channels | Push new paper entries/exits to `trade_channels`. |
| `secrets-set/-status/-delete --channel slack|discord [--url U]` | keyring | Manage push-channel webhooks (secret-free status). |
| `run-earnings-entry` / `run-earnings-exit` | module subprocess | Run the Earnings paper entry/exit (invoked by its daily task). |
| `ensure-dolt` | socket + subprocess | Start any module's declared Dolt server if down (keep-alive task). |

**Key internal helpers** (for contributors): `orchestrator/config.py::module_root` /
`paper_db_path` (portable path resolution — the single source of truth for where a module's paper DB
lives), `orchestrator/tasks.py::{create_minute_task,create_daily_task,registry_snapshot}` (OS scheduler
wrappers + the shared task-state snapshot), `orchestrator/util.py::{first_json,mask_account,
CREATE_NO_WINDOW}`, and `notify/secrets.py` (keyring webhook storage).

---

## 6. Testing & Deployment

### Testing
Each package is tested independently with **pytest** (markers: `unit` [default lane], `live`, `windows`).

```bash
# From a package dir (e.g. packages/umbrella):
python -m pytest                    # default lane: -m "not live" -q  (per pytest config)
python -m pytest tests/test_dashboard.py                     # one file
ruff check . && ruff format --check .                        # lint + format (line length 110; src/_core excluded)
```

**CI** (GitHub Actions, `.github/workflows/ci.yml`) runs a **matrix over all three packages**: checkout
with `submodules: recursive` (so each `src/_core` is at its pinned `cherrypick-core` SHA), install, lint,
and test. Current test totals: umbrella 137, meic 242, earnings 225.

### Deployment
cherrypick is **not a server** — it deploys as a set of **OS scheduled tasks** on the operating
machine:

```bash
python run.py install     # registers: watchdog (~10 min), trade-notify (~2 min), meic paper-loop
                          #   (self-healing, ~2 min), earnings entry/exit (daily 15:45 / 09:45),
                          #   Dolt keep-alive (~5 min); starts the streamer.
python run.py doctor      # confirm green (broker, Dolt, paper DBs, all tasks registered)
python run.py status      # confirm tasks Enabled with next-run times
```

Tasks run windowless (`pythonw`) and are hardened for walk-away operation (battery guards cleared,
console-window suppression, optional Windows-Update active-hours pinning via
`tools/setup-walkaway-durability.ps1`). Runtime state (paper DBs, Dolt store, keyring) lives outside the
repo, so a redeploy or checkout move never disturbs collected data. To retire the schedule:
`python run.py uninstall`.
