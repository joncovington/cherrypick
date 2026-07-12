# Quick Reference: Common Commands & Workflows

Everything here is a real command you can run today — no placeholder scripts. All operations go
through `python src/tt.py <command>` (broker), `python src/scanner.py <command>` (shared engine),
or `python src/strategies/<name>.py <command>` (strategy-specific). Every command prints JSON to
stdout, so pipe through `python -m json.tool` or `jq` if you want it pretty-printed.

---

## Daily Commands

### Morning: what's on the calendar

```bash
python src/scanner.py get_calendar --date MM/DD/YYYY
```

Returns tickers reporting earnings on that date. Cross-reference against timing (`Before market
open` / `After market close`) to know whether a name belongs in today's entry window or
tomorrow's.

### Afternoon: full tiered scan for one strategy

```bash
python src/strategies/iron_fly.py get_candidates --date MM/DD/YYYY
```

Same command shape for any strategy (`iron_condor`, `directional_credit_spread`,
`broken_wing_butterfly`, `reverse_fly`, `atm_calendar`, `double_calendar`). Returns Tier 1/2/3
candidates with pass/skip reasons per `docs/screening-criteria.md`, ranked candidates, and which
ones survived the account-wide cap/correlation filter.

### Cross-strategy: which strategy wins for each symbol tonight

```bash
python src/rank_strategies.py get_ranked_symbols --date MM/DD/YYYY
```

Evaluates every enabled strategy against every candidate on the merged today-AMC/tomorrow-BMO
calendar and picks each symbol's single best-ranked strategy. This is what the live/paper loop
(`/earnings-start`) actually calls at entry time — see `CLAUDE.md`'s Loop Step 4b.

### Building a concrete order

```bash
python src/strategies/iron_fly.py get_order --symbol AAPL --earnings_date 2026-07-15 --earnings_timing "After market close"
```

Returns strikes, legs, credit/debit, quantity, and max loss — a real order spec priced off the
live chain, not a simulation.

---

## Strategy Selection at a Glance

The actual routing logic lives in `src/rank_strategies.py` and each strategy's own tiering
(`apply_tiering` in `src/strategies/<name>.py`), not a fixed lookup table — but as a rough mental
model:

```
Medium IV, symmetric expected move           → iron_fly
Wide expected range, directional-neutral     → iron_condor
Directional bias / IV skew                   → directional_credit_spread
Asymmetric skew, skip-strike structure       → broken_wing_butterfly
Gap-premium / long-vol setups                → reverse_fly
Low IV, term-structure edge                  → atm_calendar or double_calendar
```

Every candidate has to clear the shared liquidity/quality gates in
`docs/screening-criteria.md` before any strategy-specific logic runs. See
`docs/04-entry-conditions.md` for the actual decision framework.

---

## Exit Mechanics (In Order)

1. **Early exit check (Step 3c/3b/3d)** — the first morning after entry, between market open and
   `close_window_start` (default `09:45` ET): profit-target and stop-loss checks run against
   live quotes. `iron_fly`/`iron_condor`/`directional_credit_spread`/`broken_wing_butterfly`/
   `reverse_fly` hit at 50% of credit / 1.5x credit stop; calendars hit at 25-30% of debit.
2. **Unconditional close-window backstop (Step 3)** — whatever is still open when the close
   window arrives gets closed regardless of P&L. IV crush already happened overnight; there's no
   more edge from holding, so this is a hard stop, not a target.

There is no same-day "4-hour backstop" — every strategy here holds overnight by design (that's
the whole edge: capturing the IV crush that happens around the report, not during the
afternoon before it). See `CLAUDE.md`'s Loop Steps for the full mechanics.

---

## Position Sizing

Sizing is code-enforced in `src/sizing.py`, not a spreadsheet calc — `compute_position_size`
scales quantity to keep max loss within `max_risk_per_trade_pct` of NLV (real account balance in
live mode, `available_capital_paper_mode` in paper mode), capped by `max_contracts_per_leg`
regardless of the risk budget. To see what it would do for a real candidate, just read the
`quantity` and `capital_at_risk` fields in `get_order`'s output — there's no separate sizing
command to run by hand.

---

## Configuration

Real config lives in `config/config.json` (copy from `config/config.example.json` — see
[Installation & Setup](./01-setup.md)). A few knobs you'll touch often:

### Switch risk profile for paper testing

```bash
python src/strategy_test_runner.py run_entries --date MM/DD/YYYY --profile balanced
```

`--profile` selects `conservative` / `balanced` / `aggressive` from `config.json`'s `profiles`
block (sizing basis, `risk_pct_multiplier`, `max_concurrent_earnings_positions`, `tier_floor`).
Omit it to use the base config unchanged.

### Adjust a strategy's profit target or stop

Edit `config/config.json` under `strategies.<name>`:
```json
{
  "strategies": {
    "iron_fly": {
      "profit_target_pct": 0.50,
      "stop_loss_credit_multiple": 1.5
    }
  }
}
```

### Loosen or tighten a liquidity/quality gate

```json
{
  "strategies": {
    "iron_fly": {
      "min_iv_rv_ratio": 1.25,
      "max_bid_ask_spread_pct": 0.15
    }
  }
}
```

See [Configuration Guide](./03-configuration.md) for every parameter and what it actually gates.

---

## Strategy Comparison

All seven strategies are defined-risk — max loss is known at entry for every one of them.

| Strategy | Structure | Entry | Hold |
|---|---|---|---|
| `iron_fly` | Short ATM straddle + long OTM wings | Credit | Overnight |
| `iron_condor` | Short OTM put spread + short OTM call spread | Credit | Overnight |
| `directional_credit_spread` | Short OTM put or call spread (side by skew) | Credit | Overnight |
| `broken_wing_butterfly` | Skip-strike butterfly, wings sized to skew | Credit | Overnight |
| `reverse_fly` | Long ATM + short OTM wings | Debit or small credit | Overnight |
| `atm_calendar` | Short front-month + long back-month, one side | Debit | Multi-day |
| `double_calendar` | ATM calendar on both call and put side | Debit | Multi-day |

---

## Troubleshooting Quick Guide

### "No candidates found"
Check the date and confirm it's actually an earnings date via
`python src/scanner.py get_calendar --date MM/DD/YYYY`. A day with genuinely no reporting
tickers in your universe isn't a bug.

### "Everything rejected"
Read the `reason` field on each rejected candidate rather than assuming something's broken — a
quiet, illiquid, or low-IV/RV night produces a lot of legitimate Tier 3 rejections. See
`docs/screening-criteria.md` for what each hard filter checks.

### "Strategy selection looks wrong"
Re-run `python src/rank_strategies.py get_ranked_symbols --date MM/DD/YYYY` and check the
per-strategy scores in the output — the ranking is logged to `scan_log` (`strategy = "_ranked"`)
so you can also query the database directly for the full evaluation trail.

### "Order rejected" / margin errors in dry-run
`python src/tt.py execute_trade --order '<JSON>'` without `--live` still performs a real margin
check against your account — read the response body for the specific rejection reason before
assuming it's a code bug.

### "Connection failed"
```bash
python src/tt.py get_connection_status
python src/tt.py secrets_status
```
If credentials aren't there or are stale, `python src/tt.py secrets_set` to re-enter them.

---

## Testing & Validation

```bash
pytest                                  # unit tests
python src/strategy_report.py           # per-strategy expectancy, win rate, IV crush
python src/strategy_dashboard.py        # writes reports/strategy_dashboard.html
```

For accumulating a real sample across all 7 strategies before trusting any of the above, use
`/paper-start` daily — see `docs/strategy-testing-plan.md`.

---

## Navigation

**← Previous:** [README](./README.md)
**Next →** [Configuration Guide](./03-configuration.md)
