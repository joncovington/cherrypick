# cherrypick Earnings — documentation

Guides for the cherrypick **Earnings** engine — automated, defined-risk earnings option trades built on
a multi-strategy decision framework. Paper by default; its live path (`enable_live_trading`) is
experimental, off by default and at your own risk.

> **Part of the [cherrypick](../../../README.md) suite.** This is the `cherrypick-earnings` module
> (`packages/earnings`). It runs standalone from this folder for live / interactive trading, and is driven
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
- Executes pre-earnings positions and manages exits

### 7 Strategies (all defined-risk)

Profit targets and stops are the shipped `config.example.json` values (`strategies.<name>`).

| Strategy | Profit target | Stop | Best For |
|----------|---|---|---|
| Iron Fly | 25% of credit | 1.5× credit | Medium IV |
| Iron Condor | 50% of credit | 1.5× credit | Wide range |
| Directional Spread | 50% of credit | 2.0× credit | IV skew |
| Broken Wing Butterfly | 25% of credit | 2.0× credit | Asymmetric IV |
| ATM Calendar | 15% of debit | 50% of debit | Low IV |
| Double Calendar | 15% of debit | 50% of debit (plus a 0.45-delta leg stop) | Overpriced moves |
| Expected-Move Butterfly | fly worth 50% of its width | none (the debit is the max loss) | Measuring a directional fly's fills (paper only) |

The two calendars stay registered but open nothing in the forced-sampling books from 2026-10-15
(`strat_test_harness.STRAT_TEST_RETIRED`).

### Key Metrics

- **Overnight Play**: enter once before the close (entry window default 15:30–15:55 ET) and hold
  through the earnings reaction — no same-day exit.
- **IV-Crush Capture**: the whole edge is the IV collapse that happens once the earnings
  uncertainty resolves overnight.
- **Profit Target / Stop**: per strategy (table above), checked from the first morning after entry.
- **Holding Period**: in the agent-driven loop (`/earnings-start`), an unconditional close-window
  backstop the next morning (default `09:45` ET) closes whatever is still open regardless of P&L.
  The unattended paper loop manages positions instead (since 2026-08-12): a loser closes on the
  first morning, a winner short of target may be carried up to three trading sessions
  (`management.hold_winners_max_days`), nothing acts on the entry session but the expiry close and
  the pin guard, nothing is held through a leg's expiration (the 15:30 expiry close), and an expired
  position is settled at intrinsic.
- **Entry Gate**: IV/RV ratio, term structure, and liquidity — see
  [Screening Criteria](./screening-criteria.md) for the full hard-filter list.

---

## Documentation Index

The numbers are stable identifiers, not a sequence — 07, 09, 12, and 13 are retired documents and the
remaining files keep the numbers they were written with, so existing links stay good. Gaps are expected.

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
- [Paper Trading](./paper-trading.md) — How paper mode works, data separation from live
- [Strat-Test Portfolios](./strat-test-portfolios.md) — Per-strategy paper books for the forced-sampling test
- [Strategy Testing Plan](./strategy-testing-plan.md) — Forced-sampling validation program
- [Control Book Plan](./control-book-plan.md) — Widening the control so entry screening becomes
  measurable by read-side replay, since the advisor's twin cannot express it. Phases 0–1 and the
  entry replay (phase 3) are built; phases 2 and 4 are open
- [Time Spread Screen Plan](./time-spread-screen-plan.md) — An endurable-gap screen and a combined
  side read for every earnings time spread. The engine is built (`time_spread.py`) and nothing reads it
  yet; step 0 ran 2026-10-17, and the rest waits for backtest data
- [Expected-Move Fly Plan](./expected-move-fly-plan.md) — The history study (verdict: don't build)
  behind the paper-only `expected_move_butterfly` book

### Reference
- [Glossary](./14-glossary.md) — Terms and definitions
- [Strategy Optimization Research](./strategy-optimization.md) — Hypotheses queued for paper-test validation
- [File Size Exceptions](./file-size-exceptions.md) — Documented exceptions to the 500-line guideline
- [Operating History](./operating-history.md) — Dated incidents behind the rules in `CLAUDE.md`
- [Earnings screener plan](../../../docs/earnings-screener-plan.md) — The navigator-rule history study
  (no edge after costs) and the earnings calendar. Both live in `packages/technicals` and on the
  console's Screeners page, not in this module

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
  Backstop: unconditional close-window exit next morning (agent loop)

Calendar Strategies (ATM Calendar, Double Calendar):
  Profit Target: 15% of entry debit
  Stop Loss: 50% of entry debit
  Backstop: unconditional close-window exit next morning (agent loop)

Expected-Move Butterfly (paper only):
  Profit Target: fly worth 50% of its width
  Stop Loss: none (the debit is the max loss)
  Held to the 15:30 expiry close otherwise

Every strategy (paper loop): held on its entry session; closed from
15:30 on any leg's expiration day.
```

In the agent-driven loop every strategy closes by the next morning's close window regardless of
P&L. The unattended paper loop carries a winner up to three sessions instead (see Key Metrics
above). See `CLAUDE.md`'s Loop Steps and [Exit Strategy Guide](./10-exits.md) for the exact
mechanics.

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
    │   ├── db.py                # Persistence, live ledger (db_paper.py is the paper twin)
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

### Next Morning (agent-driven loop)
```
Step 3c (market open -> close_window_start): profit-target/stop-loss check against live quotes
Step 3 (close_window_start, unconditional): whatever's still open closes regardless of P&L
```

The unattended paper loop instead acts from 09:40 ET on the management rules above.

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
