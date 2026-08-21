# Dashboard parity checklist

Card-by-card status of the console against the surfaces it replaced (re-audited after the completion
pass, 2026-08-09). "Done" means the same reads are served, not necessarily pixel-identical.

**Those surfaces are gone as of 2026-08-12.** The suite dashboard, the MEIC/flies/GEX dashboards, the
earnings strategy dashboard, and scout's web app were deleted — console is the suite's only read
surface. Recover any of them from the `pre-console-only` tag.

So this file changed jobs. It is no longer a transition checklist against a running original; every
row still marked **missing** below is now simply **console's backlog**, and there is nothing left to
diff against except that tag. The gaps were accepted knowingly at deletion time rather than gated on:

- **Live ops** (halt flag, per-module live gates, reconcile panel) — the only surface with *no*
  console equivalent at all. Deliberately never ported because it is broker-touching, and it belongs
  with the settings editor in a later phase that revisits console's "read-only, never writes
  credentials" guardrail. `liveops.py` and `reconcile.py` both survive; only their card is gone.
- Active-alerts callout and notify-channels line; equity 2x-slippage restatement, best/worst-day
  metrics, VIX overlay, data-epoch marker; the live-logs sub-block.
- MEIC per-leg badges, stop-adjustment columns, AI reasoning, explicit date-range inputs, the arm
  scorecard, and stop-policy counterfactuals.
- Flies symbol filter and the voided-rows accounting line.
- GEX WebSocket push (console polls at 15s, the cache cadence).
- ~~Scout's narrative/describe prose~~ — **closed 2026-08-12**; see the scout section below.
- The recorded-earnings screen (scout's `/api/earnings-screens`) has no console equivalent and its
  backing services went with the package. `/api/earnings/upcoming` covers only the upcoming half.

One thing worth pinning: the deleted suite dashboard's log card had a merge bug that took two rounds
to fix (plain-text stamps parsing as undated, and earnings' log resolving to the wrong directory).
Console's `readers/logs.ts` is a separate implementation and does not inherit that test.

## Suite dashboard (was :8787, deleted)

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

## MEIC dashboard (was :5050/:5051, deleted)

| Card | Status |
|---|---|
| Loop status pill (LIVE/IDLE) + IV rank / underlying chips | done |
| Symbol + profile selectors (page-wide scope) | done — threaded through every MEIC read |
| Era scope (defaults to the module's `CURRENT_ERA`, as its analytics do) | done — earlier eras reachable, never mixed in silently |
| Period stats grid | done — net is after fees, matching the calendar beside it and `core.ledgers`. The equity/drawdown and study-arm curves were still GROSS until 2026-08-20; on this ledger that overstated cumulative P&L by $55,964 (36.6% of gross) under a card already titled "Cumulative net" |
| Today's trades / trade table | partial — per-leg put/call status badges, stop-adjustment columns, AI reasoning missing |
| NLV over time | done — badged account-level, since `closing_nlv` is a whole-account balance the page's scope cannot narrow |
| Daily P&L calendar | done |
| Signal breakdowns (delta band, wing, symbol, weekday) | done |
| Win rate by session / Avg P&L by IV-rank band | done |
| Exit reasons / fee drag | done — plus today's per-profile net and fee drag, the day's "which arm made it" the cumulative Performance table cannot answer |
| Regime coverage (with degenerate flags) | done |
| Profile divergence | done (2026-08-20) — how often the arms reached DIFFERENT entry decisions on the same tick, read from `entry_attempts` so refusals count. Reported 100% agreement between control/control-drift/sign on its first run |
| Trade log filters (outcome, exit reason, search) | done — filters and paging both in SQL, so counts describe the scope and not the page; explicit date-range inputs still missing |
| Performance view: profile comparison, risk metrics, equity + underwater, study arms, per-period charts | done. **Missing: arm scorecard (breakeven identity) and stop-policy counterfactuals** |

Every row-listing table in the console is server-paged against a true match count
(`Paged<T>` in `@console/shared`, `readers/paging.ts` on the server). No table stops
at a hidden row cap.

## Flies dashboard (was :5052, deleted) — at parity+

| Card | Status |
|---|---|
| Today tiles | done — every Today card resolves ONE day page-side, so the attempts views and the book can no longer show different sessions side by side |
| Profit forest (fills, floors, settlement marker, x/y controls) | done |
| Session timeline (entry-window→close axis, gaps named, replayed book) | done |
| Decision journal (Gantt + table) | done |
| Positions / book floors | done (reference columns + pills) |
| Arm divergence | done (>80% agreement flagged) |
| History: by arm/mode/window, fee drag, calendar (click→replay), trade log + filters | done |
| Performance: tiles, P&L bars, completion + why-misses, trend, live-vs-paper | done (trend and live-vs-paper are console-only additions) |
| Performance: cumulative curve, drawdown, risk ratios | done (2026-08-20) — from the same `analytics/riskMetrics.ts` MEIC reads, so the two pages cannot disagree about what a Sharpe is. Daily whatever granularity is shown, since these annualize on sessions |
| Symbol filter (page-wide) | done — shown only when the era in scope holds more than one symbol |
| Loop status pill (LIVE/IDLE) | done — reads `fly_iterations`, which advances on a quiet market where the ledger does not |
| Era scope | done — each era readable ALONE (SPX current / XSP / pre-XSP), with counts; pooling is now an explicit "all eras" choice rather than the only way to see an earlier book |
| Voided-rows accounting line | done (2026-08-20) — `/api/flies/voided` + a note on the Performance tab stating the count, the P&L held back and the REASON, so the exclusion is stated rather than inferred from a gap. Currently 25 rows / -$491.52, all the pre-2026-08-07 bwb roll defect |

## GEX dashboard (was :5055, deleted) — at parity+

All cards done and number-verified (walls/zero-gamma definitions matched). Remaining: WebSocket push
(console polls 15s — cache-cadence equivalent); regime-drift intraday chart is a proposed
console-only addition.

The regime-history table carried a hidden `LIMIT 60` until 2026-08-20 while a session records
240–288 rows, so it showed about a fifth of the day and reported no total — a reader could not tell
a quiet session from a truncated one. It pages properly now, through the same `readers/paging.ts`
every other paged endpoint uses.

## Earnings strategy dashboard (static HTML, deleted)

| Card | Status |
|---|---|
| KPI row | done (capital at risk included) |
| Equity panels (cumulative / rolling 4w / rolling 1w / per-week) | done |
| Open positions | done (totals row included) |
| Cross-strategy comparison + sample-progress bars | done |
| Regime coverage heat + rejection histogram | done |
| Per-strategy detail (equity+drawdown, PF pass/fail, Sharpe, max DD, IV crush) | done |
| Footer caveats block | done |
| Paper/live scope | done — the analytics and strategy detail follow a mode toggle; they were pinned to paper, so the live book was unreachable. The trade and review tables still span both books on purpose, and say so per row |

## Scout (was :5057, web app deleted)

Console additions beyond scout: chain delta+OI picker, STO/BTO highlights, ±EM band, scope-gated
real dry-run validation.

### Migration audit, 2026-08-12

Route-by-route against scout's deleted API. Most of `services/` and `analytics/` is already
re-implemented in TypeScript; what is left is narrower than the line count suggests.

| scout route | console | status |
|---|---|---|
| `/api/symbol/{s}/candles` `/levels` | `GET /api/symbol/:symbol` (bars, overlays, levels, trend) | done |
| `/api/symbol/{s}/chain` `/expirations` | `GET /api/chain/:symbol` | done |
| `/api/symbol/{s}/quote` | `market/marketData.ts` | done |
| `/api/symbol/{s}/template` | `services/builderTemplates.ts` | done |
| `/api/symbol/{s}/income-grid` `/suggestions` | `GET /api/builder/{income-grid,suggestions}/:symbol` | done |
| `/api/payoff` | `POST /api/payoff` | done — the payoff engine's own numbers plus every `describe.py` field |
| `/api/screener` | `POST /api/screener/run` | done |
| `/api/order/dry-run`, `/api/staged*` | `/api/orders/stage`, `/api/orders/staged` | done |
| `/api/watchlist` | `/api/watchlist` | done |
| `/api/earnings-upcoming` | `GET /api/earnings/upcoming` | done |
| `/api/symbol/{s}/stats` | folded into the analysis payload (IV, realized vol, IV rank, earnings date) | done |
| `/api/symbol/{s}/analysis` | `GET /api/symbol/:symbol/analysis` | done |
| `/api/symbol/{s}/warnings` | `GET /api/symbol/:symbol/warnings?expiration=` | done |

**Superseded, delete rather than port**: `services/` `cache`, `candle_service`, `chain_service`,
`metrics_service`, `quote_service`, `screener_service`, `staging`, `streamcache`, `session`,
`watchlist`; `analytics/` `levels`, `payoff`, `pop`, `strategies`, `templates`, `trend`.

**Ported, 2026-08-12.** `analytics/describe.py` → `analytics/describe.ts` and
`analytics/narrative.py` → `analytics/narrative.ts`, composed by `services/symbolAnalysis.ts` and
served on the three routes above; the views are `pages/Scout/AnalysisCard.tsx` (symbol page) and
`pages/Scout/StrategyReadout.tsx` (builder). Scout's own `test_describe.py` and `test_narrative.py`
came across as vitest suites — 43 cases — because they are not ordinary unit tests: each replays an
observed reference-platform card and together they are the evidence that justified the formulae.
They passed against the TypeScript on the first run.

Two deliberate differences, both because the console holds different data than scout did:

- **Realized volatility is computed, not read.** Scout's metrics service supplied `hv_30d`; the
  console's cache never stored it. Rather than lose the IV-vs-realized bullet — the sharpest of
  them — it is computed from the daily closes the console already has (annualized stdev of log
  returns), which is the same definition.
- **Ex-dividend warnings degrade to absent.** The metrics cache carries no ex-date, so
  `eventWarnings` is given null and says nothing. Since absence of a warning is a real claim in that
  function, this is a gap to close by caching the field — not by inventing a value.

**Also unresolved**: `calendar_service.py`, `earnings_metrics_service.py` and
`earnings_watchlist_service.py` back scout's `/api/earnings-screens`, which has no console
equivalent. `/api/earnings/upcoming` covers the *upcoming* half; whether the recorded-earnings screen
is wanted at all is a product question, not a porting one.

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
