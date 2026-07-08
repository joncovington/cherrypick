---
description: Start EarningsAgent's trading loop and keep it running through today's full market session, open to close.
---

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
