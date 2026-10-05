# CLAUDE.md

Guidance for Claude Code when working in the gex package.

## What this is

cherrypick-gex is the **GEX (gamma-exposure) engine and market recorder** — the compute half of a
self-hosted gexbot / SpotGamma / MenthorQ. It computes GEX through the shared `cherrypick.core.gex`
engine and records the spot trail; the **console** renders it at <http://127.0.0.1:5070/gex>. It
serves nothing itself (its old dashboard, WebSocket push and section card are recoverable from the
`pre-console-only` tag), places no orders, and never touches live trading.

The recorder also carries the **suite-level market-regime series** (`cherrypick/gex/regime.py`;
design record [`docs/regime-recorder-plan.md`](../../docs/regime-recorder-plan.md)): one row per
reading per ~minute during RTH (the vol complex, breadth and cross-asset quotes, GLD/USO as labelled
commodity proxies, the eleven SPDR sectors) into `market_regime_history`, plus a permanent
`daily_closes` table harvested from `stream_summary`. Rules:

- **Raw measures only.** Ratios and dispersion are read-side derivations in `cherrypick.core.regime`,
  the one join helper every consumer goes through.
- **RTH-gated and basis-stamped; a stale or missing quote is a `usable = 0` refusal row**, never a
  frozen value.
- **The recorder declares its reading symbols itself**, as quote-only `legs` in its stream request —
  coverage must not depend on another module's declaration. A coverage test drives off
  `regime.READINGS`, so a reading without its subscription fails the build.
- **A missing `day_close` is recovered from the NEXT session's `prev_day_close`**, only where the
  calendar confirms the two rows are consecutive trading days (a `prev_day_close` carries no date).
  It repairs retroactively, and recovered rows are sourced `stream_summary:prev_day_close`.
- **`regime.INTERMITTENT_INTRADAY`** declares readings the feed serves only in bursts (SKEW). They
  stay in `READINGS` and the value is still never recorded, but the refusal is EXPECTED: rows say
  `intermittent_feed` rather than `stale_quote`, and `sample()` returns `expected_unusable` beside
  `usable`, so a health read is not permanently depressed. Same rule as `overview._NO_DAILY_SERIES`.
- **An entitlement probe answers "is this entitled", not "can this sustain a series".** Watch a new
  reading for a session before admitting it on one print.

**Flow is recorded beside positioning** (2026-10-05, `service.FLOW_COLUMNS`): each regime row also
carries chain-wide call and put contracts traded, their dollar gamma, and the calls traded at the call
wall and the puts at the put wall, all session-cumulative. Raw measures, NULL before that date and never
backfilled (the cache keeps no volume history). The columns arrive by `ALTER TABLE` on the live
database, so every reader selects by name. They exist to ask whether call volume outweighing put
volume at the walls moves spot. The whole-chain `net_gex_vol` sign could not answer that: it tracked
spot against the open and predicted nothing after it.
- **Volume covers the streamed window only:** today's 0DTE expiration, the contracts the streamer
  subscribes (about ±300 SPX points, 204 of 484 listed on 2026-10-05). `volume_contracts` and
  `volume_low_strike`/`volume_high_strike` record that window on every row, so a jump in a total can
  be told apart from the window moving.
- **The full per-strike profile is kept** in `gex_profile_history` (`service.PROFILE_COLUMNS`: OI,
  volume, gamma and IV per side), one row per strike with any data, joined to its regime row on
  `(symbol, ts)`. About 100 strikes × 78 readings a session. It is what lets a past session's walls,
  flip or flow be recomputed under any definition; nothing before 2026-10-05 exists to rebuild.

**The recorder publishes its liveness.** The daemon touches `data/gex/recorder.heartbeat` at the top of
every tick, and `record --status` reports `stalled: true` past `RECORDER_STALL_SECONDS` — the signal
the orchestrator's watchdog recycles on (stop, then start). A missing heartbeat degrades to "not
silence-supervised", never to a restart.

Two modes: **piggyback** (default — `source.stream_cache_db` resolves to the suite's shared cache,
`~/.cherrypick/data/marketdata/stream_cache.db`, read read-only) or **standalone** (`run.py stream`
populates its own cache, e.g. `data/stream_cache.db`, if `source.stream_cache_db` is repointed there).

Suite context: the root [documentation index](../../docs/README.md), especially
[strategy-engines.md](../../docs/strategy-engines.md). The incidents behind the rules here are in
[docs/history.md](docs/history.md).

## Commands

```bash
python run.py stream --symbol SPX     # standalone streamer -> own data/stream_cache.db
python run.py record                  # always-on spot-trail + regime recorder (--once / --interval / --status)
python run.py gex --symbol SPX --json # one-shot payload to the terminal
python run.py pin-study [--json]      # which recorded level the close settled nearest, over stored history
python run.py repair-history [--json] # report off-hours gex_regime_history rows (--apply removes; DB copied first)
python -m pytest                      # tests seed a temp cache; no streamer required
ruff check . ; ruff format .          # line-length 110
```

Config: copy `config.example.json` → `config.json` (git-ignored); paths resolve relative to the config
file's directory.

## Architecture

- **`cherrypick/gex/streamer.py`** — thin standalone wrapper over `cherrypick.core.streamer.ChainStreamer`
  with this module's own keyring session and cache (no open-position policy, ORB or HTTP API — those
  stay in MEIC's wrapper). The one place this module talks to the broker.
- **`cherrypick/gex/provider.py`** — data source → `GexSnapshot`. Reads a stream cache `?mode=ro` and
  owns that read shape; add a source by adding a provider, not by editing the schema-aware reader.
- **`cherrypick/gex/service.py`** — `build_gex(cfg, symbol)`: provider → `compute_gex_profile` → chart
  payload (spot trail read-only); the pure, HTTP-free seam. `record_spots(cfg)` samples **every**
  offered symbol into this module's own `history_db` so a trail has no gap on a symbol switch;
  `run_recorder(cfg)` is the always-on loop.
- **`cherrypick/gex/cli.py` + `run.py`** — the CLI. Not an integration point: the console reads this
  module's **data**, not its commands.
- **`cherrypick/gex/pin_study.py`** — read-only study of which recorded level (call wall / zero gamma /
  put wall) each close settled nearest, per regime, for the session's first RTH reading and the prior
  session's final one. RTH-gated by calendar-date equality (not hours alone); expired-chain rows
  excluded by `core.regime`'s forward-only rule; reports skipped sessions with reasons and n
  everywhere; draws no conclusion itself. Answer level-pinning claims here before any module grows an
  entry rule on one.

## Invariants (do not violate)

- **Only the streamer talks to the broker.** `provider`/`service` read files and never open a broker
  session or outward connection. The computation is a pure function over a chain snapshot, which is
  what lets a profile be recomputed from history.
- **Never write a cache you don't own.** In piggyback mode the provider opens the shared cache
  `?mode=ro`; the streamer writes only this module's own cache; the spot trail goes to `history_db`.
- **GEX math and the streaming engine stay in `cherrypick.core`.** Never fork dollar-gamma / walls /
  zero-gamma or the streamer into this package (a copied GEX calculation once drifted ~75×).
- **The GEX horizon is forward-only.** `provider.snapshot_from_stream_cache` picks the nearest
  expiration **at or after today** that has live greeks, never a passed one — `stream_greeks` is never
  pruned, so an expired chain keeps its last gammas (38% of readings came from one before this).
  Historical expired-chain rows are **filtered on read, not deleted**: `core.regime` requires
  `expiration >= trade_date`. This series (`gex_regime_history`) is not MEIC's gate input — the gate
  reads its own same-tick 0DTE snapshot — so it affects the advisor's pack, the console's GEX page and
  `core.regime.regime_at`, not `meic.analytics.gex_gate_counterfactual`. It **is** a gate input for
  flies' `wall-clear` arm (from 2026-10-19), which reads the walls through `core.regime.gex_at` each
  tick and fails open past 600 s: a recorder stall turns that arm into control, silently except in
  the join. Keep the 5-minute cadence and the column meanings stable, or journal the change there.
- **GEX regime rows are RTH only** (`core.clock.in_rth`). Off-hours the session's chain has no greeks
  yet, so the horizon falls forward to whatever chain is still streaming — another expiry — and until
  2026-09-30 the recorder filed that under today: 79% of the rows on disk, and the overview's pre-open
  levels and the advisor's "open" walls read them. `record_regimes` now writes nothing off-hours, and
  every reader (`core.regime`, overview's levels, the advisor's snapshot and walls) filters on read
  as well, the expired-chain rule's posture. The 15,014 off-hours rows were removed on 2026-10-01 with
  `repair-history --apply` (3,952 RTH rows kept); the full table is in
  `gex_history.db.bak-20261001001211` beside the DB. `repair-history` alone only reports.
- **Leftover strikes are not summed.** A strike the producer's window re-centred away from keeps its
  last greeks forever; `core.gex.LEFTOVER_ROW_SECONDS` (re-exported by `provider`) drops rows that far
  behind their chain's newest. Flies' snapshot uses the same constant from 2026-10-06 and the
  console's `gexProfile.ts` the same cut, so change it in core or not at all. Negligible on a 0DTE chain; on a multi-day extra
  window it moved zero-gamma 545 points.
- **Scratch work lives in a git-ignored `.tmp/`.**
