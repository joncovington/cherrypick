# cherrypick-advisor

The deterministic half of the suite's AI advisor: fact packs in, bounded paper experiments out.

After each close a scheduled script shows an AI model a **fact pack**: a deterministic,
aggregate-only snapshot of what the paper books did today (plus clearly labelled read-only live
context). The model is asked what it notices and what it would change. That one deep run, at 17:00
ET by default, designs experiments and passes verdicts. Light intraday checkpoints are still
supported but none are scheduled by default; list their times in `advisor.checkpoints` to bring
them back.

This package builds those packs, validates every reply, and runs the resulting proposals as paper
A/B experiments. It contains **no AI**: the model is invoked by `scripts/advisor_checkpoint.py`,
outside every package, so no suite package acquires an API key, a network dependency, or a reason to
be imported by a loop.

## What it can and cannot do

| | |
|---|---|
| **Can** | Read every module's paper data, and live data read-only for context |
| **Can** | Issue a bounded, single-session, expiring advice artifact for the next paper session |
| **Can** | Run any number of experiments per module concurrently (unlimited by default, capped via `max_experiments_per_module`), each its own `advised:<experiment-name>` book beside the shared control |
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
enactment.py    did each module's loop actually apply the artifact it was issued?
verdicts.py     ledger readers -> group_by_tag -> calibration_reading -> qualify_readings
settings.py     the governance knobs, read from the suite config's advisor block
bounds.py       per-module advice bounds + enablement, read from deployed configs (read-only)
store.py        advisor.db (SQLite WAL) and the one read-only opener for everyone else's data
```

## Quick start

```
python -m cherrypick.advisor init-db
python -m cherrypick.advisor factpack --slot deep       # writes ~/.cherrypick/data/advisor/packs/<session>-deep.json
python -m cherrypick.advisor status
```

Nothing runs on a schedule until `advisor.enabled` is set in `~/.cherrypick/config.json` and the
machine has the `claude` capability (Claude Code, recorded by `run.py capabilities`). No module
accepts advice until a human turns on an `advice` block in that module's own config
(`~/.cherrypick/config/<module>.json`). All of these are off by default, and the console hides the
advisor while it is off.

See [CLAUDE.md](CLAUDE.md) for the operating contract and the guardrails that are enforced by tests
rather than prose.
