# Paper Trading Profiles — Multi-Week Risk-Profile Testing Plan

Goal: run several **risk profiles** side by side in paper mode for a few weeks, on
identical market conditions, then promote the best-performing profile's exact
settings to live trading — no re-derivation, no guesswork.

---

## Design principle: profile = book

Each risk profile is an **isolated simulated account** ("book") with its own capital,
its own open positions, and its own P&L ledger. Every night, **all enabled profiles are
scored against the same earnings calendar**. The expensive live scan
(`rank_strategies.py get_ranked_symbols`, which makes the tastytrade/DoltHub calls) runs
**once**; each profile then filters, sizes, and selects from that shared ranked list under
its own rules. After several weeks the books are compared head-to-head on identical
market conditions — a controlled experiment, not three separate runs on different nights.

A profile differs from the base config in a small set of dimensions:

| Dimension | Effect |
|---|---|
| `available_capital_paper_mode` | Simulated capital basis for the risk cap |
| `risk_pct_multiplier` | Scales every strategy's `max_risk_per_trade_pct` up/down |
| `max_concurrent_earnings_positions` | How many overnight books it will hold |
| `tier_floor` | `Tier 1` (only) vs `Tier 2` (Tier 1 + 2) |
| `strategy_overrides` | Optional per-strategy gate tightening/loosening |

---

## Starter profiles

All three share the same starting capital so the comparison isolates *risk behavior*, not
starting-balance differences.

| Profile | Capital | risk×mult | max concurrent | tier floor |
|---|---|---|---|---|
| `conservative` | 100k | 0.6 | 2 | Tier 1 |
| `balanced` | 100k | 1.0 | 3 | Tier 2 |
| `aggressive` | 100k | 1.6 | 5 | Tier 2 |

---

## Phasing

### Phase 1 — foundation (this pass)
- **Profiles config** — `profiles` block in `config.json`; `_load_config(profile)` layers
  `strategy_defaults → per-strategy → profile overrides`. Backward-compatible when
  `profile` is None.
- **Code-enforced sizing** — new `src/sizing.py`. Given an order and the active profile,
  it computes per-contract max loss, the risk budget (`capital × max_risk_per_trade_pct ×
  risk_pct_multiplier`), and the resulting contract quantity — or rejects
  `risk_cap_exceeded`. This replaces the agent applying the cap by hand in the loop, making
  every night reproducible.
- **DB attribution** — `trades` and `scan_log` gain a `profile` column; `trades` gains
  `quantity` and `capital_at_risk`. Existing rows migrate to `profile = 'default'`.

### Phase 2 — parallel books
- Loop Step 4b runs the shared scan once, then iterates enabled profiles; each applies its
  own tier floor / sizing and opens into its own book (same paper DB, tagged by `profile`).
  Close logic (Steps 3/3b/3c/3d) already keys on open positions — it simply processes every
  open position regardless of profile.

### Phase 3 — analysis + promotion
- `src/compare_profiles.py` — over a date range, per profile: trades, win rate, expectancy,
  total/avg P&L, max drawdown, capital-at-risk utilization, and rejection-reason histogram.
- Promotion doc: after N weeks, copy the winning profile's parameter block into the live
  config root and flip `enable_live_trading` only after review.

---

## Per-contract max-loss rules (sizing.py)

Sizing needs a defensible per-contract max loss for each strategy. Strikes are in points;
one contract controls 100 shares.

| Strategy | Per-contract max loss |
|---|---|
| iron_fly | (widest wing − credit) × 100 |
| iron_condor | (widest wing − credit) × 100 |
| directional_credit_spread | (\|long−short\| − credit) × 100 |
| atm_calendar / double_calendar | debit × 100 |
| reverse_fly | its own `max_loss` field × 100 |
| broken_wing_butterfly | (far_width − near_width + net_debit) × 100 |

Every strategy is defined-risk, so max loss comes straight from the order's own
strikes/debit — there is no naked/undefined-risk margin proxy. The BWB gap approximation is
a **Phase-1 estimate** to be refined once real paper fills accumulate.

---

## Promotion criteria (fill in after data collection)

Decide the winner on: positive expectancy, win rate vs. the strategies' historical
backtest, max drawdown tolerance, and capital utilization. Record the chosen thresholds
here before starting the test so the decision is pre-committed, not fit to the results.
