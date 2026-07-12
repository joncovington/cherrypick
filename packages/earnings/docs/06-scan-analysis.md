# Earnings Scan Analysis

How to actually read a day's scan output — what each field means, how tiering and ranking flow
into each other, and how to tell a genuinely quiet night from something broken.

---

## The Two Commands You'll Actually Run

**Single strategy, one date** — full tiered scan against every symbol on the calendar for that
one strategy:

```bash
python src/strategies/iron_fly.py get_candidates --date MM/DD/YYYY
```

Works identically for any of the seven (`iron_condor`, `directional_credit_spread`,
`broken_wing_butterfly`, `reverse_fly`, `atm_calendar`, `double_calendar`) — every strategy's
`get_candidates` is a thin wrapper around the same shared engine function,
`scanner.run_candidate_scan()`.

**Cross-strategy, one date** — evaluates all seven against every symbol and picks each symbol's
best:

```bash
python src/rank_strategies.py get_ranked_symbols --date MM/DD/YYYY
```

This second command is what the live/paper loop calls at entry time (`CLAUDE.md`'s Step 4b) —
run it yourself before the market close to see exactly what the loop is about to do.

---

## Reading a Single Strategy's `get_candidates` Output

```json
{
  "ok": true,
  "date": "07/15/2026",
  "candidates": [
    {
      "symbol": "AAPL",
      "earnings_timing": "After market close",
      "tier": "Tier 1",
      "hard_fail_reasons": [],
      "near_miss_reasons": [],
      "criteria": {
        "price": 187.32,
        "term_structure": -0.021,
        "expected_move_dollars": 5.10,
        "atm_delta_abs": 0.51,
        "avg_volume": 58000000,
        "iv_rv_ratio": 1.41,
        "winrate": 0.71,
        "winrate_sample_size": 6
      },
      "winrate_sample_size": 6,
      "broker_data_error": null
    }
  ],
  "ranked": [ { "symbol": "AAPL", "composite_score": 0.0214 } ],
  "selected": [ { "symbol": "AAPL", "composite_score": 0.0214 } ],
  "skipped_for_selection": []
}
```

- **`candidates`** — every symbol on that date's calendar, sorted Tier 1 → Tier 2 → Near Miss →
  Reject, each with the exact criteria that were computed and which (if any) hard filter or
  near-miss band was tripped. `hard_fail_reasons` non-empty means `Reject`; a single
  `near_miss_reasons` entry with no hard fails means `Tier 2`; everything clean is `Tier 1`. See
  [Screening Criteria](./screening-criteria.md) for exactly what each named criterion checks and
  its pass/near-miss/reject thresholds.
- **`ranked`** — only Tier 1/Tier 2 candidates, scored by `scanner.compute_composite_score()`
  (`abs(term_structure) * iv_rv_ratio * shrunk_winrate`) and sorted descending. Reject and Near
  Miss never appear here — a score on a candidate that already failed hard filters doesn't mean
  anything.
- **`selected`** — what survives `scanner.select_positions()`'s account-wide
  `max_concurrent_earnings_positions` cap and `correlation_block_list` check, walking down the
  ranked list and backfilling around any skip.
- **`skipped_for_selection`** — every ranked candidate that didn't make the cut, each with a
  specific reason (`concurrency_cap_reached`, `correlation_blocked`, etc.) rather than silently
  dropped.
- **`broker_data_error`** — non-null if the live tastytrade chain pull failed for that symbol
  (e.g. no listed options at all). This is a data problem for that one symbol, not a scan-wide
  failure — every other symbol that night is still processed independently.

Always check `criteria.winrate_sample_size` before trusting a high winrate. Historical coverage
in the Dolt datasets only reaches back to late 2024 — a `winrate_lookback_quarters: 8` request
against a less-liquid or newly-listed name can legitimately come back with a sample of 2 or 3
quarters, and `compute_composite_score` already discounts this via `shrunk_winrate`, but it's
still worth eyeballing before you act on any single candidate's raw number.

---

## Reading `rank_strategies.py`'s Output

```bash
python src/rank_strategies.py get_ranked_symbols --date 07/15/2026
```

```json
{
  "ok": true,
  "date": "07/15/2026",
  "symbols": [
    {
      "symbol": "AAPL",
      "earnings_date": "2026-07-15",
      "earnings_timing": "After market close",
      "outcome": "selected",
      "reason": "selected iron_fly (score 0.0214) over iron_condor (score 0.0179) within this symbol; ranked 1/4 across today's universe",
      "best_strategy": "iron_fly",
      "best_score": 0.0214,
      "strategies": [ { "name": "iron_fly", "tier": "Tier 1", "...": "..." }, { "name": "iron_condor", "tier": "Tier 2", "...": "..." } ]
    }
  ],
  "ranked": [ "...": "same shape as selected symbols, sorted by best_score" ],
  "selected": ["AAPL"]
}
```

- **`strategies`** — the full result of running *every* registered strategy's own
  `apply_tiering()` against this one symbol, not just the winner. Useful for understanding *why*
  a symbol went to `iron_fly` instead of `iron_condor` on a given night — read both entries'
  `tier` and `criteria` side by side.
- **`best_strategy`** / **`best_score`** — the single highest-scoring Tier 1/2 strategy for this
  symbol. `null` if nothing on this symbol cleared any strategy's tiering.
- **`outcome`** / **`reason`** — human-readable summary of both the within-symbol strategy
  choice and the symbol's cross-symbol rank, or the rejection reason if it never got selected
  (`rejected_no_viable_strategy`, `concurrency_cap_reached`, `correlation_blocked`, etc.).

Every evaluated (symbol, strategy) pair, plus one `strategy = "_ranked"` summary row per symbol,
gets written to `scan_log` automatically — query the database directly if you want the full
trail for a past date rather than just tonight's console output.

---

## A Quiet Night Is Not a Bug

A day with zero Tier 1/2 candidates across every strategy usually just means nothing on that
day's earnings calendar had rich enough IV/RV, negative enough term structure, or high enough
liquidity to clear any strategy's hard filters. Read the specific `hard_fail_reasons` and
`near_miss_reasons` before assuming something's broken:

- `front_expiration_days_too_far_out` / `no_weekly_options` clustering together — a batch of
  small/mid-cap names that only have monthly option cycles. Expected by construction; see
  Screening Criteria's note on this.
- `term_structure_insufficient` — the front month's IV isn't inflated enough over the back
  month to suggest a real earnings-specific IV bump.
- `iv_rv_ratio_below_threshold` — the options aren't pricing in enough premium relative to the
  stock's own historical realized moves to be worth selling.

---

## Spot-Checking One Symbol By Hand

If a candidate's tier surprises you, pull the individual signals directly rather than trusting
the scan's summary line alone:

```bash
python src/scanner.py get_iv_rv --symbol AAPL
python src/scanner.py get_winrate --symbol AAPL --lookback_quarters 8
python src/tt.py get_option_chain --symbol AAPL --expiration 2026-07-17 --include_greeks --include_quotes --include_oi --include_volume
```

These are the exact live calls the scan itself makes — running them by hand reproduces the same
numbers `get_candidates` used, so you can confirm a rejection or tiering decision independently
instead of taking the scan's word for it.

---

## Navigation

**← Previous:** [Entry Conditions Framework](./04-entry-conditions.md)
**Next →** [Strategy Guide](./05-strategies.md)
