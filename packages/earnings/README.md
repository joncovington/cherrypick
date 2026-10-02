# cherrypick-earnings

> **⚠️ Experimental, educational software — not financial advice.** cherrypick is a prototype for
> learning about and researching options strategies. Its live-trading paths place **real,
> irreversible orders** at your own risk; options trading involves substantial risk of loss and is
> not suitable for all investors. Paper results are simulated and do not represent actual trading.
> Provided "as is", without warranty. This module has a live-trading path (`enable_live_trading`, off by default)
> that places real orders through your broker account. **Read [DISCLAIMER.md](../../DISCLAIMER.md) before use.**

**What this module does:** earnings trades defined-risk options strategies around company
earnings announcements — opening a position the afternoon before a report and managing it after
the announcement. It picks from six different structures each night based on which one fits that
stock's setup best, and it can run as **paper trading** (simulated) or **live** trading, gated
behind a setting you turn on yourself. Live trading is experimental, off by default and at your own
risk. It's one strategy module in the
cherrypick suite, alongside the 0DTE iron-condor module, the butterfly module, and the GEX
engine. Most of what you do here is run terminal commands, or ask Claude to run a
`/`-prefixed command for you — no coding required.

> **The earnings module of the [cherrypick](../../README.md) suite.** cherrypick is a monorepo of trading
> modules driven by a shared **orchestrator**. This package (`packages/earnings`) is the overnight
> earnings-play engine; its siblings are [`packages/meic`](../meic) (0DTE iron condors),
> [`packages/gex`](../gex) (the gamma-exposure engine, shown on the console's GEX page), and
> [`packages/orchestrator`](../orchestrator) (the orchestrator). It can run standalone from this folder for
> live / interactive trading, or unattended for paper collection — where the orchestrator's supervisor
> drives it by subprocess (`cherrypick install`), never by import. See [How this fits the suite](#how-this-fits-the-suite) below, this module's own
> [docs/](docs/README.md), and the suite-wide [documentation index](../../docs/README.md).

An autonomous options trading agent for overnight earnings plays. It scans the daily earnings
calendar, evaluates six defined-risk options strategies against live market data, ranks
candidates, and manages entries and exits around the announcement. The agent-driven loop opens
before the close and closes in the next morning's close window (the calendars can run longer). The
unattended paper loop manages positions instead: a loser closes on the first morning, a winner short
of its target can be carried up to three trading sessions, and anything that expires is settled at
intrinsic.

Every strategy is **defined-risk**: max loss is known at entry. Undefined-risk/naked strategies
(naked straddles, strangles, naked puts/calls) were deliberately excluded — a single-name
earnings gap on a naked short can blow out arbitrarily overnight with nobody watching.

Shared logic (market calendar, fee schedule) comes from the **`cherrypick.core`** library, a sibling
package (`packages/core`) in this same monorepo. The suite's installer at the repo root (`install.cmd` on
Windows, `install.sh` on macOS/Linux) installs it along with this package; installing by hand, install
it (`pip install -e ../core`) before this package, or `import cherrypick.core...` can't resolve.

> **This module needs the `dolt` capability.** Its earnings calendar, IV/RV history and realized-move
> data come from three free DoltHub datasets (`earnings`, `options`, `stocks`), cloned under
> `~/.cherrypick/data/earnings` and served by a local `dolt sql-server`. The root installer offers to
> install Dolt and clone them, then records `capabilities.dolt` in `~/.cherrypick/config.json`. Without
> it the orchestrator leaves earnings off even when `modules.earnings.enabled` is true.

---

## Quick Start

```bash
# Clone the cherrypick monorepo
git clone https://github.com/joncovington/cherrypick.git
cd cherrypick/packages/earnings

python -m venv venv && source venv/bin/activate   # venv\Scripts\activate on Windows
pip install -e ../core                             # shared cherrypick.core library, install first
pip install -e ".[dev]"

python -m cherrypick.earnings.tt secrets_set                       # store tastytrade OAuth credentials
python -m cherrypick.earnings.tt get_connection_status              # confirm "connected": true
```

With no config of its own, earnings runs from the shipped `config/config.example.json` (read-only). To
change settings, copy it to `~/.cherrypick/config/earnings.json` and edit that copy.

Every command below is run from inside `packages/earnings`. On macOS/Linux, if `python`/`pip` aren't
found, use `python3`/`pip3` instead.

You'll also need a local `dolt sql-server` serving three DoltHub datasets the scanner reads
live earnings-calendar, IV/RV, and realized-move data from — see
[Installation & Setup](./docs/01-setup.md) for the full walkthrough, including that step.

Once connected, try a no-risk read of tonight's candidates:

```bash
python -m cherrypick.earnings.scanner get_calendar --date MM/DD/YYYY
python -m cherrypick.earnings.strategies.iron_fly get_candidates --date MM/DD/YYYY
```

Then run the forced-sampling paper-testing program to validate the whole pipeline end-to-end
(scan → screen → size → cost-adjust → persist → close) without touching a real account:

```
/paper-start
```

On a suite install you don't need to: the supervisor's paper loop already runs the same harness
every session, and `/paper-start` refuses with `busy` while the loop holds its lock.

Full setup details, troubleshooting, and the first-trade walkthrough are in
[docs/01-setup.md](./docs/01-setup.md).

---

## How this fits the suite

This package is self-contained — everything else in this README works from `packages/earnings` on its
own. Inside the cherrypick suite it plays two roles:

- **Live / interactive (this package, standalone).** You drive the trading loop and the `/`-commands
  yourself — `/earnings-start` runs `CLAUDE.md`'s Loop Steps, `rank_strategies.py` picks each symbol's
  single best strategy. This is the only path that can place live orders, and only when you set
  `enable_live_trading: true` — experimental, off by default, at your own risk. Started from the repo
  root, `/earnings-start` shows the disclaimer verbatim and stops unless you answer an explicit YES
  whenever live trading is on. The orchestrator never touches it.
- **Unattended paper (orchestrator-orchestrated).** The [orchestrator](../orchestrator)'s supervisor runs
  this module's managed paper loop (`cherrypick.earnings.paper_loop once`) as a short-lived process every
  60 seconds, as its `earnings-paper` job. It runs only when `modules.earnings.enabled` is true AND the
  `dolt` capability is recorded. The loop drives the forced-sampling paper harness
  (`cherrypick/earnings/strat_test_harness.py`) into the isolated strat_test books; the orchestrator
  reads the resulting `paper_trades.db` — which
  lives in the shared cherrypick data home (`~/.cherrypick/data/earnings` by default) — for
  cross-module reporting. This module has no scheduler of its own. The orchestrator drives it **by
  subprocess only** — it never edits this code or config, never places, cancels, adjusts, or closes an
  order, and never flips `enable_live_trading`. Its one live-config action is onboarding
  (`cherrypick connect` / `account`), which delegates to this module's own credential tool and writes the
  chosen account into this module's `earningsagent` keyring service.

You can run the paper harness here directly too (`/paper-start`, on a machine where the supervisor
isn't already running it); letting the orchestrator manage it just
adds the watchdog, notifications, and the cross-module read side (`cherrypick report` / the console /
`calibrate`). The shared `cherrypick.core` code (calendar, fees) lives in `packages/core`, a sibling
in-repo package — see [Orchestrator & shared core](CLAUDE.md#orchestrator--shared-core) in `CLAUDE.md`
for the exact couplings.

---

## The 6 Strategies

All defined-risk, all evaluated nightly against live tastytrade chains and DoltHub data:

| Strategy | Structure | Best For |
|---|---|---|
| `iron_fly` | Short ATM straddle + long OTM wings | Medium IV, balanced risk/reward |
| `iron_condor` | Short OTM put spread + short OTM call spread | Wide expected range, directional-neutral |
| `directional_credit_spread` | Short OTM put or call spread (side chosen by skew) | Directional bias / IV skew |
| `broken_wing_butterfly` | Asymmetric (skip-strike) butterfly, wings sized to skew | Asymmetric expected moves |
| `atm_calendar` | Short front-month + long back-month, same strike | Low IV, term-structure edge |
| `double_calendar` | ATM calendar on both the call and put side | Low IV, symmetric term structure |

See [docs/05-strategies.md](./docs/05-strategies.md) for full structure, entry conditions, and
exit rules per strategy, and [docs/screening-criteria.md](./docs/screening-criteria.md) for the
shared hard filters and the accept/reject screen every candidate passes through before a strategy
sees it.

---

## How It Works

- **`cherrypick/earnings/scanner.py`** is the strategy-agnostic engine: earnings calendar, IV/RV ratio, winrate
  backtest, liquidity gates, ranking, expiration selection.
- **`cherrypick/earnings/strategies/<name>.py`** (under `src/`) holds only strategy-specific logic: hard-filter thresholds,
  accept/reject screening, strike/order construction. New strategies can be added here without
  touching the shared engine.
- **`cherrypick/earnings/rank_strategies.py`** evaluates every enabled strategy against every candidate on the
  merged today-AMC/tomorrow-BMO earnings calendar, and picks each symbol's single best strategy.
- **`cherrypick/earnings/tt.py`** is the tastytrade broker interface — quotes, chains, greeks, account info, and
  (in live mode only) order execution.
- **`packages/core`** is the shared **`cherrypick.core`** library, an in-repo sibling package, used here
  for the market calendar and the tastytrade fee schedule (via `cherrypick/earnings/costs.py`). Install it before this
  package (`pip install -e ../core`).
- **`cherrypick/earnings/paths.py`** resolves the **data home** — the trade ledgers (`earnings_trades.db`,
  `paper_trades.db`) live under `~/.cherrypick/data/earnings` by default (override with
  `EARNINGS_DATA_DIR`), the same managed location the orchestrator reads for cross-module reporting and
  where the local `dolt sql-server` serves the earnings/options/stocks datasets. Data, logs, generated
  reports and config all live under `~/.cherrypick` (config at `~/.cherrypick/config/earnings.json`,
  else a legacy in-repo `config/config.json`, else the shipped `config/config.example.json`).

Full operational detail — loop steps, config options, database schema — lives in `CLAUDE.md`,
the authoritative spec this system runs against.

---

## Paper vs. Live Mode

Controlled by `enable_live_trading` in the module's config (`~/.cherrypick/config/earnings.json`;
`false` by default). Live trading is experimental and at your own risk:

- **Paper mode**: persistence via `cherrypick/earnings/db_paper.py`, order handling stops at building the order
  spec — no order is ever submitted. Paper mode still sources live quotes/chains/greeks from the
  real tastytrade session (needed to size and price orders realistically); it just never trades.
- **Live mode**: persistence via `cherrypick/earnings/db.py`, and entries submit real orders via
  `tt.py execute_trade --live`.

Three commands, writing to isolated books:

- **`/paper-start`** — forced-sampling strategy validation (`cherrypick/earnings/strat_test_harness.py`):
  opens every strategy that clears the screen on every viable symbol, not just each symbol's
  single best, so every strategy accumulates a usable sample size quickly. Writes to per-strategy
  strat_test books (`arm='strat_test:<strategy>'`, per `strat_test_portfolio`).
- **`/paper-trading-start`** — one-shot production-ranking analysis (`rank_strategies.py`): what
  the real loop would pick tonight, without submitting anything.
- **`/earnings-start`** — the actual continuous trading loop (paper or live per
  `enable_live_trading`), run through a full market session.

The module's own EOD reports were retired 2026-08-13 — the suite review covers every module in one
place: `python -m cherrypick.review build --session <date>`. Track accumulated (multi-day) results
with `python -m cherrypick.earnings.strategy_report` (text), or on the console's earnings page (the
module's HTML strategy dashboard was retired with the other dashboards on 2026-08-12).

---

## Testing

```bash
pytest
```

---

## Documentation

- [docs/README.md](./docs/README.md) — full documentation index
- [docs/01-setup.md](./docs/01-setup.md) — installation and first-run walkthrough
- [docs/03-configuration.md](./docs/03-configuration.md) — every config parameter
- [docs/05-strategies.md](./docs/05-strategies.md) — strategy-by-strategy structure and rules
- [docs/screening-criteria.md](./docs/screening-criteria.md) — hard filters and the accept/reject screen (source
  of truth for what gates a candidate)
- `CLAUDE.md` — the authoritative operational spec (loop steps, tool reference, config options,
  database schema)

**Suite-level:** [cherrypick README](../../README.md) · [suite user guide](../../docs/PROJECT.md) ·
[orchestrator](../orchestrator) · [meic module](../meic)

---

## Disclaimer

This is a research/personal trading tool, not investment advice. Options trading involves
substantial risk of loss. Paper trade extensively before considering live capital, and never
risk more than you can afford to lose.

---

## License

[MIT](./LICENSE)
