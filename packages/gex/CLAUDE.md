# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

cherrypick-gex is a **GEX (gamma-exposure) dashboard** for the trading-tool suite — a simple
self-hosted version of gexbot.com / SpotGamma / MenthorQ. It computes GEX via the shared
`cherrypick.core.gex` engine and serves a localhost live view. It places no orders and never touches
live trading. Two modes: **standalone** (`run.py stream` runs `cherrypick.core.streamer` to populate its
own `data/stream_cache.db`) or **piggyback** (point `source.stream_cache_db` at a cherrypick-meic cache
and read it read-only). The umbrella (Cherrypick) embeds this module's GEX by subprocessing
`python run.py gex --symbol <sym> --json`.

## Commands

```bash
git submodule update --init          # pull the cherrypick-core submodule (src/_core); imports fail without it
python run.py stream --symbol SPX    # run the streamer -> own data/stream_cache.db (standalone mode)
python run.py dashboard --serve      # localhost live GEX view (default 127.0.0.1:5055)
python run.py gex --symbol SPX --json # one-shot payload (what the umbrella consumes)
python -m pytest                     # tests seed a temp cache; no streamer required
ruff check . && ruff format .        # line-length 110; src/_core excluded
```

Config: copy `config.example.json` → `config.json` (git-ignored). Paths in it resolve relative to the
config file's directory — never hardcode absolute paths.

## Architecture

- **src/streamer.py** — the standalone streamer wrapper: runs `cherrypick.core.streamer.ChainStreamer`
  with this module's own keyring session, writing its own cache. Thin — no open-position policy, ORB, or
  HTTP API (those stay in MEIC's wrapper). The one place this module authenticates / talks to the broker.
- **src/provider.py** — turns a data source into a `GexSnapshot`. Reads a stream cache with `?mode=ro`
  and picks the nearest expiration that actually has live greeks. It owns the stream-cache read shape;
  add a new source by adding a provider, not by editing the schema-aware reader.
- **src/service.py** — `build_gex(cfg, symbol)`: provider → `cherrypick.core.gex.compute_gex_profile`
  → chart payload. The pure, HTTP-free seam; also records the spot trail in this module's **own**
  SQLite (`history_db`). Bootstraps `src/_core` onto `sys.path`.
- **src/serve.py** — stdlib `ThreadingHTTPServer`, loopback-only, one self-contained page polling
  `/api/gex`.
- **src/cli.py + run.py** — the CLI; `gex --json` is the umbrella's integration point.
- **src/_core** — the shared `cherrypick-core` submodule; the GEX math (`core.gex`), the streaming engine
  (`core.streamer`), and the cache schema (`core.streamcache`) live there so this module and
  cherrypick-meic compute/stream identically. Excluded from ruff and the wheel.

## Invariants (do not violate)

- **Only the streamer talks to the broker.** `provider`/`service`/`serve` read files and never open a
  broker session or outward network connection. No MCP/AI on any path.
- **Never write a cache you don't own.** In piggyback mode `source.stream_cache_db` points at MEIC's
  cache — the provider opens it `?mode=ro` and must never write it; the streamer only writes this
  module's own cache. The spot trail goes to this module's own `history_db`.
- **GEX math and the streamer engine stay in `cherrypick.core`.** Do not fork the dollar-gamma / walls /
  zero-gamma math or the streaming engine into this repo — the whole point is one shared implementation
  with cherrypick-meic (the GEX math drifted ~75× once when copied).
- **Instruction files hold no code and no logs.** This file is build commands + guidelines only.
- **Portable paths.** Never hardcode absolute paths, usernames, or drive letters; derive from
  `Path(__file__)` or config. Scratch work lives in a git-ignored `.tmp/`.
