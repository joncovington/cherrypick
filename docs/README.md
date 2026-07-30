# cherrypick — Documentation

Reference documentation for the cherrypick suite, organized by functional area. Start with the
[User Guide](PROJECT.md) if you just want to install and run it; use the files below when you need to
understand *how* a part of the suite works or *why* it's built the way it is.

## Root reference docs (suite-wide)

| Doc | Covers |
|---|---|
| [PROJECT.md](PROJECT.md) | **User Guide** — plain-language install, setup, daily use, troubleshooting. |
| [operations.md](operations.md) | **Runbook** — the full task/daemon/port inventory, dependency order and time constants, the 09:00 ET checklist, normal-vs-real warnings, known gaps, and the 2026-07-29 cutover record. |
| [architecture.md](architecture.md) | How the suite fits together: the orchestrator + strategy modules, the shared `cherrypick.core` submodule, the read/write halves, per-schema dispatch, and the managed `~/.cherrypick` home. |
| [orchestrator-cli.md](orchestrator-cli.md) | Every `cherrypick` / `run.py` command, what it does, and its flags — the operational surface. |
| [reporting-and-dashboard.md](reporting-and-dashboard.md) | The read side: unified P&L report, calibration, the deterministic EOD reports (paper-eod + 7-section analysis), the AI EOD insight, the suite digest, the dashboard (static + live), and end-of-month log/report archiving. |
| [strategy-engines.md](strategy-engines.md) | The MEIC, Earnings, Flies, and GEX engines at a suite level, plus risk-profile **variance testing** — with pointers into each module's own docs. |
| [configuration-and-storage.md](configuration-and-storage.md) | The config model (orchestrator + per-module), the managed-home layout, environment overrides, and the databases / logs / report files each module reads and writes. |
| [guardrails-and-modes.md](guardrails-and-modes.md) | Paper vs. live isolation, the load-bearing invariants (no AI/network on the reliability path, masked accounts, defined-risk, correlation), credentials, and the one narrow live-config boundary. |
| [glossary.md](glossary.md) | Suite-wide terms (0DTE, IC, GEX, IV rank, MEIC, defined-risk, 1256, …). |
| [onboarding-redesign.md](onboarding-redesign.md) | **Shipped 2026-07-28**: the secrets + account workflow collapsed to one shared login (`cherrypick-broker`) and one no-module `connect` wizard — the shared-credential model, per-module overrides, unified status, and the design rationale. |

## Module docs (kept inside each module — the source of truth for that engine)

Suite-wide docs point into these rather than duplicating them; a module's own docs live next to its code
so they can't drift from it.

- **Orchestrator** — [`packages/orchestrator/CLAUDE.md`](../packages/orchestrator/CLAUDE.md) (build/architecture/invariants), [`ROADMAP.md`](../packages/orchestrator/ROADMAP.md) (shipped history).
- **MEIC** — [`packages/meic/CLAUDE.md`](../packages/meic/CLAUDE.md), [`GATES.md`](../packages/meic/GATES.md) (entry-gate catalog), and [`packages/meic/docs/`](../packages/meic/docs/): [strategy](../packages/meic/docs/strategy.md), [risk-profiles](../packages/meic/docs/risk-profiles.md), [paper-experiments](../packages/meic/docs/paper-experiments.md), [paper-trading](../packages/meic/docs/paper-trading.md), [operating](../packages/meic/docs/operating.md), [setup](../packages/meic/docs/setup.md).
- **Earnings** — [`packages/earnings/CLAUDE.md`](../packages/earnings/CLAUDE.md) and [`packages/earnings/docs/`](../packages/earnings/docs/): [strategies](../packages/earnings/docs/05-strategies.md), [screening-criteria](../packages/earnings/docs/screening-criteria.md), [configuration](../packages/earnings/docs/03-configuration.md), [entry-conditions](../packages/earnings/docs/04-entry-conditions.md), [exits](../packages/earnings/docs/10-exits.md), [strategy-testing-plan](../packages/earnings/docs/strategy-testing-plan.md), [glossary](../packages/earnings/docs/14-glossary.md).
- **GEX** — [`packages/gex/CLAUDE.md`](../packages/gex/CLAUDE.md), [`packages/gex/README.md`](../packages/gex/README.md).
- **Flies** — [`packages/flies/README.md`](../packages/flies/README.md) (start here), [`packages/flies/CLAUDE.md`](../packages/flies/CLAUDE.md) (strategy, honesty rules, the width-arm experiment) and [`packages/flies/docs/live-trading-plan.md`](../packages/flies/docs/live-trading-plan.md) (the gated live plan).
- **Streamer** — [`packages/streamer/CLAUDE.md`](../packages/streamer/CLAUDE.md) (the standalone market-data producer; subscription-registry contract) and [streamer-package-plan.md](streamer-package-plan.md) (why it was split out of MEIC).

## Conventions

- Everything the suite writes at runtime lives under **`~/.cherrypick`** (relocatable with
  `$CHERRYPICK_HOME`), never in a source checkout.
- Commands are shown as `python run.py <cmd>` from `packages/orchestrator`; a pip install also exposes
  them as `cherrypick <cmd>`.
- **Paper by default.** Nothing here places live orders on its own — see
  [guardrails-and-modes.md](guardrails-and-modes.md).
