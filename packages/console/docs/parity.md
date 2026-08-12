# Dashboard parity checklist

Card-by-card status of the console against the surfaces it replaces (re-audited after the completion pass, 2026-08-09).
"Done" means the same reads are served, not necessarily pixel-identical.

## Suite dashboard (:8787)

| Card | Status |
|---|---|
| Header status pill / watchdog age / session / findings | done (Overview); active-alerts callout (WARN/CRITICAL filter) and notify-channels line missing |
| Suite stats strip (net, trades, win %, avg) | done |
| Suite equity — cumulative net P&L + module lines | done; missing: 2x-slippage restatement, best/worst-day metrics, VIX right-axis overlay, data-epoch marker |
| Champions & challengers (calibrate) | moved to its own page (`/champions`), one tab per module — every arm shown, no cap; the Overview keeps the champion column on the System card |
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
| Performance view: profile comparison, risk metrics, equity + underwater, study arms, per-period charts | done. **Missing: arm scorecard (breakeven identity) and stop-policy counterfactuals** |

Every row-listing table in the console is server-paged against a true match count
(`Paged<T>` in `@console/shared`, `readers/paging.ts` on the server). No table stops
at a hidden row cap.

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

## Per-arm portfolios: what the API serves and what is still missing (2026-08-11)

Both modules now record an **entry-attempts ledger** — one row per evaluated entry opportunity per
arm — and MEIC has a **profit forest** to match flies'. The server side is complete; the React views
are not built yet.

| Surface | Endpoint | Status |
|---|---|---|
| Arm rail — per-arm cadence countdown, entries today, refusal mix, what is holding it | `/api/{meic,flies}/attempts` | done, both pages |
| Attempt timeline — one lane per arm, every evaluated opportunity by outcome | `/api/{meic,flies}/attempts` | done, both pages |
| Strike occupancy — longs vs shorts per arm, refusing strikes ringed | `/api/{meic,flies}/occupancy` | done, both pages |
| MEIC profit forest — per-profile curves, each condor faint behind the aggregate, stop-released strikes | `GET /api/meic/forest` | done |
| Refusal history (per session, stacked by reason) | — | not started |
| Day replay (scrub the timeline, occupancy rebuilding) | — | not started |
| Rule attribution / entry-slot yield | — | not started |
| Session grid (sessions × arms) | — | not started |

**One known rough edge.** The attempts views default to the latest day in the *attempts* ledger
while the forest and occupancy views default to the latest day with *positions*. On a morning where
nothing has filled yet those differ, so the cards can show yesterday's book beside today's attempts.
Both carry their own date in the heading, so it is labelled rather than misleading — but they should
share one day selector.

**What the attempts payload is for.** With each arm an independent portfolio on unbounded capital,
every arm sees the same market with the same money — so the only thing differentiating them is which
entries the rules let through. The refusals are the primary signal. The arm rail answers "why is this
arm quiet right now" (cadence, the sign rule, or a gate) without reading logs; the timeline answers
it for any past minute of the session.

**`no_fill` is a distinct outcome and the UI must keep it distinct.** Under a fill-based cadence
clock an entry that cleared every gate and simply did not fill neither spent the arm's slot nor was
refused by a rule. Rendering it as a gate refusal would make the gates look stricter than they are.

**The reader degrades rather than fails.** A ledger written by a checkout predating the attempts work
has no such table; `readEntryAttempts` returns an empty payload rather than erroring, because the
console is read-only over every other package's data and must never fail a page over a schema it does
not own.

**Paper and live are not the same experiment, and the pages should say so.** Paper runs every arm
concurrently and accepts account-level netting between them; live runs one arm at a time. That is a
deliberate difference, but it means a paper result is not a live prediction for any effect that
depends on netting.
