---
description: Register the cherrypick suite's scheduled tasks (run.py install) and verify it went live
---

Turn the cherrypick suite **on**: register its scheduled tasks and start the data feed, then verify.
This runs the orchestrator's `install` (Windows Task Scheduler / POSIX cron) from the monorepo root.
The full inventory of what gets installed lives in `docs/operations.md`.

Do this:

1. **Pre-check readiness** (read-only): `python packages/orchestrator/run.py doctor`.
   - If the broker check **FAILs** / the `onboarding` line shows missing credentials, run the suite
     onboarding wizard first: `python packages/orchestrator/run.py connect` (no `--module` — one shared
     login for every module, with per-module overrides offered). Surface this to me and ask whether to
     install anyway (tasks register fine; the broker just won't connect until credentials exist).
   - Other WARNs like "streamer not running" or "tasks not registered" are expected before install.

2. **Install**: `python packages/orchestrator/run.py install`. This registers the watchdog,
   trade-notify, the module paper loops (MEIC and flies register their own via `--install-task`),
   earnings entry/exit, the Dolt keep-alive, the monthly log-archive, and — only if enabled — the
   scheduled reconcile; it starts the **standalone streamer** (the suite's single market-data producer)
   and any enabled background **services** (e.g. `service.gex-recorder`). Report per-task ok/fail from
   its JSON output (`overall ok` + the `installed` map).

   The **EOD digest and insight are not scheduled tasks**: the watchdog fires them (detached) once
   every installed module has written its `paper-eod-<day>.md`, with `eod_digest.deadline` (16:45 ET)
   as the backstop — `install` reports them as "not registered" and deletes any stale fixed-time task
   from an older install. That is correct output, not a failure. The digest is on by default; opt out
   with `"eod_digest": {"enabled": false}` in `~/.cherrypick/config.json`.

3. **Verify**:
   - `python packages/orchestrator/run.py status` — every task **Enabled** with a **future** next-run
     time (the check `doctor` doesn't make).
   - `python packages/orchestrator/run.py doctor` — green (an off-hours **streamer WARN** is expected;
     a broker FAIL means credentials still need setting).
   - `python packages/streamer/run.py --status` — `"running": true` (in-session, also a small
     `oldest_event_age_s`).

4. **Report** clearly: which tasks registered, whether the suite is ready to collect at the next
   session, and — if the broker is still unconfigured — that `connect` must run before it can trade.

Notes: installing is idempotent and safe to run any time — the tasks self-gate on trading hours, so
it's fine to install outside market hours; re-running also re-bakes task commands at this checkout's
paths (how a new checkout becomes the live instance). To turn everything back off later: `/uninstall`.
