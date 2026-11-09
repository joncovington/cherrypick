# cherrypick Earnings — documentation

Guides for the cherrypick **Earnings** engine — automated, defined-risk earnings option trades built on
a multi-strategy decision framework. Paper-only since v0.12.0; it places no order.

> **Part of the [cherrypick](../../../README.md) suite.** This is the `cherrypick-earnings` module
> (`packages/earnings`). It runs standalone from this folder for by-hand paper work, and is driven
> unattended by the [orchestrator](../../orchestrator) for paper collection (by subprocess, never by
> import). Shared logic (`cherrypick.core`) lives in `packages/core`, a sibling in-repo package. See the
> [package README](../README.md#how-this-fits-the-suite) for how the two roles fit together, and the
> suite-wide [documentation index](../../../docs/README.md) for the big picture.

---

## Quick Start

### What Is It?

The Earnings Agent is a rules-based options trading system that:
- Scans daily earnings calendar for candidates
- Analyses 7 defined-risk strategies using entry condition framework
- Routes each candidate to optimal strategy based on market data
- Opens paper positions before the report and manages their exits

### 7 Strategies (all defined-risk)

Structure, entry gates and exit rules for each are in the one strategy table,
[Strategy Guide](./05-strategies.md); the shipped profit targets and stops are in
[Exit Strategy Guide](./10-exits.md) and `config.example.json` (`strategies.<name>`). The two calendars
and `broken_wing_butterfly` stay registered but open nothing in the forced-sampling books
(`strat_test_harness.STRAT_TEST_RETIRED`).

### Key Metrics

- **Overnight Play**: enter once before the close (entry window default 15:30–15:55 ET) and hold
  through the earnings reaction — no same-day exit.
- **IV-Crush Capture**: the whole edge is the IV collapse that happens once the earnings
  uncertainty resolves overnight.
- **Profit Target / Stop**: per strategy (table above), checked from the first morning after entry.
- **Holding Period**: the paper loop manages positions: a loser closes on the first morning, a winner
  short of target may be carried up to three trading sessions (`management.hold_winners_max_days`),
  nothing acts on the entry session but the expiry close and the pin guard, nothing is held through a
  leg's expiration (the 15:20 expiry close), and an expired position is settled at intrinsic. A manual
  `/paper-start` cycle instead closes whatever is still open at the next morning's close window
  (default `09:45` ET).
- **Entry Gate**: IV/RV ratio, term structure, and liquidity — see
  [Screening Criteria](./screening-criteria.md) for the full hard-filter list.

---

## Documentation Index

The numbers are stable identifiers, not a sequence, so gaps are expected.

### Getting Started
- [Installation & Setup](./01-setup.md) — Configure, run tests, connect to the broker and Dolt
- [Quick Reference](./02-quick-reference.md) — CLI commands, common workflows
- [Configuration Guide](./03-configuration.md) — All config parameters explained

### Learning the Framework
- [Entry Conditions Framework](./04-entry-conditions.md) — Decision matrix, routing logic
- [Strategy Guide](./05-strategies.md) — Deep dive on each strategy
- [Earnings Scan Analysis](./06-scan-analysis.md) — How to analyse daily candidates
- [Screening Criteria](./screening-criteria.md) — Hard filters and the accept/reject screen (source of truth)

### Operations
- [Trading Workflow](./08-trading-workflow.md) — Day-to-day execution
- [Exit Strategy Guide](./10-exits.md) — Profit targets, backstops, repairs
- [Examples & Case Studies](./11-examples.md) — Real-world scenarios
- [Paper Trading](./paper-trading.md) — How paper works, data separation from the pre-v0.12.0 ledger
- [Strat-Test Portfolios](./strat-test-portfolios.md) — Per-strategy paper books for the forced-sampling test
- [Strategy Testing](./strategy-testing-plan.md) — The forced-sampling paper program
- [Time Spread Screen](./time-spread-screen-plan.md) — An endurable-gap screen and a combined
  side read for every earnings time spread. The engine is built (`time_spread.py`) and nothing reads it
- [Expected-Move Fly](./expected-move-fly.md) — The history study (verdict: don't build)
  behind the paper-only `expected_move_butterfly` book

### Reference
- [Glossary](./14-glossary.md) — Terms and definitions
- [Strategy Optimization](./strategy-optimization.md) — Hypotheses queued for paper-test validation
- [Measurement Boundaries](./measurement-boundaries.md) — The dates from which results must not be pooled

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
Broken Wing Butterfly):
  Profit Target: 25-50% of entry credit (per strategy)
  Stop Loss: 1.5-2.0x entry credit (per strategy)

Calendar Strategies (ATM Calendar, Double Calendar):
  Profit Target: 15% of entry debit
  Stop Loss: 50% of entry debit

Expected-Move Butterfly (paper only):
  Profit Target: fly worth 50% of its width
  Stop Loss: none (the debit is the max loss)
  Held to the 15:20 expiry close otherwise

Every strategy (paper loop): held on its entry session; closed from
15:20 on any leg's expiration day.
```

The paper loop carries a winner up to three sessions (see Key Metrics above); a manual `/paper-start`
cycle closes everything at the next morning's close window. See the
[Exit Strategy Guide](./10-exits.md) for the exact mechanics.

### Risk Framework

```
Every strategy is defined-risk -- max loss known at entry.
Iron Fly:    Defined risk, most ATM premium, lower capital
Iron Condor: Defined risk, wider profit zone
Calendar:    Defined risk, term structure edge, time decay
```

---

## Project Structure

This package lives at `packages/earnings/` inside the [cherrypick](../../../README.md) monorepo:

```
cherrypick/
├── packages/core/                # Shared cherrypick.core library (in-repo package: calendar, fees)
└── packages/earnings/           # ← this package (cherrypick-earnings)
    ├── src/cherrypick/earnings/  # the cherrypick.earnings namespace package
    │   ├── strategies/          # 7 defined-risk strategy modules
    │   │   ├── iron_fly.py
    │   │   ├── iron_condor.py
    │   │   ├── directional_credit_spread.py
    │   │   ├── broken_wing_butterfly.py
    │   │   ├── atm_calendar.py
    │   │   ├── double_calendar.py
    │   │   └── expected_move_butterfly.py
    │   ├── scanner.py           # Strategy-agnostic scanning engine
    │   ├── rank_strategies.py   # Multi-strategy ranking
    │   ├── sizing.py            # Code-enforced risk-cap sizing
    │   ├── costs.py             # Cost model over cherrypick.core.fees
    │   ├── tt.py                # tastytrade broker interface
    │   ├── db.py                # Persistence, the pre-v0.12.0 ledger (db_paper.py is the paper one)
    │   ├── paths.py             # Resolves the data home (~/.cherrypick/data/earnings)
    │   ├── paper_loop.py        # The managed paper loop the supervisor ticks every 60 s
    │   ├── management.py        # Exit decisions for open paper positions (hold, carry, close)
    │   ├── settlement.py        # Settles expired positions at intrinsic
    │   ├── strat_test_harness.py  # Forced-sampling paper-testing program (driven by paper_loop)
    │   ├── strategy_report.py   # Per-strategy metrics, as text (the console draws the charts)
    │   └── ...
    ├── config/
    │   └── config.example.json  # Shipped config; what earnings runs from until
    │                            # ~/.cherrypick/config/earnings.json exists
    ├── tests/                   # Unit tests
    ├── docs/                    # This documentation
    ├── CLAUDE.md                # Authoritative operational spec
    └── README.md                # Project overview
```

---

## Typical Workflow

### Afternoon, Before the Close
```bash
python -m cherrypick.earnings.rank_strategies get_ranked_symbols --date MM/DD/YYYY
# Evaluates all 7 strategies against tonight's/tomorrow's calendar, picks each symbol's best
```

### Entry Window (default 15:30-15:55 ET)
```bash
python -m cherrypick.earnings.strategies.iron_fly get_order --symbol AAPL --earnings_date 2026-07-15 --earnings_timing "After market close"
# Returns a concrete order spec, priced off the live chain
```

### Overnight
Position holds through the earnings reaction — no same-day exit.

### Next Morning
The paper loop acts from 09:40 ET on the management rules above. A manual `/paper-start` cycle instead
checks profit-target/stop-loss until `close_window_start`, then closes whatever is still open
regardless of P&L.

See [Trading Workflow](./08-trading-workflow.md) for the full day-by-day walkthrough.

---

## Key Files to Read

1. **[Configuration Guide](./03-configuration.md)** — Understand the config
2. **[Entry Conditions Framework](./04-entry-conditions.md)** — Learn the routing logic
3. **[Strategy Guide](./05-strategies.md)** — Deep dive on each strategy
4. **[Earnings Scan Analysis](./06-scan-analysis.md)** — How to evaluate candidates

---

## Statistics

- **Total Strategies**: 7 (all defined-risk; one paper-only, two retired from the forced-sampling books)
- **Tests**: unit tests under `tests/` (`pytest`)
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
