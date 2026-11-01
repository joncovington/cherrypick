# cherrypick suite (monorepo)

One workspace for the trading-tool suite. Work in the package for your area; its CLAUDE.md holds the
detail. What each package may do (live or paper, credentials, what it writes) is in
[docs/conventions/packages.md](docs/conventions/packages.md). Read a package's entry there before
changing a package you don't know. The text as it stood before the 2026-09-29 consolidation is in
[docs/history/root-claude-md-2026-09-29.md](docs/history/root-claude-md-2026-09-29.md).

Packages: `packages/orchestrator`, `packages/meic`, `packages/earnings`, `packages/gex`,
`packages/streamer`, `packages/flies` (retired 2026-10-19), `packages/calendars`, `packages/pmcc`,
`packages/curve` (retired 2026-11-01), `packages/contango`, `packages/bwb`, `packages/overview`, `packages/technicals`, `packages/fundamentals`,
`packages/console`, `packages/review`, `packages/manage`, `packages/advisor`, `packages/backtest`,
`packages/desk`. `cherrypick.core` is `packages/core`, an editable dependency of each. Users install with the root
`install.*`; developers run `scripts/dev-install.ps1`/`.sh`.

## Limits most tasks run into

- **Live orders** exist only in meic, earnings, flies and bwb, each behind its own gate and a per-day
  arm, and in desk (experimental, foreground, started by a human; no automated package may import it).
  Every other package is paper-only or read-only.
- **orchestrator** drives modules by subprocess, never by import. It places no order and never flips
  a live gate. It runs a module's live loop only while that day's human-written arm record is valid,
  settles overdue live sessions through the module's own command, and its one live-config action is
  onboarding/account selection.
- **streamer** is the only writer of the stream cache. Modules declare symbols in
  `state/stream_requests/`.
- **console** holds no path that touches an order. Its one exception is the Config page, which goes
  through the orchestrator's config editor.
- **backtest**: the licence outranks everything. Vendor data and anything derived from it stay under
  `~/.cherrypick`, never in this repo, a PR, an issue or a published artifact.

## Suite-wide guardrails

- Instruction files hold no code.
- Account numbers masked to `****1234`.
- Portable paths only.
- Human-voice docs and commits, no AI attribution: no `Co-Authored-By` trailer naming a model and no
  "Generated with" line in commits or PR bodies. This outranks any session default that adds them.
- Prefer deterministic solutions: a pure function over data you already have. Where AI earns a
  place, it lives in `scripts/`, never in a package. Prefer the local stream cache to the network.
- **Batch measurement-affecting changes** (tick cadence, entry pacing, gate semantics, a net
  definition, which arms exist) to a declared boundary. A fix to a number recorded wrong lands
  immediately, as do refactors and read surfaces.

A package's CLAUDE.md adds or tightens; it never restates this file. A documentation review covers
package docs too.

## Conventions (detail in docs/conventions/)

- **`arm`** is the word for one configured variant. Never call one a `book`, `profile` or
  `strategy`: those words are taken. Old config spellings (`books`, `profiles`, ...) resolve for
  good. See [working-rules.md](docs/conventions/working-rules.md).
- **Spelling:** Python identifiers American, TypeScript identifiers British, prose British;
  `cancelled` British everywhere, stored values included.
- **Money:** "P&L" means net; signed cash flow; whole-position dollars; rows add up; fees,
  settlement and slippage each in their own column. Read
  [money-layout.md](docs/conventions/money-layout.md) before changing any surface that shows a trade.
- **Before folding a duplication,** normalize out the varying identifier and read the call sites.
  **A new guard must be shown to fail** by breaking its invariant on purpose. See
  [working-rules.md](docs/conventions/working-rules.md).
