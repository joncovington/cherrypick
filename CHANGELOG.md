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

### What's new

- **Buying power on a cash account or IRA.** Such an account holds each short put's full strike and
  each spread's max loss, and neither grows when the market moves, so manage no longer holds it to the
  VIX table written for margin. It is held to 80% in total (the rest kept free to sell after a
  volatility spike), with short puts at their strikes plus shares under 65% of net liq and the other
  trades' buying power under 35%. A new rule, `portfolio.stress`, flags SPY −20% with IV up 10 points
  costing over 20% of net liq. The income list, the hedge and the trade model size against the same
  caps, and Live → portfolio's room card lists each part. Margin accounts, and accounts of unknown kind,
  keep the VIX table. The caps are in `portfolio.buying_power.cash`; `enabled: false` restores the VIX
  table for a cash account.

## v0.12.1-beta — 2026-11-10 — moving delta
The first beta: a pre-release to try before v0.12.1. `upgrade` and the version chip will not offer
it, and a fresh install will not land on it; it is reached only by name. It adds a way to move the
book's delta without closing anything, keeps the PMCC tab to PMCCs, and gives the wheel tab a choice
of which cycles to show. No stored number changes, and there is no config to set.

### Upgrading

- **To try it:** `python packages/orchestrator/run.py upgrade --to v0.12.1-beta`. It refuses while a
  live loop is armed or holds a position, as every upgrade does.
- **Nothing to do by hand.** The manage snapshot now reads a few more broker chains (each put
  calendar's next expiries), inside the existing `manage.roll_out` cap.
- **From the beta to the release:** v0.12.1, when it is out, is an ordinary upgrade from here.

### What's new

- **Beta releases.** A version can now be tried as a beta before it is the release. A beta is a
  GitHub pre-release: `upgrade` and the console's version chip never offer it, and the install
  instructions skip it, so nobody lands on one by accident.
- **The PMCC tab shows PMCCs only.** A call calendar or diagonal whose long was not bought deep enough
  to be a PMCC is judged as a time spread, so it no longer gets a PMCC card. A line names it (now
  "calendar" or "diagonal" for what it is) and opens the positions tab's new **time spreads** filter,
  which lists every calendar and diagonal, calls and puts. A candidate whose entry delta is not known
  yet stays on the PMCC tab until it is decided.
- **Wheel tab state choice.** The cycles open on **active** (open, waiting for a put, and any that
  does not reconcile), with open, waiting, complete and all one click away, each counted. The choice
  is in the page's address.
- **Cards or rows on the wheel and PMCC tabs**, as on the positions board: one row per cycle or PMCC,
  opening its card in place, remembered per tab.

- **Delta ideas on the portfolio tab.** The direction card has an **ideas** button: what drives your
  delta, and the rolls that would move it either way without closing anything, each priced as the order
  you would enter, with flags for anything that goes against your ruleset or sizing. A poor man's
  covered call is rolled under its own PMCC rules, with the width check kept. Worked out on every
  manage run, inside the band as well as outside. The thresholds are in `portfolio.delta_ideas` in
  `config/manage.json` (defaults in `packages/manage/config.example.json`); nothing to set to use it.
- **Put calendars' rolls are now quoted.** The manage snapshot reads the next expiries after a put
  calendar's or diagonal's short, as it already did for call ones, so those rolls are priced from
  quotes. That adds a few broker reads to each manage run, inside the existing cap.

## v0.12.0 — 2026-11-09 — the guided release
Three weeks and about 560 pull requests since v0.11.2. This is the release that is written to be
upgraded to. Earnings and the manual desk stopped placing orders for good, so live orders now exist
only in MEIC, flies and bwb. The four retired modules (flies, curve, pmcc, bwb) ship switched off.
The QuikOptions integration is gone, replaced by a generic way to put any report of your own in the
console. The console grew a Help page, a version chip and a new layout, and manage learned to read
the whole account: wheels, PMCCs, income lists that fit the account, premium pace and a ledger of
what each name has earned. A research queue now runs the long studies, and a weekly put module
arrived. The release also brings the tooling for moving between releases (`run.py upgrade`), which
pays off from v0.12.0 on; getting here from v0.11.x is a manual job, spelled out below.

### Upgrading

**From v0.11.x this is manual** (`run.py upgrade` is in this release, so it cannot upgrade you to
it). Pick a quiet evening after the close, with no live loop armed.

1. **Stop the suite** (`uninstall.cmd` or `./uninstall.sh`; your data, settings and broker login are
   kept). If you run the supervisor as the Windows service, stop the service as well.
2. **Get v0.12.0.** With the ZIP, unzip it into a new folder and work from there. Don't unzip over
   the old one: files this release removed (`packages/desk` among them) would stay behind. Delete the
   old folder once v0.12.0 runs. With git: `git fetch --tags origin`, then check out the tag.
3. **Run the installer again** (`install.cmd`/`install.ps1`/`install.sh`, or `scripts/dev-install.*`).
   It rebuilds the console and, new in this release, removes `cherrypick-desk` from an older `.venv`.
   It also now sets up Dolt unless you pass `-SkipDolt`, and runs `run.py bootstrap` at the end so
   the income lists have a universe on day one (`-SkipBootstrap` skips that). If Dolt is already
   there, nothing is downloaded. `-WithDesk` is accepted and ignored.
4. **Run `python run.py doctor` and act on its warnings.** After an upgrade you can expect these,
   each for something that is now ignored and can be deleted:
   - `earnings.live_config`: the `enable_live_trading` or `live` key in your earnings config;
   - `config.quikoptions`: the `quikoptions` block in `config.json`;
   - `config.desk_notify`: the `desk_notify` block in `config.json`;
   - `config.desk`: `~/.cherrypick/config/desk.json`.
5. **Decide about the retired modules.** Your config is never rewritten. The example configs now ship
   flies, curve, pmcc and bwb off, but an install that has them on keeps them on. If you want them
   off, set `modules.<name>.enabled` to false (pmcc and bwb had entries switched off on 2026-11-08;
   pmcc's `defaults.wind_down` stops entries and closes what is open, and the module switches off once
   flat). Each package's README opens with the exact switches that reinstate it.
6. **Restart the supervisor** with `python run.py supervise --restart`, and restart any resident job
   (`meic-live` and the like) after it. The job table changed (below), and a job reads its config
   only when it starts.
7. **Check the console** at the usual address. The version chip at the right end of the status bar
   should read `v0.12.0`.

**Things to know before you act:**

- **Earnings cannot place orders.** The module is paper-only. `tt.py execute_trade`, `/earnings-start`
  and `/live-earnings-start` are gone, and a leftover `enable_live_trading` does nothing. `cherrypick
  account --module earnings --set` refuses, since earnings needs no trading account (its broker login
  stays, for quotes). Past live earnings trades stay visible, read-only, in the console's live views.
- **The manual desk is gone** (`packages/desk`, the `desk-notify` job, the `notify-desk` command). The
  console's own operator "desk" view on System is not that desk and stays.
- **QuikOptions is gone.** Its seven jobs, its config and the Options flow page are removed. Reports of
  your own go through external reports (see What's new).
- **`/live-flies-start` and `/live-bwb-start` refuse while their module is off.** Every live loop can
  now be armed from the command line: `python -m cherrypick.<meic|flies|bwb>.live_loop --arm --confirm
  YES` prints the disclaimer and arms only on that exact word; `--disarm` disarms. The slash commands
  go through the same code.
- **The arm gates new MEIC condors only.** An unarmed `meic-live` now runs from 09:30 to the disarm
  cutoff to manage what it holds (stops, fills, settlement). A held condor with a live gate unmet
  raises the new CRITICAL `live_unmanaged`. MEIC live entries are one contract; a larger configured
  quantity refuses entries and blocks arming.
- **The morning and evening data runs are two pipelines.** `morning-data` and `evening-data` replace
  28 jobs in `status` and on the System page. A pipeline catches up for as long as its longest step
  does, so after a long sleep a short-window step can run later than it would have
  as a job; those reruns rebuild the same files. A failed run is retried twice from the failed step.
  Per-step timings are in `status` and `state/pipeline.<name>.json`.
- **manage runs and posts differently.** The fill wait is 60 seconds (was 30), no snapshot is taken
  within 2 minutes of the last, `manage-marks` reads the broker's positions every 5 minutes in
  session, and scheduled runs post only when the action list changes. Set `manage.on_change` to
  `{"enabled": false}` for the old timer-only post and 30-second wait. This changes when
  evaluations exist, not how any trade's money is computed.
- **The scheduler changed.** Eighteen job rows are gone: a daily job that runs at several times is one
  job with several times (`manage-eval`, `report-screener-measure`, `report-skate-quotes`,
  `universe-measure`, `market-files`, `technicals-earnings-calendar`). `status` and the System page list
  every time in the schedule column, and `jobs.missed` and `jobs.failed` name the one id. After a long
  sleep only the latest slot runs. Jobs of switched-off retired features (flies and its agents, the
  vendor-edition collector) leave the table while off, and the `symbol-watch` job is removed. New
  supervisor jobs: `research-queue`, `account-alerts`, `technicals-dollar-volume`,
  `technicals-income-lists`, `technicals-yahoo-adjusted`, `technicals-adjust-check`, and the weekly put
  paper loop (off).
- **Order notices no longer depend on arming.** `account-alerts` runs trading days 09:00 to 18:00 and
  reports every order placed, filled or ended, by the suite or by hand.
- **The console asks GitHub once** (its first outbound request) for the latest release, at most every
  six hours. Set `"update_check": false` in `~/.cherrypick/config/console.json` to turn it off.
- **`research/` and the `archive/docs-2026-11` tag.** The hypothesis register and declared studies moved
  from `docs/` to a top-level `research/`; `cherrypick hypotheses` reads it there. The docs now
  describe only the current version. Everything removed is at the tag.
- **Backtest scripts moved** to `packages/backtest/scripts`.
- **A curve arm the config does not declare stays off** (control excepted), from 2026-10-30. A config
  that predates an arm never gains its block.

**Measurement boundaries.** A row on either side of one of these was produced under a different rule:
never pool across them. Each module's own list (MEIC `docs/reference.md#declared-boundaries`, earnings
`docs/measurement-boundaries.md`, and a section in the other packages' CLAUDE.md) is the full record.

| Module | Date | What it splits |
|---|---|---|
| All modules | 2026-11-03 | The supervisor and every job it starts are opted out of Windows power throttling (EcoQoS, where a CPU-bound loop ran 6.8 times slower). Ticks take less wall time, so tick timing, quote age at decision and stop latency either side are not poolable. One break journalled per ledger. A process takes it when it next starts. |
| MEIC | 2026-10-24 | Live kill criteria (`live.kill`) gate new condors. |
| MEIC | 2026-10-25 | A closed live condor with no net (`pnl` or `fees` NULL) refuses new ones. |
| MEIC | 2026-10-27 | A hand-placed opening order obeys the loop's entry rules and needs the day's arm. |
| MEIC | 2026-11-02 | The arm gates new condors only; settlement asks the broker first; an entry is written ahead of its order; removed orders are dead; live entries are one contract. |
| MEIC | when set | `live.resting_stop` (off by default) puts a stop-limit at the broker on each side. The broker triggers off the spread's ask, a little earlier than the loop's own stop, so switching it on is a boundary against `live-shadow`. |
| MEIC | when set | `delta_source: "computed"` picks short strikes by a delta computed from the chain's own quotes instead of the feed's, which lags by about a minute. The default is unchanged. |
| MEIC | per arm | The stabilization entry gate and the equalize imbalance cap are options an arm opts into; the arm that sets one is a different arm from its control. |
| Earnings | 2026-10-26 | Exits price at mid (clamped to the structure's payoff) for positions entered from here; `broken_wing_butterfly` retired from `strat_test`. |
| Earnings | 2026-10-28 | Entries only on names the liquidity verdict calls `tradable`: about half the entries and a universe of larger names. |
| Earnings | 2026-10-30 | A failed entry scan is retried; entries open as the scan goes; a held close is taken after 30 minutes of the spread gate. |
| Earnings | 2026-11-05 | Early-close days run no entry scan; leg quotes and term-structure inputs recorded; the winrate backtest scores the expected move. |
| Earnings | 2026-11-06 | The PEAD gate fires on the first morning's first executable check; the expected move is the report's own, from two expiries; `assignment_risk` closes; the expiry close is at 15:20. A paired twin without the gate opens beside each overnight control. |
| Earnings | v0.12.0 | Paper-only. |
| pmcc | 2026-10-26 | The `shield_guard` arm joins (additive). |
| pmcc | 2026-11-08 | Entries off (`wind_down`). |
| bwb | 2026-10-24 | Live kill criteria. Entries off 2026-11-08. |
| curve | 2026-10-30 | An undeclared arm stays off. Retired 2026-11-01. |
| flies | 2026-10-19 | Retired. |
| manage | 2026-10-22 | The spread candidates are the least-bad put spread with its odds, listed by symbol; call spreads off; income candidates at 16 delta. A ruleset without the new keys keeps the old construction. |
| manage | 2026-11-08 | Every leg is solved and priced at the name's dividend yield (#926). A high-yield fund's long-dated call that was unpriceable is priced again, IVs move by under a point on low-yield names, and one calendar's price-stop band moved by about 1%. A name that pays nothing is unchanged. Evaluations stored before this carry no yield and keep their numbers; compare manage's greeks, payoff bands and stops across it with care. |
| manage | 2026-10-20 | The theta band, the concentration rule's theta share and the candidates' theta ceiling read theta without legs two days or less from expiry. |
| Screeners | 2026-10-27 | No list shows a name whose monthly options are not confirmed liquid (`thin`): about 166 names, against 484. |
| core | pending | Per-symbol paper slippage is built but ships empty; filling the SPX entry is a boundary that waits for 30 priced live fills. |

### What's new

- **Live trading, narrower and safer.**
  - Live orders exist only in MEIC, flies and bwb. Earnings and the desk are removed.
  - Every live loop arms from the command line with the disclaimer and a literal YES (#984).
  - MEIC: a stop-limit that can rest at the broker (#796), a verdict that says whether live may arm
    (#815), entries journalled every tick (#516), a quote outage no longer stops management of what is
    held (#795), alerts that name the held condor (#820), and kill criteria on live entries (#541).
  - Hardening across the live path: the manual order door obeys the halt, orders carry an identifier, a
    timed-out submit is recovered from the broker, every broker call has a deadline, unreadable
    ledgers are reported, and the streamer spends subscriptions from a per-minute budget.
- **Retired modules ship off.** flies, curve, pmcc and bwb are off in the example configs, with their
  retirement dates and a status line at the top of each README. Live → today folds a retired, empty loop
  to one line.
- **External reports** replace QuikOptions (#983, #985). A script writes
  `~/.cherrypick/data/external/<id>.json`; the console shows it as its own tab and can post it to Discord.
  `run.py external-check` says what the console will draw. Columns, cells and rows take presentation hints
  (formats, tones, stacked bars). A `.claude/skills/external-report` skill writes one for you.
- **The console.**
  - Help page with the user guide and FAQ, searchable (#990, #992); a version chip that announces a new
    release (#989); a page layout built around questions, with cards and lightboxes on Live, the
    strategy pages, Screeners and Backtest.
  - Live → positions as cards with each trade's order chain; Live → portfolio laid out by question, with
    a risk matrix at three horizons, limits, bands over time and a premium pace; wheel, pmcc and
    time-spreads tabs; live marks on the account's positions.
  - Backtest: studies board with a Report tab per study, run cards with health checks and a scorecard,
    the research queue as cards, slices under Compare.
  - A `/weeklyput` page, a fair-value tab, a credit-spread tab, and a minimum-premium slider on the
    income lists.
  - Release fixes (#993): the Config lock banner prints the halt flag's path from `~`, the weekly put card
    on Experiments, and GEX leading with volume (a display default only).
- **manage reads the whole account.** Wheel cycles and PMCCs by the standard definition, with rules; a
  ledger of what each name has earned; trades given a stable ID for life; income trades that fit the
  account's buying power; earnings and expiry exposure added up; a collar as a third hedge; a
  time-spread scanner (no tested edge); a minute re-mark between half-hourly snapshots; a snapshot after
  each fill; and an evaluation that notes when it is older than the code. Advice only, still no order
  path.
- **Weekly put (experimental, off).** Research item 57's unhedged index put in paper (SPX, SPY, QQQ, IWM;
  item 69), with its own schema, review reader and console page.
- **Screeners without the vendor.** The suite's own short-put, covered-call and credit-spread lists, a
  liquidity verdict with a `thin` class, a fair-value model, a corporate-action tripwire against Yahoo's
  adjusted closes, and dollar volume from the broker's candles.
- **A research queue.** Long studies are rows, added with `run.py queue-add` and run by the supervisor's
  `research-queue` job, with priorities, dependencies, progress, ETA and a studies board. The backtest
  package gained end-of-day and intraday chain fetches, resumable runs and a STAGE client.
- **Docs.** Current version only: open work is an issue, research is in `research/`, the rest is at the tag
  `archive/docs-2026-11`. A guide page for each part of the console, with screenshots from a demo home
  (not shipped) and a CI check that keeps them true.
- **Upgrading between releases.** `run.py upgrade` (with `upgrade.cmd`, `upgrade.ps1` and `upgrade.sh`
  at the root, and `/cherrypick-upgrade` in Claude Code) moves an install to a newer release, and
  `--rollback` moves it back. It checks first, shows the target's notes from `release-manifest.json`,
  backs up, fetches, stops, runs the target's installer in a new upgrade mode, runs `run.py migrations`
  and checks the result. It refuses while a live loop is armed or a live position is open, outside the
  quiet window, on low disk, on a dirty or development checkout, and while the opt-in Windows service is
  on (stop the service first). `docs/guide/upgrading.md` has the detail. The release workflow now refuses
  a tag unless `release/manifest.json` has a well-formed entry for it.
- **The morning and evening data runs are two pipelines.** `morning-data` (05:30) and `evening-data`
  (18:35) replace 28 jobs. Each runs its steps in order, each as its own subprocess with the same
  switch and trading-day gate as before, and `run.py pipeline` runs one by hand. A failed landing or
  security-list step now stops the steps that read it, where they used to run on yesterday's store; a
  failed fetch stops nothing and is named. `run.py status` shows the two rows, and each step's result
  is in `state/pipeline.<name>.json`.
- **manage evaluates when the account changes.** A fill waits 60 seconds for the next one (so a roll's
  legs are one run) and no run starts within 2 minutes of the last. Every 5 minutes the broker's held
  legs are compared with the stored snapshot, which catches an assignment, exercise or transfer no
  order announces. Scheduled runs post only when the list of trades needing action changes
  (`manage.on_change`; `{"enabled": false}` restores the old behaviour).
- **Operations.** Eighteen job rows merged into multi-time jobs; an always-on `account-alerts` service;
  throttling opt-out for the supervisor and its jobs; dated reminders that close when their condition
  holds; CI installs uv from PyPI with retries.

- **A user guide, in the console.** The Help page (`/help`) serves `docs/guide/`:
  - a page for each part of the console;
  - topic guides: your first week, reading results honestly, income trades from start to finish,
    going live safely, experimenting with arms, research and backtests, an always-on machine,
    troubleshooting, privacy and upgrading;
  - an FAQ.

  Card help boxes and module Help tabs link to the section that explains them.

### In detail

Grouped by area. Dates are the session a change began to bind.

**Earnings**

- **Paper-only (#980).** See Upgrading.
- **Changed (2026-10-28, a declared measurement boundary): earnings opens only on names the liquidity
  verdict calls tradable.** Every entry path refuses any other name as an execution-stage `not_tradable`
  drop. An unjudged name is refused; a missing or stale verdict refuses everything as
  `liquidity_verdict_unavailable`. About half the recent entries were on names now refused.
- **2026-10-30:** entry retry, entries opening as the scan goes, a held close taken after 30 minutes
  (#664). **2026-11-05:** early-close clock, malformed orders refused, leg quotes recorded, old exits
  re-priced (#866-#869). **2026-11-06:** the PEAD gate fires, the expected move is the report's own move,
  `assignment_risk`, the 15:20 expiry close and the twin that opens beside each control (#874-#879).
- **Fixed:** exit slippage charged twice on natural-side fills (to 2026-10-23, #519); exits priced past the
  structure's payoff (to 2026-10-26, #520); the console stated a position total as per share and counted
  quantity twice (#439, #445). No stored number changes for one-contract trades.
- Written kill criteria on live opening orders (#558, #579) and a per-day arm (#881) came and went with
  live trading.

**MEIC and the live path**

- Live path: see What's new and the table above. Also: a computed delta (#601), the stabilization gate
  (#728, with review fixes: stale bars, IV-mode arms, XSP thresholds), an equalize imbalance cap (#503),
  the session windows in the docs matching the code (#436), and a MEIC backtest that records which
  sessions ran on intraday VIX (#444).
- **Hardening with no measurement change (2026-10-26/27):** the manual live door obeys the halt, bwb and
  flies manual orders carry an identifier, bwb settles under the live tick's lock, an unreadable MEIC ledger raises CRITICAL, `tt.py` masks account numbers, the
  narrative agents get an allow-list of tools, and the EOD narrative withholds a recommendation carrying
  a figure or account. Console: refuses a cross-site WebSocket, validates ids, bounds prefs. CI runs
  with a read-only token. Telemetry write failures are counted, gex never commits a regime row without
  its profile rows, and job heartbeats are written atomically.
- **Fixed:** a second process could write the shared stream cache; the streaming engine now refuses
  the shared cache to anything but the producer.
- Switching a live loop off mid-session no longer hides the tab showing what it still holds (#439).

**manage**

- **Wheel and PMCC.** Cycle linking and accounting, rules (`wheel.take_profit`, `wheel.tested`,
  `wheel.next_leg`, `pmcc.width`, `pmcc.assignment`, `pmcc.roll_long`), a PMCC recognised by the long's
  delta at entry (0.80 or more) or by `positions.<SYMBOL>.intent`, a short sold in its own order or past
  60 days joined to its long rather than judged naked, rolls priced on the next five quoted expiries
  (snapshot v14), the skate bands carried (v12), entry-day closes (v13), the day's orders (v10) and the
  broker's greeks (v9). The snapshot is v15 now. On every stored session no recommendation changed
  except where noted.
- **Strategy guide shapes** (straddle, strangle, lizards, zebras, broken-heart butterfly) named, with
  their own targets; `legs.long_vertical`, `roll.fly_off`, `roll.untested_side` (off by default after a
  declared local study), and `credit.stop` at confidence 50.
- **Income.** Short put and covered call at 16 delta, on the afternoon chains' own switch
  (`market_report.income`); the stress move read from the history store (52 more tradable names);
  candidates that fit the account, badged when the fund resets daily or the name is held on its expiry,
  with exposure added up (`portfolio.earnings_exposure`, `portfolio.expiry_concentration`); spread
  candidates as the least-bad put spread (see the table).
- **Boundaries that change numbers:** the theta band without expiring legs (`portfolio.theta.front_dte`,
  default 2) and the delta band scaled by it; dividend-yield pricing (#926, 2026-11-08); payoff figures to
  the cent; a diagonal judged on its modelled best case (#923); a calendar's stop on the stock's price
  (#899).
- **Premium pace** against a target of 0.2% of net liq a day (`portfolio.theta.target_pct_nlv`) from the
  account's whole trade history (`get_transactions --account_wide`, GET only); bands over time; a
  vol-expansion band.
- Manage notes it when its snapshot is older than the code (watchdog `manage.snapshot_version`).

**Screeners and technicals**

- Our own short-put, covered-call and credit-spread lists, replacing the vendor's, which froze with its
  subscription on 2026-10-24. The credit-spread list is untested.
- **Liquidity verdict gains `thin` (2026-10-27).** No screener lists a name whose options the user would
  not trade. About 166 names pass, against 484.
- **Fixed (2026-10-27):** AZN's ADR ratio change and a 2015 split, the HON, CME and ITUB dividend
  disagreements (Yahoo's amount decides a pair more than 1.2x apart), and GFI's part payouts. **Added:**
  `check-adjust`, with job `technicals-yahoo-adjusted` (19:45 ET) and `technicals-adjust-check` (06:55 ET).
- Skate: judged against N(d2) rather than delta, a band that moves with the price, the reference funds
  banded, a both-sides marker and a forward record judged by a test declared in advance.
- Universe and sectors no longer depend on the vendor; technicals keeps the Dolt server without the
  earnings module; analyst-revision symbols that fail are retried once. Screeners tabs that need Dolt
  say so.
- The fidelity tab was removed.
- Fair value: an unlevered DCF (`core.valuation`), its column and tab, a reverse DCF, and a version 2 in
  `packages/fundamentals` that failed both halves of its test.

**Orchestrator and operations**

- **Pipelines.** The morning order is: Dolt pull, index bars, SEC facts and Damodaran (independent
  fetches; a failure keeps the last good file), then the landing steps (`technicals-land`, `-history`,
  `-fundamentals`, `fundamentals-land`, which stop the run), then the report, stage lists, fair value,
  screeners, board, skate, earnings record and adjust check (independent). The evening order is:
  dollar volume, sectors, earnings times and the evening market-files fetch, then the security list and
  IV ranks (stop the run), liquidity, the earnings calendar's first pass (stops the run, since the
  strikes read it), then strikes, dividends, Yahoo adjusted and revisions. A step waits for its own
  configured time (the `*_at` keys mean earliest start) and for the step before it. `earnings-moves`,
  `universe-daily`, `backtest-fetch`, `technicals-income-lists`, `report-income-chains`,
  `vol-curve-bars`, `pmcc-earnings-refresh`, the 07:45 `market-files` retry and the 23:40 calendar pass
  stay jobs of their own. Old per-step registry rows are pruned when the supervisor restarts.
- **manage's `on_change` block** (`enabled`, `settle_seconds` 60, `min_gap_seconds` 120,
  `positions_check_seconds` 300). Every full run records its `reason` in `manage.last.json`, and the
  post says what started the run. Evaluations are still one file per session, so the console's bands
  over time, the attention list and `reevaluate` see no extra rows.

- Research queue, job progress files (`core.progress`), arm cards for every paper module, dated
  reminders, stable trade IDs, execution record, per-arm kill criteria, a studies board and a study
  report.
- Windows power-throttling opt-out (2026-11-03), the console's server and module CLIs included; the
  Dolt server unthrottled.
- `core.streamer` spends subscriptions from a per-minute token bucket (burst 500, 1,500 a minute) after
  the broker cut connections for a rate it counts over about a minute.
- Config backup skips every browser profile.

**Backtest and research**

- End-of-day chain fetch, event-driven and resumable; run cards with an account read, a compounded lens,
  a sizing check and a scorecard; resumable interrupted runs; a MEIC gates-off oracle; slices by weekday
  and event day with a random-day placebo.
- Research items 14 to 69 were declared, run and recorded in `research/`; results stay under
  `~/.cherrypick` by the data licence.

**Docs and CI**

- Docs describe the current version only (#1001, #1004). The root `CLAUDE.md` and each package's were
  shortened, with detail moved to `docs/`.
- `tools/check_personal.py` fails CI on owner-attribution phrases and unmasked account numbers;
  `tools/check_guide.py` checks the guide; `tools/check_screenshots_fresh.py` warns on stale shots; and
  `tools/check_changelog_touched.py` warns when a change to shipped code leaves this file alone (label
  `no-changelog` skips it).
- The demo home (a fake suite home for screenshots, `docs/demo-home.md`) is docs only and not shipped.
- CI installs a pinned uv from PyPI with three tries (#1003).

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
  - **pmcc:** a published held-long design as its base trade, extended to single stocks and SMH with an
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
