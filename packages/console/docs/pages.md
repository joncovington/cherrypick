# Pages

## Page index

A user-facing list of every page the rail can open. The source of truth is
`web/src/components/shell/navLinks.ts` (the rail) and `web/src/lightbox/navGroups.ts` (each page's
tabs); this index is checked against them by hand, so change the two files and this table together.
A page the suite has switched off is not shown (see *What is off is not shown* below).

**Suite pages** (the rail's top and bottom sections)

| Page | Route | Tabs | What it is |
|---|---|---|---|
| Live | `/live` | today, positions, portfolio, wheel, pmcc, flies, meic | Real money first: what is open at the broker, the book as a whole, the wheel, and one tab per live loop (flies and meic only while their live switch is on or the loop still holds something). `/` redirects here. |
| Reports | `/reports` | morning, eod, external | The morning pack, the end-of-day review, and **External reports**: the tabs people add with a script of their own (`/reports/external/<id>`). |
| Charts | `/charts` | intraday, technicals, setups | Intraday charts, the technicals cards, and the setups the technicals package finds. |
| GEX | `/gex` | gex, iv skew | Dealer gamma by strike for the recorded day, and the IV skew. Reads the market; trades nothing. |
| GEX panel | `/gexpanel` | panel | Experimental, SPX only: a walls panel on the recorded data. |
| Screeners | `/screeners` | two groups, eight tabs. Screens: earnings, earnings record, signal board, skate. Income lists: puts & calls, credit spreads, time spreads, fair value | The suite's own screens and income lists. The Dolt-backed tabs show a set-up card, not a blank, without Dolt. |
| Compare | `/compare` | arms, risk, calendar, portfolio, slices | Every arm side by side. Left off the rail in the simple view. |
| Backtest | `/backtest` | studies, queue, runs, data | Research studies and the queue. Fails closed: hidden unless the suite says the backtest feature is on (licensed data). Left off the rail in the simple view. |
| Advisor | `/advisor` | advisor | The advisor's experiments roll-up. Paused. Left off the rail in the simple view. |
| System | `/system` | health, supervisor, data, environment, logs | The suite's own health, read-only. Left off the rail in the simple view. |
| Config | `/config` | arms, modules & symbols, timing & cadence, alerts, position rules, then prefs | The one place configuration is edited, through the orchestrator's config editor. Always lists every toggle. |

**Module pages** (the rail's Modules section, then the Experiments page)

| Module | Route | Tabs | Status |
|---|---|---|---|
| MEIC | `/meic` | today, arms, forest, attempts, positions, history, sessions, exits, regime cuts, performance, analysis, costs, advisor, help | Active; live behind its own gate. |
| BWB | `/bwb` | today, positions, history, decisions, arms, entry vol, live vs paper, performance, analysis, advisor, help | Retired; ships off. |
| Earnings | `/earnings` | today, positions, history, decisions, screening, strategies, advisor | Active; paper only. |
| Experiments | `/experiments` | experiments | The comparison page for the paper-only experiments and the retired modules below. |
| PMCC | `/pmcc` | today, by arm, positions, tracker, history, decisions, arms, performance, analysis, costs, advisor, help | Retired; ships off. |
| Calendars | `/calendars` | today, plan, positions, history, decisions, arms, policies, performance, analysis, advisor, help | Experimental; paper only. |
| Curve | `/curve` | today, regime, positions, history, decisions, arms, performance, analysis, costs, advisor, help | Retired; ships off. |
| Contango | `/contango` | today, regime, positions, history, decisions, arms, performance, analysis, costs, help | Experimental; paper only. |
| Weekly put | `/weeklyput` | today, cycles, positions, history, decisions, performance, costs, help (8 tabs) | Experimental; paper only. |
| Flies | `/flies` | session, summary, forest, timeline, positions, history, books, opening range, attempts, decisions, gates vs control, live vs paper, selector, regime cuts, intraday agent, completion, performance, analysis, costs, advisor, help | Retired; ships off. |

The simple view shows a shorter tab list per page (`ModuleNavDecl.simple`); the detail view shows all.
Keyboard: `g` plus the page's key for suite pages, a bare digit for the four modules on the rail,
`j`/`k` for tabs.

The sections below are the working notes for each page. Read the section for a page before changing it.

**Shell.** A grid, not a stack: the brand head and the top bar are row 1's two
halves (ticker, session chip, the simple/detail view switch, clock; the top bar is a size container,
so as it narrows the session chip shortens to one word and the clock drops the date (under 1010px), the
chips stack symbol, price and percent (under 860px), and on a phone the ticker takes a row of its own
with prices of 1,000 or more shown without cents), the rail and the content pane are row
2's, the status bar (health chips, watchdog/session/morning phase, logs, and the suite version
last) is row 3. The version chip (`shell/VersionChip.tsx`, `/api/version`) is plain while this
checkout is at or past GitHub's latest release, and an amber link to the release notes, "vX · vY
available", once a newer one is out — the one place a release is announced. The brand
head's underline is the same grid track as the top bar's — one rule across the window in both
rail widths — and the rail's vertical is the brand head's right border continued by the nav's,
unbroken from the top of the window to the status bar. **The rail is the one nav** (mounted in
the shell, on every page; collapsed to a 64px icon strip by default, its own
toggle at its foot — the top bar holds no menu and never opens one). Below 1000px the rail's
column stays the icon strip and an expanded rail opens over the content. There is no command palette: the keyboard hints
(`g` + key for suite pages, a bare digit for modules, `j`/`k` for tabs) are in the rail's link
tooltips, built from `navLinks.ts` so a hint cannot outlive its shortcut.

**Views.** One console pref (`viewMode`; absent means `detail`) switches every module between
`simple` and `detail`. Each module lists its simple tabs in its own vocabulary (`ModuleNavDecl.simple`
in `navGroups.ts`); detail shows every tab; a module that declares no list shows everything in both.
Simple view also leaves Compare, Backtest, Advisor and System off the rail (`SIMPLE_HIDDEN_PAGES`,
`navLinks.ts`). A bare module URL lands on the view's first tab, a deep link to a tab outside the view still renders
(and the rail keeps it listed while you are on it), and `j`/`k` step through the view's tabs.
Within a page, a column, tile or card is left to detail by marking it `detailOnly` and filtering with
`useColumns` (`components/columns.tsx`); a table declares each column once, header and cell together
(`Column`, `HeadRow`/`headersOf`, `Cells`), so dropping one cannot misalign a row. The Live tables use
it; a column removed from both views is simply not declared.

**Page design tests.** A page or tab earns its place by answering one of four questions: would a
person open it, and for what; is it clear (a short card with a small set of figures beats a row with
too many columns); does it move the user toward their goals (premium income from short puts, covered
calls and credit spreads on the 30-60 DTE monthly, inside their delta and theta bands); or does it
show how an automated loop is working (entering, filling, stopping, earning against paper and
control, anything wrong today). A tab that answers none is developer detail and stays out of the
user's path. The rules that follow from them:

- **One today tab per module** (id `summary`; `/x/session` lands there). It opens with a status line
  saying whether the module is live or paper and, in words, its strategy's tested status with a link
  to the study (`web/src/lib/testedStatus.ts`). A module whose latest traded day is not today labels
  the window "last traded", never as today, and a live summary with no row says live has not traded.
  Flow says when a later capture was refused and which checks it failed.
- **Wide tables keep a short row in both views.** A column marked `secondary` (`components/columns.tsx`)
  moves to the row's expanded line; money columns (entry, exit, gross, each cost, net) are never split
  (`docs/conventions/money-layout.md`), so a wide history is trimmed on its descriptive columns.
- **The same evidence is shown once.** A module's `arms` tab keeps what Compare does not show (each
  arm's guide, Contango's NAV comparison, Curve's flip divergence, BWB's fire counts); the module
  `advisor` tabs stay off the module pages while the advisor is paused.
- **Rail keys are fixed:** MEIC 1, BWB 2, Earnings 3, Experiments 4 (an off module's digit does
  nothing); Config is `g o`; the Suite header is dropped when Config is all it would hold.
- **Income candidates stay on Live portfolio**, where they are sized to the user's delta and theta
  room, and are not ranked ("top three") because nothing about them has a tested edge.
- **No view of the user's short strikes against the GEX walls.** Walls exist only for the same-day
  SPX/XSP expiry, and the user's shorts are single names and ETFs at 20-60 DTE, so a same-day wall
  would mislead about a monthly strike. It would need a wall built for the monthly expiry to be shown
  to predict something first.

**Experiments page** (`/experiments`, `pages/Experiments/ExperimentsPage.tsx`): a card per paper-only
experiment (PMCC, Calendars, Curve, Contango) built from that module's own summary read:
the status line with the tested verdict and last traded day, all-time net, open now, and links to its
today, open, closed and performance tabs. The module pages stay whole and reachable; the rail opens
the chosen one out under Experiments while you are on it. Flies is a record card on the same page
with its paper and live all-time nets; its tabs stay because it may be reinstated.

**Help page** (`/help`): the user guide in the checkout's `docs/guide/`, so the guide always
matches the code it describes. Two tabs. `guides` holds every page at `/help/<slug>` (its own route in
`App.tsx`, since a slug is not a tab id): a table of contents grouped and ordered by
`docs/guide/README.md`, with a search over titles and headings; the page; "On this page"; and the
previous and next pages in index order. Prose keeps an 88ch line while a screenshot takes the
column's width, and a click opens the screenshot full size. "On this page" drops out below 1300px; below
900px (a tablet in portrait, a phone) the page is one column with the table of contents folded behind
a "Guides" toggle above it. `faq` (`/help/faq`) shows `faq.md` as collapsible rows, with a
filter, expand-all and deep links (`/help/faq#<question>`) that open the row. The server
(`readers/help.ts`) serves only slugs the index lists, and images from `docs/images/` by bare name
(PNG, JPEG, WebP; never SVG). Markdown is `react-markdown` + `remark-gfm`, pinned, with no raw-HTML
plugin, so a guide's HTML is shown as text. Headings carry GitHub's anchor ids, and a link to another
guide stays in the app while a link to any other repo file opens it on GitHub. `tools/check_guide.py`
(CI) keeps the index, links, anchors, images, the FAQ's shape and the quoted commands true.

**Guide links.** A card's ? box, a module's help tab, the Backtest licence flag and the Config page's
halt control end in "Read the guide →" (`components/GuideLink.tsx`), a link to the guide section that
explains the card. The address is a string literal at each call site, `<GuideLink to="/help/live#today" />`,
never built from parts, because `tools/check_guide.py` (rule 2) reads every `/help/<slug>#<anchor>`
literal in the web source and fails when that guide page or heading does not exist, so renaming a
heading breaks the build rather than the link. `web/test/guideLink.test.tsx` pins the rendered href and
that every use is a literal. Link only where a guide section really explains the card.

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

**What is off is not shown.** Whether a module, GEX (the `gex-recorder` service),
the advisor, the technicals cards or a narrative is on comes **only** from the orchestrator's `configcli` op
`features` (`services/featuresBridge.ts`, memoised 15 s and dropped on every config save or halt
toggle; `GET /api/features`), never from reading the config here — `enabled` already folds in
capabilities like Dolt and Claude Code. Every web rule is in `lib/visibility.ts`; the desk
rows and the default log sources are filtered on the server with the same answer. **Fail open:**
loading, `{ok: false}` or an id the reply does not name all show the thing, with a status-bar chip on
failure. A Live tab named for a module (flies, meic) follows that module's live master switch,
which the same reply carries as `live`, but only once that loop is idle: it hides on an
explicit `false` AND a read Live Today payload reporting nothing for the loop (no open position,
working or held order, orphan, overdue settlement or attention item; `liveLoopsInUse`). A switch
turned off mid-session must not strand the one page showing what is still open, which Live Today
links to (#422). An unread Live Today keeps it shown, and the account-wide tabs never leave. A direct URL to an off page renders `ModuleOffCard` (why, and a link to Config), never a 404
and never the module. Config always lists every toggle. Calendars, pmcc, curve and contango carry
an `experimental` chip, listed once in `moduleOrder.ts`'s `EXPERIMENTAL_MODULES`. Flies, bwb, pmcc and curve are retired and ship off, so a fresh install shows each as off until it is reinstated; Live → pmcc is the real account's view and stays.

- **The Dolt-backed Screeners tabs are shown off, not hidden** (the user's choice):
  earnings, earnings record, signal board and skate (`SCREENERS_NEED_DOLT`). With `features.technicals`
  false the tab renders `pages/Screeners/NeedsDolt.tsx` in place of its content: Dolt not set up
  (`capabilities.dolt`) gets the install steps and the INSTALL link, the switch off points to Config.
  A tab that vanished, or showed an empty table, read as "no data today"; a fresh install was told it
  needed a vendor. Fail open like the rest: unknown renders the tab.
- **The one exception fails closed: Backtest** (`FAIL_CLOSED_PAGES` in `visibility.ts`). Its results
  come from licensed vendor history whose licence forbids redistribution, so it is shown only when
  the reply was read and says `features.backtest === true` — hidden while loading, on `{ok: false}`,
  when the key is absent and when false. Hidden, it has no off reason, its tabs and rail link go, and
  a direct URL is the 404 page (`ModuleRoute`), not the off card. The server refuses its routes on its
  own (`featuresBridge.backtestOn`), so the browser hiding it is never what keeps the data in. Each
  guard was shown to fail flipped to the fail-open form (`visibility.test.ts`, `routes.test.tsx`,
  `backtest-routes.test.ts`).

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
- **The rail's order is a decision, pinned by `navLinks.test.ts`**: Live first, the
  market reads (Reports, Charts, GEX, Screeners) above the modules, the modules
  live-capable first (`TRADING_MODULE_ORDER`), then Research and the suite's own pages, Config last.
  Inside every module the tables group (positions, history) follows the today group.
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
(`packages/review`) as tabs, and the reports a person's own scripts write (the External reports tab, below); each of the first two renders its own page component unchanged, so neither report gains
a second place its shape is decided. The tab is in the URL (`/reports/eod`) because reports get sent, and
`/morning` and `/review` redirect rather than 404.

- The Morning tab also shows `packages/technicals`' report (`data/technicals/report-<session>.json`,
  `readTechnicals` in `readers/overview.ts`, `pages/Morning/TechnicalsCards.tsx`). **It is the last
  report dated strictly before the pack's session** — a same-day report was written after that
  close and would show the morning what it could not have known. A test pins the `<` and was shown to
  fail at `<=`.
- Its leaders and scan matches link to the technicals chart, which is on the Charts page.

**External reports** (`/reports/external`, the third Reports tab; `pages/External/ExternalReportsPage.tsx`,
reader `readers/externalReports.ts`, route `GET /api/external-reports`, types `shared/types/externalReports.ts`)
draws whatever a person's own scripts wrote to `data/external/<id>.json` (`docs/guide/external-reports.md`),
with no source named in the code: the file says its own title and source.

- **The tabs follow the files.** An *overview* tab lists every report (title, source label, `as_of`, a
  fresh or stale badge, and each invalid file with its reason), each row linking to that report's own
  tab at `/reports/external/<id>`. A new file adds a tab and a removed one takes it away; there is no
  config. The strip is the page's own, not the rail's: the rail is static data (`navGroups.ts`) that
  `ModuleFrame` checks against each manifest's slide list, and a tab list that depends on the server's
  files cannot live there without breaking that check. The rail has the one `external` entry; the
  report tabs route through the same frame (`App.tsx`, `ModuleRoute`'s `forced` module and slide).
- **A report's tab** shows the title, a stale badge when `as_of` is older than `stale_after_hours`, the
  source and age, the one-line summary, and each section as its own grid card titled by its heading: a
  table section as a table (null cells are a dash, never 0), a text section as plain text. Every card
  carries a small glyph so `ui-check --card "<heading>"` can capture it.
- **Bars.** A table may carry `bars: [column index, ...]` (schema 1, optional): those numeric columns get
  an inline bar beside the number, scaled by the column's largest magnitude (`barScales` / `barPercent` in
  the page). A column with any negative value gets a zero line in the middle, negatives drawn to its left
  in `--neg` and positives in `--pos` (both defined in each theme); a null cell has no bar and a column of
  nothing to scale has none. The bar is `aria-hidden`: the number stays in the cell as text. Both
  validators refuse a bar on a column holding text or past the last column (shared cases). The Discord
  text card ignores the hint. A list of positions, not a per-column object, because tables are positional.
  A column object may instead carry `bar: {scale: [min, max], track, own_column}`: a fixed scale (a value is
  drawn against its own side's end, the zero line off the middle when the scale is lopsided), a grey track
  (`--track`) and a bar column of its own after the number. `show_source_foot: false` drops the card foot
  (a hidden svg then stays as the capture mark); the overview still shows source and `as_of`.
- **Presentation hints** (schema 1, all optional, `lib/externalFormat.ts` and the `Section` component).
  A column is a string or `{label, align, format, tone, width, stack}`; a cell a scalar or
  `{value, text, tone, suffix, marker}`; a row an array or `{cells, emphasis}`; a section may add
  `numbered`, `key` and `footnote`. Formats (`money`, `compact`, `int`, `percent`, `signed`, `decimal`)
  are pure functions checked against the `formats` table in `external_report_cases.json`, which the
  Python module (what the Discord card prints) is checked against too. `percent` takes the percent number
  itself (56 is 56%). Tones are theme tokens (`external-tone-*`: `--pos`, `--neg`, `--warn`,
  `--text-muted`, `--text`), so they read in light and dark. A stacked column draws one bar per row from
  an array of numbers, its segment labels as a legend in the header; segment colours are `--stack-1..6`,
  defined on `.external-card` from existing tokens (the console has no separate categorical palette). A
  segment that has any value is kept at least 3 px wide (`stackWidths`). Every figure stays in its cell
  as text and the bars are `aria-hidden` or carry the shares as a title. Rows are a step tighter than a
  full-page table, as the old flow tables were. Invalid hints make the file invalid with the place named.
- **An invalid file** gets a tab listing every reason and none of its content; a deep link to an id with
  no file reads "No such report" with a link back, not an error.
- **Validation** is the reader's: each file is judged against `packages/core/cherrypick/core/schemas/
  external_report.schema.json`, the same file `cherrypick.core.external_report` reads, by a small
  interpreter of the subset that file uses (`readers/externalReports.ts`). `external_report_cases.json`
  (in core's test fixtures) is run by both languages' tests so the two validators cannot drift. Files
  over 512 KiB are refused unparsed. The folder is `data/external/` (`config.paths.externalDir`).
  Read-only; the console never runs a script.

**Charts** (`/charts`, `lightbox/manifests/ChartsLightbox.tsx`) — three tabs: two charts and
the setups watchlist:

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
    Levels are `?levels=ours|all|off`: our own swing levels (the default), those and our grid's high
    and low ("With grid"), or none; never the setup lines. Each is drawn from its own date and kept
    out of the autoscale. **Nothing of an outside source's is shown**: a frozen capture beside today's bars reads as
    current. A saved `?levels=vendor` link falls back to the default (`web/test/chartPage.test.tsx`). Entry and exit arrows are both amber: an arrow marks an event, and green
    or red would read as a gain or a loss.
- `/charts/setups` is the setups watchlist (`pages/Setups/SetupsPage.tsx`, `GET /api/technicals/setups`
  over `charts/setups-index.json`): recent entries and exits, or open positions, long and short,
  each symbol linking to its chart with the setup and side selected; nothing on it is vendor data. Gated with the technicals tab. The page filters and sorts
  and splits a position into entry and exit lines; every value is the package's. "Move" is close to
  close and never called P&L, and nothing ranks a signal's quality. "Tested edge" swaps in the
  study's confirmed rule's rows (`tested`), which overlap the setup rows and are never shown with
  them. The page is cut to `optionsTradable` names by default whenever the file carries a label
  (`names=all` is the way out). With no label it cuts nothing, because an unknown flag would empty
  it.

**Screeners** (`/screeners`; `lightbox/manifests/ScreenersLightbox.tsx`) — eight tabs in two
groups, `screens` and `income lists`. Our own screens first: the earnings calendar, its forward record and the signal
board. Then the income lists: short puts, covered calls and credit spreads, one tab each. 
- **Time spreads** (`TimeSpreadsTab.tsx`; manage's README, "Time spreads"): the day's
  calendars and diagonals on the names whose front week is priced rich to the back month, unsized,
  from `scripts/fetch_time_spreads.py`'s file through `readers/timeSpreads.ts`
  (`GET /api/screeners/time-spreads[?day=]`). Broker reads only, so no Dolt card. **No tested edge**:
  rows are in the scan's order, by the front/back IV ratio, labelled an untested hypothesis (item 54),
  and every figure is modelled. Live, portfolio shows the same rows sized to the account
  (`TimeSpreadsCard.tsx`, manage's `book.time_spreads`), under the income card.
- **The income lists are OURS only**: no outside source is shown and there is no source toggle. All three tabs read `technicals income-lists`' file through
  `readers/incomeLists.ts` (`GET /api/screeners/own/:list[?day=]`); the reader and page decide nothing.
- **Short puts and covered calls** (`OwnListTab.tsx`): our options-tradable universe, the
  strike nearest 16 delta (put on the cycle monthly, call on the monthly 30-45 days out), at the
  working price. **The rows are in symbol order and are never ranked by yield**, and the standing "No
  tested edge" note above the table is never removed while the list shows: the rule is the least-bad
  cell of two studies that found no edge (`cherrypick.core.incomerules`). Defaults hide a thin option
  and a trade with earnings inside it, each with its escape; an unknown or past date stays shown,
  marked.
- **Credit spreads** (`CreditSpreadTab.tsx`, `readCreditSpreadList`): the chart setups'
  fresh entries as spreads, built by `technicals creditspreads.py`. A long setup is a put spread, its
  mirror a call spread, on the 30-45 day monthly. The short is nearest 0.40 delta and the pick is the
  widest long giving 30-40% of the width. **"Not yet tested"** heads the table for as long as the
  builder's rule says so (item 17). A row with no width in the band stays, marked, and is hidden only by
  the "With a 30-40% width" filter, never by "Liquid trade". Each row opens its width table (1/2/3/5
  strikes, 0.25/0.5/0.75 of the move). Credits are per share (`cr`) and max loss is per contract. Rows
  are by symbol. A session built before the list, or without the history store, says so (`built`
  false) rather than showing an empty table. Behind `NeedsDolt` (`SCREENERS_NEED_DOLT`): the setups
  come from the history store.
- **Vs fair on every income-list row** (income-lists version 3, `FairCell` in
  `OwnListTab.tsx`, shared with the credit-spread tab): the price against the session's DCF estimate
  from the fair-value file, the range and the builder's flags in the cell's title (a `†` marks a
  flagged row). `n/a` for a fund, a bank or a REIT; `—` with the reason for a name the model has no
  value for, or a list built before the fields. **A model estimate, tested 2018–26 with no edge**, in the
  header's title; the column never orders the list.
- **Fair value** (`ValuationTab.tsx`, `readers/valuation.ts`,
  `GET /api/screeners/valuation[?day=]`): the fair-value file `technicals valuation` writes each
  session (`cherrypick.core.valuation` on Dolt's annual statements, a regression beta and the
  consensus sales growth from the history store): per name its state (valued, not applicable, no
  statements, refused -- with the reason), price, fair value, the grid range, the gap, WACC, beta
  (the broker's beside it where it disagrees), year-one growth, margin, the terminal value's share of
  EV, the implied EV/EBITDA, the reverse DCF (the margin and the growth the price needs, each beside the company's best of five years, and the builder's reach: below value, within history, beyond history) and the builder's flags. Views: valued (default), pmcc (its configured
  symbols, badged from pmcc's own config through `readers/pmcc.ts`, its funds not applicable) and
  all. **"Tested 2018–26: no edge"** heads the table and is never removed; rows are by symbol, and a column
  sort is the reader's own click. Behind `NeedsDolt`: the statements come from the local Dolt clone.

  **The earnings tab** (`pages/Screeners/EarningsCalendarTab.tsx`,
  `readers/earningsCalendar.ts`, `GET /api/screeners/earnings[?day=]`) is the earnings calendar,
  laid out like the vendor's: grouped by day, with its columns and filters.
  - **Data:** the reader renames `technicals earnings-calendar`'s per-session file and decides
    nothing.
  - **Defaults:** strong reads, very liquid, optionable, US, the next 7 days, each with its escape.
  - **Windows:** they count from today (New York) on the newest calendar, and from the calendar's own
    session on an older one.
  - **Grouped rows keep their order**, so the table does not sort.
  - **Kind has its own column** (credit or debit, each a neutral chip), ahead of the spread, whose
    cells stay on one line each.
  - **One read, ours:** direction and strength are ours (Bearing), the tooltip naming the close they
    are as of; IV rank is the broker's. The vendor's values stay in the file and the reader never
    reads them, so nothing here shows or filters on them (a deliberate choice).
  - **A standing note** says the suggested spread has no edge in history. It is never removed while
    the column shows.
  - **The spread as read:** the reader joins the latest `screener-greeks/earnings-legs/` reading
    taken OF the calendar shown (`calendar_day`, as the income tabs match `list_date`).
    `fetch_screener_greeks.py measure` snaps the targets to listed strikes, quotes them at 11:30 and
    14:30 ET, and judges them by the income tabs' leg bars. The cell then shows the listed strikes,
    the price, its share of the width, the max loss, and liquid or thin. A "Liquid trade" filter
    (default on) hides a thin spread; an unread one stays, with its targets.

  **The earnings record tab** (`EarningsRecordTab.tsx`, `readers/earningsRecord.ts`,
  `GET /api/screeners/earnings-record`) is the calendar's forward record. It reads
  `technicals earnings-record`'s `record.json` and decides nothing.
  - **Per announcement:** the move against the expected move shown the session before, and whether a
    strong read called it.
  - **The spread's money**, in the suite's layout: signed entry and exit at mid, gross, fees and
    slippage as their own columns, and net, with net at day 2 beside it.
  - **Order:** a journal, newest first; it does not sort.

  **The signal board tab** (`SignalBoardTab.tsx`, `readers/signalBoard.ts`,
  `GET /api/screeners/board[?day=]`) reads `technicals board`'s per-session file and decides nothing:
  bullish then bearish lists in the file's order (count, then symbol), each name's side tags only, a
  tie never listed. A minimum count (2 by default; 1 and 3 one click away), sector and symbol
  filters. Approximate methods carry "≈"; the note names what is not counted and that the board is
  not a tested signal.

  **The skate tab** (`SkateTab.tsx`, `skateRows.ts`, `readers/skate.ts`,
  `GET /api/screeners/skate[?day=]`; technicals' reference, "The skate screen") reads the newest scheduled run of
  `fetch_screener_greeks.py skate` (`market-report/screener-greeks/skate/<session>/<HHMM>.json`;
  a `*.trial.json` is never read) and decides nothing: rates, gap, yields, the hard and soft verdicts
  and each band's best expiry are `technicals.skate`'s. The reader keeps, per name-side and band,
  the file's best and the pick leg at that best's own expiry and strike (~0.3 MB of a ~4.5 MB run).
  - **Hard filters hide, soft thresholds mark.** A row is the band's pick on its best expiry, shown
    only when the leg's `hard` is true; `soft.all` is a pass/miss chip, and "passing only" is the
    one way it narrows. Default order is history minus implied, widest first.
  - **The band selector** (fixed / by expiry) drives the rows and the ranking; the other band's
    best strike and premium a day sit in a quieter column ("same" where both pick one strike).
  - **Stated, never empty:** no run, an unreadable run, an incomplete run and the run's `problem`
    each say so. The note says the gap is a history estimate until the forward record (step 4).
  - "vs spot" is the strike against the quote's spot, a display figure: the band is set on the
    band day's close, so a name that has moved can have its pick at or through the money, and those
    top the gap sort.
  - **The forward record** (`SkateRecord.tsx`, `readers/skateRecord.ts`,
    `GET /api/screeners/skate/record`) sits below the table and passes through
    `data/technicals/skate/record.json`. Its own route, not a field on the run payload: it is one
    file for the screen, not a session's. `decision_grade` and each band's `passes` are the file's,
    never worked out from the z scores (`skateRecord.test.tsx` fails if they are); a non-boolean
    `passes` is "no verdict stated". No pass tone before decision-grade, the test stated in words
    from the file's `test`, and a missing or unreadable file said plainly, never zeros. `record.db`'s
    rows are not read.
**Compare's risk tab** (`/compare/risk`, `pages/Compare/RiskTab.tsx` over
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
move to the async spawner too. This is the arms overview: it lives here, beside the paired comparison, rather
than as a second page that would repeat Compare's arm list.

**Compare's portfolio tab** (`/compare/portfolio`, `pages/Compare/PortfolioTab.tsx` over
`GET /api/portfolio` → `readers/portfolio.ts` → `python -m cherrypick.core.portfolio read`) — one
chosen arm per module held together, a bridge.
- **Resolving arms:** each arm resolves to its module's ledger, schema and era as the calendar's
  do, so its nets are the calendar's cells. An arm the console cannot resolve is named beside
  core's own failures.
- **The picker:** its default (`control`, else a module's first non-advised arm) is a starting point,
  not a judgement. pmcc's `control` is retired, for one.
- **Labelling:** every figure shows its periods. Thin rows are dimmed and carry no tone, and
  equal-risk scale factors are shown per arm.

**Compare's calendar tab** (`/compare/calendar`, `pages/Compare/CalendarTab.tsx` over
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
do not, and that is not expected. The calendar and risk tabs follow the page's paper/live toggle; the arms
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

- Field names stay the bridge's snake_case, verbatim (`shared/src/types/compare.ts`). A schema other than
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

**Backtest** (`/backtest`, `lightbox/manifests/BacktestLightbox.tsx`,
`pages/Backtest/BacktestPage.tsx`) — `packages/backtest`'s replays over licensed vendor history, the
ONLY place those results appear, and fail closed (above). `GET /api/backtest/overview` and
`/api/backtest/runs/:id` (`routes/backtest.ts` → `services/backtestBridge.ts` → `python -m
cherrypick.backtest read overview|run --id`, async, memoised 30 s) each 404 with the unknown-API body
unless the features answer says `backtest: true`; a run id is checked against the runner's shape
(`BACKTEST_RUN_ID` in `@console/shared`) and anything else is a 400 that spawns nothing. A bridge,
not a mirror: the unlock verdict, every check, the paired interval and `no_exposure` are the module's.
Tabs (`studies` first, and the landing tab, then `queue`, `runs`, `data`;
`validation` is part of `data`, and `slices` is on Compare; the old URLs redirect): `studies` (`pages/Backtest/StudiesTab.tsx`, `GET /api/backtest/studies` →
`services/studiesBridge.ts` → `python -m cherrypick.cli hypotheses-board`, behind the same gate
because the verdicts are read from licensed result files): what we
tested and what we found. A mosaic (`pages/Backtest/StudyCards.tsx`): one tile per
hypothesis-register item, in progress / declared / finished / observations order, the study with a
task running now as a large tile first. A finished tile's big word is the register's status, with
"2 pass · 5 fail" when the newest result's arms disagree; an in-progress tile shows its step and one
segment per queue step; a declared one what it expects; each carries stale (a replay correction since
the result), its window and provisional, all read from the board. A tile whose newest result names a
runner run (a study script's own label is passed over) also draws that run in brief,
from one `GET /api/backtest/summaries?ids=` read for the board (`read summaries`): each arm's
running net as a thin line, the arm with the highest mean drawn over the rest, and that arm's
"$xx.xx / session" with "best of N" (control left out of both while it trades on another scale,
and never the arm named while another arm has a mean). The board's words never wait on it. Group
chips filter (`?group=`); **This week** (`?group=week`, `pages/Backtest/StudyWeek.tsx`,
`GET /api/backtest/studies/digest?since=` → `services/studyReportBridge.ts` → `cherrypick.cli
study-report --since`) shows the studies with a result or a finished step since Monday, newest
first, under their verdict counts and the ones that gave no reading; the counts are the
orchestrator's. A tile opens a lightbox (`?study=`, so back closes it; Esc,
← → and a swipe step through the tiles in layout order) with five tabs. **report** (
`pages/Backtest/StudyReport.tsx`, `GET /api/backtest/studies/:study/report` → `study-report
--study`, fetched on open): what the figures say, what to do next (each suggestion with the figure
that prompted it), the gates the study uses in plain words, every arm charted by window on the
measure picked (gain over baseline first, then gain where both entered, net per session, net per
trade, return on buying power, entries a session) with its t-statistic, and the arm table. Every
sentence and suggestion is the orchestrator's (`studyreport.py`, fixed rules, never a model); the
tab draws and prints. The slug and date are checked before anything spawns. The others: overview (the decision, a
stale note, and the run's figures, fetched on open from the newest result file that names a run:
each arm's net a session, sessions, trades a session and verdict, and each arm's running net, legend
entries hiding a line so one wide arm cannot flatten the rest; control starts hidden, with a note, when
it trades more than ten times as often a session as the other arms' median, as MEIC's sampling arm
does, hundreds of condors a session against a one-condor rule), declaration (measure, expects, sample, pass test, why,
rivals, the plan section on GitHub), grid (the study's grid bars) and queue (each result file's
per-arm verdicts, run and write-up links, and the study's queue rows, each opening to its arguments,
result and log). Phones get two columns and a full-screen lightbox. A study may leave Markdown beside a result file (`results.json` -> `results.md`, its own
figures, written under the home and never committed); the lightbox's queue tab links to it, opening full width
on the tab (`?study=&notes=<result file>`), rendered as elements
by `lib/miniMarkdown.tsx`, never as HTML. A result file is shown by its name only. `?study=` opens its
lightbox. The other tabs point back (`pages/Backtest/studyIndex.ts`, one shared fetch): a
queue row carries its study's verdict as a chip linking to the board, the runs list has a `study`
column, a run report opens with an "in plain English" card (what ran, the study it answers and
that study's verdict and decision, control's net, each arm against control and where its interval
sits against zero, all the report's figures worded), and the `provisional` chip links to
data), `data` (as cards, `pages/Backtest/DataCards.tsx`: "is history trusted?" first and
large, the answer the module's unlock verdicts give, then one row per validation with its cadence,
sessions compared, dates, what stands in the way (the checks outside tolerance, or the session
count) and a dot per check from `vendor_within_tolerance`; the licence (a line while it is active
with no end date, its clock once it ends) and the stores' totals beside it; then one tile per
store, from the inventory (else the replay stores' fetch record), with what it holds, a bar of the
years its largest dated table covers, size, the largest table's rows and the sessions its fetch
refused, missing and "count pending" marked (the read never counts), filters on `?stores=`. A
validation opens a lightbox (`?item=validation:<file>`) with the module's tables as tabs: checks,
replay measures, quotes and GEX profile per session; a store (`?item=store:<file>`) opens what it
holds, its tables, the code that rebuilds it and the sessions refused, with why), `runs` (as cards, `pages/Backtest/RunsCards.tsx`: one tile per replay, newest first and
the newest large, module chips on `?module=`; a tile names the study whose result file names the run
and that study's verdict, else says none does. A tile opens a lightbox on `?run=` with five tabs:
summary (the "in plain English" card, then each arm's net a session toned by sign, trades a session,
its interval against control drawn untoned around zero, and the study's per-arm verdict; when
control trades more than ten times as often as the arms, as MEIC's sampling arm does, a note says
so and that the study judges against its own base arm), equity (each arm's running net), health and
calendar (one arm at a time, the run cards' own checks and month grid), and settings. `?run=&full=1`
reads one report: each arm's summary, the pairing with control, trials, fill model, code commit, data
fingerprint, refused sessions, and the run cards, `pages/Backtest/RunCards.tsx`: per arm
a one-line settings strip (cadence, fill model, and the params that differ from
control, or "settings not recorded" for an older provisional run), the health checks (
flagged ones on top, each a sentence against its bar), the
account, equity and drawdown, edge trend, deepest drawdowns, month-by-year grid, trades, premium
(fee drag, capture rate) and how trades ended, all the module's `cards.py`. `?capital=` (whole
dollars, `BACKTEST_CAPITAL`, else a 400 that spawns nothing) picks one account for every arm; absent,
each arm reads against its own peak session risk. The account changes percentages only, never a
dollar), `queue` (`pages/Backtest/QueueTab.tsx`,
`GET /api/backtest/queue` → `services/researchQueueBridge.ts` → `python -m cherrypick.cli queue-list`,
behind the same gate): the research tasks the supervisor runs, read-only. Each row's state and why a
queued row waits are the orchestrator's words, shown verbatim, and a file argument is shown by its name
only. What the queue is doing, not what a study found, as cards: the running task as a
large tile (its overall share done, time left, and a meter per phase from its progress file), runs
started by hand still going beside it, then up next, numbered in the runner's order (ready first,
oldest first, split on the orchestrator's `waits`; the page works out no run order), each with the
orchestrator's reason for waiting verbatim, the first ready row marked as starting next and the rest
as in line; then rows that failed or were stopped in the last 7 days, then runs started by hand in
the last 30. A tile names its study by register title (its slug as a tag; "not in the register" only
once the board has loaded) and opens a lightbox (`?task=`, by hand `?task=hand:<file>`): overview
(queued, started, finished, the wait, the meters, the study with its verdict chip), arguments (a
file by its name only), result (exit, counts, input, store, output marked missing, log, each by
name) and the log's tail. A study's finished rows are on its card in the studies tab. The lightbox is
`pages/Backtest/CardLightbox.tsx`, shared by every Backtest tab. Rows are added on the command
line (`run.py queue-add`), never from here. Every tab opens with the licensed-data note. Run money is net of fees,
signed, whole-position dollars; fees are shown as a cost, already out of net, never subtracted again.
Detail view only (`SIMPLE_HIDDEN_PAGES`); shortcut `g b`.

**The arm picker** (flies, MEIC; the header's `ArmSelect`) narrows what a tab already read; it never
changes a read. A tab whose content is one block per arm follows it: session, forest, timeline,
attempts, decisions, positions, history, and performance, analysis, costs and regime
cuts. Advised books stay with their arm (`lib/armTag.ts`: `<arm>@<experiment>` is that arm). On
performance the cumulative chart keeps `control` beside the picked arm as its reference; on regime
cuts the document-level chance baseline still counts the whole cut, and says so. A tab whose point
is a comparison across arms (gates vs control, live vs paper, selector) or a module-wide view
(summary, advisor, agent) deliberately ignores it. The picked arm is page state, kept across tabs
because the frame stays mounted; it is not in the URL.

**MEIC's `arms` tab** (`pages/Meic/MeicArmCards.tsx`) is a card per arm in the studies
mosaic's tiles, from two reads the page already makes (the forest and the attempts, under the page's
scope): a large tile with every arm's payoff at expiry drawn together and their realised net (it
opens `forest`), then each arm's realised net, its payoff in small, entered / stopped / open, and its
checks: fills of attempts and the refusal that turned most away, with its share. Arms sort by realised
net; a losing arm's tile has a red edge. Control on another scale (`controlScaleOf`, the Backtest
tiles' rule) is dashed, sorts last, says "not ranked", and is left out of the large tile. A tile opens
the shared lightbox (`?arm=`): *forest* (the forest card narrowed to the arm, its attempt timeline
beneath), *attempts* (rail, timeline and strike occupancy for the arm) and *trades* (its positions).
It sits after `today` and replaces `forest` in the simple view; `forest` and `attempts` stay as tabs.

**A summary slide is a module's own.** MEIC's summary slide is the pattern — one-read payload
computed server-side to the cent, presentation arithmetic only in the component, money in the
suite's one layout, tone from a number's sign — and each module's summary carries its own KPIs in
its own vocabulary: entry-time and weekday bands are MEIC's, capture and capital-at-risk mean
nothing to a legged flies book, cash deployed is contango's alone. There is deliberately no
central summary page: a shared one was tried under Reports and withdrawn — the modules' KPIs are not the same book, and a shared surface kept flattening
them to their common denominator or growing a registry of per-module exceptions.
What is module-local is the content, not the mechanism: the simple/detail view is shared, and
each module decides what its simple view lists.

**A summary follows its page header.** MEIC's slide always took the page's scope and session; the
generic `/api/summary/:module` summaries take it the same way, so the header's arm /
symbol / era / date controls act on the summary tab. The manifest passes `scope`
(`{arm, symbol, era}`) and `day`/`onDay` into `ModuleSummary`, the route forwards `arm`/`symbol`/
`era` beside `date`/`range`/`mode`, and `SummaryQuery` carries them. Each reader applies them
through one shared `scopeTrades` post-fetch filter over its own `SummaryTrade[]` (so every module
narrows the same way); a single-symbol book with no `symbol` on its rows is not filtered on one it
never had. Era is read only by a reader with an era model (flies, via `eraClause`); a module whose
header has no scope control passes none and keeps its own `?day=` arrows. Scope narrows the whole
summary, the by-arm card included — deliberately unlike MEIC's comparison card, whose by-arm table
never narrows.

**A summary says how sure it is, and a win rate never stands alone** (`docs/trading-book-review-plan.md (tag archive/docs-2026-11)`
B2). `shapeOf` gives the hero's shape n, mean, sample sd, a Student's t 95% interval of net per trade
(t table under 30 trades, 1.96 from 30), t, the win rate's Wilson interval, and a skew flag both ways
(adjusted skewness ≤ −1 left, ≥ 1 right). The net card's per-trade face shows the worst trade, the
interval and t, an early-estimate note under 30 trades, and the skew note. The win-rate card always
carries net per trade beside the rate and takes its tone from net per trade's sign, never from the
rate passing 50% — an 85% premium seller that loses money reads red. Null where it cannot be said
(one trade has no sd; no spread has no interval). Overlap is flagged (`overlapOf`): when sessions
closed several trades, or (where a reader passes `entry`: bwb, curve) trades were held alongside another
in their arm, the per-trade face says the interval reads narrower than it is. A reader without an entry
session reports the same-session count only.
The per-trade face also states mean R (B3) with its interval, where the module passes `heroOf` a
risk: net over the defined max loss at entry -- bwb's worst case from its widths, curve's
`entry_max_loss`, earnings' `capital_at_risk`, calendars' debit. contango (a fund) and flies (no
per-trade risk) have none, and say nothing; pmcc's long cost is not its max loss, so it is left out.

**The scope is SQL too, not only the settled roll-up.** `scopeTrades` narrows the rows a reader
sums, but a reader also counts things straight from the ledger — an open-position count, the
session list behind the day arrows, pmcc's shorts/rolls/exposure activity. Those go through the
shared `scopeClauses` (or a subquery built from its `conditions`), or the page shows two scopes at
once and `ModuleSummary`'s `trades === 0 && open === 0` empty state stops firing, drawing a hero of
zeros. The day arrows step through the sessions the scoped trades actually have, so an arm-scoped
summary never lands on a day that arm did not trade. Two things to know reading a scoped flies
summary: the header's own date list comes from days that have any position, while the summary's
latest day is the latest that **settled**, so with no date picked the tab can sit a session off the
others; and the header's symbol filter only bites once the era is widened to ALL, because the
default SPX era already pins one symbol. bwb's rows carry no symbol, so it is never filtered on one.

**Module advisor slides.** `readers/advisor.ts`'s `readAdvisorModule` serves
`/api/advisor/module/:module`: active experiments with progress against length and stall budget, the
session strip from the advisor's `enactment` table (read, never re-derived), the paired comparison as
of the last evening pass, tomorrow's artifact, the queue and the last concluded.
`components/advisor/AdvisorSlide.tsx` renders it in all seven module manifests that carry one (flies, meic, bwb, earnings, curve, pmcc, calendars); the Advisor page's
experiments tab opens with the cross-module roll-up. Module slides are read-only; kill/dismiss stay on
the Advisor page only.

**Live** (`/live`, `lightbox/manifests/LiveLightbox.tsx`, `pages/Live/`) — seven tabs: `today` (where
`/live` lands), the account-wide `positions`, `portfolio`, `wheel` and `pmcc`, and one per live loop with a page of its
own (`flies`, `meic`; each hidden when that module's live switch is off and the loop is idle, see *What is off*). The
account-wide set is `LIVE_ACCOUNT_WIDE` in `lib/visibility.ts`, the one copy: those tabs are always shown and wear the
suite badge. The simple view lists `today`, the four account views and `meic` (flies is retired and off by default). The execution record
(B10, below) is a card on its arm's own tab (MEIC's, the
only live ledger it reads), and `/live/execution` lands on `meic`. `test/manifestSlides.test.ts` holds every manifest's tabs equal to its `navGroups.ts` declaration. The
`flies` tab is the pilot's day over `GET /api/live/flies` (`routes/live.ts` → `readers/fliesLive.ts`), composing existing readers
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
`readers/meicLive.ts`) is MEIC's live loop from files alone: the arm record (armed =
dated today; past `live.disarm_time` is said), the halt flag, the orphan / hold / broker-health files
beside `meic_trades.db` (read as `core.execution` reads them: an unreadable hold file is held), open
ICs (`live_loop.overdue_settlement`'s status list; `pending` is a working order with no entry cash; a
side with a recorded close cost is closed) and resolved history (`stopped`/`expired`/`force_closed`)
through `meicTradeCash` and the MEIC page's own history columns, less slippage (a live fill is the
broker's). Cancelled rows are in neither table. No curve or feed: the live loop writes no marks and
no `loop_log`, so there is no loop pill either. Pinned by `server/test/meic-live-reader.test.ts`.
**Live vs paper** (`pages/Live/MeicLiveGap.tsx` over `GET /api/live/meic/gap` →
`readers/meicLiveGap.ts`) follows the history: each live condor beside the paper arm `live-shadow`
(`follows_live`, so it trades what the `live` block trades) from the first live session on, paired
per session with the same strikes first, then entry order. The gap is live net less paper net, + in
live's favour; paired totals are the sums of their rows, and paper condors on days live sat out are
counted on their own line. Pinned by `server/test/meic-live-gap.test.ts`. The **execution by week** card
(B10, below) closes the tab. Its gap is against the same paper arm but over every session live was
working, so it also counts the entries live missed; each card says which sessions it counts.

**The Live page's `today` tab** (`pages/Live/LiveTodayPage.tsx` over `GET /api/live/today` →
`readers/liveToday.ts`) opens `/live`. It is a mosaic: a large card
(the suite's net and its parts, armed, the halt flag, needs a person, and what to do today; a click on
it opens Live → positions) and a card per live loop with the simple view's fields (arm, state, open,
worst case, mark, net, loss breaker, working when any is). A loop's card opens the shared lightbox
(`?loop=`): *orders*, the loop's positions today as chains from its own live read
(`pages/Live/LiveTodayOrders.tsx`: MEIC's `/api/live/meic` open rows and today's resolved rows; bwb's
live payload and its history for the session; entry, a stopped side or the add-on, then how it ended
or "now"), and *detail*, every field the old table's detail view carried plus what on the loop needs a
person. A losing loop's card has a red edge, a tripped breaker or failed read a red border. The hand
trades are a card of their own beside the loops (never in the total) with their orders card below; a
retired loop folds to one line. Header badge: `2 armed · HALTED`. It **composes** the
per-loop readers (`fliesLive`/`flies`, `readMeicLive`, `readBwb`/`readBwbHistory` in live mode,
`liveBooks`' bwb read for absent-vs-failed) and adds only each loop's own `daily_loss_tripped` and
flies' `overdue_settlement` queries, mirrored and named. Attention: orphaned orders, held
submissions, a tripped breaker, overdue settlement, failing broker contact, an unreadable ledger;
"no limit" is never shown as a clear breaker. The suite total sums **only today's realised net**
(resolved today, gross − fees − settlement, broker fills): any loop's failed read makes it n/r, and
the worst case and the mark are each loop's own measure, shown per loop, never added. `/live/today`
is this tab now, not an alias for flies. Pinned by `server/test/live-today.test.ts`.
**The hand row** (`readers/liveHand.ts`, `pages/Live/HandToday.tsx`) sits under the loops:
today's 0DTE trades placed by hand on the names manage leaves to the loops, which no loop manages,
stops, force-closes or settles. It reads TODAY's manage evaluation's `zero_dte` block
(`manage/zerodte.py`) and splits, reconciles and sums nothing: a fill is a loop's when its order id is
one a loop has published for its orphan sweep, and a held leg today's fills cannot explain makes the
mark and net null rather than part-counted. Its net is stated beside the suite total (the tile's hover
and the footnote) and **never added to it**; the card below lists each hand order and the legs still
held. Fills are as fresh as manage's last snapshot (every 30 minutes), marks as its last re-mark.
On the card, red is kept for what puts today's figures in doubt (a leg that does not reconcile, a fill
after the loops' last publish). manage's caveats, and the earlier session's expired legs the broker
still lists until it processes the expiry (`expired_held`), are muted: they appear every morning after
a 0DTE session, and red there would teach the eye to pass over red. Pinned by `server/test/live-hand.test.ts` and `web/test/liveTodayPage.test.tsx`.

**The Live page's `positions` and `portfolio` tabs** read packages/manage's evaluation
(`readers/manage.ts`); `/live/manage` lands on
`positions`. `positions` is a card per trade (`pages/Live/PositionCards.tsx`; action needed and
holding are one card each), titled by manage's stable trade ID (`XYZ-260401-1`, kept through
rolls; the dialog marks a provisional ID and any ID a join merged into it): the trade's action, its P&L net of fees, capture (manage's gross figure, the one take-profit judges, so labelled before fees) and short DTE,
and its broker order chain down the card (open, rolls, then "now, close at mid" with what the chain
took in), from manage's own `chain`. A trade with no reconciled chain shows its opening alone and
says so. A losing trade is red, edge included. Chips filter (all, needs action, rolled) and sort by
P&L net either way (`?show=`, `?pnl=asc`), a trade with no net last; a card opens the shared
lightbox (`?trade=`) with the whole chain, every leg, each order's fees and the chain's running total
(the sum of its own signed cash, nothing re-priced), and the evaluation's detail. The ruleset follows;
`portfolio` is
the account as one book. Both open with the same evaluation
header (its caveats qualify both) and the same six-tile account strip. **The portfolio
tab is laid out by question** (`pages/Live/PortfolioLayout.tsx`): what needs my attention (the
guidance), am I paid enough (an income card, theta's dial beside the premium pace's target, and where
the theta comes from), which way am I leaning (a direction card, delta's dial with what 1% is worth and,
while delta is outside its band, the sized hedge's headline, and where the delta comes from), what if
it moves (the stress tiles with break-even and the small risk graph, then the risk matrix), do I have
room (a buying-power dial with your limits stacked under it, and what expires when), and what could I
add (one card, a tab each for the income trades and the time spreads; it opens on income, whose premium floor starts at $50 until one is set). Its sort, premium floor and ex-earnings switch sit behind a filter & sort button (``components/FilterSort.tsx``) that counts what is not at its default, with a line under the head saying what is on; the positions board's sort moved behind the same button (``?sort=`` in the URL). Sorts are on size, timing and fit, never credit or yield. Adding trades and the positions board also switch between cards and rows (``components/ViewSwitch.tsx``, two icons, remembered per card): a row is one line a trade drawn from the same figures as its card, and opens that card in place. Cards in a row share its height
and fold from two a row to one by the width the tab has (container queries). **What explains or
audits a card opens from it as a lightbox** (`pages/Live/PortfolioBox.tsx`, the Backtest page's
`CardLightbox`, wide; `?pf=<id>` in the URL, ← → step through them): the premium pace by month, the
bands over time (all three strips or one), every name's delta or theta, the check against the
broker's model (its button carries the delta gap), the sized hedges, the full risk graph, your limits
and the ruleset, and each expiry's trades as position cards that open on the positions tab. Each card's explanation (where its figures come from, how they are made, what was left out, the caps) sits behind a ? beside its title (`components/CardHelp.tsx`, the card's `help`): a small lightbox, a sheet on a phone. A caveat that must not be missed keeps an amber flag beside the ? (no published evidence on the guidance and the matrix's ideas, no tested edge on adding trades); what changes what you would do (an error, a missing history row, the partial-reading line) stays on the card. The rest of the suite follows the same pattern as each page is next worked on. A trade not
held yet (a hedge, an income trade or a time spread that fits) is a position-style card
(`pages/Live/TradeCard.tsx`): legs as the order that would open it, the cash, the loss the per-trade
cap is applied to as its bar, and what it does to the book. The check against the
broker's model is manage's `broker_check`, our delta and theta beside the same sums on the broker's own
greeks with the gap, untoned because neither side is the right answer; legs expiring today are
manage's own `expiring` line beneath it, never in its totals. **Every band and every in/out flag is
the reading's `bands`**: the dial draws manage's band and takes amber only from its `inside` flag,
never from where the needle sits (`web/test/managePortfolioCards.test.tsx` shows a needle inside its
band drawn as outside when the flag says so). Long and short delta carry no tone. An evaluation
written before the reading has `portfolio: null` and draws no cards. **Premium pace** (
`pages/Live/PremiumPace.tsx`, the income card's lightbox): manage's `premium` (theta against its target on the
theta band's scale, this week, six months of collected beside kept with a foot row that adds them up,
and what manage left out). It draws nothing for an evaluation written before it existed or for an
account that is not the broker tool's designated one. **Bands over time** (
`pages/Live/BandsHistory.tsx`, `GET /api/manage/bands` → `readManageBandsHistory`) is the "over time"
lightbox on the income, direction and room cards: every stored evaluation's delta, theta and buying-power reading against that session's
own band (the delta band moves with theta, the buying-power cap with the VIX), amber from manage's
flag only; one point per session, its last run. Both tabs' header shows manage's
`pre_open` as a red line (a snapshot taken before 09:30 ET reads yesterday's close and leaves today's
expiring legs unpriced). The contribution cards sum the names past their five into one "others"
row, so the rows add up to the book; the lightbox lists every name. **Live marks** (`readers/manageLive.ts`,
`/api/manage/live`, `pages/Live/LiveMarks.tsx`) re-price the NEWEST evaluation's legs from the stream
cache every five seconds: per-trade live P&L and capture on the evaluation's own basis, fees and
capture denominator, and an "at TP" / "at stop" chip against the evaluation's own levels (the
payload carries the book's streamed delta and theta). Display only: no verdict is
re-judged and no in/out-of-band flag is drawn for a live figure (that stays the evaluation's). A trade
or book figure with any quote over 120 s old reads a dash, never part-marked. A past session shows
none. Under the status line each account's day P&L (each leg's mark less the broker's
previous close, which manage now carries per leg) and an estimated net liq (the evaluation's, moved
by the evaluated legs' marks; cash and the left-out 0DTE legs are the snapshot's), with the legs the
stream is not carrying named in red: either figure is a dash while one is. While manage's evaluation
is `early` (marks from 09:30-10:00 ET) the page says so and every action chip reads "held until
10:00", the action in its title (`VerdictsHeld`). `ScenarioCards.tsx` adds a
buying-power dial (the cap is manage's, set by VIX), the expiry ladder with the earnings exposure,
the risk graph and the risk matrix. Every figure is manage's repricing; the matrix states which IV
weighting manage used, and a card manage wrote no reading for is not drawn. The matrix has a horizon
toggle (now, +7, +21 days: manage's `horizons`; it opens on +7, or the first when there is no 7-day
horizon), a last row moving IV as history has (its cell titles
say how many windows and what VIX did; when manage has no history the caption says why), and, inside the
same card, manage's ideas for the matrix: header, subheader, explanation, and what the change does to
the worst cell, the two end columns and theta, green where better than now and red where worse. `IncomeCard.tsx` lists
manage's sized income trades in the package's own ranking (never re-sorted here), money in the suite's
layout. Risk is the figure the per-trade cap was applied to: a short put's stress loss, never its
strike. The card always says the prices are a day old and to re-quote before sending. Its "waiting
for room" list is manage's `waiting` list, drawn as trade cards like the fits: each at its `if_room` size, with a chip coloured by
manage's own `bp_level` (ok / over / far over, never graded here) and a theta-over chip; the too-big
list leaves out a row already shown as waiting. An "ex earnings buying power" checkbox swaps in
manage's `ex_earnings` list, the same candidates sized with the earnings trades' buying power freed;
the card never re-sizes a row itself. It is absent where manage wrote no such list, and disabled with
manage's reason where manage refused one. There is no earnings column: manage drops a trade with a
report inside it, so a known date is always after expiry. An unknown one is an `earnings unknown`
chip on the name.

**The wheel's names only**. Live -> wheel shows the names the wheel follows and no other:
needs a look, expiring, the payoff strip and the ledger. The names are manage's (`wheel.names`, the
names with a cycle). The console keeps only those names' items from manage's account-wide `attention`
and `payoff`, which the alert post still reads whole, and the ledger arrives already summed over them
(`ledger.scope`, named in its header). An evaluation from before carries no names and shows the
whole account as it did.

**Live -> wheel's payoff** (`pages/Live/PayoffCard.tsx`): a strip of held names, each
with manage's P&L now and breakevens, opening its chart in the lightbox (`?payoff=`): the nearest
expiry's curve always, a dated curve by chip, the evaluation's price and the stream's live price
marked, the legs with their solved vols, and the model, rate and yield source stated. A curve manage
could not draw (a leg no vol prices), or with an unreadable point, is not drawn, never zero-filled.

The payoff lightbox's second tab, **model a trade**, takes up to six legs (side,
contracts, expiry, strike, right, an optional price) and shows manage's model of them
(`/api/manage/model`, bridged): the credit or debit, the worst case at expiry (or "unbounded"),
delta and theta, implied against realised vol, the chart with the trade over the name's expiry curve
without it, each leg's price and where it came from, and the candidate caps as bars, red past a cap.
It prices a hypothesis; nothing is sent, and the console still has no order path.

**Live -> wheel's needs a look** (`pages/Live/AttentionCard.tsx`) heads the tab: manage's
`attention` items worst first, each with its level and why, a meter of its distance against the alert
threshold (full at the strike or through it), spot and time value; and every leg expiring within the
ruleset's days. The level is manage's, never worked out from the distance beside it
(`web/test/attentionCard.test.tsx`).

**Live -> wheel's ledger** (`pages/Live/LedgerDesk.tsx`) sits above the cycles: manage's
`ledger` for the window in `?lw=` (1M, 3M, 6M, 1Y, YTD, ALL; ALL by default) as tiles (net, puts,
calls, shares, dividends, fees), each month's net as a bar about zero (its sources in the bar's
title), the 0DTE legs left out with their count and net, and a card per name with something closed
in the window. A card opens the name's closed spans and what it still holds (`?ledger=`), with a CSV
of the spans; the header's CSV is every name's. Every figure is manage's for the window shown, never
a sum worked out on the page (`web/test/ledgerDesk.test.tsx` gives a net its parts do not add to).

**The Live page's `wheel` tab** (`pages/Live/WheelCards.tsx`) is the third view of the
same evaluation: the account's `wheel` (packages/manage `wheel.py`), one
card per cycle. Like its siblings it never leaves the rail (there is no manage switch in the features
reply; the shared header says when no evaluation exists). The reader passes `wheel` and each trade's
`wheel` notes through with every null kept null; an evaluation written before the view has
`wheel: null` and the tab says so, and an account with no cycle states "no wheel running" with the
view's `error` when it has one. The line above the cards counts them by status (open, waiting for
a put, complete), and the cash-settled index names manage left out are said once below them. A card
shows status ("flat · waiting for a put" for a cycle flat at a loss with nothing held; an unreconciled cycle lists its problems and
gets **no wheel rule**, only "check it by hand"), days, the money in the suite's layout (signed cash,
fees a cost shown once, the bases and breakeven per share and labelled so), option and stock P&L
apart, realised and unrealised apart, `pnl_net`, the skate yield as a comparison figure, today's
check per open short, and the `wheel.*` rules fired on the trades in `trade_ids` with their roll
candidates and check-roll verdicts. **`basis_known: false` reads "unknown, shares predate the
history", never a number. "Tested", "at or under the buy-to-close" and "beyond the band" are
manage's flags**, never worked out from a price and a strike: `web/test/wheelCards.test.tsx` gives a
flag that disagrees with the arithmetic both ways and was shown to fail against a re-derived chip.
The shared pieces of the evaluation's tabs (action chip, detail card, rows, the per-share and fee
formats, the status, tested and check-roll chips, the band text) are in `manageParts.tsx`.

**PMCC's `by arm` tab** (`board`, `pages/Pmcc/PmccArmBoard.tsx`) is a column per arm
with an open position, the symbols it holds stacked as cards (long and short legs, rolls, net, and an
"exposed N" chip where the short's extrinsic fell under the assignment threshold on N marked ticks).
PMCC is read per symbol, never pooled, so an arm's head is a bar per symbol and its rule in a line,
never a total. A card opens the shared lightbox on its symbol (`?pos=`): a tab per open position on
the symbol, each its P&L since entry (zero at entry, then each priced week's close from the tracker;
an unpriced week is left off, never zero; dashed lines where a short closed early) and its chain off
the tracker (the long bought, every short sold and how it ended, then now), and an "all arms" tab with
every position's P&L on one chart. Reads `/api/pmcc` and the bridged tracker only. The `arms` tab
stays the closed-results comparison.

**The Live page's `pmcc` tab** (`pages/Live/PmccCards.tsx`) is the fourth view: the
account's `pmcc` (packages/manage `pmcc.py`), one card per name whose calls
have a PMCC's shape, recognised or not, in the wheel cards' columns wherever they mean the same thing.
Each card's title, visible while collapsed, carries manage's `recognition` as a chip: `PMCC`,
`diagonal`, or `PMCC? not yet decided` (the delta at entry could not be solved yet, for example a long
bought today before its close is in). An undecided candidate is never called a diagonal; an
evaluation written before the field says only `not a PMCC`. The position card's `intent` row says
what intent is set and where (on this long, or on the name for every PMCC on it) and gives the
`trades` key that sets it on this PMCC alone (manage's `trades.<long contract>.intent`). Its `chain`
row says where the money runs from (this long only, or since the chain's first long through its
rolls, with any confirmed two-order roll). A possible two-order roll manage could not link puts a
`check the roll` note on the width check, with the `continues` line to add.
It is the **real account's** PMCCs, not the paper pmcc module's page. Its id is a trading-module id,
so `visibility.ts` lists the account-wide Live tabs (`LIVE_ACCOUNT_WIDE`) and shows them before the
live-switch rule runs: the paper module has no live gate, the features reply names it false, and the
tab would otherwise vanish (`visibility.test.ts`, shown to fail with the check removed). The reader
passes `pmcc` and each trade's `pmcc` note (or its `error`) through, nulls kept; an evaluation from
before the view has `pmcc: null` and says so, and an empty one says how a PMCC is recognised (a long
call entered at 0.70 delta or more, or `intent: "pmcc"`; an evaluation from before the field
also names its day limits). The width card says "passes, thin" where manage flags a passing cost over
three quarters of the width, with the cost's share beside it. A card shows the long (delta at entry
with its source, "recorded N s after the fill" with the band the underlying's likely move puts on it
or "from the <date> close", or "delta at entry unknown" with manage's reason, today's delta, extrinsic share, days left, the roll
flag: amber "roll the long" only on a recognised PMCC, since manage raises that rule on no other; on
any other candidate a muted "no roll rule" with the thresholds crossed shown as information), the width check with the P&L if assigned gross and net of fees, the long's net cost after
premiums, the short's check (band or "band not in the snapshot", assignment exposure, N(d2),
earnings in its life), every short sold against the long, the skate yield as a comparison figure,
and the `pmcc.*` rules with the roll candidates and check-roll verdicts. Unrecognised: "its diagonal
rules stand". Unreconciled: its problems, no rule, and the net P&L if assigned `n/r`. **The width
check's pass and the assignment exposure are manage's flags**: `web/test/pmccCards.test.tsx` gives
each a flag that disagrees with the figures beside it, both ways, and was shown to fail against chips
re-derived from them.

**There is no Overview page**: `/` redirects to `/live`, whose `today` tab is
the every-loop summary. The morning phase chip is in the status bar and the alert-daemon chip in
the Live header. `readers/liveBooks.ts` stays, because `liveToday`
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

**GEX** (`/gex`, two tabs: gex, with the recorded day as a sparkline, and iv
skew, with volume) computes its profile in TypeScript, mirroring `core.gex`: `analytics/gex.ts`'s `strikeGamma` (one gamma per strike, the OTM side's) and
`netWalls` (no wall without a strike on its own side of zero) are the core's `strike_gamma` and
`net_walls`, and `services/gexProfile.ts`'s `LEFTOVER_ROW_SECONDS` is `core.gex.LEFTOVER_ROW_SECONDS`.
Change each pair together or not at all. `wallAlternates` (`contestedWalls`: a runner-up whose net is
above `WALL_NEAR_TIE`, 80%, of the wall's) is display only; the recorded walls and the wall-clear arm's read are
unchanged by it.

**No streaming open interest** (both GEX pages): tastytrade offers no level 2 data, so open interest is
the broker's once-a-day count as of the open and never moves intraday. GEX by volume is the intraday
read and the default (the panel's walls open on `walls by volume`); `components/GexOiNote.tsx` holds the
one note both pages show, and the GEX card's help repeats it.

**GEX panel** (`/gexpanel`, experimental, SPX only) copies a third-party 0DTE walls panel
over the GEX page's own profile (`GET /api/gex/panel` → `services/gexPanel.ts`, which calls
`buildGexProfile` and adds the chain's straddles from `stream_quotes`), beside today's spot trail with
the levels drawn on it. Broker data only. Recomputed on every request (the page polls every 15 s), so
the moves follow each quote and the clock to expiry. The levels are defined in `analytics/gexPanel.ts`,
since the panel it copies defines none. Off-hours it
shows the **next session's** chain (`buildGexProfile(..., "next_session")`: today's before the open,
else the next weekday's, and only that date's expiration, never another module's later window),
chipped, with its greeks' time; the GEX page keeps the last session's. The last session's chain at its
close had every wall on the strikes beside spot and no move left. Without the next session's chain
(a holiday, or nothing streaming it) the last session's shows, as before.

- **Walls** are the common call and put walls: the three largest call-gamma strikes above spot and put-gamma strikes below
  it, with the side's volume over its OI. Ranking on net left a put-heavy chain with no call wall. A
  toggle weights the gamma by **open interest** (the broker's once-a-day count) or **the session's
  volume** (the GEX page's flow basis): a 0DTE chain trades many times its OI (an SPX chain's walls
  moved 17x to 560x). Net GEX is shown on both bases.
- **The 1-sigma move to expiry**, two ways: the straddle interpolated to spot between the strikes
  either side, over sqrt(2/pi) = 0.798 (an ATM straddle prices the mean absolute move), and spot x ATM
  IV x sqrt(time to the 16:00 expiry, 365-day year). Live quotes older than 120 s
  (`usable_quote`'s age) are not used; neither move is shown once the chain has expired, and the last
  30 minutes are chipped "late session". **The copied panel's 0.85 x straddle band is about 0.68
  sigma, not 1**; it is drawn, labelled so.
- **Magnet** (ours; there is no standard one): the most open interest inside the straddle's 1-sigma
  band (IV's when there is no straddle), with its share and the sign of its net gamma.
- **Max pain** is the strike paying the OI least at expiry, over every strike of the expiration the
  stream cache holds OI for on the newest OI's date (wider than the profile, which drops a leftover
  strike's OI with its frozen gamma); the put/call OI ratio is over the same. OI outside that coverage
  adds a straight line to every payout, so a max pain within two held strikes of either end is marked
  "(edge)". The whole chain's OI would take a Summary subscription per option, not wanted for this.
  **Zero gamma** is the GEX page's.
- **Flow**: the session's prints from gex's `gex_prints` (broker TimeAndSale, recorded when
  gex's `prints` block is on), each signed by the feed's aggressor side, else the quote at the print
  (between bid and ask: unsigned, never guessed). Spread legs, extended hours, cancelled and invalid
  prints are excluded and counted. A customer's net buy of either right leaves the dealer that much
  shorter: flow GEX = -gamma x net bought, added to the OI net. The rule is `core.gex.print_sign` /
  `signed_flow` / `flow_adjusted`; `analytics/gexFlow.ts` mirrors it in SQL (summed per strike in the
  database, never fetched), pinned by `gex-flow.test.ts` to core's hand-worked
  `tests/fixtures/gex_flow_cases.json`. Change both or neither. With no table or no prints the
  section says why.

Display only: nothing records, gates on or is tested against these levels, and the walls the recorder
and the wall-clear arm read are still `netWalls`. The recorder already keeps every input (quote history
with IV every ~15 s, the per-strike profile, spot), so any level can be rebuilt for a past minute. The
copied panel's order-flow rows are left out (no tape or depth feed). Shown while the GEX recorder is on,
as GEX is.

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

**Analysis slide** (every trading module, under study after performance) — `components/analysis/AnalysisSlide.tsx`
over `GET /api/analysis/:module` (`routes/performance.ts` → `readers/analysis.ts` →
`services/analysisBridge.ts` → `python -m cherrypick.core.analysis read`). Per arm: session and trade
runs, drawdown episodes (high, low, new high; falling and recovering apart), the net without the top
1/5/10% of winners, the reshuffle stress test, and capital: observed peak risk with the net over it
("net / largest peak") and max drawdown over it, and the declared allocation (`<home>/config/arm_capital.json`, read by core:
the bridge passes `--module`, never the file). Bridged, never mirrored.

- "net / largest peak" is the whole net over the single largest peak. It is never labelled "return on
  peak risk": that is the performance slide's Σ net / Σ session peaks, a per-day figure (live flies
  control: -6.2% there, -96% here, in one case). A test keeps the two names apart.
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
  over the trades that record slippage. Where that is not all of them (flies before its slippage was recorded) it
  says so and starts from those trades' own net, never the arm's. An undeclared allocation reads "none declared" with dashes, never zero;
  a refused allocation file is a page-level chip with core's reason, and the rest still renders.

- It reads the performance slide's ledger per mode (`performance.ts::moduleLedgerPath`), suite epoch
  and stamped module era, and the bridge groups arms as `core.metrics`' does. A reply whose `version`
  is not `ANALYSIS_VERSION` is refused, not drawn.
- **Each arm is read over its own era** (`core.analysis.windows`): where the
  module's `measurement_breaks` name arms (flies, MEIC), its latest break, else the module's latest
  book-wide one, by `core.regimecuts`' own rule, so this and the regime cuts agree. Modules whose
  breaks name no arm read the suite window, and say why. The suite epoch is always the floor. Every
  arm card states its window (`WindowLine`). The performance slide still reads the suite window, so
  the two can differ, and the page says so: an arm can read positive over the suite window and
  negative over its own era.
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

## Book-review read surfaces

**The book-review read surfaces.** Live → `meic`'s *execution by week* card (B10; `pages/Live/LiveExecution.tsx`) renders the orchestrator's
`execution --json` through `services/executionBridge.ts`: each live arm's week, fill rate, slippage
against the mid at submit, overrides, and the gap to its paper mirror. A closed trade with no recorded
net is out of its week's net and said beside the count (`unpriced`, "1 + 1 no net"), never counted as zero. Compare → `slices` (B5;
`/backtest/slices` redirects) reads the `technicals slices` JSON
under `data/technicals/slices/` (`readers/slices.ts`), behind the backtest gate because a report may
come from a licensed replay: the tab fails closed (`FAIL_CLOSED_SLIDES`, `visibility.ts`; off, unknown
or unread, it leaves the rail and its URL falls to Compare's first tab) and the server refuses
`/api/backtest/slices` on its own. Greyed cells stay greyed and the page
says a cell is never a gate. Both rename and render; every rule is the Python side's. An "In plain English" card heads each report: what the report is and what R means, the overall result against zero, one sentence a dimension (one group, too few groups with 30 trades, separated -- two groups' 95% intervals do not overlap -- or overlapping), the upshot, and how to use it. The reading is the reader's (`analysis`, `readDimension`); the card only words it. The weekday and event-day cuts show a Days column and "Random days as good" (`placebo.beaten_by`, the share of random sets of as many days that did at least as well), and the card adds that reading to a whole-day dimension's sentence, because a trade interval overstates days whose trades move together. Both columns appear only where the report carries them.
