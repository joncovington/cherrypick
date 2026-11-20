---
description: Arm (or disarm) the ladder live gate for TODAY, behind a required literal YES confirmation. The ladder's order path ships in dry-run and off
argument-hint: [--stop]
---

Arm the ladder's live gate for today. **The ladder has an order path (issue #1337) that ships in
dry-run and off.** Arming writes the arm record. While `live.dry_run` is true (the shipped value) a
tick reads the broker and journals the orders it would send, and places and cancels nothing. Real
orders need `live.dry_run: false` set by hand, with every gate held. No supervisor job runs the tick
yet: it is `live_loop --once`, run by hand.

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
   `****1234`); `mode` (`dry-run` or `live`, from `live.dry_run`) and `held` (anything an uncertain
   submit is holding); `halt_flag` (it blocks new buys only and never touches resting sells); the
   live block's `arm`, `symbols`, `lot_dollars`, the lot caps, `max_open_dollars`
   and the drawdown breaker. A failing config gate, an unset account or an unknown arm is not
   fixed here: report it.
   Check `~/.cherrypick/state/supervisor.last.json` is fresh (under about 90 s old). A stale or absent
   heartbeat means arming is refused.

3. **Show the disclaimer, verbatim, every time** (`DISCLAIMER.md` at the repo root; never paraphrase
   or shorten it). Then ask for explicit confirmation with the AskUserQuestion tool, naming the masked
   account, the arm, the symbols and the self-disarm time, with exactly two options: **"YES, I accept
   the disclaimer; arm the ladder for today"** and **"No, cancel"**. Say plainly in the question that
   whether the loop is in dry-run (it sends nothing) or live (`live.dry_run` false: it places and
   cancels real orders). Anything but the
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
   self-disarm time, and the mode: in dry-run nothing will trade. Stop early with
   `/live-ladder-start --stop`, or `python -m cherrypick.ladder.live_loop --disarm`.

## Stop / disarm (`--stop`)

1. Show `python -m cherrypick.ladder.live_loop --status`.
2. Confirm with a plain question.
3. ```bash
   python -m cherrypick.ladder.live_loop --disarm
   ```
   This deletes the arm record. Report what was disarmed.

## Escape hatch

`python -m cherrypick.ladder.live_loop --once` runs one tick: it reads the broker and reconciles the
live ledger always, and plans orders only while armed. In dry-run (the shipped value) it sends nothing.
