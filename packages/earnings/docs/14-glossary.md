# Glossary: Earnings Agent Terms & Definitions

---

## Entry Conditions

**Dispersion (σ)**  
Standard deviation of historical earnings move magnitudes over past 8 quarters. Measures move consistency/predictability. Lower = tighter, more predictable. Typical range: 1-15%.

**Expected Move**  
Predicted stock movement around earnings, derived from at-the-money (ATM) option implied volatility and days to expiration. Formula: `(IV × √(DTE/365)) × Stock Price`. Example: 30% IV, 30 DTE, $100 stock = ~3.2% expected move.

**IV Rank (Implied Volatility Rank)**  
Normalized IV metric (0.0-1.0) showing current IV as percentile of past 52-week range. 0.0 = lowest IV ever, 1.0 = highest IV ever. Used to identify premium environment: IV > 0.8 = rich, IV < 0.4 = thin.

**Realized Move**  
Actual stock movement on earnings announcement. Measured as (post-announcement price - pre-announcement price) / pre-announcement price. Example: Stock at $150 before, $153 after = 2% realized move.

**Realized Move Ratio**  
Ratio of realized move dispersion (historical average) to expected move. Ratio > 1.10 = gap premium (stock moved more than IV suggested), ratio < 1.10 = normal regime.

---

## Option Greeks & Metrics

**Delta (Δ)**  
Rate of change of option price for $1 move in stock. Call delta ranges 0.0-1.0, put delta ranges 0.0-(-1.0). Approximates probability of finishing in-the-money (ITM). Example: 0.50 delta = ~50% probability of being ITM at expiration.

**Implied Volatility (IV)**  
Market's expectation of future volatility, backed out from option prices using Black-Scholes model. Higher IV = more expensive options, higher premiums. Range typically 10-100% annualized.

**Theta (Θ)**  
Rate of option value decay per day due to time passing. Positive theta = position benefits from time decay (short options, calendar spreads). Negative theta = position loses value over time (long options).

**Vega (ν)**  
Rate of change of option price for 1% change in IV. Positive vega = position benefits from IV increase (long options). Negative vega = position benefits from IV decrease (short options, short straddles).

**IV Crush**  
Rapid drop in IV after earnings announcement, typically 20-60% in first 15-30 minutes. Short options (straddles, strangles) profit from IV crush. Long options (spreads with bought wings) lose from IV crush.

---

## Strategy Terms

**At-The-Money (ATM)**  
Option strike exactly at current stock price. Example: stock at $150, 150 strike = ATM. ATM options have highest theta decay and are most sensitive to stock movement.

**Out-of-The-Money (OTM)**  
Option strike that is unprofitable if exercised immediately. Call OTM = strike > stock price, put OTM = strike < stock price. Example: stock at $150, 155 call = OTM.

**In-The-Money (ITM)**  
Option strike that is profitable if exercised immediately. Call ITM = strike < stock price, put ITM = strike > stock price. Example: stock at $150, 145 call = ITM.

**Straddle**  
Long or short position combining ATM call and ATM put. Long = pay debit, profit from big move. Short = collect credit, profit from no move (or small move). Most symmetric position structure.

**Strangle**  
Similar to straddle but OTM call and put (wider apart). Long = lower cost than straddle, requires bigger move. Short = lower credit than straddle, larger safety zone.

**Spread**  
Two-leg position: short one strike, long another strike (or different expiration). Creates defined max loss. Example: Iron Fly short straddle with long wings is two spreads (call + put).

**Iron Fly**  
Defined-risk short ATM straddle with protective wings. Short ATM call + put, long OTM call + put. Max loss = wing width - credit collected.

**Iron Condor**  
Defined-risk position with call spread (short OTM call + long far OTM call) and put spread (short OTM put + long far OTM put). No directional bias. Max loss = spread width - credit.

**Calendar Spread**  
Position exploiting term structure: short front-month option, long back-month option (same strike, different expiration). Profits from front-month decay without back-month decay.

**Jade Lizard**  
Directional strategy combining defined-risk spread on one side with naked short on other side. Example: short call spread + naked put (bullish). Partial unlimited risk.

**Broken Wing Butterfly**  
Asymmetric spread using unequal wing widths. Exploits IV skew by making profit zone wider on one side than the other.

---

## Position Management

**Entry Credit**  
Total premium received when entering a short position. Straddle might collect $5.00 credit. Profit target = 50% of entry credit.

**Profit Target**  
Exit point when position reaches profit threshold. Typical: 50% of max credit (for spreads/straddles), 25% of entry debit (for calendar spreads). Auto-exit trigger.

**Max Loss (Max Risk)**  
Maximum theoretical loss on position. Defined-risk strategies have known max loss. Naked strategies (straddle, strangle) have unlimited max loss.

**Per-Leg Delta Stop**  
Stop-loss on individual legs. Example: "stop if call delta reaches 0.60". Protects against one-sided blowout. Used on naked/short strategies.

**4-Hour Backstop**  
Hard exit 4 hours post-announcement. Ensures you exit after IV crush peak (typically 15-30 min post-announcement, fades over 4 hours). Safety mechanism to avoid extending positions.

**Defined risk**  
Every strategy in the system has a maximum loss known at entry, computed from the order's own
strikes/debit (see `src/sizing.py`). Undefined-risk/naked strategies were removed — a naked
short on a single-name earnings gap can blow out arbitrarily during the unmonitored overnight
hold.

**Wide Wings**  
Extended protective wings in spread. Normal Iron Fly has 3x wing width, wide-wing Iron Fly has 8x wing width. Wider wings ≈ closer to naked behavior while staying defined-risk.

---

## Market Conditions

**High IV Environment**  
IV Rank > 0.80. Options expensive. Short premium strategies (straddles, strangles) more attractive. Entry credit higher.

**Medium IV Environment**  
IV Rank 0.60-0.80. Standard premium levels. Balanced strategies (Iron Fly, Iron Condor) most common.

**Low IV Environment**  
IV Rank < 0.60. Options cheap. Short premium strategies unattractive. Calendar spreads or directional spreads better.

**IV Skew**  
Asymmetric IV across strikes. Example: put IV 25% higher than call IV. Indicates market expects downside move more than upside. Used to select directional strategies (Jade Lizard, directional spreads).

**Term Structure**  
Relationship between IV at different expirations. Contango = back month IV < front month IV (typical). Backwardation = back month IV > front month IV (rare, crisis mode). Used in calendar spread analysis.

---

## Earnings Specific

**Overnight Earnings Play**  
Strategy entered day-of (if after-hours) or day-before (if pre-market) earnings, held through announcement, exited same day. Example: AAPL earnings after close, enter before 4 PM, exit by 5 PM.

**Earnings Announcement Time**  
When company releases earnings. "After market close" = ~4:05 PM ET. "Before market open" = ~6:30 AM ET next day. Affects entry/exit timing.

**Earnings Surprise**  
Unexpected EPS or guidance, causes stock gap. Positive surprise = stock up, negative = stock down. Dispersions doesn't predict surprises, only move magnitude.

**Realized Move Dispersion**  
Same as dispersion. Standard deviation of historical earnings move magnitudes.

---

## Risk & Portfolio

**Defined Risk**  
Strategy with known, limited maximum loss. Example: Iron Fly max loss = $3.50. Easy to position size.

**Undefined Risk (Unlimited Risk)**  
Strategy with unbounded maximum loss. Example: Short straddle can lose unlimited if stock gaps 20%. Hard to position size, requires capital reserves.

**Tier 1 Candidate**  
High-confidence earnings play. Tight dispersion, rich IV, clear strategy fit. Execute first.

**Tier 2 Candidate**  
Medium-confidence earnings play. Normal dispersion, normal IV. Execute if capital available.

**Tier 3 Candidate**  
Low-confidence earnings play. Borderline metrics, calendar spreads only. Execute for fill/activity only.

**Position Sizing**  
Number of spreads/contracts entered. Example: 5 spreads = 5 × 100 shares = 500 share exposure. Determined by account size and max loss tolerance.

**Max Concurrent Positions**  
Limit on total earnings positions held simultaneously. Example: "max 3 concurrent earnings plays". Risk management lever.

**Daily Trade Target**  
Desired number of trades per day. Example: 2-3 Tier 1 entries, maybe 1-2 Tier 2 if opportunity.

---

## Analysis & Testing

**Entry Condition Framework**  
Multi-level decision matrix: Gates → Primary → Secondary → Tertiary. Used to automatically select optimal strategy for each candidate.

**Decision Matrix**  
Framework for routing candidates to strategies. Primary decision = realized vs expected move. Secondary = dispersion. Tertiary = IV rank.

**10-Day Framework Test**  
Simulation of 10 market days of earnings scans and strategy selection. Validates decision matrix. Example: 26 total candidates, 0 rejections, 46% IRON_FLY, 38% SHORT_STRADDLE, 15% CALENDAR.

**Backtesting**  
Historical testing of strategy against past earnings. Example: run 2025 earnings through decision matrix, see if strategy selection was optimal.

**Unit Test**  
Automated test of single component. Example: test that an IRON_FLY position exits at 50% profit target.

---

## Configuration

**wing_width_credit_multiple**  
Multiplier for spread wing width relative to credit collected. Default 3.0x means wings are 3x the credit width apart. Wide-wing fallback uses 8.0x.

**max_realized_move_dispersion_pct**  
Maximum allowed dispersion for entry. Default 0.15 (15%). Candidates above this are rejected.

**min_iv_rank**  
Minimum IV rank threshold for entry. Default 0.15. Candidates below this are rejected (insufficient premium).

**profit_target_pct**  
Profit target as percentage of entry credit. Default 0.50 (50%). Exit when profit = 50% of credit.

**leg_stop_delta_abs**  
Delta threshold for per-leg protective stop. Default 0.60 for naked, 0.45 for spreads. If short leg delta reaches this, exit.

**exit_after_announcement_minutes**  
Backstop exit time. Default 240 (4 hours). Force exit 4 hours post-announcement.

---

## Commands

**`get_candidates --date YYYY-MM-DD`**  
Scan earnings calendar for given date. Returns all candidates with metrics and strategy assignments.

**`get_candidate --symbol AAPL --earnings_date YYYY-MM-DD`**  
Get detailed analysis for single candidate. Returns dispersion, IV rank, expected move, strategy recommendation.

**`get_order --symbol AAPL --strategy SHORT_STRADDLE`**  
Generate concrete order spec for entry. Returns strikes, quantities, expected credit, profit target, stops.

**`log_trade --symbol AAPL --date YYYY-MM-DD`**  
Log completed trade results. Updates win/loss stats, decision matrix validation, learning.

---

## Acronyms

- **ATM** — At-The-Money
- **OTM** — Out-of-The-Money  
- **ITM** — In-The-Money
- **IV** — Implied Volatility
- **DTE** — Days To Expiration
- **EPS** — Earnings Per Share
- **μ** — Mean (expected move)
- **σ** — Sigma (standard deviation / dispersion)
- **ROI** — Return on Investment
- **P&L** — Profit & Loss
- **Δ** — Delta (rate of change)
- **Θ** — Theta (time decay)
- **ν** — Vega (volatility sensitivity)
- **VIX** — Volatility Index

---

## Navigation

**← Previous:** [Troubleshooting](./13-troubleshooting.md)  
**← Return to:** [README](./README.md)
