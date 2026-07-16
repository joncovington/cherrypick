---
description: Register the cherrypick suite's scheduled tasks (run.py install) and verify it went live
---

Turn the cherrypick suite **on**: register its scheduled tasks and start the data feed, then verify.
This runs the orchestrator's `install` (Windows Task Scheduler / POSIX cron) from the monorepo root.

Do this:

1. **Pre-check readiness** (read-only): `python packages/orchestrator/run.py doctor`.
   - If the broker check **FAILs** / "credentials not present", the tastytrade credentials aren't set —
     the paper loop won't be able to connect at the next session. Surface this to me and recommend
     `python packages/orchestrator/run.py connect --module meic` (and `--module earnings` if used)
     first. Ask whether to install anyway (tasks will register fine; the broker just won't connect until
     credentials exist).
   - Other WARNs like "streamer not running" or "tasks not registered" are expected before install.

2. **Install**: `python packages/orchestrator/run.py install`. This registers the watchdog, trade-notify,
   MEIC self-healing paper-loop, earnings entry/exit, Dolt keep-alive, and the suite **EOD-digest** tasks,
   and starts the streamer. Report per-task ok/fail from its JSON output (`overall ok` + the `installed`
   map).

   The **EOD digest** (`cherrypick-eod-digest`, daily ~16:15 box-local) writes
   `logs/eod-digest-<day>.md` and pushes a one-line net-P&L summary through the notify channels. It is
   **on by default**. To opt out, set `"eod_digest": {"enabled": false}` in the cherrypick config
   (`~/.cherrypick/config.json`, or the in-repo `config.json` until migrated) before
   installing (or set it and re-run `uninstall`/`install`); with it disabled, `install` skips the task and
   `uninstall` still removes any previously-registered one.

3. **Verify**:
   - `python packages/orchestrator/run.py status` — every task **Enabled** with a next-run time.
   - `python packages/orchestrator/run.py doctor` — green (an off-hours **streamer WARN** is expected; a
     broker FAIL means credentials still need setting).

4. **Report** clearly: which tasks registered, whether the suite is ready to collect at the next session,
   and — if the broker is still unconfigured — that credentials must be added before it can connect.

Notes: installing is idempotent and safe to run any time — the tasks self-gate on trading hours, so it's
fine to install outside market hours. To turn everything back off later:
`python packages/orchestrator/run.py uninstall`.
