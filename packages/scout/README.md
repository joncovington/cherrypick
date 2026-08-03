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
from M5 onward), `dolt.{host,port}` (used from M2 onward for the earnings-calendar/history reads).

Runtime data lives under the shared suite home (`cherrypick.core.home`, relocatable via
`$CHERRYPICK_HOME`): `~/.cherrypick/data/scout/` (`cache.db`, `watchlist.json`) and
`~/.cherrypick/logs/scout/`.

## Layout

```
packages/scout/
  CLAUDE.md  README.md  config.example.json  pyproject.toml  run.py
  src/cherrypick/
    scout/
      __init__.py  cli.py  config.py  app.py  serve.py  security.py
      api/          __init__.py  watchlist.py
      services/     __init__.py  cache.py  watchlist.py
      static/       index.html  css/scout.css  js/scout.js
        vendor/     lightweight-charts.standalone.production.js  tabulator.min.js
                     tabulator_midnight.min.css  htmx.min.js  alpine.min.js
                     LICENSE-lightweight-charts.txt  LICENSE-tabulator.txt  LICENSE-htmx.txt
                     LICENSE-alpinejs.txt  LICENSES.md
  tests/            conftest.py  test_config.py  test_cache_ttl.py  test_watchlist.py
                    test_security.py  test_self_contained.py  test_api_routes.py
```

The calendar, screener, symbol, and builder surfaces (with their own `api/`/`services/`/`analytics/`
modules and `templates/` htmx partials) land in later milestones on this branch — the nav nodes for
them already render in `static/index.html` but their routes don't exist yet.

## Security

Scout carries a handful of narrow mutating routes (watchlist save; later, order dry-run and staged-
ticket save/delete), so — like `cherrypick.orchestrator`'s settings surface — it is gated beyond plain
loopback binding: every request must carry a loopback `Host` header naming the bound port (else 403),
and every mutating request must additionally carry the per-process CSRF token baked into the page, an
`application/json` content type, and, when present, a matching local `Origin`. See
`src/cherrypick/scout/security.py`.
