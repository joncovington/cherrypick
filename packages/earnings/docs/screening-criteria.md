# Screening Criteria

> _Part of the **cherrypick-earnings** package — [suite](../../../README.md) · [package README](../README.md) · [docs index](./README.md)._


Two layers, run in this order: **(1) universe hard filters** (cheap, run against every ticker on the day's earnings calendar to cut the list down fast) then **(2) entry-time re-verification** (run only against candidates that survive layer 1, right before submitting an order — see `CLAUDE.md`'s Step 4b). A candidate must clear both layers; layer 1 alone is not sufficient to trade on.

Before either layer there is the question of which tickers are on "the day's earnings calendar" at all — see below, because getting that wrong is invisible in every downstream report.

## Layer 0 — Which names reach the screen (`scanner.fetch_entry_window_calendar`)

The entry window is one afternoon: names reporting **after tonight's close**, plus names reporting **before the next trading session's open**. The second half is the next *trading* day, not `today + 1` — as a calendar day it landed on Saturday every Friday, a date with no calendar rows, so Friday afternoon could never see a Monday-morning reporter.

Which half a name falls in comes from Dolt's `earnings_calendar.when` column, and **that column's coverage is not reliable**. It ran ~90% populated through mid-July 2026 and had decayed to 0.4% by 2026-08-12 (247 of 248 rows NULL). Requiring an exact timing string therefore stopped meaning "this reports tonight" and started meaning "this row happens to be annotated": the nightly universe collapsed from ~50 names to one or two, and liquid names were dropped on their own earnings day — CSCO on 2026-08-12 (term structure -1.80, IV/RV 1.32, winrate 0.92 on 12 quarters, 3% net combo spread) never reached the screen at all.

So a row on the scan date whose `when` is blank is admitted and **read as after-the-close**, under two constraints:

- **Bounded by the morning forward scan.** Only symbols that pass's snapshot actually measured are admissible (`symbol_watch.covered_symbols`), which is already narrowed to tastytrade's liquid-symbol watchlist union. The raw calendar carries ~250 rows a night and the entry scan costs ~8s per symbol; admitting all of them would run ~33 minutes and finish past the entry window. With no snapshot, or a stale one, this degrades to exact-timing-only — never to an unbounded scan.
- **Assumed AMC, never assumed BMO.** A name that in fact reported before this morning's open is post-event: its front IV has collapsed and its term structure is out of backwardation, so hard filter #4 rejects it on live data. The assumption never reaches an accept decision unscreened. Guessing BMO would be worse than a miss — an unannotated row is only ever considered on its own earnings date, so that guess would enter a genuinely AMC name a session early and hold it through an exit check that fires before the event.

The broker is not an alternative source here, contrary to the suite's usual preference for tastytrade data over Dolt: `get_market_metrics`'s `earnings.time_of_day` was checked on 2026-08-12 and is `null` for CSCO, AMAT, JD, NU and CORZ, and stale where present (WULF carried a `time_of_day` against an `expected_report_date` nine days past). Its `expected_report_date` *is* reliable; only the time of day is missing.

Every row the entry scan sees carries a normalized `timing` plus a `timing_assumed` flag, and that flag is recorded on the `entry_reviews` row and marked in the EOD analysis table — a screening trail must not read as though the calendar stated a timing it did not.

## Layer 1 — Universe hard filters (no exceptions)

Run once per ticker per scan, in this order (cheapest/fastest-to-reject first, matching EarningsEdgeDetection's own performance-optimized ordering):

1. **Price ≥ $10.00** — sub-$10 names have degenerate option pricing (minimum tick sizes dominate the spread) and unreliable IV calculations.
2. **Front-month expiration ≤ 9 days from today** — keeps nearly all of the front leg's extrinsic value attributable to the earnings event itself, not generic time decay. A front-month further out dilutes the IV-crush signal with unrelated decay.
3. **Combined open interest ≥ 2,000 contracts** (calls + puts, front-month, all strikes) — liquidity floor; below this, the fill price on a 4-leg iron fly will not resemble the mid. **Shared engine logic** — `scanner.fetch_liquidity_criteria()`/`apply_liquidity_gates()`, not iron-fly-specific; every strategy applies the same check via its own `min_combined_open_interest`.
4. **Term structure ≤ -0.004** — `(front_atm_iv - back_atm_iv) / back_atm_iv`, computed via the shared `scanner.compute_expected_move_and_term_structure()` (strategy-agnostic calculation; each strategy still owns its own threshold and reads the result through its own `apply_tiering`). This is the core signal: front-month IV must be inflated relative to back-month by a real margin, not just "any negative number," or the trade has no edge over background IV noise.
5. **ATM delta ≤ 0.57 in absolute value** — sanity check that the strike selected as "ATM" for the term-structure calc actually is ATM; a delta this far from 0.50 means the strike grid is too coarse near the money for this name and the term-structure reading is unreliable.
6. **Expected move ≥ $0.90** (nearest expiration, dollar terms) — a straddle price below this is too cheap to be worth the transaction cost of a 4-leg order regardless of how attractive the ratios look.
7. **Full option chain must be fetchable and both front/back expirations must exist** — reject outright (not a soft fail) if the chain is incomplete; do not guess or substitute a different expiration.
7b. **Weekly options must exist** (`require_weekly_options` in config, default `true`; numbered 7b rather than renumbering #8–#10 below, which are referenced by number throughout this project) — checks the real expiration *cadence* (a gap of ≤10 days between two consecutive expirations somewhere in the chain), not just whether the nearest expiration happens to fall inside the `max_front_expiration_days` window. A monthly-only name's single nearest expiration can coincidentally land inside that window some weeks by luck of the calendar without the name actually having the liquid, frequent option cycle the strategy assumes. **Shared engine logic** (`scanner.has_weekly_options()`) — originally iron-fly-only, now applied by every strategy via `apply_liquidity_gates()`. Verified live 2026-07-06: EPAC/PENG/SAR (all monthly-only) correctly flagged `no_weekly_options`, distinct from and in addition to their separate `front_expiration_days_too_far_out` rejections.
7c. **Bid/ask spread ≤ 15% of mid** at the front-month ATM strikes (`max_bid_ask_spread_pct`) — a wide spread erodes edge on the very trade being screened for. **Shared engine logic**, same `apply_liquidity_gates()` as 7b — reasoned starting point, not backtested for any strategy yet.

## Layer 1 — Additional criteria (soft; screened at a user-configurable level)

The five criteria below — **average volume, winrate, IV/RV ratio, market cap, and combined option volume** — are *soft* in that you choose, per criterion, how strictly each is screened, via the top-level `symbol_screen` config block. Each of the five is independently set to one of three levels:

- **`"pass"`** (the default for all five): the candidate must clear the strict `min_*` threshold in `strategies.<name>` to be accepted.
- **`"near_miss"`**: the candidate only needs to clear the looser `near_miss_min_*` threshold — the configurable way to tolerate a marginal name on that one dimension instead of rejecting it. (This is the replacement for what used to be a fixed "near-miss band.")
- **`"off"`**: the criterion isn't screened at all.

Each criterion lists both its strict `min_*` and looser `near_miss_min_*` threshold below; which one is enforced — or whether it's screened at all — is set per criterion in `symbol_screen`. Unlike the hard filters (1–7c above, which always apply and never soften), these five are where you tune how strict the universe screen is.

8. **30-day average volume** — strict `min_avg_volume` 1,500,000 shares; looser `near_miss_min_avg_volume` 1,000,000.
9. **Winrate** (historical: % of past earnings where the option-implied move — ATM straddle mid on the nearest expiration on/after the earnings reaction — exceeded the actual realized move, target 8 quarters sample): strict `min_winrate` 50%; looser `near_miss_min_winrate` 40%. **Implemented** — `scanner.compute_winrate()` backtests against DoltHub's `post-no-preference/options.option_chain` (historical chains) and `post-no-preference/stocks.ohlcv` (realized moves). Verified live 2026-07-06: AAPL scored 7/7 (100%) over the available sample, but note the **real sample-size caveat** found during testing — `option_chain`'s historical depth only reaches back to roughly late 2024, so a "last 8 quarters" request against an older or less-covered symbol may return a materially smaller sample (5 of the requested 8 quarters were skipped for AAPL with `no_matching_option_chain_data`, all skips logged, not silently dropped). **Always check `sample_size` before trusting a winrate** — a 100% winrate on a 2-quarter sample is not the same claim as on 8.
10. **IV/RV ratio** — strict `min_iv_rv_ratio` 1.25; looser `near_miss_min_iv_rv_ratio` 1.00. **Implemented, tastytrade-first with a Dolt fallback**: the cheap universe pre-gate (`rank_strategies._dolt_only_hard_fails`, runs before any broker call) always uses `scanner.fetch_iv_rv_ratio()`'s Dolt `post-no-preference/options.volatility_history` `iv_current`/`hv_current` (falling back up to 5 trading days back if the most recent row has a null `iv_current`) — it must stay Dolt-only and broker-free by construction. Once a symbol clears that pre-gate and its chain is fetched, the *final* ratio used for tiering/scoring/ranking prefers the broker's own `implied_volatility_30_day`/`historical_volatility_30_day` (via `tt.py get_market_metrics`, already fetched for market cap/OI — zero extra round trip), falling back to the Dolt value only when the broker pair is unavailable. `criteria["iv_rv_source"]` ("tastytrade" or "dolt") is recorded on every `entry_reviews` row so which source decided a given day's ratio is always visible — this is what keeps a stale/unreachable Dolt `volatility_history` from silently degrading the *final* signal even though the pre-gate itself still depends on Dolt being reachable. Verified live 2026-07-06 against a real dual-database `dolt sql-server` (AAPL: IV/RV ≈ 0.84 as of 2026-07-02 — below the 1.25 strict threshold, and below the 1.00 near-miss threshold too, so rejected at either `symbol_screen` level for that name on that day).
11. **Market cap** (strict `min_market_cap` $2B / looser `near_miss_min_market_cap` $1B) and **combined front-month option volume** (strict `min_combined_option_volume` 500 / looser `near_miss_min_combined_option_volume` 200) — both **shared engine logic** (`scanner.fetch_liquidity_criteria()`/`apply_liquidity_gates()`), sourced from the tastytrade SDK directly (REST `get_market_metrics` for market cap; DXLink `Trade.day_volume` for option volume via `tt.py get_option_chain --include_volume`), not DoltHub. Reasoned starting points, not backtested for any strategy yet.

## What gets recorded about a rejection

A gate's name says which bar a candidate missed. It never said by how much, which is the only thing that distinguishes a threshold doing its job from a threshold that is mistuned — so every `scan_log` row now carries `reject_details`, a JSON array of `{reason, criterion, measured, threshold, comparator}` built by `scanner.explain_reject_reasons()`. The threshold recorded is the one that actually applied (a soft criterion at `near_miss` records the looser bar, not the strict one) and it is pinned as of that scan, so a later analysis is not silently re-deriving today's config against last month's decisions.

Rejections are also recorded **per stage**, because acceptance used to be the last thing written down — a candidate that cleared the screen and then failed order building, sizing, the risk cap or quote availability left no record of it. `scan_log.stage` is `prefilter` → `screen` → `execution`, so the full funnel — calendar row, morning pre-filter, accept/reject, then order build / sizing / risk cap / quote availability — reads off one table.

`python -m cherrypick.earnings.screen_report` reads all of this back: the funnel, which gates fired, which ones fired *alone* (the only rejections a threshold change can rescue), how far each fell short, and `--what-if REASON=THRESHOLD` for how many more names a different bar would have admitted. It counts candidates, never P&L — a name that was never traded has no outcome to attribute.

Two cautions for anyone analysing this data. The reason vocabulary drifts: `avg_volume_below_near_miss` has 1,036 historical rows and no producer in the source any more, so a reason the current map doesn't recognise is recorded as a bare name rather than dropped. And a `*_unverified` reason is not a screening result at all — it means the value could not be measured, which is a data-pipeline problem wearing a rejection's clothes. `iv_rv_ratio_unverified` (252) and `winrate_unverified` (240) should be read as coverage gaps, not as names that failed.

## The accept/reject decision

Screening is a single binary bar, not a graded ladder: a symbol is either **accepted** or **rejected**.

- `apply_tiering()` (in each `strategies/<name>.py`, since its thresholds are strategy-specific) returns `{"accepted": bool, "reject_reasons": [...]}`. A candidate is **accepted** — `accepted: true`, empty `reject_reasons` — when it clears every hard filter (1–7c) and each of the five soft criteria (8–11) at whatever level `symbol_screen` sets for it (a criterion set to `"off"` can never contribute a reason). Otherwise it's **rejected**, with one entry in `reject_reasons` per bar it failed. Only accepted candidates are eligible for entry (per `CLAUDE.md`).
- Every criterion (#1–#11) is computed from real, live data: `get_candidates` (in `strategies/iron_fly.py`) runs the full accept/reject scan across a day's calendar (`apply_tiering()`, also in `strategies/iron_fly.py`, holds the accept/reject logic, unit-tested against synthetic accepted/rejected/threshold-violation cases). Open interest comes from a live DXLink `Summary` event subscription (`tt.py`'s `--include_oi`, verified live) — note this contradicts a comment in MEIC's own code claiming OI has "no live fallback"; that turned out to describe an architecture choice MEIC made, not a real DXLink limitation.
- Verified live 2026-07-06 against a real day's calendar (4 tickers), with real credentials: all four legitimately rejected, each for a different, correctly-labeled reason — EPAC (OI 683 < 2,000 minimum, and its nearest expiration is 11 days out vs. the 9-day maximum), PENG (ATM delta 0.62 > 0.57 ceiling, same expiration-window issue), SAR (same expiration-window issue), KRUS (no listed option chain at all). Notably, **all three names with a chain shared the same expiration-window rejection** — these small/mid-caps only have monthly option cycles, so none can satisfy a `≤9-day` front-month window tuned assuming weekly-optioned liquid names. This is a real, useful finding about the strategy's current calibration, not a bug: `max_front_expiration_days` may need to be loosened (or a distinct calibration used) for less-liquid earnings candidates if they're meant to be tradeable at all, otherwise this filter alone may exclude most of the small-cap universe by construction.
- A low-`sample_size` winrate (see #9's caveat) is a further reason to treat even an accepted result with more skepticism than the bar alone conveys.
- **A real sign-convention bug was caught and fixed during this live testing**: the term-structure calculation (now `scanner.compute_expected_move_and_term_structure()`, originally iron_fly.py's own `compute_term_structure`) originally computed `(front_iv - back_iv) / back_iv`, which is *positive* when the front month is IV-richer than the back month — backwards from this doc's own `≤ -0.004` threshold (negative-is-good) and from the function's own docstring. A real earnings candidate (EPAC, front IV ~64% richer than back) was being rejected as `term_structure_insufficient` — exactly the opposite of the intended behavior. Fixed to `(back_iv - front_iv) / back_iv`, re-verified live: EPAC's term structure flipped from `+0.64` (wrongly rejected) to `-0.64` (correctly passes).
- A second bug caught in the same pass: front-month expiration was originally picked as "nearest expiration to today," ignoring the earnings date entirely — harmless for the four low-liquidity small-caps tested (they only had monthly expirations, so the nearest-to-today and nearest-to-reaction-date happened to coincide), but confirmed live against AAPL (which has weeklies including same-day expirations) that this would otherwise grab an irrelevant 0DTE chain. Fixed to select the nearest expiration on/after the earnings *reaction* date (next trading day for "After market close," the report day itself for "Before market open").

## Recorded-only metrics (not gates, unless noted)

Every screened symbol — accepted or rejected — has its full metric vector persisted to
`entry_reviews` (`db.py`/`db_paper.py`, see each module's own docstring) via
`scanner.build_entry_review_spec()`, whether the scan ran through `rank_strategies.py` (the
agent-driven live/paper path) or `strat_test_harness.py` (the automated forced-sampling paper
harness). Four of these metrics are new and, apart from `move_tail`, are **record-only** —
visible in `entry_reviews`/scout's earnings page for calibration, but not yet gating acceptance:

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
  was ≥ `move_tail_multiple` (default 2.0) times the mean). Unlike the three metrics above, this
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

- **IV/RV ratio and winrate are multiplicative adjustments to term structure, not independent additive scores** — they're secondary confirmations of the same "is IV overpriced" question term structure already answers, so a strong core signal should outrank two merely-average ones, which summing wouldn't achieve.
- **`shrunk_winrate`** pulls winrate toward a neutral 0.5 prior in proportion to how far `sample_size` is below 8 quarters (`0.5 + min(sample_size/8, 1) * (winrate - 0.5)`) — a 100% winrate on 1 quarter scores below a 60% winrate on 6 quarters, verified via unit test. This directly follows from the #9 sample-size caveat above: an unshrunk raw winrate would let a thin, noisy sample outrank a well-supported one.

`scanner.select_positions()` then walks the ranked list applying `max_concurrent_earnings_positions` and `correlation_block_list`, skipping (not silently dropping — each skip is reported with a reason) any candidate that collides with an already-selected name's correlation group, and backfilling the next-best candidate into that slot rather than leaving it empty. This diversifies across names instead of concentrating in whichever single candidate scores highest — earnings-move risk is idiosyncratic per name, and a composite score's precision doesn't warrant betting a whole position budget on the top-ranked name alone. Verified via unit test: a lower-ranked non-conflicting candidate correctly backfills a slot vacated by a correlation-blocked higher-ranked one.

## Wing width sizing (at order construction, `strategies/iron_fly.py`'s `fetch_iron_fly_order()`)

Wing width is `wing_multiple × straddle_credit`, where `wing_multiple` is chosen from a 3-tier scale keyed to **this candidate's own IV/RV ratio**, re-fetched fresh at entry time (not reused from the scan) — deliberately *not* a market-wide VIX-style band the way MEIC's delta-target scaling uses VIX. An earnings move is idiosyncratic to one stock; a single stock's own IV/RV ratio (already computed per-candidate from its own options and realized volatility) is the more relevant regime signal here than broad market volatility would be.

| IV/RV ratio | Wing multiple |
|---|---|
| `< wing_width_band_low_max` (1.25) | `wing_width_multiple_low` (2.5) — thin edge, less excess width paid for against a marginal signal |
| `< wing_width_band_mid_max` (1.75) | `wing_width_multiple_mid` (3.0) — the prior fixed default |
| `>= wing_width_band_mid_max` | `wing_width_multiple_high` (3.5) — strong edge, more protective margin the position can afford |

Verified live 2026-07-06: AAPL's real IV/RV ratio (0.84, below the low-band boundary) correctly selected the 2.5× multiple, fully transparent in `get_order`'s output (`iv_rv_ratio`, `wing_width_multiple_used`). Like the screening thresholds above, these band breakpoints are a reasoned proposal from the general logic of "size protection to edge strength," not independently backtested for this strategy — worth revisiting once enough live/paper trades exist to check whether the banding actually improves outcomes over the flat 3.0× multiple it replaces.

## Layer 2 — Entry-time re-verification (immediately before order submission, not at scan time)

The scan runs once in the afternoon; by the entry window near the close, prices/IV may have moved. Re-check, live, right before submitting:

- Term structure and expected move — re-pull the chain and recompute; reject if either has fallen out of range since the scan (`action: "entry_skip"`, `reason: "reverify_failed_term_structure"` / `"reverify_failed_expected_move"`).
- Earnings date/timing hasn't shifted (companies do reschedule) — reject if the calendar source's `when` field for this date no longer matches what was scanned.
- Liquidity hasn't degraded — re-check live bid/ask width and OI, not just the scan-time snapshot.
- **Position-level risk cap**: max loss (wing width − credit received) must be ≤ `max_risk_per_trade_pct` of account NLV — independent of and in addition to the scanner's own risk/reward ratio.
- **Correlation check**: reject if this candidate shares a `correlation_block_list` grouping with an already-open or already-entered-tonight position.

## Rationale for filter ordering

Cheapest/fastest checks run first (price, then expiration date, both simple lookups) so a large daily ticker universe rejects fast without spending time on expensive checks (full chain pull, term-structure calc) for names that were never going to qualify. This mirrors EarningsEdgeDetection's own "Filter Chain Ordering" performance note.
