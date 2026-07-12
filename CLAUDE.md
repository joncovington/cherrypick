# cherrypick suite (monorepo)

One workspace for the trading-tool suite. Work in the package for your area — each has its own CLAUDE.md:

- **packages/orchestrator** — the orchestrator: watchdog, OS scheduler, notifications, and the read side
  (report / dashboard / reconcile / calibrate). Drives the modules **by subprocess**, never by import.
- **packages/meic** — MEIC 0DTE multiple-entry iron-condor trading module.
- **packages/earnings** — earnings-play trading module (defined-risk strategies).

The shared library `cherrypick.core` is the **`cherrypick-core`** submodule, vendored per package at
`packages/<pkg>/src/_core` (one URL, pinned SHA). Fresh clone: `git submodule update --init --recursive`.

Suite-wide guardrails apply across every package (each package's CLAUDE.md states them): instruction
files hold no code; account numbers masked to `****1234`; portable paths only; human-voice docs/commits
(no AI attribution); no MCP/network/AI on any loop-decision or reliability path; paper↔live isolation
(the orchestrator only drives paper; its one live-config action is onboarding/account selection).
