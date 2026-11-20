---
description: Arm (or disarm) the ladder live gate for TODAY, behind a required literal YES confirmation. The ladder has no order path yet
argument-hint: [--stop]
---

Arm the ladder's live gate for today. **The ladder has no order path yet (issue #1337): arming only
writes the arm record.** Nothing is bought, sold or cancelled, no supervisor job runs, and the record
does nothing until the order loop exists. The command is here so the arm ritual is the same one the
other modules use and is ready when the order path lands.

The arm record is `~/.cherrypick/state/ladder-live-arm.json`. It is good for its date only; the live
config's `disarm_at` (default 20:00 ET) ends the day, and tomorrow needs a fresh run of this command.

`--stop`: disarm, see "Stop" below. Anything else: the arm flow.

## Arm (default)

0. **Is ladder switched on?** Run the status readout (step 2) first and read the gate
   `modules.ladder.enabled`. If it is failing, stop here and say so; arm nothing and ask nothing.
   (`--install-task` repeats the check, and also refuses while `live.enabled` or `live.gate0_confirmed`
   is off.)

1. **Never reuse an old confirmation.** Ask fresh every time this command runs. A prior "YES", in this
   conversation or any past one, covers nothing.

2. **Pre-flight status readout**, shown before asking so the YES is informed:
   ```bash
   python -m cherrypick.ladder.live_loop --status
   ```
   Present in plain language: every gate with its verdict and reason; the designated account (masked,
   `****1234`); `halt_flag` (it blocks new buys only, and once there are orders it would not touch
   resting sells); the live block's `arm`, `symbols`, `lot_dollars`, the lot caps, `max_open_dollars`
   and the drawdown breaker. A failing config gate, an unset account or an unknown arm is not
   fixed here: report it.
   Check `~/.cherrypick/state/supervisor.last.json` is fresh (under about 90 s old). A stale or absent
   heartbeat means arming is refused.

3. **Show the disclaimer, verbatim, every time** (`DISCLAIMER.md` at the repo root; never paraphrase
   or shorten it). Then ask for explicit confirmation with the AskUserQuestion tool, naming the masked
   account, the arm, the symbols and the self-disarm time, with exactly two options: **"YES, I accept
   the disclaimer; arm the ladder for today"** and **"No, cancel"**. Say plainly in the question that
   the ladder has no order path yet, so this records the arm and places nothing. Anything but the
   literal YES option stops here with no action taken.

4. **Arm.** Once YES is confirmed:
   ```bash
   python -m cherrypick.ladder.live_loop --install-task
   ```
   This is the agent path: it prompts for nothing, because the YES above is the confirmation. (A person
   without Claude Code arms with `--arm --confirm YES`, which prints the disclaimer itself; never pass
   that flag on a YES you did not get this run.) A refusal (`ok: false`: a config gate off, the module
   off, no supervisor) is final: report it and do not work around it.

5. **Verify and report**: `--status` now shows `armed_today` true. Report the armed-for date, the
   self-disarm time, and that no order path exists yet so nothing will trade. Stop early with
   `/live-ladder-start --stop`, or `python -m cherrypick.ladder.live_loop --disarm`.

## Stop / disarm (`--stop`)

1. Show `python -m cherrypick.ladder.live_loop --status`.
2. Confirm with a plain question.
3. ```bash
   python -m cherrypick.ladder.live_loop --disarm
   ```
   This deletes the arm record. Report what was disarmed.

## Escape hatch

`python -m cherrypick.ladder.live_loop --once` evaluates readiness and logs that there is no order
path. It places nothing.
