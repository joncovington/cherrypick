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

## Engines, outputs and commands

Each engine's fitted rule, every output, the skate bands and the full command reference are in
[docs/reference.md](docs/reference.md). **The solved rules keep their numbers: re-score, do not
re-litigate.** A rule there was fitted against the vendor and scored out of sample. Read its entry
and the [fitting record](docs/fitting-record.md) before changing it, and change it only with a
re-score.

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
`technicals-index-bars` 06:00, `-history` 06:20, `-fundamentals` 06:25 (the fair-value file moved to
packages/fundamentals on 2026-10-29, read here by path), `-report` 06:30,
`-stage-lists` 06:35, `-board` 06:45, `-earnings-record` 06:50, `-adjust-check` 06:55 (with the
landing; exit 1 is the watchdog's failed-job warning); in the evening `-sectors` 18:40,
`-yahoo-adjusted` 19:45 (Yahoo, no credential; both follow `technicals.adjust_check`),
`-dollar-volume` 18:35 (the one that needs no Dolt: it follows the block's own switch),
`-income-lists` 15:10 (after the 14:45 income chains, no Dolt; off with `market_report.income`),
`-earnings-times` 18:45, `-securities` 18:48, `-iv-rank` 18:50, `-liquidity` 18:57,
`-earnings-calendar` 19:10 (and a late pass at 23:40), `-dividends` 19:30, `-revisions` 20:15.
