# Trading Workflow

What a real day looks like running this system, from the afternoon scan through the next
morning's close. The whole shape of this workflow follows from one architectural fact: every
position opens once before today's close and closes once after tomorrow's open, unmonitored
overnight. There's no same-day announcement-to-close cycle here — the edge is the overnight
IV crush, not an intraday move.

---

## The Slash Commands

Four slash commands cover the actual day-to-day operation; which one you reach for depends on
what you're trying to accomplish:

| Command | What it does |
|---|---|
| `/paper-start` | Forced-sampling strategy test — opens every Tier 1/2 candidate for every strategy (not just each symbol's best) into an isolated `profile='strat_test'` paper book, then closes them the next morning. Use this daily while you're still building a track record for each strategy. |
| `/paper-trading-start` | One-shot production-ranking analysis — runs `rank_strategies.py get_ranked_symbols` and shows what the real loop would pick tonight, without opening anything. Good for a quick look at tonight's candidates without committing to a full cycle. |
| `/earnings-start` | The actual continuous trading loop (paper or live, depending on `enable_live_trading`) — runs through a full entry window and the next morning's close window, following `CLAUDE.md`'s Loop Steps. |
| `/paper-trading-eod-report` | End-of-day report on today's candidates, decisions, and tomorrow's exit plan. |

Everything below walks through what's actually happening underneath these commands, in case you
want to run the pieces by hand or understand what a slash command is doing on your behalf.

---

## Afternoon: Scan and Rank

Sometime before the entry window (`entry_window_start`, default `15:30` ET), pull today's
earnings calendar and see what's on it:

```bash
python src/scanner.py get_calendar --date MM/DD/YYYY
```

Then run the cross-strategy ranking to see what the loop would actually pick tonight:

```bash
python src/rank_strategies.py get_ranked_symbols --date MM/DD/YYYY
```

This evaluates all seven strategies against every symbol on the merged today-AMC/tomorrow-BMO
calendar and picks each symbol's single best-ranked strategy — see
[Entry Conditions Framework](./04-entry-conditions.md) for how that ranking works and
[Earnings Scan Analysis](./06-scan-analysis.md) for how to read the output.

---

## Entry Window (Default 15:30–15:55 ET)

Inside the entry window, for each selected symbol the loop:

1. Skips it if a position was already opened today.
2. Re-verifies it's still Tier 1/2 with fresh live data (`rank_strategies.reverify_symbol()`) —
   prices and IV can move between the afternoon scan and the actual entry.
3. Checks the risk cap (`max_risk_per_trade_pct` of NLV) and the correlation block list.
4. Builds a concrete order via that strategy's own `get_order`:

```bash
python src/strategies/iron_fly.py get_order --symbol AAPL --earnings_date 2026-07-15 --earnings_timing "After market close"
```

5. In paper mode, records the order via `db_paper.py save_trade` and stops there — no order is
   ever submitted, but the credit/debit and sizing are computed from real live quotes. In live
   mode, submits via `tt.py execute_trade --live` and reprices toward zero credit on a timer
   while working the fill.

No new entries happen after `entry_window_end` (default `15:55` ET) — the position, once
opened, is simply held overnight. There's nothing to actively manage between the entry window
and market close; the whole point of the entry window closing 5 minutes before the bell is to
be done well before the earnings reaction actually happens.

---

## Overnight

Positions sit untouched from the close through the next morning's open. This is deliberate —
the system holds through the earnings reaction and the resulting IV crush without trying to
manage it intraday or overnight, since nobody's watching. This is exactly why every strategy in
this system is defined-risk: an undefined-risk position left unmonitored overnight can move
against you by an amount that isn't capped, which is why naked strategies were removed from
this system entirely.

Two of the seven strategies (`double_calendar`, `atm_calendar`) do get intraday management
during *regular session hours* if a position happens to still be open then (Step 3b/3d in
`CLAUDE.md`'s Loop Steps) — but this doesn't apply overnight, only during market hours the
position is open across a multi-day hold.

---

## Next Morning: Close Window (Default Starting 09:45 ET)

Two things can happen the next morning, in this order:

1. **Early exit check (Step 3c)**, from market open until `close_window_start` — profit-target
   and stop-loss checks run against live quotes for the five overnight-hold credit/debit
   strategies (`iron_fly`, `iron_condor`, `directional_credit_spread`, `broken_wing_butterfly`,
   `reverse_fly`). If the position already hit its `profit_target_pct` or
   `stop_loss_credit_multiple`/`stop_loss_pct_of_debit`, it closes right there.
2. **Unconditional close-window backstop (Step 3)** — whatever's still open once
   `close_window_start` arrives gets closed, full stop, regardless of P&L. The IV crush already
   happened overnight; there's no more edge from continuing to hold, so this is a hard stop, not
   a target to wait for.

`double_calendar` and `atm_calendar` (the two multi-day, calendar-spread strategies) instead run
their own management logic (Step 3b/3d) across however many days they're actually held, closing
either a single side or the whole position based on `evaluate_position()`'s read of the current
greeks — but they still hit the same unconditional close-window backstop as a final exit rule.

---

## End of Day

```
/paper-trading-eod-report
```

Summarizes today's candidates, what was opened (if anything), and tomorrow's exit plan. If
you're running `/paper-start`'s forced-sampling program instead, its own daily cycle already
reports opens and closes as it runs — see the command's own output rather than a separate
report step.

To check accumulated results across many days:

```bash
python src/strategy_report.py
python src/strategy_dashboard.py   # writes reports/strategy_dashboard.html
```

---

## A Realistic Day, End to End

**Afternoon (before 15:30 ET):**
```
$ python src/scanner.py get_calendar --date 07/15/2026
→ AAPL (after close), JPM (before open next day), 6 others

$ python src/rank_strategies.py get_ranked_symbols --date 07/15/2026
→ AAPL: selected iron_fly (Tier 1)
→ JPM: selected directional_credit_spread (Tier 2)
→ 6 others: rejected_no_viable_strategy
```

**Entry window (15:30–15:55 ET):**
```
Re-verify AAPL → still Tier 1, risk cap OK, no correlation conflict
Build iron_fly order for AAPL → credit $5.10, 3 contracts, max loss $840
Paper mode: save_trade, done. (Live mode: execute_trade --live, then save_trade.)

Re-verify JPM → still Tier 2, risk cap OK
Build directional_credit_spread order for JPM → credit $1.20, 5 contracts
save_trade
```

**Overnight:** both positions held, unmonitored, through the earnings reactions and the market
close/reopen.

**Next morning, market open to 09:45 ET (Step 3c):**
```
AAPL iron_fly: current value implies 55% of max profit captured → close, log win
JPM directional_credit_spread: still holding a loss, hasn't hit stop → hold
```

**09:45 ET, close window (Step 3):**
```
JPM directional_credit_spread still open → close unconditionally regardless of P&L
```

That's the full cycle for both positions — nothing held past the close window, no same-day
exit, no intraday babysitting once a position is open.

---

## Troubleshooting a Day That Went Quiet

A day with zero entries isn't a bug by itself — check the specific rejection reasons in
`rank_strategies.py`'s output before assuming something's broken. See
[Earnings Scan Analysis](./06-scan-analysis.md)'s "A Quiet Night Is Not a Bug" section and
[Screening Criteria](./screening-criteria.md) for what each hard filter actually checks.

---

## Navigation

**← Previous:** [Earnings Scan Analysis](./06-scan-analysis.md)
**Next →** [Exit Strategy Guide](./10-exits.md)
