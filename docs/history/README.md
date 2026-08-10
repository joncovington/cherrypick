# Historical records

**Nothing in this directory describes the suite as it is now.** These are point-in-time documents —
design proposals and cutover records — kept because the *reasoning* behind a decision outlives the
decision, and because a plan that shipped is the cheapest available explanation of why the code looks
the way it does.

They are here rather than in `docs/` so that "not current" is structural. A status header at the top of
a long document only works if the reader reaches it; a directory named `history/` works before they open
the file.

| Document | What it is | Frozen |
|---|---|---|
| [onboarding-redesign.md](onboarding-redesign.md) | The proposal that collapsed the suite's secrets-and-account workflow into one shared login (`cherrypick-broker`) and one no-module `connect` wizard. Every step shipped. | 2026-07-28 |
| [streamer-package-plan.md](streamer-package-plan.md) | The design for splitting the market-data streamer out of MEIC into its own package, making it the suite's single producer. Shipped. | 2026-08-02 |

For what is true today, start at the [documentation index](../README.md).

## Other frozen records, kept with their packages

Not everything historical lives here — a record that is specific to one package stays with that
package, so a reader working in that directory finds it. Each carries its own status header:

- [`packages/orchestrator/ROADMAP.md`](../../packages/orchestrator/ROADMAP.md) — the Stage 0 plan,
  frozen 2026-08-02. Shipped history is git log.
- [`packages/orchestrator/docs/design.md`](../../packages/orchestrator/docs/design.md) — the
  2026-07-11 research report that shaped the suite's architecture. Deliberately never updated as work
  ships.
- [`packages/core/CUTOVER.md`](../../packages/core/CUTOVER.md) — the per-module method for wiring in
  `cherrypick-core` when it was still a separate repository, frozen 2026-08-01.
