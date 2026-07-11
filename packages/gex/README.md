# cherrypick-gex

A simple, self-hosted GEX (gamma-exposure) dashboard — a lightweight take on what gexbot.com,
SpotGamma, and MenthorQ sell. It shows **net GEX by strike** for an index option chain with **open
interest ("positioning") and traded volume ("flow") side by side**, the **gamma-flip / zero-gamma**
level, and the **call/put walls**, plus a live spot marker and intraday spot trail.

It is a **read-only viewer**: it never touches a broker, places no orders, and opens no network
sockets outward. It reads a MEIC-style `stream_cache.db` (written by the cherrypick-meic streamer)
**read-only** and computes GEX with the shared `cherrypick.core.gex` engine — the same math
cherrypick-meic's own dashboard uses, so the two agree to the dollar.

## Requirements

The cherrypick-meic streamer must be running (or have run this session) so the stream cache is
populated — open interest and live per-option volume exist only because the streamer subscribes to the
DXLink Summary and Trade events for the ATM/GEX strike window.

## Setup

```bash
git clone --recursive <url> cherrypick-gex      # --recursive pulls the cherrypick-core submodule
cd cherrypick-gex
cp config.example.json config.json              # point source.stream_cache_db at the MEIC cache
```

## Commands

```bash
python run.py dashboard --serve                 # localhost live GEX view (default 127.0.0.1:5055)
python run.py dashboard --serve --symbol SPX --port 5055
python run.py gex --symbol SPX                  # one-shot summary to the terminal
python run.py gex --symbol SPX --json           # raw payload (what the Cherrypick umbrella embeds)

python -m pytest                                # tests (seed a temp cache; no streamer needed)
ruff check . && ruff format .                   # lint/format (src/_core is excluded)
```

## Config

`config.json` (git-ignored, machine-local). Paths resolve **relative to the config file's directory**:

- `source.stream_cache_db` — path to the cherrypick-meic `stream_cache.db` to read.
- `symbols` — default symbol list; the first is used when `--symbol` is omitted.
- `serve` — `{host, port, refresh_seconds}` for the live view.
- `history_db` — this module's own SQLite for the persisted spot trail (never the MEIC cache).
