Start the full MEIC paper-trading session: streamer and paper-trading loop.

This is the paper-trading counterpart to `/meic-start` — it starts the same shared DXLink streamer (paper trading marks positions from real market quotes, exactly like live) but runs the isolated `/paper-loop` instead of the live trading loop. It never touches `~/.cherrypick/data/meic/meic_trades.db`, never submits a live order, and is not gated by `enable_live_trading` — see `docs/paper-trading.md` for the full design.

## Step 1 — Market data (the standalone streamer)

Since the 2026-07-21 producer cutover the **standalone streamer** (`packages/streamer`) is the
suite's single writer of the shared stream cache; MEIC's own `cherrypick/meic/streamer.py` is the
disabled rollback path. **Never start `cherrypick/meic/streamer.py` while the standalone streamer
runs** — two producers means two DXLink writers on one cache and one account. Paper trading marks
positions from the same real market quotes live trading uses, so this step is identical to
`/meic-start`'s Step 1:

```bash
python ../streamer/run.py --status
```

Require **both** `"running": true` and a small `oldest_event_age_s` during market hours. If it is
down, start it via `python ../orchestrator/run.py install` (idempotent), or directly:

```bash
python ../streamer/run.py    # blocks; run detached/hidden
```

(Only if this box was deliberately rolled back to MEIC-as-producer — `modules.meic.streamer.enabled`
true in the cherrypick config — use `python -m cherrypick.meic.streamer --status` / start instead.)

## Step 2 — Paper-trading dashboard

Invoke `/serve-dashboard --meic --paper` to launch (or confirm already running) the paper-trading dashboard at http://localhost:5051, separate from the live dashboard's port 5050.

## Step 3 — Paper-trading loop (scheduled task)

Register the unattended paper loop as a Windows scheduled task and fire the first run immediately:

```bash
python -m cherrypick.meic.paper_loop --install-task
```

This creates the `cherrypick-meic-paper-loop` task, which runs `python -m cherrypick.meic.paper_loop --once` every 2 minutes. Each run is a short-lived process that reliably completes, self-heals if one fails, no-ops outside market hours (it's time-gated), and persists across sessions — the robust way to run it unattended on Windows (a long-running detached daemon proved fragile against stray console events). Each `--once` runs the parallel-shadow engine across every configured symbol: marking/exiting open ICs (per-side stops, the settlement-aware force-close cascade with physical-settlement early close + friction, and cash-settled left-to-expire settlement — no profit target) and evaluating new entries per profile. All writes go to `~/.cherrypick/data/meic/paper_trades.db`; the live account and `~/.cherrypick/data/meic/meic_trades.db` are never touched.

(For a one-off manual iteration outside the task — e.g. a final force-close pass — run `python -m cherrypick.meic.paper_loop --once`. On non-Windows hosts, run `python -m cherrypick.meic.paper_loop` in a terminal or wire a cron job instead.)

Tell the user:
"Paper-trading session started — the paper loop runs as the supervisor's `meic-paper` job on the cadence set by `paper.tick_interval_seconds`, across every enabled forward-test stream (`control`/`open`/`width-5`/`width-10` — see config.risk.json), self-healing and time-gated to market hours. It writes a deterministic end-of-day report to logs/paper-eod-<date>.md automatically at the 16:00 settlement pass. Writes go to ~/.cherrypick/data/meic/paper_trades.db only; the live account and ~/.cherrypick/data/meic/meic_trades.db are untouched. Dashboard: http://localhost:5051 (Paper Mode). Stop the session with `python -m cherrypick.meic.paper_loop --uninstall-task`; run /paper-report for a synthesized write-up or `python -m cherrypick.meic.paper_loop --eod-report` for the day's report on demand."
