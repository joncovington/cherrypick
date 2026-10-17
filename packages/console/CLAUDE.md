# console (unified web UI)

The suite's only read surface: one app over every module's read models — overview/watchdog, MEIC,
flies, earnings, PMCC, calendars, curve, BWB, GEX — plus the advisor and the reports. Every module
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

- `shared/` — types shared by server and web (`@console/shared`).
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
  Discord series captures the Options flow page's `today` cards (each titled `<name> — <session>`),
  so changing a card's title text breaks those posts. Prefer `--route` over `--click`: every slide is
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
  `packages/advisor` via ledger-readers → `compare_profiles` → `qualify_readings`. "Did the loop apply
  this artifact" is one of those verdicts, read from the `enactment` table, never recomputed here (a
  reject-all artifact beside a baseline decision IS enacted). No rows on an older store renders as
  "not scored yet"; only a dropped artifact gets a warning chip. The apply banner compares like with
  like — what is queued for the next session, and whether THIS session's artifact landed — with the
  failure count on the collapsed head; keep the signal on the head.
- **Where a module classifies its own data, ask it — don't re-derive.** `services/screenBridge.ts`
  reads earnings screening metrics via `python -m cherrypick.earnings.screen_report --json`, memoised
  ~2 min. A histogram built here off `scan_log` once named gates that never blocked a candidate alone:
  `scan_log` pools four reason vocabularies and has no sole-blocker column, which `screen_metrics`
  already solves.
- **Mirror a query, bridge a derivation.** A query can be checked against its source by reading both;
  a derivation with its own validation can only be checked by being the one that was validated. Where a
  module states read semantics but no callable surface for them, mirror them in TypeScript, name the
  module function each answers for, keep the module's rules (pmcc: `None` never means zero — a null
  renders as an em dash, never `$0.00`), and **pin the mirror with a test against the module itself**:
  - `server/test/pmcc-mirror.test.ts` — open count, book set and each book's net vs `run.py headline`,
    to the cent. Compares empty against empty until pmcc opens a position under its current design, so
    treat it as armed, not as evidence.
  - `server/test/meic-mirror.test.ts` — `readers/meic.ts` (the largest mirror) per-arm trades,
    sessions, gross, fees and net vs `python run.py headline --era ALL`. Resolved rows are an
    allow-list (`pnl IS NOT NULL`), never a deny-list of statuses, or open rows leak fees with no gross.
  - `server/test/flies-mirror.test.ts` — `analytics/fliesPayoff.ts` is a port of `fly.py`'s payoff
    core, pinned against `fly.py`'s own fixtures (run unconditionally) and against
    `fly.position_pnl`/`position_floor`/`book_floor` over the ledger to the cent. The floor is computed
    on the module's strike-anchored scan (`scanPrices`/`bookFloor`), never the 120-point display grid.
    One thing is deliberately not equalised: which zone is "the band" on an exact peak tie is the
    module's recorded pick (`fly_books.band_low/high`), and changing that rule is a declared-boundary
    change for flies; the test tolerates a different pick only when both peaks agree to the cent. The
    forest sentence says what the floor knows and no more ("from 7659 upward", "at or below 7645",
    "locked").
  - Mirror tests skip cleanly and **visibly** when the ledger or Python is unavailable.
  - **bwb evidence is bridged** (`services/bwbEvidenceBridge.ts`): `live-gap` (the live arm against
    paper's same arm, refusals named with their gate, e.g. `live.vix9d_vix_max`), `entry-context`,
    `regime-split` and `iv-premium` (the `vol` tab). Each verb opens the ledger read-only.
  - **Bridged, not mirrored:** calendars' exit-policy table and week anchors go through
    `services/calendarsBridge.ts` (memoised 5–10 min) — the policy replay is validated to the cent
    against the real books, and a TypeScript copy would be validating the wrong derivation; the
    anchors are NYSE holiday arithmetic whose structure tag keys every result.
  - **Flies evidence** (`services/fliesEvidenceBridge.ts`, memoised 2 min): `run.py live-gap`
    (live vs paper paired per session, split at the `completion_rule` break; each unmatched entry
    carries the other book's reason and category, each pair its largest gap, and a "why the books
    differ" card sums them per era -- `WhyTable`, `SessionWhy`), `gate-sessions`
    (each gate arm against control over its own window from the ledger's breaks, not `ERAS`) and
    `selector-sessions` (departures and the model's procedure-v2 intervals). The module's JSON is
    passed through in its own snake_case inside a `BridgedResult`; a failed read is an error, never
    an empty table. The verbs open ledgers read-only.
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

## Pages

**Shell.** A grid, not a stack (2026-10-12): the brand head and the top bar are row 1's two
halves (ticker, session chip, the simple/detail view switch, clock), the rail and the content pane are row
2's, the status bar (health chips, watchdog/session/morning phase, logs) is row 3. The brand
head's underline is the same grid track as the top bar's — one rule across the window in both
rail widths — and the rail's vertical is the brand head's right border continued by the nav's,
unbroken from the top of the window to the status bar. **The rail is the one nav** (mounted in
the shell since 2026-10-11, on every page; collapsed to a 64px icon strip by default, its own
toggle at its foot — the top bar holds no menu and never opens one). Below 1000px the rail's
column stays the icon strip and an expanded rail opens over the content. There is no command palette: the keyboard hints
(`g` + key for suite pages, a bare digit for modules, `j`/`k` for tabs) are in the rail's link
tooltips, built from `navLinks.ts` so a hint cannot outlive its shortcut.

**Views.** One console pref (`viewMode`; absent means `detail`) switches every module between
`simple` and `detail`. Each module lists its simple tabs in its own vocabulary (`ModuleNavDecl.simple`
in `navGroups.ts`); detail shows every tab; a module that declares no list shows everything in both.
Simple view also leaves Compare, Advisor and System off the rail (`SIMPLE_HIDDEN_PAGES`,
`navLinks.ts`). A bare module URL lands on the view's first tab, a deep link to a tab outside the view still renders
(and the rail keeps it listed while you are on it), and `j`/`k` step through the view's tabs.

**System page** (`/system`): the suite's own health, read-only, from the files the suite writes
(supervisor registry and heartbeat, watchdog and its renotify memory, holds, pid files, stamps).
A tile's level is the suite's verdict (a watchdog finding, or the supervisor's own heartbeat rule),
never a second opinion. Two reads go beyond a file, both cached and read-only: `git` (what the
checkout holds; which processes predate their package's newest commit) and one `python -c`
(interpreter and package versions). No control path: restarts and holds stay with `run.py`.
The data tab also asks `cherrypick.core` two things (`services/dataEvidenceBridge.ts`): quote-history
coverage (`python -m cherrypick.core.quotehistory coverage`, which reads only through
`core.quotehistory.session()`, so this package holds no SQL against `gex_quote_history`) and
`core.impliedvar check` for VIX9D and VIX, whose refusal (`not_bracketed`) is a reading, not a
failure (`spawnModuleCli`'s `okStatuses`).

**What is off is not shown.** Whether a module, GEX (the `gex-recorder` service), the advisor, the
technicals cards or a narrative is on comes **only** from the orchestrator's `configcli` op
`features` (`services/featuresBridge.ts`, memoised 15 s and dropped on every config save or halt
toggle; `GET /api/features`), never from reading the config here — `enabled` already folds in
capabilities like Dolt and Claude Code. Every web rule is in `lib/visibility.ts`; the desk
rows and the default log sources are filtered on the server with the same answer. **Fail open:**
loading, `{ok: false}` or an id the reply does not name all show the thing, with a status-bar chip on
failure. A direct URL to an off page renders `ModuleOffCard` (why, and a link to Config), never a 404
and never the module. Config always lists every toggle. Calendars, pmcc and curve carry an
`experimental` chip, listed once in `moduleOrder.ts`'s `EXPERIMENTAL_MODULES`.

**Module frame.** Every page renders a left rail and a content pane inside the shell. The rail
starts collapsed to its toggle; open or shut is the `navExpanded` console preference.
`registry.ts`'s `MODULE_FRAMES` and `navGroups.ts`'s `NAV_DECL` are full `Record`s over `ModuleId`, so
a page added to `moduleOrder.ts` without both does not compile. Advisor stays one page with its own
four tabs, because its write actions are wired through page-spanning state (`navGroups.ts` says why).

- **The rail is static data (`lightbox/navGroups.ts`), not the manifest.** Manifests are `lazy()`, and
  `renderToString` (every test here) emits the Suspense fallback, so the rail and breadcrumb live
  outside the boundary — that is what lets `routes.test.tsx` assert which tab a URL resolves to.
  `ModuleFrame` compares the two tab lists at runtime and says so **on the page** on a mismatch.
- **A renamed tab keeps its old id as an alias**, resolved before the first-tab fallback
  (`/flies/exits` → `divergence`); a fall-through looks like a working link showing the wrong page.
- **A tab change is a handoff, not a remount.** The frame body stays mounted, the slide subtree is
  keyed with a 120ms settle, scroll resets explicitly, and `Shell` keys its outlet on the module
  segment, not the whole pathname (or every tab change refetches the module).
- **Nothing opens an overlay; cards link to pages.** A card with a denser form sets `to`
  (`GridCard`/`StatTile`): title and ⤢ link there **carrying the current query string** (mode, date,
  arm, era). Dense tables are rail pages (flies' `tables` group), reloadable and shareable.
  `routes.test.tsx` requires every `to="/<page>/<tab>"` in the web source to resolve to a declared tab;
  `pnpm ui-check --route /flies/session --links` follows every card link in Chrome.
- **A card's tone comes only from the sign of a number or a flag a writer already set** — the
  no-verdicts rule applied to pixels. Since a chart draws absent/threw/empty alike as a flat zero,
  `StatTile` renders a null as an em dash with no tone and `Spark` refuses to draw under two points.
  (`thin` is used because the module stamps it; console-side thresholds like `dragPct > 30` are not.)
- **A chart in a grid cell measures itself** (`lib/useMeasure.ts`): hand-rolled SVGs are written at
  `width = 1150` and scaling is not reflowing. The fallback stays 1150 for SSR and first paint.
- **Animations are deferred, not removed**; frame components ship with none so that work starts
  neutral.
- **No ragged rows.** A tile row is 6 or 12 tiles through `components/performance/TileGrid.tsx`,
  whose columns follow the card's width and always divide the count; plain `.stats-grid` auto-fit
  leaves a half-empty last row. Cards of uneven height go in `.cards-pairs`, ordered so each pair
  matches, and a block that explains another card's number goes inside that card under a
  `.card-subhead`, not in a short card beside it.

**Reports** (`/reports`) holds the morning pack (`packages/overview`) and the EOD review
(`packages/review`) as tabs; each tab renders its own page component unchanged, so neither report gains
a second place its shape is decided. The tab is in the URL (`?tab=eod`) because reports get sent, and
`/morning` and `/review` redirect rather than 404.

- The Morning tab also shows `packages/technicals`' report (`data/technicals/report-<session>.json`,
  `readTechnicals` in `readers/overview.ts`, `pages/Morning/TechnicalsCards.tsx`). **It is the last
  report dated strictly before the pack's session** — a same-day report was written after that
  close and would show the morning what it could not have known. A test pins the `<` and was shown to
  fail at `<=`.
- Its leaders and scan matches link to the technicals chart, which is on the Charts page.

**Charts** (`/charts`, `lightbox/manifests/ChartsLightbox.tsx`) — two tabs, both chart only:

- `/charts/intraday?product=ES&period=5m` is a live futures chart (1m/5m/15m) over the console's own
  DXLink session, extended hours included (`market/candles.ts`, `pages/Intraday/IntradayPage.tsx`).
  The contract is the futures ticker's, from `state/futures_contracts.json`. What the feed does, as
  `scripts/probe_candles.py` measured it, and the rule each finding set (`server/test/candles.test.ts`,
  each case shown to fail):
  - `X{=1m}` comes back labelled `X{=m}`, so events are matched by the parsed period, never by the
    string subscribed.
  - History is one snapshot, newest first, between SNAPSHOT_BEGIN and SNAPSHOT_END. It is buffered
    and sent whole (`replace`), and so is a re-snapshot after the feed resubscribes.
  - The snapshot ends with a REMOVE sentinel that has no prices; no bar without a full positive OHLC
    is ever drawn.
  - The bar in progress is re-sent under its own time, so a live event replaces the bar at `t`.
  - Series are viewer-gated with a 30s linger, like quotes, and resubscribe from the newest bar held
    after a feed rebuild. The axis is ET (`etTickMark`); the library's day ticks fall at UTC midnight
    and are labelled with their ET time.
- `/charts/technicals?symbol=X` draws `data/technicals/charts/<X>.json` (`readers/technicals.ts`,
  `pages/Morning/ChartPage.tsx`); it was Reports' `chart` tab, and `/reports/chart` redirects here
  with its query. Shown only while the technicals feature is on. The symbol becomes a file name, so the
  reader accepts only ticker characters; a test sends `../` and was shown to fail with the pattern
  loosened.
  - Its arrows are ONE entry/exit setup family at a time (`?setup=`, `?side=long|short|both`,
    `packages/technicals/docs/setups.md`): entries, exits labelled with the reason, an open-position
    chip, and the lines the rule reads. Longs amber, shorts pale ("short" / "cover"), so a short's
    entry is never read as a long's exit.
    The rule text is the package's, never restated here. Where `volumeSource` is set (SPX: SPY) the
    page says so on every setup (`web/test/chartPage.test.tsx`, shown to fail when hidden).
    Levels are `?levels=ours|vendor|all|off`: our own swing levels (the default), only those the
    vendor's own chart draws (the file's `vendorView`, for comparison), everything, or none; never
    the setup lines. Vendor levels are drawn from their own date, as the vendor draws them, and kept out
    of the autoscale. Entry and exit arrows are both amber: an arrow marks an event, and green or
    red would read as a gain or a loss.
- `/screeners` (2026-10-16; `pages/Screeners/ScreenersPage.tsx`, `readers/screeners.ts`,
  `GET /api/screeners/:list[?day=]` and `/api/screeners/fidelity`) shows the income screener lists:
  short puts, covered calls, credit spreads, one row a name, and a fidelity tab (detail view only).
  The reader JOINS other packages' files and re-derives no rule: our strike is `score-screeners`'
  `picks`, each leg's verdict and the listed-or-cycle recommendation are `fetch_screener_greeks.py`'s
  (`rebar` re-applies the bars to saved readings), the tradable verdict is `technicals liquidity`'s
  (spelled `tradable`/`illiquid`). It works out only the user's calendar: the cycle monthly (45-60
  days, else 30-60) and whether an announcement falls before the TRADE's expiry -- the listed one when
  it is the trade, the cycle monthly when that is, the later of the two until a reading says which
  (a listed weekly spanning a report is never "clear" because the monthly ends first). A date that
  does not reach it is `unknown`, never clear. A reading is matched by the list DAY it read
  (`list_date`), not the date it was taken. Defaults: tradable names, no earnings in the trade, a
  liquid trade (the listed option or the cycle monthly passes every leg's bars; an unread row stays
  shown, marked), each with its escape. The source is never named, and which names make each list is
  not reproduced; the page says so.
- `/charts/setups` is the setups watchlist (`pages/Setups/SetupsPage.tsx`, `GET /api/technicals/setups`
  over `charts/setups-index.json`): recent entries and exits, or open positions, long and short,
  each symbol linking to its chart with the setup and side selected; nothing on it is vendor data. Gated with the technicals tab. The page filters and sorts
  and splits a position into entry and exit lines; every value is the package's. "Move" is close to
  close and never called P&L, and nothing ranks a signal's quality. "Tested edge" swaps in the
  study's confirmed rule's rows (`tested`), which overlap the setup rows and are never shown with
  them. The page is cut to `optionsTradable` names by default whenever the file carries a label
  (`names=all` is the way out). With no label it cuts nothing, because an unknown flag would empty
  it.

**Compare's risk tab** (`/compare/risk`, 2026-10-17, `pages/Compare/RiskTab.tsx` over
`GET /api/analysis` → `readers/analysis.ts::readArmsRisk`) — every arm of every module on one
sortable table, each over its own window (the analysis tab's rule), with peak risk, net over the
largest peak, max drawdown and drawdown over peak, the block-reordered 95th percentile, net without
the top 10%, the current session run and the slippage breakeven. Fields are PICKED from each module's
`core.analysis` reading (`riskRow`), never computed; each module read is the one its analysis tab
memoises. A module that fails is named, never an empty section; off modules are hidden with their
failures. The analysis reads are ASYNC (`services/moduleCli.ts::spawnModuleCliAsync`, the same parse
as the sync spawner) and the risk tab runs all eight at once: a cold read costs the slowest module
(~3 s, MEIC), not their sum, and never holds the event loop (a health request answered in 0.08 s
during one). A second request for a ledger being read waits on the same subprocess. Every other
bridge still spawns synchronously, holding the whole server for its subprocess; a slow one should
move to the async spawner too. This is the plan's "arms overview": it lives here, beside the paired comparison, rather
than as a second page that would repeat Compare's arm list.

**Compare's portfolio tab** (`/compare/portfolio`, 2026-10-17, `pages/Compare/PortfolioTab.tsx` over
`GET /api/portfolio` → `readers/portfolio.ts` → `python -m cherrypick.core.portfolio read`) — one
chosen arm per module held together, a bridge.
- **Resolving arms:** each arm resolves to its module's ledger, schema and era as the calendar's
  do, so its nets are the calendar's cells. An arm the console cannot resolve is named beside
  core's own failures.
- **The picker:** its default (`control`, else a module's first non-advised arm) is a starting point,
  not a judgement. pmcc's `control` is retired, for one.
- **Labelling:** every figure shows its periods. Thin rows are dimmed and carry no tone, and
  equal-risk scale factors are shown per arm.

**Compare's calendar tab** (`/compare/calendar`, 2026-10-17, `pages/Compare/CalendarTab.tsx` over
`GET /api/calendar` → `readers/pnlCalendar.ts`) — every arm's settled net per session as a Mon–Fri
month grid with a weekly total, filterable by module and arm, a day drilling to the arms that
traded it. The nets are `core.metrics`' own `session_nets_dated` (the performance tab's series, read
async through `readModuleMetricsAsync`, every module at once), over the SUITE window: a calendar says
what happened each day, not what an arm's evidence says, so it is not era-scoped and says so. The
component only adds days and weeks up. A week that straddles two months draws the neighbouring
month's days dimmed, so every row adds up to its total; a month's own total counts only its days.
An arm in a day's detail opens to its trades (`GET /api/calendar/trades` → `readPnlTrades` →
`python -m cherrypick.core.metrics trades`), fetched on click. That command shares `read`'s one
record read in core, so the rows sum to the cell. The table says "does not reconcile" if they ever
do not, and that has never been seen (every arm-session of the last five sessions, paper and live,
checked on 2026-10-17). The calendar and risk tabs follow the page's paper/live toggle; the arms
tab is paper only and shows none. A trade with a position id opens to its excursion
(`GET /api/calendar/excursion` → `services/excursionBridge.ts` → `python -m cherrypick.core.excursions
read`), a bridge: best and worst with their times, the exit, the basis named (a gross path and a net
one are not the same line), and a "does not reconcile" chip if the leg-built endpoint ever misses
the recorded gross. The older Performance MAE/MFE card still reads each module's own `excursions`
verb (`services/excursionsBridge.ts`); the two agree wherever both price a tick. Beneath the
excursion sits the position's trace (`pages/Compare/TradeTrace.tsx`, `GET /api/calendar/trace` →
`services/traceBridge.ts` → `python -m cherrypick.core.trace read`), also a bridge:
- **Entry attempts,** with how the fill was found (`linked` / `matched` / `review`).
- **Flies live:** its order trail.
- **Management runs,** each marked as done or considered and naming the gate that held it.

A part a module does not record says why. It is never an empty table.

**Compare** (`/compare`, `lightbox/manifests/CompareLightbox.tsx`, `pages/Compare/`) — every arm of
every module beside its control, over `GET /api/compare?scope=era|all` (`routes/compare.ts` →
`services/compareBridge.ts` → `python -m cherrypick.core.compare --json`). A bridge, not a mirror:
the intervals, paired differences, t bounds, sign tests and projections are `core.stats`'
(`server/test/compare-bridge.test.ts`, `web/test/comparePage.test.tsx`).

- Field names stay the bridge's snake_case, verbatim (`shared/types/compare.ts`). A schema other than
  1 is refused, never half-rendered; every failure is `{ok: false, error}`, never an empty table.
- Memoised on every module's paper ledger, the advisor's store and the New York session (a break
  dated ahead binds from its own date). A bad `scope` is a 400 and never runs the bridge.
- **No verdicts.** Tone comes only from a number's sign; interval ends, the t bound, the sign-test p
  and the projections are never toned, and a test fails on any verdict-like class. A null is an em
  dash, never `$0.00`.
- **Size-normalised beside the dollars.** In dollars a session, a capped arm beside a large control
  reads as size: MEIC's capped books sit $22k–$31k behind a control taking ~270 entries a day against
  their ~6. So each arm also shows `net / entry` (entries per session beneath) and `net / $100 risk`
  (on the capital `core.ledgers` records per trade: max loss or buying power, as the module's ledger
  states it), and each pair shows `diff / entry` and `diff / $100 risk`. These are the bridge's
  `per_entry` / `per_100_risk` paired blocks, keyed and windowed as the dollar pair. A ledger with no
  recorded capital (flies) shows a dash there, never zero, and a partial record says how many trades
  carry one.

**The arm picker** (flies, MEIC; the header's `ArmSelect`) narrows what a tab already read; it never
changes a read. A tab whose content is one block per arm follows it: session, forest, timeline,
attempts, decisions, positions, history, and (2026-10-17) performance, analysis, costs and regime
cuts. Advised books stay with their arm (`lib/armTag.ts`: `<arm>@<experiment>` is that arm). On
performance the cumulative chart keeps `control` beside the picked arm as its reference; on regime
cuts the document-level chance baseline still counts the whole cut, and says so. A tab whose point
is a comparison across arms (gates vs control, live vs paper, selector) or a module-wide view
(summary, advisor, agent) deliberately ignores it. The picked arm is page state, kept across tabs
because the frame stays mounted; it is not in the URL.

**A summary slide is a module's own.** MEIC's summary slide is the pattern — one-read payload
computed server-side to the cent, presentation arithmetic only in the component, money in the
suite's one layout, tone from a number's sign — and each module's summary carries its own KPIs in
its own vocabulary: entry-time and weekday bands are MEIC's, capture and capital-at-risk mean
nothing to a legged flies book, cash deployed is contango's alone. There is deliberately no
central summary page: one was built under Reports and withdrawn the same day (2026-10-11,
`docs/history.md`) — the modules' KPIs are not the same book, and a shared surface kept flattening
them to their common denominator or growing a registry of per-module exceptions.
What is module-local is the content, not the mechanism: the simple/detail view is shared, and
each module decides what its simple view lists.

**Module advisor slides.** `readers/advisor.ts`'s `readAdvisorModule` serves
`/api/advisor/module/:module`: active experiments with progress against length and stall budget, the
session strip from the advisor's `enactment` table (read, never re-derived), the paired comparison as
of the last evening pass, tomorrow's artifact, the queue and the last concluded.
`components/advisor/AdvisorSlide.tsx` renders it in all seven module manifests; the Advisor page's
experiments tab opens with the cross-module roll-up. Module slides are read-only; kill/dismiss stay on
the Advisor page only.

**Live** (`/live`, `lightbox/manifests/LiveLightbox.tsx`, `pages/Live/`) — the flies live pilot's day
over `GET /api/live/flies` (`routes/live.ts` → `readers/fliesLive.ts`), composing existing readers
(`services/liveLock`, `readFliesLoopStatus`, `readFliesJournal`, `readFliesAnalytics`) plus settled
net per period, the intraday series and the broker account. Rules, stated on the page:

- **Period tiles are settled net only** — the core.ledgers flies rule (`gross_pnl - fees` over
  `status = 'settled'`, by `trade_date`), mirrored and pinned by
  `server/test/flies-live-reader.test.ts`; today reads zero until the bell, with the paper control's
  figure beside each tile.
- **A mark is a mid, not a fill.** The intraday curve and "now" tile are the loop's own
  `fly_live_marks`, summed per tick with peak-to-trough drawdown; an unpriced tick is a named gap,
  never interpolated.
- Market series are read-only from other stores: SPX from `gex_spot_history` (baselined on
  `prev_day_close`, else the first tick, and the payload says which), VIX/VIX3M from
  `market_regime_history` (`usable` rows only), clipped to regular hours. Buying power is the loop's
  own gate figure against `live.max_open_margin_dollars`, so page and gate cannot disagree.
- The broker account rides `services/brokerBridge`, a memoised subprocess over the orchestrator's
  `positions` verb (masking and mid-is-not-a-fill flags stay in Python), and is **non-blocking**:
  return what the memo holds and start a refresh; a synchronous spawn skeletons the page for 10–20s.
- `readOnlyDb`, not `withReadOnlyDb`, for the live ledger: "no live ledger here" and "the read threw"
  are different facts and the page shows which. No button here touches an order.
- **Before today's session opens, the page is the last session the pilot settled** (`sessionBasis`,
  `resolveLiveSession`), and says so. "Opened" is evidence, not a calendar — the rule
  `sessionPeakWorst` holds by: a `fly_snapshots` row at or past 09:30 ET in either ledger, or a live
  position dated today.
- **The flies order-alert daemon's health** is a chip in the Live page's header, on every tab
  (`components/AlertDaemonChip.tsx`, `services/alertDaemon.ts`, on `/api/system`), read off the daemon's own pid and status
  files: ok, stale (alive but no heartbeat for three 30 s slices — the silent-websocket case), down
  (dead on an armed day) or off. Amber at worst, never red: a dead daemon costs fill latency only.
- **Return on session peak risk** is settled net over the session's peak open worst case — the
  gate's own figure, so a day that recycles its budget can exceed 100%. Never call it "max risk":
  that reads as the buying-power cap, which is a limit, not a use. One implementation,
  `readers/fliesPeakRisk.ts`, serves the Live page, the flies session tile ("daily peak risk") and
  the flies performance slide (in place of return on capital, which flies cannot carry). The peak,
  in order (`peakSource`): `booked`, flies' own figure on the settled book (`fly_books.peak_risk`,
  actual fills, the one `core.analysis` reads, so this and the analysis slide agree); `recorded`,
  the live loop's `fly_live_marks.open_margin`, for a session not yet settled; `replayed` from the
  positions (`analytics/fliesPeakRisk.ts`) where neither exists. The tick record is not the settled
  figure: it can hold an entry's limit price before a better fill is reconciled, and an order
  working at that tick that never filled, so it sat above the booked peak on 7 of 21 live sessions
  (flies' `run.py backfill-peak-risk` names each). A replay prices a position in its final state
  at its recorded `floor_dollars`, never off its `fees`, which by then include settlement. Only
  finished sessions that carried risk count, with the numerator matched to them.
- `hasColumn` memoises by file name, so a test ledger is a temp file, never `:memory:` (every
  in-memory database shares one name, and a second would be answered with the first's schema).
- The performance block carries both flies study tabs for the configured `live.arm`, never
  recomputed: the **completion** slide (`readFliesPerformance` in live mode, rendered through
  `PerformanceTab.tsx`'s own exported cards) and the **performance** slide's calibration reading
  (`core.metrics` over `live_trades.db`, rendered by `MetricTiles`). Their two max drawdowns differ on
  purpose — daily against per trade — and the page labels which is which.

**The Live page's `meic` tab** (`pages/Live/MeicLivePage.tsx` over `GET /api/live/meic` →
`readers/meicLive.ts`, 2026-10-15) is MEIC's live loop from files alone: the arm record (armed =
dated today; past `live.disarm_time` is said), the halt flag, the orphan / hold / broker-health files
beside `meic_trades.db` (read as `core.execution` reads them: an unreadable hold file is held), open
ICs (`live_loop.overdue_settlement`'s status list; `pending` is a working order with no entry cash; a
side with a recorded close cost is closed) and resolved history (`stopped`/`expired`/`force_closed`)
through `meicTradeCash` and the MEIC page's own history columns, less slippage (a live fill is the
broker's). Cancelled rows are in neither table. No curve or feed: the live loop writes no marks and
no `loop_log`, so there is no loop pill either. Pinned by `server/test/meic-live-reader.test.ts`.

**The Live page's `today` tab** (`pages/Live/LiveTodayPage.tsx` over `GET /api/live/today` →
`readers/liveToday.ts`, 2026-10-15) opens `/live`: one row per live loop (flies, meic, bwb), the
attention list above it, the suite line and a header badge (`2 armed · HALTED`). It **composes** the
per-loop readers (`fliesLive`/`flies`, `readMeicLive`, `readBwb`/`readBwbHistory` in live mode,
`liveBooks`' bwb read for absent-vs-failed) and adds only each loop's own `daily_loss_tripped` and
flies' `overdue_settlement` queries, mirrored and named. Attention: orphaned orders, held
submissions, a tripped breaker, overdue settlement, failing broker contact, an unreadable ledger;
"no limit" is never shown as a clear breaker. The suite total sums **only today's realised net**
(resolved today, gross − fees − settlement, broker fills): any loop's failed read makes it n/r, and
the worst case and the mark are each loop's own measure, shown per loop, never added. `/live/today`
is this tab now, not an alias for flies. Pinned by `server/test/live-today.test.ts`.

**There is no Overview page** (removed 2026-10-16): `/` redirects to `/live`, whose `today` tab is
the every-loop summary. The morning phase chip moved to the status bar and the alert-daemon chip to
the Live header. Its two endpoints went with it; `readers/liveBooks.ts` stays, because `liveToday`
reads bwb's ledger status through its `LIVE_BOOKS` specs and `live-books.test.ts` covers them.

**Flies intraday agent tab** (`/flies/agent`, `pages/Flies/AgentSlide.tsx` over
`GET /api/flies/agent[?session=]` → `readers/fliesAgent.ts`). It has no live view: a live read is
refused on the page (`web/test/fliesAgentSlide.test.tsx`), and the live shadow, scored by the
module, is the "live shadow" table in the Paper view, read off the same file.

- **Every verdict and every settled figure is the module's qualification file**
  (`data/flies/intraday_agent_qualification.json`, `cherrypick.flies.intraday_eval`, rewritten at
  each settlement and by `run.py agent-eval --write`): criteria, the paired bound, offered modes,
  each session's three arms, the settled tagged closes and the spend. A `pass: null` (the suite
  cannot score it yet) is shown as "not scored" and never unlocks.
  `server/test/flies-agent-reader.test.ts` runs the real writer and checks the reader carries its
  numbers unchanged; a fixture case is deliberately inconsistent so a re-derived list would fail.
- What the file cannot hold yet is read directly: the session's checks (the agent's jsonl, shaped,
  never summed except the session spend tile, on the server), the decision file under
  `read_decision`'s rule (session, expiry, admissible), and from the paper ledger the closes tagged
  on still-open positions and the trend gate's refusals.
- Check times are written as New York wall clock (`etWallClock`): the chart family's `minuteOf`
  reads characters 11-16, and a UTC ISO string there reads four or five hours out.
- A pack is served as the model saw it, `_` keys removed as `intraday_pack.for_model` removes them.
  A close is a tag priced at natural, never a fill, and the page says so.

**Regime-cuts slide** (flies and MEIC) — `components/RegimeCutsTab.tsx` over
`GET /api/<module>/regime-cuts[?session=YYYY-MM-DD]` (`routes/modules.ts` → `readers/regimeCuts.ts`).
The module writes the artifact nightly (contract: `cherrypick.core.regimecuts`); the reader derives
only the union of dimension keys for layout. Every number, the era and `thin` come off the file —
`server/test/regime-cuts.test.ts` uses a cell whose `sessions` and `thin` disagree on purpose.

- Absent (slide names the command), failed (malformed, or an unread `cut_version` — shown as a
  failure, not an empty day) and stale (ledger `MAX(trade_date)` newer than the artifact, via one
  `readOnlyDb`) are three payloads. Dated artifacts feed a session picker.
- Robustness stamps (`fragile`/`robustness`/`history` per cell, `paired` per dimension,
  `multiplicity` per document) are the writer's. **`fragile` is never defaulted to false** (missing =
  "not stamped"); paired rows dim at the writer's `paired_alpha`, never a console constant or the
  interval `alpha`. The fixture sets the two alphas apart so borrowing one for the other fails.
- Verify: after `pnpm --filter @console/shared build && pnpm build` and a restart,
  `MSYS_NO_PATHCONV=1 pnpm ui-check --route /flies/regime --expect "era since"`, and `/meic/regime`.

**Analysis slide** (every trading module, under study after performance; docs/strategy-analysis-plan.md) — `components/analysis/AnalysisSlide.tsx`
over `GET /api/analysis/:module` (`routes/performance.ts` → `readers/analysis.ts` →
`services/analysisBridge.ts` → `python -m cherrypick.core.analysis read`). Per arm: session and trade
runs, drawdown episodes (high, low, new high; falling and recovering apart), the net without the top
1/5/10% of winners, the reshuffle stress test, and capital: observed peak risk with the net over it
("net / largest peak") and max drawdown over it, and the declared allocation (`<home>/config/arm_capital.json`, read by core:
the bridge passes `--module`, never the file). Bridged, never mirrored.

- "net / largest peak" is the whole net over the single largest peak. It is never labelled "return on
  peak risk": that is the performance slide's Σ net / Σ session peaks, a per-day figure (live flies
  control, 2026-10-16: -6.2% there, -96% here). A test keeps the two names apart.
- Flies records each book's session peak at settlement (`fly_books.peak_risk`), which core reads.
- "Breakdowns" are core's bucket tables (`core.analysis.buckets`): P&L by weekday, entry hour,
  release day and month, one column set. A row under core's `min_sessions` is `thin`: dimmed, chip,
  and no tone, since a colour would read as a finding it cannot carry. A release day the calendar
  cannot vouch for is "unknown", never "none".
- "Regime" (flies and MEIC only) is the module's NIGHTLY regime cut for that arm
  (`components/analysis/ArmRegime.tsx`, the regime tab's own payload via `useRegimeCuts`, one fetch
  shared by every card). It is a different window from the rest of the card (the arm's own era, as
  of the cut's session) and says so every time it draws; it shows single buckets only, passes the
  writer's thin/fragile stamps through without tone, and links to the full regime tab. It is passed
  into `ArmCard` as a prop, so the card holds no data hook. Every other module gets two regime
  breakdowns instead (`vix`, `term`: VIX and VIX/VIX3M at entry, joined by core). They are optional
  on `Breakdowns` and absent for flies and MEIC, so one table list serves both. An entry the
  recorder did not cover reads "unmeasured".
- "Worse fills" is core's slippage ladder: the net at 1/1.5/2/3× the slippage the module modelled,
  over the trades that record slippage. Where that is not all of them (flies from 2026-09-24) it
  says so and starts from those trades' own net, never the arm's. An undeclared allocation reads "none declared" with dashes, never zero;
  a refused allocation file is a page-level chip with core's reason, and the rest still renders.

- It reads the performance slide's ledger per mode (`performance.ts::moduleLedgerPath`), suite epoch
  and stamped module era, and the bridge groups arms as `core.metrics`' does. A reply whose `version`
  is not `ANALYSIS_VERSION` is refused, not drawn.
- **Each arm is read over its own era** (version 2, 2026-10-17; `core.analysis.windows`): where the
  module's `measurement_breaks` name arms (flies, MEIC), its latest break, else the module's latest
  book-wide one, by `core.regimecuts`' own rule, so this and the regime cuts agree. Modules whose
  breaks name no arm read the suite window, and say why. The suite epoch is always the floor. Every
  arm card states its window (`WindowLine`). The performance slide still reads the suite window, so
  the two can differ, and the page says so: flies control read +$1,546 over the suite window and
  -$2,891 over its era since the 10-05 completion-rule break.
- A withheld stress test (under 20 sessions) says why and shows dashes. A drawdown is shown as the
  loss it is; its tone comes from that sign. The reordered percentile can sit BELOW the recorded
  drawdown: that is a reading (the real order was among the worst), not a defect.
- Verify: `MSYS_NO_PATHCONV=1 pnpm ui-check --route /flies/analysis --expect "deepest drawdowns"`.

## Advised books are per experiment

Each experiment writes its own book, `advised:<experiment name>` (earnings
`advised:<name>:<strategy>`; name slugged to `[a-z0-9-]`), and a module can run several. The tag no
longer names the base: that is the advisor `experiments` row's `base_profile`, beside its `tag` column.
Older rows carry the legacy `advised:<base>`, told apart only by the stamped `experiment_id`.

**Every surface resolves an advised tag through ONE place, `readers/experimentIndex.ts`**, in order:
the stamped experiment id (`core.metrics` groups stamped rows as `<tag>@<experiment id>`); then the tag
against the experiment's `tag` (or `advised:` + slug(name) on an older store — `slugExperimentName`
mirrors `cherrypick.core.advice.slug` exactly, pinned by the advice-decl test); then the legacy
`advised:<base>` reading for rows no experiment claims. Prefix stripping in several files would be
several chances to disagree about which base a book shadows.

- **Paired card** (`readers/pairs.ts`, `PairedABCard`): one pair per experiment. A stamped legacy tag
  pairs to that experiment's base; an unmatched legacy tag pairs against the base its tag names (or the
  declared base if that book has no rows in the window), flagged `unstamped`. **No date inference** —
  a second attribution rule free to drift from the advisor's.
- **Active / retired** (`adviceDecl.ts::advisedTagStatus`): a tag is active while its experiment is
  `active` in advisor.db and the module's advice layer is on. A legacy tag no experiment claims is
  history once a store exists; with no store, the old rule (advice on, declared base) applies. The
  experiment guide attributes a legacy tag to an experiment only when EVERY row carries the same stamp.
- **Advisor reader**: `readAdvisorModule.active` is a list (each with `calendarSessions`,
  `stallBudget`); the strip returns every enactment row over the last 15 scored sessions (`enactment`
  keys on `(session, module, experiment_id)`), one strip per experiment; the "not applied" count is
  per experiment. Artifacts and `advice_active.json` carry an `experiments` list whose first entry the
  legacy top-level fields mirror — read the list (`artifactExperiments`, `decisionExperiments`),
  synthesising one entry only when it is absent; reading both counts the first experiment twice.

## The trade table standard

The root money layout applied here. Flies is the reference: `readers/flies.ts` `tradeCash`/`bookCash`
derive the columns, `lib/format.ts` `fmtCash` (`+$178.75`) and `fmtPrice` (`1.25 cr`) render them.
Header order: identity and structure, qty, price, entry, exit, how it ended, gross, fees, settle, slip,
net — each money header's `title` stating its definition.

- Money is derived on the server, never in a component, so every surface of a module agrees.
- A recently added cost column is read as optional (`NULL` on an older ledger), so a missing column
  reads "not recorded" rather than `withReadOnlyDb` turning it into an empty table.
- A totals chip carries gross, fees, settlement and net over every matching row, and says how many
  rows a partially recorded measure (slippage) covers. A settlement total over any row that never
  recorded it is n/r, never a partial sum.
- Fee drag is one helper, `@console/shared`'s `feeDragPct` (trading fees, settlement out, ÷ premium
  collected; null when nothing was collected), on every surface that shows it.
- Each module on the standard has its own trade-standard test against its own fixture, shown to fail
  (the flies one by removing the subtraction and the filter):

| Module | Derivation | Test | Notes |
|---|---|---|---|
| flies | `readers/flies.ts` `tradeCash`/`bookCash` | `flies-trade-standard` | slippage inside gross |
| meic | `readers/meic.ts` `meicTradeCash`, `pages/Meic/MeicTables.tsx` | `meic-trade-standard` | entry summed from the two side credits, rounded per side as the write path does |
| bwb | `readers/bwb.ts` `bwbTradeCash` | `bwb-trade-standard` | slippage charged inside `fees`; each part subtracted once; a broker-reconciled row takes none out |
| earnings | `readers/earnings.ts` `earningsTradeCash` | `earnings-trade-standard` | `entry_cost`/`exit_cost` carry slippage and (exit) settlement fee; history is closed trades across both books |
| calendars, pmcc, curve | `readers/positionCash.ts`, `components/TradeMoney.tsx` | `position-trade-standard` (via curve), `pmcc-`/`calendars-trade-standard` on fixtures from each module's `db.connect` | exit kinds add `assigned`; calendars' history is per week and arm, net null until every position (shares included) has closed, totals over finished weeks only |
| contango | `readers/contango.ts` `contangoTradeCash` | `contango-trade-standard`, on a fixture from its `db.connect` | a stint is a fund bought and sold: entry (the purchase) + exit (the sale) + distributions = gross; slippage charged as a cost; settlement never recorded, so `n/r` |

## History-table controls

A history table declares its columns once (`components/table/columns.ts`, `ColumnDef`: header,
definition tooltip, cell, kind); the controls come from that list.

- **Columns menu** (`ColumnsMenu`): a dropdown panel, not a dialog. `describe` columns can be hidden
  and reordered; `money` columns can be hidden but stay one block in the standard's order at the end,
  and `net` (`pinned`) is always shown and always last. `resolveColumns` enforces that against any
  stored layout, drops unknown ids and shows newly declared columns (`web/test/tableColumns.test.ts`).
- **Layout storage:** the prefs store under `columns:<table>` — per viewer, follows to the desktop
  shell, never in the URL.
- **Date range** (`DateRangeBar`, `useUrlDateRange`, `DatePicker.tsx`): presets plus a two-click
  picker (Monday-first, nothing after today); `?from=&to=` written with `replace`; presets end today in
  New York time. **The server applies it** (`readers/dateRange.ts`: one parser, one clause builder), so
  counts and totals describe the range. Each module bounds the date its history is ABOUT, and the bar
  names it: trade date for flies and meic, close date for bwb/pmcc/curve/earnings/contango (an open position is
  outside any range), week for calendars.
- **`HistoryTable`** (`components/table/HistoryTable.tsx`) is the card for every module but flies.
  Money and `numeric` columns right-align per column (`td.num`), since a reorderable table cannot align
  by position. `tradeMoneyColumns` holds calendars/pmcc/curve's shared columns.
- **Deliberate differences:** flies' range sits at the top of its History tab and bounds the summaries
  too, so a narrowed tab never sets one table beside cards answering for the era. MEIC's history is one
  session by default; a range replaces the header's day, and the no-range button reads `session`.
  Earnings' range bounds its history and totals only.

## Sorting a table

Sort where ranking is a useful read: money, counts, rates, dates, symbol, arm, prices, sizes. A click
cycles largest first, smallest first, the table's own order; nulls sink either way; the header is a
button (`.th-sort`). **Where the rows live decides how** (2026-10-16):

- **A server-paged table sorts on the server, never in the browser.** Sorting one fetched page would
  rank 100 rows and call it the history. `usePage().toggleSort` sends `sort`/`dir` with the page;
  the reader passes its `SortAccessors` to `pagedQuery`/`pageArray`, which order the WHOLE result
  before slicing. Each accessor reads the MAPPED row, the figure the cell shows, so a net derived in
  the reader sorts as that net; an id the reader does not name keeps the default order. The web
  table's `serverSorts` list is held to the reader's by a test (`history-sort*.test.ts`).
- **A `HistoryTable` holding every row** sorts in the browser from each `ColumnDef`'s `sortValue`,
  and only when it passes `clientSort`. With neither mode a `HistoryTable` sorts nothing, so shared
  defs (MEIC's, paged and live) cannot switch on a client sort of a server page by accident.
- **Hand-built and `DataCard` tables** use `useSort` / `sortRows` / `SortTh` (`DataCard` takes
  `{label, k}` headers and `sort`). A client-sliced table ("show more") sorts before the slice.

Leave the order alone where it is the meaning: time series and journals, pivots and heatmaps,
key/value lists, fixed category orders, legs of one position, grouped rows, a `#` that is the row's
position, top-N previews of a server ranking (Flow's today cards), and capture layouts.

## Package guardrails

Suite guardrails apply (root CLAUDE.md). Package-specific: **loopback-only serving**; the console
**computes no verdicts** — it renders what the modules decided, and where it mirrors a module's queries
it says so and is checked against them.
