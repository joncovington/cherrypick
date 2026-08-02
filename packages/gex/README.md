# cherrypick-gex

**What this module does:** it's a read-only dashboard, not a trading strategy — it shows you
dealer gamma-exposure positioning (where market makers are likely to buy or sell to hedge as
price moves), option-implied-volatility skew, and traded volume, all by strike, updating live.
Think of it as your own self-hosted version of what gexbot.com, SpotGamma, or MenthorQ sell,
built on the suite's shared market-data feed. It never places an order — the other modules
(MEIC, earnings, flies) use the same underlying gamma-exposure math to help decide when
*they* should trade, and this dashboard is simply that same view surfaced for you to watch
directly.

Three tabs off one live option chain:

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

**Piggyback (the default).** Out of the box this dashboard reads the suite's shared, canonical
market-data cache — the same one MEIC, flies, and the standalone `streamer` package read and
write — read-only. If any of those is already running, the dashboard just works with no
market-data connection of its own to manage.

**Standalone.** You can instead have this module run its own connection to the market-data feed
(`run.py stream`) and keep its own local cache of quotes, entirely independent of any other
module. It signs in using the same secure, OS-stored credentials as the rest of the suite.

Either way, the open-interest and per-option volume figures only exist because a live data
connection is actively subscribed to the relevant strikes — without one running somewhere, the
dashboard shows the last cached snapshot rather than live data.

## Setup

```bash
git clone https://github.com/joncovington/cherrypick-next.git
cd cherrypick-next
pip install -e packages/core                    # shared cherrypick.core library, install first
cd packages/gex
pip install -e ".[dev]"
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
ruff check . && ruff format .                   # lint/format
```

## Config

`config.json` (git-ignored, machine-local). Paths resolve **relative to the config file's directory**:

- `source.stream_cache_db` — the cache path. Defaults to the suite's shared, canonical cache
  (`~/.cherrypick/data/marketdata/stream_cache.db`) if omitted — the piggyback path. Point it at
  `data/stream_cache.db` (or any path) to read this module's own standalone `run.py stream` output
  instead.
- `symbols` — default symbol list; the first is used when `--symbol` is omitted.
- `streamer` — `{window_strike_count}` for `run.py stream` (strikes each side of the money to subscribe).
- `serve` — `{host, port, refresh_seconds, ws_port, push_min_interval_seconds}` for the live view.
  The dashboard receives **live pushes** over `ws://<host>:<ws_port>` (default `port + 1`, e.g. 5056),
  sent at most once per `push_min_interval_seconds` (default 1.0) and only when the strike-window data
  changes; it falls back to `refresh_seconds` polling of `/api/gex` whenever the socket is down and
  reconnects with backoff. `ws_port` and `push_min_interval_seconds` are optional (defaults apply).
- `history_db` — this module's own SQLite for the persisted spot trail.
