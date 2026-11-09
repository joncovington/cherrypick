# cherrypick-streamer — Operational Instructions

> The suite's standalone market-data daemon. Suite context: the root
> [documentation index](../../docs/README.md).

## What this is

A long-lived infrastructure daemon that runs the shared `cherrypick.core.streamer.ChainStreamer`
engine and keeps the **canonical shared stream cache** (`~/.cherrypick/data/marketdata/stream_cache.db`)
fresh, so any consumer can price off live quotes without MEIC. It places no orders and never touches
live trading.

It owns the daemon **lifecycle** (PID guard, `--status`/`--stop`, logging), the subscription registry
and opening-range capture — and **no trading policy**: no open-position leg subscriptions of its own,
no account REST poller, no HTTP API; a module declares its needs in `state/stream_requests/`.
Opening-range capture
(`orb.py`) is not an exception: it writes a generic `orb_ranges` table any consumer reads, and it
belongs to the always-on producer because a consumer's cadence is not guaranteed to land inside
09:30–09:35.

## Commands

Commands to run the package, its tests and its lint are in [README.md](README.md#commands).

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
  implementations would recycle this daemon over a difference it never sees. `union_legs` is part of that union (gex's quote recorder, the second reader, records held strikes).
  - **A `window_hint` is a `(below, above)` span, not a width.** A plain count is symmetric;
    `{"down": N, "up": M}` is directional. Both normalise through `streamcache.window_span`, the union
    takes the max **per side**, and `window_strike_count` floors both sides, so a directional hint can
    only ask for more on one side, never narrow the other (see "Subscription budget and guard rails"
    below). pmcc declares its deep window downward-only: its long sits far below spot, and a symmetric
    window would buy a block of strikes above spot that nothing reads.
  - **A window carries only the events its symbol's declarers need.** `window_events` (`Quote`,
    `Greeks`, `Summary` = open interest, `Trade` = option volume) and `nearest_window: false` narrow a
    symbol only when EVERY module declaring it opts down; a silent declarer keeps all four events and
    the nearest window, so SPX (GEX reads gamma × OI) stays whole. Both are fixed at launch — a
    window's unsubscribe must match its subscribe — so growth recycles the producer
    (`streamrequests.subscription_snapshot`) and a reduction waits for the next restart. With the
    nearest window declined, a requested date equal to it is still served as an extra window.
  - **`TimeAndSale` (options prints) is opt-in, never default**
    (`streamrequests.OPT_IN_EVENTS`): a window carries it only when some declarer names it, naming it
    alone keeps the four (`window_event_set`), and it rides the **nearest window only**. Each add is
    a time series from 09:30 ET, so a mid-session connect gets the prints it missed, up to the feed's
    cap of about 1,000 per symbol (`scripts/probe_time_and_sale.py`). Prints land raw in the cache's
    `stream_time_and_sale`, keyed by the feed's index (a correction replaces), and are pruned to three
    days on each connect; gex's recorder keeps the history. Its listener starts only when a window
    wants it, and a write error costs that print, never the quote listeners beside it.
  - **A confirmed session value is never erased by a later event that omits it.** The `stream_summary`
    upsert COALESCEs every OHLC field against what is stored; a bare overwrite lets late Summary
    events null out the settled SPX/XSP closes (their last event of the day arrives with the close
    cleared). A value only gets more known through a session.
  - **A Summary row is keyed by the session the event describes (`day_id`), never by when it
    arrived.** Every subscribe resends the last session, so receipt-keying would file that snapshot under
    the next day; for SPX/XSP, whose live events carry no close, the stale close would then survive the
    COALESCE all session. `streamcache.repair_misfiled_summary` removes what it left (weekend rows,
    a session's snapshot a day late) and corrects a frozen close on every connect, before the close
    fill and the backfill.
  - **Quote and Greeks are filtered by what a symbol can publish, not what it is.** `build_streamer`
    asks `streamcache.publishes_quotes` / `publishes_greeks` per leg: nothing cash-settled has greeks,
    an index has no book to quote. ETF and single-name legs keep Quote. The quoteless set is a
    **declared list**, not a ticker pattern, so an unlisted symbol defaults to paying for a possibly
    wasted subscription rather than starving a reader (a leg with no price at all is the expensive
    failure; SKEW, VIX9D, VIX and VIX1D print Trade and no Quote).
- **`config.py`** — config and path resolution via `cherrypick.core.home`: the canonical cache default
  (a neutral scope owned by no trading module), operator base symbols (a seed the registry adds to),
  log/PID paths. `source.stream_cache_db` overrides the cache path.
- **`daemon.py`** — the keyring session factory, `build_streamer` (registry-driven wiring: underlyings
  at startup, dynamic legs via `extra_subscriptions`/`protected_symbols`), the PID single-instance
  guard, rotating logs, `status()`/`stop()`, and `run_daemon`. The one place this package talks to the
  broker.
- **`credentials.py`** — keyring entry for the shared OAuth **bearer** secrets (`client_secret`,
  `refresh_token`) under the shared `meicagent` service; no `account_number`, since the streamer makes
  no account-scoped call. The suite's onboarding (`python -m cherrypick.core.auth setup`) writes the
  shared `cherrypick-broker` login this daemon falls back to; `--secrets-set` is the streamer-only
  path. Writes only the keyring.
- **`orb.py`** — the opening-range `trade_hook`: per-symbol 09:30–09:35 ET high/low from Trade ticks,
  written once (idempotent per day) to `orb_ranges`, a table that already lives in
  `cherrypick.core.streamcache`.
- **`cli.py` + `run.py`** — flat args (default = run) so the orchestrator drives this with the same
  start/status/stop argv contract it uses for every daemon.
- **`cherrypick.core`** holds the engine (`core.streamer`), cache schema (`core.streamcache`), auth
  and home resolver.

## Subscription budget and guard rails

DXLink kills the socket ("Your subscription rate is too high") when a connection subscribes too much
too fast, and a producer that re-sends the identical burst on every reconnect can never recover. Four
guards keep that from happening, and the first is the only one that removes the failure mode: the
others lower the odds.

- **Subscribes are paced** (`cherrypick.core.streamer`, `_send_subs`, the one choke point for every
  subscribe and unsubscribe). Chunks of `SUBSCRIBE_CHUNK` symbols are spaced by `SUBSCRIBE_PACE_S`,
  and a token bucket (`SUBSCRIBE_BURST` at once, refilled at `SUBSCRIBE_PER_MINUTE`, one token per
  symbol per event type) holds the average down, because the limit is a budget over about a minute and
  not a per-second rate. A rate cut halves the refill (never under `SUBSCRIBE_MIN_PER_MINUTE`) and
  empties the bucket; each clean hour earns a quarter of the base back. A full resubscribe takes
  minutes, with the underlyings first so spot is fresh while strikes fill in.
- **Windows carry only what their readers read**: directional hints, `window_events` and
  `nearest_window` (above), and Quote/Greeks filtered by what a symbol can publish.
- **A declared budget.** `streamrequests.estimate_subscriptions` / `budget_status` model the cost from
  the same registry union the producer subscribes from, and the watchdog raises a finding past
  `DEFAULT_SUBSCRIPTION_BUDGET` (12,000), or past `streamer.subscription_budget` where set, read from
  the producer's own `config/streamer.json` first and the suite config second. The finding names the
  largest symbol as well as the total, because one declaration usually dominates. It is an estimate
  and will not equal the producer's own `subscribed_symbols`; it exists to catch a change of order of
  magnitude when a module declares an expensive underlying.
- **Reconnect churn is a finding of its own.** The watchdog reads the `reconnect_count` that
  `--status` reports and warns when it climbs faster than a healthy day (0-2 a day): a process that
  keeps coming back looks healthy at every instant and is not.

`window_strike_count` is the base window, per side, for symbols no module widens by hint (SPX, SPY).
The shipped example sets 60; a machine near its budget sets 30 in `config/streamer.json`, which is
+-150 points on SPX and about +-4% on SPY, well outside every module's strikes that read the base
window. Raise it only outside market hours, watching the reconnect count. A parameter cut under an
outage is a guess with a deadline: re-measure it against what the readers need (a cut below the deepest
strike a reader needs makes its entries refuse for good).

Every resident paper loop touches its heartbeat at the top of each tick
(`cherrypick.core.home.heartbeat_path`), so the freshness check has one unconditional signal for a
hold-to-expiry module that has finished entering and is otherwise quiet.

## Invariants (do not violate)

- **Exactly one producer writes the cache: this daemon.** No other writer exists. A second writer means two DXLink connections into one account;
  the PID guard, the orchestrator knowing only this producer, and the engine itself enforce it — do
  not add one. `ChainStreamer` refuses the shared cache path unless constructed with
  `producer=True`, which only this daemon passes.
  `orchestrator/tests/test_single_cache_writer.py` pins the example config and this machine's to one
  producer, and `doctor` warns on an enabled `modules.<m>.streamer` block. (gex's standalone
  `run.py stream` is development-only and must never run beside this daemon.)
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
  fetched only at start/reconnect. A dead chain can serve all session while every aggregate stays
  fresh (flies would refuse every entry). Three things hold it: `_fetch_dte0_chain` selects by ET session
  date (none on or after it is a `chain_fetch_error`, never a silent stale load); `_symbol_refresher`
  refetches and rebuilds the window when the session date moves past the load date; and each health
  row records `chain_expiration`, from which `--status` reports `stale_chains` (base windows only) so
  the watchdog restarts on it. Only symbols the registry union still serves are judged: a symbol no
  module requests any more keeps its last row, and is not stale.
- **The streaming engine stays in `cherrypick.core`.** Do not fork `ChainStreamer` or the cache schema
  here (the GEX math drifted ~75× once when copied).
- **No trading policy here.** Open-position leg subscriptions of its own, REST polling and any HTTP API
  belong in a trading module, which declares its legs through `state/stream_requests/`. (ORB capture is the generic exception described above.)
