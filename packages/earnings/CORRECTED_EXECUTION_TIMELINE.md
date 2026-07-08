# Execution Timeline: Corrected (Overnight Holdings)

**Critical Realization:** IV crush doesn't happen in extended hours after evening earnings announcements. Pre-market announcements mean positions are held overnight. Exits must happen the next day at 9:30 AM market open when IV crush materializes.

---

## Timeline Overview

### Pre-Announcement (T+0: Afternoon)

**3:00 PM ET — Analysis Window**
```
System analyzes earnings candidates
Ranks by conviction score (0-100)
Displays top recommendations
```

**3:50 PM ET — Entry Window Opens**
```
You execute selected trades
Entry orders submitted to broker
Positions entered at market or limit prices
```

**4:00 PM ET — Market Close**
```
Stock market closes
Earnings may be announced:
  - Before market open (6:30 AM BMO)
  - After market close (4:05 PM or later)
  - Before/during extended hours
```

**4:05+ PM ET — Announcement (if After-Hours)**
```
If company announces after 4:05 PM:
  Stock moves in extended hours trading
  Your positions show P&L in after-hours
  DO NOT exit yet (thin liquidity in extended hours)
  Hold overnight
```

**4:30 PM - 8:00 PM ET — Post-Announcement Monitoring**
```
Position monitoring (informational only)
IV crush begins (but optionality still available)
DO NOT try to exit profitable positions in extended hours
(Liquidity is bad, slippage will be huge)

Your position shows profit, tempting to exit
BUT: This is NOT the real move yet
Actual profit realization happens at market open
```

---

## Overnight Hold (Critical)

**8:00 PM ET - 9:30 AM ET (Next Day) — Overnight Position**

```
Position held overnight:
  - Stock may gap up or down
  - Extended hours trading (thin volume)
  - No active position management
  - Stops and alerts may not trigger
  
Overnight considerations:
  [ ] Pre-market headlines possible
  [ ] Gap risk at market open
  [ ] Sleep knowing position is at risk
  [ ] Delta stops won't execute if gap
  [ ] You can set alerts for 6:30 AM if pre-market announcement
```

**Why hold overnight?**
- IV crush (20-60% drop) happens at market open (9:30 AM) when professionals trade
- Extended hours trading has bad liquidity
- Real exit opportunity comes at 9:30 AM, not 4:15 PM

---

## Market Open Execution (CRITICAL)

**9:30 AM ET (T+1: Next Day) — Market Opens**

```
IV crush happens NOW (not in extended hours)
Stock trading resumes with normal liquidity
Your position P&L dramatically improves

IMMEDIATE (first 5 minutes):
  9:30-9:35 AM: Market opens
    - Circuit breakers may pause trading
    - Volatility highest in first 30 seconds
    - Bids/asks normalize by 9:35 AM
    
THEN (9:35 AM onward):
  Check position P&L
  Evaluate against profit target
  Decide: exit now or wait?
```

---

## Exit Window (Next Day Morning)

**9:35 AM - 10:30 AM ET — Exit Monitoring**

```
Check each position:

1. PROFIT TARGET HIT?
   (50% of entry credit remaining to collect)
   → YES: CLOSE immediately
   → NO: Continue to step 2

2. DELTA STOP TRIGGERED?
   (Stock moved > entry delta stop threshold)
   → YES: Close affected leg, monitor other leg
   → NO: Continue to step 3

3. APPROACHING BACKSTOP?
   (4 hours = 10:30 AM for after-close announcement)
   → YES: Close entire position
   → NO: Hold for target or backstop
```

**Example: AAPL Short Straddle**

```
Entry:        3:32 PM (before close)
Entry credit: $5.10 (3 spreads × $510)
Profit target: $2.55 (50% of credit)

Post-announcement (after 4:05 PM):
  Stock up +2.2%
  Position shows loss (delta stops not triggered yet)
  Extended hours: Bid-ask 50 cents wide
  You wait...

Market open (9:30 AM):
  IV crushes 40%
  Stock still up +2.2%
  150 call: now worth $1.80 (was $2.55)
  150 put: now worth $0.20 (was $2.55)
  Remaining credit: $3.10
  
  Profit target was: $2.55
  Current position: $3.10 collected (TARGET EXCEEDED!)
  
Close position at 9:35 AM
  Buy to close: $1.95 total
  Profit: $1,530 - $585 = $945
  Time: 38 minutes (entry to exit)
  Return: 61.8% on entry
```

---

## Four-Hour Backstop (Fallback)

**10:30 AM ET — Hard Exit (Backstop)**

```
If position still open at 10:30 AM:
  FORCE CLOSE regardless of profit/loss
  (4 hours from 6:30 AM announcement time)
  
Why?
  - IV crush window has closed
  - No more edge in the position
  - Holding longer is just risk
  - Theta now works against short positions
```

**Alternative Backstop: After-Market Close**

```
If announcement was after market close (4:05 PM):
  Backstop = 8:00 PM same day (4 hours from announcement)
  But position is held in extended hours
  → More realistic backstop is next day 10:30 AM
```

---

## Complete Timeline Example

### Day 1 (Earnings Day)

```
7:00 AM   - Morning research (optional)
3:00 PM   - System analysis & ranking
3:50 PM   - Entry execution (SHORT_STRADDLE on AAPL)
           Entry credit: $5.10 (3 spreads)
4:00 PM   - Market close, set overnight alerts
4:05 PM   - AAPL announces 12% earnings beat
4:10 PM   - Stock up +2.2% in extended hours
4:15 PM   - Position shows loss (delta stops not triggered)
           Temptation to exit (resist it!)
8:00 PM   - Last chance to think about position
           Tomorrow's plan: Monitor at 9:30 AM
```

### Day 2 (Next Trading Day)

```
6:30 AM   - Alert: Any pre-market news? (set your phone)
8:45 AM   - Futures opening slightly up
9:25 AM   - 5 minutes before market open, prepare
9:30 AM   - **MARKET OPENS**
            IV crush happens NOW
            Position P&L dramatically improves
9:35 AM   - Bid-ask normalized
            Check position: Profit $3.10 collected
            Profit target was $2.55
            **TARGET EXCEEDED**
9:37 AM   - Execute: Buy to close all 3 straddles
            Filled at $1.95 total
9:38 AM   - **TRADE CLOSED**
            Profit: $945 (61.8% return, 38 min hold)
10:00 AM  - Position completely out
           Already back to normal trading
```

---

## Key Timing Points

| Time | Event | Action |
|------|-------|--------|
| 3:50 PM (T+0) | Entry window | Execute trades |
| 4:05-8:00 PM (T+0) | Announcement to eve | Monitor, hold overnight |
| 8:00 PM (T+0) | End of market day | Review overnight plan |
| 6:30 AM (T+1) | Pre-market open | Check alerts, news |
| 9:30 AM (T+1) | Market open | **IV crush happens** |
| 9:35 AM (T+1) | Exits available | Monitor profit targets |
| 10:30 AM (T+1) | 4-hour backstop | Force close if needed |
| 4:00 PM (T+1) | After close | Log results, EOD report |

---

## Why This Matters

### The Old (Wrong) Thinking
```
"Entry at 3:50 PM, exit in extended hours when IV crushes"
Problem: IV doesn't crush in extended hours
         Extended hours has bad liquidity
         Fills are terrible
         You exit at bad prices
```

### The Correct Reality
```
"Entry at 3:50 PM, HOLD OVERNIGHT, exit at 9:30 AM market open"
Why it works: IV crush happens when market opens
              Normal liquidity returns
              You get real prices
              Positions become highly profitable
```

---

## Delta Stops During Overnight

**Important:** Delta stops may NOT execute overnight

```
Reason: Your broker doesn't monitor overnight
        Extended hours have thin liquidity
        Stop orders may not trigger
        
Solution: Accept overnight gap risk
          Set alerts for 6:30 AM pre-market news
          Have manual override plan
          Don't enter if you can't handle gap
```

**Per-Leg Delta Stops (Reference)**
```
Naked Strategies (Straddle, Strangle):
  Delta stop: 0.60 per leg
  (Stock moved far enough to trigger loss limit)
  
Spreads (Iron Fly, Condor):
  Delta stop: 0.45 per leg
  (Tighter stop for defined-risk positions)
  
Calendar Spreads:
  No delta stops (multiple expirations)
  Time-based exit instead
```

---

## Exit Decision Flowchart

```
At 9:35 AM (market open + 5 min):

Is position profitable?
  YES → Proceed to profit target check
  NO → Did delta stop trigger?
        YES → Close leg, monitor other
        NO → Continue holding for target or backstop

Has profit target been reached?
  (50% of entry credit collected)
  YES → CLOSE IMMEDIATELY
  NO → Are we within 30 min of backstop (10:30 AM)?
        YES → Close to avoid 4-hour backstop loss
        NO → Continue holding for target
```

---

## Capital Requirement Overnight

```
Your margin requirement stays the same overnight:
  Naked Straddle (5 spreads, $5.10 credit): ~$2,500 margin
  Iron Fly (5 spreads, $0.90 credit): ~$1,000 margin
  
Your broker holds capital from 3:50 PM through 4:00 PM next day
(Don't withdraw capital during overnight holds)
```

---

## Summary

**The Golden Rule:**
```
Entry: 3:50 PM (day of announcement)
Hold: Overnight (even though tempting to exit in extended hours)
Exit: 9:30-10:30 AM next day (when IV crush materializes)
```

**Why it works:**
```
- IV crush is real and predictable
- It happens at market open, not in extended hours
- Extended hours liquidity is terrible
- Your profit target is easy to hit at market open
- 50% exit on 15-40 minute hold is realistic
```

---

## Related Documentation

- `LATE_DAY_WORKFLOW.md` — Daily execution workflow
- `docs/10-exits.md` — Detailed exit strategy guide
- `docs/08-trading-workflow.md` — Trading workflow with timeline
- `SLASH_COMMANDS_GUIDE.md` — How to use the automation
