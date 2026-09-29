# Changelog

This is a retrospective changelog: the suite has never tagged versions before, so the entries below
were reconstructed from commit history rather than written at release time. Versions mark
architectural boundaries (a new package, a scheduler cutover, a read-side or trading-mode change),
not commit counts. Per-package `pyproject.toml` versions remain at their `0.1.0` placeholder — this
file tracks the *suite*, not any one package.

## [Unreleased]
Since v0.9.0: flies' delta-arm rollout (six arms plus the ATM twins, landing 2026-09-21), the
nightly per-book x per-regime "regime cuts" artifact for flies/MEIC, and a CLAUDE.md consolidation
cutting suite-wide guardrail duplication across every package. The regime cuts now stamp how much
each cell rests on one session, same-day paired contrasts, snapshot-to-snapshot sign changes and a
multiplicity count; and MEIC's GEX bucket is re-derived sign-first on every signed row, ending two
definitions pooled in one era. flies' regime cuts carry a replay of every gate bound for the
advisor, `entry_windows` became an advice bound, the callwall arm retired, and MEIC gained a
`live-shadow` paper arm measuring the live configuration it had never run on paper. The flies live pilot now starts at
10:15 rather than 10:30, on a replay of control's era under the live margin cap. MEIC gained a
buying-power cap and three arms that trade control under it at $5k, $10k and $25k.

## v0.9.0 — 2026-09-18 — bwb's live path
`58ebad48` wired bwb's narrow live pilot into the guards, the supervisor, and the arm command — the
first new live-trading path added to the suite since MEIC and flies. One configurable arm, armed
per day, under a worst-case margin cap and a cost-derived credit floor; every fill is still the
broker's word.

## v0.8.0 — 2026-08-23 — the paper-only module wave
`59502eb6` added bwb and curve, completing a run of credential-free, stream-cache-only modules
started with calendars (2026-08-14), pmcc and overview (2026-08-16): each a forward experiment
against the shared streamer rather than a strategy with its own broker session.

## v0.7.0 — 2026-08-20 — the advisor era
`b79c4677` opened "one experiment mechanism" — the deterministic advisor fact-packs and the
`advised:<experiment>` paper A/B books — and retired the module-specific champions/challengers
machinery (`68cb6e6d`) in its favor.

## v0.6.0 — 2026-08-12 — console becomes the one read surface
`cc3861ee` deleted the scout package after its watchlist/screener/builder/payoff surfaces were
ported into the new console (`packages/console`, first commit 2026-08-09); `357a3ec3` retired every
other read surface except it the same day.

## v0.5.0 — 2026-08-09 — the supervisor cutover
`b63c83f9` replaced the per-job Windows Task Scheduler entries with one supervisor daemon
(`run.py install`) — the orchestrator's single always-on process model that every module's resident
loop and the console now run under.

## v0.4.0 — 2026-07-30 — the settings surface
`614e1a4` added `cherrypick settings`, a loopback config editor and secrets manager — the suite's
one mutating HTTP surface, with guarded live-trading fields refused on both write paths.

## v0.3.0 — 2026-07-11 — the monorepo
`a4214b9f` restructured the suite from sibling repos (MEICAgent, EarningsAgent, cherrypick-core as a
submodule) into one monorepo under `packages/`. `packages/core` landed the same window as an
ordinary editable dependency, and gex (07-11) and streamer (07-21) arrived as the first modules
built monorepo-native rather than migrated in.

## v0.2.0 — 2026-07-06 — Earnings joins
First commit of the earnings-play module (`e487db35`, "Scaffold EarningsFlyAgent project") as a
sibling to MEIC, still in the pre-monorepo, per-repo era.

## v0.1.0 — 2026-06-18 — origin
`c8e0fe73`, "Initial commit — MEICAgent MEIC trading agent." The suite's origin: a standalone
0DTE multiple-entry iron-condor agent, before any orchestrator, shared core, or sibling module
existed.
