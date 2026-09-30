# Our own daily market report — research and plan

*Drafted 2026-09-27. A research record and a build plan, nothing built yet. The subject is a
third-party pre-open report (the 2026-09-25 edition of a commercial vendor's daily report), read
section by section to find out what it measures, what data each measurement needs, and what of that the suite
already has. The first draft's endpoints came from documentation and search results the research
sandbox could not reach; a second pass on 2026-09-27 fetched them, and "Sources, verified" below
says which were confirmed by a real fetch and which are still documentation only.*

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
  means one, two and three months all agree. Each name is measured against the S&P 500. **The
  windows are not settled:** the footnote says one, two and three months, but the report's own
  prose twice says "three weeks, six weeks and three months" and once "one-month, six-week and
  three-month". The engine is built with the windows as parameters and scored against the backfill
  both ways. **The stage is stateless** — recomputed each day, not a progression a name climbs:
  AMD went from early breakout (Sept 18) to confirmed (Sept 21) in one session, skipping building,
  and ISRG dropped out of the leader list on Sept 23 and came back as building on Sept 24.
- **Bullish share and net breadth.** Bullish share is outperformers ÷ (outperformers +
  underperformers): 33 ÷ 164 = 20.1%. Names in neither state are excluded. Net breadth is
  outperformers − underperformers = −98.
- **Rotation (section 5).** Four states, which we call by the usual relative-rotation names.
  *Leading* and *lagging* mean the weekly and daily relative trends agree; *improving* means the
  weekly base is intact and the daily trend is just turning up; *weakening* means a recent leader
  whose daily trend has started turning down without yet breaking into lagging. Measured against
  the S&P 500, except the entries marked asset, which are measured against a balanced stock-and-bond
  benchmark. The four states are not exhaustive: XLK, XLF, XLV and XLC sat in none of them on
  Sept 25, so a name whose trends disagree in any other way is in no state, and an engine has to
  say so rather than force it into one. The Sept 21–24 editions show why: the model keeps a
  separate daily and weekly reading per fund, and the report quotes them apart ("Technology is the
  leading sector on the daily view", "Semiconductors lead on the daily view") for funds that sit in
  no listed state. The four listed states are four *combinations* of the two readings; every other
  combination is left out. Funds move in and out of the list often (IWM and EFA dropped out; TLT
  and UUP dropped out and came back), and a fund can jump between opposite states in a session (XLI:
  improving Sept 18, lagging Sept 21, improving again Sept 24).
- **Directional score ("D-Edge").** "Combines trend direction and Relative Strength (performance
  versus the S&P 500) into a single directional read", in the report's own footnote. No formula is
  given; the rule inferred under "What the platform's charts show" is the only lead.
- **A 1–100 relative-strength score** appears in the report's prose (Seagate 96, Astera Labs 98,
  Sandisk 99; Royal Caribbean 2, Las Vegas Sands 2, PepsiCo 8). It is most likely the percentile
  rank behind the chart pages' 1–10 score, which would then be its decile — testable with one
  chart capture of a name whose 1–100 score the report has quoted. A 0–100 fundamental score
  appears the same way (Sandisk 82).
- **IV Rank.** Where implied volatility sits against its own past year, 0–100%. The platform's help
  pages define it as min–max: today's IV against the highest and lowest of the past 365 days.
- **Large options trades (section 7).** Calls and puts bought with at least $500K in premium, and
  puts sold obligating at least $10M, each with at least 7 days to expiration. Four categories —
  stock put sales, stock call buys, stock put buys, ETF buys — each showing its largest and
  next-largest qualifying trade. **A put sale's "capital at risk" is the obligation net of the
  premium collected: (strike − premium) × 100 × contracts**, the effective purchase price. Checked
  to the report's figures on six trades: WMB (66 − 0.45) × 1,712,000 = $112.2M ("$112M"), LOW
  (220 − 35.20) × 75,000 = $13.9M ("$14M"), META (680 − 21) × 516,100 = $340.1M, GOOG
  (350 − 33.35) × 400,000 = $126.7M, SNDK (1,630 − 89) × 15,200 = $23.4M, AMD
  (420 − 17.26) × 100,000 = $40.3M. Strike × 100 × contracts matches none of them. The report also
  prints the session's totals: long call premium, long put premium and short-put capital at risk,
  each with its count of qualifying trades.
- **1-week expected move.** ±2.17% (±167 points) is VIX ÷ √52: 15.67 ÷ 7.21 = 2.17%, and
  7,704 × 2.17% ≈ 167 — confirmed on all five editions to the point. The "implied vs realized" pair
  is VIX against realized volatility over **the last 10 sessions**, per the report's own caption
  (not 20, as first guessed). The VIX, VVIX and SKEW readings are *percentiles* of the past year —
  "options have been more expensive than this on 98% of days" — not min–max ranks; and the "five
  volatility measures we track" include Nasdaq-100 volatility (VXN).
- **The headline read** is three-way, with its flip rules stated. Sept 25 names all three: "a
  bearish one-month trend in the S&P 500 or Nasdaq-100 turns the read Cautious; the equal-weight
  S&P 500 turning bullish over one month, with breadth spreading beyond technology, would turn it
  Risk-On" — Cautious / Selective / Risk-On. (Sept 24 called the lower state "Defensive"; the
  wording drifts, the rule does not.) It sits on the 1M/6M trend and 1–10 score of
  SPX, QQQ and RSP, shown in the scoreboard — so ETFs do carry the 1–10 score.

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
algorithm, and theirs is proprietary. Ours will be approximate.

**What the platform publishes about itself.** The chart pages are a white-labelled product that
several brokers host, and its knowledge base and the brokers' help pages (fetched 2026-09-27) state
more than the report does:

- **The 1–10 "Relative Strength"** is "a summary of short, medium, and long term indicators" that
  ranks stocks *against each other*, not against the S&P 500 — which is why it disagrees with the
  stage screen. One broker page says ETFs are excluded from the ranking, yet every ETF in the
  captures carries a score; either that page is stale or ETFs are scored another way. No lookback
  or weighting is published.
- **The 1M and 6M trends** are "derived from a triple moving average method". Periods and grade
  cutoffs are not published.
- **Six daily scans**, in pairs: Trend Following, which "uses CCI indicator for trend continuation";
  Counter Trend, which "uses RSI indicator to identify potential reversals"; and Outperformance /
  Underperformance, "relative strength versus the S&P 500". So the **Reversal** signal is most
  likely the RSI counter-trend scan, not a CCI rule, and the outperformance scan is the most likely
  source of the report's stage screen. A 2026 article by the platform's strategist describes the
  pullback method as a 50-period momentum indicator with a 26-day EMA. No thresholds are published.
- **Liquidity classes** are fill distance from the mid: Very Liquid within 2–3¢, Somewhat Liquid
  5–10¢, Not Liquid 10¢ or more. Option liquidity, as suspected, but stated as an outcome, not a
  formula.
- **Support and resistance** are "calculated using a proprietary method". Nothing about pivots,
  grids, windows or adjustment is published; everything under "How the levels are placed" remains
  inferred from the charts. Chart screenshots with their levels
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
  1–2 Very Weak. The page header names it "Relative Strength" (ANET: 10/10), but **it is not the
  report's stage screen.** MS scores 8 while the report lists it as a confirmed underperformer, and
  ISRG scores 4 while it is a building outperformer. It behaves like a rank within a universe or
  sector, or over a longer lookback (ORCL, down from 322 to 137 over the year, scores 3), rather
  than a comparison against the S&P 500 — but that is a guess.
- EPS, P/E and a dividend line ("Div / Yield", and a sentence under the chart: "ANET does not pay
  a dividend"), which gives each name's dividend status directly.
- IV rank, a three-way liquidity class (Very Liquid / Somewhat Liquid / Not Liquid — DTE is "Not
  Liquid" on 1.9M shares a day, so this is almost certainly option liquidity), earnings date.
- A **3-month daily candle chart with a CCI panel** (±100 lines, shaded beyond them), and one or
  two support lines and one or two resistance lines with their prices and distance from last.
- A templated **price-action sentence** — "in a bullish trend with resistance at $X", "in a neutral
  trend, with support at $X and resistance at $Y", "breaking below its $X support level", "gap down
  on high volume on <date>", "a 3.66% move up today". It can also be empty: AMD's reads just
  "Advanced Micro Devices Inc." Where one fired, there is a **named signal** with a marker on the
  chart:
  - **Bullish Trend Following** (the platform's label; "trend pullback" in earlier drafts) — "a
    pullback within a longer term bullish trend that may provide a favorable risk/reward for a
    bullish trade" (XLE: 1M Mildly Bearish, 6M Bullish, CCI just crossed down through −100). The
    name ties the chart layer to the multi-signal table's trend-following method directly.
  - **Reversal** — an extremely bearish trend showing signs of turning bullish (MGM and TLT: both
    trends Bearish, strength 2–3, CCI deep below −100 and turning). The platform's scan list puts
    reversals on RSI, not CCI, so the CCI reading here is likely incidental; RSI on the fixture
    names is the first thing to check.

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
   108.42 / 111.92 (0.50 steps), XLE 62.68 / 60.93 / 47.93 (0.25 steps), TLT 78.83 / 81.43 /
   83.23 (0.20 steps). Swing points do not land a whole number of dollars apart in nearly every
   ticker by chance, so a pivot's price is evidently **snapped to a grid** rather than taken as
   printed. The step scales loosely with price ($0.20 on an $80 fund, $0.25 on a $62 one, $2.50 on
   a $400 stock, but XLI at $170 uses 0.50 and MS at $196 uses 1.00). The grid's anchor is open:
   for several non-dividend names it is the 52-week low (KEYS 158.79, ISRG 328.57 and MGM 29.18
   appear as a level, exactly; ANET's 114.52 is the anchor — 202.52 sits exactly 88.00 above it —
   but is not drawn as a level, even on the platform's longest view). AMD breaks that: it pays no
   dividend, yet its 159.33 support sits 2.28 above its 157.05 low. That low is from late
   September 2025, at the very edge of the 52-week window, so a slightly shorter window may be
   the explanation. IWM's and AME's grids do not line up with their adjusted lows either. The
   52-week extremes themselves are not snapped.
4. **The bars are dividend-adjusted, proportionally.** Extremes match the displayed 52-week
   figures exactly only for names that pay no dividend or have not gone ex since. For payers the
   level sits lower by about the dividends paid since, scaled by price — the proportional
   adjustment most data vendors use, not a subtraction. MSFT shows it most clearly: a 349.20 low
   and a $0.91 quarterly give 348.29 subtracted but 349.20 × (1 − 0.91/≈500) ≈ 348.56
   proportionally, and the level is 348.54. The one-dividend cases agree to a cent or two
   (AME 260.78 from 261.16, IWM 304.38 from 305.18, XLI 187.69 from 188.19). ORCL's 319.46
   against a 322.54 high is a 0.95% adjustment, too large for four $0.50 quarterlies subtracted
   ($2.00) but plausible proportionally, since the later ones went ex at well under half the
   high's price. Two adjusted *highs* now confirm it. MSFT's 549.20 is its 553.72 high after four
   $0.91 quarterlies: subtracted they give 550.08, proportionally at the chart's rough ex-dividend
   prices about 549.3. XLE's 65.78 is its 66.17 high after one $0.38 quarterly, which gives 65.78
   proportionally; subtraction's 65.79 is too close to tell apart here. Dividend amounts
   are from the chart headers (MSFT $3.64, AME $1.33, ORCL $2.00, IWM $2.73, XLI $1.88, XLE $1.52
   a year); the ex-dividend prices are still read off the charts, so each check is approximate.
   The platform computes on adjusted history while displaying raw 52-week figures, so we need
   adjusted bars, or raw bars plus a dividend history.

The 12-month captures settled two of the three unexplained levels. XLE's 62.68 is the late-March
swing high (its line starts about Apr 1) and sits on XLE's 0.25 grid. MSFT's 549.20 is the
adjusted 52-week high, drawn from the late-October 2025 bar that set it, and off-grid for the
same reason every extreme is. **AMD's 639.00 is the one real exception.** At a new high there is
no pivot above price, and the platform draws no line for it: 639.00 has no starting bar, sits
1.3% over the 630.80 high, and is off the grid AMD's supports share (574.33 and 159.33, 415.00
apart). How a resistance is placed above an all-time high is still unknown. It is for the tuning
pass, not a blocker. What these screenshots cannot settle — the swing detector's width, which
pivots are kept, the grid step and anchor — needs the bars themselves: detect pivots on adjusted
history and see which rule reproduces the fixture's exact cents.

**The directional score, tentatively.** Against the report's directional-score labels, eight of
ten names fit one rule: both trends Bullish and strength 9–10 → Strong Bull; both Bullish, lower
strength → Bull; both Bearish-leaning and strength 2–3 → Strong Bear; both Bearish-leaning,
higher strength → Bear; trends disagree or one is Neutral → Neutral. Two miss: MSFT (Bull on
Bullish / Bullish at 10, where the rule says Strong Bull) and MTN (Neutral on Bearish / Mildly
Bearish at 6, where it says Bear). Both may be the one-session gap between the report and the
charts — MSFT moved +3.66% on the capture day. The charts show Friday's close, which is what
Monday 2026-09-28's report is built on, so that report is the same-session pairing that settles
it.

**Captures to pair with the 2026-09-28 report.** Done on 2026-09-27, in the chart-set artifact:
the dividend headers for MSFT, AME, ORCL, IWM and XLI, and 12-month views of XLE, AMD and MSFT.
Still to come with Monday's report: charts for any names in its multi-signal table not already
captured, taken after Monday's close so they pair with Tuesday's report, and section 6 at a
resolution where the stage shading is legible.

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
| Sector signal net (Technology +6, …) | Bullish − bearish *names* per sector over the full stacked list | Arithmetic, over data we lack |

The Sept 24 edition, the first in this format, settles how the table is scored. **A name's score
is its count of tags** ("5 of 9": MU's five tags, TSLA's four, AMP's four all count out exactly).
**The sector net counts names, not tags, over a longer list than the one shown:** "11 of the 14
bullish Stacked Signals are technology names" against a Technology net of +11, with only five
bullish names printed. The full list's qualifying threshold is not stated.

**The flow tags are most likely per-name sums for the session**, not a longer window. Section 7
ranks single trades; the tags total every qualifying trade in the name. That explains AMD's $18M
put sale outranking section 7's second-largest ($14M) and MSFT's $5.6M call buy outranking the
largest single call ($3.72M), and the prose uses the same idea ("five separate short puts …
committed $542 million to owning the stock"). It also fits the numbers across editions: MSFT's
Sept 24 tag is a $12.5M call buy, which a cumulative window could not have shrunk to $5.6M a day
later. META's $23M tag against a $22M largest trade is still unexplained. The ninth method is the
trade scanner: the Sept 25 edition's list names it ("our trade scanner's Trend Following signals")
where Sept 24's left it out, and WMT's Sept 24 tags include "Bearish Trade Scan". The nine are
relative strength, sector rotation, call buying, put selling, put buying, analyst revisions, two
fundamental checks, and the scan.

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
- **The Dolt datasets** the earnings module pulls (`post-no-preference/stocks`, `earnings`,
  `options`, read in `packages/earnings/src/cherrypick/earnings/scanner.py`). Assessed 2026-09-27;
  see "Sources, verified" — they cover bars, dividends, splits, the earnings calendar and IV rank
  for most of the universe.
- **A 25-delta risk reversal**, recorded each minute by the gex regime recorder
  (`packages/gex/src/cherrypick/gex/regime.py`). It reads the *nearest* expiration, which for SPX
  is usually 0DTE and has no 25-delta strike, so the reading is often absent. It is a
  near-the-money skew measure, not Cboe's SKEW (a tail measure from the whole strike ladder), and
  must never be shown under that name.
- **Nothing** for yields, pre-market futures, analyst actions, fundamentals beyond market cap,
  options flow, or headlines. No CCI or support/resistance code anywhere in the packages.

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
   The cheapest faithful source is ThetaData's Options Standard ($80/month), whose `trade_quote`
   endpoint pairs every trade with the NBBO and returns a whole underlying per day. Unusual Whales
   ($150) sells flow already classified, which makes the classification theirs, and its terms are
   personal use only. Massive's trade files carry no quotes and land about 11:00 ET the next day;
   Databento is $199; Cboe DataShop about $1,000. **The free route does not work:** next-day open
   interest change (OCC per series, or Cboe's delayed chain) flags new positioning but cannot tell
   bought from sold, so put sales and put buys — two of the four categories — are
   indistinguishable. It can feed an "unusual open-interest build" list, not the vendor's filter.
   Feeds section 7 and three multi-signal tags. **Deferred** with the other paid data (Phase 0).
2. **Analyst revisions — medium, paid.** No free source gives firm and price-target change.
   Benzinga's ratings through Massive ($99 add-on) carry firm, old and new rating, and old and new
   target back to 2011 — the report's fields exactly. FMP's $19 Starter has rating changes but no
   per-firm target change, and needs a separate licence for display. **Deferred** (Phase 0).
3. **The stock universe and sector map — medium, definitional.** The report's universe is not the
   S&P 500 — the Sept 25 tables include TWLO, OKTA, NET, DDOG, ZS, CLS and a dozen ADRs (TSM, SONY,
   INFY, NMR, MFG, SMFG, ITUB, KB, SHG, TD, BN, DB). Nor is it screened on option volume alone: OCC's
   volume file shows NMR, SHG and KB trading 16, 6 and 2 contracts on Sept 25. **Its sectors are
   Yahoo's (Morningstar's):** ADP, UBER and PAYX come back Technology / Software - Application and
   GRMN Technology there, with "Basic Materials" as a sector, while the Nasdaq screener puts UBER in
   Consumer Discretionary and PAYX and GRMN in Industrials. The only scripted route to Yahoo's
   sectors is unofficial and personal-use only, so a hand-kept, versioned sector file seeded once is
   the likely answer. The Sept 25 table below is the test.
4. **Fundamentals score — medium, definitional.** SEC EDGAR's company facts are free (at most 10
   requests a second, with a User-Agent naming a person and an email; none gets a 403). The ADRs are
   covered through the `ifrs-full` taxonomy — TSM, SONY, INFY, TD and BN checked — but in local
   currency, mostly annual, and sometimes a filing behind. What "strong fundamentals" means is ours
   to write; a Piotroski-style score is the obvious start.
5. **Support and resistance levels — medium, approximate.** Deterministic, no new data, but the
   part of the build most likely to disagree with theirs.
6. **Headlines and macro narrative — agent side.** Stays outside every package, in
   `scripts/morning_narrative.py`, fed by headline sources — see "Sources, verified".

Everything else is small: see Phase 1.

## Sources, verified

Researched 2026-09-27. **Fetched** means a real request returned real data that day; **docs**
means documentation or search results only.

| Need | Source | Status | Notes |
|---|---|---|---|
| Daily bars | Dolt `post-no-preference/stocks`, table `ohlcv` | Fetched | **Raw, not adjusted** (NVDA's 10:1 split shows as a cliff). Latest bar 2026-09-25, committed 05:30 the next morning. ADRs and ETFs present. The hosted API times out on broad queries; use a local clone, as the earnings module does. |
| Dividends, splits | Same database, tables `dividend(ex_date, amount)`, `split` | Fetched | Enough to apply the proportional adjustment ourselves — which makes the adjustment rule ours and written down. |
| Earnings calendar | Dolt `earnings.earnings_calendar` | Fetched | Already wired in; rows to late October. Nasdaq's calendar API (`api.nasdaq.com/api/calendar/earnings?date=`) as a cross-check. |
| IV rank | Dolt `options.volatility_history` | Fetched | `iv_current`, `iv_year_high`, `iv_year_low` — the platform's min–max definition directly. About 1,536 names a day; **none of the 12 ADRs**. |
| Optionable list | Cboe symbol directory CSV (`cboe.com/us/options/symboldir/equity-index-options/download/`) | Fetched | 5,330 names, all 12 ADRs included. |
| Option volume by underlying | OCC volume query CSV (`marketdata.theocc.com/volume-query`) | Fetched | 4,397 underlyings a day; the liquidity screen. |
| Per-strike OI, volume, bid/ask | Cboe delayed chain JSON (`cdn-api.cboe.com/api/global/delayed_quotes/options/{SYM}.json`; indices take a `_` prefix) | Fetched | End-of-day option data for the whole universe without touching the streamer's budget; the IV-rank route for names Dolt lacks. |
| Yields | Treasury daily par curve CSV (`home.treasury.gov/.../daily-treasury-rates.csv/{YYYY}/all?type=daily_treasury_yield_curve&field_tdr_date_value={YYYY}&_format=csv`) | Fetched | Posted by about 18:00 ET, a day ahead of FRED. FRED's keyless `fredgraph.csv?id=DGS10,DGS2,DGS30&cosd=` as fallback (sometimes answers with a ZIP). |
| Pre-market 10Y | Cboe TNX delayed quote JSON | Fetched | Quoted as yield × 10; first reading about 08:20 ET, just ahead of the 08:30 build. |
| SKEW, VIX, VVIX, VIX3M history | Cboe `daily_prices/{SYM}_History.csv` | Fetched | Current to 9/25 (SKEW 144.91). Answers with a 307 to `cdn-api.cboe.com` and starts with a byte-order mark. |
| Economic calendar | BEA `apps.bea.gov/API/signup/release_dates.json`; FRED `releases/dates` (free key) | Fetched | BLS blocks scripts (403); Census is HTML only. **No free source gives consensus estimates.** |
| FOMC dates | federalreserve.gov calendar page | Fetched | HTML; a hand-kept yearly table is simpler. |
| Headlines | Fed press RSS, CNBC, MarketWatch and WSJ Markets RSS, GDELT DOC API | Fetched | Reuters RSS is dead. Store title, link and time only. |
| Futures fallback | — | Fetched | Stooq and CME no longer answer scripts; Yahoo does but against its terms. The broker feed stays the only source. |
| Fundamentals | SEC EDGAR `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`, nightly `companyfacts.zip` | Fetched | See gap 4. |
| Flow, analyst ratings | ThetaData, Benzinga via Massive, FMP | Docs | Prices and fields from their pricing and docs pages; see gaps 1 and 2. |

**The asset-class benchmark is AOR** (decided 2026-09-27; see Phase 0). AOR (iShares' 60/40 allocation
fund) is in the Dolt clone and VBINX is not; the creator of relative-rotation charts is reported to
have used VBINX (Vanguard's 60/40 fund) for asset classes, but that rests on a single 2020 search
result.

**A near-the-money skew reading, labelled as such.** Cboe's file supplies SKEW. The 25-delta risk
reversal is a different measure and worth carrying beside it, but at a constant 30 days — by
interpolating total variance between the two expirations either side of 30 days — rather than on
the nearest expiration as the recorder does now.

## Charting sources, and what each one is for

More than one charting source can be used, as long as each does a different job. Mixing them up —
scoring our engine against another tool's levels, say — would measure agreement with the wrong
thing.

| Source | Role | Why |
|---|---|---|
| The vendor's chart pages | **The fixture.** The only place its levels, 1–10 score, trend grades and named signals exist. | Collected daily (Phase 0b). Everything we build is scored against these. |
| Dolt bars (Phase 2) | **The input.** Every indicator and level of ours is computed from them. | Deterministic, rebuildable, covers the universe. |
| tastytrade (DXLink daily candles) | **A cross-check of the input.** The streamer already backfills daily candles for its own symbols. | Confirms Dolt's bars and adjustment on a small panel. Not a universe source: the streamer can't carry hundreds of names. Its in-app charts are pictures of the same bars and add nothing. |
| TradingView (the website) | **A one-off sanity check,** by eye. | Its CCI(20) and RSI(14) on a named ticker confirm our formulas: typical price, mean deviation and the 0.015 constant are easy to get subtly wrong. Its automatic support/resistance tools are community scripts with rules of their own, so they'd be a third opinion, not the vendor's; its scripts only run inside TradingView; and scripted access to the site is against its terms. It is not a data source. |
| TradingView's `lightweight-charts` library | **The render.** Apache-2.0, and already the console's chart library (`packages/console/package.json`). | Phase 7 draws our rebuilt chart — candles, our levels, the CCI panel, our signal marker — beside the vendor's values for the same name, so a disagreement is visible, not just a number in a table. |

RSI and CCI never need a chart: they are pure functions over daily bars, computed in the engine.
Charts are for the two things that are not formulas: the vendor's levels (the fixture) and a
human looking at where ours differ.

## Prior art on GitHub

Searched 2026-09-27. Only MIT, BSD and Apache code can be copied; GPL, AGPL and unlicensed code is
for reading. **Nothing public reproduces the platform's own scoring** — its 1–10 rank, its trend
grades or its levels — so those are reverse-engineered from its output, as above.

| For | Repo, file | Licence | Use |
|---|---|---|---|
| Swing pivots | `joshyattridge/smart-money-concepts`, `smc.py` `swing_highs_lows` | MIT | Port. Symmetric ±N-bar fractal, then forces highs and lows to alternate, keeping the more extreme. |
| Level clustering | `day0market/support_resistance`, `pricelevels/cluster.py` | None | Ideas only. Zigzag or extremum prices, agglomerative clustering by distance or percent, median per cluster, levels scored by touches. Easy to re-implement. |
| Rotation | `xang1234/stock-screener`, `backend/app/services/rrg_service.py` | Apache-2.0 | Port. Weekly RS, EMA-smoothed, trailing z-score centred on 100; momentum as the z-scored rate of change; quadrant on the 100/100 cross. Weekly only — daily mode is ours. |
| RS rank | `skyte/relative-strength`, `rs_ranking.py`; `xang1234/stock-screener` `percentile_ratings()` | Apache-2.0 | Reuse. IBD weighting (0.4 × 3-month + 0.2 each for 6, 9, 12) and a 1–99 percentile with ties averaged — a candidate for the platform's cross-sectional rank. |
| Trade signing | `jktis/Trade-Classification-Algorithms`, `classifytrades.py` | MIT | Reuse. Quote rule, Lee-Ready and better variants, with timestamp interpolation for trades and quotes that don't align. Built for equities, unmaintained. |
| ThetaData flow | `mastermindx-market-intelligence/macro`, `engine/tape_flow.py` | None | Ideas only: calls and puts need separate requests (`right=*` is refused); sign by mid, tick test at the mid. |
| Spread detection | `cobriensr/Options-Strike-Calculator`, `multileg_assembler.py` | GPL-3.0 | Ideas only: groups prints into spreads within 90 s. Matters because a spread leg can look like a large naked put sale. |
| EDGAR facts | `dgunning/edgartools` | MIT | Dependency or reference for concept mapping, `ifrs-full` included. It removed its own Piotroski module as broken; key facts on period end and duration, never on the filing's fiscal year. |
| Fetchers | `NomaDamas/k-skill`, `multi-asset-morning-briefing/scripts/market_data.py` | MIT | Reference for the Treasury, FRED and Cboe quirks above. |
| Analyst endpoints | `daxm/fmpsdk`; Massive's `client-python` `rest/benzinga.py` | BSD-3; MIT | Endpoint paths. Normalising grade vocabulary is ours. |

Nothing found for: a rule placing resistance above an all-time high, five-grade trend ratings,
a Piotroski score that handles `ifrs-full`, flow with sweep and block detection, or a
constant-maturity 25-delta risk reversal. Each is small enough to write.

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

**Decided 2026-09-27: a new package, `packages/technicals`** (`cherrypick.technicals`, store
`~/.cherrypick/data/technicals/`), rather than a module in `cherrypick.core`. A package gets its
own store, CLAUDE.md and tests, which suits a job that runs after the close and writes several
hundred rows a day; core suits a library several packages call. The name says what the engines are
— technical readings over daily bars — and keeps it distinct from `overview`, which assembles the
morning pack and reads this package's output. The collected inputs stay where their scripts write
them (`~/.cherrypick/data/market-report/`: the vendor editions and charts, the universe and
sectors), and the package reads them from there: the writers stay in `scripts/`, and moving live
stores under running jobs buys nothing.

## Plan

Each phase ends with something that runs and a test that has been shown to fail.

### Phase 0 — decisions, before code

- **Paid data: deferred (decided 2026-09-27).** Flow (ThetaData, $80/month) and analyst revisions
  (Benzinga via Massive, $99) are not bought for now, so the first build runs on free inputs only.
  What that rules out: the flow section (section 7) entirely, and four of the nine multi-signal
  methods — call buying, put selling, put buying and analyst revisions. What stays comparable
  against the vendor: the relative-strength stage, sector rotation, the two fundamental checks and
  the trade scan. Our multi-signal score is therefore built over those methods only, and scored
  against the vendor **per tag** rather than as a total, since a total over five methods cannot be
  compared with one over nine. Phase 6 waits on this decision. If it is revisited: whether a
  vendor's pre-classified flow is acceptable or the buy/sell rule must be ours, and each vendor's
  storage and redistribution terms, are checked before building on it.
- **The stock universe: decided 2026-09-27, being built.** Candidates are every ticker the saved
  vendor editions link (454 over Sept 21-25, the breadth table included) and every underlying the
  tastylive follow feed trades (110 from the first harvest; futures dropped). A candidate is kept
  only when it is **very liquid by measurement**: the stock's spread within 0.05% of mid or one
  cent; the worse of its at-the-money call and put about 30 days out within 3% of mid or five
  cents (both read from tastytrade REST quotes inside regular hours); and a median of at least
  10,000 option contracts a day over the last ten sessions, from OCC's daily volume file (one CSV a
  session covers every underlying; OCC counts both sides of each trade, so contracts are half its
  total). Each needs at least three sessions; fewer is *pending*, never a pass.
  **tastytrade's liquidity rating is a guide, not a gate** (same day): it is recorded beside each
  verdict and decides nothing, because it and actual volume disagree too often to screen on — on
  Sept 25 DELL (266k contracts), COST (121k), ARM (113k), GS and LLY all rated 2 while LYG rated 4
  on 22 contracts. `scripts/build_stock_universe.py` (`harvest`, `measure`, `build`) writes
  `universe/universe.json` with every name's reasons, in or out; the supervisor runs it at 11:00 and
  14:00 ET and after the close (`market_report.universe`, off by default; on locally since
  2026-09-27). On the first build, volume alone put 306 of the 493 candidates out and left 187 to
  be measured. The universe deliberately differs from the vendor's, which includes names with
  almost no option volume (NMR, KB, SHG), so the stage counts are compared as rates, not totals.
  The members are mirrored to a private tastytrade watchlist, `cherrypick universe`, by the
  script's `watchlist` step — the suite's one scheduled write to the broker account, a watchlist
  and never an order. It previews unless `--apply`, has its own switch
  (`market_report.universe_watchlist`, off by default), replaces only the list it created (marked
  by the `cherrypick` group), and refuses to strip it to its pins or cut more than half of the
  universe's names at once. SPX, NDX, SPY, QQQ and IWM are pinned: on the list since it was created
  on 2026-09-27, whether or not they pass the rule, and never removed by a sync.
- **The sector taxonomy: match the vendor's, as labelled in the Sept 25 edition (decided
  2026-09-27), and re-evaluate if the vendor changes it.** The editions carry the answer
  themselves: their leaders/laggards table is grouped by sector, so every name listed there
  has the vendor's sector for that day — 387 names across Sept 21-25, no hand file needed for them.
  Sept 25 turned out to be a relabel already: three sectors took GICS names (Consumer Cyclical to
  Consumer Discretionary, Consumer Defensive to Consumer Staples, Financial Services to Financials)
  while Technology, Healthcare and Basic Materials kept Yahoo/Morningstar's; 48 names changed label
  and none changed sector. So the canonical labels are Sept 25's, the older ones are mapped onto
  them, and `build_stock_universe.py build` rebuilds `universe/sectors.json` from every edition each
  evening, warning when a label appears that is neither canonical nor a known rename, or when a
  name moves sector — the trigger to re-evaluate. Stocks no edition has listed yet (AAPL, NVDA,
  GOOGL, LLY and ~40 others on 2026-09-27) fill in as editions accumulate, or by hand in
  `universe/sectors.manual.json`, which an edition overrides and flags when it disagrees. The
  vendor's scanner data carries a third, unrelated taxonomy ("Electronic Technology", "Retail
  Trade") and is not used.
- **The asset-class benchmark: AOR (decided 2026-09-27).** Everything in Phase 2 is
  computed from Dolt's daily bars, and Dolt carries AOR (269 sessions to 2026-09-25) but not
  VBINX, a mutual fund priced once a day that tastytrade does not quote as it does an ETF; every
  other entry in the rotation section is an ETF, so AOR keeps the benchmark on the same kind of
  prices. The case for VBINX rests on one 2020 search result. The difference to know: AOR's stock
  sleeve is global, VBINX's is US-only; a US-only 60/40 could be computed exactly from Dolt's SPY
  and AGG bars if that ever matters.
- **Trade ideas: not for now (decided 2026-09-27).** The vendor's income trade ideas (short puts
  and put spreads on leaders, expiring before the next earnings date) are the part closest to a
  trade ticket, and the report leaves them out. If that is revisited, the suite's only
  discretionary order path is `packages/desk`, and a read-side report that suggests a trade should
  say so.
- **The engines' home: a new package, `packages/technicals` (decided 2026-09-27).** See "Where the
  code goes".

### Phase 0b — collecting the vendor's editions automatically

Every edition saved is another scored day for every engine, and the fixture only grows if
collection doesn't depend on someone remembering to save a page. So the collector comes first.

**Built and run live (2026-09-27):** `scripts/fetch_vendor_edition.py` (`credentials`, `login`,
`edition`, `charts`, `validate`, `probe-chart`), with its checks tested in
`packages/orchestrator/tests/test_vendor_edition_script.py` — each check shown to fail on a
deliberately broken input, the pacing floors pinned, and all five saved editions passing. A live
`edition` run re-fetched Sept 25 byte-identical to the hand-saved copy; a live `charts` run
captured ANET and MSFT. The supervisor schedules it (`report-edition`, `report-edition-retry`,
`report-charts`), off unless `market_report.collector` is set.

**The chart pages are backed by JSON, and that is what `charts` saves.** Loading a chart page makes
the app fetch, per symbol, a response carrying: the vendor's own daily bars (about three years);
**every** support and resistance level with the date of the bar it was taken from (ANET: six
supports, where the chart draws two — 114.52 included), plus gap support/resistance; the
price-action sentence; the 1–10 `technicalRank`; IV rank and liquidity rank; and a daily history of
the 1M and 6M trends as an integer score from −4 to +4, back to October 2024. The same page load also
returns the day's whole scanner list — 185 names on Sept 25, each with its rule
(`BullishTrendFollowing`, `BearishTrendFollowing`, `BullishCounterTrend`, `BearishCounterTrend`,
`CciDipInBullishTrend`, `CciRallyInBearishTrend`, period 14), its 1–10 rank, sector and market cap.
`charts` saves these as `vendor-charts/<session>/<TICKER>.json` and `trade-ideas.json`, keyed by
the session of the latest bar. Nothing is called directly: the pages are loaded as a person would,
and the responses the app already makes are recorded.

**This settles the adjustment question outright.** The vendor's MSFT bars put the 52-week low at
348.54 on 2026-06-25 and the high at 549.20 on 2025-10-28 — exactly the support and resistance
prices, against the header's raw 349.20 and 553.72. The bars are dividend-adjusted, and the
extreme levels are those bars' extremes on the dates given. The level engine can now be scored on
every level and its source bar, not just the two drawn.

**Pacing is a requirement, not a tuning knob.** The vendor must never see fast consecutive
requests: one browser session per run, at most one login attempt, a randomized 20–45 second pause
between report cards and 30–60 seconds between chart pages (a chart page load makes ~50 requests
of its own), at most 40 chart pages per run, a backfill of at most three editions per
run, and chart pages one at a time with the same pause. Any 429 or 403 response ends the run and
starts a 24-hour cooldown that later runs honour.

- **A `scripts/` job, `fetch_vendor_edition.py`**, outside every package like the other network
  fetchers. It logs in, opens the day's edition and saves the report's HTML to
  `~/.cherrypick/data/market-report/vendor-editions/YYYY-MM-DD.html`. The site's address and page
  locations live in a local config beside that folder, not in the repository, keeping the vendor
  out of the codebase as this document does.
- **A real browser, driven headless.** The dashboard is a script-rendered app (the saved pages
  carry its widget framework's styles), so a plain HTTP fetch returns an empty shell — exactly
  the two empty files found in Downloads. Playwright is the likely tool, as a dependency of the
  script only.
- **Where the editions are.** On the dashboard's default page, the Insights panel's Research tab
  holds an accordion of headers titled "<report name> - <Month D, YYYY>", newest first; the
  whole back list is there, so missed days can be backfilled. Only one card opens at a time, and
  its content loads lazily after the header is clicked: the job clicks a header, waits until
  that card's content is over 5,000 characters, clones the card without its header button, and
  wraps it in a standalone page with the site's style tags and absolute stylesheet links, a white
  background and a 900px width — which is exactly how the five saved editions are built. Backfill
  opens the headers one at a time, top to bottom.
- **The saved file is named `YYYY-MM-DD.html` by the date in its header, and an existing file is
  never overwritten.** A second fetch of a day already on disk is logged and skipped, so a
  re-run can't replace a good edition with a worse one.
- **Credentials in the OS keyring,** through `cherrypick.core.auth.credentials` under its own
  service name, set once with the same prompt-and-store flow the broker credentials use. Never in
  the config, the environment or a log line. The browser's session cookies are kept in a profile
  folder under the same store so most runs reuse the session instead of logging in again.
- **It stops rather than fights.** The login page carries captcha styles. If a challenge appears,
  or a login is refused, the job does not retry or try to get round it; it saves nothing, sends a
  notification through the existing notify stack, and the edition is fetched by hand that day.
- **A saved file must prove it is an edition** before it counts. Its dateline ("FRIDAY, SEPTEMBER
  25, 2026") must match the header's date; its "Covers <prior day>'s closing prices" line must be
  there; its text must end with the disclaimer footer, so a card cut off mid-load fails; and its
  colour-decoded leader and laggard totals must equal its stated counts — the check that
  validated the first five. A file that fails is kept aside as `.rejected`, never as the day's
  fixture.
- **When the script stops at a challenge** (a captcha, a changed login, a check it cannot pass), a
  browser-based session follows the same steps by hand and saves to the same folder. That is the
  fallback, not the design: the suite prefers the deterministic script.
- **Scheduled by the supervisor** after the edition is published (the editions say "as of" about
  6:00–6:20 AM ET), with one later retry, the way `refresh_dolt_data.py` is scheduled. It fetches
  only the current day's edition, plus any missing recent days the site still lists, once each.
- **The chart pages are collected too, by the same job, in the evening.** A chart page shows the
  latest close, so a capture taken after that session's close (the data is delayed 20 minutes;
  17:00 ET is safe) pairs with the *next* morning's edition — the same-session pairing the
  directional-score and 1–10-score questions need. The panel is fixed plus variable: the 22 names
  already captured, SPY / QQQ / RSP / IWM, and every name the day's edition mentions (stacked
  signals, top relative strength, the eight largest trades, the earnings trade), capped at about
  forty and fetched one at a time with a pause. The chart pages sit on a different host from the
  dashboard but open under the same login (confirmed 2026-09-27), so one stored credential and one
  browser session cover both; the edition's own ticker links give the chart page's address per
  name. For each name it saves **the page's values as
  data** — last price, 52-week range, 1M and 6M trend, 1–10 score, IV rank, liquidity class,
  earnings date, dividend, every support and resistance price, the named signal and the
  price-action sentence — as `vendor-charts/YYYY-MM-DD/<TICKER>.json`. The page loads all of this
  as JSON (see "The chart pages are backed by JSON" above), so that response is what is kept: exact
  numbers rather than rounded display, and each level with the date of its source bar, which makes
  a screenshot unnecessary. A capture that
  doesn't parse (a missing price, a level that isn't a number, the wrong ticker) is rejected like
  an edition that fails its checks.
- **The subscription's terms were checked (2026-09-27):** they say nothing about automated
  access. The pacing rules above are the answer to that silence: the collector reads as a person
  would, never faster.

### Phase 1 — quick wins inside the existing posture

**Landed 2026-09-27: the daily files and the readings built on them.**
`scripts/fetch_market_files.py` fetches Cboe's SKEW/VIX/VVIX/VXN histories, Treasury's par curve
and BEA's release dates each evening (job `market-files`, 18:45 ET, with a 07:45 retry), and the
pack (v4) reads them: SKEW and VXN percentiles from Cboe's files, the weekly expected move and
10-session realized volatility, the prior session's Treasury curve with spreads and changes, and
the coming week's releases. FRED's keyless CSV hangs from here, so FRED is reached only through
its API, whose free key also brings CPI, jobs and PPI dates (BLS refuses scripts); until a key is
stored the calendar carries BEA and FOMC only (a key was stored the same evening, bringing CPI,
jobs, PPI and claims).

**Landed the same evening: the rest of Phase 1, at 27 subscriptions.** The 30-day 25-delta risk
reversal needs no subscription at all: Cboe's delayed SPX chain carries IV and delta for every
strike, and the evening fetch reduces it to one row (Friday: -3.49 vol points). The pre-market tape
is nine quote-only legs — /ES /NQ /YM /RTY /CL /BZ (active months, resolved by
`refresh_futures_contracts.py`) and NDX, DJX, IWM — and the producer went from 6,025 to 6,052
subscriptions, with no reconnects across three restarts watched live. RUT is replaced by IWM,
labelled a proxy: the stream delivers nothing for the RUT index though REST quotes it. **Phase 1 is
done.**

- Futures legs for /ES, /NQ, /YM, /RTY, /CL and /BZ, extending `scripts/refresh_futures_contracts.py`
  the way it already resolves VX and ZN; overview reads them as the pre-market tape. The producer
  starts at 07:00 and the pack builds at 08:30, so these are live readings.
- NDX, DJX and RUT (or QQQ, DIA, IWM) as quote-only legs; RSP into the pack (already recorded).
- A yields fetcher in `scripts/`: Treasury.gov's daily par yield curve file, FRED `DGS10`/`DGS30` as
  the fallback. Also worth one probe of TNX on the feed; it has never been tried (Cboe's delayed
  TNX starts only about 08:20 ET).
- Cboe's daily SKEW history file, replacing the intermittent stream reading for percentiles; VVIX
  history kept.
- Weekly expected move (VIX ÷ √52) and 10-session realized volatility in the pack; VIX, VVIX,
  VXN and SKEW as percentiles of their past year.
- A 30-day constant-maturity 25-delta risk reversal beside SKEW, under its own name.
- An economic release calendar from BEA's release-dates file and FRED's release-dates API, beside
  the FOMC dates already there. No consensus estimates.

Each new leg is a subscription decision; add them together, outside market hours, watching the
producer — the rule `packages/overview/CLAUDE.md` already states.

### Phase 2 — the end-of-day store

**Landed 2026-09-27 as `packages/technicals`.** Raw bars, splits, dividends and Dolt's IV history for
the 497 candidates, ETFs and benchmarks Dolt carries (368,904 bars back to 2023-09-25; SPX, NDX and
VIX are not in Dolt), landed at 06:15 ET after the Dolt pull, in ~13 s a morning. Adjusted bars are a
pure function over raw and reproduce the vendor's MSFT closes on all 753 sessions exactly (ANET to a
cent, pre-split); `check-vendor` repeats that over every capture. IV rank reads Dolt's
`volatility_history` where it covers a name. Still to do from this phase: ATM IV from Cboe's delayed
chain for the names Dolt's IV history lacks, ranked once a year has built up.

- Bars from the Dolt `stocks` clone, which passed the assessment (ADRs present, last night's bar
  committed by 05:30). Split and proportional dividend adjustment applied by us from its own
  tables, as a pure function, so the adjusted series can be rebuilt from raw at any time.
- The universe file and the sector map, both built by `scripts/build_stock_universe.py` (see
  Phase 0); the sector map comes from the editions themselves, with a hand-kept file for gaps.
- IV rank from Dolt's `volatility_history` where it covers a name; for the rest, record ATM IV
  daily from Cboe's delayed chain and rank it once a year has built up.
- A `scripts/` job that pulls the clone and lands one day's bars for the universe and the ~35
  rotation ETFs into the store. Backfill a year once.

### Phase 3 — the stage and rotation engines

**The stage rule landed 2026-09-27** (`packages/technicals`, `stage.py`, scored by `score-stages`).
The footnote's definition was not the whole rule: **a name is listed only on a day its own one-day
move against the index agrees with its side** (BKNG, a confirmed laggard on every window, was absent
the one day it beat the index), and adding that condition cut wrongly listed names from 859 to ~295
over the five editions. Fitted on those five: 10/30/63 sessions, margins 1%/2%/2%, against
dividend-adjusted SPY — 97% of the vendor's 980 listings on the same side, the same stage on 82% of
those. The remaining ~30% extra is not reachable by any margin, which says the vendor's universe
varies by day; so the sharp test below ("the same 33 and 131") cannot hold as totals and is replaced
by per-name side and stage agreement, with counts compared as rates. The rule is declared and
re-scored as editions accumulate, not tuned further on five days.

**Rotation and the breadth history landed the same day.** Rotation is the relative-rotation quadrant
of a slow (63-session) and fast (10-session) relative trend, against SPY or, for the nine
"Asset" funds, AOR, with 3%/1% neutral bands: the vendor's exact state for 91 of 119 placements
(76%). The breadth history is the stage rule run per session: against the sixteen sessions read off
the vendor's charts (Sept 2-24), eleven never used in fitting, the bullish share correlates at 0.93
(leaders 0.78, laggards 0.90) — the out-of-sample check that the stage rule is the vendor's. Our
counts run higher, for the universe reason above. **Phase 3 is done.**

- The stage rule over 1/2/3-month relative performance against SPX. **Validated against the Sept 25
  fixture below**: same universe, same session, the same 33 and 131. Each saved report adds a day.
  The saved editions carry every name's stage in its ticker colour (decoded totals match each
  edition's stated counts), so the check covers counts, membership and all three stages per name.
- Rotation over the ETF list, daily and weekly relative-strength trend (RS-Ratio and RS-Momentum
  as trailing z-scores; see "Prior art"), four states plus none; Asset entries against a
  stock-and-bond benchmark (AOR, decided in Phase 0). The trend definition is ours and is
  written down.
- The 2-week breadth history falls out of a backfill, since it is price-derived.

### Phase 4 — the chart layer

**Started 2026-09-27; waiting on captures.** The indicators (SMA, EMA, Wilder's RSI, CCI-14) and a
declared trend baseline landed in `packages/technicals`, with `score-trends` scoring it against
every capture. On the two captures on file the vendor's 1M/6M trend labels agree on ~80% of days,
but two mostly-bullish names cannot settle the construction, the level rule, the 1-10 rank or the
named signals. The collector saves up to 40 names a night from 2026-09-28; each carries ~700 days
of trend scores and every level with its source bar, so the fitting resumes after about a week of
captures, with agreement reported as a rate as below.

**Advanced the same night on 40 captures** (a paced hand run of the collector). Three results:
the **level grid is solved** -- all 192 levels are a 250-session extreme or a point on a grid
anchored at the 250-session low, stepping by the nice number nearest by ratio to the range / 100
(which grid points are drawn is still open; their dates are swing highs); the **1-10 rank** is the
decile of a ~6-month return percentile (Spearman 0.95, within one step on 31-33 of 34); and the
trend baseline holds out of sample (label agreement 79% short, 75% long). On the way, Dolt's
dividends proved wrong for several names (86.8% of prices matched the vendor's), and reconciling
them with tastytrade's history brought 36 of 40 names to 99% or better.

**The trend scores solved 2026-09-29** on 116 captures: `2[close > short SMA] + 2[close > long SMA]
+ [short > long] + 2[close > long WMA] - 3` over 20/50 and 50/200 sessions, -4 on a close below both
and under the lower 20-day Bollinger band. Exact on 99.7% (short) and 99.6% (long) of days on our
own bars, against ~51% for the baseline. The scan rules were refitted on the true scores: 88% of
flagged names, from 84%. Level selection is measured (`score-level-selection`) but not solved.

- Dividend-adjusted daily bars first: the level placement depends on them (see "What the
  platform's charts show"). Phase 2 supplies them.
- The 1M and 6M trends on the five-step scale, from a triple moving average; CCI; RSI; the 1–10
  rank as a cross-sectional percentile (IBD-style weighting is the first candidate), with its bands.
- Levels as swing pivots on adjusted history, snapped to a price grid. Each level in the fixture
  also has a known starting bar on the 6-month chart, so the pivot detector can be checked on
  *which* bars it picks as well as the prices. The sharp test is the exact cents: a rule either
  reproduces them or it does not.
- The two named signals (Bullish Trend Following on CCI, reversal on RSI) and the price-action events (level break,
  gap on high volume, large move); the directional score from trends × strength per the tentative
  rule.
- Scored against the collected chart captures: every level with its source bar's date, the 1–10
  rank, and the 1M/6M trends with their history. Agreement is reported as a rate, not assumed.

### Phase 5 — calendar and trade ideas

**Done 2026-09-28.** `scripts/fetch_earnings_moves.py` prices, each evening, the post-event straddle
of every stock the report covers with an announcement in the next seven days (the earnings module's
expiration rule, the suite's 0.85 x straddle expected move, the session close as spot), and the
morning pack lists them beside the release calendar. The first week: MTN ±8.5%, CCL ±6.3%, MU ±7.3%,
ACN ±6.7%, NKE ±6.9%. No trade ideas, per Phase 0.

- Earnings in the next week with implied move (the earnings module already computes it) and the
  release calendar from Phase 1.
- No income trade ideas (Phase 0: not for now).

### Phase 6 — paid inputs (deferred)

Not scheduled: paid data was deferred on 2026-09-27 (Phase 0). The report ships without it; this
is the work if that changes.

- Flow: the filter as defined above, per sector and fund lean, the eight largest trades, the
  session totals, and the three multi-signal tags as per-name session sums (see the multi-signal
  table).
- Analyst revisions and the fundamentals score, completing the multi-signal table.

### Phase 7 — the render

- The new sections in the pack and on the console's Morning tab. **Done 2026-09-28.** The
  technicals package writes one report per session (`technicals report`, the `technicals-report`
  job at 06:30 ET after the landing): breadth, stages by sector, rotation, leaders and the scan
  signals. The Morning tab shows it beside the pack's v4 blocks (pre-market futures and indexes,
  expected move and realized vol, the Treasury curve, the 25-delta risk reversal, the VXN/SKEW
  percentiles with their source, the week's releases and earnings with implied moves). Building
  the leaders list exposed two data defects in Dolt, fixed before adjustment: duplicate splits
  (APH, CNQ) and tickers carrying another security's history (BNY, SPCX, HUT).
- A chart view per name in `lightweight-charts`: our candles, levels, CCI and signal marker, with
  the vendor's captured values for the same date beside them. **Done 2026-09-28**
  (`/reports/chart?symbol=X`, from `technicals/chart.py`). It draws our grid's extremes rather than
  levels we cannot yet select, and marks each vendor level placed or not: on the first run our grid
  placed 343 of the 358 support and resistance levels across 69 captured names. All 15 misses are
  on four names (ALC, BAP, CCJ, ENB) whose bars agree with the vendor's on under 30% of prices -- a
  bar problem, not a grid one.
- A headline feed to `scripts/morning_narrative.py` for sections 3 and 8's prose. **Done
  2026-09-28.** Section 3's movers are now data (`movers` in the technicals report, version 2: the
  session's eight largest gains and losses with volume against each name's 50-session average);
  `scripts/fetch_headlines.py` (08:45 ET) stores titles from six verified RSS feeds; the narrative
  reads both beside the pack and takes section 8 from the pack's own calendar rather than the web.
  The first dry run named reasons for five movers from reporting and marked eleven unexplained.

## Fixtures from the 2026-09-21 to 09-25 editions

Five editions, received as the report's own HTML. Report dated D covers D−1's close. The
format changed on Sept 24: the older editions have Market Recap / Drivers / Rotation / Leadership /
Flow / Earnings / Stacked Signals / Catalysts, with a four-method stacked table; Sept 24 and 25
have the headline read, scoreboard and nine-method table. Sector membership is Morningstar's
throughout; the labels are Morningstar's ("Consumer Defensive", "Consumer Cyclical", "Financial
Services") up to Sept 24 and GICS-style ("Consumer Staples", "Consumer Discretionary",
"Financials") from Sept 25 — a relabel only, the names in each sector did not move.

**The stage of every name is in the HTML.** Each ticker in the leaders/laggards table is coloured
with one of six fixed hex values — #1B5E20 confirmed outperform, #2E7D32 building, #43A047 early
breakout, #7A0030 confirmed underperform, #C60651 building, #E5384F early breakdown — so the stage
the screenshots could not show is exact for every name in every edition received as HTML. Decoded
by colour, each edition's leader and laggard totals match its stated counts exactly (22/140,
30/220, 41/183, 36/144, 33/131), which is the check that the decoding is right. The raw files are
the fixture; they are kept outside the repository, one file per edition named by its date, in
`~/.cherrypick/data/market-report/vendor-editions/` (Sept 21–25 so far). A saved edition with no
report body — only the page shell — is not a fixture and is left out.

**Daily breadth, Sept 2–24.** Each edition's two-week bar chart encodes the daily counts as pixel
heights on a stated scale (0.5 px per stock where the axis tops at 220; 110 px ÷ 140 where it tops
at 140). Read off and cross-checked between editions, to ±1 where only the chart gives it; the
last six are also stated in the prose. That is sixteen sessions to score the stage rule against
before a single new report arrives.

| Session | Out | Under | | Session | Out | Under |
|---|---|---|---|---|---|---|
| Sep 2 | 60 | 95 | | Sep 15 | 60 | 87 |
| Sep 3 | 41 | 89 | | Sep 16 | 31 | 92 |
| Sep 4 | 27 | 64 | | Sep 17 | 31 | 138 |
| Sep 8 | 40 | 100 | | Sep 18 | 22 | 140 |
| Sep 9 | 40 | 121 | | Sep 21 | 30 | 220 |
| Sep 10 | 36 | 96 | | Sep 22 | 41 | 183 |
| Sep 11 | 30 | 115 | | Sep 23 | 36 | 144 |
| Sep 14 | 53 | 74 | | Sep 24 | 33 | 131 |

**Rotation, day by day** (session close; *new* marks the report's "new this session").

| Session | Leading | Improving | Weakening | Lagging (sector/asset funds only) |
|---|---|---|---|---|
| Sep 18 | HACK, PDBC | TAN, JETS, PAVE *new*, XLI *new*, TLT, LQD, UUP *new* | GDX *new*, XOP, XLE, IWM, FDN *new*, XLF *new*, EFA *new* | XLY, XLRE, XLP *new*, XLB, VNQ |
| Sep 21 | HACK, PDBC, SPY *new* | JETS | GDX, XOP, XLE, IWM, EFA | XLU *new*, XLY, XLI *new*, XLRE, XLP, XLB, VNQ |
| Sep 22 | HACK, PDBC, SPY | JETS | XOP, XLE, EFA | XLU, XLY, XLI, XLRE, XLP, XLB, TIP *new*, VNQ |
| Sep 23 | HACK, SPY | KWEB *new*, JETS, UUP *new* | GDX *new*, PDBC *new*, XOP, XLE, COPX *new*, FDN *new* | XLU, XLY, XLRE, XLI, XLP, XLB, VNQ, TIP |
| Sep 24 | see the Sept 25 fixture below | | | |

Industry funds in lagging are omitted above for width: XHB, ITA, XRT and IYT every session; PAVE
and KRE from Sep 21 (*new*); TAN from Sep 23 (*new*, after improving on Sep 18 and no state
between).

## Fixtures from the 2026-09-25 edition

Transcribed from the report as published, so a rebuild can be scored against it.

### Section 6 — leaders and laggards by sector

Nets sum to −98; leaders total 33, laggards 131.

| Sector | Net | Leaders | Laggards |
|---|---|---|---|
| Technology | +6 | P<sup>c</sup>, TWLO<sup>c</sup>, CLS<sup>b</sup>, OKTA<sup>c</sup>, AMD<sup>c</sup>, INTC<sup>b</sup>, NET<sup>c</sup>, HPE<sup>c</sup>, GRMN<sup>e</sup>, DDOG<sup>e</sup>, FTNT<sup>c</sup>, TSM<sup>b</sup>, PLTR<sup>c</sup>, ZS<sup>c</sup>, SMCI<sup>c</sup>, ANET<sup>e</sup>, KEYS<sup>e</sup> | ORCL<sup>b</sup>, PAYX<sup>b</sup>, INTU<sup>b</sup>, SONY<sup>b</sup>, INFY<sup>c</sup>, ADP<sup>b</sup>, UBER<sup>b</sup>, ERIC<sup>c</sup>, IBM<sup>b</sup>, CTSH<sup>e</sup>, HPQ<sup>e</sup> |
| Healthcare | 0 | DHR<sup>c</sup>, TEVA<sup>c</sup>, NTRA<sup>c</sup>, IQV<sup>c</sup>, MTD<sup>c</sup>, A<sup>c</sup>, ILMN<sup>c</sup>, TMO<sup>c</sup>, ISRG<sup>b</sup>, MRNA<sup>c</sup> | SNY<sup>c</sup>, MDT<sup>b</sup>, CI<sup>c</sup>, CVS<sup>c</sup>, CNC<sup>c</sup>, COR<sup>e</sup>, SYK<sup>c</sup>, ABT<sup>b</sup>, ZTS<sup>c</sup>, MCK<sup>e</sup> |
| Communication Services | −3 | META<sup>c</sup>, WBD<sup>c</sup> | TKO<sup>e</sup>, CMCSA<sup>c</sup>, BIDU<sup>c</sup>, TTWO<sup>c</sup>, AMX<sup>c</sup> |
| Consumer Staples | −4 | — | PEP<sup>c</sup>, CL<sup>c</sup>, MNST<sup>c</sup>, ABEV<sup>e</sup> |
| Industrials | −5 | RKLB<sup>e</sup>, AME<sup>e</sup>, ETN<sup>e</sup>, ROK<sup>e</sup> | UPS<sup>c</sup>, XYL<sup>c</sup>, FDX<sup>c</sup>, AXON<sup>c</sup>, GD<sup>c</sup>, PCAR<sup>c</sup>, CMI<sup>c</sup>, RTX<sup>c</sup>, CPRT<sup>b</sup> |
| Real Estate | −7 | — | CCI<sup>c</sup>, O<sup>c</sup>, VICI<sup>c</sup>, EXR<sup>c</sup>, AMT<sup>b</sup>, PSA<sup>c</sup>, PLD<sup>c</sup> |
| Energy | −7 | — | CCJ<sup>b</sup>, EPD<sup>b</sup>, EC<sup>b</sup>, MPLX<sup>b</sup>, PBA<sup>c</sup>, CQP<sup>b</sup>, ENB<sup>c</sup> |
| Basic Materials | −13 | — | KGC<sup>b</sup>, MLM<sup>c</sup>, CRH<sup>c</sup>, VMC<sup>c</sup>, VALE<sup>e</sup>, WPM<sup>e</sup>, MT<sup>b</sup>, B<sup>e</sup>, GFI<sup>e</sup>, RIO<sup>b</sup>, AU<sup>e</sup>, NEM<sup>e</sup>, AEM<sup>e</sup> |
| Consumer Discretionary | −16 | — | HD<sup>c</sup>, MELI<sup>b</sup>, VIK<sup>c</sup>, MCD<sup>c</sup>, LVS<sup>c</sup>, CPNG<sup>c</sup>, DASH<sup>b</sup>, TM<sup>e</sup>, GM<sup>b</sup>, F<sup>c</sup>, LOW<sup>c</sup>, CMG<sup>b</sup>, SBUX<sup>c</sup>, JD<sup>b</sup>, SE<sup>b</sup>, QSR<sup>c</sup> |
| Utilities | −17 | — | WEC<sup>c</sup>, AEE<sup>c</sup>, FTS<sup>c</sup>, NEE<sup>c</sup>, PEG<sup>c</sup>, XEL<sup>c</sup>, EXC<sup>c</sup>, D<sup>c</sup>, DTE<sup>c</sup>, SRE<sup>c</sup>, FE<sup>c</sup>, DUK<sup>c</sup>, ETR<sup>c</sup>, AEP<sup>c</sup>, PCG<sup>c</sup>, SO<sup>c</sup>, AWK<sup>b</sup> |
| Financials | −32 | — | AMP<sup>b</sup>, HBAN<sup>c</sup>, PGR<sup>c</sup>, NMR<sup>e</sup>, AON<sup>c</sup>, RKT<sup>c</sup>, BX<sup>c</sup>, FITB<sup>c</sup>, ARES<sup>b</sup>, APO<sup>b</sup>, DB<sup>b</sup>, AJG<sup>c</sup>, MFG<sup>e</sup>, GS<sup>c</sup>, BAM<sup>c</sup>, MS<sup>c</sup>, MUFG<sup>e</sup>, PNC<sup>c</sup>, SMFG<sup>e</sup>, USB<sup>c</sup>, BN<sup>c</sup>, BBD<sup>e</sup>, WTW<sup>b</sup>, HIG<sup>c</sup>, MTB<sup>c</sup>, KKR<sup>b</sup>, NU<sup>e</sup>, KB<sup>e</sup>, ITUB<sup>e</sup>, SHG<sup>e</sup>, WRB<sup>c</sup>, TD<sup>e</sup> |

Stage, decoded from the edition's HTML: <sup>c</sup> confirmed, <sup>b</sup> building, <sup>e</sup>
early. Leaders: 21 confirmed, 4 building, 8 early. Laggards: 75 confirmed, 32 building, 24
early. The prior session was 36 outperforming and 144 underperforming (net −108). This edition
labels its sectors Consumer Staples, Consumer Discretionary and Financials where the earlier
editions say Consumer Defensive, Consumer Cyclical and Financial Services; the membership did not
change (ADP, UBER, PAYX still in Technology), so only the labels were renamed.

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
Section 4 gives META's put sale as $23M at risk, section 7 as $22M; whether that is rounding or two
trades is unchecked.

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
| XLE | 62.04 | 42.35 – 66.17 | MB | B | 5 | 49 | 60.93, 47.93 | 62.68, 65.78 | **Bullish Trend Following signal** |
| TLT | 79.32 | 79.42 – 92.19 | Br | Br | 3 | 71 | 78.83 | 81.43, 83.23 | **Reversal signal** |

TLT's last is below its stated 52-week low; one of the two is a stale header or a misreading, and
the row is re-checked before it is scored against. ANET's page was re-captured on the platform's
longest view (about two years of daily bars, with both trend-history rows running its full width)
and agrees with its row here in every figure.
