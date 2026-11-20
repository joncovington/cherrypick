# console (unified web UI)

The suite's only read surface: one app over every module's read models — the Live page, the eleven
trading modules (MEIC, earnings, calendars, contango, weeklyput, ladder, automations, and the retired flies, bwb, PMCC and curve, which ship off by default), GEX,
Charts, Screeners, Compare, Backtest and System — plus the advisor and the reports. Every module
remains a producer this package reads; it touches none of their code. The dashboards it replaced
survive only at the `pre-console-only` tag, so **no path here touches an order** —
`dry-run-only.test.ts` pins `postOrderDryRun` to the scope probe alone and scans every reader.
[docs/not-covered.md](docs/not-covered.md) lists what the console does not cover.

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

Commands to run the package, its tests and its lint are in [README.md](README.md#commands).

**Confirming a change reached the page** (a front-end change is confirmed in a browser, not by tests
alone):

- **The running server is `server/dist/index.js`, not your source.** A source edit changes nothing
  until `pnpm build` AND a restart; otherwise you are watching the old build behave perfectly. A
  shared-type change also needs `pnpm --filter @console/shared build` first.
- **`pnpm ui-check` drives real Chrome** — clicks, expectations, screenshots, console errors;
  `--dump <file>` writes the rendered DOM. `--card <title text>` crops `--shot` to one grid card
  that has drawn its chart, and fails rather than falling back to the page.
  `--selector <css>` crops to any element instead (`.mf-slide` is one tab's content).
  `scripts/flies_payoff_post.py` captures the flies payoff card this way, and a person's own
  poster can capture the cards of the External reports page the same way (each section of a report is
  a card titled by its heading, at `/reports/external/<id>`), so changing a card's title text or
  dropping the glyph `--card` waits for breaks those posts. Prefer `--route` over `--click`: every slide is
  addressable, and on the frame `--click` cannot reach a tab (it skips anything inside a `<nav>`, and
  the rail is one).
- **Under Git Bash, prefix it with `MSYS_NO_PATHCONV=1`**, or `--route /flies` arrives as
  `C:/Program Files/Git/flies` and silently checks nothing.
- **For a reader or endpoint change:** capture the affected endpoints from the RUNNING build, then
  build, restart and re-capture. Every data field should be identical and only clock-derived ones
  (`now`, `ageSeconds`) should move. This is the only check that sees past the `withReadOnlyDb`
  fallback below.

## Data rules

- **One request to the internet: the release check** (`readers/version.ts`). GitHub's
  `releases/latest` for the suite's repo, at most every six hours, in the background (a page never
  waits on it), with a generic User-Agent and nothing of the user's. `update_check: false` in
  console.json turns it off. Every other reader stays local; a new outbound call needs the same
  off switch and a line here.
- **Read-only over every other package's data.** Module SQLite stores open with better-sqlite3
  `readonly: true`; JSON state is only read. The sole writable store is `~/.cherrypick/data/console/`.
  Handles are pooled per path and recycled on the file's stamp (`readers/db.ts`), so a module's write
  (a migration included) is seen on the next request; idle handles hold an open file, never an open
  read transaction, so they cannot starve WAL checkpointing.
- **A reader that states a total and its breakdown reads one snapshot** (`withReadSnapshot`). Each
  statement is otherwise its own transaction, so a total from one query and rows from another can
  straddle a module's write, and a summary's hero would stop equalling the sum of its arms
  mid-session. The snapshot lasts one synchronous read; the summary readers use it.
- **⚠️ `withReadOnlyDb` swallows EVERY throw into its fallback and the request still returns 200.**
  Deliberate (a store may be absent because the module never ran here), but **a broken reader is
  indistinguishable from an empty one**, and a green `vitest` run is not evidence a reader change
  works. It has hidden defects behind a healthy-looking page. So: after changing a reader, hit
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
    the answer to the whole era (289 rows beside a 34-position day). A thrown resolve scopes
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
  - **The Builder's save is the same path for one automation** (`/api/automations/builder/draft` and
    `/save`, configcli `automation_draft` / `automation_save`). The route sends a name and an
    automation and nothing else; the orchestrator runs the module's own check and writes the entry it
    returned, which is always off. The Builder never turns an automation on, and never writes its
    register label. A saved automation opens read-only; a change to one is a copy.
  - **Automations › Groups writes the groups and the switches by the same path**
    (`/api/automations/groups/edit`, `/check`, `/save`; configcli `automation_groups*`,
    `orchestrator.groupedit`). Three levels (every group, `?group=`, `?group=&automation=`), each
    change staged and written only from Review. The route sends the groups that will have members
    (each spec whole), the automations that move and the automations whose own switch flips (true or
    false), nothing else; the module (`cli groups-check`) loads the change as the paper loop would and
    returns the `groups` block, the moves and the switches with the start it gave each one switched on
    (the next session, so a paper record never begins mid-day), which are all the orchestrator
    writes. Each automation's state (`on`, `waiting`, `off_group`, `off_own`) is the module's
    (`cli groups-view`), and Review's "what it does to trading" lays the module's after-view beside the
    view now; the page derives no state. Paper only: nothing here places an order.
  - **The Builder's form reads the module's schema and "Today, live"; neither writes.** The form's
    choices are `cli builder-schema` (`/api/automations/builder/schema`, the engine's own tables),
    so the page spells no gate, strike rule or exit of its own. "Today, live" is configcli
    `automation_today` (`/api/automations/builder/today`, a POST only because it may carry an
    unsaved edit): the module decides the automation now on a read-only snapshot and writes nothing.
    The shape and chain example under the legs (and on the Groups tab's automation page,
    `TradeShape.tsx`) are configcli `automation_payoff` (`/api/automations/builder/payoff`, the same
    body): the module returns the generic shape from the rule (`sketch`: each leg's position and
    label, the curve in nominal units) and, where live quotes or our own last recorded session place
    the legs, the chain example with each row's offset from the money and the example's money; the
    page draws them and computes no strike, position, price or P&L. With a far leg both are at the
    near expiry with that leg's time value by the module's model (`modeled`): the shape's title, top
    and caption say so, the example names the modeled peak with each far leg's vol and days left, and
    a far leg's tag reads "· far". A leg's words and its ID
    (`legWords.ts`: L2 · Short Put ×2, the next ID) are the page's. A leg is written in the page's
    own keys, `action` (BTO, STO), `qty` and `side` (put, call), which the module checks; the form
    reads the older spelling too (`parts`: `side` as signed contracts, `right`) and writes any leg it
    edits back in the page's (`writtenLeg`). The module's editor and saves convert as well.
    The strategy selector (`StrategyPicker.tsx`, the legs section and the wizard's Start) is configcli
    `automation_strategy` (`/api/automations/builder/strategy`: a strategy's name and plain settings,
    checked by shape): the module builds the legs and the expiry (`strategies.py`) and the page writes
    them into the edit; the list and the settings are the schema's (`strategy`). It writes no config.
    A calendar or a diagonal also writes the far expiry, and the close before the near expiry where
    the automation has none (`withStrategy`); a one-expiry strategy leaves no far rule behind.
    Section 1 shows the far expiry under the near one (`FarExpiry`: its rules are the schema's
    `far_expiry`, a button adds it, removing it puts every leg back on near), and once there is one,
    each leg picks Near or Far in an expiry column (`has-expiry`).
    The entry presets (`EntryPicker.tsx`, the signal section and the wizard's Entry step) are configcli
    `automation_entry` (`/api/automations/builder/entry`: a preset's name and plain settings, and
    each stacked filter's name and plain settings, checked by shape): the module builds the `entry`
    and the filters' gates (`entrypresets.py`); the page sets the entry and adds the gates, and a
    second fill swaps out only the gates the last one added that still stand as added
    (`withFilterGates`). The lists and settings are the schema's (`entry_presets`). It writes no config.
    The wizard (`BuilderWizard.tsx`) is the same calls and the same save: its strategies (or, from an
    older module, its shapes) are the
    schema's, each step's Next is the module's draft check filtered to that step's keys, and it
    writes only through `/save`, always off.
  - **The Builder's backtest queues; it writes no config** (`/api/automations/builder/backtest`,
    configcli `automation_backtest`). The route sends the name and at most one window key, the
    module's own token for one of the suggestion's `windows` (checked by shape here, `WINDOW_KEY`);
    dates and a store from the page are never sent. The orchestrator takes the window and the source
    from the automation's own backtest suggestion (our own stores first, the vendor's only while the
    licence allows new work, marked `licensed`), refuses a key the suggestion does not offer, where
    a run is not possible or one is already live, and adds one
    research-queue `script` row of the automation backtest driver. The run is an explore run: no
    trial, no verdict, no label moved. The explore-runs list (`/api/automations/builder/runs`,
    configcli `automation_runs`) shows its rows and results, each with its net curve and what it
    changed; one run's card (`/api/automations/builder/run`, configcli `automation_run`) is read
    whole by the backtest package. A licensed result is shown only while `backtestResultsOn`
    (fail closed: one not plainly unlicensed is withheld and counted, and its card refused 403).
  - **The Builder's idea search queues, declares and tests; it writes no config**
    (`/api/automations/builder/ideas`, configcli `automation_ideas` and `automation_idea_searches`;
    `/idea-search`, `/idea-declare`, `/idea-holdout`). Find ideas sends the name alone; the run and the
    split are the backtest package's. Declare and the held-out test send the name, the search's id
    (`IDEA_SEARCH`) and one idea's id (`IDEA_ID`), checked by shape. Both read the search first, so a
    licensed search withheld by `backtestResultsOn` is never declared from or spent (403, fail
    closed). Declare drafts a register row into the search's result; the row is added to the
    register by hand.
  - **The Builder's settings grid queues, declares and tests the same way; it writes no config**
    (`/api/automations/builder/grid` and `/grids`, configcli `automation_grid` and `automation_grids`;
    `/grid-result`, `/grid-declare`, `/grid-holdout`, `BuilderGrid.tsx`). Try settings sends the name
    alone. Declare and the held-out test send the name, the grid's id (`GRID`) and one setting's id
    (`SETTING_ID`), checked by shape, and read the grid first, so a withheld licensed grid is never
    declared from or spent. A grid's read serves its rows, never its trades.
  - **Adding an underlying to the full set queues fetches; it writes no config**
    (`/api/automations/builder/fullset-plan`, configcli `fullset_plan`, a dry run, counts only; and
    `/api/automations/builder/fullset-add`, configcli `fullset_add`, behind the CSRF token). Offered
    (`AddToFullSet`) only where the suggestion is not possible and says the underlying is outside the
    set (`full_set: false`; an older module that does not say gets no offer). The route sends the
    symbol (checked by shape, `FULLSET_SYMBOL`) and the automation's name; the span, the 300-session
    cap, the licence check and the rows are the orchestrator's. The page shows the plan first and
    adds on a second click. The bridge gives these two ops longer timeouts (`SLOW_OPS`).
  - This surface **cannot touch `enable_live_trading`, flies' `live.enabled`/`gate0_confirmed`, or the
    live loss/deploy limits** in either direction: `configedit.GUARDED` refuses them, and the page
    shows them as locked rows with that table's hint.
  - The halt flag (`state/halt-live.flag`) is reachable via `liveops.set_halt`, with **asymmetric
    friction**: setting it is one click (a two-step stop arrives late); clearing it needs the typed
    `RESUME LIVE`, checked on the server as well as the browser. Clearing arms nothing, and the page
    says so.
  - What the page offers is an allow-list (`web/src/pages/Config/fieldMeta.ts`); the suite has no JSON
    schema, so that map is the form's schema. Every field must edit a key some code reads
    (`configFieldsAreRead.test.ts`). Writes are gated like the orchestrator's settings server
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
- **The ladder's Live tab reads the live path and nothing else.** It shows `live_loop --status` through
  `services/ladderLiveBridge.ts` (the gates, mode, arm, halt flag, held submissions and the last tick's
  checks are the module's own; no gate is re-derived) and `live_trades.db` through `readers/ladderLive.ts`
  (open and sold lots, orders, the journal). It has no write path of any kind: the bridge's one verb is
  `--status`, the store opens read only, both routes are GETs, and the page has no control
  (`ladder-live-no-write.test.ts`). Arming is the `/live-ladder-start` command. A status it cannot read is
  neither ready nor not ready; the badge says `LIVE` only on an explicit `live.dry_run: false`. The live
  ledger is absent on most machines and that is an empty state, not an error. Every account is masked in
  the reader whatever the ledger holds. The suite-wide Live page also lists the ladder as a loop
  (`readLadderToday`, files and SQLite only): its worst case is the cost of the open lots, it states no
  daily-loss limit, and its mark is n/r because the live ledger keeps none.
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
  `CURRENT_ERA` with an era picker (`test/pmcc-era.test.ts` pins the copy to the
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
- **An account is named with its broker login** (`readers/accountName.ts`): the masked number on the
  main login, `<login> ****1234` on another (`broker.logins`). Every page keys an account by that
  name, so a reader of manage's evaluation or marks never uses the bare masked number: two logins can
  share a last four. The console's own broker session stays the main login's. The rest of the suite's
  per-login state is read per login too:
  - **Quotes:** manage's live marks come from each account's own producer cache
    (`stream_instances`, `instanceCachePath`), never another login's.
  - **Hand trades:** the 0DTE hand view is one card per account.
  - **MEIC's live accounts** (`meicLiveAccounts`: ledger, arm record, `live_accounts` block,
    readiness via `--login`, job `meic-live.<login>`) are the `?login=` of Live → meic and their own
    `meic.<login>` rows on Live → today.
  - **System page:** shows each live account's arm, each producer instance's daemon and log.
  - **Model a trade:** models against the account it was opened from.
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

Package-specific: **loopback-only serving**; the console
**computes no verdicts** — it renders what the modules decided, and where it mirrors a module's queries
it says so and is checked against them.
