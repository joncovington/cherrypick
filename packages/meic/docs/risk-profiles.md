# Risk Profiles

**What this covers:** the four preset "how aggressive should this be" settings you can switch
between with one command, what each one trades off, and when to move up or down the ladder. Part
of the [MEIC module](../README.md) in the cherrypick suite.

## Overview

A **risk profile** is a named preset of entry-gate thresholds that you select based on market conditions or your confidence level. Instead of manually editing a dozen keys in `config.json` every time you want to trade more (or less) aggressively, you switch profiles with one slash command: `/set-risk-profile <name>`.

Each profile bundles gate-threshold changes with offsetting position-sizing and stop-management adjustments, following a core principle: **every relaxed gate is paired with a compensating constraint** (fewer concurrent positions or tighter stops), so you're reallocating risk, not just adding it.

> **2026-08-01 — the concurrency half of that principle no longer applies.** Every profile now runs
> as an uncapped independent-sampling stream (`max_concurrent_ics: 99`, `daily_ic_trade_target: 200`,
> `overlap_scope: "shorts"`, no entry spacing) rather than a capped book — the same one-structure-
> per-zone model flies uses, adopted so a session tests as many independent samples as the market
> offers. There is no longer a position count to offset against, so **the tighter stop is the ladder's
> only remaining offset**; the rungs differ solely in entry quality now. The per-tier tables below
> still show the *historical* `max_concurrent_ics`/`daily_ic_trade_target` values and the reasoning
> that shaped them (worth keeping — it explains why the stop tightens the way it does), each flagged
> where it no longer reflects the live config. Full rationale for the switch:
> `docs/paper-experiments.md`'s "Independent sampling" section.

---

# Design rationale

*Why the ladder is shaped the way it is. Rewritten 2026-07-18 after a review found the ladder was
not, in fact, laddering. Every claim below is tied to something measured on the paper book rather
than assumed — where a decision is still open, it says so and says what evidence would close it.*

## 1. The ladder's axis is **riskier trades, not more trades**

This is the single most important design decision, because everything else follows from it.

Climbing a rung **loosens entry quality** — IV-rank floor, credit floor, short-call delta, OTM
distance — and, historically, was **offset by fewer concurrent positions and a tighter stop**. Total
dollar exposure therefore *fell* as you climbed (conservative 4 × ~$1,000 = ~$4k; very-aggressive 2 ×
~$1,000 = ~$2k). "Aggressive" meant *each trade is a worse bet taken deliberately*, not *more bets*.

> **Since 2026-08-01, the position-count half of that offset is gone** (see the note at the top of
> this doc) — every rung runs uncapped as an independent-sampling stream, so climbing the ladder no
> longer shrinks total exposure the way the paragraph above describes. The **tighter stop is now the
> only offset**, and the invariant below no longer holds (200 > 99 at every rung) or is enforced by
> any test. The reasoning for *why trade count isn't a ladder axis* is unaffected — that argument
> never depended on the concurrency cap — it just no longer needs the cap to be true.

**Trade count is therefore not a ladder dimension at all.** `daily_ic_trade_target` was flat at **2**
on every rung under book semantics; it is flat at **200** (a never-binding backstop) now.

Previously it climbed 2 → 3 → 4 → 5 while `max_concurrent_ics` fell 4 → 4 → 3 → 2, which made the
ladder move **three signals in three directions**: selectivity loosening, position count shrinking,
and daily target growing. At best two of those can be coherent simultaneously. The climb was also
*arithmetically unreachable* at the top: with `max_concurrent_ics` of 2 and 0DTE positions held to
settlement, slots essentially never free up intraday, so very-aggressive could never open 5.

**Former invariant (book semantics, pre-2026-08-01):** `daily_ic_trade_target ≤ max_concurrent_ics`
at every rung. Retired along with the concurrency cap itself — there is no longer a position count
for the target to sit inside.

## 2. Two axes, not one: **risk appetite × instrument**

Risk appetite (the ladder) and the instrument traded (the symbol) are **orthogonal**. They are
deliberately kept separate, and the unit of measurement is their product:

> **One portfolio per (profile × symbol) pair**, each with its own `max_concurrent_ics` and its own
> daily-entry budget.

Why this matters concretely: those budgets used to be **per-profile, shared across every symbol**,
and symbols are processed in config order. So whichever symbol came last was structurally starved of
slots by whichever symbols came first. That is not hypothetical — **IWM went 1,313 consecutive
iterations without filling a single trade**, and `max_concurrent_ics_reached` was the explicit skip
reason on 183+ of them. A symbol can now be evaluated on its own merits instead of on its position in
a list.

It is also why the old symbol-pinned experiment cells (`large-spx`, `small-xsp`, …) were retired: a
cell that bakes the symbol into its identity fuses the two axes, so "the same strategy on a different
instrument" becomes inexpressible. See [paper-experiments.md](paper-experiments.md). The
symbol-agnostic form it prescribes is what every study arm has used since: the wing-width arms
(retired 2026-08-05 without trading) and the GEX arms (`gex-open`/`gex-blocked`) running now — one
variable pinned per arm, the (profile × symbol) grain supplying the instrument axis automatically.

## 3. Thresholds are **profile-relative**, never shared absolutes

Any gate expressed as an absolute IV-rank number is a latent bug, because **each tier has a different
`min_iv_rank`**. An absolute threshold sits in a different place relative to each tier's own floor —
and in practice it gives the *loosest* tier the *most* relief, which is backwards.

This flattened the ladder outright. The low-IV credit relief applied while `iv_rank ≤ 0.35` (a flat
absolute) and set the floor to `0.10` (another flat absolute). Consequences:

- Whenever IV rank sat under 0.35, **all four tiers used the same 0.10 credit floor** — the
  0.15/0.12/0.10/0.10 progression simply did not exist.
- For aggressive and very-aggressive the "relief" **equalled their normal floor**, so it did nothing.
- Measured on the paper book: **100% of SPX and XSP entries were inside that zone** (0% of QQQ; 39%
  of all 89 trades). Conservative was trading SPX — the symbol responsible for the period's losses —
  on **identical credit terms to very-aggressive**.

Both halves are now derived from the profile itself:

| Derived value | Formula | conservative → very-aggressive |
|---|---|---|
| Low-IV relief **ceiling** | `min_iv_rank + low_iv_credit_floor_iv_rank_offset` (0.05) | 0.35 / 0.27 / 0.25 / 0.20 |
| Low-IV relief **floor** | `min_credit_pct_of_width × low_iv_credit_relief_multiple` (0.85) | 0.128 / 0.102 / 0.085 / 0.068 |
| Late-entry-bias **ceiling** | `min_iv_rank + late_entry_bias_iv_rank_offset` (0.15) | 0.45 / 0.37 / 0.35 / 0.30 |

Conservative reproduces its historical values exactly (0.35 / 0.45); the looser rungs now scale.
The absolute keys are still honored if a profile sets them, so nothing external breaks.

**Why the multiple is 0.85 and not a deeper cut.** A stronger relief (0.67 was tried) let a tier
*inside* its borderline band undercut the next tier's plain floor — conservative would accept 0.101
where moderate demanded 0.120. Relief must stay shallow enough to preserve ordering. For the same
reason very-aggressive took its own **0.08** credit floor: it previously shared 0.10 with aggressive,
leaving the credit axis with only three distinct values for four tiers.

**Invariant:** the effective credit floor is **monotonic across the ladder at every IV level** — a
stricter tier must never accept thinner credit than a looser one. Verified across IV 0.16–0.60 and
enforced by test.

## 4. The daily target is **guidance**, not a cap

Hitting `daily_ic_trade_target` does **not** block further entries. Past the target, the credit floor
is multiplied by `over_target_credit_multiple` (1.5), so an extra trade is available **only when
conditions are genuinely favorable** — rich premium clears the raised bar, marginal setups are
declined with the auditable reason `over_target_credit_below_floor`.

This keeps the target doing what it was always documented to do (calibrate selectivity) rather than
becoming a hard throttle, while ensuring the extra trades are the *good* ones. Profiles that opt into
`stagger_entries` keep a hard cap, since spreading a fixed number of entries across the session is
the entire point of staggering.

## 5. What is deliberately **not** decided yet

Two questions are consciously left open because the evidence to settle them does not exist yet.
Guessing would encode an assumption as if it were a finding.

- **IV rank vs. IV percentile as the entry gate.** Rank is `(current − 52wk low) / (52wk high − low)`,
  so a *single* spike anywhere in the lookback permanently compresses it; percentile is the share of
  days below current IV and is outlier-robust. They can disagree violently: **NDX reads rank 0.192
  against percentile 0.953**, and sits ~4× off QQQ's rank despite tracking the same index. (SPX and
  SPY agree to three decimals, so this is not a general metric failure — it is specific and
  diagnosable.) Both measures are now captured per symbol per day so the question can be answered
  from data. **If the gate moves to percentile, every tier's floor must be re-baselined** — the two
  are not the same scale.
- **Whether rank should modify trade construction rather than gate entry.** A low rank alongside a
  high percentile means "premium is rich, but this product has proven it can go much higher" — which
  is a tail-risk signal for a short-premium book, arguably better expressed by pushing strikes further
  OTM and demanding more credit than by refusing the trade. Band edges for such a rule need a real
  rank/percentile distribution, roughly 3–4 weeks of capture.

## 6. Invariants any future change must preserve

1. `daily_ic_trade_target ≤ max_concurrent_ics` on every rung.
2. Effective credit floor **monotonic** across the ladder at every IV level.
3. Every relaxed gate paired with a compensating constraint (fewer positions or tighter stops).
4. No gate threshold expressed as an absolute IV-rank number — derive it from the profile's own
   `min_iv_rank`.
5. Trade count is not a ladder dimension.
6. Risk appetite and instrument stay separate axes; the portfolio is their product.

---

## Four Tiers: conservative → moderate → aggressive → very-aggressive

### Conservative (default)

**Today's settings — the baseline.**

| Gate | Value | Rationale |
|---|---|---|
| `min_iv_rank` | 0.30 | Skip if IV rank below 30%; safest premium edge |
| `min_credit_pct_of_width` | 0.15 | Require 15% of spread width as credit; widest cushion |
| `max_call_delta_entry` | 0.20 | Sell calls at ≤20 delta (furthest OTM) |
| `min_call_otm_pct` | 0.35% | Calls must be 0.35%+ above spot |
| `min_put_otm_pct` | 0.30% | Puts must be 0.30%+ below spot |
| `late_entry_bias_start_time` | 12:00 | Don't enter before noon on borderline-IV days |
| `regime_vix_pause_threshold` | 25 | Pause ICs when VIX > 25 |
| `regime_atr_pause_threshold_pct` | 0.015 | Pause ICs when 5-day ATR > 1.5% of underlying |
| `max_concurrent_ics` | ~~4~~ **99** since 2026-08-01 | Was 4 simultaneous ICs; now uncapped — see the note above |
| `stop_trigger_ratio` | 0.95 | Stop at 95% of credit received (loose stop) |
| `daily_ic_trade_target` | ~~2~~ **200** since 2026-08-01 | Was a 2/day target; now a never-binding backstop — see the note above |

**Trade-off**: Fewest entries (~1–2/day on quiet days), highest per-trade safety margin. Best for: learning, uncertain markets, or after a losing streak.

---

### Moderate (Tier 1)

**Slightly lower bars on IV/credit floors; enter earlier in the day.**

| Gate | Change | Rationale |
|---|---|---|
| `min_iv_rank` | 0.30 → **0.22** | Accept lower IV — only lose ~5–10% edge vs conservative, but unlock 30–40% more entry candidates on flat-vol days |
| `min_credit_pct_of_width` | 0.15 → **0.12** | Accept thinner credit — 20% haircut, but gates that fell just short of conservative now qualify. The low-IV relief floor scales with it (`× low_iv_credit_relief_multiple`), so the ladder stays monotonic at every IV level |
| `late_entry_bias_start_time` | 12:00 → **11:00** | Start entering at 11 AM instead of noon — capture an extra hour of morning premium (theta is still accelerating) |
| `stop_trigger_ratio` | 0.95 → **0.93** | Tighten stop slightly — 0.93 = stop at 93% of credit (2% tighter) to offset lower entry bars |
| `daily_ic_trade_target` | ~~2~~ **200**, still flat across every rung | Trade **count is not a ladder axis** — was true when the flat value was a book's target of 2; still true now that the flat value is a never-binding sample-stream backstop |
| Other gates | (unchanged) | VIX/ATR/delta/OTM thresholds stay the same |

**Trade-off**: ~1–2 additional entries/week on normal weeks, slightly thinner per-trade credit margin but matched by tighter stop management. **Start here if conservative is leaving money on the table.**

---

### Aggressive (Tier 2)

**Tier 1 + delta and OTM distance relaxed. Offset with fewer concurrent positions and tighter stop.**

| Gate | Change | Rationale |
|---|---|---|
| `min_iv_rank` | 0.30 → **0.20** | Accept even lower IV — 33% lower than conservative |
| `min_credit_pct_of_width` | 0.15 → **0.10** | Accept tight credit — 33% haircut from conservative, matched by tighter stop and fewer concurrent ICs |
| `max_call_delta_entry` | 0.20 → **0.22** | Accept calls 10% closer to ATM — higher gamma, but tighter OTM buffers gone so stops must absorb more |
| `max_call_delta_entry_open_volatile` / `_late` | 0.19 → **0.21** | The tighter open/late-session ceilings also relax by the same margin, so they stay proportionally below the base ceiling instead of becoming the binding constraint |
| `min_call_otm_pct` | 0.35% → **0.30%** | Calls only 0.30% OTM instead of 0.35% — much tighter |
| `min_put_otm_pct` | 0.30% → **0.25%** | Puts only 0.25% OTM instead of 0.30% — much tighter |
| `late_entry_bias_start_time` | 12:00 → **11:00** | Same as moderate |
| `max_concurrent_ics` | ~~4 → 3~~ **99, same as every rung** since 2026-08-01 | This offset is gone — every rung runs uncapped now, so the stop below is the only thing left absorbing the closer-to-money delta |
| `stop_trigger_ratio` | 0.95 → **0.90** | **Offset #2**: stop at 90% of credit (5% tighter than conservative) — increased stop cost paired with reduced position count keeps total risk budget similar |
| `daily_ic_trade_target` | ~~2~~ **200** (unchanged across rungs) | Count is not a ladder axis — see moderate. The position cap no longer exists to do any tightening (see the `max_concurrent_ics` row above) |
| Regime gates | (unchanged) | VIX/ATR pause thresholds unchanged — still skip in volatile regimes |

**Trade-off**: ~2–3 additional entries/week on normal weeks; each one trades tighter strikes (higher per-trade risk), but fewer concurrent positions and tighter stops cap total exposure. Requires disciplined stop management and comfort with smaller win/loss swings. **Use when you want 2–3× more activity and accept tighter daily P&L ranges.**

---

### Very-Aggressive (Tier 3)

**Tier 2 + regime-gate thresholds relaxed. Trade through higher-VIX/choppier conditions. Tightest stops, smallest position cap.**

| Gate | Change | Rationale |
|---|---|---|
| Tier 1 + 2 gates | (all carry forward) | All relaxations from moderate + aggressive stay in place |
| `min_iv_rank` | 0.20 → **0.15** | Accept IV rank down to 15% — skipping the market's quietest conditions |
| `regime_vix_pause_threshold` | 25 → **30** | **Trade when VIX 25–30** (normally paused for IC) — dealer short-gamma conditions, normally avoid; now accepted with extreme position discipline |
| `regime_atr_pause_threshold_pct` | 0.015 → **0.020** | **Trade when 5-day ATR 1.5–2.0% of underlying** (normally paused) — trending/volatile markets where mean-reversion edge weakens, now accepted |
| `max_call_delta_entry` | 0.22 → **0.24** | Accept calls 20% closer to ATM than conservative |
| `min_call_otm_pct` | 0.30% → **0.25%** | Calls only 0.25% OTM |
| `min_put_otm_pct` | 0.25% → **0.20%** | Puts only 0.20% OTM |
| `min_credit_pct_of_width` | 0.10 → **0.08** | Thinnest credit accepted. Previously identical to aggressive (0.10), which left the ladder's credit axis with only three distinct values and let a stricter tier's low-IV relief undercut this one |
| `late_entry_bias_start_time` | 11:00 → **10:00** | Start entering at 10 AM (market open) — no bias gate; accept directional exposure in the first hour |
| `max_concurrent_ics` | ~~3 → 2~~ **99, same as every rung** since 2026-08-01 | This offset is gone (see the note at the top of this doc) — the stop below now carries the full weight of the regime relaxation |
| `stop_trigger_ratio` | 0.90 → **0.85** | **Offset #2**: stop at 85% of credit (10% tighter than conservative) — each stop is deeper, accepted risk is extreme |
| `daily_ic_trade_target` | ~~2~~ **200** (unchanged across rungs) | Count is not a ladder axis. Previously 5 here, which was unreachable anyway under the old `max_concurrent_ics` = 2 cap — moot now that there is no cap |

**Trade-off**: Maximum activity (~3–5 additional ICs/week vs conservative on normal weeks); each trade is **riskiest** (closest-to-money strikes, highest gamma, widest daily swings); offsetting with smallest position count and tightest stops. **Only for experienced traders who can emotionally handle stops 10% deeper per position, or who deliberately want to test unfamiliar regimes. Not recommended for first month of operation.**

---

## Relaxation Principle: Gates First, Offsets Second

The four tiers follow a deliberate sequence. **Do not skip ahead** — the intermediate tiers exist because relaxing gates in isolation creates risk, while the offsets (position caps, tighter stops) only work if they compound properly.

### Recommended progression

1. **Start at conservative** if you're new, uncertain, or just traded out of a drawdown.
2. **Move to moderate** after 2–4 weeks when you've observed: entry rejection reasons in your logs, which 2–3 gates block most entries, and whether your per-trade win rate stays 60%+ on your entries.
3. **Escalate to aggressive** only if: moderate's ~3 ICs/day felt sustainable, most days closed green (ICs expiring/settling for a profit rather than getting stopped), and your largest losses didn't exceed 2% of account equity. Aggressive **requires active stop management** — do not set it and forget it.
4. **Reach very-aggressive only deliberately**, after running aggressive for 2+ weeks. This tier is for live-testing high-VIX/ATR tactics, not a normal mode. Plan a short experiment (1 week) with explicit drawdown limits before committing.

### What each relaxation costs

| Relaxation | Tier | Cost | Offset |
|---|---|---|---|
| IV-rank floor (0.30→0.22) | Moderate | ~5–10% edge loss per trade | Accept thinner credit margin; monitor win rate |
| Credit floor (0.15→0.12) | Moderate | ~20% net premium loss per trade if fee-heavy | Pair with tighter stops |
| Late-bias start (12:00→11:00) | Moderate | +1 hour directional exposure | Stop management picks up the slack |
| Delta relaxation (0.20→0.22) | Aggressive | +2–3% gamma per position | Cap concurrent positions to 3 |
| OTM relaxation (0.35%→0.30%) | Aggressive | Strikes 17% closer to money = 2–3× higher pin/assignment risk at expiration | Pair with 5% tighter stops (0.95→0.90) |
| Regime gate (VIX 25→30) | Very-Aggressive | Trade 30–50% of days you currently skip due to elevated vol | Reduce position cap to 2 and stop at 85% (10% tighter) |
| ATR gate (1.5%→2.0% of price) | Very-Aggressive | Trade trending markets with half the normal mean-reversion edge | Same tight offsets as VIX gate |

---

## How Profiles Actually Work: Config Mutation

When you run `/set-risk-profile moderate`:

1. The command reads `config.risk.json` and extracts the `moderate` profile object.
2. It backs up your current `config.json` → `config.json.bak` (so you can revert if needed).
3. It overwrites the matching keys in `config.json` with the moderate profile's values.
4. It updates `config.risk.json`'s `active_profile` field to track which one is active (for logging/auditing).
5. It prints a before/after table showing every key that changed, plus the profile's rationale.
6. The next loop iteration reads the updated `config.json` and picks up the new values — **no restart needed**, but the change is not retroactive to in-flight positions.

**Important**: Profile switches happen *between* loop iterations. If you're in the middle of a scan when you switch, the new config takes effect on the next 5-minute tick, not immediately. This is intentional — it prevents gates from flipping mid-trade.

---

## Switching Back

`/set-risk-profile conservative` restores today's baseline. Since the `conservative` profile contains every key at its exact current value, switching back is a complete reset.

If you've manually edited `config.json` outside of profile switches (e.g., tuned `stop_trigger_ratio` from 0.95 to 0.92), switching profiles **overwrites only the keys in that profile** — unspecified keys stay as you edited them. So manual edits can survive profile switches if you're careful.

---

## Which Profile to Use: Decision Tree

- **Conservative**: First week, learning phase, or post-loss recovery
- **Moderate**: Steady state; conservative rejected 40%+ of candidate trades last week
- **Aggressive**: 2+ weeks on moderate, win rate 60%+, largest loss < 2% account equity
- **Very-Aggressive**: Deliberate 1-week experiment testing high-VIX/ATR tactics; not a permanent mode

**Default recommendation**: Run **moderate** for most traders most of the time. It captures ~50% more entries than conservative without the execution complexity of aggressive/very-aggressive.

