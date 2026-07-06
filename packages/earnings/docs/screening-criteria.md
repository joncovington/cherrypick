# Screening Criteria

Two layers, run in this order: **(1) universe hard filters** (cheap, run against every ticker on the day's earnings calendar to cut the list down fast) then **(2) entry-time re-verification** (run only against candidates that survive layer 1, right before submitting an order — see `CLAUDE.md`'s Step 4b). A candidate must clear both layers; layer 1 alone is not sufficient to trade on.

## Layer 1 — Universe hard filters (no exceptions)

Run once per ticker per scan, in this order (cheapest/fastest-to-reject first, matching EarningsEdgeDetection's own performance-optimized ordering):

1. **Price ≥ $10.00** — sub-$10 names have degenerate option pricing (minimum tick sizes dominate the spread) and unreliable IV calculations.
2. **Front-month expiration ≤ 9 days from today** — keeps nearly all of the front leg's extrinsic value attributable to the earnings event itself, not generic time decay. A front-month further out dilutes the IV-crush signal with unrelated decay.
3. **Combined open interest ≥ 2,000 contracts** (calls + puts, front-month, all strikes) — liquidity floor; below this, the fill price on a 4-leg iron fly will not resemble the mid.
4. **Term structure ≤ -0.004** — `(front_atm_iv - back_atm_iv) / back_atm_iv`, computed via `scanner.compute_term_structure()`. This is the core signal: front-month IV must be inflated relative to back-month by a real margin, not just "any negative number," or the trade has no edge over background IV noise.
5. **ATM delta ≤ 0.57 in absolute value** — sanity check that the strike selected as "ATM" for the term-structure calc actually is ATM; a delta this far from 0.50 means the strike grid is too coarse near the money for this name and the term-structure reading is unreliable.
6. **Expected move ≥ $0.90** (nearest expiration, dollar terms) — a straddle price below this is too cheap to be worth the transaction cost of a 4-leg order regardless of how attractive the ratios look.
7. **Full option chain must be fetchable and both front/back expirations must exist** — reject outright (not a soft fail) if the chain is incomplete; do not guess or substitute a different expiration.

## Layer 1 — Additional criteria (soft; produce a near-miss band rather than an outright reject)

8. **30-day average volume**: pass ≥ 1,500,000 shares; near-miss 1,000,000–1,499,999; reject below 1,000,000.
9. **Winrate** (historical: % of past earnings where the option-implied move — ATM straddle mid on the nearest expiration on/after the earnings reaction — exceeded the actual realized move, target 8 quarters sample): pass ≥ 50%; near-miss 40–49.9%; reject below 40%. **Implemented** — `scanner.compute_winrate()` backtests against DoltHub's `post-no-preference/options.option_chain` (historical chains) and `post-no-preference/stocks.ohlcv` (realized moves). Verified live 2026-07-06: AAPL scored 7/7 (100%) over the available sample, but note the **real sample-size caveat** found during testing — `option_chain`'s historical depth only reaches back to roughly late 2024, so a "last 8 quarters" request against an older or less-covered symbol may return a materially smaller sample (5 of the requested 8 quarters were skipped for AAPL with `no_matching_option_chain_data`, all skips logged, not silently dropped). **Always check `sample_size` before trusting a winrate** — a 100% winrate on a 2-quarter sample is not the same claim as on 8.
10. **IV/RV ratio**: pass ≥ 1.25; near-miss 1.00–1.24; reject below 1.00. **Implemented** — `scanner.fetch_iv_rv_ratio()` queries `post-no-preference/options.volatility_history`'s `iv_current`/`hv_current` (falling back up to 5 trading days back if the most recent row has a null `iv_current`, which happens even for liquid large-caps). Verified live 2026-07-06 against a real dual-database `dolt sql-server` (AAPL: IV/RV ≈ 0.84 as of 2026-07-02 — below the 1.25 pass threshold, correctly landing in reject territory for that name on that day).

## Tiering (assigned after layer 1)

- **Tier 1**: passes all hard filters (1–7) and all additional criteria (8–10) at the "pass" band. Only Tier 1 is eligible for automatic entry (per `CLAUDE.md`).
- **Tier 2**: passes all hard filters, has exactly one additional criterion in its near-miss band. Logged, not auto-traded.
- **Near Miss**: passes all hard filters, multiple criteria in near-miss band or below. Logged as a watchlist candidate only.
- All three additional criteria (#8, #9, #10) are now computable, so Tier 1 is reachable for the first time — but a low-`sample_size` winrate (see #9's caveat) is a real reason to treat a "Tier 1" result with more skepticism than the label alone conveys. `get_candidates` (which will actually assign tiers across a full day's calendar) is not implemented yet; today each signal only works standalone per-symbol (`get_iv_rv`, `get_winrate`).

## Layer 2 — Entry-time re-verification (immediately before order submission, not at scan time)

The scan runs once in the afternoon; by the entry window near the close, prices/IV may have moved. Re-check, live, right before submitting:

- Term structure and expected move — re-pull the chain and recompute; reject if either has fallen out of range since the scan (`action: "entry_skip"`, `reason: "reverify_failed_term_structure"` / `"reverify_failed_expected_move"`).
- Earnings date/timing hasn't shifted (companies do reschedule) — reject if the calendar source's `when` field for this date no longer matches what was scanned.
- Liquidity hasn't degraded — re-check live bid/ask width and OI, not just the scan-time snapshot.
- **Position-level risk cap**: max loss (wing width − credit received) must be ≤ `max_risk_per_trade_pct` of account NLV — independent of and in addition to the scanner's own risk/reward ratio.
- **Correlation check**: reject if this candidate shares a `correlation_block_list` grouping with an already-open or already-entered-tonight position.

## Rationale for filter ordering

Cheapest/fastest checks run first (price, then expiration date, both simple lookups) so a large daily ticker universe rejects fast without spending time on expensive checks (full chain pull, term-structure calc) for names that were never going to qualify. This mirrors EarningsEdgeDetection's own "Filter Chain Ordering" performance note.
