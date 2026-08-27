# cherrypick-streamer — Operational Instructions

> Build commands and guidelines for the **streamer** package — the suite's standalone market-data
> daemon. Suite-wide context is the root [documentation index](../../docs/README.md); the design and
> rationale for splitting the streamer out of MEIC live in
> [docs/streamer-package-plan.md](../../docs/history/streamer-package-plan.md).

## What this is

A tiny **infrastructure** package: a long-lived daemon that runs the shared
`cherrypick.core.streamer.ChainStreamer` engine and keeps the **canonical shared stream cache**
(`~/.cherrypick/data/marketdata/stream_cache.db`) fresh. It exists so market data is owned by
infrastructure rather than by a trading module — any single installed consumer (flies, gex, MEIC's
readers) can price off live quotes with no MEIC streamer required. It places no orders and never touches
live trading.

It is the daemon **lifecycle** — PID guard, `--status`/`--stop`, logging — plus the subscription registry
and the opening-range hook, around the shared engine. It deliberately carries **none** of MEIC's
*trading* policy: no open-position leg subscriptions of its own, no account REST poller, no
`127.0.0.1:7699` HTTP API. Those stay in MEIC's wrapper
(`../meic/src/cherrypick/meic/streamer.py`); a live-trading module layers them onto the same engine.

Opening-range capture **is** here (`orb.py`), lifted from MEIC's streamer when this became the sole
producer. It is not an exception to that rule: it watches Trade ticks and writes a generic
`orb_ranges` table any consumer reads, and it belongs to the always-on producer because a consumer's
cadence is not guaranteed to land inside the 09:30–09:35 window.

## Commands

```bash
python run.py               # run the streamer daemon in the foreground (Ctrl-C / SIGTERM to stop)
python run.py --status      # print one JSON health object (running, pid, oldest_event_age_s, ...) and exit
python run.py --stop        # SIGTERM a running daemon
python run.py --symbol SPX --symbol XSP   # override the configured symbols for this run
python run.py --secrets-set    # store the shared tastytrade OAuth bearer secrets (hidden input) in the keyring
python run.py --secrets-status # print which shared OAuth secrets are present (JSON)
python -m pytest            # lifecycle tests; no broker/streamer required (temp $CHERRYPICK_HOME)
ruff check . && ruff format .             # line-length 110
```

Config: copy `config.example.json` → `config.json` (git-ignored, machine-local), or place
`~/.cherrypick/config/streamer.json`. Paths resolve under the shared cherrypick home — never hardcode
absolute paths.

## Architecture

- **`cherrypick/streamer/registry.py`** — the **subscription registry**. Each consumer module writes one file,
  `~/.cherrypick/state/stream_requests/<module>.json`; the streamer reads the **union** and streams
  exactly that. `symbols` are underlyings (spot + ATM window + GEX + opening range). `leg_sources` are
  `{db, query}` specs — the streamer opens each DB **read-only** and runs the module's `SELECT` every
  poll, treating each non-null result cell as an extra streamer-symbol to keep subscribed beyond the ATM
  window. That is how MEIC keeps its open IC legs fresh (its leg symbols are stored `ic_trades` columns);
  any module points the streamer at its own DB the same way. (`legs` is an optional explicit static list
  for a module that would rather name symbols than query.) The streamer only ever *reads* these files and
  opens the declared DBs read-only; a consumer writes only *its own* file. This is the coupling surface —
  data + the module's own SQL, not code — so no package imports another. The **symbol/window-hint union**
  is delegated to `cherrypick.core.streamrequests` rather than implemented here: the orchestrator unions
  the same files to decide whether a *running* producer's subscriptions have gone stale (underlyings bind
  once, at startup — see its `servicecfg`), and two implementations of "what did every module ask for"
  would recycle this daemon over a difference it never sees. `union_legs` stays local — legs are re-read
  every poll from module-declared DBs, this package's own sqlite concern.

  **A `window_hint` is a `(below, above)` span, not a width.** A module may declare a plain count
  (symmetric — what every module but pmcc declares) or `{"down": N, "up": M}`; both normalize
  through `streamcache.window_span`, and the union takes the max **per side**, so two modules with
  opposite needs on one symbol are both served rather than the wider single number winning. The
  configured `window_strike_count` floors both sides, so a directional hint only ever asks for more
  on one side and can never narrow the base on the other. This exists because pmcc's deep-ITM long
  sits far below spot while its short sits at it: a symmetric count bought an identical block of
  strikes above spot that no module could read, and on 2026-08-24 that block was the largest single
  waste in the suite's subscription budget (`docs/streamer-subscription-budget.md`).

  **A confirmed session value is never erased by a later event that omits it.** The
  `stream_summary` upsert COALESCEs every OHLC field against what is already stored. It did not
  until 2026-08-27, and the cost was 22 consecutive sessions of SPX and XSP closes: both kept
  receiving Summary events until ~20:07 ET with `day_close_price` cleared, and the bare overwrite
  copied that null over the settled value. `daily_closes` — the suite's only multi-year series —
  froze at 2026-07-28 for SPX while every other symbol stayed current, because symbols whose last
  event of the day landed earlier (VIX 10:08, SPY 16:15) never met the clearing event. A close does
  not un-happen; a value only ever gets MORE known through a session.

  **Quote and Greeks are filtered by what a symbol can PUBLISH, not by what it is.** `build_streamer`
  asks `streamcache.publishes_quotes` / `publishes_greeks` per leg: nothing cash-settled has greeks,
  and an index has no order book to quote from (the 2026-08-24 entitlement probe — SKEW, VIX9D, VIX
  and VIX1D all printed Trade, none printed Quote). **ETF and single-name legs keep Quote**; modules
  price legs off their real book. The quoteless set is a declared list rather than a pattern match
  on the ticker, because a leg with no price at all is the expensive failure here (2026-08-14
  Summary, 2026-08-17 Trade) and an unlisted symbol must default to paying for a possibly-wasted
  subscription rather than to starving a reader.
- **`cherrypick/streamer/config.py`** — config loading + path resolution. Owns the **canonical cache** default
  (`data/marketdata/stream_cache.db`, a neutral scope owned by no trading module), the operator *base*
  symbols (a seed the registry union adds to), and the log/PID paths, all via `cherrypick.core.home`.
  `source.stream_cache_db` overrides the cache path.
- **`cherrypick/streamer/daemon.py`** — the daemon: the keyring session factory, `build_streamer` (the `ChainStreamer`
  wiring driven by the registry union — underlyings at startup, dynamic legs via the engine's
  `extra_subscriptions`/`protected_symbols` hooks), the PID single-instance guard, logging with rotation,
  the `status()` / `stop()` helpers, and the foreground `run_daemon`. The one place this package
  authenticates / talks to the broker.
- **`cherrypick/streamer/credentials.py`** — keyring entry for the suite's shared tastytrade OAuth **bearer** secrets
  (`client_secret`, `refresh_token`) under the shared `meicagent` service. The streamer needs only these
  two — it never makes an account-scoped call, so no `account_number`. `cherrypick connect` delegates
  bearer-secret entry here for a streamer-only (no-MEIC) install; writes only the keyring, never the
  broker.
- **`cherrypick/streamer/orb.py`** — the opening-range `trade_hook`: per-symbol 09:30–09:35 ET high/low from
  live Trade ticks, written once to the cache's `orb_ranges` table when the window closes (idempotent
  per day). No schema change — that table already lives in `cherrypick.core.streamcache`.
- **`cherrypick/streamer/cli.py` + `run.py`** — the CLI. Flat args (`--status` / `--stop` / `--symbol` / `--secrets-set` /
  `--secrets-status`, default = run) so the orchestrator drives it with the same start/status/stop argv
  contract it uses for MEIC's streamer.
- **`cherrypick.core`** — an installed dependency (`packages/core` in this monorepo, `pip install -e
  packages/core`, same for every sibling); the streaming engine (`core.streamer`), cache schema
  (`core.streamcache`), auth (`core.auth`), and home resolver (`core.home`) all live there.

## Invariants (do not violate)

- **Exactly one producer writes the cache at a time.** This daemon and MEIC's streamer both write the
  same canonical cache; running both means two writers and two DXLink connections into one account. The
  PID single-instance guard plus the orchestrator only ever starting one producer are what enforce this —
  do not add a second writer path.
- **Only the daemon talks to the broker.** The `--status`/`--stop` paths read files and the PID only;
  `--secrets-set`/`--secrets-status` touch only the OS keyring. None of them open a broker session, and
  each emits a single JSON object on stdout. Deterministic throughout: this is a producer, and what
  it writes must depend only on what the feed sent.
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
