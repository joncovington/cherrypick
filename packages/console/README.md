# cherrypick console

The cherrypick suite's one read surface: every module's read models plus scout's
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

Normally you do not run this by hand — the supervisor keeps it up as an always-on resident job, and
`/console` opens it (and diagnoses it when it is down). Its log is `~/.cherrypick/logs/console/console.log`.

## Broker credential

One credential set serves the whole suite — the shared keyring entries every module reads
(`cherrypick-broker` service, `production:client_secret` / `production:refresh_token`) — and there
is exactly **one path for setting them**, the suite onboarding CLI:

```
python -m cherrypick.core.auth setup      # THE single setting path (hidden input)
```

The console only reads. At boot it probes the credential's scope live (scope rides on the refresh
token): a read-only token disables write-oriented functions (broker dry-run validation of staged
tickets) until a trade-scoped token is rotated in. Inspection commands:

```
python run.py credentials show     # source + masked values
python run.py credentials probe    # validate now and report detected scope
python run.py credentials clear    # remove pre-unification console-only slots
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

## Desktop window

`pnpm --filter @console/desktop start` opens the console in its own window (tray icon, remembered
size). It is a window and nothing else — the supervisor still owns the server — and when the console
is not answering it says which of four things went wrong rather than showing a connection error. See
[desktop/README.md](desktop/README.md).
