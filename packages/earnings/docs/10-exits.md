# Exit Strategy Guide: Profit Targets, Stops, and Backstops

How to manage and exit earnings positions profitably.

---

## Three Exit Mechanisms

All positions have three exit triggers. **Whichever hits first wins.**

### Exit Trigger Priority

1. **4-Hour Backstop** (safety, checked first)
   - Force-exit 4 hours post-announcement
   - Reason: IV crush peak is 15-30 min, fades after
   - Ensures you capture IV edge and don't overstay

2. **Profit Target** (primary)
   - Exit at 50% of entry credit (or 25% for calendars)
   - Most common exit in normal markets
   - Automatic trigger, highest probability

3. **Per-Leg Delta Stops** (protection)
   - Exit if any leg reaches delta threshold
   - Protects against one-sided blowout
   - Prevents unlimited loss on naked strategies

---

## Profit Target Exit

### Short Straddle / Strangle / Reverse Fly

**Target:** 50% of entry credit

**Example: SHORT_STRADDLE**
```
Entry:
  Sell 150 call for $2.55
  Sell 150 put for $2.55
  Entry credit: $5.10
  
Profit target: 50% = $2.55

Exit rule:
  Close position when credit remaining = $2.55
  (i.e., buy to close both legs for total $2.55)
  
Profit: $5.10 - $2.55 = $2.55 per spread
```

**Why 50%?**
- Captures most of volatility crush (typically 20-40% drop)
- Avoids greed (trying to hold for max profit)
- High probability (most positions hit 50% after IV crush)
- Time-efficient (30-60 minutes, not hours)

---

### Iron Fly / Iron Condor / Directional Spreads

**Target:** 50% of entry credit

**Example: IRON_FLY**
```
Entry:
  Sell 150 call / Buy 158 call: $1.20 credit
  Sell 150 put / Buy 142 put: $1.20 credit
  Net entry credit: $0.80 per spread (wings cost)
  
Profit target: 50% = $0.40

Exit rule:
  Close entire position for remaining $0.40 credit
  
Profit: $0.80 - $0.40 = $0.40 per spread
```

---

### Calendar Spreads (ATM Calendar, Double Calendar)

**Target:** 25% of entry debit (half-profit)

**Example: ATM_CALENDAR**
```
Entry:
  Sell front 150 call for $0.40
  Buy back 150 call for $0.70
  Entry debit: -$0.30 paid
  
Profit target: 25% profit = 50% back
  
Exit rule:
  Close position when back-month call worth $0.45
  (You paid $0.70, exit at $0.45 = profit $0.25)
  
Profit: $0.30 debit - $0.25 credit = $0.05 profit
```

**Why 25% for calendars?**
- Calendar spreads work slower (time decay driven)
- Front month expires worthless, back still has value
- 25% profit = good risk/reward for term structure play
- Often takes 5-30 days, not 30-60 minutes

---

## Per-Leg Delta Stops

Protective stops on individual legs.

### Naked Strategies (Short Straddle, Strangle)

**Stop delta:** 0.60

```
Entry:
  Short 150 call (delta +0.50)
  Short 150 put (delta -0.50)
  
Stock moves up:
  Call delta increases: +0.50 → +0.65 → +0.75 → ...
  
Trigger:
  When call delta reaches 0.60 (SHORT, so negative delta exposure):
  → STOP, close call leg
  → Keep put leg open (still profitable)
  
Rationale:
  At delta 0.60, call ITM by ~$0.60
  Loss on call leg = -$0.60 × 100 × quantity
  Time to exit before bigger loss
```

---

### Spread Strategies (Iron Fly, Condor, etc)

**Stop delta:** 0.45

```
Example: IRON_FLY
Entry:
  Sell 150 call (delta +0.50)
  Buy 158 call (delta +0.15)
  Net call delta: +0.35
  
Stock moves up:
  Sell 150 call delta: +0.50 → +0.75
  Buy 158 call delta: +0.15 → +0.35
  Net: +0.40
  
Trigger:
  When net call side delta reaches 0.45:
  → Close call spread
  → Keep put spread (still profitable or safe)
  
Rationale:
  At +0.45, spread is at risk
  Max loss on call spread not far away ($4 in Iron Fly)
  Better to exit and preserve put spread profit
```

---

## 4-Hour Backstop Exit

Hard exit 4 hours after earnings announcement.

**Rationale:**
```
IV crush timeline:
  0-5 min post-annc:    IV drops 30-50%, volatility high
  5-30 min:             IV continues dropping (20-60% total)
  30 min-2 hours:       IV stabilizes, decay slows
  2-4 hours:            IV at floor, no more crush profit
  > 4 hours:            Residual theta decay only, time to exit
```

**Example:**
```
Earnings announced: 4:00 PM
Backstop timer starts: 4:00 PM
Backstop exit trigger: 8:00 PM

Position status at 8:00 PM:
  • If profit target hit earlier: Already exited
  • If delta stop hit earlier: Partially exited
  • If still open: FORCE-EXIT at 8:00 PM
    (Reason: IV crush opportunity passed, time decay takes over)
```

**Modification:**
- Default: 240 minutes (4 hours)
- Can adjust in config: `exit_after_announcement_minutes`
- Shorter (180 min/3 hours) = earlier exit, more conservative
- Longer (300 min/5 hours) = let trades run longer, more aggressive

---

## Exit Mechanics & Examples

### Scenario 1: Stock Holds ATM (Profit Target Hit)

**AAPL SHORT_STRADDLE**
```
Entry (3:35 PM):
  Stock: 150
  Sell 150 call: $2.55
  Sell 150 put: $2.55
  Entry credit: $5.10
  Profit target: $2.55

Post-announcement (4:05 PM):
  Stock: 150 (held ATM!)
  Call price: $1.50 (down from $2.55)
  Put price: $1.20 (down from $2.55)
  
Buy to close:
  Call @ $1.50
  Put @ $1.20
  Total cost: $2.70
  
Profit: $5.10 - $2.70 = $2.40
Status: PROFIT TARGET EXCEEDED (expected $2.55, got $2.40 close enough)
Exit: YES, lock in profit

Time in position: 30 minutes
ROI: $2.40 / $5.10 = 47% return
```

---

### Scenario 2: Stock Moves, Delta Stop Triggered

**JPM IRON_FLY (one-sided move)**
```
Entry (3:40 PM):
  Stock: 150
  Sell 150 call / Buy 158 call: $1.20 credit
  Sell 150 put / Buy 142 put: $1.20 credit
  Entry credit: $0.80 per spread
  Profit target: $0.40
  Delta stop: 0.45 (per-leg)

Post-announcement (4:10 PM):
  Stock: 153 (+2%)
  Call spread: ITM (150 call now worth $3.00)
    Short 150 call delta: +0.75
    Buy 158 call delta: +0.25
    Net call delta: +0.50 (EXCEEDS 0.45 threshold)
    
Put spread: OTM (150 put now worth $0.10)
    Safe, no stop triggered
    
Delta stop triggered on call side:
  Buy to close 150 call / 158 call spread
  Close at $2.50 cost (originally $1.20 credit)
  Loss on call spread: -$1.30
  
Put spread still open:
  Sell 150 put (now $0.10)
  Still profitable
  
Decision:
  Keep put spread open (protected by 142 put)
  Let put spread hit profit target
  
Wait for put spread to decay more:
  Put 150 now $0.05, 142 put $0.01
  Total put credit now $0.04 (originally $1.20, profit $1.16)
  
Final exit:
  Buy to close put spread @ $0.04
  Total profit: $1.16 (put side only)
  
Outcome: PARTIAL WIN
  Call side: -$1.30 loss
  Put side: +$1.16 profit
  Net: -$0.14 loss (but caught it with stop)
```

---

### Scenario 3: Stock Gaps, Holding Through Backstop

**TSLA SHORT_STRADDLE (unexpected gap)**
```
Entry (3:33 PM):
  Stock: 200
  Sell 200 call: $5.00
  Sell 200 put: $5.00
  Entry credit: $10.00
  Profit target: $5.00
  Delta stop: 0.60
  Backstop: 8:00 PM (4 hours)

Post-announcement (4:05 PM):
  Stock: 212 (+6% gap!)
  Call now $8.00 (deeply ITM)
  Put now $0.10 (worthless, OTM)
  
Delta stop check:
  Call delta: +0.90 (EXCEEDS 0.60 threshold)
  → STOP TRIGGERED on call
  
Exit call side:
  Buy to close call @ $8.00
  Loss on call: -$3.00
  
Keep put side:
  Already profitable ($5.00 collected, $0.10 cost to close)
  Profit: $4.90
  
Total P&L at 4:05 PM:
  Call loss: -$3.00
  Put profit: +$4.90
  Net: +$1.90 (STILL PROFITABLE!)
  
Status: PARTIAL STOP-LOSS EXIT, NET PROFIT
```

---

### Scenario 4: Calendar Spread, Time Decay

**ATM CALENDAR (slow decay play)**
```
Entry (Day 1, 3 weeks before earnings):
  Stock: 100
  Sell front 100 call: $0.40 (30 DTE)
  Buy back 100 call: $0.70 (60 DTE)
  Entry debit: -$0.30
  Profit target: 50% back = exit when back worth $0.45

Day 5 (2.5 weeks out):
  Stock: 100 (unchanged)
  Front call now: $0.25 (10 DTE, losing fast)
  Back call now: $0.50 (45 DTE, slower decay)
  
Current credit: $0.40 - $0.25 = $0.15 (vs $0.70 entry cost)
Back current worth: $0.50

If close now:
  Buy back @ $0.25
  Sell back @ $0.50 (no, keep it)
  Actually need to close calendar to lock profit
  
Close strategy:
  Buy front call to close @ $0.25
  Sell back call to close @ $0.50
  Net credit: $0.50 - $0.25 = $0.25
  
Profit: Paid $0.30 debit, now $0.25 credit
Result: LOSS of $0.05 (but small)
  
Wait more:
  If we wait until day 10 (earnings day):
  Front call: $0.02 (1 DTE, nearly worthless)
  Back call: $0.30 (30 DTE, still has value)
  
Close at expiration:
  Front call expires worthless, profit $0.40
  Back call worth $0.30, had cost $0.70, loss $0.40
  
Net: $0.40 - $0.40 = $0.00 breakeven

Better to exit early when profit target hit ($0.45 back)
Or use time to manage: Close front, roll back to new month
```

---

## Exit Checklist

**Before any exit:**
```
□ Confirm exit trigger (profit target, stop, or backstop)?
□ Current market price vs limit order?
□ Bid-ask spread tight (or widened due to volatility)?
□ Commission factored into profit?
□ Order type correct (market vs limit)?
□ Quantity correct (all legs)?
□ Broker connection stable?
```

**After exit:**
```
□ Confirm fills received?
□ P&L calculated correctly?
□ Position removed from portfolio system?
□ Profit/loss logged?
□ Lessons learned noted?
```

---

## Common Exit Problems

### "Profit target not hit, position still open at backstop"
- IV crush slower than expected?
- Stock moved beyond profit zone?
- Consider: Close at market (avoid gap risk overnight)
- Decision: Let backstop exit or take market exit

### "Delta stop triggered, but lost money on that leg"
- This is normal: Stop protects, doesn't guarantee profit
- Other legs often profit to offset
- Review: Was stop trigger point optimal?

### "Partial fill on close, some legs still open"
- Resubmit close order for remaining legs
- Use limit order (don't let them escape)
- Adjust for partial fill in P&L calculation

### "Spreads way ITM, can't close both legs together"
- Buy/sell each leg separately
- Accept higher slippage due to ITM spread
- Or use market order for speed (avoid more loss)

---

## Navigation

**← Previous:** [Trading Workflow](./08-trading-workflow.md)  
**Next →** [Examples & Case Studies](./11-examples.md)
