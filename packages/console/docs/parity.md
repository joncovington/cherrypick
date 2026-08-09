# Dashboard parity checklist

Card-by-card status of the console against the surfaces it replaces, from a full inventory of the
old dashboards (2026-08-09). "Done" means the same reads are served, not necessarily pixel-identical.

## Suite dashboard (:8787)

| Card | Status |
|---|---|
| Header status pill / watchdog age / session / findings | done (Overview) |
| Suite stats strip (net, trades, win %, avg) | done (equity card) |
| Suite equity — cumulative net P&L + module lines | done (no VIX overlay, no 2x-slippage restatement, no data-epoch marker yet) |
| Champions & challengers (calibrate) | **missing** — needs a calibrate port (readings, qualification bars, recommendation) |
| System: scheduled tasks / modules installed / config summary / doctor live checks | **missing** |
| Live ops: halt flag, per-module live gates, reconcile panel | **missing** — reconcile is broker-touching; port deliberately |
| End-of-day card + md report links/rendering | **missing** |
| Recent logs (merged tail, level filters) | done (no per-level filter buttons yet); live-logs sub-block missing |
| Embeds | not applicable — the console's own pages replace them |

## MEIC dashboard (:5050/:5051)

| Card | Status |
|---|---|
| Period stats grid (today/week/month/year/all) | done |
| Today's trades table | partial — trade table exists; per-leg put/call status badges missing |
| NLV over time chart | done (empty until daily_summary gets closing_nlv rows) |
| Daily P&L calendar heatmap | done |
| Signal breakdowns (delta band, wing, symbol, weekday) | done |
| Win rate by session / Avg P&L by IV rank | **missing** |
| Exit reasons | done |
| Fee drag | done |
| Regime coverage | **missing** |
| Trade log with filters | partial — recent trades, no filters |
| Profile comparison / arm scorecard / stop policies / risk-adjusted metrics / equity+drawdown charts / per-period charts | **missing** |

## Flies dashboard (:5052)

| Card | Status |
|---|---|
| Today tiles (net, positions, open, risk-free, completion, fees) | done (max-possible-loss tile missing) |
| Payoff forest (per-arm payoff curves + floors) | done — fly.py payoff core ported (all five kinds, assignment fee, book floor/bands), live spot marker |
| Session timeline / decision journal | **missing** |
| Positions + book floors tables | done (M2) |
| By arm / fee drag by arm | done |
| By entry mode / entry window, daily calendar, trade log filters, completion table, why-misses | **missing** |

## GEX dashboard (:5055)

| Card | Status |
|---|---|
| GEX by strike (net / OI-vs-vol / abs views, walls + zero-gamma + spot overlays) | done |
| Intraday spot trail overlay | done |
| OI (positioning) + Volume (flow) metric panels | done |
| IV skew chart | done |
| OI by strike (mirrored, with volumes) | done |
| Volume by strike | done |
| Symbol selector | done |
| WebSocket push | polling 15s (cache-refresh cadence equivalent); WS push later |
| Regime history tables | done (console addition — not on the old page) |

## Earnings strategy dashboard (static)

| Card | Status |
|---|---|
| KPI row (expectancy, total net, closed trades, strategies active) | done (capital basis missing) |
| Equity panels (cumulative / rolling 4w / 1w / per-week) | partial — per-week bars only |
| Open positions | done |
| Cross-strategy comparison (win rate, PF, expectancy, sample progress) | done (sample-progress bars missing) |
| Regime coverage heat + rejection histogram | **missing** |
| Per-strategy detail (equity+drawdown, Sharpe, IV crush) | **missing** |

## Scout (:5057)

Watchlist, symbol detail (candles/levels/trend), builder (chain with delta+OI, payoff/POP), screener,
staged dry-run tickets, earnings browse + forward preview: done. Narrative/describe prose: missing.
