---
description: Start Earnings's trading loop and keep it running through today's full market session, open to close.
---

## Step 0 — Live or paper? (always first)

Read this module's config (`paths.config_path()`: `~/.cherrypick/config/earnings.json`, else the shipped example) → `enable_live_trading`. If it is not `true`, this is a paper
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

Read `CLAUDE.md` in full, then begin executing its Loop Steps starting from Step 0, on repeat,
for the rest of today's market session.

**Before starting**: check `.claude/scheduled_tasks.lock` for a live PID. If another process
already holds it, stop and tell the user instead of starting a second loop against the same
trade database — two concurrent loops could double-scan, double-log, or double-enter the same
position.

**Keep the session alive all day, not just until the next quiet tick.** The wakeup-interval
table's "end loop" rows exist to save cost on a genuinely idle night, but for this command the
intent is different: run continuously from now through today's close-window handling, so
nothing needs to be manually restarted partway through the day. Wherever the table would say
"end loop," schedule a wakeup for the next relevant boundary instead (the next entry window,
the next `close_window_start`, or 30-90 minutes out if nothing else applies) and keep going.
Only actually end the session once today's close-window work is fully done — every position
either force-closed or confirmed to have no open positions left, and no further entry window
remains today.

Everything else — what to check, thresholds, order building, logging — is exactly what
`CLAUDE.md`'s Loop Steps already describe. This command only changes *whether the session
keeps itself alive*, not any trading decision.
