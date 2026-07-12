# Earnings Agent: Complete Documentation

Automated overnight earnings options trading system using multi-strategy decision matrix framework.

---

## Quick Start

### What Is It?

The Earnings Agent is a rules-based options trading system that:
- Scans daily earnings calendar for candidates
- Analyzes 7 defined-risk strategies using entry condition framework
- Routes each candidate to optimal strategy based on market data
- Executes pre-earnings positions and manages exits

### 7 Strategies (all defined-risk)

| Strategy | Entry Credit | Risk | Best For |
|----------|---|---|---|
| Reverse Fly | $1.50-3.00 | Defined | Gap premium |
| Iron Fly | $0.80-1.50 | Defined | Medium IV |
| Iron Condor | $0.50-1.50 | Defined | Wide range |
| Directional Spread | $0.50-1.50 | Defined | IV skew |
| Broken Wing Butterfly | $0.20-0.60 | Defined | Asymmetric IV |
| ATM Calendar | $0.20-0.50 | Defined | Low IV |
| Double Calendar | $0.50-1.50 | Defined | Overpriced moves |

### Key Metrics

- **Overnight Play**: enter once before the close, hold unmonitored through the earnings
  reaction, close once after the next open — no same-day exit.
- **IV-Crush Capture**: the whole edge is the IV collapse that happens once the earnings
  uncertainty resolves overnight.
- **Profit Target**: 50% of max credit (calendars: 25-30% of debit), checked the first morning
  after entry (Step 3c).
- **Holding Period**: unconditional close-window backstop the next morning (default `09:45` ET)
  — whatever's still open closes regardless of P&L.
- **Entry Gate**: IV/RV ratio, term structure, and liquidity — see
  [Screening Criteria](./screening-criteria.md) for the full hard-filter list.

---

## Documentation Index

### Getting Started
- [Installation & Setup](./01-setup.md) — Configure, run tests, connect to the broker and Dolt
- [Quick Reference](./02-quick-reference.md) — CLI commands, common workflows
- [Configuration Guide](./03-configuration.md) — All `config.json` parameters explained

### Learning the Framework
- [Entry Conditions Framework](./04-entry-conditions.md) — Decision matrix, routing logic
- [Strategy Guide](./05-strategies.md) — Deep dive on each strategy
- [Earnings Scan Analysis](./06-scan-analysis.md) — How to analyze daily candidates
- [Screening Criteria](./screening-criteria.md) — Hard filters and tiering (source of truth)

### Operations
- [Trading Workflow](./08-trading-workflow.md) — Day-to-day execution
- [Exit Strategy Guide](./10-exits.md) — Profit targets, backstops, repairs
- [Examples & Case Studies](./11-examples.md) — Real-world scenarios
- [Paper Trading](./paper-trading.md) — How paper mode works, data separation from live
- [Paper Trading Profiles](./paper-trading-profiles.md) — Conservative/balanced/aggressive sizing
- [Strategy Testing Plan](./strategy-testing-plan.md) — Forced-sampling validation program

### Reference
- [Glossary](./14-glossary.md) — Terms and definitions
- [Strategy Optimization Research](./strategy-optimization.md) — Hypotheses queued for paper-test validation
- [File Size Exceptions](./file-size-exceptions.md) — Documented exceptions to the 500-line guideline

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
Credit Strategies (Iron Fly, Iron Condor, Directional Spread,
Broken Wing Butterfly, Reverse Fly):
  Profit Target: 50% of entry credit
  Stop Loss: 1.5x entry credit
  Backstop: unconditional close-window exit next morning

Calendar Strategies (ATM Calendar, Double Calendar):
  Profit Target: 25% of entry debit
  Backstop: unconditional close-window exit next morning
```

Every strategy closes by the next morning's close window regardless of P&L — nothing is held
past the overnight IV-crush event. See `CLAUDE.md`'s Loop Steps for the exact mechanics.

### Risk Framework

```
Every strategy is defined-risk -- max loss known at entry.
Iron Fly:    Defined risk, most ATM premium, lower capital
Iron Condor: Defined risk, wider profit zone
Reverse Fly: Defined risk, gap premium, long-vol hedge structure
Calendar:    Defined risk, term structure edge, time decay
```

---

## Project Structure

```
EarningsAgent/
├── src/
│   ├── strategies/        # 7 defined-risk strategy modules
│   │   ├── reverse_fly.py
│   │   ├── iron_fly.py
│   │   ├── iron_condor.py
│   │   ├── directional_credit_spread.py
│   │   ├── broken_wing_butterfly.py
│   │   ├── atm_calendar.py
│   │   └── double_calendar.py
│   ├── scanner.py           # Strategy-agnostic scanning engine
│   ├── rank_strategies.py   # Multi-strategy ranking
│   ├── sizing.py            # Code-enforced risk-cap sizing
│   ├── tt.py                # tastytrade broker interface
│   ├── db.py / db_paper.py  # Persistence (live / paper, separate SQLite files)
│   ├── strategy_test_runner.py  # Forced-sampling paper-testing program
│   ├── strategy_report.py / strategy_dashboard.py  # Per-strategy metrics & charts
│   └── ...
├── config/
│   ├── config.example.json  # Template — copy to config.json
│   └── config.json          # Your actual settings (gitignored)
├── data/                    # SQLite trade databases (earnings_trades.db, paper_trades.db)
├── tests/                   # Unit tests
├── docs/                    # This documentation
├── CLAUDE.md                # Authoritative operational spec
└── README.md                # Project overview
```

---

## Typical Workflow

### Afternoon, Before the Close
```bash
python src/rank_strategies.py get_ranked_symbols --date MM/DD/YYYY
# Evaluates all 7 strategies against tonight's/tomorrow's calendar, picks each symbol's best
```

### Entry Window (default 15:30-15:55 ET)
```bash
python src/strategies/iron_fly.py get_order --symbol AAPL --earnings_date 2026-07-15 --earnings_timing "After market close"
# Returns a concrete order spec, priced off the live chain
```

### Overnight
Position holds unmonitored through the earnings reaction — no intraday management, no same-day
exit.

### Next Morning
```
Step 3c (market open -> close_window_start): profit-target/stop-loss check against live quotes
Step 3 (close_window_start, unconditional): whatever's still open closes regardless of P&L
```

See [Trading Workflow](./08-trading-workflow.md) for the full day-by-day walkthrough.

---

## Key Files to Read

1. **[Configuration Guide](./03-configuration.md)** — Understand config.json
2. **[Entry Conditions Framework](./04-entry-conditions.md)** — Learn the routing logic
3. **[Strategy Guide](./05-strategies.md)** — Deep dive on each strategy
4. **[Earnings Scan Analysis](./06-scan-analysis.md)** — How to evaluate candidates

---

## Statistics

- **Total Strategies**: 7 (all defined-risk)
- **Test Coverage**: 224 unit tests (`pytest`)
- **Market Coverage**: Any US-listed options with earnings and a real tastytrade option chain

---

## Questions?

- **How do I get started?** → Read [Installation & Setup](./01-setup.md)
- **How does strategy selection work?** → Read [Entry Conditions Framework](./04-entry-conditions.md)
- **What's the workflow?** → Read [Trading Workflow](./08-trading-workflow.md)
- **Which strategy for X scenario?** → Read [Examples & Case Studies](./11-examples.md)
- **Something's not working** → Read the Troubleshooting section at the bottom of
  [Installation & Setup](./01-setup.md#troubleshooting)

---

## Navigation

**← Previous:** [Project README](../README.md)  
**Next →** [Installation & Setup](./01-setup.md)
