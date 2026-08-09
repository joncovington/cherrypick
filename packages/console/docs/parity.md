# Dashboard parity checklist

Card-by-card status of the console against the surfaces it replaces (re-audited 2026-08-09 evening).
"Done" means the same reads are served, not necessarily pixel-identical.

## Suite dashboard (:8787)

| Card | Status |
|---|---|
| Header status pill / watchdog age / session / findings | done (Overview); active-alerts callout (WARN/CRITICAL filter) and notify-channels line missing |
| Suite stats strip (net, trades, win %, avg) | done |
| Suite equity — cumulative net P&L + module lines | done; missing: 2x-slippage restatement, best/worst-day metrics, VIX right-axis overlay, data-epoch marker |
| Champions & challengers (calibrate) | **missing** — needs a calibrate port (readings, qualification bars, recommendation) |
| System: scheduled tasks / modules installed / config summary / doctor live checks | **missing** |
| Live ops: halt flag, per-module live gates, reconcile panel | **missing** — broker-touching; port deliberately |
| End-of-day card + md report links/rendering | **missing** |
| Recent logs (merged tail) | done; level-filter buttons and the red-bordered live-logs sub-block missing |

## MEIC dashboard (:5050/:5051)

| Card | Status |
|---|---|
| Sidebar status pill (loop freshness LIVE/IDLE) + meta (last loop, IV rank, underlying) | **missing** |
| Symbol + profile selectors (page-wide scope) | **missing** — console has mode only |
| Period stats grid | done |
| Today's trades / trade table | partial — per-leg put/call status badges, stop-adjustment columns, AI reasoning missing |
| NLV over time | done |
| Daily P&L calendar | done |
| Signal breakdowns (delta band, wing, symbol, weekday) | done |
| Win rate by session / Avg P&L by IV-rank band | **missing** |
| Exit reasons / fee drag | done |
| Regime coverage | **missing** |
| Trade log filters (date range, outcome, reason, search) | **missing** — 50 recent rows only |
| Performance view: profile comparison, arm scorecard, stop policies, risk-adjusted metrics, equity + underwater charts, study arms, six per-period charts + table | **missing** — the largest remaining block |

## Flies dashboard (:5052) — at parity+

| Card | Status |
|---|---|
| Today tiles | done except the max-possible-loss tile |
| Profit forest (fills, floors, settlement marker, x/y controls) | done |
| Session timeline (entry-window→close axis, gaps named, replayed book) | done |
| Decision journal (Gantt + table) | done |
| Positions / book floors | done (reference columns + pills) |
| Arm divergence | **missing** (last reference card) |
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
| KPI row | done except Capital Basis |
| Equity panels (cumulative / rolling 4w / rolling 1w / per-week) | partial — per-week bars only; no cumulative or rolling equity lines |
| Open positions | done except the totals row |
| Cross-strategy comparison | done except sample-progress bars (n/target with significance coloring) |
| Regime coverage heat + rejection histogram | **missing** |
| Per-strategy detail (equity+drawdown chart, PF pass/fail, Sharpe, max DD, IV crush) | **missing** |
| Footer caveats block | **missing** |

## Scout (:5057)

Done except narrative/describe prose. Console additions beyond scout: chain delta+OI picker,
STO/BTO highlights, ±EM band, scope-gated real dry-run validation.
