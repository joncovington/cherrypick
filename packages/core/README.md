# cherrypick-core

Shared core library for the **cherrypick** trading suite. Every other Python package in this monorepo
depends on it — the strategy modules (meic, earnings, calendars, contango, weeklyput, and the retired
flies, bwb, pmcc and curve), the streamer, gex, the orchestrator, review, advisor, overview,
technicals, fundamentals, manage and backtest — so the auth,
market-data, fee, risk, calendar, GEX, ledger and reporting concerns are implemented **once**.

> Distribution name is `cherrypick-core`; it imports as the **`cherrypick.core`** namespace package,
> part of the suite-wide `cherrypick.*` namespace that every package shares. Requires Python 3.11+.
> MIT-licensed; its repository is the monorepo, <https://github.com/joncovington/cherrypick>. It is not
> published to PyPI.

## Design invariants
1. **A library, never a service, on any loop decision path.** Loop-path code calls `cherrypick.core` in
   process; it must never add a network/MCP failure mode. (This is why `auth.session` builds the broker
   session lazily and lets callers inject the factory.)
2. **The core imports nothing from a consumer.** Everything a consumer supplies (service name,
   thread-local flag, session factory, snapshots) is **injected or parameterized**, never reached back
   into. `tests/test_core_imports_no_consumer.py` enforces it, deriving the consumers from the
   package directories (see [CLAUDE.md](CLAUDE.md)).
3. **Portable + secret-safe.** No hardcoded machine paths; credentials live in the OS keyring only.

## Layout (native namespace: `cherrypick/` has no `__init__.py`; the package is `cherrypick/core/`)
```
cherrypick/core/
  advice/          bounded, expiring, deterministically validated paper advice
  analysis/        per-arm strategy analysis (streaks, drawdowns, stress, capital)
  auth/            keyring credentials (CredentialStore) + lazy OAuth session (SessionManager)
  broker/          account resolution, option-chain helpers, order build/submit
  calendar/        trading-day / expiration calendar
  clock/           what "now" means, in Eastern, everywhere
  compare/         every arm beside its control (the console's Compare bridge)
  config/          reading a module config key that has more than one accepted spelling
  db/              SQLite connection + additive migrations
  dxfeed/          on-demand DXLink collectors (quotes/greeks/OI/volume; session injected)
  entry/           shared entry-permission primitives (cadence, leg-sign rule)
  events/          scheduled market releases for a session (local files only)
  excursions/      one closed position's mark path, best and worst
  execution/       the one live broker adapter and the fill primitives every live loop shares
  fees/            tastytrade cost model (one home for the fee schedule)
  gex/             gamma-exposure engine
  impliedvar/      model-free implied variance (Cboe's VIX arithmetic, any expiration)
  ledgerstore/     ledger mechanics calendars and pmcc share
  live/            the per-day arm record and dead-man's switch a live loop runs under
  livepositions/   broker positions against the live ledgers; the cross-loop opposite-leg rule
  looplock/        single-instance guards for the suite's loops
  marketregime/    daily-bar market regime (derived, never recorded)
  metrics/         the shared calibration metric bundle
  openingrange/    the 09:30-10:00 ET opening range (derived, never recorded)
  portfolio/       one arm per module held together (correlation, drawdown benefit)
  power/           is this machine on battery
  profiles/        named arm registry + merge engine + calibration comparison
  quotehistory/    the read side of gex's recorded chain quotes
  rangefeatures/   the declared daily range-features study (research/range-features.md)
  regime/          joining a timestamp against the recorded market regime
  regimecuts/      the regime-cuts artifact contract
  risk/            account-level risk primitives (fail-closed deploy cap)
  settlement/      American physical-settlement arithmetic
  spreadbook/      ledger writes calendars, pmcc and curve share
  stats/           arm-vs-control statistics, sessions as the unit
  streamcache/     the shared stream-cache schema + SQLite helpers
  streamer/        the persistent DXLink option-chain streaming engine (packages/streamer runs it)
  streamrequests/  the consumer side of the streamer's subscription registry
  structures/      shared option-structure arithmetic
  trace/           one closed position's entry and management trace
  viz/             money formatting and the section contract
  progress/        how far a long job has got, written by the job and read by anyone
  schemas/         JSON Schema files shared with the console (data, not code)
  american.py      an American option's value (Bjerksund-Stensland), a model never a price
  chaindelta.py    a chain's deltas computed from its own mids
  environment.py   the suite's one market-environment label
  external_report.py  the external-reports contract (one JSON file per report)
  incomerules.py   the income screens' strike rule
  killcriteria.py  a live arm's written, frozen condition for being wrong
  legbars.py       liquidity bars for a leg or a spread
  legmarks.py      one position leg priced at a mark
  procs.py         CPU-bound children at full speed (the EcoQoS opt-out)
  robustness.py    what a declared test states before and reports after
  slices.py        an arm's results cut by setup, with guardrails
  treasury.py      Treasury's daily par yield curve
  valuation.py     an unlevered DCF from plain numbers
  bs.py            Black-Scholes-Merton price, delta and implied vol (a model, never a price)
  home.py          the one resolver for ~/.cherrypick
  jsonio.py        the one atomic JSON writer
  ledgers.py       per-schema ledger readers: the one home for net/cost/capital/session rules
  logs.py          one log-line format for every module
  redact.py        account numbers masked to ****1234 before they reach a log
  vendor.py        the one switch every vendor-fed job and reader follows
```

## How consumers use it
Each consumer depends on `cherrypick-core` as a plain named dependency in its own `pyproject.toml`. The
suite's installer at the repo root installs it with everything else; by hand, `pip install -e
packages/core` has to come first, because pip cannot resolve it from PyPI. A consumer keeps a *thin
shim* (for example `cherrypick/meic/credentials.py`) that instantiates the core class with its own
parameters and re-exports the module-level API its call sites already import.

```python
# a consumer's credentials shim
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore

# own service first, then a pre-rename name, then the suite-wide login ("cherrypick-broker")
store = CredentialStore("meicagent", legacy_service_names=("tastytrade-mcp", SHARED_SERVICE))
get_secret = store.get_secret
set_secret = store.set_secret
missing_secrets = store.missing_secrets
# ...
```

## Develop
```
pip install -e ".[dev]"
pytest
```
