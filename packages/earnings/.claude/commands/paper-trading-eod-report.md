# /paper-trading-eod-report

Generates end-of-day analysis report for paper trading review.

## Description

Creates comprehensive EOD report showing:
1. Today's analysis summary (candidates, selected, rejected)
2. Entry decisions (what was traded)
3. Next-day exit targets (when to monitor)
4. Weekly performance metrics (if data available)
5. Key metrics and observations
6. Tomorrow's action items
7. Configuration recommendations
8. Risk tolerance assessment

Report is saved to file and displayed for review.

## Usage

```
/paper-trading-eod-report
/paper-trading-eod-report --date 2026-07-15
/paper-trading-eod-report --week
/paper-trading-eod-report --detailed
/paper-trading-eod-report --format json
```

## Options

- `--date YYYY-MM-DD` — Report for specific date (default: today)
- `--week` — Include weekly performance summary (past 7 days)
- `--detailed` — Show all candidates (not just selected)
- `--format [text|json|html]` — Output format (default: text)
- `--save` — Save to file (default: true)

## Output

Text report showing:
```
================================================================================
PAPER TRADING EOD REPORT: 2026-07-08
================================================================================

ANALYSIS SUMMARY
  Total candidates: 8
  Passed entry gates: 7
  Selected for trading: 3
  Rejected/waitlisted: 4

SELECTED TRADES (Ranked by Score)
  1. BMO    - SHORT_STRADDLE (Score: 84.0)
     Entry: Sell 97 call + put @ $3.18
     Target: $1.59 (50% profit)
     Stop: 0.60 delta per leg
     Backstop: 10:30 AM next day
     
  2. AMC    - IRON_CONDOR (Score: 77.0)
     Entry: Sell 11/16 call + 5 put spread @ $0.80
     Target: $0.40 (50% profit)
     Stop: 0.45 delta per side
     Backstop: 8:00 PM same day

NEXT-DAY EXIT MONITORING
  9:30 AM ET: Market opens, IV crush complete
  
  BMO: Monitor for profit target ($1.59 credit remaining)
       If called away: Defend with delta stops (0.60)
       Backstop: 10:30 AM (4 hours from 6:30 AM announcement)

KEY METRICS
  Average score: 78.7/100
  Tier 1 count: 1 (high conviction)
  Tier 2 count: 2 (medium conviction)
  Strategy breakdown:
    - Naked short: 1 (SHORT_STRADDLE)
    - Defined risk: 2 (IRON_CONDOR, IRON_FLY)

OBSERVATIONS
  [OK] Good dispersion/IV mix for today
  [OK] Conviction scores balanced
  [OK] Capital requirement manageable
  [--] One overnight hold (BMO pre-market)

TOMORROW ACTION ITEMS
  [ ] 6:30 AM: BMO earnings announced (pre-market)
  [ ] 9:30 AM: Market open, begin exit monitoring
  [ ] 9:30-10:30 AM: Monitor profit targets
  [ ] 10:30 AM: Force exit any remaining positions (backstop)
  [ ] EOD: Log exits and P&L

RECOMMENDATIONS FOR NEXT WEEK
  CONFIG: Very high-quality candidates today (avg 85+)
          Opportunity: Consider increasing position size

RISK TOLERANCE ASSESSMENT
  RISK: One overnight hold - manageable gap exposure
        Set alerts for 6:30 AM announcement
        Tolerance: Consider --config conservative if gap risk concerns you
  
  RISK: Medium capital utilization (50-80%)
        Balanced: Reasonable risk buffer available

  RISK: Defined-risk spreads dominate portfolio
        Managed: Max loss known and limited
        Tolerance: Good for risk-averse traders

SUGGESTED RISK ADJUSTMENTS
  If uncomfortable with current risk:
    1. Reduce: max_positions_per_day (e.g., 3 -> 2)
    2. Lower: available_capital (triggers smaller position sizes)
    3. Tighten: min_conviction to 'high' (Tier 1 only, score 80+)
    4. Avoid: naked strategies (--allow_naked_strategies false)
  
  If confident with current risk:
    1. Increase: max_positions_per_day to 4-5
    2. Raise: available_capital for larger positions
    3. Loosen: min_conviction to 'medium' (Tier 1-2 allowed)
    4. Embrace: naked strategies for max edge (SHORT_STRADDLE)

================================================================================
[SAVED] paper_trading_logs/daily_report_2026_07_08.txt
```

## Report Sections

### 1. Analysis Summary
- How many candidates analyzed
- How many passed entry gates
- How many selected vs rejected
- Why rejections (capital, conviction, etc)

### 2. Selected Trades
- Ranked by score (highest first)
- Entry details (strikes, credit)
- Exit targets (50% profit)
- Stop levels (per-leg delta)
- Backstop timing (4-hour rule)

### 3. Next-Day Monitoring
- Market open time (9:30 AM)
- IV crush expectations
- Per-position monitoring plan
- Exit decision flowchart
- Backstop deadlines

### 4. Key Metrics
- Average score
- Tier distribution (Tier 1 vs 2 vs 3)
- Strategy breakdown
- Risk profile summary

### 5. Observations
- What looks good today
- What looks risky
- Capital efficiency
- Overnight hold notes

### 6. Tomorrow Action Items
- Checklist for next day
- Time-specific actions (6:30 AM, 9:30 AM, etc)
- Exit monitoring schedule
- Reporting deadline

### 7. Configuration Recommendations
- Should you adjust conviction threshold?
- Should you increase/decrease positions?
- Should you change risk profile?

### 8. Risk Tolerance Assessment
- Overnight gap risk analysis
- Capital utilization check
- Strategy risk mix evaluation
- Suggested adjustments for less/more risk

## Examples

### Simple EOD report
```
/paper-trading-eod-report
```
Displays today's summary and exit monitoring plan.

### Weekly performance review
```
/paper-trading-eod-report --week
```
Includes:
- Week's trade statistics
- Win rate
- Avg P&L per trade
- Best/worst trades
- Strategy performance breakdown

**Output:**
```
WEEKLY PERFORMANCE: Past 7 Days
  Analysis runs: 5
  Candidates analyzed: 42
  Trades entered: 14
  Trades closed: 14
  Total P&L: +$325
  Avg P&L/trade: +$23
  Win rate: 64% (9 wins, 5 losses)
  
  Best strategy: IRON_FLY (67% win rate)
  Highest score: 89.0 (BMO, won)
  Lowest score: 71.0 (KO, lost)
  
  [OK] Strong performance (60%+ win rate)
  [OK] Ready to consider live trading
```

### Detailed report with all candidates
```
/paper-trading-eod-report --detailed
```
Shows rejected candidates too, for learning why they didn't qualify.

### Save as JSON for analysis
```
/paper-trading-eod-report --format json
```
Machine-readable format for further analysis in Python or Excel.

## When to Use

**4:00 PM ET daily** (after market close, after entry decisions)

Review before bed to plan next day's exits.

**Every Friday at 4:00 PM** — Run with `--week` to track trends.

## Output Files

Creates in `paper_trading_logs/`:
- `daily_report_2026_07_08.txt` — Today's report
- `weekly_report_2026_07.txt` — Weekly summary (if --week)

All reports are text files you can review anytime.

## Tips

- **Run at 4:00 PM ET** (after market close)
- **Review before bed** to plan next day
- **Check exit targets** so you know what to monitor next day
- **Track patterns** across weeks (which scores predict wins?)
- **Weekly review** helps you decide when to adjust scoring

## Integration with /paper-trading-start

### Workflow Example

**3:00 PM ET:**
```
/paper-trading-start
```
Analyze candidates, pick top 3 to trade.

**3:50 PM ET:**
Execute selected trades in broker.

**4:00 PM ET:**
```
/paper-trading-eod-report
```
Review tomorrow's exit plan.

**Next day 9:30 AM:**
Monitor exits per yesterday's report.

**Next day 4:00 PM:**
Log results, run report again.

## Success Tracking

Track over time:
- Win rate (target: 50-70%)
- Avg P&L per trade (should be positive)
- Score correlation (high scores → wins?)
- Recommendation accuracy (did config changes help?)

## Related Commands

- `/paper-trading-start` — Begin daily analysis and entry
- `/earnings-start` — Legacy continuous automation

## Automation

Can be scheduled to run automatically at 4:00 PM ET:
```bash
# Windows Task Scheduler
At 4:00 PM ET: python src/generate_eod_report.py

# Linux/Mac Crontab
0 16 * * 1-5 cd /path/to/earnings-agent && python src/generate_eod_report.py
```

Report will be ready for review by 4:15 PM ET.

## See Also

- `SLASH_COMMANDS_GUIDE.md` — Complete usage guide
- `PAPER_TRADING_SYSTEM_COMPLETE.md` — System overview
- `docs/08-trading-workflow.md` — Daily workflow details
- `docs/10-exits.md` — Exit timing explanation
