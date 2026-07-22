# Plan: a standalone streamer package (`packages/streamer`)

Status: **proposed** — implementation plan for review, no code written yet.

## Why

The suite has grown several consumers of live DXLink market data, but only one *producer*: MEIC's
streamer daemon ([`packages/meic/src/streamer.py`](../packages/meic/src/streamer.py), 922 lines). It
writes `data/meic/stream_cache.db`, and everyone else reads that file **read-only**:

- **flies** — [`provider.py`](../packages/flies/src/provider.py) opens MEIC's cache `?mode=ro` and turns
  it into the snapshot every entry decision is priced from. It runs no streamer of its own
  ([`paper_loop.py:334`](../packages/flies/src/paper_loop.py#L334): *"no streamer of its own, so when the
  upstream one stalls we go blind"*).
- **gex** — in piggyback mode reads the same MEIC cache; in standalone mode runs its own thin wrapper
  ([`gex/src/streamer.py`](../packages/gex/src/streamer.py)) over the shared engine.

The cache is therefore owned by a **trading module**, not by **infrastructure**. Uninstall MEIC and the
producer vanishes — flies-only or gex-only installs have no data source. That breaks the goal of a
modular framework where any module can be installed and uninstalled independently.

This plan makes the streamer a first-class infrastructure package that any single installed module can
rely on, with no trading module in the picture.

## What's already true (so this is smaller than it looks)

The reusable *engine* was already extracted into the shared core; only the *daemon wrapper* is
MEIC-bound.

- `cherrypick.core.streamer.ChainStreamer` is generic — the WebSocket, listeners, cache writes, ATM
  window, and reconnect all live there, with MEIC policy injected via `extra_subscriptions`,
  `protected_symbols`, and `trade_hook`
  ([`core/streamer/__init__.py:58-73`](../packages/meic/src/_core/cherrypick/core/streamer/__init__.py#L58-L73)).
  gex's 58-line wrapper reusing it — zero MEIC imports — is the existence proof.
- Auth is centralized in `cherrypick.core.auth` under one keyring service (`"meicagent"`, legacy
  `"tastytrade-mcp"`). Any package can build a session with no dependency on MEIC's `session.py`.
- The cache schema lives in `cherrypick.core.streamcache`. The shared surface between producers and
  consumers is **the cache file + its schema**, both already in core.
- Reader-side indirection already exists: flies
  ([`paper_loop.py:61-68`](../packages/flies/src/paper_loop.py#L61-L68)) and gex both read
  `source.stream_cache_db` from config and only *default* to MEIC's path.

The only thing missing is a **package that owns the daemon lifecycle** — the generic machinery currently
trapped inside MEIC's wrapper. The engine stays in core (it's a library); a runnable daemon with an
install footprint becomes its own package (it doesn't belong in the vendored submodule).

## Decisions locked for this plan

1. **Option B** — a standalone `packages/streamer` infrastructure package, not per-module streamers
   (per-module streamers would mean N DXLink connections into one account and N caches; the design keeps
   exactly one producer running at a time).
2. **Canonical shared cache path** — one path, `data/marketdata/stream_cache.db`, that the single
   producer writes and every consumer reads read-only. **Done** (see "Canonical cache path" below).
3. **Sole producer (B1), superseding the earlier B2 plan** — the standalone `packages/streamer` daemon
   is the market-data producer **always**, whether or not MEIC is installed. MEIC no longer runs its own
   `ChainStreamer`. This was the original "deferred B1"; a later decision (2026-07-20) brought it forward
   because a single always-on producer is the cleaner modular endgame and avoids two streamer wrappers.
4. **A subscription registry** — each module declares what it needs from the cache by writing one file,
   `~/.cherrypick/state/stream_requests/<module>.json` (per-module JSON, chosen over a shared DB table so
   the cache stays producer-write-only). The streamer streams the union. This replaces the old coupling
   where MEIC's streamer read MEIC's `ic_trades` DB directly.
5. **MEIC keeps a thin sidecar** — MEIC's `streamer.py` stops running `ChainStreamer` but retains its
   non-market-data helpers (the REST account poller and the 7699 HTTP API) as a MEIC-owned process that
   *reads* the shared cache. ORB generalizes into the streamer (it isn't MEIC-specific). Open-position
   legs move to the registry.

## Target architecture

```
                    cherrypick.core.streamer.ChainStreamer   (engine — shared, unchanged)
                                        ▲
                                        │
                              packages/streamer  ◀── reads ── ~/.cherrypick/state/stream_requests/*.json
                       (SOLE producer: lifecycle + engine +                    ▲   ▲   ▲
                        registry union + generalized ORB)          writes its own file (symbols [+legs])
                                        │                                      │   │   │
                                        ▼                                    meic flies gex
                        data/marketdata/stream_cache.db  (canonical cache — ONE writer, ever)
                                        ▲
             ┌──────────────────────────┼───────────────────────────┐
          flies (ro)                  gex (ro)              MEIC readers (ro) + MEIC sidecar
                                                            (REST poller + 7699 API, read the cache)
```

**Producer resolution:** there is now exactly **one** producer — the `packages/streamer` daemon — in
every install. The orchestrator installs and watchdogs it whenever any data-consuming module is present;
no MEIC-vs-standalone branch. The single-writer guarantee is structural (only one daemon exists to run),
not a per-install choice.

**Reliability sequencing (load-bearing):** making MEIC's live loop depend on an external producer
reintroduces the failure *shape* behind the 34-hour stall — so MEIC only cuts over **after** the
orchestrator watchdogs the standalone streamer (the stale-restart in "Orchestrator integration"). MEIC's
existing stale-cache detection (`stale_warning` → REST fallback) plus that watchdog is what makes the
dependency safe; do not flip MEIC before both are in place.

## The subscription registry

`packages/streamer/src/registry.py` ([done](../packages/streamer/src/registry.py)). Each consumer writes
one file `~/.cherrypick/state/stream_requests/<module>.json`:

```json
{
  "symbols": ["SPX", "XSP"],
  "leg_sources": [
    {"db": "~/.cherrypick/data/meic/meic_trades.db",
     "query": "SELECT put_symbol, call_symbol, long_put_symbol, long_call_symbol FROM ic_trades WHERE status IN ('pending','open','partial','partial_entry')"}
  ]
}
```

- **`symbols`** — underlyings the module needs (spot + ATM option window + GEX + opening range). Static-ish
  (from config); a new underlying is picked up at daemon **start** (it needs a chain window built), so
  adding one restarts the producer.
- **`leg_sources`** — the general, configurable form of "keep my open positions' legs subscribed." Each is
  a `{db, query}` pair; the streamer opens `db` **read-only** (`?mode=ro`) and runs the module's `SELECT`
  **each subscription poll**, treating every non-null result cell as a streamer-symbol to keep subscribed
  beyond the ATM window (fed through the engine's `extra_subscriptions`/`protected_symbols` hooks). This
  restores MEIC's "streamer reads `ic_trades`" behavior — its leg symbols are stored columns — but
  **without the streamer importing MEIC or hardcoding its schema**: MEIC declares the query in its own
  file, and any module points the streamer at its own DB the same way. It is safer than MEIC's old code,
  which opened the trades DB read-write. Dynamic — a position opening/closing is picked up with no restart.
- **`legs`** — optional explicit static list, for a module that would rather name symbols than query.

The streamer reads the union (seeded by its own config `symbols`, normally empty); it only ever *reads*
these files and opens declared DBs read-only, and a consumer only ever writes *its own* file — so the
cache stays producer-write-only. A corrupt/half-written file, a missing/locked DB, or a non-`SELECT`
query each contribute nothing and are never fatal. The reference reader+writer live in the streamer
package; consumer-side writers are a thin equivalent (candidate to consolidate into `cherrypick.core`
later — avoided now to skip a submodule bump).

## The new package: `packages/streamer`

Layout mirrors the other packages (its own `CLAUDE.md`, `run.py`, `src/_core` submodule, config):

```
packages/streamer/
  CLAUDE.md                 # build/architecture/invariants for this package
  run.py                    # launcher → cherrypick.streamer.cli:main (self-bootstraps src/_core)
  config.example.json       # symbols, cache path, window params
  src/
    _core/                  # vendored cherrypick-core submodule (same URL + SHA as siblings)
    streamer/
      __init__.py
      daemon.py             # the daemon: lifecycle + ChainStreamer wiring
      cli.py                # --status / --stop / --symbol / (default) run
```

`daemon.py` is essentially gex's wrapper (build a keyring session factory, construct
`ChainStreamer(session_factory, db_path, symbols)`, run it) **plus** the daemon lifecycle gex lacks. The
lifecycle is lifted verbatim-in-spirit from MEIC's generic machinery — the subagent confirmed these are
already cleanly separable from MEIC policy:

| Machinery to lift (generic) | Source in MEIC today |
|---|---|
| PID-file / single-instance guard | [`streamer.py:782-817`](../packages/meic/src/streamer.py#L782-L817) |
| `--status` JSON (`running`, `pid`, `oldest_event_age_s`, `stale_age_s`, `connected_since`, `last_event_at`) | [`streamer.py:820-858`](../packages/meic/src/streamer.py#L820-L858) |
| `--stop` (SIGTERM to PID) | [`streamer.py:861-870`](../packages/meic/src/streamer.py#L861-L870) |
| Logging + rotation | [`streamer.py:73-93`](../packages/meic/src/streamer.py#L73-L93) |
| Symbol resolution (CLI > `symbols` > default) | [`streamer.py:873-882`](../packages/meic/src/streamer.py#L873-L882) |
| Main-loop skeleton (signal wiring, PID write/unlink, `await streamer.run_async()`) | [`streamer.py:738-775`](../packages/meic/src/streamer.py#L738-L775) (generic parts) |

**Explicitly NOT lifted** (stays in MEIC — it's live-trading policy flies/gex don't need): ORB capture
(`_OrbTracker`, 227-267), open-position leg subscriptions reading `ic_trades` (146-181), the account REST
poller (282-340), and the 7699 HTTP API server (351-731).

The lifted `--status` schema must match byte-for-byte what the watchdog already parses, so the daemon is a
drop-in for the orchestrator's existing streamer contract.

## Canonical cache path

- Resolve via `cherrypick.core.home.data_dir("marketdata") / "stream_cache.db"` so it moves with
  `$CHERRYPICK_HOME` like everything else.
- **MEIC change (one line):** point MEIC streamer's `db_path` at the canonical path instead of
  `data/meic/stream_cache.db`. Its `--status` staleness query reads the same generic `stream_*` tables, so
  nothing else in MEIC's wrapper changes.
- **Reader change (config only):** default `source.stream_cache_db` for flies and gex to the canonical
  path. Because it's one fixed path regardless of producer, readers never need per-install rewriting.
- **Migration:** on first install after the cutover, the old `data/meic/stream_cache.db` is simply
  abandoned (the streamer recreates its schema on start — [`core/streamer/__init__.py:351`](../packages/meic/src/_core/cherrypick/core/streamer/__init__.py#L351)). No data migration needed; the cache is ephemeral live state.

## Authentication & standalone credential bootstrap

A streamer-only box (no MEIC, no earnings) must be able to store the suite's tastytrade OAuth so the
daemon can authenticate. How auth works today:

- The daemon builds its session from `cherrypick.core.auth`: `CredentialStore("meicagent",
  legacy=("tastytrade-mcp",))` → `SessionManager(store, thread_local=True).get_session`
  ([daemon.py](../packages/streamer/src/daemon.py)). OAuth2 via the tastytrade SDK; tokens auto-refresh
  from the long-lived refresh token. Secrets live only in the OS keyring — never files/env/logs.
- Two facts that shape the bootstrap:
  1. **The streamer needs only the two bearer secrets** (`client_secret`, `refresh_token`). It never
     makes an account-scoped call, so `account_number` (account selection) is *not* required — that's a
     trading module's concern.
  2. **The orchestrator's `connect` deliberately never sees bearer secrets** — its invariant is that it
     *delegates* entry to a module's own `secrets_set` tool and only ever writes `ACCOUNT_NUMBER` itself.
     So "connect owns credential entry" (the chosen option) means **connect delegates to the streamer's
     own tool**, not that the orchestrator starts handling bearer secrets.

**Done (streamer side, self-contained):** the streamer package has its own credential tool —
`run.py --secrets-set` / `--secrets-status` ([credentials.py](../packages/streamer/src/credentials.py)),
a thin wrapper over `CredentialStore("meicagent", …)` that stores only the two bearer secrets under the
shared service. So a streamer-only box can be onboarded directly, and the entry is interchangeable with
MEIC/earnings/gex (same keyring entry).

**Remaining (orchestrator side):** make `cherrypick connect` able to delegate to the streamer. Today
`connect` hardcodes `src/tt.py secrets_set` and requires a `modules.<name>` entry + checkout on disk
([connect.py](../packages/orchestrator/src/cherrypick/orchestrator/connect.py)). To target the streamer:

- Make the delegated credential command **config-driven** (e.g. a `credential_argv`, default
  `["src/tt.py"]`) so the streamer can set `["run.py"]` and connect invokes
  `[*credential_argv, "--secrets-set", …]`.
- Let a connect target **skip account selection** when it declares no account need (the streamer never
  trades) — a natural fit for connecting the streamer as the suite's shared login.

This lands in the orchestrator-integration step below; the streamer half already unblocks a manual
`python run.py --secrets-set` onboarding.

## Orchestrator integration

Two changes, one config + one small code.

### 1. Producer as a first-class managed entry (config)

Today the streamer is a sub-block of `modules.meic` and only gets stale-restart treatment inside
`_check_meic`. Repoint it at the standalone package as a top-level managed producer whose `path` selects
the run directory (resolution is purely `path`-driven —
[`config.py:106-119`](../packages/orchestrator/src/cherrypick/orchestrator/config.py#L106-L119)):

- producer `path = ../streamer`, argv `run.py` / `run.py --status` / `run.py --stop`.

There is **no** MEIC-vs-standalone branch — the standalone daemon is the producer in every install, so
the orchestrator installs and watchdogs it whenever any data-consuming module is present. (MEIC's
remaining sidecar — REST poller + 7699 API — is a separate managed process, not the producer.)

### 2. Stale-restart for the producer (code)

The generic `services[]` path only checks the `running` bool; the richer stale-age restart lives only in
`_check_meic`. The helpers it uses — `_streamer_stale_age` / `_streamer_connection_age`
([`watchdog.py:79-111`](../packages/orchestrator/src/cherrypick/orchestrator/watchdog.py#L79-L111)) — are
already generic. Extend the managed-producer check to call them so the standalone streamer gets the same
"stalled → stop → restart" behavior MEIC's does today
([`watchdog.py:216-293`](../packages/orchestrator/src/cherrypick/orchestrator/watchdog.py#L216-L293)).
No change to the launch mechanics (`_start_streamer` / `_stop_streamer` already shell out via `path` +
argv and honor the single-instance guard).

## Symbol discovery — via the registry (not install-time config)

Superseded by the subscription registry above: each module writes its own
`stream_requests/<module>.json` and the streamer reads the union at runtime. No orchestrator-computed
config union, and no daemon crawling sibling configs. Adding/removing a module updates its own file (and
a new *underlying* triggers a producer restart to build its chain window); `doctor` should still warn if
an installed consumer's registered symbols aren't being streamed.

## MEIC as a sidecar (B1), concretely

MEIC stops being a producer and becomes a consumer + a thin sidecar:

- **Producer** → the standalone `packages/streamer` daemon, always. MEIC's `streamer.py` no longer runs
  `ChainStreamer`.
- **MEIC's four old streamer subsystems:**
  - *generic chain/quote/greeks/OI cache* → produced by the standalone streamer (MEIC reads it).
  - *open-position leg subscriptions* → MEIC writes a `leg_sources` entry (its `ic_trades` query) to
    `stream_requests/meic.json` once at startup; the streamer runs that query read-only each poll to keep
    the open legs subscribed. This restores the "streamer reads `ic_trades`" behavior generically — the
    query is MEIC's, executed by the streamer, so no code coupling.
  - *ORB capture* → generalized into the streamer as an opening-range feature over registered symbols,
    written to the cache; MEIC reads it (its `get_orb_range` reads the cache exactly as today).
  - *REST account poller + 7699 HTTP API* → stay in MEIC's `streamer.py`, now a **sidecar** that runs
    those two (reading the shared cache) but not the engine. A separate orchestrator-managed process.
    Neither generalizes the way `leg_sources` did (decided 2026-07-20 — "keep as sidecar for now"):
    - The **REST poller** is *account state, not market data* — account-scoped and live-adjacent. Moving
      it into the shared producer would make a paper-only (flies) install's streamer touch account/live
      data, breaking the "shared producer is pure market-data" principle and paper↔live isolation. It
      stays MEIC-side, full stop.
    - The **7699 API** is two things: `get_strategies` (MEIC IC-building — always MEIC-side) and a
      *warm quote/chain fetch-on-miss* (the streamer answering from its live session for a symbol not in
      the window). The warm-fetch is the one genuinely reusable core — it fits the infra streamer's role
      — but no second consumer wants it today (flies/gex deliberately **refuse** on stale rather than
      live-fetch), so building it now would be speculative. **Deferred reuse path:** when a second
      consumer needs live-fetch, add a streamer-native `quote(sym)`/`chain(sym)` endpoint (cache-first,
      warm-fetch fallback) and MEIC's 7699 server dissolves into it. Until then MEIC keeps the sidecar.
- **Reliability sequencing:** flip MEIC onto the shared producer only after the watchdog covers the
  standalone streamer (see Orchestrator integration) — MEIC's `stale_warning` → REST fallback plus that
  watchdog is what keeps the external-producer dependency off the "new failure mode" list.

## Guardrail compliance

- **No AI / network / MCP on the reliability path** — the daemon uses stdlib + `core.streamer` +
  `core.auth` (keyring + tastytrade session) only, exactly like MEIC's today. The watchdog path is
  unchanged in character (stdlib + OS shell). ✔
- **Paper ↔ live isolation** — the streamer is read-market-data-only; it places no orders and touches no
  live-trading flag. The orchestrator still drives it purely by subprocess. ✔
- **Managed home** — canonical cache under `~/.cherrypick/data/marketdata/`, relocatable with
  `$CHERRYPICK_HOME`. ✔
- **One core URL + pinned SHA** — the new package vendors the same submodule; no core change, no SHA bump.
  ✔
- **Human-voice docs/commits, masked accounts, portable paths** — apply as everywhere. ✔

## Implementation checklist (suggested order)

1. **Scaffold `packages/streamer`** ✓ — package skeleton, `run.py`, `src/_core` submodule, `CLAUDE.md`,
   `config.example.json`, `pyproject.toml`.
2. **Daemon core** ✓ — `daemon.py`: keyring session factory + `ChainStreamer` wiring writing the canonical
   path (the gex-equivalent core).
3. **Lifecycle** ✓ — PID guard, `--status`/`--stop`, logging, symbol resolution; `--status` emits ONE
   merged JSON object with the watchdog's parsed keys (`running`/`pid`/`oldest_event_age_s`/`stale_age_s`
   /`connected_since`). 8 tests pass, ruff clean.
3b. **Credential tool** ✓ — `credentials.py` + `run.py --secrets-set` / `--secrets-status` for the
   standalone bootstrap (see Authentication section). Streamer half done; connect delegation is in step 6.
5. **Canonical-path cutover** ✓ — MEIC's single resolver `paths.stream_cache_path()` → `marketdata`
   scope (moves writer + all MEIC readers in lockstep); flies default → canonical; gex stream-cache
   default → canonical (its spot-trail `history_db` stays gex-owned). All suites green: streamer 8,
   flies 185, gex 12, meic 290. CLAUDE.md path references corrected in meic/flies.
6. **Subscription registry (streamer side)** ✓ — `registry.py` (per-module JSON; `union_symbols` +
   `union_legs`, the latter running each module's read-only `leg_sources` `SELECT`) wired into
   `build_streamer`: underlyings from the union at startup, dynamic legs (pulled from module DBs) via the
   engine's `extra_subscriptions`/`protected_symbols` hooks. 20 streamer tests, ruff clean.
7. **Consumer registry writers** — flies ✓ + gex ✓ write their `symbols` best-effort at startup
   (`stream_request.py` in each; flies in `paper_loop.main`, gex in `cli.main`; gex gained a
   `managed_home` autouse conftest so tests can't write the real home). Cross-package union verified (two
   modules requesting SPX → one entry). MEIC's writer (`symbols` + its `ic_trades` `leg_sources` query,
   written once) lands with the sidecar refactor (step 8). Open: registry-file **lifecycle** — a stopped
   module's file lingers; handle via `uninstall` removal (step 9) and/or a streamer-side staleness guard.
8. **ORB generalization** ✓ — `streamer/src/orb.py` (`OpeningRangeTracker`, lifted from MEIC's
   `_OrbTracker`) wired as the engine's `trade_hook`; writes the shared cache's existing `orb_ranges`
   table (already in `core.streamcache` DDL — no core change), which MEIC's `get_orb_range` reads
   unchanged. 25 streamer tests, ruff clean. Fully additive, zero MEIC risk.
8b. **MEIC sidecar** ✓ — added a `--sidecar` mode to MEIC's `streamer.py`: REST poller + 7699 API only,
   no `ChainStreamer`. The REST poller now writes MEIC's own `rest_cache.db` (not the shared cache — the
   double-writer fix); the 7699 handler splits `_market_db` (shared cache, plain connect so WAL reads are
   fresh) from `_rest_db` (MEIC's rest cache); separate sidecar PID. Opt-in `meic-sidecar` service
   (disabled by default). 293 MEIC tests. NOT auto-started — post-cutover `tt.py` reads the cache directly
   so the quote fast-path was already intact; the sidecar's real value is caching account/market-overview
   REST calls, so it's opt-in for the live/interactive loop. (`_OrbTracker` and
   `_open_trade_streamer_symbols` are still present in the full-streamer path, kept for rollback; ORB is
   already generalized in the standalone streamer, and MEIC's `meic.json` `leg_sources` supersedes the
   open-leg reader.)

**⚡ THE CUTOVER WAS EXECUTED LIVE (2026-07-21, pre-open):** stopped MEIC's streamer, validated the
standalone streamer live (1982 symbols, all 7 underlyings, fresh), flipped the real config (top-level
`streamer` enabled, `modules.meic.streamer` disabled), repointed flies, and committed (`e1da682`). The
standalone streamer is the sole producer. See the memory note `streamer-package-extraction` for the exact
actions + rollback.
9. **Watchdog stale-restart (generalized)** ✓ — extracted the streamer silence-restart from `_check_meic`
   into a shared `_check_streamer_health(label, root, spec)` (behavior-preserving); added
   `_check_producer(cfg, in_session)` watchdogging a top-level `streamer` block, wired into `run()` but
   **dormant** (config block added to `config.example.json`, `enabled:false`). 11 new tests; full
   orchestrator suite green, ruff clean. No behavior change — MEIC still owns the streamer today.
10. **Orchestrator flip wiring (the cutover, still to do)** — enable the top-level `streamer` producer +
    set `modules.meic.streamer.enabled=false`; `install` starts/`uninstall` stops the producer; add
    MEIC's sidecar as a managed process. Make `connect`'s delegated credential command config-driven
    (`credential_argv`) so it can onboard the streamer, and let a connect target skip account selection.
    This is the coordinated flip with step 8b (MEIC sidecar refactor).
11. **Standalone acceptance (live smoke)** — run the daemon against the broker and confirm flies + MEIC
    price off the shared cache with the registry driving symbols/legs. User-supervised (market hours +
    keyring creds), per the suite's broker-cutover pattern.
12. **Docs** — update [`architecture.md`](architecture.md),
    [`configuration-and-storage.md`](configuration-and-storage.md), and each consumer's CLAUDE.md "Data
    source" section for the sole-producer + registry model.

## Risks & open questions

- **Concurrent DXLink limit** — the whole design assumes one producer at a time to stay under tastytrade's
  concurrent-streamer-connection limit. The install owner-resolution must be airtight. (Worth confirming
  the exact limit during build.)
- **Symbol-union staleness** — adding a module without re-running `install` leaves the producer streaming
  a stale symbol set. Mitigated by the `doctor` warning; consider whether the watchdog should also flag it.
- **`stream_status` table columns** — MEIC's `--status` line 2 dumps the `stream_status` row plus computed
  keys ([`streamer.py:834-856`](../packages/meic/src/streamer.py#L834-L856)); the lifted daemon must write
  the same table via `core.streamcache` so both producers present an identical status contract to the
  watchdog.
- **Install ordering** — `packages/streamer` should install as a dependency whenever any data-consuming
  module is present, even if that module (flies) is `self_healing` and self-registers its own poller task.
- **`architecture.md` currency** — its package table predates flies; fold flies + the streamer package in
  during step 9.
