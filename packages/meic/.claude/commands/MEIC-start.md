Start the full MEIC session: verify the market-data producer, then the agent loop.

## Step 1 — Market data (the standalone streamer)

Since the 2026-07-21 producer cutover the **standalone streamer** (`packages/streamer`) is the suite's
single writer of the shared stream cache; MEIC's own `src/streamer.py` is the disabled rollback path.
**Never start `src/streamer.py` while the standalone streamer runs** — two producers means two DXLink
writers on one cache and one account.

Check the producer (from the monorepo root):

```bash
python ../streamer/run.py --status
```

Require **both** `"running": true and a small `oldest_event_age_s` during market hours (a connected but
silent socket is the 2026-07-01 failure mode). If it is down, the orchestrator normally owns it — start
it via `python ../orchestrator/run.py install` (idempotent; also re-registers tasks), or directly:

```bash
python ../streamer/run.py    # blocks; run detached/hidden
```

(Only if this box was deliberately rolled back to MEIC-as-producer — `modules.meic.streamer.enabled`
true in the cherrypick config — use `python src/streamer.py --status` / start instead. Exactly one
producer ever runs.)

## Step 2 — Agent loop

Invoke the `/loop` skill with the prompt:

> Execute the next MEIC agent loop iteration following the operating instructions in CLAUDE.md.

Tell the user:
"Startup complete — agent loop started. The loop will self-pace each iteration."
