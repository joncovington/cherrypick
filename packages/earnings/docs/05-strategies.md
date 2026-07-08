# Complete Strategy Guide: All 10 Strategies Explained

Deep dive on structure, entry conditions, exits, and when to use each strategy.

---

## Strategy Overview Matrix

| # | Strategy | Entry | Risk | Entry Condition | Exit Target | Best For |
|---|----------|-------|------|---|---|---|
| 1 | **Short Straddle** | Sell ATM call + put | Unlimited | High IV + σ < 0.08 | 50% credit | Maximum edge, max premium |
| 2 | **Reverse Fly** | Long ATM + short wings | Defined | Gap premium (ratio > 1.10) | 50% credit | Capture IV crush + gap |
| 3 | **Iron Fly** | Short ATM + long wings | Defined | Medium IV + σ < 0.20 | 50% credit | Balanced risk/reward |
| 4 | **Iron Condor** | Short OTM spread both sides | Defined | Wide range + σ < 0.25 | 50% credit | Directional-neutral |
| 5 | **Short Strangle** | Sell OTM call + put | Unlimited | OTM strikes only | 50% credit | Lower capital, OTM only |
| 6 | **Jade Lizard** | Short spread + naked opposite | Partial | Directional + σ 0.10-0.20 | 50% credit | Directional bias + hedge |
| 7 | **Directional Spread** | Short call/put + long hedge | Defined | Directional + IV skew | 50% credit | One-sided move |
| 8 | **Broken Wing Butterfly** | Short wide middle + long narrow wings | Defined | IV skew + σ < 0.20 | 50% credit | Asymmetric moves |
| 9 | **ATM Calendar** | Short front + long back call | Defined | Low IV + σ < 0.10 | 25% debit | Term structure edge |
| 10 | **Double Calendar** | Short call + short put (both front) | Defined | Low IV + both sides quiet | 25% debit | Symmetric term structure |

---

## 1. SHORT STRADDLE (Unlimited Risk)

### Structure

```
SELL ATM Call    (e.g., 150 call)
SELL ATM Put     (e.g., 150 put)

Entry:   Collect $5.00 (e.g., $2.50 call + $2.50 put)
Max Profit:  $5.00 (if stock stays ATM)
Max Loss:  UNLIMITED (stock can gap 20%+)
```

### Entry Conditions

- **Dispersion** < 0.08 (ultra-predictable only)
- **IV Rank** > 1.20 (high premium required)
- **Entry Credit** > $3.00 (worth the risk)
- **Realized Move Ratio** < 1.10 (not expecting gap)

### Exit Strategy

**Primary:** 50% profit target
- Entry credit: $5.00
- Profit target: $2.50
- Exit at this level or earlier

**Secondary:** Per-leg delta stops
- Call stops if delta reaches 0.60
- Put stops if delta reaches 0.60
- Protects against one-sided blowout

**Tertiary:** 4-hour IV-crush backstop
- Exit at 4 hours post-announcement
- IV crush main profit driver, capture it and get out

**Priority order:** Backstop checked first (safety), then profit target, then delta stops

### Real-World Example

```
AAPL Earnings, Expected Move: 3.2%, σ = 0.015 (ultra-tight)

Entry (Day before close):
  Sell 150 call for $2.60
  Sell 150 put for $2.70
  Entry credit: $5.30
  Profit target: 50% = $2.65

Post-announcement outcomes:
  Stock closes at 150 (ATM):     Max profit ($5.30)
  Stock closes at 152 (+1.3%):   Profit $3.30 (still safe)
  Stock closes at 155 (+3.3%):   Profit $1.30 (wings don't exist!)
  Stock closes at 160 (+6.7%):   UNLIMITED LOSS (stock at wing level)
```

### Why It Works

- Ultra-tight historical moves justify naked risk
- High IV provides generous entry premium ($3-5)
- IV crush (20-40% drop) profits you even if stock moves slightly
- Selective use on only most predictable stocks

### Risk Profile

- ✓ Highest entry credit ($3-5 per spread)
- ✓ Highest win rate (70-75% on predictable stocks)
- ✗ Unlimited max loss (tail risk)
- ✗ Capital intensive (need reserves for gap risk)
- ✗ Only for ultra-predictable stocks

---

## 2. REVERSE FLY (Defined Risk)

### Structure

```
LONG ATM Call    (e.g., 150 call)
SHORT ATM Call   (e.g., 150 call) x2
LONG ATM Call    (e.g., 150 call)
Minus:
SHORT ATM Put    (e.g., 150 put)
LONG ATM Put     (e.g., 150 put) x2
SHORT ATM Put    (e.g., 150 put)

Simplification: Long ATM straddle + short OTM wings
Entry: Collect $2.00 (long straddle costs $1.00, wings collected)
Max Profit: Limited to max credit
Max Loss: DEFINED at wing width - credit
```

### Entry Conditions

- **Realized Move Ratio** > 1.10 (gap premium detected)
- **Dispersion** < 0.30 (not too unpredictable despite gap)
- **Entry Credit** > $1.50 (worth the long straddle cost)

### Exit Strategy

**Primary:** 50% of max credit
- Profit target: Half the net credit collected
- Take at this level

**Secondary:** Max defined loss
- Stop if position drops below -100% of max loss
- Max loss = wing width - credit

### Real-World Example

```
BioTech Stock, Gap Premium Detected, realized_move_ratio = 1.25

Entry (day-of):
  Long ATM 100 call - costs $2.50
  Short ATM 100 call - receives $2.50
  (Net so far: $0, call straddle is flat)
  
  Long ATM 100 put - costs $2.50
  Short ATM 100 put - receives $2.50
  (Net so far: $0)
  
  Buy OTM 108 call for $0.50
  Buy OTM 92 put for $0.50
  Net: PAYING $1.00 (wings cost, not collect)
  
Wait, that's a debit, not credit! Correction:

Entry (corrected):
  Sell ATM 100 call for $2.50
  Buy ATM 100.50 call for $2.40
  Net call spread: $0.10 credit
  
  Sell ATM 100 put for $2.50
  Buy ATM 99.50 put for $2.40
  Net put spread: $0.10 credit
  
  Total entry credit: $0.20
  Max loss: ($0.50 spread width) - $0.20 credit = $0.30
  Win rate: High because you only collect small credit but get defined loss
```

### Why It Works

- Captures gap premium (earnings surprise premium)
- But defined risk protects on the downside
- Better than long straddle (you're selling premium, not just buying)

### Risk Profile

- ✓ Gap premium capture ($1.50-3.00 credit)
- ✓ Defined max loss (manageable)
- ✓ Good for portfolios with risk constraints
- ✗ Lower credit than naked straddle
- ✗ Complex structure

---

## 3. IRON FLY (Defined Risk)

### Structure

```
SELL ATM Call    (e.g., 150 call)
BUY  OTM Call    (e.g., 158 call)  - 8% OTM
SELL ATM Put     (e.g., 150 put)
BUY  OTM Put     (e.g., 142 put)   - 8% OTM

Entry:   Collect $1.20 (net of short ATM, long wings)
Max Profit:  $1.20 (if ATM stays safe)
Max Loss:  DEFINED at wing width - credit = $8.00 - $1.20 = $6.80
```

### Entry Conditions

- **Dispersion** < 0.20 (medium predictability)
- **IV Rank** 0.75-1.00 (medium premium)
- **Entry Credit** > $0.80 (economical)
- **Realized Move Ratio** < 1.10 (normal IV crush regime)

### Exit Strategy

**Primary:** 50% profit target
- Entry credit: $1.20
- Profit target: $0.60
- Exit at this level

**Secondary:** Per-leg delta stops (0.45 for spreads)
- Monitor short call + put deltas
- Exit if either reaches 0.45

**Tertiary:** 4-hour backstop
- Exit at 4 hours post-announcement

### Real-World Example

```
JPM Earnings, Medium IV, Dispersion = 0.045

Entry (3:50 PM):
  Sell 150 call for $1.50
  Buy 158 call for $0.30
  Net call spread: $1.20 credit
  
  Sell 150 put for $1.30
  Buy 142 put for $0.10
  Net put spread: $1.20 credit
  
  Total entry credit: $0.80 per spread
  Max loss: $8.00 width - $0.80 = $7.20
  Profit target: 50% of $0.80 = $0.40

Post-announcement outcomes:
  Stock at 150:      Max profit ($0.40 realized)
  Stock at 152:      Profit $0.35 (still safe)
  Stock at 156:      Profit $0.10 (getting close to wings)
  Stock at 160:      Max loss ($7.20) hit
  
  But max loss is KNOWN upfront, easy to size
```

### Why It Works

- Balanced: high entry credit ($0.80-1.50) but defined risk
- Medium IV makes it economical
- Wings protect if stock moves 6-8%
- Most common strategy for earnings plays

### Risk Profile

- ✓ Defined risk (easy to size positions)
- ✓ Good entry credit ($0.80-1.50)
- ✓ Flexible wing widths (3x, 6x, 8x credit)
- ✓ Works in most market conditions
- ✗ Requires wider wings than normal for earnings
- ✗ Lower credit than naked straddle

---

## 4. IRON CONDOR (Defined Risk)

### Structure

```
Short Call Spread:
  SELL OTM Call    (e.g., 155 call)
  BUY  OTM Call    (e.g., 160 call)
  Net credit: $0.30

Short Put Spread:
  SELL OTM Put     (e.g., 145 put)
  BUY  OTM Put     (e.g., 140 put)
  Net credit: $0.30

Total entry:   $0.60 credit
Max Loss:  DEFINED at width - credit = $5.00 - $0.60 = $4.40
```

### Entry Conditions

- **Dispersion** < 0.25 (wide range OK)
- **No directional bias** (move could be either direction)
- **IV Rank** 0.60-0.95 (medium)
- **Entry Credit** > $0.50

### Exit Strategy

**Primary:** 50% profit target
- Entry: $0.60
- Target: $0.30
- Exit at level

**Secondary:** Per-leg stops (0.45 delta)
**Tertiary:** 4-hour backstop

### Real-World Example

```
XOM Earnings, Expected Move: $3.00 (3%)

Entry:
  Stock at 100
  Sell 103 call / Buy 105 call — collect $0.30
  Sell 97 put / Buy 95 put — collect $0.30
  Total: $0.60
  
Profit zone: 97 to 103 (wider than Iron Fly at ATM)

Post-announcement:
  Stock at 100: Max profit ($0.30)
  Stock at 101: Profit $0.20
  Stock at 102: Profit $0.10
  Stock at 103: Call spread at edge, breakeven
  Stock at 104: Call spread losing, put spread still safe
  Stock at 105: Max loss on call side ($4.40)
```

### Why It Works

- One-directional expected move? Offset the bias with wider put spread vs call
- Iron Condor is symmetrical; good when no directional bias
- Wide profit zone (good win rate)

### Risk Profile

- ✓ Defined risk
- ✓ Wider profitable range than Iron Fly
- ✓ Good for neutral moves
- ✗ Lower credit than Iron Fly at ATM
- ✗ Requires two spreads (twice the management)

---

## 5. SHORT STRANGLE (Unlimited Risk)

### Structure

```
SELL OTM Call    (e.g., 155 call, 5% OTM)
SELL OTM Put     (e.g., 145 put, 5% OTM)

Entry:   Collect $0.80 (smaller premium than straddle)
Max Profit:  $0.80
Max Loss:  UNLIMITED
```

### Entry Conditions

- **Dispersion** < 0.15 (predictable)
- **IV Rank** > 1.00 (high premium needed)
- **Realized Move Ratio** < 1.10 (not gappy)
- **Entry Credit** > $0.50

### Exit Strategy

**Primary:** 50% profit target
- Entry: $0.80
- Target: $0.40
- Exit at level

**Secondary:** Per-leg delta stops (0.60)
**Tertiary:** 4-hour backstop

### Real-World Example

```
Quiet blue-chip stock, tight earnings history

Entry:
  Sell 105 call (5% OTM) for $0.50
  Sell 95 put (5% OTM) for $0.30
  Entry credit: $0.80
  Profit target: 50% = $0.40

Advantage: Only collect $0.80 (vs $5.00 for straddle)
Disadvantage: Same unlimited risk on premium

Used when: Want smaller position size but still naked risk
```

### Risk Profile

- ✓ Lower capital requirement
- ✓ Undefined risk for smaller credit
- ✗ Unlimited loss (same as straddle)
- ✗ Often not worth it (why risk unlimited for $0.80?)

---

## 6. JADE LIZARD (Partial Risk)

### Structure

```
SELL OTM Call Spread (Short 155, Long 160 call)
  — Defined risk on call side
SELL OTM Naked Put (e.g., 145 put)
  — Undefined risk on put side

Entry:   Collect $0.60 from call spread + $0.40 naked put = $1.00
Max Profit:  $1.00
Max Loss:  UNLIMITED on put side
Hedge:  Directional bias reduces one side
```

### Entry Conditions

- **Clear directional bias** (IV skew strongly favors one side)
- **Dispersion** 0.10-0.20 (moderate)
- **IV Rank** > 0.80 (rich premium on short side)
- **Entry Credit** > $1.00

### Exit Strategy

**Primary:** 50% profit target
**Secondary:** Per-leg delta stops
**Tertiary:** 4-hour backstop

### Real-World Example

```
Tech stock expected to rally, IV skew shows put premium cheap

Entry:
  Sell 155 call / Buy 160 call — collect $0.60
  Sell 145 put naked — collect $0.40
  Total: $1.00
  
Profit zone: If stock rallies past 155, call spread maxes, naked put safe
Drawdown: If stock crashes, naked put bleeds

This is a "bullish" Jade Lizard (can also do bearish with call naked)
```

### Risk Profile

- ✓ Directional hedge on one side
- ✓ Good credit ($1.00-2.00)
- ✗ Partial unlimited risk (one side still naked)
- ✗ Wrong directional bias = losses

---

## 7. DIRECTIONAL SPREAD (Defined Risk)

### Structure

```
Bullish example:
  SELL OTM Put Spread
    Sell 95 put / Buy 90 put
    Net credit: $0.40
    
Bearish example:
  SELL OTM Call Spread
    Sell 155 call / Buy 160 call
    Net credit: $0.40

Max Profit:  $0.40 (credit)
Max Loss:  DEFINED (spread width - credit = $5.00 - $0.40 = $4.60)
```

### Entry Conditions

- **Directional bias** (clear IV skew)
- **Dispersion** < 0.25
- **Entry Credit** > $0.40

### Exit Strategy

**Primary:** 50% profit target ($0.20)
**Secondary:** Per-leg delta stops
**Tertiary:** 4-hour backstop

### Real-World Example

```
Tech stock IV skew: calls expensive (high IV), puts cheap (low IV)

Bearish strategy:
  Sell 155 call for $0.50
  Buy 160 call for $0.10
  Net credit: $0.40
  
This is a "call spread" (bearish)
If stock rallies too much past 155, you lose money (but capped)
```

### Risk Profile

- ✓ Defined risk
- ✓ Directional positioning
- ✓ Skew exploitation
- ✗ Lower credit than Iron Fly
- ✗ Requires directional conviction

---

## 8. BROKEN WING BUTTERFLY (Defined Risk)

### Structure

```
Short wide middle (e.g., sell 150 call, sell 150 put)
Long narrow wings (e.g., buy 155 call, buy 145 put)

Net: Wider on one side, narrower on other

Entry:   Collect $0.40
Max Profit:  $0.40
Max Loss:  DEFINED (asymmetric)
```

### Entry Conditions

- **IV skew** favors asymmetric structure
- **Dispersion** < 0.20
- **Entry Credit** > $0.30

### Exit Strategy

**Primary:** 50% profit target
**Secondary:** Per-leg delta stops
**Tertiary:** 4-hour backstop

### Real-World Example

```
Earnings expected to move down (IV skew shows put premium high)

Entry:
  Sell 150 call for $1.50
  Buy 155 call for $0.40
  Net call spread: $1.10 credit
  
  Sell 150 put for $2.00
  Buy 145 put for $0.80
  Net put spread: $1.20 credit
  
But we break the wings:
  Use $1.10 call credit + only $0.40 put credit = $1.50 total
  (Not symmetric — wider on call side, narrower on put side)
  
Result: Asymmetric profit zone, takes advantage of skew
```

### Risk Profile

- ✓ Skew exploitation
- ✓ Defined risk
- ✓ Asymmetric P&L
- ✗ Complex structure
- ✗ Requires IV skew reading

---

## 9. ATM CALENDAR (Defined Risk)

### Structure

```
SELL Front-month ATM Call (30 DTE)
BUY  Back-month ATM Call  (60 DTE)

Entry:   PAY debit $0.30 (long back more expensive than short front)
Max Profit:  Front expires worthless, back still has value (~$0.20)
Max Loss:  DEFINED at entry debit = $0.30
```

### Entry Conditions

- **IV Rank** < 0.60 (low premium environment)
- **Dispersion** < 0.10 (ultra-stable)
- **Entry Debit** small (< $0.30)

### Exit Strategy

**Primary:** 25% of entry debit (half-profit)
- Entry: -$0.30
- Target: Close for -$0.15 (half back)
- Profit: $0.15

**Secondary:** 5 days before front expiration
- Exit remaining position
- Don't let back-month expire, roll forward

### Real-World Example

```
Quiet stock, low IV, no gap expected

Entry (3 weeks out):
  Sell front-month 150 call for $0.50
  Buy back-month 150 call for $0.80
  Net debit: $0.30 paid

Time decay works:
  As weeks pass, front loses value faster
  Back loses value slower (far-out still has theta)
  Calendar spread narrows: pay $0.30, sell for $0.15 = profit $0.15

Exit timing: 5 days before expiration or profit target
```

### Why It Works

- Front-month IV crush after earnings = fast decay
- Long back-month benefits from term structure
- Low IV makes front cheap to sell

### Risk Profile

- ✓ Defined risk (entry debit is max loss)
- ✓ Low capital requirement
- ✓ Time decay works for you
- ✓ Good for boring stocks
- ✗ Low profit ($0.15 on $0.30 debit = 50% ROI)
- ✗ Requires good timing (sell before crush, buy back after)
- ✗ Not profitable if stock moves big (calendar spreads love stillness)

---

## 10. DOUBLE CALENDAR (Defined Risk)

### Structure

```
SELL Front-month ATM Call (30 DTE)
BUY  Back-month ATM Call  (60 DTE)
SELL Front-month ATM Put  (30 DTE)
BUY  Back-month ATM Put   (60 DTE)

Entry:   PAY debit $0.60 (both calendars pay)
Max Profit:  Both expire worthless front, back still has value
Max Loss:  DEFINED at $0.60
```

### Entry Conditions

- **IV Rank** < 0.60 (low premium)
- **Dispersion** < 0.10 (ultra-stable)
- **No directional bias** (both sides same)

### Exit Strategy

**Primary:** 25% of entry debit (half-profit)
- Entry: -$0.60
- Target: Close for -$0.30 (half back)
- Profit: $0.30

**Secondary:** Exit 5 days before expiration

### Real-World Example

```
Super quiet dividend stock, earnings expected to be boring

Entry:
  Sell front 150 call for $0.40
  Buy back 150 call for $0.70
  Call calendar: -$0.30
  
  Sell front 150 put for $0.40
  Buy back 150 put for $0.70
  Put calendar: -$0.30
  
  Total debit: -$0.60
  Profit target: -$0.30 (half back)
```

### Why It Works

- Symmetric (both sides capture time decay)
- Works on very stable stocks
- Double the theta decay benefit

### Risk Profile

- ✓ Symmetric profit zone
- ✓ Very low risk
- ✗ Lowest profit ($0.30 on $0.60 = 50% ROI)
- ✗ Requires very quiet stocks
- ✗ Max profit very small

---

## Strategy Selection Quick Reference

**Want maximum credit?** → SHORT_STRADDLE (if σ < 0.08)

**Want defined risk?** → IRON_FLY (most common)

**Expect gap premium?** → REVERSE_FLY

**Neutral move expected?** → IRON_CONDOR

**Directional bias?** → JADE_LIZARD or DIRECTIONAL_SPREAD

**Low IV environment?** → ATM_CALENDAR or DOUBLE_CALENDAR

**Unpredictable stock?** → Wider wings or calendar spreads (avoid naked)

**Portfolio has risk limits?** → FALLBACK to IRON_FLY_WIDE (8x wings)

---

## Navigation

**← Previous:** [Entry Conditions Framework](./04-entry-conditions.md)  
**Next →** [Earnings Scan Analysis](./06-scan-analysis.md)
