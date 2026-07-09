# Glossary

---

## Core Signals

**Term Structure**
`(back_atm_iv - front_atm_iv) / back_atm_iv`, computed live via
`scanner.compute_expected_move_and_term_structure()`. Negative and large in magnitude means the
front month's IV is meaningfully inflated relative to the back month — the core "is there a real
earnings-specific IV bump" signal most strategies gate on (`min_term_structure`, e.g. `-0.004`).

**IV/RV Ratio**
Implied move vs. historical realized move, from `scanner.fetch_iv_rv_ratio()` against DoltHub's
`volatility_history` table. Above 1.0 means the options are priced for more movement than the
stock has actually delivered historically — the central "are these options overpriced" signal
(`min_iv_rv_ratio`, e.g. `1.25`).

**Winrate**
Historical percentage of past earnings where the option-implied move (ATM straddle mid) exceeded
the actual realized move, over `winrate_lookback_quarters` (default 8). Computed by
`scanner.compute_winrate()`. Always check the accompanying `sample_size` — Dolt's historical
option-chain coverage only reaches back to roughly late 2024, so a "last 8 quarters" request can
come back with a much smaller real sample, especially for less-liquid names.

**Expected Move**
The options-implied move around earnings, in percent (`min_expected_move_pct`) or dollar terms
(`min_expected_move_dollars`), derived from ATM straddle pricing on the nearest expiration.

**Skew** (`min_skew_abs`, `skew_delta_target`)
A 25-delta risk reversal — the IV difference between a delta-symmetric out-of-the-money call and
put — used by `directional_credit_spread`, `broken_wing_butterfly`, and `reverse_fly` to pick
which side (calls or puts) to sell/build around. Compared at a fixed delta target, not at the
expected-move strikes themselves, since those aren't delta-symmetric once skew is present.

**Composite Score**
`abs(term_structure) * iv_rv_ratio * shrunk_winrate`, computed by
`scanner.compute_composite_score()`. Used both to rank a single strategy's own candidates
against each other and, via `rank_strategies.py`, to pick which strategy wins for a symbol that
qualifies under more than one. `shrunk_winrate` pulls a thin-sample winrate toward a neutral 0.5
prior in proportion to how far `sample_size` falls below the target lookback.

---

## Option Greeks

**Delta (Δ)**
Rate of change of an option's price per $1 move in the underlying. Roughly approximates
probability of finishing in-the-money. Used directly in `iron_fly`'s `max_atm_delta_abs` sanity
check and `double_calendar`'s `leg_stop_delta_abs` intraday management trigger.

**Theta (Θ)**
Rate of option value decay per day from time passing. The core edge behind the calendar
strategies (`atm_calendar`, `double_calendar`) — front-month theta decays faster than back-month.

**Vega (ν)**
Rate of change of option price per 1% change in IV. What actually gets captured by the overnight
IV-crush edge this whole system is built around: short vega positions (credit strategies) profit
when IV collapses after the earnings reaction.

**IV Crush**
The rapid drop in implied volatility once earnings uncertainty resolves. This is the core edge
every credit strategy in this system is built to capture — see
[Exit Strategy Guide](./10-exits.md) for exactly when and how a position captures it.

---

## Strategies (All Seven, All Defined-Risk)

Full structure/entry/exit detail for each lives in [Strategy Guide](./05-strategies.md); this is
a one-line reminder of the shape of each:

- **`iron_fly`** — short ATM straddle + long OTM wings.
- **`iron_condor`** — short OTM put spread + short OTM call spread, strikes at the
  expected-move boundary instead of ATM.
- **`directional_credit_spread`** — a single-sided vertical credit spread, side chosen by skew.
- **`broken_wing_butterfly`** — body-anchored (no ATM leg) butterfly with an asymmetric near/far
  wing, sized to skew.
- **`reverse_fly`** — long ATM + two shorts at the expected-move strike + a further long OTM,
  aimed at gap-premium capture rather than IV crush.
- **`atm_calendar`** — short front-month ATM call + long back-month same strike, single 2-leg
  unit.
- **`double_calendar`** — an ATM calendar run on both the call and put side, the only strategy
  whose sides can close independently.

Every strategy is **defined-risk** — max loss is known at entry. Undefined-risk/naked strategies
(naked straddles, strangles, naked short verticals) were deliberately excluded from this system:
a single-name earnings gap on a naked short can blow out arbitrarily during the unmonitored
overnight hold.

**At-The-Money (ATM) / Out-of-The-Money (OTM) / In-The-Money (ITM)**
Standard option moneyness terms relative to the current stock price — ATM strike near spot, OTM
unprofitable if exercised now, ITM profitable if exercised now.

**Straddle**
A same-strike call+put position (long or short). `iron_fly`'s short straddle is the credit
engine at the center of that strategy, hedged by long OTM wings.

**Wing / Wing Width**
The long, further-OTM options that cap max loss in a credit structure. Wing width is normally
picked from IV/RV-banded multiples (`wing_width_multiple_low/mid/high`) scaled to the
candidate's own IV/RV ratio — see [Configuration Guide](./03-configuration.md).

---

## Position Management

**Entry Credit / Entry Debit**
Net premium received (credit strategies) or paid (debit strategies, e.g. the calendars and
`reverse_fly`) at entry. Stored with a consistent sign convention in the database — see
`CLAUDE.md`'s Database section.

**Profit Target** (`profit_target_pct`)
Step 3c early-exit target, checked the first morning after entry — a fraction of max profit for
credit strategies, or of max profit potential for debit strategies.

**Stop Loss** (`stop_loss_credit_multiple` for credit strategies, `stop_loss_pct_of_debit` for
debit strategies)
Step 3c early-exit stop threshold, checked alongside the profit target.

**Close-Window Backstop**
The unconditional exit (Step 3 in `CLAUDE.md`'s Loop Steps) that closes whatever's still open
once `close_window_start` arrives, regardless of P&L. There's no same-day, hours-after-the-
announcement backstop in this system — every strategy is either a single overnight hold or a
multi-day calendar hold, closed out by this mechanism if nothing else has closed it first. See
[Exit Strategy Guide](./10-exits.md).

**`leg_stop_delta_abs`**
`double_calendar`-specific per-side delta threshold, checked during its intraday management
(Step 3b). A side crossing this gets closed independently of the other side.

**Defined Risk / Undefined Risk**
Defined-risk: maximum loss is known at entry (every strategy in this system). Undefined risk:
maximum loss is theoretically unbounded (naked strategies — deliberately not implemented here).

**Tier 1 / Tier 2 / Near Miss / Reject**
Per-strategy classification from that strategy's own `apply_tiering()`. Tier 1 clears every hard
filter and every additional criterion at the "pass" band; Tier 2 clears every hard filter with
exactly one criterion in its near-miss band; Near Miss clears hard filters but has multiple
near-miss criteria; Reject fails a hard filter outright. Only Tier 1/2 are eligible for entry.
See [Screening Criteria](./screening-criteria.md).

**Position Sizing**
Number of contracts per leg, computed by `src/sizing.py`'s `compute_position_size` to keep max
loss within `max_risk_per_trade_pct` of NLV, capped by `max_contracts_per_leg` regardless of the
risk budget.

**Max Concurrent Positions** (`max_concurrent_earnings_positions`)
Account-wide cap on simultaneous overnight positions across every strategy.

---

## Analysis & Testing

**Cross-Strategy Ranking**
`rank_strategies.py`'s process of evaluating every registered strategy against every symbol on
the calendar and picking each symbol's single best-ranked strategy by composite score. See
[Entry Conditions Framework](./04-entry-conditions.md).

**Forced-Sampling Paper Test**
`strategy_test_runner.py`'s testing program (`/paper-start`) — opens every Tier 1/2 candidate
under every qualifying strategy (not just each symbol's single best), so every strategy
accumulates a usable sample size instead of only the ones that happen to win the head-to-head
comparison. Writes to an isolated `profile='strat_test'` book. See
`docs/strategy-testing-plan.md`.

**Cost-Adjusted Expectancy**
Expected P&L per trade after subtracting tastytrade's real commission/fee schedule (modeled in
`src/costs.py`), computed downstream in `strategy_metrics.py` — kept separate from the raw
`pnl` column stored per trade, which always stays gross.

**IV Crush (measured)**
`entry_iv - exit_iv`, computed in `strategy_metrics.py` from the average live IV across a
trade's Sell-to-Open leg(s), captured at entry and exit.

---

## Configuration Keys (Selected)

Full parameter reference, per strategy, lives in [Configuration Guide](./03-configuration.md).
A few worth calling out by name since they show up throughout the other docs:

- **`available_capital_paper_mode`** — simulated NLV basis for paper-mode risk-cap checks.
- **`max_risk_per_trade_pct`** — per-strategy fraction of NLV a single trade's max loss may
  consume.
- **`profit_target_pct`** / **`stop_loss_credit_multiple`** / **`stop_loss_pct_of_debit`** —
  Step 3c early-exit thresholds, per strategy.
- **`min_iv_rv_ratio`** / **`min_term_structure`** / **`min_winrate`** — the shared core
  quality gates every strategy applies with its own threshold.
- **`max_bid_ask_spread_pct`** / **`min_market_cap`** / **`min_combined_open_interest`** /
  **`min_combined_option_volume`** — shared liquidity gates.
- **`profiles`** — named risk profiles (`conservative`/`balanced`/`aggressive`) for paper-mode
  testing via `--profile`.

---

## Commands

**`python src/scanner.py get_calendar --date MM/DD/YYYY`**
Fetch tickers with earnings on a given date.

**`python src/strategies/<name>.py get_candidates --date MM/DD/YYYY`**
Full tiered scan for one strategy: Tier 1/2/3, pass/skip reasons, ranked candidates, selected.

**`python src/rank_strategies.py get_ranked_symbols --date MM/DD/YYYY`**
Cross-strategy ranking — evaluates all seven strategies against every symbol, picks each
symbol's best.

**`python src/strategies/<name>.py get_order --symbol X --earnings_date DATE --earnings_timing "..."`**
Build a concrete tradeable order from live chain data.

**`python src/tt.py execute_trade --order '<JSON>' [--live]`**
Dry-run validate (no `--live`, still performs a real margin check) or submit a live order.

**`python src/strategy_report.py`** / **`python src/strategy_dashboard.py`**
Per-strategy expectancy/win-rate/IV-crush text report or self-contained HTML dashboard.

See `CLAUDE.md`'s Tool Reference for the complete command list.

---

## Acronyms

- **ATM** — At-The-Money
- **OTM** — Out-of-The-Money
- **ITM** — In-The-Money
- **IV** — Implied Volatility
- **RV** — Realized Volatility
- **DTE** — Days To Expiration
- **NLV** — Net Liquidating Value (account equity)
- **σ** — Sigma (standard deviation / dispersion)
- **Δ** — Delta
- **Θ** — Theta
- **ν** — Vega
- **P&L** — Profit & Loss

---

## Navigation

**← Previous:** [Examples & Case Studies](./11-examples.md)
**← Return to:** [Documentation Index](./README.md)
