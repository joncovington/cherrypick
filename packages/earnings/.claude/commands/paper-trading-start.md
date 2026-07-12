# /paper-trading-start

Runs the real earnings-candidate scan for tonight's/tomorrow-morning's entry window and ranks candidates by composite score.

## Description

Calls `rank_strategies.py get_ranked_symbols`, the production cross-strategy ranking engine (see CLAUDE.md's Loop Step 4b): fetches today's real AMC + tomorrow's real BMO earnings calendar from DoltHub, evaluates every registered strategy against live tastytrade/DoltHub data per symbol, and picks each symbol's single best-ranked viable strategy. For each selected symbol, builds a concrete tradeable order via that strategy's own `get_order`.

This is a **one-shot analysis**, not the trading loop — it does not check today's account status, does not submit orders, and does not schedule any wakeup. Use `/earnings-start` to start the actual continuous loop.

## Usage

```
/paper-trading-start
/paper-trading-start --count 2
/paper-trading-start --config conservative
```

## Options

- `--count N` — Max number of candidates to build orders for (default: 3)
- `--config [conservative|moderate|aggressive]` — Risk profile (default: moderate)
  - `conservative`: Tier 1 only
  - `moderate` / `aggressive`: Tier 1-2 (rank_strategies.py's `viable` list only ever contains Tier 1/2 — there is no Tier 3 tier to relax into; `aggressive` currently behaves the same as `moderate`)
- `--date MM/DD/YYYY` — Passed through for the log/report label only. **Does not change which calendar day is scanned** — `rank_strategies.py`'s calendar fetch always pulls today's AMC + tomorrow's BMO. For a different historical date, use `scanner.py get_calendar --date` directly.

## Output

Displays real, live-scanned candidates, e.g.:
```
================================================================================
PAPER TRADING ANALYSIS - 07/08/2026
================================================================================

ANALYSIS SUMMARY
  Total candidates: 6
  Selected for trading: 0
  Rejected/waitlisted: 6

REJECTED/WAITLISTED (6)
  - PSMT: rejected_no_viable_strategy (iron_fly: atm_delta_abs_above_maximum; ...)
  ...

READY FOR 3:50 PM ENTRY WINDOW
Entry orders logged to: logs/paper/runs_2026_07.json
================================================================================
```

A quiet night with zero selections is expected and normal — small/mid-cap names failing liquidity gates (OI, spread, weekly-options requirement) or having too small an expected move are legitimate, common rejections, not bugs.

If any are selected, each shows:
- Symbol, earnings date/timing
- Composite score and tier (Tier 1 or Tier 2)
- Winning strategy
- Concrete order (credit/debit, strikes, legs) or the specific error if order-building failed

## When to Use

**Mid-afternoon ET on earnings days** — before the entry window (`entry_window_start`/`entry_window_end` in config.json, typically 15:30–15:55 ET)

## Workflow

1. Run command → see selected candidates (if any) with score, tier, and strategy
2. Review the built order for each (credit, strikes, legs)
3. Execute manually in your broker during the entry window, or let `/earnings-start`'s loop handle Step 4b automatically
4. Run `/paper-trading-eod-report` after close window handling

## Files Generated

- `logs/paper/runs_YYYY_MM.json` — One appended entry per run: date, timestamp, total candidates, selected trades with full order detail

Real trade persistence (for the actual loop, not this standalone scan) goes through `db_paper.py save_trade` into `data/paper_trades.db`, per CLAUDE.md's Step 4b — this command does not write there itself.

## Related Commands

- `/paper-trading-eod-report` — Generate end-of-day review
- `/earnings-start` — Starts the actual continuous trading loop (Steps 0-5, self-scheduling)

## Common Issues

**"Total candidates: 0"**
- No earnings tonight/tomorrow morning per DoltHub's calendar — check `python src/scanner.py get_calendar --date MM/DD/YYYY` directly to confirm

**"Selected for trading: 0" with all rejected**
- Normal on a quiet night. Check the printed `reason` per symbol — usually a specific, real liquidity/signal hard-fail (see `docs/screening-criteria.md`), not a system error

**"Order build FAILED"**
- The candidate tiered Tier 1/2 at scan time but a live chain/quote call failed when building the concrete order (e.g. no quote data for a strike). Check the printed `order_error`.

## See Also

- `docs/04-entry-conditions.md` — How scoring/tiering works
- `docs/05-strategies.md` — Each strategy explained
- `docs/screening-criteria.md` — Hard-filter/tiering source of truth
