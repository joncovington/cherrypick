Display the current MEIC session status as a clear, readable summary.

Run the following in parallel:
```bash
python -m cherrypick.meic.db get_open_trades
python -m cherrypick.meic.db get_today_count
python -m cherrypick.meic.db get_today_pnl
```

Then read today's loop log from the end-of-day summary command, which returns the day's `loop_log` rows newest first (use the first five):
```bash
python -m cherrypick.meic.db get_eod_summary
```

Present the results as a concise status report:

- **Open positions**: grouped by symbol; for each open/partial trade show order ID, strikes, net credit, current stop levels, status, and time entered
- **Today's summary**: total entries and net P&L to date, account-wide across all symbols (add a per-symbol breakdown if more than one symbol has activity today)
- **Last loop action per symbol**: the most recent `loop_log` row for each symbol that appears in the last 5 (rows with `symbol` = null are account-wide summary rows, not tied to one symbol — call these out separately)
- **Recent loop history**: brief summary of the last 5 actions, each labelled with its symbol
- **Any active conflicts or warnings**: flag if the most recent loop logged a WARN, and which symbol (if any) it pertains to

Keep the output brief and scannable — this is a quick check, not a full report.
