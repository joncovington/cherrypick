# cherrypick-advisor

The deterministic half of the suite's AI advisor: fact packs in, bounded paper experiments out.

Eight times a trading day a scheduled script shows an AI model a **fact pack** — a deterministic,
aggregate-only snapshot of what the paper books did today (plus clearly-labeled read-only live
context) — and asks it what it notices and what it would change. Seven light checkpoints run
intraday; one deep run after the close designs experiments and passes verdicts.

This package builds those packs, validates every reply, and runs the resulting proposals as paper
A/B experiments. It contains **no AI**: the model is invoked by `scripts/advisor_checkpoint.py`,
outside every package, so no suite package acquires an API key, a network dependency, or a reason to
be imported by a loop.

## What it can and cannot do

| | |
|---|---|
| **Can** | Read every module's paper data, and live data read-only for context |
| **Can** | Issue a bounded, single-session, expiring advice artifact for the next paper session |
| **Can** | Run one experiment per module as an `advised:<base>` book beside its control |
| **Can** | Propose anything at all — new arms, new strategies, whole new modules — as propose-only memos |
| **Cannot** | Touch a live account, in any way, ever |
| **Cannot** | Write a module config, a risk profile, or a module's database |
| **Cannot** | Widen a parameter past the bounds a human declared for it |
| **Cannot** | Make advice stick: every artifact names one session and expires |

## The pieces

```
factpack.py     deterministic pack builder (light/deep); every foreign DB opened read-only
proposals.py    raw-reply parse + taxonomy validation
experiments.py  lifecycle: admit / cap / queue / activate / tune / expire, with a journal
enact.py        next-session walk + core.advice.write per active experiment
verdicts.py     ledger readers -> compare_profiles -> qualify_readings
bounds.py       per-module advice bounds + enablement, read from deployed configs (read-only)
store.py        advisor.db (SQLite WAL) and the one read-only opener for everyone else's data
```

## Quick start

```
python -m cherrypick.advisor init-db
python -m cherrypick.advisor factpack --slot open       # writes data/advisor/packs/<session>-open.json
python -m cherrypick.advisor status
```

Nothing runs on a schedule until `advisor.enabled` is set in the orchestrator config, and no module
accepts advice until a human puts an `advice` block in that module's deployed config. Both are off
by default.

See [CLAUDE.md](CLAUDE.md) for the operating contract and the guardrails that are enforced by tests
rather than prose.
