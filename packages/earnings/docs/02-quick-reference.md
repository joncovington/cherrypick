# Quick Reference: Common Commands & Workflows

---

## Daily Commands

### Morning Scan (7:00 AM)

```bash
python get_candidates.py --date YYYY-MM-DD
```

**Output:** List of candidates with strategies, sorted by tier.

---

### Entry Preparation (11:00 AM)

```bash
python get_order.py --symbol AAPL --strategy AUTO --earnings_date YYYY-MM-DD
```

**Output:** Concrete order spec (strikes, credit, profit target, stops).

---

### Execute Entry (3:30 PM)

Submit order in your broker platform using spec from above.

---

### Log Results (After hours)

```bash
python log_trade --date YYYY-MM-DD --save results.json
```

**Output:** JSON with P&L, fill prices, execution times, decision matrix validation.

---

## Entry Conditions Reference

### When to Use Each Strategy

```
High IV + Ultra-predictable (σ < 0.08):  SHORT_STRADDLE
Medium IV + Normal (σ 0.08-0.20):        IRON_FLY
Low IV + Stable (σ < 0.10):              ATM_CALENDAR
Gap premium detected:                    REVERSE_FLY
Directional bias:                        JADE_LIZARD or DIRECTIONAL_SPREAD
Wide range expected:                     IRON_CONDOR
```

---

## Exit Triggers (In Order)

1. **4-Hour Backstop** (safety, checked first)
   - Exit 4 hours after earnings announcement
   - Ensures capture of IV crush, prevents overnight hold

2. **Profit Target** (primary)
   - Exit at 50% of entry credit
   - Automatic trigger, usually first to hit

3. **Per-Leg Delta Stops** (protection)
   - Exit if any leg hits delta threshold (0.60 naked, 0.45 spreads)
   - Protects against one-sided blowout

---

## Position Sizing Quick Calc

```
Max account risk per trade: $1,000
Iron Fly entry credit: $80 per spread
Profit target: 50% = $40
Position size: $1,000 / $40 = 25 spreads max

OR:

Max account risk per day: $2,500
Expected day: 3 trades
Risk per trade: $2,500 / 3 = $833
Max spreads: See calc above
```

---

## Configuration Tweaks

### Allow Naked Strategies

```json
{
  "allow_naked_strategies": true  // Enable SHORT_STRADDLE, SHORT_STRANGLE
}
```

### Set Fallback Profile

Edit `config.json`:
```json
{
  "fallback_profile": "aggressive"  // or "conservative", "moderate"
}
```

### Adjust Profit Target

```json
{
  "profit_target_pct": 0.50  // 50% of entry credit (default)
}
```

### Loosen Entry Gates

```json
{
  "max_realized_move_dispersion_pct": 0.20,  // vs 0.15 default
  "min_iv_rank": 0.10  // vs 0.15 default
}
```

---

## Strategy Comparison Matrix

| Strategy | Entry | Risk | Time | Complexity |
|----------|-------|------|------|------------|
| SHORT_STRADDLE | $3-5 | Unlimited | 30-60 min | Low |
| REVERSE_FLY | $1.50-3 | Defined | 30-60 min | Medium |
| IRON_FLY | $0.80-1.50 | Defined | 30-60 min | Medium |
| IRON_CONDOR | $0.50-1 | Defined | 30-60 min | High |
| SHORT_STRANGLE | $0.50-1.20 | Unlimited | 30-60 min | Low |
| JADE_LIZARD | $1-2 | Partial | 30-60 min | High |
| DIRECTIONAL | $0.50-1.50 | Defined | 30-60 min | Medium |
| BROKEN_WING | $0.20-0.60 | Defined | 30-60 min | High |
| ATM_CALENDAR | -$0.30 (debit) | Defined | 5-30 days | Medium |
| DOUBLE_CALENDAR | -$0.60 (debit) | Defined | 5-30 days | Medium |

---

## Metrics Reference

### Dispersion (σ) Buckets

```
< 2%:     Ultra-predictable (blue chips, staples)
2-5%:     Normal (most stocks)
5-10%:    Volatile (tech, biotech)
> 10%:    Too unpredictable (rejected)
```

### IV Rank Zones

```
< 0.50:   Very thin (calendar spreads only)
0.50-0.75: Light (IRON_FLY, IRON_CONDOR)
0.75-1.00: Medium (most strategies OK)
> 1.00:   Rich (SHORT_STRADDLE, STRANGLE viable)
```

### Expected Move as % of Stock Price

```
Low:    1-2% (boring stock, low IV)
Normal: 2-4% (typical earnings)
High:   4-6% (volatile stock, high IV)
Huge:   > 6% (rare, gap expected)
```

---

## Troubleshooting Quick Guide

### "No candidates found"
- Check earnings calendar date correct?
- Minimum 2-3 earnings per day expected

### "Too many rejections"
- Loosen `max_realized_move_dispersion_pct` (0.15 → 0.20)
- Lower `min_iv_rank` (0.15 → 0.10)

### "Strategy selection unexpected"
- Verify decision matrix logic (check code)
- Validate dispersion calculation
- Check IV Rank actually updated

### "Profit target not hit"
- Stock moved beyond profit zone?
- IV crush happened slower than expected?
- Consider early exit vs waiting for target

### "Naked strategy selected but portfolio says no"
- Check `allow_naked_strategies: true/false`
- Verify fallback system is enabled
- Check strategy selected in decision matrix

### "Order rejected by broker"
- Liquidity issue? Check bid-ask
- Wrong strike or quantity?
- Account permissions OK?
- Sufficient buying power?

---

## Testing & Validation

### Run 10-Day Framework Test

```bash
python run_strategy_selection_test.py --days 10 --output results.csv
```

**Expected:** ~80% IRON_FLY, ~15% SHORT_STRADDLE, ~5% calendar spreads (typical distribution).

---

### Test Single Candidate

```bash
python get_candidate.py --symbol AAPL --earnings_date 2026-07-15 --verbose
```

**Output:** Full analysis with all metrics and decision tree.

---

### Backtest Strategy

```bash
python backtest.py --strategy SHORT_STRADDLE --start 2025-01-01 --end 2026-06-30 --output backtest_results.json
```

**Output:** Win rate, avg profit/loss, max loss, etc.

---

## Risk Checklist

Before entering position:

```
□ Capital available for max loss?
□ Position sizing < 5% of account?
□ Total earnings exposure < 20%?
□ All stops configured?
□ Exit alerts active?
□ Backup plan if fills partial?
□ Broker connection stable?
□ Order margins correct?
□ Commission factored in?
```

---

## Post-Trade Checklist

After exiting position:

```
□ P&L logged?
□ Decision matrix validated?
□ Metrics recalculated for actual outcome?
□ Dispersion threshold adjusted if needed?
□ Notes on what worked / didn't work?
□ Lessons learned documented?
```

---

## Typical Daily Earnings Calendar

**US Market:**
- Morning (6:00-8:30 AM): Pre-market announcements
- Mid-day (10:00 AM-2:00 PM): During-market announcements
- Afternoon (3:55-4:05 PM): Pre-close announcements
- After-market (4:05 PM+): Post-close announcements

**Entry Windows:**
- Pre-market (6:00 AM): Enter before open (T-1 if after close)
- After close (3:30-3:55 PM): Last chance to enter before announcement

---

## Example: Complete Day

```
7:00 AM:  Scan earnings calendar
          Found 8 candidates, 3 Tier 1 (AAPL, MSFT, JPM)

9:00 AM:  Verify Tier 1 candidates
          All metrics confirmed
          
11:00 AM: Generate order specs
          Review with risk team
          
1:00 PM:  Final check
          Candidate metrics still valid
          IV Rank updated
          
3:30 PM:  Enter AAPL SHORT_STRADDLE
          Filled 5 spreads @ $5.10 credit
          
3:35 PM:  Enter MSFT SHORT_STRADDLE
          Filled 3 spreads @ $4.80 credit
          
3:40 PM:  Enter JPM IRON_FLY
          Filled 8 spreads @ $0.90 credit
          
4:00 PM:  Earnings announced
          All stocks gap
          Monitor exits
          
4:10 PM:  AAPL hits profit target
          Exit 5 spreads for $2.60 profit = $1,300
          
4:15 PM:  MSFT hits profit target
          Exit 3 spreads for $2.40 profit = $720
          
4:30 PM:  JPM still open
          P&L +$400
          Approaching 4-hour backstop
          
8:00 PM:  4-hour backstop triggered
          Exit JPM position for +$280
          
Total profit: $2,300 (3 positions, 0 losses)
Win rate: 3/3 (100%)
Avg time: 45 minutes
ROI: $2,300 / $15,000 at-risk = 15.3%
```

---

## Common Mistakes to Avoid

```
✗ Entering after 3:55 PM (too late, no time for exit)
✗ Position too large (can't manage max loss)
✗ Not setting stops before announcement (reactive vs proactive)
✗ Holding through backstop hoping for more (greedy, ignores plan)
✗ Ignoring dispersion gate (unpredictable move risk)
✗ Low IV + naked strategy (insufficient premium for risk)
✗ Partial fill but same position size (now overleveraged)
✗ Different strategy than recommended (override decision matrix)
✗ Not logging results (can't improve if don't measure)
```

---

## Navigation

**← Previous:** [README](./README.md)  
**Next →** [Configuration Guide](./03-configuration.md)
