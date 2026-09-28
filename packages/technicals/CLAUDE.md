# cherrypick-technicals — Operational Instructions

> Operating contract for the market report's **end-of-day store and technical engines**. The plan
> it belongs to is [`docs/market-report-plan.md`](../../docs/market-report-plan.md); suite-wide
> context is in the root [documentation index](../../docs/README.md).

This package answers one question: **what did every name in the universe do, session by session,
on prices adjusted the way the vendor adjusts them.** Phase 2 of the plan is the store; the stage,
rotation and chart engines (Phases 3 and 4) land here as pure functions over it.

**Credential-free and network-free.** It reads the LOCAL `dolt sql-server` (the `stocks` and
`options` clones the earnings module also reads; pulled at 05:30 by `scripts/refresh_dolt_data.py`,
outside every package) and, read-only, the market-report store the scripts write (the universe
candidates, the vendor's chart captures). It writes only `~/.cherrypick/data/technicals/eod.db`.

## The store holds raw; adjusted is computed

`bars`, `splits`, `dividends` and `iv` hold what Dolt now states, upserted wholesale so a correction
upstream replaces the old row. The adjusted series is `adjust.adjust` over them, on read, so it can
be rebuilt from raw at any time and a restated dividend changes every reading at once.

**The adjustment is the vendor's, and was matched, not assumed.** Proportional dividends (every bar
before an ex-date scaled by `1 - D / prior raw close`) and splits (`for / to` on price, the inverse
on volume). Against the vendor's 2026-09-25 chart data: MSFT's adjusted closes on all 753 sessions
exactly; ANET's to within a cent on the 21 of 3,012 prices where a pre-split division lands on a half
cent, because the vendor's raw prices carry more precision than Dolt's. So adjusted prices are
stored and returned unrounded; rounding is a display decision. `check-vendor` repeats the
comparison over every capture on file; a handful of OPEN prices differ by tens of cents (the two
sources disagree about which print is the open), which is a known source difference, not a defect
in the adjustment.

## Dividends: two sources, reconciled

Checked against the vendor's own adjusted bars for 40 names (2026-09-27), Dolt's `dividend` table
alone agreed on only 86.8% of prices: it has gaps (CSCO's and CHT's July 2024 dividends), zeros
(BAP's September 2024 is 0.0) and wrong amounts (BAP's May 2024 is 0.939 for a 9.2875 payout).
`scripts/fetch_dividends.py` fetches tastytrade's per-symbol history (paced, a week between
refreshes) and `dividends.reconcile` merges the two: same event within three days, gaps filled
from either side, tastytrade's amount when the two are within 3x, and a gross outlier on either
side losing to the name's usual payout -- tastytrade has errors too (BAP's May 2026 as 50.00). With
it, 36 of the 40 names match the vendor at 99% or better (93.3% of all prices). The four that do
not are foreign issuers (BAP, ALC, CCJ, AU), where the vendor evidently uses amounts neither
source carries; fits that need exact cents leave them out.

## Reading Dolt: the query shape is the whole cost

`ohlcv` and `volatility_history` lead their primary keys with `date`. A query filtered by a list of
symbols walks the table — one month for 200 symbols took 27.5 s, the same month for all ~13,000
symbols 4.4 s — and the first landing written the obvious way ran past ten minutes. So data is read
in month-wide date windows with **no symbol filter**, and the wanted symbols are kept in Python.

Two more rules, both learned on the first landing (2026-09-27):

- **A symbol Dolt does not list is reported, never planned.** SPX, NDX and VIX (indexes) came in
  through the universe candidates, never landed, and so looked new on every run: each morning read
  three years for nothing (3 min 24 s). They are now `not_in_dolt` in the report, and a steady-state
  landing takes ~13 s.
- **Every read is incremental.** A symbol's bars re-read ten days behind its newest stored bar (so a
  restatement lands), a new symbol backfills three years (the depth of the vendor's own chart
  data), and IV works the same way over a year.

## What it lands

`symbols.all_symbols()`: every universe **candidate** (not only the members — the stage engine is
scored against the vendor's own table, most of whose names are not liquid enough to be members),
the 35 rotation ETFs (the union of every fund the vendor's rotation section placed in a state
across Sept 21-25, plus XLK, XLV and XLC), and the benchmarks (SPY, AOR, AGG, RSP, QQQ, DIA, IWM).
Dolt carries no SPX; SPY stands in for it until an engine needs the index itself.

## The stage rule (Phase 3)

`stage.py` is the vendor's leaders/laggards screen as a pure function, and `score-stages` holds it
against every saved edition, whose stages are decoded from the ticker colours (`editions.py`). The
footnote's definition — early on the one-month screen only, building when one and two months agree,
confirmed when all three do — is only part of it. Fitting the five editions of Sept 21-25 found:

- **The one-day move is a fourth condition.** A name is listed only on a day its own move against
  the index is on the same side: BKNG trailed by 16-26% on every window and was a confirmed laggard
  four days running, then absent the one day it beat the index. That condition alone cut the names
  wrongly listed from 859 to about 295.
- **The rule's numbers are parameters, fitted on five days.** 10/30/63 sessions with margins of 1%,
  2% and 2% against dividend-adjusted SPY: 955 of the vendor's 980 listings on the same side (97%),
  the same stage on 82% of those. That is a large grid on a small sample, so the declared
  `StageRule` is re-scored as editions accumulate rather than tuned further now.
- **About 30% extra is structural.** The ~295 names the rule lists that the vendor does not are not
  removed by any margin: the vendor's universe itself varies by day in a way prices cannot show. So
  our counts run higher than the vendor's stated ones, and the two are compared as rates, never
  as totals.

## The rotation rule and the breadth history (Phase 3)

`rotation.py` places each of the 35 funds in the relative-rotation quadrant its slow (63-session)
and fast (10-session) relative trends put it in, against SPY, or against AOR for the nine funds the
vendor types "Asset"; inside either neutral band (3% slow, 1% fast) it is in no state. Fitted on
the five editions: the vendor's exact state for 91 of 119 placements (76%), 26 funds placed where
the vendor had none. `editions.decode_rotation` finds the four headings by their markup, never by
the phrase -- the paragraph above them uses the same words, and matching the phrase put funds in
the wrong state.

`breadth` rebuilds the report's daily chart from prices. Held against the sixteen sessions the plan
reads off the vendor's charts (Sept 2-24), eleven of them never used in fitting, the bullish share
correlates at 0.93 (leaders 0.78, laggards 0.90) -- the out-of-sample evidence that the stage rule
is the vendor's rule and not a fit to five days. Our counts run higher, as the stage section says.

## The chart layer (Phase 4, started)

`indicators.py` holds SMA, EMA, Wilder's RSI and Lambert's CCI (period 14, the vendor's scanner
period), each undefined until its window is full. `trend.py` is a **declared baseline** for the
1M and 6M trend scores (-4..+4) and their five-step labels: the sum of four price-versus-average
signs. The vendor's captures carry both scores daily, and on the two names on file (ANET, MSFT)
no simple construction reproduces the exact number (55-58% of days), while the label agrees on
about 80% (short) and 79% (long). Re-scored on 36 names (~25,000 short-term and ~20,000 long-term
daily scores; 2026-09-27) it holds out of sample -- label agreement 79% short term and 75% long
term, within one step 85% and 78% -- while no moving-average combination gets the exact number
past ~50%. `score-trends` keeps re-scoring against every capture. Levels, the 1-10 rank and the
named signals come next.

**The level grid is solved** (`levels.py`, `score-levels`). Every one of the vendor's 192 levels on
the 35 names captured is either the high or the low of the last 250 sessions, as printed, or a
point on a grid anchored at that low with a step of the "nice" number nearest -- by ratio -- to
(high - low) / 100. Both details came from misses: the window is 250 sessions, not 252 (three
names' lows sat on the 252nd), and ADI's range/100 of 2.24 takes 2.50, nearer by ratio. On our own
bars the grid places all 186 levels on the names whose bars agree with the vendor's. Which grid
points become levels is still open: 83% of level dates are swing highs, supports included, but the
price is not simply that high snapped.

**The 1-10 rank** (`score-rank`) is the decile of a ~6-month (125-session) return percentile:
Spearman 0.95 against the vendor's over 34 names, within one step on 31-33 depending on the
universe it is ranked in, exact on about 40%. The vendor ranks within its own universe, which 34
names cannot pin down.

## Scheduling

One supervisor job, `technicals-land` (06:15 ET daily, after the 05:30 Dolt pull; config block
`technicals`). It is enabled only while the earnings module is, because that module's job is what
keeps the dolt sql-server alive — without it the landing has nothing to read.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ Suite-wide guardrails apply — see root `CLAUDE.md`.

## Tool Reference

| Command | Purpose |
|---|---|
| `python -m cherrypick.technicals land [--symbols ...]` | Land bars, splits, dividends and IV from the local Dolt clones. Incremental; idempotent. |
| `python -m cherrypick.technicals status` | What the store holds, and the last landing. |
| `python -m cherrypick.technicals bars SYMBOL [--raw] [--last N]` | A symbol's adjusted (or raw) bars and its IV rank. |
| `python -m cherrypick.technicals check-vendor [--all]` | Our adjusted bars against every vendor chart capture; exits non-zero on any disagreement. |
| `python -m cherrypick.technicals stages [--session D]` | Every candidate's relative-strength stage on a session. |
| `python -m cherrypick.technicals rotation [--session D]` | Every rotation fund's state on a session. |
| `python -m cherrypick.technicals score-rotation` | The rotation rule against every saved edition. |
| `python -m cherrypick.technicals breadth [--sessions N]` | Daily leaders, laggards, net and bullish share. |
| `python -m cherrypick.technicals score-trends` | The trend baseline against every vendor chart capture: exact, label and within-one agreement. |
| `python -m cherrypick.technicals score-levels` | How many of the vendor's levels our grid places, on names whose bars agree to the cent. |
| `python -m cherrypick.technicals score-rank` | Our 1-10 rank against the vendor's. |
| `python -m cherrypick.technicals score-stages` | The stage rule against every saved edition: side recall, stage agreement, extra rate, counts. |
