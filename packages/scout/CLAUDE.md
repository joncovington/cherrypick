# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

cherrypick-scout is a **self-hosted options research surface** for the cherrypick suite: an earnings
calendar, a credit-spread/iron-condor screener, per-symbol candlestick charts, and a leg-list payoff
builder — content ideas borrowed from OptionsPlay-style research tools, built on the suite's own data
sources (`tastytrade.metrics.get_market_metrics`, the shared Dolt DBs, a new candle/chain cache). It is
**standalone**: its own port (5057), no orchestrator section card or dashboard-embed registration, and
not on any reliability path. It is being built milestone-by-milestone on the long-lived `feature/scout`
branch (see git history for what has actually landed) and merges to `main` only when feature-complete.

## Commands

```bash
python run.py watchlist add AAPL MSFT NVDA   # curate the research universe
python run.py watchlist remove NVDA
python run.py watchlist list
python run.py cache stats                    # row counts per cache table
python run.py cache clear                    # delete the local cache DB (safe -- pure cache)
python run.py serve                          # localhost research app (default 127.0.0.1:5057)
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
- **Never write a cache this module doesn't own.** `services/cache.py` opens only this module's own
  `~/.cherrypick/data/scout/cache.db`. `calendar_service`'s Dolt read (`earnings.earnings_calendar`)
  and `candle_service`'s (`stocks.ohlcv`) are read-only and never write their source.
- **The streamer comes before API calls, whenever practical.** Prefer the cached/batched path over a
  fresh broker round trip; the broker API is for acting (dry-run) and for confirming what only it can
  know. `services/cache.py`'s `get_or_fetch`/`async_get_or_fetch` are the shared mechanism — TTL
  cache, stale-serve on fetch failure, honest `as_of`/`stale` on every payload. **One narrow,
  deliberate exception** to "only the streamer talks to the broker" (a streaming-path rule):
  `candle_service`'s DXLink tail top-up opens its own short-lived `DXLinkStreamer` (bounded by an
  idle timeout and a hard wall-clock cap, opened on demand, never resident) to fill the gap between
  Dolt's last row and now. It never informs a decision — this package makes none — only fills in
  recent chart history; a DXLink failure falls back to a single synthesized bar from a snapshot quote
  rather than blocking the page.
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
  costs zero broker calls, not merely a throttled few.
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
  (`services/staging.py`, M6), and `symbol_meta`, still unused — reserved for an optional future
  sector/industry enrichment service, not load-bearing for anything today.
- `analytics/` (`levels.py`, `payoff.py`, `pop.py`, `strategies.py`) is stdlib + dataclasses only, no
  I/O, so a future promotion to `cherrypick.core` is a file move once stable. Don't reach for a broker
  call or a cache read inside this package — that belongs in a `services/` module (`screener_service`
  for `strategies.py`) that calls into `analytics/`, not the other way around.
- Live per-option greeks (delta/gamma/theta/vega) have no source yet — `chain_service`'s quotes come
  from `get_market_data_by_type`, which doesn't carry them, and the SDK's option-chain call doesn't
  either. `payoff.Leg`/`net_greeks` already treat greeks as optional per leg; don't invent a greeks
  source by guessing at one (e.g. backing into delta from historical Dolt data) without deciding it
  deliberately — a wrong greek is worse than an honestly missing one. This is also why
  `strategies.py`'s short-strike selection uses nearest-OTM-by-expected-move rather than a delta
  target, and why the screener's skew column is a price-based proxy, not a true delta-matched IV skew.
- **A real value can still be zero or degenerate — validate it, don't just check for `None`.**
  `candle_service` originally accepted any DXLink candle whose `open` wasn't `None`, which let a
  zero-filled placeholder for the still-forming current-day bar through as genuine data (a live
  smoke test caught it: it silently broke every OTM-strike calculation downstream). When adding a new
  broker-sourced field, ask what a *specifically wrong but technically present* value would look
  like, not only what a *missing* one looks like.
