---
description: Arm (or disarm) the bwb LIVE trading loop for TODAY, gated behind a required literal YES confirmation
argument-hint: [--stop]
---

Arm the bwb live loop for today — real money, real SPX orders, against the real designated
account. Arming writes the **arm record** (`~/.cherrypick/state/bwb-live-arm.json`), which the
orchestrator's supervisor daemon reads to enable its `bwb-live` job (one
`cherrypick.bwb.live_loop --once --live` tick per minute). There is no scheduled-task fallback:
without a running supervisor, arming is refused. The loop **self-disarms at `live.disarm_time`
(default 17:00 ET)** — disarming deletes the arm record, and any tick that finds a record that is
not today's disarms immediately — arming is per-day by design, and the orchestrator watchdog
backstops with the suite halt flag. This command exists so nothing live ever starts without a
fresh, explicit confirmation, a current status readout, and a visible way to stop.

What the loop does once armed, in one paragraph, so the YES is informed: at the entry time
(default 10:00 ET) it places ONE broken-wing butterfly for the configured arm (`live.arm`), the
same structure the paper books enter, as a three-leg limit at mid minus the concession, walking
the limit down one tick every couple of minutes and never under the live floor (fees plus the
declared minimum net) until the cutoff (default 11:00) cancels it. `max_structures_per_day` is 1.
If the arm is not `control`, its add-on fires live on the same trigger the paper book uses, one
order per tick, recorded only when the broker confirms. Nothing is ever closed with an order: SPX
cash-settles on Friday and the ledger settles on the official print or waits. The daily ladder
means several live positions ride at once under `max_open_margin_dollars`.

`--stop`: disarm — see "Stop" below. Anything else: the arm flow.

## Arm (default)

1. **Never reuse an old confirmation.** Ask fresh every time this command runs — a prior
   session's "YES" (in this conversation or any past one) covers nothing.

2. **Pre-flight status readout** — gather and show all of this before asking, so the YES is
   informed, not reflexive.
   ```bash
   python -m cherrypick.bwb.live_loop --status
   ```
   and present, in plain language:
   - **Unmet readiness gates** — additionally run the readiness check:
     ```bash
     python -c "
   from cherrypick.bwb.cli import load_config
   from cherrypick.bwb import live_loop, credentials as creds
   import os
   cfg = load_config()
   unmet = live_loop.readiness(cfg, halt_present=os.path.exists(live_loop.halt_flag_path()), designated=creds.designated_account())
   print('unmet gates:', unmet or 'NONE -- readiness passes')
   "
     ```
     If any gate other than the halt flag is unmet, **stop here** and report — there is nothing
     to arm if every tick would refuse.
   - **Halt flag**: if `--status` shows `halt_flag: true`, surface it PROMINENTLY — the watchdog
     backstop may have set it after a failed self-disarm, which is worth understanding before
     re-arming. Only a fresh YES may clear it (step 4).
   - **The arm, the caps and the floor** from the module config's `live` block: `arm`,
     `max_structures_per_day`, `max_open_margin_dollars` (and the per-expiration cap if set),
     `min_net_credit_dollars`, `mark_drawdown_halt_dollars`. Say them out loud.
   - **Open live positions / pending orders / breaker state / open marked loss** from the
     `--status` JSON, plus whether today's session is already settled.
   - **Orphaned orders**: if `--status` shows any `orphaned_orders`, **stop here** — the last
     tick's broker-truth sweep found working orders the ledger has never heard of. That must be
     resolved in the broker UI before arming anything.
   - **Broker held**: if `--status` shows a non-empty `broker_held`, **stop here** — a prior
     submission's outcome is unknown or an order was placed that no row records; the adapter
     refuses every live submission until a human acknowledges it.
   - **Market state**: whether it's currently a trading day inside RTH. Off-hours arming is
     allowed (the ticks no-op until the open) but say so plainly.
   - **Supervisor liveness**: check `~/.cherrypick/state/supervisor.last.json` is fresh (< ~90s
     old). If it's stale or absent, say so PROMINENTLY — **nothing will tick, and arming will be
     refused**; run `python packages/orchestrator/run.py ensure-supervisor` (or check the
     `cherrypick-supervisor` anchor task) before arming.
   - **The per-day contract**: state that this arms TODAY only — the loop self-disarms at the
     configured `disarm_time` and tomorrow needs a fresh `/live-bwb-start`.

3. **Ask for explicit confirmation** using the AskUserQuestion tool (never free-text parsing)
   with a question naming the masked account, the arm, `max_structures_per_day`, the margin cap,
   and the self-disarm time — exactly two options: **"YES — arm live trading for today"** and
   **"No, cancel"**. Anything but the literal YES option stops here with no action taken.

4. **Arm.** Once YES is confirmed:
   - If the halt flag exists, delete it now (`~/.cherrypick/state/halt-live.flag`) — the YES
     covers this explicitly; say that it was cleared and why it existed if known.
   - ```bash
     python -m cherrypick.bwb.live_loop --install-task
     ```
     This writes today's arm record (the supervisor enables its `bwb-live` job within one pass)
     and fires the first tick immediately. A refusal (`ok: false`, no supervisor) is final: report
     it, do not work around it.

5. **Verify + report**: confirm `--status` now shows `armed_for` = today, and that
   `python packages/orchestrator/run.py status` shows the `bwb-live` job enabled with a future
   `next_run`. Then report: the armed-for date, the arm, the self-disarm time, the log to watch
   (`tail -f ~/.cherrypick/logs/bwb/bwb_live.log`), the live ledger
   (`~/.cherrypick/data/bwb/live_trades.db`, read by `python packages/orchestrator/run.py report
   --live`), and how to stop early: `/live-bwb-start --stop`, or create the halt flag
   (`~/.cherrypick/state/halt-live.flag` — stops new entries and add-ons within one tick; open
   positions still ride to settlement), or `python -m cherrypick.bwb.live_loop --uninstall-task`
   directly. Also mention settlement: on an expiration Friday the loop settles after 16:20 ONLY
   on an official print it fetches itself (tastytrade's posted close, else Yahoo/Barchart); if
   none arrives before self-disarm, settle by hand that evening or the next morning with
   `python -m cherrypick.bwb.live_loop --settle --price <official SET> --date <YYYY-MM-DD>`.

## Stop / disarm (`--stop`)

1. Show current state first: `python -m cherrypick.bwb.live_loop --status` (armed? open positions?
   pending orders?).
2. Confirm the disarm (a plain question is fine — disarming only stops NEW ticks; it places
   no order and touches no open position).
3. ```bash
   python -m cherrypick.bwb.live_loop --uninstall-task
   ```
   This deletes the arm record; the supervisor's `bwb-live` job disables within one pass.
4. Report what was disarmed and remind: open live positions are unaffected — they ride to
   settlement, and a resting entry or add-on order left working at the broker is NOT cancelled by
   disarming (it dies at the day's close as a Day order, or cancel it in the broker UI). Any
   pending fill confirmation and Friday's settlement need either re-arming or manual
   `--once --live` ticks. If the intent is a harder stop that also blocks manual ticks, mention
   the halt flag.

## Escape hatch

A single manual live tick is:
```bash
python -m cherrypick.bwb.live_loop --once --live
```
The rung-0 dry-run smoke (preflights against the real account, places nothing, records nothing
but the journal line) remains:
```bash
python -m cherrypick.bwb.live_loop --once
```
