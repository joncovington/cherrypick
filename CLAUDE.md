# cherrypick suite (monorepo)

One workspace for the trading-tool suite. Work in the package for your area — each has its own CLAUDE.md:

- **packages/orchestrator** — the orchestrator: watchdog, OS scheduler, notifications, and the read side
  (report / dashboard / reconcile / calibrate). Drives the modules **by subprocess**, never by import.
- **packages/meic** — MEIC 0DTE multiple-entry iron-condor trading module.
- **packages/earnings** — earnings-play trading module (defined-risk strategies).
- **packages/gex** — the live GEX (gamma exposure) engine and spot-trail recorder. It computes and
  records; the console renders it.
- **packages/streamer** — the standalone market-data streamer: the suite's **single** producer of the
  shared stream cache every module reads. Modules declare their symbols via `state/stream_requests/`;
  it streams the union. Nothing else may write that cache.
- **packages/flies** — 0DTE net-credit butterfly ("profit forest") module. Paper by default, with a
  deliberately narrow, per-day-armed live pilot (one arm, one symbol, one incomplete position at a
  time); deliberately built to make a negative result usable: floors are measured after fees, and a
  book-level floor always carries the price band over which it holds.
- **packages/console** — the reactive web UI (Node + TypeScript, React SPA on 127.0.0.1:5070) and the
  suite's **only** read surface since 2026-08-12: every module's read models plus the research and
  screening surfaces in one app. The supervisor keeps it running as an always-on resident job, restarted on
  death and on a stale `state/console.heartbeat` (a wedged Node event loop stays alive). Read-only
  over every other package's data (its own store is `~/.cherrypick/data/console/`); reads the shared
  suite credential and never writes one, gating its write-oriented functions on the token's probed
  scope; no order-placement code paths.
- **packages/desk** — ⚠️ **EXPERIMENTAL.** The **manual trading desk** and the suite's only
  *discretionary* live-order path (meic/earnings/flies each have a live loop behind their own
  `enable_live_trading` gate; this has no loop): a foreground, human-initiated CLI for
  discretionary live orders, authorized entirely on its own (own config, own PIN kept as a salted
  verifier, per-order ticket, own policy gates) so it never touches a module's `enable_live_trading`.
  It stores no broker secrets — it borrows a module's keyring session, because borrowing credentials
  is not borrowing permissions. Not a strategy module — no loop, no
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