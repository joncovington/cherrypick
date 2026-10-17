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

## State

Built: the config, the key verbs, the licence clock, purge, the backup skip and the fence.
Not yet: the fetch, the store (the gex recorder's schema, one SQLite file a month), validation against
the recorder, the flies runner and its statistics, the console page (hidden unless config, key,
data and an active licence are all present), and the advisor's fenced proposals.
