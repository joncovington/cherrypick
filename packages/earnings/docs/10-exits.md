# Exit Strategy Guide

> _Part of the **cherrypick-earnings** package — [suite](../../../README.md) · [package README](../README.md) · [docs index](./README.md)._

How positions actually get closed. Every strategy is either an overnight hold or a short multi-day
calendar hold, and most of the exit mechanics follow from that — but note the post-announcement
backstop below, which fires the same session and which earlier versions of this page denied existed.

---

## Two Exit Checks, Run in Order

1. **Early exit check (Step 3c)** — runs the first morning after entry, from market open until
   `close_window_start` (default `09:45` ET). Profit-target and stop-loss checks run against
   live quotes.
2. **Unconditional close-window backstop (Step 3)** — whatever's still open once
   `close_window_start` arrives gets closed, regardless of P&L. This is a hard stop, not a
   target — the IV crush this system is built to capture already happened overnight, so there's
   no more edge in continuing to hold.

Those two checks cover the four overnight-hold strategies (`iron_fly`, `iron_condor`,
`directional_credit_spread`, `broken_wing_butterfly`). The two calendar strategies (`atm_calendar`,
`double_calendar`) add their own intraday management on top, during the days they're held — covered
separately below.

**Two exits fire ahead of those checks, and both are easy to miss:**

- **A post-announcement time backstop, same session.** `iron_fly`, `iron_condor`, and
  `directional_credit_spread` each close unconditionally once `exit_after_announcement_minutes` have
  elapsed since entry — **default 240 (4 hours)** — with reason `iv_crush_backstop`. It is evaluated
  *before* the profit/stop check, so it wins. The key is **not present in `config.example.json`**, so
  the 240-minute default is what actually runs unless you add it. This means an overnight position can
  be force-closed the same day it was opened.
- **A per-leg delta stop on `broken_wing_butterfly`.** `leg_stop_delta_abs`, **default 0.60**, checked
  before the whole-position exit; reason `leg_stop_delta` (or `leg_stop_delta_overnight_gap`). The
  calendars have their own at 0.45.

Everything else does wait for the next morning: positions open before the close and get their first
live look after the open.

---

## ⚠️ The configured thresholds below may not be the ones actually applied

The values in this section are what each strategy's `strategies.<name>` config block *says*. On the
forced-sampling paper path they are **not** what runs. `strategy_test_runner` passes the **full**
project config to `evaluate_position`, which hands that same dict to
`scanner.evaluate_credit_spread_exit`. That function reads `profit_target_pct` and
`stop_loss_credit_multiple` off the dict it is given — and `_load_config` merges `strategy_defaults`
*downward into* each strategy's sub-config, never upward — so the per-strategy values are out of
scope and the function's own defaults (**0.10 profit target, 2.0× stop**) apply instead.

Until that is fixed in code, treat the numbers below as the configured intent and read
`scan_log` for the exit reason actually recorded. This is a code defect, not a documentation one; it
is written down here so nobody spends an afternoon wondering why an iron fly closed at 10%.

## Profit Target / Stop Loss by Strategy Type

### Credit strategies (`iron_fly`, `iron_condor`, `directional_credit_spread`)

`scanner.evaluate_credit_spread_exit()` checks the position against its own config:

```
profit = credit_received - exit_debit
if profit >= credit_received * profit_target_pct:
    close_all (profit_target)
if exit_debit >= credit_received * stop_loss_credit_multiple:
    close_all (stop_loss)
```

Default `profit_target_pct` is `0.50` (50% of credit received), `stop_loss_credit_multiple` is
`1.5` (stop once it would cost 1.5× the original credit to close). See
[Configuration Guide](./03-configuration.md) for each strategy's actual current values.

**Example — iron_fly on AAPL:**
```
Entry credit: $0.80 per spread
Profit target: 50% → close if buying back costs ≤ $0.40
Stop loss: 1.5x → close if buying back would cost ≥ $1.20
```

### `broken_wing_butterfly`

Despite being entered for a debit, it exits through **`scanner.evaluate_credit_spread_exit()`** — the
same function the credit strategies above use — after its per-leg delta stop has been checked:

```
1. per-leg delta stop:  abs(leg delta) >= leg_stop_delta_abs   → close_all (leg_stop_delta)
2. otherwise, the credit-spread exit on profit_target_pct / stop_loss_credit_multiple
```

`leg_stop_delta_abs` defaults to **0.60**. `profit_target_pct` is set to `0.25` in the config;
`stop_loss_credit_multiple` is **not** set there, so the function's default of **2.0×** applies.

> **`stop_loss_pct_of_debit` is dead for this strategy.** The config block sets it to `0.40` and the
> adjacent `_exit_note` describes it as the stop, but `evaluate_credit_spread_exit` never reads that
> key — it reads `stop_loss_credit_multiple`. The effective stop is 2× credit, not 40% of debit. Fix
> the config note rather than trusting it.

### Calendar strategies (`atm_calendar`, `double_calendar`)

Same `evaluate_debit_spread_exit()` shape, but looser: `stop_loss_pct_of_debit` defaults to
`1.0` (the position has to round-trip all the way back to the entry debit before the stop
fires), and `profit_target_pct` is `0.25`–`0.30`. These positions decay over days, not hours, so
the stop tolerates more round-trip noise before giving up on the position.

---

## `double_calendar` and `atm_calendar`: Multi-Day Management

These two are the only strategies that might still be open during regular market hours on a
day other than the entry/exit day, since they're held across multiple expirations rather than
closed the next morning by default.

**`atm_calendar`** (Step 3d): a single 2-leg position (short front-month ATM call, long
back-month same strike). Each check during session hours calls `atm_calendar.evaluate_position()`
with live greeks — `action: hold` does nothing, `action: close_all` closes both legs together
and calls `save_close`.

**`double_calendar`** (Step 3b): the only strategy where each side (call side, put side) can
close independently, tracked leg-by-leg in the `trade_legs` table instead of `legs_json`.
`double_calendar.evaluate_position()` returns `action: hold`, `action: close_side` (closes just
the threatened side's 2 legs via `save_leg_close`, keeps the other side open), or
`action: close_all` (closes everything, then `save_close`). A side crossing
`leg_stop_delta_abs` (default `0.45`) is what typically triggers a `close_side` decision — the
side that's moved too far ITM gets taken off while the other side, still likely profitable,
stays open.

Both still hit `exit_days_before_front_expiration` (default 5) as a hard time-stop — once the
front leg is within 5 days of expiring, gamma risk on a short ATM option overwhelms whatever
theta benefit is left, so the position exits regardless of where profit-target/stop-loss
otherwise stand.

---

## A Realistic Example: `iron_fly` Overnight Hold

```
Entry (day 1, 15:32 ET): AAPL iron_fly, entry credit $5.10 for 3 contracts
Overnight: unmonitored, earnings announced after close

Next morning, 09:15 ET (Step 3c check):
  Live quotes pulled, exit_debit computed at $2.10
  profit = $5.10 - $2.10 = $3.00 ≥ $5.10 * 0.50 ($2.55) → close_all, reason: profit_target
  Position closed, save_close records the trade

Result: no need to wait for the 09:45 close-window backstop -- the early-exit check already
caught the profit target.
```

And a case where the early-exit check doesn't fire:

```
Next morning, 09:15 ET (Step 3c check):
  exit_debit computed at $4.00
  profit = $5.10 - $4.00 = $1.10, below the $2.55 target
  exit_debit ($4.00) below the $7.65 stop-loss threshold (1.5x $5.10)
  → hold

09:45 ET (Step 3, unconditional):
  Still open → close regardless of the current $1.10 unrealized profit
```

---

## Troubleshooting

**"Position didn't close at the early-exit check, but I expected it to"**
Check the actual `exit_debit` computed from live quotes against the specific formula above —
the position may simply not have hit either threshold yet. It'll still close at the
unconditional close-window backstop no later than `close_window_start`.

**"`double_calendar` closed one side but left the other open"**
That's `action: close_side` working as intended — one side crossed `leg_stop_delta_abs` while
the other hadn't. Check `trade_legs` for that position's per-leg `status` to confirm.

**"Exit debit computation returned nothing / position didn't close on schedule"**
`scanner.compute_generic_exit_debit()` returns `None` if any leg's live quote is missing —
by design, the system retries next tick rather than closing on incomplete data. Check that
every leg's option symbol still has a live quote available.

---

## Navigation

**← Previous:** [Trading Workflow](./08-trading-workflow.md)
**Next →** [Examples & Case Studies](./11-examples.md)
