# cherrypick-backtest — Operational Instructions

> Operating contract for the suite's **backtests over licensed vendor history**. Suite-wide context is
> in the root [documentation index](../../docs/README.md). What is built so far, and what comes
> next, is listed under "State" below.

This package replays the suite's **own rules** over years of vendor quotes (ThetaData, Options
Standard), so a question about a strategy can be answered in weeks instead of waiting for paper arms
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
- **Every store is declared, with the code that rebuilds it (2026-10-25).** A new file in the vendor
  directory is added to `stores.STORES` in the same change that adds its fetch code, and that code is
  committed (a package verb or a `scripts/` file; a test fails on a script that is not). The backup
  skips licensed data and `purge` removes it, so a store survives only by being rebuildable. The Data
  tab lists every declared store and flags any undeclared file. Two stores were built from session
  scratch until this date (`addon_weeklies.db`, `SPY-monthly-1545.db`); their fetch code is now in
  `scripts/`.
- **The data must stay purgeable.** It lives in one directory carrying a marker file
  (`paths.LICENSED_MARKER`); `purge` removes only marked directories (dry run by default), and the
  nightly backup skips any marked directory (`orchestrator/backup.py` spells the same name; a test
  here holds the two equal). The operator records the end of a subscription in
  `vendor.license.terminated_on`, and `licensing.status` turns it into a purge deadline.

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
queue's `fetch-sessions`, never by the nightly job. A study points the runner at it with
`store.tagged(settings, tag)`; such a store is never validated, so its runs are provisional.

**History is 1-minute; the recorder era is 15 s (2026-10-17).** The vendor drops every sub-minute
history request at 60 s before 2026 (15 s, 30 s and tick alike, even ten minutes of two strikes, by
the library and by the terminal's REST API). That is a vendor behaviour, not a finding from its data.
So history is fetched at `--cadence 60`, and validation must measure what a minute costs the replay
(a completion that touches between minutes is missed) by validating 60 s vendor sessions against the
recorder's 15 s ones, never by assuming it.

**One vendor session per account.** A second login with the same key ends the first, and every later
request fails `UNAUTHENTICATED`. A fetch stops on that (`vendor.FATAL`) rather than recording every
remaining session as refused. **So the client takes a lock before it logs in**
(`<home>/state/thetadata-session.lock`, the holder's PID, `core.looplock`'s never-steal-from-the-
living semantics): a second client built here WAITS, asking again every minute and logging who holds
it, and gives up with `session_in_use` after six hours. Every fetch, the nightly job and any script
using `client_for` take it; the Theta Terminal and the library used by hand do not, so never run
those while a fetch runs.

How each field is rebuilt from the vendor is declared in `fetch.py`'s docstring. The two that differ
from a recording in kind, and that validation must measure rather than assume:

- **`age_s` is NULL.** An interval row is the quote standing at that instant; its age is not
  reported, and the replay treats NULL as fresh.
- **Gamma is computed** (`greeks.bs_gamma`) from the vendor's IV, because gamma is a Professional
  field. The recorder's gamma came from the live feed.

A session is written whole or not at all; a refusal is a `vendor_sessions` row with its reason, never
an empty session. `fetch` is resumable (a complete session is skipped) and `--refetch` clears a
session before rewriting it.

**The VIX minute series (`vixfetch`, 2026-10-18).** MEIC's replay reads VIX at each tick, because MEIC
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

## Validation comes before history

`validate` runs on the days both the recorder and the vendor store hold, in three layers (quotes,
the rebuilt GEX surface, and flies' own `replay-check` run once per source, by subprocess). **History
runs stay locked until it unlocks** (`validate.UNLOCK_SESSIONS` sessions, and the vendor-sourced
replay at least as good as the recorder-sourced one on every declared check). The bar compares the
vendor with our own recording, not with perfection: the recorder-sourced replay itself fails flies'
completion-timing check. Its reports are derived results and are written only under
`results/validation/`.

**Amended 2026-10-17: the recording is compared at the vendor's cadence.** The bar as first written
compared a 60 s store with the 15 s recording, which measures the cadence rather than the vendor. The
bar is now the vendor against the recording thinned to the vendor's own instants
(`validate.thin_to_vendor_instants`, a temporary copy that lives only for the check); what a minute
costs is reported beside it (`cadence_cost`: each measure full and thinned, and the completion
outcome, which is what moves P&L), never a bar and never assumed. Stores of the same cadence compare
as before. Thresholds, checks and `UNLOCK_SESSIONS` are unchanged. The validation reports behind this
amendment are kept locally under `~/.cherrypick`, per the licence.

A profile disagreement is read against each side's strike count, which the report carries: either
side's profile can be the thinner one, and fewer strikes move the walls and the flip.

**MEIC's delta source (2026-10-27).** Before that session MEIC chose strikes by the data feed's delta,
which lags its quotes by about a minute. The recording carries that lag and the vendor's delta (taken
at its quotes' instant) does not, so on its default delta (each session's own) the vendor replay
cannot choose paper's strikes whatever its data. `validate --module meic --delta-source computed`
replays both sources on `core.chaindelta`'s delta from each one's own mids, so the decisions layer
compares the data. That report is its own file (`...-delta-computed.json`) and records
`delta_source`; history runs use the config's source, `computed` from 2026-10-27.

## The runner

`run flies --arms ... --window validation|design|holdout` replays each session through flies itself
(`flies.cli replay --history-db`, a subprocess; this package imports no strategy module), settles at
the store's close for the day, and reads the scratch ledgers back through `core.ledgers`, so the net
is paper's arithmetic. Control always runs beside the named arms; every arm is paired with it
(`core.compare.pair_with_control`) and flagged `no_exposure` when it matched control to the cent.

- **`validation`** (from 2026-10-13, the recorder era) always runs and is no trial.
- **`design`** runs only once validation has unlocked this store's cadence. **Every (spec, arm) is a
  trial** (`results.trials`), keyed by a hash of what decides its trades (flies' `defaults`, the arm's
  entry, `symbols`, cadence, fill model): re-running a spec adds nothing, changing a parameter adds
  one. DSR deflates by this count.
- **`holdout`** needs `--spend-holdout` and is spent once per spec, ever. `control` never spends it
  (2026-10-29): it runs beside every arm unjudged, and spending it refused every later study's holdout.

Statistics (`stats.py`) are per session, never annualised: PSR, DSR at the trial count, minimum track
record, and a stationary-bootstrap interval. The fill model is flies paper's own (`FILL_MODEL`).

**MEIC is the second module (`run meic`, 2026-10-18).** What differs per module is one `Module` entry
in `runner.MODULES`: schema, fill model, config loader, the spec a trial is keyed on, the replay
command. MEIC replays through `python -m cherrypick.meic.replay run` (its own engine, tick by tick at
paper's 60 s cadence) from a snapshot of BOTH its files, `meic.json` and `meic.risk.json`
(`$MEIC_CONFIG` / `$MEIC_RISK_CONFIG` for the subprocess), and reads back through `core.ledgers`'
`meic_ic` reader. A MEIC trial is keyed on the arm's params merged over the base with comments
dropped, so an inherited base key that changes is a new trial for every arm, and a reworded note is
none. Advised arms stay flies'. **Each module validates and unlocks its own history**: `validate
--module meic` runs `meic.replay check` per source under the same cadence-matched bar, the report
names its module, and a flies report unlocks nothing for MEIC (`unlocked(..., module=)`). The fence
now forbids importing any strategy module (`STRATEGY` in `test_fence.py`): everything is replayed by
subprocess, and MEIC's import would also pull the broker in where the AST scan cannot see it.

**Declared gaps:** the suite's events calendar does not reach the history (2022–2024 read unknown on
BEA, FRED and FOMC; 2025 on FRED), so an event-conditioned arm cannot be backtested until it is
backfilled; and the vendor store carries no volume, so the regime rows' flow columns are NULL.

## State

Built: the config, the key verbs, the licence clock, purge, the backup skip, the fence, the store, the
fetch (SPXW 0DTE, any cadence the profile cadence divides), regime rows, validation and the flies
and MEIC runners with their statistics, and the flow fetch (below). Its declared test (item 32) is built, not yet run: the rules are
`flowstudy.py` (pure), the stores are read by `scripts/flow_signal_item32.py`, which reads its grid
from the plan file (`scripts/flow_fetch_plan.py`, never recounted from the calendar) and spends the
holdout once (a marker is written before the read).
**The end-of-day chain fetch** (`chainfetch.py`, `fetch-chains`, `chains-status`, 2026-10-20):
- **Event-driven:** a study writes the (name, session, expiry) requests it needs, and this fetches
  exactly those, one session-chain a request, into `vendor/<name>/eod_chains.db` beside the marker.
- **Why event-driven:** the Standard tier serves single-stock end-of-day chains from 2016 only, has
  no whole-market daily file, and answers about a request a second, so a full crawl would take weeks.
- **What each request becomes:** complete, empty or refused (with why). A re-run skips the first two
  and retries refusals; a fatal refusal stops the run. Workers share the one session, and every
  write is on the main thread.
- Its first user is edge-hypotheses item 14.
- **Daily paths** (`fetch-chain-paths`, 2026-10-23): a request names an expiry and a date range, and
  every session's close in that range lands as its own complete session-chain, so a replay can mark
  a held contract each day. The vendor drops a two-month whole-chain range at 60 s, so the client
  asks a week at a time. An empty week before the expiry was listed is skipped; an empty week
  after one that answered refuses the path (`path_gap`), so it is retried rather than stored with a
  hole. Paths are recorded in `eod_chain_paths`, resumable the same way, and `--stop-by` bounds a
  run as it does `fetch-chains` (2026-10-28), so the research queue can run one.
The console's Backtest page reads `python -m cherrypick.backtest read overview|run` (`readout.py`)
and is **fail-closed**: shown only when the orchestrator's `features.backtest` is true
(`orchestrator/backtestgate.py`: the operator's config enabled, the licence active, the key in the
keyring, a complete session in the store), with its server routes refusing on their own.
`overview` carries `provisional_looks` beside `trials` (2026-10-30): the distinct specs run as
provisional looks, per module. A look records no trial, so it never enters a DSR; the count is for
the reader, who would otherwise see "trials: none" after dozens of looks at the same history.
**`read overview` never counts a store (2026-10-30).** Its inventory is the cache as last counted; a
store changed since then comes back marked `pending`. The `inventory` verb does the counting, run by
the supervisor's `backtest-inventory` job. A cold count of the largest stores takes over a minute and
the console's bridge gives a read 30 s, so a read that counted was killed mid-count, banked nothing,
and the page showed "unavailable" while a queue fetch kept a store changing.

**The run cards** (`cards.py`, 2026-10-26): `read run` also returns each arm's path, drawdown
periods, month and year rows, session and trade anatomy, premium (fee drag, and capture rate from
`core.metrics`), exits (`core.ledgers`' `outcome`), an edge trend (first half against second,
block-bootstrap interval) and an account read. They come from `session_results` and the ledgers a run
keeps under `results/runs/<run_id>/`. They are views: no trial, nothing to the advisor. **The account
is a lens, not a replay**: fixed lots, never compounded, so a chosen capital (`--capital`) changes
percentages only, and is one account for every arm. Absent, each arm reads against its own peak
session risk (MEIC's condors' max losses summed, flies' book peak risk), never one shared peak, which
MEIC's control sampling stream would set for every arm; a schema with neither has no account.
`read trades --id --arm` (2026-10-31) lists one arm's trades from the same ledgers (`trade_log`):
each trade's times, its exit reason in the module's words and its money columns as `core.ledgers`
names them, a field the schema does not record as null. Its own read, so `read run` stays light.

**The grid block** (`grid.py`, 2026-10-31): a study that tests one parameter at several values (items
23, 24, 43) writes `grid` into its result file: each setting's own mean net per session, and a label
for the live setting and each passing one (`core.robustness.neighbourhood`, with `not_positive` and
`neighbour_better` taken first because the core's two words misdescribe them). The studies board
draws it. Description only: it never re-chooses a setting or moves a verdict. A finished study gains
the block with its script's `--annotate FILE`, which reads the file and replays nothing, so no holdout
is spent twice.

**The advisor's specs** (`run-advised`, `advised.py`): the advisor queues `backtest_spec` replies it
admitted against flies' own advice bounds (off until `advisor.backtest_proposals`); this reads the
queue by subprocess, turns each into a config-defined arm `advised:<name>-<id>` (the base arm's params
with the overlay, the rule flies' paper loop uses, which flies' replay accepts), runs them over the
DESIGN window only, and writes each result back. Each is a trial. Every run replays from its own
snapshot of the flies config, kept beside its ledgers (`flies-config.json`).

**The flow fetch** (`fetch-flow`, `flowfetch.py`; `docs/flow-signal-plan.md` step 1): each stock's
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
research queue runs it. The coverage pilot that decides item 31's basis is `scripts/earnings_item31.py`
(`plan`, `pilot`); it reads presence and counts, never a price.

A request may also carry `strike_range` (a positive whole number of strikes either side of the money; item
38 asks for 50, its listing probes 2); absent means the whole chain, as item 31's files always have. The
range is no part of a request's identity, so two requests for overlapping bars of one expiry should ask for
the same range, and a file repeating a window with two ranges is refused. Item 38 (the calendars module's
own rule, `scripts/calendars_item38.py`) finds its start with the same verb: its listing step is a file of
one-bar probes, not a call to the vendor's expirations list, which a `script` row may not make.

**The add-on weeklies fetch** (`fetch-addon-weeklies`, `addonfetch.py`; item 35): SPXW weekly put minute
greeks over a bwb position's life, plus a 10:00 strip of ATM calls, from a `{requests: [{expiration, first,
last}]}` file (an expiry and a span of sessions, so a Thursday entry into a Friday is a one-session
request). It writes `addon_weeklies.db`, the add-on standalone study's store, in the shape that study
read; `fetched_spans` records each request and a span replaces only its own sessions. The research queue
runs it as a `fetch-addon-weeklies` row; `scripts/fetch_addon_weeklies.py requests` works out what the
store lacks. `fetch-oi` keeps only an SPXW weekly's expiries within ten days (`oifetch.WEEKLY_ONLY`),
not the hundred days of dailies the other roots keep.
