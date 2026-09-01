# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

cherrypick-gex is the **GEX (gamma-exposure) engine and market recorder** for the trading-tool suite —
the compute half of a self-hosted gexbot.com / SpotGamma / MenthorQ. It computes GEX via the shared
`cherrypick.core.gex` engine and records the spot trail; the **console** (`packages/console`) is what
renders it, on <http://127.0.0.1:5070/gex>. It serves nothing itself, places no orders, and never
touches live trading.

Since 2026-08-23 the recorder also carries the **suite-level market-regime series**
(`cherrypick/gex/regime.py`; design record `docs/regime-recorder-plan.md` at the repo root): one row
per reading per ~minute during RTH — the vol complex (VIX/VIX3M/VIX1D/VVIX), breadth and cross-asset
quotes (SPY/RSP, HYG/LQD, TLT, GLD/USO as labeled commodity proxies, the eleven SPDR
sectors) — into `market_regime_history`, plus a
permanent `daily_closes` table harvested from `stream_summary`. **A session whose own `day_close`
is missing is recovered from the NEXT session's `prev_day_close`** — the same number written by the
same feed one row later, and only where the calendar confirms the two rows are consecutive trading
days (a `prev_day_close` carries no date, so trusting it across a gap would attribute one session's
close to another). That route exists because a producer defect erased SPX's and XSP's closes for 22
sessions from 2026-07-29, freezing this series while every other symbol stayed current; it repairs
retroactively on the next run, and the recovered rows are sourced `stream_summary:prev_day_close`
so a reader can tell a close the feed confirmed on the day from one reconstructed a row later. Raw measures only (ratios and
dispersion are read-side derivations in `cherrypick.core.regime`, the one join helper every consumer
goes through); RTH-gated and basis-stamped, with a stale or missing quote written as a `usable = 0`
refusal row, never a frozen value. The recorder declares the reading symbols itself as quote-only
`legs` in its stream request — coverage must not depend on another module's declaration — and a
coverage test drives off `regime.READINGS`, so a new reading without its subscription fails the
build. The charter widened deliberately (recorder of GEX → recorder of market state): one daemon,
one store, one supervision entry, and the console already reads this database.

**The recorder publishes its liveness** (2026-08-23, the flies heartbeat convention): the daemon
touches `data/gex/recorder.heartbeat` at the top of every tick, and `record --status` reports
`stalled: true` when the beat goes silent past `RECORDER_STALL_SECONDS`. That is the signal the
orchestrator's watchdog recycles on (stop then start — a plain start would lose to the wedged
pid's single-instance lock). A pid check alone reads a wedged loop as healthy — the 2026-07-23
stale-config incident's shape with a different cause — and a missing heartbeat degrades to "not
silence-supervised", never to a restart. Two modes: **piggyback** (the default — `source.stream_cache_db` resolves to the suite's
canonical shared cache, `~/.cherrypick/data/marketdata/stream_cache.db`, read read-only) or
**standalone** (`run.py stream` runs `cherrypick.core.streamer` to populate its own cache path instead,
e.g. `data/stream_cache.db`, if `source.stream_cache_db` is repointed there).

**This module's own dashboard (`serve.py`), its WebSocket push (`push.py`), and its suite-dashboard
section card (`section.py`) were deleted on 2026-08-12**, when the console became the suite's one read
surface. The console reads `gex_history.db` and the stream cache directly and computes the profile in
TypeScript, so nothing here serves HTTP any more. Recover them from the `pre-console-only` tag.

Suite-wide context lives in the root [documentation index](../../docs/README.md) (see especially
[strategy-engines.md](../../docs/strategy-engines.md) for how GEX fits the suite).

## Commands

```bash
python run.py stream --symbol SPX    # run the streamer -> own data/stream_cache.db (standalone mode)
python run.py record                 # always-on spot-trail recorder (run alongside the streamer; --once/--interval)
python run.py gex --symbol SPX --json # one-shot payload to the terminal
python run.py pin-study [--json]     # which recorded level the close settled nearest, over stored history
# To look at it: the console's GEX page, http://127.0.0.1:5070/gex
python -m pytest                     # tests seed a temp cache; no streamer required
ruff check . && ruff format .        # line-length 110
```

Config: copy `config.example.json` → `config.json` (git-ignored). Paths in it resolve relative to the
config file's directory — never hardcode absolute paths.

## Architecture

- **`cherrypick/gex/streamer.py`** — the standalone streamer wrapper: runs `cherrypick.core.streamer.ChainStreamer`
  with this module's own keyring session, writing its own cache. Thin — no open-position policy, ORB, or
  HTTP API (those stay in MEIC's wrapper). The one place this module authenticates / talks to the broker.
- **`cherrypick/gex/provider.py`** — turns a data source into a `GexSnapshot`. Reads a stream cache with `?mode=ro`
  and picks the nearest expiration that actually has live greeks. It owns the stream-cache read shape;
  add a new source by adding a provider, not by editing the schema-aware reader.
- **`cherrypick/gex/service.py`** — `build_gex(cfg, symbol)`: provider → `cherrypick.core.gex.compute_gex_profile`
  → chart payload (reads the spot trail **read-only**). The pure, HTTP-free seam. `record_spots(cfg)`
  samples **every** offered symbol's spot into this module's **own** SQLite (`history_db`) so a trail has
  no gap when the viewer switches symbols; `run_recorder(cfg)` is the always-on loop (`run.py record`).
- **`cherrypick/gex/cli.py` + `run.py`** — the CLI: `gex` (one-shot payload), `stream`, `record`,
  `pin-study` (below).
- **`cherrypick/gex/pin_study.py`** — a read-side study over this module's own history: which
  recorded level (call wall / zero gamma / put wall) each session's close settled nearest, per
  regime, for both the session's own first RTH reading and the prior session's final one. Exists to
  answer level-pinning strategy claims from recorded data before any module grows an entry rule;
  read-only, RTH-gated the hard way (calendar-date equality, not hours alone — this history has
  crossed midnight mid-session once), and expired-chain rows are excluded by `core.regime`'s own
  forward-only rule. It reports skipped sessions with reasons and carries n everywhere; it draws no
  conclusion by itself.
  Nothing here is an integration point any more — the console reads this module's **data**, not its
  commands, which is why deleting the serving layer changed no consumer.
- **`cherrypick.core`** — an installed dependency (`packages/core` in this monorepo, `pip install -e
  packages/core`); the GEX math (`core.gex`), the streaming engine (`core.streamer`), and the cache
  schema (`core.streamcache`) live there so this module and cherrypick-meic compute/stream identically.

## Invariants (do not violate)

- **Only the streamer talks to the broker.** `provider`/`service` read files and never open a
  broker session or outward network connection. The computation is deterministic throughout — a pure
  function over an option-chain snapshot, which is what lets a profile be recomputed from history.
- **Never write a cache you don't own.** In piggyback mode `source.stream_cache_db` points at MEIC's
  cache — the provider opens it `?mode=ro` and must never write it; the streamer only writes this
  module's own cache. The spot trail goes to this module's own `history_db`.
- **GEX math and the streamer engine stay in `cherrypick.core`.** Do not fork the dollar-gamma / walls /
  zero-gamma math or the streaming engine into this repo — the whole point is one shared implementation
  with cherrypick-meic (the GEX math drifted ~75× once when copied).
- **Instruction files hold no code and no logs.** This file is build commands + guidelines only.
- **Portable paths.** Never hardcode absolute paths, usernames, or drive letters; derive from
  `Path(__file__)` or config. Scratch work lives in a git-ignored `.tmp/`.


## The GEX horizon is forward-only (2026-08-26)

`provider.snapshot_from_stream_cache` picks the nearest expiration **at or after today**, and never
one that has passed. It used to order candidates by `ABS(JULIANDAY(expiration) - JULIANDAY('now'))`,
which ranks yesterday's chain as near as tomorrow's and nearer than the day after — and because
nothing prunes `stream_greeks`, an expired chain keeps its last gammas, satisfies the has-greeks
test, and wins the horizon. The result is a GEX reading computed from contracts that no longer
exist, frozen at one value for hours.

It was not rare. Measured over `gex_regime_history` on 2026-08-26: **3,991 of 10,516 readings (38%)
came from an already-expired chain**, nearly all a single constant `net_gex` repeated across a whole
session — 96 readings on 2026-08-19 all at −16.99bn off the expired 08-18 chain, and the same shape
across 23 sessions.

**Where it surfaced.** The advisor reported on 2026-08-21 that "a regime signal that three sources
in this pack describe three different ways" made the GEX gate unassessable: `market.gex.today_counts`
said 181 positive against 23 negative while meic's gate refused 349 entries. Those are two different
series. meic's gate reads its own per-iteration `get_gex` over the same-day 0DTE chain; the pack's
counts come from this recorder, which on that session was on a stale or non-0DTE chain. The advisor
was right that nobody had characterised the signal, and right to refuse to tune a threshold on it.

**What it does and does not affect.** `meic.analytics.gex_gate_counterfactual` is unaffected: it
scores `gex_positive_at_entry`, which `paper._gex_at_entry` stamps from the same snapshot dict the
gate itself tests, on the same tick — the gate's own input, never this series. What IS affected is
anything reading `gex_regime_history`: the advisor's fact pack, the console's GEX page, and
`cherrypick.core.regime.regime_at`, the suite's shared regime attribution.

**The historical rows are filtered on read, not deleted.** `core.regime` now requires
`expiration >= trade_date`. The rows carry the expiration they used, so the bad ones are
identifiable, and a regime series is evidence — the honest move is to stop believing them, not to
erase the record that they happened. A lookup whose only samples are expired-chain ones now reports
`no_sample_at_or_before` or falls through to the staleness check, both of which are true.

## A reading can be entitled and still not sustain a series (SKEW, 2026-08-26)

`regime.INTERMITTENT_INTRADAY` declares readings whose live quote the feed serves only in bursts.
Same rule and same reason as `overview._NO_DAILY_SERIES`: nothing in the data distinguishes "the
feed was down today" from "this symbol never sustains a series", and a permanent refusal that looks
temporary teaches a reader to skim the row.

SKEW is the case, and how it got here is the useful part. The 2026-08-24 entitlement probe printed
it (143.9) through the ordinary legs path and it was admitted on that evidence, which was correct
at the time. Three sessions of recording say otherwise: **30 usable samples of 1,105**, arriving in
bursts with silence between — 22 prints between 09:49 and 11:46 ET on 08-24 then nothing, one print
on 08-25, three on 08-26 with a 7.9-hour gap. VIX over the same window printed 363 times at a
60-second median. Its daily series had already failed the same way (five scattered rows over seven
months, one of them a zero), so both horizons agree.

**It stays in `READINGS` deliberately.** The refusal rows are the evidence that it is unavailable,
`dropped_readings` would flag a silent removal, and if the feed ever sustains the series fills with
no code change. The value is still never recorded — a burst-feed quote is as stale as any other.
What the declaration buys is that the refusal is EXPECTED: rows say `intermittent_feed` rather than
`stale_quote`, and `sample()` returns `expected_unusable` beside `usable`, so a health read is not
permanently depressed by something the recorder cannot fix. Without that, "skew usable=30 of 1,105"
reads as a recorder fault and gets re-investigated from scratch — which is exactly what happened.

**The generalisable point: a probe answers "is this entitled". It does not answer "can this sustain
a series".** For SKEW the two came apart. Watch a new reading for a session before admitting it on
one print.
