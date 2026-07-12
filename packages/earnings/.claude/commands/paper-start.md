---
description: Run one day's cycle of the strategy-testing paper book (see docs/strategy-testing-plan.md) — checks connections, self-schedules to the entry window if run before market open, then to tomorrow's close window.
---

# /paper-start

Runs one full open→close cycle of the forced-sampling strategy test
(`src/strategy_test_runner.py`, see `docs/strategy-testing-plan.md`):
checks the dolt/tastytrade connections, opens every Tier 1/2 candidate
into the `profile='strat_test'` paper book, then closes them all the
following morning. Used both for the initial Week 0 validation (confirm
all 10 strategies construct, size, cost-adjust, persist, and close with no
error) and for every day of the Weeks 1-8 accumulation phase — re-invoke
it daily. This is a **separate program** from the live/paper trading loop
(`/earnings-start`) — it never touches `db.py`, never calls `tt.py
execute_trade`, and writes only to the isolated `profile='strat_test'`
book in the shared paper database, so it's safe to run alongside
`/earnings-start` the same day.

## What to do when this command runs

**Before starting**: check `.claude/strategy_test_lock` for a live PID.
This is a **separate lock from `.claude/scheduled_tasks.lock`** (the
live-loop's own) — the two can run concurrently, since this only ever
writes `profile='strat_test'` rows. If another `/paper-start` session
already holds this lock, stop and tell the user instead of starting a
second one (double-entries into the same test book would corrupt the
sample).

1. **Check connections** before anything else: `python src/tt.py
   get_connection_status` (tastytrade OAuth session) and confirm dolt
   sql-server is reachable (`rank_strategies._ensure_dolt_running()` does
   this automatically inside `run_entries`, but fail fast and tell the
   user here if the account/session itself isn't ready — same account-gate
   discipline as `/earnings-start`'s Step 4a).

2. **If invoked before today's entry window** (`entry_window_start` in
   config.json, typically 15:30 ET) — including first thing in the
   morning, before market open — do not run anything yet. Schedule a
   wakeup for the entry window and end the turn. This is the "easy
   button": the user can type this command any time and it waits for the
   right moment on its own.

3. **At the entry window**, run:
   ```
   python src/strategy_test_runner.py run_entries --date <today MM/DD/YYYY> --profile balanced
   ```
   Report the result clearly: how many (strategy, symbol) pairs opened,
   broken down by strategy, and how many were skipped with their reasons
   (tier-excluded, risk-cap-exceeded, order-build-failed, etc.). A
   strategy opening zero trades tonight is not a bug — check the skip
   reasons before assuming something's wrong.

4. **Schedule a wakeup for tomorrow's close window** (`close_window_start`
   in config.json, typically 09:45 ET) immediately after step 3 — don't
   wait for the user to ask.

5. **At the close window**, run:
   ```
   python src/strategy_test_runner.py run_closes --profile balanced
   ```
   Report closed trades with P&L (gross and cost-adjusted per
   `entry_cost`/`exit_cost`), and any skips.

6. **Release `.claude/strategy_test_lock`** and end — one full day's cycle
   is complete. Re-invoke `/paper-start` again the next day to continue
   accumulating (this command does not loop on its own across multiple
   days — see "When to use").

## Usage

```
/paper-start
```

No options — this always runs the fixed `balanced` sizing profile (100k
capital, `risk_pct_multiplier` 1.0) per the strategy-testing plan's "fixed
testing basis" design, so results aren't confounded by which risk profile
was active.

## When to use

**Once per day**, any time before that day's entry window (including
before market open) — it waits for the right moment on its own, runs one
open→close cycle, then ends. Invoke it:
- **Once at the start of Week 0** to confirm the whole pipeline works end
  to end before committing to the nightly accumulation schedule.
- **Every day during Weeks 1-8** to keep accumulating each strategy's
  sample toward the 30/directional and 100/significant targets. This
  command intentionally does not keep looping across multiple days on its
  own — re-invoke it each day, or ask for a longer-running variant if you
  want that automated too.

## Common issues

**"dolt sql-server not available" / "tastytrade connection failed"**
- Same account-gate checks the live loop uses. Run
  `python src/tt.py get_connection_status` directly to diagnose.

**Everything skipped with `tier_excluded_Tier 3` or no candidates at all**
- Normal on a quiet night — the shared calendar scan found nothing
  Tier 1/2 tonight. Re-run tomorrow; a single quiet night doesn't mean the
  harness is broken.

**`order_build_failed` or `leg_quotes_unavailable` for a specific strategy**
- A live chain/quote call failed for that (symbol, strategy) pair at
  build time. Logged as a skip, not a crash — every other candidate that
  night still gets processed (per-candidate exception isolation).

## See also

- `docs/strategy-testing-plan.md` — full multi-week plan this cycle feeds
- `docs/paper-trading-profiles.md` — the risk-profile testing program this reuses later
- `/earnings-start` — the actual live/paper trading loop (separate program, separate lock)
