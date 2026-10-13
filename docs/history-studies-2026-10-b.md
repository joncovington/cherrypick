# History studies, October 2026 (b)

Seven questions answered from history we already hold, rather than from months of paper. Each
section gives the sample, the net, the drawdown and a verdict, and names the script that reproduces
it. Every script reads the live stores read-only (`mode=ro` SQLite, the Cboe files the market-files
fetcher keeps, the technicals history store) and writes nothing under `~/.cherrypick`.

| # | Question | Script | PR |
|---|---|---|---|
| 1 | Recover the scratchpad studies | `curve_replay.py`, `flies_range_gate.py`, `flies_close_scaling.py` | this one |
| 2 | How often does curve's `hook` fire, and what does it earn? | `curve_replay.py --section hook` | this one |
| 3 | A deterministic pmcc roll policy | `pmcc_shield_replay.py --policy-study` | #182 |
| 4 | The /VXM half-size second-month arm | `contango_vx_replay.py`, `contango_replay.py --validate` | #191 |
| 5 | Premium-selling cycle and the education recipes | `premium_cycle_replay.py` | PREMIUM_PR |
| 6 | calendars' replay "validated to the cent" | `exit_policies.validate_against_control` | #180 |
| 7 | Hourly SPX bars for close-or-hold and range features | skipped | – |

## 1. The recovered studies

All three were found in old session scratchpads and are now scripts with a `--validate` step.

- **`scripts/curve_replay.py`** (from `curve_replay.py` + `curve_width.py`, 2026-10-05). The
  intrinsic section reproduces the calibration that set `spread_width` 2.0 to within a few tenths
  on VXX data to 2026-10-02: per trade on max loss, $1 −3.5%, $2 +2.7%, $5 +1.9%, $10 +1.6% (the
  2026-10-05 run said −3.8 / +2.5 / +1.8 / +1.3). 96 trades over 8.7 years; at 10% of the account at
  risk per trade the $2 spread compounds at +2.6% a year with a −20.7% drawdown. **Validation**
  against the curve paper ledger: on all 27 recorded sessions that have a Cboe close, the replay's
  close-ratio regime matches the loop's recorded classification and hook flag (ratio difference
  median 0.0095, max 0.034); the option model prices the five recorded entries' credits at a median
  error of +1% (−22% to +8%) and their short deltas within 0.02.
- **`scripts/flies_range_gate.py`** (from `flies_range_gate.py`, `flies_range_gate2.py`,
  `rg_extra.py`, 2026-10-09). Run to 2026-10-09 it reproduces the finding behind the `range-high`
  arm on 392 paper control entries over 52 sessions (the original had 386 over 51; rows settled and
  re-feed since): entries at the session low made +$3,050 (n 116), at the high −$1,721 (n 143), and
  the arm's cut, p ≥ 0.9 with a range under 30 points, −$2,740 (n 51, completion 69%), ahead in both
  halves (+$1,527 / +$1,213) but hurting more sessions (16) than it helps (11), and −$1,629 without
  its two worst sessions. Live control, 84 entries over 17 sessions: the same cut refuses 10 entries
  that made −$472, carried by two sessions (+$82 without them). **Validation:** the study reads the
  range off the gex spot trail; the arm reads `stream_summary`. On the arm's first session
  (2026-10-12) all 4 of its fills were also "not refused" by `engine.range_high_refusal` fed the
  trail's range; no refusal at the high has happened yet to compare. The script also reconciles
  with the ledger: two sessions (07-21 and 07-23, −$1,820 together) are unscored because the trail
  was frozen or had too few prints at entry.
- **`scripts/flies_close_scaling.py`** (from `validate_scaling.py`, 2026-10-09). **Validation:** the
  unscaled training moves now equal `close_or_hold.session_moves` exactly (0.0 difference on every
  shared session at all six times; the 2–4 sessions only the module holds are the ones the test drops
  for a stuck RV grid). The scratch version did not: it read print times to the whole second and so
  sometimes took a different print inside the ±5 minute window (up to 3.2e-4 of spot, about two SPX
  points). Fixed, and the verdict is unchanged on 61 sessions to 10-09: `rv_open` beats unscaled in
  both halves at 0 of 6 times and loses coverage at all 6; `rv_60` at 1 of 6 and 2 of 6. CRPS
  differences stay within ±0.3 points on a CRPS of 7–15. Unscaled stays.

## 2. curve's `hook` arm

**n:** 21 hook days in 2,108 sessions, 2018-01-26 to 2026-10-02 (1.0%), in four episodes:
2018-02 (5 days), 2018-12 (3), 2020-02/03 (10) and 2025-04 (3). The ratio sat above 1.10 on 49
sessions. So the arm is idle for a year or more at a time, and 0 fires in 33 sessions is what the
history predicts, not a fault.

**The arm as built cannot hold a position.** `management.evaluate` closes any position of a flip
arm (`control`, `hook`, `near`) whenever today's regime reads backwardation, i.e. a ratio at or
above `contango_max` 0.97. The hook only fires above 1.10. So the tick after a hook entry would close
it on `regime_flip`, and every hook trade is a round trip that pays its costs and nothing else.

| Arm (2018-01 → 2026-10, $2 structure, modelled marks) | n | win | mean / trade | total | CAGR at 10% risk | max DD |
|---|---|---|---|---|---|---|
| control (contango, flip exit) | 209 | 61% | −4.9% of ML | −10.3 ML | −11.4% | −65.5% |
| noflip | 141 | 90% | −2.6% | −3.6 ML | −4.4% | −33.9% |
| hook as built (flip on) | 21 | 0% | −5.4% | −1.1 ML | −1.3% | −10.8% |
| hook without the flip | 5 | 80% | −19.5% | −1.0 ML | −1.2% | −10.5% |

ML is the spread's max loss. Without the flip, four hook trades took small profits (+0% to +3% net)
and the fifth, entered 2020-02-26 at the start of the COVID spike, lost the full width (−105%).
Over IV ×0.8 and ×1.25 the hook rows barely move (it is a costs-and-one-tail story); control and
noflip stay negative throughout.

The mark path is modelled (VXX IV from VVIX, fitted to the ledger's five entries, all calm days), so
the control and noflip rows are less trustworthy than the intrinsic section's +2.7% a trade at
recorded credits. The difference between them is the module's own exits: a modelled control
re-enters after every profit-take and pays a round trip each time (costs are about 5.5% of max loss
per trade), and 78 of its 209 exits are flips, which close into the spike.

**Verdict.** Retiring `hook` at the 2026-10-19 boundary is right. It fires about twice a year, in
clusters, and as built it can only lose its costs, because the flip exit it shares with control
closes it on the next tick. Held to control's other exits it would have made pennies four times and
lost the whole width once in 2020: no edge on five trades, and the one loss is the trade's known
tail. If a hook arm is ever revived, it needs its own exit (no flip, or a flip keyed to the ratio
rising again) declared up front. Separately, the modelled control's flip exit fires into spikes 78
times in 8.7 years, which is worth a look at the same boundary.

## 3. pmcc roll policy (#182)

The policy, declared before any run: `shield_hold` plus an early roll of the weekly short only when
it is both spent (extrinsic ≤ 15% of its value at sale) and deep in the money (delta ≥ 0.90), at
least a day before expiry. No breach roll. 2011-03 to 2026-10-02, each symbol at its model skew `b`
(0.20 for XSP, QQQ, IWM, SMH; 0.05 for GLD, SLV). CAGR and alpha are % a year; windows are the
11 five-year windows starting each January.

| Symbol | shield_hold CAGR / alpha / DD | shield (early roll) | roll_policy | roll_policy windows won |
|---|---|---|---|---|
| XSP | 4.9 / +1.3 / −18.7 | 5.2 / +1.6 / −11.6 | 4.0 / −1.1 / −23.2 | 6/11 |
| QQQ | 5.3 / −0.2 / −22.3 | 4.0 / −1.5 / −14.1 | 3.4 / −4.0 / −26.8 | 5/11 |
| GLD | 2.8 / −0.3 / −13.7 | 3.4 / +0.3 / −10.4 | 3.7 / +0.3 / −12.6 | 6/11 |
| IWM | −0.0 / −3.6 / −34.7 | 2.4 / −1.1 / −19.7 | 0.5 / −3.7 / −36.8 | 5/11 |
| SLV | −4.1 / −6.3 / −52.8 | −4.8 / −7.5 / −58.0 | −3.9 / −6.5 / −52.8 | 2/11 |
| SMH (proxy IV) | 17.6 / +9.6 / −24.5 | 13.4 / +5.0 / −28.3 | 14.3 / +3.5 / −37.9 | 3/11 |

n: 717–839 weekly shorts and 18–24 long positions per symbol and arm. SMH is priced off VXN × 1.79
before VXSMH exists (2025-09), so read that row as a proxy.

**Skew.** Over b = 0, 0.2 and 0.4 the policy's alpha is below `shield_hold`'s in 16 of 18
symbol-skew cells. `--skew-check` ran on the 4 sessions of `pmcc_skew_samples` on file (2026-10-06 to
10-09): too few to calibrate b, and what it mainly shows is the model's IV *level* reading low against
this week's market (observed 1.05–1.31× the model for the weekly ATM call, 1.14–1.61× for the year). Measured against the observed ATM, the weekly short's skew is about
0.06–0.16, inside the 0–0.4 range tested.

**Verdict.** The declared roll policy does not beat `shield_hold`. Rolling a spent deep-ITM short up
to a fresh 0.70-delta strike after a rally adds delta into strength (XSP beta 0.18 → 0.33), which
deepens drawdowns on four of six symbols and costs alpha on four. `shield`'s breach roll does the
opposite and has the smallest drawdown on four of six. No change to the arms on this evidence; the
advisor has no deterministic roll policy worth reading yet.

## 4. contango: the /VXM half-size second-month arm (#191)

Short second-month VX(M), rolled 5 sessions before the front settles, sized at ½× notional, gated by
the same rule as each contango arm, fills at the settle, costed at $2.00 a VXM side (VX $3.50) plus
half a tick, cash at SHV less posted margin (45% of notional assumed). Same sessions for every row,
2018-03-01 to 2026-10-02:

| Arm | CAGR | Max DD | Worst day | Trades (rolls) |
|---|---|---|---|---|
| SVXY control | 7.4% | −39.8% | −17.0% | 165 switches |
| /VXM ½× control, $100k | 0.9% | −48.2% | −13.9% | 258 (83) |
| SVXY flipexit | 9.8% | −41.6% | −17.0% | 81 switches |
| /VXM ½× flipexit, $100k | 3.7% | −44.6% | −13.7% | 182 (88) |
| /VXM 1× control, $100k | −5.4% | −79.8% | −27.3% | 275 (83) |

From 2013-05 against SVXY rebuilt from the settles (0.981 daily correlation with the real fund, 1–2
points a year rich): /VXM ½× 2.7% and 5.9% against 7.7% and 11.5%. With no costs at all it still
trails (5.8% and 7.2% from 2018). Peak margin use was about a third of NAV, so margin is not the
obstacle; the monthly roll costs about 3.5% of NAV a year.

**Validation of `contango_replay.py`** against the paper ledger: the recorded rule reproduces from
the ledger's own ratio and state on 8 of 8 arm-sessions, and the replay's decision and fund match on
6 of 6 sessions with a Cboe close. 2026-10-08 has no session row at all (the loop was not running at
15:50; the arms held SVXY, as the replay does). Fund prices could not be checked: the technicals
store ends 2026-10-02. Shown to fire on a deliberately broken ledger copy.

**Verdict.** The arm is not built. Second-month VX carries less roll-down than SVXY's 30-day blend
and pays a monthly roll on top, so at ½× it earns 4–6 points a year less than SVXY with drawdowns as
deep or deeper. Futures' advantages (capacity, 1256 tax treatment, no issuer risk) are not worth
that at this size. 1× notional remains unsurvivable. This replaces "after a quarter of SVXY paper".

## 5. The premium-selling cycle and the education recipes

PREMIUM_SECTION

## 6. calendars' replay validation (#180)

The derivation was described everywhere as validated against the real books to the cent, but
`validate_against_control` defaulted to $0.50. Checked read-only at $0.01 first: `control` matched
every graded week within a cent (rounding), but `path` was $0.44–0.49 rich on all of them. The
recorder marks open legs only, so the Monday disposal tick is the last it writes and lands before
the 09:45 disposition minute; the derivation then falls back to the recorded close price, and that
fallback left out the disposal's fee and slippage. Fixed, and the default is now $0.01: all twelve
graded week-arms agree within a cent (path $0.00, control −$0.01). `expiry-longs-mon` in the policy
table drops by $0.44–0.49 a week, a correction rather than a measurement change. The wording now
holds, so root `CLAUDE.md` is unchanged.

## 7. Hourly SPX bars: skipped

Neither test is cheap on hourly bars. The bars are not stored anywhere locally; the broker serves
them from 2025-12-22 (`docs/range-features.md`), so getting them means a live DXLink session, which a
read-only history study should not open. And the resolution does not fit either question:
close-or-hold scaling is built on 5-minute realised vol (an hour gives at most six returns a
session), and the range-high cut is an intraday position-in-range at the entry instant, which hourly
highs and lows would place up to an hour late. Both stay on the gex spot trail; re-run them at about
120 sessions of trail (close-or-hold) and at the arm's 14-session read (range-high).
