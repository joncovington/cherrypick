# /VXM plan: the contango switch in Mini VIX futures

**Status: plan, not built (2026-10-05).** It follows `packages/contango`, which runs the same switch
in SVXY. Nothing here changes code. Each open decision is marked **Decide** and needs your answer
before anything is built.

## The short answer

At sizes that survive, the futures earn less than SVXY. What they would add is something SVXY
can't give: capacity, nearly round-the-clock trading, Section 1256 tax treatment, and no risk of
an issuer closing the fund. So build them **only as a second contango arm, at half notional or
less, and only once SVXY's paper run has said something**. Never build them as a replacement.

## What the research found (2026-10-05)

All figures are gated on VIX/VIX3M < 0.97, use CFE settlement prices and are rebalanced daily, from
the scratch replay that produced the contango module. The VX history (2013 onward) stands in for
VXM, which is the same contract at a tenth of the size and has traded only since 2020-09.

| Short VIX futures, 2018 onward | CAGR | max drawdown |
|---|---|---|
| front month, 1× notional | −10.0% | −90.6% |
| front month, ½× | 1.7% | −59.8% |
| second month, ½× | 1.9% | −45.5% |
| constant 30-day (m1/m2 blend), ½× | 4.6% | −43.9% |
| constant 30-day, ½×, `flipexit` (out at 1.0) | 7.5% | −46.1% |
| *SVXY (contango `control`), for comparison* | *7.4%* | *−39.8%* |

- **1× is not survivable.** Even gated, a full-notional front-month short drew down 90%, with
  a −37% day on 2020-06-11. SVXY's −0.5× is the leverage the fund's own issuer chose after February 2018, and the
  replay agrees with them.
- **The 2022-04 onward window looks far better** (second month at ½×: 10.8% CAGR, −25.9% drawdown).
  That window holds no 2018- or 2020-sized spike, so it is the flattering case, not the expected one.
- **One VXM contract, held short at a fixed size** (gated, 2018 onward, no compounding):
  - front month: +$519 a year, −$2,271 max drawdown, worst day −$1,065 on 2020-06-11;
  - second month: +$299 a year and −$1,798.
- **A VIX call hedge costs more than it saves.** A rolling 30-day call cost about $1,133 a year
  against $687 of payoff, and its drawdown was worse, because the spikes that matter gap through
  the strike before the hedge re-strikes. The plan carries no option hedge.
- **Liquidity is thin.** VXM averages about 4,650 contracts a day across all months (front month
  about 3,800), and open interest was 9,362 on 2026-10-02. VX trades about 180,000 a day. A paper
  arm of a few contracts is fine; anything sized for real money needs its fills measured, not assumed.

## The design, if built

- **An arm in `packages/contango`, not a new package.** The regime read, the decision window, the
  sessions/NAV ledger and the replay are the same. What differs is the instrument: a contract
  multiplier ($100 × the VIX futures price), daily mark-to-market, a roll before final settlement,
  and margin in place of a purchase price. An arm would declare `instrument: "future"` beside the
  fund arms' implicit `"fund"`.
- **Size at ½× notional or less**, computed from the arm's NAV at each decision: contracts =
  floor(notional fraction × NAV ÷ (100 × futures price)). On $10,000 with VXM near 18 that is two
  contracts. The idle collateral earns the T-bill rate, credited the way SHV's distributions are,
  so the arm and the fund arms are measured on the same footing.
- **Contract: the second month by default.** It had the shallowest drawdown at ½× and is a real
  contract, where the constant-30-day blend means holding two. Roll at a fixed number of sessions
  before the front month's final settlement (the Wednesday VRO), never into the settlement print.
- **Decision timing.** The VIX index closes at 16:15 ET; the futures trade nearly round the clock.
  The fund arms decide at 15:50 because the funds stop trading at 16:00. A futures arm could read
  the ratio at 16:15 and trade in the futures' extended session, a cleaner read than 15:50's.
  **That changes the timing against the fund arms, so it is a declared difference, not a hidden
  one.**
- **Stream request.** VXM contracts as quote-only legs, named exactly as the broker's instruments
  endpoint names them. `scripts/refresh_futures_contracts.py` already resolves VX's two front
  contracts for the GEX regime recorder and records the exchange suffix it is told (`XCBF`, not the
  guessable `XCFE`). VXM would join its `PRODUCTS`, never be assembled by hand.
- **Fills and costs.** Fill at mid with half the spread as slippage, the fund arms' model, at VXM's
  0.05 tick ($5). Commission and the exchange, clearing and NFA fees come from a declared table in
  `core.fees`, read from the broker's published schedule. No number is guessed.

## Prerequisites (measure, don't assume)

1. **Quotes in the cache.** Declare one VXM contract as a leg for a week and confirm the streamer
   keeps it fresh through the 15:50–16:15 window and overnight.
2. **Margin.** The broker's actual initial and maintenance margin for VXM, recorded per session. The
   replay ignored margin calls. A ½× arm should never meet one, and the paper run should show that.
3. **Fees.** The futures commission and pass-throughs from the broker's schedule, as a declared table.
4. **Futures approval** on the account, for a later live step only. The paper arm needs none.

## Decide

- **Build now, or after SVXY has a quarter of paper data?** Recommended: after. The futures arm
  answers "does the switch survive in a cheaper wrapper", which only matters once the switch
  itself has held up.
- **Second month or constant 30-day?** Recommended: second month, which is simpler and had the
  shallower drawdown.
- **½× or ¼× notional?** Recommended: ½×, which is the replay's survivable size and matches SVXY's
  exposure.
