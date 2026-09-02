# M0 spike findings (2026-08-08)

Three risky integrations were spiked before scaffolding. All passed.

## tastytrade JS SDK

- The official package is **`@tastytrade/api`** (v7.0.2, published 2026-05-17 from
  tastytrade/tastytrade-api-js). The similarly named `tastytrade-api` on npm is a stale 0.0.1
  placeholder from the same repo — do not use it.
- `TastytradeClient` exposes the full REST service set (instruments, market metrics, symbol search,
  watchlists, balances/positions, orders, …) plus two streamers:
  - **`quoteStreamer` (QuoteStreamer)** — the one to use. Wraps `@dxfeed/dxlink-api`, works in Node
    (`isomorphic-ws`), subscription types include **Quote, Trade, Greeks, Summary, Profile,
    Underlying, Candle** (candles with from-time backfill). `connect()` fetches the DXLink URL and
    quote token itself from the session.
  - `MarketDataStreamer` — **deprecated** upstream ("use @dxfeed/dxlink-api instead"); avoid.
- Auth is OAuth-first in v7: `ClientConfig` takes `clientSecret` + `refreshToken` + `oauthScopes`,
  and the HTTP client auto-generates/refreshes access tokens. A dedicated OAuth grant with read-only
  scope gives the console a credential that structurally cannot place orders. Legacy
  username/password `SessionService.login()` also exists as a fallback.
- Still to verify live (needs a real credential): end-to-end OAuth login + `quoteStreamer.connect()`
  + a Greeks subscription tick. Planned as the first step of M3.

## Credential store (`@napi-rs/keyring` on Windows)

- v1.3.0 installs with prebuilt binaries on win32/Node 24 — no build step.
- `new Entry(service, user)` → `setPassword` / `getPassword` / `deletePassword` roundtrip verified
  against Windows Credential Manager.

## better-sqlite3 read-only vs the live streamer

- v13.0.3 installs cleanly (prebuilt) on Node 24.
- `stream_cache.db` is **WAL**; opened with `{ readonly: true, fileMustExist: true }` +
  `busy_timeout=2000` while the Python streamer holds it: reads of all tables succeeded
  (stream_quotes/stream_greeks ~8.9k rows each; stream_chain ~31.8k).
- A write attempt correctly fails with `SQLITE_READONLY` — the readonly flag is a hard guarantee,
  not advisory.
