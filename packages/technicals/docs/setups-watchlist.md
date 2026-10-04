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

**The vendor's page shows three labels** (measured on 18 names on 2026-10-03: −4 to −2 Bearish, −1
Neutral, +3 to +4 Bullish). The first watchlist used that wording, guessing the 0 to +2 range. It was
replaced on 2026-10-04 by our own five-step label, so nothing in the table rests on a vendor
convention or a guess.

## Filters

- **Setup:** one setup family or all of them. A family includes its short.
- **Side:** long, short, or both.
- **Event:** entries, exits or both.
- **Trend agrees:** both trend scores on the trade's side of zero, above it for a long and below
  it for a short (`watchlist.trend_agrees`).
- **RS ≥ 7:** only names in the top 30% of the market.
- **Symbol search.**
- **Sort:** most recent (the default), RS, Move, or 1M vs SPY.
- Every control is kept in the URL, so a filtered view can be shared as a link.

Nothing ranks signals by quality. These setups aren't scored for outcomes, so the table shouldn't
imply which signal is better.

## The numbers

On 2026-10-03, longs only: 403 rows across 527 charts, with 183 open positions (114 mean reversion,
42 pullback, 15 trend following, 12 breakout), and 57 names with an entry in the last 5 sessions.
With the shorts (2026-10-04): 730 rows.
