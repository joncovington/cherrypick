# Strategy Fallback System: Undefined Risk Mitigation

Automatically substitute high-edge naked strategies with wide-wing defined-risk alternatives when portfolio constraints require it.

---

## Overview

Some portfolios have strict risk constraints: "No unlimited risk strategies."

The Fallback System solves this by automatically substituting high-edge naked strategies with **wide-wing defined-risk alternatives** that approximate naked behavior while maintaining a defined maximum loss.

**Core idea:** Wider the wings → closer to naked → more like unlimited

---

## Configuration

### Enable/Disable Fallback

In `config.json`:

```json
{
  "allow_naked_strategies": false  // Enable fallback
}
```

**If false:** Naked strategies automatically fall back to wide-wing versions  
**If true:** Use naked strategies directly (full unlimited risk)

---

## Fallback Mappings

### 1. Short Straddle → Iron Fly (Wide Wings)

**Trigger:** `allow_naked_strategies: false` + `SHORT_STRADDLE` selected

**Structure:**
```
Original (Naked):
  Sell ATM 150 call
  Sell ATM 150 put
  Max loss: UNLIMITED

Fallback (Defined):
  Sell ATM 150 call
  Sell ATM 150 put
  Buy OTM 158 call (8% OTM)
  Buy OTM 142 put (8% OTM)
  Max loss: DEFINED at wing width - credit
```

**Wing Multiple:** 8.0x credit width  
**Rationale:** Very wide wings approximate naked short behavior

**Example:**
```
Entry credit: $4.50
Wing width: 8 × $0.50 credit width = $4.00 ($0.04 per point)
Max loss: $4.00 - $0.50 credit = $3.50

Win probability: Similar to naked (wider wings = wider profit zone)
Return %: $0.50 profit / $3.50 max risk = 14.3% on risk
```

---

### 2. Short Strangle → Iron Condor (Wide Wings)

**Trigger:** `allow_naked_strategies: false` + `SHORT_STRANGLE` selected

**Structure:**
```
Original (Naked):
  Sell OTM call
  Sell OTM put
  Max loss: UNLIMITED

Fallback (Defined):
  Sell OTM call
  Buy far-OTM call (wings beyond expected move)
  Sell OTM put
  Buy far-OTM put (wings beyond expected move)
  Max loss: DEFINED
```

**Wing Multiple:** 8.0x  
**Rationale:** Captures strangle edge with defined risk

---

### 3. Jade Lizard → Broken Wing Butterfly (Wide Wings)

**Trigger:** `allow_naked_strategies: false` + `JADE_LIZARD` selected

**Structure:**
```
Original (Partial Naked):
  Short spread on one side (defined)
  Naked on other side (unlimited)

Fallback (Full Defined):
  Short spread both sides
  Add wide wings on naked side to define it
```

**Wing Multiple:** 6.0x (asymmetric strategy)  
**Rationale:** Maintains directional hedge but defines all risk

---

## Wide-Wing Profiles

Choose fallback aggressiveness:

### CONSERVATIVE (4.0x wings)

```json
"conservative": {
  "wing_multiple": 4.0,
  "use_case": "Tight risk, capital safety prioritized"
}
```

**Example:**
```
Entry credit: $1.00
Wing width: 4 × $1.00 = $4.00
Max loss: $4.00 - $1.00 = $3.00
Profit zone: 4% move tolerance

Who: Risk-averse portfolios, limited capital
```

---

### MODERATE (6.0x wings) — DEFAULT

```json
"moderate": {
  "wing_multiple": 6.0,
  "use_case": "Balanced risk/reward (RECOMMENDED)"
}
```

**Example:**
```
Entry credit: $1.00
Wing width: 6 × $1.00 = $6.00
Max loss: $6.00 - $1.00 = $5.00
Profit zone: 6% move tolerance

Who: Most portfolios, standard earnings plays
```

---

### AGGRESSIVE (8.0x wings)

```json
"aggressive": {
  "wing_multiple": 8.0,
  "use_case": "Simulate naked behavior closely"
}
```

**Example:**
```
Entry credit: $1.00
Wing width: 8 × $1.00 = $8.00
Max loss: $8.00 - $1.00 = $7.00
Profit zone: 8% move tolerance (approaching naked)

Who: Capital available, want edge similar to naked
```

---

## How It Works: Side-by-Side Comparison

### Scenario: AAPL Earnings, Entry Credit = $4.50

**OPTION A: Naked Short Straddle**
```
allow_naked_strategies: true

Entry:
  Sell 150 call for $2.25
  Sell 150 put for $2.25
  Net credit: $4.50

Outcomes:
  Stock at 150: Max profit = $4.50
  Stock at 152: Profit = $3.50 (safe)
  Stock at 155: Profit = $1.50 (getting risky)
  Stock at 158: Profit = $0 (at edge)
  Stock at 160: LOSS = -$1.50 (unlimited potential)
  
Max loss: UNLIMITED
Risk management: Difficult (don't know max loss)
Position sizing: Hard to calculate
```

---

**OPTION B: Iron Fly with MODERATE Wide Wings**
```
allow_naked_strategies: false

Entry (automatic fallback):
  Sell 150 call for $2.25
  Buy 156 call for $0.25
  Call spread credit: $2.00
  
  Sell 150 put for $2.25
  Buy 144 put for $0.25
  Put spread credit: $2.00
  
  Total credit: $4.00 (wings cost $0.50)
  Wing width: 6 × $4.00 total credit ÷ 2 sides ≈ $6/side
  
Actually, let me recalculate wing width correctly:
Wing width in points = credit × wing_multiple
If credit=$0.50 per spread = $50 per spread
Wing width = $50 × 6 = $300 per spread = $3 per share
So buy wings at 150+3=153 (call) and 150-3=147 (put)

Entry (corrected):
  Sell 150 call
  Buy 153 call
  Sell 150 put
  Buy 147 put
  Net credit: ~$4.00

Outcomes:
  Stock at 150: Max profit = $2.00 (50% of $4.00)
  Stock at 152: Profit = $1.50 (still profitable)
  Stock at 153: Profit = $0.50 (at wing level)
  Stock at 154: Profit = $0 (beyond wing)
  Stock at 155: Max loss = -$3.00 (DEFINED, wing width $3 - credit $1)

Max loss: $3.00 (DEFINED, manageable)
Risk management: Easy (known upfront)
Position sizing: Easy to calculate
```

---

**Comparison Table:**

| Metric | Naked Straddle | Wide-Wing Fallback |
|--------|---|---|
| Entry Credit | $4.50 | $4.00 |
| Max Profit | $4.50 | $2.00 (50% profit target) |
| Win Probability | 60-70% | 65-75% (wider zone) |
| Max Loss | UNLIMITED | $3.00 (DEFINED) |
| Return % | 100%+ if stock holds | 66.7% on risk ($2/$3) |
| Risk Known Upfront? | NO | YES |
| Position Sizing | Difficult | Easy |
| Capital Required | Large reserves | Only max loss |

**Key insight:** Trade $0.50 in entry credit and wider max loss for DEFINED RISK that's easy to manage.

---

## Entry Condition Matrix with Fallback

### Example: AAPL Earnings Decision Tree

```
AAPL metrics:
  - Dispersion: 0.0125 (very predictable)
  - IV Rank: 1.25 (high)
  - Expected move: 3.2%
  - Realized move ratio: 0.95 (not gappy)

Decision Matrix (original):
  Low dispersion + High IV + No gap
  → SHORT_STRADDLE selected

Risk Constraint Check:
  allow_naked_strategies: false
  → Fallback triggered!

Get Fallback Strategy:
  original = SHORT_STRADDLE
  fallback = IRON_FLY_WIDE
  wing_multiple = 8.0x

Apply Fallback Config:
  strategy_config = config["iron_fly"]
  strategy_config["wing_width_credit_multiple"] = 8.0
  
Final Result:
  Strategy: IRON_FLY_WIDE
  Wing Multiple: 8.0x credit width
  Entry Credit: $4.00-4.50 (wings cost $0.50)
  Max Loss: Defined at wing width
  Reason: "Simulated naked short straddle with defined risk"
  
Print Warning to User:
  [WARNING] Strategy fallback applied for AAPL
  Original: SHORT_STRADDLE
  Fallback: IRON_FLY_WIDE (8.0x credit multiple)
  Reason: Undefined risk not allowed
  To enable: Set "allow_naked_strategies": true in config
```

---

## When to Use Each Profile

### Use CONSERVATIVE (4.0x) when:
- Portfolio has strict risk limits (e.g., daily max loss per position)
- Capital is limited
- Want tightest possible risk management
- Willing to trade lower win rate for smaller losses
- Building up trading history / track record

### Use MODERATE (6.0x) when:
- Balanced risk/reward desired
- Standard earnings plays
- Most common recommendation
- **Default choice** for most situations
- Sufficient capital but not unlimited

### Use AGGRESSIVE (8.0x) when:
- Significant capital available for larger max losses
- Want to closely simulate naked strategy edge
- Win rate more important than tight risk
- Confident in edge (low dispersion stocks)
- Experienced trader comfortable with wider swings

---

## Implementation: Code Integration

### Selection Logic

```python
from src.strategy_fallback import get_fallback_strategy

# Normal flow
original_strategy = "SHORT_STRADDLE"
allow_naked = config.get("allow_naked_strategies", False)

# Get final strategy
result = get_fallback_strategy(original_strategy, allow_naked, config)

# Use result
if result["is_fallback"]:
    print(f"[WARNING] {result['reason']}")
    final_strategy = result["strategy"]      # e.g., "IRON_FLY_WIDE"
    wing_multiple = result["wing_multiple"]  # e.g., 8.0
else:
    final_strategy = original_strategy
    wing_multiple = config["wing_width_credit_multiple"]
```

### Config Modification

```python
from src.strategy_fallback import apply_wide_wings_config

# Get base strategy config
strategy_config = config["strategies"]["iron_fly"]

# Apply fallback modifications
if result["is_fallback"]:
    strategy_config = apply_wide_wings_config(
        strategy_config,
        wing_multiple=8.0
    )
    # Now config has wing_width_credit_multiple = 8.0
```

---

## Daily Workflow with Fallback

### Morning: Entry Condition Analysis

```bash
python get_candidates.py --date 2026-07-15 --check_fallback
```

**Output:**
```
Candidate: AAPL
  Original Strategy: SHORT_STRADDLE
  Fallback Status: NOT NEEDED (allow_naked_strategies: true)
  Final Strategy: SHORT_STRADDLE
  
Candidate: TSLA
  Original Strategy: SHORT_STRADDLE
  Fallback Status: APPLIED (allow_naked_strategies: false)
  Final Strategy: IRON_FLY_WIDE (8.0x wings)
  Warning: Undefined risk not allowed in config
  Alternative: Wide-wing Iron Fly with 8.0x credit multiple
```

### Entry: Order Spec Generation

```bash
python get_order.py --symbol AAPL --strategy AUTO --check_fallback
```

System returns:
- If naked allowed: SHORT_STRADDLE order spec
- If naked not allowed: IRON_FLY_WIDE order spec with 8x wings

### Exit: Risk Management

All exit triggers work the same:
- 50% profit target (half of entry credit)
- Per-leg delta stops (0.60 for naked/wide-wing straddles, 0.45 for spreads)
- 4-hour IV-crush backstop (exit after IV drops)

---

## FAQ

**Q: Does fallback reduce my win rate?**
A: No. Win rates stay similar because:
- Same entry price (ATM)
- Same exit target (50% profit)
- Wider wings just provide a larger safety net
- Most earnings moves stay within 8% anyway

**Q: How much does fallback cost?**
A: Cost = the wing credit (small):
- You sell the wings, collecting credit
- Typical cost: $0.30-0.50 per spread
- This reduces your max profit by $0.30-0.50
- But gives you defined max loss (huge benefit)

**Q: Can I pick my own wing width?**
A: Yes:
- Edit `WIDE_WING_PROFILES` in `src/strategy_fallback.py`
- Or add custom profile and specify in config

**Q: What if I want NO fallback?**
A: Set `"allow_naked_strategies": true` in config.json
- System will use `SHORT_STRADDLE`, `SHORT_STRANGLE`, `JADE_LIZARD` directly
- Full profit potential
- Unlimited risk on both sides

**Q: When should I use fallback?**
A: Always use fallback when:
- Portfolio has risk limits
- Managing multiple positions (need to know max loss)
- Want consistency (predictable P&L)
- Building track record (want to prove strategy works with defined risk first)

**Q: Does fallback change anything else in my config?**
A: No, only the wing width (`wing_width_credit_multiple`). Everything else stays the same:
- Entry conditions unchanged
- Profit target 50% unchanged
- Delta stops unchanged
- IV crush backstop unchanged

---

## Testing Fallback

```bash
python test_strategy_fallback.py
```

**Expected output:**
```
Test: AAPL | SHORT_STRADDLE | allow_naked=true
  Final strategy: SHORT_STRADDLE
  Is fallback: NO
  
Test: AAPL | SHORT_STRADDLE | allow_naked=false
  Final strategy: IRON_FLY_WIDE
  Is fallback: YES
  Wing multiple: 8.0x
  Reason: Undefined risk not allowed

Test: JPM | IRON_FLY | allow_naked=false
  Final strategy: IRON_FLY
  Is fallback: NO (already defined risk)
```

---

## Summary

| Scenario | Config Setting | Result |
|----------|---|---|
| Want naked | `"allow_naked_strategies": true` | Use SHORT_STRADDLE directly, unlimited risk |
| Want defined | `"allow_naked_strategies": false` | Auto-fallback to Iron Fly / Condor wide-wing |
| Very conservative | Use CONSERVATIVE profile (4.0x) | Tight risk, smaller max loss |
| Balanced (DEFAULT) | Use MODERATE profile (6.0x) | Best risk/reward tradeoff |
| Aggressive | Use AGGRESSIVE profile (8.0x) | Wider zone, closer to naked edge |

**Recommendation:** Start with `"allow_naked_strategies": false` and MODERATE profile (6.0x wings) for balanced, manageable earnings plays.

---

## Navigation

**← Previous:** [Earnings Scan Analysis](./06-scan-analysis.md)  
**Next →** [Trading Workflow](./08-trading-workflow.md)
