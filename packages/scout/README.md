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
for the big picture. This package was built milestone-by-milestone on `feature/scout` (see git
history); M1 through M8 (skeleton, calendar, symbol view, payoff/builder, screener, order staging,
live quotes, and this polish pass) are all landed on the branch. The file tree below describes what
actually exists.

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
  docs/             strategy-screening-parameters.md
  src/cherrypick/
    scout/
      __init__.py  cli.py  config.py  app.py  serve.py  security.py  sse.py  templates.py
      api/          __init__.py  watchlist.py  calendar.py  symbol.py  payoff.py  builder.py
                     screener.py  orders.py
      services/     __init__.py  cache.py  watchlist.py  session.py  metrics_service.py
                     calendar_service.py  candle_service.py  chain_service.py
                     screener_service.py  sector_service.py  staging.py  quote_service.py
                     streamcache.py
      analytics/    __init__.py  describe.py  levels.py  narrative.py  payoff.py  pop.py
                     strategies.py  templates.py  trend.py
      static/       index.html  css/scout.css  js/scout.js  js/payoff.js
        vendor/     lightweight-charts.standalone.production.js  tabulator.min.js
                     tabulator_midnight.min.css  htmx.min.js  alpine.min.js
                     LICENSE-lightweight-charts.txt  LICENSE-tabulator.txt  LICENSE-htmx.txt
                     LICENSE-alpinejs.txt  LICENSES.md
      templates/    calendar.html  symbol.html  builder.html  screener.html  staged.html
  tests/            conftest.py  test_config.py  test_cache_ttl.py  test_cache_candles.py
                    test_cache_symbol_meta.py
                    test_watchlist.py  test_security.py  test_self_contained.py
                    test_api_routes.py  test_session.py  test_metrics_service.py
                    test_calendar_service.py  test_calendar_routes.py  test_candle_service.py
                    test_levels.py  test_symbol_routes.py  test_chain_service.py  test_payoff.py
                    test_pop.py  test_payoff_routes.py  test_builder_routes.py
                    test_strategies.py  test_screener_service.py  test_screener_routes.py
                    test_staging.py  test_order_routes.py  test_dry_run_only.py
                    test_quote_service.py  test_sse.py  test_streamcache.py  test_trend.py
                    test_narrative.py  test_describe.py  test_templates.py  test_sector_service.py
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
seeded once per symbol from DXLink's own `Candle` history feed (`refresh.candles_backfill_days`,
default 12 months) via a **short-lived** `DXLinkStreamer` — bounded by an idle timeout and a hard
wall-clock cap, opened on demand and never held resident — then topped up incrementally from the
last cached bar on later fetches. (Originally the deep seed came from the `stocks` Dolt database's
`ohlcv` table; its date-led primary key made the per-symbol query full-scan 28.5M rows, ~2 minutes
measured, timing out on every symbol — so the seed moved to the broker's own feed, per the
suite-wide prefer-tastytrade-sources rule. Dolt remains only on the calendar path.) If DXLink
can't be reached at all, a single daily bar is synthesized from a snapshot equity quote so the chart
still shows today rather than nothing. A failed attempt is retry-floored independently of the
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

A chip-filter panel sits above the table: **IV Rank** (`<50`/`>=50`),
**Liquidity** (not/somewhat/very, mapped from the 1-4 `liquidity_rating`), and **Cap size**
(small/medium/large/mega at the conventional $2B/$10B/$200B breakpoints, from the metrics call's
`market_cap`). All three filter in the zero-broker-call pre-filter step -- no chain is fetched for a
name a chip excludes. An explicit chip selection *replaces* that dimension's config default gate
(picking "Not liquid" must actually show not-liquid names, which `min_liquidity_rank` would
otherwise silently veto); an empty chip group leaves the config default in force. Unknown bucket
names in the query are a 400, never silently ignored. The parameter conventions behind the buckets
are catalogued in [docs/strategy-screening-parameters.md](docs/strategy-screening-parameters.md).

Two more chips filter after the pre-filter, since neither is available from the batched metrics
call: **Scan** (bullish/neutral/bearish, the symbol's own 1M `price_ma_count` trend label,
mildly-bullish/mildly-bearish collapsed into their base direction -- filtered right after candles
are fetched, so a non-matching symbol never reaches a chain fetch) and **Sentiment**
(bullish/neutral/bearish, `strategies.directional_edge`'s chain-implied skew tilt, dead-zoned at
0.25% of spot -- filtered after the candidate's strikes are windowed, since it needs real option
quotes). Sentiment's bucket thresholds are scout's own choice, not reverse-engineered from any
observed reference-platform value -- there's no screenshot evidence for a skew-sentiment chip.

A **Sector** chip filters in the zero-broker-call pre-filter, alongside IV/liquidity/cap:
`sector_service.get_sector_map` is a tastytrade-owned source (the suite's prefer-tastytrade-sources
rule), not a third-party one -- `PublicWatchlist.get(session)` (one call, live-verified 2026-08-05)
returns every public watchlist tastytrade publishes, and filtering `group_name == "Sectors"` gives
the eleven standard sector groupings (Technology, Healthcare, Energy, ...) with their member
symbols. Stored in the `symbol_meta` table `cache.py`'s schema reserved for exactly this since M3
(previously unused) rather than a generic cache blob -- one bulk fetch upserts every row together,
and staleness is checked via a table-wide MAX(`fetched_at`) rather than a per-symbol TTL, since
every row is refreshed in the same call. A symbol absent from every sector watchlist (an ETF, an
index, an unclassified name) is excluded while the Sector filter is active -- the same "missing
can't prove membership" posture the Cap chip already follows.

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

## Stream-cache-first quotes + loading states (latency pass)

`services/streamcache.py` is a read-only client for the suite's **shared** stream cache
(`cherrypick.core.streamcache`, `~/.cherrypick/data/marketdata/stream_cache.db`) -- the standalone
streamer daemon's output, when that daemon happens to be running. `quote_service.get_quotes` checks
it first for every requested symbol and only falls back to a direct `get_market_data_by_type` call
for symbols missing there or older than `refresh.stream_cache_max_age_seconds` (default 10 s). This
had been an open gap: scout already registered its watchlist with the streamer
(`services/watchlist.py`'s `write_request` call, present since M1) so the shared cache would warm
for scout's symbols if the daemon were running, but nothing in scout's own read path ever checked
that cache -- every quote request still went straight to the broker regardless. Verified against the
real, live-maintained shared cache (not a mock): `services/streamcache.read_equity_quotes` correctly
serves real SPX/XSP/QQQ spot rows written by the actually-running streamer daemon on this machine,
and a direct `QuotePoller` integration test against that same data published a tick without ever
touching a broker session.

One coverage caveat, documented in CLAUDE.md: the streamer's subscription registry promises each
requested underlying a spot **and an ATM option window**, sized for near-term/0DTE-style consumers
like MEIC -- not necessarily the 30-45 DTE monthly expirations this package's screener targets. So
today only equity/underlying spot (`quote_service`, the SSE feed) has a reliable win; option-level
quotes/greeks (`chain_service`, which would also fix the still-open "no live greeks" gap via the
shared cache's `stream_greeks` table) would need resolving each OCC symbol to its DXLink
streamer-symbol via `stream_chain` and confirming actual coverage first, rather than assuming the
ATM window lines up -- left as a deliberate follow-up, not done speculatively.

Also added: lightweight loading states (a small pulsing `.loading` indicator, not a blocking overlay)
on every view with a real fetch in flight -- symbol chart/stats, the screener's first-ever scan, the
builder's chain load, and the staged-ticket list -- so a slow cold fetch (DXLink candle backfill, an
uncached chain) reads as "still working" rather than a blank or frozen page. An existing screener
table stays visible during a strategy-switch refresh rather than being wiped, so only the very first
load shows the indicator.

### `GET /api/symbol/{sym}/quote` -- decoupling the builder from candle backfill

Measured, not assumed: cold chain + quote fetches (`chain_service`) were never the bottleneck --
1.66 s for a full multi-expiration chain structure, 0.14 s for a batched 66-leg quote snapshot,
against a symbol touched for the first time all session. The actual latency the builder felt on
every symbol selection was `GET /api/symbol/{sym}/stats`, which calls `candle_service.get_candles`
*first* (for week52-range/avg-volume) before metrics -- and that call's cold-cache DXLink backfill
took **38.8 s** for one live symbol in testing. The builder never even reads those two fields; it
only ever used `stats.last_close` and `stats.iv_30d`.

`GET /api/symbol/{sym}/quote` answers exactly that -- `quote_service` (stream-cache-first, REST
fallback, **no DXLink at all**) for spot, `metrics_service` for IV -- and `static/js/payoff.js`'s
`mountBuilderView` now calls it instead of `/stats`. Measured end to end against the real broker for
a symbol untouched all session: **38.9 s -> 5.9 s**, a 6.6x reduction, with `/stats` left unchanged
for the Symbol view (which genuinely needs the candle-derived fields `/quote` deliberately omits).
`test_symbol_routes.py::test_api_symbol_quote_never_touches_candle_service` pins this as a
regression guard, not just a one-time measurement.

## S/R levels on the chart + candidate trend models

`GET /api/symbol/{sym}/levels` finally wires up `analytics/levels.py` -- computed and tested since
M3, but never actually called by any route until this pass. It returns the clustered swing-extrema
support/resistance levels, the nearest level on each side of the last close (the two label values
the stats panel now shows as **Support** / **Resistance**), and SMA 20/50/200 as chart-ready line
series. The symbol view draws the SMAs as overlays and the strongest three levels per side (most
touches first) as dashed price lines -- every clustered swing at once would wallpaper the chart.

`analytics/trend.py` holds five rival implementations of a "triple moving average" trend
classifier -- alignment-ordering, MACD-state, TEMA, TRIX, and `price_ma_count` -- fitted against
observed labels from a reference platform's proprietary trend indicator, not shipped as truth:
`classify_all(bars)` produces every candidate's labels for one symbol, the row a label-matching
experiment scores. Two modeling decisions are pinned by tests because the first draft got them
wrong: MACD's zero-line is primary (a decelerating decline must read mildly_bearish, not
mildly_bullish), and TEMA requires `4 * period` bars of warmup (a barely-long-enough series leaves
the triple-smoothed stage seed-dominated -- a synthetic 400-bar monotonic downtrend classified as
*bullish* under TEMA(126) before the floor existed). `price_ma_count` is the current best-fitting
candidate (68% exact match on 25 labeled rows at both horizons) and is what `GET
/api/symbol/{sym}/analysis` and the screener's **Scan** chip both use today -- clearly provisional
(one 25-row fit, pending re-validation against a fresh same-day-close label batch), not a graduated
winner. The Scan chip collapses the five-grade label to bullish/neutral/bearish, mildly-* joining
its base direction.

## Plain-language symbol analysis

`GET /api/symbol/{sym}/analysis` generates the narrative the symbol view shows under the chart: an
optional **scan headline** (CCI dip/rally within a trend when CCI(20) crosses ±100 -- the more
specific setup, checked first -- else bullish/bearish trend-following when the horizons disagree),
and **Price Action** as up to three bullets. Bullet one is the priority-picked concrete event:
200-day MA cross today > 50-day MA cross > gap on ≥1.5x average volume > S/R level break (with role
reversal: "broke above its 102.00 resistance, which now becomes support") > ≥5% three-session move
> trading at a nearby level > a trend + support/resistance fallback, with an earnings-timing suffix
when the report is today/tomorrow. Bullets two and three add the strongest **options context** (IV
vs realized ratio at ≥1.25x/≤0.8x, IV-rank extremes, skew lean -- the layer a price-only narrative
lacks) and **technical/market context** (golden/death cross, 52-week proximity or a new closing
high/low, ≥5-session streaks, tightest-20-day-range squeeze, ≥12% extension from the 50-day, and
true relative strength vs cached SPX closes over 3 months -- computed, not the reference platform's
same-named trend composite). `GET /api/symbol/{sym}/warnings?expiration=` serves the builder's
event warnings: an earnings report or ex-dividend date landing inside the chosen expiration
(early-assignment note included), rendered above the chain and refreshed on every expiration
change. All of it is generated from data scout already computes (`analytics/narrative.py`,
pure/stdlib, no free-written text -- every sentence carries its numbers); metrics fields that were
previously fetched-and-discarded (`historical_volatility_30_day`, `corr_spy_3month`, dividend
dates/rate) are now kept for exactly this. Trend wording uses scout's own provisional
`price_ma_count` classifier and is labeled as such in the UI.

## Strategy cards (returns, POW, model greeks, checklist, plain-language text)

`analytics/describe.py` adds the strategy-card math and text: **raw and annualized return**
(annualized is *compounded*, `(1 + credit/max_risk)^(365/dte) - 1` -- reverse-engineered from a
reference platform's own displayed pairs and pinned by two observed fixtures in `test_describe.py`;
the UI keeps the asterisk because compounding assumes the same trade repeats all year),
**probability of worthless** (the premium-seller's POW: every short option expiring OTM, an
interval probability under the same lognormal as `pop.py`), **model greeks** (Black-Scholes
delta/gamma/theta-per-day/vega from strike/spot/IV/T/r -- scout has no live greeks feed, and a
clearly-labeled model greek beats a silently absent one), a **strategy explanation** ("This is a
bullish strategy with limited risk of $X... profits if the stock closes above $Y... Z% model
probability"), the wheel-style **short-put suggestion** ("Consider selling the ... put to
potentially acquire the stock at a N% discount..."), and a pass/warn/fail **checklist** (POW,
annualized return, earnings-inside-expiration, spread width -- thresholds documented as guesses in
the module docstring). `/api/payoff` now returns all of it (suggestion only for a lone short put
with `symbol`/`expiration` params), the builder renders the card under the payoff SVG, and the
screener gains an `Annualized*` column on every credit candidate.

## Strategy checklist (both variants, in the builder)

The builder's strategy card now grades every basket through `/api/payoff`'s checklist: the
**income** variant (POW / annualized / earnings / spread) for a lone short option, the
**directional** variant (stock-trend and market-trend alignment / earnings / spread) for
everything else, auto-selected by shape. Spread is graded on the **net combo** bid/ask
(`describe.combo_spread_pct`) per the observed reference behavior; trend rows are cache-only reads
of the provisional 1M classifier for the symbol and for SPX (a symbol with no cached candles warns
rather than guessing); every threshold is the calibrated set recorded in `describe.py`'s
docstring. The direction read probes the +/-40% tails -- a live-caught bug fix, pinned by a
regression test, after +/-10% probes landed both inside an OTM put spread's max-profit plateau and
called a bullish vertical "neutral".

## Order editor (strategy templates, leg table, flip/width)

The builder's leg list is now an **editor**: a per-leg table (Buy/Sell, quantity, expiry, strike
and type dropdowns snapped to the loaded chain, editable premium), quick add-leg buttons
(+Call/+Put at the nearest ATM strike, +Stock), a **strategy dropdown** applying any of eleven
templates (`analytics/templates.py` -- long call/put, short put, covered call, credit/debit
verticals, short straddle/strangle, iron condor) built server-side via
`GET /api/symbol/{sym}/template` against the live chain with delta-targeted strikes, plus
**Flip Strategy** (mirror around spot, snapped to listed strikes), **-/+ Width**, and **Reset**.
A **Price by** toggle switches every non-manually-priced leg between mid and the natural side
(sell at bid / buy at ask); editing a premium by hand pins it until that leg's strike or type
changes. Changing strike/type re-pulls the leg's quote and live greeks from the chain, so the
payoff, checklist, and greeks panels stay honest as the basket is edited.

## Three-suggestion cards by sentiment

Sentiment chips (Bullish / Bearish / High Implied Volatility) above the builder fetch
`GET /api/symbol/{sym}/suggestions`: three candidate structures per sentiment, each card carrying
the payoff engine's own numbers (cost/credit, max reward/risk with unbounded flagged honestly, POP,
annualized when a defined-risk credit) and a mini payoff thumbnail; clicking a card loads its legs
into the editor. The sentiment -> template mapping follows the reference platform's own three-card
sets -- single option / debit vertical / credit vertical for the directional sentiments -- with one
deliberate deviation: High IV suggests the ~16-delta short strangle rather than the ATM straddle
(the user's call; OTM strikes leave room to be wrong where ATM ones don't).

## Income grid (short-put candidates by risk tier)

`GET /api/symbol/{sym}/income-grid?spot=&kind=put` serves the risk-tolerance x DTE-bucket strike
grid the builder renders below the strategy card: for each bucket (20-39 / 40-70 / 71-180 days) the
nearest expiration inside the window, and per tier the strike whose **live delta** lands nearest
~15 (conservative) / ~25 (optimal) / ~35 (aggressive) -- a rule reverse-engineered from a reference
platform's displayed grids (its picks' live deltas clustered tightly at those targets across
symbols and tenors, and its published covered-call guidance independently names 15-20 delta as
conservative; evidence recorded at `chain_service.INCOME_TIERS`). Cells carry mid, credit,
raw/annualized return, POW, and delta; clicking one loads it as the builder's leg. Verified live:
scout's grid reproduced 7 of 9 of the reference's own STM cells exactly, the other two one strike
adjacent (one-bar-stale greeks).

## Verification (M8)

All five surfaces (calendar, screener, symbol, builder, staged) were exercised end to end against a
live broker session and the real local Dolt server, not just green pytest output -- the same posture
the M5 screener verification set. Watchlisted `AAPL MSFT NVDA AMD TSLA`: sidebar quotes ticked over
SSE within one poll interval; the calendar returned 2800+ Dolt rows plus fresh metrics rows for the
watchlist; the symbol view rendered real candles/stats; a builder leg basket priced a real put credit
spread (`/api/payoff` returned correct max profit/loss/breakeven/POP for the actual quoted strikes);
"Validate with broker" returned a real preflight (buying-power effect, fees, one `tif` warning, masked
account) with **no order created** (confirmed both by the preflight response itself -- id `-1`, never
a live order id -- and by `test_dry_run_only.py`'s source-scan guarantee that no live path exists to
create one); a staged ticket saved with that dry-run result attached, listed, and deleted cleanly.

One honest finding from that run, worth recording rather than smoothing over: the plan's `<30 s cold`
screener estimate undersold real DXLink latency for a multi-symbol watchlist. A screener request
against a fully cold cache (candle backfill via DXLink for all 5 symbols, an uncached options chain,
metrics, and quotes) took **~2.5 minutes**, not 30 seconds -- `candle_service`'s bounded DXLink tail
top-up is single-flight and timeout-capped per symbol (see its own CLAUDE.md entry), so a five-symbol
cold start pays that cost five times serially. A **warm** request (candle/metrics/chain all cached,
quotes past their 60 s TTL) returned in ~17 seconds; a **fully warm** request (nothing past any TTL)
returned in under 200 ms. This is real broker/DXLink round-trip latency, not a call storm or a
correctness bug -- the log showed exactly the batched, TTL-respecting call pattern the plan calls for,
and every cache layer behaved as designed. Anyone hitting a slow first screener load after adding new
watchlist symbols is seeing this, not a hang.

## Security

Scout carries a handful of narrow mutating routes (watchlist save; order dry-run; staged-ticket
save/delete), so — like `cherrypick.orchestrator`'s settings surface — it is gated beyond plain
loopback binding: every request must carry a loopback `Host` header naming the bound port (else 403),
and every mutating request must additionally carry the per-process CSRF token baked into the page, an
`application/json` content type, and, when present, a matching local `Origin`. See
`src/cherrypick/scout/security.py`.
