---
description: Arm (or disarm) the flies LIVE trading loop for TODAY, gated behind a required literal YES confirmation
argument-hint: [--stop]
---

Arm the flies live loop for today — real money, real orders, against the real designated
account. Arming writes the **arm record** (`~/.cherrypick/state/flies-live-arm.json`), which the
orchestrator's supervisor daemon reads to enable its `flies-live` job (one
`cherrypick.flies.live_loop --once --live` tick per minute, with burst fill-watchers spawned as
needed); on a box without a running supervisor, arming falls back to registering the legacy
`cherrypick-flies-live-loop` OS task. The loop **self-disarms at `live.disarm_time` (default
17:00 ET)** — disarming deletes the arm record, and any tick that finds a stale record disarms
immediately — arming is per-day by design, and the orchestrator watchdog backstops with the
suite halt flag. This command exists so nothing live ever starts without a fresh, explicit
confirmation, a current status readout, and a visible way to stop.

`--stop`: disarm — see "Stop" below. Anything else: the arm flow.

## Arm (default)

1. **Never reuse an old confirmation.** Ask fresh every time this command runs — a prior
   session's "YES" (in this conversation or any past one) covers nothing.

2. **Pre-flight status readout** — gather and show all of this before asking, so the YES is
   informed, not reflexive.
   ```bash
   python -m cherrypick.flies.live_loop --status
   ```
   and present, in plain language:
   - **Unmet readiness gates** — additionally run the readiness check:
     ```bash
     python -c "
   from cherrypick.flies.cli import load_config
   from cherrypick.flies import live_loop, credentials as creds
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
   - **Open live positions / pending orders / breaker state** from the `--status` JSON, plus
     whether today's session is already settled.
   - **Orphaned orders**: if `--status` shows `orphaned_orders > 0`, **stop here** — the last
     tick's broker-truth sweep found working orders the ledger has never heard of. That must be
     resolved in the broker UI before arming anything.
   - **Market state**: whether it's currently a trading day inside RTH. Off-hours arming is
     allowed (the ticks no-op until the open) but say so plainly.
   - **Supervisor liveness**: check `~/.cherrypick/state/supervisor.last.json` is fresh (< ~90s
     old). If it's stale or absent, say so PROMINENTLY — on a supervisor-driven box **nothing
     will tick if the supervisor is down**; run `python packages/orchestrator/run.py
     ensure-supervisor` (or check the `cherrypick-supervisor` anchor task) before arming. Arming
     on a legacy (schtasks) box doesn't need this.
   - **Order-alert daemon** (only when `live.use_order_alert_daemon` is true): `--status` carries
     an `alert_daemon` block. Report it, but treat it as INFORMATIONAL — the daemon only makes
     fills get *noticed* sooner; a dead or missing one costs latency and nothing else, and is
     never a reason to refuse to arm. If it reports `running: true` with a stale `heartbeat_at`,
     say so (that's the silently-dead-websocket tell) — arming will restart it anyway.
   - **The per-day contract**: state that this arms TODAY only — the loop self-disarms at the
     configured `disarm_time` and tomorrow needs a fresh `/live-flies-start`.

3. **Show the disclaimer, verbatim, every time** (from `DISCLAIMER.md` at the repo root; never
   paraphrase it, shorten it or skip it because it was shown before):

   > ⚠️ **EXPERIMENTAL PROTOTYPE — EDUCATIONAL USE ONLY — NOT FINANCIAL ADVICE.** This places
   > **REAL, irreversible orders with real money** in your brokerage account. Options trading carries
   > substantial risk of loss and is not suitable for all investors. The software can fail (bugs,
   > stale data, outages); its limits reduce accidents but do not make trading safe. **You alone are
   > responsible for every order and every loss.** Full text: `DISCLAIMER.md`.

   Then **ask for explicit confirmation** using the AskUserQuestion tool (never free-text parsing)
   with a question naming the masked account, the arm/symbol, the concurrency rule (one
   incomplete position at a time), and the self-disarm time — exactly two options:
   **"YES — I accept the disclaimer; arm live trading for today"** and **"No, cancel"**. Anything but the literal YES
   option stops here with no action taken.

4. **Arm.** Once YES is confirmed:
   - If the halt flag exists, delete it now (`~/.cherrypick/state/halt-live.flag`) — the YES
     covers this explicitly; say that it was cleared and why it existed if known.
   - ```bash
     python -m cherrypick.flies.live_loop --install-task
     ```
     This writes today's arm record (the supervisor enables its `flies-live` job within one
     pass — the JSON output's `driver` field says which path armed: `supervisor` or the legacy
     `schtasks`) and fires the first tick immediately.
   - **Only if `live.use_order_alert_daemon` is true**, also start the order-alert daemon,
     detached and headless, after first stopping any stale one:
     ```bash
     python -m cherrypick.flies.alert_daemon --stop
     pythonw -m cherrypick.flies.alert_daemon
     ```
     It self-exits at `disarm_time`. If it fails to start, say so and CONTINUE — the loop
     confirms fills without it (just later); a failed daemon never blocks arming.

   **If the JSON carries a `warning`, report it first and prominently.** Today it means a
   quarter-end session: arming succeeded, but the loop places no new entries all day (fills,
   resting orders and settlement still run), and the watchdog posts the same warning to Discord.

4b. **The intraday agent, chosen for today** (packages/flies/docs/intraday-agent-plan.md). Only when
   `--status`'s `intraday_agent.enabled` is true; otherwise skip this step and say the agent is off.
   - Show the `intraday_agent` block from `--status`: the model, `live_mode_max`, the modes on
     offer (`offered_modes`), and for each criterion its value and pass. "null" means it cannot be
     scored yet.
   - Ask a SECOND, separate question with the AskUserQuestion tool, never folded into the YES.
     Offer one option per mode in `offered_modes` and nothing else:
     - **"No agent today"** (`off`);
     - **"Shadow — record decisions, act on nothing"** (`shadow`);
     - **"Gates — the agent may block live entries on the trend's losing side; it never places an
       order"** (`gates`).
     Mark "Shadow" as recommended while gates is not on offer. In the question, name each locked
     mode and what it still waits for. `gates_and_closures` is never offered: there is no live
     closing-order path.
   - Write the answer with `python -m cherrypick.flies.live_loop --agent-mode <off|shadow|gates>`,
     and report its JSON. A refusal (`ok: false`) leaves live exactly as armed, without the agent.
     Say so, and do not retry with another mode unless the user asks.
   - The choice is for today only. It lives on the arm record and goes with it at disarm or
     `--stop`.

5. **Verify + report**: confirm `--status` now shows `armed_for` = today, and (supervisor-driven)
   that `python packages/orchestrator/run.py status` shows the `flies-live` job enabled with a
   future `next_run`. Then report: the driver (supervisor job or legacy task), the armed-for
   date, the self-disarm time, the log to watch
   (`tail -f ~/.cherrypick/logs/flies/flies_live.log`), the console's flies page with its
   mode set to live (http://127.0.0.1:5070/flies), and how to stop early: `/live-flies-start --stop`,
   or create the halt flag (`~/.cherrypick/state/halt-live.flag` — stops new entries within one
   tick; open positions still follow their normal hold-to-settlement rules), or
   `python -m cherrypick.flies.live_loop --uninstall-task` directly. Also mention settlement: the loop
   settles provisionally at 16:20 from the last streamed trade, and the official-print confirm
   is `python -m cherrypick.flies.live_loop --settle --price <official>`.

## Stop / disarm (`--stop`)

1. Show current state first: `python -m cherrypick.flies.live_loop --status` (armed? open positions?
   pending orders?).
2. Confirm the disarm (a plain question is fine — disarming only stops NEW ticks; it places
   no order and touches no open position).
3. ```bash
   python -m cherrypick.flies.live_loop --uninstall-task
   ```
   This deletes the arm record (the supervisor's `flies-live` job disables within one pass) and
   removes the legacy scheduled task if one exists.
   Then, if `live.use_order_alert_daemon` is true, also stop the alert daemon (it holds an
   authenticated broker session open; disarming should not leave one running):
   ```bash
   python -m cherrypick.flies.alert_daemon --stop
   ```
4. Report what was disarmed and remind: open live positions are unaffected — they follow their
   normal hold-to-settlement rules, and the completion/cutoff/settlement handling for anything
   already open requires either re-arming or manual `--once --live` ticks. If the intent is a
   harder stop that also blocks manual/future ticks, mention the halt flag.

## Escape hatch

A single manual live tick (no task, no watcher respawn beyond this tick's own) is:
```bash
python -m cherrypick.flies.live_loop --once --live
```
The rung-0 dry-run smoke (preflights against the real account, places nothing) remains:
```bash
python -m cherrypick.flies.live_loop --once
```
