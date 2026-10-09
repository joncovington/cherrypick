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

## Step 2 — The read surface

Invoke `/console` to open the suite console at http://127.0.0.1:5070 and confirm its MEIC page is
serving. The module's own dashboard (5050/5051) was retired on 2026-08-12 — the console reads both
ledgers and tags every row with the mode it came from, so paper and live are separated by the data
rather than by which port you opened. The supervisor keeps the console running; you should not need
to start anything here.

## Step 3 — Paper-trading loop (the supervisor's `meic-paper` job)

The paper loop is NOT started here. The orchestrator's supervisor runs it as the `meic-paper` job
whenever `modules.meic.enabled` is true in `~/.cherrypick/config.json` (one `paper_loop --once` per
`paper.tick_interval_seconds`, time-gated to market hours). There is no second launcher to
register: the module's own scheduled task (`--install-task`) was removed on 2026-10-08.

Check that it is being driven, from the repo root:

```bash
python packages/orchestrator/run.py ps
```

`meic-paper` should be listed. If the supervisor itself is not running, run `/install` (or the
installer) rather than starting the loop by hand. For a one-off manual iteration, e.g. a final
force-close pass, `python -m cherrypick.meic.paper_loop --once` is still safe.

Tell the user:
"Paper-trading session started — the paper loop runs as the supervisor's `meic-paper` job on the cadence set by `paper.tick_interval_seconds`, across every enabled arm in the registry (~/.cherrypick/config/meic.risk.json, or the shipped control-only example), self-healing and time-gated to market hours. It rolls the session into daily_summary at the 16:00 settlement pass; the suite review reports the day across every module. Writes go to ~/.cherrypick/data/meic/paper_trades.db only; the live account and ~/.cherrypick/data/meic/meic_trades.db are untouched. Read surface: the console at http://127.0.0.1:5070/meic (rows carry their own paper/live mode). Stop it by switching `modules.meic.enabled` off on the console's Config page (or `python packages/orchestrator/run.py stop meic-paper` for a moment); run /paper-report for a synthesized write-up or `python -m cherrypick.review build --session <date>` for the day's review on demand."
