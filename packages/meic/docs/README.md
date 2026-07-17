# MEIC documentation

Guides for the cherrypick **MEIC** engine — 0DTE multiple-entry iron condors. The operating contract the
agent follows lives in [`../CLAUDE.md`](../CLAUDE.md); the full entry-gate catalog is
[`../GATES.md`](../GATES.md). For the suite-wide picture see the root
[documentation index](../../../docs/README.md).

## Start here

| Doc | What it covers |
|---|---|
| [setup.md](setup.md) | Install, credentials (OS keyring), the managed `~/.cherrypick` home, and first connection. |
| [strategy.md](strategy.md) | The MEIC strategy: structure, entry logic, the VIX-banded delta scale, regime gates (VIX/VIX1D/ATR/GEX), and the settlement-aware exit cascade (no profit target). |
| [risk-profiles.md](risk-profiles.md) | The conservative → moderate → aggressive → very-aggressive **risk ladder**, the trade-offs at each tier, and progression guidance. |

## Paper trading & variance testing

| Doc | What it covers |
|---|---|
| [paper-trading.md](paper-trading.md) | The parallel-shadow paper engine: how it marks/exits every profile against live quotes with zero capital, the deterministic EOD reports, and the self-healing daemon. |
| [paper-experiments.md](paper-experiments.md) | The experiment-cell profiles (`large-spx`, `small-xsp`, `…-holdtoexpiry`, delta sweeps, GEX-gated, timing) that pin one `(symbol, wing, credit)` cell for account-size / single-lever comparison. |
| [paper-practice-plan.md](paper-practice-plan.md) | A structured plan for building confidence in the paper workflow before any live consideration. |

## Reference

| Doc | What it covers |
|---|---|
| [operating.md](operating.md) | Day-to-day operation: sessions, the streamer, the dashboard, and routine checks. |
| [0dtespx-api.md](0dtespx-api.md) | Notes on the 0DTE SPX data/API specifics the engine depends on. |

## How MEIC relates to the rest of the suite

- The **orchestrator** (`packages/orchestrator`) drives this module by subprocess for unattended paper
  collection and reads its paper DB for cross-module reporting — see
  [reporting-and-dashboard.md](../../../docs/reporting-and-dashboard.md).
- The **GEX** dashboard (`packages/gex`) shares the same `cherrypick.core.gex` engine this module's GEX
  regime gate uses.
- Suite-wide guardrails (paper↔live isolation, no AI/network on the loop path, masked accounts) are in
  [guardrails-and-modes.md](../../../docs/guardrails-and-modes.md) and enforced here too.
