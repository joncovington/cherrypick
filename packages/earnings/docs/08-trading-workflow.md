# Trading Workflow: Day-to-Day Execution

Complete daily checklist and execution steps for overnight earnings plays.

---

## Quick Start: Using Slash Commands

The simplest way to run the system is with two slash commands:

### 3:00 PM ET - Analysis & Strategy Selection
```
/paper-trading-start
```
System analyzes all earnings candidates, ranks them by conviction score, and displays top 3-5 recommendations with entry details.

**Output:** Ranked candidates with recommended strategies, entry prices, profit targets, stops

**Your action:** Review the top candidates, select which ones to trade

### 3:50 PM ET - Execute Trades
You submit orders to your broker using the entry specifications provided by the system.

### 4:00 PM ET - Daily Report & Exit Plan
```
/paper-trading-eod-report
```
System generates end-of-day summary showing:
- Today's analysis summary
- Tomorrow's exit monitoring plan (profit targets, stops)
- Configuration recommendations for adjusting risk
- Risk assessment

**Your action:** Review the plan, understand tomorrow's exit targets

### Next Day 9:30 AM ET - Monitor Exits
Market opens. Your positions profit from IV crush (20-60% volatility drop).

Follow the exit plan from yesterday's `/paper-trading-eod-report` command:
- Monitor for 50% profit target (auto-close if hit)
- Watch for delta stops (triggered by adverse moves)
- Apply 4-hour backstop (force exit if still open)

### Next Day 4:00 PM ET - Log Results
Log exit prices and P&L. System calculates win rate and performance metrics.

For full command documentation, see [SLASH_COMMANDS_GUIDE.md](../SLASH_COMMANDS_GUIDE.md).

---

## Trading Day Timeline (Reference)

### T-1: Day Before Earnings (Optional Pre-work)

**3:00 PM - 4:00 PM ET**

```
1. Review tomorrow's earnings calendar
2. Identify high-quality candidates
3. Gather historical data (dispersion, IV)
4. Pre-screen for entry condition gates
5. Prepare order specs (draft only)
```

---

## Earnings Day Execution

### T+0: Morning (7:00 AM - 12:00 PM ET)

#### Step 1: Scan & Analyze (7:00-9:00 AM)

```bash
# Run automated scan
python get_candidates.py --date YYYY-MM-DD

# Returns: Tier 1, Tier 2, Tier 3 candidates with metrics
```

**Output example:**
```
Found 12 earnings today (2026-07-15)

TIER 1 (3 candidates):
  ✓ AAPL: σ=0.0125, IV=1.35 → SHORT_STRADDLE
  ✓ MSFT: σ=0.0150, IV=1.30 → SHORT_STRADDLE
  ✓ JPM:  σ=0.0450, IV=1.18 → IRON_FLY

TIER 2 (5 candidates):
  • XOM:  σ=0.0350, IV=1.05 → IRON_FLY
  • BAC:  σ=0.0420, IV=0.95 → IRON_CONDOR
  • PG:   σ=0.0120, IV=0.65 → DOUBLE_CALENDAR
  • WMT:  σ=0.0250, IV=0.95 → ATM_CALENDAR
  • KO:   σ=0.0180, IV=0.60 → PASS (IV too low)

TIER 3 (2 candidates):
  ~ GE:   σ=0.0280, IV=0.45 → PASS (insufficient premium)
  ~ F:    σ=0.0320, IV=0.50 → PASS
  
Rejected (3):
  ✗ IMMUNO: σ=0.18, IV=1.25 → REJECT (dispersion > 0.15)
  ✗ RIOT:   σ=0.35, IV=0.80 → REJECT (too volatile)
  ✗ UPRO:   σ=0.42, IV=0.70 → REJECT (too unpredictable)
```

**Actions:**
- Print or save report
- Flag Tier 1 candidates for entry
- Note Tier 2 for backup entries
- Confirm Tier 3 decisions

---

#### Step 2: Research & Confirm (9:00-11:00 AM)

For each Tier 1 candidate:

```
1. Verify fundamentals
   □ Earnings announcement time confirmed?
   □ Any pending news / SEC filings?
   □ Previous quarter surprise?

2. Re-check metrics
   □ Dispersion calculation still valid?
   □ IV Rank update (volatility regime change)?
   □ Expected move recalculation?
   □ Bid-ask spreads on options?

3. Review decision matrix
   □ Gate checks still pass?
   □ Strategy selection optimal?
   □ Confidence score Tier 1?

4. Prepare order spec
   □ Entry strikes & premiums confirmed?
   □ Profit target level calculated?
   □ Stop levels defined?
   □ Max loss understood?

5. Simulate execution
   □ Dry run order in system?
   □ Expected fills reasonable?
   □ Quantity per spread confirmed?
   □ Commissions factored in?
```

**Example:** AAPL SHORT_STRADDLE

```
Morning check (7:00 AM):
  σ = 0.0125 ✓
  IV = 1.35 ✓
  Expected move = 3.2%
  Strategy: SHORT_STRADDLE ✓

Update check (10:00 AM):
  σ still 0.0125 ✓
  IV now 1.38 (slightly higher, even better) ✓
  Expected move same 3.2% ✓
  Bid-ask on 150 call: 2.50-2.55 (tight) ✓
  Bid-ask on 150 put: 2.50-2.55 (tight) ✓
  
Order spec:
  Sell 150 call @ ask 2.55
  Sell 150 put @ ask 2.55
  Expected entry credit: $5.10
  Profit target: 50% = $2.55
  Per-leg delta stop: 0.60
  Backstop: 4 hours post-announcement
  
Max risk: Undefined (ultra-predictable stock justified)
Position size: 5 spreads (test size)
Max loss if gap (10%): -$5000 (acceptable risk for portfolio)
```

---

#### Step 3: Pre-Entry Prep (11:00 AM - 3:30 PM)

**Checklist:**
```
□ Capital available for max loss?
□ Profit target limits set in system?
□ Delta stop orders prepared?
□ Backdoor exit strategy defined?
□ Order routing confirmed (live vs paper)?
□ Position monitoring dashboard ready?
□ Exit alerts configured?
□ Backup plan if fills partial?
```

**Actions:**
- Move candidates to "Ready to Enter" status
- Load order specs into order management system
- Set profit target alerts
- Verify stops are configured correctly
- Test alert system (dry run notification)

---

### T+0: Entry Window (3:30 PM - 3:55 PM ET)

#### Step 4: Execute Entries (3:30-3:55 PM)

**Hard rule:** No entries after 3:55 PM (5 minutes before close)

**Execution sequence:**
```
3:30 PM:  First signal sent for Tier 1 candidate #1 (AAPL)
          Monitor order status, confirm fill
          
3:35 PM:  If AAPL filled, send order for Tier 1 #2 (MSFT)
          Stagger entries by 3-5 minutes to ensure fills
          
3:40 PM:  If MSFT filled, send order for Tier 1 #3 (JPM)
          
3:45 PM:  Confirm all fills received
          Verify positions in portfolio
          Activate profit target exits
          Activate stop-loss monitors
          
3:55 PM:  ENTRY WINDOW CLOSES
          No new earnings entries allowed
          All positions should be filled by now
```

**If partial fills:**
```
Scenario 1: AAPL filled only 3 of 5 spreads
  → Accept 3-spread fill
  → Adjust profit target (still 50%)
  → Proceed with position management

Scenario 2: MSFT order rejected (liquidity issue)
  → Fallback to Tier 2 candidate (XOM)
  → OR skip and focus on filled positions
  → DO NOT increase size on other positions

Scenario 3: All fills rejected
  → Cancel remaining orders
  → Review execution logs for errors
  → Prepare Tier 2 entries for next day
```

---

### T+0: Post-Announcement (4:00 PM - 4:30 PM)

#### Step 5: Monitor Exits (4:00-4:30 PM)

**Immediate post-announcement (first 15 minutes):**

```
4:00-4:15 PM:
  □ Stock price stable after gap (if any)?
  □ IV crushing as expected?
  □ Positions showing profit?
  □ Any delta stops triggered?
  □ Any profit targets hit?
  
If profit target hit:
  → AUTO-EXIT triggered
  → Close position at target
  → Log profit
  → Monitor remaining positions

If delta stop triggered:
  → PROTECTIVE EXIT
  → Close affected leg
  → Monitor other leg
  → Do NOT leg out (both sides simultaneously)

If IV crush happening:
  → POSITIVE, as expected
  → Profit target easier to reach
  → Monitor for exit opportunity
```

---

#### Step 6: Extended Monitoring (4:15-4:30 PM)

```
4:15 PM:  IV crush (20-30% drop) likely complete
          Check remaining positions

4:20 PM:  If position still open:
          • Review profit/loss
          • Check 4-hour backstop timer
          • Assess probability of hitting target
          • Consider early exit if profitable

4:30 PM:  Market closes
          Status update on all positions
          Log trade results
```

---

### T+0 Evening: Post-Trade Analysis (4:30-5:30 PM)

#### Step 7: Log Results & Learning (4:30-5:00 PM)

```bash
python log_trade --date YYYY-MM-DD --save results.json
```

**Log each trade:**
```
Symbol: AAPL
Strategy: SHORT_STRADDLE
Entry time: 3:32 PM
Entry credit: $5.10
Exit trigger: Profit target (50%)
Exit time: 4:08 PM (36 minutes post-announcement)
Exit price: $2.55 (half of entry)
Profit: $255 (5 spreads × $0.51)
Realized move: 1.8% (within expected 3.2%)
Outcome: WIN
Notes: IV crush happened fast, exited smoothly
```

---

#### Step 8: Review Decision Matrix (5:00-5:30 PM)

```
For each trade, ask:
1. Was strategy selection optimal?
   - Did SHORT_STRADDLE outperform alternatives?
   - Could IRON_FLY have worked better?
   - Was dispersion accurately measured?

2. Did entry conditions hold through announcement?
   - Any gap or move outside expectations?
   - IV crush as predicted?
   - Bid-ask widened unexpectedly?

3. What would improve next time?
   - Earlier exit at target?
   - Wider stops?
   - Different profit target %?
   - Bigger or smaller position size?

4. Update mental model
   - Confirm or adjust dispersion threshold?
   - IV crush behavior as expected?
   - Realized vs expected move ratio accurate?
```

---

## Example: AAPL Short Straddle - Complete Trade

### Morning (7:00 AM)
```
Scan results:
  AAPL: σ=0.0125, IV=1.35, expected=3.2%
  → Strategy: SHORT_STRADDLE (Tier 1)
```

### Pre-Entry (11:00 AM)
```
Confirmation:
  σ = 0.0125 (tight, justified)
  IV = 1.38 (even better)
  150 call: bid 2.50, ask 2.55
  150 put: bid 2.50, ask 2.55
  
Order prep:
  Sell 5 × 150 call @ 2.55 = $1,275
  Sell 5 × 150 put @ 2.55 = $1,275
  Total credit: $2,550
  
Profit target: $1,275 (50%)
Per-leg delta stop: 0.60
Backstop: 4 hours (8:00 PM)
Max loss: Undefined
Position limit: $2,550 total risk
```

### Entry (3:32 PM)
```
Order sent: Sell 5 straddles, 150 strike

3:33 PM - Partial fill: 3 spreads filled @ $5.10
         (Better than expected!)
         
3:34 PM - Remaining 2 spreads on market
         Waiting for fill...
         
3:35 PM - Market moving (seller imbalance)
         Adjust limit order up to $5.05
         
3:36 PM - Remaining 2 spreads rejected
         Accept 3-spread fill
         Total credit: 3 × $510 = $1,530
         Profit target: $765 (50%)
         
3:37 PM - Order confirmed
         Position now LIVE
         Set profit target alert at -$765
         Set delta stop at 0.60
```

### Announcement (4:00 PM, after close)
```
AAPL announces 12% earnings beat
Stock jumps +2.2% after hours
New price: 153.30

Delta impact on position:
  Short 150 call: now ITM by $3.30
  Short 150 put: now OTM by $3.30
  
Position P&L:
  -$3.30 × 100 shares × 3 spreads = -$990
  Plus collected credit: $1,530
  Current P&L: $540 loss so far
  
Alert: Per-leg delta stops NOT triggered yet
       (need 0.60 delta, call is ~0.90)
       
Decision: Hold for 4-hour backstop, target still reachable
```

### Continued Monitoring (4:05-4:15 PM)
```
4:05 PM: IV crushing hard (down 35%)
         Option premiums collapsing
         Call 150 now worth $1.80 (bought back at $2.55)
         Put 150 now worth $0.20 (bought back at $2.55)
         
Position P&L update:
         Call spread: $0.75 credit remaining
         Put spread: $2.35 credit remaining
         Total: $3.10 collected (vs $5.10 entry)
         
Profit: $1,530 - $310 = $1,220
Target was: $765
Status: PROFIT TARGET EXCEEDED!

Decision: EXIT NOW, lock in $1,220 profit
```

### Exit (4:10 PM)
```
4:10 PM: Buy to close all 3 straddles
         Filled @ $1.95 total ($0.95 call + $1.00 put)
         Cost: 3 × $195 = $585
         
Final P&L:
  Credit received: $1,530
  Cost to close: $585
  Profit: $945 total (3 spreads)
  Per spread: $315
  Return: 61.8% on $510 entry
  Time: 38 minutes
  
Status: TRADE CLOSED, PROFIT LOCKED
```

### Post-Trade Analysis (5:00 PM)
```
Trade review:
  ✓ Entry: Executed smoothly
  ✓ Strategy selection: SHORT_STRADDLE correct
  ✓ Exit: Early exit beat target (IV crush faster than expected)
  ✓ Win/loss: WIN (+$945)
  
Lessons:
  • IV crush was 35% (expected 20-25%)
  • Stock moved more than expected (+2.2% vs 3.2% expected)
  • Both factors combined = fast profit
  • Early exit was correct decision
  
Improvements:
  • Could have let target execute (got extra $ by exiting early)
  • Consider trailing stop in future?
  • Strategy worked exactly as planned
  
Update dispersion calibration:
  • Realized σ = 2.2% / actual move
  • Predicted σ = 1.25%
  • Ratio = 1.76x, so gap premium existed
  • Decision matrix correctly weighted this
```

---

## Typical Daily Summary

### Tier 1 Executions

**Best case:** 3 positions entered, all hit profit target
```
AAPL  SHORT_STRADDLE  +$945  (38 min)
MSFT  SHORT_STRADDLE  +$620  (45 min)
JPM   IRON_FLY       +$180  (62 min)
TOTAL                 +$1,745 (avg 48 min)
Win rate:             3/3 (100%)
```

**Typical case:** 2-3 positions, mixed results
```
AAPL  SHORT_STRADDLE  +$945
JPM   IRON_FLY       -$120  (delta stop)
XOM   IRON_CONDOR    +$80   (backstop exit)
TOTAL                 +$905
Win rate:             2/3 (66%)
```

**Challenging case:** Positions go sideways, backstop exits
```
AAPL  SHORT_STRADDLE  +$250  (backdoor exit, 4hr)
MSFT  SHORT_STRADDLE  -$680  (delta stop, gap)
JPM   IRON_FLY       +$100  (profit target)
TOTAL                 -$330
Win rate:             2/3 (66%)
```

---

## Risk Management During Trading

**Real-time checks:**
```
□ No position > 5% of portfolio?
□ No total exposure > 20%?
□ All stops configured?
□ Exit alerts active?
□ Backup communication plan ready?
□ Broker account status normal?
```

**Mid-trade adjustments:**
```
If adverse move > 25% of max risk:
  □ Evaluate early exit vs holding
  □ Check delta stops are working
  □ Prepare to defend if needed
  
If favorable move > profit target:
  □ Let system execute
  □ Or take early profit
  □ Decision depends on time to IV crush
  
If glitch/error:
  □ Contact broker immediately
  □ Document for review
  □ Prepare manual exit plan
```

---

## Navigation

**← Previous:** [Strategy Fallback System](./07-strategy-fallback.md)  
**Next →** [Risk Management](./09-risk-management.md)
