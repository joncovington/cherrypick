# Theta Profits' 0DTE break-even iron condor: rules from one interview, and its fit as a MEIC arm

*Drafted 2026-10-20 from the YouTube video "After 9,000 Trades, This Is Still My Most Profitable
0DTE Strategy" (Theta Profits, self-interview, ~40 min;
<https://www.youtube.com/watch?v=Fj41ojAdwJ8>). Quotes below are short excerpts for the points being
made; the video is the source. The arm at the bottom is a **proposed** registry profile; it was
installed into the machine-local registry (~/.cherrypick/config/meic.risk.json — not this repo) on
2026-10-20 at 09:59 ET and took its first trade the same minute — see Installing the arm below for
that dated record. Paper only, live untouched.*

## The short version

A **0DTE SPX iron condor sold repeatedly through the session — about one per hour — with equal
premium on both legs and a stop loss on each side set equal to the TOTAL credit collected.** One
side stopping out costs what the credit paid, so the trade lands near flat: hence "break-even
condor". The real loss only comes when **both** sides stop — he reports that at ~8% of trades. The
win rate is ~40% because most surviving trades are kept small winners (the credit minus noise), and
the edge, if it is one, is that the ~40% of trades that survive are worth more than the 8% double
stops cost.

He explicitly frames this as **the same trade as MEIC** — "essentially the same trade… MEIC is
maybe a more known name" — the multiple-entry iron condor lineage our own module is named after
(see [strategy-sources.md](strategy-sources.md), MEIC section). What he adds on top of the
mechanical version is (a) the break-even stop arithmetic, (b) an entry-stabilization read, and
(c) a hard day-risk discipline.

## Source and lineage

- **Theta Profits** — "John" (Yona Aina Sandan), ex-journalist near Oslo, trading the strategy since
  **April 2021**, self-interview format, ~9,000 trades. He cites Tammy Chambliss' "MEIC" as the
  sibling strategy ("she trades a bit more mechanical than me and typically also collects higher
  premiums") and points at the Quantum Options group. Same root as our module; this is the closest
  published relative we have found.
- His own numbers: **39.3% on the account last year**, profitable every year since losing half the
  account in 2020 (over-sizing — the lesson the 1–2% rule encodes).
- He has **not automated** it; he says explicitly the strategy needs monitoring or good automation.
  That is precisely the gap our paper engine fills — this is the rare source whose unautomated
  discretion is the thing we can measure mechanically.

## The rules, as he states them

1. **Structure:** one 0DTE iron condor = call credit spread + put credit spread, opened together but
   entered leg-group by leg-group — **call side first, put side immediately after**, never legging
   for price. "Less slippage… easier to control that you get the same premium on both sides."
2. **Equal premium both sides:** target **$100–200 per side** ($200–400 per condor; higher when vol
   is rich). The Feb 17 example collected $300 total ($150/side).
3. **Strikes:** short at **10–15 delta**. No other strike rule.
4. **Width:** starts at **30 points** each side, then **adjusted per side** (25, 35, occasionally 40+)
   to make the two sides pay equally. Width is the equalizer, not the short strike.
5. **Entry cadence:** **~one condor per hour** on average — first ~09:45, then ~10:15, after 11:00,
   after 12:00, 13:00, 14:00, and 15:00 "depending on the total risk picture". Up to 10 open at
   once is possible, ~6–7 typical by the close.
6. **Entry trigger — stabilization, not the clock:** "wait until I see two or three five-minute
   candles on more or less the same level", whether the market is rallying or dropping. He enters
   when the move pauses, not during it.
7. **Stops — the signature:** stop loss **per side = the total credit of the condor**, placed **on
   the shorts only** (not the spreads — "much less slippage… the order can rest at the exchange").
   $300 credit → a $300 stop on the put short and a $300 stop on the call short. One stop firing =
   the kept credit offsets the lost side ≈ flat; two = lose ~one wing's width net.
8. **Stop mechanics:** OCO bracket — a **stop-limit 40 points wide**, plus a **stop-market 30 points
   further out** as the last line of defense; if both occasionally fire he can end up long what he
   was short ("a few times every year").
9. **The 5-cent rule:** shorts are **bought back at $0.05** when they decay there, never held to
   expiration — removes the last-30-minute swing tail (he cites sessions where shorts closed at 5c
   then ripped), takes risk off, and frees the (now near-worthless) longs to be **re-used as
   protection for late-day strangles**.
10. **Management — stops only:** the only management is tightening stops (a) as theta decays the
    longs so a stop still means break-even, and (b) to shed total day-risk when many trades are
    threatened. **No rolls, no adjustments, no re-strikes.** If a side stops and the runner long is
    worth more than the break-even assumption, sometimes a stop-market on the long lets it run
    (April 9 2025 tariff melt-up: stopped call-side, the longs paid, the day ended profitable).
11. **Day risk caps:** never risk more than **1–2% of the account in a day**, measured as *all* stops
    hitting on *both* sides of *all* trades; never more than **50% of buying power** on the strategy
    in a day. Both caps feed back into whether he adds the next hourly entry.
12. **No hour filters, despite the stats:** his 5-year data says the **last three hours (13:00,
    14:00, 15:00 ET)** and **Mondays/Fridays** are most profitable, **Thursdays** flat — but he
    deliberately trades every hour anyway because the hour-of-day edge "varies a lot from month to
    month".

## His example, as walked through (Tue 2026-02-17, ~09:35 ET)

SPX ~6,800. Sold 6,710P / bought 6,680P and sold 6,870C / bought 6,900C — 30-wide both sides, $150
per side, $300 credit, $300 stops both sides. Ten minutes later SPX had drifted ~10 points down and
the trade was "still pretty much in break even". He notes the profile tightens fast early (a small
move eats $300 of risk when credit is thin relative to the day's range) and tolerates progressively
larger moves as theta builds — the geometry every 0DTE short-premium book lives with.

## Results as he reports them (self-reported, unaudited)

| Metric | Value |
|---|---|
| Trades / duration | ~9,000, April 2021 → mid-2026 |
| Win rate | ~40% ("break-even" one-side stops counted as small win/loss either way) |
| Double-stop (real loss) rate | **~8%**, higher in the last year than the first |
| Net profit per trade, **% of risk** (risk = widest wing − credit = buying power) | **0.28%** |
| **Premium capture rate** (net P&L ÷ total credit) | **5.65%** |
| Wins vs losses | average win > 2× average loss (positive expectancy at 40%) |
| Day/hour effects | Mon/Fri best, Thu ~flat; 13:00–16:00 entries best but unstable quarter to quarter |
| Self-rated risk | 4/10 "if you are disciplined" |

The two headline metrics are exactly the two our ledger can compute per arm: net-per-trade as a
fraction of buying power, and premium capture. An arm that replicates this shape can be scored
against him directly.

## Fit as a MEIC paper arm

**Verdict: yes, with high fidelity — the module already is this trade, and most of his rules are
existing config seams.** He is trading the same structure the engine was built for; the arm is a
parameter bundle, not new machinery. Four of his rules have no engine seam (below); none of the four
change the trade's shape, only its measurement fidelity.

### Rule → engine mapping

| His rule | Engine seam | Arm setting |
|---|---|---|
| Per-side stop = total credit | `per_side_stop_management` + `stop_trigger_ratio` — engine semantics are literally "side's cost reaches X of the total IC credit" | `true`, `1.0` |
| ~1 entry/hour, 6–7/day | `stagger_entries` (target = hard cap) + `daily_ic_trade_target` + `min_minutes_between_entries` (5-min loop → 55 ≈ hourly) | `true`, `7`, `55` |
| Sessions 09:45 → last hour | `paper_entry_window_start` / `entry_window_end` | `09:45` / `15:30` |
| Entry trigger — stabilization, not the clock ("two or three flat five-minute candles") | `stabilization_entry` + `stabilization_oracle` — the completed-bar ToS "Universal Adaptive Market Stabilization Indicator" rule (`cherrypick.meic.stabilization_gate`); opt-in per arm, default off, so the arms measuring it keep the ungated foil | `true`, `multi` (as the stab arms) |
| 10–15 delta shorts | `delta_target` flat (he does not VIX-band; mid of his range) | `0.12` |
| 30-wide wings, adjusted until the premiums match | `wing_widths_by_symbol` as a search list + `equalize_credit` — the most credit-balanced clearing candidate wins instead of the widest-first one (`max_wing_width` is documentation only — the width resolver never clamps) | `[25, 30, 35, 40]`, `true` |
| $100–200/side ≈ 3–7% of width | `min_credit_pct_of_width` — control's `0.15` would reject nearly every one of his trades on 30-wide wings; his floor is the credit itself | `0.05` |
| Call spread first, then put | `separate_spread_entry` (cosmetic in paper fills; the honest part is parity with his live order flow) | `true` |
| No regime timing (every hour) | neutralize the gate block the way `bt-1400-2x` does (VIX/ATR/VIX1D `999`/`null`, GEX block off, IV-rank floor 0, OTM floors `0.0001`, late-bias off) | as `bt-1400-2x` |
| Concurrent book ≤ 10 | `max_concurrent_ics` | `10` |
| Same-zone discipline | `overlap_scope "none"` (matches control and `bt-1400-2x`; his hourly re-entries naturally land on new strikes as the market moves) | `none` |

### The proposed arm — a registry profile named `be-hourly-1x`

```json
"be-hourly-1x": {
  "enabled": true,
  "_note": "Theta Profits' 0DTE break-even condor (docs/theta-profits-be-condor.md): ~1 IC/hour, 10-15 delta, 25-40-wide with per-side widths equalized to balance the premiums, per-side stop = total credit (the 'break-even' identity), neutralized gates like bt-1400-2x. Paper only.",
  "symbols": ["SPX"],
  "min_iv_rank": 0.0,
  "min_credit_pct_of_width": 0.05,
  "max_call_delta_entry": 0.2,
  "max_call_delta_entry_open_volatile": 0.19,
  "max_call_delta_entry_late": 0.19,
  "min_call_otm_pct": 0.0001,
  "min_put_otm_pct": 0.0001,
  "late_entry_bias_enabled": false,
  "regime_vix_pause_threshold": 999,
  "regime_atr_pause_threshold_pct": null,
  "regime_vix1d_ratio_pause_threshold": 999,
  "regime_gex_block_negative": false,
  "regime_gex_require_positive": false,
  "regime_gex_min_flip_distance_pct": null,
  "delta_target": 0.12,
  "delta_target_vix_low": 0.12,
  "delta_target_vix_elevated": 0.12,
  "delta_target_vix_high": 0.12,
  "delta_target_vix_crisis": 0.12,
  "wing_widths_by_symbol": { "SPX": [25, 30, 35, 40] },
  "equalize_credit": true,
  "separate_spread_entry": true,
  "paper_entry_window_start": "09:45",
  "entry_window_end": "15:30",
  "per_side_stop_management": true,
  "stop_trigger_ratio": 1.0,
  "overlap_scope": "none",
  "stagger_entries": true,
  "min_minutes_between_entries": 55,
  "max_concurrent_ics": 10,
  "daily_ic_trade_target": 7
}
```

Read against `control` this arm differs on exactly the axes his bundle does: cadence (7/day vs
sample-stream), width (25–40, credit-equalized, vs 5/10), delta (0.12 vs VIX-banded), stop basis
(1.0× per-side vs 0.95 whole-IC), credit floor (0.05 vs 0.15), and separate-spread entry. Like
`bt-1400-2x` it is a **bundle** replication, so a P&L difference vs control is "his package vs
ours", not any single knob. The cleanest single-axis read is against `bt-1400-2x` (same neutralized
gates, SPX-only, same engine): hourly-vs-14:00-only, 25–40-vs-10-wide, 1.0-vs-2.0 stop,
0.12-vs-0.16 delta.

### Fidelity gaps (rules with no seam)

1. **Equal premium both sides — closed the same day, 2026-10-20 12:33 ET.** The gap as written at
   install time: each short was picked by delta with one per-symbol width, so his width-asymmetry
   (25–40 to balance the premiums) was unexpressable. The engine flag `equalize_credit` (branch
   `paper/equalize-credit`, off by default, pairs hidden from every other profile) builds
   width-pair candidates — each wing sized independently from a search list — and enters the most
   credit-balanced candidate that clears every gate instead of the widest-first one; this arm's
   menu became `[25, 30, 35, 40]`, his own adjustment range around the 30-wide start. Each side's
   width is recorded as `put_wing`/`call_wing` (`wing_width` keeps the wider wing, so buying
   power and the credit floors read the same scalar as before), and a `gate_changed` break (id 9)
   quarantines the pre-flip session. Worth keeping in proportion, in both directions: the impact
   was always the *small* one — two 0.12-delta SPX shorts usually pay within a few tens of
   dollars of each other, and the break-even stop identity never depended on the split (trigger =
   ratio × total credit either way) — so the closure buys fidelity to his stated practice, not a
   different P&L shape.
2. **The stabilization read** (2–3 flat 5-min candles). CLOSED 2026-10-31: the gate exists —
   `stabilization_entry` (per arm, default off) + `cherrypick.meic.stabilization_gate`, the
   completed-bar form of the Thinkorswim "Universal Adaptive Market Stabilization Indicator" the
   read mirrors: the last two consecutive COMPLETED 5-min bars' total range within 1.25 × Wilder
   ATR(5) and ATR at or below its 20-bar average; `stabilization_oracle` picks the warmup
   semantics ("multi" — the roll carries across sessions, what a chart shows — or "perday");
   `stabilization_threshold_mode` picks the threshold basis ("atr", the script's default — or
   its "iv" alternate, close × IV × 0.006, /10 for XSP/SPY, IV from the recorded `atm_iv`
   trail, missing IV fails closed). Wired into the paper loop and the live loop; a missing read
   fails closed (the arm waits). The
   be-* arms still enter on the first passing tick — the gate is opt-in so the comparison keeps
   its foil.
3. **The 5-cent buy-back and long re-use.** MEIC has no profit target by design (cash-settled SPX
   rides to 16:00 settlement; the module's rule is explicit that this is not to be added as a
   key). Terminal P&L on the *short* is the same (5c ≈ worthless), but the arm keeps the
   last-30-minute gamma tail he buys insurance against — his stated reason for the rule. Mitigated
   but not eliminated: the window ends at 15:30, so at most an early-hour trade rides the tail.
   Long re-use for late strangles is simply out of scope.
4. **Stops on the shorts only, long managed by hand.** The engine closes the whole side (both legs)
   at the stop — his long-value capture on runners happens implicitly in that fill; what's lost is
   only his optional "stop the long and let it run" move (the April 9 case). Minor.
5. **OCO stop-limit/stop-market bracket** — live execution mechanics against exchange order types
   the paper engine doesn't model (paper uses `slippage_frac_of_spread`); no port needed, live loop
   is software-stop anyway.
6. **1–2% day risk / 50% buying power** — paper profiles have no account and model no buying power
   (sample-stream semantics; see the independent-sampling note in `config.example.json`). A 30-wide
   SPX condor risks ~$2.85k each, ~7/day: the worst-case double-stop day is a ~$20k string of
   single-wing losses, which is fine to *measure* and must be re-derived as % of account before
   anything live. `max_open_bp_dollars` exists if a dollar proxy is wanted later; deliberately left
   uncapped here, like control.
7. **Dynamic stop tightening** (re-tightening as the day decays / for day-risk) — the engine's
   stop is a fixed ratio per trade (`stop_trigger_current`), with `max_stop_adjustments_per_ic`
   bookkeeping around it; his continuous re-tightening isn't modeled. His own description says the
   fixed-ratio stop IS the break-even tightener (ratio 1.0 against a decaying long-side cost is the
   same shape), so the unmodeled part is only the discretionary risk-shedding pass.
8. **Inherited event guards.** FOMC days: the 13:30–14:30 blackout deletes one of the seven hourly
   slots and the post-blackout premium gate applies (as flagged when `bt-1400-2x` was built); he
   trades through it. Quarterly/witching gates likewise bind where his rules don't. Kept on
   purpose — the arm stays comparable to the other arms, and the removed slots are visible in the
   ledger as `fomc_blackout` declines.

### How it would be judged

Scored like every other arm from the paper ledger, on **his two metrics**: net profit per trade as
% of buying power (his 0.28%) and premium capture rate (his 5.65%), plus the double-stop rate (his
~8%) — all three computable per arm from the recorded trades. Both source metrics are per-trade
ratios, which is the honest comparison given sample-stream semantics (no account equity to compute
returns on). Minimum bar to keep running: beat control on capture rate without a materially worse
double-stop rate; the arm is a *candidate shape for the live rotation*, not a threat to anything
today. Paper only — `enable_live_trading` untouched, live arm untouched.

### Installing the arm

The JSON above is a **proposed profile**, not a repo artifact. Arm configs live in the
machine-local registry (`~/.cherrypick/config/meic.risk.json`), which is outside this repo, so the
branch and the running arm travel on different rails: this doc carries the reasoning, the operator
carries the position. To stand the arm up, merge the block into that registry's profile map and let
the paper loop (every 60 s) pick it up on its next tick — no restart, and no stream-request change
when SPX 0DTE is already subscribed. `equalize_credit` and the per-side wing search
(`wing_widths_by_symbol`) must both be present, or the arm trades the scalar-width single it used to.

The arm was **installed on 2026-10-20** and ran through the following same-day adjustment, on this
machine only. That record is kept here as a dated note rather than as a step of the proposal.

**2026-10-20, 09:56–10:00 ET.** Same procedure as `bt-1400-2x`, while that Tuesday's session was
open: registry backed up to `~/.cherrypick/state/config-backups/meic.risk.20261020-0659.json`, the
profile added, the merge validated through `paper._merged_params` (SPX width menu came out
`[5, 10, 30]`), and the `arm_added` break journaled in the paper ledger's `measurement_breaks`
(id 8, break date 2026-10-20, beside `bt-1400-2x`'s). The first trade filled the same minute,
**09:59:42 ET** — short 7655P / 7740C, 30-wide, spot 7703.61, deltas −0.138 / +0.124, $372.50
credit, per-side stop armed at 1.0× the credit. The 10:00 and 10:01 ticks were declined
`entry_spacing_wait`, so the 55-minute cadence held from the first hour. For the record: the entry
landed on an `open_volatile` tick with confirmed-negative GEX — the neutralized gate block working
as specified (he times nothing), not a gate leak.

**2026-10-20, 12:33 ET — gap #1 closed.** The one fidelity gap that could be closed without
inventing a new gate was closed mid-session, on branch `paper/equalize-credit`: the engine gained
`equalize_credit` (per profile, default off, documented in `config.example.json`), and the registry
was flipped to the `[25, 30, 35, 40]` width search with the flag on — backup
`meic.risk.20261020-0933.json`, merge re-validated through `paper._merged_params`, `gate_changed`
break journaled (id 9, break date 2026-10-20). The loop runs from the working tree as a fresh
process per tick, so every tick from 12:33 onward had both the new code and the new profile; no
restart. The three 30-wide singles entered before the flip (09:59, 10:55, 11:50) keep reading their
wings from the scalar via the column fallback, so that session is mixed either way — the first clean
read of the equalized arm was the following day.
