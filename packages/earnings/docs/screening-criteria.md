# Screening Criteria

> _Part of the **cherrypick-earnings** package — [suite](../../../README.md) · [package README](../README.md) · [docs index](./README.md)._


Two layers, run in this order: **(1) universe hard filters** (cheap, run against every ticker on the day's earnings calendar to cut the list down fast) then **(2) entry-time re-verification** (run only against candidates that survive layer 1, right before an order is built). A candidate must clear both layers; layer 1 alone is not sufficient to trade on.

Before either layer there is the question of which tickers are on "the day's earnings calendar" at all — see below, because getting that wrong is invisible in every downstream report.

## Layer 0 — Which names reach the screen (`scanner.fetch_entry_window_calendar`)

The entry window is one afternoon: names reporting **after tonight's close**, plus names reporting **before the next trading session's open**. The second half is the next *trading* day, not `today + 1` — as a calendar day it landed on Saturday every Friday, a date with no calendar rows, so Friday afternoon could never see a Monday-morning reporter.

Which half a name falls in comes from Dolt's `earnings_calendar.when` column, and **that column's coverage is not reliable**: most rows are NULL. Requiring an exact timing string would collapse the nightly universe to a name or two and drop liquid names on their own earnings day, with nothing in any downstream report to show it.

So **every** row on the scan date whose `when` is blank is admitted and **read as after-the-close**:

- **Assumed AMC, never assumed BMO.** A name that in fact reported before this morning's open is post-event: its front IV has collapsed and its term structure is out of backwardation, so hard filter #4 rejects it on live data. The assumption never reaches an accept decision unscreened. Guessing BMO would be worse than a miss — an unannotated row is only ever considered on its own earnings date, so that guess would enter a genuinely AMC name a session early and hold it through an exit check that fires before the event.
- **Ordered, not bounded, by the morning forward scan.** The forward scan's snapshot (`symbol_watch.covered_symbols`) only orders the entry scan so that, if `entry_scan_budget_seconds` binds, the liquid names finish first. It never gates admission: a gate on a snapshot that comes back empty reads as "admit nothing" and silently drops most of a night's calendar, while the cost it would control is already bounded by `entry_scan_budget_seconds`. The entry scan also splits the calendar by the suite's liquidity verdict before screening: tradable names first, refused names only with the budget left (they are still recorded when reached; an unreached one reads `entry_scan_budget_exceeded`), and retired strategies run no screen.

The broker is not an alternative source here, contrary to the suite's usual preference for tastytrade data over Dolt: `get_market_metrics`'s `earnings.time_of_day` is mostly `null`, and stale where present. Its `expected_report_date` *is* reliable; only the time of day is missing.

Every row the entry scan sees carries a normalised `timing` plus a `timing_assumed` flag, and that flag is recorded on the `entry_reviews` row and shown on the console's Earnings page — a screening trail must not read as though the calendar stated a timing it did not.

## Layer 1 — Universe hard filters (no exceptions)

Run once per ticker per scan, in this order (cheapest/fastest-to-reject first):

1. **Price ≥ $10.00** — sub-$10 names have degenerate option pricing (minimum tick sizes dominate the spread) and unreliable IV calculations.
2. **Front-month expiration ≤ 9 days from today** — keeps nearly all of the front leg's extrinsic value attributable to the earnings event itself, not generic time decay. A front-month further out dilutes the IV-crush signal with unrelated decay.
3. **Combined open interest ≥ 2,000 contracts** (calls + puts, front-month, all strikes) — liquidity floor; below this, the fill price on a 4-leg iron fly will not resemble the mid. **Shared engine logic** — `scanner.fetch_liquidity_criteria()`/`apply_liquidity_gates()`, not iron-fly-specific; every strategy applies the same check via its own `min_combined_open_interest`.
4. **Term structure ≤ -0.004** — `(back_atm_iv - front_atm_iv) / back_atm_iv`, computed via the shared `scanner.compute_expected_move_and_term_structure()` (strategy-agnostic calculation; each strategy still owns its own threshold and reads the result through its own `apply_tiering`). This is a **ratio** of the IV gap to the back IV, not a slope: it does not divide by the days between the two expirations. The -0.004 threshold comes from a per-day-slope definition, `(back_iv - front_iv) / (back_dte - front_dte)`, where -0.004 per day over a few weeks is a real gap. Applied to the ratio, -0.004 is 0.4% of back IV, which almost any backwardation clears, so the gate binds far less than its source implies and should not be read as evidence of a margin. Each entry review records the inputs (`front_iv`, `back_iv`, `front_dte`, `back_dte`, `front_expiration`, `back_expiration`; NULL before 2026-11-05) so the per-day slope can be replayed beside the ratio.
5. **ATM delta ≤ 0.57 in absolute value** — sanity check that the strike selected as "ATM" for the term-structure calc actually is ATM; a delta this far from 0.50 means the strike grid is too coarse near the money for this name and the term-structure reading is unreliable.
6. **Expected move ≥ $0.90** (dollar terms) — a move below this is too cheap to be worth the transaction cost of a 4-leg order regardless of how attractive the ratios look. **The expected move is the report's own move, not the front straddle's** (from 2026-11-06, a measurement boundary: read results by `expected_move_method`). The front expiry can sit up to nine days after the report, so its straddle carries ordinary time value as well as the event. `scanner.resolve_expected_move()` separates the two from the front and back ATM IVs the term-structure gate already reads (the back is the first expiry at least 21 days past the front, so both follow the report). With vols σf, σb at T_f, T_b years (calendar days / 365): ex-event variance rate σx² = (σb²·T_b − σf²·T_f) / (T_b − T_f); event variance E² = σf²·T_f − σx²·T_f; E is the event's one-SD move as a share of spot. The expected move is `0.85 × sqrt(2/π) × E × spot`, i.e. 0.85 times the straddle the event alone would price (a straddle on a normal move is worth sqrt(2/π) of one SD). That keeps it in the units the floors and strike placement were set in; the 1-SD dollars ride separately as `event_move_sd_dollars`. Note that 0.85 × straddle is about 0.68 SD, not 1 SD. Worked example: spot 100, σf 0.80 at 3 days, σb 0.45 at 31 days gives σx 39.4%, E 6.31%, a one-SD move of $6.31 and an expected move of $4.28, against $4.92 from the front straddle the same vol implies. It falls back to `0.85 × front straddle` when an input is missing (`missing_inputs`) or the split is invalid (`bad_inputs`: T_b not above T_f; `back_variance_not_above_front`: σx² ≤ 0; `no_event_premium`: E² ≤ 0). Recorded on `criteria_json`, `entry_reviews` and `entry_context`: `expected_move_method` (`event_variance` or `straddle_fallback`), `expected_move_reason`, `expected_move_straddle` and `expected_move_event` (dollars, side by side), `event_base_vol`. The top-level config key `expected_move_method: "straddle"` selects the old definition outright (item 31's study sets it). The winrate backtest (screen 9) and `broken_wing_butterfly` (retired) still use the straddle.
7. **Full option chain must be fetchable and both front/back expirations must exist** — reject outright (not a soft fail) if the chain is incomplete; do not guess or substitute a different expiration.
7b. **Weekly options must exist** (`require_weekly_options` in config, default `true`; numbered 7b rather than renumbering #8–#10 below, which are referenced by number throughout this project) — checks the real expiration *cadence* (a gap of ≤10 days between two consecutive expirations somewhere in the chain), not just whether the nearest expiration happens to fall inside the `max_front_expiration_days` window. A monthly-only name's single nearest expiration can coincidentally land inside that window some weeks by luck of the calendar without the name actually having the liquid, frequent option cycle the strategy assumes. **Shared engine logic** (`scanner.has_weekly_options()`) — applied by every strategy via `apply_liquidity_gates()`. A monthly-only name is flagged `no_weekly_options`, separately from any `front_expiration_days_too_far_out` rejection.
7c. **Bid/ask spread ≤ 15% of mid** at the front-month ATM strikes (`max_bid_ask_spread_pct`) — a wide spread erodes edge on the very trade being screened for. **Shared engine logic**, same `apply_liquidity_gates()` as 7b — a reasoned starting point, not backtested for any strategy.

## Layer 1 — Additional criteria (soft; screened at a user-configurable level)

The five criteria below — **average volume, winrate, IV/RV ratio, market cap, and combined option volume** — are *soft* in that you choose, per criterion, how strictly each is screened, via the top-level `symbol_screen` config block. Each of the five is independently set to one of three levels:

- **`"pass"`** (the default for all five): the candidate must clear the strict `min_*` threshold in `strategies.<name>` to be accepted.
- **`"near_miss"`**: the candidate only needs to clear the looser `near_miss_min_*` threshold — the configurable way to tolerate a marginal name on that one dimension instead of rejecting it.
- **`"off"`**: the criterion isn't screened at all.

Each criterion lists both its strict `min_*` and looser `near_miss_min_*` threshold below; which one is enforced — or whether it's screened at all — is set per criterion in `symbol_screen`. Unlike the hard filters (1–7c above, which always apply and never soften), these five are where you tune how strict the universe screen is.

8. **30-day average volume** — strict `min_avg_volume` 1,500,000 shares; looser `near_miss_min_avg_volume` 1,000,000.
9. **Winrate** (historical: % of past earnings where the option-implied expected move — the ATM straddle mid on the nearest expiration on/after the earnings reaction, times the shared 0.85 straddle-to-expected-move factor — exceeded the actual realized move, over `winrate_lookback_quarters`, 12 in the shipped config): strict `min_winrate` 50%; looser `near_miss_min_winrate` 40%. `scanner.compute_winrate()` backtests against DoltHub's `post-no-preference/options.option_chain` (historical chains) and `post-no-preference/stocks.ohlcv` (realized moves). **Sample-size caveat:** `option_chain`'s historical depth only reaches back to roughly late 2024, so a request for several quarters against an older or less-covered symbol may return a materially smaller sample (the skipped quarters are logged as `no_matching_option_chain_data`, not silently dropped). The backtest requires the pre-close and reaction close to be the exact sessions either side of the report (else the quarter is a `gap`), scores a quarter with NULL report time both ways and counts it only when BMO and AMC agree (else `timing_unknown`), and reports its skip counts (`gap`, `no_matching_option_chain_data`, `timing_unknown`) as `winrate_skips` in `criteria_json`. Winrates recorded before 2026-11-05 used the full straddle and a nearest-close lookup; do not pool them. One bias stays: the straddle is on the first expiry on or after the reaction day, which can carry up to nine more days of time value than a next-day expiry; the data gives no cleaner choice, and `expiration_used` records it per quarter. **Always check `sample_size` before trusting a winrate** — a 100% winrate on a 2-quarter sample is not the same claim as on 8.
10. **IV/RV ratio** — strict `min_iv_rv_ratio` 1.25; looser `near_miss_min_iv_rv_ratio` 1.00. **Tastytrade-first with a Dolt fallback**: the cheap universe pre-gate (`rank_strategies._dolt_only_hard_fails`, runs before any broker call) always uses `scanner.fetch_iv_rv_ratio()`'s Dolt `post-no-preference/options.volatility_history` `iv_current`/`hv_current` (falling back up to 5 trading days back if the most recent row has a null `iv_current`) — it must stay Dolt-only and broker-free by construction. Once a symbol clears that pre-gate and its chain is fetched, the *final* ratio used for tiering/scoring/ranking prefers the broker's own `implied_volatility_30_day`/`historical_volatility_30_day` (via `tt.py get_market_metrics`, already fetched for market cap/OI — zero extra round trip), falling back to the Dolt value only when the broker pair is unavailable. `criteria["iv_rv_source"]` ("tastytrade" or "dolt") is recorded on every `entry_reviews` row so which source decided a given day's ratio is always visible — this is what keeps a stale/unreachable Dolt `volatility_history` from silently degrading the *final* signal even though the pre-gate itself still depends on Dolt being reachable.
11. **Market cap** (strict `min_market_cap` $2B / looser `near_miss_min_market_cap` $1B) and **combined front-month option volume** (strict `min_combined_option_volume` 500 / looser `near_miss_min_combined_option_volume` 200) — both **shared engine logic** (`scanner.fetch_liquidity_criteria()`/`apply_liquidity_gates()`), sourced from the tastytrade SDK directly (REST `get_market_metrics` for market cap; DXLink `Trade.day_volume` for option volume via `tt.py get_option_chain --include_volume`), not DoltHub. Reasoned starting points, not backtested for any strategy.

## What gets recorded about a rejection

A gate's name says which bar a candidate missed, not by how much, and by how much is what distinguishes a threshold doing its job from one that is mistuned. So every `scan_log` row carries `reject_details`, a JSON array of `{reason, criterion, measured, threshold, comparator}` built by `scanner.explain_reject_reasons()`. The threshold recorded is the one that actually applied (a soft criterion at `near_miss` records the looser bar, not the strict one) and it is pinned as of that scan, so a later analysis is not silently re-deriving today's config against last month's decisions.

Rejections are also recorded **per stage**, so a candidate that cleared the screen and then failed order building, sizing, the risk cap or quote availability still leaves a record. `scan_log.stage` is `prefilter` → `screen` → `execution`, so the full funnel — calendar row, morning pre-filter, accept/reject, then order build / sizing / risk cap / quote availability — reads off one table.

`python -m cherrypick.earnings.screen_report` reads all of this back: the funnel, which gates fired, which ones fired *alone* (the only rejections a threshold change can rescue), how far each fell short, and `--what-if REASON=THRESHOLD` for how many more names a different bar would have admitted. It counts candidates, never P&L — a name that was never traded has no outcome to attribute.

Two cautions for anyone analysing this data. The reason vocabulary drifts: older rows hold reasons the current map doesn't recognise, which are recorded as bare names rather than dropped. And a `*_unverified` reason is not a screening result at all — it means the value could not be measured, which is a data-pipeline problem wearing a rejection's clothes. `iv_rv_ratio_unverified` and `winrate_unverified` should be read as coverage gaps, not as names that failed.

## The accept/reject decision

Screening is a single binary bar, not a graded ladder: a symbol is either **accepted** or **rejected**.

- `apply_tiering()` (in each `strategies/<name>.py`, since its thresholds are strategy-specific) returns `{"accepted": bool, "reject_reasons": [...]}`. A candidate is **accepted** — `accepted: true`, empty `reject_reasons` — when it clears every hard filter (1–7c) and each of the five soft criteria (8–11) at whatever level `symbol_screen` sets for it (a criterion set to `"off"` can never contribute a reason). Otherwise it's **rejected**, with one entry in `reject_reasons` per bar it failed. Only accepted candidates are eligible for entry (per `CLAUDE.md`).
- Every criterion (#1–#11) is computed from real, live data: `get_candidates` (in `strategies/iron_fly.py`) runs the full accept/reject scan across a day's calendar (`apply_tiering()`, also in `strategies/iron_fly.py`, holds the accept/reject logic, unit-tested against synthetic accepted/rejected/threshold-violation cases). Open interest comes from a live DXLink `Summary` event subscription (`tt.py`'s `--include_oi`, verified live).
- Small and mid-cap names that only have monthly option cycles cannot satisfy the front-month window (`max_front_expiration_days`), which is tuned for weekly-optioned liquid names; that filter alone excludes most of that universe by construction.
- A low-`sample_size` winrate (see #9's caveat) is a further reason to treat even an accepted result with more skepticism than the bar alone conveys.
- The front-month expiration is the nearest one on or after the earnings *reaction* date (the next trading day for "After market close", the report day itself for "Before market open"), never the nearest to today, which would pick an irrelevant 0DTE chain.

## Recorded-only metrics (not gates, unless noted)

Every screened symbol — accepted or rejected — has its full metric vector persisted to
`entry_reviews` (`db.py`/`db_paper.py`, see each module's own docstring) via
`scanner.build_entry_review_spec()`, whether the scan ran through `rank_strategies.py` (the
production ranking) or `strat_test_harness.py` (the forced-sampling paper harness). Apart from
`move_tail`, these metrics are **record-only** — visible in `entry_reviews` and the console's Earnings page
for calibration, but not gating acceptance:

- **Implied vs. historical move** — `criteria["expected_move_pct"]` (this scan's option-implied
  move) divided by `avg_actual_move_pct` (the mean of the same `winrate_lookback_quarters`
  quarters' *actual* post-earnings moves, from `scanner.compute_historical_move_stats()` — reuses
  `compute_winrate()`'s already-fetched quarters, no extra Dolt walk). Recorded as
  `implied_vs_avg_actual`; a value well above 1.0 says the market is pricing a bigger move than
  this name has historically delivered, the core edge premium-selling strategies are targeting.
  `move_dispersion_pct` (standard deviation of the same moves) and `max_actual_move_pct` ride
  along — a name that averages a tame move but once printed a blowout is a different risk profile
  than one that is consistently tight, information the mean alone hides.
- **Move-history tail flag** — `move_tail_veto` (true if any of the lookback quarters' actual move
  was ≥ `move_tail_multiple` (default 2.0) times the mean). Unlike the other metrics here, this
  one **can** gate: `symbol_screen.move_tail` is `"off"` (record-only, default) or `"veto"`
  (rejects with reason `move_tail_veto`) — off by default until the recorded `entry_reviews` data
  supports calibrating the multiple.
- **Bid-ask spread quality** — `net_combo_spread_pct` (`scanner.compute_spread_quality()`,
  `sum(ask-bid)/sum(mid)` across the front-month ATM call/put already fetched for
  `bid_ask_spread_pct`) — the combined round-trip cost of trading both legs together, which
  matters more than any single leg's spread (already a hard filter, #7c) once several legs must
  all fill.
- **IV rank / percentile** — `iv_rank`/`iv_percentile` from the same `get_market_metrics` call
  used for market cap and the tastytrade IV/RV ratio above — a secondary richness signal (where
  this name's current IV sits in its own 52-week range), lower-signal than the implied-vs-actual
  comparison specifically around an earnings event, so not screened.

## Ranking and position selection (after screening, before entry)

The accept/reject bar alone doesn't answer "which of several accepted candidates do we actually trade" on a day with more accepted names than `max_concurrent_earnings_positions` allows. `scanner.rank_candidates()` scores every accepted candidate (rejected names are excluded from ranking entirely — a score doesn't make a name that failed a screening bar viable) via `compute_composite_score()`:

```
score = abs(term_structure) * iv_rv_ratio * shrunk_winrate
```

A strategy with no term-structure reading uses `skew_abs` in its place (`broken_wing_butterfly`).
One with neither (`expected_move_butterfly`) has no score and is never ranked.

- **IV/RV ratio and winrate are multiplicative adjustments to term structure, not independent additive scores** — they're secondary confirmations of the same "is IV overpriced" question term structure already answers, so a strong core signal should outrank two merely-average ones, which summing wouldn't achieve.
- **`shrunk_winrate`** pulls winrate toward a neutral 0.5 prior in proportion to how far `sample_size` is below 8 quarters (`0.5 + min(sample_size/8, 1) * (winrate - 0.5)`) — a 100% winrate on 1 quarter scores below a 60% winrate on 6 quarters, verified via unit test. This directly follows from the #9 sample-size caveat above: an unshrunk raw winrate would let a thin, noisy sample outrank a well-supported one.

`scanner.select_positions()` then walks the ranked list applying `max_concurrent_earnings_positions` and `correlation_block_list`, skipping (not silently dropping — each skip is reported with a reason) any candidate that collides with an already-selected name's correlation group, and backfilling the next-best candidate into that slot rather than leaving it empty. This diversifies across names instead of concentrating in whichever single candidate scores highest — earnings-move risk is idiosyncratic per name, and a composite score's precision doesn't warrant betting a whole position budget on the top-ranked name alone. Verified via unit test: a lower-ranked non-conflicting candidate correctly backfills a slot vacated by a correlation-blocked higher-ranked one.

## Wing width sizing (at order construction, `strategies/iron_fly.py`'s `fetch_iron_fly_order()`)

Wing width is `wing_multiple × straddle_credit`, where `wing_multiple` is chosen from a 3-tier scale keyed to **this candidate's own IV/RV ratio**, re-fetched fresh at entry time (not reused from the scan) — deliberately not a market-wide VIX-style band. An earnings move is idiosyncratic to one stock; a single stock's own IV/RV ratio (already computed per-candidate from its own options and realized volatility) is the more relevant regime signal here than broad market volatility would be.

| IV/RV ratio | Wing multiple |
|---|---|
| `< wing_width_band_low_max` (1.25) | `wing_width_multiple_low` (2.5) — thin edge, less excess width paid for against a marginal signal |
| `< wing_width_band_mid_max` (1.75) | `wing_width_multiple_mid` (3.0) — the prior fixed default |
| `>= wing_width_band_mid_max` | `wing_width_multiple_high` (3.5) — strong edge, more protective margin the position can afford |

`get_order`'s output shows `iv_rv_ratio` and `wing_width_multiple_used`. Like the screening thresholds above, these band breakpoints are a reasoned proposal ("size protection to edge strength"), not backtested.

## Layer 2 — Entry-time re-verification (immediately before order submission, not at scan time)

The scan runs once in the afternoon; by the entry window near the close, prices/IV may have moved. Re-check, live, right before submitting:

- Term structure and expected move — re-pull the chain and recompute; reject if either has fallen out of range since the scan (`action: "entry_skip"`, `reason: "reverify_failed_<reason>"`, e.g. `reverify_failed_term_structure_insufficient`).
- Earnings date/timing hasn't shifted (companies do reschedule) — reject if the calendar source's `when` field for this date no longer matches what was scanned.
- Liquidity hasn't degraded — re-check live bid/ask width and OI, not just the scan-time snapshot.
- **Position-level risk cap** (ranked path): max loss (wing width − credit received) must be ≤ `max_risk_per_trade_pct` of account NLV — independent of and in addition to the scanner's own risk/reward ratio.
- **Correlation check**: reject if this candidate shares a `correlation_block_list` grouping with an already-open or already-entered-tonight position.

## Rationale for filter ordering

Cheapest/fastest checks run first (price, then expiration date, both simple lookups) so a large daily ticker universe rejects fast without spending time on expensive checks (full chain pull, term-structure calc) for names that were never going to qualify.

## Replaying a screen

Entry screening is judged on the read side. An advised twin cannot express an entry-side change: a looser
screen produces a position the control does not hold, so nothing pairs. So the forced-sampling harness
trades a deliberately wide set, `entry_reviews` records the full metric vector for every name reviewed,
and `python -m cherrypick.earnings.entry_replay --screen "winrate=off,iv_rv_ratio=near_miss"` scores a
proposed screen over recorded outcomes. A replay is only as wide as the book beneath it: a screen can be
loosened in replay only if the book actually traded the names the looser screen would admit.

Gates fall into three kinds, and only the middle kind is a candidate for replay:

- **Measurement preconditions stay hard entry gates:** `no_listed_options`, `no_weekly_options`,
  `bid_ask_spread_too_wide`, `combined_open_interest_below_minimum`, `chain_complete_unverified`,
  `avg_volume`, `combined_option_volume`, `price_below_minimum`. They decide whether a recorded fill is a
  number or a fiction: a paper fill at the mid of a 40%-wide market is a trade that could never have
  happened, and no replay can undo what it does to every statistic downstream.
- **Edge opinions are recorded covariates.** `iv_rv_ratio`, `winrate` and `market_cap` are switchable to
  `"off"` through `symbol_screen`; `term_structure_insufficient`, `expected_move_pct_below_minimum`,
  `realized_move_too_inconsistent`, `insufficient_skew_signal` and `move_tail_veto` are hardcoded in each
  strategy and are not switchable. Each is a hypothesis about which trades are good.
- **Strategy-applicability gates stay hard:** `atm_delta_abs_above_maximum` and
  `front_expiration_days_too_far_out` answer whether the structure is constructible for the name, not
  whether the trade is good.
- `*_unverified` stays a rejection and stays a coverage gap, never a screening result, in any replay.

**Honesty rule.** The replay's `measured` block carries returns; its `counterfactual` block carries no
P&L key at all and cannot grow one by accident. Names outside the traded bar (micro-caps, anything the
liquidity preconditions dropped) are counts and symbols only. Each result states which side it came
from.

**Fidelity.** The replay calls the scanner's own `apply_soft_criteria`, so it answers what the scanner
would have done. The `symbol_screen` levels in force are recovered per date from `measurement_breaks`;
the `min_*` thresholds are not journalled, so today's apply to every date. `validate()` reproduces the
recorded decisions and every `replay()` result carries its `validation` block with a `verified_from`
date.

The forced-sampling book never respects `max_concurrent_earnings_positions` or the correlation block
list and sizes every trade at one contract with no risk cap. It holds correlated overnight risk by
design, which is acceptable in paper at that size and must not be inherited by a posture that places
orders. The ranked path (`rank_strategies`) still applies the cap and the block list and picks one
strategy per name.
