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

- **The console says more about what a page is showing.** Live > today now shows a dry run, live or unknown chip on
  MEIC's and the ladder's cards, armed or not, with a ? that explains a dry run; the value is each loop's own status, and
  an unreadable status reads unknown, never dry run. The MEIC summary calls an open trade on a past day "still open"
  instead of "settling at the close". The Weekly put page says "half day, not traded" on an early close. The skate tab
  marks a name whose best call and put both pass, and the income lists get a Hide leveraged funds switch that starts off.
  Nothing to do.
- **A rebuilt past session reports what it carried past its close, from each ledger's own dates.**
  `core.ledgers.OPEN_AS_OF_READERS` answers "which positions were held at the close of session S" for
  all nine multi-day schemas, so review's `carried_overnight` and its capital at risk no longer come
  from today's book (which dropped every position closed since) or from a stored copy. The stored-block
  stopgap and its `carried_overnight_kept_from` stamp are gone. A rebuild of a past session can change
  its carried figures, correcting numbers that were wrong. Nothing to do.
- **`account --set` and `--clear` refuse while a live loop is armed or holds a book.** The designated
  account cannot change under a live loop that is armed for today, or under one that holds a position or
  a working order (MEIC's main account manages its book even when it is not armed). The refusal is in
  the orchestrator's account code, so the console's account step and `connect` get it too; there is no
  override, and a status that cannot be read refuses. The suite-wide account is checked against every
  module that inherits it, a second login's against that login alone. A retired module (switched off in
  the suite config and in its own live switch) still refuses when it is armed or holds something, but a
  status of its that cannot be read is only a warning in the result. The command names the module and
  the reason, and `account` now exits 1 on any refusal it prints (an unresolvable selector included).
  The ladder's `--status` now reports `open_lots`, `working_orders` and `orphaned_orders`. Nothing to do.

- **`upgrade` checks the release ZIP against a SHA-256 before it unzips, and four small security gaps close.**
  The release workflow adds the digest of the tag's source ZIP to the published manifest; a ZIP upgrade that downloads something else stops before unzipping, and a manifest with no checksum warns and goes on if you confirm. A change of the live arm now journals `live_arm_change_attempted` first and `live_arm_changed` only once the config write landed (`live_arm_change_failed` otherwise). The demo console drops inherited per-scope data and log directory overrides, the replay token mask shows two characters instead of eight, and CI pins `pnpm/action-setup` to a commit. Nothing to do.
- **The watchdog's settlement checks follow an early close.** The paper and live settlement checks count
  their grace from the day's own close (13:00 on a half day, not 16:00), and the live-positions check drops
  the day's expired contracts from 13:00, so an unsettled book is flagged three hours sooner on 2026-12-24
  and settled 0DTE legs no longer read as a mismatch until 16:00. `weeklyput`'s `--force` skips the window
  but never the half-day refusal. Nothing to do.
- **A chain fetch no longer asks the vendor for a day that is not a trading session, and the store's
  quality report lists any such day it holds.** The vendor answers a Saturday with a placeholder market
  (every option bid 0.05, ask 99.00), which a study read as a session. `fetch-chains` counts these
  requests as `not_trading` and sends none; the report gains `off_calendar` per symbol. Item 109's study
  skips those sessions. Nothing to do.
- **`queue-list` says why a lane is idle.** It reports each lane's running row and, when nothing runs,
  what the rest wait behind. The local lane counts only rows that are not waiting on a queued fetch, so
  "idle" there means no local work exists. A queued row also names where its chain of `after` rows ends.
  Nothing to do.
- **The study kit's `Inputs.sessions` returns trading days only.** A stored non-trading day (the
  Saturday placeholder under every name, holiday sessions under SPXW) is no longer a session to a study
  on the kit, and `chain` reads such a day as none. Items 57, 74, 77 and 78 read such days and carry a
  correction entry; items 59, 63, 64, 70, 86 and 101 reproduce identically. Nothing to do.
- **MEIC's live guards are checked by the guard mutants.** Eleven `meic-*` mutants break the master
  switch, readiness, the arm record, the halt, the daily-loss breaker, the kill criteria, the orphan
  sweep, the per-day cap, the dry-run default, and the manual door's refusal of an open and pass for a
  close; each guard's tests must fail under its mutant. New tests pin that the tick reads the halt flag
  and the breaker, and that every order door in `tt` reads the master switch. A change to meic now runs
  the `guards` CI job. Nothing to do.
- **The ladder's live guards are checked by the guard mutants too.** Nine `ladder-*` mutants break the
  dry-run default, the adapter's refusals (gates unmet, a buy under the halt flag), the tick's gate
  check, the arm gate, half days, the drawdown breaker, the caps and the orphan sweep; each guard's
  tests must fail under its mutant. Nothing to do.

## v0.14.1 — 2026-11-26 — held queue rows and corrupt windows
Fixes to v0.14.0: one that stopped the research queue behind a fetch that could never finish, one
that made a first intent answer work on an older config, and a reminder so a held row is not missed.
Nothing to do by hand.

- **Fixed: a repeat fetch over windows the vendor serves corrupt held the research queue.** For a
  few windows the vendor answers every request with a cut-off or wrong-width reply. They were
  recorded as a plain `vendor_error`, so a repeat pass over them read as "nothing fetched", failed,
  and held every row after it. Re-queuing it failed the same way, and nothing said so after the
  first notice.
  Those two errors are now their own settled reason, `corrupt_data`: the repeat pass finishes done,
  with the warning that nothing is left the vendor will serve. A `vendor_error` already recorded with
  that text counts as it, so no store needs refetching.
- **Added: a held research row reminds you each day.** The first notice is unchanged. A row still
  held a day later gets "Research task still held", with how long and why, and another each day until
  the prerequisite is re-queued. The older queue gains a `nudged_at` column on its own.
- **Fixed: a first intent answer works on an older `manage.json` under a relocated home.** An answer
  on a `manage.json` that has no `trades` block adds the block from manage's shipped example, which is
  found beside the code whatever `CHERRYPICK_HOME` says.
- **Added: the guide shows a trade's Intent tab** (an image of the unanswered tab).

## v0.14.0 — 2026-11-26 — automations and the live arm
Three changes lead. The console can now arm a live loop for the
day from the Live page, behind the module's own checks and a typed `YES`; it ships off
(`live_arming` in `console.json`). MEIC's live loop ships in dry run: an armed tick preflights each new
entry against the real account and sends none until you set `live.dry_run` to `false`. And v0.13.0's
`ensemble` became `automations`, a package with its own console page, a Builder to design, backtest and
group them, and a nightly check of paper against the backtest. The ladder gained a live path that also
ships in dry run and off, and the Live page was reorganized around two summary cards. Calendars,
contango and four automations were retired after research items 38, 39 and 75 failed them. manage leaves
a far out-of-the-money time-spread short to expire, reads the cost-strike call and the dispersion of the
book, and evaluates on Sunday evenings. A trade's intent (a put a wheel or not, a diagonal a PMCC or not)
is now asked and answered per trade, on the position. This is an ordinary upgrade from v0.13.0 with one thing to
decide: a live MEIC config that never named `live.dry_run` now sends no entries.

### Upgrading

- **From v0.13.0:** `python packages/orchestrator/run.py upgrade` (or `upgrade.cmd` / `./upgrade.sh`).
  It refuses while a live loop is armed or holds a position, as every upgrade does. Afterwards run
  `python run.py supervise --restart` (the research queue has a second job and manage a Sunday one), restart
  any resident job, and restart the console to load its new server code. Its one data migration finds
  nothing to do on a v0.13.0 home (below).
- **MEIC's live loop is in dry run until you say otherwise.** `live.dry_run` is `true` unless it is the
  literal `false`, and a missing key means `true`. A live config that never named the key now preflights
  each new entry and sends none. Set `live.dry_run` to `false` by hand, deliberately, to send entries
  again; the Config page cannot change it. A held book is still confirmed, stopped, force-closed and
  settled live either way.
- **The console's arm switches are off.** Nothing about arming changes until you set `"live_arming":
  true` in `~/.cherrypick/config/console.json` (new `packages/console/config.example.json` lists every key
  that file takes) and restart the console; see
  [going-live](docs/guide/going-live.md#from-the-console-the-live-pages-switches). The slash commands and
  `live_loop --arm` work as before.
- **`packages/ensemble` is now `packages/automations`** (`cherrypick-automations`, import
  `cherrypick.automations`). v0.13.0 shipped the engine alone under the old name, with nothing switched
  on, so an install of v0.13.0 has no data under it. `cherrypick.ensemble` no longer imports: a script of
  your own that imported it needs the new name. A checkout updated by hand needs its packages reinstalled
  (`scripts/dev-install.ps1` or `.sh`; `upgrade` does it).
- **A checkout that ran `main` between the releases** may hold automations data under the old name
  (`config/ensemble.json`, `data/ensemble/`, `ensemble_` tables, `modules.ensemble`, the trade schema
  `ensemble_trade` and the `ensemble` group). Nothing reads those names any more, and the paper loop
  refuses a ledger that still has `ensemble_` tables. Stop the suite and run `python
  packages/orchestrator/run.py migrations --apply` before starting it again; `upgrade` runs it for you.
  The migration (`v0.14.0-01-automations-rename`) moves the file and the folder, renames the tables, the
  module block and the schema, renames the `ensemble` group to `default` and updates the schema in your
  research register. It stops, changing nothing, if `config.json` holds both module blocks,
  `automations.json` already has a `default` group, or both table sets hold rows. `ENSEMBLE_CONFIG` and
  `ENSEMBLE_DB_PATH` are no longer read; use `AUTOMATIONS_CONFIG` and `AUTOMATIONS_DB_PATH`.
- **Calendars, contango and four automations are retired** (2026-11-21). Both modules ship off with a
  retired date on each arm. A machine that had calendars on keeps its open positions running to their own
  exits; switch the module off once it is flat. `iron-fly-1000`, `iron-fly-1dte-1330`,
  `gld-call-spread-20d` and `orb-put-spread` are switched off in the shipped config; a config of your own
  keeps what it says.
- **The research queue runs two lanes.** The `research-queue-vendor` job replaces `research-queue` and
  `research-queue-local` is new; the supervisor restart above starts both. The register of studies and
  each Result now live under `~/.cherrypick/data/research/`, not in the repo.
- **manage has new readings and one changed rule,** all with optional config keys and defaults that
  apply when a key is absent: `expiry.time_spread_hold_max_delta` (0.10), `attention.cost_call`,
  `portfolio.dispersion`, and a Sunday run at `manage.sunday_at` (18:15 ET; `null` turns it off alone).
  Its verdicts for a calendar or diagonal near expiry can differ (below).
- **A trade's intent is now per trade.** Nothing is required: until you answer a put or a diagonal that
  manage asks about, it is judged by the stricter rules and says so. Answer on Live › positions (the card's
  strip, or the opened trade's Intent tab); the answer is saved at `trades.<ID>.intent` in `manage.json`
  (a config without a `trades` block gets one on the first answer) and read at the next evaluation. A name's
  intent stays readable and is offered as the suggestion. `wheel.tested` has two optional keys,
  `review_beyond_days` (60) and `review_min_extrinsic` (0.10). The supervisor restart above loads the new
  `intent_set` operation; the console restart loads the pages.
- **A developer checkout runs a pre-commit check.** `scripts/dev-install.ps1` or `.sh` now sets
  `git config core.hooksPath tools/hooks`, whose pre-commit hook runs `tools/check_personal.py
  --staged`: a commit that holds a home directory, a path mangled from one, or this machine's user
  name is refused. Rerun dev-install, or set that one config line, to get it.
- **Measurement boundaries** moved between 2026-11-18 and 2026-11-27 (table in the detail). Do not pool
  a series across them.
- **Known issues:** a second security pass left follow-ups, tracked in issue #1465.

### What's new

**Live trading and arming**

- **Arm a live loop from the Live page.** With `live_arming` on, MEIC's main account and the ladder get an
  arm-for-today switch: the module's own readiness, the disclaimer in full and a typed `YES`, then the
  module's own `--arm`. The console never clears the halt flag and still places no order.
- **Every live arm and disarm is journaled** (`arm_events`), with who asked and why, for MEIC, flies, bwb
  and the ladder; a loop's own self-disarm names itself.
- **Choose MEIC's live arm on Config.** A `live` tab offers the arm each live loop runs; for MEIC's main
  login the change is its own guarded operation, typed `CHANGE ARM`, only when the loop is flat and settled.
- **MEIC's live loop ships in dry run** (`live.dry_run`), and **a live trade records the arm live ran**
  (`ic_trades.live_arm`).
- **A paper twin for each arm live may run** (`shadow_of`), so live can be measured per arm, and the
  execution page's paper-live gap follows the arm live ran.
- **The ladder has live gates, a daily arm and an order path that ships in dry run and off,** buys only with
  settled cash in a cash account, and its page has a Paper/Live selector.
- **A halted dot on the rail** shows beside Live while the halt flag is up.

**The Live page**

- **Live today is two summary cards:** the loops' day, with an attention chip that opens a list and links to
  each loop, and the broker positions' action items. A disabled loop with nothing open and an empty by-hand
  card no longer take a line. The Live meic tab is gone; Live today lists its orders and the MEIC page holds
  the rest. Each loop's day is read by arm and symbol, and a live condor names the arm it ran from its
  filled attempt.

**MEIC**

- **No new entries on a half day** (an NYSE 13:00 close), paper and live; the next is 2026-11-27.
- **A page for each arm** (`/meic/arm/<name>`), a table comparing arms on the performance tab, the sessions
  ahead and today's conditions in an arm's regime record, and an arm against its backtest.

**manage**

- **A far out-of-the-money short of a calendar or diagonal is left to expire** (delta 0.10 or less, with a
  longer long of the same right at or beyond its strike), and the 80% capture buy-back and the roll that
  follows it skip it.
- **The cost-strike call:** a wheel cycle holding assigned shares with no call written is flagged when the
  call at its effective cost bids 2% of the stock's price or more. Shown, never placed.
- **Leverage as times the account, and a dispersion line:** the delta band reads the same at any account
  size, and every name moved against its own position at once is a REVIEW over 10% of net liquidating value.
- **A Sunday evaluation** (`manage-eval-sunday`, 18:15 ET) reads Monday's list the night before.
- **A trade's intent is per trade, and it is asked on the position.** A naked short put is a wheel or not; a
  call diagonal the PMCC view cannot decide is a PMCC or just a diagonal. manage detects the shape from the
  legs and asks only where the legs cannot say, with a suggestion and why (the name's completed wheel cycles,
  the entry delta), never applied without your answer. Until you answer, the trade is judged by the
  stricter rules and its reason line says "no intent set"; each unanswered trade is one needs-a-look item.
  A name's `pmcc` no longer shows as the intent of a put or hides it from the question.
  **A wheel put keeps no stop and no breached close, so an answer is not assumed.** Measurement boundary,
  2026-11-25.
- **A tested wheel put with time left is a review, not a roll or a close. Measurement boundary, 2026-11-25.**
  `wheel.tested` on a short put you wheel that is at or through its strike, has more than 60 days left
  (`wheel.tested.review_beyond_days`, the horizon the snapshot reads roll-out chains inside) and at least
  0.10 a share of extrinsic value (`review_min_extrinsic`, a broker's rule of thumb for when early exercise
  becomes likely: practice, untested) now says REVIEW. There is no roll to price that far out and assignment
  is unlikely while time value remains. Its reason line gives how far through the strike it is, its extrinsic
  value and days, what holding to expiry at today's price makes on the trade's net credits, and the
  assignment against the wheel breakeven, each figure named for its basis. Inside 60 days, or under the
  floor, it is the roll, close or assignment it was, so a deep put is never left unwatched;
  `risk.oversize` still fires on its own. Nothing to do.

**Automations and the Builder**

- **The Builder** designs an automation in a form or a wizard, checks it, saves it off, runs a backtest on our
  own data first, and reads how the run holds up; **Find ideas** and **Try settings** search a run for entry
  filters and exit settings and test each survivor once on held-out sessions. An underlying can be added to
  the full set from the page.
- **Automations have pages, groups and a reference backtest:** each automation's own page, the groups tab
  (switch, start, allocation, cap, shares), a pinned Builder run as an automation's backtest reference,
  and a read of whether it is working against that backtest.
- **A nightly replay checks paper against the backtest,** and automations ignore a quote that puts a
  structure's price outside what it can be worth.
- **More to build with:** signals (VWAP, relative volume, trend, levels, a developing daily bar), event
  labels, strikes by premium or GEX, more exits, sizing by allocation and streak, and a versioned signal
  library.

**Console**

- **Answer a trade's intent where the trade is.** An undecided put or call diagonal carries an amber
  question on its card (one tap, with Undo), and a collapsed row in the table view carries an `intent?`
  marker; the opened trade's **Intent** tab shows both verdicts side by side to change an answer later;
  the PMCC card has the same choice on its intent row, and the pmcc page lists what still needs an answer.
  Config › Intents is now an audit list: names stay editable there, trades link to their position and can be
  cleared. One new write, narrow (below); the console still places no order.
- **The scorecard on Compare,** one row per arm and automation with its research verdict, its forward
  check and the first thing worth a look; a **changes** tab where a modification is decided; and
  backtest arms and the risk held at once on the portfolio tab.
- **The ladder has a page and the rail is in reading order;** the console hides what cannot support a
  decision yet and shows plain card titles.
- **Charts:** Market Profile on the intraday chart, a profile view across sessions, and the prior session's
  profile levels in the morning pack.

**Research, backtest and data**

- **The research queue runs fetches and scripts in two lanes,** and a finished study records its own verdict
  on this machine in one command (`tools/record_verdict.py`).
- **The backtest fetch runs on the vendor's Options Value tier,** a full set of seven underlyings was
  scripted, and at the licence's end the results are kept through its 30 days, reviewed once in the last week, then deleted.
- **A source registry** declares every data store with its span and says which covers a request.
- **The morning pack's SPX risk reversal comes from the broker** now that the Cboe chain refuses scripts.
- **New recordings:** one-minute profile bars (`profile-bars`) and gold bars (`gold-bars`).
- **`core.quotes` and `core.preflight`** price an idea by hand from the stream cache and the broker's dry
  run instead of a scratch script.

**Retired**

- **Calendars, contango and four automations** (above). Flies, bwb, pmcc and curve stay retired and off;
  the placeholder `live` gate is gone from the calendars, pmcc and curve example configs, and the
  retired flies and bwb switches (`/modules/flies/enabled`, `/modules/bwb/enabled`) cannot be flipped from
  the Config page.

### In detail

**Measurement boundaries.** A series breaks at each of these dates.

| Area | Date | What it splits |
|---|---|---|
| Automations (paper and backtest) | 2026-11-18 | A quote that puts a structure's mid outside what it can be worth is no longer taken (`unrealistic_price`), and a wing beyond the recorded strikes is read as a range, not as unpriced. Earlier runs that closed on such a mark overstated their result; backtests with intraday exits change, mostly on trending days. |
| Morning pack | 2026-11-19 | The 30-day 25-delta SPX risk reversal is read from the broker's SPXW greeks, not Cboe's chain. The two IVs were never compared on one session, so read a step across 11-19 with that in mind. |
| Calendars, contango, automations | 2026-11-21 | Calendars and contango retired (items 38 and 39); `iron-fly-1000`, `iron-fly-1dte-1330`, `gld-call-spread-20d` and `orb-put-spread` switched off (item 75). |
| manage | 2026-11-21 | The cost-strike call reading (`attention.cost_call`) joins the needs-a-look list. A reading; nothing is placed or posted for it. |
| manage | 2026-11-22 | `portfolio.dispersion` can raise a REVIEW (over 10% of net liquidating value on a two-sigma one-day move of every name against its position; graded ours), and beta-weighted delta is stated in times the account. A Sunday evaluation runs on Friday's quotes. |
| manage | 2026-11-25 | A tested wheel put (at or through its strike) with more than 60 days left and at least 0.10 a share of extrinsic value is a REVIEW, not a roll, close or assignment (`wheel.tested.review_beyond_days`, `review_min_extrinsic`; the 0.10 is a broker's rule of thumb, practice, untested). Wheel-put verdicts from this date are not comparable with earlier ones. |
| manage | 2026-11-25 | A trade's intent is per trade. A name's `pmcc` no longer marks or hides a naked put (it is asked, and judged by the stricter rules until answered), and an answered wheel put takes the wheel rules (no stop, no breached close). Nothing changes for a trade until you answer it; from your answer its verdicts are not comparable with before. |
| manage | 2026-11-24 | A calendar's or diagonal's expiring short at or under 0.10 delta, with a long of the same right outliving it at or beyond its strike, is left to expire, not closed at three days, bought back at 80% captured or rolled. The 0.10 delta is ours; broker guides say only "well out of the money". |
| MEIC live | 2026-11-24 | `live.dry_run` ships true: an armed tick sends no entry. Live results from here on come only after it is set false. |
| MEIC | 2026-11-24 | The paper twins and the live-shadow retirement: a machine that runs `scripts/boundary_2026_11_24.py --apply` journals `arm_added` for each twin and `arm_retired` for `live-shadow`; from then the paper-live gap follows the arm live ran. Nothing changes until it runs. |
| MEIC | 2026-11-27 | No new entries on a half day, paper and live. The current era from 2026-11-03 held no half day, so no session on either side was traded under the other rule and no break was journaled. |

**Live arming, the arm journal and Config.**

- The console's switch runs the module's own `live_loop --arm --confirm YES` (or `--disarm`), never
  `--install-task`. The server refuses an arm unless the loop is ready and not halted when the `YES`
  arrives, and when anything the dialog showed has changed; each confirmation is used once and expires
  after ten minutes. A second broker login's MEIC account shows a locked switch with its blocker; flies and
  bwb show none.
- `arm_events` is `data/live/arm_events.jsonl`, append-only, with the actor and a reason slug
  (`record_not_today`, `past_disarm_time`). A ladder disarm past `disarm_at` happens even when a gate is
  unmet.
- `set_live_arm` re-reads the config just before it writes and refuses with code `changed` if it is not as
  it was read, so a hand edit made while the status was read is never reverted by stale text. `not_flat`
  counts a leftover entry intent: an entry sent but not yet reconciled means an order may be at the broker.
- The orchestrator's `configedit.GUARDED` now covers `/modules/flies/enabled` and `/modules/bwb/enabled`.
  Those switches lift the "retired, refuses to arm while off" check.
- `scripts/demo_home.py` refuses a folder that is not a demo home: the real home wherever it is
  (including `CHERRYPICK_HOME`), anything inside or above it, the user's home, the repo and a drive root.
  The demo home no longer includes calendars and contango.
- The guide's console images were retaken for this release, and dated history notes were taken out of the
  docs, which describe the current version only.

**manage.**

- The snapshot gained the cost call's monthlies (`cost_call` in the orchestrator's manage block caps the
  calls and seconds) and the Sunday run is its own job because its day gate differs from the trading-day
  one (`JobSpec.weekdays`). Config keys: `expiry.time_spread_hold_max_delta`, `attention.cost_call`,
  `portfolio.dispersion`, `manage.sunday_at`, `manage.cost_call`.
- The README cites what a literature pass found for each portfolio band and names the research items that
  test them (108, 109).
- A trade carries one `intent_ask` block (the intents its shape allows, whether it is asked, manage's
  suggestion with its evidence, the verdict each answer would give, and the config key an answer is
  written at). The kind is decided from the legs: a naked short put asks wheel or not, a call diagonal the
  PMCC view cannot decide (entry delta under the floor or unknown, nothing set on the long's contract)
  asks PMCC or diagonal. A name's `pmcc` or `diagonal` no longer sets or hides the intent of a trade that is
  not a call diagonal, and `wheel` and `no_wheel` apply only to a naked short put. An unanswered trade is
  judged by the stricter rules and its reason line says so; it raises one needs-a-look item
  (`attention.intent`). `wheel.tested` gained `review_beyond_days` (60) and `review_min_extrinsic` (0.10).
  `manage.json`'s `trades` block ships empty in the example and the engine reads it; the console now writes it.

**Console.**

- The intent surfaces: the strip on a position (card or table row), the Intent tab in an opened trade, the
  PMCC card's intent row and the pmcc needs-a-look section all write through one narrow route,
  `POST /api/config/intent`, to the orchestrator's `intent_set` operation: `trades.<key>.intent` in
  `manage.json` and no other byte, through the config editor with its backup, the key checked by shape
  (a stable trade ID, or a call's long contract), the replaced value returned for Undo. It is advice
  config: it places no order and touches no live gate. Every control on these surfaces is 44px on a tablet
  or phone, and the strip's answers stack whenever the strip is narrow.

- The scorecard (`run.py scorecard`) joins the research verdict, forward check, change against control,
  sample, net and drawdown; a forward state against a licensed reference is withheld while the results
  gate is closed.
- `core.changes` lists every modification under test in one module, each with its gap and interval; a
  variant that enters more is not read as better for its size, and is "too early" under ten paired sessions.
- Regime cuts read only for arms with enough sessions in them and name how many more each needs; a stale CSRF
  token is retried once; an empty group is saved off and cannot be on; a move between groups starts at
  the next session and says what it carries; a group's budget can be drawn from its members' backtests.
- Charts, the EOD review and the morning report were rearranged (candle history reaches the sessions asked
  for; the intraday and technicals tabs are one chart; the morning report is a one-screen Brief and tabs).

**Orchestrator and research.**

- `run.py draft-verdict` drafts a study's Result in words only, with a check that refuses any number other
  than the item's own. `queue-chain` queues a study's declared chain whole, `study-times` shows each stage's
  dates, a row that did nothing fails, and a result is stale when code it ran on has changed.
- Passed items can declare `retest_forward`; the monthly forward retests (`research_queue.forward_retests`)
  ship off. A study is declared in one PR with its register row in a fenced `register-row` block in its
  plan section, and `research/hypotheses.json` in the repo is refused by `check_docs` and the pre-commit hook.
- Studies declared since v0.13.0 include the market-profile entry readings, signal and trend studies,
  the ladder's rules on minute bars, the portfolio caps, and open-interest and gap-fill studies; each
  result and verdict stays under `~/.cherrypick/data/research/`.

**Core and shared code.**

- `core.jsonio` is the one atomic JSON writer, `core.paperloop` holds the paper loops' shared scaffolding,
  `core.clock.ET` is the one Eastern zone, and `core.quotes` falls back to REST for option legs the stream
  has not cached. `core.implied_vol` is about 3.7 times faster and returns the same floats.
- `technicals` drops Dolt splits the prices never made and reads a rounded stock dividend as one event.
- A paced PMCC arm asks the streamer for the deep window only where it will enter.
- The five studies that read chain paths read only those they declared; a chain with no parity forward
  offers no strike.

**The rest of the release, by area.**

**MEIC and live trading.**

- **MEIC's live loop ships in dry run.** `live.dry_run` is `true` unless it is the literal `false`, and a
  missing key means `true`. An armed tick then preflights each new entry against the real account and
  sends none, writing nothing to the live ledger. It governs entries only: a held book is still
  confirmed, stopped, force-closed and settled live. Each further login reads its own key; `--status`
  reports it, the arming readout warns while it is on, and the Config page cannot change it. **Upgrading:**
  a live config that never named the key now runs in dry run; set `live.dry_run` to `false` by hand to
  send entries again.
- **A live MEIC trade records the arm live ran.** `ic_trades.live_arm` holds the registry arm (`pm-200`)
  at entry, so live results read per arm no longer depend on joining the trade to its entry attempt. The
  console reads the column first and the attempt second; older rows keep working, and
  `python -m cherrypick.meic.live_arm_backfill` (a dry run until `--apply`, backed up first) fills them
  where an attempt names the arm.
- **MEIC's live arm has its own guarded operation.** `live.arm` is still refused by the general config
  save. `configcli`'s `set_live_arm` (with `live_arm_options` to read) changes that one key and nothing
  else, and only when you type `CHANGE ARM`, the arm has a paper twin and the loop is flat and settled
  (not armed today, no open condor, no working order, nothing unsettled). It is journaled in the arm
  journal, takes effect at the next arming and arms nothing. Config's new `live` tab offers it for MEIC's main login.
- **`execution`'s paper-live gap follows the arm live ran.** MEIC's mirror was one fixed paper arm,
  `live-shadow`. Once `live-shadow` retires (its `arm_retired` break in the paper ledger) each session is
  set against the twin (`shadow_of`) of the arm live ran that session, read from the live ledger's entry
  attempts; a session whose arm has no twin is left out of the gap and listed, never matched to another
  arm's twin. Until then nothing changes.
- **MEIC can measure live per arm: a paper twin for each arm live may run.** `follows_live` arms (and
  `live-shadow`) resolve to whatever `live.arm` names today, so a switch of live's arm would pool two arms'
  paper results under one name. A profile with `shadow_of: "<arm>"` is pinned to one arm plus the live
  block's overrides and does not move when `live.arm` does. The registry check names a twin that shadows an
  unknown arm, a follower or another twin, and `live.arm` cannot name a twin. Nothing changes until a
  machine's registry adds twins: `scripts/boundary_2026_11_24.py --arms ...` does that, retires the unpinned
  `live-shadow` and journals the break (dry run by default).

**Ladder.**

- **The ladder has live gates, a daily arm and an order path that ships in dry-run.** Its config gains
  a `live` block, shipped off, and `python -m cherrypick.ladder.live_loop` gains `--status` (every
  gate with its verdict, the account masked, dry-run or live), `--arm --confirm YES`, `--install-task`
  (for `/live-ladder-start`), `--disarm` and `--once`. `--once` reads the broker, keeps a separate live
  ledger (`live_trades.db`) up to date with what filled, and while armed plans the live arm's orders.
  `live.dry_run` is true as shipped: it journals the orders it would send and sends none. `live.enabled`,
  `live.gate0_confirmed` and `live.dry_run` are guarded from the Config page. Issue #1337.
- **In a cash account the ladder buys only with settled cash.** An IRA is a cash account, and a buy
  paid with an unsettled sale's proceeds and sold before they settle is a good-faith violation. Each
  tick reads the account's withdrawable cash and equity buying power and takes off the ladder's own
  sales of the day; a buy that does not fit waits (`settled_cash_short`), and a read that fails stops
  new buys. A margin account is not limited. The rule is shared (`cherrypick.core.live.cash`), so any
  live path can use it.
- **The ladder's console page has a Live mode** (a Paper/Live selector). It shows the live loop's nine gates with the module's
  own reason for each, whether it is in dry-run or live, whether it is armed today, the halt flag, held
  submissions and orphans, the last tick's positions check and the standing dry-run intents, and, once the
  loop has a ledger, the lots held, the lots sold (fees in their own column, with whether they are the
  schedule's or the broker's), the orders and the journal. It reads only: arming is the
  `/live-ladder-start` command, or the Live page's switch while `live_arming` is on. The suite-wide Live page lists the ladder as a loop too. Issue #1337.
- **The ladder has a console page.** `/ladder` (on the rail, key 6) shows each fund's previous close and
  every arm's next buy level, the open lots with the group's 18-day "hospital" flagged, the sold lots as
  trades, and each arm's realised and unrealised result. Its help tab says the rule is not a tested edge.
- **Ladder, a share dip ladder in paper.** A new package, `ladder`, buys a leveraged index fund (TQQQ to
  start) in lots when it falls 7% and sells each lot on its own at +4%, with three arms: `chain` (each
  further lot 7% under the last), `once` (one lot a session) and `decay` (the target falls to breakeven
  after a month). Its rule failed research item 26, so this is for watching it at real quotes, not a
  claim of an edge. Experimental, paper-only and off by default: turn it on with `modules.ladder.enabled` after
  copying `packages/ladder/config.example.json` to `~/.cherrypick/config/ladder.json`. The suite reads
  its lots like any other module's trades, so they land in the report, the compare and the review.

**Retirements.**

- **Calendars and contango are retired.** Research item 38 (calendars) and item 39 (contango) failed every
  arm at real quotes and prices. Both modules ship off, with a retired date on each arm; calendars'
  open positions run to their own exits and the module goes off once flat. The same change switched off
  the four automations item 75 failed (`iron-fly-1000`, `iron-fly-1dte-1330`, `gld-call-spread-20d` and
  `orb-put-spread`); the two that passed and the two with no verdict stay on. The docs, the Help page
  and the install guide now say so.

**Automations and the Builder.**

- **A filter on the move since the open.** The Builder's "Move since the open" (`open_change_pct`) reads
  the price against today's open, in percent, beside the opening gap and the move since the previous
  close.
- **Automations ignore impossible prices.** A quote that puts a structure's mid outside what it can
  be worth (a $10 fly entered for an $11 credit, or closed past its width) is no longer taken: the
  entry is skipped as `unrealistic_price`, no exit fires on such a mark, and a close waits for the
  next sane quote. Backtests and paper both change where a stored quote was bad; earlier runs that
  closed on one overstated their result.
- **Find ideas, on a saved automation.** The Builder searches the automation's newest long explore
  run for entry filters (VIX, RSI, trend and gap bands, weekdays, event days, alone or two at once)
  and lists them as hypotheses, never a change. A filter is scored by the days it would skip, and
  must beat the best random filter of the whole menu, hold across neighbouring bands and folds, and
  leave trades that pay at 1.5x costs, all on the part of the run before a split fixed when the
  search is queued. A survivor can be declared (its register row drafted) and then tested once on
  the later part. `core.stats.menu_placebo` is the max-over-menu baseline.
- **Try settings, on a saved automation.** The Builder replays the automation as its newest long
  explore run did, and beside it the same automation with one exit or width setting changed: stops,
  profit targets, a close before expiry, the width, and each exit it sets switched off. A setting is
  scored by what it changes per session against the automation's own replay, and must beat the best
  random change of the whole grid, agree with its neighbouring settings, gain across folds, and still
  gain at 1.5x costs, all before a split fixed when the grid is queued. Declare and the one held-out
  test work as Find ideas' do. Each setting is a full replay, so a grid takes queue time.
  `core.stats.setting_placebo` is its max-over-grid baseline.
- **How a Builder run holds up.** The run card gains the reads a study gets, from the same trades
  and never a verdict: what the 5 best days carry, P&L per trade with costs at 1.5x and 2x, the
  drawdown range with the trades reshuffled, and how many quarters were profitable.
- **The Builder builds an automation in a form.** One automation at a time, opened, new or copied.
  It is built in five sections with each block's backtest label: underlying and data, the entry
  signal in plain English, the market and chain gates, the legs and their price gates, and exits,
  size and limits. Every choice comes from the engine's own tables (automations `cli builder-schema`),
  and **Edit as JSON** covers the rest. The action row is Check, Save (off) and Explore run, with
  Declare and Promote to paper shown disabled. Beside it, **Today, live** decides the automation
  now as paper would, on a read-only snapshot (`cli builder-today`): every check, and the legs it
  would take or the check that stops it. The explore runs and run cards sit beside it too.
- **Multi-day automations replayed through the session.** An automation that enters inside the
  session on an expiry after it (a 10:30 entry on a monthly) is backtested from the full set's
  intraday chain stores: expiries 1-7 days out at one minute and 8-90 at five, each read no older
  than its interval. Where the same-day store has no spot that session, the nearest expiry's parity
  forward stands for it, a declared limit. Before, such a run counted `needs_intraday` every session.
  The Builder's coverage counts those stores as they fill.
- **A daily signal on the developing bar.** A daily trigger takes `"developing": true` (with `within`
  null) to read today's bar as it stands each minute: the session's open, its high and low so far
  and the price now. It is due at any minute in the window when the signal fires on that bar. A
  daily trigger without it still acts on yesterday's completed bar.
- **A gap measured at the first bar's close.** The gap signal takes `at: "first_bar_close"` to read
  the session's first bar's close (9:35 on five-minute bars) instead of its open. Without it a gap
  reads, and versions, as before.
- **A trailing stop in percent, and net at mid on the run card.** An automation's trailing stop can
  be `{"pct": p, "activate_pct": a}`, each a percent of the trade's max profit (it never fires on a
  structure without one). The Builder's run card shows each run's net at mid beside its P&L: the
  same trades filled at mid, P&L before slippage, as the run recorded it.
- **Long automation backtests run in linear time.** The driver's snapshot at each minute copied every
  arm's entry history and rebuilt every arm's closed results from the whole book, so a run slowed
  as its book grew (item 90's placebo pass: 63 s a session near its end, 11 s now). It now carries
  what a decision reads; trades and reasons are identical.
- **The Builder's Explore run picks its window.** Beside the button, every span a store holds for the
  whole automation: the last year and everything that store holds, ours first, a thin span's share of
  sessions shown, and the vendor's only where it reaches further back than ours, marked licensed. A
  store that stopped early still offers its span. The page sends only the module's window key; the
  orchestrator refuses one the module does not offer.
- **Backtest coverage counts only stores the backtest can replay.** A same-day chain comes from a
  store in the recorder's schema and a later expiry from end-of-day chains; an entry inside the
  session on a later expiry has no replay yet, and the coverage line says so. Before this, the full
  set's one- and five-minute stores could be named for a run that reads neither.
- **Monthly expiries, and coverage that reaches our own chains.** An expiry rule (`between`,
  `at_least`) takes `"monthly": true` to pick only standard monthlies (`core.calendar.monthly_expiry`:
  the third Friday, or the session before a holiday). The wizard's shapes use it, so each is covered
  by our own Dolt end-of-day chains and can be explored without the vendor. Backtest coverage counts
  a store up to five trading days behind the last finished session (one written the next morning)
  as covering, and the run ends at its last session. A need no store can supply now says why, and a
  rule allowing any expiry that monthlies would cover says so, with **Use monthlies only** in the
  wizard. The Builder's form shows `monthly` as a checkbox (the schema names the expiry keys that
  are true or false).
- **The Builder's wizard.** **New** and **Copy as new** build an automation in seven steps (start,
  underlying and expiry, legs, entry, gates, exits and size, review), for someone new to
  backtesting. It starts from a shape (short put, credit spreads, iron condor, iron fly, custom legs),
  each a whole automation the engine accepts, with the credit-trade exits filled in. Each step
  shows only its fields, an "About this step" panel and a **?** on each part with a short
  plain-language note. Leaving a step asks the module's Check. **Open in the full form** carries the
  draft across. Saving is the form's save, always off.
- **Backtest coverage reads a near-close time entry as daily.** A time entry whose window holds
  15:59, the end-of-day chains' one tick, now needs the chains at `1d` rather than minute data, as
  the replay prices it; only an entry at exactly 16:00 was read as daily before, a time the
  engine's window never meets.
- **The Builder's run card.** **Runs** now lists an automation's explore runs with a small P&L curve
  and what each changed from the run before. Opening one shows its card:
  - tiles: P&L, trades, per trade, win rate, max drawdown, net over drawdown;
  - the cumulative P&L with its gross line and drawdown;
  - a by-year table of gross, fees, settlement, slippage and P&L;
  - why sessions were skipped, what the run changed, and its trade log.

  Every figure is read from the run's own trades. Backtest `read automation-run` is the read behind it.
- **An intraday signal automation backtests in minutes, not days.** The driver read the five earlier
  sessions' spot trails from the store again at every minute of a session, so a year of one-minute
  SPX sessions took days. It now reads each session's trail once and keeps it. The trades are
  unchanged.
- **The Builder runs a backtest.** An automation card whose blocks our data covers has
  **Backtest**: one explore run of that saved automation, queued on the research queue by the
  orchestrator over the window and store its backtest suggestion names (descriptive, no verdict, no
  label moved, nothing turned on). Our own recordings are asked first; where they fall short and the
  vendor licence allows new work, the vendor's store covers it and the page says the result is
  licensed. **Runs** shows the card's queued runs and each result's sessions,
  trades and P&L per arm, a licensed result only while licensed results may be shown. Backtest
  `read automation-runs --name` is the read behind it.
- **An automation's chain request never takes the feed past its budget.** Enrolling an automation
  asks the streamer for its symbol and, around its entry window, the one expiry it trades. That request
  is now priced against the subscription budget before it is written. If it would go over, that day's
  entries lose their chain and refuse, open positions keep theirs, and the status says what was dropped.
  The watchdog and the module read the budget from the same place.
- **The Builder page edits automations.** Each card has Edit and Copy as new. Check asks the
  automations module whether the paper loop would accept the edit and lists its problems, or the label
  each block would carry. Save writes it through the suite's config editor, with a backup, and always
  off: turning an automation on stays a separate step. A copy under a new name is not backtested.
- **The Builder page.** A new page (`/builder`, shortcut `u`) shows every automation block by block.
  Each block is marked backtested, changed since its backtest, or not backtested. Each card also shows
  the start date, the forward check, and whether our own data could back a backtest. This first
  version is read-only.
- **An automation not yet backtested is told whether it could be.** Its status lists what its blocks
  read, and whether our own data covers each over the last year. Where the data falls short, it gives
  the nearest store's span and the reason.
- **Automations enrol with a start date and their own forward check.** An automation can set the first
  day paper may enter it, and a group's start is the day its members join. Each block (structure,
  entry, gates, exit, size) is labelled backtested, changed since its backtest, or not backtested. A
  forward-check rule written with the automation is read by `forward-checks` beside the research
  register's.
- **A nightly replay checks paper against the backtest.** After each session, the new
  `automations-replay` job runs the day's recorded paper decisions again through the backtest's decision
  path, on the market paper saw. Any decision or fill that differs is listed, and the job fails, so the
  watchdog says so.
- **More signals.** A signal can fire when an indicator has risen (or fallen) for N bars in a row, when
  the price moves a set percent from a fixed price, and when it crosses the previous week's or month's
  high or low or the opening range's midpoint.
- **More event labels for automations.** Triple witching, the VIX monthly expiration and the futures
  roll are computed by rule. PPI, the jobs report and PCE come from the calendar store. Each symbol's
  earnings and ex-dividend dates come from a new daily `symbol-events` job, which reads the local Dolt
  calendar and the technicals store. A label no source covers for a day is unknown, never quiet.
- **Automations size by allocation and by streak, and a group shares its capital.** A size can be a
  percent of an allocation (capped in dollars or contracts), and any size can step by a set number of
  contracts, or double, after each loss or each win, between a floor and a ceiling. An automation group
  can set an allocation, a cap on its members' open risk and each member's share; an entry the cap has
  no room for is refused and says so. The shipped automations size as before.
- **Ensemble is now automations.** The package, its import, its config file, data folder, log,
  heartbeat and stream request, its suite-config key, the ledger's tables (`automations_*`) and its trade
  schema (`automations_trade`) are named `automations`; trade notifications say "Automation paper". The
  migration `v0.14.0-01-automations-rename` moves a home set up under the old name (config files edited
  by exact replacement and checked to parse to the intended document; a second run finds nothing), and
  `automations.db.connect` refuses a ledger it has not reached. The finished research scripts keep their
  file names (`ensemble_item75.py`, `ensemble_bars_item85.py`) and import `cherrypick.automations`.
- **Automation groups.** The eight former ensemble arms are the `default` automation group, each
  labelled with research item 75's verdict (two passed, four failed, two no verdict). Switch the whole
  group off with `"groups": {"default": {"enabled": false}}` in `automations.json`: no new entries,
  while anything open is still managed and settled.
- **The console's Ensemble page is Automations** (`/automations`).
  Its summary ends with the automation groups: each group's switch, and each automation with its
  research verdict.
- **`automations.json` names its block `automations`.** The block each automation is configured in
  was `arms`; that spelling still resolves, and a file holding both is read by `automations`.
- **Automation backtests through the engine paper runs.** `packages/backtest/scripts/automation_backtest.py`
  replays automations over a stored history with `cherrypick.automations.driver`: the same decisions,
  exits, gates and fills as the paper loop (a test holds one day to the same trade, to the cent), so
  every signal, exit and gate added since v0.13.0 can be backtested. It reads our own SPX 0DTE recordings,
  or the vendor's stores while the licence allows, chosen by the source registry. An automation held
  across sessions reads end-of-day chains (Dolt, or the vendor's while licensed) at one tick a session,
  15:59; an entry at another time is counted as needing intraday data, never moved to the close.
- Automation exits: profit and stop in dollars, a stop as a share of a debit, a trailing stop, a
  forced close the session before a listed event, and a guard that holds any exit while the
  structure's bid/ask is wider than a limit. Paper and the backtest driver read them alike.
- Automation strikes and expiries: a strike by premium (bid, ask or mid), by standard deviations or
  the expected move (both from the at-the-money straddle, so quotes alone are enough), off the opening
  range, the open, or the spot when the entry fired, and at the largest net positive or negative GEX,
  call or put GEX, or call or put open interest. Every rule takes a match mode: exactly, nearest, next
  above or next below. An expiry can be at least N days out or between N and M. A misspelt key inside
  a strike rule is now refused, as one elsewhere in an arm already was.
- **A signal library with versions.** `python -m cherrypick.core.signallib add NAME SPEC.json` files a
  signal in `~/.cherrypick/config/signals.json`; each edit is a new version named by a hash of its
  settings, and old versions stay. An automation points at one with
  `{"library": NAME, "version": HASH}` in place of the spec, always naming the version, and an
  automation backtest's result lists the versions it read.
- Signals: VWAP (anchored to the session, a time or an event; volume or equal weights) and its bands,
  relative volume against the same minute of earlier sessions, and a volume ratio for surges. A signal
  that reads volume refuses bars without it. An automation's volume signal names whose volume weighs
  its bars (SPY for SPX): paper records it each minute, and the backtest reads the recorded one-minute
  bars, refusing dates they do not cover.
- **Automations state their safeguards.** `limits.max_open` and `limits.per_day` are required in every
  automation, with no default in the engine: an entry in `~/.cherrypick/config/automations.json` that replaces
  `limits` must name both. The shipped arms already ran one entry a day; they now say so.
- **IV gates on the vol index.** The automations' `vol_index_rank`, `vol_index_percentile` and
  `vol_index_minus_rv` gates read the underlying's Cboe vol index (VIX for SPX, GVZ for GLD, RVX for
  IWM): its previous close against its own history, or against realized vol. The `market-files` job
  now also keeps RVX, GVZ, VXSLV and OVX.
- **Automations: three more exits, and signal exits in paper.** `stop_credit_multiple` closes a credit
  position when its loss reaches m times the credit, `delta_limit` when the structure's delta reaches a
  limit, `close_at_dte` a set number of days before expiry. Paper's position snapshot now carries the
  recorded trail, earlier sessions, daily bars, the event calendar and leg deltas: without them a signal
  exit could never fire in paper, though it fired in the backtest.
- **One firing rule for intraday signals.** In the automations' engine an intraday signal now reads the
  previous five sessions as well as today's, as a signal study does, so a seeded indicator is warm at
  the open and a gap or prior-session signal has the prior session. A test holds that the study,
  core's trigger and the engine (ticking as paper does) fire on the same minute.
- **Daily signals.** A trigger with `"daily": true` reads completed daily bars only and acts today on
  a firing at the previous session's close (a daily RSI or MACD cross, a close above a moving average).
  Today's still-forming bar is never read, and bars that stop before yesterday are refused as stale.
  The automations' entries and signal exits can use it.
- **Automations: exits on a signal, entries on event dates.** An arm's `exit.signal` closes a position
  when a `core.signals` signal fires on its underlying (the entry trigger's shape), checked after the
  touch and the profit target; the backtest and paper both run it. An entry trigger may now be an event
  signal, such as FOMC day at 13:45 or the session before CPI: the dates come from the event calendar,
  and a stretch the calendar cannot answer is refused by name, never read as quiet.
- **Automations, in paper.** Eight short-dated SPX and GLD option arms (0DTE and 1DTE flies, a condor and
  put spreads, an opening-range put spread, a long put and two GLD call spreads) now trade in paper
  through one decision function, the same one their backtest uses. Experimental and off by default: turn
  on `modules.automations.enabled` after copying `packages/automations/config.example.json` to
  `~/.cherrypick/config/automations.json`. An arm added in that file trades with no code change. Every
  decision, taken or refused, is kept with the gate that decided it and the snapshot it saw, so paper can
  be replayed against the backtest. A position is a ledger row, never an order. An arm can enter on a
  signal (a cross, a breakout, a gap and the like, on bars of the minutes it names) with a `trigger`
  entry, and a misspelt gate setting in the file is refused when the arms load instead of at the first
  decision.

**Backtest, data and research.**

- **Backtested exits no longer go blind when a wing leaves the recorded strikes.** The same-day
  stores hold a band of strikes around the spot, so on a day the market ran, a condor's far wing
  could drop out of it and the position had no price for the rest of the day: its stop or target
  never fired and it rode to settlement. Such a wing is now read as worth anywhere from 0 to the
  farthest recorded strike's ask, an exit fires when it fires across that whole range, and the
  close fills at the costly end. Backtests with intraday exits change, mostly on trending days.
- **Backtests on Compare's portfolio tab.** Beside one paper arm per module, add any number of
  backtest arms: each Builder automation's newest run and the arms of a study result (item 75's eight,
  for one). Correlation, effective arms, the drawdown saving and contributions read them as they read
  paper, each backtest arm counted from its run's first session and labelled backtest. A licensed
  result is offered only while the results gate is open, and the reading says it holds one.
  `core.portfolio`'s manifest takes a `backtest` entry (a result file and an arm), and
  `backtest read portfolio-sources` lists what can be chosen.
- **Risk held at once, on the portfolio tab.** For the chosen arms, each session's peak of the max
  loss open at the same time, the median, 95th percentile and worst of those peaks, and the worst
  sessions with each arm's part. A trade with no recorded max loss is named and the card says it is
  a floor. An automation's closed trades now record `risk`, their max loss, so Builder runs and
  studies from here on are measured; older runs read as unmeasured.
- **Add an underlying to the full set from the Builder.** Where our data does not cover an
  automation on an underlying outside the full set's seven, the Builder offers to add it: the plan
  first (requests and sessions, a year back by default), then one click queues its fetches on the
  vendor's queue and the automation's explore run after them. Up to 300 sessions a click, only while
  the licence allows new work; the data and the result are licensed. Additions are recorded on this
  machine only and share three stores (`added-60s.db`, `added-near-1m.db`, `added-mid-5m.db`), keyed
  by symbol, which the source registry reads per symbol. `fetch_full_set.py add SYM` adds a longer
  span by hand, and `plan` keeps every addition current.
- **A fetch run in steps estimates the step under way.** A job counted in steps (the SPX session
  fetch, then its VIX pass) shows the time left in the running step at that step's own pace, and
  how many steps follow ("about 48 min left in this step, then 1 more step"), on the queue card and
  in the progress line. Before, the light VIX step counted as half the job and the estimate ran
  hours long.
- **A finished study's verdict, recorded in one command.** The queue's finish notice names each arm's
  verdict and the command that records it. `python tools/record_verdict.py itemNN` sets the register
  row's status and a words-only outcome from the study kit's verdict block, checks the register and
  opens the docs PR from a fresh worktree; a result without the kit's block is refused.
- **The research queue runs in two lanes.** Fetches run one at a time on the vendor's session (the
  `research-queue-vendor` job, which replaces `research-queue`), and scripts run one at a time beside
  them (the `research-queue-local` job), so a day-long study no longer holds up a fetch. A chain's order holds across the lanes. The
  queue tab shows each lane's running task and its own up-next list, and lists rows in the runner's
  order, priority included. Needs a supervisor restart (`supervise --restart`) to start the second
  job.
- **The backtest fetch runs on the vendor's Options Value tier.** `vendor.subscription` picks the path.
  On a tier without greeks, every intraday fetch asks for quotes and computes delta and IV from each
  instant's chain (`core.chaindelta.computed_greeks`), with the underlying taken from the vendor when a
  row carries it and from put-call parity on a 0DTE expiry. VIX minutes without the vendor's own price,
  and option prints, are refused `not_on_tier`. The client never has more requests in flight than the
  tier allows (Value 2, Standard 4), and `backtest status` shows what the tier can still add.
- **Every data source in one list.** `python -m cherrypick.core.sources` declares each store the suite
  holds, ours and the vendor's: what it holds, its cadence, the expiries a chain carries and whether it
  is licensed. `inventory` reads each one's span per symbol, read-only, into
  `~/.cherrypick/state/sources_inventory.json`, and `coverage --field chain --symbol QCOM --from ... --to
  ... --expiry monthly` says which source covers a request, or why none does (it starts too late, ends
  too early, or is thin inside the window). A data file nobody declared is named. The
  `sources-inventory` job reads it every morning at 09:00, and the Backtest page's **sources** tab lists
  every source with its span and answers the coverage question. A store not on the machine is listed
  as absent, not a failure.
- **Signal studies on the underlying.** `packages/backtest/scripts/signal_study.py` takes any signal
  (a cross, a gap, an opening-range break and the like) and shows what the price did after each firing:
  the move to the close and to N sessions, the range against the expected move, and how often a
  distance was breached. Each is shown by year and against the same minute on sessions that had not
  fired by then. It runs on the daily history from 2011 or the recorder's minutes, with no subscription.
  Read the results under Compare → signals. Descriptive only.
- **The Backtest page is always there.** Only what shows the data vendor's licensed data is hidden:
  - hidden: the runs and data tabs, a study's figures, report and notes, a queue task's log, and
    Compare's slices;
  - always shown: the studies' names, statuses and verdicts, the research queue, and studies on our own
    data.

  The licensed pieces stay readable after a licence ends, until its purge date, so the results can be
  reviewed one last time.
- **Backtests run on data as recorded.** Data from our own recorders or a subscribed vendor is no longer
  checked against the recorder before a design or holdout run, so history is never locked and no run is
  marked provisional for that reason. A study is marked provisional only when its register outcome says
  so: an earlier study read the same data, or its holdout was small. The data tab's validation panel is
  gone, and `validate` is no longer a command or a research-queue kind. `--provisional` and
  `--unvalidated-holdout` are still accepted and do nothing.

**Morning pack, charts and data jobs.**

- **The morning pack's SPX risk reversal comes from the broker.** Cboe's delayed SPX chain moved
  behind a host that refuses scripts on 2026-11-19, so every market-files run warned and the row
  stopped. The 30-day 25-delta risk reversal is now read from the broker's SPXW greeks by the
  evening run, between the close and 20:15 ET (when SPX options' overnight session opens); a morning
  retry reports a missing session rather than take overnight values. Rows keep a `source`, and the
  Cboe and broker IVs were never compared on one session, so read a step across 11-19 with that in
  mind.
- **Market Profile on the intraday chart.** Charts → intraday now also draws SPX, SPY, NDX and QQQ
  beside the futures. By default it overlays the session's Market Profile: time spent at each price as a
  shaded histogram, plus lines for the point of control, the value area (70% of the time), the first
  hour's range and the prior session's levels, each named on the price axis and in a legend under the
  chart. The first-hour and prior-session lines each have a button in that legend to hide them when
  the price axis gets crowded, and the ? beside the chart's title explains every line. For the recorded
  symbols it also marks untested POCs (nPOC): earlier sessions' points of control that no trade since,
  overnight included, has reached, listed in the line above the chart too; for /ES and /NQ, the
  overnight session's high, point of control and low (ONH, ON POC, ONL); and, off until you switch it
  on, the 5-session composite's POC and value area (cPOC, cVAH, cVAL). Turn the whole
  overlay off with the `profile off` button. The profile
  covers the regular session, 09:30–16:00 ET, for every symbol, a future's overnight excluded. It is a
  chart and nothing trades on it. The chart's time axis now names the day where each session starts
  ("Fri 13 09:30"), and the line above it says which days are in view. The same arithmetic is in
  `cherrypick.core.marketprofile` for
  research.
- **Profile bars are kept.** A new job, `profile-bars`, saves 1-minute bars of SPX, SPY, NDX, QQQ, /ES
  and /NQ every trading day at 17:15 ET into `~/.cherrypick/data/market-files/profile_bars.db`. The
  broker's feed only ever hands back its newest 8,000 one-minute bars (about six days of a future), so
  this is what lets profiles be compared across weeks. It reads the broker and changes nothing; turn
  it off with `technicals.profile_bars: false`. After upgrading, run `python run.py supervise
  --restart` so the supervisor picks up the new job. If a session's bars come up short for any symbol,
  the watchdog warns that evening (from 20:15 ET), while the broker still has the minutes to refetch.
- **Gold bars are kept.** A new job, `gold-bars`, saves 1-hour and daily bars of gold futures and GLD
  every trading day at 20:15 ET into `~/.cherrypick/data/market-files/gold_bars.db`, for the coming
  engulf study and paper module. The broker keeps hourly bars for about 9.5 months. `/GC` is the front
  month from the futures map, recorded from its first run and never overwritten across a roll;
  `/GC:XCEC`, the broker's continuous series, carries the history (daily from 1999, unadjusted at
  rolls). Each dated contract is kept under its own name too (the active month and the next, every
  day), so a front-month series with known roll dates can be built from them; `python
  scripts/fetch_gold_bars.py --backfill-contracts` fills in every contract since 2016 once (daily bars
  from 2014, hourly bars from February 2026). It reads the broker and changes nothing; turn it off with
  `technicals.gold_bars: false`.
  After upgrading, run `python run.py supervise --restart` so the supervisor picks up the new job.
- **Charts → profile.** A new tab shows the recorded sessions' Market Profiles side by side (5, 10 or
  20 days) for SPX, SPY, NDX, QQQ, /ES and /NQ, with a composite of the newest 5, 10 or 20 sessions and
  a table of each day's point of control, value area, first-hour range and whether value moved higher,
  lower or overlapped the day before. It reads the bars the `profile-bars` job keeps, so it fills in
  as that job runs; today is drawn from the live candles, marked "live", until it is recorded. Click a
  day to open it full size with every letter and its facts, and switch the bars between time and volume
  at price (where the symbol has volume). A chart
  only; nothing trades on it.
- **Market profile in the morning pack.** The pack (fact version 6) and the Morning tab now carry the
  prior session's profile levels for SPX, NDX, /ES and /NQ: point of control, value area, first-hour
  range, range and close, whether the high and low were poor or tailed, and the untested POCs, each
  linking to that day on the Profile tab. Record-only; no gate or score reads it. The GEX panel can
  draw the same levels beside its walls (the `profile levels` button on its chart).

## v0.13.0 — 2026-11-13 — evidence and accounts
About 45 pull requests since v0.12.0, and it includes everything in the v0.12.1 beta (delta ideas, the
PMCC-only tab, the wheel tab's state choice). The suite can now read a second broker login beside the
first, and manage judges a cash account or IRA by its own buying-power caps instead of the margin
table. Manage's rules carry an evidence grade where they carried a confidence number, and it learned
covered calls, the super bull, wheel puts by intent and a few time-exit corrections. Earnings times
come from Yahoo's calendar where Nasdaq's refuses. A new package, `ensemble`, holds a rules engine for
short-dated option arms; it is backtest-only and nothing in it is switched on. This is an ordinary
upgrade from v0.12.0 (or from the beta); there is nothing to do by hand.

### Upgrading

- **From v0.12.0 or the v0.12.1 beta:** `python packages/orchestrator/run.py upgrade` (or
  `upgrade.cmd` / `./upgrade.sh`). It refuses while a live loop is armed or holds a position, as every
  upgrade does. Afterwards run `python run.py supervise --restart` and restart any resident job (a job
  reads its config only when it starts), and restart the console to load its new server code.
- **Nothing is required by hand, and no data migrates.** A second broker login is off until you add
  one; the example configs gain only optional keys.
- **manage.json: `confidence`, `hold` and `conflict_penalty` are gone.** Each rule now has an
  `evidence` grade. A leftover `confidence` is reported as an unknown key and ignored; delete it when
  convenient. Reasons and rule ties can read differently (below).
- **Cash accounts and IRAs** are held to new buying-power caps (80% in total, and so on). Set
  `portfolio.buying_power.cash.enabled` to false in manage.json to keep the VIX table for such an
  account.
- **Earnings times** come from Yahoo's calendar where Nasdaq answers 403. This needs `yfinance`
  (already a technicals dependency); without it the fallback is skipped and listed as failed.
- **Measurement boundaries:** manage's verdicts and caps moved on 2026-11-10 to 2026-11-13 (table in
  the detail). Do not pool a manage figure across them.

### What's new

- **A second broker login across the suite (#1037).** Add one under `broker.logins` in `config.json`
  (`run.py connect --login NAME`; the installers offer it). Each login has its own keyring entry, its
  own streamer producer (`streamer.instances`), and MEIC can have a live account per login
  (`live_accounts.<login>` in meic.json). The orchestrator reads, alerts on and settles each login's
  accounts, manage judges every account on its own history, and the console names and reads each
  login's own account. Off by default: with no extra login the job table is unchanged, and an extra
  login's live account stays off until its own gate, enable and arm are set.
- **An account select on Live (#1041).** With more than one account, positions, portfolio, wheel and
  pmcc draw the first account by default, another by name, or all. The choice is `?acct=` in the
  address and holds across the tabs. The Live head now shows only what qualifies the reading (#1044).
- **Evidence grades replace confidence (#1040).** A rule is graded mechanics, tested, contested,
  practice or ours, by the kind of evidence behind it, and `RULE_ORDER` (safety, sizing, stops, targets,
  time, rolls) breaks ties between rules naming the same action. The console and the Discord post show
  the grade. The credit stop and the untested-side roll are `contested`.
- **Buying power on a cash account or IRA (#1026).** A cash account holds each short put's full strike
  and each spread's max loss, neither of which grows when the market moves, so manage no longer holds
  it to the VIX table written for margin. It is held to 80% in total (the rest kept free to sell after a
  volatility spike), with short puts at their strikes plus shares under 65% of net liq and the other
  trades' buying power under 35%. A new rule, `portfolio.stress`, flags SPY −20% with IV up 10 points
  costing over 20% of net liq. The income list, the hedge and the trade model size against the same
  caps, and Live → portfolio's room card lists each part. Margin accounts, and accounts of unknown kind,
  keep the VIX table. The caps are in `portfolio.buying_power.cash`; `enabled: false` restores the VIX
  table for a cash account.
- **manage learned more shapes and cases.**
  - Covered calls (short calls the shares cover) are no longer called naked; `covered_call.assigned`
    judges an in-the-money one by its call-away (#1033).
  - Wheel puts by intent: a short put on a name with `intent: "wheel"` is judged by `wheel.tested`
    (roll, close or take assignment) rather than the credit stop (#1034). Live → wheel lists the short
    puts with no intent, with both verdicts, and asks whether each is a wheel; the choice is staged and
    saved in Config → Position rules → Intents (#1035, #1036).
  - Super bull and super bear (diagonal) are named before the diagonal test, and directional trades
    get their own time exit, `directional.time_exit`, off by default (#1043).
  - A diagonal's target is capped at 75% of its modelled gain (`debit.take_profit.max_gain_cap`), the
    PMCC long is flagged for rolling at 60 days left (was 120), and `short_term_short_days` is 21
    (was 14) (#1033, #1034).
  - The wheel tab keeps to the wheel's own legs, not every trade on its names (#1017).
- **The v0.12.1 beta's changes**, folded in: the portfolio tab's **ideas** button (what drives your
  delta and the rolls that would move it, each priced; a PMCC rolled under its own rules), put
  calendars' rolls quoted from the broker, the PMCC tab holding PMCCs only with a **time spreads**
  filter on positions, the wheel tab's state choice, cards or rows on the wheel and PMCC tabs, and live
  prices on the wheel, PMCC, positions and portfolio tabs (#1018 to #1021).
- **Earnings times keep coming (#1030).** Nasdaq's calendar has answered 403 since 2026-11-09 and the
  times file went empty. Every date it does not answer is now read from Yahoo's calendar; the file
  records each date's source, and a run that gets nothing writes nothing, so the last good file stays.
- **Console.** Guide screenshots expand on a click and the guide reads on a tablet and a phone (#1013);
  the futures ticker fits small screens (#1014); a silent DXLink session is rebuilt overnight (#1015); a
  sleeping tab no longer strobes the tick flash on waking (#1016); every order's legs are drawn in the
  shape of the structure, puts before calls from the lowest strike up (#1032).
- **Fixes.** A config save no longer fails with "changed on disk" because the browser rounded the
  file's mtime (#1039). The hourly status digest shows the real accounts, lists switched-off modules
  once as `off:` and says plainer things (#1047). Weeklyput's SPX plus SPY pair is held on purpose, so
  the same-index lint allows that one pair (#1048).
- **A new package, `packages/ensemble` (#1052).** A rules engine for short-dated option arms: an arm is
  configuration and one pure function decides. Paper-only and credential-free, with no loop, ledger,
  config or console page yet, so it does nothing on an install. It serves research item 75, whose
  backtest driver is in `packages/backtest` (#1053).
- **Research.** Items 70 to 75 were declared (a dividend-yield put, LEAPS as leverage, MEIC on XSP by
  stop and width, one to three 14:00 condors, the super bull as leverage, the ensemble's rules). Item 72
  failed, so the margin login's XSP live account stays off; item 68 dropped NDX before any P&L was read.
  Verdicts and declarations are in `research/`.

### In detail

**Measurement boundaries.** manage's verdicts are advice, but a series of them breaks here.

| Area | Date | What it splits |
|---|---|---|
| manage | 2026-11-10 | The cash-account buying-power caps judge a cash account or IRA (#1026). Margin and unknown-kind accounts are unchanged. |
| manage | 2026-11-13 | Wheel puts by intent, the capped diagonal target, the PMCC long's roll at 60 days, covered calls, `short_term_short_days` 21 and the expiry rules for earnings trades and for trades opened inside three days of expiry (#1033, #1034). On 25 stored sessions seven verdicts changed, each an earnings trade now held through its report. |
| manage | 2026-11-13 | Evidence grades replace confidence (#1040): actions were identical on 25 stored sessions; 2 of 300 trade-days show a different reason under the same CLOSE. |
| manage | 2026-11-13 | Super bull and bear are named before the diagonal test, and directional trades lose the credit 21-day exit (#1043). |
| manage | 2026-11-13 | A second login's trade is numbered per login (`-2`), where a single-login history numbered across logins (#1037). |
| Earnings times | 2026-11-09 | The times file was empty from here until the Yahoo fallback (#1030); names it could not time lost their time of day in between. |

**manage**

- Snapshot and rule changes are in What's new; config keys are in `packages/manage/config.example.json`
  (`evidence` on each rule, `covered_call`, `directional.time_exit`, `debit.take_profit.max_gain_cap`,
  `expiry.wait_if_entered_inside`, `portfolio.buying_power.cash`, `portfolio.delta_ideas`).
- The README cites item 53 for trades opened inside 21 days: no challenger beat the current rules
  (#1049).

**Broker logins**

- New keys: `broker.logins` and `streamer.instances` in `config.json`, `live_accounts` in `meic.json`.
  MEIC `tt --login` reads another login. A live account that never traded gets no job. Reconcile leaves
  dust positions out, and the watchdog no longer calls a producer with nothing to stream stalled.
- The orchestrator's account tests use invented digits (#1038).

**Backtest**

- An XSP dataset beside QQQ and NDX (`XSP-60s-wide.db`), a single progress bar for a fetch and its VIX
  pass, and open-interest plan alias `SPXW-1D` (#1027, #1028, #1053). Vendor data stays under
  `~/.cherrypick`.

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
