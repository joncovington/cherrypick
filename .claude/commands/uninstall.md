---
description: Fully stop the cherrypick suite — unschedule, then stop every running service cleanly
---

Bring the cherrypick suite to a **complete, clean stop**: nothing running, nothing scheduled, and it
stays stopped. Runs from the monorepo root. Data (paper DBs, Dolt store, keyring) is never touched —
`/install` brings everything back.

**Order matters: unschedule first.** If you stopped a daemon while its task was still registered, the
Dolt keep-alive (every ~5 min) or the watchdog's auto-restarts (streamer in-session; services every
tick) would resurrect it within minutes. Remove the schedule first so the stops stick.

Do this:

1. **Remove the scheduled tasks and stop the managed services** (idempotent):
   `python packages/orchestrator/run.py uninstall`
   Confirm from its JSON that every registered task was removed — the module paper loops
   (`cherrypick-meic-paper-loop`, `cherrypick-flies-paper-loop`, each removed via the module's own
   `--uninstall-task`), `cherrypick-earnings-paper-entry` / `-exit`, `cherrypick-earnings-dolt`,
   `cherrypick-watchdog`, `cherrypick-trade-notify`, `cherrypick-log-archive`, and (attempted by name
   even when unregistered, which reports as a no-op) `cherrypick-eod-digest`, `cherrypick-eod-insight`,
   `cherrypick-reconcile` — and that `service.gex-recorder` (and any other `services` entry) reports
   stopped. **The one thing it deliberately leaves running is the streamer** — that's what the next
   step is for.

2. **Stop what uninstall leaves behind** (each is best-effort — "not running" is a fine result):
   - **Streamer** (the standalone producer, `packages/streamer` — the suite's single market-data
     daemon): `python packages/streamer/run.py --stop`, then confirm with
     `python packages/streamer/run.py --status` (`"running": false`).
     (Only if this box was rolled back to MEIC-as-producer — `modules.meic.streamer.enabled` true —
     stop that one instead: `python -m cherrypick.meic.streamer --stop`. Exactly one producer ever
     runs.)
   - **Dashboard servers** (only if any `--serve` is up): run the `/serve-dashboard --stop all`
     procedure, which covers the suite (8787) and every module dashboard (8801–8803, 5050/5051, 5055)
     with its confirm-before-kill guard.
   - **Dolt sql-server** (earnings' local market-data DB; its keep-alive task was removed in step 1):
     stop the process serving port **3306**:
     `Get-NetTCPConnection -LocalPort 3306 -State Listen -EA SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`
     (This is the shared Dolt server — skip it if you use Dolt outside cherrypick and want it running.)
   - **MEIC sidecar** (optional, off by default — if `meic-sidecar` was enabled in `services` it was
     already stopped in step 1; a manually-started one stops with
     `python -m cherrypick.meic.streamer --sidecar --stop`).

3. **Verify a clean stop:**
   - No cherrypick tasks: `Get-ScheduledTask | Where-Object { $_.TaskName -like 'cherrypick*' }`
     returns nothing.
   - Streamer down: `python packages/streamer/run.py --status` reports `"running": false`.
   - gex recorder down: `python packages/gex/run.py record --status` reports `"running": false`.
   - Nothing listening on ports **3306**, **8787**, or **7699**.

4. **Report** clearly: the suite is fully stopped and unscheduled, which daemons were stopped (vs.
   already down), and that all data/credentials are intact and `/install` restores everything. The full
   inventory of what was installed lives in [docs/operations.md](../../docs/operations.md).
