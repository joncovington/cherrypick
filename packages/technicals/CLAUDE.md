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
the market-report store the scripts write (universe candidates, vendor chart captures). Writes only
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
disagree on which print is the open) — a known source difference, not a defect.

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
name's usual payout. 36 of 40 then match at 99%+. The four foreign issuers that do not (BAP, ALC,
CCJ, AU) are left out of fits that need exact cents.

## Reading Dolt: the query shape is the whole cost

`ohlcv` and `volatility_history` lead their keys with `date`, so a symbol-filtered query walks the
table. Read in **month-wide date windows with no symbol filter**, keep wanted symbols in Python.

- **A symbol Dolt does not list is reported (`not_in_dolt`), never planned** — otherwise it looks
  new every run and re-reads three years each morning.
- **Every read is incremental**: ten days behind a symbol's newest bar (so restatements land), three
  years for a new symbol (the vendor chart's depth), IV likewise over a year.

**What it lands** (`symbols.all_symbols()`): every universe **candidate** (not only members — the
stage engine is scored against the vendor's whole table), the 35 rotation ETFs (every fund the
vendor's rotation placed across Sept 21-25, plus XLK, XLV, XLC), and the benchmarks (SPY, AOR, AGG,
RSP, QQQ, DIA, IWM). Dolt carries no SPX; SPY stands in for it in every engine.

**The cash indexes (`symbols.INDEXES`, SPX) are charted, never measured.** Their bars come from the
broker's daily candles, which `scripts/fetch_index_bars.py` writes to `market-report/index/bars.db`
at 06:00 (a session missing from the daily series is rebuilt from hourly candles only when those
reproduce the daily bars they overlap; `source` says which). `land_index_bars` lands them before
Dolt is touched, with no volume. `store.stocks` excludes them, so an index never enters breadth,
stages, ranks or scoring though the universe lists SPX as a candidate; its chart carries no rank
(a decile of stocks) and null volume.

## Solved rules (keep the numbers; re-score, do not re-litigate)

- **Stage** (`stage.py`, `score-stages`; stages decoded from ticker colours by `editions.py`): early
  on the one-month screen only, building when one and two months agree, confirmed when all three do,
  **plus a fourth condition — the one-day move against the index is on the same side**. Parameters
  10/30/63 sessions, margins 1%/2%/2%, against dividend-adjusted SPY: 955 of 980 vendor listings on
  the same side (97%), same stage on 82%. Fitted on five days, so the declared `StageRule` is
  re-scored as editions accumulate, not tuned further. About 30% extra listings are structural (the
  vendor's universe varies by day), so counts are compared **as rates, never totals**.
- **Rotation** (`rotation.py`, `score-rotation`): the quadrant from slow (63) and fast (10) relative
  trends against SPY, or AOR for the nine funds the vendor types "Asset"; inside either neutral band
  (3% slow, 1% fast) no state. 91 of 119 placements exact (76%), 26 placed where the vendor had none.
  `editions.decode_rotation` finds headings **by markup, never by phrase** (the paragraph above
  uses the same words).
- **Breadth** rebuilds the daily chart from prices: over sixteen sessions (Sept 2-24, eleven unused
  in fitting) bullish share correlates 0.93 (leaders 0.78, laggards 0.90) — the out-of-sample
  evidence the stage rule is the vendor's.
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
- **Level grid** (`levels.py`, `score-levels`): every vendor level is the high or low of the last
  **250** sessions as printed, or a point on a grid anchored at that low with step = the "nice"
  number nearest **by ratio** to (high - low) / 100. All 192 levels placed on names whose bars agree.
- **Gap levels** (`levels.gap_edges`): every vendor gap level is an edge of a true two-bar gap in the
  same 250-session window, dated on the edge's own bar; the TOP edge is gap support and the BOTTOM
  gap resistance, whichever way it gapped. 221/221 on our bars (SGOV excepted).
- **Rank 1-10** (`levels.rank_score`, `score-rank`): the decile of a name's percentile, across the
  **WHOLE US-listed market** (~8,500 names trading $100k a day, split-affected dropped), of
  `0.5 x 21-session return + 126-session return`. 52 of 62 exact, all within one. The landing stores
  only nine cut-offs per session (`rank_cutoffs`, last 15 sessions); report, chart files and
  `score-rank` all use them, and a session without cut-offs falls back to ranking within the store.
  Not dividend-adjusted across the market (a point or so on six months).
- **Scan rules** (`signals.py`, `score-signals`): 245 of 278 flagged names (88%). Trend rules use whole
  labels (Bullish 3..4, Bearish -3..-4), never "trend = -4"; bullish trend-following is RSI(14)
  40..50; CCI rules run on CCI-5; a dip is yesterday's CCI below -100 with today's back above. Both
  saved lists were used in the refit, so there is no unseen day yet; re-score as lists accrue.
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
  the vendor does not recompute every name every night, so any "date to last bar" feature must stop
  at the day the set was computed; polarity/role reversal is at chance. Price and date do not
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
  `report`: last 250 sessions of adjusted bars, the level grid, CCI 14 and 5, RSI 14, both trend
  scores, scan matches per session. Where the vendor chart was captured the file carries its levels
  (each marked whether our grid produces it; gap levels marked against our gap edges,
  `chart_version` 2), trend grades, rank and bar agreement. The console's `/charts/technicals` page draws
  **our grid's extremes, never levels claimed as the vendor's** — it is built to show where we
  differ. Per-session matches come from `signal_days` (one pass); a test pins it equal to
  `signals.readings` on every day and fails when the CCI-5 lag breaks. ~15 MB per session, overwritten.
- **Entry/exit setups** (`setups.py`, `chart_version` 5; the reasoning and the measurements are in
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
  whole-market decile, NOT vs SPY), 1M vs SPY in points, and our own nearest levels.
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
only while the earnings module is, since that job keeps the dolt sql-server alive.
`technicals-iv-rank` runs after the close on trading days.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ Suite-wide guardrails apply — see root `CLAUDE.md`.

## Tool Reference

| Command | Purpose |
|---|---|
| `python -m cherrypick.technicals land [--symbols ...]` | Land bars, splits, dividends and IV from the local Dolt clones. Incremental; idempotent. |
| `python -m cherrypick.technicals status` | What the store holds, and the last landing. |
| `python -m cherrypick.technicals bars SYMBOL [--raw] [--last N]` | Adjusted (or raw) bars and IV rank (ours, or tastytrade's where Dolt has none). |
| `python -m cherrypick.technicals check-vendor [--all]` | Our adjusted bars against every vendor capture; non-zero exit on any disagreement. |
| `python -m cherrypick.technicals stages [--session D]` | Every candidate's stage on a session. |
| `python -m cherrypick.technicals score-stages` | Stage rule against every saved edition: side recall, stage agreement, extra rate, counts. |
| `python -m cherrypick.technicals rotation [--session D]` | Every rotation fund's state on a session. |
| `python -m cherrypick.technicals score-rotation` | Rotation rule against every saved edition. |
| `python -m cherrypick.technicals breadth [--sessions N]` | Daily leaders, laggards, net and bullish share. |
| `python -m cherrypick.technicals score-trends` | Trend scores vs every capture: exact, label, within-one. |
| `python -m cherrypick.technicals score-levels` | Vendor levels we place (grid and gap edges) on names whose bars agree to the cent. |
| `python -m cherrypick.technicals score-level-selection` | Where vendor levels sit among grid points, each measure against chance. |
| `python -m cherrypick.technicals score-rank` | Our 1-10 rank (stored whole-market cut-offs) vs the vendor's. |
| `python -m cherrypick.technicals score-signals` | Our six scan rules vs every saved scan list. |
| `python -m cherrypick.technicals history land` | Land Dolt's whole daily history (2011 on, every name) into `history.db`. Incremental. |
| `python -m cherrypick.technicals history check` | `history.db` against `eod.db` on the overlap, to the cent, and price jumps no split explains. |
| `python -m cherrypick.technicals study run [--workers N]` | The historical study under analysis plan v2; writes `study/history-results-<stamp>.json`. |
| `python -m cherrypick.technicals report [--session D]` | Write one session's report readings and per-name chart files. |
