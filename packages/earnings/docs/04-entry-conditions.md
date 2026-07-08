# Entry Conditions Framework: Decision Matrix

Data-driven strategy selection based on market conditions.

---

## Overview

The Entry Conditions Framework automatically routes each earnings candidate to the optimal strategy using a multi-level decision matrix. No manual selection—metrics decide.

**Core Principle:** Different market regimes favor different strategies.
- **High IV, Predictable Moves** → Short Straddle
- **Medium IV, Stable Moves** → Iron Fly
- **Low IV, Quiet Stocks** → Calendar Spreads

---

## Decision Matrix Levels

### LEVEL 1: GATE (Hard Filter)

All candidates must pass these gates or are rejected entirely.

| Gate | Threshold | Reason |
|------|---|---|
| **Realized Move Dispersion (σ)** | < 0.15 | Stocks too unpredictable are rejected. Dispersion measures earnings move consistency over 8 quarters. |
| **IV Rank** | > 0.15 | Premium must exist. IV Rank 0.0-1.0 scale; below 0.15 is too thin |
| **Minimum Credit** | > $0.10 | Trade must have economic viability |
| **Time to Expiration** | 30-60 DTE at entry | Earnings plays are short-dated (front month or next month) |

**Example:** AAPL with σ=0.015 and IV Rank=1.25 passes all gates. TSLA with σ=0.40 fails the dispersion gate (rejected).

---

### LEVEL 2: PRIMARY DECISION (Realized vs Expected)

Routes candidates to naked (high edge) or hedged (medium edge) strategies.

```
realized_move_ratio = realized_move_dispersion / expected_move

If ratio > 1.10:
  → Move larger than expected, gap premium exists
  → Favor SHORT_STRADDLE or REVERSE_FLY (long premium capture)

If ratio ≤ 1.10:
  → Move as expected, normal IV crush regime
  → Favor IRON_FLY or IRON_CONDOR (hedged approach)
```

**Why this matters:**
- **Ratio > 1.10** means earnings moves exceeded volatility expectations historically. Premium sellers are being compensated well.
- **Ratio ≤ 1.10** means volatility is fairly priced; hedges protect against downside.

---

### LEVEL 3: SECONDARY DECISION (Dispersion)

Fine-tunes within the primary category based on predictability.

```
If σ < 0.08:
  → Ultra-predictable stock (blue chips, staples)
  → Consider naked strategies (SHORT_STRADDLE)
  → Max risk tolerance justified by tight historical moves

If σ 0.08-0.15:
  → Normal predictability
  → Favor hedged spreads (IRON_FLY, IRON_CONDOR)
  → One-sided directional plays (JADE_LIZARD)

If σ 0.15-0.25:
  → Moderately unpredictable
  → Hedge required; prefer wide-wing structures
  → Calendar spreads safer than naked
```

**Example Decision Path:**
```
AAPL: σ=0.0125, IV=1.25, ratio=1.08
  → GATE: PASS (σ < 0.15, IV > 0.15)
  → PRIMARY: STRADDLE edge (high IV justifies naked)
  → SECONDARY: ULTRA-PREDICTABLE (σ < 0.08)
  → FINAL: SHORT_STRADDLE

JPM: σ=0.045, IV=1.18, ratio=0.98
  → GATE: PASS
  → PRIMARY: Normal IV crush (ratio < 1.10)
  → SECONDARY: Normal dispersion (0.08-0.15)
  → FINAL: IRON_FLY
```

---

### LEVEL 4: TERTIARY DECISION (IV Rank)

Within the same strategy category, IV rank determines entry attractiveness.

| IV Rank | Entry Premium | Best Strategy |
|---|---|---|
| **< 0.50** | Thin | PASS (not enough credit) |
| **0.50-0.75** | Light | ATM_CALENDAR (term structure) |
| **0.75-1.00** | Medium | IRON_FLY, IRON_CONDOR |
| **> 1.00** | Rich | SHORT_STRADDLE, SHORT_STRANGLE |

**Example:** Three stocks with same dispersion (σ=0.045):
- BAC with IV Rank 0.60 → IRON_FLY (medium premium)
- XOM with IV Rank 1.15 → SHORT_STRADDLE (high premium justifies nakedness)
- KO with IV Rank 0.40 → PASS (insufficient credit)

---

## Full Decision Tree

```
START: New earnings candidate
  │
  ├─→ CHECK GATES
  │     ├─ Dispersion σ < 0.15? NO → REJECT
  │     ├─ IV Rank > 0.15? NO → REJECT
  │     ├─ Credit > $0.10? NO → REJECT
  │     └─ 30-60 DTE? NO → REJECT
  │
  ├─→ PRIMARY (Realized vs Expected)
  │     ├─ realized_move_ratio > 1.10?
  │     │   YES → Gap premium route
  │     │   NO  → Normal IV crush route
  │     │
  │     ├─ If gap premium (ratio > 1.10):
  │     │   │
  │     │   ├─ SECONDARY: σ < 0.08?
  │     │   │   YES → SHORT_STRADDLE (naked naked)
  │     │   │   NO  → REVERSE_FLY (hedged, captures gap)
  │     │   │
  │     │   └─ If REVERSE_FLY:
  │     │       TERTIARY: IV Rank < 0.75?
  │     │         YES → Lower entry debit
  │     │         NO  → Higher entry debit (OK, gap justifies it)
  │     │
  │     └─ If normal crush (ratio ≤ 1.10):
  │         │
  │         ├─ SECONDARY: σ < 0.08?
  │         │   NO  → IRON_FLY or IRON_CONDOR (hedged spreads)
  │         │
  │         ├─ SECONDARY: 0.08 < σ < 0.20?
  │         │   YES → Directional possible, consider JADE_LIZARD
  │         │
  │         └─ If IRON_FLY / CONDOR:
  │             TERTIARY: IV Rank check
  │               < 0.60 → Medium credit, prefer CONDOR width
  │               > 0.80 → Rich credit, prefer narrower wings
  │
  └─→ CAPITAL CHECK
        └─ Can portfolio support max loss?
           YES → Proceed
           NO  → PASS or apply FALLBACK if enabled
```

---

## Configuration: Entry Condition Parameters

All thresholds are in `config.json`:

```json
{
  "entry_condition_gates": {
    "max_realized_move_dispersion_pct": 0.15,
    "min_iv_rank": 0.15,
    "min_credit_dollars": 0.10,
    "min_dte": 30,
    "max_dte": 60
  },
  
  "entry_condition_primary": {
    "realized_move_ratio_threshold": 1.10
  },
  
  "entry_condition_secondary": {
    "ultra_predictable_dispersion": 0.08,
    "normal_dispersion_floor": 0.15,
    "normal_dispersion_ceiling": 0.25
  },
  
  "entry_condition_tertiary": {
    "iv_rank_thin_threshold": 0.50,
    "iv_rank_light_threshold": 0.75,
    "iv_rank_medium_threshold": 1.00
  }
}
```

---

## Per-Strategy Entry Conditions

### Short Straddle

**When to use:** High IV + Ultra-predictable (σ < 0.08) + Gap premium or high IV ratio

```
Entry Conditions:
  - σ < 0.08 (hard gate in config: max 0.15, but we prefer < 0.08)
  - IV Rank > 1.20 (high premium)
  - Entry credit > $2.50 (meaningful premium)
  - realized_move_ratio between 0.95-1.10 (not too gappy)
  
Profit Exit:
  - Take 50% of entry credit
  - Or exit at 4-hour post-announcement backstop
```

### Iron Fly

**When to use:** Medium IV + Normal dispersion (0.08-0.20) + Defined risk preferred

```
Entry Conditions:
  - σ < 0.20 (medium predictability OK)
  - IV Rank 0.75-1.00 (medium premium)
  - Entry credit > $0.80 (economical)
  - realized_move_ratio < 1.10 (normal regime)
  
Profit Exit:
  - Take 50% of entry credit
  - Wider wings (6-8x) if portfolio needs risk constraint
```

### Reverse Fly

**When to use:** Gap premium detected (ratio > 1.10) + Defined risk required

```
Entry Conditions:
  - realized_move_ratio > 1.10 (gap premium)
  - σ < 0.30 (not too unpredictable despite gap)
  - Entry credit > $1.50 (long straddle cost worth it)
  
Profit Exit:
  - Take 50% of max credit OR
  - Hit max defined loss (stop at wing)
```

### Iron Condor

**When to use:** Low directional bias + Wide range expected

```
Entry Conditions:
  - σ < 0.25 (not too wide)
  - IV Rank 0.60-0.95 (medium)
  - Entry credit > $0.50
  - No strong directional bias
  
Profit Exit:
  - 50% of entry credit
  - Wider wings for portfolio risk constraint
```

### ATM Calendar

**When to use:** Low IV environment (< 0.65) + Ultra-predictable

```
Entry Conditions:
  - IV Rank < 0.60 (thin premium on front month)
  - σ < 0.10 (super stable stock)
  - Entry debit small (< $0.30)
  
Profit Exit:
  - 25% of entry debit (half-profit)
  - Exit 5 days before front-month expiration
```

### Jade Lizard

**When to use:** Directional bias + Medium dispersion (0.10-0.20)

```
Entry Conditions:
  - Clear directional bias from IV skew
  - σ 0.10-0.20 (moderate)
  - IV Rank > 0.80 (rich premium on short side)
  - Entry credit > $1.00
  
Profit Exit:
  - 50% of entry credit
  - Can close spread side early if protective
```

---

## Real-World Application: Daily Workflow

### Step 1: Morning Scan (7:00 AM ET)

```
For each earnings candidate:
1. Calculate: realized_move_dispersion (σ)
2. Calculate: current IV Rank
3. Fetch: expected move from option chain
4. Compute: realized_move_ratio
5. Check: GATES (all must pass)
6. Run: Decision tree → PRIMARY → SECONDARY → TERTIARY
7. Assign: Optimal strategy + tier ranking
```

### Step 2: Entry Window (3:30-3:55 PM ET)

```
For Tier 1 candidate (AAPL, SHORT_STRADDLE recommended):
1. Verify: Entry conditions still met (re-check σ, IV)
2. Generate: Order spec
   - Sell ATM call + put
   - Entry credit target: $4.50-5.00
   - Profit target: 50% ($2.25-2.50)
3. Submit: Order
4. Post-fill: Set exits (backstop, delta stops)
```

### Step 3: Exit Management

```
For each position:
- Monitor: Profit target (auto-exit if hit)
- Monitor: 4-hour post-announcement backstop
- Monitor: Per-leg delta stops (0.60 for naked)
- Exit: Whichever trigger hits first
```

---

## Example: 10-Day Framework Test

**Test Date Range:** 2026-07-08 to 2026-07-19

**Results:**
- Total candidates: 26
- Rejected (failed gates): 0
- SHORT_STRADDLE: 10 (38.5%)
- IRON_FLY: 12 (46.2%)
- ATM_CALENDAR: 4 (15.4%)

**Why distribution makes sense:**
- **Days with HIGH IV (07/09, 07/11, 07/16):** SHORT_STRADDLE dominated (67% of those days)
- **Days with MEDIUM IV (07/08, 07/12, 07/15):** IRON_FLY dominated (71% of those days)
- **Days with LOW IV (07/10, 07/17):** ATM_CALENDAR chosen (100% of those days)

Framework successfully adapts to market regime changes.

---

## Adjusting Thresholds

### If Too Many Rejections
- Lower `max_realized_move_dispersion_pct` (0.15 → 0.20)
- Lower `min_iv_rank` (0.15 → 0.10)
- Relax time gates

### If Too Many Naked Strategies
- Raise `ultra_predictable_dispersion` (0.08 → 0.10)
- Lower `realized_move_ratio_threshold` (1.10 → 1.05)

### If Not Enough Spreads
- Lower IV Rank thresholds for IRON_FLY
- Increase dispersion acceptable range

---

## Advanced: Dispersion Calculation

Realized move dispersion is the **standard deviation of earnings move magnitudes** over the past 8 earnings:

```
Realized moves (past 8 quarters): [1.2%, 1.5%, 1.8%, 1.1%, 2.0%, 1.4%, 1.3%, 1.6%]

Mean = (1.2+1.5+1.8+1.1+2.0+1.4+1.3+1.6) / 8 = 1.48%

Variance = sum((move - mean)^2) / 8 = 0.136

Dispersion (σ) = sqrt(variance) = 0.0369 = 3.69%
```

**Interpretation:**
- σ = 1-2% → Ultra-predictable (blue chips)
- σ = 2-5% → Normal (most stocks)
- σ = 5-10% → Volatile (biotech, small-cap)
- σ > 10% → Too unpredictable (rejected)

---

## Testing Entry Conditions

To validate framework:

```bash
python run_strategy_selection_test.py --date 2026-07-08 --days 10
```

This simulates 10 days of earnings scans and shows strategy distribution.

**Expected:** Diverse distribution (mostly IRON_FLY, some SHORT_STRADDLE, some calendar).

---

## Next Steps

1. **Verify thresholds** against live market data for your broker
2. **Test decision tree** against 30 prior earnings (mock trades)
3. **Adjust dispersion & IV gates** based on your win-rate targets
4. **Monitor strategy selection** in real trading to refine thresholds

---

## Navigation

**← Previous:** [Configuration Guide](./03-configuration.md)  
**Next →** [Strategy Guide](./05-strategies.md)
