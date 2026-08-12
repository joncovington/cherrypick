---
description: Open the console — the suite's one read surface (127.0.0.1:5070); --status / --restart / --logs
argument-hint: [--status] | [--restart] | [--logs] | [--stop] | [--no-browser]
---

Open the **console** (`packages/console`), the suite's single read surface: every module's read
models (overview/watchdog, MEIC, flies, earnings, GEX) plus research and screening, in one app
on **127.0.0.1:5070**. Read-only over every other package's data, loopback-only.

**The supervisor owns this process.** It is an always-on resident job (`console` in
`state/supervisor-jobs.json`) with no clock window and no trading-day gate — restarted if it dies,
and restarted if it wedges (the server rewrites `state/console.heartbeat` every ~15s; a stale mtime
is the wedge signal). So there is no "start the console" step: if it is down, the question is why the
supervisor is not running it, not how to launch one by hand.

The per-module dashboards this replaced (suite 8787, MEIC 5050/5051, flies 5052, GEX 5055, scout
5057) were deleted on 2026-08-12 — recover them from the `pre-console-only` tag if ever needed.

## Route on `$ARGUMENTS`

- **`--status`** → run the Status procedure only, no browser.
- **`--restart`** → Restart, then Status.
- **`--logs`** → tail the log (see Logs), no browser.
- **`--stop`** → Stop (read it first — the supervisor restarts it).
- otherwise → **Open** (Status, then a browser tab unless `--no-browser`).

---

## Open

1. Resolve the port: `serve.port` in `~/.cherrypick/config/console.json`, else **5070**.
2. Probe it: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<port>/api/health` — expect
   `200`.
3. If it answers, open `http://127.0.0.1:<port>/` (skip on `--no-browser`) and report the URL.
4. If it does **not** answer, do NOT start a server by hand — run the Status procedure and report
   what is actually wrong. A hand-started console takes the port the supervisor's own child needs,
   and every supervised restart then fails to bind.

## Status

Answer "is it up, and if not, why" in this order — each step explains a different failure:

1. **Is the supervisor running?**
   `python packages/orchestrator/run.py status` — the `supervisor` block should show
   `running: true` with a small `heartbeat_age_seconds`. If it is down, that is the whole answer:
   `/install` (or `python packages/orchestrator/run.py supervise`) brings it and the console back.
2. **What does the job registry say?** In the same output, the `console` job:
   - `resident_state: running` with a live `running_pid` → it is up.
   - `enabled: false` with `"console not built"` → the checkout has no built server. Fix it:
     from `packages/console`, `pnpm install` then `pnpm build`.
   - `enabled: false` with `"disabled in config (console)"` → `console.enabled` is false in
     `~/.cherrypick/config.json`.
   - `resident_state: backoff` → it is crash-looping. Read the log (below); the usual cause is
     another process already holding the port.
3. **Is something else on the port?**
   `Get-NetTCPConnection -LocalPort <port> -State Listen` — if the owning process is not the
   supervisor's child, that is why the supervised console cannot bind.
4. **Heartbeat freshness:** `~/.cherrypick/state/console.heartbeat` should be seconds old. A stale
   file with a live process means it is wedged and the supervisor is about to restart it.

## Restart

The supervisor is the restart mechanism — kill the tree and let it come back:

1. Find the listener's owning process on the port, confirm it is `node` running
   `packages/console/server/dist/index.js`, then `taskkill /T /F /PID <pid>`. **`/T` matters**:
   `run.py` is a launcher and node is its grandchild, so killing only the tracked PID leaves node
   holding the port and every replacement dies on `EADDRINUSE`.
2. Wait for the supervisor's next pass (~1s) plus any crash backoff, then run Status. Note the
   supervisor backs off 30s after the first failure, doubling to a 10-minute cap.
3. To pick up a **code change** the build must come first: from `packages/console`, `pnpm build`,
   then restart.

## Stop

Rarely what you want: the supervisor restarts it within a pass. To keep it down, set
`"console": {"enabled": false}` in `~/.cherrypick/config.json` — the supervisor re-derives its job
table from config every pass, so the job goes disabled-with-a-reason on the next one — and only then
kill the tree as in Restart. `/uninstall` stops it along with everything else.

## Logs

`~/.cherrypick/logs/console/console.log` (rotated at 5 MB, 3 backups). Per-request logging is off by
design, so what is in here is startup, credential scope, DXLink reconnects, and errors — not traffic.
The supervisor's own view of the job is in `~/.cherrypick/logs/supervisor.log`.

## Report

The URL, that it is read-only and loopback-only, and — when it is down — which of the Status steps
identified the cause and the one command that fixes it.
