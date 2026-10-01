# cherrypick-streamer — Operational Instructions

> The suite's standalone market-data daemon. Suite context: the root
> [documentation index](../../docs/README.md); why it was split out of MEIC:
> [docs/history/streamer-package-plan.md](../../docs/history/streamer-package-plan.md). Dated incident
> detail behind the rules below: [docs/history.md](docs/history.md).

## What this is

A long-lived infrastructure daemon that runs the shared `cherrypick.core.streamer.ChainStreamer`
engine and keeps the **canonical shared stream cache** (`~/.cherrypick/data/marketdata/stream_cache.db`)
fresh, so any consumer can price off live quotes without MEIC. It places no orders and never touches
live trading.

It owns the daemon **lifecycle** (PID guard, `--status`/`--stop`, logging), the subscription registry
and opening-range capture — and **no trading policy**: no open-position leg subscriptions of its own,
no account REST poller, no `127.0.0.1:7699` HTTP API. Those stay in MEIC's wrapper
(`../meic/src/cherrypick/meic/streamer.py`), layered onto the same engine. Opening-range capture
(`orb.py`) is not an exception: it writes a generic `orb_ranges` table any consumer reads, and it
belongs to the always-on producer because a consumer's cadence is not guaranteed to land inside
09:30–09:35.

## Commands

```bash
python run.py               # run the daemon in the foreground (Ctrl-C / SIGTERM to stop)
python run.py --status      # print one JSON health object (running, pid, oldest_event_age_s, ...) and exit
python run.py --stop        # SIGTERM a running daemon
python run.py --symbol SPX --symbol XSP   # override the configured symbols for this run
python run.py --secrets-set    # store the shared tastytrade OAuth bearer secrets (hidden input) in the keyring
python run.py --secrets-status # print which shared OAuth secrets are present (JSON)
python -m pytest            # lifecycle tests; no broker/streamer required (temp $CHERRYPICK_HOME)
ruff check . && ruff format .             # line-length 110
```

Config: copy `config.example.json` → `config.json` (git-ignored, machine-local), or place
`~/.cherrypick/config/streamer.json`.

## Architecture

- **`registry.py`** — the subscription registry. Each consumer writes only its own
  `~/.cherrypick/state/stream_requests/<module>.json`; the streamer streams the **union**. `symbols`
  are underlyings (spot + ATM window + GEX + opening range). `leg_sources` are `{db, query}` specs: the
  streamer opens each DB **read-only**, runs the module's `SELECT` every poll, and keeps each non-null
  cell subscribed beyond the ATM window (how MEIC keeps its open IC legs fresh). `legs` is an optional
  static list. The coupling surface is data plus the module's own SQL, never code, so no package
  imports another. The symbol/window-hint union is delegated to `cherrypick.core.streamrequests` —
  the orchestrator unions the same files to decide whether a running producer has gone stale, and two
  implementations would recycle this daemon over a difference it never sees. `union_legs` stays local.
  - **A `window_hint` is a `(below, above)` span, not a width.** A plain count is symmetric;
    `{"down": N, "up": M}` is directional. Both normalise through `streamcache.window_span`, the union
    takes the max **per side**, and `window_strike_count` floors both sides, so a directional hint can
    only ask for more on one side, never narrow the other (see `docs/streamer-subscription-budget.md`).
  - **A window carries only the events its symbol's declarers need.** `window_events` (`Quote`,
    `Greeks`, `Summary` = open interest, `Trade` = option volume) and `nearest_window: false` narrow a
    symbol only when EVERY module declaring it opts down; a silent declarer keeps all four events and
    the nearest window, so SPX (GEX reads gamma × OI) stays whole. Both are fixed at launch — a
    window's unsubscribe must match its subscribe — so growth recycles the producer
    (`streamrequests.subscription_snapshot`) and a reduction waits for the next restart. With the
    nearest window declined, a requested date equal to it is still served as an extra window.
  - **A confirmed session value is never erased by a later event that omits it.** The `stream_summary`
    upsert COALESCEs every OHLC field against what is stored; a bare overwrite once let late Summary
    events null out 22 sessions of SPX/XSP closes. A value only gets more known through a session.
  - **A Summary row is keyed by the session the event describes (`day_id`), never by when it
    arrived.** Every subscribe resends the last session, so receipt-keying filed that snapshot under
    the next day; for SPX/XSP, whose live events carry no close, the stale close then survived the
    COALESCE all session. `streamcache.repair_misfiled_summary` removes what it left (weekend rows,
    a session's snapshot a day late) and corrects a frozen close on every connect, before the close
    fill and the backfill.
  - **Quote and Greeks are filtered by what a symbol can publish, not what it is.** `build_streamer`
    asks `streamcache.publishes_quotes` / `publishes_greeks` per leg: nothing cash-settled has greeks,
    an index has no book to quote. ETF and single-name legs keep Quote. The quoteless set is a
    **declared list**, not a ticker pattern, so an unlisted symbol defaults to paying for a possibly
    wasted subscription rather than starving a reader.
- **`config.py`** — config and path resolution via `cherrypick.core.home`: the canonical cache default
  (a neutral scope owned by no trading module), operator base symbols (a seed the registry adds to),
  log/PID paths. `source.stream_cache_db` overrides the cache path.
- **`daemon.py`** — the keyring session factory, `build_streamer` (registry-driven wiring: underlyings
  at startup, dynamic legs via `extra_subscriptions`/`protected_symbols`), the PID single-instance
  guard, rotating logs, `status()`/`stop()`, and `run_daemon`. The one place this package talks to the
  broker.
- **`credentials.py`** — keyring entry for the shared OAuth **bearer** secrets (`client_secret`,
  `refresh_token`) under the shared `meicagent` service; no `account_number`, since the streamer makes
  no account-scoped call. `cherrypick connect` delegates bearer-secret entry here for a streamer-only
  install. Writes only the keyring.
- **`orb.py`** — the opening-range `trade_hook`: per-symbol 09:30–09:35 ET high/low from Trade ticks,
  written once (idempotent per day) to `orb_ranges`, a table that already lives in
  `cherrypick.core.streamcache`.
- **`cli.py` + `run.py`** — flat args (default = run) so the orchestrator drives this with the same
  start/status/stop argv contract it uses for MEIC's streamer.
- **`cherrypick.core`** holds the engine (`core.streamer`), cache schema (`core.streamcache`), auth
  and home resolver.

## Invariants (do not violate)

- **Exactly one producer writes the cache at a time.** This daemon and MEIC's streamer write the same
  cache; both running means two writers and two DXLink connections into one account. The PID guard and
  the orchestrator starting only one producer enforce this — do not add a second writer path.
- **Only the daemon talks to the broker.** `--status`/`--stop` read files and the PID only;
  `--secrets-*` touch only the OS keyring. Each emits a single JSON object. What the producer writes
  must depend only on what the feed sent.
- **Credentials live in the OS keyring only** (never files, env vars or logs), under `meicagent`: only
  the two bearer secrets. Account selection is a trading module's concern.
- **`--status` prints one merged JSON object** — `running`/`pid` and the staleness/connection fields
  (`oldest_event_age_s`, `stale_age_s`, `connected_since`, `stale_chains`) together. The orchestrator's
  `util.first_json` parses the buffer, so a second JSON line is dropped. It must also keep answering on
  a cache the producer has not migrated yet: the watchdog reads status before it auto-starts the
  streamer.
- **A base window's "0DTE" chain is the first expiration on or after the ET session date, and it
  rolls with the date** — never the expiration nearest the local calendar date by distance, never
  fetched only at start/reconnect. A dead chain served all session once while every aggregate stayed
  fresh (flies refused 4,662 entries). Three things hold it: `_fetch_dte0_chain` selects by ET session
  date (none on or after it is a `chain_fetch_error`, never a silent stale load); `_symbol_refresher`
  refetches and rebuilds the window when the session date moves past the load date; and each health
  row records `chain_expiration`, from which `--status` reports `stale_chains` (base windows only) so
  the watchdog restarts on it.
- **The streaming engine stays in `cherrypick.core`.** Do not fork `ChainStreamer` or the cache schema
  here (the GEX math drifted ~75× once when copied).
- **No trading policy here.** Open-position leg subscriptions of its own, REST polling and any HTTP API
  belong to a trading module's wrapper. (ORB capture is the generic exception described above.)
