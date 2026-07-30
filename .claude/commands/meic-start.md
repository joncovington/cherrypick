---
description: Start a MEIC agent trading session (streamer + agent loop) from the monorepo root
---

Start a full **MEIC** agent session — the live/interactive **agent trading loop** described in
`packages/meic/CLAUDE.md`. (This is the human-driven agent path, *not* the automated paper scheduler that
`/install` runs.) It delegates to the module's own start command; the root just sets the working
directory and adds a mode pre-check.

1. **Work from `packages/meic`.** The module's command and `CLAUDE.md` use paths relative to it —
   `src/…`, `CLAUDE.md`, `.claude/…` all live under `packages/meic/`. Run the steps from there.

2. **Mode pre-check.** Read MEIC's config (`~/.cherrypick/config/meic.json`, or `packages/meic/config.json` until migrated) → `enable_live_trading`. If `true`, this session
   can place **live orders** — stop and confirm with me before starting. If `false`/absent (paper),
   continue.

3. **Follow the module's start flow** — `packages/meic/.claude/commands/MEIC-start.md`:
   - Ensure the **standalone streamer** — the suite's single producer since the 2026-07-21 cutover — is
     up (`python packages/streamer/run.py --status`: require `running: true` AND a small
     `oldest_event_age_s` in-session). If down, `python packages/orchestrator/run.py install` starts it.
     **Never start MEIC's own `src/streamer.py`** while the standalone producer runs — it is the
     disabled rollback path, and two producers means two DXLink writers on one cache and account. Then
   - read `packages/meic/CLAUDE.md` in full and run the MEIC agent loop — invoke the `/loop` skill with:
     *"Execute the next MEIC agent loop iteration following the operating instructions in
     packages/meic/CLAUDE.md."*

4. Tell me startup is complete and the loop is self-pacing.
