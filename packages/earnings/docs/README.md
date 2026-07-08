# Earnings Agent: Complete Documentation

Automated overnight earnings options trading system using multi-strategy decision matrix framework.

---

## Quick Start

### What Is It?

The Earnings Agent is a rules-based options trading system that:
- Scans daily earnings calendar for candidates
- Analyzes 10 strategies using entry condition framework
- Routes each candidate to optimal strategy based on market data
- Executes pre-earnings positions and manages exits

### 10 Strategies

| Strategy | Entry Credit | Risk | Best For |
|----------|---|---|---|
| Short Straddle | $3.00-5.00 | Unlimited | Predictable, high IV |
| Reverse Fly | $1.50-3.00 | Defined | Gap premium |
| Iron Fly | $0.80-1.50 | Defined | Medium IV |
| Iron Condor | $0.50-1.50 | Defined | Wide range |
| Short Strangle | $0.50-1.20 | Unlimited | OTM, low IV |
| Jade Lizard | $1.00-2.00 | Partial | Directional |
| Directional Spread | $0.50-1.50 | Defined | IV skew |
| Broken Wing Butterfly | $0.20-0.60 | Defined | Asymmetric IV |
| ATM Calendar | $0.20-0.50 | Defined | Low IV |
| Double Calendar | $0.50-1.50 | Defined | Overpriced moves |

### Key Metrics

- **Overnight Play**: Enter day-of or day-before earnings
- **IV-Crush Exit**: Capture 20-60% IV drop in first 15-30 minutes
- **Profit Target**: 50% of max credit (calendars: 25%)
- **Holding Period**: 4-hour backstop post-announcement
- **Entry Gate**: Realized move dispersion < 0.15-0.30 (predictability)

---

## Documentation Index

### Getting Started
- [Installation & Setup](./01-setup.md) — Configure, run tests, connect to scanner
- [Quick Reference](./02-quick-reference.md) — CLI commands, common workflows
- [Configuration Guide](./03-configuration.md) — All config.json parameters explained

### Learning the Framework
- [Entry Conditions Framework](./04-entry-conditions.md) — Decision matrix, routing logic
- [Strategy Guide](./05-strategies.md) — Deep dive on each strategy
- [Earnings Scan Analysis](./06-scan-analysis.md) — How to analyze daily candidates
- [Strategy Fallback System](./07-strategy-fallback.md) — Risk constraint handling

### Operations
- [Trading Workflow](./08-trading-workflow.md) — Day-to-day execution
- [Risk Management](./09-risk-management.md) — Position sizing, stops, exposure
- [Exit Strategy Guide](./10-exits.md) — Profit targets, backstops, repairs
- [Examples & Case Studies](./11-examples.md) — Real-world scenarios

### Advanced
- [Testing & Validation](./12-testing.md) — Running tests, 10-day framework sweep
- [Troubleshooting](./13-troubleshooting.md) — Common issues and solutions
- [Glossary](./14-glossary.md) — Terms and definitions

---

## Key Concepts at a Glance

### Entry Condition Matrix

Routes candidates to optimal strategy based on:

```
PRIMARY:   Realized move vs Expected move (gap premium detection)
SECONDARY: Realized move dispersion (predictability)
TERTIARY:  IV rank (premium availability)
GATE:      Capital requirements
```

### Profit Exit Logic

```
Naked Strategies (Straddle, Strangle):
  Profit Target: 50% of entry credit
  Stop Loss: 2x entry credit
  Backstop: 4 hours post-announcement

Spread Strategies (Iron Fly, Condor, etc):
  Profit Target: 50% of entry credit
  Stop Loss: 1.5x entry credit
  Backstop: 4 hours post-announcement

Calendar Strategies (ATM, Double):
  Profit Target: 25% of entry debit
  Time Exit: 5 days before expiration
  Backstop: N/A (manage through expiration)
```

### Risk Framework

```
Short Straddle: Undefined risk, high edge, capital intensive
Iron Fly: Defined risk, medium edge, lower capital
Reverse Fly: Defined risk, gap premium, hedge structure
Calendar: Defined risk, term structure edge, time decay
```

---

## Project Structure

```
ruflo_projects/
├── src/
│   ├── strategies/        # 10 strategy modules
│   │   ├── short_straddle.py
│   │   ├── reverse_fly.py
│   │   ├── iron_fly.py
│   │   ├── iron_condor.py
│   │   ├── short_strangle.py
│   │   ├── jade_lizard.py
│   │   ├── directional_credit_spread.py
│   │   ├── broken_wing_butterfly.py
│   │   ├── atm_calendar.py
│   │   └── double_calendar.py
│   ├── scanner.py         # Core scanning engine
│   ├── rank_strategies.py # Multi-strategy ranking
│   ├── strategy_fallback.py # Risk constraint fallback
│   └── ...
├── config.json            # All parameters, per-strategy
├── tests/                 # Unit tests (174 tests)
├── docs/                  # This documentation
└── README.md              # Project overview
```

---

## Typical Workflow

### Morning (7:00 AM ET)
```bash
get_candidates --date MM/DD/YYYY
# Returns: Tier 1-3 candidates with recommended strategies
```

### Entry Window (3:30-3:55 PM ET)
```bash
get_order --symbol AAPL --earnings_date YYYY-MM-DD --earnings_timing "After market close"
# Returns: Concrete order spec ready for submission
```

### Post-Announcement (After close)
```bash
# System manages exits:
# - 50% profit target (auto-exits if hit)
# - 4-hour backstop (forces exit after IV crush window)
# - Per-leg delta stops (protects against moves)
```

---

## Key Files to Read

1. **[Configuration Guide](./03-configuration.md)** — Understand config.json
2. **[Entry Conditions Framework](./04-entry-conditions.md)** — Learn the routing logic
3. **[Strategy Guide](./05-strategies.md)** — Deep dive on each strategy
4. **[Earnings Scan Analysis](./06-scan-analysis.md)** — How to evaluate candidates

---

## Statistics

- **Total Strategies**: 10
- **Entry Condition Thresholds**: 45+ parameters configurable
- **Test Coverage**: 174 unit tests
- **Documentation**: 15 guides covering all aspects
- **Market Coverage**: Any US-listed options with earnings

---

## Questions?

- **How do I get started?** → Read [Installation & Setup](./01-setup.md)
- **How does strategy selection work?** → Read [Entry Conditions Framework](./04-entry-conditions.md)
- **What's the workflow?** → Read [Trading Workflow](./08-trading-workflow.md)
- **Which strategy for X scenario?** → Read [Examples & Case Studies](./11-examples.md)
- **How do I fix a failing test?** → Read [Troubleshooting](./13-troubleshooting.md)

---

## Navigation

**← Previous:** [Project README](../README.md)  
**Next →** [Installation & Setup](./01-setup.md)
