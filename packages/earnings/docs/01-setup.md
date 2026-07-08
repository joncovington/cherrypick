# Installation & Setup: Get the Earnings Agent Running

Complete setup guide from fresh checkout to first trade.

---

## Prerequisites

- Python 3.8+
- pip (Python package manager)
- Git (for version control)
- Broker connection (API keys for live trading)
- ~2 GB disk space

---

## Step 1: Clone Repository

```bash
git clone https://github.com/your-org/earnings-agent.git
cd earnings-agent
```

---

## Step 2: Install Dependencies

```bash
# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows

# Install packages
pip install -r requirements.txt
```

**Dependencies:** requests, pandas, numpy, scipy, python-dateutil, python-dotenv

---

## Step 3: Configure Settings

### Create Config File

```bash
cp config.example.json config.json
```

### Edit config.json

**Minimum setup:**
```json
{
  "allow_naked_strategies": false,
  "max_concurrent_earnings_positions": 3,
  
  "entry_condition_gates": {
    "max_realized_move_dispersion_pct": 0.15,
    "min_iv_rank": 0.15,
    "min_credit_dollars": 0.10,
    "min_dte": 30,
    "max_dte": 60
  },
  
  "strategies": {
    "short_straddle": {
      "max_realized_move_dispersion_pct": 0.08,
      "min_iv_rank": 1.20,
      "min_entry_credit_dollars": 3.00
    },
    "iron_fly": {
      "max_realized_move_dispersion_pct": 0.20,
      "min_iv_rank": 0.75,
      "min_entry_credit_dollars": 0.80
    }
  }
}
```

See [Configuration Guide](./03-configuration.md) for all parameters.

---

## Step 4: Set Up Broker Connection

### Create .env File

```bash
touch .env
```

### Add Credentials

```
BROKER_API_KEY=your_api_key_here
BROKER_SECRET=your_secret_here
BROKER_ACCOUNT_ID=your_account_id
LIVE_TRADING=false  # Start with false for testing
```

**Warning:** Never commit .env to git! It's in .gitignore.

---

## Step 5: Validate Installation

### Run Syntax Check

```bash
python -c "import json; json.load(open('config.json')); print('Config OK')"
```

**Expected:** "Config OK" prints

### Test Imports

```bash
python -c "from src.strategies import iron_fly; print('Imports OK')"
```

**Expected:** "Imports OK" prints

### List Available Strategies

```bash
python -c "from src.rank_strategies import STRATEGY_REGISTRY; print(f'Found {len(STRATEGY_REGISTRY)} strategies')"
```

**Expected:** "Found 10 strategies"

---

## Step 6: Run Test Suite (Optional)

```bash
# Run all tests
python -m pytest tests/ -v

# Or specific test
python -m pytest tests/test_short_straddle.py -v
```

**Expected:** Most tests pass (some may fail if DB unavailable)

---

## Step 7: Test with Dry Run

### Scan Earnings (Paper)

```bash
python get_candidates.py --date 2026-07-15 --dry_run
```

**Output:**
```
Scanning earnings for 2026-07-15...
Found 8 candidates
3 Tier 1: AAPL, MSFT, JPM
5 Tier 2-3: XOM, BAC, PG, WMT, KO
Dry run complete (no real orders)
```

---

### Generate Order Spec (Paper)

```bash
python get_order.py --symbol AAPL --strategy AUTO --dry_run
```

**Output:**
```
Symbol: AAPL
Strategy: SHORT_STRADDLE
Entry: Sell 150 call @ 2.55, Sell 150 put @ 2.55
Entry credit: $5.10
Profit target: $2.55 (50%)
Max loss: Unlimited (ultra-predictable stock)
Dry run (no real order submitted)
```

---

## Step 8: Connect to Live Broker (Optional)

### Test Broker Connection

```bash
python -c "from src.broker import get_account_status; status = get_account_status(); print(f'Account: {status}')"
```

**Expected:** Account balance and status print

### Enable Live Trading

Edit .env:
```
LIVE_TRADING=true
```

**Warning:** Set to true ONLY after extensive paper trading!

---

## Step 9: Schedule Daily Scans (Optional)

### Create Cron Job (macOS/Linux)

```bash
# Edit crontab
crontab -e

# Add this line for 7:00 AM daily scan
0 7 * * 1-5 cd /path/to/earnings-agent && python get_candidates.py --date TODAY --output scan_results_$(date +\%Y\%m\%d).json
```

### Windows Task Scheduler

```
1. Open Task Scheduler
2. Create Basic Task
3. Trigger: 7:00 AM on weekdays
4. Action: Run python get_candidates.py --date TODAY
5. Save
```

---

## First Trade Walkthrough

### Day 1 (Morning, 7:00 AM)

```bash
# Scan today's earnings
python get_candidates.py --date YYYY-MM-DD

# Review candidates
# Find 3-5 Tier 1 candidates

# Pick one to test
symbol = "AAPL"
```

### Day 1 (Mid-day, 11:00 AM)

```bash
# Get order spec
python get_order.py --symbol AAPL --strategy AUTO

# Review spec
# - Entry: $5.10 credit
# - Profit target: $2.55
# - Stops: 0.60 delta, 4-hour backstop
```

### Day 1 (Entry, 3:45 PM)

```bash
# MANUALLY submit order through broker platform
# Sell 150 call @ $2.55
# Sell 150 put @ $2.55
# Quantity: 1 spread (small test size)

# Or use CLI if broker integration ready:
python place_order.py --symbol AAPL --quantity 1 --live
```

### Day 1 (Post-exit, After Hours)

```bash
# Log the trade
python log_trade --symbol AAPL --exit_price 2.55 --date YYYY-MM-DD

# Review results
# Profit/loss?
# Time in position?
# Decision matrix correct?
```

---

## Troubleshooting Setup

### "Config validation failed"

```bash
# Check JSON syntax
python -m json.tool config.json > /dev/null
```

**Fix:** Correct JSON syntax (missing comma, quote, brace)

---

### "Broker connection refused"

```bash
# Verify API keys
python -c "import os; print(os.getenv('BROKER_API_KEY'))"
```

**Fix:** Check .env file has correct API key, broker API status

---

### "Module not found error"

```bash
# Verify venv is activated
which python  # Should show venv path

# Reinstall packages
pip install -r requirements.txt
```

**Fix:** Activate virtual environment, reinstall requirements

---

### "No earnings found for date"

```bash
# Check date format
python get_candidates.py --date 2026-07-15  # Correct: YYYY-MM-DD
```

**Fix:** Use correct date format, verify it's a trading day

---

## Configuration Profiles

### Conservative (Start Here)

```bash
cp config.example.json config.json
# Already conservative by default
```

### Moderate

```bash
# Edit config.json
"allow_naked_strategies": true
"max_concurrent_earnings_positions": 4
```

### Aggressive

```bash
# Edit config.json
"allow_naked_strategies": true
"max_daily_earnings_trades": 8
"max_realized_move_dispersion_pct": 0.20
```

---

## Development Setup (Optional)

If contributing code:

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run linter
pylint src/

# Run tests with coverage
pytest --cov=src tests/

# Format code
black src/ tests/
```

---

## Next Steps

1. **Read** [Quick Reference](./02-quick-reference.md) for daily workflows
2. **Study** [Entry Conditions Framework](./04-entry-conditions.md) to understand strategy selection
3. **Review** [Strategy Guide](./05-strategies.md) for detailed strategy rules
4. **Practice** dry runs on 3-5 earnings days before live trading
5. **Start small** with 1-2 spreads per trade for first 10 trades

---

## Support

### Common Questions

- **How long does scan take?** 5-10 minutes (depends on data source)
- **Can I run multiple scans?** Yes, they're independent
- **What if I miss the entry window?** Use next day's earnings
- **Can I trade in paper account first?** Yes, set LIVE_TRADING=false
- **Do I need market data subscription?** Depends on broker API

---

## Navigation

**← Previous:** [README](./README.md)  
**Next →** [Quick Reference](./02-quick-reference.md)
