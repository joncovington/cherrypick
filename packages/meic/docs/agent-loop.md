# MEIC agent loop

Moved from the meic CLAUDE.md. The operating instructions for interactive `/meic-start` sessions:
the `tt` command table, then the Loop Steps. The rules in CLAUDE.md apply throughout.

## `tt` command table

| Command | Purpose | Requires live trading? |
|---|---|---|
| `python -m cherrypick.meic.tt get_connection_status` | Verify OAuth session and account access | No |
| `python -m cherrypick.meic.tt get_market_overview --symbols XSP` | IV rank, underlying price, market summary | No |
| `python -m cherrypick.meic.tt get_transactions --underlyings GLD --since 2026-01-01` | Trade history per underlying, GET only -- the order chains the daily position evaluation (packages/manage) judges | No |
| `python -m cherrypick.meic.fee_reconcile [--date D] [--symbol S] [--apply]` | Settled LIVE rows' fees against the broker's: trade lines by the row's own order ids, settlement lines per contract on the sides `ic_spread_legs` records `expired`. Dry-run reports; `--apply` writes `fees`/`settlement_fees` and keeps the model once in `fee_reconciliations`. The gross is reported, never written (it is split per side in `ic_spread_legs`). The supervisor's `meic-fee-reconcile` job runs it with `--apply` daily, armed or not | No |
| `python -m cherrypick.meic.live_loop --status \| --once [--live] \| --settle-overdue \| --settle --date D --price P [--symbol S] \| --install-task \| --uninstall-task` | The deterministic live loop: its state with no broker read (`--status`); one tick, dry-run unless `--live`; past sessions settled at their own official close; arm or disarm today (the `/live-meic-start` path) | `--live`: Yes |
| `python -m cherrypick.meic.tt get_quote --symbol XSP` | Last trade price (stream cache → DXLink fallback) | No |
| `python -m cherrypick.meic.tt get_vix1d` | Live VIX1D via direct DXLink fetch — feeds the `vix1d_ratio` trigger (Step 4) | No |
| `python -m cherrypick.meic.tt get_calendar [--year N]` | Shared market calendar from `cherrypick.core.calendar`: `nyse_holidays`, `fomc_dates` (+ `fomc_year_known`), `quarterly_expiry_dates`, `triple_witching_dates`. No broker. | No |
| `python -m cherrypick.meic.tt get_option_chain --symbol XSP [--expiration DATE] [--include_greeks] [--include_quotes] [--strike_count N] [--around_price F]` | Option chain with optional live greeks/quotes | No |
| `python -m cherrypick.meic.tt get_strategies --symbol XSP [--target_dte N] [--wing_width N] [--short_delta F] [--around_price F]` | IC candidate with POP estimate and credit | No |
| `python -m cherrypick.meic.gate_health [--symbols SPX,QQQ] [--json]` | **Which regime gates are armed now** — read-only over the stream cache. Gates fail OPEN, so this is how you see GEX/ATR/intraday-range have stood down; ATR reports sessions still missing after a streamer outage. Never changes the loop. | No |
| `python -m cherrypick.meic.tt get_gex --symbol XSP [--strike_count N] [--around_price F]` | GEX profile: net_gex, gamma_flip, call_wall, put_wall, per-strike. Needs the streamer (OI from Summary events). | No |
| `python -m cherrypick.meic.tt get_account_info` | Buying power, NLV, balances | No |
| `python -m cherrypick.meic.tt get_positions` | Open positions detail | No |
| `python -m cherrypick.meic.tt get_working_orders` | Live/unfilled orders | No |
| `python -m cherrypick.meic.tt get_quotes --symbols .APO260918C120 ...` | Bid/ask/mid for specific streamer symbols **without writing the shared cache** — for symbols no module declared, where seeding the cache would leave rows nothing refreshes | No |
| `python -m cherrypick.meic.tt get_greeks --symbols .APO260918C120 ...` | The broker's per-share greeks for specific streamer symbols, under the same no-cache-write rule (the manage snapshot's check against the broker's model) | No |
| `python -m cherrypick.meic.tt list_accounts` | Account numbers | No |
| `python -m cherrypick.meic.tt execute_trade --order '<JSON>'` | Dry-run validate an order (default) | No |
| `python -m cherrypick.meic.tt execute_trade --order '<JSON>' --live` | Place a live order | Yes |
| `python -m cherrypick.meic.tt adjust_order --order_id N --order '<JSON>' --live` | Replace a working order | Yes |
| `python -m cherrypick.meic.tt close_position --order_id N` | Cancel a working order by ID | Yes |
| `python -m cherrypick.meic.tt stream_status` | Streamer health and cache stats | No |
| `python -m cherrypick.meic.live_smoke [--symbol XSP] [--wing_width N] [--yes]` (or `.\src\live_smoke.ps1`, which adds market-hours / governor / `enable_live_trading` advisories) | **User-supervised dry-run smoke of the core.broker write path** — the gate before any live loop. Builds a real 0DTE IC, prints the order, requires typing DRY-RUN, then preflights via `execute_trade` **without** `--live` (real auth/margin/buying-power/governor; nothing placed; no live code path; `enable_live_trading` stays false). PASS/FAIL plus a manual broker-UI checklist. Run in regular hours on a trading day. | No |
| `python -m cherrypick.meic.tt stream_subscribe --symbols .XSP260630C745 ...` | Warm the cache for specific symbols immediately | No |

**Note**: `close_position` cancels a *working order*. To flatten an open position, use `execute_trade
--live` with closing actions (Buy to Close / Sell to Close).

---

## Loop Steps

1. **Load state** — open trades (all symbols), today's trade count and P&L, current ET time. Use
   `daily_ic_trade_target` to guide selectivity, never as a block; buying power (Step 4) binds.

2. **Time gate** — outside 09:30–15:55 ET, in pre-market (08:00–09:29), on a weekend or an NYSE holiday
   (`tt get_calendar` → `nyse_holidays`): skip Steps 3–7 and go to Step 8. **End of day, by settlement
   type** (no profit-target close): a position whose symbol is **not** in `cash_settled_symbols` is
   closed (BTC full IC) at or after `physical_settlement_force_close_time` (15:30, backstop 15:45);
   cash-settled positions are **left to expire** — never force-closed at EOD — and reconciled next
   session. (Event force-closes — FOMC 13:30, triple-witching/quarterly 14:00 — still close every
   symbol; Step 5.) **Assignment-risk escalation** (non-cash-settled only): a failed force-close past
   its deadline is **critical** — physically settled 0DTE options can be assigned, even early when deep
   ITM. Retry immediately with a marketable limit (cross the spread if necessary) and log `CRITICAL` if
   still open.

3. **Daily connection check** — `/daily-check`, once per trading day, account-wide.

4. **Market assessment** — an account-wide pass, then per symbol (each immediately followed by that
   symbol's Steps 6–7).

   **4a. Account-wide (once per iteration):**
   - Confirm the connection.
   - **Streamer cache health:** `tt stream_status`. `running`/`pid` are the standalone producer's
     (`producer`: `"standalone"`, or `null` when it is down). If `stale_warning` (no event in >10 min, or ever),
     **distrust cached quotes/greeks/OI for every symbol this iteration**, log `stale_reason`, and rely
     on `tt`'s direct DXLink reads — a silently dead connection once caused a 34+ hour outage.
   - Fetch buying power, NLV, working orders, open positions (account-wide calls).
   - **Halt entries for every symbol if NLV is down more than 5%** on yesterday.
   - Reconcile broker positions against the DB (read-only; surface mismatches for human review).
   - Fetch VIX once (`get_market_overview`), and VIX1D once (`tt get_vix1d`, a direct DXLink Trade
     subscription); compute `vix1d_ratio = vix1d / vix`. If VIX1D fails, skip the ratio trigger this
     iteration rather than blocking.

   **4b. Per symbol (in `symbols` order):**
   - **Timing start:** capture ms (`python -c "import time; print(int(time.time()*1000))"`); logged
     after Step 7.
   - **Re-check global caps** (buying power, `max_concurrent_ics`) against current values — an earlier
     symbol may have used the last slot. If exhausted, do no entry evaluation for this symbol.
   - IV rank and underlying price.
   - **Delta target by VIX band** for `--short_delta`: 0.16 (VIX ≤18), 0.14 (≤25), 0.12 (≤35), 0.10
     (above); `delta_target` (0.18) only if VIX is missing. Moving the target, not just the OTM floors,
     is what avoids trading one hard-stop failure (OTM distance) for another (credit floor) on low-VIX
     days. The high/crisis bands mostly matter for ORB strikes, since VIX >25 already pauses ICs.
   - Fetch the chain.
   - **Choose a width shortlist** up to `max_wing_width`, drop widths buying power can't carry, pick by
     session, IV rank, skew, gamma and existing positions. **Fee-drag bias:** fees are fixed per
     contract (`get_fee_estimate` gives `avg_fee_per_contract` and `fallback_per_contract`), so lean
     wide — SPX ≥5-wide (drag <10% vs >20% at 1-wide), XSP ≥2-wide (1-wide runs 18–30%). Session,
     buying power and gamma still override.
   - Classify session (open volatile / prime / midday / afternoon / late — same for every symbol), IV
     skew and price action (bearish / bullish / neutral).
   - **Regime detection:** `trending_regime = true` for this symbol if VIX > 25 OR `vix1d_ratio` >
     `regime_vix1d_ratio_pause_threshold` (1.30) OR this symbol's `atr_5day / underlying_price` >
     0.015. IC entries pause for this symbol; ORB stays eligible. Log each triggering metric distinctly
     (`vix_elevated` / `vix1d_ratio_elevated` / `atr_elevated`). VIX and the ratio are market-wide; ATR
     is per symbol. The 1.30 ratio is convention, not backtested — watch its trigger rate before
     trusting it like VIX/ATR.
   - **GEX regime check** (`tt get_gex --symbol <SYM>`, needs the streamer): if `ok`, (a)
     `gex_positive` false adds `gex_negative` to this symbol's regime flags — IC entries blocked; (b)
     record `call_wall`, `put_wall`, `gamma_flip` for Steps 5–6. If not `ok`, warn and proceed without
     GEX for this symbol — **never block on missing GEX alone**. **Zero-gamma threat:** positive but
     within 0.3% of the flip — don't block, but tighten this symbol's open ICs toward 0.85.
   - **ORB range** (if `orb_enabled`): `tt get_orb_range --symbol <SYM>`. The streamer captures the
     range from live Trades regardless of loop timing. If `ok: false`, skip ORB for this symbol and log
     `action: "orb_skip"` with `reason: "pre_range_window"` or `"not_captured"` so the skip is
     auditable.
   - Then run Steps 6–7 for this symbol before moving on. Never batch all assessments first — caps
     change between symbols.

5. **Stop management** — time it (ms before and after `/stop-management`). Runs every iteration over
   **all open trades on every symbol**, each using its own symbol's fees, floors and settlement type.
   Priority per trade (no profit-target close): (1) per-side software stop when a side's cost reaches
   the trigger; (2) stop-tightening evaluation; (3) event force-close (FOMC 13:30, triple-witching/
   quarterly 14:00 — every symbol) and a discretionary post-15:00 gamma safety close; (4) EOD by
   settlement type (non-cash force-closed by 15:30, backstop 15:45; cash-settled left to expire at
   16:00). tastytrade does not support exchange multi-leg stops on combos, so software monitoring at
   the 120 s open-position cadence is the only mechanism. Log `python -m cherrypick.meic.db
   log_loop_action --action timing_stop_management --duration_ms <elapsed>` (no `symbol`); review with
   `db get_step_timing`.

6. **Entry decision** (per symbol, after its assessment). Hard stops first; ORB evaluated in parallel;
   everything else is judgement (session quality, IV, credit vs risk, POP, this symbol's exposure, skew
   symmetry, width, OTM distance). Buying power and `max_concurrent_ics` bind across all symbols;
   `daily_ic_trade_target` never blocks.

   **0DTE expiration hard stop**: `get_strategies --target_dte 0` silently falls back to the next cycle
   when a symbol lists nothing today. Reject unless `dte == 0` / expiration is today, logging
   `action: "entry_skip"`, `reason: "no_0dte_expiration"` — every stop, force-close and floor is
   calibrated for same-day decay.

   **Strike overlap hard stop**: reject if any of the four proposed strikes matches a strike in any
   open IC **on this symbol**, whatever the leg direction (it would net out a leg or double a strike).
   Strikes are only compared within a symbol.

   **Call delta hard stop (non-negotiable)**: reject if actual `call_delta_at_entry` exceeds
   `max_call_delta_entry` (0.20), or 0.19 in open_volatile/late sessions. The scan target is a
   heuristic; the returned delta must be verified.

   **OTM distance hard stop**: reject if the short call's `(strike − spot)/spot` < `min_call_otm_pct`
   (0.0035) or the short put's `(spot − strike)/spot` < `min_put_otm_pct` (0.003).

   **Concurrent IC hard stop**: at `max_concurrent_ics` open ICs **across every symbol**, reject new
   entries anywhere until one closes.

   **IV rank floor**: reject all new entries below `min_iv_rank` (0.30).

   **Credit floor (hard stop)**: `ic_natural_bid ≥ min_credit_pct_of_width × wing_width` (2-wide
   ≥ $0.30, 5-wide ≥ $0.75). **Low-IV relief is relative to each arm**: while IV rank is between
   `min_iv_rank` and `min_iv_rank + low_iv_credit_floor_iv_rank_offset` (0.05), the floor is
   `min_credit_pct_of_width × low_iv_credit_relief_multiple` (0.85) — always strictly below the arm's
   own floor, so the ladder is never flattened. The legacy absolute keys apply only if an arm sets
   them. Prefer a wider width clearing the relaxed floor to a narrow one barely clearing it.

   **Fee-adjusted credit floor (hard stop, in addition)**: `db get_fee_estimate --symbol <SYM>
   --lookback fee_estimate_lookback_trades`; use `avg_fee_per_contract` when `sample_size ≥
   fee_estimate_min_sample_size`, else `fallback_per_contract`. Reject if `(ic_natural_bid ×
   multiplier) − est_fee_per_contract < applicable_credit_pct_of_width × wing_width × multiplier`, with
   the same pct the Credit floor applied. The estimate is open-only (most 0DTE ICs expire with no
   closing commission) — a floor, not a round trip; note higher drag when an active close is likely.
   This exists because a narrow, low-credit setup can pass the pct check while fees eat the credit
   (XSP once: $4.00 gross, $4.96 fees). Narrow low-IV SPX credits of ~$0.45–$0.75 net qualifying is
   intended.

   **FOMC blackout hard stop** (`get_calendar` → `fomc_dates`; if `fomc_year_known` is false, no
   scheduled gating is available): at/after 13:30 reject entries and close open positions before the
   announcement; entries allowed only before 13:30 or after 14:30, and post-announcement only with IV
   rank ≥ 0.40 and intraday range ≤ 0.5% of spot (`tt get_intraday_range`, off the streamer's
   `stream_summary`). At 13:00 on FOMC days tighten `stop_trigger_current` on all open ICs by 10%.

   **Quarterly expiry hard stops** (`quarterly_expiry_dates` / `triple_witching_dates`): (a) no entries
   in open_volatile; (b) call OTM ≥ 0.0067; (c) halt entries for the session once intraday range exceeds
   0.5% of spot; (d) on triple witching, no entries after 12:30 and force-close all by 14:00.
   (e) **Live only: no new entry all day on a quarterly expiry** (the last trading day of a quarter,
   2026-09-30). `tt execute_trade --live` refuses any opening order that day with
   `quarter_end_no_new_entries`; closes still go through. Paper keeps (a)-(c) and trades the day.

   **Regime gate (ICs only)**: `trending_regime` rejects IC entries for this symbol this iteration. A
   VIX pause hits every symbol; ATR and GEX pauses are symbol-specific. Log reason and metric. ORB is
   NOT blocked — it profits from the directional environment.

   **GEX strike placement** (GEX available and positive; guidance only): anchor the short call at/above
   `call_wall`, the short put at/below `put_wall`; if call GEX dominates, the call may sit closer and
   the put gets more room, and vice versa. Delta, OTM and credit hard stops always win.

   **GEX stop tightening** (in Step 5, per trade's own symbol): (a) zero-gamma threat → toward 0.85;
   (b) flip breached (`gex_negative`) → toward 0.80 and consider closing the threatened side now; (c)
   approaching but not through the call wall → hold (dealer resistance); if it breaks on volume, close
   the threatened side. One symbol's GEX never affects another's positions.

   **Late-entry credit bias**: if enabled, IV rank ≤ 0.45 and before 12:00, skip IC entries until noon
   (morning entries carry 3+ hours of directional exposure for the credit theta pays by afternoon).
   IV rank above 0.45 bypasses it.

   **ORB debit spread** (if `orb_enabled`, range set, and before 12:00):
   - Bullish if spot > `orb_high × (1 + orb_breakout_threshold_pct)`; bearish if spot <
     `orb_low × (1 − orb_breakout_threshold_pct)`.
   - On a first-of-session break with no ORB open: bull call debit spread (buy ATM call, sell
     `orb_wing_width_by_symbol[symbol]` higher) or bear put debit spread (buy ATM put, sell that width
     lower), same-day expiry. Dry-run, then submit live if it passes.
   - Record in `loop_log` (`orb_entry`, full legs), never `ic_trades`. Close at 100% profit, 50% loss or
     15:30, checked every iteration.
   - One ORB per day per direction **per symbol**; no re-entry after a stop-out.
   - **Log every evaluation**: an `orb_evaluated` row each run with `underlying_price`, `orb_high`,
     `orb_low` and outcome (`no_breakout`, `entered`, `already_open`, `direction_exhausted`) — otherwise
     a quiet day and a broken check are indistinguishable.

7. **Execute entry** (per symbol, if Step 6 said yes): `/execute-entry --symbol <SYM>`, then continue
   to the next symbol. **Timing end** (whether or not an entry ran): log `python -m cherrypick.meic.db
   log_loop_action --symbol <SYM> --action timing_entry_evaluation --duration_ms <elapsed>`; review with
   `db get_step_timing --action timing_entry_evaluation`.

8. **Record and notify** (once per iteration, after every symbol): a `loop_log` row per symbol plus one
   account-wide row (`symbol` NULL), a one-line status covering all symbols, then schedule the next
   wakeup. After 15:55 on a trading day, run EOD once: persist closing NLV, spawn `/eod-report live`,
   log completion. (The paper loop writes its own deterministic EOD report; `/eod-report` with no
   argument reproduces both.)

---

After Step 8, schedule the next wakeup:

| Condition | Interval |
|---|---|
| No market action expected within 90 min (weekend, holiday, or before 08:00 ET) | **end loop** |
| After 15:55 ET on a trading day (EOD complete) | **Step 8 then end loop** |
| Pre-market 08:00–09:00 ET | **600s** |
| Pre-market 09:00–09:29 ET (approaching open) | **120s** |
| Market hours, off-hours outside pre-market window | **1800s** |
| Market hours with no open positions | **300s** |
| Market hours with one or more open positions | **120s** |

Use the longest applicable interval.
