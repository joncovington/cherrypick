# cherrypick-review — Operational Instructions

> Operating contract for the suite's cross-module **end-of-day review**. Suite-wide context is in
> the root [documentation index](../../docs/README.md).

This package answers one question every session: **what did the suite do today, was it what we
expected, and what should change.** It covers MEIC, flies and earnings together, because the
question is a suite question and answering it inside each package produced six report families that
could not be compared and two normalisation layers that had already drifted apart.

**It is read-only over every other package.** It reads each module's ledger through
`cherrypick.core.ledgers` and writes only into its own home (`~/.cherrypick/data/review`). It never
opens, closes, adjusts or cancels anything, never writes to a module's database, and has no
broker credentials or network access of any kind.

## The artifact is the product

The output is a **fact set**, not a document: one versioned JSON per session, plus renders of it.

```
~/.cherrypick/data/review/eod-<date>.json        the facts — the only thing any surface reads
~/.cherrypick/data/review/eod-<date>.md          human render of those facts
~/.cherrypick/data/review/eod-<date>.note.md     the narrative, beside the facts, never inside them
```

Nothing downstream re-derives. The markdown render, the console page and the narrative all read the
same JSON, so they cannot disagree — which is exactly what went wrong before, when the orchestrator's
report and the console's TypeScript port each computed flies' P&L from a different table.

## Rules the fact set enforces

- **`None` is not zero.** A field with no recorded value is null. The earnings paper book holds 46
  trades whose slippage predates the column; averaging those as zero understated cost by ~90% and
  produced a confident, wrong conclusion about which strategies were viable.
- **Effective sample sits beside raw N.** Trades sharing a symbol and session share one market
  event. MEIC books 673 trades on a single session — that is one day, not 673 observations.
- **Measurement breaks travel with the numbers.** Results either side of a break must never be
  pooled. Earnings and MEIC record them; flies has no such table, and the fact set reports `null`
  rather than `[]` so a trend line cannot silently assume continuity it never verified.
- **A session is `provisional` before it is `final`.** MEIC and flies are 0DTE and complete at the
  close; earnings opens before the close and settles the next morning, so session D is finalised on
  session D+1. The narrative only ever runs on `final` sets, which is what lets it be written once
  and frozen as the record of what was concluded that day.

## The arms are the experiment — never collapse them

MEIC runs `open`, `width-5` and `width-10`, and flies runs `control`, `time_window`, `gex`,
`debit-first` and others, **against the same underlying on the same sessions**. That is a paired
comparison, which is worth considerably more than the raw session count suggests, and it is the
reason those arms exist at all.

A module-level row averages them and hides the finding. On 2026-08-12 MEIC's module total showed
19.6% on risk; underneath it, `open` took **zero stops all session** and returned 21.6%, while
`width-5` stopped 28% of its book and returned 16.7% — and on the down session, 08-11, the width
arms stopped 91-93% of trades and lost more than `open` did. None of that is visible without the
split, and the split is where every interesting question about these modules lives.

So `by_profile` rides on every module block and every trend, grouped through
`cherrypick.core.profiles.compare_profiles` — the helper the orchestrator's own per-profile
reporting already uses, rather than a fourth hand-rolled grouping. The render shows the table only
when a module has more than one arm, because a one-row comparison table implies a comparison.

## Trends stop at breaks, and suspected breaks get flagged

A trend never crosses a journaled measurement break — results either side are not the same
experiment, so a line through one describes neither side. Both earnings and MEIC have breaks within
days of the current session, so most windows currently report one or two usable sessions. That
thinness is the output, not a defect: a five-session trend spanning a policy change looks more
informative than a two-session trend that stops where the evidence stops, and is worth less.

Review also **detects** regime changes nobody journaled and reports them — it never writes one,
because deciding that a book changed is a judgement about what the module did. The detector needs
all three of its conditions to stay useful: a departure from the trailing median, a departure from
the immediately preceding session, and an absolute floor. Without the second it re-reports one
event every session until the median catches up (MEIC's launch flagged three times); without the
third it fires on ratios between trivial counts (earnings going from 6 trades to 2). With all
three, 24 backfilled sessions produce two flags, both real: flies on 2026-07-29 and MEIC on
2026-08-07 — the four-stream launch, which its journal records as 2026-08-11.

## The narrative lives outside every package

`scripts/eod_narrative.py` writes `eod-<day>.note.md` beside the fact set. It is deliberately **not**
a package: `packages/*` is what the trading loops import, so a script the scheduler runs cannot be
imported by a loop, no package gains an API key or a network dependency, and deleting the file costs
a note and nothing else. That is the distinction that retired `orchestrator/eod_insight.py` rather
than moving it — it lived in the package whose watchdog fired it, one refactor from the reliability
path.

Four constraints hold it in place. It is given **the fact set JSON and nothing else** — no database,
no ledger, no shell — so every claim traces to a recorded number. It runs on **final sessions only**,
because a narrative written against numbers that will still move records something that never
happened. It is **written once and frozen**: an existing note is left alone unless `--force`, which
stamps a new version rather than pretending. And it can only ever **fail to write a note** — no exit
path touches the fact set, a ledger or a loop.

Recommendations become tracked issues with `--file-issues` (label `eod-finding`), capped per run and
deduped against open issues: an unattended agent filing the same standing observation daily produces
a backlog nobody reads.

**It earned its place on the first run.** Reading only the artifact, it noticed that flies'
`gex-intrinsic` and `control` reported byte-identical results and called it a probable attribution
bug. It was not a bug — `gex-intrinsic` had degraded to ATM centring for the whole session, which is
this module's documented fallback — but it *was* a real problem: the arm was running the control's
strategy under another name, and the fact set was counting it as an independent observation. The
`centred_by` field and the render's collapsed-arms note exist because of that.

## Reconciliation is not optional

`reconcile` re-counts each module's totals with **independent SQL** — a different route to the same
question than the readers took — and reports deltas rather than merely failing. Proving the code
equals itself is worthless; the failures worth catching are scope differences, and it earned its
place on its first run by catching one (the earnings reader deliberately does no SQL date pushdown,
so trusting its bounds reported every trade the book had ever closed as though it settled today).

Run it after any change to a collector, and after any module changes its schema.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ This file is strictly for build commands, tech-stack reference, and project guidelines:
> - **No code here** — no Python, no scripts, no logic, and no scratchpad content, changelogs, or task trackers.
> - **Mask account numbers** to the last 4 digits (`****1234`) anywhere they surface.
> - **Portable paths only** — never hardcode absolute paths, usernames, hostnames, or drive letters.
> - **Human-voice docs & commits** — never add AI/co-author attribution to commit messages.
> - **No AI or network on any loop-decision or reliability path.** The narrative is deliberately
>   generated *outside* this package by a scheduled agent reading the fact set, so no suite package
>   ever acquires an API key or a network dependency, and a failed narrative can never damage a
>   report.

## Tool Reference

| Command | Purpose |
|---|---|
| `python -m cherrypick.review build [--session YYYY-MM-DD] [--final]` | Build and write one session's fact set. Defaults to today, `provisional` unless `--final`. |
| `python -m cherrypick.review backfill [--since YYYY-MM-DD]` | Build every session any module has a closed trade for. Backfilled sessions are `final` by definition — everything that was going to settle has. |
| `python -m cherrypick.review render [--session YYYY-MM-DD]` | Re-render one session's markdown from its fact set. `build` and `backfill` render automatically. |
| `python -m cherrypick.review reconcile [--since YYYY-MM-DD]` | Check every written fact set against independently-computed ledger totals. Reports the delta per module and field. |

## Where the shared rules live

`cherrypick.core.ledgers` is the single Python home for per-schema net, cost, capital and session
rules across `meic_ic`, `fly_book` and `earnings`. The orchestrator's report imports from there;
so does this package. **Do not add a fourth implementation** — that module's docstring records what
happened the first three times.
