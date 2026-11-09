# MEIC documentation

Guides for the cherrypick **MEIC** engine — 0DTE multiple-entry iron condors. The operating contract the
agent follows lives in [`../CLAUDE.md`](../CLAUDE.md); the full entry-gate catalogue is
[`../GATES.md`](../GATES.md). For the suite-wide picture see the root
[documentation index](../../../docs/README.md).

## Start here

| Doc | What it covers |
|---|---|
| [setup.md](setup.md) | Install, credentials (OS keyring), the managed `~/.cherrypick` home, and first connection. |
| [strategy.md](strategy.md) | The MEIC strategy: structure, entry logic, the VIX-banded delta scale, regime gates (VIX/VIX1D/ATR/GEX), and the settlement-aware exit cascade (no profit target). |
| [risk-profiles.md](risk-profiles.md) | The example conservative to very-aggressive preset ladder that `/set-risk-profile` can apply to the live config (not shipped), and the design rules every arm obeys: the arm x symbol portfolio model, arm-relative thresholds, and the invariants a change must preserve. |
| [reference.md](reference.md) | The read-side CLI, quote-history replay, advisor bounds, the arm registry, the declared measurement boundaries, and every config key. |

## Paper trading & variance testing

| Doc | What it covers |
|---|---|
| [paper-trading.md](paper-trading.md) | The parallel-shadow paper engine: how it marks/exits every enabled arm against live quotes with zero capital, the fee and slippage model, and the self-healing loop. A base install runs one arm, `control`; any other arm is this machine's own registry. |
| [paper-experiments.md](paper-experiments.md) | How MEIC's paper arms are designed and read: the breakeven identity, the independent-sampling model, the derived stop policies computed read-side from `control`'s recorded paths, and how to read results (sessions before trades). |

## Reference

| Doc | What it covers |
|---|---|
| [operating.md](operating.md) | Day-to-day operation: sessions, the streamer, the console, and routine checks. |
| [0dtespx-api.md](0dtespx-api.md) | The 0DTESPX API and its acceptable-use limits, and the practice-session backtester built on it. |

## How MEIC relates to the rest of the suite

- The **orchestrator** (`packages/orchestrator`) drives this module by subprocess for unattended paper
  collection and, while a human has armed the day with `/live-meic-start`, the `meic-live` tick; it
  reads both ledgers for cross-module reporting — see
  [reporting-and-dashboard.md](../../../docs/reporting-and-dashboard.md). Live arming, settlement and
  the quote-history replay are described in [`../CLAUDE.md`](../CLAUDE.md) and
  [operating.md](operating.md).
- The **GEX** engine (`packages/gex`) shares the same `cherrypick.core.gex` engine this module's GEX
  regime gate uses.
- Suite-wide guardrails (paper↔live isolation, no AI/network on the loop path, masked accounts) are in
  [guardrails-and-modes.md](../../../docs/guardrails-and-modes.md) and enforced here too.
