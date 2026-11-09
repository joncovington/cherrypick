---
description: Arm (or disarm) the MEIC LIVE trading loop for TODAY, gated behind a required literal YES confirmation
argument-hint: [--stop]
---

Arm the MEIC live loop for today. That means real money, real 0DTE iron condors on the pinned
`live.symbol` (read it from `--status`; never assume SPX), against the real designated account. Arming writes the **arm record**
(`~/.cherrypick/state/meic-live-arm.json`). The orchestrator's supervisor runs the `meic-live` job,
one `cherrypick.meic.live_loop --once --live` tick per minute; unarmed it only manages what the book
holds, and the record is what lets it open new ICs. Without a running supervisor, arming is
refused. The loop **self-disarms at `live.disarm_time` (default 17:00 ET)**:
disarming deletes the arm record, and any tick that finds a record that is not today's disarms at
once. Arming is per-day by design, and the orchestrator watchdog backstops with the suite halt
flag. `enable_live_trading` in `~/.cherrypick/config/meic.json` stays the module's master switch;
the arm record is what makes one day live.

What the loop does once armed, so the YES is informed: inside the live entry window (the `live`
block's `entry_window_start`/`entry_window_end`, over `live.arm` or the top level) it evaluates
the same entry function the paper arms use, with live's own gates (IV rank floor, GEX block, OTM
floors, VIX/ATR pauses). When an IC clears them and fits under `max_open_bp_dollars` together with
what is already open, it places one four-leg iron condor on `live.symbol` as a limit order, at the
width from the `live` block's `wing_widths_by_symbol`, else the arm's (or the top level's). `live.max_structures_per_day` caps new ICs a day (absent: 1; `null`:
no count cap, sizing by buying power alone; a cancelled entry never counts). Exits depend on
`per_side_stop_management` -- READ IT from `--status` and say it: when true each side is stopped
at `stop_trigger_ratio` (re-priced every tick), also read from `--status`, never from the config's
top level, which `live.arm` overrides; when false every IC is held to settlement and the
worst case per IC is its width less its credit. A cash-settled symbol (SPX, XSP) is never
force-closed at 15:45 -- only on an FOMC day (from 13:30) or a triple-witching/quarterly expiry
day (from 14:00) -- and a held side settles ONLY on the official closing print (tastytrade's
posted close, else Yahoo/Barchart); until one is posted the IC stays open and the next tick asks
again, and a session the machine missed is settled by the hourly `settle-overdue-live` job at
that day's close. Every fill is recorded only on the broker's confirmation. Each tick first
sweeps the broker's working orders on the symbol; one that no live ledger knows (MEIC's, flies'
or bwb's) blocks new entries until a person looks, and an entry opposite a leg another live loop
holds is refused.

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
   - **The verdict first** (`arm_ready`). If `ready` is false, **stop here**: report each of its
     `blockers` in plain words and what resolves it, and ask nothing. `--install-task` refuses
     while any blocker stands, so there is nothing to work around. Every warning in `warnings`
     is said out loud below. The items that follow are what the verdict is made of.
   - **Unmet gates** (`unmet_gates`). If any is unmet (`enable_live_trading`, `live.symbol`,
     `live.gate0_confirmed`, the designated account, a bad `live.arm`), **stop here** and report.
     There is nothing to arm if every tick would refuse.
   - **Kill criteria** (`kill`): `blocked` stops here; the reasons say why (a latched drawdown, a
     closed IC with no net recorded, unwritten criteria).
   - **Halt flag**: if `halt_flag` is true, surface it PROMINENTLY. The watchdog backstop may have
     set it after a failed self-disarm, which is worth understanding before re-arming. Only a
     fresh YES may clear it (step 4).
   - **What live trades, said out loud**: `symbol`, `arm` (null = the top level), `entry_window`,
     `max_open_bp_dollars`, `max_structures_per_day`, `per_side_stop_management` and `stop_trigger_ratio`, the width from
     the `live` block's `wing_widths_by_symbol` (else the arm's or the top level's), and the `daily_loss_halt_dollars` breaker from the
     config's `live` block. If the breaker is unset, say so PROMINENTLY and offer to set it before
     the YES -- with stops off it is the only intraday brake besides the halt flag.
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
   - **Order-alert daemon** (only when `live.use_order_alert_daemon` is true): arming names it on
     the arm record as a companion, and the supervisor keeps it up until `disarm_time`, restarting
     it on death or a silent heartbeat. Informational: it only gets fills noticed sooner, and is
     never a reason to refuse to arm.
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
   Name the masked account, the symbol and width, the entry window, `max_structures_per_day`, the
   BP cap, whether stops are on, and the self-disarm time. Offer exactly two options: **"YES — I accept the disclaimer; arm live
   trading for today"** and **"No, cancel"**. Anything but the literal YES option stops here with
   no action taken.

4. **Arm.** Once YES is confirmed:
   - If the halt flag exists, delete it now (`~/.cherrypick/state/halt-live.flag`). The YES covers
     this explicitly; say that it was cleared, and why it existed if known.
   - ```bash
     python -m cherrypick.meic.live_loop --install-task
     ```
     This is the agent path: it prompts for nothing, because the YES above is the confirmation.
     (A person without Claude Code arms with `--arm --confirm YES`, which prints the disclaimer
     itself; never pass that flag on a YES you did not get this run.)
     It writes today's arm record (the supervisor enables `meic-live` within one pass) and fires
     the first tick immediately. A refusal (`ok: false`: a blocker named in `blockers`, such as
     no supervisor) is final: report it, do not work around it.

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
   --disarm` directly.

## Stop / disarm (`--stop`)

1. Show current state first: `python -m cherrypick.meic.live_loop --status` (armed? open
   positions? pending orders?).
2. Confirm the disarm (a plain question is fine: disarming stops NEW ICs; it places no order, and
   the loop keeps managing what it holds).
3. ```bash
   python -m cherrypick.meic.live_loop --disarm
   ```
   This deletes the arm record. Within one pass the supervisor moves `meic-live` to its unarmed
   schedule: trading days from the open to the disarm cutoff, managing only.
4. Report what was disarmed. Tell the user: **an open IC is still managed after disarming** --
   fills confirmed, resting stops polled, stops and force-closes sent, and the side settled on the
   official print, until the disarm cutoff (`disarm_time` plus grace). Entries are refused as
   `not_armed`. A resting entry order is NOT cancelled by disarming; a fill on it is confirmed and
   managed, or cancel it in the broker UI. An IC still held after the cutoff is settled by the
   hourly `settle-overdue-live` job at that day's official close, or by hand with `python -m
   cherrypick.meic.live_loop --settle --date <YYYY-MM-DD> --price <official close>`. Turning
   `enable_live_trading` off stops management too: nothing at the broker is touched, and the
   watchdog's `live_unmanaged` is CRITICAL while positions are held. To stop new trades and keep
   managing, use the halt flag or disarm, never the master switch.

## Escape hatch

A manual live tick without today's arm record manages what the book holds and opens nothing (with
nothing held it ends at once):
```bash
python -m cherrypick.meic.live_loop --once --live
```
The dry-run tick (preflights against the real account, places nothing, records nothing) needs no
arm:
```bash
python -m cherrypick.meic.live_loop --once
```
