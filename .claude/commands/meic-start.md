---
description: Start a MEIC agent trading session (streamer + agent loop) from the monorepo root
---

Start a full **MEIC** agent session — the live/interactive **agent trading loop** described in
`packages/meic/CLAUDE.md`. (This is the human-driven agent path, *not* the automated paper scheduler that
`/install` runs.) It delegates to the module's own start command; the root just sets the working
directory and adds a mode pre-check.

1. **Work from `packages/meic`.** The module's command and `CLAUDE.md` use paths relative to it —
   `src/…`, `CLAUDE.md`, `.claude/…` all live under `packages/meic/`. Run the steps from there.

2. **Mode pre-check.** Read `packages/meic/config.json` → `enable_live_trading`. If `true`, this session
   can place **live orders** — stop and confirm with me before starting. If `false`/absent (paper),
   continue.

3. **Follow the module's start flow** — `packages/meic/.claude/commands/MEIC-start.md`:
   - Ensure the DXLink streamer is up (`python src/streamer.py --status`; if `running` is false, start it
     hidden in the background), then
   - read `packages/meic/CLAUDE.md` in full and run the MEIC agent loop — invoke the `/loop` skill with:
     *"Execute the next MEIC agent loop iteration following the operating instructions in
     packages/meic/CLAUDE.md."*

4. Tell me startup is complete and the loop is self-pacing.
