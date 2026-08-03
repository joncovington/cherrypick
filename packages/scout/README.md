# cherrypick-scout

**What this module does:** a self-hosted options research surface — an earnings calendar, a
credit-spread/iron-condor screener, per-symbol candlestick charts, and a leg-list payoff builder.
It is a **research surface with order staging, never order placement**: the builder can validate a
ticket against the broker's dry-run API (buying-power effect, fees, warnings — no order is created)
and save staged tickets locally for manual execution in the tastytrade platform. No code path in this
package may submit a real order.

Its universe is a user-curated watchlist plus earnings-calendar names, not the whole market. It is a
fully standalone package — its own port, no orchestrator section/embed registration, and not on any
reliability path.

Part of the **cherrypick** trading-tool suite; see the suite's [documentation index](../../docs/README.md)
for the big picture. This package is being built milestone-by-milestone on `feature/scout` (see git
history) — the file tree and CLI table below describe **what exists on this branch today**, not the
eventual full surface.

## Setup

```bash
git clone https://github.com/joncovington/cherrypick.git
cd cherrypick
pip install -e packages/core                    # shared cherrypick.core library, install first
cd packages/scout
pip install -e ".[dev]"
cp config.example.json config.json               # optional -- defaults work out of the box
```

## Commands

```bash
python run.py watchlist add AAPL MSFT NVDA        # add symbols to the watchlist
python run.py watchlist remove NVDA               # remove symbols
python run.py watchlist list                      # print the current watchlist
python run.py cache stats                         # row counts per cache table
python run.py cache clear                         # delete the local cache DB
python run.py serve                               # -> http://127.0.0.1:5057

python -m pytest                                  # tests (temp DB/home; no broker or network)
ruff check . && ruff format .                     # lint/format
```

## Config

`config.json` (git-ignored, machine-local; falls back to `~/.cherrypick/config/scout.json`, then
`config.example.json`, then built-in defaults). See `config.example.json` for the full shape:
`serve.{host,port}`, `refresh.*` (per-service cache TTLs), `screener.*` (screening thresholds used
from M5 onward), `dolt.{host,port,user,connect_timeout_seconds}` (the earnings-calendar Dolt read).

Runtime data lives under the shared suite home (`cherrypick.core.home`, relocatable via
`$CHERRYPICK_HOME`): `~/.cherrypick/data/scout/` (`cache.db`, `watchlist.json`) and
`~/.cherrypick/logs/scout/`.

## Layout

```
packages/scout/
  CLAUDE.md  README.md  config.example.json  pyproject.toml  run.py
  src/cherrypick/
    scout/
      __init__.py  cli.py  config.py  app.py  serve.py  security.py  sse.py  templates.py
      api/          __init__.py  watchlist.py  calendar.py  symbol.py  payoff.py  builder.py
                     screener.py  orders.py
      services/     __init__.py  cache.py  watchlist.py  session.py  metrics_service.py
                     calendar_service.py  candle_service.py  chain_service.py
                     screener_service.py  staging.py  quote_service.py
      analytics/    __init__.py  levels.py  payoff.py  pop.py  strategies.py
      static/       index.html  css/scout.css  js/scout.js  js/payoff.js
        vendor/     lightweight-charts.standalone.production.js  tabulator.min.js
                     tabulator_midnight.min.css  htmx.min.js  alpine.min.js
                     LICENSE-lightweight-charts.txt  LICENSE-tabulator.txt  LICENSE-htmx.txt
                     LICENSE-alpinejs.txt  LICENSES.md
      templates/    calendar.html  symbol.html  builder.html  screener.html  staged.html
  tests/            conftest.py  test_config.py  test_cache_ttl.py  test_cache_candles.py
                    test_watchlist.py  test_security.py  test_self_contained.py
                    test_api_routes.py  test_session.py  test_metrics_service.py
                    test_calendar_service.py  test_calendar_routes.py  test_candle_service.py
                    test_levels.py  test_symbol_routes.py  test_chain_service.py  test_payoff.py
                    test_pop.py  test_payoff_routes.py  test_builder_routes.py
                    test_strategies.py  test_screener_service.py  test_screener_routes.py
                    test_staging.py  test_order_routes.py  test_dry_run_only.py
                    test_quote_service.py  test_sse.py
```

## The earnings calendar (M2)

`GET /api/calendar?days=14` / `GET /partial/calendar` union two sources with different honesty
profiles: **metrics** (fresh, watchlist-scoped — `tastytrade.metrics.get_market_metrics`'s earnings
date/consensus-EPS/IV-rank fields, refreshed on `refresh.metrics_ttl_seconds`) and **Dolt** (broad,
stale-labeled — the `earnings` Dolt database's `earnings_calendar` table, a periodic snapshot rather
than a live feed, covering every symbol Dolt knows about, not just the watchlist). When both name the
same symbol/date the metrics row wins; a row from Dolt alone is always `stale: true`. If Dolt is
unreachable the calendar quietly degrades to metrics-only with a notice, rather than erroring. Expected
move (`0.85 x front straddle`) is fetched only for metrics-sourced (watchlist) rows, via a single
narrow ATM chain snapshot cached under `refresh.calendar_ttl_seconds` — not a full chain fetch, and
never for the broad Dolt rows, which could number in the hundreds.

This is also the first milestone that talks to the broker: `services/session.py`'s `BrokerSession`
holds one process-wide `cherrypick.core.auth.session.SessionManager` over the shared
`cherrypick-broker` keyring service. Missing credentials or a broker hiccup degrade the metrics side
of the calendar to empty rather than raising — the page always renders.

## The symbol view (M3)

`GET /api/symbol/{sym}/candles`, `GET /api/symbol/{sym}/stats`, `GET /partial/symbol/{sym}` (click a
watchlist name, or the Symbol nav tab, which opens the first watchlist symbol). Candle history is
seeded once per symbol from the `stocks` Dolt database's `ohlcv` table (years of daily bars, no
websocket), then topped up from Dolt's cutoff to now via a **short-lived** `DXLinkStreamer` — bounded
by an idle timeout and a hard wall-clock cap, opened on demand and never held resident. If DXLink
can't be reached at all, a single daily bar is synthesized from a snapshot equity quote so the chart
still shows today rather than nothing. A failed top-up attempt is retry-floored independently of the
candle TTL so a stalled DXLink/broker never turns a page load into a retry storm.
`analytics/levels.py` (stdlib-only, no I/O) computes SMA 20/50/200 and support/resistance levels
from swing highs/lows over the cached bars; the stats panel adds `metrics_service`'s IV rank,
liquidity rating, and beta alongside the candle-derived 52-week range and 30-day average volume.
Rendering is Lightweight Charts v5 (`chart.addSeries(CandlestickSeries, ...)` +
`HistogramSeries` for volume), mounted in `static/js/scout.js` on `htmx:afterSwap`.

## Payoff engine + builder (M4)

`analytics/payoff.py` and `analytics/pop.py` (stdlib + dataclasses only, no I/O -- same posture as
`levels.py`) are the analytical core: a `Leg(kind, quantity, price, strike?, expiration?, greeks?)`
list in, `payoff_at`/`payoff_curve`/`breakevens`/`max_profit`/`max_loss`/`net_greeks` out. An option
payoff is *exactly* piecewise-linear with kinks only at strikes, so the curve is evaluated only at
the strikes (exact, not a dense approximation) and breakevens/extrema follow from those points plus
the two analytic tail slopes -- which is also how an unbounded position (naked short call, long call)
is detected rather than guessed at. `pop.py` is a lognormal probability-of-profit (`N(-d2)` via
`math.erf`, no scipy), integrated over the same breakeven-bounded intervals.

`services/chain_service.py` adds the multi-expiration chain cache (`get_option_chain`, TTL
`chain_ttl_seconds`) and batched option-quote snapshots (`get_market_data_by_type`, chunked
~100 symbols/call, TTL 60 s) -- `services/cache.py` gained `async_get_or_fetch` (the sync
`get_or_fetch` primitive's async twin) to back both without hand-rolling the same TTL logic a third
time. Live per-option greeks aren't wired up yet (the SDK's quote/chain calls don't carry them); a
leg without greeks still prices and plots correctly, it just won't show up in the net-greeks panel.

`GET /api/payoff?legs=<json>&spot=&dte=&iv=` is pure computation -- no broker call at all unless
`dte`/`iv` are both given, in which case POP additionally needs `metrics_service.get_risk_free_rate`
(cached once a day); a missing rate falls back to `r=0` rather than failing the whole payoff. Legs are
built interactively at `GET /partial/builder/{sym}` (the Builder nav tab, or "send to builder" from
elsewhere): click a strike in the chain table to add a leg, and `static/js/payoff.js` recomputes and
redraws a hand-rolled SVG payoff curve (no charting library -- a P/L polyline plus a zero line and a
spot marker) on every change.

## The screener (M5)

`GET /api/screener?strategy=put_credit_spread|call_credit_spread|short_put|covered_call` and
`GET /partial/screener` run the plan's five-step compute flow: one batched metrics call across the
whole watchlist; a zero-broker-call pre-filter on IV rank and liquidity; a chain fetch (nearest
30-45 DTE expiration, preferring a standard monthly) and a +/-15-strike quote snapshot, both only for
survivors; candidate construction (0 further calls); and a weighted composite rank. Spot is read from
`candle_service`'s already-cached daily bars rather than a fresh equity quote -- one more reuse of an
existing TTL-cached path instead of a new broker round trip.

`analytics/strategies.py` (stdlib only, no I/O -- same posture as the rest of `analytics/`) generates
each strategy's legs and hands them to `payoff.py`/`pop.py` for pricing. **Short-strike selection has
no live delta to key off** (see the M4 gap on live greeks), so every generator uses the plan's
documented fallback: nearest-OTM-strike to one expected move from spot, not a ~0.30-delta strike.
The directional-edge ("skew") column is a price-based proxy for the same reason -- OTM call mid minus
OTM put mid at matched dollar-distance from spot, mirroring `cherrypick.flies`' `skew_bucket` rather
than a true delta-matched IV skew. Credit is priced at the mid quote with a haircut standing in for a
`cherrypick.core.fees`-style fill-slippage adjustment.

The table (Tabulator, `persistence: "local"` so layout/sort/filter survive a reload) shows the
model POP alongside MEIC's `1 - 2*short_delta` heuristic as a cross-check column, and every symbol
row opens the builder (pre-loaded with that symbol, not yet with the exact screened legs).

**Two regressions caught by live smoke tests against real data, both worth remembering:**

1. DXLink pushes a zero-filled placeholder for the still-forming current-day candle before any real
   trade has printed. `candle_service._dxlink_tail` originally accepted it (only checking for `None`,
   not zero), which wrote a spot of `0.0` into the cache and broke every downstream OTM-strike
   calculation. Fixed by rejecting non-positive OHLC values from DXLink and from the
   snapshot-synthesis fallback; `test_candle_service.py` has a regression test for it.
2. `implied_volatility_30_day` arrives from the SDK as a percentage-point number (e.g. `27.16`
   meaning 27.16%) -- unlike `implied_volatility_index_rank`, which despite its name is already a
   0..1 fraction. Feeding the un-normalized value straight into Black-Scholes sigma inflated the
   expected move ~100x, which pushed every strike-selection target off the far end of the chain and
   silently failed every candidate. Fixed at the boundary in `metrics_service._serialize` (so every
   consumer -- the screener's sigma, the builder's IV pre-fill -- gets an already-correct fraction);
   `test_metrics_service.py` has a regression test for it.

Neither bug was visible from mocked tests alone -- both only showed up once a real `get_market_metrics`/
DXLink response was in the loop, which is why this milestone's verification ran the screener against
the real local Dolt server and a live broker session rather than stopping at green pytest output.

## Order staging (M6)

`POST /api/order/dry-run` validates a leg basket against the broker's own preflight -- buying-power
effect, fees, warnings, **no order created** -- and `GET`/`POST /api/staged` +
`POST /api/staged/delete` persist tickets to the `staged_orders` table for manual execution in the
tastytrade platform. `services/staging.py` is the **single call site in the whole package** that
reaches `cherrypick.core.broker.place_order`, and it passes `live=False` as a hardcoded literal --
never a variable, config value, or request field -- so no path anywhere in this package can submit a
real order. `test_dry_run_only.py` enforces this as a source scan (one `place_order` call site, in
`staging.py`, with `live=False` as an AST-verified literal constant) rather than trusting a comment to
stay true, the same pattern `orchestrator`'s `test_headless.py` uses for its own call-site invariant.

Staging never depends on validation succeeding: `stage_ticket` always saves the ticket, even when the
dry-run call itself fails (missing credentials, a network hiccup, a preflight rejection, an SDK
response-shape change) -- the failure is recorded as the ticket's `dry_run` field rather than blocking
the save, so a broker hiccup never costs a research session its work. Account numbers are masked
(`****1234`) before a dry-run result is ever returned or stored. The builder's leg-basket click-picker
(M4) now carries each leg's OCC option symbol through to "Validate with broker" and "Stage ticket";
every staged leg is an opening trade (buy/sell to open) -- the builder has no closing-order or
stock-leg picker. The Staged nav view lists tickets with a copyable order description and delete.

## Live quotes (M7)

`GET /api/stream` is a Server-Sent Events endpoint pushing live watchlist quote ticks, backed by
`sse.py`'s `QuotePoller`. The poller runs one shared background task per app lifespan, but that task
only exists **while at least one browser tab has the page open**: the first SSE subscriber starts it,
the last one disconnecting cancels it -- zero clients means zero broker calls, the same
connect-gated posture the plan calls for. Each tick is one batched `quote_service.get_quotes` call
(chunked ~100 symbols/call, same discipline as `chain_service`) diffed against the last-sent
snapshot, so only symbols whose quote actually changed get pushed; a quiet tick sends a heartbeat
comment instead, keeping the connection alive through proxies without spamming an idle tab. This is
poll-and-push REST, deliberately not a resident DXLink connection -- the plan's call, since this
surface only needs to exist while a human has the page open, unlike `candle_service`'s bounded DXLink
top-up which fills in chart history regardless of who's watching.

`static/js/scout.js` opens one `EventSource` per page load and fans live ticks out to two places:
the watchlist sidebar (an Alpine-reactive `quotes` map, colored by `change_pct`) and, when the
Symbol view is open for the ticking symbol, the chart's still-forming last daily bar (`close`
updated in place, `high`/`low` extended if the tick breaks the bar's current range) via
`Lightweight Charts`' `series.update()` -- no full re-fetch needed for a live tick.

## Security

Scout carries a handful of narrow mutating routes (watchlist save; order dry-run; staged-ticket
save/delete), so — like `cherrypick.orchestrator`'s settings surface — it is gated beyond plain
loopback binding: every request must carry a loopback `Host` header naming the bound port (else 403),
and every mutating request must additionally carry the per-process CSRF token baked into the page, an
`application/json` content type, and, when present, a matching local `Origin`. See
`src/cherrypick/scout/security.py`.
