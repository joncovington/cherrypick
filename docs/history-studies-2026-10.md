# History studies, October 2026

Four questions that the suite was waiting on live data to answer. Each one can be answered now from
history already on disk. Each section names its script, what it read, and what it found, and ends
with a verdict. These are research results. No module's behaviour changed because of them.

**Modelled history is a model.** The bwb results come from a price model fitted to seven weeks of
calm paper data. They describe what the module's rules would have done under that model. They are
not what the module would have made. Section 0 says how close the model gets on the weeks where the
real result is known.

Run on 2026-10-12, with Cboe files fetched the same day:

```
python scripts/bwb_history_backtest.py fetch .tmp/cboe
python scripts/bwb_history_backtest.py validate --cboe-dir .tmp/cboe --verbose
python scripts/bwb_history_backtest.py run --start 2024-10-07 --end 2026-10-01 --cboe-dir .tmp/cboe --validate
python scripts/bwb_history_backtest.py bars-check --cboe-dir .tmp/cboe
python scripts/bwb_regime_gate_history.py --cboe-dir .tmp/cboe
python scripts/spy_variance_premium.py --cboe-dir .tmp/cboe
python scripts/bwb_cvar_calibration.py --cboe-dir .tmp/cboe
python scripts/impliedvar_validate.py --cboe-dir .tmp/cboe
```

Every script is read-only against the stores. The bwb studies refuse to report unless the model
still reproduces the paper ledger, and the implied-variance studies refuse unless
`core.impliedvar` still reproduces VIX.

## 0. Recovered studies and their validation

Two studies from 2026-10-09 were left only in session scratchpads. Both are now scripts with a
refusal built in.

**`scripts/bwb_history_backtest.py`** is the two-year bwb backtest, cleaned up. It covers the smile
fit, the run, and a validation against the paper ledger. Where SPX bars are missing (before
2023-08-30), it uses SPY bars scaled by Cboe's SPX close. The model's trade comes from the module
itself (`clock`, `engine.select_strikes` and the fee stack). Prices come from a VIX9D-scaled put
smile fitted to the ledger's recorded legs. The script's docstring states every modelling choice.

Validation on 2026-10-12 covered 32 settled paper entries, 2026-08-24..10-08. A position whose
add-on was stamped missed during the 10-08 outage is left out for that arm only. The declared
tolerance: whether the add-on fired agrees on at least 80% of positions, net per trade is within
$25, entry credit MAE is at most $0.20, and each arm has at least 20 positions. Result: **PASS**.

| | ledger | model | bias / trade | fired: agree | fired in both | same day | within 1 session |
|---|---:|---:|---:|---:|---:|---:|---:|
| control (n 32) | $1,685 | $1,908 | +$7.0 | | | | |
| delta (n 30) | $6,472 | $6,842 | +$12.3 | 93% | 14 | 9 | 13 |
| bounce (n 30) | $5,470 | $5,718 | +$8.3 | 93% | 13 | 4 | 10 |

Entry credit: bias +$0.009, MAE $0.104. Expected move: MAE 6.0 points.

**The model is generous by $7-12 a trade.** Read every modelled loss below as an understatement of
the real loss. The 2026-10-09 note said "same fire day 13/13". The true figure is that both fired
13/13 at the time. For delta, 9 of 14 fire on the same day and 13 of 14 within one session. For
bounce, 4 of 13 fire on the same day. Daily bars do not see a 60-second loop's bounce timing.

**Reproduction.** The 2024-10-07..2026-10-01 run reproduces the 2026-10-09 figures to the dollar:
498 entries, with control −$9,128, delta −$9,983 and bounce −$131.

**The SPY proxy** (`bars-check`) was compared with real SPX bars on their overlap, 2023-09..2026-10.
That is 778 positions. Bar error is 8.8 bps MAE on the open and 3-4 bps on the high and low. The
backtest run both ways gives:

| | SPX bars | SPY proxy |
|---|---:|---:|
| control | −$17,735 | −$19,166 |
| delta | −$28,243 | −$25,170 |
| bounce | −$8,522 | −$8,216 |

Add-on fires agree 95.8% (delta) and 93.3% (bounce). The proxy moves results by $2-4 a trade, which
is smaller than the model's own bias.

**`scripts/impliedvar_validate.py`** is the `iv_validate.py` study recovered. It compares SPY's
30-day model-free implied vol, computed from Dolt's end-of-day strips, with the VIX close, every
Wednesday from 2019-02. The tolerance is correlation ≥ 0.99, MAE ≤ 0.75 points, ≥ 90% within 2
points, and ≥ 90% of dates computed. It now reads Dolt one date at a time. A `SELECT DISTINCT date`
over `option_chain` ran past ten minutes against the shared server and was killed. Result on
2026-10-12: **PASS**. 336 of 337 dates computed, correlation 0.9956, MAE 0.51 points, mean −0.37,
96.4% within 2 points, 0% complete strips. This reproduces the 2026-10-09 figures. The worst gaps
are the stress days (2020-03-11 −6.9, 2020-03-18 −6.5, 2024-12-18 −4.6).

## a. The VIX9D/VIX entry gate and the bounce add-on, 2011-2026

`scripts/bwb_regime_gate_history.py`. The live gate `live.vix9d_vix_max = 1.0` (PR #162) refuses an
entry when VIX9D/VIX ≥ 1.0. It was picked as the best of six gates on the 2024-10..2026-10 run, so
that window is in-sample. **2011-01..2024-10 is the out-of-sample test.**

Method:

- Positions are scaled to SPX 7000 at entry, so 2011's trade is today's trade. The $5/$10 wings are
  fixed in points.
- The gate reads the prior session's closes. The prior close and the same day's close fall on the
  same side of 1.0 in 85.5% of sessions, so a 10:00 reading will sometimes disagree.
- Sessions are settlement Fridays.

Out of sample (2011-01..2024-10), 3,455 entries. The gate kept 2,486 and skipped 969.

| arm | ungated | gated | per trade, gated | per trade, skipped | max drawdown ungated → gated |
|---|---:|---:|---:|---:|---:|
| control | −$84,139 | −$48,244 | −$19.4 | −$37.0 | $88.7k → $50.9k |
| delta | −$131,263 | −$70,262 | −$28.3 | −$63.0 | $144.0k → $80.2k |
| bounce | −$62,936 | −$29,383 | −$11.8 | −$34.6 | $75.3k → $46.7k |

In-sample (2024-10..2026-10, 498 entries, 420 kept), gated: control −$4,690, delta +$236, bounce
+$4,785. The skipped entries lost $66-135 a trade.

The add-on, paired position by position, per settlement session:

- **Bounce minus control:** out of sample +$21,203 ungated (t 0.99) and +$18,861 gated (t 1.09).
  Over the whole history +$30,317 (t 1.33).
- **Delta minus control:** out of sample −$47,124 (t −1.46).

Per settlement year, ungated / gated (gated kept n of all n):

| year | control | delta | bounce | kept |
|---|---:|---:|---:|---:|
| 2011 | −9,121 / −2,580 | −13,269 / −2,049 | −9,569 / −2,355 | 115/248 |
| 2012 | −5,459 / −2,460 | −8,484 / −1,623 | −1,294 / 4,775 | 179/249 |
| 2013 | 1,748 / 2,499 | 9,041 / 7,982 | 14,270 / 8,244 | 168/251 |
| 2014 | −2,689 / −4,029 | −3,063 / −7,346 | −1,347 / −4,877 | 186/251 |
| 2015 | −15,000 / −13,152 | −29,834 / −27,622 | −21,621 / −20,453 | 177/254 |
| 2016 | −5,422 / −3,277 | −925 / 1,126 | −3,313 / −1,757 | 197/252 |
| 2017 | 5,799 / 4,992 | 17,111 / 14,735 | 18,572 / 16,617 | 232/251 |
| 2018 | −11,598 / −2,463 | −23,669 / −2,302 | −13,348 / 1,490 | 156/250 |
| 2019 | 2,122 / 1,652 | 9,461 / 6,086 | 8,072 / 3,789 | 184/250 |
| 2020 | −6,518 / −4,992 | −10,863 / −9,379 | −9,102 / −7,006 | 191/255 |
| 2021 | −3,917 / −5,021 | −6,515 / −9,314 | −7,579 / −8,573 | 227/252 |
| 2022 | −26,942 / −14,386 | −55,598 / −29,167 | −34,344 / −17,015 | 155/249 |
| 2023 | −2,633 / −1,934 | −5,942 / −5,504 | 1,642 / −853 | 179/250 |
| 2024 | −7,295 / −5,455 | −16,409 / −12,515 | −5,798 / −2,574 | 189/250 |
| 2025 | −4,927 / −3,262 | −5,818 / −2,643 | −265 / 1,571 | 204/249 |
| 2026 | −1,845 / 1,256 | 4,505 / 10,758 | 1,646 / 5,502 | 172/197 |

Threshold sensitivity out of sample, net per trade kept (skipped):

- below 0.90: control −12.9 (−29.2), bounce −9.1 (−22.1), n 1,034
- below 0.95: control −18.7 (−30.4), bounce −14.6 (−22.1), n 1,790
- below 1.00: control −19.4 (−37.0), bounce −11.8 (−34.6), n 2,486
- below 1.05: control −21.9 (−41.2), bounce −14.1 (−46.2), n 3,012

These are four looks at one series. Read them as a shape, not as a menu to choose from.

The live pilot's shape is bounce, one lot, Tuesdays only, gated:

- all Tuesdays: n 813, −$15,368
- gated Tuesdays: n 570, −$4,893 (−$8.6 a trade), max drawdown $18.3k
- gated Tuesdays out of sample: n 484, −$9,720 (−$20.1 a trade)

**Where the model is weak:**

- VIX9D starts in 2011, so there is nothing before it. That leaves no 2008.
- The smile was fitted at VIX9D 11-15, and every stressed price is an extrapolation. The fitted
  shape is multiplicative in VIX9D, so a stress-day skew steepening beyond it is not modelled.
- Entry is at the open, and triggers are on daily bars. The trigger level uses that day's VIX9D
  close, a small look-ahead kept so the validation reproduces.
- Before 2023-08-30 the bars are the SPY proxy.
- The model is $7-12 a trade generous against paper.

**Verdict.**

- **Keep the gate on live.** It is the one finding that holds out of sample, and it holds every way
  it was cut. Skipped entries lose about twice as much per trade in every arm. The gate roughly
  halves drawdown.
- **The gate does not make the trade profitable.** Gated bounce is −$11.8 a trade out of sample.
  That is before the model's ~$8 generosity, and it is negative in 9 of 16 years.
- **Bounce beats control by about $6 a trade.** That is consistent but not significant (t ≈ 1).
  **Delta is worse than control out of sample.** The paper book's delta lead is the 2026 regime.
- **The live pilot as planned (gated bounce, Tuesdays) has negative modelled expectancy:** −$20 a
  trade over 484 out-of-sample entries. Arm it to measure fills and execution, not for edge, and
  size it as a cost.
- **Don't tune the threshold off this table.**

## b. The short-dated S&P variance premium

`scripts/spy_variance_premium.py`. This is the question `bwb iv-premium` is waiting on, with the
first windows settling 2026-10-16: does short-dated implied vol exceed what the index then realises?

**VIX9D against SPX realised over the next 9 calendar days, 2011-01..2026-10.** n 3,960 overlapping
windows. The t statistic uses every 7th window (n 566).

| slice | n | implied | realised | premium mean / median (vol pts) | implied above | t |
|---|---:|---:|---:|---:|---:|---:|
| all | 3,960 | 17.47 | 13.80 | +3.67 / +4.41 | 83% | 12.5 |
| VIX < 30 | 3,727 | 16.07 | 12.49 | +3.57 / +4.31 | 82% | 16.4 |
| VIX ≥ 30 | 233 | 39.86 | 34.65 | +5.21 / +7.39 | 84% | 2.0 |
| VIX9D < VIX | 2,910 | 15.09 | 11.36 | +3.72 / +4.24 | 84% | 17.5 |
| VIX9D ≥ VIX | 1,050 | 24.07 | 20.55 | +3.52 / +5.03 | 80% | 3.6 |

The mean is positive in every year. The weakest years are 2018 (+2.1) and 2022 (+2.2), and the
strongest are 2021 (+5.4) and 2012 (+5.2). The worst single window is −74.5 points (from 2020-03-10).

**Dolt's SPY strip through `core.impliedvar`** is the arithmetic bwb records at entry. It runs
Wednesdays 2020-01..2026-09, n 334, at the nearest expiry Dolt lists (12-16 days, median 14; Dolt
holds nothing shorter). The 30-day validation check above passed on the same dates. The strip's
reading tracks VIX9D at correlation 0.989, with a mean gap of −0.17 points.

| slice | n | implied | realised | premium mean / median | implied above | t (every 2nd) |
|---|---:|---:|---:|---:|---:|---:|
| all | 334 | 19.40 | 16.20 | +3.19 / +4.25 | 80% | 4.8 |
| + that day's 30-day shortfall vs VIX | 333 | 19.74 | 16.21 | +3.53 / +4.37 | 82% | 4.5 |
| VIX < 30 | 308 | 17.71 | 14.58 | +3.13 / +4.06 | 80% | 5.4 |
| VIX ≥ 30 | 26 | 39.42 | 35.46 | +3.95 / +10.34 | 88% | 0.4 |

**No Dolt strip is complete.** Every implied reading, and so every premium, is a lower bound. The
shortfall is largest on stress days, when the wings are fattest: VIX ≥ 30 is the slice where the
figure is least trustworthy, and in the direction of understating the premium. The "+ shortfall"
row adds that day's 30-day gap to VIX as a rough correction. It is a different tenor, and SPY is not
SPX.

**Verdict.** The short-dated variance premium exists and is robust: about +3.5 vol points at 1-2
weeks, with implied above realised four times in five, in every year since 2011. The 10-16
`iv-premium` reading will confirm or contradict this. It is not the evidence.

**This is not evidence that bwb makes money.** A positive variance premium is a statement about the
whole strip. bwb is short a narrow put fly one expected move down, and its P&L lives in where the
index lands relative to the body and wings. Section (a) says that structure loses in the model even
though the premium was there. Nothing to change. Stop treating the 10-16 settlements as a gating
question.

## c. CVaR minimum sessions

`scripts/bwb_cvar_calibration.py`, for `docs/metrics-plan.md`'s open question.
`core.metrics.CVAR_MIN_SESSIONS = 20` was proposed and never measured.

The data is 823 modelled weekly bwb sessions (2011-2026, scaled). A bwb "session" is its settlement
Friday, because the ledger keys on `closed_session`. For every contiguous window of n sessions, the
window's CVaR (the mean of the worst 10% of sessions, at least one) is compared with the whole
history's CVaR. **The criterion, declared before the run:** the smallest n with at least 80% of
windows within ±50%.

| n (k worst) | control: within ±50% / understated | delta | bounce | next 52 weeks worse by >50% (control / delta / bounce) |
|---|---|---|---|---|
| 10 (1) | 84% / 16% | 79% / 21% | 79% / 21% | 22% / 23% / 30% |
| 14 (1) | 92% / 8% | 90% / 10% | 89% / 11% | 14% / 14% / 21% |
| 20 (2) | 96% / 4% | 95% / 5% | 94% / 6% | 10% / 11% / 14% |
| 26 (2) | 100% / 0% | 100% / 0% | 100% / 0% | 2% / 2% / 7% |
| 52 (5) | 100% / 0% | 100% / 0% | 100% / 0% | 0% / 1% / 5% |

Whole-history CVaR: control −$2,026, delta −$4,886, bounce −$3,677. The criterion is first met at n
= 10 for control and n = 14 for delta and bounce.

**Why it converges so fast:** the bwb tail is capped by construction. Control's CVaR (−$2,026)
equals the median session's summed worst case ($2,051). The worst tenth of weeks are max-loss weeks,
and a max-loss week turns up in most windows of 14 or more.

**Verdict.**

- **Keep `CVAR_MIN_SESSIONS = 20`.** For a bounded-loss weekly ladder it is enough: 94-96% of
  20-session windows read within ±50% of the long-run tail. The figure it lets through understates
  the tail 4-6% of the time. For bwb, 20 sessions are 20 weeks, about five months.
- **This does not calibrate the daily modules.** meic and flies have path-dependent tails: stops,
  stranded verticals, gap-throughs. A capped weekly structure is the easy case. Their calibration
  belongs to the quote-history replay (`docs/quote-history-replay-plan.md`).
- **Regime shift is the bigger risk.** Even at n = 20, the following year's tail is more than 50%
  worse 10-14% of the time. Sampling error stops mattering past ~26 sessions.
