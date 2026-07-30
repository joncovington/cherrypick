---
description: Start (or stop) the flies LIVE trading loop, gated behind a required literal YES confirmation
argument-hint: [--stop]
---

Fire real ticks of the flies live loop (`packages/flies/src/live_loop.py --live`) against the real
designated account — real money, real orders. This is deliberately **not** a one-line wrapper: it
exists specifically so nothing live ever starts without an explicit, freshly-typed confirmation, a
current status readout, and a visible way to stop.

`--stop` (or no live session currently running): stop the background tick loop started by a prior
`/live-flies-start` — see "Stop" below. Anything else: the start flow.

## Start

1. **Refuse silently reusing an old confirmation.** This command must ask fresh every time it runs —
   never treat a prior session's "YES" (in this conversation or a past one) as covering a new
   invocation.

2. **Pre-flight status readout** — gather and show all of this before asking for confirmation, so the
   "YES" is informed, not reflexive:
   - `readiness()`: run
     ```bash
     python -c "
import sys; sys.path.insert(0, 'packages/flies/src')
from cli import load_config
import live_loop, credentials as creds, os
cfg = load_config()
halt = os.path.exists(live_loop.halt_flag_path())
designated = creds.designated_account()
unmet = live_loop.readiness(cfg, halt_present=halt, designated=designated)
print('unmet gates:', unmet or 'NONE -- readiness passes')
print('arm:', (cfg.get('live') or {}).get('arm'))
print('gate0_confirmed:', (cfg.get('live') or {}).get('gate0_confirmed'))
print('designated account:', ('****' + designated[-4:]) if designated else None)
print('halt flag present:', halt)
"
     ```
     If `unmet gates` is non-empty, **stop here** and report the unmet gates — do not proceed to
     confirmation. There is nothing to confirm if the loop would refuse to act anyway.
   - **Current live positions and concurrency state** for the configured arm/symbol (today's date):
     query `live_trades.db` directly (read-only) for open rows, and for each one show
     `position_id`, `kind`, whether it's currently blocking (`live_loop._is_blocking`), and — for a
     completed fly — its floor via `fly.position_floor`. If nothing is open, say so plainly ("no open
     live positions — the next tick may enter").
   - **Daily-loss breaker state**: `live_loop.daily_loss_tripped(conn, today, daily_loss_halt_dollars)`
     against the live ledger.
   - **Market state**: is it currently within regular trading hours (roughly 09:30–16:00 ET) on a
     trading day? If not, say so — the loop will preflight/tick but the entry/completion gates will
     naturally decline outside market hours; don't imply an off-hours confirmation will immediately
     trade.

3. **Ask for explicit confirmation** using the AskUserQuestion tool (not free-text parsing) with a
   question naming the account (masked), the arm/symbol, and the concurrency rule, and exactly two
   options: **"YES — start live ticking"** and **"No, cancel"**. Anything other than the literal YES
   option must not proceed — including an ambiguous or partial answer. If the user picks "No", stop
   here and take no further action.

4. **Start the tick loop, detached and observable.** Once YES is confirmed, run the loop in the
   background so this conversation isn't blocked for the rest of the session, on a cadence matching
   flies' paper loop (2 minutes) — but only during a reasonable trading window, and stopping itself at
   the close or on the halt flag:
   ```bash
   cd packages/flies/src && nohup bash -c '
     while true; do
       now_min=$(python -c "import datetime,zoneinfo; n=datetime.datetime.now(zoneinfo.ZoneInfo(\"America/New_York\")); print(n.hour*60+n.minute)")
       if [ -f "$HOME/.cherrypick/state/halt-live.flag" ]; then
         echo "$(date): halt flag present, stopping"; break
       fi
       if [ "$now_min" -ge 960 ]; then
         echo "$(date): past 16:00 ET, stopping"; break
       fi
       python live_loop.py --once --live 2>&1
       sleep 120
     done
   ' > "$HOME/.cherrypick/logs/flies/live_loop_manual.log" 2>&1 &
   echo "started, pid $!"
   ```
   Run this via the Bash tool with `run_in_background: true` regardless (belt and suspenders — the
   `nohup`/`&` above already detaches it from this shell, but the tool's own backgrounding keeps this
   conversation free either way). Record the PID reported.

5. **Report** the PID, the log path, the confirmed arm/symbol, and how to check on it
   (`tail -f ~/.cherrypick/logs/flies/live_loop_manual.log`) or stop it (`/live-flies-start --stop`, or
   directly: create the halt flag, or `kill <pid>`). Remind that the halt flag
   (`~/.cherrypick/state/halt-live.flag`) is the suite-wide kill switch and stops new entries within one
   tick (existing positions still follow their normal hold-to-settlement rules).

## Stop (`--stop`)

1. Find the running tick-loop process (the `nohup bash -c '...while true...'` loop from step 4 above,
   or a plain `python live_loop.py` process) — check for a tracked PID from this conversation first;
   otherwise look for a matching process by command line.
2. Confirm before killing anything: show what was found, ask for confirmation the same way any
   destructive action would (a plain "stop this?" is fine here — this only stops a polling loop, it
   does not touch open positions or place any order).
3. Kill it, confirm it's no longer running, and report. Existing open live positions are unaffected —
   they still follow their normal hold-to-settlement rules; stopping the loop only stops new
   entries/completions/cancels from being evaluated. If the intent is a harder stop, mention the halt
   flag (`~/.cherrypick/state/halt-live.flag`) as the suite-wide switch that also blocks the orchestrator
   and any future scheduled invocation, not just this manual loop.
