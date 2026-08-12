# Operating the Agent

**What this covers:** the day-to-day routine for running a MEIC session once it's set up —
starting the loop, watching the console, and checking status. Part of the
[MEIC module](../README.md) in the cherrypick suite.

## Pre-market session setup

Run `/meic-start` before 9:30 ET — it verifies the shared market-data streamer, then starts the agent loop:

```
/meic-start
```

This checks (and starts, if down) the standalone streamer producer (`packages/streamer`), then
starts the agent loop directly. To watch the session, open the console with `/console` — it is
already running. The agent will not enter new trades before `entry_window_start` (default 10:00 ET) or after `entry_window_end` (default 14:30 ET). At end of day it force-closes non-cash-settled positions before the bell (`physical_settlement_force_close_time`/`force_close_time`) and leaves cash-settled positions to expire and settle in cash (`expiration_settlement_time`); either way, starting early is safe. On the first iteration of each trading day, the loop runs a **daily connection check** to verify the tastytrade broker session is live before any market assessment begins.

To start components individually instead:

**Read surface** — opens the console at `http://127.0.0.1:5070/meic` (already running; the supervisor
keeps it up):
```
/console
```

**Loop** — begins the MEIC agent iterations:
```
/loop
```

---

## Starting the loop

Launch Claude Code from this package's folder (`packages/meic` — run `claude` in this directory), then start the loop **before 9:30 ET**:

```
/loop
```

The agent runs every ~2-30 minutes depending on session and open positions (see the loop cadence table in `CLAUDE.md`). The loop's own time gate (Step 2) skips all market-hours checks outside 09:30–15:55 ET, on weekends, or on a NYSE holiday, so starting early or leaving it running after close is safe — it will not attempt to trade outside market hours. New entries are additionally blocked before `entry_window_start` (default 10:00 ET) to avoid open-bell volatility.

---

## Paper trading

Before committing real capital, run the parallel-shadow paper engine. It evaluates every enabled forward-test stream (`control`/`open`/`width-5`/`width-10` — see `docs/paper-experiments.md`) against the same live-quote snapshot per symbol, each on its own $100,000 virtual bankroll, and never touches the live account or the live `meic_trades.db`.

Start a full unattended paper session:

```
/paper-start
```

This starts the shared DXLink streamer and registers a Windows scheduled task (`cherrypick-meic-paper-loop`) that runs `python -m cherrypick.meic.paper_loop --once` every 2 minutes — headless, time-gated to market hours, self-healing, and persistent across sessions. At the 16:00 ET settlement pass it writes both deterministic end-of-day files — `logs/meic/paper-eod-<date>.md` (metrics) and `logs/meic/eod-analysis-<date>.md` (the 7-section analysis).

Manage the session directly:

```bash
python -m cherrypick.meic.paper_loop --status          # task status + open-position count
python -m cherrypick.meic.paper_loop --once            # run a single manual iteration
python -m cherrypick.meic.paper_loop --eod-report      # regenerate both paper EOD files (metrics + analysis; --date to backfill)
python -m cherrypick.meic.paper_loop --eod-analysis    # regenerate just the 7-section analysis
python -m cherrypick.meic.paper_loop --uninstall-task  # stop the unattended session
```

For a multi-day, profile-by-profile performance write-up (equity curves, risk-adjusted metrics, graduation-gate checklist):

```
/paper-report
```

On non-Windows hosts, run `python -m cherrypick.meic.paper_loop` in a terminal or wire a cron job to `--once`. See [paper-trading.md](paper-trading.md) for the engine design, fee model, historical-replay accelerator, and graduation criteria.

> **Inside the suite:** you don't have to manage this task yourself. The [orchestrator](../../orchestrator) registers and watchdogs the same `cherrypick-meic-paper-loop` task (via `cherrypick install`), restarts a stalled streamer, sends notifications, and adds the cross-module read side (`cherrypick report` / `calibrate` / the console). It drives this module by subprocess only — it never places live orders or edits this config. Running `/paper-start` here is the standalone equivalent, minus the watchdog and notifications.

---

## Checking status during the day

```
/meic-status
```

Prints a live summary of open positions, today's P&L, and the last few loop actions without interrupting the running loop.

---

## The read surface

The console is the suite's one read surface: `http://127.0.0.1:5070/meic`, opened with `/console`.
The supervisor keeps it running, so there is nothing to start. It reads **both** ledgers and tags
every row with the mode it came from — so paper and live are separated by the data rather than by
which port you opened, which is what the old two-port arrangement (5050 live / 5051 paper) did.
It also defaults to the module's own `CURRENT_ERA`, the way this module's analytics do, with earlier
eras reachable through a visible scope control.

**Today view**
- Multi-period stats grid — Net P&L, total trades, wins, losses, and W/L ratio across today / this week / this month / this year / all-time (live trades only)
- Trades table — each IC with entry time, strikes, wing width, per-spread credits, per-spread stop status badges (e.g. `STOPPED 11:21`), and P&L

**History view**
- NLV trend chart — account value over all days where the EOD sequence has run
- Session win rate breakdown
- Exit reason breakdown
- Avg P&L by IV rank bucket
- All-time fee drag summary

The console reads `meic_trades.db` and `paper_trades.db` from the data home
(`~/.cherrypick/data/meic/` by default), read-only. In paper scope the Performance view can be
filtered by risk profile as well as by symbol.

The compact suite-dashboard card this module used to emit (`cherrypick.meic.section`) went with the
suite dashboard on 2026-08-12. The console reads the ledger directly, so there is no payload to keep
in sync any more — which is why deleting the card cost nothing.

---

## Verifying chain and strike selection

Before the first live session, or after any tastytrade SDK update, run:

```
/check-chain
```

This calls `get_market_overview`, `get_option_chain`, and `get_strategies` against today's expiration (or the next trading day if the market is closed), then cross-checks that:
- Greeks and quotes are complete
- `get_strategies` used live greeks for strike selection (not a positional fallback)
- The selected strikes appear within the chain window
- Short strike deltas are within ±0.05 of `delta_target`

A **PASS** result means the chain and strike selection are ready. A **NEEDS ATTENTION** result identifies the specific failing check.

---

## End-of-day report

After 15:55 ET the agent automatically spawns the `/eod-report` skill, which:

1. Reads today's trades and loop log
2. Writes a plain English analysis of entry quality, stop management, and what worked or didn't
3. Saves the analysis to the `daily_summary` table

You can also trigger it manually at any time. `/eod-report` accepts a scope argument — `both` (default), `live`, or `paper` — and an optional `--date YYYY-MM-DD`:

```
/eod-report                       # both live and paper reports for today
/eod-report live                  # live report only
/eod-report paper --date 2026-07-08
```

The **live** report is a plain-English synthesis (agent-written). The **paper** side is deterministic and
code-generated — two files per session in `~/.cherrypick/logs/meic/`:

- `paper-eod-<date>.md` — the terse metrics report (per-profile table, exits-by-reason, per-symbol P&L
  across all profiles).
- `eod-analysis-<date>.md` — a conversational **7-section** read on the same session (executive snapshot,
  position detail, trade log, risk metrics, market context, tax notes, notes/journal). Still rule-based,
  no agent; regenerate just this one with `python -m cherrypick.meic.paper_loop --eod-analysis [--date <d>]`.

The orchestrator's suite digest and (opt-in) AI insight build on these files — see the suite
[reporting docs](../../../docs/reporting-and-dashboard.md).

---

## Logs

All loop actions are written to `logs/agent.log` as newline-delimited JSON via `python -m cherrypick.meic.notify log_event --level <LEVEL>`. Each entry includes a timestamp, level (typically `INFO`, `WARN` for conflicts, or `CRITICAL` for escalated failures like a missed force-close on a non-cash-settled symbol), message, and optional structured data. Review `WARN`/`CRITICAL` entries after EOD to identify conflict patterns and refine agent behavior.

Every log file is size-capped with rotation (10 MB per file, 5 backups), so `agent.log`, `streamer.log`, and `paper_loop.log` never grow without bound. The paper daemon logs to `logs/paper_loop.log` in a human-readable one-line-per-iteration format.

The easiest way to watch the log live is the console's log card, which merges every module's tail
with level filters.

To tail from the terminal instead:

Logs live in the **logs home** — `~/.cherrypick/logs/meic/` by default (override with `MEIC_LOGS_DIR`, or
`CHERRYPICK_HOME` for the whole suite):

```bash
Get-Content ~/.cherrypick/logs/meic/agent.log -Wait -Tail 20   # PowerShell
tail -f ~/.cherrypick/logs/meic/agent.log                      # bash
```
