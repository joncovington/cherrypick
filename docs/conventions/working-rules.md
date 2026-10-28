# Working rules and vocabulary

Moved from the root CLAUDE.md, which keeps a one-line summary of each.

## Deterministic first, and measurement boundaries

**Deterministic solutions are preferred over AI or agentic ones** — a standing preference, not a
prohibition. Where a problem can be solved by a pure function over data you already have, solve it
that way: this suite exists to measure whether strategies make money, and a measurement is only worth
its reproducibility (a deterministic path fails the same way twice and can be re-run over last month's
rows). That is why `engine.py`, `management.py`, `fly.py` are pure functions over a pre-fetched
snapshot. Network/MCP dependencies are non-deterministic inputs too: prefer the local stream cache,
and reach outward only to ACT or to confirm a fill — an external streamer once stalled silently for 34
hours and looked exactly like a quiet market. Where AI earns its place, contain it OUTSIDE the
packages, in `scripts/` (`eod_narrative.py`, `morning_narrative.py`, `advisor_checkpoint.py`,
`flies_intraday_agent.py`), so a failure costs a narrative or one arm's decision and never a report, a
ledger or a loop. Leaning away from this is a choice
to write down.

**Measurement-affecting changes are batched to declared boundaries.** A measurement-affecting change
alters what a session's numbers MEAN — tick cadence, entry pacing, gate semantics, a book's net
definition, which arms exist. Each landing restarts the evidence clock, so hold them to a declared
boundary, land them together, and journal the boundary once. A bug fix correcting a number the module
was recording WRONG is not in this category and lands immediately. Pure code changes (refactors, read
surfaces, dedup) aren't either.

## The suite's vocabulary

**An `arm` is one configured variant, run as its own portfolio** — control and treatment, in the
clinical sense. Modules shipped four names for it (`arm` in flies; `book` in bwb/pmcc/curve/calendars;
`risk_profile` in meic; `profile` in earnings), and `core/ledgers.py` (`profile`) and the console's
attempts reader (`armColumn`) disagree on the canonical one. **The word is now `arm`, everywhere a new
name is chosen.**

**Three words are taken, and must never be used for an arm:**

| Word | What it already means | Where |
|---|---|---|
| `book` | flies' per-session P&L roll-up **of** an arm — a time slice, not a variant | `fly_books`, `book_id` |
| `book` | the paper-vs-live ledger designation | review fact sets, `modules.<m>.book = "paper"` |
| `profile` | a config preset registry | MEIC's arm registry (`meic.risk.json`), `core.profiles.load_profiles` |
| `profile` | the gamma-by-strike curve | `GexProfileChart`, `useGexProfile` |
| `strategy` | earnings' **structure type** (iron_fly vs double_calendar) | earnings is the only two-axis module: profile × strategy |

**Existing spellings are never withdrawn.** A module config is a file a person keeps across upgrades
with no migration, so `books`/`profiles`/`base_book`/`base_profile` resolve for good through
`cherrypick.core.config`. That module also separates what `cfg.get(key, {})` collapses: a registry
**declared empty** is an operator turning every arm off; a registry **absent** is a moved key, and only
that one warns. Every config read defaults silently, so a moved key does not fail — every arm falls
through to its `enabled` default on `defaults`, and the A/B measures nothing while the P&L looks fine.

**Spelling: Python identifiers are American, TypeScript identifiers are British, prose is British, and
`cancelled` is British everywhere including stored values.** That is the existing convention
(`realized_net` in `.py`, `unrealisedNet` in `.ts`); the boundary re-spells, which is not a bug to fix.

## Two working rules, both learned the hard way

**Measure a duplication before folding it, and normalize the identifier first.** "The same function in
six places" is the least reliable claim in this repo, both ways: one dedup pass found about ten false
premises (helpers that only look alike), and another reported 6 identical functions when there were
**22**, because it hashed table-name constants with the logic. Compare bodies with the varying
identifier normalized out, then read call sites before folding. Where copies differ, ask whether the
difference is the point — `_wing_width_multiple` is byte-identical across three earnings strategies and
stays copied, because it shapes an order and that file says so.

**A guard has to be shown to fail.** The config lints and enforcement tests (same-index correlation,
single cache writer, guarded-live-pointer table) are only worth their failures, and each was verified
by breaking the invariant on purpose and watching it report the right thing. Do the same for any new
one, and drive it off what the system itself declares (a module's config example, its own
`stream_requests` file) rather than a hand-kept list. A green check that cannot fire is worse than no
check: it reads as coverage.
