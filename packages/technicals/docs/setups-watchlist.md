# The setups watchlist

The console's `/charts/setups` table lists every recent signal from the chart setups
([setups.md](setups.md)), and every position they still hold, across all charted names. Each symbol
opens that name's chart with the setup selected.

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
| Move | Close to close: the exit against the entry, or the last close against the entry while open. **Not P&L** (no fill, no cost). |
| 1M / 6M | Our trend scores (−4 to +4, which match the vendor's day by day), with the vendor page's three-way label. |
| RS | Our 1–10 rank, the vendor's "Relative Strength": a decile across the whole US market of half the 1-month return plus the 6-month return. **Not a comparison with SPY.** It matched the vendor's page on 12 of 16 names and was within one on the rest (2026-10-03). |
| 1M vs SPY | The name's 21-session return less SPY's over the same sessions, in percentage points. This is what RS is often taken to be. |
| Support / Resistance | The nearest level below and above the last close among those the vendor's chart draws ([vendor-view.md](vendor-view.md)). Blank where the vendor's chart was never captured. |

**The three-way label** is how the vendor's page shows a score, measured on 18 names on 2026-10-03:
−4 to −2 is Bearish, −1 is Neutral, and +3 to +4 is Bullish. Scores 0 to +2 weren't on that panel.
`page_label` assumes the mirror image (0 and +1 Neutral, +2 Bullish). That's unconfirmed until a
capture shows one of those scores. Our own chart page keeps the five-step label.

## Filters

- **Setup:** one setup or all of them.
- **Event:** entries, exits or both.
- **Trend agrees:** 1M and 6M both read Bullish.
- **RS ≥ 7:** only names in the top 30% of the market.
- **Symbol search.**
- **Sort:** most recent (the default), RS, Move, or 1M vs SPY.
- Every control is kept in the URL, so a filtered view can be shared as a link.

Nothing ranks signals by quality. These setups aren't scored for outcomes, so the table shouldn't
imply which signal is better.

## The numbers on 2026-10-03

403 rows across 527 charts: 183 open positions (114 mean reversion, 42 pullback, 15 trend following,
12 breakout). 57 names had an entry in the last 5 sessions.
