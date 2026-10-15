---
description: Arm (or disarm) the MEIC LIVE trading loop for TODAY, gated behind a required literal YES confirmation
argument-hint: [--stop]
---

Arm the MEIC live loop for today. That means real money, real SPX 0DTE iron condors, against the
real designated account. Arming writes the **arm record**
(`~/.cherrypick/state/meic-live-arm.json`). The orchestrator's supervisor reads it to enable its
`meic-live` job, one `cherrypick.meic.live_loop --once --live` tick per minute. Without a running
supervisor, arming is refused. The loop **self-disarms at `live.disarm_time` (default 17:00 ET)**:
disarming deletes the arm record, and any tick that finds a record that is not today's disarms at
once. Arming is per-day by design, and the orchestrator watchdog backstops with the suite halt
flag. `enable_live_trading` in `~/.cherrypick/config/meic.json` stays the module's master switch;
the arm record is what makes one day live.

What the loop does once armed, so the YES is informed: inside the live entry window (the `live`
block's `entry_window_start`/`entry_window_end`, over `live.arm` or the top level) it evaluates
the same entry function the paper arms use, with live's own gates (IV rank floor, GEX block, OTM
floors, VIX/ATR pauses). When an IC clears them and fits under `max_open_bp_dollars`, it places ONE
four-leg SPX iron condor as a limit order. It opens at most `live.max_structures_per_day` ICs a
day (default 1; a cancelled entry does not count). Each side is stopped by the per-side stop
(`stop_trigger_ratio`, re-priced every tick) and force-closed at `force_close_time`. A held side
cash-settles at 16:00. Every fill is recorded only on the broker's confirmation. Each tick first
sweeps the broker's working SPX orders. One that no live ledger knows (MEIC's, flies' or bwb's)
blocks new entries until a person looks.

`--stop`: disarm (see "Stop" below). Anything else: the arm flow.

## Arm (default)

1. **Never reuse an old confirmation.** Ask fresh every time this command runs. A prior session's
   "YES" (in this conversation or any past one) covers nothing.

2. **Pre-flight status readout.** Gather and show all of this before asking, so the YES is
   informed, not reflexive.
   ```bash
   python -m cherrypick.meic.live_loop --status
   ```
   and present, in plain language:
   - **Unmet gates** (`unmet_gates`). If any is unmet (`enable_live_trading`, `live.symbol`,
     `live.gate0_confirmed`, the designated account, a bad `live.arm`), **stop here** and report.
     There is nothing to arm if every tick would refuse.
   - **Halt flag**: if `halt_flag` is true, surface it PROMINENTLY. The watchdog backstop may have
     set it after a failed self-disarm, which is worth understanding before re-arming. Only a
     fresh YES may clear it (step 4).
   - **What live trades, said out loud**: `symbol`, `arm` (null = the top level), `entry_window`,
     `max_open_bp_dollars`, `max_structures_per_day`, `per_side_stop_management`, and the
     `daily_loss_halt_dollars` breaker from the config's `live` block.
   - **Open positions / pending orders / `entries_today` / `breaker_tripped`**, plus whether
     today's session is already settled (`session_settled`).
   - **Orphaned orders**: if `orphaned_orders` is non-empty, **stop here**. The last tick's sweep
     found working orders no live ledger has heard of. Resolve them in the broker UI before arming
     anything.
   - **Broker held**: if `broker_held` lists anything unresolved or unrecorded, **stop here**. A
     prior submission's outcome is unknown, and the seam refuses every live order until it is
     resolved.
   - **Overdue settlement**: if `overdue_settlement` lists a past session, **stop here**. A past
     day's live IC was left open in the ledger and must be reconciled against the broker first.
   - **Market state**: whether it is a trading day inside RTH. Off-hours arming is allowed (the
     ticks no-op until the window) but say so plainly.
   - **Supervisor liveness** (`supervisor_fresh`): if false, say so PROMINENTLY. **Nothing will
     tick, and arming will be refused.** Run `python packages/orchestrator/run.py
     ensure-supervisor` before arming.
   - **The per-day contract**: this arms TODAY only. The loop self-disarms at the configured
     `disarm_time`, and tomorrow needs a fresh `/live-meic-start`.

3. **Show the disclaimer, verbatim, every time** (from `DISCLAIMER.md` at the repo root; never
   paraphrase it, shorten it or skip it because it was shown before):

   > ⚠️ **EXPERIMENTAL PROTOTYPE — EDUCATIONAL USE ONLY — NOT FINANCIAL ADVICE.** This places
   > **REAL, irreversible orders with real money** in your brokerage account. Options trading carries
   > substantial risk of loss and is not suitable for all investors. The software can fail (bugs,
   > stale data, outages); its limits reduce accidents but do not make trading safe. **You alone are
   > responsible for every order and every loss.** Full text: `DISCLAIMER.md`.

   Then **ask for explicit confirmation** with the AskUserQuestion tool (never free-text parsing).
   Name the masked account, the symbol, the entry window, `max_structures_per_day`, the BP cap and
   the self-disarm time. Offer exactly two options: **"YES — I accept the disclaimer; arm live
   trading for today"** and **"No, cancel"**. Anything but the literal YES option stops here with
   no action taken.

4. **Arm.** Once YES is confirmed:
   - If the halt flag exists, delete it now (`~/.cherrypick/state/halt-live.flag`). The YES covers
     this explicitly; say that it was cleared, and why it existed if known.
   - ```bash
     python -m cherrypick.meic.live_loop --install-task
     ```
     This writes today's arm record (the supervisor enables `meic-live` within one pass) and fires
     the first tick immediately. A refusal (`ok: false`, no supervisor) is final: report it, do
     not work around it.

   **If the JSON carries a `warning`, report it first and prominently.** Today it means a
   quarter-end session: arming succeeded, but the loop places no new entry all day (open ICs are
   still managed).

5. **Verify and report**: confirm `--status` now shows `armed_for` = today, and that
   `python packages/orchestrator/run.py status` shows the `meic-live` job enabled with a future
   `next_run`. Then report the armed-for date, the entry window, the self-disarm time, the log to
   watch (`~/.cherrypick/logs/meic/meic_live.log`, one JSON line per tick), the live ledger
   (`~/.cherrypick/data/meic/meic_trades.db`, read by `python packages/orchestrator/run.py report
   --live`), and how to stop early: `/live-meic-start --stop`, or create the halt flag
   (`~/.cherrypick/state/halt-live.flag`, which stops new entries within one tick while open ICs
   are still stopped, force-closed and settled), or `python -m cherrypick.meic.live_loop
   --uninstall-task` directly.

## Stop / disarm (`--stop`)

1. Show current state first: `python -m cherrypick.meic.live_loop --status` (armed? open
   positions? pending orders?).
2. Confirm the disarm (a plain question is fine: disarming only stops NEW ticks; it places no order
   and touches no open position).
3. ```bash
   python -m cherrypick.meic.live_loop --uninstall-task
   ```
   This deletes the arm record; the supervisor disables `meic-live` within one pass.
4. Report what was disarmed. Remind the user: **an open 0DTE IC is no longer stopped or
   force-closed once the loop is disarmed**. Its working stop orders stay at the broker, but no tick
   re-prices them. Either re-arm, run manual ticks, or manage it in the broker UI. A resting entry
   order is NOT cancelled by disarming (it dies at the close as a Day order, or cancel it in the
   broker UI). For a harder stop that keeps managing open ICs but blocks new ones, use the halt
   flag instead of disarming.

## Escape hatch

A single manual live tick needs today's arm record (a tick without one disarms and stops):
```bash
python -m cherrypick.meic.live_loop --once --live
```
The dry-run tick (preflights against the real account, places nothing, records nothing) needs no
arm:
```bash
python -m cherrypick.meic.live_loop --once
```
