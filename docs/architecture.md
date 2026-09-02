# Architecture

How the cherrypick suite is put together — the pieces, how they talk, and the boundaries that keep the
automation safe.

## The monorepo

One workspace holds the whole trading-tool suite as separate packages under `packages/`:

| Package | Role |
|---|---|
| `packages/core` | The shared **`cherrypick.core`** library — calendar, fees, profiles, GEX math, the streaming engine, auth, broker, db, viz, home resolution. Every other package installs it as an editable dependency; see below. |
| `packages/orchestrator` | The **orchestrator** (`cherrypick`): supervisor, watchdog, notifications, onboarding, and the file-side read commands (report / calibrate / EOD reports / archive). Drives the modules **by subprocess**, never by import. |
| `packages/meic` | The **MEIC** 0DTE multiple-entry iron-condor engine. |
| `packages/earnings` | The **Earnings** defined-risk earnings-play engine. |
| `packages/gex` | The **GEX** (gamma-exposure) engine and spot-trail recorder, built on the shared GEX math. It computes and records; the console renders it. |
| `packages/flies` | The **Flies** 0DTE net-credit butterfly ("profit forest") paper engine — deliberately built so a negative result is usable (floors measured after fees, arm-based experiments). |
| `packages/calendars` | The **Calendars** weekly SPX double-calendar paper engine — a forward exit-parameter experiment: control and path books over shared entry fills, a per-tick mark path, and a read-side exit-policy replay validated against the real books. Paper-only, credential-free; its 4DTE/7DTE chains come from the streamer's `expirations` request field. |
| `packages/pmcc` | The **PMCC-99** deep-ITM covered-call paper engine on TQQQ — an 85-90-delta ~21DTE long against an ATM ~7DTE short (no yield floor, either side of spot), holding to the short's own expiration before closing both legs. Single `control` book plus an advised A/B against the old early-tv-exit rule. Paper-only, credential-free; its deep strikes come from the streamer's `expirations` **and `window_hints`** request fields, and early assignment is measured (exposure telemetry), never modelled. |
| `packages/curve` | The **Curve** VXX call-credit-spread paper engine harvesting the VIX term-structure roll yield, gated by a daily VIX/VIX3M regime read. Three books differ only in entry gate and exit rule; the daily regime classification is recorded every session, RTH-gated and basis-stamped. Paper-only, credential-free. |
| `packages/bwb` | The **BWB** daily-laddered SPX put broken-wing-butterfly paper engine — one net-credit BWB per session at the expected move, ~7 DTE, held to expiry. Four books share the identical base structure and differ only in the reversal add-on trigger; a cohort-keyed trigger-tick path is recorded for read-side replay. Plus an opt-in call-side book at the GEX call wall. Paper-only, credential-free. |
| `packages/overview` | The **Overview** pre-open morning fact pack — index/vol/sector readings from the stream cache, gamma flip and walls from the suite's own GEX history, and a mechanical GREEN/YELLOW/RED phase from five declared gates. Missing data can never produce RED and always blocks GREEN. Credential-free, network-free, read-only. |
| `packages/streamer` | The **standalone streamer** — the suite's single market-data producer, writing the canonical shared stream cache that every module reads; modules declare their symbols via `state/stream_requests/`. |
| `packages/console` | The reactive **console** UI (Node + TypeScript, React SPA on `127.0.0.1:5070`): every module's read models plus the research and screening surfaces in one app. The suite's **only** read surface since 2026-08-12, and the supervisor keeps it running as an always-on resident job. Read-only over every other package's data, with its own store. It reads the shared suite credential and never writes credentials; it probes the token's scope at boot, so a read-only token disables its write-oriented functions. No order-placement code paths. |
| `packages/desk` | ⚠️ **Experimental.** The **manual trading desk** — the suite's only *discretionary* live-order path (MEIC, earnings, and flies each have a live loop behind their own `enable_live_trading` gate; this one has no loop at all). A foreground, human-initiated CLI for discretionary live orders, authorized entirely on its own (own config, own keyring PIN, per-order ticket) so placing one order never requires flipping a module's `enable_live_trading`. Not a strategy module: no loop, no schedule, no ledger. Never scheduled, and no automated package may import it. |

Each package has its own `CLAUDE.md` with build commands, tech-stack reference, and invariants.
`packages/console` is the one non-Python package; the rest share the src-layout described below.

## Shared library: `cherrypick.core` (an in-repo package)

Common logic — `cherrypick.core.calendar`, `.fees`, `.profiles`, `.gex`, `.streamer`, `.auth`, `.broker`,
`.db`, `.viz`, `.home` — lives in **`packages/core`**, a sibling package in this same monorepo,
consumed by every other package as a normal editable-installed dependency. This is why:

- A fresh clone needs one install step before anything else: `pip install -e packages/core` (or run
  `scripts/dev-install.ps1`/`.sh` from the repo root, which does that plus every package). Skip it and
  every `import cherrypick.core…` fails.
- Every package declares `cherrypick-core` as a plain named dependency in its `pyproject.toml`. It is
  **not** on PyPI (`Private :: Do Not Upload`) — pip only ever resolves the name from what's already
  installed, so `packages/core` must be installed first.
- There is no `sys.path` bootstrap for core anywhere in the suite — none should be reintroduced. The
  orchestrator's `doctor` fails loudly (`cherrypick.core: not installed`) if the install step was
  skipped, rather than surfacing as a confusing traceback deep in a detached subprocess.

The shared core is what lets the orchestrator's `report`/`calibrate` and a module's own engine agree on
fees, calendar dates, and profile attribution without copy-pasting logic. It was landed here via
`git subtree add` from the standalone `cherrypick-core` repo (full history preserved,
`git subtree split --prefix=packages/core` reproduces it exactly) — that repo is now archived, and this
monorepo is the source of truth.

## src-layout & the import namespace

**Every package shares one import root.** A module lives at `packages/<pkg>/src/cherrypick/<pkg>/<mod>.py`
and imports as `cherrypick.<pkg>.<mod>` — `cherrypick.meic.tt`, `cherrypick.flies.engine`,
`cherrypick.earnings.scanner`, and so on, alongside `cherrypick.core.*` and `cherrypick.orchestrator.*`.

`src/cherrypick/` has **no `__init__.py`** in any package. That is what makes it a PEP 420 namespace, so
all seven distributions compose under one `cherrypick.*` root instead of colliding. One level deeper,
`src/cherrypick/<pkg>/__init__.py` **does** exist — that is an ordinary package.

The packages were flat (`src/tt.py`, `src/db.py`, …) until this was unified. Fifteen top-level names
collided across packages — `credentials` in four, `cli` / `section` / `stream_request` in three each,
plus `db`, `paths`, `tt`, `streamer`, `dashboard`, `config`, `session` — so **two packages could never
be imported into one process.** Subprocess isolation is still the right call (crash isolation,
independent lifecycle, paper↔live fencing), but it is now a *decision* rather than something the
layout forced.

The practical consequence is how everything is invoked: **`-m cherrypick.<pkg>.<mod>`**, never a file
path. Scheduled-task command lines used to bake in `os.path.abspath(__file__)`, so moving a file
stranded a registered OS task; `-m` is location-independent. The orchestrator's config argv follows the
same form (`["-m", "cherrypick.meic.paper_loop", "--once"]`).

> **Gotcha:** the launcher is `run.py`, **not** `cherrypick.py`. A root module named `cherrypick.py`
> would shadow the `src/cherrypick` namespace package (a regular module outranks a PEP 420 namespace on
> `sys.path`). Scheduled tasks invoke `run.py`.

## Two halves, one config

Everything hangs off one config file per package. The orchestrator's `config.json`
(`orchestrator/config.py`) splits into two halves:

- **Write side — the reliability guarantee.** The **supervisor daemon** (`orchestrator/supervisor.py`,
  kept alive by the single `cherrypick-supervisor` OS task since the 2026-08-09 cutover) derives every
  recurring job from config each pass (`orchestrator/jobspec.py`, ET/DST-correct) and spawns them as
  short-lived headless ticks; `orchestrator/watchdog.py` runs as its 10-minute job, checks each
  module's paper pipeline (job present, data fresh in-session, streamer alive, earnings SLA met) plus
  the supervisor and its anchor, logs findings, and pushes alerts through `notify/notifier.py`. It has
  a dedup / re-notify / recovery state machine (`state/watchdog_state.json`). This whole path uses
  **only the stdlib + the OS shell + local files** — no network, no AI — so it has no new failure
  mode. (A 34-hour silent stall from an external streamer dependency is why that rule is
  load-bearing.)
- **Read side — look whenever you want.** `report.py` (cross-module paper P&L), `calibrate.py` (per-profile
  promotion advisor) and `logrotate.py` (monthly archive), over the shared per-schema ledger readers in
  `cherrypick.core.ledgers`. End-of-day reporting moved out to **`packages/review`** on 2026-08-13 —
  one versioned fact set per session across every module, which every surface renders rather than
  re-deriving. The page that composes them is the console.
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
| `fly_book` | Flies | `fly_positions` | settled rows; net after the modeled fee stack; tag = experiment *arm* (not a risk profile). |

The canonical schema set lives in `schemas.SCHEMAS`, and coverage is enforced by a test
(`tests/test_schema_registry.py`), not prose: every surface registry (`report.py`, `reconcile.py`,
`trade_notifier.py`, `eval_activity.py`) must account for every schema — with a reader or an explicit
not-applicable declaration. **Add a schema by adding it to `schemas.SCHEMAS` and extending each
surface**; a schema wired into some surfaces but not others fails CI instead of vanishing silently.

## The managed home (`~/.cherrypick`)

All runtime state lives under a single per-user home, resolved by `cherrypick.core.home` and relocatable
wholesale with `$CHERRYPICK_HOME`:

```
~/.cherrypick/
  config.json              # orchestrator config
  config/<engine>.json     # per-module configs (meic.json, earnings.json)
  data/<module>/           # paper + live SQLite DBs, streamer cache
  logs/                    # suite logs
  logs/<module>/           # per-module logs
  data/review/             # eod-<day>.json fact sets + their renders
  logs/archive/<YYYY-MM>/  # monthly zipped reports + rotated logs
  state/                   # watchdog state, heartbeats
```

Nothing runtime lands in a source checkout. A relative module `path` in config (e.g. `../meic`) is
resolved against the config file's directory / the source anchor, not the home. See
[configuration-and-storage.md](configuration-and-storage.md).

## Optional dev/AI tooling (off every runtime path)

`graphify` and `agentmemory` are local authoring aids; their artifacts (`graphify-out/`, most of
`.claude/`) are gitignored and are never a runtime dependency. The one tracked exception is
`.claude/commands/` — checked-in slash commands are shared dev conveniences (e.g. `/console`).
**No AI is invoked from any suite package.** The EOD narrative is generated outside them, by a
scheduled agent reading `packages/review`'s fact set, and the AI advisor's model call lives in
`scripts/advisor_checkpoint.py` behind the same fence — `packages/advisor` holds only its
deterministic half (the fact packs, the reply validation, and the paper A/B experiments its admitted
proposals run as). No package holds an API key or a network dependency — see
[reporting-and-dashboard.md](reporting-and-dashboard.md) and
[guardrails-and-modes.md](guardrails-and-modes.md).
