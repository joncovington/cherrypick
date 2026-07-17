# Architecture

How the cherrypick suite is put together — the pieces, how they talk, and the boundaries that keep the
automation safe.

## The monorepo

One workspace holds the whole trading-tool suite as separate packages under `packages/`:

| Package | Role |
|---|---|
| `packages/orchestrator` | The **orchestrator** (`cherrypick`): scheduler, watchdog, notifications, onboarding, and the entire read side (report / calibrate / dashboard / EOD reports / archive). Drives the modules **by subprocess**, never by import. |
| `packages/meic` | The **MEIC** 0DTE multiple-entry iron-condor engine. |
| `packages/earnings` | The **Earnings** defined-risk earnings-play engine. |
| `packages/gex` | The standalone **GEX** (gamma-exposure) dashboard, built on the shared GEX engine and embedded by the orchestrator. |

Each package has its own `CLAUDE.md` with build commands, tech-stack reference, and invariants.

## Shared library: `cherrypick.core` (a submodule)

Common logic — `cherrypick.core.calendar`, `.fees`, `.profiles`, `.gex`, `.streamer`, `.auth`, `.broker`,
`.db`, `.viz`, `.home` — lives in the **`cherrypick-core`** git submodule, vendored per package at
`packages/<pkg>/src/_core` (one URL, pinned SHA). This is why:

- A fresh clone must run `git submodule update --init --recursive`, or every `import cherrypick.core…`
  fails.
- Each package **self-bootstraps** `src/_core` onto `sys.path` at import time (in files like `paths.py`,
  `db.py`, `credentials.py`) so imports resolve under `run.py`, pytest, and an editable install alike.
  Those inserts look redundant but are load-bearing — don't remove them.
- `src/_core` is excluded from ruff and from the packaged wheel.

The shared core is what lets the orchestrator's `report`/`calibrate` and a module's own engine agree on
fees, calendar dates, and profile attribution without copy-pasting logic.

## src-layout & the import namespace

`packages/orchestrator/src/cherrypick/` has **no root `__init__.py`** — it's a PEP 420 namespace package,
so it composes with the `cherrypick.core` package (from `src/_core`) under one `cherrypick.*` import
root. `run.py` puts `src/` on `sys.path` and delegates to `cherrypick.cli:main`.

> **Gotcha:** the launcher is `run.py`, **not** `cherrypick.py`. A root module named `cherrypick.py`
> would shadow the `src/cherrypick` namespace package (a regular module outranks a PEP 420 namespace on
> `sys.path`). Scheduled tasks invoke `run.py`.

## Two halves, one config

Everything hangs off one config file per package. The orchestrator's `config.json`
(`orchestrator/config.py`) splits into two halves:

- **Write side — the reliability guarantee.** `orchestrator/watchdog.py` runs on a schedule
  (`orchestrator/tasks.py` → Windows `schtasks` / POSIX cron), checks each module's paper pipeline (task
  registered, data fresh in-session, streamer alive, earnings SLA met), logs findings, and pushes alerts
  through `notify/notifier.py`. It has a dedup / re-notify / recovery state machine
  (`state/watchdog_state.json`). This path uses **only the stdlib + the OS shell** — no network, no AI —
  so it has no new failure mode. (A 34-hour silent stall from an external streamer dependency is why that
  rule is load-bearing.)
- **Read side — look whenever you want.** `report.py` (cross-module paper P&L), `calibrate.py` (per-profile
  promotion advisor), `eod_digest.py` (one session's cross-module roll-up), `eod_insight.py` (opt-in AI
  synthesis), `logrotate.py` (monthly archive), and `dashboard.py` (a static HTML page + a live server).
  These are **read-only and file-only** — they read paper DBs (SQLite read-only), watchdog state, logs,
  and report files; never the broker. See [reporting-and-dashboard.md](reporting-and-dashboard.md).

## How the orchestrator drives modules

The orchestrator runs each module **in place, by subprocess**, using paths from config — it never edits
a module's code or config, and never imports its engine. The boundary is strict: it only ever invokes the
**paper** engine / paper DB, and **never places, cancels, adjusts, or closes an order** and never flips a
module's live-trading flag. Its one live-adjacent action is onboarding config (`connect`/`account`), which
delegates to the module's own credential tool — see [guardrails-and-modes.md](guardrails-and-modes.md).

### Per-schema dispatch

Each module's paper DB has a different schema, selected by `paper.trade_schema` in the orchestrator config:

| `trade_schema` | Module | Table | Closed-trade rule |
|---|---|---|---|
| `meic_ic` | MEIC | `ic_trades` | `exit_time` set; net = `pnl − fees`; tag = `risk_profile`. |
| `earnings` | Earnings | `trades` | `closed_at` set; net = `pnl − entry_cost − exit_cost`; tag = `profile`. |

`report.py`, `calibrate.py`, and `trade_notifier.py` each carry a small reader/adapter registry keyed by
that value. **Add a schema by extending those registries, not the callers.**

## The managed home (`~/.cherrypick`)

All runtime state lives under a single per-user home, resolved by `cherrypick.core.home` and relocatable
wholesale with `$CHERRYPICK_HOME`:

```
~/.cherrypick/
  config.json              # orchestrator config
  config/<engine>.json     # per-module configs (meic.json, earnings.json)
  data/<module>/           # paper + live SQLite DBs, streamer cache
  logs/                    # suite logs + eod-digest / eod-insight
  logs/<module>/           # per-module logs + paper-eod / eod-analysis
  logs/archive/<YYYY-MM>/  # monthly zipped reports + rotated logs
  state/                   # watchdog state, heartbeats
  dashboard.html           # the static dashboard render
```

Nothing runtime lands in a source checkout. A relative module `path` in config (e.g. `../meic`) is
resolved against the config file's directory / the source anchor, not the home. See
[configuration-and-storage.md](configuration-and-storage.md).

## Optional dev/AI tooling (off every runtime path)

`graphify` and `agentmemory` are local authoring aids; their artifacts (`graphify-out/`, most of
`.claude/`) are gitignored and are never a runtime dependency. The one tracked exception is
`.claude/commands/` — checked-in slash commands are shared dev conveniences (e.g. `/serve-dashboard`).
The AI EOD **insight** (`cherrypick eod-insight`) is the single place AI is invoked in the product, and it
is deliberately fenced off the reliability path — see
[reporting-and-dashboard.md](reporting-and-dashboard.md) and
[guardrails-and-modes.md](guardrails-and-modes.md).
