# Configuration Guide

Everything here describes real keys in `config/config.example.json`. Copy it to
`config/config.json` (gitignored) and edit that copy — the example stays in version control as
the documented template, `config.json` is yours.

```bash
cp config/config.example.json config/config.json
```

The example file itself is heavily commented with `_..._note` fields explaining the reasoning
behind each default — read those inline notes alongside this guide rather than treating this
page as the only source. Where this guide and the example file ever disagree, trust the file;
it's what the code actually reads.

---

## Top-Level Options

| Key | Meaning |
|---|---|
| `enable_live_trading` | `false` = paper mode (default), `true` = live mode. See `CLAUDE.md`'s Loop Step 0 for the full paper/live split. Flip this only after you trust what paper mode has been finding. |
| `available_capital_paper_mode` | Simulated NLV paper mode uses for `max_risk_per_trade_pct` sizing checks. Paper mode never looks at your real tastytrade balance — size this to whatever capital you'd actually intend to trade live, or every order will get sized off a number that has nothing to do with your real account. |
| `available_capital_live_mode_source` | Always `"tastytrade"` — documents that live mode sources NLV/buying power from `tt.py get_account_info`, not this file. |
| `max_contracts_per_leg` | Hard ceiling on contracts per leg, enforced in `src/sizing.py` regardless of what the risk budget would otherwise allow. A backstop against a sizing bug rather than a knob you'll usually touch. |
| `max_concurrent_earnings_positions` | Account-wide cap on simultaneous overnight positions across every strategy. |
| `earnings_calendar_source` | Currently only `"dolthub"` is implemented. |
| `dolthub_host` / `dolthub_port` / `dolthub_user` / `dolthub_database` / `dolthub_options_database` / `dolthub_stocks_database` | Connection details for the local `dolt sql-server` from `docs/01-setup.md` Step 2. Defaults (`127.0.0.1:3306`, user `root`, databases `earnings`/`options`/`stocks`) match a stock local Dolt setup — only change these if you're serving the three datasets from somewhere else. |
| `winrate_lookback_quarters` | How many past quarters `scanner.compute_winrate()` backtests over. Coverage in the Dolt datasets only reaches back to late 2024, so a large lookback on a less-liquid name can return a much smaller sample than you asked for — always check the `sample_size` field in `get_winrate`'s raw output before trusting a thin sample. |
| `finnhub_api_key_env_var` | Name of the environment variable holding a Finnhub API key, if you're using it as a supplementary data source. Not required for the core scan/rank/order pipeline, which runs entirely on tastytrade + Dolt. |
| `entry_window_start` / `entry_window_end` | The window before close where new positions get opened, e.g. `15:30`–`15:55` ET. These are ET times regardless of your local timezone. |
| `close_window_start` | Next-morning time where Step 3's unconditional close-window backstop kicks in, e.g. `09:45` ET — chosen to be after the open's initial volatility has settled a bit. |
| `correlation_block_list` | Sector/date groupings you don't want opened simultaneously (e.g. two banks reporting the same night). Empty by default — see the note in `CLAUDE.md`: correlation risk isn't fully guarded today, so treat this as a partial mitigation, not a complete one. |
| `reprice_interval_s` / `reprice_step` | Live-mode only: how often (seconds) and by how much an unfilled limit order reprices toward the market while working an entry. |
| `tastytrade_costs` | Fee model for paper-mode cost-adjusted P&L — see below. |
| `profiles` | Named risk profiles for paper-mode testing — see below. |
| `strategies` | Per-strategy parameter blocks — see below. |

---

## `tastytrade_costs`

Models tastytrade's real commission schedule so paper-mode P&L reflects live trading costs
instead of a frictionless fantasy fill. Consumed by `src/costs.py`, kept separate from the raw
`pnl` column in the database (see `CLAUDE.md`'s Database section — `pnl` always stays gross,
cost-adjusted expectancy is computed downstream in `strategy_metrics.py`).

```json
"tastytrade_costs": {
  "commission_open_per_contract": 1.00,
  "commission_close_per_contract": 0.00,
  "commission_cap_per_leg": 10.00,
  "clearing_fee_per_contract": 0.10,
  "regulatory_fee_per_contract": 0.04,
  "slippage_frac_of_spread": 0.25
}
```

`commission_close_per_contract` is `0.00` by design — this mirrors tastytrade's actual
open-only commission model, not a placeholder. `slippage_frac_of_spread` haircuts the fill
price by a fraction of the quoted bid-ask width rather than assuming a mid-price fill. These
rates come from tastytrade's published pricing page and are checked periodically — re-verify
against the current schedule if it's been a while, since exchange/regulatory pass-through fees
do change.

---

## `profiles`

Named risk profiles for paper-mode testing (see `docs/paper-trading-profiles.md` for the full
rationale). Each layers on top of the base config: strategy defaults → per-strategy overrides →
profile overrides.

```json
"profiles": {
  "conservative": {
    "description": "Tightest sizing, Tier 1 only.",
    "available_capital_paper_mode": 100000,
    "risk_pct_multiplier": 0.6,
    "max_concurrent_earnings_positions": 2,
    "tier_floor": "Tier 1"
  },
  "balanced": {
    "description": "Base-tuned sizing, Tier 1+2.",
    "available_capital_paper_mode": 100000,
    "risk_pct_multiplier": 1.0,
    "max_concurrent_earnings_positions": 3,
    "tier_floor": "Tier 2"
  },
  "aggressive": {
    "description": "Larger sizing and more concurrent positions, Tier 1+2.",
    "available_capital_paper_mode": 100000,
    "risk_pct_multiplier": 1.6,
    "max_concurrent_earnings_positions": 5,
    "tier_floor": "Tier 2"
  }
}
```

- `risk_pct_multiplier` scales every strategy's `max_risk_per_trade_pct` up or down.
- `tier_floor` is either `"Tier 1"` (only the highest-conviction candidates) or `"Tier 2"`
  (Tier 1 and Tier 2 both eligible).
- Selected via `--profile` on `strategy_test_runner.py run_entries` / `run_closes`
  (`python src/strategy_test_runner.py run_entries --date MM/DD/YYYY --profile balanced`).
  Omitting `--profile` leaves the base config unchanged (an implicit `"default"` profile — no
  multiplier, no tier floor beyond whatever each strategy's own tiering produces).
- This is a paper-testing tool, not a live-trading feature — it exists so
  `strategy_test_runner.py` can compare how the same night's candidates would size and tier
  under different risk appetites, all writing to the same `profile='strat_test'` book tagged
  by profile name.

---

## `strategies.<name>`

Every one of the 7 strategies (`iron_fly`, `iron_condor`, `directional_credit_spread`,
`broken_wing_butterfly`, `reverse_fly`, `atm_calendar`, `double_calendar`) gets its own block
under `strategies`, keyed by the exact module name in `src/strategies/`. This avoids threshold
collisions — each strategy tunes its own liquidity/quality bars independently, even though many
of them share the same starting values today.

A handful of parameter names recur across most strategies (the shared liquidity gates live in
`scanner.py`'s `apply_liquidity_gates`, called once per strategy with that strategy's own
config block):

| Key | Meaning |
|---|---|
| `min_price` | Underlying price floor. |
| `max_bid_ask_spread_pct` | Max ATM bid-ask spread as a fraction of mid — the shared liquidity gate. `0.15` (15%) is generous enough to tolerate normal earnings-week widening while still catching genuinely illiquid chains. |
| `min_market_cap` / `near_miss_min_market_cap` | Market-cap floor (and a looser "near miss" floor used for tiering rather than an outright reject) via a live REST lookup. |
| `min_combined_option_volume` / `near_miss_min_combined_option_volume` | Front-month, chain-wide daily contract volume floor. |
| `min_combined_open_interest` | Front-month chain-wide open interest floor, sourced from on-demand DXLink `Summary` events. |
| `require_weekly_options` | If `true`, hard-rejects names without a genuine weekly expiration cadence. Small/mid-cap names with only monthly options can legitimately fail here by construction — that's expected, not a bug. |
| `min_avg_volume` / `near_miss_min_avg_volume` | Underlying shares-traded floor (distinct from *option* volume above). |
| `min_iv_rv_ratio` / `near_miss_min_iv_rv_ratio` | Implied vs. realized move ratio — the core "are these options overpriced" signal, computed live from DoltHub data. |
| `min_winrate` / `near_miss_min_winrate` | Historical backtest winrate floor over `winrate_lookback_quarters`. Always check `sample_size` in `get_winrate`'s raw output before trusting a thin sample. |
| `min_term_structure` | Front-vs-back IV term-structure slope floor (calendars and iron_fly use this to confirm a genuine earnings IV bump exists). |
| `max_front_expiration_days` | Ceiling on how far out the front/only expiration can be from earnings. |
| `min_expected_move_pct` / `min_expected_move_dollars` | Floor on the options-implied expected move, in percent or dollar terms depending on the strategy. |
| `max_risk_per_trade_pct` | Fraction of NLV this strategy's max loss is allowed to consume — feeds `sizing.compute_position_size`. |
| `profit_target_pct` | Step 3c early-exit profit target, as a fraction of max profit (credit strategies) or max profit potential (debit strategies). |
| `stop_loss_credit_multiple` (credit strategies) / `stop_loss_pct_of_debit` (debit strategies) | Step 3c early-exit stop-loss threshold. |

Everything below that isn't in the shared table above is genuinely strategy-specific.

### `iron_fly`

Short ATM straddle + long OTM wings, sized as a multiple of the credit received.

```json
"iron_fly": {
  "max_risk_per_trade_pct": 0.02,
  "min_price": 10.00,
  "max_front_expiration_days": 9,
  "require_weekly_options": true,
  "min_combined_open_interest": 2000,
  "max_atm_delta_abs": 0.57,
  "min_expected_move_dollars": 0.90,
  "min_term_structure": -0.004,
  "min_avg_volume": 1500000,
  "near_miss_min_avg_volume": 1000000,
  "min_iv_rv_ratio": 1.25,
  "near_miss_min_iv_rv_ratio": 1.00,
  "min_winrate": 0.50,
  "near_miss_min_winrate": 0.40,
  "wing_width_credit_multiple": 3.0,
  "wing_width_multiple_low": 2.5,
  "wing_width_multiple_mid": 3.0,
  "wing_width_multiple_high": 3.5,
  "wing_width_band_low_max": 1.25,
  "wing_width_band_mid_max": 1.75,
  "profit_target_pct": 0.50,
  "stop_loss_credit_multiple": 1.5,
  "max_bid_ask_spread_pct": 0.15,
  "min_market_cap": 2000000000,
  "near_miss_min_market_cap": 1000000000,
  "min_combined_option_volume": 500,
  "near_miss_min_combined_option_volume": 200
}
```

`max_atm_delta_abs` caps how far the short straddle's strike can drift from true delta-neutral
before the candidate is rejected — a sanity check on the ATM assumption. Wing width is normally
picked from the `wing_width_multiple_low/mid/high` bands, scaled to *this candidate's own*
IV/RV ratio (not broad market volatility — an earnings move is idiosyncratic to one name, so its
own IV/RV is a more relevant regime signal than VIX); `wing_width_credit_multiple` is only the
fallback if IV/RV can't be refetched at order-build time.

### `iron_condor`

Same credit-spread-plus-wings shape as `iron_fly`, but the short strikes sit at the
expected-move boundary instead of ATM — wider profit zone, lower credit.

```json
"iron_condor": {
  "min_price": 10.00,
  "max_front_expiration_days": 9,
  "min_term_structure": -0.004,
  "min_expected_move_pct": 0.04,
  "min_avg_volume": 1500000,
  "near_miss_min_avg_volume": 1000000,
  "min_iv_rv_ratio": 1.25,
  "near_miss_min_iv_rv_ratio": 1.00,
  "min_winrate": 0.50,
  "near_miss_min_winrate": 0.40,
  "wing_width_credit_multiple": 3.0,
  "wing_width_multiple_low": 2.5,
  "wing_width_multiple_mid": 3.0,
  "wing_width_multiple_high": 3.5,
  "wing_width_band_low_max": 1.25,
  "wing_width_band_mid_max": 1.75,
  "profit_target_pct": 0.50,
  "stop_loss_credit_multiple": 1.5,
  "require_weekly_options": true,
  "min_combined_open_interest": 2000,
  "max_bid_ask_spread_pct": 0.15,
  "min_market_cap": 2000000000,
  "near_miss_min_market_cap": 1000000000,
  "min_combined_option_volume": 500,
  "near_miss_min_combined_option_volume": 200
}
```

### `directional_credit_spread`

A single-sided vertical credit spread (put spread if bullish, call spread if bearish), sold on
whichever side a 25-delta risk reversal shows richer.

```json
"directional_credit_spread": {
  "min_price": 10.00,
  "max_front_expiration_days": 9,
  "min_term_structure": -0.004,
  "min_expected_move_pct": 0.04,
  "min_skew_abs": 0.02,
  "skew_delta_target": 0.25,
  "min_avg_volume": 1500000,
  "near_miss_min_avg_volume": 1000000,
  "min_iv_rv_ratio": 1.25,
  "near_miss_min_iv_rv_ratio": 1.00,
  "min_winrate": 0.50,
  "near_miss_min_winrate": 0.40,
  "wing_width_credit_multiple": 3.0,
  "wing_width_multiple_low": 2.5,
  "wing_width_multiple_mid": 3.0,
  "wing_width_multiple_high": 3.5,
  "wing_width_band_low_max": 1.25,
  "wing_width_band_mid_max": 1.75,
  "profit_target_pct": 0.50,
  "stop_loss_credit_multiple": 1.5,
  "require_weekly_options": true,
  "min_combined_open_interest": 2000,
  "max_bid_ask_spread_pct": 0.15,
  "min_market_cap": 2000000000,
  "near_miss_min_market_cap": 1000000000,
  "min_combined_option_volume": 500,
  "near_miss_min_combined_option_volume": 200
}
```

`min_skew_abs` / `skew_delta_target` gate and measure the 25-delta risk reversal used to pick
which side to sell. The short strike itself is chosen so that *breakeven* (short strike net of
credit received) lands at the expected-move boundary, not the strike itself — a genuinely
different strike-selection convention from `iron_condor`'s.

### `broken_wing_butterfly`

Body-anchored butterfly: two short contracts at the expected-move strike (side picked by skew),
protected by a narrow long wing toward spot and a wide long wing away from it.

```json
"broken_wing_butterfly": {
  "min_price": 10.00,
  "min_expected_move_pct": 0.04,
  "min_skew_abs": 0.02,
  "skew_delta_target": 0.25,
  "max_front_expiration_days": 9,
  "wing_width_multiple_low": 1.0,
  "wing_width_multiple_mid": 1.25,
  "wing_width_multiple_high": 1.5,
  "wing_width_band_low_max": 1.25,
  "wing_width_band_mid_max": 1.75,
  "wide_wing_multiple": 2.5,
  "min_avg_volume": 1500000,
  "near_miss_min_avg_volume": 1000000,
  "min_iv_rv_ratio": 1.25,
  "near_miss_min_iv_rv_ratio": 1.00,
  "min_winrate": 0.50,
  "near_miss_min_winrate": 0.40,
  "profit_target_pct": 0.25,
  "stop_loss_pct_of_debit": 0.40,
  "require_weekly_options": true,
  "min_combined_open_interest": 2000,
  "max_bid_ask_spread_pct": 0.15,
  "min_market_cap": 2000000000,
  "near_miss_min_market_cap": 1000000000,
  "min_combined_option_volume": 500,
  "near_miss_min_combined_option_volume": 200
}
```

The near wing scales off the body leg's own premium via the IV/RV-banded
`wing_width_multiple_low/mid/high` (deliberately smaller than `iron_fly`'s bands, since this
prices a single leg, not a straddle); the far wing is `wide_wing_multiple` times the near wing.
`get_order` hard-rejects any candidate that doesn't price out to a net credit or breakeven — if
real premiums at these wing widths land as a net debit, the position didn't buy back enough
premium to finance itself, so it's skipped rather than entered as a smaller-debit butterfly. If
you see a lot of rejections for that specific reason, that's the knob to revisit
(`wide_wing_multiple` or the `wing_width_multiple_*` bands), not the liquidity gates.

### `reverse_fly`

Long ATM + two shorts at the expected-move strike + a further long OTM, equidistant — a
long-vol structure aimed at capturing gap premium rather than IV crush.

```json
"reverse_fly": {
  "min_price": 10.00,
  "min_expected_move_pct": 0.04,
  "min_skew_abs": 0.02,
  "skew_delta_target": 0.25,
  "max_front_expiration_days": 9,
  "min_avg_volume": 1500000,
  "near_miss_min_avg_volume": 1000000,
  "min_iv_rv_ratio": 1.25,
  "near_miss_min_iv_rv_ratio": 1.00,
  "min_winrate": 0.50,
  "near_miss_min_winrate": 0.40,
  "profit_target_pct": 0.25,
  "stop_loss_pct_of_debit": 0.40,
  "require_weekly_options": true,
  "min_combined_open_interest": 2000,
  "max_bid_ask_spread_pct": 0.15,
  "min_market_cap": 2000000000,
  "near_miss_min_market_cap": 1000000000,
  "min_combined_option_volume": 500,
  "near_miss_min_combined_option_volume": 200
}
```

Side (call or put) is picked the same way as `directional_credit_spread` — a 25-delta risk
reversal — deliberately *not* compared at the expected-move strikes themselves, since those
aren't delta-symmetric once skew is present and would conflate ordinary structural single-name
put skew with a genuine earnings-specific signal. `min_skew_abs` is a reasoned floor, not a full
correction for structural skew — this project doesn't have a per-name pre-earnings skew
baseline to net that out against.

### `atm_calendar`

Sell a front-month ATM call, buy the same strike in a later monthly expiration — always the call
side (config-fixed, not skew-selected; call and put ATM calendars perform virtually identically
in practice). Closes as a single 2-leg unit, never leg-by-leg.

```json
"atm_calendar": {
  "min_price": 10.00,
  "min_term_structure": -0.004,
  "min_combined_open_interest": 2000,
  "back_month_min_days_after": 21,
  "require_weekly_options": true,
  "min_avg_volume": 1500000,
  "near_miss_min_avg_volume": 1000000,
  "min_iv_rv_ratio": 1.25,
  "near_miss_min_iv_rv_ratio": 1.00,
  "min_winrate": 0.50,
  "near_miss_min_winrate": 0.40,
  "profit_target_pct": 0.30,
  "stop_loss_pct_of_debit": 1.0,
  "exit_days_before_front_expiration": 5,
  "max_bid_ask_spread_pct": 0.15,
  "min_market_cap": 2000000000,
  "near_miss_min_market_cap": 1000000000,
  "min_combined_option_volume": 500,
  "near_miss_min_combined_option_volume": 200
}
```

`back_month_min_days_after` sets how far past the front expiration the back-month leg must
land. `exit_days_before_front_expiration` is a hard time-stop: once the front leg gets within 5
days of expiring, the position exits regardless of P&L, since gamma risk on a short ATM option
overwhelms any remaining theta benefit inside that window. `stop_loss_pct_of_debit: 1.0` means
the stop triggers once the position's value has round-tripped back to roughly the entry debit —
looser than the credit strategies' stop, matching a calendar's slower-resolving decay profile.

### `double_calendar`

An ATM calendar run on both the call and put side simultaneously — the only strategy whose
sides can close independently (`trade_legs` persistence, not just `legs_json`).

```json
"double_calendar": {
  "min_price": 10.00,
  "min_term_structure": -0.004,
  "min_expected_move_pct": 0.05,
  "min_combined_open_interest": 2000,
  "back_month_min_days_after": 21,
  "require_weekly_options": true,
  "min_avg_volume": 3000000,
  "near_miss_min_avg_volume": 1500000,
  "min_iv_rv_ratio": 1.25,
  "near_miss_min_iv_rv_ratio": 1.00,
  "min_winrate": 0.50,
  "near_miss_min_winrate": 0.40,
  "max_realized_move_dispersion_pct": 0.15,
  "profit_target_pct": 0.25,
  "stop_loss_pct_of_debit": 1.0,
  "leg_stop_delta_abs": 0.45,
  "exit_days_before_front_expiration": 5,
  "max_bid_ask_spread_pct": 0.15,
  "min_market_cap": 2000000000,
  "near_miss_min_market_cap": 1000000000,
  "min_combined_option_volume": 500,
  "near_miss_min_combined_option_volume": 200
}
```

Liquidity/volume floors here run stricter than `iron_fly`'s — execution cost matters more
across two expirations, and a tail-risk surprise hurts a debit trade worse than `iron_fly`'s
width-defined max loss. `max_realized_move_dispersion_pct` gates on how *predictable* the
underlying's historical earnings moves have been — high dispersion means the expected-move
assumption this structure leans on is less trustworthy. `leg_stop_delta_abs` is checked
per-side during Step 3b's intraday management (`evaluate_position()`); a side crossing this
delta threshold gets closed independently of the other side, which is what `trade_legs` exists
to support.

---

## Common Adjustments

**Loosen or tighten a liquidity gate for one strategy:**

```json
{
  "strategies": {
    "iron_fly": {
      "max_bid_ask_spread_pct": 0.20
    }
  }
}
```

**Adjust a strategy's exit target/stop:**

```json
{
  "strategies": {
    "iron_condor": {
      "profit_target_pct": 0.60,
      "stop_loss_credit_multiple": 2.0
    }
  }
}
```

**Change entry/close windows for your timezone workflow:**

```json
{
  "entry_window_start": "15:30",
  "entry_window_end": "15:55",
  "close_window_start": "09:45"
}
```

Remember these are always ET regardless of where you're running the agent from.

If you're tempted to change several of these at once based on a single night's result — don't.
See `docs/strategy-optimization.md`'s "do not blind-tune" protocol: change one parameter, run it
through `strategy_test_runner.py`'s paper program for a real sample, then compare cost-adjusted
expectancy before and after.

---

## Validating a Config Change

```bash
# Confirm it's still valid JSON
python -c "import json; json.load(open('config/config.json')); print('Config OK')"

# Confirm all 7 strategies still register
python -c "from src.rank_strategies import STRATEGY_REGISTRY; print(f'Found {len(STRATEGY_REGISTRY)} strategies')"

# Run a dry-run scan to see the change reflected in candidate output
python src/strategies/iron_fly.py get_candidates --date MM/DD/YYYY
```

To reset a strategy block back to its documented default, just re-copy that section from
`config/config.example.json` — the example file is always kept current with the reasoned
starting points described above.

---

## Navigation

**← Previous:** [Quick Reference](./02-quick-reference.md)
**Next →** [Entry Conditions Framework](./04-entry-conditions.md)
