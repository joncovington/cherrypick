# console (unified web UI)

The suite's unified reactive web UI: one app covering every module's read models (overview/watchdog,
MEIC, flies, earnings, GEX) plus scout's interactive surfaces (watchlist, screener, builder,
payoff/POP, staged dry-run tickets). It **replaced** them on 2026-08-12: the suite dashboard, the
MEIC/flies/GEX dashboards, the earnings strategy dashboard and scout's web app were deleted, and this
is the suite's only read surface. It still touches none of their code — every module remains a
producer this package reads. `pre-console-only` is the tag that still has them.

**The supervisor keeps this running** as an always-on resident job (`console` in
`state/supervisor-jobs.json`): no clock window and no trading-day gate, since a read surface you can
only open during RTH cannot be used to read the session that just ended. Two things follow that are
easy to break:

- **The heartbeat is load-bearing.** `services/heartbeat.ts` rewrites `state/console.heartbeat` every
  ~15s and the supervisor restarts this process if that mtime goes stale. It is how a *wedged* event
  loop gets caught, which process-liveness cannot see. Do not make it conditional, and do not move it
  before `app.listen` — a heartbeat written before a failed bind reports a console that never came up.
- **`run.py` is a launcher, so node is the supervisor's GRANDchild.** Anything that stops the console
  must kill the process **tree**; terminating only the tracked PID leaves node holding :5070, and every
  supervised restart then dies on `EADDRINUSE`.

Unlike the rest of the suite this package is **Node + TypeScript**, not Python:

- `shared/` — types shared by server and web (`@console/shared`).
- `server/` — Fastify backend, binds **127.0.0.1:5070** (loopback hard-coded; port via `serve.port`
  in `~/.cherrypick/config/console.json`). Serves the built SPA, `/api/*`, and (from M3) `/ws`.
- `web/` — React + Vite SPA.
- `desktop/` — Electron shell (`@console/desktop`), a **window only**: it never starts the server, so
  it can never contend with the supervisor for the port. See its README; the short version is that
  home/port resolution lives in `shared/src/paths.ts` precisely so the shell and the server cannot
  disagree, and that no native module is ever loaded inside Electron (the server is its own process),
  which is what keeps `electron-rebuild` out of the package.
- `run.py` — thin launcher (`python run.py dashboard --serve`) so the supervisor and `/console` never
  need to know about the Node toolchain. Spawns node with `CREATE_NO_WINDOW`, or every restart under
  `pythonw` pops a terminal window.

## Commands

The one package with a Node toolchain — `pip`/`pytest`/`ruff` do not apply here. From this directory:

```bash
pnpm install
pnpm build                        # build shared + server + the SPA + the desktop main process
pnpm dev:server                   # backend on :5070 with reload
pnpm dev:web                      # Vite on :5173, proxying /api and /ws to :5070
pnpm test                         # vitest
pnpm typecheck                    # tsc --noEmit across all three workspaces
python run.py dashboard --serve   # what the supervisor's `console` job invokes
pnpm --filter @console/desktop start   # the desktop window
```

## Data rules

- **Read-only over every other package's data.** Module SQLite stores are opened with
  better-sqlite3 `readonly: true`; JSON state is only ever read. The console's sole writable store is
  `~/.cherrypick/data/console/`.
- **Paper/live isolation**: every trade payload carries `mode` taken from its source DB
  (`paper_trades.db` vs the live DB). Mode is never merged across sources or inferred client-side.
- **A module's own evidence window is the default.** Where a module narrows its analytics to a
  current era or study window, the console's reads default to the same narrowing rather than
  showing every row it can reach — MEIC's `CURRENT_ERA` is the case in hand. Earlier eras stay
  reachable through a visible scope control, so widening is a stated choice and never the quiet
  default. Filtering to nothing is reported as a filtered-out result, not an empty page.
- **Market data**: the console opens its own DXLink session via the official `@tastytrade/api` npm
  SDK (`quoteStreamer`). The Python streamer and its `stream_cache.db` are untouched; the cache is
  read read-only as the off-hours / disconnected fallback.
- **Single source of broker auth**: the console reads THE suite credential — the
  `production:client_secret` / `production:refresh_token` entries under the `cherrypick-broker`
  keyring service, the same entries every Python module reads through and onboarding manages.
  Python-keyring targets aren't addressable from Node, so reads bridge through Python
  (`auth/suiteBridge.ts`). **The console never writes credentials** — there is exactly one setting
  path suite-wide, `python -m cherrypick.core.auth setup`; the console CLI's `set` prints that
  pointer, `probe` re-validates on demand, and `clear` touches only the pre-unification Node
  slots. Scope is detected via a dry-run probe (per-process, never persisted): a
  **read-only** refresh token gets a loud warning and every write-oriented function disables
  itself (staged tickets save without broker dry-run validation; the header shows a read-only
  chip). Scope rides on the refresh token.
  **This package contains no order-placement code paths** — staged tickets are dry-run records in the
  console's own store. It never touches any module's `enable_live_trading`.

## Suite guardrails (apply here too)

Instruction files hold no code; account numbers masked to `****1234`; portable paths only
(`os.homedir()` + `path.join`, never a literal user path); human-voice docs/commits; loopback-only
serving; no MCP/network/AI on any reliability path beyond the broker SDK itself.
