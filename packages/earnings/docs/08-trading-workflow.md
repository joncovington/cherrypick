# Trading Workflow

> _Part of the **cherrypick-earnings** package — [suite](../../../README.md) · [package README](../README.md) · [docs index](./README.md)._

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
| `/paper-start` | Forced-sampling strategy test — opens a paper trade for every strategy that clears the screen on every viable symbol (not just each symbol's best) into per-strategy strat_test books, then closes them the next morning. Use this daily while you're still building a track record for each strategy. |
| `/paper-trading-start` | One-shot production-ranking analysis — runs `rank_strategies.py get_ranked_symbols` and shows what the real loop would pick tonight, without opening anything. Good for a quick look at tonight's candidates without committing to a full cycle. |
| `/earnings-start` | The actual continuous trading loop (paper or live, depending on `enable_live_trading`) — runs through a full entry window and the next morning's close window, following `CLAUDE.md`'s Loop Steps. |

Everything below walks through what's actually happening underneath these commands, in case you
want to run the pieces by hand or understand what a slash command is doing on your behalf.

These slash commands are the **interactive / live** side you drive yourself from this package. The
day-to-day paper collection runs the other way: the [orchestrator](../../orchestrator) drives the same
`/paper-start` forced-sampling program **unattended** via OS-scheduled entry (15:45 ET) and exit
(09:45 ET) tasks it watchdogs — this module has no scheduler of its own, and the orchestrator never
places live orders. See the [package README](../README.md#how-this-fits-the-suite) for how the two roles
divide up.

---

## Afternoon: Scan and Rank

Sometime before the entry window (`entry_window_start`, default `15:30` ET), pull today's
earnings calendar and see what's on it:

```bash
python -m cherrypick.earnings.scanner get_calendar --date MM/DD/YYYY
```

Then run the cross-strategy ranking to see what the loop would actually pick tonight:

```bash
python -m cherrypick.earnings.rank_strategies get_ranked_symbols --date MM/DD/YYYY
```

This evaluates all six strategies against every symbol on the merged today-AMC/tomorrow-BMO
calendar and picks each symbol's single best-ranked strategy — see
[Entry Conditions Framework](./04-entry-conditions.md) for how that ranking works and
[Earnings Scan Analysis](./06-scan-analysis.md) for how to read the output.

---

## Entry Window (Default 15:30–15:55 ET)

Inside the entry window, for each selected symbol the loop:

1. Skips it if a position was already opened today.
2. Re-verifies it's still accepted with fresh live data (`rank_strategies.reverify_symbol()`) —
   prices and IV can move between the afternoon scan and the actual entry.
3. Checks the risk cap (`max_risk_per_trade_pct` of NLV) and the correlation block list.
4. Builds a concrete order via that strategy's own `get_order`:

```bash
python -m cherrypick.earnings.strategies.iron_fly get_order --symbol AAPL --earnings_date 2026-07-15 --earnings_timing "After market close"
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

Two of the six strategies (`double_calendar`, `atm_calendar`) do get intraday management
during *regular session hours* if a position happens to still be open then (Step 3b/3d in
`CLAUDE.md`'s Loop Steps) — but this doesn't apply overnight, only during market hours the
position is open across a multi-day hold.

---

## After the Announcement: the Managed Lifecycle

Positions used to be force-closed at 09:45 the next morning, full stop. They are managed now — the
loop marks every open position once a minute and closes it when a rule says so, which means a
position can live for one morning or for three sessions.

The full rule set, the thresholds, and the execution gates are in
[Exit Strategy Guide](./10-exits.md); the shape of a day is:

1. **09:30–09:40** — marks are taken but nothing is acted on. Opening spreads on an earnings name can
   be wider than the edge being managed, so a target computed off that mid is not a price.
2. **09:40 onward** — decisions are acted on. Most positions resolve here: a winner at its target
   closes, and a **loser closes regardless**, because the gap that put it there tends to continue
   rather than revert.
3. **A winner short of its target is held** — that is the change. It carries overnight and is
   re-marked from the next open, up to a three-session cap.
4. **Every close is confirmed through the broker** before it is recorded, even when the cache decided
   it.

**A worked multi-day hold:**

```
Mon 15:47  iron_fly on AAPL opened for $5.00 credit, carried into earnings
Tue 09:31  first mark: exit_debit 4.60 → +$40 unrealized. Marked, not acted on (open_window)
Tue 09:41  4.55 → +$45. A winner, short of the $1.25 target → hold  (used to close here)
Tue 15:39  4.10 → +$90. Still working → hold
Wed 09:41  3.70 → +$130. Still short of target → hold
Thu 09:41  3.40 → +$160, and the session cap is reached → closed, exit_reason=max_hold
```

That position would have closed Tuesday at +$45 under the old sweep. Whether holding it was right is
exactly what the `hold_days` and excursion columns now let the reports answer.

## End of Day

The forced-sampling close pass (`run_closes`, driven by the orchestrator's 09:45 ET exit task)
writes **two** deterministic end-of-day files automatically to `~/.cherrypick/logs/earnings/`:

- `paper-eod-<date>.md` — the terse metrics report: an account-wide net-P&L summary plus per-profile,
  per-strategy, and per-symbol breakdowns, all net of costs.
- `eod-analysis-<date>.md` — a conversational **7-section** read on the same session (executive
  snapshot, position detail, trade log, risk metrics, market context, tax notes, and a notes/journal
  with recommendations). Still fully deterministic/code-generated — no agent — just written in
  plain-English analysis language.

Regenerate today's or backfill a past day on demand (`eod_report` writes both; `eod_analysis` writes just
the analysis):

```
python -m cherrypick.earnings.strat_test_harness eod_report [--date YYYY-MM-DD]
python -m cherrypick.earnings.strat_test_harness eod_analysis [--date YYYY-MM-DD]
```

The orchestrator's suite digest and (opt-in) AI insight build on these files — see the suite
[reporting docs](../../../docs/reporting-and-dashboard.md).

For accumulated (multi-day) results across the whole sample, use `python -m cherrypick.earnings.strategy_report`
or the console's Earnings page instead.

To check accumulated results across many days:

```bash
python -m cherrypick.earnings.strategy_report
# The page version lives in the console: http://127.0.0.1:5070/earnings
```

---

## A Realistic Day, End to End

**Afternoon (before 15:30 ET):**
```
$ python -m cherrypick.earnings.scanner get_calendar --date 07/15/2026
→ AAPL (after close), JPM (before open next day), 6 others

$ python -m cherrypick.earnings.rank_strategies get_ranked_symbols --date 07/15/2026
→ AAPL: selected iron_fly (accepted)
→ JPM: selected directional_credit_spread (accepted)
→ 6 others: rejected_no_viable_strategy
```

**Entry window (15:30–15:55 ET):**
```
Re-verify AAPL → still accepted, risk cap OK, no correlation conflict
Build iron_fly order for AAPL → credit $5.10, 3 contracts, max loss $840
Paper mode: save_trade, done. (Live mode: execute_trade --live, then save_trade.)

Re-verify JPM → still accepted, risk cap OK
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
