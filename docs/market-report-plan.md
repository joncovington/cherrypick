# Our own daily market report — research and plan

*Drafted 2026-09-27. A research record and a build plan, nothing built yet. The subject is a
third-party pre-open report (the 2026-09-25 edition of a commercial vendor's daily report), read
section by section to find out what it measures, what data each measurement needs, and what of that the suite
already has. Endpoints named below come from vendor documentation and search results; the research
sandbox could not reach them, so each one still needs a first fetch before it is relied on.*

## Why

`packages/overview` already produces a pre-open fact pack modelled on the research-report format,
with every number auditable because every number is ours. The vendor's report goes further on
four fronts the pack does not touch at all: relative-strength rotation across ~35 ETFs, stock-level
breadth, a multi-method signal table per stock, and institutional options flow. The point of this
document is to price each of those honestly before anything is built — which parts are pure
functions over data we can already get, which need a new (free) input, and which need paid data.

## The report, decoded

Nine parts. Each row says what produces it and whether we can. The section and label names used
throughout this document are our own generic descriptions, not the vendor's.

| Section | What it is | What produces it |
|---|---|---|
| Header tape | S&P 500, Nasdaq-100, Dow, Russell 2000, 10Y yield, WTI, VIX; prior close plus pre-market | Quotes, futures, a yield series |
| 1. Headline read | A three-way market stance, with stated flip conditions | A mechanical phase over the trend rows below, plus prose |
| 2. Summary board | Equities, Rates, Volatility, Rotation, Breadth, Institutional Flow — one label each | Roll-up of sections 3–7 |
| 3. Prior-session movers | Yields, oil, single-stock movers, each with an interpretation | Narrative over headlines; the movers are data |
| 4. Multi-signal table | Bullish and bearish tables; nine methods, count of agreement per name | See "The multi-signal table" below |
| 5. Rotation | ~35 ETFs in four states | The rotation rule below |
| 6. Breadth and leadership | Breadth counts, a 2-week chart, top-5 names, a sector leaders/laggards table | The stage rule below, over a stock universe |
| 7. Large options trades | Lean per sector/fund, the eight largest trades | The flow filter below |
| 8. Week ahead | Economic releases, earnings with implied move, FOMC, the live macro risk | Calendars plus narrative |

### Definitions the report states about itself

These are paraphrased from its own footnotes, which is what makes most of it reproducible.

- **Relative-strength stages (section 6).** Three stages each way. The newest, *early*, shows on the
  one-month lookback only; *building* means the one- and two-month lookbacks agree; *confirmed*
  means one, two and three months all agree. Each name is measured against the S&P 500.
- **Bullish share and net breadth.** Bullish share is outperformers ÷ (outperformers +
  underperformers): 33 ÷ 164 = 20.1%. Names in neither state are excluded. Net breadth is
  outperformers − underperformers = −98.
- **Rotation (section 5).** Four states, which we call by the usual relative-rotation names.
  *Leading* and *lagging* mean the weekly and daily relative trends agree; *improving* means the
  weekly base is intact and the daily trend is just turning up; *weakening* means a recent leader
  whose daily trend has started turning down without yet breaking into lagging. Measured against
  the S&P 500, except the entries marked asset, which are measured against a balanced stock-and-bond
  benchmark.
- **Directional score.** A per-name bullish/bearish read combining trend and relative strength.
- **IV Rank.** Where implied volatility sits against its own past year, 0–100%.
- **Large options trades (section 7).** Calls and puts bought with at least $500K in premium, and
  puts sold obligating at least $10M, each with at least 7 days to expiration. Four categories —
  stock put sales, stock call buys, stock put buys, ETF buys — each showing its largest and
  next-largest qualifying trade. A put sale's "at risk" figure is the obligation (strike × 100 × contracts).
- **1-week expected move.** ±2.17% (±167 points) is VIX ÷ √52: 15.67 ÷ 7.21 = 2.17%, and
  7,704 × 2.17% ≈ 167. The implied-against-realized pair (15.7 vs 12.7) is VIX against, almost
  certainly, 20-session realized volatility.

### The chart layer

The vendor's platform uses relative strength for more than the stage screen: its charting
identifies support and resistance levels, the trend, and CCI, and signals three kinds of setup —
**continuation** of an existing trend, a **breakout or breakdown** through one of the levels, and a
**minor pullback** inside an intact trend that favours an option trade expecting the trend to
continue. This layer most likely feeds the directional score and the trend-following signals that
are one of the nine multi-signal methods.

It needs no data beyond daily high/low/close, and three of its four parts are standard: CCI (20
sessions: typical price less its moving average, over 0.015 × mean deviation), a trend rule, and the
signal rules on top. The fourth — **where the support and resistance levels are** — has no standard
algorithm, and theirs is proprietary. Ours will be approximate. Chart screenshots with their levels
and signals drawn on named tickers are the fixture for tuning it (Phase 4).

### What the platform's charts show

Twenty-two of the vendor's charts were captured on 2026-09-27 (Friday 2026-09-25's close, one session
after the report), chosen to span every state the report names; the readings are transcribed under
"Fixtures" below. Each chart page carries more than the report does, and most of it is legible
enough to reverse-engineer.

**What each page shows.**

- A **1M and a 6M trend**, graded Bullish / Mildly Bullish / Neutral / Mildly Bearish / Bearish, and
  a daily history of both drawn as two rows of green/amber/red squares under the price chart. These
  are *absolute* price trends, not relative ones: XLI reads Bearish/Bearish on the same weekend the
  report put it in the improving state against the S&P 500.
- **A 1–10 strength score**, with a band: 9–10 Very Strong, 7–8 Strong, 5–6 Neutral, 3–4 Weak,
  1–2 Very Weak. **This is not the report's stage screen.** MS scores 8 while the report lists it
  as a confirmed underperformer, and ISRG scores 4 while it is a building outperformer. It behaves like a longer-lookback rank (ORCL, down from 322 to 137 over the year,
  scores 3), but that is a guess.
- IV rank, a three-way liquidity class (Very Liquid / Somewhat Liquid / Not Liquid — DTE is "Not
  Liquid" on 1.9M shares a day, so this is almost certainly option liquidity), earnings date.
- A **3-month daily candle chart with a CCI panel** (±100 lines, shaded beyond them), and one or
  two support lines and one or two resistance lines with their prices and distance from last.
- A templated **price-action sentence** — "in a bullish trend with resistance at $X", "in a neutral
  trend, with support at $X and resistance at $Y", "breaking below its $X support level", "gap down
  on high volume on <date>", "a 3.66% move up today" — and, where one fired, a **named signal**
  with a marker on the chart:
  - **Trend pullback** — a pullback within a longer-term bullish trend (XLE: 1M Mildly Bearish, 6M
    Bullish, CCI just crossed down through −100).
  - **Reversal** — an extremely bearish trend showing signs of turning bullish (MGM and TLT: both
    trends Bearish, strength 2–3, CCI deep below −100 and turning).

**How the levels are placed.** A second capture of the same 22 tickers on a 6-month chart (same
session, same artifact) settled part of this and overturned part of it. Four findings, all from the
prices themselves:

1. **The levels do not depend on the chart.** Every support and resistance price is identical on
   the 3-month and the 6-month chart. They are computed over a fixed history (at least the 52
   weeks the header shows), not over whatever window is on screen.
2. **Each line starts at a swing point.** On the 6-month chart every level is drawn from a
   specific bar forward, and that bar is a swing high or low: KEYS 374.96 from its late-June
   high, MGM 51.59 from the June 1 high, ORCL 114.50 from the late-July low, SPY 777.44 from the
   mid-August high. Old highs become support once price clears them: AMD 574.33 runs from the
   early-July peak, and SPY 756.11 from the early-June high. Lines from pivots older than six
   months run across the whole chart (META 741.87, AVGO 372.98 and 388.98). So levels are
   **pivot-based** — a swing detector, then a choice of which pivots to show — and not a price
   histogram, which would have no starting bar.
3. **The prices sit on a grid.** Within a ticker the levels share their cents: ANET 202.52 /
   197.52 (exactly 5.00 apart), ISRG 361.07 / 328.57 / 416.07 / 443.57 (all multiples of 2.50
   apart), MS 193.47 / 184.47 / 197.47 / 203.47, IWM 278.60 / 269.60 / 289.60, IGV 99.42 / 96.92 /
   108.42 / 111.92 (0.50 steps), TLT 78.83 / 81.43 / 83.23 (0.20 steps). Swing points do not land
   a whole number of dollars apart in nearly every ticker by chance, so a pivot's price is
   evidently **snapped to a grid** rather than taken as printed. The step scales loosely with
   price ($0.20 on an $80 fund, $2.50 on a $400 stock, but XLI at $170 uses 0.50 and MS at $196
   uses 1.00). The grid's anchor is open: for non-dividend names it is the 52-week low (ANET
   114.52, KEYS 158.79, ISRG 328.57 and MGM 29.18 all appear as a level, exactly), but IWM's and
   AME's grids do not line up with their adjusted lows. The 52-week extremes themselves are not
   snapped.
4. **The bars are dividend-adjusted, proportionally.** Extremes match the displayed 52-week
   figures exactly only for names that pay no dividend or have not gone ex since. For payers the
   level sits lower by about the dividends paid since, scaled by price — the proportional
   adjustment most data vendors use, not a subtraction. MSFT shows it most clearly: a 349.20 low
   and a $0.91 quarterly give 348.29 subtracted but 349.20 × (1 − 0.91/≈500) ≈ 348.56
   proportionally, and the level is 348.54. The one-dividend cases agree to a cent or two
   (AME 260.78 from 261.16, IWM 304.38 from 305.18, XLI 187.69 from 188.19); ORCL's 319.46 against
   a 322.54 high is about six $0.50 quarterlies. The ex-dividend prices used here are estimates.
   The platform computes on adjusted history while displaying raw 52-week figures, so we need
   adjusted bars, or raw bars plus a dividend history.

At a new high the resistance is the adjusted high or a level above it (AMD shows 639.00 over a
630.80 high), and a few levels fit none of this (XLE's 62.68). Both are for the tuning pass, not
blockers. What these screenshots cannot settle — the swing detector's width, which pivots are
kept, the grid step and anchor — needs the bars themselves: detect pivots on adjusted history and
see which rule reproduces the fixture's exact cents.

**The directional score, tentatively.** Against the report's directional-score labels, nine of
ten names fit one rule: both trends Bullish and strength 9–10 → Strong Bull; both Bullish, lower strength → Bull; both
Bearish-leaning and strength 2–3 → Strong Bear; both Bearish-leaning, higher strength → Bear;
trends disagree or one is Neutral → Neutral. The tenth (MTN, Neutral on Bearish / Mildly Bearish)
may be the one-session gap between the report and the charts. A same-day capture settles it.

### The multi-signal table, method by method

| Tag in the table | What it is | Ours? |
|---|---|---|
| Confirmed / building outperformer or underperformer | The stage rule applied to the name | Buildable, exact |
| Sector inflow / outflow | The rotation state of the name's sector | Buildable |
| Trend-following signal | The chart layer above | Buildable, approximate |
| Large put sale / call buy / put buy, $ | Section 7's flow filter, per name | Paid data |
| Analyst upgrade / downgrade (e.g. a price target +15%) | Firm plus rating or price-target change | Paid data |
| Strong / weak fundamentals (two checks) | A fundamentals score | Free data, our own definition |
| Fundamentals leading price / price leading fundamentals | Disagreement between the fundamentals score and relative strength | Falls out of the two above |
| Sector signal net (Technology +6, …) | Bullish − bearish tags per sector | Arithmetic |

## What the suite already has

From an inventory of the tree on 2026-09-26:

- **One market-data source:** tastytrade DXLink, through the streamer, into the shared stream cache.
  Quotes, trades, greeks, open interest, daily summary, and daily candles for backfill. Option chains
  are an ATM strike window plus declared expirations, never a full chain.
- **Overview's pack** reads SPX, VIX, VIX1D/9D/3M/6M/1Y, VVIX, SKEW, the eleven sector ETFs, USO,
  GLD, HYG, TLT; GEX flip and walls from the gex recorder; a sector board; the record-only deployment
  score; FOMC and expiry dates.
- **The gex regime recorder** adds SPY, RSP, LQD and three futures readings (VX front two, ZN front),
  one row a minute during regular hours only.
- **IV rank** exists only inside meic and earnings, from tastytrade market metrics — both modules
  hold credentials; overview does not.
- **The Dolt datasets** the earnings module pulls include a `stocks` database with daily price
  history. Not yet assessed for coverage or freshness.
- **Nothing** for yields, pre-market futures, stock-level history, analyst actions, fundamentals
  beyond market cap, options flow, or headlines. No CCI or support/resistance code anywhere in the
  packages.

Two known reliability facts shape the plan:

- **SKEW is intermittent on the feed** (30 usable intraday samples of 1,105), so the header's SKEW
  percentile needs Cboe's daily file instead.
- **The streamer cannot carry a stock universe.** Declaring sixteen extra underlyings pushed it to
  ~20,000 subscriptions and a crash loop on 2026-08-17, and a 1000-day history request never
  finished (`packages/overview/CLAUDE.md`, `docs/streamer-subscription-budget.md`). Several hundred
  single names with a year of history belong in an end-of-day file fetcher, not in
  `state/stream_requests/`.

## The gaps, ranked

1. **Institutional options flow — large, paid.** Classifying a trade as bought or sold needs every
   OPRA print paired with the NBBO at that instant; our stream sees only the chains we request.
   Candidates: ThetaData (trades with the quote attached), Massive — formerly Polygon — (options
   trades plus quotes), or the Unusual Whales API (flow already classified, which makes the
   classification theirs rather than ours). Feeds section 7 and three multi-signal tags.
2. **Analyst revisions — medium, paid.** No good free source gives firm and price-target change.
   Financial Modeling Prep's grades and price-target endpoints, or Benzinga.
3. **The stock universe and sector map — medium, definitional.** The report's universe is not the
   S&P 500 — the Sept 25 tables include TWLO, OKTA, NET, DDOG, ZS, CLS and a dozen ADRs (TSM, SONY,
   INFY, NMR, MFG, SMFG, ITUB, KB, SHG, TD, BN, DB). Its sectors are not GICS either: UBER, ADP, PAYX
   and GRMN sit in Technology, and it says "Basic Materials". Matching the counts means matching both
   choices, and the Sept 25 table below is the test.
4. **Fundamentals score — medium, definitional.** SEC EDGAR's company financials are free (a
   declared User-Agent is required); what "strong fundamentals" means is ours to write.
5. **Support and resistance levels — medium, approximate.** Deterministic, no new data, but the
   part of the build most likely to disagree with theirs.
6. **Headlines and macro narrative — agent side.** Stays outside every package, in
   `scripts/morning_narrative.py`, fed by a headline source (Finnhub's market-news endpoint has a
   free tier).

Everything else is small: see Phase 1.

## Where the code goes

The suite's fences decide most of this:

- **Anything that reaches the network lives in `scripts/`** and writes dated snapshots into a local
  store, as `scripts/refresh_dolt_data.py` does today. Packages read the snapshots. That keeps every
  report rebuildable from stored data and keeps overview credential-free and network-free.
- **The engines are pure functions over bars** — the stage rule, rotation, trend, CCI, levels,
  signals. They belong in a package, not in `scripts/`, and they belong in one place so the ETF
  rotation and the stock breadth cannot drift apart: the report's own vocabulary says they are the
  same engine.
- **Overview remains the pre-open pack.** It gains readings (Phase 1) and reads the engines'
  end-of-day output; it does not grow a stock universe of its own.

Open decision: whether the engines and their end-of-day store become a new package or a module in
`cherrypick.core`. A new package gets its own store, CLAUDE.md and tests, which suits a job that
runs after the close and writes several hundred rows a day; core suits a library that several
packages call. Leaning new package, decided at Phase 2.

## Plan

Each phase ends with something that runs and a test that has been shown to fail.

### Phase 0 — decisions, before code

- Budget for paid data (flow and analyst revisions), and whether a vendor's pre-classified flow is
  acceptable or the buy/sell rule must be ours.
- The stock universe: a liquidity-screened optionable list, its size, and how often it is rebuilt.
- The sector taxonomy: match theirs (not GICS) or use our own and accept different counts.
- Whether the report proposes trades. The vendor's income trade ideas (short puts and put
  spreads on leaders, expiring before the next earnings date) are the part closest to a trade ticket; the suite's only
  discretionary order path is `packages/desk`, and a read-side report should say so if it suggests
  one.
- The engine's home (above).

### Phase 1 — quick wins inside the existing posture

- Futures legs for /ES, /NQ, /YM, /RTY, /CL and /BZ, extending `scripts/refresh_futures_contracts.py`
  the way it already resolves VX and ZN; overview reads them as the pre-market tape. The producer
  starts at 07:00 and the pack builds at 08:30, so these are live readings.
- NDX, DJX and RUT (or QQQ, DIA, IWM) as quote-only legs; RSP into the pack (already recorded).
- A yields fetcher in `scripts/`: Treasury.gov's daily par yield curve file, FRED `DGS10`/`DGS30` as
  the fallback. Also worth one probe of TNX on the feed; it has never been tried.
- Cboe's daily SKEW history file, replacing the intermittent stream reading for percentiles; VVIX
  history kept.
- Weekly expected move (VIX ÷ √52) and 20-session realized volatility in the pack.
- An economic release calendar from FRED's release-dates API, beside the FOMC dates already there.

Each new leg is a subscription decision; add them together, outside market hours, watching the
producer — the rule `packages/overview/CLAUDE.md` already states.

### Phase 2 — the end-of-day store

- Assess the Dolt `stocks` history for coverage (does it carry the ADRs?) and freshness (is last
  night's bar there by 06:00?). Only if it fails, price a bulk end-of-day source.
- The universe file and the sector map, versioned.
- A `scripts/` fetcher that lands one day's bars for the universe and the ~35 rotation ETFs into
  the store. Backfill a year once.

### Phase 3 — the stage and rotation engines

- The stage rule over 1/2/3-month relative performance against SPX. **Validated against the Sept 25
  fixture below**: same universe, same session, the same 33 and 131. Each saved report adds a day.
- Rotation over the ETF list, daily and weekly relative-strength trend, four states; Asset entries
  against a stock-and-bond benchmark (AOR is the obvious stand-in). The trend definition is ours and
  is written down.
- The 2-week breadth history falls out of a backfill, since it is price-derived.

### Phase 4 — the chart layer

- Dividend-adjusted daily bars first: the level placement depends on them (see "What the
  platform's charts show"). Establish whether the bar source already adjusts, or where the dividend
  history comes from.
- The 1M and 6M trends on the five-step scale; CCI; the 1–10 strength rank and its bands.
- Levels as swing pivots on adjusted history, snapped to a price grid. Each level in the fixture
  also has a known starting bar on the 6-month chart, so the pivot detector can be checked on
  *which* bars it picks as well as the prices. The sharp test is the exact cents: a rule either
  reproduces them or it does not.
- The two named signals (trend pullback, reversal) and the price-action events (level break,
  gap on high volume, large move); the directional score from trends × strength per the tentative
  rule.
- Scored against the 22-chart fixture. Agreement is reported as a rate, not assumed.

### Phase 5 — calendar and trade ideas

- Earnings in the next week with implied move (the earnings module already computes it) and the
  release calendar from Phase 1.
- Income trade ideas only if Phase 0 says yes.

### Phase 6 — paid inputs

- Flow: the filter as defined above, per sector and fund lean, the eight largest trades, and the
  three multi-signal tags.
- Analyst revisions and the fundamentals score, completing the multi-signal table.

### Phase 7 — the render

- The new sections in the pack and on the console's Morning tab.
- A headline feed to `scripts/morning_narrative.py` for sections 3 and 8's prose.

## Fixtures from the 2026-09-25 edition

Transcribed from the report as published, so a rebuild can be scored against it.

### Section 6 — leaders and laggards by sector

Nets sum to −98; leaders total 33, laggards 131.

| Sector | Net | Leaders | Laggards |
|---|---|---|---|
| Technology | +6 | P, TWLO, CLS, OKTA, AMD, INTC, NET, HPE, GRMN, DDOG, FTNT, TSM, PLTR, ZS, SMCI, ANET, KEYS | ORCL, PAYX, INTU, SONY, INFY, ADP, UBER, ERIC, IBM, CTSH, HPQ |
| Healthcare | 0 | DHR, TEVA, NTRA, IQV, MTD, A, ILMN, TMO, ISRG, MRNA | SNY, MDT, CI, CVS, CNC, COR, SYK, ABT, ZTS, MCK |
| Communication Services | −3 | META, WBD | TKO, CMCSA, BIDU, TTWO, AMX |
| Consumer Staples | −4 | — | PEP, CL, MNST, ABEV |
| Industrials | −5 | RKLB, AME, ETN, ROK | UPS, XYL, FDX, AXON, GD, PCAR, CMI, RTX, CPRT |
| Real Estate | −7 | — | CCI, O, VICI, EXR, AMT, PSA, PLD |
| Energy | −7 | — | CCJ, EPD, EC, MPLX, PBA, CQP, ENB |
| Basic Materials | −13 | — | KGC, MLM, CRH, VMC, VALE, WPM, MT, B, GFI, RIO, AU, NEM, AEM |
| Consumer Discretionary | −16 | — | HD, MELI, VIK, MCD, LVS, CPNG, DASH, TM, GM, F, LOW, CMG, SBUX, JD, SE, QSR |
| Utilities | −17 | — | WEC, AEE, FTS, NEE, PEG, XEL, EXC, D, DTE, SRE, FE, DUK, ETR, AEP, PCG, SO, AWK |
| Financials | −32 | — | AMP, HBAN, PGR, NMR, AON, RKT, BX, FITB, ARES, APO, DB, AJG, MFG, GS, BAM, MS, MUFG, PNC, SMFG, USB, BN, BBD, WTW, HIG, MTB, KKR, NU, KB, ITUB, SHG, WRB, TD |

The table shades each name by stage; the shades were not reliably legible in the capture, so stages
are recorded only where the report states them: AME, ANET, ETN and KEYS **early**, ISRG
**building**. The prior session was 36 outperforming and 144 underperforming (net −108).

### Section 5 — rotation states

| State | Members (type) |
|---|---|
| Leading | HACK (industry), IGV (industry, new), SPY (asset) |
| Improving | KWEB (industry), JETS (industry), XLI (sector, new) |
| Weakening | PDBC (asset), XOP (industry), XLE (sector) |
| Lagging | TAN, XHB, ITA, XRT, PAVE, IYT, KRE (industry); XLU, XLY, XLRE, XLP, XLB (sector); TLT (asset, new), LQD (asset, new), VNQ, TIP (asset) |

Also named as leaving a state that session: gold miners, copper miners and internet left
weakening; the US dollar left improving. XLK, XLF, XLV and XLC appear in no state.

### Section 4 — the multi-signal table

| Side | Name | Direction | IV rank | Tags |
|---|---|---|---|---|
| Bull | AMD | Strong Bull | 22% | Confirmed outperformer; sector inflow; put sale $18M at risk; strong fundamentals |
| Bull | META | Strong Bull | 58% | Confirmed outperformer; sector inflow; put sale $23M at risk; analyst upgrade (PT +15%) |
| Bull | AVGO | Bear | 0% | Sector inflow; call buy $0.6M; strong fundamentals |
| Bull | ISRG | Neutral | 66% | Building outperformer; strong fundamentals; fundamentals leading price |
| Bull | MSFT | Bull | 24% | Sector inflow; call buy $5.6M; strong fundamentals |
| Bear | MS | Bear | 53% | Confirmed underperformer; sector outflow; put buy $1.0M; price leading fundamentals |
| Bear | ARE | Neutral | 44% | Sector outflow; analyst downgrade; weak fundamentals |
| Bear | MGM | Strong Bear | 22% | Sector outflow; analyst downgrade (PT −8%); weak fundamentals |
| Bear | MTN | Neutral | 64% | Sector outflow; analyst downgrade (PT −8%); weak fundamentals |
| Bear | DTE | Strong Bear | 1% | Confirmed underperformer; sector outflow; weak fundamentals |

Sector signal net: Technology +6, Communication Services +1, Healthcare 0, Industrials −1,
Real Estate −1, Basic Materials −2, Utilities −2, Financials −4, Consumer Discretionary −5.

### Section 7 — the eight largest trades

| Category | Largest | Next |
|---|---|---|
| Stock put sales | META short 757.50P 10/2, $22M at risk | ADBE short 230P 11/6, $14M at risk |
| Stock call buys | ARM long 332.50C 10/16, $3.72M | WMT long 120C 9/17/27, $1.52M |
| Stock put buys | NVDA long 221P 12/18, $6.84M | FSLR long 140P 6/17/27, $989K |
| ETF buys | QQQ long 748C 10/9, $2.48M | IWM long 268P 12/18, $615K |

The session's largest trade overall was an IWM put sale obligating about $198M near the 268 strike.

### The vendor's chart pages, captured 2026-09-27 (2026-09-25 close)

The images are in a private chart-set artifact, as a 3-month and a 6-month chart per
ticker (the levels are identical on both); the readings are transcribed here.
Trends: B = Bullish, MB = Mildly Bearish, N = Neutral, Br = Bearish.

| Ticker | Last | 52W low – high | 1M | 6M | Str. | IV rank | Support | Resistance | Signal / price action |
|---|---|---|---|---|---|---|---|---|---|
| ANET | 206.55 | 114.52 – 214.89 | B | B | 10 | 12 | 202.52, 197.52 | 214.89 | Bullish trend, resistance 214.89 |
| KEYS | 362.15 | 158.79 – 374.96 | B | B | 9 | 34 | 294.79, 158.79 | 374.96 | Bullish trend, resistance 374.96 |
| AME | 250.74 | 179.24 – 261.16 | B | B | 8 | 49 | 248.15, 203.15 | 260.78 | Bullish trend, resistance 260.78 |
| ETN | 439.98 | 311.92 – 478.00 | B | B | 9 | 33 | 341.33, 333.33 | 478.00 | Bullish trend, resistance 478.00 |
| AMD | 630.63 | 157.05 – 630.80 | B | B | 10 | 20 | 574.33, 159.33 | 639.00 | — |
| META | 751.66 | 520.26 – 779.82 | B | B | 10 | 48 | 741.87, 519.37 | 779.82 | 4.50% move higher 2 days before |
| MSFT | 516.17 | 349.20 – 553.72 | B | B | 10 | 30 | 430.54, 348.54 | 530.54, 549.20 | 3.66% move up today |
| ISRG | 405.18 | 328.57 – 603.88 | B | N | 4 | 71 | 361.07, 328.57 | 416.07, 443.57 | Neutral trend, support 361.07, resistance 416.07 |
| MGM | 32.58 | 29.18 – 51.59 | Br | Br | 2 | 21 | 29.18 | 33.18, 51.59 | **Reversal signal**; gap down on high volume Sep 24 |
| ORCL | 137.08 | 114.50 – 322.54 | Br | Br | 3 | 18 | 132.50, 114.50 | 319.46 | Gap down on high volume Sep 24 |
| DTE | 121.44 | 121.05 – 155.75 | Br | Br | 2 | 1 | 120.24 | 128.24, 154.33 | — |
| MS | 196.31 | 151.84 – 232.25 | Br | MB | 8 | 47 | 193.47, 184.47 | 197.47, 203.47 | Bearish trend, breaking below 197.47 support |
| AVGO | 352.81 | 289.96 – 495.00 | Br | Br | 7 | n/a | 288.98 | 372.98, 388.98 | Bearish trend, support 288.98 |
| ARE | 49.92 | 39.41 – 85.37 | Br | N | 6 | 67 | 48.90, 38.90 | 81.75 | Neutral trend, support 48.90, resistance 81.75 |
| MTN | 136.11 | 118.51 – 163.34 | Br | MB | 6 | 67 | 123.19, 121.19 | 142.69, 155.69 | Bearish trend, support 123.19 |
| SPY | 771.35 | 629.28 – 779.37 | B | B | 8 | 8 | 764.11, 756.11 | 777.44 | Bullish trend, resistance 777.44 |
| QQQ | 744.50 | 555.60 – 748.65 | B | B | 9 | 22 | 732.41, 726.41 | 748.35 | — |
| IWM | 281.97 | 228.90 – 305.18 | Br | MB | 7 | 11 | 278.60, 269.60 | 289.60, 304.38 | Bearish trend, support 278.60 |
| IGV | 106.01 | 73.93 – 117.76 | B | B | 9 | 50 | 99.42, 96.92 | 108.42, 111.92 | Bullish trend, resistance 108.42 |
| XLI | 170.43 | 147.13 – 188.19 | Br | Br | 5 | 6 | 165.97, 156.47 | 187.69 | Bearish trend, support 165.97 |
| XLE | 62.04 | 42.35 – 66.17 | MB | B | 5 | 49 | 60.93, 47.93 | 62.68, 65.78 | **Trend pullback signal** |
| TLT | 79.32 | 79.42 – 92.19 | Br | Br | 3 | 71 | 78.83 | 81.43, 83.23 | **Reversal signal** |
