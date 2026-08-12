# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

cherrypick-scout is a **self-hosted options research surface** for the cherrypick suite: a
recorded-earnings-screen view, a credit-spread/iron-condor screener, per-symbol candlestick charts,
and a leg-list payoff builder — content ideas borrowed from commercial options-research platforms,
built on the suite's own data
sources (`tastytrade.metrics.get_market_metrics`, the shared Dolt DBs, a new candle/chain cache). It is
**standalone**: no orchestrator section card or dashboard-embed registration, and not on any
reliability path.

**Its web app was deleted on 2026-08-12** — the console (`packages/console`) is the suite's one read
surface, and it already re-implements most of `services/` and `analytics/` in TypeScript. What is left
here is the services-and-cache half, kept until a gap audit ports whatever the console still needs
(`analytics/describe.py` and `analytics/narrative.py` at minimum); then this package goes. Treat it as
a library with a watchlist/cache CLI, not a surface. The dry-run invariant below still binds: this
package still owns the single broker-order call site in `services/staging.py`. It was built milestone-by-milestone on the long-lived `feature/scout`
branch (see git history) and merges to `main` only when feature-complete — the merge itself is a
separate, deliberate step, not implied by the branch reaching M8.

## Commands

```bash
python run.py watchlist add AAPL MSFT NVDA   # curate the research universe
python run.py watchlist remove NVDA
python run.py watchlist list
python run.py cache stats                    # row counts per cache table
python run.py cache clear                    # delete the local cache DB (safe -- pure cache)
python -m pytest                             # tests run against a temp DB/home; no broker/network
ruff check . && ruff format .                # line-length 110, E501 enforced (new code)
```

Config: copy `config.example.json` → `config.json` (git-ignored), or omit it entirely and rely on
`~/.cherrypick/config/scout.json` / built-in defaults — see `config.py`'s load order.

## Invariants (do not violate)

- **Research surface with order *staging*, never order *placement*.** The builder (from M6) validates
  a ticket via the broker's dry-run API (`dry_run=True` — buying-power effect, fees, warnings, no order
  created) and saves it locally for manual execution in the tastytrade platform. **`dry_run=True` must
  be hardcoded at the single broker-order call site, never parameterized or threaded through as a
  variable** — a source-scan test (`test_dry_run_only.py`, from M6) asserts no call path in this
  package can reach real order submission. Dry-run is still an authenticated broker write-shaped call:
  it must never run on a timer, a page load, or the SSE poller — button-triggered only.
- **Additive-only outside `packages/scout/`.** The one deliberate exception is a single line in
  `.github/workflows/ci.yml`'s package matrix. Everything else this branch needs lives inside this
  package; do not reach into `packages/core` or a sibling module to add scout-specific behavior.
- **The suite's one mutating-surface posture, ported down from `cherrypick.orchestrator.settings_serve`.**
  Loopback binding alone is not enough once a route writes anything (a malicious webpage can fetch
  `http://127.0.0.1:<port>`, and DNS rebinding defeats same-origin). Every request needs a matching
  loopback `Host` header; every mutating request additionally needs the per-process CSRF token, an
  `application/json` content type, and a matching local `Origin` when one is present. See
  `security.py`; do not add a mutating route that bypasses `SecurityMiddleware`.
- **Prefer tastytrade-owned data sources over third-party ones** (user rule, suite-wide). Broker
  metrics, DXLink feeds, and REST snapshots come first; a third-party source (the shared Dolt DBs)
  is acceptable only where tastytrade genuinely has no equivalent. This is why `candle_service`
  seeds from DXLink history rather than `stocks.ohlcv` — the Dolt table's date-led primary key
  made per-symbol reads full-scan 28.5M rows (~2 min, measured, past every sane timeout), and the
  fix was switching to the broker's own history feed, not indexing a shared database this package
  treats as read-only. Same reasoning behind `sector_service` (the screener's Sector chip reads
  `tastytrade.watchlists.PublicWatchlist`'s public "Sectors" groupings) and `liquidity_service`
  (its "Liquid Symbols" watchlist, pre-filtering the earnings calendar). The calendar's own
  original premise for using Dolt as its broad-coverage source — "acceptable only where tastytrade
  has no equivalent" — turned out to be wrong: `earnings_watchlist_service` reads tastytrade's own
  public "All Earnings" watchlist (85 symbols), unioned into the live metrics call so those names
  get real dates instead of Dolt's third-party snapshot. Dolt remains the fallback for the genuine
  long tail beyond even that broader tastytrade list, not the primary broad source anymore.
- **Never write a cache this module doesn't own.** `services/cache.py` opens only this module's own
  `~/.cherrypick/data/scout/cache.db`. `calendar_service`'s Dolt read (`earnings.earnings_calendar`)
  is read-only and never writes its source. `services/earnings_metrics_service.py` is a second,
  explicitly-named sibling of `services/streamcache.py`'s read-only exception: it opens the
  **cherrypick.earnings** module's own `entry_reviews` table (in either `earnings_trades.db` or
  `paper_trades.db`, resolved via `cherrypick.core.home.data_dir("earnings",
  env="EARNINGS_DATA_DIR")` -- the same path earnings' own `paths.py` resolves to, duplicated as a
  string rather than imported) with `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`. Scout is a
  reader of this data, never the producer -- it must never write to either earnings database, and
  per this file's "additive-only outside `packages/scout/`" invariant it must never import
  `cherrypick.earnings` either (the two DB paths and the `entry_reviews` column names are the only
  things duplicated here, deliberately, to keep the packages decoupled). A missing DB file, or one
  that predates `entry_reviews`, degrades to an empty result rather than raising -- same posture
  `streamcache.py` takes for a streamer daemon that isn't running. A third, narrower sibling of the
  same exception: `earnings_metrics_service.py` also reads `symbol_watch.json`, a plain JSON file
  (not a DB) in that same earnings data directory, written by `cherrypick.earnings`'s own scheduled
  forward-preview scan (`python -m cherrypick.earnings.symbol_watch refresh`, run by an
  orchestrator-scheduled task, never by scout). It carries price and the broker-chain-heavy
  signals (expected move, term structure, IV/RV, winrate, historical move stats) plus a
  recommended/near_miss/fail tier badge, for symbols reporting in the next ~10 **trading** days
  and pre-filtered on the earnings side to a liquid-enough universe (tastytrade's "Liquid
  Symbols" + "High Options Volume" + "tasty Earnings" public watchlists) -- exactly the
  per-symbol cost the Earnings page must never pay on its own request path (see below). Read with
  a plain `json.load`, never written. The Upcoming section's own display is a **filter**, not
  just a merge: a calendar row that never matched a scan entry (by symbol AND earnings_date) is
  dropped entirely, so the page only ever shows what the scan actually reached -- a missing/
  empty/mid-pass file simply means nothing has matched yet, rendered as the page's own
  never-run/scanning/last-refreshed status banner rather than a failed request.
- **The streamer comes before API calls, whenever practical.** Prefer the cached/batched path over a
  fresh broker round trip; the broker API is for acting (dry-run) and for confirming what only it can
  know. `services/cache.py`'s `get_or_fetch`/`async_get_or_fetch` are the shared mechanism — TTL
  cache, stale-serve on fetch failure, honest `as_of`/`stale` on every payload. As of the latency pass
  that added `services/streamcache.py`, this is literal, not just aspirational: `quote_service`
  checks the suite's **shared** stream cache (`~/.cherrypick/data/marketdata/stream_cache.db`,
  written by the standalone streamer daemon when it's running) for each symbol first, and only
  reaches the broker for a symbol that's missing there or older than
  `refresh.stream_cache_max_age_seconds`. Read-only, never writes that cache — scout is a reader,
  never the producer. Coverage caveat worth remembering: the streamer's registry only promises a
  spot + ATM option window per requested underlying, so option-level quotes/greeks for a screener's
  30-45 DTE monthly (far from the ATM window a 0DTE-focused consumer like MEIC requests) will
  typically miss the shared cache and fall through to REST regardless — only equity/underlying spot
  (`quote_service`, the SSE feed) has a reliably-covered win today. **One narrow, deliberate
  exception** to "only the streamer talks to the broker" (a streaming-path rule): `candle_service`
  opens its own short-lived `DXLinkStreamer` (bounded by an idle timeout and a hard wall-clock cap,
  opened on demand, never resident) to seed a symbol's daily-candle history
  (`refresh.candles_backfill_days`, default 12 months) and to top up the gap since the last cached
  bar. It never informs a decision — this package makes none — only fills chart history; a DXLink
  failure falls back to a single synthesized bar from a snapshot quote rather than blocking the
  page.
- **Rate-limit discipline.** `metrics_service` batches every stale/missing symbol into one
  `get_market_metrics` call rather than one call per symbol (the calendar and the screener both go
  through it); `chain_service.get_quotes` batches into ~100-symbol `get_market_data_by_type` chunks
  the same way; a manual `?fresh=1` refresh is still floored (`refresh_floor_seconds`) so a refresh
  button can't be used to hammer the broker. `calendar_service`'s straddle-based expected move and
  `/api/payoff`'s POP calculation are the two places a single narrow broker call rides on an
  otherwise pure-computation route — both degrade to omitting the number rather than failing the
  request if that call is unavailable. `screener_service` follows the plan's five-step compute flow
  precisely because skipping a step (e.g. fetching chains before the IV-rank/liquidity pre-filter)
  would turn a watchlist-sized request into a whole-chain-per-symbol one regardless of whether the
  symbol was ever going to survive the filter. `sse.py`'s `QuotePoller` (`/api/stream`) applies the
  same discipline to a live surface: one batched `quote_service.get_quotes` call per tick, and the
  poll loop itself exists only while at least one SSE client is connected — a closed browser tab
  costs zero broker calls, not merely a throttled few. `GET /api/symbol/{sym}/quote` (the builder's
  symbol-selection prefill) is the same idea applied to route *choice*, not batching: it deliberately
  bypasses `candle_service` (a cold DXLink backfill, measured at 38.8s for one symbol) for spot/IV,
  since `quote_service`/`metrics_service` already answer that in a fraction of the time and the
  builder never reads the candle-derived fields `/stats` exists for. Don't route a fast consumer
  through a slow service's endpoint just because the data happens to be a superset of what it needs.
- **Credentials in the OS keyring only**, via `services/session.py`'s `BrokerSession` (one
  process-wide `cherrypick.core.auth.session.SessionManager` over the shared `cherrypick-broker`
  keyring service, behind an `asyncio.Lock`, one retry on a 401-shaped failure). Never files, env
  vars, or logs. Missing credentials must degrade a service to an empty/partial result, never a
  hard error — `calendar_service` does this for its metrics side.
- **Account numbers masked** to `****1234` anywhere they surface (dry-run responses, staged tickets).
- **Portable paths only.** Runtime data/logs/config resolve through `cherrypick.core.home`
  (`data_dir("scout")`, `logs_dir("scout")`, `config_path("scout")`), relocatable in one move via
  `$CHERRYPICK_HOME`. Never hardcode an absolute path, username, or drive letter.
- **No CDN, no npm/Node, no build step.** `static/vendor/` holds the exact fetched bytes of each
  pinned frontend library (see `static/vendor/LICENSES.md` for versions/sources/licenses); do not
  hand-edit a vendored file or add a `<script src="https://...">` pointing off-box. The one allowed
  exception is the Lightweight Charts Apache-2.0 attribution link in the page footer.
- **Instruction files hold no code.** This `CLAUDE.md` is build commands + invariants only.
- **No MCP / network / AI on any loop-decision path.** Nothing in this package makes an unattended
  trading decision — it is a human-driven research tool — but the suite-wide rule still applies to
  anything resembling automation added here.

## Layout

See README.md's file tree for what currently exists. Two things worth knowing up front:

- `src/cherrypick/scout/` has an `__init__.py` (an ordinary package marker); its parent
  `src/cherrypick/` deliberately does not, so this composes with `cherrypick.core` and every sibling
  module under one `cherrypick.*` namespace root.
- `services/cache.py`'s schema declares every table up front rather than migrating per milestone:
  the generic `kv_cache` TTL store, `candles`/`candle_meta` (`candle_service`, M3), `staged_orders`
  (`services/staging.py`, M6), and `symbol_meta` (reserved since M3 for a future sector/industry
  enrichment service) — now used by `sector_service` (the screener's Sector chip), one bulk
  `tastytrade.watchlists.PublicWatchlist.get` fetch upserting every row together, staleness
  checked via `cache.symbol_meta_freshness`'s table-wide MAX rather than a per-symbol TTL. The
  `industry` column remains genuinely unused — no source populates it yet.
- `analytics/` (`levels.py`, `payoff.py`, `pop.py`, `strategies.py`) is stdlib + dataclasses only, no
  I/O, so a future promotion to `cherrypick.core` is a file move once stable. Don't reach for a broker
  call or a cache read inside this package — that belongs in a `services/` module (`screener_service`
  for `strategies.py`) that calls into `analytics/`, not the other way around.
- Live per-option greeks come from DXLink `Greeks` events via `chain_service.get_greeks` — scout's
  own TTL cache first, then the shared stream cache's `stream_greeks` (keyed by the
  `streamer_symbol` the chain fetch now serializes), then one short-lived, bounded `DXLinkStreamer`
  subscription for the remainder (the second instance of `candle_service`'s opened-on-demand /
  never-resident exception). An earlier version of this file claimed "no live greeks source
  exists"; that was an over-generalization from the REST quote endpoint (`get_market_data_by_type`
  genuinely carries none) — corrected by the user, since the dxfeed feed serves greeks per option
  and the suite's shared streamer had always demonstrated it. The builder's chain rows attach them
  to legs so `net_greeks` is real; `analytics/describe.py`'s Black-Scholes model greeks remain as
  the clearly-labeled fallback when a leg has none. `strategies.py`'s
  nearest-OTM-by-expected-move short-strike selection and the screener's price-based skew proxy
  predate this source and are now upgradeable to true delta targeting — a deliberate follow-up,
  not an accident to preserve.
- **A real value can still be zero or degenerate — validate it, don't just check for `None`.**
  `candle_service` originally accepted any DXLink candle whose `open` wasn't `None`, which let a
  zero-filled placeholder for the still-forming current-day bar through as genuine data (a live
  smoke test caught it: it silently broke every OTM-strike calculation downstream). When adding a new
  broker-sourced field, ask what a *specifically wrong but technically present* value would look
  like, not only what a *missing* one looks like.
