# /paper-trading-start

Launches daily earnings analysis at 3 PM ET and ranks candidates by conviction score.

## Description

Analyzes all earnings candidates for the day, scores each one (0-100 conviction), ranks by opportunity, and displays top 3-5 recommendations. Logs entry decisions for paper trading.

Supports both manual selection (you choose which to trade) and auto-submit mode (system logs top N automatically).

## Usage

```
/paper-trading-start
/paper-trading-start --mode auto --count 2
/paper-trading-start --config conservative
/paper-trading-start --date 2026-07-10
```

## Options

- `--mode [manual|auto]` — Manual: you select which trades (default). Auto: system logs top N.
- `--count N` — Number of candidates to trade (default: 3)
- `--config [conservative|moderate|aggressive]` — Risk profile (default: moderate)
  - `conservative`: Tier 1 only (score 80+)
  - `moderate`: Tier 1-2 allowed (score 70+)
  - `aggressive`: Tier 1-3 allowed (score 60+)
- `--date YYYY-MM-DD` — Analyze specific date (default: today)

## Output

Displays:
```
[3:00 PM ET] Paper Trading Analysis

Candidates analyzed: 8
Selected for trading: 3
Average score: 80.5/100

Top Candidates:
  1. BMO    84.0  SHORT_STRADDLE   [Entry at 3:50 PM]
  2. AMC    77.0  IRON_CONDOR      [Entry at 3:50 PM]
  3. JPM    75.0  IRON_FLY         [Entry at 3:50 PM]

Entry orders logged to: paper_trading_logs/runs_2026_07.json
Ready for 3:50 PM entry window!
```

For each candidate shows:
- Symbol
- Conviction score (0-100)
- Recommended strategy
- Entry status

## When to Use

**3:00 PM ET on earnings days** — Daily analysis before entry window opens

## Manual Mode Workflow

1. Run command → see top 5 candidates with scores and strategies
2. Review the recommendations
3. Decide which ones to trade (usually top 1-3)
4. Execute selected trades in your broker at 3:50 PM ET
5. Run `/paper-trading-eod-report` at 4:00 PM ET

## Auto Mode Workflow

1. Run command with `--mode auto --count 3`
2. System automatically logs top 3 candidates
3. You can still review what was selected
4. Execute trades in broker at 3:50 PM ET
5. Run `/paper-trading-eod-report` at 4:00 PM ET

## Example: Conservative Profile

```
/paper-trading-start --config conservative
```

Only shows Tier 1 candidates (score 80+). Strict entry gates. Fewest trades but highest confidence.

## Example: Aggressive Profile

```
/paper-trading-start --config aggressive
```

Tier 1-3 allowed (score 60+). More trades but lower confidence. Use only if capital/experience sufficient.

## Files Generated

- `paper_trading_logs/runs_2026_07.json` — Entry log (appended daily)
- `paper_trading_logs/earnings_decisions.json` — Raw analysis

## Tips

- **First time:** Use `--mode manual` to learn how system thinks
- **After 10 trades:** Switch to `--mode auto --count 2-3` for hands-off operation
- **Test date:** Use `--date 2026-07-01` to analyze historical day

## Related Commands

- `/paper-trading-eod-report` — Generate end-of-day review (4:00 PM ET)
- `/run-today` — Legacy continuous automation command

## Automation

Can be scheduled to run automatically at 3:00 PM ET:
```bash
# Windows Task Scheduler
At 3:00 PM ET: python src/late_day_earnings_ranked.py

# Linux/Mac Crontab
0 15 * * 1-5 cd /path/to/earnings-agent && python src/late_day_earnings_ranked.py
```

## Common Issues

**"No candidates found"**
- Check market is open (US trading hours)
- Verify earnings calendar loaded
- Check configuration (capital, positions available)

**"Wrong number of candidates"**
- Adjust `--config` for different conviction thresholds
- Modify `max_positions_per_day` in config.json

**"Score seems off"**
- Review `RANKING_CONFIG.md` for scoring weights
- Check individual strategy entry conditions in docs/05-strategies.md

## See Also

- `SLASH_COMMANDS_GUIDE.md` — Complete usage guide
- `docs/04-entry-conditions.md` — How scoring works
- `docs/05-strategies.md` — Each strategy explained
