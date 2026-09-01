# cherrypick-core

Shared library for the Cherrypick trading suite — auth, broker, calendar, dxfeed, fees, gex, risk, db,
and profiles primitives, consumed by every module under `../` as `cherrypick.core.*`. See
[README.md](README.md) for the design invariants and package layout; [CUTOVER.md](CUTOVER.md) is the
historical record of the original submodule cutover from the pre-monorepo, multi-repo world.

## This monorepo is the source of truth

`packages/core` was landed from the standalone `cherrypick-core` GitHub repo via `git subtree add`,
full history preserved. That standalone repo is now archived, read-only. Core is developed here from
now on; there is no separate repo to keep in sync.

The one out-of-repo consumer, `tastytrade-mcp`, is not part of this monorepo and does not get an
in-place update — it pins the last SHA from the standalone repo, or vendors a copy.

## Stay import-self-contained

Per README.md's own invariant: **the core imports nothing from a consumer's `src/`.** Everything a
consumer supplies is injected or parameterized, never reached back into. This is not just a design
preference here — it is what keeps `git subtree split --prefix=packages/core` a byte-identical
reproduction of the standalone repo, the escape hatch if core ever needs to be split back out (e.g. for
`tastytrade-mcp`, or if the suite's structure changes again). A reach-back into a consumer's `src/`
would make that split lossy or impossible.

**Do not rely on running that split as the check — it segfaults on this machine.** `git subtree` is
a large shell script and it exits 139 under Git Bash (git 2.44.0.windows.1), verified pre-existing:
it fails identically at commits from before any of this work, so a failure there says nothing about
your change. Check the invariant directly instead, which is what it was standing in for:

```bash
test -f packages/core/cherrypick/__init__.py && echo VIOLATION || echo "PEP 420 intact"
grep -rn "cherrypick\.\(meic\|flies\|calendars\|pmcc\|gex\|earnings\|orchestrator\|streamer\|console\|review\|advisor\|desk\|overview\)"   packages/core/cherrypick/ --include=*.py    # must print nothing
```

Plus the obvious one: a new core module should import from `cherrypick.core.*` and the standard
library, nothing else. If the split ever needs to run for real, do it somewhere `git subtree` works
rather than treating a segfault here as a finding.

## Layout stays flat, not src-layout

`packages/core/cherrypick/` sits directly under the package root (no `src/` prefix), unlike the other
six packages. This is deliberate, not an oversight:
- It matches the standalone repo's original layout exactly, so the subtree split above stays
  byte-identical with zero path rewriting.
- `tests/conftest.py` bootstraps `parents[1]` (the package root) onto `sys.path`, which depends on
  this layout — moving to `src/` would break it.
- **`cherrypick/` must never gain an `__init__.py`.** It is a native PEP 420 namespace package so it
  composes with `cherrypick.orchestrator` (and, eventually, every other module's own
  `cherrypick.<module>` namespace) under one `cherrypick.*` import root in the same interpreter. An
  `__init__.py` here would break that composition for every consumer at once.

One cost of staying flat: `tests/` is a top-level importable name if `packages/core` ever lands
directly on `sys.path`. Nothing in the suite does that — core is always reached through an installed
`cherrypick-core` distribution — so this is accepted, not a bug to fix.

## The module map

What lives here and who leans on it. Deliberately one line each and no signatures — an API listing over
this much code would drift within a sprint and nothing could verify it. The docstring at the top of each
module is the real reference; this is the index that tells you which one to open.

| Module | What it owns |
|---|---|
| `home` | The one resolver for the per-user cherrypick home. Everything else derives paths from it. |
| `db` | SQLite connection mechanics + additive migrations, including the shared read-only opener. |
| `logs` | One line format for every module log in the suite. |
| `calendar` | The shared market calendar. The suite's single source of trading days and holidays. |
| `fees` | The tastytrade cost model — one home for the fee schedule. Every "net" figure in the suite goes through it. |
| `auth` | Keyring credentials + a lazy OAuth session, parameterized per consumer. |
| `broker` | Shared tastytrade primitives: account resolution, option-chain helpers, and the live write path with its governor. |
| `risk` | Account-level risk primitives. Fail-closed and opt-in. |
| `entry` | Entry-permission rules MEIC and flies must apply identically: cadence and the leg-sign rule. |
| `structures` | Shared option-structure arithmetic — pure formulas (the straddle-based expected move) earnings and calendars must agree on. |
| `streamer` | The generic persistent DXLink streaming engine. `packages/streamer` is the daemon around this. |
| `streamcache` | The shared stream-cache schema and its SQLite helpers — the contract between producer and every reader. |
| `streamrequests` | The subscription registry: how a module declares the symbols it needs, plus the union read the streamer subscribes from and the orchestrator checks staleness against. |
| `dxfeed` | On-demand DXLink event collectors, for callers that want a snapshot rather than a stream. |
| `gex` | The GEX engine: a pure function over an option-chain snapshot. Copying this once let the math drift ~75×. |
| `profiles` | The named risk-profile registry and merge engine — how a partial override becomes an effective config. |
| `metrics` | The shared calibration metric bundle: one vocabulary for promotion evidence. |
| `advice` | Bounded, expiring, deterministically-validated parameter advice. Both the orchestrator and the module loop validate through this same code. `session_decision` is the read-once rule all seven consuming modules share; a **baseline** decision is deliberately never persisted, so a process reaching it with an advice-less config cannot fix the day for the loop that comes after it (2026-08-25: meic and earnings each lost their most informative session to exactly that). |
| `ledgers` | Per-schema readers for every module's ledger — the one home for the net, cost, capital and session rules. `concentration` answers, over those normalised records, how much of a module net rests on a single arm and whether removing it flips the sign; a total that changes sign without its largest contributor is a measurement of that arm, not of the module. |
| `regime` | The one at-or-before, staleness-bounded join against the recorded market-regime series (gex's history DB). Derived ratios/dispersion are computed here at read time, never stored. |
| `viz` | A declarative dashboard-section contract plus one generic renderer. |

The reason to put something here is that **two packages would otherwise disagree** — on what a fee is,
what a trading day is, or what "net" means. That is the bar; a helper only one package uses belongs in
that package.

## Commands

```bash
pip install -e ".[dev]"
ruff check .
pytest
```
