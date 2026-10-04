# The shield study: what the held-long arms were expected to do, and why these five symbols

*Dated record, 2026-10-04. This is the evidence behind the `shield` and `shield_hold` arms and the
symbol change at the Part 1 boundary. It is a replay over modelled prices, not a result. The arms
exist so that weeks of real quotes can say what fifteen years of a model cannot.*

Re-run with `python scripts/pmcc_shield_replay.py --until 2026-10-02` (add `--fetch` the first
time). The script states its model, what it leaves out, and the check it must pass before any table
prints unflagged. `scripts/pmcc_leap_probe.py` checks the other half: that each symbol lists a
year-long expiry deep enough to trade.

## What was compared

The design comes from Tom King's "Income Shield" (Theta Profits, video P43DluId17o): hold a ~1-year
call at about 0.925 delta, and every week sell a ~0.70-delta call against it. By put-call parity, a
deep long call plus a short ITM weekly call is a short ~30-delta weekly put plus a far-OTM long put.
It earns the variance premium on the weekly, with a crash floor far below.

| arm | long | short | management |
|---|---|---|---|
| `control` | 17–25 DTE nearest 21, 0.875 delta, **re-bought every cycle** | ATM, soonest Friday 5–11 DTE | settles at the bell; the long rides to the next session, is sold, and the next position enters |
| `shield_hold` | third-Friday monthly nearest 360 DTE, 0.925 delta, **held** | 0.70 delta, soonest Friday 5–11 DTE, above the long's strike | rolled on its expiry day; closed at 45 DTE on the long or on a 30% stop |
| `shield` | as `shield_hold` | as `shield_hold` | also rolls early at 85% decayed or on a breach, at most once a session |

**Sizing.** One share-equivalent per dollar of NAV at entry, held until the position closes. Every
figure is therefore a percentage of the stock value controlled. Tom's spreadsheet states returns on
the long's cost, about a third of that, so his percentages run about three times these.

## The model, and the check that moved it

Options are priced off each symbol's own Cboe vol index, with VIX9D/VIX for the weekly term
structure and a put-skew term (the script's docstring has the formula).

**The check.** By put-call parity, a weekly ATM buy-write on SPY's total-return closes is Cboe's WPUT.
Priced through the model, its CAGR must land within 1.5 points a year of WPUT's.

At the IV level fitted to the PMCC ledger (c = 0.87), the check **failed**: 6.9% a year against
WPUT's 4.7%. The gap had the same sign in every five-year sub-period (+1.9, +3.4, +1.5), so it is a
level bias, not a regime. The ledger's fit rested on a few weeks of 2026 IVs; WPUT is fifteen years
of traded prices. So the SPY family was refitted to c = 0.83 (0.87 × 0.954), and the check now
reads 4.9% against 4.7%.

**This changes the earlier study.** The same session's scratchpad study priced weeklies about 5%
too rich. Every short-premium figure it produced was optimistic by roughly that much, and the
numbers below replace it.

QQQ and IWM borrow XSP's fit. GLD and SLV use the video's own SLV prices. No weekly benchmark exists
for them, so their figures carry less weight.

## Results, 2011-03-16 to 2026-10-01, IV at the fitted level

Alpha is the intercept of each arm's weekly excess return regressed on the underlying's, a year.

| symbol | control | shield_hold | shield | held − control | of which costs |
|---|---|---|---|---|---|
| XSP | −2.4 | **+1.3** | **+1.6** | +3.7 | 2.0 |
| QQQ | −5.3 | −0.2 | −1.5 | +5.1 | 1.4 |
| GLD | −4.3 | −0.3 | +0.3 | +4.0 | 1.3 |
| IWM | −6.1 | −3.6 | −1.1 | +2.5 | 0.6 |
| SLV | −7.7 | −6.3 | −7.5 | +1.4 | 0.7 |

**1. Holding the long beats re-buying it, on every symbol over the whole window, but not in every
window.**
- Five-year sub-periods (2011–15, 2016–20, 2021–26): the held long wins 11 of 15. It wins all three
  windows on GLD and IWM.
- It loses 2016–20 on XSP and QQQ, where the 2020 crash took a long that kept 0.9 delta while the
  short's protection collapsed.
- It loses both later windows on SLV.
- Costs explain about half the gap on XSP and less elsewhere. On XSP, control's cost line is 2.8% of
  NAV a year and `shield_hold`'s 0.8%. The rest of the gap is structure: control's short is ATM and its
  long rides unhedged to the next session every cycle.

**2. The short's edge is the IV assumption's.**

| IV scale | ×0.9 | ×1.0 | ×1.1 |
|---|---|---|---|
| XSP `shield_hold` | −1.9 | +1.3 | +4.9 |
| SLV `shield_hold` | −11.6 | −6.3 | +1.2 |

The sign flips within ±10% on every symbol. That is the variance premium, and fifteen years of an
index proxy cannot settle it. A few months of real weekly quotes, recorded at the moment each short
is sold, can.

**3. The downside is the long's, whatever the net delta says.**
- The shield arms carry about 0.25 net delta per unit of NAV and a beta of 0.2–0.3.
- In a crash, though, the short's delta collapses while the long's stays near 0.9.
- So SLV's worst week cost `shield_hold` 26.7% (SLV itself fell 25.0%), and XSP's cost 5.9% (SPY
  fell 15.0%).
- The low beta comes from capped upside, not from protection.

**4. The 30% stop is mixed; it is kept because it is Tom's rule, and it is measured.**

| | XSP | QQQ | GLD | IWM | SLV |
|---|---|---|---|---|---|
| stop, `shield_hold` | +0.8 | +1.1 | −0.1 | −0.4 | −1.9 |
| stop, `shield` | +0.7 | +2.2 | 0.0 | +0.9 | −0.6 |

- It cut drawdowns on the equity indexes and raised them on SLV: −52.8% with the stop against −40.2%
  without. A stop followed by mechanical re-entry is a costly re-strike of the long.
- Daily closes fire it a session late.

**5. The early roll is mixed too**, `shield` against `shield_hold`: +0.3 XSP, −1.3 QQQ, +0.6 GLD,
+2.5 IWM, −1.2 SLV. On XSP it sells about a quarter more shorts (1,029 against 811), most of its
rolls coming early (944). That the sign is unknown is the reason both arms run.

**What to expect from the paper arms**, then:
- near-zero alpha on XSP, QQQ and GLD;
- negative alpha on IWM and SLV;
- a large and real reduction in cost against control on every symbol.

**SLV is the live pilot because of buying power** (about $2.3k a contract, where XSP's debit does not
fit the account), **not because of this table**, where it is the worst of the five. The pilot tests mechanics in an
IRA (gate 0, plan Part 2). It is not a bet on the replay.

## Why these five symbols

From the 2026-10-04 scorecard over 24 candidates. OCC volume is averaged over 25 sessions. The spread
is the measured ATM monthly. VRP is the RMS ratio of the symbol's own vol index to the realised vol
of the following 21 sessions, since 2011.

| symbol | calls/day | expiries ≤35d | ATM monthly spread | dividends | VRP | corr SPY | worst week |
|---|---|---|---|---|---|---|---|
| XSP (SPY) | 5.5M | 13 | 0.4% | none refused (cash-settled) | 1.14 | 1.00 | −15.0% |
| QQQ | 3.8M | 13 | 0.7% | 4/yr, 0.41% | 1.08 | 0.92 | −11.5% |
| IWM | 0.67M | 13 | 0.8% | 4/yr, 0.97% | 1.11 | 0.85 | −19.4% |
| GLD | 0.30M | 13 | 3.1% | none (grantor trust) | 1.09 | 0.18 | −12.0% |
| SLV | 0.27M | 9 | 2.5% | none (grantor trust) | 1.06 | 0.29 | −26.3% |

- **XSP** stays. It is cash-settled and European: no early assignment and no ex-dividend refusal. It
  is the arm's cleanest symbol, and the only one with an IV level fitted to a traded benchmark.
- **QQQ** and **IWM** are the deepest equity-ETF chains after SPY. Each pays quarterly, so a short
  spanning an ex-date is refused. The replay puts that at about 1.4 weeks per ex-date without a short
  (93 weeks on QQQ over the window).
- **GLD** pays no dividend and is nearly uncorrelated with the rest, the one real diversifier here.
  Its spread is the widest of the five.
- **SLV** pays no dividend, is cheap enough to trade live in a small IRA, and has the weakest variance
  premium of the five. It is kept for the live pilot and measured on paper beside it.

**Turned away:**
- **TQQQ** (the symbol these replace): decay, about eight weeks a year below −7%, a −37.5% worst
  week, and no vol index of its own.
- **DIA, EEM, EFA, FXI, XLK, XBI**: too thin, under 60k calls a day.
- **TLT and HYG**: monthly dividends, so a refused short every month.
- **EWZ, USO, GDX, KWEB, ARKK, KRE, SMH, XLE, XLF**: ATM spreads of 5.5% to 29%, most with fat
  tails.
- **IBIT**: too short a history to replay.

## The year-long expiry: why the pick is a standard monthly

The probe's first run, 2026-10-04:
- The nearest-to-360 rule chose the end-of-quarter 2027-09-30 on four of the five symbols, and those
  grids stop far above the 0.90–0.95 band. SLV's lowest strike was 39 at a 54.74 spot, about 0.85
  delta; QQQ's was 525.
- The third-Friday 2027-09-17 lists deep strikes everywhere: SLV from 5, QQQ from 285, GLD from 205,
  IWM from 120, XSP from 50.
- So `clock.leap_expiration` now picks among the standard monthlies in [240, 540] DTE, and falls back
  to any listed date only when no monthly is in the band.
- Re-probed, all five pick 2027-09-17. XSP lists 46 strikes in its deep band, QQQ 48, GLD 27, IWM 20.

## What the replay leaves out

- **Daily closes:** an intraday roll trigger fires a session late.
- **Not modelled:** early assignment, pin risk, settlement fees, and the gap between the roll time
  and the bell.
- **The IRA buyback is free here:** an expiring short is bought back at intrinsic, so an OTM buyback
  costs nothing in the replay and a few cents in life.
- **Spreads are assumed** except XSP's, which come from the ledger. The year-long leg's spread is
  assumed everywhere.

The paper arms record each of these as they happen; the replay cannot.
