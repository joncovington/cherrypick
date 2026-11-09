# Time-spread scanner plan

> **Status (2026-11-08): built in `packages/manage` (`timespreads`) on 2026-11-06 and shown on Screeners and the portfolio candidates; the test of its ratio (research item 54) ran and failed, so it stays labelled "no tested edge".**

Agreed 2026-11-06. A daily scan for calendars and diagonals: which names have a front week priced rich
to the back month, which structures on them are worth a look, and which fit the account. It turns a
screen that had been run by hand, a name at a time, into one reading the positions page can show.

## Where the edge would live

The hypothesis is a term-structure premium: when the front week's implied volatility stands well above
the back month's, the short leg is overpriced relative to what the stock then does.

It is untested, and the nearest evidence is against it: the earnings time-spread step 0 (#348) lost in
every bucket, the unscreened calendars lost in the strategy tests, and the earnings navigator lost after
costs. So the scanner ships labelled "no tested edge". Its rows are ordered by the front/back IV ratio,
marked "hypothesis, untested", until a declared study (item 54) shows the ratio predicts profit after
costs. If it does not, the scanner stays a convenience and is never presented as a source of trades.

## Pipeline

1. **Universe.** The liquidity verdict's tradable names: weekly options and a $100M median dollar volume,
   held-out illiquid names left out. A stock needs a $20B market cap; a fund (no market cap) is kept.
2. **Term-structure read.** The broker's market metrics for the universe in batches: the front IV (the
   expiry 5 to 16 days out nearest the middle of that range), the back IV (a standard monthly 30 to 75
   days out, else any expiry there), IV rank, 30-day IV against 30-day realised volatility, and the next
   earnings date.
3. **Prefilter.** Front/back IV ratio at least 1.15, IV rank at least 30%, and no earnings between today
   and the back expiry: a spread across a report is a different trade, screened by the earnings
   module's own time-spread engine. Every name's row is kept with the reason it failed.
4. **Shortlist chains.** For the 20 best ratios only: the spot, and both expiries' strikes within reach
   of it, quoted with volume and open interest. Broker reads, not stream subscriptions, so the streamer's
   budget is untouched.
5. **Structures.** Per name: same-strike put and call calendars at the money and one strike either side;
   put and call diagonals with the long at 0.55 to 0.75 delta, the short at 0.15 to 0.35 delta, and a
   debit of at most 75% of the distance between the strikes (manage's diagonal-width rule).
6. **Gates.** Every leg two-sided and within the suite's leg bars as one order, and the natural fill no
   more than 15% of the debit away from the mid.
7. **Model columns**, each labelled modelled: the debit at mid and at the natural fill, debit against the
   strike gap, front and back IV, net delta, theta a day, profit at the short's expiry with the stock
   unchanged, up 5% and down 10%, the breakeven band and the 25%-loss price-stop band, the take profit,
   the value stop and the roll date.
8. **Account fit.** Buying power after the trade at most 60% of net liq; the debit within the 2.5%
   per-trade budget; above +100 beta-weighted delta only neutral and bearish structures are shown; a name
   or sector already held is flagged; a quantity, with the cap that binds named.
9. **Output.** Rows by name, then structure, ordered by the ratio and labelled untested until item 54.

## Phases

| # | What | Where |
|---|---|---|
| 1 | The term-structure read and prefilter, written to `time-spreads/<day>.json` | `scripts/fetch_time_spreads.py`, its pure part in `manage/timespreads.py` |
| 2 | Shortlist chains, and the structure builder with its gates and model columns | the script's reads; the builder in `manage` |
| 3 | Account fit and sizing, beside the income candidates | `manage`, read from the snapshot |
| 4 | Console: a time-spreads tab on Screeners, and the rows on Live, portfolio | `console` |
| 5 | A job at about 10:30 ET on trading days, an on-demand command, a `/time-spreads` skill | orchestrator, skill |
| 6 | Item 54: does the ratio, or the "loses on an unchanged stock" verdict, predict calendar and diagonal profit after costs | `scripts/`, the research queue |

The broker reads live in a script, the income reading's pattern: the orchestrator runs packages and
scripts by subprocess and imports neither, and manage stays credential-free, judging only what the
snapshot carries.

## Decisions (user, 2026-11-06)

- Universe: weeklies, $100M a day, $20B cap.
- Earnings before the back expiry: excluded.
- Order before item 54: by the ratio, labelled untested.
- Shown on Screeners and on the portfolio candidates.
- Refresh: about 10:30 ET on trading days, and on demand.
- Above +100 beta-weighted delta: neutral and bearish structures only.

Nothing here touches an order path. The scanner is advice, like the rest of manage.
