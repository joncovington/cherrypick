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
| 2 | Spread construction | **Short by delta, side by direction.** The short at a target delta, starting at 0.30; a side is offered only when a direction read supports it (board count on that side; call spreads only on a short setup). The delta is set by a study over 0.20 / 0.25 / 0.30 on the local history before it lands, judged by edge after costs. **Decided 2026-10-22 after the studies (below):** no direction signal passed, so there is no side rule; put spreads only, short near 0.20 delta and long near 0.10, held to expiry, with no credit floor -- the least-bad cell -- each saying it has no tested edge. Call spreads are off (`call_spreads`); short delta comes from the hedge. Done. |
| 3 | Ranking and display | **Show the odds, rank by cost drag.** Each candidate shows P(loss) (≈ credit ÷ width), P(max loss) (≈ the long's delta), break-even and round-trip cost; ranked by lowest round-trip cost as a share of max loss. No drift assumption is built in. **Decided 2026-10-22:** the odds are shown (`odds`, P(loss) read from the legs' deltas at the break-even); the cost-drag ranking is dropped, since the lowest cost drag sat at the strikes that lost most, and the list is by symbol. Done. |
| 4 | Earnings on screener lists | **Earnings in the trade fails `passes`**, as manage's candidates already refuse it. The row stays listed and says why; an unknown date is marked, not failed. Found already done (2026-10-21): the own-list tab hides a trade with earnings in it by default ("No earnings in the trade", escaped by `?earnings=all`), and an unknown date stays shown, marked; the review's count read the raw list file, not the tab. `passes` stays the liquidity verdict it is. No change. |
| 5 | Account fit on screener lists | **Badge and a "fits the account" filter**, on by default: cash-securable within buying power for a short put, shares held for a covered call. The full screen stays underneath. Done: manage judges each list trade against the account (`candidates.screen_fit`: a put's buying power at every strike within the lists' delta tolerance, cash-secured or Reg T, against the buying power available; the calls the uncovered shares allow) and the console looks the row up. An unjudged row stays shown. |
| 6 | Credit-spreads tab | **Our own list**, built with the #2 rule over our universe and chains, as the short-put and covered-call tabs already are. Lands with or after #2. **Not built (2026-10-22):** with no selector passing, our own list would be a list of trades that lose after costs. Manage's put-spread candidates, sized to the account, are where spreads appear; the vendor's tab freezes after 10-24. |
| 7 | Earnings exposure | **A portfolio card and a band**: downside summed by report date, and a REVIEW when total earnings downside passes a configurable share of net liq (default 10%). Done (`portfolio.earnings_exposure`; the ladder's earnings line gains `by_date`). |
| 8 | Expiry concentration | **A REVIEW over a share**: one expiry's downside past a configurable share of net liq (default 20%) fires a REVIEW, and candidates prefer another expiry while it is over. Done (`portfolio.expiry_concentration`). The income reading carries one cycle monthly per name, so there is no other expiry to prefer: a candidate on an expiry already over its share waits instead (a room cap, `expiry concentration`). |
| 9 | Leveraged and inverse ETFs | **Badged**, not excluded, on the lists and candidates. Done: from the declared list skate already leaves out (`technicals/leveraged_funds.json`: leveraged, inverse, volatility); each list row carries `leveraged`, and manage's candidates are badged from the session's own list rows. |
| 10 | A candidate on a name already held | **Stands alone, badged** when the name already has a position on the *same expiry*. Done (`held_same_expiry`, any held option leg on that name and date; sizing unchanged). |
| 11 | Retired loops on Live → today | **Collapsed** to one "off" line when the module is disabled and nothing is open or overdue; expanded again otherwise. Done (`LiveTodayLoop.retired`; any open, working, held, orphaned or overdue item, an arm record, or an unreadable ledger keeps the full row). |
| 12 | Snapshot older than the code | **A banner and a watchdog WARN**, no extra broker read. First agreed as a re-take on deploy, then changed (2026-10-21) on finding manage already snapshots every 30 minutes from 10:00 to 16:35 ET, so only a merge after the day's last run leaves a stale snapshot, and only until 10:00. The watchdog's `manage.snapshot_version` WARNs while the newest snapshot is older than the installed `SNAPSHOT_VERSION`; the console's manage tabs show that finding verbatim. Done. |
| 13–14 | Payoff profile figures | **Rounded to cents** where manage computes them. The review read them as value at expiry rather than P&L, but that was the raw `min_value`/`max_value` fields; the positions card already shows P&L from the opening cash (max loss from open, left to win, left to lose). Done. |

## The boundary for #2 and #3

#2 and #3 change which trades manage suggests and in what order, so they land in one change, after
the delta study, and the boundary is written once in the changelog. Nothing trades the candidate list,
so no ledger restarts; the boundary is for anyone comparing suggestions across it.

## The study (2026-10-21), and what it holds back

Run offline over the licensed end-of-day chains the credit-spread selection test fetched, so its
figures stay local and are not given here:

- **The declared selection test** (edge-hypotheses item 14) gave **no verdict** for its put and call
  selectors: too few trades per half, because a 0.40-delta short rarely admits the declared 35-40%
  credit at the working price at any width. The board selector was not run (no historical board).
- **The delta comparison** for #2 found **no short delta with edge after costs** -- 0.20, 0.25, 0.30
  and the first out-of-the-money strike manage uses today, on either side, each negative in both
  halves of the deciding window. The first out-of-the-money strike was the worst, and it is also the
  one with the lowest cost drag, so ranking by cost drag (#3) would favour the strikes that lose most.

So **#2, the cost-drag ranking in #3, and #6 are held for a decision**, not landed: no direction
selector passed (the condition for a side rule and for our own list), no delta earned its place, and
the ranking #3 chose is contradicted. The odds #3 would show (P(loss), P(max loss), break-even, cost)
do not depend on the study.

**Two more studies followed (2026-10-21; edge-hypotheses items 15 and 16, declared first):**

- **Item 15**, `mr-300m` -- the one directional signal that passed its historical confirmation --
  on bullish put spreads on single names, shorts at 0.20, 0.30 and 0.40 delta, held or closed at
  half the credit: **no cell passes.** It did not beat its random-date baselines.
- **Item 16**, put spreads with no signal on the funds the user trades (GLD, SLV, GDX, GDXJ, SIL,
  SILJ; BITO for bitcoin, IBIT reported): **no cell passes on any fund.** Closing at half the credit
  did worse than holding on every fund. SILJ's entry quotes almost never clear the leg bars, and SIL
  never makes the tradable universe.

**Decided (2026-10-22):** #2 and #3 land as above, at one boundary; #6 is not built. SILJ's thin
chain needs no special case: the live leg bars already mark it thin, and the lists hide it by default.

## Order

1. #13–14 profile figures (done).
2. #1 theta band (done).
3. #11 retired loops (done).
4. #12 stale snapshot banner and WARN (done).
5. #4 (found done), #9 (done), #5 (done) on the screener lists.
6. #7, #8 earnings and expiry exposure (done).
7. #10 same-expiry badge (done).
8. The spread delta study (done, 2026-10-21: no edge), then items 15 and 16 (done: no edge); #2 and
   #3 landed together 2026-10-22 as the least-bad put spread with its odds, by symbol; #6 not built.

Each is its own PR from its own worktree.
