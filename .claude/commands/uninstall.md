---
description: Fully stop the cherrypick suite — unschedule, then stop every running service cleanly
---

Bring the cherrypick suite to a **complete, clean stop**: nothing running, nothing scheduled, and it
stays stopped. Runs from the monorepo root. Data (paper DBs, Dolt store, keyring) is never touched —
`/install` brings everything back.

**The order is built in.** `uninstall` deletes the `cherrypick-supervisor` anchor task FIRST (so
nothing can restart the daemon), then stops the supervisor (polite stop file, ≤10s wait, terminate
fallback) — with the supervisor gone, no job fires and nothing resurrects a stopped daemon.

Do this:

1. **Unschedule, stop the supervisor, and stop the managed services** (idempotent):
   `python packages/orchestrator/run.py uninstall`
   It prints a doctor-style `[ OK ]`/`[FAIL]` line per item, ending in `Result: ALL REMOVED` or
   `Result: FAILURES -- action needed` (exit code follows). Confirm: `anchor_task` `[ OK ]`,
   `supervisor` stopped, every `legacy.cherrypick-*` deletion `[ OK ]` (these are attempted by name
   even when long gone — a no-op reports `[ OK ]`; on a pre-cutover box this is what removes the old
   per-job tasks), and `service.gex-recorder` (and any other `services` entry) stopped. If a
   `<module>.live_arm` line appears, a **live arm record was removed** — check the broker UI for
   resting orders. The "Left running by design" section names the streamer, any dashboard server,
   and Dolt — **the streamer is why the next step exists.**

2. **Stop what uninstall leaves behind** (each is best-effort — "not running" is a fine result):
   - **Streamer** (the standalone producer, `packages/streamer` — the suite's single market-data
     daemon): `python packages/streamer/run.py --stop`, then confirm with
     `python packages/streamer/run.py --status` (`"running": false`).
     (Only if this box was rolled back to MEIC-as-producer — `modules.meic.streamer.enabled` true —
     stop that one instead: `python -m cherrypick.meic.streamer --stop`. Exactly one producer ever
     runs.)
   - **Dashboard servers** (only if any `--serve` is up): run the `/serve-dashboard --stop all`
     procedure, which covers the suite (8787) and every module dashboard (8801–8802, 5050–5052, 5055)
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
     returns nothing (the anchor included).
   - Supervisor down: `~/.cherrypick/state/supervisor.last.json` goes stale (its `pid` no longer
     running); `python packages/orchestrator/run.py status` shows the legacy empty-tasks view.
   - Streamer down: `python packages/streamer/run.py --status` reports `"running": false`.
   - gex recorder down: `python packages/gex/run.py record --status` reports `"running": false`.
   - Nothing listening on ports **3306**, **8787**, or **7699**.

4. **Report** clearly: the suite is fully stopped and unscheduled, which daemons were stopped (vs.
   already down), and that all data/credentials are intact and `/install` restores everything. The full
   inventory of what was installed lives in [docs/operations.md](../../docs/operations.md).
