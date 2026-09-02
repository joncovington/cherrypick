---
description: Launch the console's desktop window (Electron shell) — a window only, never a server
argument-hint: [--no-build]
---

Launch the **desktop shell** (`packages/console/desktop`, `@console/desktop`): the cherrypick
console as an Electron window. This is the one console process a human starts by hand — the server
itself stays supervisor-owned, and the shell **never starts it**. It only points a window at
`127.0.0.1:<port>` (port from `serve.port` in `~/.cherrypick/config/console.json`, else 5070).

## Launch

1. From `packages/console/desktop`, run **`pnpm start`** in the background (it blocks for the
   window's lifetime). With `--no-build`, skip straight to launching only if a prior build exists
   (`dist/` present); `pnpm start` builds first, which is normally what you want.
2. Always go through `pnpm start` — never the Electron binary directly. The launcher
   (`scripts/start.mjs`) clears `ELECTRON_RUN_AS_NODE`, which VS Code sets in its integrated
   terminal; with it set, Electron runs as plain Node and the failure reads like an app bug.
3. Give it a few seconds, then confirm it came up: the process should still be alive, and its
   output free of errors. A second launch is harmless — the single-instance lock focuses the
   existing window instead.

## When the window shows the not-running splash

That is the shell doing its job, not a launch failure. The console server is down, and the splash
names which of the usual causes it found (supervisor down, not built, disabled in config,
crash-looping, wedged) with the one command that fixes it. Fix the *server* side — `/console
--status` walks the same diagnosis — and the shell picks the console up on its own 5-second retry
without being restarted.

Do **not** start a console server by hand to make the window fill in: a hand-started server takes
the port the supervisor's child needs, and every supervised restart then dies on `EADDRINUSE`.

## Report

That the window is up and what it is pointing at; that quitting the window never stops the console;
and — if the splash is showing — which cause it named and the fix, rather than treating the launch
as failed.
