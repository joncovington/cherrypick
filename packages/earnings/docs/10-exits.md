# Exit Strategy Guide

> _Part of the **cherrypick-earnings** package — [suite](../../../README.md) · [package README](../README.md) · [docs index](./README.md)._

How positions get closed. Positions used to be force-closed the morning after entry, on the reasoning
that the overnight IV crush was the whole edge. They are **managed** now: marked once a minute,
exited when a rule says so, and carried across sessions when carrying them is the better bet.

> **Measurement break, 2026-08-12.** Both the exit timing and the per-strategy thresholds changed on
> this date, so win rate, expectancy and hold time all shift with it. Results either side are not
> comparable and must never be pooled. Recorded as a `lifecycle_cutover` row in `measurement_breaks`
> (`python -m cherrypick.earnings.db_paper get_measurement_breaks`).

---

## The loop, and its phases

`cherrypick.earnings.paper_loop` runs one short-lived tick every 60 seconds, spawned by the
orchestrator's supervisor. It derives what to do from the clock and the database, never from memory,
so a tick can be reasoned about without knowing anything about the tick before it.

| Phase | ET | What it does |
|---|---|---|
| `off_hours` | outside the below | nothing, and records no row — an out-of-session tick is not a measurement |
| `forward_scan` | ~06:30, once daily | the slow, stable half of screening, pre-market (see below) |
| `pre_open` | 09:00–09:30 | refreshes the producer's subscription request — the **only** phase allowed to GROW it (see below) |
| `open_window` | 09:30–09:40 | **marks, never acts** |
| `management` | 09:40–15:40 | marks, decides, acts |
| `entry` | 15:35, once daily | the forced-sampling entry scan |
| `eod` | 16:00–16:30 | writes the session's reports |

The open window is a phase of its own because the first ten minutes of an earnings name's options are
not reliably priceable — spreads can be wider than the edge being managed, so a target computed off
that mid is arithmetic rather than a price. Marks are still recorded through it, and a decision
reached there is recorded with the gate that held it and taken on the first tick that clears.

**Screening is split across the day.** The slow half — the earnings calendar and every
Dolt-derived metric (winrate, IV/RV, market cap, average volume, historical move stats) — is computed
pre-market by `forward_scan` for the next ten trading days. The fast, perishable half — live price,
expected move, term structure, spread, open interest — is fetched at entry, where it has to be fresh.

That split exists because the entry scan costs roughly **35s of fixed overhead plus 8s per symbol**
(measured over eight real runs: 17 symbols took 1m17s, 35 took 5m24s). The heaviest night on record,
87 symbols, extrapolates to about twelve minutes — which at the old 15:45 start would have finished
*past* the 15:55 entry window. The scan now starts at **15:35** (`entry_scan_at`, deliberately its own
key rather than `entry_window_start`, which means "when may entries be placed at all") and reads the
morning snapshot to **pre-filter** its candidate list.

The pre-filter drops a name only on criteria that cannot move intraday — **winrate, average volume,
market cap** — and only against the *near-miss* floor, the loosest bar any `symbol_screen` setting can
ask for. So it can only ever drop a name that could not have passed under any configuration, and every
survivor is re-screened entirely on live data. `iv_rv_ratio` is deliberately excluded even though it is
cheap and Dolt-derived: implied vol *rises* into an announcement, so a name below the floor in the
morning can legitimately clear it by the afternoon. A snapshot whose pass did not complete **today** is
ignored outright rather than partially trusted. Dropped names are recorded in `scan_log` under strategy
`_prefilter`, so a symbol missing from the evening's candidates is explained rather than simply absent.

**Why only `pre_open` grows the stream request.** A producer binds its underlyings when it
starts, so the watchdog recycles it when the union grows — and a recycle costs a settling window in
which nothing streams for anyone. Growing the set at 15:45 would blind the 0DTE modules trading into
their own close, to make symbols available fourteen hours before this module marks anything. Shrinking
(a position closing) is always safe and happens whenever it needs to: an over-subscribed producer
serves every consumer correctly and never triggers a recycle.

**Known gap:** the entry scan holds the loop's single-writer lock for up to ~25 minutes, so positions
go unmarked roughly 15:45–16:10. Accepted deliberately — the morning is where management matters, and
the alternative (a second writer against the same SQLite book) trades a documented gap for a much
harder class of bug.

## What closes a position

Checked in this order. The pin guard is first because it is about an outcome nothing else prices; the
strategy's own verdict is next because it owns every threshold; the last two only ever turn a *hold*
into a *close*, never the reverse.

1. **Pin guard** — any **short** strike within `pin_guard_dollars` (1.00) of spot inside the last
   `pin_guard_window_minutes` (60) of its expiration day. Fires on *proximity*, not on being in the
   money: assignment is decided by the settlement print, which has not happened yet. Reason `pin_risk`.
2. **The strategy's own `evaluate_position`** — profit target, stop, leg-delta stops, and (for the
   calendars) the front-expiration time stop. Thresholds live in `strategies.<name>` and are listed
   below. The management layer never restates them.
3. **Session cap** — an overnight structure closes after `hold_winners_max_days` (3) sessions
   whatever the verdict. Counted in **trading** sessions, so a Friday entry still open on Monday has
   been held one session, not three, and a weekend cannot spend the budget. Reason `max_hold`.
4. **PEAD gate** — on the first check of a day, a position at or below breakeven closes. Holding a
   winner past the first morning is worth roughly +1.4pp on average as the residual crush drains over
   three to five sessions; holding a **loser** fights post-earnings drift, which continues rather than
   reverting. Reason `pead_loser`; disable per strategy with `close_losers_first_morning: false`.

### Execution gates

A close decided by the rules above is not necessarily taken on that tick. Each of these records the
verdict with `executed = 0` and the gate that held it, and the next tick reconsiders:

| Gate | Meaning |
|---|---|
| `before_exec_window` | earlier than `exec_window_start` (09:40) |
| `open_window` | the tick's phase never acts |
| `spread_too_wide` | widest leg spread over `max_leg_spread_pct` (0.35 of mid) |
| `unusable_mark` | the position could not be priced at all this tick |
| `tick_execution_cap` | more than `max_executions_per_tick` (3) closes already taken; deferred a minute |
| `close_failed` | the close itself failed; `close_attempts` is bumped and it becomes `stranded` at 2 |

Any close decided on **cached** quotes is re-priced through the broker before it is recorded. The
decision is the cache's; the price on the ledger should be one we could actually have traded.

### The exit_reason taxonomy

Every close records one, on the trade itself (`trades.exit_reason`). Before this it reached only
`scan_log`, joinable back to a trade by nothing better than (date, symbol, strategy) — which cannot
identify a position held across several sessions at all.

`profit_target` · `stop_loss` · `pead_loser` · `max_hold` · `pin_risk` · `leg_stop_delta` ·
`time_exit` · `iv_crush_backstop` · `close_window` · `legacy_next_morning` (every pre-cutover exit).

### The same-session backstop, and why it no longer binds

`exit_after_announcement_minutes` (240) closes a position unconditionally that many minutes after
entry. Eighteen hours pass between a 15:45 entry and the first morning mark, so left in force it
would fire on **every** position before any management rule was reached — multi-day holds would be
unreachable while appearing to work. The management layer therefore injects a value past any hold it
could preempt. Lowering `management.exit_after_announcement_minutes` re-enables the old behavior; the
top-level key still applies to the agent-driven loop, which has no session cap of its own.

## Thresholds, per strategy

The values that ship, and where each comes from. **Research-backed** means a published study or a
widely-documented convention specific to *earnings*; **house rule** means no such source was found
and the number is a reasoned starting point. Say which, always — the two invite different confidence.

| Strategy | Target | Stop | Time rule | Basis |
|---|---|---|---|---|
| `iron_fly` | 25% of credit | 1.5× credit | 3 sessions | **Research-backed.** A fly's max-profit zone is a *point*, not a band, so the convention manages it at 25% where a condor's band supports 50%. Was `0.50` before the cutover. |
| `iron_condor` | 50% of credit | 1.5× credit | 3 sessions | **Research-backed.** Practitioner guidance puts the earnings capture at 50–75% by mid-morning. A breached short side closes the whole position; no rolling. |
| `directional_credit_spread` | 50% of credit | 2.0× credit | 3 sessions | **House rule.** No published backtest of stop multiples for an *overnight earnings* vertical was found; this is the generic credit-spread convention carried over. Stop widened from 1.5× at the cutover to match it. |
| `broken_wing_butterfly` | 25% of credit | 2.0× credit, plus `leg_stop_delta_abs` 0.60 | 3 sessions | **House rule.** tastylive has traded BWBs into earnings but published no management study. Exits through the *credit*-spread path despite pricing as a net debit. |
| `double_calendar` | 15% of debit | 50% of debit | front-leg 5 DTE; leg-delta 0.45 | **Research-backed.** A 30-name backtest puts the target at 15% of debit, usually hit within minutes of the open, at an 87% win rate. Stop and time exit from the same source. |
| `atm_calendar` | 15% of debit | 50% of debit | front-leg 5 DTE | **Thin.** No earnings-specific backtest as clean as the double calendar's; aligned with it deliberately rather than guessed at separately. |

The credit strategies all run through `scanner.evaluate_credit_spread_exit()`:

```
profit = credit_received - exit_debit
if profit >= credit_received * profit_target_pct:      close_all (profit_target)
if exit_debit >= credit_received * stop_loss_credit_multiple:  close_all (stop_loss)
```

**Worked example — iron_fly, $5.00 of credit, under the current 25% target:**

```
09:33  exit_debit 3.50 → profit 1.50 ≥ 1.25 (25% of 5.00)  → close_all/profit_target
                                                              GATED: before_exec_window
09:41  re-evaluated, still at target, broker confirms 3.55  → closed, exit_reason=profit_target
```

Both rows are recorded. The gated one is what makes the 09:41 exit read as deliberate rather than as
a late reaction to a 09:33 signal.

**And when it does not hit:**

```
09:41  exit_debit 4.50 → profit 0.50, short of target; position is a WINNER → hold
       (this used to close unconditionally at 09:45)
day 2  still working, still a winner                        → hold
day 3  session cap reached                                  → closed, exit_reason=max_hold
```

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

---

## Watching it run

The loop's own observability, and what each surface answers.

| Surface | Question it answers |
|---|---|
| `position_marks` | what was each position worth, each minute, and were the quotes good enough to act on |
| `management_events` | every verdict, including the ones a gate held back |
| `loop_iterations` | is the loop alive — one row per in-session tick, `status` `ok` or the refusal |
| `measurement_breaks` | which dates results must not be pooled across |
| `~/.cherrypick/state/earnings_entry.last.json` / `_exit.last.json` | the SLA heartbeats the watchdog and the console read |
| console → Earnings → **open** | positions, marks, loop health, management log |
| `data/review/eod-<day>.md` | What each module did, the arms, and the trend (suite review) |

```bash
python -m cherrypick.earnings.paper_loop status        # phase, last tick, open positions, lock
python -m cherrypick.earnings.paper_loop once          # run one tick by hand
python -m cherrypick.earnings.db_paper get_iterations --session_date 2026-08-12
python -m cherrypick.earnings.db_paper get_management_events --order_id <id>
python -m cherrypick.earnings.db_paper get_measurement_breaks
```

**Reading the failure modes.** A stretch of `loop_iterations` rows with `status` other than `ok` is a
feed problem; a stretch with *no rows at all* during the session is the loop not running. Those two
were indistinguishable before this table existed, which is exactly why it does. A position whose
marks are mostly `usable = 0` is unpriceable rather than unmanaged — it is being looked at every
minute and refused, and the refusal reason says why.

**Stranded positions.** Two failed closes and a position's `status` becomes `stranded`. That
threshold used to be applied inside a single sweep, so a position missing one attempt per day looked
fresh every morning; it outlives the run that noticed it now. It still shows in the EOD report's
stranded section and still carries real risk.

## Troubleshooting

**"A position closed later than the target was reached."** Expected, and recorded: look for a
`management_events` row with `executed = 0` and a `gate`. The exit was seen and held — most often by
`before_exec_window` (opening spreads) or `spread_too_wide`.

**"A winner was not closed at the target."** Check `profit_target_pct` for that strategy — the fly
manages at 25% and the condor at 50%, deliberately. If the mark is `unpriced`, the position could not
be quoted; see the refusal on its latest `position_marks` row.

**"A position is still open after three sessions."** The session cap counts *trading* sessions. Check
`hold_days` on close, or `session_span` from `opened_at`. A calendar is exempt: it runs its own
front-expiration time stop instead.

**"Nothing is being marked."** Check the phase — `paper_loop status`. Outside 09:30–15:40 ET the loop
deliberately does nothing, and between 15:45 and roughly 16:10 the entry scan holds the lock.
