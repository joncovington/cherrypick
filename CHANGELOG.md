# Changelog

The entries up to v0.9.0 are retrospective: the suite tagged no versions until then, so they were
reconstructed from commit history rather than written at release time. Versions mark
architectural boundaries (a new package, a scheduler cutover, a read-side or trading-mode change),
not commit counts. Per-package `pyproject.toml` versions remain at their `0.1.0` placeholder — this
file tracks the *suite*, not any one package. From v0.10.0 every version is a published GitHub
Release, cut from `main` when a batch of work is finished, and the install docs point at the latest
one; [docs/releasing.md](docs/releasing.md) has the procedure. `[Unreleased]` is what `main` holds
beyond the latest release.

## [Unreleased]

- **Fixed: the skate score set history against the wrong implied odds.** A skate is judged at
  expiry, so its odds are the chance of finishing out of the money, 1 − N(d2), not 1 − |delta|. At a
  0.15-delta band that read calls about two points too risky and puts about two points too safe,
  the size of the effect the score ranks on. New: `core.bs.prob_itm`.
- **Added: manage checks its BWD and theta against the broker's model every day.** Snapshot v9
  carries the broker's own greeks for each option leg held (cache first, then the feed through the
  new read-only `get_greeks`), and each evaluation's `portfolio.broker_check` sums both models over
  the same legs and betas, with the gap overall and by underlying.

## v0.11.2 — 2026-10-19 — review follow-ups
The decisions the documentation review (v0.11.1) left open. One fixes how earnings marks and closes
a multi-contract position (no stored number changes); the rest bring shipped defaults and labels in
line with the docs.

- **Fixed: earnings marked a multi-contract position at its P&L times its quantity.**
  `management.unrealized_pnl` multiplied by quantity, but `entry_credit` and the legs are already
  stored as position totals. That overstated a mark and a managed close by the contract count.
  Every trade recorded so far is one contract, so no stored number changes.
- **Changed: pmcc's example config ships the full roster.** XSP, QQQ, GLD, IWM, SLV, SMH and the
  stocks AMZN, TSLA, AMD, NVDA and PLTR, with each symbol's settlement, dividend, earnings and
  window entries, and `max_positions` 12. An install that copies the example now trades what the
  docs describe.
- **Changed: manage's default ruleset is named `manage-2026-10`** (was `zhang-2026-10`). It is only
  a label on each evaluation; stored evaluations keep the old one.
- **Removed: `flask` from MEIC's dependencies.** Nothing has imported it since the REST sidecar was
  removed.
- **Fixed: flies' `live_vs_paper` defaulted to the retired `gex` arm.** It now defaults to
  `control`; every caller already passed its arm, so no reading changes.

## v0.11.1 — 2026-10-19 — documentation corrections
A review of every document in the repo against the code, which v0.11.0 should have carried. About
130 docs changed; no behaviour did. What matters if you act on the docs:

- **Commands that would have failed or misled.**
  - `/live-bwb-start`'s readiness check passed an argument the function no longer takes, so it
    raised a TypeError; it now runs.
  - `/install` refuses while **any** live module (flies, bwb, meic) is armed for today, not only
    flies. Its refusal message now names the armed module's own disarm command.
  - `/live-flies-start` no longer describes the scheduled-task fallback that was removed on
    2026-10-08. It now stops on unresolved broker submissions and overdue settlements, as MEIC's and
    bwb's do.
  - The flies README's paper-loop example used a 300-second interval; the supervised cadence is
    15 seconds, and a different interval records a cadence break.
- **MEIC's live path is described as it runs.** It is the deterministic `live_loop.py`, armed per
  day with `/live-meic-start` and ticked by the supervisor as `meic-live`, sized from the `live` block
  (which may name one arm in `live.arm`). The agent-driven `/meic-start` session is the second,
  manual path. Several MEIC docs still described the agent loop as the only live path, the retired
  forward-test streams as current, and gates in an order the code does not use.
- **Each package's docs caught up with the last three weeks.** That covers contango, manage and
  backtest, pmcc's full roster, curve's `near` arm and `hook`'s retirement, and earnings' seventh
  strategy and its entry-session hold. It also covers the console's Live, Screeners and Charts pages
  and the orchestrator's live jobs and Windows-service mode. Boundary dates that moved from 10-19 to
  10-15 are corrected everywhere.
- **Nothing personal or licensed in the repo.**
  - Test fixtures no longer echo a real account's last four digits, a real balance, a first name or
    a machine user name.
  - The author email is gone from two `pyproject.toml` files.
  - The portfolio-guidance plan's worked figures are a hypothetical account.
  - Statements of results derived from the licensed backtest history now say only what was decided,
    with the results kept locally.
- **Removed:** the Overview screenshot in QUICKSTART (the page no longer exists), and the earnings
  file-size exceptions page, which had drifted from the code.

## v0.11.0 — 2026-10-19 — live loops and the read side
Three weeks and about 420 pull requests since the public release. MEIC got a live path of its own,
armed a day at a time like flies and bwb, and every live loop now stops new risk the same way. Three
packages arrived: contango, manage and backtest. The console was reorganised around the Live page,
and gained a Compare page, an analysis tab on every module and the Screeners page. Under all of it,
the GEX recorder began keeping the chain's quotes, so rules a module never ran can now be replayed
against the session as it actually traded.

### Upgrading

- **Re-run the installer** (`install.cmd`/`install.ps1`/`install.sh`, or `scripts/dev-install.*`).
  It installs every package it finds, so the three new ones come in, and it rebuilds the console.
  The broker SDK moved to tastytrade 13.
- **The new packages are all off until you switch them on.** contango: `modules.contango.enabled`.
  manage: `manage.enabled` in `config.json`, then `install` again (it reads the real account
  read-only every trading day). backtest: a vendor subscription, its key in the keyring
  (`python -m cherrypick.backtest key set`), `~/.cherrypick/config/backtest.json` enabled and the
  vendor extra (`pip install -e "packages/backtest[thetadata]"`); until then its console page does
  not exist.
- **MEIC live is armed per day now.** `enable_live_trading` stays the master switch, but nothing
  trades until `/live-meic-start` writes that day's arm record, and the loop disarms itself at
  17:00 ET. Live-only sizing lives in the `live` block, never the top level, which every paper arm
  inherits. `python -m cherrypick.meic.live_migrate` (a dry run by default) moves top-level live keys
  into it.
- **The halt flag stops new risk only.** Every live loop still confirms fills, manages stops,
  closes and settles while `halt-live.flag` is up. Before, a halt stopped the whole tick and left
  open positions unmanaged.
- **Gone:** the Overview page (`/` opens the Live page), MEIC's REST sidecar and its 7699 API, the
  rollback streamer, and the Windows-only module scheduler launchers. Anything that called the
  sidecar's API has to move to the stream cache.
- **Optional, off by default:** the chain quote history (`quote_history` in the gex config, about
  10 MB a session per symbol), Telegram as a push channel, the supervisor as a Windows service, and
  QuikOptions.

### What's new

- **New packages.**
  - **contango** (#66, #67, #191): the VIX/VIX3M switch held in shares, SVXY in contango and T-bills
    otherwise, decided ten minutes before the close. Paper only.
  - **manage** (#246–#255, #390–#421): a daily read of the real account. Each position gets an
    action and the rule behind it; the account is also read as one book, with beta-weighted delta,
    theta, scenarios, a risk matrix, a sized XSP hedge and income trades sized to the account.
    Advice only: no order path.
  - **backtest** (#342–#388): the suite's own rules replayed over licensed vendor history, flies
    and MEIC so far. Every vendor session is first validated against the recorder. Results stay
    under `~/.cherrypick`, and the console page fails closed without a licence.
- **Live trading.**
  - **MEIC.** A `live` block that names an arm and sizes live alone, per-day arming and a count cap
    (#269–#277). Settlement on the official print, a fee reconcile against the broker and a
    read-only Live tab (#274–#276). A GEX block that can require volume GEX to be negative too
    (#282).
  - **Every live loop.** The halt flag and the daily-loss breakers stop new risk only (#115). An
    uncertain submit holds entries until it resolves, and an unknown working order blocks them
    (#117). One orphan sweep (#219). No entry opposite a leg another live loop holds (#272). Fill
    alerts in seconds (#277). Broker positions are checked against the live ledgers (#126), and a
    past session left open is CRITICAL and settled at its official close (#129, #130).
  - **flies.** Live books the broker's fill price, not its limit (#169). A failed submit no longer
    counts as an entry (#128), and pending fills are confirmed before a book settles (#239).
- **Console.**
  - **The Live page comes first.** A `today` tab over every live loop (#281), the manage tabs
    (`positions` and `portfolio`), and a tab for each live loop, hidden while that module's live
    switch is off (#422). The Overview page was removed (#304).
  - **Compare** (#207, #328–#338): every arm beside its control, a risk tab over every module, a P&L
    calendar that opens to a day's trades, each trade's excursion and trace, and a portfolio view.
  - **An analysis tab on every trading module** (#316–#327): streaks, drawdown episodes, stress,
    capital, a slippage ladder and P&L breakdowns, each arm read over its own era.
  - **Screeners** (#311, #346–#362, #368): the income lists, an earnings calendar with its forward
    record, and a signal board from our own engines.
  - **Around it:** an icon-first rail ordered by use, a simple/detail view switch (#292, #297,
    #410), sortable tables (#306), a Charts page with a live intraday futures chart (#6), a costs page
    per module (#76), and React 19, Vite 8 and Electron 44 (#60–#62).
- **Measurement.**
  - **The recorder keeps the chain's quotes** (#172–#174, #197), read back through
    `cherrypick.core.quotehistory`, with a recording verdict per session (#263).
  - **Replays.** flies and MEIC replay against the recorded quotes and are checked against their
    own paper (#339, #340, #385, #386).
  - **New in core:** `core.stats` (#205), `core.analysis` (#316), and model-free implied variance
    (#144), which bwb records at entry (#145, #146).
  - **The GEX profile** uses one gamma per strike, and a one-sided chain no longer makes a wall
    (#152, #157).
- **Modules.**
  - **flies:** the intraday agent (shadow on live, acting on paper; #77–#93) and the `range-high`
    arm (#152).
  - **pmcc:** the Income Shield as its base trade, extended to single stocks and SMH with an
    earnings refusal (#39, #52, #55).
  - **curve:** the 10-06 boundary, $2 wide with a net credit floor (#64).
  - **earnings:** the expected-move butterfly, on paper (#244).
  - **bwb:** live enters only while VIX9D is below VIX, and records the vol term structure at
    entry (#162).
  - **technicals:** six study rounds, setups and a watchlist, analyst revisions (#31–#38,
    #150–#155, #298–#301, #365–#372).
- **Operations.**
  - **The supervisor** can run as a Windows service (#136–#139, #156, #258), manages the Dolt server
    as a daemon (#158), and replaces itself when hung (#135).
  - **The watchdog** reports failed and missed jobs (#131, #148) and low disk (#135).
  - **Linux and macOS:** notifications, battery reads and process control work there (#118–#123).
  - **Elsewhere:** a broken `config.json` runs on its last good copy, loudly (#134), and
    `run.py sent` reads back every webhook send (#102).

### In detail

The entries below were written as each change landed, newest first.

- **Added: Telegram as a push channel.** `telegram` joins `desktop`, `slack` and `discord` in the
  `notify` channel lists. It takes a bot token and a chat ID, stored in the OS keyring with
  `cherrypick secrets-set --channel telegram` (prompted without echo, never in config files or env
  vars) — there is no webhook URL to store, so the settings page deliberately offers no Telegram
  URL field, and the notification secrets live on only for its two entries. Same best-effort
  posture as every other channel: the log floor is written first, push failures are swallowed, and
  the watchdog now watches Telegram delivery like the webhook channels', naming a configured
  Telegram channel with no token stored and a window of failed posts.
- **Changed: the QuikOptions weekly scorecard goes to the Discord notify channel only.** It is the
  suite's measurement of the derived flow, not part of the options report, so it never posts to the
  reporting webhook, whatever `quikoptions.post_webhook` says.
- **Added: the QuikOptions schedule.** Six trading-day jobs: the capture at 16:30 ET (with up to ten
  minutes of random delay), scoring at 16:50, the series at 17:00, the open-interest confirmation at
  08:30 and its morning post at 08:45, and the weekly scorecard at 17:15 on the week's last trading
  day. Each later step waits up to 45 minutes for the one before it, so a machine that wakes after
  the evening still runs it in order. `quikoptions_flow.py score --require-today` scores only the
  day's own capture, and every score refreshes the fixed test's progress. The console's Config page
  can now pause, retitle and re-route the series (`post`, `post_title`, `post_webhook`).
- **Changed: the QuikOptions Discord series, after test posts.** One screen capture a message (Derived
  flow from a hidden post page without the factor columns, and Trades, the Birdseye card renamed),
  a header led by the derived flow and yesterday's calls a day on, a morning follow-up once the
  overnight open interest is published, and a Friday scorecard. Every flow now reads symbol, date,
  strike, kind. The trade-size bands have a colour each, so the 100+ band shows. The open-interest
  confirmation refuses while nothing has moved (a Saturday check had called every flow mixed).
- **Added: verification for the derived flow.** The broker's own delta (the streamer's greeks at the
  close) is now primary and the model's is checked against it; each day records the read against
  the site's sentiment, the next morning the closes against Dolt; `quikoptions_flow.py audit` records
  hand checks against Time & Sales; `review` runs a test fixed in advance (40 sessions: strong scores
  must beat every simpler read). All on a Checks card. The site's sentiment, not its fill wording, is
  now the second vote.
- **Added: derived flow** (`scripts/quikoptions_flow.py`). Each QuikOptions capture becomes one
  row per order (outrights, sweeps, spreads, with paired prints and rolls grouped), read
  (bought/sold from where the fill sat, or unread), sized in stock-equivalent dollars from the
  broker's close and the trade's own implied volatility, flagged (deep in the money, lottery,
  sold, near-max spread, earnings event, before ex-dividend, opening evidence) and scored on size,
  conviction, purity and opening. The next morning's open interest confirms opened or closed, and
  each run records the 1- and 5-session outcome. Shown as a `derived flow` tab and two cards on
  the Options flow page, and as the first section of the text post. The score's constants are
  provisional until the outcome record can judge them.
- **Added: the QuikOptions Discord series** (`scripts/quikoptions_post.py`). A session as a
  header embed (the day in four fields, the capture time in its footer) and then the
  Options flow cards named in `quikoptions.post_cards`, two to a message; by default Largest
  outrights + Top sweeps, in one of four styles (`post_style`: card images, one image a message,
  one embed of small tables, or plain text), with an Events section of the day's releases against
  estimate and the next ones. `quikoptions.post_webhook` picks the suite's Discord notify webhook or the
  reporting channel (`post_webhook: "reporting"`, keyring entry `discord_reporting`, new in
  `secrets-set` and the settings surface, never a push
  channel), never one as a fallback for the other. Mentions are off; a re-run resumes rather than
  repeats. Off by default (`quikoptions.post`); not yet scheduled.
- **Added: the console's Options flow page** (`/flow`), QuikOptions' Hot Options Report for a
  session. `today` is the day at a glance (most traded, the largest trade, premium by the site's
  own Bullish/Bearish call, the names in more than one table, and a card per table); `birdseye`,
  `trades`, `spreads` and `vol / OI` are the site's tables in full. The capture now derives what
  the page shows (an outright's premium, a spread's direction and the trades printed together, the
  four trade-size bands) so the console computes nothing, and `fetch_quikoptions.py reparse`
  rebuilds saved days from their HTML. The Morning report's OCC hot-options card marks the names the
  same session's flow also holds. Shown only with `quikoptions.enabled`, a new suite config block,
  off by default.
- **Added: QuikOptions' Hot Options Report, captured daily** (`scripts/fetch_quikoptions.py`;
  plan in `docs/quikoptions-plan.md`). One paced load a day in the installed Chrome on a profile
  signed in by hand: the six tables (Birdseye, top outrights, sweeps, spreads, and both
  volume-over-open-interest lists) and the economic calendar, read from the drawn page and kept
  only when the page's own sums hold. The console page, the Discord series and the schedule
  follow.
- **Fixed: the preliminary Michigan sentiment reading was never on the event calendar.** FRED lists
  only the final reading, so 2026-10-09 read as a known day with no release. The morning fetch now
  folds Michigan's own "next data release" note into `umich.json`, which keeps every release (a
  moved one replaces its old date) and the span each fetch speaks for. A day no note covered,
  including every day before the first fetch, is degraded, not unknown. flies' event cache now keys
  on every file the calendar reads. Found by comparing against QuikOptions' calendar.
- **Documented: the first read of the flies event tags.** Release days completed more than quiet
  ones (81% vs 75%) at the same implied volatility. Quiet low-volatility days were weakest (67%), and
  NFP is the only release with a consistent negative, on three sessions. Recorded in the flies
  experiment log with the dates to re-read each.
- **Changed: the event calendar takes headline dates from Census and covers more releases.**
  - **Census for its own releases.** FRED files other updates under the same release ids: it
    listed retail sales on 2026-09-28, and new home sales as housing starts. Census's own calendar
    is now the source for retail sales, housing starts, new home sales, durable goods and factory
    orders, kept in a never-dropping `census.json`, with FRED as the fallback.
  - **More releases.** ADP and ECI are added from FRED. ISM Manufacturing and Services,
    Conference Board confidence, FOMC minutes and monthly options expiry are computed by rule and
    labelled so.
  - **Re-tagging.** `run.py backfill-events --restamp` re-tags rows after a calendar correction.
- **Added: every flies entry and completion records the day's scheduled releases.**
  - **The calendar.** A new `cherrypick.core.events` reads the release calendar the morning fetch
    already stores: BEA, FRED and the curated FOMC days. It says when a source cannot speak for a
    day, so a missing FRED key reads unknown, never quiet.
  - **History.** The fetcher now keeps `fred_history.json`, which never drops a date; until now
    each run replaced FRED's 45-day window. `fetch_market_files.py fred-history --since` seeds it.
  - **The tag.** flies records `event_bucket` (after/before/none/unknown against the major
    releases), the minutes since the latest one, and every release's label.
  - **Reading it.** The regime cuts carry the dimension; `run.py backfill-events` stamps earlier
    rows (a dry run unless `--write`).
  - **No gate.** Release days completed like quiet ones over 43 sessions, and restricting their
    mornings cost money; the flies experiment log has the numbers. Overview now shares core's BEA
    parser.
- **Fixed: two 2026 FOMC dates were wrong, and 2027 is now bundled.** `core.calendar` had the June
  decision on 06-10 (the meeting was 16-17) and December on 12-16 (the meeting is 8-9). MEIC blocks
  entries 13:30-14:30 and force-closes at 13:30 on FOMC days, so on 12-09 it would have held
  through the announcement and on 12-16 closed for nothing. No ledger was affected: MEIC's records
  start after June. 2027's eight meetings are added, so FOMC-dependent reads do not go unknown on
  2027-01-01. Both years come from the Fed's own calendar, and the test now pins them from that
  source rather than from the config it once mirrored.
- **Changed: CI runs only what a pull request touches, installs with uv, and a 62-second core
  test takes one.**
  - A new `changes` job (`scripts/ci_changed_packages.py`) maps the changed paths to the package
    jobs that need them. A package's own changes run that package; core, `scripts/`, `tools/`,
    `.github/` and root config run everything; docs-only changes run only the docs job; a push to
    main runs everything.
  - Jobs that aren't needed skip their steps but still report, so every required check keeps its
    name. A failed detection runs everything rather than nothing.
  - Installs moved from pip to uv, with a cache.
  - The slow test was the streamer's chain-fetch retry, which backed off 2+4+8+16+32 seconds without
    checking for a stop. The backoff now ends the moment a stop is requested. That also stops a real
    streamer asked to shut down during a retry from waiting up to a minute.
- **Added: the flies `vol-floor` paper arm (from 2026-10-05).** `control` plus one variable: no
  entry while the ATM straddle is under 0.0022 of spot (`engine.low_vol_refusal`). The low-vol third
  of control's sessions completed 70% against a ~75% break-even. In-sample it was ahead in both
  halves, but not significantly, so it is an arm, not a default. The rule it will be judged by is
  declared in the flies experiment log, which also records why side, placement, credit, trend,
  later cutoffs, wider wings and stacking on completed flies would not raise completion.
- **Documented: entry credit is not a useful flies entry gate (a negative result).** Neither a
  minimum entry credit nor a 2.40–2.59 band improved `control` on a replay of 43 sessions.
  Completion is 78% in every credit bucket, and a high credit mostly reflects the side sold and
  where the short strike sat. Recorded in the flies experiment log.
- **Changed (a measurement break, 2026-10-05): paper flies completions pay the live limit.**
  Live has filled every completion at exactly its resting limit, while paper paid its modelled
  debit, about $4 a completion better than live ever got. From 2026-10-05 every legged arm pays the
  limit live's order rests at, on the unchanged trigger. The paper loop journals a book-wide
  `completion_rule` break; do not pool completion P&L across it. `completion_price: "modelled"`
  keeps the old rule for an arm.
- **Added: flies records what live fills needed, and paper shadows a live completion.** Every live
  order now gets a `fly_live_orders` row (filled or not): its limit, the broker's own fill time and
  leg-fill price, and at the fill, spot past the centre, spot past the completing long strike, and
  the mid/natural gap to the limit. Each working order also gets a `fly_order_path` of quotes and
  spot at every tick and watcher cycle. Every paper legged entry stamps the limit a live completion
  would rest at and keeps first touches against it until settlement, down to net targets of
  credit − 1.00. The order status from `cherrypick.core.broker` now carries the broker's per-leg
  fills. Read with `run.py fill-model`; past orders are backfilled from broker transactions and the
  gex spot trail with `python -m cherrypick.flies.fill_facts backfill` (a dry run unless `--write`).
  Telemetry only, so not a measurement break. Write failures are logged, never raised into a fill.
- **Fixed: the console's bridges reached the system Python on Linux.** The launcher prepends its
  own interpreter's directory to node's PATH so a bare `python` resolves to the one with the suite,
  but it resolved the path through symlinks — and a Linux venv's `bin/python` is a symlink to the
  BASE interpreter, so the PATH entry became `/usr/bin` and every bridge (config editor, keyring,
  positions, features) answered "the orchestrator package must be installed". Windows venvs copy a
  real launcher in, so `resolve()` happened to be correct there; on Linux the venv must be reached
  THROUGH the symlink. Now `absolute()`, guarded by a regression test that builds both venv shapes
  (symlinked and real-file interpreter) and pins which directory leads the child's PATH.
- **Fixed: the suite-wide account listing ran a bare argv.** `accounts._first_broker_module`
  called `cfgmod.broker_tool(mcfg)` without the module name, so for any module whose config block
  does not declare `broker_tool` itself (meic ships without one), `cherrypick account --set` and
  the Live Ops listing probed the login with `python list_accounts` — no module, no output, and
  "list_accounts not ok" on every such machine, while `account --module`, per-module `connect`
  and `reconcile` (all of which pass the name) resolved the same tool fine. Suite-wide
  `cherrypick connect` uses the same probe for its connection check, so it had the bug too and is
  fixed with it. Now passes the name like every other call site; guarded by a regression test
  asserting the argv the probe hands the broker tool.
- **Fixed: the suite-wide account probe could pick a module with no broker.** It took the first
  enabled module with a checkout, so calendars, pmcc or curve enabled ahead of meic, flies, bwb and
  earnings became the probe and ran `-m cherrypick.<name>.tt`, which does not exist: "list_accounts
  not ok" again. It now takes the first enabled module with a keyring service, and says so when
  none is enabled. Tests cover module order, a broker tool declared only in config (bwb), and
  suite-wide `connect`.
- **Hot options** in the morning pack (fact version 5). `scripts/fetch_market_files.py` lands OCC's
  daily option volume by underlying (one keyless CSV a session, reduced to call/put sides by account
  type) and Nasdaq Trader's symbol directory; `cherrypick.overview.occ` ranks the prior session the
  way the Options Insider's Hot Options Report does — VIX, SPY, SPX, IWM and QQQ, then the top ten
  single-name equities and top five funds — with call/put, the customer share of sides and volume
  against 20 prior sessions. Rendered on the console's Morning tab and in the markdown. Record-only.
- **One OCC fetch.** The stock universe's harvest no longer downloads OCC's file itself: it lands
  missing sessions through the same fetcher and reads contracts from the shared store. The two
  copies agreed on every underlying of all 13 sessions both held, and the universe builds
  identically from either. `universe/occ-volume/` is no longer read or written.
- **Releases.** v0.10.0 is published as the first GitHub Release, and README, INSTALL and
  QUICKSTART now install from the latest release instead of `main` (git users check out its tag;
  updating is a fetch and the same checkout). Pushing a `v*` tag on `main` publishes the next one
  with its changelog section as the notes (`.github/workflows/release.yml`, `tools/release_notes.py`).
  [docs/releasing.md](docs/releasing.md) has the procedure.
- **Charts page** (console). A live intraday futures chart — /ES, /NQ, /CL, /GC, /ZB in 1-, 5- or
  15-minute candles, extended hours, the bar in progress updating about once a second — over the
  console's own DXLink session, measured first with `scripts/probe_candles.py`. The technicals chart
  moved here from Reports (`/charts/technicals`; `/reports/chart` redirects). The console's feed now
  aggregates at 1s instead of the SDK's 10s.

## v0.10.0 — 2026-10-01 — the public release
The first release meant to be installed by someone other than its author: one command takes a fresh
clone to a running console, the experimental modules ship switched off, every example config
carries only its `control` arm, and a feature runs only where the machine can carry it. Around that,
the market report's own engines arrived as `packages/technicals`, and every trade surface now states
money one way.

- **Install.** `install.cmd`/`install.ps1`/`install.sh` (`c25d2cda`) check Python 3.11+ and Node 22+,
  take the disclaimer only on a typed YES, build the venv and console, and offer an optional Dolt
  setup; `uninstall.*` is the full stop. `DISCLAIMER.md` (`898cbd27`) and `QUICKSTART.md` (`9a7b0bb2`) are new,
  and the docs were reorganised for a reader arriving fresh.
- **Capabilities** (`6194577f`). `config.json` declares `capabilities: {claude, dolt}`, and
  `run.py capabilities [--detect --write]` resolves them; a module or feature missing one is never
  scheduled, and the console hides what is switched off or unequipped (`465fade5`). calendars, pmcc and curve
  carry an "experimental" chip.
- **The base install** (`21a59bb5`, `240d14d5`). calendars, pmcc and curve are EXPERIMENTAL and off
  by default; module examples ship only `control`; push notifications ship off (every channel list
  is `["log"]`; the console's own toasts need no setting; `3d6a3cfa`). MEIC's arm registry left the repo
  (`packages/meic/config.risk.json` is gone): it reads `$MEIC_RISK_CONFIG`, else
  `~/.cherrypick/config/meic.risk.json`, else the control-only `config.risk.example.json`.
- **Packaging.** Every package declares the MIT licence and `requires-python >=3.11`, the streamer
  declares its tastytrade dependency, and `.gitattributes` pins line endings (`0d25c564`).
- **technicals** (new, `51f5378d`): the market report's end-of-day store over the local Dolt clones,
  its splits and dividends reconciled, with the vendor's level grid, scan rules, trend scores and
  1-10 rank fitted and scored out of sample; level selection is unsolved and says so (`fa180372`).
  SPX bars come from the broker's daily candles (`803da580`).
- **The market report.** A vendor-edition collector (`e96ac202`) and a stock universe (`cece8060`)
  feed it; the morning pack gained market files, the pre-market tape, the SPX 25-delta risk reversal
  and next week's earnings (`51007148`, `0cf79b2b`); report and chart page sit on the console's
  Morning tab (`bf846a58`, `1af3c615`).
- **One money layout** (`f5ba7daf`): net P&L, signed cash flow and whole-position dollars on every
  surface, settlement fees recorded as their own column in every module (`7b3340fe`, `f33589db`).
- **One word for a variant: `arm`** (`8d819b14`), in code, ledgers, review and advisor; the old
  spellings resolve for good, and a moved config key is now loud (`2ac18928`).
- **flies.** The delta-arm rollout, six arms plus the ATM twins, landed at the 2026-09-21 boundary
  (`aa23d5db`); the callwall arm retired. The live pilot sizes by its buying-power cap alone
  (`69f74f3a`) and starts at 10:15, on a replay of control's era under the live margin cap.
- **MEIC.** A `live-shadow` paper arm measures the live configuration it had never run on paper, and
  a buying-power cap carries three arms trading control at $5k, $10k and $25k (`57945bbc`).
- **Regime cuts** (`988612c3`): a nightly per-arm x per-regime artifact for flies and MEIC, stamped
  with one-session weight, paired contrasts, sign changes and a multiplicity count; MEIC's GEX bucket
  is re-derived sign-first, and flies' carries a gate replay with `entry_windows` an advice bound.
- **Calibration** gained a per-session Sharpe and its Probabilistic Sharpe Ratio (`session_sharpe`,
  `psr`) beside the per-trade one (`48ce143a`).
- **Elsewhere.** bwb scores its add-on as its own trade (`23307da9`); earnings closes at 15:30 on
  expiry day (`b58ddf27`); live loops skip quarter-end sessions (`93c6bd91`); a nightly suite backup
  (`d4329271`); CI runs every package's tests and shows each guard can fail (`dd3d6a93`); the
  console moved onto a module frame (`4e5c6c21`) with a System page (`6367ca04`); CLAUDE.md files
  consolidated (`f1bf8ecd`).
- **Not recorded before.** review (`92cc84ab`) and the console's desktop window (`6d0b2e02`) date
  from 2026-08-12. desk (`f19012d2`) is an EXPERIMENTAL prototype, not installed or enabled by
  default: it needs `enabled` and `CHERRYPICK_DESK_EXPERIMENTAL=1`, under low caps.

## v0.9.0 — 2026-09-18 — bwb's live path
`a6fd2bde` wired bwb's narrow live pilot into the guards, the supervisor, and the arm command — the
first new live-trading path added to the suite since MEIC and flies. One configurable arm, armed
per day, under a worst-case margin cap and a cost-derived credit floor; every fill is still the
broker's word.

## v0.8.0 — 2026-08-23 — the paper-only module wave
`22ec497d` added bwb and curve, completing a run of credential-free, stream-cache-only modules
started with calendars (2026-08-14), pmcc and overview (2026-08-16): each a forward experiment
against the shared streamer rather than a strategy with its own broker session.

## v0.7.0 — 2026-08-20 — the advisor era
`d81ac97b` opened "one experiment mechanism" — the deterministic advisor fact-packs and the
`advised:<experiment>` paper A/B books — and retired the module-specific champions/challengers
machinery (`1c7e52bd`) in its favor.

## v0.6.0 — 2026-08-12 — console becomes the one read surface
`6b3732ab` deleted the scout package after its watchlist/screener/builder/payoff surfaces were
ported into the new console (`packages/console`, first commit 2026-08-09); `de352a13` retired every
other read surface except it the same day.

## v0.5.0 — 2026-08-09 — the supervisor cutover
`a0a4b9d4` replaced the per-job Windows Task Scheduler entries with one supervisor daemon
(`run.py install`) — the orchestrator's single always-on process model that every module's resident
loop and the console now run under.

## v0.4.0 — 2026-07-30 — the settings surface
`9f2aa3a` added `cherrypick settings`, a loopback config editor and secrets manager — the suite's
one mutating HTTP surface, with guarded live-trading fields refused on both write paths.

## v0.3.0 — 2026-07-11 — the monorepo
`75b8539a` restructured the suite from sibling repos (MEICAgent, EarningsAgent, cherrypick-core as a
submodule) into one monorepo under `packages/`. `packages/core` landed the same window as an
ordinary editable dependency, and gex (07-11) and streamer (07-21) arrived as the first modules
built monorepo-native rather than migrated in.

## v0.2.0 — 2026-07-06 — Earnings joins
First commit of the earnings-play module (`e487db35`, "Scaffold EarningsFlyAgent project") as a
sibling to MEIC, still in the pre-monorepo, per-repo era.

## v0.1.0 — 2026-06-18 — origin
`c8e0fe73`, "Initial commit — MEICAgent MEIC trading agent." The suite's origin: a standalone
0DTE multiple-entry iron-condor agent, before any orchestrator, shared core, or sibling module
existed.
