# What the console does not cover

The console is the suite's read surface. These are the things a person might look for in it and will
not find, and why.

- **Per-module live gates and the reconcile panel.** Both touch the broker or arm a live loop. The
  console holds no path to an order, so arming and reconciling stay with each module's own command
  (the `live-*-start` slash commands and the module CLIs). The Config page edits settings through
  the orchestrator's editor, and the halt flag is the one live control it offers, set in one click
  and cleared only by typing `RESUME LIVE`.
- **Live doctor checks and the operating-system task registry.** The System page shows the files the
  suite writes (supervisor, watchdog, holds, stamps). A doctor check or a task-registry read needs
  the orchestrator's own subprocess, which the page does not start.
- **Model prose in a trade table.** A trade table shows numbers the module recorded. Text an AI
  wrote about a trade does not belong in it, and the console computes no verdicts of its own.
- **Verdict recomputation.** Where a module judges something (an arm's scorecard, a stop
  counterfactual, a gate's blocking reason) the console renders the module's answer through a
  bridge. It never rebuilds the derivation. See [mirrors.md](mirrors.md).
- **Quote-history and research tooling outside the Backtest page.** The watchlist, builder,
  payoff-and-probability and staged ticket surfaces of the earlier research app are not here. The
  `pre-console-only` tag still has them.
