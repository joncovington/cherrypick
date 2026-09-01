# Dashboard parity checklist

Card-by-card status of the console against the surfaces it replaced (re-audited after the completion
pass, 2026-08-09). "Done" means the same reads are served, not necessarily pixel-identical.

**Those surfaces are gone as of 2026-08-12.** The suite dashboard, the MEIC/flies/GEX dashboards, the
earnings strategy dashboard, and scout's web app were deleted — console is the suite's only read
surface. Recover any of them from the `pre-console-only` tag.

So this file changed jobs. It is no longer a transition checklist against a running original; every
row still marked **missing** below is now simply **console's backlog**, and there is nothing left to
diff against except that tag.

**Re-audited 2026-08-26, and this file had drifted in both directions.** Several rows marked missing
had shipped and the row was never updated (the shared day selector, the ex-dividend warning), which
makes a backlog read as longer than it is. Others were carried as "missing" when the honest answer
was that they are not wanted — a ported column from a deleted dashboard is not a debt just because
the dashboard had it. Those were **struck rather than deferred**, each with the reason, because a
list nobody intends to act on stops being a backlog and starts being noise. What survives below is
what someone would actually build.

The gaps accepted knowingly at deletion time were:

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

**Struck 2026-08-26 — decided against, not deferred.** Each was a row ported forward from a deleted
dashboard rather than something anyone wanted here:

- **Equity 2x-slippage restatement, best/worst-day metrics, and the VIX right-axis overlay** — three
  ways to make one chart busier. Best/worst-day is already legible in the daily calendar beside it,
  and a VIX overlay on a cumulative-P&L axis invites eyeballed causation, which is exactly what the
  regime cuts in `docs/metrics-plan.md` are the disciplined version of. *The data-epoch marker in
  that same row is NOT struck* and stays wanted: it marks declared measurement breaks on a curve
  that must never be read across them, which is a suite-wide rule made visible.
- **MEIC per-leg put/call status badges and stop-adjustment columns** — detail for a live-managed
  book, on a page whose trade table answers what the trade did. Nothing has needed them.
- **MEIC "AI reasoning" column** — struck on principle as well as on demand. It would put model
  prose in a trade table, and this suite keeps every AI-shaped thing outside the packages precisely
  so a narrative can never be mistaken for a recorded fact.
- **Active-alerts callout, notify-channels line, and the live-logs sub-block** — the header already
  carries the status pill, watchdog age, session and findings, and the merged log tail already has
  level filters. A WARN/CRITICAL filter over a filterable log is a filter, not a surface.
- **Refusal history, day replay, rule attribution and the session grid** (the per-arm table below) —
  four unstarted views on top of an attempts payload whose three EXISTING views have barely been
  read against real data. Build one when a question arrives that the arm rail, the timeline and the
  occupancy map cannot answer; the payload is recorded either way, so nothing is lost by waiting.
- **GEX WebSocket push** — the console polls at 15s, which IS the cache cadence. Push would add a
  transport and deliver no extra information.
- ~~Scout's narrative/describe prose~~ — **closed 2026-08-12**; see the scout section below.
- The recorded-earnings screen (scout's `/api/earnings-screens`) has no console equivalent and its
  backing services went with the package. `/api/earnings/upcoming` covers only the upcoming half.

One thing worth pinning: the deleted suite dashboard's log card had a merge bug that took two rounds
to fix (plain-text stamps parsing as undated, and earnings' log resolving to the wrong directory).
Console's `readers/logs.ts` is a separate implementation and does not inherit that test.

## Suite dashboard (was :8787, deleted)

| Card | Status |
|---|---|
| Header status pill / watchdog age / session / findings | done (Overview); active-alerts callout and notify-channels line **struck 2026-08-26** — the merged log tail already filters by level |
| Suite stats strip (net, trades, win %, avg) | done |
| Suite equity — cumulative net P&L + module lines | done. 2x-slippage restatement, best/worst-day metrics and the VIX overlay **struck 2026-08-26** (see above). Still wanted: the data-epoch marker, so a curve says where its measurement breaks are |
| Champions & challengers (calibrate) | **REMOVED 2026-08-20** — the page, the `/api/calibration` route, the champion column on the System card and `core.profiles.recommend_champion` all went together. Judging whether an arm earned anything belongs to `packages/advisor`'s experiments now, so the suite has one mechanism rather than two answering that on different evidence and thresholds. `cherrypick calibrate` still reports the per-tag reading |
| System: modules, services, config summary, halt flag | done (live doctor checks and OS task registry not ported — they need the orchestrator's own subprocess) |
| Live ops: halt flag, per-module live gates, reconcile panel | **missing** — broker-touching; port deliberately |
| End-of-day card + md report links/rendering | done (in-page markdown rendering, allowlisted files) |
| Recent logs (merged tail, level filters) | done; live-logs sub-block **struck 2026-08-26** — a filter over a filterable log |

## MEIC dashboard (was :5050/:5051, deleted)

| Card | Status |
|---|---|
| Loop status pill (LIVE/IDLE) + IV rank / underlying chips | done |
| Symbol + profile selectors (page-wide scope) | done — threaded through every MEIC read |
| Era scope (defaults to the module's `CURRENT_ERA`, as its analytics do) | done — earlier eras reachable, never mixed in silently |
| Period stats grid | done — net is after fees, matching the calendar beside it and `core.ledgers`. The equity/drawdown and study-arm curves were still GROSS until 2026-08-20; on this ledger that overstated cumulative P&L by $55,964 (36.6% of gross) under a card already titled "Cumulative net" |
| Today's trades / trade table | done. Per-leg badges, stop-adjustment columns and an AI-reasoning column all **struck 2026-08-26** — the last on principle: model prose does not belong in a trade table |
| NLV over time | done — badged account-level, since `closing_nlv` is a whole-account balance the page's scope cannot narrow |
| Daily P&L calendar | done |
| Signal breakdowns (delta band, wing, symbol, weekday) | done |
| Win rate by session / Avg P&L by IV-rank band | done |
| Exit reasons / fee drag | done — plus today's per-profile net and fee drag, the day's "which arm made it" the cumulative Performance table cannot answer |
| Regime coverage (with degenerate flags) | done |
| Profile divergence | done (2026-08-20) — how often the arms reached DIFFERENT entry decisions on the same tick, read from `entry_attempts` so refusals count. Reported 100% agreement between control/control-drift/sign on its first run |
| Trade log filters (outcome, exit reason, search) | done — filters and paging both in SQL, so counts describe the scope and not the page. Date-range inputs are **not applicable here**: this log lives under the Today tab and is scoped to the one session the tab's own day selector names, so a range would contradict it. The range landed on flies' multi-day history log instead (2026-08-26) |
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

All cards done and number-verified (walls/zero-gamma definitions matched). WebSocket push **struck
2026-08-26**: the console polls at 15s, which IS the cache cadence, so push would add a transport
and deliver nothing extra. The regime-drift intraday chart remains a proposed console-only addition.

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

## Scout / research — RETIRED 2026-08-31

**The whole section is gone: watchlist, screener, builder, payoff, staged orders, and the symbol
page.** Scout's own web app was deleted on 2026-08-12 and its surfaces were ported here; that port
is now retired in turn, so the migration audit this section used to carry has nothing left to
describe. Recover any of it from the `pre-console-only` tag or from the commits of 2026-08-31.

What that means for the rest of this file: the console is now a **read surface for the trading
modules only**. There is no research surface to be at parity with, and no product question left
about the recorded-earnings screen — the answer became "no" by retiring everything around it.

Three things deliberately survived the teardown, and each is worth not re-deleting by accident:

- **`analytics/payoff.ts`** — `readers/meic.ts` uses `payoffAt` for the profit-forest curves. Three
  of its four consumers went with the section; deleting it by reading the directory rather than the
  imports would have left MEIC's forest silently empty rather than failing a build.
  `test/payoff-survives.test.ts` pins that.
- **`analytics/describe.ts` and `analytics/narrative.ts`** with their 43 tests — kept as preserved
  evidence. Each case replays an observed reference-platform card, and together they are what
  justified those formulae. Nothing calls them now; re-deriving them would mean re-observing a
  platform this suite no longer runs against.
- **`market/marketData.ts` and `ws/hub.ts`** — the Overview's live quotes ride `/ws`, which has
  nothing to do with research.

And one guard got STRONGER rather than going away: `dry-run-only.test.ts` now pins `postOrderDryRun`
to exactly one file (the scope probe) instead of two, because order staging was the console's only
path that touched an order at all. It asserts nothing has grown back.

The console.db tables the section owned (`tt_watchlists`, `tt_metrics`, `tt_public_pins`,
`symbol_blacklist`, `chain_eod`, `chain_eod_meta`) are **kept for one cycle** — nothing reads or
writes them, but the DDL and rows stay so the retirement is reversible without a restore. Drop them
in a follow-up. `~/.cherrypick/data/scout/` was renamed `scout.retired-2026-08-31`, matching how
`scout.json.retired-2026-08-17` was handled.

## Per-arm portfolios (2026-08-11; re-audited 2026-08-26)

Both modules now record an **entry-attempts ledger** — one row per evaluated entry opportunity per
arm — and MEIC has a **profit forest** to match flies'. The server side is complete, and **the three
views that read it are built on both pages**; the intro here said otherwise long after they shipped.
The four remaining rows are struck rather than pending — see the table.

| Surface | Endpoint | Status |
|---|---|---|
| Arm rail — per-arm cadence countdown, entries today, refusal mix, what is holding it | `/api/{meic,flies}/attempts` | done, both pages |
| Attempt timeline — one lane per arm, every evaluated opportunity by outcome | `/api/{meic,flies}/attempts` | done, both pages |
| Strike occupancy — longs vs shorts per arm, refusing strikes ringed | `/api/{meic,flies}/occupancy` | done, both pages |
| MEIC profit forest — per-profile curves, each condor faint behind the aggregate, stop-released strikes | `GET /api/meic/forest` | done |
| Refusal history (per session, stacked by reason) | — | **struck 2026-08-26** — build when the arm rail cannot answer it |
| Day replay (scrub the timeline, occupancy rebuilding) | — | **struck 2026-08-26** — same; the substrate is recorded either way |
| Rule attribution / entry-slot yield | — | **struck 2026-08-26** — same |
| Session grid (sessions × arms) | — | **struck 2026-08-26** — same |

**~~One known rough edge~~ — closed.** The attempts views defaulted to the latest day in the
*attempts* ledger while the forest and occupancy views defaulted to the latest day with *positions*,
so on a morning before anything filled the cards could show yesterday's book beside today's
attempts — each correctly labelled and contradictory side by side. Both pages now carry ONE day
selector governing the arm rail, attempt timeline, occupancy map and forest together
(`MeicPage.tsx`, `FliesPage.tsx`), so they cannot describe different days. The multi-day tabs
deliberately drop it: pinning one session would empty every trend on them.

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
