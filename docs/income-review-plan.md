# Live and Screener income review — plan

Agreed 2026-10-20, from a review of every Live and Screeners tab against one session's evaluation
of the real account. It records fourteen decisions and the order they land in. It builds on
[portfolio-guidance-plan.md](portfolio-guidance-plan.md), whose step 4 (screener candidates sized to
the account) is what most of this revisits.

Everything here stays advice: manage places no order, and the screeners are lists. Only #2 and #3
change *what is suggested*, so they land together at one boundary (below); the rest are read
surfaces, flags and bug-level fixes that land when ready.

## What the review found

- **Every income candidate was a near-the-money spread.** `candidates.structures` builds a put spread
  *and* a call spread on every name, short at the first out-of-the-money strike (delta 0.41 to 0.55
  that session), and ranks them by theta per dollar of buying power. At the mid a vertical's price is
  its risk-neutral expected payoff, so credit as a share of width is close to the chance of losing,
  not an edge; after the working-price concession and fees every one was negative, and the call
  spreads also sell against equity drift with no direction read behind them. The direction filter in
  the credit-spread selection plan (board count either side, short setups as calls) was never wired
  into manage.
- **The theta band was read off one expiring option.** A one-day leg carried 99% of the book's
  theta, so the band read five times over its ceiling, the concentration rule named that name, and
  every candidate was marked "theta over". Without it the book sat below the floor. The band flips
  from overloaded to starved as front-week options come and go.
- **Exposure that no flag adds up.** Half the trades spanned an earnings report (about a quarter of
  net liq at risk), and over half expired on one date (also about a quarter). Each trade passed its
  own rules.
- **Screener passes ignore earnings and the account.** 130 of 165 short-put passes spanned earnings,
  including all of the top 25 by yield, which ranks by jump risk. Fewer than half could be
  cash-secured in the account, and no covered call was writable (no 100-share lot held).
- **The credit-spreads tab is the vendor's list** and freezes when that subscription ends (10-24).
- **Smaller things:** a snapshot older than the code (v11 against v14) leaves wheel and PMCC
  empty with only a field-level note; the stored payoff figures carry float noise
  (`-300.00000000000364`); the retired flies loop shows in full on Live → today every day.

## The decisions

| # | Item | Decision |
|---|---|---|
| 1 | Theta band | **Read without the expiring legs.** The band, the concentration rule and the candidates' theta cap read theta without legs two days or less from expiry; the raw figure is shown beside it. The band still fires its REVIEW and still caps candidates, on the figure that is not about to vanish. Done (`portfolio.theta.front_dte`, default 2). The delta band, scaled by theta, reads the same figure and is floored at the net-liq band, so thin theta never narrows it below the no-theta band. |
| 2 | Spread construction | **Short by delta, side by direction.** The short at a target delta, starting at 0.30; a side is offered only when a direction read supports it (board count on that side; call spreads only on a short setup). The delta is set by a study over 0.20 / 0.25 / 0.30 on the local history before it lands, judged by edge after costs. |
| 3 | Ranking and display | **Show the odds, rank by cost drag.** Each candidate shows P(loss) (≈ credit ÷ width), P(max loss) (≈ the long's delta), break-even and round-trip cost; ranked by lowest round-trip cost as a share of max loss. No drift assumption is built in. |
| 4 | Earnings on screener lists | **Earnings in the trade fails `passes`**, as manage's candidates already refuse it. The row stays listed and says why; an unknown date is marked, not failed. |
| 5 | Account fit on screener lists | **Badge and a "fits the account" filter**, on by default: cash-securable within buying power for a short put, shares held for a covered call. The full screen stays underneath. |
| 6 | Credit-spreads tab | **Our own list**, built with the #2 rule over our universe and chains, as the short-put and covered-call tabs already are. Lands with or after #2. |
| 7 | Earnings exposure | **A portfolio card and a band**: downside summed by report date, and a REVIEW when total earnings downside passes a configurable share of net liq (default 10%). |
| 8 | Expiry concentration | **A REVIEW over a share**: one expiry's downside past a configurable share of net liq (default 20%) fires a REVIEW, and candidates prefer another expiry while it is over. |
| 9 | Leveraged and inverse ETFs | **Badged**, not excluded, on the lists and candidates. |
| 10 | A candidate on a name already held | **Stands alone, badged** when the name already has a position on the *same expiry*. |
| 11 | Retired loops on Live → today | **Collapsed** to one "off" line when the module is disabled and nothing is open or overdue; expanded again otherwise. Done (`LiveTodayLoop.retired`; any open, working, held, orphaned or overdue item, an arm record, or an unreadable ledger keeps the full row). |
| 12 | Snapshot older than the code | **Re-taken on deploy**: when the code's `SNAPSHOT_VERSION` is newer than the latest snapshot's, the supervisor takes one fresh read-only snapshot and re-evaluates. Once per version; the read places nothing. |
| 13–14 | Payoff profile figures | **Rounded to cents** where manage computes them. The review read them as value at expiry rather than P&L, but that was the raw `min_value`/`max_value` fields; the positions card already shows P&L from the opening cash (max loss from open, left to win, left to lose). Done. |

## The boundary for #2 and #3

#2 and #3 change which trades manage suggests and in what order, so they land in one change, after
the delta study, and the boundary is written once in the changelog. Nothing trades the candidate list,
so no ledger restarts; the boundary is for anyone comparing suggestions across it.

## Order

1. #13–14 profile figures (done).
2. #1 theta band (done).
3. #11 retired loops (done).
4. #12 snapshot re-take on deploy.
5. #4, #5, #9 on the screener lists.
6. #7, #8 earnings and expiry exposure.
7. #10 same-expiry badge.
8. The spread delta study, then #2 + #3 together, then #6.

Each is its own PR from its own worktree.
