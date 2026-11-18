# cherrypick-backtest — Operational Instructions

> Operating contract for the suite's **backtests over licensed vendor history**. Suite-wide context is
> in the root [documentation index](../../docs/README.md). What the package contains is listed under
> "What the package holds" below.

This package replays the suite's **own rules** over years of vendor quotes (ThetaData), so a question about a strategy can be answered in weeks instead of waiting for paper arms
to accumulate sessions. It never trades, never touches a live loop, and never asks a model.

## The licence outranks everything here

ThetaData's subscriber agreement is personal use by one person. **The data and anything derived from
it may never be redistributed** (§1(b), §8(e)), and **everything obtained from the vendor must be
expunged within 30 days of termination** (§8(e)). Two consequences shape the whole package:

- **The repo is public.** No vendor data and no result computed from it ever goes into the repo, a
  PR, an issue, a commit message, a journal or a published artifact. Not a table, not a headline
  number. Results stay under `~/.cherrypick/data/backtest/`. This is stricter than the suite's other
  replays (flies' quote-history numbers are written into a plan doc; these never are). Tests use
  synthetic fixtures; a test over real sessions reads the local store and is skipped where it is
  absent, which is always the case in CI.
- **Every store is declared, with the code that rebuilds it.** A new file in the vendor
  directory is added to `stores.STORES` in the same change that adds its fetch code, and that code is
  committed (a package verb or a script in `stores.SCRIPT_DIRS`; a test fails on a script that is
  not). The backup skips licensed data and `purge` removes it, so a store survives only by being
  rebuildable. The Data tab lists every declared store and flags any undeclared file.
- **A script that imports this package lives in `packages/backtest/scripts/`**, not in
  the root `scripts/`: every item study on the engine and the fetchers that fill its stores. Other
  scripts stay at the root. The research queue and the study chain accept both folders, and a change
  here runs every CI job, because other packages' tests load these scripts by path.
- **A new study takes shared pieces from the study kit** (`cherrypick.backtest.study`: calendars,
  chains), never by loading another study's script. The kit is copied from recorded scripts and
  pinned to them by test; a finished study keeps its own copy until a logged correction moves it.
  Its constraints (no defaults for declared parameters, no trading rules, costs from core) are in
  the package docstring and #1068. **A new study reads the chain store only through `Inputs`**
  (the paths its own requests file names) and writes `Inputs.fingerprint` into its result as
  `inputs`, so another study's fetch cannot move its result and the reproduce step can tell a data
  change (`inputs_changed`) from a code change.
- **A new study declares its queue chain** as a module-level `CHAIN` literal (plan, fetches,
  study), and is queued with `run.py queue-chain --script <path>`, never row by row
  (`orchestrator/studychain.py`; item 76's script is the example).
- **A new study starts from the scaffold:** `python tools/study_new.py itemNN <name>` writes its
  script (the correction log, an unfilled `RULE`, the `CHAIN`, `plan` and `study` writing the kit's
  verdict block) and its test file, whose rule test fails until the declaration fills `RULE`. Its
  chain fetches a sample of 20 paths and runs `study --smoke` on it (`smoke.json`, never a verdict)
  before the full fetch, so a crash or an arm judging no trades shows in minutes (#1072).
- **A change to shared study code is reproduced before it merges.** From the PR's worktree (its
  packages first on `PYTHONPATH`), `python packages/backtest/scripts/reproduce_studies.py run`
  re-runs the standing sample (`SAMPLE`; `--studies` for others) by each study's last queue command
  and compares every result file with the recorded one, restoring the recorded folder whatever
  happens. Merge on `identical`, or on `expected` (the study's correction log grew); a `differs`
  is read before anything merges. After the merge, a `reproduce/run` queue row confirms the live
  checkout. Do not run it by hand while the queue runs one of the sample's studies.
- **The stores have a committed contract** (`storecontract.py`, `store_contract.json`): each table's
  columns and the spellings its coded columns hold (the chain stores spell a right `CALL`/`PUT`,
  the open-interest store `C`/`P`). CI holds each store's DDL and the kit's readers to it; a study's
  fixtures take their spellings from `storecontract.rights`. After a store's schema or spellings
  change, `python packages/backtest/scripts/store_contract.py check` says so and `write` updates it.
- **Each chain store has a quality report** (`storequality.py`; `scripts/store_quality.py run`, a
  `quality/run` queue row after a fetch): per symbol and year the sessions, chains, strikes a
  chain, the two-sided, zero-bid and crossed shares, the intraday store's missing delta and iv,
  and each store's request outcomes, written to `results/quality/` (local: derived from licensed
  data). A declaration names the coverage it needs, and its study checks it with
  `storequality.meets` before judging anything.
- **The data must stay purgeable.** It lives in one directory carrying a marker file
  (`paths.LICENSED_MARKER`); `purge` removes only marked directories (dry run by default), and the
  nightly backup skips any marked directory (`orchestrator/backup.py` spells the same name; a test
  here holds the two equal). The operator records the end of a subscription in
  `vendor.license.terminated_on`, and `licensing.status` turns it into a purge deadline.
- **At the end of a subscription the results are kept, reviewed once, then deleted** (the user's
  decision). `licensing.status` reports the phase: `keeping` from the termination, `final_review`
  for the last week before `purge_by` (read the results one last time, write up anything still
  worth knowing without figures, queue nothing new), then `purge_due`: `purge --derived --apply`
  removes the stores and every result together, inside the licence's 30 days. A recorded verdict
  stands without its data, read as not reproducible (the studies board marks it
  `not_reproducible`). From the termination the research queue refuses and cancels vendor fetches,
  the forward retests skip every study on the stores (studies on public data go on), and a forward
  check whose reference is purged judges its paper arm alone.

## The fence

- **The network is reached from `vendor/thetadata.py` only**, and only by the fetch. A run reads the
  local store and nothing else, so it is a pure function of data on disk and reproduces exactly.
- **The keyring is touched from `credential.py` only.** The key is never in a config file, an
  environment variable the suite reads, a log or a reply: `key status` and `status` report presence
  (`True`, `False`, or `None` for "the keyring could not be read", which is not "absent"). The key
  is passed to the vendor client explicitly, so the client never looks in the environment or a
  `.env` file on its own.
- **No AI, no broker, anywhere in the package.**

`tests/test_fence.py` enforces all three by reading the source, and shows itself failing on a planted
import.

## The config is strict

`~/.cherrypick/config/backtest.json`, through `cherrypick.core.home.load_module_config`, ending at the
shipped example (disabled). Unlike most module configs, **an unknown key or a bad value is refused
with its path**: a misspelt `windows` key defaulting silently would move the holdout without a word.
Keys starting with `_` are comments. `tests/test_settings.py` holds the example's keys equal to the
defaults, so a setting cannot exist that the example does not show.

**`windows` are declared once and never tuned.** The holdout is invisible to every search and spent
once per spec. They are never chosen by the advisor, which already knows how those years went.

## The store is the recorder's schema

`store.py` writes vendor sessions into **the gex recorder's own tables** (`gex_quote_samples`,
`gex_quotes`, `gex_spot_history`, `gex_profile_history`, `daily_closes`), one SQLite file per dataset
and cadence (`vendor/thetadata/SPX-15s.db`, `SPX-60s.db`). Everything that reads a recorded session takes a `history_db` path, so a
vendor session replays through the same readers as a recorded one, with no second reader to drift.
This package may not import gex, so it spells the DDL; `tests/test_store.py` (reading gex's source)
and `packages/gex/tests/test_backtest_schema_mirror.py` (reading this one's) hold the two equal, one
from each side, because CI runs only changed packages' tests.

One file per dataset, not per month: the replay's trailing-range input reads earlier sessions from the
same file, and a month boundary would leave each month's first sessions without one. One file per
cadence, too: the replay ticks once per stored sample, so a mixed file would replay some days at 15 s
and some at 60 s. And one file per strike count: a declared store tag (`store.TAGS`; `wide` keeps 40
strikes each side for items 42 and 44) is its own file (`SPX-60s-wide.db`), fetched by the research
queue's `fetch-sessions`, never by the nightly job. A tag may carry its own first session (`from`),
which overrides the dataset's: `full`, the full set's, starts at 2020-01-01. A study points the runner at it with
`store.tagged(settings, tag)`.

**History is 1-minute; the recorder era is 15 s.** The vendor drops every sub-minute
history request at 60 s before 2026 (15 s, 30 s and tick alike, even ten minutes of two strikes, by
the library and by the terminal's REST API). That is a vendor behaviour, not a finding from its data.
So history is fetched at `--cadence 60`, and a replay over it ticks once a minute: a completion that
touches between minutes is missed, a limit every 60 s study states.

**One vendor session per account.** A second login with the same key ends the first, and every later
request fails `UNAUTHENTICATED`. A fetch stops on that (`vendor.FATAL`) rather than recording every
remaining session as refused. **So the client takes a lock before it logs in**
(`<home>/state/thetadata-session.lock`, the holder's PID, `core.looplock`'s never-steal-from-the-
living semantics): a second client built here WAITS, asking again every minute and logging who holds
it, and gives up with `session_in_use` after six hours. Every fetch, the nightly job and any script
using `client_for` take it; the Theta Terminal and the library used by hand do not, so never run
those while a fetch runs.

**A check by hand while a fetch runs goes to STAGE**, the vendor's second environment:
`client_for(settings, env="STAGE")`. Its login leaves the PROD session alive and takes no lock
. It serves the same quotes, but computes greeks and IV slightly differently
from PROD, so a store is never built from it, and nothing in `src/` or `scripts/` asks for it
(`tests/test_fence.py`). The PROD client always names its environment, so a stray
`THETADATA_MDDS_TYPE` cannot move a fetch.

How each field is rebuilt from the vendor is declared in `fetch.py`'s docstring. The two that differ
from a recording in kind:

- **`age_s` is NULL.** An interval row is the quote standing at that instant; its age is not
  reported, and the replay treats NULL as fresh.
- **Gamma is computed** (`greeks.bs_gamma`) from the row's IV, because gamma is a Professional
  field. The recorder's gamma came from the live feed.
- **On a tier without greeks, delta, IV and spot are computed too.** `vendor.subscription` picks the
  path (`settings.TIERS`). Options Value serves quotes only, so the client asks the quote endpoint
  with the same arguments and fills each instant's chain from its own mids (`core.chaindelta`:
  the forward from put-call parity, zero rate, time to the 16:00 ET close). The underlying is the
  vendor's own price when a row carries one, the parity forward on an expiry's own day, and
  None on any other. VIX minutes need the vendor's own price (the forward of VIX options is the
  future, not the index) and are refused `not_on_tier` without it. So are option prints, before
  any request. Stores fetched on Standard keep the vendor's greeks.
- **Requests in flight never exceed the tier's** (`settings.concurrency`: the configured
  `max_concurrent_requests`, capped at Value 2, Standard 4), whatever `--workers` asks for.
  `licensing.status` carries the tier, so a read can say what the stores can still add.

A session is written whole or not at all; a refusal is a `vendor_sessions` row with its reason, never
an empty session. `fetch` is resumable (a complete session is skipped) and `--refetch` clears a
session before rewriting it.

**The VIX minute series (`vixfetch`).** MEIC's replay reads VIX at each tick, because MEIC
moves its delta band the moment VIX crosses 18, 25 or 35. The account's tier refuses the index
endpoints, but every VIX option reports the VIX index as its underlying price, so one narrow request
a session (nearest expiry after it, one strike each side, one-minute bars) gives the day's series.
It is written into `market_regime_history`, the recorder's table, in the recorder's own form
(`reading = 'vix'`, `symbol = 'VIX'`), so `core.regime` reads a vendor session as it reads a recorded
one; the mirror tests hold that table equal to gex's too. `fetch` fills it for the sessions it
writes; `fetch-vix --from --to` backfills stored sessions (resumable, `--refetch` replaces, never an
empty series), and exits 1 when any session was refused or came back empty. A session without VIX
rows replays on the Cboe daily open, so a MEIC run records each session's VIX source and states
`vix_fallback_sessions`, and MEIC's data fingerprint covers the VIX rows and the Cboe files
(`runner._meic_inputs`): adding VIX to a session is new evidence under a new stamp.

The vendor library is an **extra** (`pip install -e "packages/backtest[thetadata]"`), not a
dependency: it pins its own pandas/polars/grpc, and nothing but a fetch needs it. It imports
python-dotenv without declaring it, so the extra lists both.

## Data is taken as recorded

A store recorded by our own recorders, or fetched from a subscribed vendor, is not checked against
another source before a run: nothing locks history. What a store cannot carry (a quote's age, the
feed's gamma, a sub-minute tick before 2026) is declared above and stated as a limit by each study.

**MEIC's delta source** is a measurement boundary: MEIC paper before 2026-10-27 chose strikes by the
data feed's delta, which lags its quotes by about a minute; from then on, and in every history run, it
is `core.chaindelta`'s delta from each source's own mids (`computed`). Do not pool MEIC paper or
recorded-era results across that date.

## Every long job reports its progress, overall and per symbol

Every fetch verb, the runner and every study script report through `core.progress`, and a phase over
several symbols opens with `Reporter.split` (one key per item) and counts each item with `advance(...,
key=symbol)`, so the queue and the console show the whole and each symbol. A resumed job counts what it
already holds as skipped, overall and per symbol. A new fetch or study is not done until it does both.

## The runner

`run flies --arms ... --window design|holdout|all` replays each session through flies itself
(`flies.cli replay --history-db`, a subprocess; this package imports no strategy module), settles at
the store's close for the day, and reads the scratch ledgers back through `core.ledgers`, so the net
is paper's arithmetic. Control always runs beside the named arms; every arm is paired with it
(`core.compare.pair_with_control`) and flagged `no_exposure` when it matched control to the cent.

- **`design`** always runs. **Every (spec, arm) is a
  trial** (`results.trials`), keyed by a hash of what decides its trades (flies' `defaults`, the arm's
  entry, `symbols`, cadence, fill model): re-running a spec adds nothing, changing a parameter adds
  one. DSR deflates by this count.
- **`holdout`** needs `--spend-holdout` and is spent once per spec, ever. `control` never spends it: it runs beside every arm unjudged, and spending it refused every later study's holdout.
- **`all`** is one run over the whole history, from the design window's start (or `--from`) through
  what the store holds (or `--to`), for a study declared with split dates: it cuts the sessions at
  them afterwards (`study.rule.cut`). A span that reaches the holdout's dates reads them, so it needs
  `--spend-holdout`, spends it per spec as a holdout run does, and is refused once spent; a forward
  run wholly after the holdout (a forward retest) spends nothing. It counts as a trial as a design
  run does.

Runs made while history waited on a check against the recorder recorded no trial and carry a
`provisional` mark in their stored report; the readout leaves that mark out (`readout.OLD_MARKS`),
and the trial count covers only the runs since. `--provisional` and `--unvalidated-holdout` are
still accepted, so older commands run, and do nothing.

Statistics (`stats.py`) are per session, never annualised: PSR, DSR at the trial count, minimum track
record, and a stationary-bootstrap interval. The fill model is flies paper's own (`FILL_MODEL`).

**MEIC is the second module (`run meic`).** What differs per module is one `Module` entry
in `runner.MODULES`: schema, fill model, config loader, the spec a trial is keyed on, the replay
command. MEIC replays through `python -m cherrypick.meic.replay run` (its own engine, tick by tick at
paper's 60 s cadence) from a snapshot of BOTH its files, `meic.json` and `meic.risk.json`
(`$MEIC_CONFIG` / `$MEIC_RISK_CONFIG` for the subprocess), and reads back through `core.ledgers`'
`meic_ic` reader. A MEIC trial is keyed on the arm's params merged over the base with comments
dropped, so an inherited base key that changes is a new trial for every arm, and a reworded note is
none. Advised arms stay flies'. The fence
forbids importing any strategy module (`STRATEGY` in `test_fence.py`): everything is replayed by
subprocess, and MEIC's import would also pull the broker in where the AST scan cannot see it.

**Declared gaps:** the suite's events calendar does not reach the history (2022–2024 read unknown on
BEA, FRED and FOMC; 2025 on FRED), so an event-conditioned arm cannot be backtested until it is
backfilled; and the vendor store carries no volume, so the regime rows' flow columns are NULL.

## What the package holds

The config, the key verbs, the licence clock, purge, the backup skip, the fence, the store, the
fetch (SPXW 0DTE, any cadence the profile cadence divides), regime rows, the flies
and MEIC runners with their statistics, and the flow fetch (below). The flow test (item 32): the rules are
`flowstudy.py` (pure), the stores are read by `packages/backtest/scripts/flow_signal_item32.py`, which reads its grid
from the plan file (`packages/backtest/scripts/flow_fetch_plan.py`, never recounted from the calendar) and spends the
holdout once (a marker is written before the read).
**The end-of-day chain fetch** (`chainfetch.py`, `fetch-chains`, `chains-status`):
- **Event-driven:** a study writes the (name, session, expiry) requests it needs, and this fetches
  exactly those, one session-chain a request, into `vendor/<name>/eod_chains.db` beside the marker.
- **Why event-driven:** the vendor serves single-stock end-of-day chains from 2016 at most, has
  no whole-market daily file, and answers about a request a second, so a full crawl would take weeks.
- **What each request becomes:** complete, empty or refused (with why). A re-run skips the first two
  and retries refusals; a fatal refusal stops the run. Workers share the one session, and every
  write is on the main thread.
- Its first user is edge-hypotheses item 14.
- **Daily paths** (`fetch-chain-paths`): a request names an expiry and a date range, and
  every session's close in that range lands as its own complete session-chain, so a replay can mark
  a held contract each day. The vendor drops a two-month whole-chain range at 60 s, so the client
  asks a week at a time. An empty week before the expiry was listed is skipped; an empty week
  after one that answered refuses the path (`path_gap`), so it is retried rather than stored with a
  hole. A request refused again with the same fixed reason as before (`chainfetch.SETTLED`) is
  counted `settled`, so the research queue passes a repeat pass with nothing left to serve. Paths are recorded in `eod_chain_paths`, resumable the same way, and `--stop-by` bounds a
  run as it does `fetch-chains`, so the research queue can run one.
The console's Backtest page reads `python -m cherrypick.backtest read overview|run` (`readout.py`).
The page is always shown; **what shows this package's licensed results fails closed**: the runs and
data tabs, run figures, study reports and notes, queue logs and licensed signal studies are shown
only while the orchestrator's `features.backtest_results` is true (`orchestrator/backtestgate.py`:
the operator's config enabled, today before the licence's purge date, licensed data on this machine),
with the server routes refusing on their own. That runs past the licence's end through the keeping and
final-review weeks, so the results can be read once more before `purge --derived`. A file under
`results/` or `vendor/` counts as licensed. `features.backtest` (the licence active, the key in the
keyring) is the separate question of whether new work may use the vendor.
**`read overview` never counts a store.** Its inventory is the cache as last counted; a
store changed since then comes back marked `pending`. The `inventory` verb does the counting, run by
the supervisor's `backtest-inventory` job. A cold count of the largest stores takes over a minute and
the console's bridge gives a read 30 s, so a read that counted would be killed mid-count and the
page would show "unavailable".

**The run cards** (`cards.py`): `read run` also returns each arm's path, drawdown
periods, month and year rows, session and trade anatomy, premium (fee drag, and capture rate from
`core.metrics`), exits (`core.ledgers`' `outcome`), an edge trend (first half against second,
block-bootstrap interval) and an account read. They come from `session_results` and the ledgers a run
keeps under `results/runs/<run_id>/`. They are views: no trial, nothing to the advisor. **The account
is a lens, not a replay**: fixed lots, never compounded, so a chosen capital (`--capital`) changes
percentages only, and is one account for every arm. Absent, each arm reads against its own peak
session risk (MEIC's condors' max losses summed, flies' book peak risk), never one shared peak, which
MEIC's control sampling stream would set for every arm; a schema with neither has no account.
**The health checks** (`health`) put the warnings first, each against a bar declared in
`cards.py`: the result rests on its best five sessions (`core.robustness.best_sessions`), session
win rate under 25%, a losing run of 10+ sessions, a drawdown of 60+ sessions, a decaying edge trend,
fees at 25%+ of premium. A check missing its numbers is left out, never passed. Description only.
**The settings strip** (`arm_settings`): each arm's cadence, fill model and the params
that differ from control's, so a grid arm reads as the setting it tests. A report stores each arm's
spec; a report written before 2026-11-01 reads it from `trials` by hash, and an older run with no trial
row reads `recorded: false`, never "no difference".
**The compounded lens** (`compounded`, `read run --risk-pct`) is the one exception to
"never compounded", and is labelled a lens wherever it is shown: the same sessions re-sized each
session to put a share of the equity at that point at risk (default 2%), one unit being the lots the
replay held that session at its own risk, known at entry: `--risk-unit max_loss` (default) reads the
positions' max loss; `stop` reads what MEIC's stops lose at the trigger with both sides stopped,
(2 x `stop_trigger_original` - 1) x credit, read from MEIC's own ledger (a gap loses more; a schema
without stops sizes nothing under it). Whole units floored as
the headline, fractional beside as the upper bound; a session that floors to none or has no recorded
risk is counted, not hidden. It assumes P&L, fills and fees scale with size, and it is never an input
to a verdict, a trial or the advisor.
**The sizing check** (`sizing`, `read run --capital --dd-pct`) answers how many units a
chosen account can carry: the most whole units whose 99th-percentile drawdown over 100 sessions
(`core.robustness.drawdown_range`, the B1 bootstrap) stays within `dd_pct` of the account (default
30%, the operator's choice), via `robustness.max_size`. It reports what that size
risks a session two ways: max loss, and for MEIC both sides stopped with fills at the 95th percentile
of how far its recorded stops landed past their trigger (cost / trigger, from MEIC's own ledger),
capped at max loss. Only against a chosen capital; a check, never a verdict, and it never writes live
config: a size change stays the operator's, through the module's `live` block.
`read trades --id --arm` lists one arm's trades from the same ledgers (`trade_log`):
each trade's times, its exit reason in the module's words and its money columns as `core.ledgers`
names them, a field the schema does not record as null. Its own read, so `read run` stays light.

`read summaries --ids A,B` gives each run's arms in brief for the console's studies
tiles: the mean net a session, sessions and trades as the run's report states them (never
recomputed), and the running net from `session_results` thinned to 48 points, for a line's shape
only. One read answers for a board's worth of runs (at most 80); an id with no report is listed in
`missing`.

**The trade path** (`tradepath.py`, `read path --id --arm --session --position`): one MEIC
condor through its session, re-read from the store the run read: each side's cost to close at mid at
every stored sample until its own exit, then its recorded exit price; the position's P&L at those
marks; spot and VIX at or before each sample. At mid and before any fill model, so it shows the market
the trade sat in, not a second replay. A leg outside the stored strike band is a gap, never a
neighbour's quote. MEIC only: other schemas name no strikes in their ledgers. A run report records
its store file as `store`; a report written before 2026-10-31 is read against the untagged file at
its cadence.

**The grid block** (`grid.py`): a study that tests one parameter at several values (items
23, 24, 43) writes `grid` into its result file: each setting's own mean net per session, and a label
for the live setting and each passing one (`core.robustness.neighbourhood`, with `not_positive` and
`neighbour_better` taken first because the core's two words misdescribe them). The studies board
draws it. Description only: it never re-chooses a setting or moves a verdict. A finished study gains
the block with its script's `--annotate FILE`, which reads the file and replays nothing, so no holdout
is spent twice.

**The gates-off oracle** (`gateoracle.py`, `packages/backtest/scripts/meic_gate_oracle.py --arm A`): what
each switchable entry gate a MEIC arm has on adds over the trigger it gates. One design run
replays the arm, `A-open` (every listed gate off) and `A-no-<gate>` (each gate off alone); the read
pairs each `-no-` arm with `A` per session (leave-one-out), and credits every `-open` trade to the
gate `A`'s `entry_attempts` refused on the same tick (first refusal only, so overlapping gates go to
the earlier; a spacing or capacity refusal is `path`). MEIC's reasons and keys are spelt here, since
the package may not import it; a test holds them to `paper.py`'s source. A read, never a verdict: the
holdout is refused, nothing is a trial, and no live gate moves.

**Signal studies** (`signalstudy.py`, pure; `packages/backtest/scripts/signal_study.py run --request
FILE` reads the bars; `read signals` / `read signal --name`): a `core.signals` spec's firings and what
the underlying did after each, per horizon and by year, against a control. The rules a change must keep:

- **The control knows only the past.** At each firing minute it takes the sessions that had not fired
  by then. A session that fires later that day is in it, because "never fired that day" would pick
  sessions by their own outcome. Its weights match the firings' minutes.
- **Intraday expected moves read only the vol index's closes from before the entry**
  (`EM_INDEX`, the Cboe files). An entry at 16:00 reads its own session's close.
- **Only exchange trading days are read**, and intraday only sessions whose bars reach the close. The
  recorder writes weekends at a flat price.
- **Event signals are refused**, because the events calendar does not reach the history.
- **Where a result lives depends on whether it is licensed** (`paths.signals_dir`): over the vendor's
  store under `results/`, else beside it.

Descriptive only: no trial, no verdict, nothing to the advisor. A vendor result records
`reads_holdout` when its sessions reach the configured holdout window.

**Automation backtests** (`packages/backtest/scripts/automation_backtest.py run --arms --symbol --from --to
--name [--source]`): the automations group's automations replayed by `cherrypick.automations.driver`, the engine paper runs. The
store is the source registry's best cover for the symbol's same-day chains over the dates (or the one
named); a vendor store only while `licensing.status` is active, so new work never reads the vendor's
data after its subscription ends. An arm that does not expire the session it enters reads end-of-day
chains instead (`dolt-chains`, or `vendor-eod-chains` while licensed, its delta computed from mids by
`core.chaindelta`) at one tick a session, 15:59, with daily bars from the stock history (raw);
`--min-density` accepts Dolt's thinner years knowingly. An arm that declares a volume symbol reads it
from the recorded one-minute bars (`profile-bars`, regular hours), and the run is refused where those
do not cover the dates. The result is an explore run (no trial, no
verdict; `signal_versions` names each library signal an arm read, by version) under `paths.automations_dir`: `results/automations/` when it read the vendor's store, beside
it otherwise. The Builder's runs (`builder-<name>-<stamp>.json`) are read back by `read automation-runs`
and `read automation-run` (`automationcards.py`, pure): the net is the verdict kind's, every year row is
gross - fees - settlement - slippage = P&L, trades fall in their exit year, the drawdown is the closed
trades', skips are the driver's own reasons without `entered`, and a change is a key of the automation
each run stored (its switch and notes ignored).

**The advisor's specs** (`run-advised`, `advised.py`): the advisor queues `backtest_spec` replies it
admitted against flies' own advice bounds (off until `advisor.backtest_proposals`); this reads the
queue by subprocess, turns each into a config-defined arm `advised:<name>-<id>` (the base arm's params
with the overlay, the rule flies' paper loop uses, which flies' replay accepts), runs them over the
DESIGN window only, and writes each result back. Each is a trial. Every run replays from its own
snapshot of the flies config, kept beside its ledgers (`flies-config.json`).

**The flow fetch** (`fetch-flow`, `flowfetch.py`; `research/flow-signal-plan.md` step 1): each stock's
day of option prints with the quote at each, grouped into orders by `flow.py` (pure, and the same
rules the broker's nightly feed will import), every order of `flow.KEEP_PREMIUM` or more kept with
its contract's open interest either side, and every order's premium in a per-day histogram for the
top-share rule. Its own store, `flow.db`, in the marked licensed directory. A day waits as
`awaiting_oi` until the next session's open interest is reported, and is fetched again whole.

- **The rules are declared in the plan and never tuned here**: the condition sets, `ORDER_GAP_S`,
  `READ_EDGE`, `CUTS`, `TOP_SHARE`, `OPEN_SHARE`. Changing one is changing the declared test.
- **Most of a day's cost is not the vendor's network**: the library converts the reply row by row
  in Python, and a liquid name's day is hundreds of thousands of prints, so the vendor's reply time
  also varies widely between requests. The full design window across the universe does not fit in
  nights; how it is thinned is decided before any outcome is read (the plan).

**The open-interest fetch** (`fetch-oi`, `oifetch.py`; item 30): each name's open interest per
contract per session, every expiry from the session through `MAX_DAYS` (100) calendar days, one
vendor call a name-day, from a `{sessions: {session: [symbols]}}` plan as `fetch-flow` reads. Its own
store, `open_interest.db`, in the marked licensed directory. Session D's row is the vendor's morning
report, the previous close's figure, so a reading at D's close uses nothing published after it.

**The intraday chain fetch** (`fetch-intraday-chains`, `intradayfetch.py`; item 31): a single name's expiry
chain at the bars of a window of one session (`ThetaDataClient.chain_window`: quote, delta, implied
volatility and the underlying's price per contract), from a `{requests: [{symbol, session, expiration,
start, end, interval?}]}` file as `fetch-chains` reads. Its own store, `intraday_chains.db`, in the
marked licensed directory; resumable, a refusal recorded and retried, `--stop-by` bounds a run, so the
research queue runs it. The coverage pilot that decides item 31's basis is `packages/backtest/scripts/earnings_item31.py`
(`plan`, `pilot`); it reads presence and counts, never a price.

A request may also carry `strike_range` (a positive whole number of strikes either side of the money; item
38 asks for 50, its listing probes 2); absent means the whole chain, as item 31's files always have. The
range is no part of a request's identity, so two requests for overlapping bars of one expiry should ask for
the same range, and a file repeating a window with two ranges is refused. Item 38 (the calendars module's
own rule, `packages/backtest/scripts/calendars_item38.py`) finds its start with the same verb: its listing step is a file of
one-bar probes, not a call to the vendor's expirations list, which a `script` row may not make.

A file may name its store at the top (`"store"`), one of `intradayfetch.STORES`: the shared one or the
full set's per-underlying stores (`fullset.INTRADAY_STORES`, same schema). Any other name is refused before
a request is sent.

**The full set** (`fullset.py`, pure; `packages/backtest/scripts/fetch_full_set.py`): SPX, QQQ, XSP, SPY,
IWM, GLD and TLT from 2020-01-01 in three stores per underlying (same-day at 60 s under the `full` tag,
1-7 days out at one minute, 8-90 days out at five minutes), plus every weekly, monthly and quarter-end
expiry's end-of-day path and open interest. The plan asks for candidate expiries by the exchanges' rules,
never the vendor's listing, so planning holds no vendor session; an expiry never listed comes back
`empty` once. `plan` writes the request files, the script's CHAIN queues the fetches (probe first), and
`status` reads overall, per-part and per-underlying progress from the stores' own ledgers.

**The add-on weeklies fetch** (`fetch-addon-weeklies`, `addonfetch.py`; item 35): SPXW weekly put minute
greeks over a bwb position's life, plus a 10:00 strip of ATM calls, from a `{requests: [{expiration, first,
last}]}` file (an expiry and a span of sessions, so a Thursday entry into a Friday is a one-session
request). It writes `addon_weeklies.db`, the add-on standalone study's store, in the shape that study
read; `fetched_spans` records each request and a span replaces only its own sessions. The research queue
runs it as a `fetch-addon-weeklies` row; `packages/backtest/scripts/fetch_addon_weeklies.py requests` works out what the
store lacks. `fetch-oi` keeps only an SPXW weekly's expiries within ten days (`oifetch.WEEKLY_ONLY`),
not the hundred days of dailies the other roots keep.
