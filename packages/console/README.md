# cherrypick console

Unified reactive web UI for the cherrypick suite: dashboards for every module plus scout's
interactive tooling, in one app on `http://127.0.0.1:5070/`.

## Prerequisites

- Node.js 22+ and pnpm (`npm install -g pnpm`)
- Python (only for the `run.py` launcher)

## Build and run

```
cd packages/console
pnpm install
pnpm build
python run.py dashboard --serve
```

Or via the suite command: `/serve-dashboard --console`.

## Broker credential

tastytrade allows one client secret per OAuth application but many refresh tokens, and scope rides
on the refresh token. The console therefore uses the application's shared client secret paired with
its **own read-only refresh token** (generate one at my.tastytrade.com → API → OAuth applications).
Both are stored together as one entry in the OS credential store under the `cherrypick-console`
service:

```
python run.py credentials set     # prompts for client secret + refresh token, input hidden
python run.py credentials show    # masked values only
python run.py credentials clear
```

## Development

```
pnpm dev:server   # Fastify on 127.0.0.1:5070, restarts on change
pnpm dev:web      # Vite on 127.0.0.1:5173, proxies /api and /ws to 5070
```

## Layout

- `shared/` — TypeScript types shared by server and web
- `server/` — Fastify backend: module-store readers (read-only), status/overview API, and (from M3)
  the console's own DXLink market-data session fanned out over WebSocket
- `web/` — React + Vite single-page app

See `CLAUDE.md` for the data rules (read-only over module stores, own credential, no order
placement) and `docs/verify-notes.md` for the M0 spike findings.
