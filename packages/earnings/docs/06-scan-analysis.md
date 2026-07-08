# Earnings Scan Analysis: Daily Process

How to analyze earnings calendar and identify candidates for each strategy.

---

## Overview

Each morning, scan the earnings calendar for candidates. Analyze each using the entry condition framework to assign the optimal strategy.

**Daily Output:** 3-5 Tier 1 candidates with concrete order specs ready for entry.

---

## Three-Tier Analysis Approach

### TIER 1: Data Collection

For each candidate, gather:

```
Stock Metrics:
  - Symbol, earnings date, announcement timing
  - Expected move (IV skew, option chain)
  - Current IV rank, historical IV
  
Historical Analysis:
  - Past 8 earnings moves (magnitude only)
  - Realized move dispersion (σ)
  - Consistency score (low σ = predictable)
  
Current Market:
  - Stock price, bid-ask spreads
  - Call/put prices at ATM and wings
  - Skew direction (call IV vs put IV)
  - Days to expiration (DTE)
```

**Tool:** `get_candidates --date YYYY-MM-DD` returns this data

**Output:** Candidate scorecard with all metrics

---

### TIER 2: Decision Matrix Routing

Apply entry condition framework:

```
1. CHECK GATES
   ✓ Dispersion < 0.15?
   ✓ IV Rank > 0.15?
   ✓ Credit > $0.10?
   ✓ 30-60 DTE?
   
   If NO to any → REJECT

2. PRIMARY DECISION (Realized vs Expected)
   computed_ratio = realized_move / expected_move
   
   If ratio > 1.10 → Gap premium (use SHORT_STRADDLE or REVERSE_FLY)
   If ratio ≤ 1.10 → Normal crush (use IRON_FLY or CONDOR)

3. SECONDARY DECISION (Dispersion Fine-tuning)
   If σ < 0.08  → Favor naked (SHORT_STRADDLE if gap approved)
   If σ < 0.20  → Favor spreads (IRON_FLY, CONDOR)
   If σ < 0.30  → Wider wings or calendar

4. TERTIARY DECISION (IV Rank)
   If IV < 0.60  → Calendar strategies
   If IV 0.60-1.00 → Medium strategies (IRON_FLY, CONDOR)
   If IV > 1.00  → Premium strategies (SHORT_STRADDLE, STRANGLE)

5. FINAL ASSIGNMENT
   Strategy: [SHORT_STRADDLE | REVERSE_FLY | IRON_FLY | ...]
   Confidence: TIER 1 | TIER 2 | TIER 3
```

---

### TIER 3: Strategy Assignment & Ranking

Rank all passing candidates by edge quality:

**TIER 1 (High Edge):**
- Tight dispersion (σ < 0.10)
- Rich IV (IV Rank > 1.00)
- Clear strategy fit
- Credit > entry threshold

**Example:** AAPL, σ=0.0125, IV=1.35
- Dispersion exceptional (Tier 1)
- IV rich (Tier 1)
- Strategy: SHORT_STRADDLE
- Ranking: TIER 1 (execute first)

---

**TIER 2 (Medium Edge):**
- Normal dispersion (0.10 < σ < 0.15)
- Medium IV (0.70 < IV < 1.00)
- Standard strategy fit

**Example:** JPM, σ=0.045, IV=1.18
- Dispersion normal (Tier 2)
- IV good (Tier 1-2)
- Strategy: IRON_FLY
- Ranking: TIER 2 (execute if capital available)

---

**TIER 3 (Low Edge):**
- Barely pass gates
- Low IV or high dispersion
- Calendar strategies only

**Example:** QUIET, σ=0.0065, IV=0.78
- Dispersion excellent but IV too low
- Strategy: ATM_CALENDAR
- Ranking: TIER 3 (execute if filling time)

---

## Real-World Examples

### Example 1: Apple Earnings

**Data Collection:**
```
Symbol: AAPL
Earnings: 2026-07-15 (after close)
Expected move: 3.2% ($4.80 on $150 stock)
IV Rank: 1.35 (high)
Dispersion (σ): 0.0125 (ultra-tight, 1.25%)
Realized move (past 8): [1.1%, 1.3%, 1.5%, 1.2%, 1.4%, 1.1%, 1.0%, 1.3%]
Realized/Expected ratio: 1.2% / 3.2% = 0.375 (NOT gap)
```

**Decision Matrix:**
```
GATE 1: σ < 0.15? YES (0.0125)
GATE 2: IV > 0.15? YES (1.35)
GATE 3: Credit > $0.10? YES
GATE 4: 30-60 DTE? YES

PRIMARY: realized_ratio = 0.375 < 1.10 → Normal IV crush regime
  ✓ No gap premium, normal conditions

SECONDARY: σ < 0.08? YES (0.0125)
  ✓ Ultra-predictable, naked justified

TERTIARY: IV = 1.35 > 1.00
  ✓ Rich premium, SHORT_STRADDLE viable

FINAL: SHORT_STRADDLE
TIER: TIER 1 (highest edge)
```

**Order Spec:**
```
Sell 150 call for $2.55
Sell 150 put for $2.65
Entry credit: $5.20
Profit target: 50% = $2.60
Max loss: UNLIMITED (but σ ultra-tight justifies it)
```

---

### Example 2: Biotech Earnings (Gap Premium)

**Data Collection:**
```
Symbol: IMMUNO
Earnings: 2026-07-16 (before open, 6 AM ET)
Expected move: 4.5% ($2.25 on $50 stock)
IV Rank: 1.25 (high)
Dispersion: 0.085 (slightly volatile)
Realized moves (past 8): [3.2%, 4.1%, 3.8%, 5.2%, 3.5%, 4.0%, 3.7%, 4.3%]
Average realized: 4.1%
Realized/Expected ratio: 4.1% / 4.5% = 0.91

Wait, that's < 1.10. Let me recalculate:
Realized move dispersion σ should be: sqrt(var(moves)) = ~0.64%

Hmm, let me use pre-earnings gap premium instead:
IV term structure: Front month IV = 1.25, back month = 0.95
This means market is pricing gap premium: 30% IV drop expected
```

**Decision Matrix:**
```
GATE 1: σ < 0.15? BORDERLINE (0.085)
GATE 2: IV > 0.15? YES (1.25)
GATE 3: Credit > $0.10? YES
GATE 4: 30-60 DTE? YES

PRIMARY: IV skew shows front premium rich
  → Gap premium detected (term structure skew)
  ✓ Use REVERSE_FLY (hedged gap capture)

SECONDARY: σ = 0.085 (slightly above ultra-predictable)
  → Not naked territory, need hedge

TERTIARY: IV = 1.25 (high)
  ✓ Can justify gap strategy

FINAL: REVERSE_FLY
TIER: TIER 1 (gap premium detected)
```

**Order Spec:**
```
Buy ATM 50 call for $2.40
Sell ATM 50 call for $2.35
  (net call: $0.05 debit)
Buy ATM 50 put for $2.30
Sell ATM 50 put for $2.25
  (net put: $0.05 debit)
Total entry debit: $0.10
Max loss: If move huge beyond wings: $0.10 + wing width
But you're hedged on both sides
```

---

### Example 3: Quiet Stock (Low IV)

**Data Collection:**
```
Symbol: PG (Procter & Gamble)
Earnings: 2026-07-20
Expected move: 1.5% ($0.75 on $50 stock)
IV Rank: 0.65 (medium)
Dispersion: 0.0120 (tight)
Historical moves: All < 2%
Option prices:
  ATM 50 call: $0.40 (front month)
  ATM 50 call: $0.70 (back month)
  ATM 50 put: $0.35 (front month)
  ATM 50 put: $0.65 (back month)
```

**Decision Matrix:**
```
GATE 1: σ < 0.15? YES
GATE 2: IV > 0.15? YES (0.65)
GATE 3: Credit > $0.10? MARGINAL (options very cheap)
GATE 4: 30-60 DTE? YES

PRIMARY: IV rank 0.65 = medium-low
  → No gap, normal regime, but IV depressed
  ✓ Consider calendar

SECONDARY: σ = 0.0120 (ultra-tight)
  ✓ Very predictable

TERTIARY: IV = 0.65 < 0.75 (threshold for straddles)
  → IV too low to justify premium selling
  → Calendar spread better captures theta

FINAL: ATM_CALENDAR or DOUBLE_CALENDAR
TIER: TIER 2 (lower credit, but defined risk)
```

**Order Spec:**
```
Sell front-month 50 call for $0.40
Buy back-month 50 call for $0.70
  Entry debit: -$0.30
Sell front-month 50 put for $0.35
Buy back-month 50 put for $0.65
  Entry debit: -$0.30
Total double calendar debit: -$0.60
Profit target: Close for -$0.30 (half back)
Profit: $0.30 on $0.60 = 50% ROI
```

---

## Daily Workflow

### 7:00 AM ET: Morning Scan

```bash
python get_candidates.py --date 2026-07-15 --tiers all
```

**Output:**
```
Found 8 earnings today:
1. AAPL      σ=0.0125, IV=1.35  → SHORT_STRADDLE   (TIER 1)
2. MSFT      σ=0.0150, IV=1.30  → SHORT_STRADDLE   (TIER 1)
3. JPM       σ=0.0450, IV=1.18  → IRON_FLY         (TIER 1)
4. XOM       σ=0.0350, IV=1.05  → IRON_FLY         (TIER 2)
5. BAC       σ=0.0420, IV=0.95  → IRON_CONDOR      (TIER 2)
6. PG        σ=0.0120, IV=0.65  → DOUBLE_CALENDAR  (TIER 2)
7. KO        σ=0.0180, IV=0.55  → PASS (IV too low)
8. WMT       σ=0.0250, IV=0.95  → ATM_CALENDAR     (TIER 2)

Tier 1 count: 3 (AAPL, MSFT, JPM)
Recommended action: Enter all 3 Tier 1 positions
```

### 9:00 AM ET: Review & Confirm

- Verify metrics haven't changed overnight
- Check option liquidity (bid-ask spreads)
- Confirm entry premium still available
- Rank final candidates

### 12:00 PM ET: Pre-Entry Preparation

- Load order specs into order management system
- Set profit targets and stop levels
- Verify capital available for max loss
- Test order execution (dry run)

### 3:30 PM ET: Entry Window Opens

- Monitor order book
- Enter first Tier 1 position (AAPL SHORT_STRADDLE)
- After fill, enter second Tier 1 position (MSFT SHORT_STRADDLE)
- After fill, enter third Tier 1 position (JPM IRON_FLY)
- Stagger entries by 2-3 minutes each

### 3:55 PM ET: Entry Window Closes

- No new entries allowed (earnings announcement imminent)
- Check all positions filled
- Verify stops and profit targets set
- Monitor exit triggers

### 4:00 PM ET: Announcement

- Monitor live price
- Watch for 50% profit target hits
- Monitor per-leg delta stops
- Be ready for IV crush exit

### 4:30 PM ET: Post-Exit Review

- Log profits/losses
- Review decision matrix for actual outcome
- Update dispersion calibration if needed
- Prepare next-day report

---

## Red Flags During Scan

**Reject if:**
- Dispersion > 0.15 (unpredictable)
- IV Rank < 0.15 (no premium)
- Bid-ask spread > 50% of width (illiquid)
- Company halted or pending major news
- Options expire before earnings (too soon)

**Caution if:**
- Dispersion 0.12-0.15 (borderline, hedge only)
- IV Rank < 0.60 (very thin, calendar spreads only)
- Options illiquid (wide bid-ask, size < 1000 contracts)
- Single-source premium skew (technical, not fundamental)

**Green light if:**
- Dispersion < 0.10 (very predictable)
- IV Rank > 0.80 (rich premium)
- Options liquid (tight bid-ask, volume > 5000)
- Clear fundamental reason for IV level

---

## Entry Confidence Scoring

Rate each candidate 1-10:

| Factor | Weight | Scoring |
|--------|--------|---------|
| Dispersion | 30% | σ < 0.08 (9-10), < 0.12 (7-8), < 0.15 (5-6) |
| IV Rank | 25% | > 1.00 (9-10), 0.80-1.00 (7-8), 0.60-0.80 (5-6) |
| Credit | 20% | > $2.00 (9-10), > $1.00 (7-8), > $0.50 (5-6) |
| Liquidity | 15% | Tight spread (9-10), Normal (7-8), Wide (5-6) |
| Signal clarity | 10% | Clear edge (9-10), Subtle (7-8), Unclear (5-6) |

**Final score = sum of (factor score × weight)**

**Score > 8.0** = TIER 1 (highest conviction)  
**Score 6.0-8.0** = TIER 2 (medium conviction)  
**Score < 6.0** = TIER 3 (low conviction) or PASS

---

## Command Reference

```bash
# Scan earnings for given date
get_candidates --date 2026-07-15

# Get detailed analysis for one symbol
get_candidate --symbol AAPL --earnings_date 2026-07-15

# Get order spec for entry
get_order --symbol AAPL --strategy SHORT_STRADDLE --earnings_date 2026-07-15

# Log the scan analysis
log_scan --date 2026-07-15 --save results.json
```

---

## Navigation

**← Previous:** [Entry Conditions Framework](./04-entry-conditions.md)  
**Next →** [Strategy Fallback System](./07-strategy-fallback.md)
