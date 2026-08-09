# console (unified web UI)

The suite's unified reactive web UI: one app covering every module's read models (overview/watchdog,
MEIC, flies, earnings, GEX) plus scout's interactive surfaces (watchlist, screener, builder,
payoff/POP, staged dry-run tickets). Built to replace the per-module dashboards and scout; during the
transition it runs **in parallel** with them and touches none of their code.

Unlike the rest of the suite this package is **Node + TypeScript**, not Python:

- `shared/` — types shared by server and web (`@console/shared`).
- `server/` — Fastify backend, binds **127.0.0.1:5070** (loopback hard-coded; port via `serve.port`
  in `~/.cherrypick/config/console.json`). Serves the built SPA, `/api/*`, and (from M3) `/ws`.
- `web/` — React + Vite SPA.
- `run.py` — thin launcher (`python run.py dashboard --serve`) so the serve-dashboard command and the
  orchestrator never need to know about the Node toolchain.

Commands (from this directory): `pnpm install`, `pnpm build`, `pnpm dev:server` + `pnpm dev:web`
(Vite on :5173 proxying to :5070), `pnpm test`, `pnpm typecheck`.

## Data rules

- **Read-only over every other package's data.** Module SQLite stores are opened with
  better-sqlite3 `readonly: true`; JSON state is only ever read. The console's sole writable store is
  `~/.cherrypick/data/console/`.
- **Paper/live isolation**: every trade payload carries `mode` taken from its source DB
  (`paper_trades.db` vs the live DB). Mode is never merged across sources or inferred client-side.
- **Market data**: the console opens its own DXLink session via the official `@tastytrade/api` npm
  SDK (`quoteStreamer`). The Python streamer and its `stream_cache.db` are untouched; the cache is
  read read-only as the off-hours / disconnected fallback.
- **Own credential, read-only intent**: broker auth pairs the OAuth application's shared client
  secret with the console's **own read-only refresh token** (scope rides on the refresh token, and
  tastytrade allows one secret but many refresh tokens per application). Both are stored under the
  `cherrypick-console` service in the OS credential store (`@napi-rs/keyring`).
  **This package contains no order-placement code paths** — staged tickets are dry-run records in the
  console's own store. It never touches any module's `enable_live_trading`.

## Suite guardrails (apply here too)

Instruction files hold no code; account numbers masked to `****1234`; portable paths only
(`os.homedir()` + `path.join`, never a literal user path); human-voice docs/commits; loopback-only
serving; no MCP/network/AI on any reliability path beyond the broker SDK itself.
