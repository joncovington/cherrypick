Start the full MEIC session: verify the market-data producer, then the agent loop.

## Step 0 — Live or paper? (always first)

Read this module's config (`paths.config_path()`: `~/.cherrypick/config/meic.json`, else the shipped example) → `enable_live_trading`. If it is not `true`, this is a paper
session: continue. If it is `true`, this session can place **real orders**. Stop, and **show the
disclaimer verbatim, every time** (from `DISCLAIMER.md` at the repo root; never paraphrase, shorten
or skip it because it was shown before):

> ⚠️ **EXPERIMENTAL PROTOTYPE — EDUCATIONAL USE ONLY — NOT FINANCIAL ADVICE.** This places
> **REAL, irreversible orders with real money** in your brokerage account. Options trading carries
> substantial risk of loss and is not suitable for all investors. The software can fail (bugs,
> stale data, outages); its limits reduce accidents but do not make trading safe. **You alone are
> responsible for every order and every loss.** Full text: `DISCLAIMER.md`.

Then confirm with the AskUserQuestion tool, exactly two options: **"YES — I accept the disclaimer;
start a LIVE session"** and **"No, cancel"**. Anything else stops here with no action taken. A YES
from an earlier session covers nothing.

## Step 1 — Market data (the standalone streamer)

Since the 2026-07-21 producer cutover the **standalone streamer** (`packages/streamer`) is the suite's
single writer of the shared stream cache; MEIC's own `cherrypick/meic/streamer.py` is the disabled rollback path.
**Never start `cherrypick/meic/streamer.py` while the standalone streamer runs** — two producers means two DXLink
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
true in the cherrypick config — use `python -m cherrypick.meic.streamer --status` / start instead. Exactly one
producer ever runs.)

## Step 2 — Agent loop

Invoke the `/loop` skill with the prompt:

> Execute the next MEIC agent loop iteration following the operating instructions in CLAUDE.md.

Tell the user:
"Startup complete — agent loop started. The loop will self-pace each iteration."
