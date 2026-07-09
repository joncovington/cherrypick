# Installation & Setup

Complete setup from a fresh checkout to your first paper trading cycle.

---

## Prerequisites

- Python 3.10 or newer
- A [tastytrade](https://tastytrade.com) account (sandbox or live) — required even for paper
  mode, since paper mode still sources live quotes, chains, greeks, and open interest from the
  real tastytrade session; only order submission is skipped.
- [Dolt](https://docs.dolthub.com/introduction/installation) installed locally, to serve three
  DoltHub datasets (`post-no-preference/earnings`, `post-no-preference/options`,
  `post-no-preference/stocks`) over a local `dolt sql-server` — this is where the earnings
  calendar, IV/RV history, and realized-move data come from.
- Git

---

## Step 1: Clone and install dependencies

```bash
git clone <your-fork-or-remote-url>
cd EarningsAgent

python -m venv venv
source venv/bin/activate      # macOS/Linux
# venv\Scripts\activate       # Windows

pip install -r requirements.txt
```

For running tests or contributing, also install dev dependencies:

```bash
pip install -r requirements-dev.txt
```

`requirements.txt` pulls in `tastytrade` (broker SDK), `keyring` (OS-native credential storage
for OAuth secrets), `mysql-connector-python` (talks to `dolt sql-server`, which speaks the MySQL
wire protocol), and `matplotlib` (for `strategy_dashboard.py`'s charts).

---

## Step 2: Start the local Dolt data server

Clone the three datasets this project reads from and serve them together:

```bash
mkdir dolt-data && cd dolt-data
dolt clone post-no-preference/earnings earnings
dolt clone post-no-preference/options options
dolt clone post-no-preference/stocks stocks
dolt sql-server --data-dir . --host 127.0.0.1 --port 3306
```

Leave this running in its own terminal — the scanner connects to it on demand for every scan.
Default connection settings (`dolthub_host`/`dolthub_port`/`dolthub_user`/`dolthub_database`/
`dolthub_options_database`/`dolthub_stocks_database`) live in `config/config.json` and match the
defaults above; only change them if you're serving the databases elsewhere.

---

## Step 3: Configure

```bash
cp config/config.example.json config/config.json
```

The example ships with sane, documented defaults (`enable_live_trading: false`, i.e. paper mode,
and an `available_capital_paper_mode` simulated capital basis rather than a real balance). Open
`config/config.json` and review at minimum:

- `available_capital_paper_mode` — simulated NLV for paper mode's risk-cap checks.
- `max_concurrent_earnings_positions` — account-wide cap on simultaneous overnight positions.
- `entry_window_start` / `entry_window_end` / `close_window_start` — your local timezone offset
  from ET matters here; these are ET times.

See [Configuration Guide](./03-configuration.md) for every parameter, and
`config/config.example.json`'s inline `_..._note` fields for the reasoning behind each default.

---

## Step 4: Connect your tastytrade account

Credentials are stored in your OS keyring (via the `keyring` package), never in a file:

```bash
python src/tt.py secrets_set
```

This interactively prompts for your tastytrade OAuth **client secret**, **refresh token**, and
(optionally) a specific **account number**. Then verify:

```bash
python src/tt.py secrets_status
python src/tt.py get_connection_status
```

`get_connection_status` should report `"connected": true`. This step is required in both paper
and live mode — see `CLAUDE.md`'s Loop Step 0 for the paper/live distinction.

---

## Step 5: Validate the install

```bash
# Config is valid JSON
python -c "import json; json.load(open('config/config.json')); print('Config OK')"

# All 7 strategies register cleanly
python -c "from src.rank_strategies import STRATEGY_REGISTRY; print(f'Found {len(STRATEGY_REGISTRY)} strategies')"
# Expected: Found 7 strategies

# Run the test suite
pytest
# Expected: all tests pass
```

---

## Step 6: First dry run

Pull today's earnings calendar and see what the scanner finds, without touching any account:

```bash
python src/scanner.py get_calendar --date MM/DD/YYYY
```

Then run a full one-shot candidate scan for a single strategy (no orders submitted):

```bash
python src/strategies/iron_fly.py get_candidates --date MM/DD/YYYY
```

This prints Tier 1/2/3 candidates with pass/skip reasons per `docs/screening-criteria.md`'s hard
filters. A quiet night with zero Tier 1/2 candidates is normal and not a bug.

---

## Step 7: Run the forced-sampling strategy test (recommended first)

Before running the live/paper trading loop, use `/paper-start` (see
`docs/strategy-testing-plan.md`) to force-sample all 7 strategies nightly into an isolated
`profile='strat_test'` paper book — this validates the whole pipeline end-to-end (scan, tier,
size, cost-adjust, persist, close) and starts building the sample size needed to evaluate each
strategy, without affecting the real paper/live trading book:

```
/paper-start
```

Or run its underlying commands directly:

```bash
python src/strategy_test_runner.py run_entries --date MM/DD/YYYY --profile balanced
python src/strategy_test_runner.py run_closes --profile balanced
```

Check progress at any time with:

```bash
python src/strategy_report.py
python src/strategy_dashboard.py   # writes reports/strategy_dashboard.html
```

---

## Step 8: Run the actual paper/live trading loop

Once you're comfortable with the candidates it's finding, start the real loop (paper mode by
default, since `enable_live_trading` is `false` in the example config):

```
/earnings-start
```

This executes `CLAUDE.md`'s Loop Steps continuously through the day's entry and close windows.
Only flip `enable_live_trading: true` in `config/config.json` after you've validated results in
paper mode — live mode submits real orders via `tt.py execute_trade --live`.

---

## Troubleshooting

**"dolt sql-server not available" / scan hangs or errors on calendar/IV-RV fetch**
Confirm the server from Step 2 is still running and reachable: `mysql-connector-python` connects
to `dolthub_host`/`dolthub_port` from `config/config.json` (defaults `127.0.0.1:3306`).

**`get_connection_status` reports `"connected": false`**
Re-run `python src/tt.py secrets_set` to refresh credentials, then `secrets_status` to confirm
they're stored, then `get_connection_status` again.

**Everything comes back Tier 3 / rejected**
Normal on a quiet night — check the specific `reason` fields in the output rather than assuming
something's broken. See `docs/screening-criteria.md` for what each hard filter checks.

**`pytest` fails on tests touching the database**
Tests use isolated fixtures (see `tests/conftest.py`) and shouldn't require a live Dolt server or
tastytrade connection — if a failure mentions either, check `tests/conftest.py`'s fixtures first.

---

## Next Steps

1. **Read** [Quick Reference](./02-quick-reference.md) for daily CLI workflows.
2. **Read** `CLAUDE.md` in full — it's the authoritative operational spec (loop steps, tool
   reference, config options, database schema).
3. **Study** [Strategy Guide](./05-strategies.md) for how each of the 7 strategies works.
4. **Run** `/paper-start` daily for a few weeks before considering live trading.

---

## Navigation

**← Previous:** [README](./README.md)
**Next →** [Quick Reference](./02-quick-reference.md)
