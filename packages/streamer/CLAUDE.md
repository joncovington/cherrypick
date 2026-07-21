# cherrypick-streamer — Operational Instructions

> Build commands and guidelines for the **streamer** package — the suite's standalone market-data
> daemon. Suite-wide context is the root [documentation index](../../docs/README.md); the design and
> rationale for splitting the streamer out of MEIC live in
> [docs/streamer-package-plan.md](../../docs/streamer-package-plan.md).

## What this is

A tiny **infrastructure** package: a long-lived daemon that runs the shared
`cherrypick.core.streamer.ChainStreamer` engine and keeps the **canonical shared stream cache**
(`~/.cherrypick/data/marketdata/stream_cache.db`) fresh. It exists so market data is owned by
infrastructure rather than by a trading module — any single installed consumer (flies, gex, MEIC's
readers) can price off live quotes with no MEIC streamer required. It places no orders and never touches
live trading.

It is the generic daemon **lifecycle only** — PID guard, `--status`/`--stop`, logging — around the shared
engine. It deliberately carries **none** of MEIC's trading policy: no ORB capture, no open-position leg
subscriptions, no account REST poller, no `127.0.0.1:7699` HTTP API. Those stay in MEIC's wrapper
(`../meic/src/streamer.py`); a live-trading module layers them onto the same engine.

## Commands

```bash
# Fresh clone: pull the cherrypick-core submodule (src/_core) first — imports fail without it.
git submodule update --init

python run.py               # run the streamer daemon in the foreground (Ctrl-C / SIGTERM to stop)
python run.py --status      # print one JSON health object (running, pid, oldest_event_age_s, ...) and exit
python run.py --stop        # SIGTERM a running daemon
python run.py --symbol SPX --symbol XSP   # override the configured symbols for this run
python run.py --secrets-set    # store the shared tastytrade OAuth bearer secrets (hidden input) in the keyring
python run.py --secrets-status # print which shared OAuth secrets are present (JSON)
python -m pytest            # lifecycle tests; no broker/streamer required (temp $CHERRYPICK_HOME)
ruff check . && ruff format .             # line-length 110; src/_core excluded
```

Config: copy `config.example.json` → `config.json` (git-ignored, machine-local), or place
`~/.cherrypick/config/streamer.json`. Paths resolve under the shared cherrypick home — never hardcode
absolute paths.

## Architecture

- **src/registry.py** — the **subscription registry**. Each consumer module writes one file,
  `~/.cherrypick/state/stream_requests/<module>.json`; the streamer reads the **union** and streams
  exactly that. `symbols` are underlyings (spot + ATM window + GEX + opening range). `leg_sources` are
  `{db, query}` specs — the streamer opens each DB **read-only** and runs the module's `SELECT` every
  poll, treating each non-null result cell as an extra streamer-symbol to keep subscribed beyond the ATM
  window. That is how MEIC keeps its open IC legs fresh (its leg symbols are stored `ic_trades` columns);
  any module points the streamer at its own DB the same way. (`legs` is an optional explicit static list
  for a module that would rather name symbols than query.) The streamer only ever *reads* these files and
  opens the declared DBs read-only; a consumer writes only *its own* file. This is the coupling surface —
  data + the module's own SQL, not code — so no package imports another.
- **src/config.py** — config loading + path resolution. Owns the **canonical cache** default
  (`data/marketdata/stream_cache.db`, a neutral scope owned by no trading module), the operator *base*
  symbols (a seed the registry union adds to), and the log/PID paths, all via `cherrypick.core.home`.
  `source.stream_cache_db` overrides the cache path.
- **src/daemon.py** — the daemon: the keyring session factory, `build_streamer` (the `ChainStreamer`
  wiring driven by the registry union — underlyings at startup, dynamic legs via the engine's
  `extra_subscriptions`/`protected_symbols` hooks), the PID single-instance guard, logging with rotation,
  the `status()` / `stop()` helpers, and the foreground `run_daemon`. The one place this package
  authenticates / talks to the broker.
- **src/credentials.py** — keyring entry for the suite's shared tastytrade OAuth **bearer** secrets
  (`client_secret`, `refresh_token`) under the shared `meicagent` service. The streamer needs only these
  two — it never makes an account-scoped call, so no `account_number`. `cherrypick connect` delegates
  bearer-secret entry here for a streamer-only (no-MEIC) install; writes only the keyring, never the
  broker.
- **src/cli.py + run.py** — the CLI. Flat args (`--status` / `--stop` / `--symbol` / `--secrets-set` /
  `--secrets-status`, default = run) so the orchestrator drives it with the same start/status/stop argv
  contract it uses for MEIC's streamer.
- **src/_core** — the shared `cherrypick-core` submodule (same URL + pinned SHA as every sibling); the
  streaming engine (`core.streamer`), cache schema (`core.streamcache`), auth (`core.auth`), and home
  resolver (`core.home`) all live there. Excluded from ruff and the wheel.

## Invariants (do not violate)

- **Exactly one producer writes the cache at a time.** This daemon and MEIC's streamer both write the
  same canonical cache; running both means two writers and two DXLink connections into one account. The
  PID single-instance guard plus the orchestrator only ever starting one producer are what enforce this —
  do not add a second writer path.
- **Only the daemon talks to the broker.** The `--status`/`--stop` paths read files and the PID only;
  `--secrets-set`/`--secrets-status` touch only the OS keyring. None of them open a broker session, and
  each emits a single JSON object on stdout. No MCP/AI on any path.
- **Credentials live in the OS keyring only** (Windows Credential Manager / macOS Keychain / Linux Secret
  Service) under the shared `meicagent` service — never files, env vars, or logs. The streamer stores
  only the two bearer secrets; account selection is a trading module's concern, not the streamer's.
- **`--status` prints one merged JSON object.** `running`/`pid` and the staleness/connection fields
  (`oldest_event_age_s`, `stale_age_s`, `connected_since`) go in the **same** object — the orchestrator's
  `util.first_json` parses the whole buffer, so a second JSON line would be dropped. Keep it one object.
- **The streaming engine stays in `cherrypick.core`.** Do not fork `ChainStreamer` or the cache schema
  into this package — the whole point is one shared engine (the GEX math drifted ~75× once when copied).
- **No trading policy here.** ORB, open-position leg subscriptions, REST polling, and any HTTP API belong
  to a trading module's wrapper, not this infrastructure daemon.
- **Instruction files hold no code and no logs.** This file is build commands + guidelines only. Scratch
  work lives in a git-ignored `.tmp/`.
- **Portable paths, masked accounts, human-voice docs/commits.** Never hardcode absolute paths, usernames
  (except `127.0.0.1`/`localhost`), or drive letters; derive from `Path(__file__)`, an env var, or
  config. Mask account numbers to `****1234`. No AI/co-author attribution in commits.
