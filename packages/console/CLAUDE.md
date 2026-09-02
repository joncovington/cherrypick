# console (unified web UI)

The suite's unified reactive web UI: one app covering every module's read models — overview/watchdog,
MEIC, flies, earnings, PMCC-99, calendars, curve, BWB, GEX — plus the advisor and the reports. It
**replaced** the old surfaces on 2026-08-12: the suite dashboard, the MEIC/flies/GEX dashboards, the
earnings strategy dashboard and scout's web app were deleted, and this is the suite's only read
surface. It touches none of their code — every module remains a producer this package reads.
`pre-console-only` is the tag that still has them.

**The research surfaces are gone too, as of 2026-08-31**: watchlist, screener, builder, payoff/POP
and the staged dry-run tickets, inherited from scout in the 2026-08-12 port and retired in turn. So
this is now a read surface for the TRADING MODULES only, and it no longer holds a path that touches
an order at all — `dry-run-only.test.ts` pins `postOrderDryRun` to the scope probe alone. See
`docs/parity.md` for what deliberately survived the teardown and why.

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

**The two session reports share one page.** `/reports` holds the pre-open morning pack
(`packages/overview`) and the end-of-day review (`packages/review`) as tabs — the same question
asked at two ends of a session, and two nav links each leading to half of it made the nav longer
without making either easier to find. The page holds the tab and nothing else: each tab renders its
own page component unchanged, so neither report gains a second place where its shape is decided. The
tab lives in the URL (`?tab=eod`) because a report is a thing you send someone, and the old
`/morning` and `/review` routes redirect rather than 404 — both appear in the suite's own docs.

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
pnpm ui-check --route /flies --click performance --expect "drawdown"   # drive the REAL browser
```

**Confirming a change actually reached the page.** The suite's rule is that a front-end change is
confirmed in a browser, not by tests alone. Three things make that non-obvious here:

- **The running server is `server/dist/index.js`, not your source.** The supervisor launches the
  built artifact, so a source edit changes nothing until `pnpm build` AND the process restarts.
  Editing, re-running the tests and reloading the page will show you the OLD build behaving
  perfectly. (A shared-type change also needs `pnpm --filter @console/shared build` before the
  server typechecks against it.)
- **`pnpm ui-check` drives real Chrome** — clicks, expectations, screenshots, and console errors —
  which is what reaches anything behind a tab held in component state. `--dump <file>` writes the
  rendered DOM when you want to read it rather than assert on it.
- **Under Git Bash, prefix it with `MSYS_NO_PATHCONV=1`** or a route is rewritten into a filesystem
  path: `--route /flies` arrives as `C:/Program Files/Git/flies` and the check silently targets
  nothing useful.

The reliable recipe for a reader or endpoint change: capture the affected endpoints from the
RUNNING build first, then build, restart, and re-capture. Every data field should be identical and
only clock-derived ones (`now`, `ageSeconds`) should move. That is what caught this session's
changes as safe, and it is the only check that sees past the fallback below.

## Data rules

- **Read-only over every other package's data.** Module SQLite stores are opened with
  better-sqlite3 `readonly: true`; JSON state is only ever read. The console's sole writable store is
  `~/.cherrypick/data/console/`. Handles are POOLED per path and recycled on the file's stamp
  (`readers/db.ts`), so a module's write — a migration included — is picked up on the next request;
  idle handles hold an open file, never an open read transaction, so they cannot starve WAL
  checkpointing.
- **⚠️ `withReadOnlyDb` swallows EVERY throw into its fallback, and the request still returns
  HTTP 200.** That is deliberate — a module store may legitimately be absent because the module has
  never run here — but it means **a broken reader is indistinguishable from an empty one**, on the
  wire and on the page. It also means a green `vitest` run is NOT sufficient evidence that a reader
  change works: the tests exercise the query, and the fallback hides the query failing.
  This has produced two real defects. `/api/flies/meta` once returned `{arms: [], dates: [],
  symbols: []}` with no log line, from one bad column in a UNION. And a day resolver that named a
  journal table an older ledger lacks read as "no latest session", so a tab meant to show one day
  answered for every day in its era. Both looked healthy.
  So: after changing a reader, **hit the endpoint against a rebuilt, restarted server** (the recipe
  under Commands) and check the payload is populated, not merely 200. Where a reader can be handed
  a store whose shape varies — an older paper book, a live book, a test fixture — ask
  `sqlite_master` which tables exist rather than naming one and relying on the catch.
  (`withReadOnlyDb` still collapses "store absent" and "query threw" into one return value, and that
  is fine — ~65 call sites are written against it and it is unchanged. **`readOnlyDb` is the opt-in
  form beside it** (2026-08-26), returning `{status: "ok" | "absent" | "failed"}`, for a reader whose
  EMPTINESS IS MEANINGFUL. It is the single implementation and `withReadOnlyDb` is a thin wrapper
  over it: two copies of the pooling, stamping and eviction logic would be two chances to disagree
  about when a handle is recycled, which is the bug the stamp exists to prevent. Migration is per
  call site and needs no sweep. `readFliesMeta` is migrated — the documented incident — and now
  returns the same empty lists plus an optional `degraded: {reason}`, absent on both healthy reads
  and legitimately absent stores, so a consumer that ignores it is right in every case that is not a
  defect.

  **The day resolver is migrated too, and its failure is not symmetric with the others.** `null`
  from `latestTradeDate` means "latest day", and `filterSql` turns that into NO date clause — right
  for "this ledger has no rows", wrong for "the query threw", because the second WIDENS the answer
  to every session in the era. That is the recorded incident: 289 rows beside a 34-position day,
  both correctly labelled and irreconcilable. A thrown resolve now scopes to a day that matches
  nothing instead, because showing nothing is visibly wrong while showing the whole era looks
  plausible. Note the existing `sqlite_master` guard already covers a MISSING day-source table; what
  remained was schema drift inside a present one — `fly_positions` without `trade_date` — which
  passes that guard and then throws. **What changed 2026-08-26 is that the second case is no longer
  invisible.** A throw is recorded per store (path, message, count, last seen), logged once per
  distinct message — repeats are counted, not re-logged, since the SPA polls every few seconds and a
  wedged reader logging each poll buries itself as effectively as logging nothing — and surfaced by
  `/api/health` as a `readers` array. An absent store is deliberately NOT recorded: a fresh machine
  would otherwise warn about every module it has not installed.

  `/api/health`'s `ok` still means "the server is up" and is unchanged, so a watchdog reading it does
  not start failing because one ledger has a bad column. Read `readers` for that; an empty array is
  the healthy case. This does not remove the need for the recipe above — a reader can still return a
  structurally empty result without throwing at all, which is what the day-resolver defect did.)
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
- **The Advisor page's two buttons are the second bounded exception, and they hold no logic either.**
  Kill an experiment, dismiss a proposal — both POST to `routes/advisorOps.ts`, which invokes
  `python -m cherrypick.advisor <verb>` as a subprocess (`services/advisorBridge.ts`, the same
  shape and the same reason as `configBridge.ts`). Killing an experiment journals a reason, stops
  tonight's artifact being issued for it, and lets a queued experiment take its slot; that
  lifecycle lives in one place in Python, and the scheduled runs and the browser go through the
  same door. Both actions only ever make the advisor do LESS — there is deliberately no way to
  start, tune or enact anything from the browser, because those are the directions that add
  exposure and they belong to the validated, scheduled path. Everything else on the page is a
  read of `data/advisor/advisor.db` (read-only) plus the advice artifacts, and it **computes no
  verdicts**: those come from `packages/advisor`, through the suite's own
  ledger-readers → `compare_profiles` → `qualify_readings` chain. A TypeScript re-derivation would
  be a second opinion free to drift, which is the mistake `services/report.ts` already made once.
  **"Did the loop apply this artifact" is one of those verdicts**, and it is read from the
  `enactment` table rather than recomputed by comparing an artifact's params to a decision file
  here — same rule, and the comparison is genuinely subtle (a reject-all artifact beside a baseline
  decision IS enacted). Rows are absent on a store that predates the table, which the page renders
  as "not scored yet"; an unscored session and a dropped artifact are different facts and only the
  second gets a warning chip.

  The apply banner is worth knowing the history of. It used to show tomorrow's artifact beside
  **today's** decision — two different sessions, which can never agree — so on 2026-08-25 meic and
  earnings sat in it reading "written" next to "advice_disabled" with no warning anywhere, and the
  card is collapsed by default so the closed head was all anyone saw. Two columns now: what is
  queued for the next session, and whether THIS session's artifact landed, with the count of
  failures on the head. If you touch that table, keep the signal on the head.
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
- **Where a module states its read semantics but not a callable surface for them, MIRROR them and
  say so.** `packages/pmcc` declares `analytics.py` "the one query layer every read surface goes
  through", but its CLI exposes only part of what a page needs (no per-cycle legs or rolls, no
  attempts/events), and a subprocess per request at a 15s refetch is not what that layer was built
  to carry. So `readers/pmcc.ts` re-implements those queries in TypeScript, names the analytics
  function each one answers for, and keeps that module's stated rule that `None` never means zero —
  a null time value renders as an em-dash, never `$0.00`, because "not recorded" and "was zero" are
  different facts. This is a deliberate exception to the bridging rule above and it is only safe
  while the mirror is checked: the page's headline must equal `python run.py headline`. Verified when
  the page landed (2026-08-17); an earlier version of this check also compared a drawn Keltner band
  against the gate's own stamped measures, which caught a real off-by-one-bar error before it
  shipped — that half retired with the 2026-08-23 redesign (see `packages/pmcc/CLAUDE.md`'s
  measurement-break note), which dropped the module to one symbol, one book, and no Keltner gate at
  all. **MEIC has the same arrangement and, since 2026-08-26, the same check.** `readers/meic.ts` is the
  largest mirror in this package, over the module with the most data and the only live sibling, and
  it had nothing to compare against because meic was the one package without a `run.py`. It has one
  now, and `server/test/meic-mirror.test.ts` compares the page's per-arm trades, sessions, gross,
  fees and net against `python run.py headline --era ALL`. It failed on its first run and the page
  was wrong: `RESOLVED` here is a deny-list (`NOT IN ('cancelled','pending','partial_entry')`) and
  this ledger contains none of those, so it admitted still-open rows, which contribute fees with no
  gross — every arm reported down by exactly what it had paid so far. The module uses an allow-list.
  `readMeicAnalytics` already guarded the identical case in SQL and its comment names it ("a number
  that looks like a result"); `readMeicPerformance` did not. Both now filter `pnl IS NOT NULL`.

  **The headline half is automated** — `server/test/pmcc-mirror.test.ts` invokes the module's
  own `run.py headline` and compares open-position count, book set and each book's net to the cent,
  skipping cleanly (and visibly) where the ledger or Python is unavailable. Note it compares empty
  against empty until this module opens a position under the new design, so treat it as armed rather
  than as evidence.
- **The calendars page is the same question answered the other way, and the split is the point.**
  `readers/calendars.ts` reads that ledger directly like every other reader here, but two things it
  will not compute go out through `services/calendarsBridge.ts` as a subprocess: the exit-policy
  table and the week's calendar anchors. Not because they are expensive — both are memoised at 5–10
  minutes and neither moves on a poll's timescale — but because both would be *re-derivations*
  rather than reads. The policy table is a tick-by-tick replay welded to a validation that
  reproduces the real books to the cent, and a TypeScript second implementation is the one kind of
  drift that validation could not catch: it would be validating the wrong derivation. The anchors
  are NYSE holiday arithmetic whose structure tag is the key every result is grouped by, and a
  second calendar is a second calendar free to disagree. The rule that separates the two pages:
  **mirror a query, bridge a derivation.** A query can be checked against its source by reading
  both; a derivation with its own validation can only be checked by being the one that was
  validated.

- **A module's own evidence window is the default.** Where a module narrows its analytics to a
  current era or study window, the console's reads default to the same narrowing rather than
  showing every row it can reach — MEIC's `CURRENT_ERA` is the case in hand. Earlier eras stay
  reachable through a visible scope control, so widening is a stated choice and never the quiet
  default. Filtering to nothing is reported as a filtered-out result, not an empty page.
  Since 2026-08-21 this applies to EVERY history/performance/reporting surface: meic and flies
  scope on their own era models, and the modules with no era column (earnings, plus the suite
  report and review totals) bound to the suite's `data_epoch` via `readers/db.ts::suiteEra` —
  one source, the same lever `calibrate` enforces, widened per-surface with the shared `"ALL"`
  convention. calendars/pmcc need no bound: their ledgers were empty before the era began, so
  current-era and all-history are the same set until a second era exists.
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
serving; deterministic solutions preferred over AI/agentic ones (root file). This package computes
no verdicts of its own — it renders what the modules decided, and where it mirrors a module's
queries it says so and is checked against them.
