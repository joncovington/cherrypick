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
      __init__.py  cli.py  config.py  app.py  serve.py  security.py  templates.py
      api/          __init__.py  watchlist.py  calendar.py
      services/     __init__.py  cache.py  watchlist.py  session.py  metrics_service.py
                     calendar_service.py
      static/       index.html  css/scout.css  js/scout.js
        vendor/     lightweight-charts.standalone.production.js  tabulator.min.js
                     tabulator_midnight.min.css  htmx.min.js  alpine.min.js
                     LICENSE-lightweight-charts.txt  LICENSE-tabulator.txt  LICENSE-htmx.txt
                     LICENSE-alpinejs.txt  LICENSES.md
      templates/    calendar.html
  tests/            conftest.py  test_config.py  test_cache_ttl.py  test_watchlist.py
                    test_security.py  test_self_contained.py  test_api_routes.py
                    test_session.py  test_metrics_service.py  test_calendar_service.py
                    test_calendar_routes.py
```

The screener, symbol, and builder surfaces (with their own `api/`/`services/`/`analytics/` modules)
land in later milestones on this branch — their nav nodes already render in `static/index.html` but
the routes don't exist yet.

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

## Security

Scout carries a handful of narrow mutating routes (watchlist save; later, order dry-run and staged-
ticket save/delete), so — like `cherrypick.orchestrator`'s settings surface — it is gated beyond plain
loopback binding: every request must carry a loopback `Host` header naming the bound port (else 403),
and every mutating request must additionally carry the per-process CSRF token baked into the page, an
`application/json` content type, and, when present, a matching local `Origin`. See
`src/cherrypick/scout/security.py`.
