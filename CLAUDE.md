# cherrypick suite (monorepo)

One workspace for the trading-tool suite. Work in the package for your area — each has its own CLAUDE.md:

- **packages/orchestrator** — the orchestrator: watchdog, OS scheduler, notifications, and the read side
  (report / dashboard / reconcile / calibrate). Drives the modules **by subprocess**, never by import.
- **packages/meic** — MEIC 0DTE multiple-entry iron-condor trading module.
- **packages/earnings** — earnings-play trading module (defined-risk strategies).
- **packages/gex** — live GEX (gamma exposure) dashboard; a self-hosted read-only surface.
- **packages/flies** — 0DTE net-credit butterfly ("profit forest") module. Paper by default, with a
  deliberately narrow, per-day-armed live pilot (one arm, one symbol, one incomplete position at a
  time); deliberately built to make a negative result usable: floors are measured after fees, and a
  book-level floor always carries the price band over which it holds.
- **packages/desk** — the **manual trading desk**: a foreground, human-initiated CLI for
  discretionary live orders, authorized entirely on its own (own config, own keyring PIN, per-order
  ticket) so it never touches a module's `enable_live_trading`. Not a strategy module — no loop, no
  schedule, no ledger. It exists so placing one discretionary order never requires temporarily
  flipping a guarded, plan-gated module flag. Never scheduled; no automated package may import it.

The shared library `cherrypick.core` is **`packages/core`**, a sibling package in this same monorepo,
installed as an editable dependency by every other package. Fresh clone: `pip install -e packages/core`
first (or run `scripts/dev-install.ps1`/`.sh` from the repo root, which installs it and every package).

Suite-wide guardrails apply across every package (each package's CLAUDE.md states them): instruction
files hold no code; account numbers masked to `****1234`; portable paths only; human-voice docs/commits
(no AI attribution); no MCP/network/AI on any loop-decision or reliability path; paper↔live isolation
(the orchestrator only drives paper; its one live-config action is onboarding/account selection).

Any developer initiated documentation review and update must also review and update package documentation.