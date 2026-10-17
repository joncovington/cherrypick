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
and some at 60 s.

**History is 1-minute; the recorder era is 15 s (2026-10-17).** The vendor drops every sub-minute
history request at 60 s before 2026 (15 s, 30 s and tick alike, even ten minutes of two strikes, by
the library and by the terminal's REST API). That is a vendor behaviour, not a finding from its data.
So history is fetched at `--cadence 60`, and validation must measure what a minute costs the replay
(a completion that touches between minutes is missed) by validating 60 s vendor sessions against the
recorder's 15 s ones, never by assuming it.

**One vendor session per account.** A second login with the same key ends the first, and every later
request fails `UNAUTHENTICATED`. A fetch stops on that (`vendor.FATAL`) rather than recording every
remaining session as refused; never run a second client (a probe, the terminal) while a fetch runs.

How each field is rebuilt from the vendor is declared in `fetch.py`'s docstring. The two that differ
from a recording in kind, and that validation must measure rather than assume:

- **`age_s` is NULL.** An interval row is the quote standing at that instant; its age is not
  reported, and the replay treats NULL as fresh.
- **Gamma is computed** (`greeks.bs_gamma`) from the vendor's IV, because gamma is a Professional
  field. The recorder's gamma came from the live feed.

A session is written whole or not at all; a refusal is a `vendor_sessions` row with its reason, never
an empty session. `fetch` is resumable (a complete session is skipped) and `--refetch` clears a
session before rewriting it.

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

What the first read already showed (described here, never quantified): the recorder's own profile
can be the thinner one (on a monthly-expiry session it carried visibly fewer strikes, which moves
walls and the flip), so a profile disagreement is read against each side's strike count, which the
report carries.

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
- **`holdout`** needs `--spend-holdout` and is spent once per spec, ever.

Statistics (`stats.py`) are per session, never annualised: PSR, DSR at the trial count, minimum track
record, and a stationary-bootstrap interval. The fill model is flies paper's own (`FILL_MODEL`).

**Declared gaps:** the suite's events calendar does not reach the history (2022–2024 read unknown on
BEA, FRED and FOMC; 2025 on FRED), so an event-conditioned arm cannot be backtested until it is
backfilled; and the vendor store carries no volume, so the regime rows' flow columns are NULL.

## State

Built: the config, the key verbs, the licence clock, purge, the backup skip, the fence, the store, the
fetch (SPXW 0DTE, any cadence the profile cadence divides), regime rows, validation and the flies
runner with its statistics.
The console's Backtest page reads `python -m cherrypick.backtest read overview|run` (`readout.py`)
and is **fail-closed**: shown only when the orchestrator's `features.backtest` is true
(`orchestrator/backtestgate.py`: the operator's config enabled, the licence active, the key in the
keyring, a complete session in the store), with its server routes refusing on their own.

**The advisor's specs** (`run-advised`, `advised.py`): the advisor queues `backtest_spec` replies it
admitted against flies' own advice bounds (off until `advisor.backtest_proposals`); this reads the
queue by subprocess, turns each into a config-defined arm `advised:<name>-<id>` (the base arm's params
with the overlay, the rule flies' paper loop uses, which flies' replay accepts), runs them over the
DESIGN window only, and writes each result back. Each is a trial. Every run replays from its own
snapshot of the flies config, kept beside its ledgers (`flies-config.json`).
