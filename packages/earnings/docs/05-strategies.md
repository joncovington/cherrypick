# Strategy Guide

Structure, entry gates, and exit rules for all seven strategies. Every strategy is
**defined-risk** — max loss is known at entry. Undefined-risk/naked strategies were deliberately
excluded: a naked short on a single-name earnings gap can blow out arbitrarily during the
unmonitored overnight hold this system is built around.

Two shared facts apply to every strategy below, so they aren't repeated seven times:

- **Exit mechanics are the same shape for all seven** — a Step 3c early-exit check (profit
  target / stop loss, checked against live quotes the first morning after entry, from market
  open until `close_window_start`) followed by Step 3's unconditional close-window backstop
  (whatever's still open gets closed regardless of P&L). See
  [Exit Strategy Guide](./10-exits.md) for the exact formulas. There is no same-day, hours-
  after-the-announcement exit anywhere in this system.
- **Every strategy shares the same liquidity/quality gates** (price floor, bid-ask spread,
  market cap, option volume, open interest, IV/RV ratio, winrate) with its own threshold values
  — see [Screening Criteria](./screening-criteria.md) for the full shared-gate reference and
  [Configuration Guide](./03-configuration.md) for every strategy's actual current numbers.

---

## Strategy Overview Matrix

| # | Strategy | Structure | Entry | Best For |
|---|----------|-----------|-------|----------|
| 1 | `iron_fly` | Short ATM straddle + long OTM wings | Credit | Medium IV, balanced risk/reward |
| 2 | `iron_condor` | Short strangle at expected-move boundaries + wings | Credit | Wide expected range, directional-neutral |
| 3 | `directional_credit_spread` | Single-sided vertical credit spread (side by skew) | Credit | Directional bias / IV skew |
| 4 | `broken_wing_butterfly` | Body-anchored butterfly, asymmetric wings (skew) | Credit | Asymmetric expected moves |
| 5 | `reverse_fly` | Long ATM straddle + short OTM wings | Debit | Historically bigger-than-priced-in moves |
| 6 | `atm_calendar` | Short front-month ATM call + long back-month same strike | Debit | Low IV, term-structure edge |
| 7 | `double_calendar` | ATM-ish calendar on both call and put side | Debit | Low IV, symmetric term structure |

---

## 1. Iron Fly

**Structure:** Sell the ATM call and put (a short straddle), buy protective OTM wings on both
sides. Wing width is picked from IV/RV-banded multiples of the credit received
(`wing_width_multiple_low/mid/high`, default 2.5×/3.0×/3.5×, scaled to this candidate's own
IV/RV ratio — richer IV/RV earns a wider, more protective wing).

**Entry gate:** negative term structure (front IV meaningfully above back IV,
`min_term_structure`), expected move above a dollar floor, ATM delta sanity check
(`max_atm_delta_abs`), plus the shared IV/RV/winrate/liquidity gates.

**Exit:** `evaluate_credit_spread_exit()` — default 50% of credit as the profit target, 1.5×
credit as the stop.

```
Sell 150 call / Buy 158 call
Sell 150 put / Buy 142 put
Entry credit: $0.80/spread
Profit target: $0.40  (50%)
Stop loss: $1.20       (1.5x)
Max loss: wing width - credit = $8.00 - $0.80 = $7.20
```

Most common strategy in this system's day-to-day scans — a straightforward, symmetric
IV-crush play. See [Screening Criteria](./screening-criteria.md) for the exact hard-filter
numbers, since `iron_fly` is that doc's reference implementation.

---

## 2. Iron Condor

**Structure:** Same credit-spread-plus-wings shape as `iron_fly`, but the short strikes sit at
the expected-move boundary (price ± expected move) instead of ATM — a short strangle, not a
short straddle. Wider profit zone, lower credit, a genuinely different historical win-rate
profile than `iron_fly`, not just a config variant of it.

**Entry gate:** expected move above a percentage floor (`min_expected_move_pct`), negative term
structure, plus the shared gates.

**Exit:** same `evaluate_credit_spread_exit()` shape as `iron_fly` — 50% profit target, 1.5×
stop by default.

```
Stock at 100, expected move $3.00 (3%)
Sell 103 call / Buy 105 call — $0.30 credit
Sell 97 put / Buy 95 put — $0.30 credit
Entry credit: $0.60, max loss $5.00 - $0.60 = $4.40
Profit zone: roughly 97 to 103 — wider than iron_fly's ATM-centered zone
```

---

## 3. Directional Credit Spread

**Structure:** A single-sided vertical credit spread — sell a put spread if bullish, a call
spread if bearish. Side is picked by a 25-delta risk reversal (`scanner.select_side()`, the same
skew calculation `broken_wing_butterfly` and `reverse_fly` reuse): sell whichever side (calls or
puts) is carrying richer IV.

**What makes this genuinely directional, not just half an iron condor:** the short strike isn't
placed directly at the expected-move boundary the way `iron_condor`'s is. Instead, the strike is
chosen so that *breakeven* (short strike net of the credit received) lands at that boundary —
the strike itself sits further OTM, with the credit providing the rest of the cushion. That
means evaluating multiple candidate strikes at order-build time rather than a single formula
lookup.

**Entry gate:** skew magnitude floor (`min_skew_abs`), expected move floor, negative term
structure, plus the shared gates.

**Exit:** same `evaluate_credit_spread_exit()` shape — 50% profit target, 1.5× stop by default.

```
Tech stock, calls richer than puts (bearish skew signal)
Sell 155 call / Buy 160 call
Net credit: $0.40, max loss $5.00 - $0.40 = $4.60, breakeven near 155.40
```

---

## 4. Broken Wing Butterfly

**Structure:** Body-anchored, no ATM leg — two short contracts at the expected-move strike
(side picked by the same 25-delta risk reversal as `directional_credit_spread`), protected by a
narrow long wing toward current price and a wide long wing away from it. The near wing scales
off the body leg's own premium via IV/RV-banded multiples (`wing_width_multiple_low/mid/high`,
default 1.0×/1.25×/1.5× — deliberately smaller than `iron_fly`'s bands, since this prices a
single leg, not a straddle); the far wing is `wide_wing_multiple` (default 2.5×) times the near
wing.

**A hard rule at order-build time:** `get_order` rejects any candidate that doesn't price out to
a net credit or breakeven. If the real premiums at these wing widths land as a net debit, the
position didn't buy back enough premium to finance itself, and it's skipped rather than entered
as a smaller-debit butterfly.

**Entry gate:** skew magnitude floor, expected move floor, negative term structure not required
(unlike the credit spreads above — this strategy doesn't gate on term structure), plus the
shared gates.

**Exit:** `evaluate_debit_spread_exit()` (despite pricing as a net credit, the exit math treats
the position like a debit strategy's profit-zone check) — 25% profit target, 40% stop by
default.

```
Earnings expected to move down (put side richer)
Body: sell 2x 150 put (expected-move strike)
Near wing: buy 1x 148 put (narrow, toward spot)
Far wing: buy 1x 145 put (wide, away from spot)
```

---

## 5. Reverse Fly

**Structure:** Long ATM straddle (long call + long put) plus short OTM wings above and below —
a "reverse iron fly." Wing width is a flat percentage of the ATM strike (`wing_width_pct`,
default 10%), not skew- or expected-move-anchored the way the credit strategies above are. Net
debit; max profit and max loss are both capped at wing width minus the debit paid.

**Entry gate — this is the strategy's real edge, and it's a different kind of signal than every
other strategy here:** rather than gating on IV/RV ratio pricing the *options* rich, this
strategy checks whether the *stock itself* has historically moved more than its own options
priced in (`realized_move_pct >= expected_move_pct * min_realized_move_ratio`, default ratio
`1.10`) — a persistent gap-premium pattern, plus a ceiling on how inconsistent those historical
moves can be (`max_realized_move_dispersion_pct`, default `0.30`) so the edge isn't just noise.

**Exit:** `evaluate_debit_spread_exit()` — 25% profit target, 40% stop by default.

```
Stock with a track record of earnings moves exceeding its own priced-in expected move
Long ATM 100 call + long ATM 100 put (long straddle)
Short 110 call + short 90 put (10% wings)
Net debit paid, max loss/profit both capped at wing width minus debit
```

---

## 6. ATM Calendar

**Structure:** Sell a front-month ATM call, buy the same strike in a later monthly expiration.
Always the call side — literature treats call and put ATM calendars as performing virtually
identically, so this is a config-fixed choice, not a computed one. Always closes as a single
2-leg unit (`legs_json`, never `trade_legs`).

**Entry gate:** negative term structure (the core signal — front IV must be inflated relative to
back), `back_month_min_days_after` (back-month must land a documented distance past the front),
plus the shared gates. No expected-move-boundary filter — this is a pure ATM term-structure
play, not targeting where the stock lands.

**Exit:** `evaluate_debit_spread_exit()` at a looser 30%/100% target/stop (the position tolerates
more round-trip noise since it decays over days, not hours), plus a hard time-stop
(`exit_days_before_front_expiration`, default 5 days) — once the front leg is within 5 days of
expiring, gamma risk on the short ATM option overwhelms any remaining theta benefit.

```
Sell front-month 150 call ($0.40) / Buy back-month 150 call ($0.70)
Entry debit: $0.30
Profit target: 30% → close once back-front spread narrows to ~$0.21
```

---

## 7. Double Calendar

**Structure:** An ATM calendar run on both the call and put side simultaneously — sell
front-month calls and puts at the expected-move boundaries, buy the same two strikes in a later
monthly expiration. The only strategy whose two sides can close independently (`trade_legs`
persistence, not just `legs_json`), managed via Step 3b's intraday `evaluate_position()` check.

**Entry gate:** stricter liquidity/volume floors than every other strategy — execution cost
matters more across two expirations, and a tail-risk surprise hurts a debit trade worse than a
width-defined credit strategy's max loss. Also gates on `max_realized_move_dispersion_pct` (how
consistent the underlying's historical earnings moves have been), since this structure leans on
the expected-move assumption more than a single-expiration credit spread does.

**Exit:** same 25%/100% target/stop shape and 5-day time-stop as `atm_calendar`, plus a per-side
delta stop (`leg_stop_delta_abs`, default 0.45) checked during Step 3b — a side crossing this
threshold gets closed independently while the other side, likely still healthy, stays open.

```
Sell front 150 call ($0.40) + front 150 put ($0.40)
Buy back 150 call ($0.70) + back 150 put ($0.70)
Total entry debit: $0.60 (both calendars)
```

---

## Strategy Selection at a Glance

There's no fixed lookup table mapping a market condition to a strategy — `rank_strategies.py`
evaluates all seven against every symbol and picks whichever scores highest among those that
clear their own tiering. As a rough mental model of what tends to win under which conditions:

- **Rich IV/RV, negative term structure, no strong skew** → `iron_fly` or `iron_condor`
  (the choice between them is a scoring outcome, not a rule — see
  [Entry Conditions Framework](./04-entry-conditions.md))
- **Clear directional skew** → `directional_credit_spread` or `broken_wing_butterfly`
- **A name with a track record of moving more than its own options price in** → `reverse_fly`
- **Low IV, clean term-structure edge, quiet historical moves** → `atm_calendar` or
  `double_calendar`

Every strategy here is defined-risk — max loss is always known at entry.

---

## Navigation

**← Previous:** [Entry Conditions Framework](./04-entry-conditions.md)
**Next →** [Earnings Scan Analysis](./06-scan-analysis.md)
