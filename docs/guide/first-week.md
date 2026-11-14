# Your first week

Once the installer has finished, nothing needs starting. A background supervisor runs every job on a
timetable, and the console shows what came of it. This page is that timetable, a short routine for
the morning and the evening, what a normal first week looks like, and what is and is not worth
worrying about.

All times here are US Eastern, wherever you are, and they are the shipped defaults. Most jobs only
run on trading days; on a weekend or an exchange holiday they do nothing, and that is correct.

## What runs on its own, and when

### The day at a glance

| Time (ET) | What happens | Where you see it |
| --- | --- | --- |
| Overnight | The nightly backup (01:30). With Dolt: the data pull (05:30), then the technicals jobs from 06:00 to about 07:00. | System → data |
| 07:45 | The market files are fetched again, so the pack has the previous session's volume. | Reports → morning |
| 08:30 | **The morning pack** is built: the overview's readings, the gamma levels and the GREEN, YELLOW or RED phase. Headlines and the futures contract table follow at 08:45. | Reports → morning |
| 09:15 | The streamer (the live quote feed) must be running. The watchdog starts looking after it from here. | Status bar, `streamer` chip |
| 09:30 | **The open.** The paper loops of the modules you have switched on start looking for entries. MEIC's paper entries fall between 10:00 and 14:30. | Each module's page |
| 10:00 onward | If you turned on the account evaluation, the manage package judges your own account at 10:00 and at any further times you set. Between those runs it re-marks every minute until 16:10. | Live → positions |
| 15:35 | Earnings' entry scan starts, if the earnings module is on. | Earnings page |
| 16:00 | **The close.** Cash-settled positions settle. | Each module's page |
| 16:30 | **The end-of-day review** is built, marked `provisional`. | Reports → eod |
| 16:40 | MEIC's regime cuts are written. | MEIC → regime cuts |
| 18:35 to 19:30 | The evening data jobs: dollar volume, earnings moves, market files, the security list, IV rank, the liquidity verdict, dividends. The screeners read these next day. | Screeners |
| 10:15 next trading day | The review is rebuilt as `final` for the session before. | Reports → eod |

The supervisor runs dozens of jobs in all, many of them quiet housekeeping. The ones above are the ones
you will notice.

### What is on and what is off

A fresh install runs MEIC and, if you installed Dolt, earnings, both on paper. The experimental modules
(calendars, contango, weeklyput, ensemble) and the retired ones (flies, bwb, PMCC, curve) ship switched off, and
so does the AI advisor and the written narratives. The daily evaluation of your real account is also
off until you turn it on with the "Daily position evaluation" switch on Config's `rules` tab, because it reads your
account every day. A job that is off still appears in the supervisor's table, with the reason it is
off. That is healthy.

To see every job and when it last ran:

```
python packages/orchestrator/run.py status
```

Each daily job lists the time it is due. Use the **Config** page's `timing` tab to move times. Change
one thing at a time, and not in the middle of a session. See [system-and-config.md](system-and-config.md).

### Live trading is not on this timetable

Nothing here places a real order. MEIC's live loop runs only on a day you have armed it yourself, and
disarms itself at 17:00 ET. See [live.md](live.md).

## Your daily routine

It takes about five minutes. You can skip days.

### Each morning, before the open

1. **Glance at the status bar** along the bottom of any page. Small ages and green chips mean every
   producer is working. `watchdog OK` is the one to look for.
2. **Open Reports → morning** (`g r`). Read the phase and, if it is not GREEN, the gate that blocked
   it. The phase is a summary of conditions, not an order. See [reports.md](reports.md).
3. **If you use the account pages,** open Live → today (`g l`) for the list of what needs a person.
   See [live.md](live.md).

### Each evening, after 16:30

1. **Open Reports → eod.** Start with **Needs attention**. A green `clear` means every module ticked and
   none errored. The review is `provisional` until 10:15 the next morning, so a module that settles
   overnight shows its result then.
2. **Open a module's page** (`1` for MEIC) and read `today`. If it did not trade, `decisions` or
   `attempts` says why. A quiet day is usually a rule doing its job.
3. **Open System → health** (`g s`) once. Green means nothing is stale.

You do not need to do anything else. Do not judge an arm on one day.

## What a normal first week looks like

### The first day

- **Most pages are empty.** MEIC says it has no ledger yet, the review has nothing to build from, and
  Compare and Backtest show nothing. That is expected until a module has taken a trade.
- **The first morning pack arrives the next trading morning at 08:30.** Until then Reports → morning says
  "No morning pack has been written yet."
- **A day started after 09:30 is a partial day.** The streamer records the opening range only between
  09:30 and 09:35 while it is running, so a session you installed into has no opening range. This is
  not a fault.
- **With Dolt, the first data pull is large.** Earnings and the technicals pages stay empty until the
  05:30 pull and the jobs after it have run once.

### The first few days

- The paper modules take their first trades, and `today`, `positions` and `history` start to fill in.
  Not every day has a trade. A volatility, event or price rule can say no, and the reason is written
  down.
- Reports → eod appears after the first 16:30 and turns `final` the next morning at 10:15.
- Results are **provisional** until a module has enough sessions to read. A row built on few trades is
  marked thin or "too few to read", and it is never evidence. Give it weeks, not days.
- Paper results are an upper bound on live. Fees, settlement and slippage are in their own columns.
  P&L means net. See [strategy-pages.md](strategy-pages.md).

### By the end of the week

- Each module has several sessions behind its numbers. The arms can be read side by side, still with
  caution.
- The system health page has been green on most mornings, and you know what a normal day looks like.
  That is the point of the week: when something changes, you notice.

## What not to worry about

- **A weekend or holiday with nothing running.** Only a few overnight and keep-alive jobs
  run.
- **A job that has not run yet.** A daily job that is not yet due shows no last run. The review jobs
  show as not run before 16:30 and 10:15.
- **The streamer looking idle overnight,** or an old event age out of hours. Supervision is
  session-gated and starts at 09:15.
- **A stale warning between about 09:15 and 09:32.** The paper loops first write after 09:30, so a
  check in that gap says the data is stale and clears itself on the first tick after the open. Look
  again only if it is still there at 09:35.
- **`dxlink idle`** in the status bar. It means no page is watching live quotes.
- **A job shown as disabled with a reason** naming a config switch or a missing capability.
- **A quiet day with no entries.**
- **Retired modules listed as "could not be read"** on the review. They have no ledger.
- **An amber version chip.** Update when it suits you.

## When to worry

- **`watchdog` is not OK for more than about 20 minutes,** or its age keeps growing. Something has
  stopped, and the supervisor is the first thing to check.
- **The streamer chip is red during market hours,** or its warning repeats tick after tick. One stale
  warning is cleared by the automatic restart; repeated ones mean the feed or the broker login is broken.
- **A module's loop "did not tick"** on the review, on a day it should have.
- **A critical notification,** such as `supervisor.down`, an earnings entry that did not run by 16:20,
  or a live book left open after the close.
- **`run.py doctor` shows `[FAIL]`.** Warnings are non-blocking, but a failure needs action.
- **You have been away and the computer slept.** Every job in that gap missed its time.

For each of these, the next page is [troubleshooting.md](troubleshooting.md). If the machine is a laptop, read
[always-on-machine.md](always-on-machine.md) in your first week, not after the first miss.
