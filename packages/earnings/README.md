# EarningsAgent

An autonomous options trading agent for overnight earnings plays. It scans the daily earnings
calendar, evaluates seven defined-risk options strategies against live market data, ranks
candidates, and manages entries and exits around a single overnight hold — position opened once
before the close, closed once after the next open, unmonitored overnight.

Every strategy is **defined-risk**: max loss is known at entry. Undefined-risk/naked strategies
(naked straddles, strangles, naked puts/calls) were deliberately excluded — a single-name
earnings gap on a naked short can blow out arbitrarily overnight with nobody watching.

---

## Quick Start

```bash
git clone <your-fork-or-remote-url>
cd EarningsAgent
python -m venv venv && source venv/bin/activate   # venv\Scripts\activate on Windows
pip install -r requirements.txt

cp config/config.example.json config/config.json
python src/tt.py secrets_set                       # store tastytrade OAuth credentials
python src/tt.py get_connection_status              # confirm "connected": true
```

You'll also need a local `dolt sql-server` serving three DoltHub datasets the scanner reads
live earnings-calendar, IV/RV, and realized-move data from — see
[Installation & Setup](./docs/01-setup.md) for the full walkthrough, including that step.

Once connected, try a no-risk read of tonight's candidates:

```bash
python src/scanner.py get_calendar --date MM/DD/YYYY
python src/strategies/iron_fly.py get_candidates --date MM/DD/YYYY
```

Then run the forced-sampling paper-testing program to validate the whole pipeline end-to-end
(scan → tier → size → cost-adjust → persist → close) without touching a real account:

```
/paper-start
```

Full setup details, troubleshooting, and the first-trade walkthrough are in
[docs/01-setup.md](./docs/01-setup.md).

---

## The 7 Strategies

All defined-risk, all evaluated nightly against live tastytrade chains and DoltHub data:

| Strategy | Structure | Best For |
|---|---|---|
| `iron_fly` | Short ATM straddle + long OTM wings | Medium IV, balanced risk/reward |
| `iron_condor` | Short OTM put spread + short OTM call spread | Wide expected range, directional-neutral |
| `directional_credit_spread` | Short OTM put or call spread (side chosen by skew) | Directional bias / IV skew |
| `broken_wing_butterfly` | Asymmetric (skip-strike) butterfly, wings sized to skew | Asymmetric expected moves |
| `reverse_fly` | Long ATM + short OTM wings | Capturing gap premium (long-vol) |
| `atm_calendar` | Short front-month + long back-month, same strike | Low IV, term-structure edge |
| `double_calendar` | ATM calendar on both the call and put side | Low IV, symmetric term structure |

See [docs/05-strategies.md](./docs/05-strategies.md) for full structure, entry conditions, and
exit rules per strategy, and [docs/screening-criteria.md](./docs/screening-criteria.md) for the
shared hard filters and tiering every candidate passes through before a strategy sees it.

---

## How It Works

- **`src/scanner.py`** is the strategy-agnostic engine: earnings calendar, IV/RV ratio, winrate
  backtest, liquidity gates, ranking, expiration selection.
- **`src/strategies/<name>.py`** holds only strategy-specific logic: hard-filter thresholds,
  tiering, strike/order construction. New strategies can be added here without touching the
  shared engine.
- **`src/rank_strategies.py`** evaluates every enabled strategy against every candidate on the
  merged today-AMC/tomorrow-BMO earnings calendar, and picks each symbol's single best strategy.
- **`src/tt.py`** is the tastytrade broker interface — quotes, chains, greeks, account info, and
  (in live mode only) order execution.

Full operational detail — loop steps, config options, database schema — lives in `CLAUDE.md`,
the authoritative spec this system runs against.

---

## Paper vs. Live Mode

Controlled by `enable_live_trading` in `config/config.json` (`false` by default):

- **Paper mode**: persistence via `src/db_paper.py`, order handling stops at building the order
  spec — no order is ever submitted. Paper mode still sources live quotes/chains/greeks from the
  real tastytrade session (needed to size and price orders realistically); it just never trades.
- **Live mode**: persistence via `src/db.py`, and entries submit real orders via
  `tt.py execute_trade --live`.

Two separate paper-testing programs exist, and can run concurrently since they write to
isolated books:

- **`/paper-start`** — forced-sampling strategy validation (`src/strategy_test_runner.py`):
  opens every Tier 1/2 candidate for every strategy, not just each symbol's single best, so every
  strategy accumulates a usable sample size quickly. Writes to `profile='strat_test'`.
- **`/paper-trading-start`** — one-shot production-ranking analysis (`rank_strategies.py`): what
  the real loop would pick tonight, without submitting anything.
- **`/earnings-start`** — the actual continuous trading loop (paper or live per
  `enable_live_trading`), run through a full market session.
- **`/paper-trading-eod-report`** — end-of-day report on today's candidates, decisions, and
  tomorrow's exit plan.

Track accumulated results with `python src/strategy_report.py` (text) or
`python src/strategy_dashboard.py` (self-contained HTML dashboard, written to `reports/`).

---

## Testing

```bash
pytest
```

---

## Documentation

- [docs/README.md](./docs/README.md) — full documentation index
- [docs/01-setup.md](./docs/01-setup.md) — installation and first-run walkthrough
- [docs/03-configuration.md](./docs/03-configuration.md) — every `config.json` parameter
- [docs/05-strategies.md](./docs/05-strategies.md) — strategy-by-strategy structure and rules
- [docs/screening-criteria.md](./docs/screening-criteria.md) — hard filters and tiering (source
  of truth for what gates a candidate)
- `CLAUDE.md` — the authoritative operational spec (loop steps, tool reference, config options,
  database schema)

---

## Disclaimer

This is a research/personal trading tool, not investment advice. Options trading involves
substantial risk of loss. Paper trade extensively before considering live capital, and never
risk more than you can afford to lose.

---

## License

[MIT](./LICENSE)
