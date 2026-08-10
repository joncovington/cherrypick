# Dashboard parity checklist

Card-by-card status of the console against the surfaces it replaces (re-audited after the completion pass, 2026-08-09).
"Done" means the same reads are served, not necessarily pixel-identical.

## Suite dashboard (:8787)

| Card | Status |
|---|---|
| Header status pill / watchdog age / session / findings | done (Overview); active-alerts callout (WARN/CRITICAL filter) and notify-channels line missing |
| Suite stats strip (net, trades, win %, avg) | done |
| Suite equity — cumulative net P&L + module lines | done; missing: 2x-slippage restatement, best/worst-day metrics, VIX right-axis overlay, data-epoch marker |
| Champions & challengers (calibrate) | done — core.metrics + profiles qualification ported; per-check progress bars |
| System: modules, services, config summary, halt flag | done (live doctor checks and OS task registry not ported — they need the orchestrator's own subprocess) |
| Live ops: halt flag, per-module live gates, reconcile panel | **missing** — broker-touching; port deliberately |
| End-of-day card + md report links/rendering | done (in-page markdown rendering, allowlisted files) |
| Recent logs (merged tail, level filters) | done; live-logs sub-block still missing |

## MEIC dashboard (:5050/:5051)

| Card | Status |
|---|---|
| Loop status pill (LIVE/IDLE) + IV rank / underlying chips | done |
| Symbol + profile selectors (page-wide scope) | done — threaded through every MEIC read |
| Era scope (defaults to the module's `CURRENT_ERA`, as its analytics do) | done — earlier eras reachable, never mixed in silently |
| Period stats grid | done |
| Today's trades / trade table | partial — per-leg put/call status badges, stop-adjustment columns, AI reasoning missing |
| NLV over time | done |
| Daily P&L calendar | done |
| Signal breakdowns (delta band, wing, symbol, weekday) | done |
| Win rate by session / Avg P&L by IV-rank band | done |
| Exit reasons / fee drag | done |
| Regime coverage (with degenerate flags) | done |
| Trade log filters (outcome, exit reason, search) | done — filters and paging both in SQL, so counts describe the scope and not the page; explicit date-range inputs still missing |

Every row-listing table in the console is server-paged against a true match count
(`Paged<T>` in `@console/shared`, `readers/paging.ts` on the server). No table stops
at a hidden row cap.
| Performance view: profile comparison, risk metrics, equity + underwater, study arms, per-period charts | done. **Missing: arm scorecard (breakeven identity) and stop-policy counterfactuals** |

## Flies dashboard (:5052) — at parity+

| Card | Status |
|---|---|
| Today tiles | done |
| Profit forest (fills, floors, settlement marker, x/y controls) | done |
| Session timeline (entry-window→close axis, gaps named, replayed book) | done |
| Decision journal (Gantt + table) | done |
| Positions / book floors | done (reference columns + pills) |
| Arm divergence | done (>80% agreement flagged) |
| History: by arm/mode/window, fee drag, calendar (click→replay), trade log + filters | done |
| Performance: tiles, P&L bars, completion + why-misses, trend, live-vs-paper | done (trend and live-vs-paper are console-only additions) |
| Symbol filter (page-wide) | **missing** — arm/date only; live-quote spot assumes XSP |
| Voided-rows accounting line | **missing** (console suggestion) |

## GEX dashboard (:5055) — at parity+

All cards done and number-verified (walls/zero-gamma definitions matched). Remaining: WebSocket push
(console polls 15s — cache-cadence equivalent); regime-drift intraday chart is a proposed
console-only addition.

## Earnings strategy dashboard (static)

| Card | Status |
|---|---|
| KPI row | done (capital at risk included) |
| Equity panels (cumulative / rolling 4w / rolling 1w / per-week) | done |
| Open positions | done (totals row included) |
| Cross-strategy comparison + sample-progress bars | done |
| Regime coverage heat + rejection histogram | done |
| Per-strategy detail (equity+drawdown, PF pass/fail, Sharpe, max DD, IV crush) | done |
| Footer caveats block | done |

## Scout (:5057)

Done except narrative/describe prose. Console additions beyond scout: chain delta+OI picker,
STO/BTO highlights, ±EM band, scope-gated real dry-run validation.
