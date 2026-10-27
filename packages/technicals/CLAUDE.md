# cherrypick-technicals — Operational Instructions

> Operating contract for the market report's **end-of-day store and technical engines**. The plan
> is [`docs/market-report-plan.md`](../../docs/market-report-plan.md); the dated fitting record is
> [docs/fitting-record.md](docs/fitting-record.md); suite-wide context is the root
> [documentation index](../../docs/README.md).

One question: **what did every name in the universe do, session by session, on prices adjusted the
way the vendor adjusts them.** The store (Phase 2) and the stage, rotation and chart engines
(Phases 3-4) are pure functions over it.

**Credential-free and network-free.** Reads the LOCAL `dolt sql-server` (the `stocks` and `options`
clones, pulled at 05:30 by `scripts/refresh_dolt_data.py`, outside every package) and, read-only,
the market-report store the scripts write (universe candidates, vendor chart captures, analyst
revision events, Yahoo's adjusted closes). Writes only
`~/.cherrypick/data/technicals/`: `eod.db` and its report/chart files, and the historical study's
`history.db` and `study/` results.

## The store holds raw; adjusted is computed

`bars`, `splits`, `dividends` and `iv` hold what Dolt now states, upserted wholesale so an upstream
correction replaces the old row. Adjusted is `adjust.adjust` on read, so it rebuilds from raw and a
restated dividend changes every reading at once.

**The adjustment is the vendor's, matched not assumed**: proportional dividends (bars before an
ex-date scaled by `1 - D / prior raw close`) and splits (`for / to` on price, inverse on volume).
MSFT matches on all 753 sessions; ANET within a cent where a pre-split division lands on a half
cent. So adjusted prices are stored and returned **unrounded**; rounding is a display decision.
`check-vendor` repeats the comparison; a few OPEN prices differ by tens of cents (the sources
disagree on which print is the open) — a known source difference, not a defect. **`check-vendor`
is historical now**: the captures stopped on 2026-10-23, so it compares against a frozen set and
can no longer see a new event.

**The live tripwire is `check-adjust`** (`adjust_check.py`, 2026-10-27). Each evening
`scripts/fetch_yahoo_adjusted.py` keeps a year of Yahoo's adjusted closes for every stored name
(`market-report/adjusted/yahoo.db`, each name replaced whole, since every dividend restates the
past). Each morning after the landing the check reads the ratio ours / Yahoo's, which must be flat
between corporate actions whatever its level.
- **A step** is the median of the five sessions before against the five from (`WINDOW`), placed
  where the means' difference peaks. It fails beyond `TOLERANCE`, 0.2%, read off the first run (933
  names: flat to 0.0005 on 90%, exactly flat on the median name); near the newest session the
  windows shrink and the bar is 10%, so a split Dolt missed is seen the morning after.
- **A missed move beyond 3x is a series restart, not a step** (`series_break` starts our series
  over on it), so the restart is read from the raw closes (`series_restart`); a test fails without it.
- Each step names its cause from its size and both sides' events within three sessions. **Yahoo
  states a spin-off as a split** of the price ratio (BDX 1272:1000).
- **Three causes never fail, by declared rule**, and are listed: `dividend: ours only` (Yahoo's
  chart misses dividends, 57 names on the first run -- ACN's 2026-01-13 1.63 is in neither its
  events nor its adjusted closes); `small split: ours only` (scrip dividends and small
  consolidations Yahoo does not adjust for: PHG, NGG, FER, TRI, OPEN); excursions (two steps that
  undo each other within ten sessions, no event).
- Everything else fails unless declared with its reason: `KNOWN_DIVIDEND_NAMES` excuses only a
  name's differing dividend amounts (the CAD-dividend names and four ADRs; a split on them still
  fails), `KNOWN_STEPS` one dated step. A Yahoo file more than five days behind the store is `stale`
  and fails, so a fetch that stopped cannot pass as a clean check.
- **First run, 2026-10-27**: 934 names read, all agree once the 30 declared names are set aside.
  Those are real: seven spin-offs we do not model (BDX, CMCSA, CTVA, FDX, HON twice, SPGI), BNY's
  ticker change, and four names where ours was wrong, all fixed the same day: AZN's 2026-02-02 ADR
  ratio change (a 1-for-2 no source of ours carried; `fetch_split_history.py AZN` stored it, with a
  2015 2-for-1 that was missing too), and HON's three dividends, CME's 2026-03-10 variable dividend
  and ITUB's December extraordinary one (now decided by Yahoo, below). After the fixes all 934
  agree with 28 names declared. Shown to fail on the real store: dropping APH's 2026-09-03 2:1 or KLAC's
  2026-06-12 10:1 from a copy flags that date at 2.0 / 10.0 and exits 1.

**Every comparison with a capture adjusts AS OF the capture's last session**
(`store.adjusted_bars(..., as_of=)`: bars, splits and dividends through that day). A capture states
prices as adjusted then; a dividend since re-scales every earlier bar of ours. Compared as of
today, a name that went ex after its capture agreed on 0 bars and placed none of its levels (seven
names on 2026-10-03, CSCO among them, all ~3,010/3,012 once fixed). The chart file, `check-vendor`
and the trend/level scorers all use it; a test fails if the chart file stops.

**Two Dolt defects are corrected before adjustment** (`adjust.dedupe_splits`,
`adjust.series_break`): of two same-ratio splits within 20 days only the one the raw prices jump on
is kept; a one-day move beyond 3x either way (a ticker that changed hands) starts the series over.
Real large moves (GME, MRNA) are kept.

**A split Dolt misses is fetched once, by hand.** Dolt held three of TQQQ's eight splits
(2026-10-01), and tastytrade has no split endpoint. `scripts/fetch_split_history.py SYMBOL` reads a
public split-history page into `market-report/splits/split_history.db` (`split_history`), checking
each row against the clone's raw overnight jump: `verified` 1/0/NULL. `store.splits` adds the
verified and uncheckable rows to Dolt's, and `dedupe_splits` keeps one of any pair both state; a
contradicted row is never applied. Not scheduled — run it for a symbol when its adjusted series
shows a fake split-sized crash.

**Dividends: two sources, reconciled.** Dolt alone matched only 86.8% of prices (gaps, zeros, wrong
amounts). `scripts/fetch_dividends.py` fetches tastytrade's per-symbol history (paced, a week
between refreshes); `dividends.reconcile` merges: same event within three days, gaps filled from
either side, tastytrade's amount when within 3x, and a gross outlier on either side losing to the
name's usual payout (the median within two years, never all history). 36 of 40 then match at 99%+.
**Tastytrade states some names' pre-split history on today's share basis** (AAPL, BKNG, AVGO, WMT,
not the sector funds), so an amount is restated by its later splits only where Dolt confirms it;
exact repeats inside one source are one event, and same-date parts that sum to the other source's
amount are one payout (GFI). **Yahoo breaks a large tie** (2026-10-27): a pair more than 1.2x apart
takes the side Yahoo's amount for the event (`store.yahoo_dividends`, a year of it, put back on its
own date's basis through Yahoo's split list) is within 5% of -- a special payout (CME's annual
variable, ITUB's extraordinary) or a whole history on another basis (HON's tastytrade amounts are
twice Dolt's back to 2009). PBR is excluded by name (`dividends.THIRD_EXCLUDED`): Yahoo mixes its
per-share and per-ADR amounts. The cost is that `check-adjust` cannot flag an event Yahoo decided;
`check-vendor` on every capture is unchanged by it. On 2026-10-17 that took 207 of 218 captured names to
99%+. What still fails is not reconcilable from these two sources: CAD/CHF amounts (SU, ENB, CCJ,
ALC), spin-offs neither models (APTV, LEN, ILMN), and the vendor's own misses (AU, MU). Those names
are left out of fits that need exact cents.

## Reading Dolt: the query shape is the whole cost

`ohlcv` and `volatility_history` lead their keys with `date`, so a symbol-filtered query walks the
table. Read in **month-wide date windows with no symbol filter**, keep wanted symbols in Python.

- **A symbol Dolt does not list is reported (`not_in_dolt`), never planned** — otherwise it looks
  new every run and re-reads three years each morning.
- **Every read is incremental**: ten days behind a symbol's newest bar (so restatements land), three
  years for a new symbol (the vendor chart's depth), IV likewise over a year.

**What it lands** (`symbols.all_symbols()`): every universe **candidate** (not only members — the
stage engine is scored against the vendor's whole table), the 35 rotation ETFs (every fund the
vendor's rotation placed across Sept 21-25, plus XLK, XLV, XLC), the benchmarks (SPY, AOR, AGG,
RSP, QQQ, DIA, IWM), and the newest earnings calendar's optionable names rated liquidity 1 or 2
(`earnings_names`, 2026-10-17: the calendar's read is ours, so it needs their bars). Dolt carries
no SPX; SPY stands in for it in every engine.

**The cash indexes (`symbols.INDEXES`, SPX) are charted, never measured.** Their bars come from the
broker's daily candles, which `scripts/fetch_index_bars.py` writes to `market-report/index/bars.db`
at 06:00 (a session missing from the daily series is rebuilt from hourly candles only when those
reproduce the daily bars they overlap; `source` says which). `land_index_bars` lands them before
Dolt is touched, with no volume. `store.stocks` excludes them, so an index never enters breadth,
stages, ranks or scoring though the universe lists SPX as a candidate; its chart carries no rank
(a decile of stocks) and null volume.

## Solved rules (keep the numbers; re-score, do not re-litigate)

- **Stage** (`stage.py`, `score-stages`; stages decoded from ticker colours by `editions.py`): **a
  channel breakout on the price ratio** (adjusted close / dividend-adjusted SPY). A leader's ratio is
  above every ratio of the prior 20 sessions (early), of the prior 20 and 41 (building), of the
  prior 20, 41 and 62 (confirmed); a laggard's is below every one of the prior 14, 30 and 62. No
  margins, no one-day condition. Refit 2026-10-14, landed 2026-10-27; scored on all 24 editions
  (the answer key ends at session 2026-10-22, so these are final): fit half (10 sessions through
  10-02) 97.3% same side, 97.5% same stage, 5.9% extra; later half (14 unseen) **99.4% side, 97.3%
  stage, 3.9% extra**. The rule before (`EXCESS_RULE`: excess returns over 10/30/63 sessions,
  margins 1/2/2%, plus the one-day move on the same side) scored 97.6/82.4/73% and 98.3/78.1/88%.
  The "~30% structural extras" once put down to the vendor's universe varying by day were the
  rule's. Reading "each of the prior N" as the one ratio N back loses (94% side, 77% stage, extras
  2x listings, later half). `score-stages --rule excess` re-scores the old rule.
- **Rotation** (`rotation.py`, `score-rotation`): the daily quadrant (refit 2026-10-17). Relative
  strength against SPY, or AOR for the 13 funds the vendor types "Asset"; its level is relative
  strength over its own 63-session average, its momentum that level against itself 10 sessions back,
  each read against 1, and every fund is placed (the vendor's daily view has no neutral band). 411 of
  496 placements exact on 18 editions (83%); out of sample, 80% of 163 and 93% of the sector funds.
  The banded rule before it (`BANDED_RULE`, 3%/1% bands) scored 55% and ran weeks late.
  `editions.decode_rotation` finds headings **by markup, never by phrase** (the paragraph above
  uses the same words).
- **Breadth** rebuilds the daily chart from prices: over the 24 edition sessions our bullish share
  (every candidate) correlates 0.98 with the vendor's stated counts (leaders 0.997, laggards 0.95);
  the excess rule managed 0.91 (0.90, 0.85) on the same sessions, 0.93 on the sixteen of Sept 2-24.
- **Indicators** (`indicators.py`): SMA, EMA, WMA, sd, Wilder's RSI, Lambert's CCI (period 14),
  each undefined until its window is full.
- **Trend scores** (`trend.py`, `score-trends`): `2[close > short SMA] + 2[close > long SMA] +
  [short SMA > long SMA] + 2[close > long WMA] - 3`, 20/50 short-term and 50/200 long, and -4
  outright when the close is below both SMAs and under the lower 20-session Bollinger band
  (population sd, 2 wide). Exact on 99.7% of ~81,000 short-term and 99.6% of ~64,000 long-term days,
  label 99.8%.
- **Sentiment** (`trend.sentiment`): Bullish above both the 50 SMA and 200 WMA, Bearish below both,
  Neutral between — 149/149 on vendor bars, 82/82 on ours. `score-trends` counts a capture whose
  session our store has not landed as not yet landed, never scores it.
  **Trap: captures list daily trend scores NEWEST-first (105 of 121), so `series[-1]` is the oldest
  day. Sort by date first** (`chart.py` does).
- **Level grid** (`levels.py`, `score-levels`): every vendor level is the high or low of the window
  as printed, or a point on a grid anchored at that low with step = the "nice" number nearest **by
  ratio** to (high - low) / 100. **The window is a calendar year, not 250 sessions** (refitted
  2026-10-27, `levels.year_window`): bars dated on or after the same date a year back, both ends
  inclusive, from the day the levels were computed -- for a capture the **UTC** date it was fetched
  (`levels.capture_as_of`; later than its session after midnight UTC and at weekends), for our chart
  the session. A name listed under a year gets all its bars. The 250 came from weekend-fetched 09-25
  captures. Over all 737 agreeing captures it places 3,738 of 3,761 levels (250 sessions: 3,490);
  through 10-02 1,090/1,093 (250: 1,064), after 2,648/2,668 (250: 2,426). ET or exclusive ends
  score lower. Left: ZS and TSM, whose range / 100 (2.224, 2.234) sits just under the 2.236 ratio
  midpoint, so we take 2.0 and the vendor 2.5 -- across every x1.25 step pair the vendor's switch
  falls between 1.107x and 1.112x the lower step, not at 1.118x; not refitted -- and three names
  whose history in our store is shorter than the vendor's (ECHO, P, DRAM). `score-levels --every`
  scores every capture, `--sessions 250` the old rule (`levels.SESSION_WINDOW`).
- **Gap levels** (`levels.gap_edges`): every vendor gap level is an edge of a true two-bar gap whose
  later bar is in the same calendar-year window, dated on the edge's own bar; the TOP edge is gap
  support and the BOTTOM gap resistance, whichever way it gapped. 1,277/1,277 over every agreeing
  capture (250 sessions: 1,276).
- **Rank 1-10** (`levels.rank_score`, `score-rank`): the decile of a name's percentile, across the
  **WHOLE US-listed market** (~8,500 names trading $100k a day, split-affected dropped), of
  `0.5 x 21-session return + 126-session return`. 52 of 62 exact, all within one. The landing stores
  only nine cut-offs per session (`rank_cutoffs`, last 15 sessions); report, chart files and
  `score-rank` all use them, and a session without cut-offs falls back to ranking within the store.
  Not dividend-adjusted across the market (a point or so on six months).
- **Scan rules** (`signals.py`, `score-signals`; refit 2026-10-14, landed 2026-10-27). Trend
  following: CCI(50) > 0, close below the 26 EMA, RSI(14) <= 50.5 (bearish mirrored, >= 50.5).
  Counter-trend: the mean of RSI(10) and RSI(40) <= 35 / >= 70, **options-liquid names only** — our
  proxy for the vendor's liquidity rank (gone with it): >= 200 option contracts a day on our public
  security list (`signals.LIQUID_CONTRACTS`), an unrated name counting as liquid. CCI rally: short
  trend <= -2, sentiment Bearish, CCI(5) from above +100 to at or below it. CCI dip unchanged
  (yesterday's CCI(5) below -100, today's back above, short trend >= 3). On all 17 saved lists (the
  last is session 2026-10-23, so final): fit (5 lists through 10-02) recall 95.0%, precision 79.4%;
  later (12 unseen) **recall 92.5%, precision 81.3%**. The rules before (`FIT_2026_09_29`, trend
  labels and RSI(14)) scored 87.5/65.3% and 86.7/64.9%; `score-signals --rules fit-2026-09-29`.
  **The scorer dates each idea by its `dateOfScan`**, not the folder (three ideas were saved a
  session late), and scores only sessions with a list of their own.
- **Analyst revisions** (`revisions.py`, `score-revisions`): a tag is an upgrade or downgrade, an
  initiation on a side, or a target move of 5%+, from **that session's events only** (every matched
  tag had lag zero). 32 of 37 board tags agree, 1 extra in 77 untagged rows; all five misses are the
  source's (an event Yahoo lacks, or DOCS's target swapped). Events are Yahoo's per-firm history,
  fetched by `scripts/fetch_analyst_revisions.py` into `market-report/analysts/revisions.db`
  (append-only, `first_seen_at`/`last_seen_at`), nightly at 20:15 ET from 2026-10-17 (the
  orchestrator's `technicals-revisions`) for every stored name and every income screener name. An edition with no revision tag on any row
  (2026-10-08) is reported as `method_absent`, never scored. Which firm the tag names is not
  reproduced.
- **The signal board** (`board.py`, `board`, `score-board`, 2026-10-17): each listed stock's count of
  methods agreeing on one side, the board's rule read off the 15 saved boards (every row's tags are
  on its own side, 0 of ~430 against; every row has 3+). Counted: stage, sector rotation (into =
  leading, out = lagging or weakening, on the name's sector fund), fundamentals, fundamentals vs
  price, the trend scan (counter-trend never counts) and the session's revisions.
  - **Fundamentals** is our score (`fundamentals.market_percentiles(..., by_industry=True)`: four
    measures ranked against the name's industry peers, `peer_groups` -- Yahoo's industry, its sector
    where fewer than five -- margin twice) placed across every name with estimates and a price on
    the newest weekly snapshot; compelling is the top quarter, weak the bottom 35%. **Industry peers
    are the user's choice (2026-10-17): a company against its competitors, not an exact match of
    the vendor** -- by sector, banks, insurers and brokers were one group. A bank-specific variant
    (price-to-book and ROE) reproduced no better and was left out.
  - **Fundamentals vs price**: that place against our 1-10 rank -- fundamentals ahead at the median
    or better with rank 6 or less, price ahead at the median or worse with rank 7 or more.
  - On the boards: stage 69/73 found (11 extra in 130 rows), revision 33/38, rotation 89/96,
    fundamentals 74/95 (13 extra; 59/95 at 8 by sector), fundamentals vs price 29/50 (7 extra; under
    half by sector) and scan 9/16 (the two trend-following rules over the session and the one
    before, read off the saved scan lists). Fundamentals, fundamentals vs price and the scan are
    marked approximate; 114 of 130 rows land on the board's side. Not counted: the three flow tags.
    Re-scored on every saved board with the refit stage and scan rules (2026-10-27): stage 114/115
    at 5 extra (109 at 14 before), scan 17/21 at 1 extra (12 at 6 before), 163 rows on the board's
    side (161 before).
  - A tie has no side. The history store is topped up each morning for it (`history land` 06:20,
    `history fundamentals` 06:25 ET); the board is written at 06:45 ET to
    `data/technicals/board/<session>.json`; record-only.
- **Bearing** (`compass.py`, `score-bearing`): our name for the vendor's direction read. It is a
  lookup on (1M trend, 6M trend, score tier 1-4 / 5-6 / 7-10) fitted on the dashboard's security
  list, which `scripts/fetch_vendor_edition.py` keeps once a session in
  `market-report/vendor-securities/`. On the 2026-10-07 list, 90.3% on held-out symbols (strong bull
  95%, strong bear 99%). The label names other than strong bull and bear are ours, inferred.
  `score-bearing` fits on earlier lists and scores the latest; it needs two lists.
- **Income screens' expiry and strike** (`screeners.py`, `score-screeners`; final record, seven
  sessions 2026-10-07 to 10-16):
  expiry nearest 30 days (short puts) / 45 (covered calls, credit spreads) — 100% wherever a chain
  listing was recorded. Strike by Black-Scholes |delta| on each strike's own broker IV at 4%: short
  put nearest 0.40 (91% monthly, 77% weekly), covered call nearest 0.165 (72% / 50%); spread short
  = first strike out of the money (208/209), long nearest 0.25 (65%), credit ≥ 33.1% of the width
  (234/234).
  The misses are one strike either side: the vendor's own chain deltas at its picks centre on 0.400
  and 0.157, so the rule is right and the residual is the IV input and the list's 15:49 ET build
  time against our 19:00 chain. Tried and worse: the broker's own delta, one IV per expiry, the IV
  index, a delta cap or floor, a one-SD move. Returns: mid ÷ share price, × 365 ÷ days (100% of
  short puts; ~98% of covered calls — the rest divide by price less mid, condition unknown).
  **Which names make each list is not here**; the spread list's selection is open. The console's
  fidelity tab that showed these rates was retired on 2026-10-19; the per-session files stay in
  `screeners/`. Nothing reads them now: the income tabs' "ours" strike that read its `picks` went
  with the vendor's lists (2026-10-24, the tabs read `income-lists`), so since 2026-10-27 the job
  follows `market_report.collector`, the only source of a new list. The last session scored is
  2026-10-23.
- **Our income lists** (`incomelists.py`, `income-lists`, 2026-10-19): the short-put and covered-call
  lists the Screeners page shows in place of the vendor's. From the afternoon income chains
  (`fetch_screener_greeks.py income`, the tradable names and the passing reference funds), each
  option priced from its mid (Black-Scholes IV and delta at 4%), the strike nearest 16 delta -- a put
  on the cycle monthly, a call on the reading's covered-call expiry -- kept only within 0.05 of it, the
  history replay's tolerance. Rule numbers are `cherrypick.core.incomerules`', shared with manage.
  The price is the working one, the return over the cash it ties up (strike for a put, share price
  for a call), rows by symbol. **No edge**: the rule is the least-bad of two studies that found none.
  The fetch always quotes the two listed strikes nearest one standard deviation out (`aimed_strikes`),
  so thinning cannot skip the 16-delta strike.
- **Our credit-spread list** (`creditspreads.py`, in the same `income-lists` file from version 2,
  2026-10-24): the four chart setups' open positions entered on one of the last 5 closes, walked over
  the history store as item 17's study walks them, a long setup a put spread and its mirror a call
  spread, on the income chains' `cs` block (the 30-45 day monthly, both sides, unthinned). The short
  is nearest 0.40 delta within 0.05; the pick is the user's rule, the widest long giving 35-40% of the
  width at the working price, null when none does (often). Every row carries the width table item 17
  will score (1/2/3/5 strikes, 0.25/0.5/0.75 of the move). **Untested**: the rule says so until item 17
  runs. No history store, no list (never an empty one).
- **The security list** (`securities.py`, `securities`, 2026-10-17): every listed symbol's type
  (Nasdaq Trader's directory), options (any OCC volume in 20 sessions) and a liquidity rank that is
  **ours**: 2 at 200 contracts and $100M of stock a day, 1 at 2,000 and $300M. The dollar volume
  is the broker's daily candles' (`dollarvolume.py`, written by `scripts/fetch_dollar_volume.py`),
  not Dolt's, from 2026-10-19, so the list builds without Dolt; on that day's list the two agreed
  to a median ratio of 1.0000 and on 889 of 890 rank-1-or-2 names (CTVA the one apart). **The
  candle must be the extended-hours one**: the SDK's default is regular hours, a median 0.40 of
  the consolidated volume, and halved the rank-1-or-2 set; a test pins the flag. Against the
  vendor's 2026-10-16 list: types 98.2%, options 96.7%, its rank-1-or-2 set at 78% precision and
  78% recall — the vendor's rank is not volume alone, and these inputs get no closer. **Which list
  readers get is `cherrypick.core.vendor`'s switch (`market_report.collector`)**: the vendor's while
  it is on and at most four days old, else this one; `securities/current.json` names the choice
  for the console, and `securities.latest` is every Python reader's door. Never read a security
  list from its folder directly. The public list carries no direction read, so the screener greeks
  plan covers both sides of its names.
- **The stage lists** (`stagelists.py`, `stage-lists`, 2026-10-17): the vendor's six stage
  watchlists (Early Breakout ... Early Breakdown) rebuilt from the stage rule on one close,
  **options-tradable names only** (the vendor's are mostly not: 34 of its 98 Early Breakouts on
  10-16). On the vendor's 10-16 lists (the only ones saved), 100% same side and 96% same stage
  with the channel rule (99% and 76% with the excess rule before it, which promoted a name to
  confirmed a step sooner). The vendor's watchlists carry no date, so each session is scored against
  the vendor's filed under it and the one before, to learn which close its evening lists describe.
  Mirrored to tastytrade by hand (`scripts/sync_stage_watchlists.py --apply`), never on a schedule.
- **The earnings calendar** (`earnings_calendar.py`, `earnings-calendar`, 2026-10-17;
  docs/earnings-screener-plan.md): every announcement in the next 30 days, joined from local data
  and the local Dolt.
  - **Date and time:** a confirmed broker date wins, then Dolt's, then a broker estimate. The time of
    day comes from the broker (with its own date), then Nasdaq's public calendar
    (`scripts/fetch_earnings_times.py`), then Dolt; the latter two only for a date within a day. All
    three agreed on every name two of them timed (2026-10-17).
  - **The usual time:** where none gives one, the company's last timed report is kept as a marked
    estimate (`usual_timing`; right 97% of the time on Dolt 2022-26). It never sets the entry
    session or the reaction day.
  - **Direction and strength: OURS** (`our_reads`, the user's choice 2026-10-17): Bearing and our
    1-10 score from the store's bars, read on its last bar, which at 19:10 is the PREVIOUS close (the
    same one the vendor's list describes). A name with no bars has no read, so it drops out of the
    strong-read view. The vendor's values are kept as `vendor_edge`/`vendor_strength` and never shown
    or filtered on.
  - **IV rank: the BROKER's**, and it picks the spread's side against the cut. It can read above 100.
    The vendor's is kept as `vendor_iv_rank`, unused; a name with no broker rank gets no suggestion.
  - **Expected move:** the broker's earnings rule, 0.85 × the at-the-money straddle on the first
    expiry after the reaction day (chosen 2026-10-17), the straddle read off that expiry's IV. One SD
    (price × IV × √(days/365), the vendor's figure; days from the close the price is) is kept beside
    it. The first LISTED expiry can be a monthly weeks out (286 of 649 moves on the 10-20 calendar),
    so each row carries `event_expiry_days` and the tabs flag one past `EARNINGS_WEEK_DAYS` (7) as a
    fortnight's or month's move; the record states its inside-the-move share over those within a week
    too. Which expiry to use is the user's call, unchanged.
  - **Spread:** the navigator spread, as target strikes.
  - **Sector:** Yahoo's (`scripts/fetch_sectors.py`, `paths.yahoo_sectors`), relabelled to the
    vendor's eleven sector names so the spelling never changed, else the vendor's frozen map
    (`fundamentals.sector_sources`). `sector_source` says which. Yahoo has been first since
    2026-10-27, because the vendor's map stopped at its last edition (2026-10-23) while Yahoo's file
    is refreshed nightly. Measured at the switch: of the 521 names both held, they disagreed on one
    (FISV: the vendor's Consumer Discretionary, Yahoo's Technology). One of the report's names (WBD)
    has no Yahoo sector and keeps the vendor's. The calendar, the signal board, the report's sector
    measures (`report._sectors`) and the B5 slices all read this one function. `fetch_sectors.py`
    asks for every calendar name and universe candidate.
  - **Checked against the vendor's page** for Oct 20-21, while its reads still led: its 12 names,
    same date, direction and strength; moves within 0.42 points on average. History found no edge in
    the spread.
- **Out of sample** (2026-09-28): bars 99%+ on 73 of 79 names; the grid places all 375 levels.

## Unsolved — measured, not approximated

- **IV rank does not match, and the formula is not why.** The vendor ranks an IV series neither Dolt
  nor tastytrade carries (correlation ~0.65 either way). `store.iv_rank` is ours first, tastytrade's
  where Dolt has no IV (ADRs), naming which in `source` — **a coverage fallback agreed with the
  user, not a claim to match the vendor**. Fetched by the `technicals-iv-rank` job after the close,
  one reading per symbol per session, liquidity rating included for a later `liquidityRank` look.
- **Level selection** (`level_selection.py`, `score-level-selection`): the report can place levels
  exactly but **cannot pick them, and does not approximate it**. Known: levels sit where price spent
  little time (crossing-profile minima, 2-3x chance; touch counts point the wrong way); dated bars
  are swing highs ~82% (supports included), swing lows 0.5%; a level's identity is its date and its
  price is re-derived nightly on the grid (a histogram of bars overlapping each cell fits best);
  the "stale" AMD and MU sets once read as the vendor not recomputing every name nightly were the
  250-session window -- every AMD and MU capture fits the calendar year from its fetch date -- so
  nothing yet says a set is computed on any day but the fetch's; polarity/role reversal is at chance. Price and date do not
  determine each other by any rule tried (~35-37%). **What would settle it is a fixed panel
  re-captured every evening**, turning it into a differential problem; not more fitting.
  - **Which of its levels the vendor's chart DRAWS is a separate, solved question** (2026-10-03,
    [docs/vendor-view.md](docs/vendor-view.md)): the two nearest of its support list and of its
    resistance list, never a gap level (`chart.vendor_view`, `VIEW_PER_SIDE`). The chart file
    flags each level `vendor_view`; the console shows it beside our own levels, for comparison.
- **Which gaps are drawn**: an unfilled gap mostly needs to be near one current ATR wide (F1 0.91),
  both edges when wider; a crossed gap keeps at most its POST-gap edge; no feature tried says which.
  TradingView's "closed once entered" convention does not fit.

## Outputs (Phase 7)

- **Chart files** (`chart.py`): `data/technicals/charts/<SYMBOL>.json` plus `index.json`, written by
  `report`: last 250 sessions of adjusted bars (the grid's whole calendar year when longer), the
  level grid, CCI 14 and 5, RSI 14, both trend scores, scan matches per session. Where the vendor chart was captured the file carries its levels
  (each marked whether our grid produces it; gap levels marked against our gap edges,
  `chart_version` 2), trend grades, rank and bar agreement. The console's `/charts/technicals` page draws
  **our grid's extremes, never levels claimed as the vendor's** — it is built to show where we
  differ. Per-session matches come from `signal_days` (one pass); a test pins it equal to
  `signals.readings` on every day and fails when the CCI-5 lag breaks. ~15 MB per session, overwritten.
- **Entry/exit setups** (`setups.py`, `chart_version` 6; the reasoning and the measurements are in
  [docs/setups.md](docs/setups.md)): four textbook setups (trend following, pullback, mean
  reversion, breakout) and their short mirrors, each walked over the whole history as one position,
  written into each chart file with the lines their rules read. **No vendor data in them**: the
  vendor only checks our trend scores and rank. Our own support and resistance (`swings.py`: swing
  points, 10 bars either side, the two nearest per side) are the chart's default levels and the
  watchlist's. They are what the chart's arrows show; the scan
  matches are listed but no longer drawn. Not fitted and not scored for outcomes. **SPX's breakout
  volume is SPY's** (`chart.VOLUME_PROXY`, by date), and the file names it in `volume_source`. The
  pullback's RSI is a dip within 5 sessions because on the entry bar it could not fire; the
  parameters are constants in `setups.py`, and changing one changes what every arrow means.
- **Setups watchlist** (`watchlist.py`, [docs/setups-watchlist.md](docs/setups-watchlist.md)):
  `charts/setups-index.json`, written by `chart.write_all` from the chart files it just wrote, so it
  cannot disagree with them. One row per setup position, long or short, open or traded in the last
  20 sessions, with our 1M/6M trend and five-step label, `trend_agrees`, RS (our rank -- a
  whole-market decile, NOT vs SPY), 1M vs SPY in points, our own nearest levels, the dollar volume
  at entry and `options_tradable`. **The study's confirmed rule** (`chart.TESTED`, `mr-300m`) is
  walked with the study's own code into each chart file's `tested` and gets its own rows, marked
  `tested`; they overlap the setup's rows, so a reader shows one kind or the other.
  **Options-tradable** (`tradable.py`) is weekly options plus $100M a day in the stock. It is read
  from each name's own latest IV-rank reading (no older than 10 days); tastytrade's rating is not
  part of it, because the rating marks a name down for its share price.
- **Liquidity verdicts** (`liquidity.py`, `liquidity` command, nightly 18:57 ET) hold a name that
  fails the options-tradable test **illiquid until the monthly sweep** (the first trading day of the
  month). The test's dollar volume is the candle file's for every name (2026-10-19; before, the
  store's bars, else Dolt's). It judges the store's names, the screener names and, from
  2026-10-19, every **liquid optionable** name (`securities.liquid_optionable`: the current security
  list's rank 1-2 optionable names, else the shipped `seed_universe.json`) -- the income lists'
  universe, which a fresh install (no universe builder, no vendor lists) would otherwise lack; the
  IV-rank fetch reads the same names. Every broker-spending script skips those names (universe measurement, the IV-rank fetch,
  the screener spread readings and greeks, the earnings moves), and no listed view names them:
  chart files, the picker, the watchlist, and the report's lists. Their bars stay in the store and
  every count still includes them -- breadth, sector net, rank cut-offs -- because the engines are
  scored against the vendor's tables, which are mostly illiquid names. Benchmarks, rotation funds
  and cash indexes are reference series and are never judged.
- **The historical study** (`history.py`, `universe.py`, `study.py`, `tuning_names.py`; plan and
  reasons in [docs/signal-log-plan.md](../../docs/signal-log-plan.md), Phase 1, analysis plan v2).
  - **`history.db`** is Dolt's whole daily history from 2011, every name, in `eod.db`'s schema. It
    is a research store, rebuildable from Dolt and read by nothing nightly, so `store.adjusted_bars`
    reads it unchanged.
  - **`study run`** scores the eight setups over a universe chosen as of each day from the session
    before (price ≥ $5, 50-session median dollar volume ≥ $20M, on RAW bars). Fills are at the next
    open. The baseline is a seeded random one, 20 same-date and 20 same-name entries held to the
    setup's own exit through `setups.exit_from`. The test is calendar-time and one-sided, with Holm
    across the eight. Costs (Corwin–Schultz spread, 1%/yr borrow on shorts) were declared before the
    first run.
  - **The four tuned setup-sides** (both pullbacks, both breakouts) count only before `TUNING_END`,
    or on names outside the frozen `tuning_names.NAMES`.
  - Results go to `study/history-results-<stamp>.json`.
  - **Round 3** (`round3.py`, `study round3`) scored two infographic setups (Supertrend + Vortex;
    squeeze + RSI divergence), long and short. Each was tested against a random baseline and one
    matched to the state its trigger fires in. Nothing passed (docs/setups.md, "Round 3").
    - Their rules are `setups.STUDIED`: study-only, and kept out of `SETUPS`/`RUN`, so the chart
      and round 1 are unchanged.
    - `Readings` carries the Vortex and RSI divergence they read.
  - **Round 4** (`round4.py`, `study round4`) scored the vendor's relative-strength breakout (price
    and price/SPY at a 21-session high), with and without its 1.5x volume rule. It was tested only
    on the $20M-$300M slice an exploratory look never saw. Both are worse than random entry
    (docs/setups.md, "Round 4").
    - Its rules read SPY as well as the name, so they live in `round4.py`, not `setups.RULES`.
      `setups.STUDIED` holds only their records.
  - **Round 5** (`round5.py`, `study round5`) tested the 1-10 score inside the vendor's
    fundamentals: a low score under compelling fundamentals with a bullish trigger, and the
    mirror for shorts. Each was compared with the same label and trigger at the other scores.
    Nothing passed. A low score was worse on the trend trigger (docs/setups.md, "Round 5").
    - `fundamentals.py` is the score: four measures from Dolt's weekly consensus estimates
      (`estimates`, `quarters` in `history.db`, landed by `history fundamentals`), each a
      percentile within the market report's sector map. That map lists only today's names.
    - The 1-10 score on history is rebuilt as `land.land_rank_cutoffs` builds it live.
  - **Rounds 6 and 7** (`round6.py`, `round7.py`, the frozen `bearing_table.py`): a revision inside
    the strong bearing, and entering a bearing at all, each against its declared baseline. Nothing
    passed; the bearing is a weak filter, not a signal (docs/setups.md, "Round 6", "Round 7").
  - **Dolt's split table misses many splits before 2014** (KO, NKE, GILD, TJX, IBB, BEN, DUK...;
    254 suspects on in-universe days). `history.suspected_actions` flags an unexplained 40%+ jump
    whose opening gap is a clean split ratio. The study excludes positions and draws held through
    one, and entries in the 120 sessions after. Never feed `history.db` to anything that assumes
    Dolt's splits are complete.
  - The `peaks` table (each name's largest dollar-volume day) lets the study skip names that could
    never qualify. Never find it with `GROUP BY` on `bars`: that walks the index and fetches 29M rows
    one by one.
  - **`setups.RULES` / `run` / `exit_from`** are the one implementation of each setup's entry, exit
    and target; `exit_from` must reproduce every exit the walk makes (a test, and an audit on every
    chart name).
- **Report** (`report.py`): one session's stages by sector, 10-session breadth, rotation, scan
  signals, RS leaders and (v2) largest movers with volume against the 50-session average, into
  `data/technicals/report-<session>.json`. **Nothing downstream recomputes them.**

## Scheduling

`technicals-land` (06:15 ET daily, after the 05:30 Dolt pull; config block `technicals`), enabled
with Dolt set up. The sql-server it reads is kept by earnings' declared `dolt_service`, with or
without the earnings module on (`orchestrator.config.dolt_hosts`, 2026-10-19; before, switching
earnings off stopped the server and every Dolt-reading job here). The income lists' inputs --
`-dollar-volume`, `-securities`, `-liquidity`, `-iv-rank` -- read no Dolt and need only the block's
switch.
`technicals-iv-rank` runs after the close on trading days. `technicals-screeners` (06:40 ET,
`screeners_at`) scores every saved screener session with its broker chain, after the previous
evening's late greeks run; a scored session is skipped. It runs only while `market_report.collector`
is on (off since the subscription ended). `technicals-skate` (06:45 ET, `skate_at`)
writes the skate screen's bands from `history.db` (docs/skate-plan.md), then the forward record
(`skate_record.py`: what the tab showed, what happened at expiry, the declared test; a failure there
is reported and never fails the bands).

The rest run daily under the same `technicals` block (times ET, each overridable by its `*_at` key):
`technicals-index-bars` 06:00, `-history` 06:20, `-fundamentals` 06:25, `-report` 06:30,
`-stage-lists` 06:35, `-board` 06:45, `-earnings-record` 06:50, `-adjust-check` 06:55 (with the
landing; exit 1 is the watchdog's failed-job warning); in the evening `-sectors` 18:40,
`-yahoo-adjusted` 19:45 (Yahoo, no credential; both follow `technicals.adjust_check`),
`-dollar-volume` 18:35 (the one that needs no Dolt: it follows the block's own switch),
`-income-lists` 15:10 (after the 14:45 income chains, no Dolt; off with `market_report.income`),
`-earnings-times` 18:45, `-securities` 18:48, `-iv-rank` 18:50, `-liquidity` 18:57,
`-earnings-calendar` 19:10 (and a late pass at 23:40), `-dividends` 19:30, `-revisions` 20:15.

## The skate bands

`skate.py` records history on **split-adjusted, never dividend-adjusted** prices
(`skate.price_bars`): an option's strike stays put when the price drops on an ex-date, so a
dividend-adjusted series would show calls breached that were not. Leveraged, inverse and
volatility funds are dropped by the declared `leveraged_funds.json` (name rule confirmed against
Yahoo's fund category, each entry's `evidence` saying which); its tests fail when a known fund is
removed. An expiry that spans a stock's report is dropped, the shorter ones kept, and the band file
records each name's trailing dividend yield.

**The score** (`skate.score`, `best_expiries`, `select`; skate plan step 2) is read by
`scripts/fetch_screener_greeks.py skate` (jobs `report-skate-quotes-1/2`, 12:00 and 15:05 ET): the
morning's bands snapped to listed strikes and quoted in regular hours; history at the strike read off
the band's curve, implied odds from an IV solved at the mid, skate yield, hard filters (two-sided,
tight, open interest) and soft thresholds as a marker. Each leg also carries the fee to open one
short contract (`cherrypick.core.fees`), the net premium and net yield, and the band's history
window count; these are shown only -- the best expiry, `select` and the forward record stay on the
gross, and ranking by net is the user's decision.

## Tool Reference

| Command | Purpose |
|---|---|
| `python -m cherrypick.technicals land [--symbols ...]` | Land bars, splits, dividends and IV from the local Dolt clones. Incremental; idempotent. |
| `python -m cherrypick.technicals status` | What the store holds, and the last landing. |
| `python -m cherrypick.technicals liquidity` | Judge every name liquid or not from local data; the illiquid are skipped by broker-spending scripts and hidden from listed views until the monthly sweep. |
| `python -m cherrypick.technicals bars SYMBOL [--raw] [--last N]` | Adjusted (or raw) bars and IV rank (ours, or tastytrade's where Dolt has none). |
| `python -m cherrypick.technicals check-vendor [--all]` | Historical (captures end 2026-10-23): our adjusted bars against every vendor capture; non-zero exit on any disagreement. |
| `python -m cherrypick.technicals check-adjust [--symbols ...] [--tolerance X] [--all]` | The live tripwire: steps in the ratio of our adjusted closes to Yahoo's (`scripts/fetch_yahoo_adjusted.py`), each with its date, size, likely cause and both sides' events; non-zero exit on an undeclared step or a stale Yahoo file. |
| `python -m cherrypick.technicals stages [--session D]` | Every candidate's stage on a session. |
| `python -m cherrypick.technicals score-stages` | Stage rule against every saved edition: side recall, stage agreement, extra rate, counts. |
| `python -m cherrypick.technicals rotation [--session D]` | Every rotation fund's state on a session. |
| `python -m cherrypick.technicals score-rotation` | Rotation rule against every saved edition. |
| `python -m cherrypick.technicals breadth [--sessions N]` | Daily leaders, laggards, net and bullish share. |
| `python -m cherrypick.technicals score-trends` | Trend scores vs every capture: exact, label, within-one. |
| `python -m cherrypick.technicals score-levels [--every] [--sessions 250]` | Vendor levels we place (grid and gap edges) on names whose bars agree to the cent; every capture, or the old session-count window. |
| `python -m cherrypick.technicals score-level-selection` | Where vendor levels sit among grid points, each measure against chance. |
| `python -m cherrypick.technicals score-rank` | Our 1-10 rank (stored whole-market cut-offs) vs the vendor's. |
| `python -m cherrypick.technicals score-signals` | Our six scan rules vs every saved scan list. |
| `python -m cherrypick.technicals score-revisions` | The analyst-revision rule vs every saved board: agree, opposite, missed, not in source, extra. |
| `python -m cherrypick.technicals stage-lists [--session D]` | Our six stage lists on one close, tradable names only, in `stage-lists/<session>.json`, scored against the vendor's watchlists filed under that session and the one before. |
| `python -m cherrypick.technicals board [--session D]` | Write one session's signal board (each stock's count of agreeing methods). |
| `python -m cherrypick.technicals score-board` | The board's six methods vs every saved board: found, tagged, extra; rows on the board's side. |
| `python -m cherrypick.technicals securities [--session D]` | The public security list from local files, `securities/current.json` naming the list readers use, and the public list scored against the vendor's of that day where one is saved. |
| `python -m cherrypick.technicals score-bearing` | The bearing lookup fitted on earlier security lists, scored on the latest. |
| `python -m cherrypick.technicals seed-universe [--write]` | Where the income lists' universe comes from tonight (the security list's rank 1-2 optionable names, or the shipped seed when none is built); `--write` regenerates `seed_universe.json` from the newest PUBLIC list -- tickers only, never the vendor's list, never a figure. |
| `python -m cherrypick.technicals income-lists [--day D] [--source FILE]` | Our own short-put and covered-call lists from the newest afternoon income chains: the strike nearest 16 delta within 0.05 (`cherrypick.core.incomerules`), at the working price, earnings against each trade's expiry; from version 2 also the untested credit-spread list (`creditspreads.py`); `income-lists/<session>.json` (`.trial.json` with `--source`). |
| `python -m cherrypick.technicals score-screeners [--rescore]` | The income screens' expiry, strike and return rules vs every saved list with its broker chain; one file per session in `screeners/`, totals printed. |
| `python -m cherrypick.technicals skate [--day YYYY-MM-DD]` | The skate screen's fixed and by-expiry range bands for every options-tradable name, side and weekly expiry to 63 days, with each band's skate curve over history; `skate/<day>.json`. |
| `python -m cherrypick.technicals earnings-record [--today D] [--dry-run]` | The earnings calendar's forward record: each past announcement's move against the expected move (0.85 × straddle) the calendar showed the session before, whether a strong read called it, and the suggested spread's money on its marks; `earnings-calendar/record.json`, rebuilt whole. Reads the local Dolt. |
| `python -m cherrypick.technicals earnings-calendar [--day D] [--dry-run]` | The next 30 days' announcements for the console's earnings tab; one file per session in `earnings-calendar/`, a rerun replacing it. Reads the local Dolt. |
| `python -m cherrypick.technicals study round7 [--workers N] [--count]` | Round 7: entering a bearing (frozen `bearing_table.py`) against random entry; `--count` gives frequencies only. |
| `python -m cherrypick.technicals study round6 [--workers N] [--count]` | Round 6: a revision inside the strong bearing, against the same bearing without one; `--count` gives frequencies only. |
| `python -m cherrypick.technicals history land` | Land Dolt's whole daily history (2011 on, every name) into `history.db`. Incremental. |
| `python -m cherrypick.technicals history fundamentals` | Land Dolt's next-year EPS and sales estimates and quarterly income statements into `history.db` (round 5's inputs). |
| `python -m cherrypick.technicals history check` | `history.db` against `eod.db` on the overlap, to the cent, and price jumps no split explains. |
| `python -m cherrypick.technicals study run [--workers N]` | The historical study under analysis plan v2; writes `study/history-results-<stamp>.json`. |
| `python -m cherrypick.technicals study round2 --stage A\|B [--workers N]` | Round 2's declared improvements, discovery on half A, confirmation on half B. |
| `python -m cherrypick.technicals study round3 [--workers N]` | Round 3: Supertrend + Vortex and squeeze + RSI divergence, long and short, against the random and the matched baseline; writes `study/round3-<stamp>.json`. |
| `python -m cherrypick.technicals study round4 [--workers N]` | Round 4: the relative-strength breakout, with and without volume, on the $20M-$300M slice; writes `study/round4-<stamp>.json`. |
| `python -m cherrypick.technicals study round5 [--workers N]` | Round 5: the 1-10 score inside the fundamentals label, against the same label and trigger at the other scores; writes `study/round5-<stamp>.json`. |
| `python -m cherrypick.technicals report [--session D]` | Write one session's report readings and per-name chart files. |

**Entry context (B4, 2026-10-24).** `entrycontext` tags a ledger's closed trades with SPX against
its 50/200-day, the name's ATR %, relative volume, sector, entry time and holding period, always
as of the close BEFORE the entry session (a test breaks it to `<=` and fails). It reads; nothing
trades on it. The `environment` column is `core.environment`'s label (B8), never a
per-study regime.

**Slice report (B5, 2026-10-24).** `slices` runs `core.slices` over an arm's trades with this
package's entry context: mean R per cell, n, interval, cells under 30 greyed. In-sample description
only; a cell is never a gate, and a good one is declared in `docs/hypotheses.json` and tested.
