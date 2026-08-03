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
  `~/.cherrypick/data/scout/cache.db`. When later milestones read a Dolt database or the shared stream
  cache, they read read-only and never write it.
- **The streamer comes before API calls, whenever practical.** Once chain/quote services exist
  (M4/M5/M7), prefer the cached/batched path over a fresh broker round trip; the broker API is for
  acting (dry-run) and for confirming what only it can know. `services/cache.py`'s `get_or_fetch` is
  the shared mechanism — TTL cache, stale-serve on fetch failure, honest `as_of`/`stale` on every
  payload.
- **Rate-limit discipline.** One batched metrics call per screener refresh (M5); chains fetched only
  for pre-filter survivors; a manual `?fresh=1` refresh is still floored (`cache.get_or_fetch`'s
  `refresh_floor_seconds`) so a refresh button can't be used to hammer the broker.
- **Credentials in the OS keyring only**, via a `cherrypick.core.auth.session.SessionManager` over the
  shared `cherrypick-broker` keyring service (from M4 onward, when a broker session is first needed).
  Never files, env vars, or logs.
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
- `services/cache.py`'s schema already declares tables later milestones will use (`candles`,
  `candle_meta`, `symbol_meta`, `staged_orders`) alongside the generic `kv_cache` TTL store M1 actually
  exercises — declared once so the schema doesn't need a migration step per milestone.
