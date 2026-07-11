# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

cherrypick-gex is a **read-only GEX (gamma-exposure) dashboard** for the trading-tool suite — a simple
self-hosted version of gexbot.com / SpotGamma / MenthorQ. It reads a MEIC-style `stream_cache.db`
(written by cherrypick-meic's streamer) **read-only**, computes GEX via the shared
`cherrypick.core.gex` engine, and serves a localhost live view. It **never** touches a broker, places
orders, or opens outward network connections. The umbrella (Cherrypick) embeds this module's GEX by
subprocessing `python run.py gex --symbol <sym> --json`.

## Commands

```bash
git submodule update --init          # pull the cherrypick-core submodule (src/_core); imports fail without it
python run.py dashboard --serve      # localhost live GEX view (default 127.0.0.1:5055)
python run.py gex --symbol SPX --json # one-shot payload (what the umbrella consumes)
python -m pytest                     # tests seed a temp cache; no streamer required
ruff check . && ruff format .        # line-length 110; src/_core excluded
```

Config: copy `config.example.json` → `config.json` (git-ignored). Paths in it resolve relative to the
config file's directory — never hardcode absolute paths.

## Architecture

- **src/provider.py** — turns a data source into a `GexSnapshot`. The only provider reads MEIC's
  `stream_cache.db` with `?mode=ro`. It owns the (MEIC-specific) stream-cache schema; add a new source
  by adding a provider, not by editing the schema-aware reader.
- **src/service.py** — `build_gex(cfg, symbol)`: provider → `cherrypick.core.gex.compute_gex_profile`
  → chart payload. The pure, HTTP-free seam; also records the spot trail in this module's **own**
  SQLite (`history_db`), never the read-only cache. Bootstraps `src/_core` onto `sys.path`.
- **src/serve.py** — stdlib `ThreadingHTTPServer`, loopback-only, one self-contained page polling
  `/api/gex`.
- **src/cli.py + run.py** — the CLI; `gex --json` is the umbrella's integration point.
- **src/_core** — the shared `cherrypick-core` submodule; the GEX math lives there so this module and
  cherrypick-meic compute identically. Excluded from ruff and the wheel.

## Invariants (do not violate)

- **Read files, never the broker; no outward network.** The provider only opens the stream cache
  read-only. No REST, no broker session, no MCP/AI on any path.
- **Never write the MEIC cache.** It is opened `?mode=ro`; the spot trail goes to this module's own DB.
- **GEX math stays in `cherrypick.core.gex`.** Do not fork the dollar-gamma / walls / zero-gamma math
  into this repo — the whole point is one shared implementation with cherrypick-meic (it drifted ~75×
  once when copied).
- **Instruction files hold no code and no logs.** This file is build commands + guidelines only.
- **Portable paths.** Never hardcode absolute paths, usernames, or drive letters; derive from
  `Path(__file__)` or config. Scratch work lives in a git-ignored `.tmp/`.
