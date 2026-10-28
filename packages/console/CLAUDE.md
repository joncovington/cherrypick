# console (unified web UI)

The suite's only read surface: one app over every module's read models — the Live page, the eight
trading modules (flies, MEIC, bwb, earnings, PMCC, calendars, curve, contango), GEX, Options flow,
Charts, Screeners, Compare, Backtest and System — plus the advisor and the reports. Every module
remains a producer this package reads; it touches none of their code. The dashboards it replaced
survive only at the `pre-console-only` tag, and the scout research surfaces are retired too, so **no
path here touches an order** — `dry-run-only.test.ts` pins `postOrderDryRun` to the scope probe alone
and scans every reader. See `docs/parity.md` for what survived the teardown and why, and
[docs/history.md](docs/history.md) for the dated stories behind the rules below.

**The supervisor keeps this running** as an always-on resident job (`console` in
`state/supervisor-jobs.json`), with no clock window or trading-day gate — a read surface only open in
RTH cannot read the session that just ended. Two things are easy to break:

- **The heartbeat is load-bearing.** `services/heartbeat.ts` rewrites `state/console.heartbeat` every
  ~15s and the supervisor restarts the process when it goes stale — the only way a *wedged* event loop
  gets caught. Never make it conditional, and never move it before `app.listen` (a heartbeat written
  before a failed bind reports a console that never came up).
- **`run.py` is a launcher, so node is the supervisor's grandchild.** Anything that stops the console
  must kill the process **tree**; killing only the tracked PID leaves node holding :5070 and every
  supervised restart dies on `EADDRINUSE`.

Unlike the rest of the suite this package is **Node + TypeScript**:

- `shared/` — types shared by server and web (`@console/shared`). The barrel is **browser-safe**:
  the SPA imports it and Vite's dev server loads every module it names, so a Node builtin there
  breaks pages in dev only. Node-only helpers (`paths.ts`) are the `@console/shared/paths` subpath;
  `web/test/sharedBarrelBrowserSafe.test.ts` walks the barrel and fails on a builtin.
- `server/` — Fastify, binds **127.0.0.1:5070** (loopback hard-coded; port via `serve.port` in
  `~/.cherrypick/config/console.json`). Serves the built SPA, `/api/*` and `/ws`.
- `web/` — React + Vite SPA.
- `desktop/` — Electron shell, a **window only**: it never starts the server, so it can never contend
  for the port. Home/port resolution lives in `shared/src/paths.ts` so shell and server cannot
  disagree, and no native module is loaded inside Electron (which keeps `electron-rebuild` out).
- `run.py` — thin launcher (`python run.py dashboard --serve`) so the supervisor never needs the Node
  toolchain. Spawns node with `CREATE_NO_WINDOW`, or every restart under `pythonw` pops a terminal.

## Commands

The one package with a Node toolchain — `pip`/`pytest`/`ruff` do not apply. From this directory:

```bash
pnpm install
pnpm build                        # build shared + server + the SPA + the desktop main process
pnpm dev:server                   # backend on :5070 with reload
pnpm dev:web                      # Vite on :5173, proxying /api and /ws to :5070
pnpm test                         # vitest
pnpm typecheck                    # tsc --noEmit across all three workspaces
python run.py dashboard --serve   # what the supervisor's `console` job invokes
pnpm --filter @console/desktop start   # the desktop window
pnpm ui-check --route /flies/performance --expect "drawdown"   # drive the REAL browser
```

**Confirming a change reached the page** (a front-end change is confirmed in a browser, not by tests
alone):

- **The running server is `server/dist/index.js`, not your source.** A source edit changes nothing
  until `pnpm build` AND a restart; otherwise you are watching the old build behave perfectly. A
  shared-type change also needs `pnpm --filter @console/shared build` first.
- **`pnpm ui-check` drives real Chrome** — clicks, expectations, screenshots, console errors;
  `--dump <file>` writes the rendered DOM. `--card <title text>` crops `--shot` to one grid card
  that has drawn its chart, and fails rather than falling back to the page;
  `scripts/flies_payoff_post.py` captures the flies payoff card this way, and the QuikOptions
  Discord series captures the Options flow page's `today` cards (each titled `<name> — <session>`;
  some from `/post/flow`, a capture-only page outside the shell, with no rail or header and in no
  nav), so changing a card's title text breaks those posts. Prefer `--route` over `--click`: every slide is
  addressable, and on the frame `--click` cannot reach a tab (it skips anything inside a `<nav>`, and
  the rail is one).
- **Under Git Bash, prefix it with `MSYS_NO_PATHCONV=1`**, or `--route /flies` arrives as
  `C:/Program Files/Git/flies` and silently checks nothing.
- **For a reader or endpoint change:** capture the affected endpoints from the RUNNING build, then
  build, restart and re-capture. Every data field should be identical and only clock-derived ones
  (`now`, `ageSeconds`) should move. This is the only check that sees past the `withReadOnlyDb`
  fallback below.

## Data rules

- **Read-only over every other package's data.** Module SQLite stores open with better-sqlite3
  `readonly: true`; JSON state is only read. The sole writable store is `~/.cherrypick/data/console/`.
  Handles are pooled per path and recycled on the file's stamp (`readers/db.ts`), so a module's write
  (a migration included) is seen on the next request; idle handles hold an open file, never an open
  read transaction, so they cannot starve WAL checkpointing.
- **A reader that states a total and its breakdown reads one snapshot** (`withReadSnapshot`). Each
  statement is otherwise its own transaction, so a total from one query and rows from another can
  straddle a module's write: the MEIC summary's hero stopped equalling the sum of its arms
  mid-session (2026-10-16). The snapshot lasts one synchronous read; the summary readers use it.
- **⚠️ `withReadOnlyDb` swallows EVERY throw into its fallback and the request still returns 200.**
  Deliberate (a store may be absent because the module never ran here), but **a broken reader is
  indistinguishable from an empty one**, and a green `vitest` run is not evidence a reader change
  works. It has produced two real defects that both looked healthy. So: after changing a reader, hit
  the endpoint on a rebuilt, restarted server and check the payload is populated, not merely 200; and
  where a reader can meet a store whose shape varies (older paper book, live book, fixture), ask
  `sqlite_master` which tables exist rather than naming one and relying on the catch.
  - **`readOnlyDb`** returns `{status: "ok" | "absent" | "failed"}` for a reader whose emptiness is
    meaningful. It is the single implementation and `withReadOnlyDb` a thin wrapper over it — never
    two copies of the pooling/stamping/eviction logic. Migrate per call site; no sweep. Migrated
    readers return their usual empty shape plus an optional `degraded: {reason}` (absent on healthy
    reads and on legitimately absent stores), so a consumer that ignores it is right unless there is a
    defect.
  - **A failed day resolve must narrow, never widen.** `null` from `latestTradeDate` means "latest
    day" and becomes NO date clause — right for an empty ledger, wrong for a throw, which would widen
    the answer to the whole era (once: 289 rows beside a 34-position day). A thrown resolve now scopes
    to a day that matches nothing: showing nothing is visibly wrong, showing the era looks plausible.
    The `sqlite_master` guard covers a missing table; this covers schema drift inside a present one.
  - **Reader throws are audible.** Each is recorded per store (path, message, count, last seen),
    logged once per distinct message (repeats counted, not re-logged — the SPA polls every few
    seconds), and surfaced in `/api/health`'s `readers` array; empty is healthy. An absent store is
    not recorded, or a fresh machine would warn about every uninstalled module. `/api/health`'s `ok`
    still means only "server is up", so a watchdog reading it does not fail on one bad column. None of
    this replaces the rebuild-and-capture recipe: a reader can return a structurally empty result
    without throwing.
- **The Config page is the one bounded write exception, and holds no write logic.** Every config edit
  and the halt toggle go through the orchestrator as a subprocess (`python -m
  cherrypick.orchestrator.configcli`, JSON in/out, via `services/configBridge.ts` — the same pattern
  and reason as `auth/suiteBridge.ts`). The guarded-pointer table, byte-span splicing that preserves
  `_note`/`_header` and key order, the timestamped backup and the atomic write all stay in
  `configedit.py`. **Never port any of that here** — a second copy of a live-safety rule is free to
  drift.
  - This surface **cannot touch `enable_live_trading`, flies' `live.enabled`/`gate0_confirmed`, or the
    live loss/deploy limits** in either direction: `configedit.GUARDED` refuses them, and the page
    shows them as locked rows with that table's hint.
  - The halt flag (`state/halt-live.flag`) is reachable via `liveops.set_halt`, with **asymmetric
    friction**: setting it is one click (a two-step stop arrives late); clearing it needs the typed
    `RESUME LIVE`, checked on the server as well as the browser. Clearing arms nothing, and the page
    says so.
  - What the page offers is an allow-list (`web/src/pages/Config/fieldMeta.ts`); the suite has no JSON
    schema, so that map is the form's schema. Every field must edit a key some code reads
    (`configFieldsAreRead.test.ts`; "MEIC loop interval" edited a dead key until 2026-09-29). Writes are gated like the orchestrator's settings server
    (loopback Host, CSRF, JSON content type), deliberately **not** on the broker credential scope — a
    config file is not the broker.
- **The Advisor page's two buttons are the second bounded exception, and hold no logic either.** Kill
  an experiment and dismiss a proposal POST to `routes/advisorOps.ts`, which runs `python -m
  cherrypick.advisor <verb>` via `services/advisorBridge.ts`; the lifecycle lives in Python, and
  scheduled runs and the browser use the same door. Both only make the advisor do LESS — there is
  deliberately no way to start, tune or enact anything from the browser. The rest of the page reads
  `data/advisor/advisor.db` (read-only) and the artifacts, and **computes no verdicts**: they come from
  `packages/advisor` via ledger-readers → `group_by_tag` → `qualify_readings`. "Did the loop apply
  this artifact" is one of those verdicts, read from the `enactment` table, never recomputed here (a
  reject-all artifact beside a baseline decision IS enacted). No rows on an older store renders as
  "not scored yet"; only a dropped artifact gets a warning chip. The apply banner compares like with
  like — what is queued for the next session, and whether THIS session's artifact landed — with the
  failure count on the collapsed head; keep the signal on the head.
- **Where a module classifies its own data, ask it — don't re-derive.** `services/screenBridge.ts`
  reads earnings screening metrics via `python -m cherrypick.earnings.screen_report --json`, memoised
  ~2 min; `services/verdictBridge.ts` reads the strategy verdict (`strategy_report --json`) the
  same way, and earnings' history takes its `flagged` column from it. A histogram built here off `scan_log` once named gates that never blocked a candidate alone:
  `scan_log` pools four reason vocabularies and has no sole-blocker column, which `screen_metrics`
  already solves.
- **Mirror a query, bridge a derivation.** A query can be checked against its source by reading both;
  a derivation with its own validation can only be checked by being the one that was validated. Where a
  module states read semantics but no callable surface for them, mirror them in TypeScript, name the
  module function each answers for, keep the module's rules (pmcc: `None` never means zero — a null
  renders as an em dash, never `$0.00`), and **pin the mirror with a test against the module itself**.
  The current mirrors and bridges, one per module, each with its pinning test, are listed in
  [docs/mirrors.md](docs/mirrors.md). Mirror tests skip cleanly and **visibly** when the ledger or
  Python is unavailable.
- **Console preferences are read synchronously, from a local mirror.** The server store
  (`/api/config/prefs`) is the source of truth (it follows you to the desktop shell), but a preference
  fetched late cannot decide the first render — the paper/live default would paint paper and flip to
  live. So `web/src/lib/prefs.ts` keeps a localStorage mirror (hydrated at import, written through,
  reconciled once per session via `usePrefsSync`), never a react-query hook. A `?mode=` in the URL
  always outranks the preference; `useMode` states both directions.
- **Paper/live isolation**: every trade payload carries `mode` from its source DB (`paper_trades.db`
  vs the live DB). Mode is never merged across sources or inferred client-side. **Every slide under
  a page's paper/live toggle follows it** — the shared performance slide included
  (`readers/performance.ts::LIVE_LEDGER`); a module with no live book refuses a live read rather
  than showing paper under a live badge.
- **A module's own evidence window is the default.** Reads default to the module's era/study window
  (MEIC's `CURRENT_ERA`, flies' era model); modules with no era column (earnings, suite report and
  review totals) bound to the suite `data_epoch` via `readers/db.ts::suiteEra`, the lever `calibrate`
  enforces. Earlier eras stay reachable through a visible scope control using the shared `"ALL"`
  convention; widening is a stated choice, never the default. Filtering to nothing is reported as a
  filtered-out result, not an empty page. pmcc scopes its arm comparison and weekly A/B to its own
  `CURRENT_ERA` with an era picker (2026-10-05; `test/pmcc-era.test.ts` pins the copy to the
  module's), and its performance slide to the same era by the stamp on each row (`MODULE_ERA` in
  `readers/performance.ts`, through `core.metrics read --era`); history stays the full record, bounded
  by its date range. Calendars needs no bound until a second era exists.
- **Market data**: the console opens its own DXLink session via the official `@tastytrade/api` SDK
  (`quoteStreamer`). The Python streamer and `stream_cache.db` are untouched; the cache is read
  read-only as the off-hours / disconnected fallback.
  - **The feed aggregates at 1s** (`FEED_AGGREGATION_S`), not the SDK's 10s, which held every event
    type — a live candle included — to one update per symbol per 10 seconds (measured with
    `scripts/probe_candles.py --aggregation 10`).
  - **Candles go to the feed directly** (`addCandleSubscription`/`removeCandleSubscription`): the
    SDK's `unsubscribe()` loops over every event type except Candle, so a candle subscribed through
    it can never be removed.
- **Single source of broker auth.** The console reads THE suite credential (`production:client_secret`
  / `production:refresh_token` under the `cherrypick-broker` keyring service) through Python
  (`auth/suiteBridge.ts`), since Python-keyring targets aren't addressable from Node. **It never writes
  credentials** — the one setting path is `python -m cherrypick.core.auth setup`; the console CLI's
  `set` prints that pointer, `probe` re-validates, `clear` touches only the pre-unification Node
  slots. Scope is detected per process by a dry-run probe, never persisted; a **read-only** refresh
  token gets a loud warning, a read-only status-bar chip, and every write-oriented function disables
  itself. **No order-placement code path exists here**, and it never touches any module's
  `enable_live_trading`.

## Pages and tables

- **Before changing a page,** read its section in [docs/pages.md](docs/pages.md): the shell grid
  and rail, view modes, each module's tabs and slides, advised-book resolution
  (`readers/experimentIndex.ts` is the one place an advised tag is resolved), and the book-review
  surfaces.
- **Before changing a trade or history table,** read [docs/tables.md](docs/tables.md). Money is
  derived on the server, never in a component. Each module on the trade table standard has a
  trade-standard test shown to fail. A server-paged table sorts on the server, never in the browser.

## Package guardrails

Suite guardrails apply (root CLAUDE.md). Package-specific: **loopback-only serving**; the console
**computes no verdicts** — it renders what the modules decided, and where it mirrors a module's queries
it says so and is checked against them.
