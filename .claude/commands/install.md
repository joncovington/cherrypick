---
description: Register the cherrypick supervisor anchor + start the daemon (run.py install) and verify it went live
---

Turn the cherrypick suite **on**: register the ONE OS anchor task, start the supervisor daemon (which
derives and fires every job from config), and start the data feed, then verify. This runs the
orchestrator's `install` from the monorepo root. The full job inventory lives in `docs/operations.md`.

Do this:

1. **Pre-check readiness** (read-only): `python packages/orchestrator/run.py doctor`.
   - If the broker check **FAILs** / the `onboarding` line shows missing credentials, run the suite
     onboarding wizard first: `python packages/orchestrator/run.py connect` (no `--module` — one shared
     login for every module, with per-module overrides offered). Surface this to me and ask whether to
     install anyway (the supervisor runs fine; the broker just won't connect until credentials exist).
   - Other WARNs like "streamer not running" or "supervisor NOT running" are expected before install.
   - If flies is **live-armed for today**, `install` refuses (deleting the legacy live task mid-day
     must never silently disarm). Disarm first (`/live-flies-start --stop`) or, only with my explicit
     say-so, use `install --force`.

2. **Install**: `python packages/orchestrator/run.py install`. This registers the single
   `cherrypick-supervisor` anchor task (a 2-minute `ensure-supervisor` probe that restarts a dead
   daemon), starts the supervisor now, **deletes every legacy per-job scheduled task** (watchdog,
   preopen, trade-notify, module loops, earnings entry/exit, dolt, archive, reconcile, symbol-watch,
   follow-notify — so the two mechanisms can never double-fire), and starts the **standalone
   streamer** plus any enabled background **services** (e.g. `service.gex-recorder`). Report per-item
   ok/fail from its JSON output (`overall ok` + the `installed` map — the `legacy.*` entries are
   deletions and "not registered" there is a clean no-op).

   Every recurring job (watchdog, streamer-health, trade-notify, the module paper loops, earnings
   entry/exit, dolt keep-alive, log-archive, opt-in reconcile/symbol-watch/follow-notify) is a
   **supervisor job derived from `~/.cherrypick/config.json`** — no per-job registration exists
   anymore. The **EOD digest and insight remain event-driven** (watchdog-fired once every module has
   written its `paper-eod-<day>.md`, deadline backstop 16:45 ET) — they appear in no schedule, and
   that is correct output, not a failure.

3. **Verify**:
   - `python packages/orchestrator/run.py status` — the `supervisor` block shows `running: true`
     with a small `heartbeat_age_seconds`; the `anchor` task exists; every enabled job has a recent
     `last_start` or a future `next_run` (a job `disabled` with a config-opt-out reason is healthy).
   - `python packages/orchestrator/run.py doctor` — green, including the `supervisor`,
     `supervisor.anchor`, and `supervisor.legacy_tasks` rows (the last one WARNs if any old
     `cherrypick-*` task survived the cutover).
   - PowerShell: `Get-ScheduledTask | Where-Object { $_.TaskName -like 'cherrypick*' }` — exactly
     ONE entry, `cherrypick-supervisor`.
   - `python packages/streamer/run.py --status` — `"running": true` (in-session, also a small
     `oldest_event_age_s`).

4. **Report** clearly: supervisor up (pid + heartbeat age), anchor registered, how many jobs derived
   (and any `derive_errors`), legacy tasks removed, and — if the broker is still unconfigured — that
   `connect` must run before it can trade.

Notes: installing is idempotent and safe to run any time — jobs self-gate on trading hours, so it's
fine to install outside market hours; re-running re-anchors at this checkout's paths (how a new
checkout becomes the live instance). Rollback for the transition window: `git tag pre-supervisor`
(see docs/operations.md). To turn everything back off later: `/uninstall`.
