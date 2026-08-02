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

## Commands

```bash
pip install -e ".[dev]"
ruff check .
pytest
```
