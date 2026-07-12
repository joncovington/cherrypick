---
description: Fully stop the cherrypick suite — unschedule, then stop every running service cleanly
---

Bring the cherrypick suite to a **complete, clean stop**: nothing running, nothing scheduled, and it
stays stopped. Runs from the monorepo root. Data (paper DBs, Dolt store, keyring) is never touched —
`/install` brings everything back.

**Order matters: unschedule first.** If you stopped a service while its task were still registered, the
Dolt keep-alive (every ~5 min) or the watchdog's streamer auto-restart (in-session) would resurrect it
seconds later. Remove the schedule first so the stops stick.

Do this:

1. **Remove the scheduled tasks** (idempotent):
   `python packages/orchestrator/run.py uninstall`
   Confirm from its JSON that the `cherrypick-*` tasks were removed (watchdog, trade-notify, meic
   paper-loop, earnings entry/exit, Dolt keep-alive, eod-digest). Note: this command intentionally leaves
   running services alone — that's why the next step exists.

2. **Stop the running services cleanly** (each is best-effort — "not running" is a fine result):
   - **Streamer** (meic DXLink data feed): `python packages/meic/src/streamer.py --stop`
   - **Paper-loop daemon** (meic — only if one is running in daemon mode; a no-op for the normal
     `--once` scheduled runs): `python packages/meic/src/paper_loop.py --stop`
   - **Dashboard server** (only if a `dashboard --serve` is up): stop the process on its port
     (default **8787**, or `dashboard.serve.port` from `config.json`):
     `Get-NetTCPConnection -LocalPort 8787 -State Listen -EA SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`
   - **Dolt sql-server** (earnings' local market-data DB, kept alive by the task removed in step 1):
     stop the process serving port **3306**:
     `Get-NetTCPConnection -LocalPort 3306 -State Listen -EA SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`
     (This is the shared Dolt server — skip it if you use Dolt outside cherrypick and want it to keep running.)

3. **Verify a clean stop:**
   - No cherrypick tasks: `Get-ScheduledTask | Where-Object { $_.TaskName -like 'cherrypick*' }` returns nothing.
   - No lingering services: `python packages/meic/src/tt.py stream_status` reports not running, and
     nothing is listening on ports **3306** or **8787**.

4. **Report** clearly: the suite is fully stopped and unscheduled, which services were stopped (vs. already
   down), and that all data/credentials are intact and `/install` restores it.
