# Research records

This folder holds the suite's research: what was tested, how the test was declared before any data was
read, and what it found. These files are records, not documentation of the software. They keep the
wording a test was declared in, because a declaration changed after the fact is no longer a test.

Results computed from licensed vendor data are not here: they stay under `~/.cherrypick` (see
[packages/backtest/CLAUDE.md](../packages/backtest/CLAUDE.md)). A file here records a verdict and the local
path of the result file, never a licensed figure.

| File | What it is |
|---|---|
| [edge-hypotheses-plan.md](edge-hypotheses-plan.md) | The register of research items: each idea stated as the edge it would need, what would falsify it, the declared test, and the verdict. |
| [hypotheses.json](hypotheses.json) | The register in machine-readable form, one row per item with its status, rule and pointer. `cherrypick hypotheses` checks it, and the console's Backtest studies board reads it. |
| [history-studies-2026-10.md](history-studies-2026-10.md), [-b](history-studies-2026-10-b.md), [-c](history-studies-2026-10-c.md) | Questions answered from history rather than by waiting: each one modelled, with a pass test declared before the run. |
| [flow-signal-plan.md](flow-signal-plan.md) | Large options orders as a signal: the declared test (research item 32). It failed. |
| [signal-log-plan.md](signal-log-plan.md) | The chart setups' historical study, scored against a matched random baseline. No setup was profitable after costs and better than random. |
| [openingrange.md](openingrange.md) | The frozen contract of the opening-range study (09:30 to 10:00 ET against the session's travel), a declared holdout read by `core.openingrange` and the console. It gates nothing. |
| [range-features.md](range-features.md) | The declared range-features study: four chart features against a HAR baseline for SPX range. It found nothing. |
| [box-edge-0dte-routine.md](box-edge-0dte-routine.md) | A daily SPX 0DTE routine reconstructed from public sessions, tested as reversion at the expected-move box edge (research item 8). It failed. |
| [breakeven-condor.md](breakeven-condor.md) | The 0DTE break-even iron condor and its fit as a MEIC arm. |
| [vxm-plan.md](vxm-plan.md) | The contango switch in Mini VIX futures, answered from settlement history. The half-size arm earns less than SVXY for the same drawdown, so it is not built. |

Sources are credited in [docs/strategy-sources.md](../docs/strategy-sources.md), each with our measured result.
