# The advisor, re-read against the trading-book review (2026-10-25)

The advisor (`packages/advisor`) has admitted 39 paper experiments since 2026-08-21. None reached a
measured verdict: of the 14 with a verdict, 8 were `underpowered` and 6 `no_exposure` (the advised
book never diverged from its control); the rest were killed or expired before one. Read against the
rules the trading-book review added ([trading-book-review-plan.md](trading-book-review-plan.md)), the
process has four gaps. The user's decision (2026-10-25): **pause, then redesign around all four**.

## Done now

- **Paused** (`advisor.paused: true`): new experiments are admitted `queued` and nothing new
  activates; the active ones are re-issued and scored to their end, so no running sample is cut
  short. Proposals made while paused wait in the queue for review, held: lifting the pause activates
  none of them, and each runs only by an operator's `activate <id>` (or is dropped by `kill <id>`).
  While paused the model cannot tune a running experiment or action a kill verdict; a human kill
  still works. The stall clock counts from activation, so time spent queued is not counted.
- **Length:** `advisor.experiment_sessions` was 10 in the deployed config, under the 14-session
  qualification floor, so every experiment admitted at the default was `underpowered` by
  construction. Set to 15, the package's documented default.

## The redesign, before the pause lifts

1. **Qualification on net, not win rate (B2, B3).** `core.profiles.QUALIFICATION_RULE` gates on a 60%
   win rate, and `min_net_pnl` is opt-in. Replace the win-rate check with net per trade and its
   interval (in R where the schema has it), with `min_net_pnl` on by default. A premium seller wins
   often and loses big; the 2026-08-14 flies arms qualified while lifetime-negative. This changes
   what `qualified` means for every caller of `core.profiles`, so it lands at a declared boundary.
2. **MDE at admission (B12).** Before an experiment is admitted, compute the smallest effect its
   planned sessions can detect from the module's own paired session spread (`core.robustness.
   planned_mde`). Refuse one that cannot detect a plausible effect, and state on every verdict what a
   no-verdict rules out. Expect this to show that session-level paired tests over 15 sessions are
   too short for most modules, which is a finding, not a bug.
3. **The register in the prompt (B7).** Show the model the rejected rows that match a proposal
   (`hypotheses.similar`), and record each admitted experiment as a register row with its four
   parts, so a re-proposal of a closed idea is visible and deliberate.
4. **Multiplicity.** Count every admitted experiment as a trial, per module and suite-wide, and
   correct verdicts for the number run (Holm), as the backtests already do. With 39 tries a lucky
   pass is expected.

## Unchanged

The fence: no AI inside a package, live opened read-only and only in `factpack.py`, advice applied
to synthetic `advised:` books only, verdicts computed in code. Advised arms are paper, so B1's kill
criteria do not apply to them.
