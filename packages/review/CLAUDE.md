# cherrypick-review — Operational Instructions

> Operating contract for the suite's cross-module **end-of-day review**. Suite-wide context is in
> the root [documentation index](../../docs/README.md); the incidents behind these rules are in
> [docs/history.md](docs/history.md).

Every session this package answers **what did the suite do today, was it what we expected, and what
should change** — for every module with a ledger reader, together, because answering it per package
produced report families that could not be compared.

**It is read-only over every other package.** It reads ledgers through `cherrypick.core.ledgers` and
writes only into `~/.cherrypick/data/review`. It never opens, closes, adjusts or cancels anything,
never writes a module's database, and has no broker credentials or network access.

## The artifact is the product

A **fact set**, not a document: one versioned JSON per session, plus renders.

- `eod-<date>.json` — the facts, and the only thing any surface reads;
- `eod-<date>.md` — the human render of those facts;
- `eod-<date>.note.md` — the narrative, beside the facts, never inside them.

Nothing downstream re-derives. The render, the console page and the narrative read the same JSON, so
they cannot disagree.

## Rules the fact set enforces

- **`None` is not zero.** A field with no recorded value is null (averaging missing slippage as zero
  once understated earnings' cost by ~90%).
- **Effective sample sits beside raw N.** Trades sharing a symbol and session share one market event.
- **Measurement breaks travel with the numbers**, and results either side are never pooled. A module
  with no breaks table reports `null`, not `[]`, so a trend cannot assume continuity it never verified.
- **A session is `provisional` before it is `final`.** 0DTE modules complete at the close; earnings
  settles the next morning, so session D is finalised on D+1.
- **Every module with a `core.ledgers` reader is reviewed.** A module with no results reports as such;
  being absent is a different, wrong statement. `tests/test_module_coverage.py` drives off `READERS`
  and fails on a reader with no `MODULES` entry, on a reviewed module naming a nonexistent schema, and
  on a module missing from `HEALTH_READERS` or `EXPECTED_READERS` (which `build_module_facts` indexes
  directly).

## The arms are the experiment — never collapse them

A module's arms run against the same underlying on the same sessions: a paired comparison, and the
reason they exist. A module-level total averages them and hides the finding. So `by_profile` rides on
every module block and every trend, grouped through `cherrypick.core.profiles.compare_profiles`
(never a hand-rolled grouping). The render shows the table only when a module has more than one arm,
because a one-row comparison table implies a comparison. An arm that degraded to the control's own
strategy is not an independent observation: `centred_by` and the render's collapsed-arms note say so.

**Concentration** (fact set v6) rides beside it: each arm's contribution, the largest, the net without
it, and `sign_flips_without_largest`. **Read `sign_flips_without_largest` first** — a total that
changes sign without its biggest contributor measures that arm, not the module; being *dominated* is
not being *inverted*, and a flag that fired on both would be ignored. Two shares: `share_of_net`
(signed arm/total; may exceed 100% when other arms net against the leader; `None` at a ~0 total) and
`share_of_movement` (|arm| / Σ|arm|, bounded). The arithmetic lives in `cherrypick.core.ledgers` so
every module gets the same answer. This package publishes it and labels nothing: whether the leader
clears its module's sample and day bars is that module's rule, so its trade and session counts travel
with it.

## Trends stop at breaks, and suspected breaks get flagged

A trend never crosses a journaled measurement break. A thin window that stops where the evidence stops
is the correct output, not a defect.

Review also **detects** unjournaled regime changes and reports them — it never writes one, because
deciding that a book changed is a judgement about what the module did. The detector needs all three
conditions: a departure from the trailing median, a departure from the preceding session (or one event
re-reports daily), and an absolute floor (or it fires on ratios of trivial counts).

## The narrative lives outside every package

`scripts/eod_narrative.py` writes `eod-<day>.note.md`. It is deliberately not in a package, so no loop
can import it, no package gains an API key or network dependency, and deleting it costs a note and
nothing else. Four constraints hold:

- it is given **the fact set JSON and nothing else** — no database, ledger or shell — so every claim
  traces to a recorded number;
- it runs on **final sessions only**;
- it is **written once and frozen** — an existing note is left alone unless `--force`, which stamps a
  new version;
- it can only ever **fail to write a note** — no exit path touches the fact set, a ledger or a loop.

`--file-issues` turns recommendations into issues (label `eod-finding`), capped per run and deduped
against open issues.

## Reconciliation is not optional

`reconcile` re-counts each module's totals with **independent SQL** and reports deltas rather than
merely failing. Proving the code equals itself is worthless; the failures worth catching are scope
differences (the earnings reader does no SQL date pushdown, which once reported every closed trade as
settling today). Run it after any change to a collector and after any module changes its schema.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> Suite-wide guardrails apply — see root CLAUDE.md.
> - **The fact set is deterministic; the narrative is not, and is fenced accordingly.** Every figure is
>   computed from closed rows; the narrative is generated outside this package by a scheduled agent
>   reading the fact set, so a failed narrative can never damage a report.

## Tool Reference

| Command | Purpose |
|---|---|
| `python -m cherrypick.review build [--session YYYY-MM-DD] [--final]` | Build and write one session's fact set. Defaults to today, `provisional` unless `--final`. |
| `python -m cherrypick.review backfill [--since YYYY-MM-DD]` | Build every session any module has a closed trade for. Backfilled sessions are `final` by definition. |
| `python -m cherrypick.review render [--session YYYY-MM-DD]` | Re-render one session's markdown. `build` and `backfill` render automatically. |
| `python -m cherrypick.review reconcile [--since YYYY-MM-DD]` | Check every written fact set against independently computed ledger totals; reports the delta per module and field. |

## Where the shared rules live

`cherrypick.core.ledgers` is the single Python home for per-schema net, cost, capital and session
rules, and for the concentration arithmetic. The orchestrator's report and this package both import
from it. **Do not add another implementation** — its docstring records what happened the first three
times.
