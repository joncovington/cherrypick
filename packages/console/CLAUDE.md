# console (unified web UI)

The suite's unified reactive web UI: one app covering every module's read models (overview/watchdog,
MEIC, flies, earnings, GEX) plus the research surfaces inherited from the retired scout
package (watchlist, screener, builder, payoff/POP, staged dry-run tickets). It **replaced** them on 2026-08-12: the suite dashboard, the
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
- **The Config page is the one bounded exception, and it holds no write logic of its own.** Every
  config edit and the halt toggle go out through the orchestrator's own surface as a subprocess
  (`python -m cherrypick.orchestrator.configcli`, JSON in/out — `services/configBridge.ts`, the same
  bridging pattern and the same reason as `auth/suiteBridge.ts`). That is what makes the exception
  narrow: the guarded-pointer table, the byte-span splicing that preserves each config's
  `_note`/`_header` documentation and key order, the timestamped backup and the atomic write all stay
  in `configedit.py`, where they are already tested. **Never port any of that into this package** —
  a second copy of a live-safety rule is a second copy free to drift. It follows that this surface
  **still cannot touch `enable_live_trading`, flies' `live.enabled`/`gate0_confirmed`, or the live
  loss/deploy limits** in either direction: `configedit.GUARDED` refuses them, and the page renders
  them as locked rows carrying that table's own hint. The halt flag (`state/halt-live.flag`) is
  reachable, because its whole design is that a click may toggle it — via `liveops.set_halt`, with
  **asymmetric friction**: setting it is one click (a stop that takes two steps arrives late),
  clearing it requires the typed `RESUME LIVE` confirmation, checked on the server and not only in
  the browser. Clearing it arms nothing by itself, and the page says so. What the page *offers* to
  edit is an allow-list (`web/src/pages/Config/fieldMeta.ts`) covering the settings that actually
  change between sessions; the suite has no JSON schema anywhere, so that map is the form's schema.
  Config writes are gated exactly as the orchestrator's settings server gates its own (loopback Host,
  CSRF, JSON content type) and deliberately **not** on the broker credential scope — that describes
  what a token may do at the broker, and a config file is not the broker.
- **Where a module already classifies its own data, ask it — don't re-derive it.** `services/
  screenBridge.ts` reads the earnings screening metrics by invoking
  `python -m cherrypick.earnings.screen_report --json` (same bridging pattern and the same reason as
  `configBridge.ts`), memoised ~2 min because classifying the whole scan history costs a subprocess
  and the answer moves only when a scan runs. The card it feeds used to build its own histogram
  straight off `scan_log` and got the answer wrong in a way that looked authoritative — naming gates
  that have never blocked a candidate **alone**, which a threshold change cannot rescue. Two
  structural causes, neither fixable in a SQL query here: `scan_log` pools four incompatible reason
  vocabularies, and a raw count has no sole-blocker column. `screen_metrics` already solves both, so
  the authority stays there and this package renders it.
- **Console preferences are read synchronously, from a local mirror.** The server store
  (`/api/config/prefs`) is the source of truth — it is what makes a preference follow you to the
  desktop shell — but a preference that only arrives after a fetch cannot decide what the FIRST
  render looks like. Defaulting the paper/live toggle is the sharp case: reading it late paints the
  paper book and flips to live a moment later, which on a trading surface is worse than having no
  preference at all. So `web/src/lib/prefs.ts` keeps a localStorage mirror (hydrated at import,
  written through on change, reconciled from the server once per session via `usePrefsSync`), the
  same shape the card-collapse state already uses. Preferences deliberately do **not** live in a
  react-query hook. A `?mode=` in the URL always outranks the preference — a link to a page is a link
  to the mode it names — so `useMode` states both directions explicitly.
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
