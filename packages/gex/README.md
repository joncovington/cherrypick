# cherrypick-gex

A simple, self-hosted GEX (gamma-exposure) dashboard — a lightweight take on what gexbot.com,
SpotGamma, and MenthorQ sell. Three tabs off one live option chain:

- **GEX** — **net GEX by strike** with **open interest ("positioning") and traded volume ("flow") side
  by side**, the **gamma-flip / zero-gamma** level, the **call/put walls**, and a live spot marker with
  an intraday spot trail. The stats panel shows two blocks — **Open Interest (positioning)** and
  **Volume (flow)** — each with total call/put GEX, net GEX, zero gamma, and call/put walls.
- **IV Skew** — call vs put implied-volatility curve and open interest by strike.
- **Volume** — call/put/total traded volume by strike.

It computes GEX with the shared `cherrypick.core.gex` engine — the same math the suite's MEIC trading
loop uses for its GEX regime gate — and never places orders or touches live trading. (This is the full
GEX view the suite used to render inside MEIC's dashboard; it now lives here.)

Part of the **cherrypick** trading-tool suite; the orchestrator embeds this dashboard and its GEX section
card. See the suite's [documentation index](../../docs/README.md) for the big picture.

## Two ways to run

**Standalone (default).** `run.py stream` runs the shared `cherrypick.core.streamer` engine with this
module's own tastytrade OAuth session and writes its **own** `data/stream_cache.db`; `run.py gex` /
`dashboard --serve` read it. No cherrypick-meic needed. Credentials come from the OS keyring under the
suite's service (`meicagent`, with a read-only fallback to the pre-rename `tastytrade-mcp`).

**Piggyback.** Point `source.stream_cache_db` at a running cherrypick-meic streamer's
`data/stream_cache.db` and **don't** run this module's streamer — it then only reads (read-only) the
cache MEIC already maintains.

Either way, open interest and live per-option volume exist only because a streamer subscribes to the
DXLink Summary and Trade events for the ATM/GEX strike window.

## Setup

```bash
git clone --recursive <url> cherrypick-gex      # --recursive pulls the cherrypick-core submodule
cd cherrypick-gex
cp config.example.json config.json              # point source.stream_cache_db at the MEIC cache
```

## Commands

```bash
python run.py stream --symbol SPX               # run the streamer -> own data/stream_cache.db (foreground)
python run.py dashboard --serve                 # localhost live GEX view (default 127.0.0.1:5055)
python run.py dashboard --serve --symbol SPX --port 5055
python run.py gex --symbol SPX                  # one-shot summary to the terminal
python run.py gex --symbol SPX --json           # raw GEX payload
python run.py section --symbol SPX --json        # cherrypick.core.viz section payload (umbrella embeds this)

python -m pytest                                # tests (seed a temp cache; no streamer needed)
ruff check . && ruff format .                   # lint/format (src/_core is excluded)
```

## Config

`config.json` (git-ignored, machine-local). Paths resolve **relative to the config file's directory**:

- `source.stream_cache_db` — the cache path. Default `data/stream_cache.db` (this module's own, written
  by `run.py stream`). Repoint at a cherrypick-meic cache to piggyback instead.
- `symbols` — default symbol list; the first is used when `--symbol` is omitted.
- `streamer` — `{window_strike_count}` for `run.py stream` (strikes each side of the money to subscribe).
- `serve` — `{host, port, refresh_seconds, ws_port, push_min_interval_seconds}` for the live view.
  The dashboard receives **live pushes** over `ws://<host>:<ws_port>` (default `port + 1`, e.g. 5056),
  sent at most once per `push_min_interval_seconds` (default 1.0) and only when the strike-window data
  changes; it falls back to `refresh_seconds` polling of `/api/gex` whenever the socket is down and
  reconnects with backoff. `ws_port` and `push_min_interval_seconds` are optional (defaults apply).
- `history_db` — this module's own SQLite for the persisted spot trail.
