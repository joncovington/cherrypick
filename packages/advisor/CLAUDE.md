# cherrypick-advisor — Operational Instructions

> Operating contract for the suite's **AI advisor machinery**. Suite-wide context is in the root
> [documentation index](../../docs/README.md).

This package exists so an AI can influence the paper books **without any AI running inside a suite
package**. It holds every deterministic part of that arrangement: the fact packs the model reads,
the parse-and-validate of what it replies, the experiment lifecycle, and the nightly issuing of
bounded advice artifacts through `cherrypick.core.advice`.

## The fence

**This package never invokes AI, holds no API key, and opens no socket.** The one AI touchpoint is
`scripts/advisor_checkpoint.py`, outside every package — the same fence that holds
`scripts/eod_narrative.py`, for the same reason: `packages/*` is what the trading loops import, so a
script the scheduler runs can never be imported by a loop. Deleting the script costs the advice and
nothing else; the loops keep running on baseline, which is exactly what `core.advice` guarantees
when advice is absent.

The model gets one channel in and one channel out. In: a fact pack on **stdin**, with tools denied.
Out: strict JSON, parsed here, validated here against the module's declared bounds.

## It can never touch a live account

The advisor reads live facts — they are context a competent observer would want, and hiding them
produces worse advice about the paper books that shadow them. It cannot act on them:

- Live databases and live config keys are opened **read-only and only in `factpack.py`**. Every
  other module in `src/` is proven free of them by a source scan.
- The only loop-facing output this package can produce is a paper advice artifact at
  `state/advice/<module>-<session>.json`, and each module's consumer applies it to a **synthetic
  `advised:<base>` book beside its control** — never to the control, never to a live loop.
- Nothing here writes a module config, `config.risk.json`, or any module's database. New-profile
  and new-strategy ideas come out as `creative` proposals with ready-to-paste specs; a human
  applies them or does not.

## Paper arms are the dry run

There is no replay engine and there will not be one. A proposal's test is a **next-session paper arm
beside its control**, scored as sessions accrue through `cherrypick.core.profiles.compare_profiles`
and `qualify_readings` — the same qualification the suite uses for every other promotion decision.
That is why the default experiment length is 15 sessions: shorter, and an expiring experiment cannot
satisfy the promotion gate (min 14 days, min sample 20), so its verdict would be structurally
`underpowered`.

## An experiment outlives its advice artifact

Advice is single-session by contract — never sticky, always expiring. An experiment is not: it lives
in `advisor.db` with a base profile, a param overlay, an expiry and a journal, and every evening the
deterministic `enact` step **re-issues** the next session's artifact for each active experiment,
re-validated against the module's *current* bounds. So a human who tightens bounds tonight has
tightened them by tomorrow morning, without touching the experiment; and the loops still only ever
see one validated, expiring artifact through their existing read-once consumers.

`enact` runs **unconditionally** in the deep slot, even when the AI call failed. An AI outage must
never truncate an active A/B sample — that would corrupt the measurement, which is worse than
getting no advice.

## Verdicts are computed, not written

`verdicts.py` computes the comparison deterministically (ledger readers → `compare_profiles` →
`qualify_readings`). The model only *recommends* over those numbers, and its recommendation is
stored beside them, never instead of them. A verdict that fires below the qualification thresholds
is labeled `underpowered` — never silently passed or failed.

## The advisor tunes only its own experiments

Structurally: the only thing it can emit is an `advised:*` overlay. A `tune` proposal naming a
control arm, a human-configured profile, or an unknown id is rejected `not_an_advisor_experiment`.

## The cap is one per module, by construction

Each module's consumer builds exactly **one** `advised:<base>` book from the day's artifact, so one
active experiment per module is what the consumers can express. Over-cap specs are admitted as
`queued` and activate FIFO when a slot frees. More than one per module is a documented future
consumer extension, not a config knob that silently does nothing.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ This file is strictly for build commands, tech-stack reference, and project guidelines:
> - **No code here** — no Python, no scripts, no logic, and no scratchpad content, changelogs, or task trackers.
> - **Mask account numbers** to the last 4 digits (`****1234`) anywhere they surface.
> - **Portable paths only** — never hardcode absolute paths, usernames, hostnames, or drive letters.
> - **Human-voice docs & commits** — never add AI/co-author attribution to commit messages.
> - **No AI, no network, no broker.** No `tastytrade`, `keyring`, `requests`, `socket` or
>   `cherrypick.core.auth`/`broker` import may appear in `src/` — enforced by a source scan, not by
>   prose.
> - **Writes are confined** to `data/advisor/**` and `state/advice/*.json` — enforced by a
>   file-tree snapshot test around a full factpack→admit→enact run.

## Tool Reference

| Command | Purpose |
|---|---|
| `python -m cherrypick.advisor init-db` | Create/migrate `data/advisor/advisor.db`. Idempotent. |
| `python -m cherrypick.advisor factpack --slot {am,midday,pm,deep} [--session D]` | Build one deterministic fact pack and print its path. |
| `python -m cherrypick.advisor admit --slot S [--session D] --raw <path>` | Parse a raw model reply, validate every proposal against module bounds, record admissions and rejections. |
| `python -m cherrypick.advisor enact [--session D]` | Issue the next session's advice artifact for every active experiment. Runs nightly, unconditionally. |
| `python -m cherrypick.advisor verdicts [--session D]` | Compute deterministic verdicts for expiring experiments. |
| `python -m cherrypick.advisor status [--session D]` | What the advisor thinks is true right now: checkpoints, experiments, apply status per module. |
| `python -m cherrypick.advisor kill <experiment_id>` | Stop an experiment tonight. Journaled; a queued experiment activates in its place. |
| `python -m cherrypick.advisor dismiss <proposal_id>` | Mark a proposal dismissed by the user. Fed back to the model so it stops re-proposing it. |

The console's two write actions (kill, dismiss) invoke exactly these verbs as a subprocess — the
console holds no advisor logic, the same shape as its Config page.

## Where the shared rules live

- `cherrypick.core.advice` — the one validator. Both the producer (here) and every loop-side
  consumer call it, so a disagreement between the two sides is impossible by construction.
- `cherrypick.core.ledgers` — per-schema net/risk/session rules for `meic_ic`, `fly_book`,
  `earnings`. Do not add another implementation; that module's docstring records what happened the
  first three times.
- `cherrypick.core.profiles` — `compare_profiles` and `qualify_readings`, the suite's one
  arm-comparison and promotion gate.
