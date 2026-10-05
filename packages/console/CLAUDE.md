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
  - **Bridged, not mirrored:** calendars' exit-policy table and week anchors go through
    `services/calendarsBridge.ts` (memoised 5–10 min) — the policy replay is validated to the cent
    against the real books, and a TypeScript copy would be validating the wrong derivation; the
    anchors are NYSE holiday arithmetic whose structure tag keys every result.
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

**Shell.** A top bar (menu, the futures ticker, the clock) and a bottom status bar (health chips,
watchdog/session/morning phase, logs) are on every page; only the content between them scrolls.

**System page** (`/system`): the suite's own health, read-only, from the files the suite writes
(supervisor registry and heartbeat, watchdog and its renotify memory, holds, pid files, stamps).
A tile's level is the suite's verdict (a watchdog finding, or the supervisor's own heartbeat rule),
never a second opinion. Two reads go beyond a file, both cached and read-only: `git` (what the
checkout holds; which processes predate their package's newest commit) and one `python -c`
(interpreter and package versions). No control path: restarts and holds stay with `run.py`.

**What is off is not shown.** Whether a module, GEX (the `gex-recorder` service), the advisor, the
technicals cards or a narrative is on comes **only** from the orchestrator's `configcli` op
`features` (`services/featuresBridge.ts`, memoised 15 s and dropped on every config save or halt
toggle; `GET /api/features`), never from reading the config here — `enabled` already folds in
capabilities like Dolt and Claude Code. Every web rule is in `lib/visibility.ts`; the Overview's desk
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
- `/charts/setups` is the setups watchlist (`pages/Setups/SetupsPage.tsx`, `GET /api/technicals/setups`
  over `charts/setups-index.json`): recent entries and exits, or open positions, long and short,
  each symbol linking to its chart with the setup and side selected; nothing on it is vendor data. Gated with the technicals tab. The page filters and sorts
  and splits a position into entry and exit lines; every value is the package's. "Move" is close to
  close and never called P&L, and nothing ranks a signal's quality. "Tested edge" swaps in the
  study's confirmed rule's rows (`tested`), which overlap the setup rows and are never shown with
  them. The page is cut to `optionsTradable` names by default whenever the file carries a label
  (`names=all` is the way out). With no label it cuts nothing, because an unknown flag would empty
  it.

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
- **The flies order-alert daemon's health** is a chip on the Overview title row beside the live
  chip (`services/alertDaemon.ts`, on `/api/system`), read off the daemon's own pid and status
  files: ok, stale (alive but no heartbeat for three 30 s slices — the silent-websocket case), down
  (dead on an armed day) or off. Amber at worst, never red: a dead daemon costs fill latency only.
- **Return on session peak risk** is settled net over the session's peak open worst case — the
  gate's own figure, so a day that recycles its budget can exceed 100%. Never call it "max risk":
  that reads as the buying-power cap, which is a limit, not a use. One implementation,
  `readers/fliesPeakRisk.ts`, serves the Live page, the flies session tile ("daily peak risk") and
  the flies performance slide (in place of return on capital, which flies cannot carry). The peak
  is the live loop's recorded `fly_live_marks.open_margin` where one exists, else replayed from the
  positions (`analytics/fliesPeakRisk.ts`) — equal to the recorded peak to the cent on all eight
  live sessions that have both. A replay prices a position in its final state at its recorded
  `floor_dollars`, never off its `fees`, which by then include settlement. Only finished sessions
  that carried risk count, with the numerator matched to them.
- The performance block carries both flies study tabs for the configured `live.arm`, never
  recomputed: the **completion** slide (`readFliesPerformance` in live mode, rendered through
  `PerformanceTab.tsx`'s own exported cards) and the **performance** slide's calibration reading
  (`core.metrics` over `live_trades.db`, rendered by `MetricTiles`). Their two max drawdowns differ on
  purpose — daily against per trade — and the page labels which is which.

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
  rows a partially recorded measure (slippage) covers.
- Each module on the standard has its own trade-standard test against its own fixture, shown to fail
  (the flies one by removing the subtraction and the filter):

| Module | Derivation | Test | Notes |
|---|---|---|---|
| flies | `readers/flies.ts` `tradeCash`/`bookCash` | `flies-trade-standard` | slippage inside gross |
| meic | `readers/meic.ts` `meicTradeCash`, `pages/Meic/MeicTables.tsx` | `meic-trade-standard` | entry summed from the two side credits, rounded per side as the write path does |
| bwb | `readers/bwb.ts` `bwbTradeCash` | `bwb-trade-standard` | slippage charged inside `fees`; each part subtracted once; a broker-reconciled row takes none out |
| earnings | `readers/earnings.ts` `earningsTradeCash` | `earnings-trade-standard` | `entry_cost`/`exit_cost` carry slippage and (exit) settlement fee; history is closed trades across both books |
| calendars, pmcc, curve | `readers/positionCash.ts`, `components/TradeMoney.tsx` | `position-trade-standard` (via curve), `pmcc-`/`calendars-trade-standard` on fixtures from each module's `db.connect` | exit kinds add `assigned`; calendars' history is per week and arm, net null until every position (shares included) has closed, totals over finished weeks only |

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
  names it: trade date for flies and meic, close date for bwb/pmcc/curve/earnings (an open position is
  outside any range), week for calendars.
- **`HistoryTable`** (`components/table/HistoryTable.tsx`) is the card for every module but flies.
  Money and `numeric` columns right-align per column (`td.num`), since a reorderable table cannot align
  by position. `tradeMoneyColumns` holds calendars/pmcc/curve's shared columns.
- **Deliberate differences:** flies' range sits at the top of its History tab and bounds the summaries
  too, so a narrowed tab never sets one table beside cards answering for the era. MEIC's history is one
  session by default; a range replaces the header's day, and the no-range button reads `session`.
  Earnings' range bounds its history and totals only.

## Package guardrails

Suite guardrails apply (root CLAUDE.md). Package-specific: **loopback-only serving**; the console
**computes no verdicts** — it renders what the modules decided, and where it mirrors a module's queries
it says so and is checked against them.
