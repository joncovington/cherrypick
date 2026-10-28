# Strategy sources — where the logic is documented, and where it came from

**What this covers:** for the four strategy engines a fresh install runs — MEIC, earnings, flies and
bwb — this records (a) where each strategy's logic is documented inside the repo, (b) the git commits
that introduced it, (c) every external source the repo's own docs actually cite, and (d) the external
lineage research done on 2026-10-12 — the published strategies, their authors, the GitHub ancestor and
the video genre — which the repo docs only half-record. The other modules' named sources are listed at
the end, because they follow the same pattern.

External links were verified live on 2026-10-12 (pages fetched and read, not assumed from titles).
The flies-side video survey is representative rather than exhaustive; everything stated about a
video was read off its page.

---

## MEIC — multiple entry iron condors on 0DTE SPX

### Where the logic is documented

| File | What it holds |
|---|---|
| [`packages/meic/CLAUDE.md`](../packages/meic/CLAUDE.md), [`docs/agent-loop.md`](../packages/meic/docs/agent-loop.md) and [`docs/reference.md`](../packages/meic/docs/reference.md) | The operating contract: the guardrails (CLAUDE.md), the agent loop step by step (agent-loop.md), the full config table with every key's meaning and default (reference.md), and the pinned decisions (e.g. the stop basis is fixed in code, never a config choice). |
| [`packages/meic/docs/strategy.md`](../packages/meic/docs/strategy.md) (188 lines) | **The strategy document.** IC structure, delta targeting with the VIX-banded scale, session classification, OTM guardrails, the entry price strategies against CBOE Complex Order Book constraints, stop management with the tightening triggers, post-stop evaluation, and the three exit paths (no profit target; cash-settled symbols expire, physically settled force-closed). |
| [`packages/meic/GATES.md`](../packages/meic/GATES.md) (416 lines) | The complete entry-gate catalog: 33 gates in 12 sections, in evaluation order, each with trigger, effect, config key, rationale and IC/ORB impact. |
| [`packages/meic/docs/paper-experiments.md`](../packages/meic/docs/paper-experiments.md) | The study record: the breakeven identity the module sits against, the 2026-08 four-stream forward test, and the four derived stop policies (`stop-none`, `stop-0.75-net`, `stop-2.0-side`, `strike-touch`) computed read-side from recorded paths. |
| [`packages/meic/docs/history.md`](../packages/meic/docs/history.md) | The dated narrative behind each rule, with the numbers (the 2026-06-30 fee floor's origin, the 2026-07-01 streamer stall that produced `stale_warning`, …). |
| [`docs/strategy-engines.md`](strategy-engines.md) | The suite-level summary of what MEIC trades and how it gates. |

### The strategy in brief

Four legs — a put spread below the market, a call spread above — entered repeatedly through the 0DTE
session rather than once. Short strikes start from a delta target (0.18 in the shipped example, the
fallback of a VIX-banded scale that tightens to 0.10 in crisis), guarded by minimum OTM distances and
a credit floor net of estimated fees. Entry price strategies: `natural_bid`, `mid`, `ioc_step`,
`day_improve`, or `auto`. There is **no profit target**: exits are the per-side software stops
(Tastytrade has no exchange multi-leg stop), checked on the loop cadence, with the remaining spread
re-evaluated after any stop; cash-settled symbols (SPX/XSP/NDX/RUT) are left to expire, physically
settled ones are closed before the bell.

### Origin commits

- **`c8e0fe73` (2026-06-18) — "Initial commit — MEICAgent MEIC trading agent."** The whole original
  repo arrived at once: a 349-line `CLAUDE.md` operating contract, `config.json` (XSP, delta 0.15,
  `stop_trigger_ratio: 0.90`, wing widths 1/2/3/5, at most four ICs a day, a 5-minute loop),
  `db.py`, notifications, and the Claude Code slash skills that drove it as an AI agent.
- **`08805458` (2026-06-18) — "Add README with setup instructions and strategy overview."** The first
  prose statement of the strategy: multiple ICs through the day; wing width evaluated per entry;
  stops as DAY stop-limits sized to about break-even on the **full IC credit**; post-stop re-evaluation;
  EOD expiry for cash-settled symbols; ambiguous signals resolved to the capital-protective default.

### External references the repo cites

- **The Chambless MEIC convention** — the per-side stop keyed to the *whole IC's* credit rather than
  that side's own credit is called "the published Chambless MEIC convention" in
  [`docs/strategy.md`](../packages/meic/docs/strategy.md) and
  [`docs/paper-experiments.md`](../packages/meic/docs/paper-experiments.md), and pinned in
  [`docs/reference.md`](../packages/meic/docs/reference.md) (`per_side_stop_management`: "the Chambless convention …
  fixed in code"). It is designed so one side stopping scratches the trade — the other side's
  remaining credit roughly offsets the loss. `stop_trigger_ratio: 0.95` is identified in the repo as
  the documented **"MEIC+"** variant (the stop $0.10 below exact breakeven, so a scratch trends
  slightly positive); the original commit shipped 0.90. Who Chambless is: see the lineage section below.
- **Option Alpha's SPY/0DTE backtests** — cited once, as the external literature predicting that
  holding to settlement beats every stop level tested; it is what motivates the derived `stop-none`
  stream, whose scorecard therefore carries a held-through-ITM count and a worst-loss line.
- **0DTESPX.com** — a third-party historical 0DTE data source, fully researched in
  [`docs/0dtespx-api.md`](../packages/meic/docs/0dtespx-api.md) and then **rejected on its own
  terms**: the local-cache replay design violates the site's acceptable-use clause (walking the
  archive to build a local dataset), so `paper_replay.py` must not run against it. The compliant
  alternatives (their server-side practice sessions and strategy backtester) were judged not
  drop-in for this suite's own engine; the compliant path needing no third party is forward paper
  on tastytrade, which is what the module does.

The breakeven identity and the derived stop policies are the suite's own work — nothing external is
cited for them. Same for the delta scale and every gate value.

---

## BWB — daily-laddered SPX put broken-wing butterflies

### Where the logic is documented

| File | What it holds |
|---|---|
| [`packages/bwb/README.md`](../packages/bwb/README.md) | The structure, the four books and the live pilot's rulebook in plain terms — start here. |
| [`packages/bwb/CLAUDE.md`](../packages/bwb/CLAUDE.md) | The full experiment design: the base structure strike by strike, the four triggers in one table, the add-on lifecycle, the honesty rules (the trigger-tick substrate that makes a read-side threshold replay possible, `addon_missed`, fire counts as the honest sample), the live-path contract. |
| [`packages/bwb/config.example.json`](../packages/bwb/config.example.json) | **"The example's `_note` keys are the design document"** — every parameter's meaning, its default and the reason it cannot be otherwise (why `credit_floor` is a declared zero, why `delta` and `bounce` differ by exactly one condition, why `flip_buffer` is 1.001). |
| [`packages/bwb/docs/history.md`](../packages/bwb/docs/history.md) | The lineage section and the incident narratives: the trigger ticks that measured nothing for four sessions, the add-on that could never price (SPX vs SPXW roots), the missed-add-on rule born of the 2026-10-08 power outage. |
| [`docs/strategy-engines.md`](strategy-engines.md) | The suite-level summary. |

### The strategy in brief

One net-credit SPX put broken-wing butterfly every session at one fixed tick (default 10:00 ET):
body (short ×2) at the nearest strike to `spot − expected_move` off the target expiration's ATM
straddle mids, near long wing one $5 increment above, far long wing two increments ($10) below. The
wider far wing is what makes it broken: the credit is kept everywhere above the near wing, the loss
beyond the far wing is bounded to the near-spread width (5 × multiplier, less the credit), and the
tent's peak sits at the body. The next PM-settled weekly Friday strictly after today (a 1–7 DTE
ladder), held to expiry, settled on an official print or not at all; a new one every session, so
~5–7 ride concurrently per book. The experiment: four books trade the identical base, and the only
variable is whether/when a reversal-triggered put credit spread add-on — bracketing the far wing,
turning the fly into a 1-3-2 — fires: `control` never, `delta` on a raw 50Δ touch, `bounce` on a
confirmed pullback off a peak, `flip` on a gamma-flip reclaim. Since 2026-09-18 one arm can trade
live, armed per day by `/live-bwb-start`, under a worst-case margin cap with a mark-drawdown breaker
that blocks entries only.

### Origin commits — an in-suite lineage, not an external one

- **2026-08-22** — a "1-3-2 put condor" is floated as a fourth book for `packages/ratios`.
- **`22ec497d` (2026-08-23) — "add bwb and curve modules."** bwb absorbs and supersedes the float.
  Per [`docs/history.md`](../packages/bwb/docs/history.md)'s lineage section, what survived of the
  condor is exactly three things: the 1-3-2 shape, the add-on as a plain matched vertical (which
  keeps the combined position defined-risk by construction), and the reversal-not-falling-knife entry
  philosophy. It shipped with the four books already the experiment's design; `ratios` kept jade
  lizard / backratio / LT112.
- **`95208d52`** adds the live plumbing (credentials, broker seam, a separate live ledger); the
  narrow live path lands 2026-09-18 and is journaled as its own measurement boundary.

### The external genre it belongs to — tastylive's

No external source is named anywhere in the module — the config's one `_source_note` is about a
stream-cache path, not a lineage. But the structure is unmistakably the **tastylive** (tastytrade's
media arm) SPX broken-wing butterfly, one of that house's flagship income trades, and the genre is
well documented on YouTube:

- **tastylive's own definition** ([tastylive.com/concepts-strategies/broken-wing-butterfly](https://www.tastylive.com/concepts-strategies/broken-wing-butterfly)):
  "a net credit, high probability trade" in which "the debit spread portion of the trade is more
  narrow than the credit spread portion of the trade, so that we collect a credit up front" — bwb's
  narrow-near/wide-far shape is that sentence, with the far wing on SPX's expected move.
- **Tom Preston's "hidden spread"** — [A 30-Year Veteran Shows the Hidden Spread Inside Every Broken
  Wing Butterfly](https://www.youtube.com/watch?v=UoII79Y1qtA) (tastylive, 2026-05-24, ~15K views):
  every BWB contains an embedded short vertical that drives the risk, the reward and the theta; see
  it and you manage the trade by buying that spread back for less than the credit collected. A
  striking structural resonance with bwb's add-on — which re-prices a short vertical beside the fly
  on a trigger — though the repo cites it nowhere, and the add-on fires on its own reversal rules,
  never as management.
- **The tastylive canon**: Tony Battista's favourite SPX income trade (["The Broken Wing Butterfly:
  Tony Battista's Favorite SPX Income Strategy"](https://www.youtube.com/watch?v=G9K6w8xM6tY),
  reposted 2026-05-22) and the long-running Option Trades Today segments, e.g. [Broken Wing
  Butterfly in SPX](https://www.youtube.com/watch?v=UKy0Q06FB0M) (2023-09-21).
- **0DTE/1DTE practitioners**: ["Making $1,000 Overnight, Trading Options! Broken Wing Butterfly on
  SPX (1 DTE Strategy)"](https://www.youtube.com/watch?v=Q9w4RpKHh8Q) (LoganLaj, 2021-05-18, ~57K
  views); [a live 0DTE SPX call BWB](https://www.youtube.com/watch?v=qyxCNvt42rw) (Trading Litt,
  2023-12-30); ["REFINED 0DTE Broken Wing Butterfly SPX strategy!"](https://www.youtube.com/watch?v=X3fS-8euWq8)
  (Optionsmith, 2025-11-04 — the same channel that surfaced in flies' "profit forest" genre below);
  ["This Broken Wing Butterfly Strategy Works!"](https://www.youtube.com/watch?v=RW3JAph8Tag)
  (Stock Market Options Trading, 2021-07-11, ~79K views); [How to Trade the SPX Broken Wing
  Butterfly](https://www.youtube.com/watch?v=8-V5vCQM2Ao) (Al Losada, 2022-05-06); ["Instead Of
  Credit Spreads, Try The Broken Wing Butterfly!"](https://www.youtube.com/watch?v=22bMa4t0cFM)
  (dtoptions, 2026-09-07, ~19K views). Data-Driven Options keeps the written counterpart
  ([broken-wing put condor](https://datadrivenoptions.com/strategies-for-option-trading/favorite-strategies/broken-wing-put-condor/)).

What the module does **not** take from the genre: tastylive's canonical trade carries management
rules — their own FAQ closes a partially-ITM fly rather than hold it through expiration — while bwb
ladders a new fly **daily**, holds **everything to expiry on every book**, and its one variation,
when a reversal-triggered 1-3-2 add-on may fire, has no external analogue found anywhere in the
genre. The experiment is the suite's; the structure is the genre's.

---

## Earnings — overnight defined-risk plays on the IV crush

### Where the logic is documented

| File | What it holds |
|---|---|
| [`packages/earnings/README.md`](../packages/earnings/README.md) | Plain-language start: what the module does, paper vs live, the Dolt dependency. |
| [`packages/earnings/CLAUDE.md`](../packages/earnings/CLAUDE.md) | The operating contract: the six strategies, the engine-vs-strategy split, the unattended paper loop's clock-derived phases, the advised-twin mechanism. |
| [`packages/earnings/docs/screening-criteria.md`](../packages/earnings/docs/screening-criteria.md) | **The declared source of truth for the screen**: Layer 0 (which names reach the screen at all — the AMC-never-BMO assumption for Dolt's mostly-NULL `when` column), the seven hard filters, the five soft criteria with their three strictness levels. |
| [`packages/earnings/docs/05-strategies.md`](../packages/earnings/docs/05-strategies.md) | Each strategy's structure, entry gates and exits with worked examples — and, unusually, a house-rules admission wherever no published study exists. |
| [`packages/earnings/docs/strategy-optimization.md`](../packages/earnings/docs/strategy-optimization.md) | Research-backed optimization hypotheses, each with the literature behind it and a **Sources** section — the module's own literature review. |
| [`packages/earnings/docs/strategy-testing-plan.md`](../packages/earnings/docs/strategy-testing-plan.md) | The forward test: strat_test force-samples all six strategies nightly, so every hypothesis gets a sample. |
| [`packages/earnings/docs/10-exits.md`](../packages/earnings/docs/10-exits.md) | The full exit ladder: expiry close, pin guard, per-strategy target/stop, session cap, the PEAD gate, and the execution gates that hold a decided close. |

### The strategy in brief

Open a defined-risk position the afternoon before an earnings report, capture the overnight IV
crush, manage it after the announcement. The screen demands front-month IV inflated over back-month
(term structure ≤ −0.004), an expected move priced above the name's realised history (IV/RV ≥ 1.25,
the historical-winrate gate), and liquidity; `rank_strategies.py` then picks whichever of six
structures — `iron_fly`, `double_calendar`, `iron_condor`, `atm_calendar`,
`directional_credit_spread`, `broken_wing_butterfly` — scores highest on live data. Undefined-risk
structures were deliberately excluded: a naked short on a single-name gap can blow out arbitrarily
overnight with nobody watching. The unattended paper loop opens the strat_test books — every
strategy clearing the screen on every viable name, in per-strategy books — so each structure
accumulates a sample; a loser closes the first morning, a winner may carry three sessions, expired
legs settle at intrinsic against the local Dolt bars.

### Origin commits (all one day, 2026-07-06)

- **`e487db35` — "Scaffold EarningsFlyAgent project."** An iron-fly earnings agent explicitly
  mirroring MEICAgent's CLAUDE.md pattern; `tt.py`, `db.py` and the scanner bridge are stubs.
- **`5b569d64` — "Implement tt.py: real tastytrade OAuth2 CLI, adapted from MEICAgent."** The
  keyring service is still named `earningsflyagent` so credentials never collide with MEIC's.
- **`53a93271` — "Define concrete earnings-candidate screening criteria and tiering."** The
  screen's first statement.
- **`4408fa45` — "Replace external scanner dependency with an internal Tier-A scanner."** The
  provenance commit: until then, candidates came from an **external tool**, `EarningsEdgeDetection
  --iron-fly`, tiered TIER 1 / TIER 2 / NEAR MISS, with only TIER 1 eligible for automatic entry.
  This commit reimplemented the cheap half of that scanner as pure functions over tastytrade
  chains, keeping the tier language.

### The external lineage

**EarningsEdgeDetection — the screen's ancestor, named in the repo, verified live.** The tier bar
in [`symbol_watch.py`](../packages/earnings/src/cherrypick/earnings/symbol_watch.py) is commented
"EarningsEdgeDetection-derived (github.com/Jayesh-Chhabra/EarningsEdgeDetection)", and
[`docs/screening-criteria.md`](../packages/earnings/docs/screening-criteria.md) says the filter
order "matches EarningsEdgeDetection's own performance-optimized ordering" and the term-structure
math "mirrors" theirs. Upstream ([Jayesh-Chhabra/EarningsEdgeDetection](https://github.com/Jayesh-Chhabra/EarningsEdgeDetection),
created 2025-03-19, Python, 81 stars): a scanner that filters earnings names "into only the most
favorable picks to short volatility on" — price ≥ $10, options expiration ≤ 9 days, combined OI
≥ 2,000, term structure ≤ −0.004, ATM |delta| ≤ 0.57, expected move ≥ $0.90; soft criteria of
volume ≥ 1.5M, winrate ≥ 50% (originally Market Chameleon's overestimate percentage), IV/RV ≥ 1.25;
three tiers (recommended / near miss / fail); and a `--iron-fly` strike calculator (ATM ~50Δ
shorts, wings at 3× credit). **The suite's thresholds are those numbers carried over.** The
implementation replaced upstream's scrape-based pipeline (Investing.com / DoltHub / Finnhub,
ChromeDriver, Market Chameleon) with pure functions over tastytrade chains and the local Dolt
clones, then extended the screen where upstream had nothing: the weekly-options cadence check
(filter 7b), the AMC-never-BMO timing rule for the ~47%-NULL `when` column (Layer 0), and
move-dispersion. Two real bugs were found during the reimplementation, including a term-structure
sign-convention error that initially rejected exactly the candidates the screen exists to find.

**The written sources the module itself cites.** [`docs/strategy-optimization.md`](../packages/earnings/docs/strategy-optimization.md)
closes with a Sources section backing each hypothesis: iron condor/fly earnings mechanics and the
50–75% earnings profit target (apexvol.com, optionsamurai.com, projectoption.com, schwab.com),
calendar term-structure and DTE spacing (steadyoptions.com, optionstradingiq.com, MenthorQ's
[ACES earnings-calendar guide](https://menthorq.com/guide/aces-earnings-calendar-strategy/)), skew
and broken-wing structures (alpaca.markets, datadrivenoptions.com). Re-verified live 2026-10-12,
except the projectoption URL, which has moved (the live page is
[projectoption.com/iron-condor-options-strategy](https://projectoption.com/iron-condor-options-strategy)).
The document's own discipline is the point: "**hypotheses to validate, not changes to apply** — do
not blind-tune to the literature." Every hypothesis goes through the strat_test forward test
against cost-adjusted expectancy, one parameter at a time.

**tastylive, cited for a negative.** The earnings BWB's exits (25% of credit, 2.0× stop) are
labelled **house rules** in [`05-strategies.md`](../packages/earnings/docs/05-strategies.md) and
[`10-exits.md`](../packages/earnings/docs/10-exits.md): "tastylive has traded BWBs into earnings
but published no management study for them."

**The PEAD gate — academic literature carried without its citation.** The exit ladder's PEAD gate
(a position at or below breakeven closes the first morning; carrying a winner is worth roughly
+1.4pp as the residual crush drains over three to five sessions; carrying a loser fights
drift "which continues rather than reverting") is the classic **post-earnings-announcement drift**:
Ball & Brown (1968), "An Empirical Evaluation of Accounting Income Numbers", *Journal of Accounting
Research* (DOI [10.2307/2490232](https://doi.org/10.2307/2490232)), and Bernard & Thomas (1989),
"Post-Earnings-Announcement Drift: Delayed Price Response or Risk Premium?", *Journal of Accounting
Research* 27 (DOI [10.2307/2491062](https://doi.org/10.2307/2491062)). The repo names the effect
and never the papers; recorded here so the lineage is complete.

**The closest video analogue — and it is not the lineage.** One YouTube video matches this
module's whole method more closely than anything found for the other modules: ["This Option
Strategy Turned $10k Into $1 Million In One Year"](https://www.youtube.com/watch?v=oW6MHjzxHpU)
(Volatility Vibes, 2025-02-12, ~778K views). Its chapter list is, in order: trading edge, the
strategy, structuring the trade, the dataset (a backtest of **72,500 earnings events**), an aside
on **post-earnings-announcement drift**, whether the predictor variables predict, the model, the
**Kelly criterion**, Monte Carlo simulation, full- and fractional-Kelly results, **straddle vs
calendar**, a long-term simulation, a live trade example. That is this module read as a video:
short premium into the earnings IV crush, defined-risk structures chosen per event (its two
structures — short straddle and calendar spread — are two of the module's six), PEAD named in an
aside, and sizing by Kelly (the module sizes on simulated NLV; the advisor's `kelly-cap` rule caps
the same idea read-side). It is nonetheless **not the lineage**: nothing in the repo references the
channel or its oquants.com tool, and although the video (2025-02-12) is older than the module
(2026-07-06), the module's screen and structures came from EarningsEdgeDetection plus the written
literature above. The
resemblance is convergence from the same public research, not derivation — and the module's own
discipline answers the video where they differ: every parameter goes through the strat_test forward
test rather than a simulation whose results the video's own disclaimer calls "hypothetical,
simulated, or unverified".

**Video lineage: none.** Like flies and bwb, no video or course is cited or implied. The
module's provenance is a GitHub scanner plus the written options-education literature above; the
closest institutional video genre is tastylive's own earnings/IV-crush programming, which the repo
has never referenced.

---

## Flies — 0DTE net-credit butterflies, the "profit forest"

### Where the logic is documented

| File | What it holds |
|---|---|
| [`packages/flies/README.md`](../packages/flies/README.md) (222 lines) | "The idea in plain terms" and the two ways a credit is manufactured — start here. |
| [`packages/flies/CLAUDE.md`](../packages/flies/CLAUDE.md) and [`docs/reference.md`](../packages/flies/docs/reference.md) | The full statement: what the strategy actually is and the honesty rules (CLAUDE.md); **"The arms"** (the canonical arm list) and the layout (reference.md). |
| [`packages/flies/docs/faq.md`](../packages/flies/docs/faq.md) | The strategy explained question by question, including why only SPX/XSP (European, cash-settled) and never SPY or /ES-/MES futures options. |
| [`packages/flies/docs/history.md`](../packages/flies/docs/history.md) (850 lines) | The verbatim dated record every rule came from — including the 2026-08-01 XSP→SPX switch (fees 41.4% drag vs 10.9%). |
| [`packages/flies/docs/experiment-log.md`](../packages/flies/docs/experiment-log.md) | The append-only per-session record since 2026-07-20 — the evidence behind the standing conclusions. |
| [`packages/flies/docs/completion-timing.md`](../packages/flies/docs/completion-timing.md) | Why the completion gate is first-qualifying-tick, measured and then confirmed against the optimal-stopping literature. |
| [`packages/flies/docs/iron-completion.md`](../packages/flies/docs/iron-completion.md) | The retired iron arm's negative result, kept as required. |
| [`packages/flies/docs/live-trading-plan.md`](../packages/flies/docs/live-trading-plan.md) | The Gate-0 statistical bar and the live pilot's rulebook. |
| [`packages/flies/tests/fixtures/books.json`](../packages/flies/tests/fixtures/books.json) | **Three real tastytrade order chains, transcribed** — the strategy's origin artefact, shipped as regression fixtures. |

### The strategy in brief

A long butterfly pays `max(0, W − |S − K|)` at expiry, bounded to `[0, W]` — so buying one for a net
debit is fine, but **entering one for a net credit means the worst case is profit**. Such a credit has
to be manufactured, and both real mechanisms are implemented: legging in (sell a credit spread, buy
the completing spread cheaper) and entering a cheap fly outright funded by premium already collected
in the same session (now off; `entry_modes: ["legged"]`). The module measures whether the
manufactured credit survives real costs: floors are computed after fees, `is_risk_free` returns False
when the fee stack ate the floor, and a book-level floor always carries the price band over which it
holds. A long fly's worst case being zero only holds because SPX/XSP are European and cash-settled —
a physically settled symbol would deliver shares or a futures position instead of cash.

### Origin commits — decoded from real order chains, not from a published strategy

- **`7f5f0aac` (2026-07-19) — "flies: new paper module for 0DTE net-credit butterflies."** The commit
  message states the design's provenance outright: *"The design is decoded from three real
  tastytrade order chains rather than assumed, and those chains ship as regression fixtures — they
  test the accounting against something that actually happened rather than against a model we also
  wrote."* The sign convention (positive = credit) is pinned by arithmetic from the operator's
  screenshots, with its own tests, because getting it backwards would flip every floor in the module.
  It shipped with three arms (gex, time_window, control) so the GEX claim would be falsifiable
  rather than assumed.

This is the honest answer to "which study or video is flies based on": **none is cited, and the
origin commit says why** — the structure came out of real order chains the operator had placed, not
out of a published method. The distinctive pieces (the debit-first 15-delta OTM pair, the bwb roll,
the completion gates, the arm methodology) are the suite's own, measured in the experiment log.

### External references the repo cites

- **du Toit & Peskir, "Selling a stock at the ultimate maximum"** — *The Annals of Applied
  Probability* 2009, Vol. 19, No. 3, pp. 983–1014 (arXiv:[0908.1014](https://arxiv.org/abs/0908.1014),
  DOI [10.1214/08-AAP566](https://doi.org/10.1214/08-aap566)). Cited in
  [`docs/completion-timing.md`](../packages/flies/docs/completion-timing.md) under "What the theory
  says": the finite-deadline optimal-stopping result is bang-bang in the drift — against adverse
  drift, sell immediately (first-qualifying-tick is optimal, which is what the ledger found);
  under favorable drift, the optimal rule is a trailing retracement from the running maximum,
  tightening to zero as the deadline approaches. That second branch is why the door stays open on
  future gate work, and why no fixed tightening of the gate was adopted.

---

## The external lineage — verified 2026-10-12, recorded here because the repo docs only name "Chambless"

### Tammy Chambless and MEIC

The "Chambless" of the MEIC convention is **Tammy Chambless**, called "the Queen of 0DTE" in the
coverage below — a full-time options trader, trading options since 2006 and exclusively 0DTE since
2019. She founded the Quantum Options Facebook Group and Discord Group, where her research is
posted, and has presented to trading groups in the US and abroad.

**Her YouTube channel — [Tammy Chambless 0 DTE Options Trading](https://www.youtube.com/@tammychambless-0dteoptions)** —
holds the strategy's canonical videos (all by her; view counts as found on 2026-10-12):

| Video | ID | Date | Views |
|---|---|---|---|
| [0 DTE SPX MEIC Strategy](https://www.youtube.com/watch?v=cOoOaKvHRqk) — the original statement: why she started, the basic parameters, how results vary with them, the backtesting tools | `cOoOaKvHRqk` | 2023-01-16 | 46K |
| [Updated MEIC+ 0 DTE SPX Trading Strategy](https://www.youtube.com/watch?v=1ROiR05DJbU) — where **MEIC+** is defined: turning break-even trades into profitable ones; tweaks for a low-volatility market; trade sizing; the BYOB backtester guide | `1ROiR05DJbU` | 2023-07-22 | 42K |
| [DFWOTG MEIC Presentation, Part One](https://www.youtube.com/watch?v=NvxYunqQHXU) — basics, the variables you can trade, backtesting software (to the DFW Options Trading Group, 2025-04-12) | `NvxYunqQHXU` | 2025 | 17K |
| [MEIC Presentation, Part Two — Automated Trading Platforms](https://www.youtube.com/watch?v=AaCXtz6vBUI) | `AaCXtz6vBUI` | 2025 | 7.4K |
| [Update to 0 DTE SPX Credit Spread Strategy](https://www.youtube.com/watch?v=cqnL6-44ZOk) — the pre-MEIC credit-spread work: spread widths vs stop-out rates, short-leg-only stop-out comparison | `cqnL6-44ZOk` | 2021-07-28 | 67K |
| [0 DTE Credit Spreads Updated Backtesting](https://www.youtube.com/watch?v=38CKbJgQbZ8) | `38CKbJgQbZ8` | 2022-01-18 | 14K |
| [0 DTE SPX Recommended Adjustments for this Market](https://www.youtube.com/watch?v=O0C_oroBtUM) | `O0C_oroBtUM` | 2022-05-06 | 21K |
| [How to Check a Chart of an Options Strike](https://www.youtube.com/watch?v=g7GAQ9UiUJk) | `g7GAQ9UiUJk` | recent | 2.1K |

**The written summary — [Theta Profits, "Tammy Chambless explains her MEIC strategy for trading 0DTE options"](https://www.thetaprofits.com/tammy-chambless-explains-her-meic-strategy-for-trading-0dte-options/)** (John Einar Sandvand, 2025-09-24, an excerpt of the Theta LIVE session *"3 Ways to Trade 0DTE"* held 2025-09-10) states her rules plainly:

- **Six trades per day**, typically 30–60 minutes apart, at predefined times.
- **Credit aim $1.00–$1.75 per side**; spread widths 50–60 on average, 100 maximum (other traders
  down to 25).
- **Per-side stops, each set at the full iron condor's credit** — her example: $1.50 collected per
  side, $3.00 total, so the stop on each side sits at $3.00. Each side is entered and managed
  separately; trades are left on until they hit the stop or expire worthless.
- **MEIC+** — her own twist, aimed at the roughly 30% of days that finish flat: set the stop $0.10
  *below* the 1× net level (a $2.00 total credit stops each side at $1.90), so a day where one side
  stops and the other expires worthless nets slightly positive instead of zero. Particularly useful
  on trending days.
- Goals: consistency, minimized drawdowns, manageable losses through clear stops, flexibility
  through multiple entries rather than one all-in trade.
- Self-reported results: 20.7% compound annual since January 2023, 4.31% maximum drawdown.
- Automation via Trade Automation Toolbox and Trade Steward.

**How this maps onto this suite.** The correspondence is exact and was clearly known when MEIC was
built: the repo's stop trigger is `stop_trigger_ratio × net_credit` with the basis keyed to the
*whole IC's* credit — precisely her rule — and the shipped `stop_trigger_ratio: 0.95` is precisely
MEIC+ (a $2.00 total credit stops each side at $1.90, $0.10 under break-even). What the suite does
**not** take from her is everything after entry: the delta targeting and its VIX bands, the gate
catalog, the regime gates, the fee floors and the arm machinery are this suite's own, and the
derived stop policies exist precisely because her convention's cost (stopping sides that would have
expired worthless anyway) is real and unpriced by the method. The writeup above does not state her
strike-selection rule, so no comparison is offered there.

**Also verified:** [Option Omega Academy](https://academy.optionomega.com/course/meic) sells the paid
course *"M.E.I.C — The definitive guide to the M.E.I.C strategy"*, over 14 hours including live
trading sessions, **taught by Tammy Chambless**, composed of live classes from late 2023 and early
2024 with an update in June 2024. Option Omega hosted an **[MEIC Roundtable](https://www.youtube.com/watch?v=htwCH2v56f8)**
("Tammy, Dan and Friends!", 2026-10-06) on its own channel.

### The "profit forest" genre — flies' nearest external analogues

No source is cited for flies, but the phrase "profit forest" and the underlying structure
(a credit spread completed into a fly so the expiry graph grows a risk-free tent) circulate on
YouTube independently. The closest verified analogues:

- **"🌳SPX 0DTE Risk Free Profit Forest Walkthrough"** — [Stock Market Options Trading](https://www.youtube.com/watch?v=jLKG5ozYKrI):
  a 1DTE put credit spread entered off Alpha Crunching's "Tomorrow's Triumph Rate", adjusted after a
  gap-up into a call credit spread plus a long put butterfly — a "profit forest" of multiple tents.
- **A reaction to the same trade** by [Christopher Uhl — OVTLYR](https://www.youtube.com/watch?v=IPwxhvFLvCw):
  "every trader has seen those videos claiming you can make 'risk free profit' … there is no such
  thing as a risk free trade. But sometimes it is worth digging in to see what people are actually
  doing" — the same scepticism the flies module answers with measured floors instead of rhetoric.
- **"Profit Forest Options Strategy Explained using Optionsmith"** — [Optionsmith](https://www.youtube.com/watch?v=C37oFLn1iw0):
  call and put butterflies as profit "trees", delta-selected strikes, broken-wing butterflies to
  reduce the risk floor, and the admission that "risk-free" trades usually still carry hidden risk.
- Theta Profits' *"3 Ways to Trade 0DTE"* (the full Tammy Chambless / Dale Perryman / Mark Anderson
  session, [video `MaNYhyjQ30E`](https://www.youtube.com/watch?v=MaNYhyjQ30E); the condensed
  Chambless excerpt is [video `o-CmLEeiaoU`](https://www.youtube.com/watch?v=o-CmLEeiaoU)) — the
  third presenter's method combines condors, flies and long volatility in one portfolio.

The suite's flies differs from all of these in one load-bearing way: it does not *claim* risk
freeness, it **measures** it — a fly whose floor the fee stack ate reports `is_risk_free = false`,
and a book-level floor carries the price band over which it holds.

---

## The pattern elsewhere in the suite

Modules that came from a published source name it; the suite's own contributions are then measured
against it. Every named source found:

| Module | Source | Where recorded |
|---|---|---|
| **PMCC** | Tom King's "Income Shield" (Theta Profits, [video `P43DluId17o`](https://www.youtube.com/watch?v=P43DluId17o)): a ~1-year call at about 0.925 delta, a ~0.70-delta weekly call sold against it | [`packages/pmcc/docs/shield-study.md`](../packages/pmcc/docs/shield-study.md) |
| **ivan** (shelved /NQ proposal) | Rob and Maria Helmick, "Math Makes Money" on YouTube, described in full on Theta Profits' *My Trade* segment recorded 2026-09-14 — their own parameter table is carried, thresholds converted to percentages | [`docs/history/ivan-plan.md`](history/ivan-plan.md) |
| **volcrush** (plan-only branch) | Option Omega's "Tasty Vol Crush" — an SPX short-call backtest, 2013–2026 | commit `e51318bf`, 2026-10-09 |
| **GEX engine** | What gexbot.com, SpotGamma and MenthorQ sell — a self-hosted lightweight take | [`packages/gex/README.md`](../packages/gex/README.md) |
| **MEIC** | Chambless (convention + MEIC+), Option Alpha (stop literature), 0DTESPX.com (rejected data source) | cited in the module docs above |
| **BWB** | No source named in the module; the structure is tastylive's flagship SPX broken-wing butterfly genre, with a 0DTE practitioner wing on YouTube — and tastylive cited for a **negative** (they have published no management study for earnings BWBs) | [tastylive's own concept page](https://www.tastylive.com/concepts-strategies/broken-wing-butterfly) and the genre survey above |
| **Earnings** | EarningsEdgeDetection (the screen's thresholds and tier language, named in-repo, since reimplemented as pure functions); the written sources in `strategy-optimization.md`; the PEAD gate, named without its academic citation (Ball & Brown 1968, Bernard & Thomas 1989); tastylive cited for a negative on earnings BWBs; MenthorQ's ACES earnings-calendar guide, referenced in [`strategy-optimization.md`](../packages/earnings/docs/strategy-optimization.md) | cited in the module docs above |

MEIC and flies are the only two strategy engines with academic-literature or named-originator
citations inside their docs (MEIC the convention, flies the stopping paper). Earnings names its
upstream repo and its written literature plainly; its two unrecorded debts are the PEAD papers and
the EED thresholds, both filled in above. BWB names nothing at all — its only lineage is the
tastylive genre, which is this document's finding, not the repo's claim. Everything else in all
four modules is the suite's own measurement.

## What was searched and not found

- **No YouTube video, podcast or course is cited anywhere in the repo's own documentation** for
  MEIC or flies. The only in-repo YouTube references anywhere in the suite are the PMCC
  shield study's Tom King video and the shelved ivan plan's "Math Makes Money" — neither touches
  these two modules. bwb and earnings name no video either; bwb's lineage is a genre, not a video.
- No reference to the Option Omega MEIC course, to Tammy Chambless's channel, or to Theta Profits
  exists in the repo; the "Chambless" name is carried bare, as if the reader already knew it. This
  document exists to fix that.
- Flies' origin is claimed from artefacts (the transcribed order chains), not from literature — the
  one academic citation it carries (du Toit & Peskir) governs the completion gate's timing, not the
  structure itself.
- **BWB** names no external source at all — not in the README, the CLAUDE.md, the config example
  (whose `_source_note` is about a stream-cache path) or the history doc. The tastylive genre above
  is this document's finding, not the repo's claim; the module's own recorded lineage is entirely
  in-suite (the ratios float of 2026-08-22).
- **Earnings** names its upstream repo and its written sources plainly; the gap is the PEAD gate,
  which carries the effect's name but never the two classic papers behind it (recorded above), and
  the fact that the shipped thresholds are EarningsEdgeDetection's numbers carried over — stated in
  a code comment, not in the screening doc, where a reader would look for it.
- Search coverage caveat: the video lists above are a sample, not an exhaustive catalogue. The
  tastylive genre list is broad enough to characterise the genre.
