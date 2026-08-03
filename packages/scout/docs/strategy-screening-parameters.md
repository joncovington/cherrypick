# Strategy screening parameters — a narrowing reference

> _Part of the **cherrypick-scout** package — [package README](../README.md)._

A reference for turning a broad symbol list (a tastytrade public watchlist like `High Options
Volume` or `tasty IVR`, an earnings calendar, a sector list) into a short list worth deeper
analysis. Two kinds of numbers appear side by side, and the difference matters:

- **Suite-tested** — parameters this monorepo already runs (scout's screener config, the earnings
  module's screening gates, MEIC/flies defaults). These have at least been exercised against live
  data, and the earnings gates have documented live-verification history
  (`../../earnings/docs/screening-criteria.md`).
- **Practitioner-standard** — widely used community/industry defaults (largely the tastytrade
  research mechanics, which dominate retail premium-selling practice). Reasonable starting points,
  not validated by this suite.

Nothing here is advice or a backtested claim; it's a catalog of the conventional knobs and their
conventional settings, so a narrowing pass can be explicit about which convention it's applying.

## 1. Universe gates (apply before any strategy-specific filter)

Cheap, strategy-agnostic filters that cut a raw list fast. Run these first; every strategy below
assumes its candidates already cleared them.

| Gate | Suite-tested value (earnings module) | Notes |
|---|---|---|
| Share price | ≥ $10 | Sub-$10 names: tick-size-dominated spreads, unreliable IV |
| Combined open interest | ≥ 2,000 contracts (front month, calls+puts) | Below this, multi-leg fills won't resemble mid |
| Bid/ask spread at ATM | ≤ 15% of mid | Wide spreads erode the edge being screened for |
| 30-day average share volume | ≥ 1.5M strict / 1M near-miss | |
| Market cap | ≥ $2B strict / $1B near-miss | |
| Combined front-month option volume | ≥ 500 strict / 200 near-miss | |
| Weekly options exist | required (expiration gap ≤ 10 days somewhere in chain) | Monthly-only names fail most short-DTE strategies by construction |
| Liquidity rating (tastytrade metrics) | ≥ 3 of 4 (scout screener default) | The one-number shortcut for most of the above |

## 2. Volatility regime selectors

The single biggest branch point: **is IV rich or cheap?** It decides which strategy family is even
applicable, so it's the first strategy-aware narrowing cut.

| Signal | Threshold | Selects for |
|---|---|---|
| IV rank (IVR) | ≥ 30 (practitioner standard for premium selling; scout's default gate is a looser ≥ 25) | Credit strategies: spreads, condors, strangles, flies |
| IV rank | ≤ 30, ideally ≤ 20 | Debit/long-vega strategies: calendars, diagonals, debit spreads |
| IV/RV ratio | ≥ 1.25 strict / 1.00 near-miss (suite-tested, earnings) | IV actually overpriced vs realized — the "is there an edge" check behind the IVR number |
| Term structure (event names) | front-vs-back ATM IV ≤ −0.004, i.e. front richer (suite-tested, earnings) | Event-driven IV-crush plays specifically |

The `tasty IVR` public watchlist is pre-filtered on the first row; `High Options Volume` is
pre-filtered on section 1's liquidity gates. Intersecting the two is a legitimate two-line
narrowing pass before any per-symbol computation.

## 3. Per-strategy parameters

### Defined-risk credit (put/call credit spread, iron condor, iron fly)

| Parameter | Practitioner standard | Suite-tested |
|---|---|---|
| DTE at entry | 45 (research-favored); 30–45 acceptable band | scout screener: 30–45, prefer monthly |
| Short strike | 16–20Δ for condors/strangles (~1σ); 25–30Δ for single spreads | scout: 0.30Δ target (proxied by expected move — no live greeks source); earnings condor: short strikes at the expected-move boundaries |
| Wing width | $5–$20, or credit ≈ ⅓ of width as the fairness check | scout: 5% of short strike; earnings iron fly: 2.5×/3.0×/3.5× straddle credit, banded by that name's own IV/RV |
| Entry IVR | ≥ 30 | scout gate: ≥ 25 |
| Profit target | 50% of credit (the heavily-researched number) | earnings credit strategies: 50% |
| Stop | 1.5×–2× credit received | earnings: 1.5× credit |
| Time exit | manage/roll at 21 DTE regardless | n/a (earnings positions are overnight-only) |

### Short put (cash-secured) / covered call / wheel

| Parameter | Practitioner standard |
|---|---|
| DTE at entry | 30–45 |
| Delta | 0.20–0.30 (≈70–80% OTM probability) |
| Profit target | 50–75% of premium; typically hit near 21–25 DTE |
| Roll trigger | strike breached, or 21 DTE |
| Underlying selection | names you'd hold at the strike — assignment is a feature, not a failure |
| Expected yield | 1–3% per 30–45-day cycle (sanity check on premium/collateral before entering) |

### Short strangle / straddle (undefined risk — **not implemented anywhere in this suite, by policy**)

Listed only because the parameters inform their defined-risk cousins: 16Δ shorts (~1σ), 45 DTE,
IVR ≥ 30, manage at 50% / 21 DTE. The earnings module deliberately removed naked strategies
(unmonitored overnight gap risk); scout stages defined-risk tickets only.

### Calendar / double calendar / diagonal

| Parameter | Practitioner standard | Suite-tested (earnings ATM/double calendar) |
|---|---|---|
| Entry regime | low IVR (≤ 30), or an event term-structure inversion | term structure ≤ −0.004 (event-driven variant) |
| Front leg | 7–10 days (event) or ~30 days (non-event) | front expiration ≤ 9 days from entry |
| Back leg | 30–60 days beyond front | next monthly beyond the event |
| Profit target | 25–30% of debit | 30% (ATM calendar), 25% (double calendar) |
| Stop | ~100% of debit (debit is the max loss anyway) | 100%, plus a 5-day time stop |
| Per-side delta stop (double calendar) | — | 0.45 abs delta on a side |

### Broken wing butterfly / skewed structures

Suite-tested (earnings BWB): body at the 25Δ risk-reversal-favored side, near wing scaled by
IV/RV band (2.5×/3.0×/3.5× the body premium), far wing 2.5× the near wing, entered at net credit
or breakeven only (reject if it prices as a debit); 25% profit target, 40% stop. The
25Δ risk-reversal skew read (`scanner.select_side()`) is the same signal scout's screener surfaces
as its price-based "skew edge" column.

### Event / earnings IV-crush plays

The most fully suite-tested set — see `../../earnings/docs/screening-criteria.md` for the
authoritative version with live-verification history. Compressed: universe gates from section 1,
front expiration ≤ 9 days, term structure ≤ −0.004, ATM |Δ| ≤ 0.57 (strike-grid sanity),
expected move ≥ $0.90, IV/RV ≥ 1.25, historical implied-beat-realized winrate ≥ 50% (sample-size
shrunk — a 100% winrate on 2 quarters ranks below 60% on 6), entered before the close, closed the
next morning unconditionally.

### 0DTE structures (MEIC-style, for completeness)

Same-day expiration, short strikes placed by premium target rather than delta, entries staggered
across the session, per-side stops near total-credit received, VIX-banded sizing. Parameters live
in `../../meic/` and are deliberately not duplicated here — 0DTE is out of scout's 30–45 DTE
screening scope.

## 4. A worked narrowing funnel

The intended use of this doc, end to end:

1. **Start broad** — e.g. the `High Options Volume` public watchlist (~200 names, liquidity
   pre-filtered by tastytrade).
2. **Regime cut** — one batched `get_market_metrics` call: keep IVR ≥ 30 for credit candidates
   (or ≤ 25 into a calendar shortlist). Zero per-symbol chain fetches yet.
3. **Quality cut** — liquidity rating ≥ 3, price ≥ $10, plus any section-1 gate the metrics call
   already answered. Typically leaves a few dozen names.
4. **Strategy fit** — only now fetch chains for survivors and apply the per-strategy table
   (short-strike delta/expected-move placement, credit ≥ ⅓ width for condors, etc.). This is
   exactly scout's five-step screener flow; the tables above are the defensible defaults for its
   `screener.*` config block.
5. **Rank, don't just filter** — composite score (return-on-risk × POP × IVR × liquidity in
   scout; |term structure| × IV/RV × shrunk winrate in earnings). A ranked short list of 5–15
   names is the target output, not a binary pass list.

## Sources

Suite-internal: [`earnings/docs/screening-criteria.md`](../../earnings/docs/screening-criteria.md),
[`earnings/docs/05-strategies.md`](../../earnings/docs/05-strategies.md), scout's
`config.example.json`, MEIC/flies package docs. External (practitioner-standard numbers):
tastytrade-derived mechanics as summarized by
[Days to Expiry's DTE comparison](https://www.daystoexpiry.com/blog/best-dte-for-credit-spreads-a-data-driven-comparison-of-30-45-and-60-day-trades),
[projectoption's iron condor guide](https://projectoption.com/learn/iron-condor-options-strategy),
[Days to Expiry's iron condor playbook](https://www.daystoexpiry.com/blog/iron-condor-strategy-entry-exit-playbook),
[the wheel-strategy delta guide](https://wheelstrategy.substack.com/p/cash-secured-put-delta), and
[Days to Expiry's wheel DTE playbook](https://www.daystoexpiry.com/blog/wheel-options-trading-strategy-complete-dte-playbook).
Retrieved 2026-08; conventions drift, re-verify before treating any single number as load-bearing.
