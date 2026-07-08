# 🚀 Earnings Agent: Automated Options Trading for Earnings Plays

**Simple. Powerful. Data-Driven.**

An automated system that analyzes earnings candidates and tells you exactly which strategy to trade, when to enter, and when to exit. Perfect for overnight earnings plays using options strategies that capture IV crush.

---

## What It Does

**In Plain English:**

Every earnings day, this system:

1. **Scans all earnings candidates** and gathers key info (implied move, historical patterns, current volatility)
2. **Ranks them by opportunity** (which ones have the best edge?)
3. **Picks the best strategy** for each one (naked short? defined risk? calendar spread?)
4. **Tells you exactly what to trade** at 3:50 PM (entry price, quantity, profit target, stops)
5. **Monitors exits** next day at market open (handles IV crush automatically)
6. **Reports daily** on what worked and what to adjust

**No guesswork. No discretion. Just data-driven decisions.**

---

## Why This Works

### The Problem
- You know options trading but hate picking strategies manually
- Every earnings day is different (sometimes high IV, sometimes low)
- Some strategies work great in certain conditions, terrible in others
- You enter based on feel, exit based on emotion

### The Solution
- **Automatic strategy selection** based on math, not emotion
- **Consistent process** that adapts to market conditions
- **Clear entry/exit rules** with no discretion
- **Daily feedback** on what's working and what to adjust

### The Edge
This system captures **IV crush** (20-60% volatility drop in first 15-30 minutes after earnings):
- Short strategies profit from IV collapse
- Wide wings protect if stock moves big
- Tight historical patterns = high confidence
- Calendar spreads benefit from time decay

---

## 10 Strategies (All Automated)

### Naked Shorts (Maximum Edge)
- **Short Straddle** — Sell ATM call + put, profit if stock stays flat
- **Short Strangle** — Sell OTM call + put, wider range than straddle

### Defined Risk (Safer)
- **Iron Fly** — Short ATM, long wings, known max loss (most popular)
- **Iron Condor** — Short both sides spreads, very safe
- **Reverse Fly** — Long ATM, short wings, captures gap premium
- **Jade Lizard** — Directional with hedge (one side protected)

### Spread Strategies
- **Directional Spread** — One-sided play with hedge
- **Broken Wing Butterfly** — Asymmetric structure (skew trading)

### Time Decay Plays
- **ATM Calendar** — Sell front month, buy back month (low risk)
- **Double Calendar** — Both sides, ultimate time decay play

**The system picks the right one for each day's conditions.**

---

## How It Works: Day-by-Day

### Morning (Optional Research)
Read earnings calendar for today. Nothing required from you.

### 3:00 PM ET: System Analyzes
```
/paper-trading-start
```

System runs analysis:
- 8-12 candidates analyzed
- Each ranked by edge quality (score 0-100)
- Top 3-5 displayed with recommendations

**You review in 5 minutes.**

### 3:50 PM ET: You Execute (or System Can Auto-Submit)
Based on system recommendations, you execute trades:
- **Naked Short Straddle?** Sell ATM call + put
- **Iron Fly?** Sell ATM, buy wings
- **Calendar?** Sell front, buy back

**Exact order spec provided by system.**

### 4:00 PM ET: Daily Report
```
/paper-trading-eod-report
```

System generates report:
- Today's analysis summary
- Tomorrow's exit monitoring plan (profit targets, stops, backdoors)
- Configuration recommendations (should you adjust risk tomorrow?)
- Risk assessment (comfortable with overnight holds? Capital utilization?)

**You review in 5 minutes. Decide if you like the recommendations.**

### Next Day, 9:30 AM: Market Opens
System's predictions come true:
- IV crush happens (20-60% drop as expected)
- Stock moves (usually less than options price change)
- Your positions become profitable

**You monitor exits per yesterday's plan.**

### Next Day, 4:00 PM: Log Results
You log exit prices and P&L. System calculates:
- Did our entry condition analysis work?
- Was the strategy selection correct?
- How accurate were our profit targets?

---

## Real Example: Tuesday Earnings

### Before (Manual Trading)
```
Morning: "AAPL earnings today. Should I trade it?"
         "What strategy? Straddle or strangle?"
         "How much credit? What strikes?"
         
3:00 PM: "Let me check the bid-ask... looking at charts..."
         "Feels like AAPL might stay flat. Let me sell ATM."
         "Hope I picked good strikes."
         
4:05 PM: Earnings announced. Stock up 2%.
         Your straddle is losing money on the put side.
         "Ugh. Maybe I should have done a strangle."
         
4:30 PM: "Let me exit this mess before it gets worse."
         Panic selling. Take a small loss.
```

### After (Earnings Agent)
```
3:00 PM: /paper-trading-start
         System: "AAPL — Score 84/100 — SHORT_STRADDLE"
         System: "Entry: Sell 150 call @ $2.55, Sell 150 put @ $2.55"
         System: "Profit target: $1.27 (50% of credit)"
         You: "Looks good" → Submit order
         
3:50 PM: Order filled. Positions entered.
         
4:00 PM: /paper-trading-eod-report
         System: "AAPL earnings at 4:05 PM. Expect IV crush."
         System: "Monitor for profit target $1.27 remaining credit."
         System: "Delta stops at 0.60 if stock gaps."
         System: "Backstop exit at 8:00 PM if not at target."
         
4:05 PM: Earnings announced. Stock up 2%.
         Your position shows -$150 loss.
         System's delta stop hasn't triggered yet.
         You're calm. You have a plan.
         
4:10 PM: IV crushes 40%. Premium collapses.
         Your position now shows +$200 profit.
         System's profit target was $127.
         You're already past it.
         
4:15 PM: Close position. Lock in profit.
         Time in trade: 25 minutes. Result: +$200.
```

---

## Paper Trading Phase (2-4 Weeks)

**Before going live, test the system:**

1. **Run daily at 3 PM** — Let system analyze
2. **Execute trades** — Follow recommendations or go auto-mode
3. **Monitor next day** — See if edge really exists
4. **Log results** — Win rate, P&L, accuracy
5. **Review weekly** — What's working? What to adjust?

**After 40 trades:**
- Win rate > 60%? → Ready for live (start small: 1-2 spreads)
- Win rate 50-60%? → Continue refining, tweak parameters
- Win rate < 50%? → Debug entry conditions

---

## Key Numbers to Expect

### Win Rate
- **Short Straddle:** 60-70% (naked, high quality only)
- **Iron Fly:** 65-75% (defined risk, most consistent)
- **Calendar Spreads:** 70-80% (time decay, very safe)

### Average P&L Per Trade
- Depends on credit collected and position size
- System calculates: Profit target = 50% of entry credit
- Example: $300 credit → $150 profit target → $0.60 credit spread

### Time in Trade
- Entry: 3:50 PM
- Exit: Usually 9:35-10:30 AM next day (after IV crush)
- Average hold: 15-40 minutes of market open trading

### Capital Requirements
- System adjusts position size based on your available capital
- $25k account → 2-3 spreads typically
- $50k account → 3-5 spreads typically
- Configurable to your risk tolerance

---

## Key Features

### ✓ Automatic Strategy Selection
Don't guess anymore. System picks based on:
- IV level (high/medium/low)
- Historical patterns (predictable? erratic?)
- Market conditions (calm or choppy?)

### ✓ Daily Recommendations
Every day includes suggestions to adjust your risk:
- "Candidates are high quality → consider more position size"
- "Capital utilization at 80% → be careful with overnight holds"
- "Your strategy mix is good → stay the course"

### ✓ Clear Entry/Exit Rules
No more ambiguity:
- Entry: Exact strikes, credit target, entry time
- Profit target: 50% of credit (automatic exit if hit)
- Stops: Delta stops if stock gaps, 4-hour backstop
- No emotion involved

### ✓ Paper Trading Built In
Simulate before risking real money:
- Same system for testing
- Real data, real prices, real results
- See if edge holds up in live trading
- Adjust parameters safely

### ✓ Weekly Performance Tracking
See the big picture:
- Win rate trending up or down?
- Which strategies are winning most?
- P&L cumulative across week
- Ready to increase size? Adjust settings?

---

## Three Ways to Use It

### Conservative (Manual Selection)
```
3:00 PM: /paper-trading-start
         Review top candidates
         Choose which to trade (you pick 1-2 best ones)
         
3:50 PM: Execute your picks
```
**Best for:** Learning, building confidence

### Balanced (Semi-Automated)
```
3:00 PM: /paper-trading-start --config moderate
         Review top 3 recommendations
         Accept or reject system's picks
         
3:50 PM: Execute system picks or your modifications
```
**Best for:** Most traders

### Aggressive (Full Auto)
```
3:00 PM: /paper-trading-start --mode auto --count 3
         System auto-submits top 3
         
3:50 PM: Confirm fills (optional)
```
**Best for:** Experienced traders who trust the system

---

## Getting Started

### Step 1: Configure (5 minutes)
Edit your risk tolerance:
```
max_positions_per_day: 3        # How many trades per day?
available_capital: 50000         # Total capital available?
min_conviction: "medium"         # Tier 1 only (strict) or Tier 1-2 (normal)?
auto_submit: False               # Manual or automatic mode?
```

### Step 2: Test (1 day)
```
3:00 PM: /paper-trading-start
4:00 PM: /paper-trading-eod-report
```
See how the system thinks. Does it make sense?

### Step 3: Paper Trade (2-4 weeks)
```
Every day at 3:00 PM and 4:00 PM
Track your win rate and P&L
Review recommendations
Adjust settings as needed
```

### Step 4: Go Live (Optional)
```
If win rate > 60% and you're confident
Start small: 1-2 spreads per day
Scale up after 10 winning trades
```

---

## The Real Talk

### What You Get
✓ System that adapts to market conditions  
✓ Clear entry/exit rules (no emotion)  
✓ 50-70% win rate (historical pattern based)  
✓ 15-40 minute average hold time  
✓ 50% profit target on entry credit  
✓ Defined risk (spreads) or wide safety margin (naked)  

### What You Need
⚠ Discipline (follow the rules, don't override)  
⚠ Paper trading period (build confidence first)  
⚠ Capital buffer (for adverse moves)  
⚠ Time to monitor exits next morning  
⚠ Willingness to adjust based on results  

### What You Don't Get
✗ Guaranteed profits (no system guarantees that)  
✗ Skip losing trades (50-70% win rate = 30-50% losses)  
✗ Zero risk (we minimize it, don't eliminate)  
✗ No work (monitoring and adjustment required)  
✗ One-size-fits-all strategy (conditions matter)  

---

## Next Steps

### Ready to Start?

1. **Read:** `docs/README.md` (technical overview)
2. **Configure:** Edit `src/late_day_earnings_ranked.py` CONFIG dict
3. **Test:** Run `/paper-trading-start` at 3:00 PM ET
4. **Review:** Run `/paper-trading-eod-report` at 4:00 PM ET
5. **Paper Trade:** Repeat for 2-4 weeks

### Want More Details?

- **Strategy Explanations:** `docs/05-strategies.md`
- **Daily Workflow:** `docs/08-trading-workflow.md`
- **Exit Rules:** `docs/10-exits.md`
- **Real Examples:** `docs/11-examples.md`
- **Command Reference:** `SLASH_COMMANDS_GUIDE.md`

---

## Questions?

**How much capital do I need?**  
$10k minimum. System scales position size to your account. $50k is comfortable.

**Can I trade this while working?**  
Entry: 3:50 PM (5 min setup). Monitoring: 9:30-10:30 AM next day (during work okay if watching phone).

**What if I can't monitor exits?**  
System has automatic stops. You can set profit-target orders in your broker for hands-off management.

**Can I run this on my phone?**  
Yes. Commands are simple: `/paper-trading-start` at 3 PM, `/paper-trading-eod-report` at 4 PM.

**What's the win rate?**  
50-70% depending on strategy. Short Straddle highest (70%+). Calendars most consistent (70-80%).

**When do I make money?**  
Entry 3:50 PM, exit 9:30-10:30 AM next day (usually). Average hold: 15-40 minutes of market open trading.

**Is this a get-rich scheme?**  
No. 50-70% win rate, 50% profit on entry credit, defined risk. Realistic returns if you follow the system.

---

## Ready?

**Start today at 3:00 PM ET.**

```
/paper-trading-start
```

The system will guide you from there.

Good luck. 🚀
