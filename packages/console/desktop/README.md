# @console/desktop

The cherrypick console as a desktop window.

```bash
pnpm start        # builds, then launches (from packages/console/desktop)
```

## Window only, deliberately

This shell **never starts the console server**. The supervisor owns that process, and a second
console would take the port the supervisor's own child needs — after which every restart it attempts
dies on `EADDRINUSE`. One owner of `:5070`, always.

So when the port does not answer, the useful thing the shell can do is *diagnose*. It reads the same
local files `/console --status` reads and names which of four things went wrong, with the one command
that fixes it:

| What it found | What it says |
|---|---|
| No supervisor heartbeat, or one older than 90s | The supervisor is not running — `run.py install` |
| The `console` job disabled, reason "not built" | `pnpm install && pnpm build` |
| The `console` job disabled in config | Set `"console": {"enabled": true}` |
| `resident_state: backoff` | It is crash-looping — read `logs/console/console.log` |
| The job is up but its heartbeat is stale | It is wedged; the supervisor is about to restart it |

It retries every 5s, so a console that comes back is picked up without touching the window. Quitting
the shell never stops the console.

## No native rebuild

`better-sqlite3` and `@napi-rs/keyring` live in the server, which runs as its **own Node process**
spawned by the supervisor — they are never loaded inside Electron. That is what keeps `electron-rebuild`
out of this package entirely, and it is a consequence of the window-only design rather than a
coincidence: bundling the server into the shell would bring both native modules with it.

## `ELECTRON_RUN_AS_NODE`

`pnpm start` goes through `scripts/start.mjs`, which clears that variable before launching. VS Code
sets it in its integrated terminal, and with it set the Electron binary runs as **plain Node**: no
main process, `process.type` undefined, and `import ... from "electron"` resolves to the npm package's
shim (a path string) instead of the built-in module. The failure reads like a bug in the app, so the
launcher removes the trap rather than documenting it.

## Layout

| file | role |
|---|---|
| `src/main.ts` | the main process: single-instance lock, window, tray, menu, retry loop |
| `src/status.ts` | why the console is not answering — file reads plus one loopback health GET |
| `src/splash.ts` | the not-running page, self-contained (it loads as a data URL) |
| `src/bounds.ts` | remembered window geometry in `data/console/desktop-window.json` |
| `scripts/start.mjs` | the launcher that clears `ELECTRON_RUN_AS_NODE` |

Home and port resolution are **not** here — they are in `@console/shared` (`paths.ts`), shared with
the server so the two cannot disagree about which port to open or which `~/.cherrypick` to read.

Packaging (an installer, a signed binary) is deliberately deferred: run it from the repo. It would
still assume the suite and Python are present, since the console bridges to Python for the keyring.
