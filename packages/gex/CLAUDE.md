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
quotes (SPY/RSP, HYG/LQD, TLT, the eleven SPDR sectors) — into `market_regime_history`, plus a
permanent `daily_closes` table harvested from `stream_summary`. Raw measures only (ratios and
dispersion are read-side derivations in `cherrypick.core.regime`, the one join helper every consumer
goes through); RTH-gated and basis-stamped, with a stale or missing quote written as a `usable = 0`
refusal row, never a frozen value. The recorder declares the reading symbols itself as quote-only
`legs` in its stream request — coverage must not depend on another module's declaration — and a
coverage test drives off `regime.READINGS`, so a new reading without its subscription fails the
build. The charter widened deliberately (recorder of GEX → recorder of market state): one daemon,
one store, one supervision entry, and the console already reads this database. Two modes: **piggyback** (the default — `source.stream_cache_db` resolves to the suite's
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
- **`cherrypick/gex/cli.py` + `run.py`** — the CLI: `gex` (one-shot payload), `stream`, `record`.
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
