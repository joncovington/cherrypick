# The setups watchlist

The console's `/charts/setups` table lists every recent signal from the chart setups, long and
short ([setups.md](setups.md)), and every position they still hold, across all charted names. Each
symbol opens that name's chart with the setup and side selected. None of it is vendor data: the
vendor only checks our trend scores and RS rank.

`watchlist.py` builds it from the chart files as `chart.write_all` writes them, into
`charts/setups-index.json`. The table and the charts therefore can't disagree about a trade. The
console filters and sorts it, and decides nothing.

## What a row is

One row per setup position: every open position, plus any position that entered or exited within
the last 20 sessions (`WINDOW`). The console splits a position into an entry line and an exit line
for its "Entries & exits" view (1, 5 or 20 sessions; 5 by default). It lists positions as they are
in its "Open positions" view.

Each row carries the context the signal is read against, all as of the chart's last session:

| Column | What it is |
|---|---|
| Side | Long or short. A short's entry reads "▼ short" and its exit "▲ cover". |
| Move | Close to close **in the trade's direction**: the exit against the entry, or the last close against the entry while open. A fall is a positive move for a short. **Not P&L** (no fill, no cost). |
| 1M / 6M | Our trend scores (−4 to +4) with our own five-step label (`trend.label`). The vendor checks the scores (99.7% day by day); its page's wording is not used. |
| RS | Our 1–10 rank, the vendor's "Relative Strength": a decile across the whole US market of half the 1-month return plus the 6-month return. **Not a comparison with SPY.** It matched the vendor's page on 12 of 16 names and was within one on the rest (2026-10-03). |
| 1M vs SPY | The name's 21-session return less SPY's over the same sessions, in percentage points. This is what RS is often taken to be. |
| Support / Resistance | The nearest of **our own** levels below and above the last close: swing points in our bars (`swings.py`, described in [setups.md](setups.md)). |
| $ Vol | The 50-session median of close × volume **to the session before the entry**: the figure the historical study's universe and its "$300M a day" rule read (`universe.membership`, over raw bars). |
| Options-tradable | Whether the name lists weekly options and its stock trades at least $100M a day (below). Null when there is no label. |

**The vendor's page shows three labels** (measured on 18 names on 2026-10-03: −4 to −2 Bearish, −1
Neutral, +3 to +4 Bullish). The first watchlist used that wording, guessing the 0 to +2 range. It was
replaced on 2026-10-04 by our own five-step label, so nothing in the table rests on a vendor
convention or a guess.

## The tested edge

The historical study confirmed one change to a setup: **mean reversion (long), taken only when the
name trades at least $300M a day** (`mr-300m`; [setups.md](setups.md), "Round 2"). Each chart file
carries that rule's own trades in `tested` (`chart.TESTED`). They are walked with the study's code
(`hypotheses.positions`), not by filtering the setup's finished trades. A signal under the bar
therefore never holds a position that blocks a later one over it. An entry outside the study's
universe (a close under $5) is dropped, as the study dropped it.

The watchlist gives each of those trades a row with `tested: "mr-300m"`, beside the setup's own
rows with `tested: null`. The two overlap: on 2026-10-02 the 77 tested rows were exactly the mean
reversion longs at $300M or more. The console therefore shows one kind or the other. Its "Tested
edge" button swaps the list, fixes the setup and side, and switches "Trend agrees" off for it: an
oversold entry never has a positive 1-month trend score, so that filter would hide every row. A
symbol opens the mean-reversion chart, whose arrows include these trades.

## Options-tradable (revised 2026-10-04)

The user trades options, so the table is cut to options-tradable names by default, with "All
names" one click away. A name is options-tradable when both hold (`tradable.py`):
- **It lists weekly options:** at least 4 expiries in the next 35 days, from the nightly
  market-metrics fetch (`scripts/fetch_iv_rank.py`).
- **Its stock trades at least $100M a day:** the 50-session median of close × volume to the last
  session, from our own bars.

A cash index (SPX) has no stock volume; its weeklies alone decide.

**Tastytrade's liquidity rating was dropped the same day it was adopted.** It marks a name down for
its share price. Among names with weeklies, the median close was $42 at rating 4, $88 at 3, $189 at
2 and $544 at 1. HD, LOW, APP, GS, CAT, LLY and COST were all rated 2. The market report's universe
had reached the same verdict on 2026-09-27 (`scripts/build_stock_universe.py`, "guides, never
gates").

**Measured bid/ask comes next.** That script snapshots the at-the-money call and put twice a
session.
- **Since 2026-10-05 it reads the standard monthly expiry.** Until then it read the expiry nearest
  30 days, which was a weekly (30 October) for 188 of 190 names on 2026-10-02. Weeklies quote wider
  than the monthly.
- **It quotes every listed candidate**, including names under the universe's own volume bar, such
  as LOW and ABT.
- **The bar is not chosen yet.** Once three sessions of monthly readings exist, the spread
  distribution across the label's names sets it. The universe's own bar (the worse leg within 3% of
  mid or $0.05) passes only 14 of 558 candidates, which is too strict for this label.

On 2026-10-02, 248 of the watchlist's 460 names were options-tradable, and 48 of the 77 tested
rows. Most of the rest list monthly options only (MCO, CB, BNY, LIN).

## Filters

- **Tested edge:** the confirmed rule's rows in place of the setups' (above).
- **Options-tradable:** on by default when the file carries a label; "All names" turns it off, and
  marks a name that fails it "no opts". With no label at all, nothing is cut and the page says so.
- **Setup:** one setup family or all of them. A family includes its short.
- **Side:** long, short, or both.
- **Event:** entries, exits or both.
- **Trend agrees:** both trend scores on the trade's side of zero, above it for a long and below
  it for a short (`watchlist.trend_agrees`).
- **RS ≥ 7:** only names in the top 30% of the market.
- **Symbol search.**
- **Sort:** most recent (the default), RS, Move, 1M vs SPY, or $ Vol.
- Every control is kept in the URL, so a filtered view can be shared as a link.

Nothing ranks signals by quality. These setups aren't scored for outcomes, so the table shouldn't
imply which signal is better.

## The numbers

On 2026-10-03, longs only: 403 rows across 527 charts, with 183 open positions (114 mean reversion,
42 pullback, 15 trend following, 12 breakout), and 57 names with an entry in the last 5 sessions.
With the shorts (2026-10-04): 730 rows.
