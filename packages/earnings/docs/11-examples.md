# Examples & Case Studies: Real-World Trading Scenarios

Complete worked examples showing decision-making, ranking, and execution.

---

## Example 1: Late-Day Earnings Analysis (AMC & BMO)

### Scenario

**Time:** 3:00 PM ET  
**Task:** Analyze AMC (after-close) and BMO (pre-market) earnings  
**Constraints:** $50,000 available capital, max 3 positions/day  
**Goal:** Rank candidates, select top 2-3 for entry at 3:50 PM

---

### Analysis Results

#### Candidate 1: AMC (After Close)

```
Company: AMC Entertainment
Earnings: 2026-07-08, 4:05 PM ET (after close)

Entry Conditions:
  Dispersion: 0.0882 (8.82%) - slightly high
  IV Rank: 1.42 - very high, rich premiums
  Expected Move: ±8.5%
  
Decision Matrix:
  Gate check: PASS (0.0882 < 0.15)
  Primary: Normal IV crush (ratio 0.01)
  Secondary: High volatility (σ > 0.08)
  Tertiary: Very high IV (1.42 > 1.20)
  
Result: IRON_CONDOR (defined risk preferred due to volatility)
Tier: TIER 2 (medium conviction)
Reason: High volatility requires hedging
```

**Ranking Score: 77.0/100**
- Dispersion score: 80 (not ultra-tight)
- IV Rank score: 100 (very high, excellent premiums)
- Strategy score: 65 (Iron Condor = balanced)
- Tier score: 60 (Tier 2 = medium conviction)
- Capital efficiency: 90 (defined risk)

---

#### Candidate 2: BMO (Before Open)

```
Company: Bank of Montreal
Earnings: 2026-07-09, 6:30 AM ET (before open)

Entry Conditions:
  Dispersion: 0.0222 (2.22%) - ultra-tight
  IV Rank: 0.78 - medium premium
  Expected Move: ±2.8%
  
Decision Matrix:
  Gate check: PASS (0.0222 < 0.15)
  Primary: Normal IV crush (ratio 0.01)
  Secondary: Ultra-predictable (σ < 0.05)
  Tertiary: Medium IV (0.75 < 0.78 < 1.00)
  
Result: SHORT_STRADDLE (naked strategy justified)
Tier: TIER 1 (high conviction)
Reason: Bank earnings very stable + medium IV
```

**Ranking Score: 84.0/100**
- Dispersion score: 100 (ultra-tight)
- IV Rank score: 50 (medium, less optimal)
- Strategy score: 95 (Short Straddle = maximum edge)
- Tier score: 100 (Tier 1 = highest conviction)
- Capital efficiency: 50 (naked = capital intensive)

---

### Ranking Decision

**Top Candidates (In Order):**

| Rank | Symbol | Score | Strategy | Tier | Capital Est. | Rationale |
|------|--------|-------|----------|------|---|---|
| 1 | BMO | 84.0 | SHORT_STRADDLE | 1 | $1,000 | Ultra-predictable, maximum edge |
| 2 | AMC | 77.0 | IRON_CONDOR | 2 | $500 | High IV but too volatile for naked |

**Capital Allocation:**
```
Available Capital: $50,000
Position 1 (BMO): $1,000 risk (2%)
Position 2 (AMC): $500 risk (1%)
Remaining Capital: $48,500 (97%)

Execution: Enter both (capital allows)
```

---

### Trade Execution

#### Entry (3:50 PM ET)

**BMO: SHORT_STRADDLE**
```
Order Type: Sell-to-open ATM straddle
Entry strikes: 97 call + 97 put
Entry credit: ~$3.18 per spread
Quantity: 3 spreads
Total credit: $954
Profit target: 50% = $477
Estimated max loss: Unlimited (but σ = 2.22% justifies)
```

**AMC: IRON_CONDOR**
```
Order Type: Sell-to-open call & put spreads
Call spread: Sell 11 call / Buy 16 call
Put spread: Sell 5 put / Buy 0 put
Entry credit: ~$0.80 per spread
Quantity: 6 spreads
Total credit: $480
Profit target: 50% = $240
Max loss: $4.20 per spread = $2,520 max
```

---

#### Post-Entry Management

**Timeline:**
```
4:00 PM     Market close
4:05 PM     AMC earnings announced (+$0.75, +10% gap up!)
            BMO: No announcement yet (tomorrow pre-market)

4:08 PM     AMC position impact:
            Call spread ITM by $0.75
            Put spread OTM by $0.75 (protected)
            
            Delta stops:
            Short call: delta +0.85 (EXCEEDS 0.45 threshold)
            → STOP TRIGGERED on call side
            → Buy back call spread @ $0.95 loss per spread
            → Loss: 6 × $0.95 = $570
            
            Put spread: Still profitable
            → Keep open until profit target or backstop

4:15 PM     AMC put spread status:
            Collected $0.30 on puts (ITM side still works)
            Current P&L on AMC: -$570 (calls) + $180 (puts) = -$390
            
            Decision: Let backstop handle

8:00 PM     4-hour backstop triggered
            Force-exit all remaining AMC positions
            Put spreads closed @ $0.15 credit remaining
            
            Final AMC P&L: -$390 + $90 = -$300 loss
            
            Learning: High-IV gap move hit stop. Defined risk worked.

9:30 AM     BMO market open (earnings announced 6:30 AM pre-market)
(Next day)  BMO stock: +1.2% gap up
            
            Short straddle impact:
            Call: Now $1.80 (vs $1.59 entry)
            Put: Now $1.20 (vs $1.59 entry)
            Total credit: $3.00 remaining (collected $3.18)
            
            Already at 94% profit! (vs 50% target)
            
            Exit straddle immediately
            Profit: $3.18 - $3.00 = $0.18 per spread = $54 total
            
            Actually, let me recalculate:
            Need to buy to close both legs
            Call cost: $1.80
            Put cost: $1.20
            Total cost: $3.00
            Entry credit: $3.18
            Profit: $3.18 - $3.00 = $0.18 × 3 spreads = $54
```

---

### Summary

| Trade | Entry Credit | Exit | P&L | ROI |
|-------|---|---|---|---|
| AMC IRON_CONDOR | $480 | 4-hour backstop | -$300 | -62% |
| BMO SHORT_STRADDLE | $954 | Profit target | +$54 | +5.7% |
| **Total** | **$1,434** | — | **-$246** | **-17%** |

**Lessons:**
- ✓ Ranking worked: BMO (84 score) profited, AMC (77 score) took loss
- ✓ Capital management: 3 spreads each, managed risk properly
- ✗ AMC gap move vs predictions: High IV overrode dispersion concern
- ✓ Stops protected against larger loss (could've been -$1,000+)

---

## Example 2: Ranking with Capital Constraints

### Scenario

**Available Capital:** $10,000  
**Max Positions:** 3  
**Candidates:** 5 earnings today

---

### Candidates & Scores

```
1. AAPL   Score: 92.0  Strategy: SHORT_STRADDLE   Est Risk: $1,200
2. MSFT   Score: 87.0  Strategy: IRON_FLY         Est Risk: $600
3. JPM    Score: 82.0  Strategy: IRON_FLY         Est Risk: $500
4. XOM    Score: 75.0  Strategy: IRON_CONDOR      Est Risk: $800
5. KO     Score: 70.0  Strategy: ATM_CALENDAR     Est Risk: $300
```

---

### Capital Allocation

```
Position 1: AAPL (Score 92) - $1,200 risk
  Remaining: $10,000 - $1,200 = $8,800

Position 2: MSFT (Score 87) - $600 risk
  Remaining: $8,800 - $600 = $8,200

Position 3: JPM (Score 82) - $500 risk
  Remaining: $8,200 - $500 = $7,700

Position 4 attempt: XOM (Score 75) - $800 risk
  Check: Can afford? Yes ($7,700 > $800)
  Check: Max positions? No! (already 3)
  Result: REJECTED - max positions reached

Waitlist: XOM (score 75, capital available but position limit)
Waitlist: KO (score 70, capital available but position limit)
```

---

### Execution Plan

```
Entry trades: AAPL, MSFT, JPM
Waitlist: XOM, KO (if any position exits early)

Capital Utilization: $2,300 / $10,000 = 23%
Unused Capital: $7,700 (27% buffer for adverse moves)
```

---

## Example 3: Auto-Submit vs Manual Selection

### Configuration Options

#### Option A: Auto-Submit Top 2

```json
{
  "auto_submit": true,
  "auto_submit_count": 2,
  "max_positions_per_day": 5,
  "available_capital": 25000
}
```

**Result:**
- System automatically enters top 2 ranked candidates
- No manual confirmation needed
- All 5 candidates evaluated, top 2 selected by score
- Useful for: Active traders who want speed

#### Option B: Manual Selection (Recommended for Beginners)

```json
{
  "auto_submit": false,
  "auto_submit_count": 0,
  "max_positions_per_day": 3,
  "available_capital": 50000
}
```

**Result:**
- System ranks all candidates
- Presents top N candidates to user
- User chooses which to trade (select by rank or manually override)
- Useful for: Learning, risk-averse trading, final approval

#### Option C: Hybrid (Best of Both)

```json
{
  "auto_submit": false,
  "auto_submit_count": 0,
  "max_positions_per_day": 3,
  "available_capital": 50000,
  "require_approval_for": ["HIGH_RISK", "UNLIMITED_RISK"],
  "auto_approve_if_score": 90
}
```

**Result:**
- Scores >= 90: auto-submit (highest conviction)
- Scores 80-89: require manual approval (good but review)
- Scores < 80: require manual approval (lower conviction, be selective)
- Useful for: Balanced approach with guardrails

---

## Example 4: Ranking System Scoring

### Scoring Formula

```
Total Score = 
  (Dispersion Score × 0.25) +
  (IV Rank Score × 0.20) +
  (Strategy Score × 0.20) +
  (Tier Score × 0.25) +
  (Capital Efficiency × 0.10)

Score 90+:  Highest conviction (execute first)
Score 80-89: High conviction (execute second)
Score 70-79: Medium conviction (execute if capital)
Score 60-69: Low conviction (waitlist or skip)
Score <60:  Very low conviction (reject)
```

---

### Detailed Scoring Example

**BMO: SHORT_STRADDLE**

```
1. Dispersion Score: 100/100
   σ = 0.0222 < 0.05
   Very predictable (highest tier)
   
2. IV Rank Score: 50/100
   IV = 0.78 (medium, not high)
   Less than ideal for premium selling
   
3. Strategy Score: 95/100
   SHORT_STRADDLE = maximum edge
   Only naked short straddle ranks higher
   
4. Tier Score: 100/100
   TIER 1 = highest conviction
   Best positioning for earnings surprise
   
5. Capital Efficiency: 50/100
   Naked strategy = capital intensive
   No hedges, need large reserves
   
Total: (100×0.25) + (50×0.20) + (95×0.20) + (100×0.25) + (50×0.10)
     = 25 + 10 + 19 + 25 + 5
     = 84.0/100
```

---

## Example 5: Decision Logging & Audit Trail

### Log Entry (Auto-Generated)

```json
{
  "timestamp": "2026-07-08T15:00:00",
  "action": "RANKED",
  "candidates_analyzed": 5,
  "candidates_selected": 3,
  "top_ranked": "BMO"
},
{
  "timestamp": "2026-07-08T15:45:00",
  "action": "SELECTED_FOR_TRADING",
  "rank": 1,
  "symbol": "BMO",
  "strategy": "SHORT_STRADDLE",
  "score": 84.0,
  "conviction": "High",
  "estimated_capital": 1000
},
{
  "timestamp": "2026-07-08T15:50:00",
  "action": "ORDER_SUBMITTED",
  "symbol": "BMO",
  "strategy": "SHORT_STRADDLE",
  "entry_price": 3.18,
  "quantity": 3,
  "total_credit": 954
},
{
  "timestamp": "2026-07-08T16:05:00",
  "action": "TRADE_UPDATE",
  "symbol": "BMO",
  "event": "ANNOUNCEMENT_PENDING"
},
{
  "timestamp": "2026-07-09T09:30:00",
  "action": "TRADE_EXIT",
  "symbol": "BMO",
  "exit_reason": "PROFIT_TARGET_HIT",
  "exit_price": 3.00,
  "profit": 54
}
```

### Benefits

- **Audit Trail:** Full history of every decision
- **Learning:** Review past rankings vs actual performance
- **Validation:** Confirm strategy selection was optimal
- **Compliance:** Document all trades for regulatory review
- **Improvement:** Identify which scoring factors predict wins

---

## Navigation

**← Previous:** [Exit Strategy Guide](./10-exits.md)  
**← Return to:** [README](./README.md)
