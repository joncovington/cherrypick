---
description: Start an Earnings agent trading session for today, from the monorepo root
---

Start an **Earnings** agent session — the live/interactive **agent trading loop** described in
`packages/earnings/CLAUDE.md`, kept alive through today's close. (This is the human-driven agent path,
*not* the automated paper scheduler that `/install` runs.) It delegates to the module's own start
command; the root just sets the working directory and adds a mode pre-check.

1. **Work from `packages/earnings`.** The module's command and `CLAUDE.md` use paths relative to it
   (`CLAUDE.md`, `.claude/…`, `src/…`). Run the steps from there.

2. **Mode pre-check.** Read Earnings' config (`~/.cherrypick/config/earnings.json`, or `packages/earnings/config/config.json` until migrated) → `enable_live_trading`. If `true`,
   this session can place **live orders** — stop. **Show the disclaimer, verbatim, every time** (from `DISCLAIMER.md` at the repo root; never
   paraphrase it, shorten it or skip it because it was shown before):

   > ⚠️ **EXPERIMENTAL PROTOTYPE — EDUCATIONAL USE ONLY — NOT FINANCIAL ADVICE.** This places
   > **REAL, irreversible orders with real money** in your brokerage account. Options trading carries
   > substantial risk of loss and is not suitable for all investors. The software can fail (bugs,
   > stale data, outages); its limits reduce accidents but do not make trading safe. **You alone are
   > responsible for every order and every loss.** Full text: `DISCLAIMER.md`.

   Then confirm with the AskUserQuestion tool, exactly two options: **"YES — I accept the
   disclaimer; start a LIVE session"** and **"No, cancel"**. Anything else stops here. If `false`/absent
   (paper), continue.

3. **Follow the module's start flow** — `packages/earnings/.claude/commands/earnings-start.md`:
   - First check `packages/earnings/.claude/scheduled_tasks.lock` for a live PID; if another loop already
     holds it, stop and tell me (don't start a second concurrent loop against the same trade DB).
   - Read `packages/earnings/CLAUDE.md` in full, then run its Loop Steps from Step 0 on repeat, keeping
     the session alive through today's close-window handling (schedule the next boundary instead of
     ending the loop until today's close work is fully done).
