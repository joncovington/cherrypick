# History studies, 2026-10 (c): post-earnings drift, and the expected-move box edge

*Run 2026-10-17. These are the first two tests from [edge-hypotheses-plan.md](edge-hypotheses-plan.md).
Each pass test was declared in its script's docstring before the first full run. Both scripts are
read-only.*

## 1. Post-earnings drift (`scripts/pead_history.py`): no drift, hypothesis closed

**The question.** A put credit spread sold under the pre-report price after a stock gaps up on
earnings (video 20 of the review) only earns if such stocks keep drifting up for the weeks the spread
is held. That is post-earnings announcement drift. This measures the drift from daily bars, before
any option is priced.

**Data.**
- Events: Dolt `earnings.earnings_calendar`, 2020-01-22 to 2026-10-02, for every symbol the chain
  clone lists.
- Prices: adjusted bars from the technicals history store. Returns are abnormal against SPY over the
  same two dates.
- Entry: the reaction-day close.
- Sample: 44,549 events over 2,150 symbols (482 dropped for a missing session, 118 for no bars).
  13,816 pass the liquidity screen ($100M a day, price ≥ $10).

**The video's screen, as close as daily bars allow (`rule`):**
- liquid;
- in an uptrend (close above its 50-session mean, the 50 above the 200);
- reaction between 0 and +10%;
- the symbol held at least 6 of its last 8 positive reactions 21 sessions later.

458 events qualify.

**Result.** Abnormal return from the reaction close, with t over calendar-week means:

| Group | n | 8 sessions | 21 sessions | 30 sessions |
|---|---|---|---|---|
| liquid, all | 13,816 | −0.15% (t −0.0) | −0.08% (t +0.2) | −0.27% (t −0.9) |
| gap up (liquid, reaction > 0) | 6,910 | −0.12% (t −0.9) | −0.13% (t −1.1) | −0.28% (t −1.7) |
| gap down | 6,884 | −0.17% (t +0.3) | −0.00% (t +1.0) | −0.26% (t +0.3) |
| the screen without the history test | 2,389 | +0.00% (t +0.8) | −0.12% (t +0.6) | −0.46% (t −0.9) |
| **the screen (`rule`)** | **458** | **−0.27% (t +0.1)** | **−0.79% (t −0.9)** | **−0.64% (t −1.2)** |
| screened names that failed the history test | 1,210 | −0.09% (t +1.5) | −0.15% (t +1.0) | −0.81% (t −1.0) |
| reaction ≥ +10% | 938 | +0.13% (t +0.0) | +0.48% (t +0.6) | +0.78% (t +0.9) |

The `rule` group's 21-session abnormal return by year:

| 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| −0.83% (n 64) | +3.27% (n 30) | −1.85% (n 61) | −0.63% (n 107) | −1.02% (n 98) | −1.31% (n 96) |

**Declared pass test: fail on every count.** The mean is negative, t is −0.9 (not ≥ 2), only 1 of 6
years is positive, and the drift is not positive in both halves.

**What this means:**
- In this sample (optionable US names, 2020–2026), a gap-up on earnings is not followed by drift
  beyond the market, at any horizon from a week to six weeks. The textbook anomaly, documented mainly
  on older and smaller-cap samples, does not show up here.
- The video's "held its gap before" screen makes it worse, not better.
- The spread still wins often (the stock is above its pre-report close 21 sessions later on 67% of
  `rule` events). But that is just a strike placed below the price after a gap. The option market
  prices that distance; there is no drift for the seller to collect.
- Step 2 (pricing the spread from the chains) is not built. Hypothesis 1 is closed.

**Caveats.**
- Daily bars only. Entry at the reaction close misses the video's possibly earlier entry.
- The history screen needs four earlier positive reactions, so `rule` is thin before 2022.

## 2. The expected-move box edge, and two direction reads (`scripts/box_touch_history.py`)

**The question.** The reconstructed Daily Profit Play
([pulver-daily-profit-play.md](pulver-daily-profit-play.md)) earns only if SPX closes back inside the
gap-adjusted expected-move box after touching its edge more often than chance. The same sessions also
answer hypothesis 4:
- *the fade:* does price move back after touching the expected move early in the day?
- *the trend:* does the 1-minute 20/40 EMA call the side of the close?

**Method, as declared.**
- The gap-adjusted "widest" box: upper = max(open, prior close) + EM, lower = min(open, prior close)
  − EM. EM is `core.structures.expected_move` on the 0DTE ATM straddle at the open.
- After the first edge touch before 15:00 ET: is the close back inside?
- That rate is compared against placebo levels inside the box (open ± 0.5 and 0.75 EM), where a
  martingale predicts the same rate. Rates are resampled by session.
- Pass needs at least a 10-point excess over the placebo, its interval above zero, and two-thirds of
  years positive.

**Data and where the result lives.** The run reads the backtest store's licensed 1-minute SPX history.
Under that licence no result derived from it enters this repo, so the outcome, tables and per-year
figures are kept under `~/.cherrypick/data/backtest/results/research/` (`box-touch-0.85.json`, with a
1.0-factor sensitivity run beside it). The script can be re-run against the recorder's own
`gex_history.db` with `--history-db`, and results from our own recording may be written here once
enough sessions carry opening quotes (from 2026-10-13).

**The decision the result drove** (stated here because it is ours, not the vendor's): the
resting-order fill model (step 3 of the Pulver plan) and the fade and trend pricing are **not being
built**. Hypotheses 4 and 8 are closed unless a new, declared question reopens them.

## 3. A down-day entry filter for the put BWB (`scripts/bwb_entry_split.py`): no filter, closed

**The question.** Video 11 enters its put BWBs on down days and avoids all-time highs. The base
structure has no edge from 2011 ([history-studies-2026-10.md](history-studies-2026-10.md)), so a
filter would have to create one.

**Data.** `scripts/bwb_history_backtest.py run --start 2011-01-03 --bars auto`:
- 3,905 modelled positions, 2011-01-05 to 2026-10-15 (SPY bars to 2023-09, SPX after);
- all three arms negative (control −$107k, delta −$192k, bounce −$99k).

Each entry is split by the S&P's previous-session return and by how far its previous close sat below
the running high, both known before the 10:00 entry.

| Entries | n | control net | per trade | bounce per trade |
|---|---|---|---|---|
| all | 3,905 | −$106,997 | −$27.4 | −$25.4 |
| **previous session ≤ −0.5%** | **837** | **−$17,422** | **−$20.8** | −$14.1 |
| between | 1,984 | −$64,841 | −$32.7 | −$34.3 |
| previous session ≥ +0.5% | 1,084 | −$24,734 | −$22.8 | −$17.8 |
| within 1% of the high | 1,360 | −$36,228 | −$26.6 | −$26.7 |
| 1–3% below | 744 | −$12,142 | −$16.3 | −$0.8 |
| 3–10% below | 938 | −$37,344 | −$39.8 | −$51.7 |
| more than 10% below | 863 | −$21,282 | −$24.7 | −$15.8 |

**Declared pass test: fail.** The down-day subset is negative in total, and positive in only 4 of 16
years (2017, 2021, 2025, 2026).
- Down-day entries lose less per trade than the rest, which fits richer premium after a drop, but
  not enough to turn positive.
- "Avoid all-time highs" doesn't hold up: entries near the high do no worse than those 3–10% below.

The video's own 21 DTE take-profit shape is not modelled. With no filter and no base edge, it isn't
built. Hypothesis 6 is closed.

## 4. The dynamic PMCC's trend signal (`scripts/ema_trend_signal_history.py`): no signal, closed

**The question.** Video 15 chooses the weekly short call's delta from the daily 8-vs-21 EMA:
out of the money in an uptrend, in the money in a downtrend. That only beats pmcc's fixed 0.70-delta
short if the EMA state predicts the next week.

**Data.** Adjusted daily closes from the technicals store, 2011 on. One sample per week at the
week's last close (so samples don't overlap); the outcome is the next week's return.

| Symbol | Up-week minus down-week return | t (Welch) | Hit rate | Before 2019 | 2019 on |
|---|---|---|---|---|---|
| SPY | −0.31% | −1.5 | 52.4% | −0.54% | −0.07% |
| QQQ | −0.41% | −1.7 | 52.0% | −0.63% | −0.18% |
| GLD | −0.03% | −0.2 | 48.5% | −0.03% | −0.12% |
| IWM | −0.24% | −1.0 | 50.4% | −0.13% | −0.34% |
| SLV | −0.03% | −0.1 | 48.5% | +0.09% | −0.31% |
| SMH | −0.51% | −1.6 | 52.1% | −0.37% | −0.67% |

**Declared pass test: fail, 0 of 6 symbols.** On every symbol the week after an "up" state returned
*less* than the week after a "down" state. None of these differences is significant. The sign is the
point: over a week, these indexes revert slightly rather than trend. So the dynamic rule (shorter
delta in uptrends, in-the-money shorts in downtrends) sells the weeks that bounce.

The variant is not added to `pmcc_shield_replay.py`. Hypothesis 5 is closed.
