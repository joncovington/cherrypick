# Skate screener, wheel and PMCC views — plan

Status: plan, 2026-10-18; steps 1–2 and the score are built (table below). The study it rests on was run by hand on 2026-10-18
(results below); the scripts were scratch files and are not in the repo. The three open questions
were decided by the user on 2026-10-18 (see "Decisions").

| Step | State (2026-10-18) |
|---|---|
| 1, the range band engine (`technicals skate`) | built: `skate.py`, `leveraged_funds.json`, job `technicals-skate` 06:45 ET, `core.calendar.trading_days_between` |
| 2, regular-hours quotes (`fetch_screener_greeks.py skate`, jobs `report-skate-quotes-1/2`) | built |
| 3, the score and the console's Skate tab | the score built (`skate.score`, in the quote file); the tab built 2026-10-19; the income tabs' leveraged badge not started |
| 4, the forward record | built: `skate_record.py`, recording from 2026-10-20; the tab section built 2026-10-20 |
| 5, the wheel view (`manage`, Live → positions) | cycle linking, accounting and the day's check built (`manage/wheel.py`, 2026-10-20); its rules built (`wheel.take_profit`, `wheel.tested`, `wheel.next_leg`, 2026-10-20); the bands in the snapshot and the tab not started |
| 6, the PMCC view (`manage`, beside the wheel view) | not started |

A screen for short options sold where the seller does not want assignment: covered calls above
the stock's recent range and cash-secured puts below it, on our own options-tradable names. The
method is Dan Passarelli's "skate" objective ([Stop Picking Wheel Strategy Strikes by
Delta](https://www.youtube.com/watch?v=pd6KYv5O0b4), Theta Profits; [Ep73 – My Exact Process for
Trading Covered Calls](https://www.youtube.com/watch?v=_7sX9naKTaE)). It is a read surface, a new
tab on the console's Screeners page. It places no order and opens no paper position.

Once one of its trades is open in the real account, the wheel view follows it: the put, any
assignment, the covered calls written against the shares, and the cycle's money across every roll.
A PMCC view does the same for a covered call written against a long call. Both belong to `manage`,
and like the rest of `manage` they are advice only.

The screen does not depend on the vendor's income lists. Those stop on 2026-10-24, when the
subscription ends; the study's universe came from them only because they were the newest lists on
disk.

## The rule, as the material states it

| Part | Rule | Source confidence |
|---|---|---|
| Objective | Decide before selling whether assignment is wanted. "Skate" = collect the premium and avoid assignment; "trade" = get assigned on purpose at a price the valuation likes. This screen is skate only. | stated |
| Strike | Not by delta. Sell beyond a band built from the recent range: calls at or above the upper band, puts at or below the lower band, alongside support and resistance. | stated |
| Band | True range over a lookback equal to the trading days left to expiry, smoothed by a moving average, set above and below the price, scaled by a "haircut" (usually 75%). | stated in Ep73; the formula is unpublished (a paid indicator) |
| Expiry | No fixed days to expiry. Compare candidates by straight-line theta (premium ÷ days), against implied volatility relative to its usual level; avoid spanning earnings; liquidity, commissions and ease of rolling break ties. | stated |
| Worth it | Skate yield = premium ÷ capital × 365 ÷ days (capital = the strike for a put, the shares for a call), against Treasuries and the stock's volatility. | stated |
| Exit | A GTC buy-to-close at $0.03 ($0.05 if illiquid) placed right after the fill. | stated |
| Tested | Net-zero roll (sell the new option for what the old costs to buy back) or premium-gambit roll (buy back at the original sale price, sell a further-out option). Losers are rolled "99% of the time". | stated |

**The range band** is our reconstruction, and the code calls it that, never by the indicator's
name. Range = highest high − lowest low over N sessions (N = trading days to expiry; his numbers fit
this better than average true range), smoothed by a 10-day simple average; upper band = close +
0.75 × range, lower band = close − 0.75 × range. The haircut and the smoothing are config, and a
second, by-expiry haircut schedule runs beside the fixed one (step 1).

## What the 2026-10-18 study found

Run on the 2026-10-16 covered-call and short-put lists, the 20 Nov monthly, broker chains fetched
2026-10-18 (Friday's book), and the local daily bars from 2015. Skate rate = the share of past days
on which the same band, at the same strike offset, held to the close N sessions later.

- **On average the band matches delta and does not beat it.** Band strikes land near 0.15 delta.
  Calls skated 85.5% of the time over 11 years against 84.9% implied by delta (82.1% over the last
  3 years); puts 91.1% against 89.2%.
- **The spread around the average is systematic.** Where delta's implied skate rate was low (high
  delta at the band), history beat it: calls under 82% implied, 12 of 13; puts under 85%, 10 of 10.
  Where it was high (far-out strikes, mostly momentum tech), history fell short: calls 88% and up,
  16 of 18; puts 93% and up, 17 of 18. History minus implied is the candidate screen, not the band
  alone.
- **The band holds the per-cycle risk constant across expiries.** At each expiry's own band the
  skate rate was 89% from 7 to 63 days. On the model price the 7- or 14-day option paid the most
  per day for 41 of 47 name-sides; on a bid with open interest of at least 100, the monthly won 23.
  Median open interest at the strike: 209 in the front week, 6 at 28 days, 825 on the monthly.
- **Weeklies are tested more often.** At 89% a cycle, one monthly goes untested 89% of the time;
  five 7-day cycles in the same span, 56%.

**Corrected 2026-10-19: the study's "implied" was 1 − |delta|, and that is the wrong odds.** A
skate is judged at expiry, so the model's odds are 1 − N(d2) (call) and 1 − N(−d2) (put); delta is
N(d1). At a 0.15-delta band, 30 days, 30% vol, a call's implied skate is about 87%, not 85%, and a
put's about 83%. So the calls' average beat above shrinks to about nothing or reverses, and the puts'
grows. The bucket results above are stated against delta and were not re-run (the study's scripts
were scratch). The score has used N(d2) since this date; quote files written on 2026-10-18 and
earlier carry the delta figure.

## What exists, and what's missing

| Need | Exists | Missing |
|---|---|---|
| Universe | `technicals liquidity` verdicts (`market-report/liquidity/verdicts.json`): weeklies and $100M a day | — |
| Bars | `history.db` daily bars and splits, from 2015 | a trading-days counter in `cherrypick.core.calendar` (advisor has `sessions_between`) |
| Earnings | broker report dates, `earnings_calendar.broker_dates(day)` | — |
| Fund types | `securities.latest(day)` types; `listings.is_etf` | **any leveraged/inverse flag**: none exists in the repo |
| Listed strikes | `screener-greeks/chains/<day>.json`, `universe/chains/<day>.json` | expiries beyond the cycle monthly for most names |
| Regular-hours quotes | `measure` at 11:30 / 14:30 (REST quotes, `LEG_BARS`) | IV and delta (REST returns quotes only) |
| Trade chains | `manage` rebuilds order chains with rolls and reconciles against deliveries | linking a put chain to the shares it delivered, and those shares to later calls |
| Resting orders | the `manage` snapshot's `working_orders` (v10, 2026-10-19): the day's orders, slimmed, no account number | — |

## What other trackers do, and what we take

A survey on 2026-10-18 of 15 wheel and PMCC spreadsheets and apps and 11 open-source repos. Most
are spreadsheets fed by hand or by CSV. Their failures are consistent: rolls entered as two
unrelated trades reset the basis or count the premium twice; collateral is defined three ways in
one article; short cycles annualized into absurd returns. No repo checks ex-dividend early
assignment or times the LEAPS roll, which steps 5 and 6 do.

| Idea | Seen in | Where it goes |
|---|---|---|
| **Two bases, never one**: tax basis (assignment price, by lot) and wheel basis ((share cost − all net option cash in the cycle, assigning put and rolls included − dividends) ÷ shares). A net-debit roll raises the wheel basis. | [QuantWheel](https://quantwheel.com/learn/cost-basis-tracking), [zyerusha/wheel-options-trading](https://github.com/zyerusha/wheel-options-trading), [TastyMechanics](https://github.com/crux1s/TastyMechanics) | step 5 |
| Shares that predate the transaction history get `basis_known: false`, never a guessed basis | zyerusha | step 5 |
| Cash is the broker's net amount after fees, never price × quantity recomputed | zyerusha; [tastypl](https://github.com/gotasty/tastypl) disabled its own fee maths after getting it wrong | steps 5, 6 (`manage` already does this) |
| Rolls linked by the order chain first, a same-day close-and-reopen (same right, expiry not earlier) as fallback, ambiguity flagged, never guessed | zyerusha, TastyMechanics | step 5 (`manage`'s chains already do the first) |
| Assignment matched option row to share row: same account, ticker and side, shares = contracts × multiplier, at the strike, within a few days, each share row claimed once | zyerusha, tastypl | step 5 |
| Option P&L and stock P&L kept apart; realized and unrealized kept apart | zyerusha, [OptionsPilot](https://optionspilot.app/blog/wheel-strategy-annualized-return-calculation) | steps 5, 6 |
| Annualized = return × 365 ÷ days (simple, as every tracker does), on time-weighted capital: strike × 100 while a put is open, the shares' cost while held; a covered call adds none. Labelled a comparison figure, capped per trade, never a median across mixed durations | zyerusha, OptionsPilot, TastyMechanics | steps 3, 5, 6 |
| Dual yield on a covered call: premium only, and if called | [PremiumGuard](https://premiumguardhq.com/blog/covered-call-tracking-spreadsheet) | steps 3, 5 |
| Calls still sellable = shares − short calls − working sell orders | [AllYouNeedIsWheel](https://github.com/WahBun/AllYouNeedIsWheel) | step 5; built in `manage.candidates` 2026-10-19 (filled short calls, then working sell-to-open calls) |
| A roll may not pay for itself by taking more risk: a rolled put's strike is capped at old strike + original premium − buyback cost | [thetagang](https://github.com/brndnmtthws/thetagang) | step 5, as a `check-roll` test |
| PMCC P&L if assigned = (short strike − long strike − long's net cost + premiums) × 100, shown in dollars beside the width check | [trading_skills `ib-pmcc-advisor`](https://github.com/staskh/trading_skills) | step 6 |
| PMCC short roll candidates: delta ≤ 0.40, net debit no worse than $0.10, next five expiries, never past the long's expiry; assignment chance N(d2) | trading_skills | step 6 |
| LEAPS extrinsic as a share of its price; roll the long at 90–120 days left or below ~0.70 delta | [coveredcallcalculator.net](https://coveredcallcalculator.net/guides-hub/poor-mans-covered-call-guide.html), [TheOptionPremium](https://www.theoptionpremium.com/p/pmcc-management-rules-rolling-short-calls-dividend-risk) | step 6 floors |

Not taken: win rate by delta bracket and A–F trade grades (they need many closed trades and grade
nothing the forward record doesn't), projected annual income from a weekly average (the
annualizing trap again), and "months to pay off the LEAPS", which no source defines.

## Steps

### 1. The range band engine (packages/technicals)

`technicals skate` (job `technicals-skate`, 06:45 ET, after `technicals-screeners`): a pure
function of the store, the verdicts, the broker earnings dates and the chain listings. For each
tradable name, each side and each Friday expiry out to 63 days:

- drop an expiry when the name is a stock reporting on or before it (shorter expiries stay), and
  the stock when its report date is unknown ("unknown" is never "clear"); funds are exempt;
- drop leveraged, inverse and volatility products from a **declared list**, since nothing in the
  repo flags them. It was started 2026-10-18: the optionable funds in the Nasdaq Trader directory
  (1,805), flagged by name (an explicit multiple of 1.25× or more, Ultra, Inverse, Short, Bear,
  Bull, VIX futures), then confirmed against Yahoo's fund category (Morningstar's "Trading--"
  categories), which also catches names the words miss. A disagreement is decided by hand and
  noted. Its test drives off the list itself and is shown to fail by removing a known fund. As
  built: 487 funds (356 leveraged, 123 inverse, 8 volatility); 458 flagged by both, 6 by name
  alone (new funds Yahoo has not categorized), 23 by Yahoo alone and decided by hand (21 Roundhill
  WeeklyPay funds, about 1.2× single-name, still to confirm; EMTY; PFIX). MEME, WMTI and TAIL are
  left out pending the user's call. Settled 2026-10-18 from the issuers' pages, the user leaning to
  caution: the WeeklyPay family is 120% of the calendar-week return through swaps (all 24, NVDW,
  TSLW and TOPW added), WMTI is 105–150% of WMT daily through options, TAIL (puts plus Treasuries, a
  decaying hedge) is excluded for caution, and MEME, a plain basket, stays in: 492 funds, each
  citing its source;
- drop a name with under a year of history (as built: an expiry whose band has under 250 windows to
  judge it on is dropped, and the name only when every expiry is; a fund is any type that never
  reports, `securities.NO_EARNINGS`, which includes commodity trusts such as IAU);
- compute **two bands** and, for each, the skate rate over 11 years and the last 3 **at a grid of
  strikes from 2% inside the band to 5% past it** (quarter-point steps; inside, for the strike one
  closer than the band's), so step 3 reads the rate at whatever listed
  strike it quotes. History is on **split-adjusted, not dividend-adjusted** prices: a strike stays
  put when the price drops on an ex-date, so dividend adjustment would show calls breached that
  were not (it moved MAGS, VOO, EWJ and EWZ by up to 5 points against the study's raw prices):
  - **fixed**: haircut 0.75 at every expiry;
  - **by expiry**: a declared schedule in config, defaulting to 0.65 for expiries of 14 days or
    fewer (Passarelli's Ep73 example) and 0.75 beyond. When both land on the same price, the row
    says so.

On the 2026-10-16 close: 458 tradable names, 336 with bands, 1,986 name-side-expiry rows, 93
seconds. Against the study's 20 Nov rows the skate rates agree to a median 0.1 point; the outliers
are S and MAGS, whose raw history carried an earlier company under the same ticker, which the
series break now cuts.

Writes `data/technicals/skate/<date>.json`: the plan for the quote step and the history each row
will be judged with. Counting sessions to expiry needs a trading-days counter; it goes in
`cherrypick.core.calendar` (moving advisor's `sessions_between`), not copied a third time.

### 2. Regular-hours quotes (scripts)

The study's prices were Friday's after-close book, where spreads run wide; the screen needs
regular-hours quotes. **As built** this is its own mode, `fetch_screener_greeks.py skate`, and its
own jobs (`report-skate-quotes-1/2`, 12:00 and 15:05 ET), not a pass inside `measure`: a failure
cannot touch the screener legs, as the `income` mode already showed. Its chain listings reach 1–70
days, wider than the builder's 14–60, so they are cached apart, once a day. Two stages:

1. every name-side once: its cycle monthly, or, where a report comes first (most stocks in
   earnings season), its furthest expiry before the report; each band's strike and one either side;
2. every weekly to 63 days, both bands' strikes and one either side, for the `stage_two` (60)
   name-sides whose tradable band strike has the widest history-minus-implied gap.

Both stages read each band **moved to the run's price** (`skate.shift_for`: band + spot − the band
day's close; the range stays the band day's). Until 2026-10-19 they used the morning file's band as
it stood, so a name that had moved through its band since the close was quoted and ranked on a
strike already in the money (AMZN's 260 call at 262.62 topped the gap sort).

REST quotes carry no IV, so implied volatility is solved from the mid by `cherrypick.core.bs`
(moved from `manage.pricing`, which now imports it), and delta from it at r = 4%. The solver
carries each name's trailing dividend yield, which the band file now records. Writes
`screener-greeks/skate/<date>/<HHMM>.json`. A Sunday smoke test on 25 names (Friday's book): 49
name-sides scored, 5 to stage two, 309 legs, 8 seconds with the chains cached.

### 3. The score and the console's Skate tab

The skate run scores its own quotes with pure functions from technicals (`skate.score`,
`skate.best_expiries`; built with step 2), so the console reads verdicts and never re-derives them:

- skate yield on the bid and on the mid; implied skate = 1 − N(d2) for a call, 1 − N(−d2) for a
  put (`core.bs.prob_itm`); history minus implied;
- **hard filters** (a row that fails is not shown): open interest ≥ 100, the `LEG_BARS` spread, no
  earnings in the cycle;
- **soft thresholds**, config, shown as a pass/fail marker and never used to hide a row: skate
  rate ≥ 84% over 11 years and ≥ 80% over 3, skate yield ≥ 6%;
- per name-side, the best expiry by premium per day on the bid among legs that pass the hard
  filters, its ratio to the monthly, and per-cycle and per-month test odds;
- all of the above for both bands, fixed and by expiry;
- a "both sides" marker where the call and the put both pass (as built 2026-10-20:
  `skate.mark_both_sides`, each row's `both_sides` per band, true when both sides' best picks pass
  every soft threshold; the tab does not show it yet).

Console: a **Skate** slide in `ScreenersLightbox.tsx`, `/api/screeners/skate?day=`, a reader in
`server/src/readers/skate.ts` with its own types, calls and puts views.

- Sorted by history minus implied by default; a "passing only" toggle narrows to the soft
  thresholds. Closer strikes tend to top that sort, so skate yield and skate rate stay beside it.
- A **band** selector, fixed or by expiry, picks which band's strikes drive the rows and the
  ranking. Each row shows the other band's strike and premium per day in a quieter column, so the
  difference is visible without switching.
- Columns as the study's tables; the best expiry bold when it beats the monthly. Money follows the
  suite's layout (premium per contract, a per-share price labelled).

The income tabs (short puts, covered calls, credit spreads) get a **leveraged** badge on names in
the declared list, and an optional "hide leveraged" toggle, off by default so the rows the
fidelity score is judged on stay visible.

### 4. The forward record

Each day's passing rows are kept; at expiry the outcome is read from the bars (skated, tested
mid-cycle, or through the strike at expiry). After 20 expiries' worth, the record answers what the
study could only estimate from overlapping history: does history-minus-implied pick the strikes
that skate more than priced? The test is declared now: rows with history minus implied above 5
points must skate more often than their implied rate, and more often than rows below zero, by more
than the binomial noise at that count. Until then, the tab says the gap is a history estimate.

Both bands are recorded, with each row's delta. That answers the haircut question the same way:
after 20 expiries, did the by-expiry band's closer short-dated strikes skate at their implied rate
or better, and did their extra premium per day survive the extra tests? Until then the selector is
a preference, not a finding.

**As built (2026-10-19):** `technicals.skate_record`, run by the `technicals-skate` job after the
bands (and alone as `python -m cherrypick.technicals skate-record`); its failure is reported and
never fails the bands.

- **What is a row.** Each session's newest scheduled quote run, per name-side and band: the band's
  pick on its best expiry, only when it passed the hard filters, which is the Skate tab's row. A
  strike is kept once, the first session it was shown: the same strike seen again is the same bet,
  and counting it daily would shrink the noise the test is judged by.
- **First session: 2026-10-20.** Runs before it scored strikes against the band day's close (#435),
  so some of their picks were already in the money.
- **Outcome.** Read from split-adjusted closes, as the skate rate is. `skated`: the close at expiry
  on the safe side of the strike. `tested`: skated, but a bar reached the strike on the way. `through`:
  finished past it. A tested row counts as skated in the test, as the skate rate counts it.
- **"20 expiries" means 20 distinct expiry dates**, weeklies included: about five months at the
  screen's weekly-heavy picks, not twenty monthlies.
- **"The binomial noise"** is a one-sided z > 1.645. Against implied, it is the row count's own
  Poisson-binomial spread, each row at its own implied rate. Against the below-zero rows, it is a
  two-proportion z. The test is judged per band.

The reading lands beside the bands as `data/technicals/skate/record.json`, with rows in `record.db`.
The tab section (2026-10-20) reads `record.json` through its own route, below the Skate table: the
counts, each band's groups and z scores, and the record's verdict as it states it, "not yet
decision-grade" until then.

### 5. The wheel view (packages/manage)

For each underlying that holds a short put, shares, or a covered call:

- **Cycle linking** (`manage/wheel.py`): follow the order chains through the broker's
  Receive Deliver rows, from the put chain to the delivered shares to the calls written against
  them, until the shares are called away or sold. A cycle that cannot be replayed to today's
  quantities is shown as unreconciled, never guessed.
- **Cycle accounting**: credits and debits across every chain (the broker's net cash), net
  premium, both bases (tax and wheel, as in the table above) and the breakeven from the wheel
  basis, option and stock P&L apart, realized and unrealized apart, days in cycle, and skate yield
  to date on time-weighted capital against the yield the screen projected at entry. A cycle is
  complete when it is flat and credits exceed debits. Assignment is matched as in the table above;
  an unmatched delivery is flagged, never filled in.
- **Today's check**: strike against today's range band (the orchestrator copies the held names'
  bands from step 1 into the snapshot, so `reevaluate` still gets the same answer twice; built
  2026-10-20 as snapshot v12's `skate_bands`: the newest band file at most three sessions behind,
  cut to the held names, each row's bands with their skate curves and the file's `offsets`, so the
  skate rate at any strike can be read from the snapshot alone. The copy is the band day's; manage
  moves it to today's price in `wheel._band`, as the screen's `shift_for` does. A missing or stale
  file is `skate_bands: null` with `skate_bands_error`), tested or
  not, extrinsic value left, and whether a buy-to-close is resting. That needs a **fourth read-only
  broker call**, open orders, in `orchestrator/managesnap.py` (built 2026-10-19: snapshot v10's
  `working_orders`, from the broker tool's `get_working_orders`).
- **Suggested action**, from `manage`'s fixed vocabulary: `TAKE_PROFIT` when the mark is at or
  under the buy-to-close price; `ROLL_OR_CLOSE` when tested, with the net-zero and premium-gambit
  candidates priced and each passed through `check-roll`; after an assignment, the next leg named
  from the Skate screen's call band. Each rule gets a confidence and a README row, as `manage`
  requires.
- **Console**: a wheel tab beside Live → positions.

The real account is an IRA, so only cash-secured puts and covered calls arise; margin accounts are
supported by `manage` and need nothing extra here.

### 6. The PMCC view (packages/manage)

A PMCC is a covered call with a long call standing in for the shares, so it gets the same view
with the long call in the shares' place. `manage` already classifies it: `shapes.classify` calls
it a diagonal, and `roll.diagonal_short` rolls the short at 21 days or 80% captured. Shape alone
does not make a PMCC: the 2026-10-16 evaluation's TLT (long 80 call Jan 2029, short 85 call
Jan 2027) has the shape but was entered as a diagonal, and stays one. What it adds:

- **Recognition**: a call diagonal (a long with at least 180 days left, a short at or above its
  strike with 60 days or fewer, one short per long) is a PMCC only when the long was **entered at
  0.80 delta or higher**, or when `manage.json` says so (`positions.<SYMBOL>.intent: "pmcc"`). An
  intent of `"diagonal"` keeps a trade out whatever its delta. Everything else stays under the
  diagonal rules it has today.
- **The long**: the view shows its delta at entry (solved from the opening fill and that day's
  close; the method enters at 0.80 or higher) and its delta today, and flags a long
  that has drifted below a configured floor as a candidate to roll down. The solver has to carry
  the dividend yield: TLT's long (80 strike, stock at 80.16, ~4.3% yield) is about 0.49 delta at
  its mid, and without the yield the model can't price it at all. Also shown:
  extrinsic as a share of the long's price, and days left, with a "roll the long" flag below a
  configured floor (the pmcc package's base trade holds a ~1-year long).
- **The width check**: short strike − long strike must exceed the long's net cost after all
  premium collected, or an assignment locks in a loss. Shown on every row, with the P&L if
  assigned in dollars.
- **The short**: tested against today's call band (step 1), early-assignment exposure (in the
  money with extrinsic under the next dividend before an ex-date, the pmcc package's
  near-zero-extrinsic flag), and earnings inside its span (refused at entry in the pmcc package).
- **Cycle accounting**: every short sold against the long, from the order chains; net basis of the
  long; skate yield on the long's cost.
- **Suggested action**: the existing `roll.diagonal_short`, plus `ROLL_OR_CLOSE` for a failed width
  check or an assignment-exposed short (with roll candidates chosen as in the table above), and a
  `REVIEW` when the long needs rolling (90–120 days left or under ~0.70 delta, both config). Each with a
  confidence and a README row.
- **Console**: a PMCC tab beside the wheel tab, same columns where they mean the same thing.

## The build, in pull requests

1. `core.calendar` trading-days counter; `technicals skate` engine, its config block, job and tests.
2. The measure pass and the IV solver move; tests on recorded fixtures.
3. `skate.score`, the console reader, route and tab.
4. The forward record and its tab section.
5. `managesnap` open-orders read; `manage/wheel.py`, its rules and the wheel tab.
6. PMCC recognition, checks and rules in `manage`, and the PMCC tab.

None of it touches a trading loop, so none of it is measurement-affecting; each lands when green.

## Not in this plan

- Placing, rolling or closing anything. The desk is the only discretionary order path, and nothing
  automated may import it.
- The "trade" objective (assignment wanted, strikes from valuation).
- Support and resistance: our technicals don't reproduce level selection yet.
- What a breach costs. Skate rate is a frequency; weighing the loss needs historical option prices
  (the backtest package's licensed data), which would stay local.

## Decisions (user, 2026-10-18)

1. **Filters**: liquidity and earnings are hard filters; the skate-rate and yield thresholds are a
   marker with a "passing only" toggle; the default sort is history minus implied.
2. **Leveraged funds in the income tabs**: a badge, with an optional hide toggle, off by default.
3. **Haircut**: both bands computed, fixed 0.75 and a by-expiry schedule; the user picks which one
   drives the tab; the forward record compares them.
